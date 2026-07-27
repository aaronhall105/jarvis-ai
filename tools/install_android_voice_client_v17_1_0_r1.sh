#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ -d "$SCRIPT_DIR/../updates/v17.1.0-r1" ]; then
  ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
elif [ -d "$SCRIPT_DIR/../../../updates/v17.1.0-r1" ]; then
  ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
else
  echo "Unable to locate updates/v17.1.0-r1 from $SCRIPT_DIR"
  exit 1
fi

STAGE="$ROOT/updates/v17.1.0-r1"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
BACKUP="$ROOT/backup/android-voice-client-v17.1.0-r1/$STAMP"

required=(
  "$STAGE/android/jarvis-voice-client/app/src/main/AndroidManifest.xml"
  "$STAGE/android/jarvis-voice-client/app/src/main/java/com/aaron/jarvisvoice/VoiceService.java"
  "$STAGE/android/jarvis-voice-client/app/src/main/java/com/aaron/jarvisvoice/TranscriptPolicy.java"
  "$STAGE/.github/workflows/android-voice-client-v17.1.0.yml"
  "$STAGE/tests/test_transcript_policy_contract.py"
  "$STAGE/tests/test_release_package.py"
)
for path in "${required[@]}"; do
  test -f "$path" || { echo "Missing staged file: $path"; exit 1; }
done

echo "[v17.1.0-r1] Running dependency-free voice-policy contract tests..."
python3 "$STAGE/tests/test_transcript_policy_contract.py"

echo "[v17.1.0-r1] Running release package validation..."
python3 "$STAGE/tests/test_release_package.py"

if command -v javac >/dev/null 2>&1 && command -v java >/dev/null 2>&1; then
  echo "[v17.1.0-r1] Java detected; running optional compiled policy tests..."
  TMP="$(mktemp -d)"
  trap 'rm -rf "$TMP"' EXIT
  mkdir -p "$TMP/com/aaron/jarvisvoice"
  cp "$STAGE/android/jarvis-voice-client/app/src/main/java/com/aaron/jarvisvoice/TranscriptPolicy.java" "$TMP/com/aaron/jarvisvoice/"
  cp "$STAGE/tests/TranscriptPolicyStandaloneTest.java" "$TMP/com/aaron/jarvisvoice/"
  javac -source 17 -target 17 -d "$TMP/classes" \
    "$TMP/com/aaron/jarvisvoice/TranscriptPolicy.java" \
    "$TMP/com/aaron/jarvisvoice/TranscriptPolicyStandaloneTest.java"
  java -cp "$TMP/classes" com.aaron.jarvisvoice.TranscriptPolicyStandaloneTest
else
  echo "[v17.1.0-r1] Java compiler not installed; skipping optional Java execution test."
  echo "[v17.1.0-r1] This server is an installer host, not the Android build host."
fi

mkdir -p "$BACKUP"
if [ -d "$ROOT/android/jarvis-voice-client" ]; then
  cp -a "$ROOT/android/jarvis-voice-client" "$BACKUP/"
fi
if [ -f "$ROOT/.github/workflows/android-voice-client-v17.1.0.yml" ]; then
  mkdir -p "$BACKUP/.github/workflows"
  cp -a "$ROOT/.github/workflows/android-voice-client-v17.1.0.yml" "$BACKUP/.github/workflows/"
fi

mkdir -p "$ROOT/android" "$ROOT/.github/workflows" "$ROOT/docs"
rm -rf "$ROOT/android/jarvis-voice-client"
cp -a "$STAGE/android/jarvis-voice-client" "$ROOT/android/"
cp -a "$STAGE/.github/workflows/android-voice-client-v17.1.0.yml" "$ROOT/.github/workflows/"
cp -a "$STAGE/release/CHANGES_V17_1_0_R1.md" "$ROOT/"
cp -a "$STAGE/release/INSTALL_V17_1_0_R1.md" "$ROOT/"
cp -a "$STAGE/release/TESTED_V17_1_0_R1.md" "$ROOT/"
cp -a "$STAGE/release/MANIFEST_V17_1_0_R1.json" "$ROOT/"
cp -a "$STAGE/release/ANDROID_VOICE_CLIENT_V17_1_0_R1.md" "$ROOT/docs/"

python3 -m json.tool "$ROOT/MANIFEST_V17_1_0_R1.json" >/dev/null

echo "[v17.1.0-r1] Backup: $BACKUP"
echo "[v17.1.0-r1] Installation completed"
echo "No Docker rebuild or Home Assistant change was made."
echo "The Android project is ready for the GitHub Actions APK build."
