"""Descriptor-relative, bounded source snapshots. No caller paths become mounts.

This module does not assert Git ownership or SHA provenance: the adapter must
verify those separately. It rejects links and non-source entries, rather than
letting root follow caller-controlled paths into the host filesystem.
"""

import hashlib
import os
import stat
from pathlib import Path

from tools.autonomy_broker import Denied, canonical, relative_source

MAX_FILES = 15000
MAX_FILE_BYTES = 8 * 1024 * 1024
MAX_TREE_BYTES = 128 * 1024 * 1024
# This tracked example is documentation, not build input. Do not open or copy
# it: an autonomous worktree might have replaced its contents with credentials.
# All other environment/credential paths still cause rejection.
OMITTED_ROOT_ENTRIES = frozenset({".git", ".env.example"})


def read_source_tree(root: Path, expected_uid: int) -> tuple[dict[str, bytes], str]:
    # Caller must obtain this root from the protected job registry. Each ancestor
    # is traversed without following symlinks; no resolve-then-open race.
    if not root.is_absolute() or ".." in root.parts:
        raise Denied("invalid_workspace")
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in root.parts[1:]:
            next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = next_fd
        if os.fstat(fd).st_uid != expected_uid:
            raise Denied("workspace_owner_changed")
        files: dict[str, bytes] = {}
        total = 0
        visited: set[tuple[int, int]] = set()

        def walk(directory: int, prefix: str, depth: int) -> None:
            nonlocal total
            info = os.fstat(directory)
            inode = (info.st_dev, info.st_ino)
            if depth > 16 or inode in visited or info.st_uid != expected_uid:
                raise Denied("invalid_source_directory")
            visited.add(inode)
            names = sorted(os.listdir(directory))
            if len(names) > MAX_FILES:
                raise Denied("source_budget_exceeded")
            for name in names:
                # Git metadata is never copied into a test/build context.
                if not prefix and name in OMITTED_ROOT_ENTRIES:
                    continue
                path = relative_source(prefix + name)
                before = os.stat(name, dir_fd=directory, follow_symlinks=False)
                if before.st_uid != expected_uid or before.st_dev != info.st_dev:
                    raise Denied("source_owner_or_device_changed")
                if stat.S_ISDIR(before.st_mode):
                    child = os.open(
                        name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory
                    )
                    try:
                        if os.fstat(child).st_ino != before.st_ino:
                            raise Denied("source_changed_during_snapshot")
                        walk(child, path + "/", depth + 1)
                    finally:
                        os.close(child)
                    continue
                if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
                    raise Denied("source_link_or_special_file")
                if before.st_size > MAX_FILE_BYTES or len(files) >= MAX_FILES:
                    raise Denied("source_budget_exceeded")
                child = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
                try:
                    opened = os.fstat(child)
                    if opened != before:
                        raise Denied("source_changed_during_snapshot")
                    chunks = []
                    length = 0
                    while True:
                        chunk = os.read(child, min(65536, MAX_FILE_BYTES + 1 - length))
                        if not chunk:
                            break
                        chunks.append(chunk)
                        length += len(chunk)
                        if length > MAX_FILE_BYTES:
                            raise Denied("source_budget_exceeded")
                    after = os.fstat(child)
                    if (
                        after.st_size != length
                        or after.st_mtime_ns != opened.st_mtime_ns
                        or after.st_ctime_ns != opened.st_ctime_ns
                        or after.st_nlink != 1
                    ):
                        raise Denied("source_changed_during_snapshot")
                finally:
                    os.close(child)
                total += length
                if total > MAX_TREE_BYTES:
                    raise Denied("source_budget_exceeded")
                files[path] = b"".join(chunks)

        walk(fd, "", 0)
        digest = hashlib.sha256(
            canonical(
                {name: hashlib.sha256(data).hexdigest() for name, data in sorted(files.items())}
            )
        ).hexdigest()
        return files, digest
    finally:
        os.close(fd)


def materialize_snapshot(
    parent: Path, name: str, files: dict[str, bytes], owner_uid: int = 0
) -> Path:
    # Root-owned scratch hierarchy, not an existing caller workspace. The name
    # must be a broker-generated hex digest, never supplied by the model.
    if len(name) != 64 or any(c not in "0123456789abcdef" for c in name):
        raise Denied("invalid_snapshot_id")
    for ancestor in (parent, *parent.parents):
        info = ancestor.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != owner_uid or info.st_mode & 0o022:
            raise Denied("unprotected_snapshot_parent")
    target = parent / name
    target.mkdir(mode=0o700)  # Exclusive; an existing snapshot is never overwritten.
    for relative, data in sorted(files.items()):
        safe = relative_source(relative)
        destination = target / safe
        destination.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
        fd = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o444)
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fchmod(handle.fileno(), 0o444)
            os.fsync(handle.fileno())
    target.chmod(0o755)
    return target
