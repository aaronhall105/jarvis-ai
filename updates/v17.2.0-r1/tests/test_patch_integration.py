from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path


class PatchIntegrationTests(unittest.TestCase):
    def test_patch_adds_realtime_endpoints_without_touching_ha(self) -> None:
        release = Path(__file__).resolve().parents[1]
        simulated = Path(os.environ.get('JARVIS_TEST_BASE_ROOT', '/mnt/data/sim_current'))
        if not (simulated / 'bridge/app/main_v16.py').is_file():
            self.skipTest('Jarvis v17.0.3 simulation base not available')
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'bridge/app').mkdir(parents=True)
            for name in ('main.py', 'main_v16.py', 'voice_session_engine.py'):
                (root / 'bridge/app' / name).write_bytes((simulated / 'bridge/app' / name).read_bytes())
            subprocess.run(
                [
                    'python3',
                    str(release / 'tools/patch_realtime_voice_v17_2_0_r1.py'),
                    str(root),
                    '--source-root',
                    str(release),
                ],
                check=True,
            )
            text = (root / 'bridge/app/main_v16.py').read_text(encoding='utf-8')
            self.assertIn('app.version = "2.8.0"', text)
            self.assertIn('/api/realtime/status', text)
            self.assertIn('/api/realtime/voice', text)
            self.assertIn('RealtimeVoiceProxy', text)
            self.assertTrue((root / 'bridge/app/realtime_voice.py').is_file())
            self.assertFalse((root / 'home_assistant').exists())


if __name__ == '__main__':
    unittest.main(verbosity=2)
