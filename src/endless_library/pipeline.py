from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

from endless_library.config import Config
from endless_library.convert import ConvertError, convert_to_epub
from endless_library.db.bench import BenchRunRepo
from endless_library.db.books import BookRepo, BookRow
from endless_library.db.candidates import CandidateRepo
from endless_library.db.events import EventRepo
from endless_library.db.mirrors import MirrorRepo
from endless_library.db.schema import init_db
from endless_library.db.sources import SourceAccountRepo
from endless_library.domain.format_router import decide_format_action
from endless_library.domain.models import Candidate, SearchQuery
from endless_library.domain.scoring import score_candidate
from endless_library.domain.state_machine import decide_auto_pick
from endless_library.download import DownloadError, download
from endless_library.kindle import KindleSendError, send_to_kindle
from endless_library.notifier import Notifier
from endless_library.scrapers import registry as scrapers_registry
from endless_library.security.archive_safety import SafetyLimits
from endless_library.security.unpack import UnpackError, unpack_if_archive
from endless_library.sources import registry as sources_registry

log = logging.getLogger(__name__)


@dataclass(slots=True)
class PipelineDeps:
    cfg: Config
    db_path: Path
    notifier: Notifier
    books: BookRepo
    cands: CandidateRepo
    events: EventRepo
    sources: SourceAccountRepo
    bench: BenchRunRepo
    mirrors: MirrorRepo

    @classmethod
    def build(cls, *, cfg: Config, db_path: Path) -> PipelineDeps:
        init_db(db_path)
        mirrors = MirrorRepo(db_path)
        mirrors.seed_curated()  # idempotent
        return cls(
            cfg=cfg,
            db_path=db_path,
            notifier=Notifier(cfg.pushover),
            books=BookRepo(db_path),
            cands=CandidateRepo(db_path),
            events=EventRepo(db_path),
            sources=SourceAccountRepo(db_path),
            bench=BenchRunRepo(db_path),
            mirrors=mirrors,
        )


# ----- source polling -----


def poll_source_account(deps: PipelineDeps, account_id: int) -> int:
    """Poll a single source account. Returns count of new books added."""
    acct = deps.sources.get(account_id)
    if acct is None or not acct.enabled:
        return 0
    added = 0
    try:
        src = sources_registry.build(acct.source)
        refs = list(src.list_to_read(identifier=acct.identifier, token=acct.token))
    except Exception as e:
        deps.events.append(
            book_id=None,
            kind="error",
            message=f"poll {acct.source} {acct.identifier} failed: {e}",
        )
        log.warning("poll failed for %s: %s", acct.source, e)
        return 0
    for ref in refs:
        existing = deps.books.count()
        bid = deps.books.upsert(
            title=ref.title,
            author=ref.author,
            isbn13=ref.isbn13,
            source=ref.source,
            source_id=ref.source_id,
            source_added_at=ref.source_added_at,
        )
        if deps.books.count() > existing:
            added += 1
            deps.events.append(
                book_id=bid,
                kind="state_change",
                message=f"added from {ref.source}",
                meta={"isbn13": ref.isbn13, "source_id": ref.source_id},
            )
    deps.sources.mark_polled(acct.id)
    log.info("poll account %d (%s) added %d new books", acct.id, acct.source, added)
    return added


def poll_sources(deps: PipelineDeps) -> int:
    """Iterate every enabled source account. Used by manual Run-Now."""
    total = 0
    for acct in deps.sources.list_enabled():
        total += poll_source_account(deps, acct.id)
    log.info(
        "poll_sources added %d new books across %d accounts",
        total,
        len(deps.sources.list_enabled()),
    )
    return total


# ----- queue processing -----


def _search_with_strategies(
    deps: PipelineDeps,
    book: BookRow,
) -> tuple[list[Candidate], str | None]:
    """Try each enabled scraper strategy until one returns candidates.
    Returns (candidates, strategy_name_or_None)."""
    sq = SearchQuery(
        title=book.title,
        author=book.author,
        isbn13=book.isbn13,
        format_priority=tuple(deps.cfg.scrapers.format_priority),
        language=deps.cfg.scrapers.language,
    )
    for s_name in scrapers_registry.enabled_order_for_query(deps.cfg.scrapers, book.title or ""):
        try:
            scraper = scrapers_registry.build(s_name, deps.cfg.scrapers)
            cands = scraper.search(sq)
        except NotImplementedError:
            continue
        except Exception as e:
            deps.events.append(
                book_id=book.id,
                kind="error",
                scraper=s_name,
                message=f"search error: {e}",
            )
            continue
        if cands:
            deps.events.append(
                book_id=book.id,
                kind="scrape",
                scraper=s_name,
                message=f"got {len(cands)} candidates",
            )
            deps.books.mark_stage(book.id, "searched")
            return cands, s_name
    return [], None


def _score_and_persist(deps: PipelineDeps, book: BookRow, candidates: list[Candidate]):
    deps.cands.clear_for_book(book.id)
    scored: list[tuple[float, Candidate]] = []
    for c in candidates:
        isbn_match = bool(book.isbn13) and book.isbn13 in (c.raw.get("isbns") or [])
        sq = SearchQuery(
            title=book.title,
            author=book.author,
            isbn13=book.isbn13,
            format_priority=tuple(deps.cfg.scrapers.format_priority),
            language=deps.cfg.scrapers.language,
        )
        sb = score_candidate(c, sq, deps.cfg.scoring, isbn13_match=isbn_match)
        if sb.is_hard_skip:
            continue
        deps.cands.insert(
            book_id=book.id,
            provider=c.provider,
            md5=c.md5,
            title=c.title,
            author=c.author,
            language=c.language,
            format=c.format,
            filesize_bytes=c.filesize_bytes,
            year=c.year,
            publisher=c.publisher,
            edition_hints=c.edition_hints,
            score=sb.total,
            detail_url=c.detail_url,
            raw_json=json.dumps(c.raw or {}),
        )
        scored.append((sb.total, c))
    scored.sort(key=lambda x: x[0], reverse=True)
    return scored


def _resolve_and_download(deps: PipelineDeps, book: BookRow, c: Candidate) -> Path | None:
    """Find a scraper that can resolve this candidate's CDN URL and stream the file down."""
    for s_name in scrapers_registry.enabled_order_for_query(deps.cfg.scrapers, book.title or ""):
        try:
            scraper = scrapers_registry.build(s_name, deps.cfg.scrapers)
            handle = scraper.resolve_cdn(c)
        except NotImplementedError:
            continue
        except Exception as e:
            deps.events.append(
                book_id=book.id, kind="error", scraper=s_name, message=f"resolve_cdn error: {e}"
            )
            continue
        if not handle:
            continue
        deps.events.append(
            book_id=book.id,
            kind="scrape",
            scraper=s_name,
            message=f"resolved cdn: {handle.url[:80]}",
        )
        try:
            result = download(
                handle,
                dest_dir=Path(deps.cfg.general.books_dir),
                fallback_name=f"{book.title} - {book.author or 'unknown'}",
                expected_md5=c.md5,
            )
        except DownloadError as e:
            deps.events.append(
                book_id=book.id, kind="download", scraper=s_name, message=f"download failed: {e}"
            )
            continue
        deps.events.append(
            book_id=book.id,
            kind="download",
            scraper=s_name,
            message=f"downloaded {result.size} bytes -> {result.path.name}",
        )
        # Hygiene + optional AV on archive-wrapped downloads (kindlebangla's
        # single-file Drive cases ship the .epub inside a .rar). Bare ebooks
        # pass through unchanged after a direct AV scan.
        try:
            limits = SafetyLimits(
                max_archive_size_mb=deps.cfg.security.max_archive_size_mb,
                max_extracted_size_mb=deps.cfg.security.max_extracted_size_mb,
                max_members=deps.cfg.security.max_members,
            )
            unpacked = unpack_if_archive(
                result.path,
                limits=limits,
                require_clamav=deps.cfg.security.require_clamav,
            )
        except UnpackError as e:
            deps.events.append(
                book_id=book.id,
                kind="error",
                scraper=s_name,
                message=f"unpack/AV rejected: {e}",
            )
            continue
        if unpacked.was_archive:
            deps.events.append(
                book_id=book.id,
                kind="convert",
                scraper=s_name,
                message=f"unpacked archive -> {unpacked.path.name}",
            )
        final_path = unpacked.path
        deps.books.set_status(
            book.id,
            "downloading",
            md5=result.md5,
            file_path=str(final_path),
            format=final_path.suffix.lstrip("."),
            file_size=final_path.stat().st_size,
        )
        deps.books.mark_stage(book.id, "downloaded")
        return final_path
    return None


def process_one(deps: PipelineDeps, book: BookRow) -> str:
    """Run the full pipeline on a single book. Returns final status.

    Resume semantics:
      - If `downloaded_at` is set AND the file is still on disk, skip the
        search+download steps entirely and resume at convert/send.
      - If `converted_at` is set AND the converted file is on disk, skip convert.
      - `attempts` only increments when we actually do a fresh search.
    """
    if book.downloaded_at and book.file_path:
        existing = Path(book.file_path)
        if existing.exists() and existing.stat().st_size > 0:
            deps.events.append(
                book_id=book.id,
                kind="state_change",
                message=f"resume: already downloaded at {book.downloaded_at}, jumping to convert/send",
            )
            return _process_from_downloaded(deps, book, existing)
        deps.books.clear_stages_from(book.id, stage="downloaded")
        deps.events.append(
            book_id=book.id,
            kind="state_change",
            message="resume: downloaded file missing on disk; restarting from search",
        )

    deps.books.set_status(book.id, "searching")
    deps.books.increment_attempts(book.id)
    deps.events.append(book_id=book.id, kind="state_change", message="-> searching")

    candidates, _strat = _search_with_strategies(deps, book)
    if not candidates:
        deps.books.set_status(book.id, "failed", error="no candidates from any scraper")
        return "failed"
    scored = _score_and_persist(deps, book, candidates)
    if not scored:
        deps.books.set_status(book.id, "failed", error="all candidates hard-skipped (audio?)")
        return "failed"
    top = scored[0][0]
    second = scored[1][0] if len(scored) > 1 else 0.0
    # Script-aware failure floor: non-Latin queries can't easily clear
    # the Latin 40-point floor because they have no ISBN, no clean author
    # parse, and shorter token sets in rapidfuzz. Use the (lower) non-Latin
    # floor when the queried title is non-Latin.
    from endless_library.domain.scoring import _is_non_latin

    cfg_score = deps.cfg.general
    if _is_non_latin(book.title or ""):
        floor = cfg_score.min_score_for_failure_non_latin
    else:
        floor = cfg_score.min_score_for_failure
    decision = decide_auto_pick(
        top=top,
        second=second,
        threshold=deps.cfg.general.auto_pick_threshold,
        gap=deps.cfg.general.auto_pick_gap,
        min_score_for_failure=floor,
    )
    if decision == "failed":
        deps.books.set_status(book.id, "failed", error=f"no plausible match (top={top:.1f})")
        return "failed"
    if decision == "needs_review":
        deps.books.set_status(
            book.id, "needs_review", error=f"low confidence top={top:.1f} second={second:.1f}"
        )
        deps.notifier.book_needs_review(book.title, book.author)
        return "needs_review"
    # auto-pick
    picked_cand = scored[0][1]
    file_path = _resolve_and_download(deps, book, picked_cand)
    if not file_path:
        deps.books.set_status(book.id, "failed", error="all scrapers failed to resolve/download")
        return "failed"
    return _process_from_downloaded(deps, book, file_path)


def _process_from_downloaded(deps: PipelineDeps, book: BookRow, file_path: Path) -> str:
    """Resume the pipeline from a known-good downloaded file."""
    action = decide_format_action(file_path.suffix)
    if action == "skip":
        deps.books.set_status(book.id, "skipped", error=f"format unsupported: {file_path.suffix}")
        return "skipped"
    if action == "convert" and deps.cfg.calibre.enabled:
        already_converted = (
            book.converted_at
            and book.file_path
            and Path(book.file_path).suffix == ".epub"
            and Path(book.file_path).exists()
        )
        if already_converted:
            file_path = Path(book.file_path)
            deps.events.append(
                book_id=book.id,
                kind="state_change",
                message="resume: skipping convert, output already exists",
            )
        else:
            deps.books.set_status(book.id, "converting")
            try:
                cr = convert_to_epub(
                    file_path,
                    output_profile=deps.cfg.calibre.output_profile,
                    timeout_seconds=deps.cfg.calibre.conversion_timeout_seconds,
                )
                file_path = cr.path
                deps.books.set_status(
                    book.id, "converting", format="epub", file_path=str(file_path)
                )
                deps.books.mark_stage(book.id, "converted")
                deps.events.append(
                    book_id=book.id,
                    kind="convert",
                    message=f"converted to {file_path.name}",
                )
            except ConvertError as e:
                deps.books.set_status(book.id, "failed", error=f"convert failed: {e}")
                return "failed"
    deps.books.set_status(book.id, "sending")
    # Enrich epub/azw3 metadata so Kindle sees real author/series/tags.
    if deps.cfg.calibre.enabled and file_path.suffix.lower() in {".epub", ".azw3", ".mobi"}:
        from endless_library.convert import enrich_metadata

        tags = (book.tags or "").split(",") if book.tags else None
        tags = [t.strip() for t in tags if t.strip()] if tags else None
        try:
            enrich_metadata(
                file_path,
                title=book.title,
                author=book.author,
                series=book.series,
                tags=tags,
                isbn=book.isbn13,
            )
            deps.events.append(
                book_id=book.id,
                kind="convert",
                message="metadata enriched (author/series/tags/isbn)",
            )
        except ConvertError as e:
            deps.events.append(
                book_id=book.id,
                kind="error",
                message=f"metadata enrich failed (non-fatal): {e}",
            )
        # Push the file into our Calibre library so Calibre-Web shows it
        try:
            from endless_library.convert import add_to_calibre_library

            calibre_lib = Path(deps.cfg.general.books_dir).parent / "calibre-library"
            cb_id = add_to_calibre_library(
                file_path,
                library_path=calibre_lib,
                series=book.series,
                tags=tags,
            )
            if cb_id is not None:
                deps.events.append(
                    book_id=book.id,
                    kind="convert",
                    message=f"added to Calibre library as id={cb_id}",
                )
        except Exception as e:
            deps.events.append(
                book_id=book.id,
                kind="error",
                message=f"calibre import failed (non-fatal): {e}",
            )
    # Pre-flight: SMTP size guard. Gmail caps outbound message at ~25 MB
    # which is ~22 MB raw attachment after base64. Rejecting at this stage
    # gives a clear error and lets us try PDF->EPUB rescue first.
    raw_bytes = file_path.stat().st_size
    cap_bytes = deps.cfg.smtp.max_attachment_mb * 1024 * 1024
    # Base64 inflates by 4/3; with email headers + boundaries, 1.4x is safe
    inflated = int(raw_bytes * 1.4)
    if inflated > cap_bytes and file_path.suffix.lower() == ".pdf":
        # Try ebook-convert PDF -> EPUB; text-only PDFs compress ~5-10x
        try:
            epub_path = convert_to_epub(file_path)
            new_inflated = int(epub_path.stat().st_size * 1.4)
            if new_inflated <= cap_bytes:
                deps.events.append(
                    book_id=book.id,
                    kind="convert",
                    message=f"oversize PDF ({raw_bytes // 1_048_576}MB) -> EPUB "
                    f"({epub_path.stat().st_size // 1_048_576}MB) for SMTP fit",
                )
                file_path = epub_path
                raw_bytes = file_path.stat().st_size
                inflated = new_inflated
                deps.books.set_status(
                    book.id,
                    "downloading",
                    file_path=str(file_path),
                    format=file_path.suffix.lstrip("."),
                    file_size=raw_bytes,
                )
        except Exception as e:
            deps.events.append(
                book_id=book.id,
                kind="error",
                message=f"PDF->EPUB rescue failed: {e}",
            )

    if inflated > cap_bytes:
        msg = (
            f"too large for SMTP: {raw_bytes // 1_048_576}MB raw "
            f"-> ~{inflated // 1_048_576}MB after base64, cap "
            f"{deps.cfg.smtp.max_attachment_mb}MB"
        )
        deps.books.set_status(book.id, "needs_review", error=msg)
        deps.events.append(book_id=book.id, kind="error", message=msg)
        return "needs_review"

    try:
        send_to_kindle(
            attachment=file_path,
            kindle=deps.cfg.kindle,
            smtp=deps.cfg.smtp,
            title=book.title,
            author=book.author,
        )
    except KindleSendError as e:
        deps.books.set_status(book.id, "failed", error=f"kindle send failed: {e}")
        return "failed"
    deps.books.set_status(book.id, "sent")
    deps.events.append(book_id=book.id, kind="send", message="sent to kindle")
    deps.notifier.book_sent(book.title, book.author, file_path.suffix.lstrip("."))
    return "sent"


def process_queue(deps: PipelineDeps) -> dict[str, int]:
    """Walk every pending book; returns a tally by terminal status."""
    deps.books.reset_zombies(stale_minutes=deps.cfg.general.zombie_stale_minutes)
    tally = {"sent": 0, "failed": 0, "needs_review": 0, "skipped": 0}
    for b in deps.books.pending(max_attempts=deps.cfg.general.max_attempts):
        try:
            st = process_one(deps, b)
        except Exception as e:
            log.exception("pipeline crashed on book %s", b.id)
            deps.books.set_status(b.id, "failed", error=f"pipeline crash: {e}")
            st = "failed"
        tally[st] = tally.get(st, 0) + 1
    return tally
