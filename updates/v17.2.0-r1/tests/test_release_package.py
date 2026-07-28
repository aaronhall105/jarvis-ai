from __future__ import annotations

import json
import unittest
from pathlib import Path


class ReleasePackageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.release = Path(__file__).resolve().parents[1]
        cls.android = cls.release / 'android/jarvis-voice-client'

    def test_required_files_exist(self) -> None:
        required = [
            'bridge/app/realtime_voice.py',
            'bridge/tests/test_realtime_voice.py',
            'tools/patch_realtime_voice_v17_2_0_r1.py',
            'tools/install_realtime_voice_v17_2_0_r1.sh',
            '.github/workflows/android-realtime-v17.2.0.yml',
            'android/jarvis-voice-client/app/src/main/java/com/aaron/jarvisvoice/RealtimeAudioEngine.java',
            'android/jarvis-voice-client/app/src/main/java/com/aaron/jarvisvoice/RealtimePlayback.java',
            'android/jarvis-voice-client/app/src/main/java/com/aaron/jarvisvoice/JarvisRealtimeClient.java',
            'android/jarvis-voice-client/app/src/main/java/com/aaron/jarvisvoice/VoiceService.java',
            'release/MANIFEST_V17_2_0_R1.json',
        ]
        missing = [item for item in required if not (self.release / item).is_file()]
        self.assertEqual([], missing)

    def test_android_version_and_permissions(self) -> None:
        build = (self.android / 'app/build.gradle.kts').read_text(encoding='utf-8')
        manifest = (self.android / 'app/src/main/AndroidManifest.xml').read_text(encoding='utf-8')
        self.assertIn('versionCode = 17200', build)
        self.assertIn('versionName = "17.2.0"', build)
        self.assertIn('android.permission.RECORD_AUDIO', manifest)
        self.assertIn('android.permission.WAKE_LOCK', manifest)
        self.assertIn('foregroundServiceType="microphone"', manifest)

    def test_realtime_contract_is_present(self) -> None:
        proxy = (self.release / 'bridge/app/realtime_voice.py').read_text(encoding='utf-8')
        audio = (self.android / 'app/src/main/java/com/aaron/jarvisvoice/RealtimeAudioEngine.java').read_text(encoding='utf-8')
        playback = (self.android / 'app/src/main/java/com/aaron/jarvisvoice/RealtimePlayback.java').read_text(encoding='utf-8')
        self.assertIn('semantic_vad', proxy)
        self.assertIn('interrupt_response', proxy)
        self.assertIn('input_audio_buffer.append', proxy)
        self.assertIn('response.output_audio.delta', proxy)
        self.assertIn('DEFAULT_MODEL = "gpt-realtime"', proxy)
        self.assertNotIn('from websockets.asyncio.client import connect\n', proxy)
        self.assertIn('VOICE_COMMUNICATION', audio)
        self.assertIn('AcousticEchoCanceler', audio)
        self.assertIn('generation.incrementAndGet()', playback)

    def test_manifest_matches_release(self) -> None:
        manifest = json.loads((self.release / 'release/MANIFEST_V17_2_0_R1.json').read_text(encoding='utf-8'))
        self.assertEqual('17.2.0-r1', manifest['version'])
        self.assertEqual('2.8.0', manifest['core_application_version'])
        self.assertFalse(manifest['home_assistant_changes'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
