#!/usr/bin/env python3
"""Benchmark annas_patchright resolve_cdn + optional partial download.

Reads a list of md5s from a file (one per line; '#' lines ignored), runs
resolve_cdn against each via annas_patchright, optionally fetches the
first N bytes of the resolved URL to measure end-to-end timing.
Emits JSON with per-md5 + aggregate metrics.

Used to measure before/after of each resilience/speed change.

Usage:
    python tools/bench_annas.py run \\
        --md5-file tools/bench_md5s.txt \\
        --out /tmp/bench_baseline.json \\
        --label baseline \\
        [--fetch-bytes 1048576]

    python tools/bench_annas.py compare \\
        /tmp/bench_baseline.json /tmp/bench_after.json
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
import urllib.request
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

# Make src/ importable without `pip install -e .`
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from endless_library.config import Config
from endless_library.domain.models import Candidate
from endless_library.scrapers.annas_patchright import AnnasArchivePatchright


def bench_one(scraper, md5: str, *, fetch_bytes: int = 0) -> dict:
    cand = Candidate(
        provider="annas",
        md5=md5,
        title="bench",
        author=None,
        language=None,
        format="epub",
        filesize_bytes=None,
        year=None,
        publisher=None,
        edition_hints="",
        detail_url=f"https://annas-archive.gl/md5/{md5}",
    )
    out: dict = {
        "md5": md5,
        "ok": False,
        "resolve_ms": None,
        "fetch_ms": None,
        "bytes": 0,
        "error": None,
        "resolved_url": None,
    }
    t0 = time.monotonic()
    try:
        handle = scraper.resolve_cdn(cand)
    except Exception as e:
        out["error"] = f"resolve_cdn: {type(e).__name__}: {e}"
        return out
    out["resolve_ms"] = int((time.monotonic() - t0) * 1000)
    if not handle:
        out["error"] = "resolve_cdn returned None"
        return out
    out["resolved_url"] = handle.url[:120]
    if fetch_bytes <= 0:
        out["ok"] = True
        return out
    t1 = time.monotonic()
    try:
        req = urllib.request.Request(handle.url, headers=dict(handle.headers or {}))
        with urllib.request.urlopen(req, timeout=45) as r:
            buf = r.read(fetch_bytes)
            out["bytes"] = len(buf)
    except Exception as e:
        out["error"] = f"fetch: {type(e).__name__}: {e}"
        return out
    out["fetch_ms"] = int((time.monotonic() - t1) * 1000)
    out["ok"] = True
    return out


def _p95(xs: list[int]) -> int:
    if not xs:
        return 0
    s = sorted(xs)
    return s[min(len(s) - 1, int(len(s) * 0.95))]


def _stats(xs: list[int]) -> dict | None:
    if not xs:
        return None
    return {
        "mean": int(statistics.mean(xs)),
        "p50": int(statistics.median(xs)),
        "p95": _p95(xs),
        "min": min(xs),
        "max": max(xs),
    }


def aggregate(results: list[dict]) -> dict:
    ok = [r for r in results if r["ok"]]
    resolve_ms = [r["resolve_ms"] for r in ok if r["resolve_ms"] is not None]
    fetch_ms = [r["fetch_ms"] for r in ok if r["fetch_ms"] is not None]
    return {
        "n": len(results),
        "n_ok": len(ok),
        "success_rate": round(len(ok) / len(results), 3) if results else 0,
        "resolve_ms": _stats(resolve_ms),
        "fetch_ms": _stats(fetch_ms),
        "bytes_total": sum(r["bytes"] for r in ok),
    }


def _worker_resolve(payload: tuple[str, int]) -> dict:
    """Top-level so ProcessPoolExecutor can pickle it. Each subprocess
    builds its own scraper (and thus its own Chromium); the file-backed
    URL/cookie caches at /data are shared via atomic tmp+rename."""
    md5, fetch_bytes = payload
    cfg = Config()
    scraper = AnnasArchivePatchright(cfg.scrapers, headless=True)
    return bench_one(scraper, md5, fetch_bytes=fetch_bytes)


def cmd_run(args: argparse.Namespace) -> None:
    md5s = [
        line.strip()
        for line in Path(args.md5_file).read_text().splitlines()
        if line.strip() and not line.startswith("#")
    ]
    # `--repeat N` walks the md5 list N times. PR #40 introduced the
    # md5 -> partner URL cache; pass after the first round of the same
    # fixture should be sub-ms per resolve. Aggregate is grouped per
    # pass so the cache impact is visible.
    # `--workers N` runs N resolves concurrently per pass via
    # ProcessPoolExecutor (each subprocess has its own browser; caches
    # are shared via /data files). Used in PR #41 to measure whether
    # annas tolerates concurrent slow_download requests from one IP.
    print(
        f"# bench label={args.label}  n={len(md5s)}  "
        f"fetch_bytes={args.fetch_bytes}  repeat={args.repeat}  "
        f"workers={args.workers}",
        file=sys.stderr,
    )
    passes: list[dict] = []
    t0 = time.monotonic()
    if args.workers == 1:
        cfg = Config()
        scraper = AnnasArchivePatchright(cfg.scrapers, headless=True)
    for pass_idx in range(args.repeat):
        if args.repeat > 1:
            print(f"== pass {pass_idx + 1}/{args.repeat} ==", file=sys.stderr)
        pass_results: list[dict] = []
        if args.workers == 1:
            for i, md5 in enumerate(md5s, 1):
                print(f"[{i}/{len(md5s)}] {md5} ... ", end="", flush=True, file=sys.stderr)
                r = bench_one(scraper, md5, fetch_bytes=args.fetch_bytes)
                tag = "OK" if r["ok"] else f"FAIL({r['error']})"
                rms = r["resolve_ms"] or 0
                fms = r["fetch_ms"] or 0
                print(
                    f"{tag}  resolve={rms}ms  fetch={fms}ms  bytes={r['bytes']}",
                    file=sys.stderr,
                )
                pass_results.append(r)
        else:
            print(
                f"[parallel {args.workers}w] dispatching {len(md5s)} resolves...",
                file=sys.stderr,
            )
            pass_t0 = time.monotonic()
            with ProcessPoolExecutor(max_workers=args.workers) as ex:
                payloads = [(m, args.fetch_bytes) for m in md5s]
                futures = {ex.submit(_worker_resolve, p): p[0] for p in payloads}
                for fut in as_completed(futures):
                    md5 = futures[fut]
                    try:
                        r = fut.result()
                    except Exception as e:
                        r = {
                            "md5": md5,
                            "ok": False,
                            "resolve_ms": None,
                            "fetch_ms": None,
                            "bytes": 0,
                            "error": f"worker died: {e}",
                            "resolved_url": None,
                        }
                    pass_results.append(r)
                    tag = "OK" if r["ok"] else f"FAIL({r['error']})"
                    rms = r["resolve_ms"] or 0
                    print(f"  done: {md5} {tag} resolve={rms}ms", file=sys.stderr)
            print(
                f"[parallel {args.workers}w] pass wallclock: "
                f"{int((time.monotonic() - pass_t0) * 1000)} ms",
                file=sys.stderr,
            )
        passes.append(
            {
                "pass": pass_idx + 1,
                "results": pass_results,
                "aggregate": aggregate(pass_results),
            }
        )
    all_results = [r for p in passes for r in p["results"]]
    out = {
        "label": args.label,
        "md5_file": str(args.md5_file),
        "fetch_bytes": args.fetch_bytes,
        "repeat": args.repeat,
        "workers": args.workers,
        "wallclock_seconds": int(time.monotonic() - t0),
        "passes": passes,
        "results": all_results,
        "aggregate": aggregate(all_results),
    }
    Path(args.out).write_text(json.dumps(out, indent=2))
    print(f"# wrote {args.out}", file=sys.stderr)
    if args.repeat > 1:
        for p in passes:
            agg = p["aggregate"]
            rs = agg.get("resolve_ms")
            mean = rs["mean"] if rs else "n/a"
            print(f"# pass {p['pass']} aggregate: mean_resolve_ms={mean}")
    print(json.dumps(out["aggregate"], indent=2))


def _delta(before: int, after: int) -> str:
    if before == 0:
        return "n/a"
    pct = (after - before) / before * 100
    arrow = "down" if after < before else "up"
    return f"{arrow} {abs(pct):5.1f}%"


def cmd_compare(args: argparse.Namespace) -> None:
    a = json.loads(Path(args.before).read_text())
    b = json.loads(Path(args.after).read_text())
    aa, bb = a["aggregate"], b["aggregate"]
    print(f"# {a['label']} (before)  -->  {b['label']} (after)")
    print(
        f"  n          {aa['n']:>6d}     ->  {bb['n']:>6d}\n"
        f"  n_ok       {aa['n_ok']:>6d}     ->  {bb['n_ok']:>6d}\n"
        f"  success    {aa['success_rate']:>6.3f}     ->  {bb['success_rate']:>6.3f}"
    )
    for key in ("resolve_ms", "fetch_ms"):
        if not aa.get(key) or not bb.get(key):
            continue
        print(f"  --- {key} ---")
        for sub in ("mean", "p50", "p95", "max"):
            av, bv = aa[key][sub], bb[key][sub]
            print(f"    {sub:6s}  {av:>6d} ms  ->  {bv:>6d} ms   ({_delta(av, bv)})")
    print(
        f"  wallclock  {a['wallclock_seconds']:>6d} s   ->  "
        f"{b['wallclock_seconds']:>6d} s   ({_delta(a['wallclock_seconds'], b['wallclock_seconds'])})"
    )


def main() -> None:
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="run bench")
    r.add_argument("--md5-file", required=True, type=Path)
    r.add_argument("--out", required=True, type=Path)
    r.add_argument("--label", default="bench")
    r.add_argument(
        "--fetch-bytes",
        type=int,
        default=0,
        help="fetch first N bytes of resolved URL (0 = skip fetch step)",
    )
    r.add_argument(
        "--repeat",
        type=int,
        default=1,
        help="iterate the md5 list N times (used to exercise the URL cache)",
    )
    r.add_argument(
        "--workers",
        type=int,
        default=1,
        help="run N resolves concurrently per pass via ProcessPoolExecutor",
    )
    r.set_defaults(func=cmd_run)
    c = sub.add_parser("compare", help="compare two bench json files")
    c.add_argument("before", type=Path)
    c.add_argument("after", type=Path)
    c.set_defaults(func=cmd_compare)
    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
