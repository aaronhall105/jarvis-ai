#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path
import re
import shutil
import sys

VERSION = "17.3.0"
CORE_APP_VERSION = "2.9.0"


def patch_main_v16(path: Path) -> None:
    text = path.read_text(encoding="utf-8")

    required_markers = (
        "from app.realtime_voice import RealtimeVoiceProxy",
        "realtime_voice = RealtimeVoiceProxy.from_environment()",
        '"/api/realtime/voice"',
        "return await core._execute_ai_request(request)",
    )
    missing = [marker for marker in required_markers if marker not in text]
    if missing:
        raise RuntimeError(
            "Jarvis Realtime Voice v17.2.0-r1 base is incomplete; missing: "
            + ", ".join(missing)
        )

    text, count = re.subn(
        r'app\.version = "2\.(?:8\.0|9\.0)"',
        'app.version = "2.9.0"',
        text,
        count=1,
    )
    if count != 1:
        raise RuntimeError("Core application v2.8.0/v2.9.0 marker not found")

    path.write_text(text, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", nargs="?", default=".")
    parser.add_argument("--source-root", required=True)
    args = parser.parse_args()

    root = Path(args.root).resolve()
    source_root = Path(args.source_root).resolve()
    main_v16 = root / "bridge/app/main_v16.py"
    source_proxy = source_root / "bridge/app/realtime_voice.py"
    target_proxy = root / "bridge/app/realtime_voice.py"

    for required in (main_v16, source_proxy):
        if not required.is_file():
            raise RuntimeError(f"Required file not found: {required}")

    current_proxy = target_proxy.read_text(encoding="utf-8") if target_proxy.is_file() else ""
    if 'VERSION = "17.2.0-r1"' not in current_proxy and 'VERSION = "17.3.0"' not in current_proxy:
        raise RuntimeError("Jarvis Realtime Voice v17.2.0-r1 base was not found")

    shutil.copy2(source_proxy, target_proxy)
    patch_main_v16(main_v16)

    print(
        {
            "version": VERSION,
            "core_application_version": CORE_APP_VERSION,
            "realtime_proxy": str(target_proxy),
            "unified_brain": True,
            "voice_selector": True,
            "wake_phrase_mode": True,
        }
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"[v17.3.0] Patch failed: {exc}", file=sys.stderr)
        raise
