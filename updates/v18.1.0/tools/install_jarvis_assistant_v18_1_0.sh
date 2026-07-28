#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT=""
for candidate in "$SCRIPT_DIR/.." "$SCRIPT_DIR/../../.."; do
  if [[ -d "$candidate/updates/v18.1.0" ]]; then
    ROOT="$(cd "$candidate" && pwd)"
    break
  fi
done
if [[ -z "$ROOT" ]]; then
  echo 'STOP: Could not locate the Jarvis repository root.'
  false
fi

STAGED="$ROOT/updates/v18.1.0"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
BACKUP_DIR="$ROOT/backup/jarvis-assistant-v18.1.0/$STAMP"
NEW_WORKFLOW="android-jarvis-assistant-v18.1.0.yml"
OLD_WORKFLOWS=(
  "android-jarvis-chat-v18.0.0.yml"
  "android-unified-voice-v17.3.0.yml"
  "android-realtime-v17.2.0.yml"
  "android-voice-client-v17.1.0.yml"
)
MODIFIED=0
INSTALL_OK=0

cd "$ROOT"

restore_workflow() {
  local name="$1"
  mkdir -p .github/workflows
  if [[ -f "$BACKUP_DIR/.github/workflows/$name" ]]; then
    cp -a "$BACKUP_DIR/.github/workflows/$name" ".github/workflows/$name"
  else
    rm -f ".github/workflows/$name"
  fi
}

rollback() {
  local code=$?
  trap - ERR
  if [[ "$INSTALL_OK" -eq 1 || "$MODIFIED" -eq 0 ]]; then
    return "$code"
  fi
  echo '[v18.1.0] Installation failed; restoring the pre-install backup...'
  set +e
  [[ -f "$BACKUP_DIR/bridge/app/main_v16.py" ]] && cp -a "$BACKUP_DIR/bridge/app/main_v16.py" bridge/app/main_v16.py
  [[ -f "$BACKUP_DIR/bridge/app/realtime_voice.py" ]] && cp -a "$BACKUP_DIR/bridge/app/realtime_voice.py" bridge/app/realtime_voice.py
  if [[ -f "$BACKUP_DIR/bridge/tests/test_realtime_voice.py" ]]; then
    mkdir -p bridge/tests
    cp -a "$BACKUP_DIR/bridge/tests/test_realtime_voice.py" bridge/tests/test_realtime_voice.py
  fi
  rm -rf android/jarvis-voice-client
  if [[ -d "$BACKUP_DIR/android/jarvis-voice-client" ]]; then
    mkdir -p android
    cp -a "$BACKUP_DIR/android/jarvis-voice-client" android/jarvis-voice-client
  fi
  restore_workflow "$NEW_WORKFLOW"
  for workflow in "${OLD_WORKFLOWS[@]}"; do restore_workflow "$workflow"; done
  docker compose up -d --build >/dev/null 2>&1 || true
  echo "[v18.1.0] Rollback completed: $BACKUP_DIR"
  return "$code"
}
trap rollback ERR

echo '[v18.1.0] Checking installed Jarvis voice base...'
REALTIME_STATUS="$(curl -fsS http://localhost:8000/api/realtime/status 2>/dev/null || true)"
if ! printf '%s' "$REALTIME_STATUS" | grep -Eq '"version"[[:space:]]*:[[:space:]]*"(17\.3\.0|18\.0\.0|18\.1\.0)"'; then
  echo 'STOP: Install Jarvis Unified Voice v17.3.0 or Jarvis Chat v18.0.0 first.'
  echo "$REALTIME_STATUS"
  false
fi

if [[ ! -f .env ]]; then
  echo 'STOP: ~/jarvis/.env was not found.'
  false
fi
if [[ ! -f bridge/requirements.txt ]] || ! grep -Eq '^[[:space:]]*websockets([<>=!~]|$)' bridge/requirements.txt; then
  echo 'STOP: bridge/requirements.txt does not declare websockets.'
  false
fi

REQUIRED=(
  "$STAGED/bridge/app/realtime_voice.py"
  "$STAGED/bridge/tests/test_realtime_voice.py"
  "$STAGED/tools/patch_jarvis_assistant_v18_1_0.py"
  "$STAGED/tests/test_patch_integration.py"
  "$STAGED/tests/test_android_contract.py"
  "$STAGED/tests/test_release_package.py"
  "$STAGED/android/jarvis-voice-client/app/src/main/java/com/aaron/jarvisvoice/JarvisVoiceInteractionService.java"
  "$STAGED/android/jarvis-voice-client/app/src/main/java/com/aaron/jarvisvoice/JarvisVoiceInteractionSession.java"
  "$STAGED/android/jarvis-voice-client/app/src/main/java/com/aaron/jarvisvoice/JarvisVoiceInteractionSessionService.java"
  "$STAGED/android/jarvis-voice-client/app/src/main/java/com/aaron/jarvisvoice/JarvisRecognitionService.java"
  "$STAGED/android/jarvis-voice-client/app/src/main/res/xml/voice_interaction_service.xml"
  "$STAGED/.github/workflows/$NEW_WORKFLOW"
  "$STAGED/release/MANIFEST_V18_1_0.json"
)
for file in "${REQUIRED[@]}"; do
  if [[ ! -f "$file" ]]; then
    echo "STOP: Missing staged release file: $file"
    false
  fi
done

if [[ -d "$STAGED/home_assistant" ]]; then
  echo 'STOP: The v18.1.0 release unexpectedly contains Home Assistant integration files.'
  false
fi

echo '[v18.1.0] Compiling Core and release validation source...'
python3 -m py_compile \
  "$STAGED/bridge/app/realtime_voice.py" \
  "$STAGED/bridge/tests/test_realtime_voice.py" \
  "$STAGED/tools/patch_jarvis_assistant_v18_1_0.py" \
  "$STAGED/tests/test_patch_integration.py" \
  "$STAGED/tests/test_android_contract.py" \
  "$STAGED/tests/test_release_package.py"

python3 "$STAGED/bridge/tests/test_realtime_voice.py"
python3 "$STAGED/tests/test_patch_integration.py"
python3 "$STAGED/tests/test_android_contract.py"
python3 "$STAGED/tests/test_release_package.py"

if command -v javac >/dev/null 2>&1; then
  echo '[v18.1.0] Running dependency-free Java contracts...'
  JAVA_TMP="$(mktemp -d)"
  javac -d "$JAVA_TMP" \
    "$STAGED/android/jarvis-voice-client/app/src/main/java/com/aaron/jarvisvoice/AudioFrameSizer.java" \
    "$STAGED/android/jarvis-voice-client/app/src/main/java/com/aaron/jarvisvoice/ConversationMode.java" \
    "$STAGED/android/jarvis-voice-client/app/src/main/java/com/aaron/jarvisvoice/CoreUrl.java" \
    "$STAGED/android/jarvis-voice-client/app/src/main/java/com/aaron/jarvisvoice/VoiceCatalog.java" \
    "$STAGED/android/jarvis-voice-client/app/src/main/java/com/aaron/jarvisvoice/WakePhrasePolicy.java" \
    "$STAGED/tests/JarvisChatStandaloneTest.java"
  java -cp "$JAVA_TMP" JarvisChatStandaloneTest
  rm -rf "$JAVA_TMP"
else
  echo '[v18.1.0] Java compiler not installed; GitHub Actions will run the authoritative Android build.'
fi

mkdir -p \
  "$BACKUP_DIR/bridge/app" \
  "$BACKUP_DIR/bridge/tests" \
  "$BACKUP_DIR/android" \
  "$BACKUP_DIR/.github/workflows"
cp -a bridge/app/main_v16.py "$BACKUP_DIR/bridge/app/main_v16.py"
cp -a bridge/app/realtime_voice.py "$BACKUP_DIR/bridge/app/realtime_voice.py"
[[ -f bridge/tests/test_realtime_voice.py ]] && cp -a bridge/tests/test_realtime_voice.py "$BACKUP_DIR/bridge/tests/test_realtime_voice.py"
[[ -d android/jarvis-voice-client ]] && cp -a android/jarvis-voice-client "$BACKUP_DIR/android/jarvis-voice-client"
for workflow in "$NEW_WORKFLOW" "${OLD_WORKFLOWS[@]}"; do
  [[ -f ".github/workflows/$workflow" ]] && cp -a ".github/workflows/$workflow" "$BACKUP_DIR/.github/workflows/$workflow"
done
MODIFIED=1

echo '[v18.1.0] Installing default-assistant service, compact overlay and persistent wake host...'
python3 "$STAGED/tools/patch_jarvis_assistant_v18_1_0.py" "$ROOT" --source-root "$STAGED"
mkdir -p bridge/tests android .github/workflows
cp -a "$STAGED/bridge/tests/test_realtime_voice.py" bridge/tests/test_realtime_voice.py
rm -rf android/jarvis-voice-client
cp -a "$STAGED/android/jarvis-voice-client" android/jarvis-voice-client
cp -a "$STAGED/.github/workflows/$NEW_WORKFLOW" ".github/workflows/$NEW_WORKFLOW"
for workflow in "${OLD_WORKFLOWS[@]}"; do rm -f ".github/workflows/$workflow"; done

python3 -m py_compile bridge/app/main_v16.py bridge/app/realtime_voice.py bridge/tests/test_realtime_voice.py
python3 bridge/tests/test_realtime_voice.py

echo '[v18.1.0] Rebuilding Jarvis Core...'
docker compose up -d --build

READY=0
STATUS=''
for _ in $(seq 1 75); do
  STATUS="$(curl -fsS http://localhost:8000/api/realtime/status 2>/dev/null || true)"
  if printf '%s' "$STATUS" | grep -Eq '"version"[[:space:]]*:[[:space:]]*"18\.1\.0"' && \
     printf '%s' "$STATUS" | grep -Eq '"core_application_version"[[:space:]]*:[[:space:]]*"3\.1\.0"' && \
     printf '%s' "$STATUS" | grep -Eq '"configured"[[:space:]]*:[[:space:]]*true' && \
     printf '%s' "$STATUS" | grep -Eq '"android_default_assistant"[[:space:]]*:[[:space:]]*true' && \
     printf '%s' "$STATUS" | grep -Eq '"assistant_overlay"[[:space:]]*:[[:space:]]*true'; then
    READY=1
    break
  fi
  sleep 1
done

if [[ "$READY" -ne 1 ]]; then
  echo 'STOP: Jarvis Assistant v18.1.0 did not become ready.'
  docker compose logs --tail=180 jarvis-core || true
  false
fi

printf '%s\n' "$STATUS"
INSTALL_OK=1
trap - ERR

echo "[v18.1.0] Backup: $BACKUP_DIR"
echo '[v18.1.0] Installation completed'
echo 'No Home Assistant integration files were changed.'
echo 'The mobile and Home Assistant tokens were preserved and were not displayed.'
echo 'Push the v18.1.0 Android project, Core bridge, workflow and release files to conversation-engine.'
