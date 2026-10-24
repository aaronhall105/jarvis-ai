"""Conservative release compatibility. Never restore or rewind persistent data.

Initial policy permits asset-only Core changes. Every executable/data-handling
file and dependency must be identical to the operator-provisioned baseline.
New migration mechanisms require a separately reviewed compatibility policy;
a PR description or model assertion is not compatibility evidence.
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
import stat
import tempfile
import time
from pathlib import Path
from urllib.parse import quote

from tools.autonomy_broker import Denied, canonical


def persistence_surface(tree: dict[str, str]) -> dict[str, str]:
    return {
        name: digest
        for name, digest in tree.items()
        if name.startswith("bridge/")
        and not name.startswith(("bridge/app/static/", "bridge/tests/"))
    }


def compatible_code(
    previous: dict[str, str], candidate: dict[str, str], pinned: dict[str, str]
) -> None:
    if not pinned or not any(name.startswith("bridge/app/") for name in pinned):
        raise Denied("compatibility_policy_unprovisioned")
    if persistence_surface(previous) != pinned or persistence_surface(candidate) != pinned:
        raise Denied("unknown_persistence_compatibility")
    allowed = {".html", ".css", ".js", ".svg", ".png", ".jpg", ".jpeg", ".ico", ".woff2"}
    if any(
        Path(name).suffix.lower() not in allowed
        for name in candidate
        if name.startswith("bridge/app/static/")
    ):
        raise Denied("unsupported_core_asset")
    # Assets may change; no new executable source, deleted migration, dependency
    # or Dockerfile can obtain authority by declaring itself backwards-compatible.


def schema_state(directory: Path) -> dict[str, dict[str, int | str]]:
    """Read-only health/schema evidence, no row values or DDL in returned state.

    Directory is a fixed policy-approved persistent mount, never a request path.
    Links/nonregular databases are rejected. Progress handler bounds expensive
    checks. No immutable=1 (which would ignore live WAL), no backup restoration.
    """
    for parent in (directory, *directory.parents):
        if parent.is_symlink() or not parent.is_dir():
            raise Denied("invalid_data_directory")
    files = sorted(directory.glob("*.db"))
    if not files or len(files) > 128:
        raise Denied("database_inventory_unavailable")
    result = {}
    deadline = time.monotonic() + 30
    for path in files:
        if time.monotonic() > deadline:
            raise Denied("database_sample_timeout")
        before = path.lstat()
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise Denied("unsafe_database_path")
        # SQLite must not reopen a mutable host path as root after a symlink
        # check. Read no-follow descriptors into a private, temporary snapshot
        # including WAL; never expose it to tools/containers or retain it as a
        # rollback backup. Any concurrent change rejects the sample.
        with tempfile.TemporaryDirectory(prefix="jarvis-schema-") as scratch:
            captures: list[tuple[Path, os.stat_result | None]] = []
            total = 0
            for source in (path, Path(str(path) + "-wal")):
                if time.monotonic() > deadline:
                    raise Denied("database_sample_timeout")
                try:
                    fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW)
                except FileNotFoundError:
                    captures.append((source, None))
                    continue
                with os.fdopen(fd, "rb") as handle:
                    opened = os.fstat(handle.fileno())
                    if not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1:
                        raise Denied("unsafe_database_path")
                    if opened.st_size > 128 * 1024 * 1024:
                        raise Denied("database_sample_limit")
                    payload = handle.read(128 * 1024 * 1024 + 1)
                    total += len(payload)
                    after_read = os.fstat(handle.fileno())
                    if total > 128 * 1024 * 1024 or (
                        after_read.st_ino,
                        after_read.st_size,
                        after_read.st_mtime_ns,
                        after_read.st_ctime_ns,
                    ) != (opened.st_ino, opened.st_size, opened.st_mtime_ns, opened.st_ctime_ns):
                        raise Denied("database_changed_during_sample")
                    target = Path(scratch) / source.name
                    with target.open("xb") as output:
                        os.fchmod(output.fileno(), 0o600)
                        output.write(payload)
                    captures.append((source, opened))
            for source, captured in captures:
                try:
                    observed = source.lstat()
                except FileNotFoundError:
                    observed = None
                # atime can change on reads; inode/size/mtime/ctime cannot.
                fields = lambda s: (
                    None
                    if s is None
                    else (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns, s.st_nlink)
                )
                if fields(observed) != fields(captured):
                    raise Denied("database_changed_during_sample")
            connection = sqlite3.connect(
                "file:" + quote(str(Path(scratch) / path.name)) + "?mode=ro", uri=True, timeout=2
            )
            try:
                connection.execute("PRAGMA query_only=ON")
                connection.execute("PRAGMA trusted_schema=OFF")
                connection.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
                if connection.execute("PRAGMA quick_check").fetchone() != ("ok",):
                    raise Denied("database_health_failed")
                rows = connection.execute(
                    "SELECT type,name,tbl_name,sql FROM sqlite_schema ORDER BY type,name"
                ).fetchmany(10001)
                if len(rows) > 10000:
                    raise Denied("database_schema_limit")
                result[path.name] = {
                    "user_version": connection.execute("PRAGMA user_version").fetchone()[0],
                    "schema_sha256": hashlib.sha256(canonical(rows)).hexdigest(),
                }
            except sqlite3.Error:
                raise Denied("database_preflight_failed") from None
            finally:
                connection.close()
    return result


def compatible_state(before: dict, after: dict) -> None:
    if not before or before != after:
        raise Denied("schema_changed_reconciliation_required")
