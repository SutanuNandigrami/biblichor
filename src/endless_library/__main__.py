"""endless-library CLI."""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path

from endless_library import __version__
from endless_library.bench import format_table, load_queries, run_bench
from endless_library.config import load_config
from endless_library.pipeline import PipelineDeps, poll_sources, process_queue
from endless_library.scheduler import build_scheduler


def _setup_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def _resolve_paths(args: argparse.Namespace) -> tuple[Path, Path]:
    config_path = Path(args.config or os.environ.get("CONFIG_PATH", "config/config.yaml"))
    db_path = Path(args.db or os.environ.get("LIBRARY_DB", "data/library.db"))
    return config_path, db_path


def cmd_status(args):
    config_path, db_path = _resolve_paths(args)
    cfg = load_config(config_path)
    deps = PipelineDeps.build(cfg=cfg, db_path=db_path)
    by_status: dict[str, int] = {}
    for b in deps.books.pending(max_attempts=10_000):
        by_status[b.status] = by_status.get(b.status, 0) + 1
    print("queue status:")
    for s, n in sorted(by_status.items()):
        print(f"  {s:<14} {n}")


def cmd_run_once(args):
    config_path, db_path = _resolve_paths(args)
    cfg = load_config(config_path)
    _setup_logging(cfg.general.log_level)
    deps = PipelineDeps.build(cfg=cfg, db_path=db_path)
    poll_sources(deps)
    tally = process_queue(deps)
    print(f"done: {tally}")


def cmd_run(args):
    config_path, db_path = _resolve_paths(args)
    cfg = load_config(config_path)
    _setup_logging(cfg.general.log_level)
    sched, _deps = build_scheduler(cfg, db_path)
    sched.start()
    print(f"endless-library {__version__} running, jobs:")
    for j in sched.get_jobs():
        print(f"  {j.id:<10} next={j.next_run_time}")
    loop = asyncio.get_event_loop()
    try:
        loop.run_forever()
    except KeyboardInterrupt:
        sched.shutdown()


def cmd_send(args):
    """Send an arbitrary file to Kindle via configured SMTP."""
    from pathlib import Path as _Path

    from endless_library.kindle import KindleSendError, send_to_kindle

    config_path, _ = _resolve_paths(args)
    cfg = load_config(config_path)
    _setup_logging(cfg.general.log_level)
    fp = _Path(args.path).expanduser()
    if not fp.exists():
        print(f"file not found: {fp}", file=sys.stderr)
        return 1
    title = args.title or fp.stem
    try:
        r = send_to_kindle(
            attachment=fp,
            kindle=cfg.kindle,
            smtp=cfg.smtp,
            title=title,
            author=None,
        )
    except KindleSendError as e:
        print(f"send failed: {e}", file=sys.stderr)
        return 1
    print(f"ok: {r.response}")
    return 0


def cmd_bench(args):
    config_path, db_path = _resolve_paths(args)
    cfg = load_config(config_path)
    _setup_logging(cfg.general.log_level)
    qs, quick = load_queries(Path(args.queries))
    if args.quick:
        qs = [qs[i] for i in quick if i < len(qs)]
    deps = PipelineDeps.build(cfg=cfg, db_path=db_path)
    outcomes = run_bench(cfg, qs, repo=deps.bench)
    print(format_table(outcomes))


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="endless-library", description="Self-hosted books -> kindle")
    p.add_argument("--config", help="Path to config.yaml")
    p.add_argument("--db", help="Path to library.db")
    sub = p.add_subparsers(dest="cmd")
    sub.required = True

    s_run = sub.add_parser("run", help="Start scheduler in foreground")
    s_run.set_defaults(func=cmd_run)

    s_once = sub.add_parser("run-once", help="Single poll+process cycle, then exit")
    s_once.set_defaults(func=cmd_run_once)

    s_status = sub.add_parser("status", help="Print queue summary")
    s_status.set_defaults(func=cmd_status)

    s_send = sub.add_parser("send", help="Send an existing epub/file straight to Kindle")
    s_send.add_argument("path", help="Path to the file to send")
    s_send.add_argument("--title", default=None, help="Override subject/title")
    s_send.set_defaults(func=cmd_send)

    s_bench = sub.add_parser("bench", help="Run scraper benchmark")
    s_bench.add_argument("--queries", default="bench/queries.yaml")
    s_bench.add_argument("--quick", action="store_true")
    s_bench.set_defaults(func=cmd_bench)

    args = p.parse_args(argv)
    return args.func(args) or 0


if __name__ == "__main__":
    sys.exit(main())
