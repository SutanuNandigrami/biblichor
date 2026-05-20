from __future__ import annotations

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

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class BenchQuery:
    title: str
    author: str
    isbn13: str
    language: str


@dataclass(frozen=True, slots=True)
class BenchOutcome:
    scraper: str
    query: str
    success: bool
    duration_ms: int
    candidates: int
    matched_isbn: bool
    note: str = ""


def load_queries(path: Path | None = None) -> tuple[list[BenchQuery], list[int]]:
    """Load queries.yaml. When path is None, resolve relative to the
    repo root so it works regardless of cwd — fixes the container
    HTTP 500 where the cwd-relative path missed the bundled file."""
    if path is None:
        path = Path(__file__).resolve().parent.parent.parent / "bench" / "queries.yaml"
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    qs = [BenchQuery(**q) for q in raw.get("queries", [])]
    quick = list(raw.get("quick_indices", []))
    return qs, quick


def run_bench(
    cfg: Config,
    queries: Iterable[BenchQuery],
    *,
    repo: BenchRunRepo | None = None,
    strategies: list[str] | None = None,
) -> list[BenchOutcome]:
    strats = strategies or registry.enabled_order(cfg.scrapers)
    outcomes: list[BenchOutcome] = []
    for s_name in strats:
        try:
            scraper = registry.build(s_name, cfg.scrapers)
        except Exception as e:
            log.warning("could not build %s: %s", s_name, e)
            continue
        for q in queries:
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
                cands = scraper.search(sq)
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
            except NotImplementedError as e:
                note = f"stub: {e}"
            except Exception as e:
                note = f"{type(e).__name__}: {e}"
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
