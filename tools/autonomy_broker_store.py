"""Protected bounded records shared by broker and its fixed release helper."""

import hashlib
import json
import os
import stat
import tempfile
import time
import fcntl
from pathlib import Path

from tools.autonomy_broker import Denied, canonical, identifier

KINDS = {"jobs", "artifacts", "releases", "handoff", "results"}


class Records:
    def __init__(self, base: Path, owner: int = 0, check_parents: bool = True):
        self.base, self.owner = base, owner
        for path in (base, *base.parents) if check_parents else (base,):
            info = path.lstat()
            if not stat.S_ISDIR(info.st_mode) or info.st_uid != owner or info.st_mode & 0o022:
                raise Denied("unprotected_records")

    def path(self, kind: str, key: str) -> Path:
        if kind not in KINDS:
            raise Denied("invalid_record_kind")
        identifier(key)
        return self.base / (kind + "-" + key + ".json")

    def read(self, kind: str, key: str) -> dict | None:
        path = self.path(kind, key)
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        except FileNotFoundError:
            return None
        with os.fdopen(fd, "rb") as handle:
            info = os.fstat(handle.fileno())
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != self.owner
                or info.st_mode & 0o077
                or info.st_nlink != 1
            ):
                raise Denied("unprotected_record")
            raw = handle.read(262145)
        if len(raw) > 262144:
            raise Denied("record_limit")
        value = json.loads(raw)
        if (
            set(value) != {"payload", "sha256"}
            or hashlib.sha256(canonical(value["payload"])).hexdigest() != value["sha256"]
        ):
            raise Denied("record_integrity")
        return value["payload"]

    def audit(self, kind: str, key: str, digest: str) -> None:
        fd = os.open(self.base / "audit.jsonl", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "r+b") as handle:
            info = os.fstat(handle.fileno())
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != self.owner
                or info.st_nlink != 1
                or info.st_mode & 0o077
            ):
                raise Denied("unprotected_record_audit")
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            if info.st_size > 32 * 1024 * 1024:
                raise Denied("audit_retention_required")
            previous = "0" * 64
            for line in handle:
                item = json.loads(line)
                entry = {k: v for k, v in item.items() if k != "hash"}
                if (
                    entry.get("previous") != previous
                    or hashlib.sha256(canonical(entry)).hexdigest() != item["hash"]
                ):
                    raise Denied("record_audit_chain_invalid")
                previous = item["hash"]
            entry = {
                "kind": kind,
                "id": key,
                "record_sha256": digest,
                "at": int(time.time()),
                "previous": previous,
            }
            handle.write(
                canonical({**entry, "hash": hashlib.sha256(canonical(entry)).hexdigest()}) + b"\n"
            )
            handle.flush()
            os.fsync(handle.fileno())

    def write(self, kind: str, key: str, data: dict, *, new: bool = False) -> None:
        path = self.path(kind, key)
        existing = self.read(kind, key)
        if new and existing is not None:
            raise Denied("record_already_exists")
        raw = canonical({"payload": data, "sha256": hashlib.sha256(canonical(data)).hexdigest()})
        if len(raw) > 262144:
            raise Denied("record_limit")
        fd, temporary = tempfile.mkstemp(prefix=".record-", dir=self.base)
        try:
            with os.fdopen(fd, "wb") as handle:
                os.fchmod(handle.fileno(), 0o600)
                handle.write(raw)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
            self.audit(kind, key, hashlib.sha256(raw).hexdigest())
            directory = os.open(self.base, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            Path(temporary).unlink(missing_ok=True)
