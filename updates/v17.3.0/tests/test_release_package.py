from __future__ import annotations

import json
import unittest
from pathlib import Path


class ReleasePackageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.release = Path(__file__).resolve().parents[1]
        cls.android = cls.release / "android/jarvis-voice-client"

    def test_manifest_matches_release(self) -> None:
        manifest = json.loads((self.release / "release/MANIFEST_V17_3_0.json").read_text(encoding="utf-8"))
        self.assertEqual("17.3.0", manifest["version"])
        self.assertEqual("2.9.0", manifest["core_application_version"])
        self.assertEqual(17300, manifest["android_version_code"])
        self.assertTrue(manifest["architecture"]["jarvis_core_authoritative"])
        self.assertFalse(manifest["architecture"]["automatic_realtime_answers"])
        self.assertTrue(manifest["voices"]["original_home_assistant"])
        self.assertIn("marin", manifest["voices"]["realtime"])
        self.assertTrue(manifest["wake_phrase"]["enabled"])
        self.assertFalse(manifest["home_assistant_changes"])

    def test_required_files_exist(self) -> None:
        required = [
            "bridge/app/realtime_voice.py",
            "bridge/tests/test_realtime_voice.py",
            "tools/patch_unified_voice_v17_3_0.py",
            "tools/install_unified_voice_v17_3_0.sh",
            "tests/test_patch_integration.py",
            "tests/test_release_package.py",
            "tests/UnifiedVoiceStandaloneTest.java",
            ".github/workflows/android-unified-voice-v17.3.0.yml",
            "release/CHANGES_V17_3_0.md",
            "release/INSTALL_V17_3_0.md",
            "release/MANIFEST_V17_3_0.json",
            "release/TESTED_V17_3_0.md",
            "release/UNIFIED_VOICE_V17_3_0.md",
            "android/jarvis-voice-client/app/src/main/java/com/aaron/jarvisvoice/VoiceCatalog.java",
            "android/jarvis-voice-client/app/src/main/java/com/aaron/jarvisvoice/WakePhrasePolicy.java",
            "android/jarvis-voice-client/app/src/main/java/com/aaron/jarvisvoice/WakePhraseEngine.java",
            "android/jarvis-voice-client/app/src/main/java/com/aaron/jarvisvoice/HomeAssistantTtsClient.java",
        ]
        missing = [name for name in required if not (self.release / name).is_file()]
        self.assertEqual([], missing)

    def test_android_version_permissions_and_settings(self) -> None:
        gradle = (self.android / "app/build.gradle.kts").read_text(encoding="utf-8")
        manifest = (self.android / "app/src/main/AndroidManifest.xml").read_text(encoding="utf-8")
        activity = (self.android / "app/src/main/java/com/aaron/jarvisvoice/MainActivity.java").read_text(encoding="utf-8")
        service = (self.android / "app/src/main/java/com/aaron/jarvisvoice/VoiceService.java").read_text(encoding="utf-8")
        self.assertIn("versionCode = 17300", gradle)
        self.assertIn('versionName = "17.3.0"', gradle)
        self.assertIn("android.permission.RECORD_AUDIO", manifest)
        self.assertIn("android.speech.RecognitionService", manifest)
        self.assertIn("Jarvis — Home Assistant original voice", (self.android / "app/src/main/java/com/aaron/jarvisvoice/VoiceCatalog.java").read_text(encoding="utf-8"))
        self.assertIn("Require wake word", activity)
        self.assertIn("Home Assistant long-lived access token", activity)
        self.assertIn("armWakeWord", service)
        self.assertIn("onOriginalTts", service)

    def test_unified_core_contract(self) -> None:
        proxy = (self.release / "bridge/app/realtime_voice.py").read_text(encoding="utf-8")
        self.assertIn('VERSION = "17.3.0"', proxy)
        self.assertIn('CORE_APPLICATION_VERSION = "2.9.0"', proxy)
        self.assertIn('"create_response": False', proxy)
        self.assertIn('"tool_choice": "none"', proxy)
        self.assertIn("brain_handler(command", proxy)
        self.assertIn("Amber", proxy)
        self.assertIn('"type": "original.tts"', proxy)
        self.assertIn("speak_response_event", proxy)

    def test_workflow_builds_single_v1730_artifact(self) -> None:
        workflow = (self.release / ".github/workflows/android-unified-voice-v17.3.0.yml").read_text(encoding="utf-8")
        self.assertIn("Build Jarvis Unified Voice v17.3.0", workflow)
        self.assertIn(":app:testDebugUnitTest :app:assembleDebug", workflow)
        self.assertIn("jarvis-voice-client-v17.3.0-debug.apk", workflow)
        self.assertIn("debug.keystore", workflow)

    def test_release_contains_no_home_assistant_integration_files(self) -> None:
        forbidden = [path for path in self.release.rglob("*") if "home_assistant/custom_components" in path.as_posix()]
        self.assertEqual([], forbidden)


if __name__ == "__main__":
    unittest.main(verbosity=2)
