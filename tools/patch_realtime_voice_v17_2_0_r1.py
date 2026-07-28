#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path
import re
import shutil
import sys

VERSION = "17.2.0-r1"
CORE_APP_VERSION = "2.8.0"


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if new in text:
        return text
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one marker, found {count}")
    return text.replace(old, new, 1)


def patch_main_v16(path: Path) -> None:
    text = path.read_text(encoding="utf-8")

    text = replace_once(
        text,
        "from fastapi import Header, HTTPException\n",
        "from fastapi import Header, HTTPException, WebSocket\n",
        "FastAPI WebSocket import",
    )

    text = replace_once(
        text,
        "from app.voice_session_engine import VoiceSessionEngine\n",
        (
            "from app.voice_session_engine import VoiceSessionEngine\n"
            "from app.realtime_voice import RealtimeVoiceProxy\n"
        ),
        "RealtimeVoiceProxy import",
    )

    voice_marker = '''voice_sessions = VoiceSessionEngine(
    database_path="/app/data/jarvis_voice_sessions.db",
    idle_timeout_seconds=_env_int("JARVIS_VOICE_SESSION_IDLE_SECONDS", 45),
    max_session_seconds=_env_int("JARVIS_VOICE_SESSION_MAX_SECONDS", 300),
)

'''
    text = replace_once(
        text,
        voice_marker,
        voice_marker + "realtime_voice = RealtimeVoiceProxy.from_environment()\n\n",
        "Realtime voice initialisation",
    )

    text, version_count = re.subn(
        r'app\.version = "2\.(?:7\.[0-9]+|8\.0)"',
        'app.version = "2.8.0"',
        text,
        count=1,
    )
    if version_count != 1:
        raise RuntimeError("Core application v2.7.x/v2.8.0 marker not found")

    endpoint_block = r'''

# Jarvis v17.2.0-r1 low-latency realtime phone voice


async def _realtime_jarvis_tool(
    command: str,
    metadata: dict[str, Any],
) -> dict[str, Any]:
    request = core.TextCommandRequest(
        text=command,
        conversation_id=str(metadata.get("conversation_id") or "") or None,
        user_id=str(metadata.get("user_id") or "aaron"),
        user_name=str(metadata.get("user_name") or "Aaron"),
        user_is_admin=bool(metadata.get("user_is_admin", True)),
        device_id=str(metadata.get("device_id") or "jarvis_android"),
        voice_mode=True,
    )
    return await core._execute_ai_request(request)


@app.get("/api/realtime/status")
async def realtime_voice_status() -> dict[str, Any]:
    return realtime_voice.status()


@app.websocket("/api/realtime/voice")
async def realtime_voice_socket(websocket: WebSocket) -> None:
    await realtime_voice.handle(websocket, _realtime_jarvis_tool)
'''
    if '"/api/realtime/voice"' not in text:
        text = text.rstrip() + endpoint_block + "\n"

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

    main_text = main_v16.read_text(encoding="utf-8")
    if 'app.version = "2.7.3"' not in main_text and 'app.version = "2.8.0"' not in main_text:
        raise RuntimeError("Jarvis Core v17.0.3 / application v2.7.3 base was not found")

    shutil.copy2(source_proxy, target_proxy)
    patch_main_v16(main_v16)

    print(
        {
            "version": VERSION,
            "core_application_version": CORE_APP_VERSION,
            "realtime_proxy": str(target_proxy),
            "status_endpoint": "/api/realtime/status",
            "websocket_endpoint": "/api/realtime/voice",
        }
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"[v17.2.0-r1] Patch failed: {exc}", file=sys.stderr)
        raise
