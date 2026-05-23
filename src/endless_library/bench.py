from __future__ import annotations

import concurrent.futures as _cf
import logging
import time
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

import yaml

from endless_library.config import Config
from endless_library.db.bench import BenchRunRepo
from endless_library.domain.models import SearchQuery
from endless_library.scrapers import registry
from endless_library.scrapers.base import NotConfigured

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class BenchQuery:
    title: str
    author: str
    isbn13: str
    language: str
    tags: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class BenchOutcome:
    scraper: str
    query: str
    success: bool
    duration_ms: int
    candidates: int
    matched_isbn: bool
    note: str = ""


def _resolve_queries_path(path: Path | None) -> Path:
    if path is not None:
        return path
    return Path(__file__).resolve().parent.parent.parent / "bench" / "queries.yaml"


def load_queries(path: Path | None = None) -> tuple[list[BenchQuery], list[int]]:
    """Load queries.yaml. When path is None, resolve relative to the
    repo root so it works regardless of cwd — fixes the container
    HTTP 500 where the cwd-relative path missed the bundled file.

    Tagged-query schema is Phase 6v.3 — the optional `tags` field on
    each query is read here, but the per-scraper corpus filter lives
    in `load_corpus_tags` so this signature stays backwards-compatible.
    """
    p = _resolve_queries_path(path)
    raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    qs: list[BenchQuery] = []
    for q in raw.get("queries", []):
        tags = tuple(q.get("tags", []) or ())
        qs.append(
            BenchQuery(
                title=q["title"],
                author=q.get("author", ""),
                isbn13=q.get("isbn13", ""),
                language=q.get("language", "en"),
                tags=tags,
            )
        )
    quick = list(raw.get("quick_indices", []))
    return qs, quick


def load_corpus_tags(path: Path | None = None) -> dict[str, frozenset[str]]:
    """Per-scraper corpus filter from queries.yaml.

    Returns a mapping of scraper-name to the set of tags it accepts.
    Scrapers absent from the map are general-purpose (accept every
    query in the corpus).
    """
    p = _resolve_queries_path(path)
    raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    raw_corpus = raw.get("corpus_tags", {}) or {}
    return {scraper: frozenset(tags) for scraper, tags in raw_corpus.items()}


def queries_for_scraper(
    queries: Iterable[BenchQuery],
    scraper: str,
    corpus_tags: dict[str, frozenset[str]],
) -> list[BenchQuery]:
    """Subset of `queries` this scraper should be benched against.

    - If `scraper` has no entry in `corpus_tags`, it gets every query
      (general-purpose backends like annas_curl / libgen / zlib).
    - If it does, only queries whose tags intersect the scraper's
      accepted tags are kept (e.g. kindlebangla_curl gets only bn
      queries, gutendex gets only PD queries).
    """
    wanted = corpus_tags.get(scraper)
    if wanted is None:
        return list(queries)
    return [q for q in queries if any(t in wanted for t in q.tags)]


def run_bench(
    cfg: Config,
    queries: Iterable[BenchQuery],
    *,
    repo: BenchRunRepo | None = None,
    strategies: list[str] | None = None,
    corpus_tags: dict[str, frozenset[str]] | None = None,
) -> list[BenchOutcome]:
    """Run each scraper against the queries it accepts.

    Phase 6v.3: `corpus_tags` filters which queries each scraper sees
    (e.g. kindlebangla_curl only bn, gutendex only PD). When None,
    every scraper sees every query (legacy behaviour).
    """
    strats = strategies or registry.enabled_order(cfg.scrapers)
    outcomes: list[BenchOutcome] = []
    all_queries = list(queries)
    if corpus_tags is None:
        try:
            tag_map = load_corpus_tags()
        except Exception as e:
            log.warning("bench: could not load corpus_tags, running unfiltered: %s", e)
            tag_map = {}
    else:
        tag_map = corpus_tags
    for s_name in strats:
        try:
            scraper = registry.build(s_name, cfg.scrapers)
        except NotConfigured as e:
            log.info("bench: %s not configured, recording per-query outcomes", s_name)
            _nc_scoped = queries_for_scraper(all_queries, s_name, tag_map)
            for _ncq in _nc_scoped:
                _note = f"creds-missing: {e}"
                outcomes.append(BenchOutcome(
                    scraper=s_name, query=_ncq.title, success=False,
                    duration_ms=0, candidates=0, matched_isbn=False, note=_note,
                ))
                if repo:
                    repo.record(scraper=s_name, query=_ncq.title, success=False,
                                duration_ms=0, notes=_note)
            continue
        except Exception as e:
            log.warning("could not build %s: %s", s_name, e)
            continue
        scoped = queries_for_scraper(all_queries, s_name, tag_map)
        if not scoped:
            log.info(
                "bench: skipping %s — no queries match its corpus tags %s",
                s_name,
                tag_map.get(s_name),
            )
            continue
        timeout_sec = float(getattr(cfg.bench, "per_query_timeout_sec", 20))
        breaker_limit = int(getattr(cfg.bench, "circuit_break_after_consecutive_fails", 3))
        consecutive_fails = 0
        for q in scoped:
            if consecutive_fails >= breaker_limit:
                outcomes.append(BenchOutcome(
                    scraper=s_name, query=q.title, success=False, duration_ms=0,
                    candidates=0, matched_isbn=False,
                    note=f"circuit-broken: skipped after {breaker_limit} consecutive failures",
                ))
                if repo:
                    repo.record(scraper=s_name, query=q.title, success=False,
                                duration_ms=0, notes="circuit-broken")
                continue
            sq = SearchQuery(
                title=q.title,
                author=q.author,
                isbn13=q.isbn13,
                format_priority=tuple(cfg.scrapers.format_priority),
                language=q.language,
            )
            t0 = time.monotonic()
            success = False
            n_cands = 0
            matched = False
            note = ""
            try:
                ex = _cf.ThreadPoolExecutor(max_workers=1)
                try:
                    fut = ex.submit(scraper.search, sq)
                    try:
                        cands = fut.result(timeout=timeout_sec)
                    except _cf.TimeoutError:
                        # Hard cancellation requires async-native scrapers; thread runs
                        # to its own network timeout. Don't wait for it.
                        ex.shutdown(wait=False, cancel_futures=True)
                        raise
                    ex.shutdown(wait=True)
                except _cf.TimeoutError:
                    raise
                n_cands = len(cands)
                if cands:
                    # Match if any candidate's title/author roughly matches AND format ok.
                    # Stronger: ISBN match would require fetching md5 pages; for the bench
                    # we accept "≥1 candidate with the expected title token" as success.
                    title_lc = q.title.lower().split(":")[0].strip()
                    for c in cands[:5]:
                        if c.title and title_lc.split()[0] in (c.title or "").lower():
                            matched = True
                            break
                    success = matched
            except _cf.TimeoutError:
                note = f"timeout after {timeout_sec}s"
            except NotImplementedError as e:
                note = f"stub: {e}"
            except Exception as e:
                note = f"{type(e).__name__}: {e}"
            if not success:
                consecutive_fails += 1
            else:
                consecutive_fails = 0
            dur = int((time.monotonic() - t0) * 1000)
            outcomes.append(
                BenchOutcome(
                    scraper=s_name,
                    query=q.title,
                    success=success,
                    duration_ms=dur,
                    candidates=n_cands,
                    matched_isbn=matched,
                    note=note,
                )
            )
            if repo:
                repo.record(
                    scraper=s_name, query=q.title, success=success, duration_ms=dur, notes=note
                )
    return outcomes


def format_table(outcomes: list[BenchOutcome]) -> str:
    if not outcomes:
        return "(no outcomes)\n"
    # Group by scraper, summarize
    by_strat: dict[str, list[BenchOutcome]] = {}
    for o in outcomes:
        by_strat.setdefault(o.scraper, []).append(o)
    lines = ["| Scraper | Pass | Fail | Avg ms |", "|---|---|---|---|"]
    for name, rows in by_strat.items():
        p = sum(1 for r in rows if r.success)
        f = len(rows) - p
        avg = int(sum(r.duration_ms for r in rows) / len(rows)) if rows else 0
        lines.append(f"| {name} | {p} | {f} | {avg} |")
    lines.append("")
    lines.append("### Per-query")
    lines.append("| Scraper | Query | OK | ms | cands | note |")
    lines.append("|---|---|---|---|---|---|")
    for o in outcomes:
        ok = "✓" if o.success else "✗"
        lines.append(
            f"| {o.scraper} | {o.query[:40]} | {ok} | {o.duration_ms} | {o.candidates} | {o.note[:60]} |"
        )
    return "\n".join(lines) + "\n"
