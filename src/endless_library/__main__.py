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


def cmd_repair_filenames(args):
    """Rename on-disk files using the Unicode-safe safe_filename so
    titles in Bengali / CJK / Cyrillic survive (Phase X.iii).

    Idempotent: re-running on already-fixed files is a no-op."""
    import sqlite3

    from endless_library.download import safe_filename

    config_path, db_path = _resolve_paths(args)
    cfg = load_config(config_path)
    _setup_logging(cfg.general.log_level)

    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    rows = con.execute(
        "SELECT id, title, author, file_path, format FROM books "
        "WHERE file_path IS NOT NULL "
        "AND status IN ('sent','sending','downloading','converting','needs_review','failed')"
    ).fetchall()

    renamed, skipped, missing = 0, 0, 0
    for r in rows:
        fp = Path(r["file_path"])
        if not fp.exists():
            missing += 1
            continue
        ext = fp.suffix.lstrip(".")
        # Re-derive a clean filename from the DB title + author
        title = (r["title"] or "").strip()
        author = (r["author"] or "").strip()
        if author:
            new_name = safe_filename(f"{title} -- {author}.{ext}")
        else:
            new_name = safe_filename(f"{title}.{ext}")
        new_path = fp.parent / new_name
        if new_path == fp:
            skipped += 1
            continue
        if new_path.exists():
            # Disambiguate with id suffix
            stem = new_name[: -len(ext) - 1]
            new_path = fp.parent / f"{stem} ({r['id']}).{ext}"
            if new_path.exists():
                print(f"[skip] id={r['id']}: target exists: {new_path}")
                skipped += 1
                continue
        action = "would rename" if args.dry_run else "renaming"
        print(f"[{action}] id={r['id']}")
        print(f"  from: {fp.name}")
        print(f"  to:   {new_path.name}")
        if not args.dry_run:
            fp.rename(new_path)
            con.execute(
                "UPDATE books SET file_path = ?, updated_at = datetime('now') WHERE id = ?",
                (str(new_path), r["id"]),
            )
            con.commit()
            renamed += 1
    print()
    print(f"summary: renamed={renamed} skipped={skipped} missing_file={missing} total={len(rows)}")
    return 0


def cmd_resend(args):
    """Re-enrich metadata + resend selected books to Kindle (Phase X.iv).

    Use case: previously sent PDFs whose embedded metadata was broken
    (transliterated or filename-derived). The enrich step writes the
    DB'\''s correct title/author into the file via Calibre'\''s
    ebook-meta; then we re-send via the configured SMTP.
    """
    import sqlite3

    from endless_library.convert import ConvertError, enrich_metadata
    from endless_library.kindle import KindleSendError, send_to_kindle

    config_path, db_path = _resolve_paths(args)
    cfg = load_config(config_path)
    _setup_logging(cfg.general.log_level)
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row

    if args.book_ids:
        ids = [int(x) for x in args.book_ids.split(",")]
        placeholders = ",".join(["?"] * len(ids))
        where = f"id IN ({placeholders})"
        params = ids
    elif args.format:
        where = "format = ? AND status = 'sent' AND file_path IS NOT NULL"
        params = [args.format]
    else:
        print("must provide --book-ids or --format", file=sys.stderr)
        return 1

    rows = con.execute(
        f"SELECT id, title, author, isbn13, file_path, format FROM books WHERE {where}",
        params,
    ).fetchall()
    if not rows:
        print("no books match")
        return 1

    print(f"will re-enrich + resend {len(rows)} book(s):")
    for r in rows:
        print(f"  id={r['id']} {r['title']!r} [{r['format']}]")
    if not args.yes:
        confirm = input("proceed? [y/N] ").strip().lower()
        if confirm != "y":
            print("aborted")
            return 0

    ok, failed = 0, 0
    for r in rows:
        fp = Path(r["file_path"])
        if not fp.exists():
            print(f"[skip] id={r['id']}: file missing: {fp}")
            failed += 1
            continue
        # 1. Re-enrich metadata in place
        try:
            enrich_metadata(
                fp,
                title=r["title"],
                author=r["author"],
                isbn=r["isbn13"],
            )
            print(f"[enrich] id={r['id']}: metadata written")
        except ConvertError as e:
            print(f"[enrich-fail] id={r['id']}: {e}", file=sys.stderr)
            # Don'''t bail on enrich failure for the resend
        # 2. Send to Kindle
        try:
            res = send_to_kindle(
                attachment=fp,
                kindle=cfg.kindle,
                smtp=cfg.smtp,
                title=r["title"],
                author=r["author"],
            )
            print(f"[sent]   id={r['id']}: {res.response}")
            con.execute(
                "UPDATE books SET sent_at = datetime('now'), "
                "updated_at = datetime('now') WHERE id = ?",
                (r["id"],),
            )
            con.commit()
            ok += 1
        except KindleSendError as e:
            print(f"[send-fail] id={r['id']}: {e}", file=sys.stderr)
            failed += 1
    print()
    print(f"summary: sent={ok} failed={failed} total={len(rows)}")
    return 0 if failed == 0 else 1


def cmd_storage_migrate(args):
    """Migrate every key from the currently configured storage backend
    to a target backend (Phase 4c). Idempotent + resumable.
    """
    from endless_library.storage.factory import build_store
    from endless_library.storage.migrate import migrate_all

    config_path, db_path = _resolve_paths(args)
    cfg = load_config(config_path)
    _setup_logging(cfg.general.log_level)

    src = build_store(cfg.storage, data_root=Path(cfg.general.books_dir))

    # Build a synthetic StorageCfg for the target so we can construct
    # the dst Store without persisting config until migration succeeds.
    target_cfg = cfg.storage.model_copy(update={"backend": args.to})
    if args.to in ("rclone", "hybrid"):
        if args.rclone_remote:
            target_cfg = target_cfg.model_copy(update={"rclone_remote": args.rclone_remote})
        if args.rclone_bucket_path:
            target_cfg = target_cfg.model_copy(
                update={"rclone_bucket_path": args.rclone_bucket_path}
            )
    dst = build_store(target_cfg, data_root=Path(cfg.general.books_dir))

    def progress(key, n, total):
        print(f"  [{n}/{total}] {key}")

    print(f"migrating {src.name} -> {dst.name}")
    result = migrate_all(
        src, dst, prefix=args.prefix or "", overwrite=args.overwrite, on_progress=progress
    )
    print()
    print(f"summary: total={result.total} copied={result.copied} "
          f"skipped_existing={result.skipped_existing} failed={result.failed}")
    for key, err in result.errors[:10]:
        print(f"  FAIL: {key}: {err}")
    if len(result.errors) > 10:
        print(f"  ... and {len(result.errors) - 10} more failures")
    return 1 if result.failed else 0


def cmd_backup(args):
    """Create a backup bundle and push it via the configured Store
    (Phase 5a)."""
    from endless_library.backup import BackupError, make_backup
    from endless_library.storage.factory import build_store

    config_path, db_path = _resolve_paths(args)
    cfg = load_config(config_path)
    _setup_logging(cfg.general.log_level)

    secrets_path = Path(args.secrets) if args.secrets else None
    library_dir = Path(args.library) if args.library else Path(cfg.general.books_dir)
    if not library_dir.exists():
        library_dir = None  # treat absent library as "config-only backup"

    store = build_store(cfg.storage, data_root=Path(cfg.general.books_dir))
    recipient = args.age_recipient or os.environ.get("BIBLICHOR_AGE_RECIPIENT") or ""

    try:
        result = make_backup(
            db_path=db_path,
            config_path=config_path,
            secrets_path=secrets_path,
            library_dir=library_dir,
            store=store,
            age_recipient=recipient if recipient else None,
            remote_prefix=args.prefix or "backups",
        )
    except BackupError as e:
        print(f"backup failed: {e}", file=sys.stderr)
        return 1

    print(f"backup ok: {result.remote_key} ({result.bytes_written:,} bytes)")
    print(f"  encrypted: {result.manifest.encrypted}")
    print(f"  files: {len(result.manifest.file_checksums)}")
    return 0


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

    s_repair = sub.add_parser("repair-filenames", help="Re-derive on-disk filenames with Unicode-safe sanitization (Phase X.iii)")
    s_repair.add_argument("--dry-run", action="store_true", help="Print what would change, don't rename")
    s_repair.set_defaults(func=cmd_repair_filenames)

    s_resend = sub.add_parser("resend", help="Re-enrich metadata + resend to Kindle (Phase X.iv)")
    s_resend.add_argument("--book-ids", help="Comma-separated book IDs to resend")
    s_resend.add_argument("--format", help="Resend all sent books of this format (e.g. pdf)")
    s_resend.add_argument("--yes", action="store_true", help="Skip the confirmation prompt")
    s_resend.set_defaults(func=cmd_resend)

    s_backup = sub.add_parser("backup", help="Create a disaster-recovery backup (Phase 5a)")
    s_backup.add_argument("--secrets", default="", help="Path to .env (defaults to none)")
    s_backup.add_argument("--library", default="", help="Override library dir (default: general.books_dir)")
    s_backup.add_argument("--age-recipient", default="", help="age public key for encryption")
    s_backup.add_argument("--prefix", default="backups", help="Remote key prefix")
    s_backup.set_defaults(func=cmd_backup)

    s_storage = sub.add_parser("storage", help="Storage management commands (Phase 4c)")
    sub_storage = s_storage.add_subparsers(dest="storage_cmd")
    sub_storage.required = True
    s_migrate = sub_storage.add_parser("migrate", help="Migrate keys to a target storage backend")
    s_migrate.add_argument("--to", required=True, choices=("local", "rclone", "hybrid"))
    s_migrate.add_argument("--rclone-remote", default="", help="Override rclone remote for target")
    s_migrate.add_argument("--rclone-bucket-path", default="", help="Override rclone bucket path")
    s_migrate.add_argument("--prefix", default="", help="Migrate only keys under this prefix")
    s_migrate.add_argument("--overwrite", action="store_true", help="Overwrite existing keys on dst")
    s_migrate.set_defaults(func=cmd_storage_migrate)

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
