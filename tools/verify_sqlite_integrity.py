#!/usr/bin/env python3
"""Read-only integrity checks for Jarvis persistent SQLite databases."""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path


def verify_database(path: Path) -> str:
    connection = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True, timeout=30)
    try:
        row = connection.execute("PRAGMA quick_check").fetchone()
    finally:
        connection.close()
    result = str(row[0]) if row else "missing result"
    if result.casefold() != "ok":
        raise RuntimeError(f"{path}: PRAGMA quick_check returned {result!r}")
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    paths = sorted(args.directory.glob("*.db"))
    if not paths:
        raise SystemExit(f"No SQLite databases found in {args.directory}")
    for path in paths:
        verify_database(path)
        print(f"{path.name}: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
