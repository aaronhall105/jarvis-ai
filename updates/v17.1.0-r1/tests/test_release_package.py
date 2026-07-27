from pathlib import Path
import json

root = Path(__file__).resolve().parents[1]
manifest = json.loads((root / "release" / "MANIFEST_V17_1_0_R1.json").read_text())
assert manifest["version"] == "17.1.0-r1"
assert manifest["android_client_version"] == "17.1.0"
assert manifest["home_assistant_change"] is False
assert manifest["jarvis_core_change"] is False
assert manifest["installer_requires_jdk"] is False
for relative in manifest["required_files"]:
    assert (root / relative).is_file(), relative

android_manifest = (root / "android/jarvis-voice-client/app/src/main/AndroidManifest.xml").read_text()
for permission in (
    "android.permission.RECORD_AUDIO",
    "android.permission.FOREGROUND_SERVICE_MICROPHONE",
    "android.permission.INTERNET",
):
    assert permission in android_manifest
assert 'foregroundServiceType="microphone"' in android_manifest

all_source = "\n".join(
    path.read_text(errors="ignore")
    for path in (root / "android/jarvis-voice-client").rglob("*")
    if path.is_file()
)
assert "eyJ0eXAiOiJKV1Qi" not in all_source
assert "Newyork1994" not in all_source
assert "Bearer " in all_source

workflow = (root / ".github/workflows/android-voice-client-v17.1.0.yml").read_text()
assert ":app:testDebugUnitTest" in workflow
assert ":app:assembleDebug" in workflow

installer = (root / "tools/install_android_voice_client_v17_1_0_r1.sh").read_text()
assert "command -v javac" in installer
assert "Java compiler not installed; skipping optional Java execution test" in installer
assert "test_transcript_policy_contract.py" in installer
print({"version": manifest["version"], "required_files": len(manifest["required_files"]), "status": "ok"})
