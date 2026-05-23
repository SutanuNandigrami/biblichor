from __future__ import annotations

import datetime
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from endless_library.bookorbit.drop import (
    BookOrbitDropError,
    drop_into_library,
)
from endless_library.bookorbit.service import BookOrbitService
from endless_library.config import Config
from endless_library.convert import ConvertError, convert_to_epub
from endless_library.db.bench import BenchRunRepo
from endless_library.db.bench_jobs import BenchJobsRepo
from endless_library.db.books import BookRepo, BookRow
from endless_library.db.candidates import CandidateRepo
from endless_library.db.events import EventRepo
from endless_library.db.mirrors import MirrorRepo
from endless_library.db.schema import init_db
from endless_library.db.sources import SourceAccountRepo
from endless_library.domain.format_router import decide_format_action
from endless_library.domain.models import Candidate, ScoreBreakdown, SearchQuery
from endless_library.domain.scoring import score_candidate
from endless_library.domain.state_machine import decide_auto_pick
from endless_library.download import DownloadError, download
from endless_library.kindle_router import deliver as _kindle_deliver
from endless_library.notifier import Notifier
from endless_library.scrapers import registry as scrapers_registry
from endless_library.security.archive_safety import SafetyLimits
from endless_library.security.unpack import UnpackError, unpack_if_archive
from endless_library.sources import registry as sources_registry

log = logging.getLogger(__name__)


def _book_context(
    book,
    cfg,
) -> tuple[bool, bool, str | None]:
    """Return (is_pd, is_recent_release, source) for a book row.

    Extracted from the duplicate computation that appeared in both
    _search_with_strategies and _resolve_and_download (ultrareview M7).
    """
    is_pd = bool(getattr(book, "is_public_domain", None)) or (
        getattr(book, "pub_year", None) is not None
        and (book.pub_year or 0) > 0
        and book.pub_year < 1928
    )
    source = getattr(book, "source", None)
    current_year = datetime.datetime.now().year
    recent_window = getattr(cfg.scrapers, "recent_release_window_years", 1)
    is_recent = (getattr(book, "pub_year", None) or 0) >= (current_year - recent_window)
    return is_pd, is_recent, source


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
    bench_jobs: BenchJobsRepo
    mirrors: MirrorRepo
    bookorbit_service: Any = field(default=None)

    @classmethod
    def build(cls, *, cfg: Config, db_path: Path) -> PipelineDeps:
        init_db(db_path)
        mirrors = MirrorRepo(db_path)
        mirrors.seed_curated()  # idempotent
        secrets_dir = Path(cfg.general.books_dir).parent / "secrets"
        bookorbit_service = BookOrbitService(
            cfg=cfg,
            db_path=db_path,
            restore_key_path=secrets_dir / "restore.key",
        )
        return cls(
            cfg=cfg,
            db_path=db_path,
            notifier=Notifier(cfg.pushover),
            books=BookRepo(db_path),
            cands=CandidateRepo(db_path),
            events=EventRepo(db_path),
            sources=SourceAccountRepo(db_path),
            bench=BenchRunRepo(db_path),
            bench_jobs=BenchJobsRepo(db_path),
            mirrors=mirrors,
            bookorbit_service=bookorbit_service,
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
        # Phase 6s.8: best-effort ISBN backfill via OpenLibrary for
        # sources that don't provide one (StoryGraph public profiles
        # often lack ISBN; same for some Wikidata results).
        isbn = ref.isbn13
        if not isbn and ref.title and ref.author:
            try:
                from endless_library.metadata.openlibrary import resolve_by_title_author

                meta = resolve_by_title_author(ref.title, ref.author, db_path=deps.db_path)
                if meta and meta.get("isbn"):
                    isbn = meta["isbn"].replace("-", "").strip() or None
            except Exception:
                pass
        bid = deps.books.upsert(
            title=ref.title,
            author=ref.author,
            isbn13=isbn,
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


def _peek_top_score(deps: PipelineDeps, book: BookRow, cands: list[Candidate]) -> float:
    """Probe-score the candidates against this book to decide chain fallthrough.

    Cheap: scoring doesn\'t hit the network. We re-score in _score_and_persist
    later when we\'re committing to the final pick; this peek tells us whether
    the current scraper\'s results are good enough to stop searching.
    """
    sq = SearchQuery(
        title=book.title,
        author=book.author,
        isbn13=book.isbn13,
        format_priority=tuple(deps.cfg.scrapers.format_priority),
        language=deps.cfg.scrapers.language,
    )
    top = 0.0
    for c in cands:
        isbn_match = bool(book.isbn13) and book.isbn13 in (c.raw.get("isbns") or [])
        s = score_candidate(c, sq, deps.cfg.scoring, isbn13_match=isbn_match)
        if not s.is_hard_skip and s.total > top:
            top = s.total
    return top


def _search_with_strategies(
    deps: PipelineDeps,
    book: BookRow,
) -> tuple[list[Candidate], str | None]:
    """Quality-gated scraper chain.

    Old behaviour: return at the first scraper that returns ANY candidate.
    That starved welib/libgen/archive when Anna\'s returned 25 garbage
    candidates (low title overlap, no ISBN match) for books Anna\'s
    doesn\'t actually have.

    New behaviour: try each enabled scraper in order, peek-score what it
    returns, and ONLY stop early when the best candidate clears a quality
    floor. Otherwise accumulate, continue, and let the final scoring step
    pick across the union. Floor differs by script class (Latin needs 60,
    non-Latin 40 — empirical numbers from the live queue).
    """
    from endless_library.domain.scoring import _is_non_latin

    sq = SearchQuery(
        title=book.title,
        author=book.author,
        isbn13=book.isbn13,
        format_priority=tuple(deps.cfg.scrapers.format_priority),
        language=deps.cfg.scrapers.language,
    )
    is_non_latin = _is_non_latin(book.title or "")
    configured_floor = (
        deps.cfg.general.fallthrough_quality_floor_non_latin
        if is_non_latin
        else deps.cfg.general.fallthrough_quality_floor
    )
    threshold = (
        deps.cfg.general.auto_pick_threshold_non_latin
        if is_non_latin
        else deps.cfg.general.auto_pick_threshold
    )
    # Invariant: never stop the chain below the auto-pick threshold.
    # If the best candidate from the first scraper is only good enough
    # for needs_review, we keep going in case a later scraper (welib /
    # libgen / archive / kindlebangla) has the actual book. Without
    # this, annas returning top=65 with threshold=70 short-circuited
    # the chain and dumped the book straight into needs_review.
    floor = max(configured_floor, threshold)
    pool: list[Candidate] = []
    seen_md5: set[str] = set()
    last_strategy: str | None = None
    _is_pd, _is_recent, _book_source = _book_context(book, deps.cfg)
    # Phase 6u.4: when the Source already emits a per-book slug (e.g.
    # kindlebangla.com Bengali slugs), bypass the scraper's search step
    # — synthesise a Candidate directly so we don't depend on
    # kindlebangla.com's brittle /index.php?search= behaviour, which
    # routinely misses long Bengali titles and slug-disambiguator
    # suffixes (e.g. "...-1" appended to repeat titles).
    if _book_source == "kindlebangla" and book.goodreads_id:
        slug = book.goodreads_id
        synth = Candidate(
            provider="kindlebangla",
            md5=None,
            title=book.title,
            author=book.author,
            language="bn",
            format="epub",
            filesize_bytes=None,
            year=None,
            publisher=None,
            edition_hints="",
            detail_url=f"https://www.kindlebangla.com/book/{slug}",
            raw={"slug": slug},
        )
        deps.events.append(
            book_id=book.id,
            kind="scrape",
            scraper="kindlebangla_curl",
            message=f"synthesised candidate from source slug ({slug[:40]})",
        )
        deps.books.mark_stage(book.id, "searched")
        return [synth], "kindlebangla_curl"

    for s_name in scrapers_registry.chain_for_source(
        deps.cfg.scrapers, source=_book_source, query_title=book.title or "",
        is_pd=_is_pd, is_recent_release=_is_recent
    ):
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
        if not cands:
            deps.events.append(
                book_id=book.id,
                kind="scrape",
                scraper=s_name,
                message="0 candidates; trying next scraper",
            )
            continue

        # Dedup by md5 against what we already have
        new_cands = [c for c in cands if (c.md5 or "") not in seen_md5 or not c.md5]
        for c in new_cands:
            if c.md5:
                seen_md5.add(c.md5)
        pool.extend(new_cands)
        last_strategy = s_name

        peek_top = _peek_top_score(deps, book, new_cands)
        if peek_top >= floor:
            deps.events.append(
                book_id=book.id,
                kind="scrape",
                scraper=s_name,
                message=f"got {len(cands)} candidates (top={peek_top:.1f} ≥ floor {floor:.0f}); stopping chain",
            )
            deps.books.mark_stage(book.id, "searched")
            return pool, s_name

        deps.events.append(
            book_id=book.id,
            kind="scrape",
            scraper=s_name,
            message=f"got {len(cands)} candidates (top={peek_top:.1f} < floor {floor:.0f}); falling through",
        )

    if pool:
        deps.books.mark_stage(book.id, "searched")
        deps.events.append(
            book_id=book.id,
            kind="scrape",
            scraper=last_strategy,
            message=(
                f"chain exhausted (no scraper >= floor {floor:.0f}); "
                f"using union of {len(pool)} candidates from {len(seen_md5) or 'pool'} dedup"
            ),
        )
    return pool, last_strategy


def _score_and_persist(deps: PipelineDeps, book: BookRow, candidates: list[Candidate]):
    """Return (total_score, candidate, score_breakdown) tuples, sorted high to low.

    Carrying the breakdown lets the caller read identity signals
    (isbn13_matched, title_similarity_raw) for the ISBN+title auto-pick
    override rule introduced in Phase 3b.
    """
    deps.cands.clear_for_book(book.id)
    scored: list[tuple[float, Candidate, ScoreBreakdown]] = []
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
        scored.append((sb.total, c, sb))
    scored.sort(key=lambda x: x[0], reverse=True)
    return scored


def _resolve_and_download(
    deps: PipelineDeps, book: BookRow, c: Candidate
) -> tuple[Path | None, str | None]:
    """Find a scraper that can resolve this candidate's CDN URL and stream
    the file down.

    Phase 6u.7: returns (path, last_error). When the chain exhausts the
    last_error carries the most specific failure cause (truncated RAR,
    Drive HTTP 403, no candidates) so the book's failed message is
    actionable instead of the generic 'all scrapers failed'."""
    last_error: str | None = None
    _is_pd, _is_recent, _book_source = _book_context(book, deps.cfg)
    for s_name in scrapers_registry.chain_for_source(
        deps.cfg.scrapers, source=_book_source, query_title=book.title or "",
        is_pd=_is_pd, is_recent_release=_is_recent
    ):
        try:
            scraper = scrapers_registry.build(s_name, deps.cfg.scrapers)
            handle = scraper.resolve_cdn(c)
        except NotImplementedError:
            continue
        except Exception as e:
            last_error = f"resolve_cdn error ({s_name}): {e}"
            deps.events.append(
                book_id=book.id, kind="error", scraper=s_name, message=f"resolve_cdn error: {e}"
            )
            continue
        if not handle:
            last_error = f"{s_name} returned no CDN handle"
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
            last_error = f"download failed ({s_name}): {e}"
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
            last_error = f"unpack rejected ({s_name}): {e}"
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
        return final_path, None
    return None, last_error


def _search_fail_or_skip(deps: PipelineDeps, book: BookRow, error: str) -> str:
    """Decide whether a search-step failure should bump attempts (failed)
    or park the book terminally (skipped).

    Once we've already failed `max_search_attempts_before_skip - 1`
    times, this attempt would be the Nth — parking is the right call
    so the queue doesn't keep cycling unfindable books.
    """
    threshold = deps.cfg.general.max_search_attempts_before_skip
    if book.attempts + 1 >= threshold:
        deps.books.set_skipped(book.id, error=f"{error} (parked after {threshold} attempts)")
        deps.events.append(
            book_id=book.id,
            kind="state_change",
            message=f"-> skipped (auto, {threshold} fruitless search rounds)",
        )
        return "skipped"
    deps.books.set_failed(book.id, error=error)
    return "failed"


def process_one(deps: PipelineDeps, book: BookRow) -> str:
    """Run the full pipeline on a single book. Returns final status.

    Resume semantics:
      - If `downloaded_at` is set AND the file is still on disk, skip the
        search+download steps entirely and resume at convert/send.
      - If `converted_at` is set AND the converted file is on disk, skip convert.
      - `attempts` only increments when we actually do a fresh search.

    Concurrency (Phase 6u.5): two scheduler jobs (_process_job + _retry_job)
    both call process_queue, and APScheduler's max_instances=1 is per-job —
    so they can race. claim_for_processing() atomically flips the row from
    queued/failed/needs_review to 'searching'; if another worker beat us
    to it the UPDATE matches 0 rows and we skip this book this cycle.
    Books already mid-pipeline (searching/picked/downloading/sending etc.)
    are returned to a sentinel "in-flight" status so process_queue can
    skip counting them.
    """
    # Phase 6u.5b: atomic claim FIRST. The resume path also passes
    # through this gate now so two cycles can't both Kindle-send the
    # same already-downloaded book. claim_for_processing flips the
    # row queued/failed/needs_review -> searching; if 0 rows match,
    # someone else owns it this cycle.
    if not deps.books.claim_for_processing(book.id):
        return "in_flight"

    # Resume paths (downloaded_at + file_path). The 'searching' state
    # is a benign mid-pipeline placeholder while we convert/send; the
    # status will flip to 'sent' or 'sending' as we progress.
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

    deps.events.append(book_id=book.id, kind="state_change", message="-> searching")

    candidates, _strat = _search_with_strategies(deps, book)
    if not candidates:
        return _search_fail_or_skip(deps, book, "no candidates from any scraper")
    # Set a dynamic deliverable cap on the scoring config so candidates
    # that can't possibly be SMTPed get hard-skipped at score time
    # rather than wasted a download + convert cycle. We use 1/1.4 of
    # the raw cap to account for base64 overhead (Gmail caps the
    # *encoded* message size).
    smtp_cap = int(deps.cfg.smtp.max_attachment_mb * 1024 * 1024 / 1.4)
    deps.cfg.scoring.deliverable_max_bytes = smtp_cap
    scored = _score_and_persist(deps, book, candidates)
    if not scored:
        return _search_fail_or_skip(deps, book, "all candidates hard-skipped (audio?)")
    top = scored[0][0]
    second = scored[1][0] if len(scored) > 1 else 0.0
    # Script-aware floor + auto-pick threshold. Bengali/Devanagari/CJK
    # queries without ISBN bottom out near 40 and top out near 65-70 —
    # both ends are too tight for the Latin defaults.
    from endless_library.domain.scoring import _is_non_latin

    cfg_g = deps.cfg.general
    if _is_non_latin(book.title or ""):
        floor = cfg_g.min_score_for_failure_non_latin
        threshold = cfg_g.auto_pick_threshold_non_latin
    else:
        floor = cfg_g.min_score_for_failure
        threshold = cfg_g.auto_pick_threshold
    # Phase 6u: kindlebangla emits per-book slugs that the companion
    # scraper resolves directly. There is no other ladder to compare
    # against, so we force auto-pick regardless of the score (which will
    # be near zero — no ISBN, Bengali title, no Latin author similarity).
    if getattr(book, "source", None) == "kindlebangla":
        floor = 0.0
        threshold = 0.0
    # Phase 3b: feed identity signals from the top candidate's breakdown
    # into decide_auto_pick so it can override on rock-solid ISBN+title.
    top_breakdown = scored[0][2]
    top_isbn_matched = top_breakdown.components.get("isbn13_matched", 0.0) >= 1.0
    top_title_sim = top_breakdown.components.get("title_similarity_raw", 0.0)
    decision = decide_auto_pick(
        top=top,
        second=second,
        threshold=threshold,
        gap=deps.cfg.general.auto_pick_gap,
        min_score_for_failure=floor,
        top_isbn_matched=top_isbn_matched,
        top_title_similarity=top_title_sim,
    )
    if decision == "auto" and top < threshold:
        deps.events.append(
            book_id=book.id,
            kind="auto_pick",
            message=(
                f"override: ISBN match + title similarity "
                f"{top_title_sim:.2f} >= 0.92 (total={top:.1f})"
            ),
        )
    if decision == "failed":
        return _search_fail_or_skip(deps, book, f"no plausible match (top={top:.1f})")
    if decision == "needs_review":
        deps.books.set_status(
            book.id, "needs_review", error=f"low confidence top={top:.1f} second={second:.1f}"
        )
        deps.notifier.book_needs_review(book.title, book.author)
        return "needs_review"
    # auto-pick
    picked_cand = scored[0][1]
    file_path, dl_err = _resolve_and_download(deps, book, picked_cand)
    if not file_path:
        # Phase 6u.7: surface the actual cause (truncated RAR, Drive
        # HTTP 403, etc.) instead of the generic "all scrapers failed".
        msg = dl_err or "all scrapers failed to resolve/download"
        deps.books.set_failed(book.id, error=msg)
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
                deps.books.set_failed(book.id, error=f"convert failed: {e}")
                return "failed"
    deps.books.set_status(book.id, "sending")
    # Enrich epub/azw3 metadata so Kindle sees real author/series/tags.
    # PDFs included: ebook-meta writes the PDF /Info dict which Kindle
    # reads for display. Without this, Bengali/CJK PDFs from Anna's
    # Archive show up on Kindle with whatever transliterated junk was
    # in the original file (often filename-derived "Foo -- Bar / Unknown").
    if deps.cfg.calibre.enabled and file_path.suffix.lower() in {".epub", ".azw3", ".mobi", ".pdf"}:
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
            convert_result = convert_to_epub(file_path)
            epub_path = convert_result.path
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

    # If still oversize after the EPUB rescue (e.g. scanned PDFs where
    # the EPUB ends up the same size as the source), try the dedicated
    # compressor: ocrmypdf --optimize 3 for PDFs, pngquant+jpegoptim
    # for EPUBs. See compress.try_compress for the ladder.
    if inflated > cap_bytes:
        try:
            from endless_library.compress import try_compress

            compressed = try_compress(file_path, target_bytes=cap_bytes)
        except Exception as e:
            compressed = None
            deps.events.append(
                book_id=book.id,
                kind="error",
                message=f"compress step crashed (non-fatal): {e}",
            )
        if compressed is not None:
            old_bytes = raw_bytes
            file_path = compressed
            raw_bytes = file_path.stat().st_size
            inflated = int(raw_bytes * 1.4)
            deps.books.set_status(
                book.id,
                "downloading",
                file_path=str(file_path),
                format=file_path.suffix.lstrip("."),
                file_size=raw_bytes,
            )
            deps.events.append(
                book_id=book.id,
                kind="compress",
                message=(
                    f"compressed {file_path.suffix.lstrip('.')} "
                    f"{old_bytes // 1_048_576}MB -> {raw_bytes // 1_048_576}MB for SMTP fit"
                ),
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

    # Phase 6u.4: hand the enriched file off to BookOrbit BEFORE the
    # SMTP gate. BookOrbit has no rate limit; the library should fill
    # in real time even when Kindle delivery is throttled. Idempotent
    # via the event log — we skip the drop if any prior cycle already
    # emitted kind='bookorbit' for this book.
    _bo_already_done = any(
        e.kind == "bookorbit" for e in deps.events.recent_for_book(book.id, limit=200)
    )
    if (
        not _bo_already_done
        and deps.cfg.bookorbit.enabled
        and deps.cfg.bookorbit.library_root
    ):
        library_root_path = Path(deps.cfg.bookorbit.library_root)
        if not library_root_path.exists():
            log.warning(
                "bookorbit: library_root=%r does not exist; books are NOT being "
                "dropped into the library. Run `biblichor bookorbit-setup` to fix.",
                deps.cfg.bookorbit.library_root,
            )
            deps.events.append(
                book_id=book.id,
                kind="error",
                message=(
                    f"bookorbit drop skipped: library_root "
                    f"{deps.cfg.bookorbit.library_root!r} not found"
                ),
            )
        else:
            try:
                drop = drop_into_library(
                    file_path,
                    library_root=library_root_path,
                    title=book.title or "",
                    author=book.author,
                    organization_mode=deps.cfg.bookorbit.organization_mode,
                )
                deps.events.append(
                    book_id=book.id,
                    kind="bookorbit",
                    message=f"added to library: {drop.target_path.name}",
                )
            except BookOrbitDropError as e:
                deps.events.append(
                    book_id=book.id,
                    kind="error",
                    message=f"bookorbit drop failed (non-fatal): {e}",
                )

    # Phase STK 12: unified delivery via kindle_router (STK-primary, SMTP-fallback).
    # The router handles retry, backoff, rate-gate, and audit-event recording.
    # If both STK and SMTP are exhausted, result.ok is False and we mark failed.
    result = _kindle_deliver(
        file_path=file_path,
        book=book,
        cfg=deps.cfg,
        db_path=deps.db_path,
        svc=deps.bookorbit_service,
    )
    if result.ok:
        deps.books.mark_kindled(book.id, method=result.method.value)
        deps.events.append(book_id=book.id, kind="send", message=f"sent via {result.method.value}")
        deps.notifier.book_sent(book.title, book.author, file_path.suffix.lstrip("."))
        return "sent"
    else:
        deps.books.set_failed(book.id, error=f"kindle send failed: {result.error}")
        return "failed"


def process_queue(deps: PipelineDeps) -> dict[str, int]:
    """Walk every pending book; returns a tally by terminal status.

    Phase 6u.5: "in_flight" is the sentinel process_one returns when
    another worker beat us to the claim. We don't count it as a
    terminal outcome and we don't mark the book failed."""
    deps.books.reset_zombies(stale_minutes=deps.cfg.general.zombie_stale_minutes)
    tally = {"sent": 0, "failed": 0, "needs_review": 0, "skipped": 0, "deferred": 0, "in_flight": 0}
    for b in deps.books.pending(max_attempts=deps.cfg.general.max_attempts):
        try:
            st = process_one(deps, b)
        except Exception as e:
            log.exception("pipeline crashed on book %s", b.id)
            deps.books.set_failed(b.id, error=f"pipeline crash: {e}")
            st = "failed"
        tally[st] = tally.get(st, 0) + 1
    return tally
