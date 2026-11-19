#!/usr/bin/env python3
"""Verify and copy a ShelfWise SQLite database without modifying its source."""
from __future__ import annotations

import argparse
import hashlib
import os
import sqlite3
import sys
import uuid
from pathlib import Path


def connect(path: Path, *, readonly: bool = False) -> sqlite3.Connection:
    if readonly:
        return sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    return sqlite3.connect(path)


def integrity_and_counts(path: Path) -> tuple[dict[str, int], list[str]]:
    with connect(path, readonly=True) as conn:
        result = conn.execute("PRAGMA integrity_check").fetchone()[0]
        if result != "ok":
            raise RuntimeError(f"SQLite integrity check failed for {path}: {result}")
        names = [row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )]
        counts = {name: conn.execute(f' SELECT COUNT(*) FROM "{name}"').fetchone()[0] for name in names}
    return counts, names


def ensure_demo_only_target(path: Path) -> None:
    """Allow replacement only when target contains no registered account data."""
    with connect(path, readonly=True) as conn:
        tables = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )}
        if "users" in tables and conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] != 0:
            raise RuntimeError("Refusing to replace a target that contains registered users.")
        if "sessions" in tables and conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] != 0:
            raise RuntimeError("Refusing to replace a target that contains login sessions.")
        if "profile_photos" in tables and conn.execute("SELECT COUNT(*) FROM profile_photos").fetchone()[0] != 0:
            raise RuntimeError("Refusing to replace a target that contains profile images.")
        if "shops" in tables:
            shops = [row[0] for row in conn.execute("SELECT shop_id FROM shops")]
            if any(shop_id != "demo-shop" for shop_id in shops):
                raise RuntimeError("Refusing to replace a target that contains a non-demo shop.")
        for table in tables - {"shops", "users", "sessions", "profile_photos"}:
            columns = {row[1] for row in conn.execute(f'PRAGMA table_info("{table}")')}
            if "shop_id" in columns:
                invalid = conn.execute(
                    f'SELECT COUNT(*) FROM "{table}" WHERE shop_id<>?', ("demo-shop",)
                ).fetchone()[0]
                if invalid:
                    raise RuntimeError(f"Refusing to replace target with non-demo rows in {table}.")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def migrate(source: Path, target: Path, replace_demo_only: bool) -> None:
    source = source.expanduser().resolve()
    target = target.expanduser().resolve()
    if not source.is_file():
        raise RuntimeError(f"Source database does not exist: {source}")
    if source == target:
        raise RuntimeError("Source and target paths must be different.")
    source_counts, source_tables = integrity_and_counts(source)
    target.parent.mkdir(parents=True, exist_ok=True)

    if target.exists():
        if not replace_demo_only:
            raise RuntimeError("Target already exists. Refusing to overwrite it; use --replace-demo-only only for a verified seed-only deployment database.")
        ensure_demo_only_target(target)
        for suffix in ("-wal", "-shm"):
            if Path(str(target) + suffix).exists():
                raise RuntimeError(f"Target has an active SQLite sidecar ({suffix}); stop the application before migration.")

    temporary = target.with_name(f".{target.name}.migration-{uuid.uuid4().hex}.tmp")
    try:
        with connect(source, readonly=True) as source_conn, sqlite3.connect(temporary) as target_conn:
            source_conn.backup(target_conn)
        os.chmod(temporary, 0o600)
        copied_counts, copied_tables = integrity_and_counts(temporary)
        if source_tables != copied_tables or source_counts != copied_counts:
            raise RuntimeError("Post-copy verification failed: source and destination tables/counts differ.")
        os.replace(temporary, target)
        os.chmod(target, 0o600)
    finally:
        if temporary.exists():
            temporary.unlink()

    print("Migration verified.")
    print(f"Tables copied: {len(source_tables)}")
    print(f"Destination SHA-256: {sha256(target)}")
    print("Row counts:")
    for table in source_tables:
        print(f"  {table}: {source_counts[table]}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="verified local SQLite backup")
    parser.add_argument("target", type=Path, help="destination database on persistent storage")
    parser.add_argument(
        "--replace-demo-only", action="store_true",
        help="replace an existing target only if it contains the seeded demo workspace and no registered users",
    )
    args = parser.parse_args()
    try:
        migrate(args.source, args.target, args.replace_demo_only)
        return 0
    except (OSError, sqlite3.Error, RuntimeError) as error:
        print(f"Migration stopped safely: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
