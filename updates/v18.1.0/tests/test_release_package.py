from __future__ import annotations

from pathlib import Path
import json
import unittest

ROOT = Path(__file__).resolve().parents[1]


class ReleasePackageTests(unittest.TestCase):
    def test_required_files_exist(self) -> None:
        required = [
            ".github/workflows/android-jarvis-assistant-v18.1.0.yml",
            "android/jarvis-voice-client/app/src/main/AndroidManifest.xml",
            "android/jarvis-voice-client/app/src/main/res/xml/voice_interaction_service.xml",
            "android/jarvis-voice-client/app/src/main/java/com/aaron/jarvisvoice/JarvisVoiceInteractionService.java",
            "android/jarvis-voice-client/app/src/main/java/com/aaron/jarvisvoice/JarvisVoiceInteractionSession.java",
            "android/jarvis-voice-client/app/src/main/java/com/aaron/jarvisvoice/JarvisVoiceInteractionSessionService.java",
            "android/jarvis-voice-client/app/src/main/java/com/aaron/jarvisvoice/JarvisRecognitionService.java",
            "bridge/app/realtime_voice.py",
            "tools/patch_jarvis_assistant_v18_1_0.py",
            "tools/install_jarvis_assistant_v18_1_0.sh",
            "release/CHANGES_V18_1_0.md",
            "release/INSTALL_V18_1_0.md",
            "release/MANIFEST_V18_1_0.json",
            "release/TESTED_V18_1_0.md",
        ]
        for path in required:
            self.assertTrue((ROOT / path).is_file(), path)

    def test_workflow_builds_one_assistant_artifact(self) -> None:
        workflow = (ROOT / ".github/workflows/android-jarvis-assistant-v18.1.0.yml").read_text(encoding="utf-8")
        self.assertIn("Build Jarvis Assistant v18.1.0", workflow)
        self.assertIn("jarvis-assistant-v18.1.0-debug", workflow)
        self.assertIn("jarvis-voice-debug-keystore-v1730-v1", workflow)

    def test_manifest_and_no_home_assistant_integration_files(self) -> None:
        manifest = json.loads((ROOT / "release/MANIFEST_V18_1_0.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["version"], "18.1.0")
        self.assertTrue(manifest["default_android_assistant"])
        self.assertTrue(manifest["compact_overlay"])
        self.assertTrue(manifest["always_on_wake_host"])
        self.assertIsNone(manifest["fixed_follow_up_timeout_seconds"])
        self.assertFalse(manifest["home_assistant_changes"])
        self.assertFalse((ROOT / "home_assistant").exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
