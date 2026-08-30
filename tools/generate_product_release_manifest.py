#!/usr/bin/env python3
"""Generate one-source provenance for a unified Jarvis product release."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path


SHA256 = re.compile(r"[0-9a-f]{64}")
SOURCE_SHA = re.compile(r"[0-9a-f]{40}")


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_manifest(
    *,
    phone: Path,
    watch: Path,
    version: str,
    version_code: int,
    source_sha: str,
    signer_sha256: str,
) -> dict[str, object]:
    signer = signer_sha256.casefold().replace(":", "")
    if not phone.is_file() or not watch.is_file():
        raise ValueError("phone and Watch APKs must exist")
    if not SOURCE_SHA.fullmatch(source_sha):
        raise ValueError("source SHA must be an exact lowercase Git commit")
    if not SHA256.fullmatch(signer):
        raise ValueError("signer SHA-256 is invalid")
    if version_code <= 0:
        raise ValueError("versionCode must be positive")
    manifest: dict[str, object] = {
        "schemaVersion": 1,
        "versionName": version,
        "versionCode": version_code,
        "jarvisSourceSha": source_sha,
        "coreSourceSha": source_sha,
        "phoneSourceSha": source_sha,
        "watchSourceSha": source_sha,
        "phone": {
            "file": phone.name,
            "sha256": digest(phone),
            "size": phone.stat().st_size,
        },
        "watch": {
            "file": watch.name,
            "sha256": digest(watch),
            "size": watch.stat().st_size,
        },
        "signerSha256": signer,
    }
    source_fields = (
        "jarvisSourceSha",
        "coreSourceSha",
        "phoneSourceSha",
        "watchSourceSha",
    )
    if len({manifest[field] for field in source_fields}) != 1:
        raise ValueError("product source provenance diverged")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--phone", required=True, type=Path)
    parser.add_argument("--watch", required=True, type=Path)
    parser.add_argument("--version", required=True)
    parser.add_argument("--version-code", required=True, type=int)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--signer-sha256", required=True)
    parser.add_argument("--output", required=True, type=Path)
    arguments = parser.parse_args()
    manifest = build_manifest(
        phone=arguments.phone,
        watch=arguments.watch,
        version=arguments.version,
        version_code=arguments.version_code,
        source_sha=arguments.source_sha,
        signer_sha256=arguments.signer_sha256,
    )
    arguments.output.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
