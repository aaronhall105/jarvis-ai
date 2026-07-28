from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path


class PatchIntegrationTests(unittest.TestCase):
    def test_patch_upgrades_v1720_without_touching_home_assistant(self) -> None:
        release = Path(__file__).resolve().parents[1]
        base_root = os.environ.get("JARVIS_TEST_BASE_ROOT", "")

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            app = root / "bridge/app"
            app.mkdir(parents=True)

            if base_root and (Path(base_root) / "bridge/app/main_v16.py").is_file():
                base = Path(base_root)
                (app / "main_v16.py").write_bytes((base / "bridge/app/main_v16.py").read_bytes())
                (app / "realtime_voice.py").write_bytes((base / "bridge/app/realtime_voice.py").read_bytes())
            else:
                (app / "main_v16.py").write_text(
                    '''from app.realtime_voice import RealtimeVoiceProxy\n\n'''
                    '''realtime_voice = RealtimeVoiceProxy.from_environment()\n'''
                    '''app.version = "2.8.0"\n\n'''
                    '''async def _realtime_jarvis_tool(command, metadata):\n'''
                    '''    request = object()\n'''
                    '''    return await core._execute_ai_request(request)\n\n'''
                    '''@app.websocket("/api/realtime/voice")\n'''
                    '''async def realtime_voice_socket(websocket):\n'''
                    '''    await realtime_voice.handle(websocket, _realtime_jarvis_tool)\n''',
                    encoding="utf-8",
                )
                (app / "realtime_voice.py").write_text(
                    'VERSION = "17.2.0-r1"\n',
                    encoding="utf-8",
                )

            subprocess.run(
                [
                    "python3",
                    str(release / "tools/patch_unified_voice_v17_3_0.py"),
                    str(root),
                    "--source-root",
                    str(release),
                ],
                check=True,
            )

            text = (app / "main_v16.py").read_text(encoding="utf-8")
            proxy = (app / "realtime_voice.py").read_text(encoding="utf-8")
            self.assertIn('app.version = "2.9.0"', text)
            self.assertIn('VERSION = "17.3.0"', proxy)
            self.assertIn("create_response\": False", proxy.replace("'", '"'))
            self.assertIn("Jarvis Core", proxy)
            self.assertFalse((root / "home_assistant").exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
