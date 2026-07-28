from __future__ import annotations

from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "android/jarvis-voice-client/app"
JAVA = APP / "src/main/java/com/aaron/jarvisvoice"


class AndroidContractTests(unittest.TestCase):
    def test_version_and_white_monochrome_theme(self) -> None:
        gradle = (APP / "build.gradle.kts").read_text(encoding="utf-8")
        styles = (APP / "src/main/res/values/styles.xml").read_text(encoding="utf-8")
        self.assertIn('versionCode = 18100', gradle)
        self.assertIn('versionName = "18.1.0"', gradle)
        self.assertIn("Theme.Material.Light.NoActionBar", styles)
        self.assertIn("Theme.JarvisAssistantOverlay", styles)
        self.assertIn('<item name="android:windowLightStatusBar">true</item>', styles)

    def test_default_assistant_manifest_contract(self) -> None:
        manifest = (APP / "src/main/AndroidManifest.xml").read_text(encoding="utf-8")
        metadata = (APP / "src/main/res/xml/voice_interaction_service.xml").read_text(encoding="utf-8")
        self.assertIn("android.service.voice.VoiceInteractionService", manifest)
        self.assertIn("android.permission.BIND_VOICE_INTERACTION", manifest)
        self.assertIn("JarvisVoiceInteractionSessionService", manifest)
        self.assertIn("JarvisRecognitionService", manifest)
        self.assertIn("android.speech.RecognitionService", manifest)
        self.assertIn("JarvisVoiceInteractionSessionService", metadata)
        self.assertIn("JarvisRecognitionService", metadata)
        self.assertIn('android:supportsAssist="true"', metadata)

    def test_overlay_and_side_button_role_setup_are_present(self) -> None:
        settings = (JAVA / "SettingsActivity.java").read_text(encoding="utf-8")
        overlay = (JAVA / "JarvisVoiceInteractionSession.java").read_text(encoding="utf-8")
        service = (JAVA / "JarvisVoiceInteractionService.java").read_text(encoding="utf-8")
        self.assertIn("RoleManager.ROLE_ASSISTANT", settings)
        self.assertIn("Set Jarvis as default assistant", settings)
        self.assertIn("Open chat", overlay)
        self.assertIn("ACTION_ASSISTANT_INVOKE", overlay)
        self.assertIn("showSession", service)

    def test_always_on_wake_host_and_fallback_are_present(self) -> None:
        assistant = (JAVA / "JarvisVoiceInteractionService.java").read_text(encoding="utf-8")
        voice = (JAVA / "VoiceService.java").read_text(encoding="utf-8")
        store = (JAVA / "SecureStore.java").read_text(encoding="utf-8")
        recognizer = (JAVA / "JarvisRecognitionService.java").read_text(encoding="utf-8")
        self.assertIn("WakePhraseEngine", assistant)
        self.assertIn("assistantWakeAlways", assistant)
        self.assertIn("showOverlayIfActive", voice)
        self.assertIn("assistant_wake_always_v1810", store)
        self.assertIn("createOnDeviceSpeechRecognizer", recognizer)

    def test_chat_live_standard_and_no_fixed_timeout_remain(self) -> None:
        main = (JAVA / "MainActivity.java").read_text(encoding="utf-8")
        voice = (JAVA / "VoiceService.java").read_text(encoding="utf-8")
        self.assertIn("Message Jarvis", main)
        self.assertIn("How can I help?", main)
        self.assertNotIn("FOLLOW_UP_MILLIS", voice)
        self.assertIn("ConversationMode.STANDARD", voice)
        self.assertIn("Live voice — listening continuously", voice)


if __name__ == "__main__":
    unittest.main(verbosity=2)
