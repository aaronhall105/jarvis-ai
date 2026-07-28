from __future__ import annotations

from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
PATCHER = ROOT / "tools/patch_jarvis_assistant_v18_1_0.py"

V17_MAIN = '''from typing import Any
app = type("App", (), {})()
app.version = "2.9.0"
core = None

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
'''

V18_MAIN = '''from typing import Any
app = type("App", (), {})()
app.version = "3.0.0"
core = None

# Jarvis v18.0.0 product voice and chat


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


class PatchTests(unittest.TestCase):
    def run_case(self, main_text: str, proxy_version: str) -> str:
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary)
            (target / "bridge/app").mkdir(parents=True)
            (target / "bridge/app/main_v16.py").write_text(main_text, encoding="utf-8")
            (target / "bridge/app/realtime_voice.py").write_text(f'VERSION = "{proxy_version}"\n', encoding="utf-8")
            subprocess.run(
                ["python3", str(PATCHER), str(target), "--source-root", str(ROOT)],
                check=True,
                capture_output=True,
                text=True,
            )
            patched = (target / "bridge/app/main_v16.py").read_text(encoding="utf-8")
            proxy = (target / "bridge/app/realtime_voice.py").read_text(encoding="utf-8")
            self.assertIn('app.version = "3.1.0"', patched)
            self.assertIn("on_delta: Any", patched)
            self.assertIn("on_text_delta=on_delta", patched)
            self.assertIn('VERSION = "18.1.0"', proxy)
            return patched

    def test_patch_upgrades_v1730(self) -> None:
        patched = self.run_case(V17_MAIN, "17.3.0")
        self.assertIn("Jarvis v18.1.0 product voice and chat", patched)

    def test_patch_upgrades_v1800(self) -> None:
        patched = self.run_case(V18_MAIN, "18.0.0")
        self.assertIn("Jarvis v18.1.0 product voice and chat", patched)


if __name__ == "__main__":
    unittest.main(verbosity=2)
