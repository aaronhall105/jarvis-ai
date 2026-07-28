#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path
import shutil

VERSION = "18.1.0"
CORE_APP_VERSION = "3.1.0"

V17_BLOCK = '''# Jarvis v17.2.0-r1 low-latency realtime phone voice


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
'''

V18_BLOCK = '''# Jarvis v18.0.0 product voice and chat


async def _realtime_jarvis_tool(
    command: str,
    metadata: dict[str, Any],
    on_delta: Any,
) -> dict[str, Any]:
    request = core.TextCommandRequest(
        text=command,
        conversation_id=str(metadata.get("conversation_id") or "") or None,
        user_id=str(metadata.get("user_id") or "aaron"),
        user_name=str(metadata.get("user_name") or "Aaron"),
        user_is_admin=bool(metadata.get("user_is_admin", True)),
        device_id=str(metadata.get("device_id") or "jarvis_android"),
        voice_mode=bool(metadata.get("speak", False)),
    )
    return await core._execute_ai_request(request, on_text_delta=on_delta)
'''

V181_BLOCK = V18_BLOCK.replace("v18.0.0", "v18.1.0")


def patch_main_v16(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    base = ""
    if 'app.version = "2.9.0"' in text:
        base = "17.3.0"
        text = text.replace('app.version = "2.9.0"', 'app.version = "3.1.0"', 1)
        if V17_BLOCK not in text:
            raise RuntimeError("v17.3.0 realtime bridge marker was not found")
        text = text.replace(V17_BLOCK, V181_BLOCK, 1)
    elif 'app.version = "3.0.0"' in text:
        base = "18.0.0"
        text = text.replace('app.version = "3.0.0"', 'app.version = "3.1.0"', 1)
        if V18_BLOCK in text:
            text = text.replace(V18_BLOCK, V181_BLOCK, 1)
        elif V181_BLOCK not in text:
            raise RuntimeError("v18 streaming bridge marker was not found")
    elif 'app.version = "3.1.0"' in text:
        base = "18.1.0"
        if V18_BLOCK in text:
            text = text.replace(V18_BLOCK, V181_BLOCK, 1)
    else:
        raise RuntimeError("Jarvis Core v2.9.0/v3.0.0 base was not found")
    path.write_text(text, encoding="utf-8")
    return base


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
    if not any(marker in current_proxy for marker in (
        'VERSION = "17.3.0"',
        'VERSION = "18.0.0"',
        'VERSION = "18.1.0"',
    )):
        raise RuntimeError("Jarvis Unified Voice v17.3.0 or Jarvis Chat v18.0.0 base was not found")

    base = patch_main_v16(main_v16)
    shutil.copy2(source_proxy, target_proxy)
    print({
        "version": VERSION,
        "core_application_version": CORE_APP_VERSION,
        "upgraded_from": base,
        "realtime_proxy": str(target_proxy),
        "default_android_assistant": True,
        "assistant_overlay": True,
        "always_on_wake_host": "voice_interaction_service",
        "chat_ui": True,
    })
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"[v18.1.0] Patch failed: {exc}")
        raise
