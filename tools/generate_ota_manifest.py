#!/usr/bin/env python3
"""Generate deterministic, validated Jarvis OTA metadata."""

from __future__ import annotations
import argparse
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

VERSION = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:-(alpha|beta)([1-9]\d*))?$")


def channel(version: str) -> str:
    match = VERSION.fullmatch(version)
    if not match:
        raise ValueError("invalid semantic release version")
    return match.group(4) or "stable"


def generate(
    apk: Path, version: str, version_code: int, commit: str, notes: str, published: str
) -> dict[str, object]:
    release_channel = channel(version)
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("commit must be a full lowercase SHA")
    datetime.fromisoformat(published.replace("Z", "+00:00"))
    payload = apk.read_bytes()
    if not payload:
        raise ValueError("APK is empty")
    name = f"jarvis-assistant-v{version}-release.apk"
    return {
        "apkSize": len(payload),
        "apkUrl": f"https://github.com/aaronhall105/jarvis-ai/releases/download/v{version}/{name}",
        "channel": release_channel,
        "commitSha": commit,
        "minRealtimeProtocol": 2,
        "minSdk": 31,
        "publishedAt": published,
        "releaseNotes": notes.strip() or f"Jarvis {version}",
        "schemaVersion": 1,
        "sha256": hashlib.sha256(payload).hexdigest(),
        "tag": f"v{version}",
        "versionCode": version_code,
        "versionName": version,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apk", type=Path, required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--version-code", type=int, required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--notes-file", type=Path, required=True)
    parser.add_argument("--published-at")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    published = args.published_at or datetime.now(timezone.utc).replace(
        microsecond=0
    ).isoformat().replace("+00:00", "Z")
    result = generate(
        args.apk,
        args.version,
        args.version_code,
        args.commit,
        args.notes_file.read_text(),
        published,
    )
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
