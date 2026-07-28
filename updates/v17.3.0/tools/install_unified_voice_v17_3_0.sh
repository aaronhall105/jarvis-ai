#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT=""
for candidate in "$SCRIPT_DIR/.." "$SCRIPT_DIR/../../.."; do
  if [[ -d "$candidate/updates/v17.3.0" ]]; then
    ROOT="$(cd "$candidate" && pwd)"
    break
  fi
done
if [[ -z "$ROOT" ]]; then
  echo 'STOP: Could not locate the Jarvis repository root.'
  exit 1
fi
STAGED="$ROOT/updates/v17.3.0"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
BACKUP_DIR="$ROOT/backup/unified-voice-v17.3.0/$STAMP"
NEW_WORKFLOW="android-unified-voice-v17.3.0.yml"
OLD_WORKFLOWS=(
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
    exit "$code"
  fi
  echo '[v17.3.0] Installation failed; restoring the pre-install backup...'
  set +e
  [[ -f "$BACKUP_DIR/bridge/app/main_v16.py" ]] && cp -a "$BACKUP_DIR/bridge/app/main_v16.py" bridge/app/main_v16.py
  [[ -f "$BACKUP_DIR/bridge/app/realtime_voice.py" ]] && cp -a "$BACKUP_DIR/bridge/app/realtime_voice.py" bridge/app/realtime_voice.py
  if [[ -f "$BACKUP_DIR/bridge/tests/test_realtime_voice.py" ]]; then
    mkdir -p bridge/tests
    cp -a "$BACKUP_DIR/bridge/tests/test_realtime_voice.py" bridge/tests/test_realtime_voice.py
  else
    rm -f bridge/tests/test_realtime_voice.py
  fi
  rm -rf android/jarvis-voice-client
  if [[ -d "$BACKUP_DIR/android/jarvis-voice-client" ]]; then
    mkdir -p android
    cp -a "$BACKUP_DIR/android/jarvis-voice-client" android/jarvis-voice-client
  fi
  restore_workflow "$NEW_WORKFLOW"
  for workflow in "${OLD_WORKFLOWS[@]}"; do restore_workflow "$workflow"; done
  [[ -f "$BACKUP_DIR/.env" ]] && cp -a "$BACKUP_DIR/.env" .env
  docker compose up -d --build >/dev/null 2>&1 || true
  echo "[v17.3.0] Rollback completed: $BACKUP_DIR"
  exit "$code"
}
trap rollback ERR

echo '[v17.3.0] Checking installed Jarvis Realtime Voice base...'
REALTIME_STATUS="$(curl -fsS http://localhost:8000/api/realtime/status 2>/dev/null || true)"
if ! printf '%s' "$REALTIME_STATUS" | grep -Eq '"version"[[:space:]]*:[[:space:]]*"17\.2\.0-r1"'; then
  echo 'STOP: Jarvis Realtime Voice v17.2.0-r1 must be installed first.'
  echo "$REALTIME_STATUS"
  exit 1
fi

if [[ ! -f .env ]]; then
  echo 'STOP: ~/jarvis/.env was not found.'
  exit 1
fi

if [[ ! -f bridge/requirements.txt ]] || ! grep -Eq '^[[:space:]]*websockets([<>=!~]|$)' bridge/requirements.txt; then
  echo "STOP: bridge/requirements.txt does not declare the required websockets package."
  exit 1
fi

OPENAI_KEY_PRESENT="$(python3 - <<'PY'
from pathlib import Path
value = ''
for raw in Path('.env').read_text(encoding='utf-8').splitlines():
    line = raw.strip()
    if not line or line.startswith('#') or '=' not in line:
        continue
    key, candidate = line.split('=', 1)
    if key.strip() == 'OPENAI_API_KEY':
        value = candidate.strip().strip('"').strip("'")
        break
print('yes' if value and not value.lower().startswith(('replace', 'change-me', 'your-')) else 'no')
PY
)"
if [[ "$OPENAI_KEY_PRESENT" != 'yes' ]]; then
  echo 'STOP: OPENAI_API_KEY is missing or empty in ~/jarvis/.env.'
  exit 1
fi

REQUIRED=(
  "$STAGED/bridge/app/realtime_voice.py"
  "$STAGED/bridge/tests/test_realtime_voice.py"
  "$STAGED/tools/patch_unified_voice_v17_3_0.py"
  "$STAGED/tests/test_patch_integration.py"
  "$STAGED/tests/test_release_package.py"
  "$STAGED/tests/UnifiedVoiceStandaloneTest.java"
  "$STAGED/android/jarvis-voice-client/app/src/main/java/com/aaron/jarvisvoice/VoiceCatalog.java"
  "$STAGED/android/jarvis-voice-client/app/src/main/java/com/aaron/jarvisvoice/WakePhraseEngine.java"
  "$STAGED/android/jarvis-voice-client/app/src/main/java/com/aaron/jarvisvoice/HomeAssistantTtsClient.java"
  "$STAGED/.github/workflows/$NEW_WORKFLOW"
  "$STAGED/release/MANIFEST_V17_3_0.json"
)
for file in "${REQUIRED[@]}"; do
  if [[ ! -f "$file" ]]; then
    echo "STOP: Missing staged release file: $file"
    exit 1
  fi
done

echo '[v17.3.0] Compiling staged Core source...'
python3 -m py_compile \
  "$STAGED/bridge/app/realtime_voice.py" \
  "$STAGED/bridge/tests/test_realtime_voice.py" \
  "$STAGED/tools/patch_unified_voice_v17_3_0.py" \
  "$STAGED/tests/test_patch_integration.py" \
  "$STAGED/tests/test_release_package.py"

echo '[v17.3.0] Running unified-brain voice tests...'
PYTHONPATH="$STAGED/bridge" python3 -S "$STAGED/bridge/tests/test_realtime_voice.py"
JARVIS_TEST_BASE_ROOT="$ROOT" python3 "$STAGED/tests/test_patch_integration.py"
python3 "$STAGED/tests/test_release_package.py"

if command -v javac >/dev/null 2>&1; then
  echo '[v17.3.0] Running dependency-free Java voice and wake tests...'
  JAVA_TMP="$(mktemp -d)"
  javac -d "$JAVA_TMP" \
    "$STAGED/android/jarvis-voice-client/app/src/main/java/com/aaron/jarvisvoice/AudioFrameSizer.java" \
    "$STAGED/android/jarvis-voice-client/app/src/main/java/com/aaron/jarvisvoice/CoreUrl.java" \
    "$STAGED/android/jarvis-voice-client/app/src/main/java/com/aaron/jarvisvoice/VoiceCatalog.java" \
    "$STAGED/android/jarvis-voice-client/app/src/main/java/com/aaron/jarvisvoice/WakePhrasePolicy.java" \
    "$STAGED/tests/UnifiedVoiceStandaloneTest.java"
  java -cp "$JAVA_TMP" UnifiedVoiceStandaloneTest
  rm -rf "$JAVA_TMP"
else
  echo '[v17.3.0] Java compiler not installed; GitHub Actions will run the authoritative Android build.'
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
cp -a .env "$BACKUP_DIR/.env"
MODIFIED=1

echo '[v17.3.0] Installing unified Jarvis brain and voice client...'
python3 "$STAGED/tools/patch_unified_voice_v17_3_0.py" "$ROOT" --source-root "$STAGED"
mkdir -p bridge/tests android .github/workflows
cp -a "$STAGED/bridge/tests/test_realtime_voice.py" bridge/tests/test_realtime_voice.py
rm -rf android/jarvis-voice-client
cp -a "$STAGED/android/jarvis-voice-client" android/jarvis-voice-client
cp -a "$STAGED/.github/workflows/$NEW_WORKFLOW" ".github/workflows/$NEW_WORKFLOW"
for workflow in "${OLD_WORKFLOWS[@]}"; do rm -f ".github/workflows/$workflow"; done

python3 - <<'PY'
from pathlib import Path

path = Path('.env')
updates = {
    'JARVIS_REALTIME_ENABLED': 'true',
    'JARVIS_REALTIME_MODEL': 'gpt-realtime',
    'JARVIS_REALTIME_VOICE': 'marin',
    'JARVIS_REALTIME_USER_ID': 'aaron',
    'JARVIS_REALTIME_USER_NAME': 'Aaron',
    'JARVIS_REALTIME_USER_IS_ADMIN': 'true',
    'JARVIS_REALTIME_TRANSCRIPTION_PROMPT': (
        'Private names and smart-home terms may include Aaron, Amber, Jarvis, '
        'Home Assistant, bedroom floodlight, living room, hallway, front door, '
        'Reolink, Frigate, Tamworth and Durham. Preserve names exactly.'
    ),
}
lines = path.read_text(encoding='utf-8').splitlines()
seen = set()
result = []
for raw in lines:
    if '=' in raw and not raw.lstrip().startswith('#'):
        key = raw.split('=', 1)[0].strip()
        if key in updates:
            result.append(f'{key}={updates[key]}')
            seen.add(key)
            continue
    result.append(raw)
if result and result[-1].strip():
    result.append('')
result.append('# Jarvis v17.3.0 unified phone voice')
for key, value in updates.items():
    if key not in seen:
        result.append(f'{key}={value}')
path.write_text('\n'.join(result).rstrip() + '\n', encoding='utf-8')
PY

python3 -m py_compile bridge/app/main_v16.py bridge/app/realtime_voice.py bridge/tests/test_realtime_voice.py
PYTHONPATH="$ROOT/bridge" python3 -S bridge/tests/test_realtime_voice.py

echo '[v17.3.0] Rebuilding Jarvis Core...'
docker compose up -d --build

READY=0
for _ in $(seq 1 75); do
  STATUS="$(curl -fsS http://localhost:8000/api/realtime/status 2>/dev/null || true)"
  if printf '%s' "$STATUS" | grep -Eq '"version"[[:space:]]*:[[:space:]]*"17\.3\.0"' && \
     printf '%s' "$STATUS" | grep -Eq '"configured"[[:space:]]*:[[:space:]]*true' && \
     printf '%s' "$STATUS" | grep -Eq '"unified_brain"[[:space:]]*:[[:space:]]*true' && \
     printf '%s' "$STATUS" | grep -Eq '"automatic_model_answers"[[:space:]]*:[[:space:]]*false'; then
    READY=1
    break
  fi
  sleep 1
done

if [[ "$READY" -ne 1 ]]; then
  echo 'STOP: Jarvis Unified Voice v17.3.0 did not become ready.'
  docker compose logs --tail=180 jarvis-core || true
  false
fi

echo '[v17.3.0] Validating production voice dependency inside Jarvis Core...'
docker compose exec -T jarvis-core python - <<'PY'
import websockets
from app.realtime_voice import VERSION, CORE_APPLICATION_VERSION, SUPPORTED_VOICES
assert VERSION == '17.3.0', VERSION
assert CORE_APPLICATION_VERSION == '2.9.0', CORE_APPLICATION_VERSION
assert 'marin' in SUPPORTED_VOICES and 'cedar' in SUPPORTED_VOICES
print({
    'websockets': websockets.__version__,
    'version': VERSION,
    'core_application_version': CORE_APPLICATION_VERSION,
    'unified_brain': True,
    'voices': len(SUPPORTED_VOICES),
})
PY

curl -fsS http://localhost:8000/api/realtime/status
echo
INSTALL_OK=1
trap - ERR

MOBILE_TOKEN="$(python3 - <<'PY'
from pathlib import Path
value = ''
for raw in Path('.env').read_text(encoding='utf-8').splitlines():
    if '=' not in raw:
        continue
    key, candidate = raw.split('=', 1)
    if key.strip() == 'JARVIS_MOBILE_VOICE_TOKEN':
        value = candidate.strip().strip('"').strip("'")
        break
print(value)
PY
)"

echo "[v17.3.0] Mobile voice token unchanged: $MOBILE_TOKEN"
echo '[v17.3.0] Jarvis Core URL at home: http://192.168.1.40:8000'
echo "[v17.3.0] Backup: $BACKUP_DIR"
echo '[v17.3.0] Installation completed'
echo 'No Home Assistant integration files were changed.'
echo 'The old Android build workflows were removed to prevent duplicate APK builds.'
echo 'Push the Android project, workflow deletion and v17.3.0 release files to conversation-engine.'
