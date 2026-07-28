#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
STAGED="$ROOT/updates/v17.2.0-r1"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
BACKUP_DIR="$ROOT/backup/realtime-voice-v17.2.0-r1/$STAMP"
WORKFLOW_NAME="android-realtime-v17.2.0.yml"
MODIFIED=0
INSTALL_OK=0

cd "$ROOT"

rollback() {
  local code=$?
  trap - ERR
  if [[ "$INSTALL_OK" -eq 1 || "$MODIFIED" -eq 0 ]]; then
    exit "$code"
  fi
  echo '[v17.2.0-r1] Installation failed; restoring the pre-install backup...'
  set +e
  [[ -f "$BACKUP_DIR/bridge/app/main_v16.py" ]] && cp -a "$BACKUP_DIR/bridge/app/main_v16.py" bridge/app/main_v16.py
  if [[ -f "$BACKUP_DIR/bridge/app/realtime_voice.py" ]]; then
    cp -a "$BACKUP_DIR/bridge/app/realtime_voice.py" bridge/app/realtime_voice.py
  else
    rm -f bridge/app/realtime_voice.py
  fi
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
  mkdir -p .github/workflows
  if [[ -f "$BACKUP_DIR/.github/workflows/$WORKFLOW_NAME" ]]; then
    cp -a "$BACKUP_DIR/.github/workflows/$WORKFLOW_NAME" ".github/workflows/$WORKFLOW_NAME"
  else
    rm -f ".github/workflows/$WORKFLOW_NAME"
  fi
  [[ -f "$BACKUP_DIR/.env" ]] && cp -a "$BACKUP_DIR/.env" .env
  docker compose up -d --build >/dev/null 2>&1 || true
  echo "[v17.2.0-r1] Rollback completed: $BACKUP_DIR"
  exit "$code"
}
trap rollback ERR

echo '[v17.2.0-r1] Checking installed Jarvis voice-session base...'
VOICE_STATUS="$(curl -fsS http://localhost:8000/api/voice-sessions/status 2>/dev/null || true)"
if ! printf '%s' "$VOICE_STATUS" | grep -Eq '"version"[[:space:]]*:[[:space:]]*"17\.0\.3"'; then
  echo 'STOP: Jarvis Core Voice Session Engine v17.0.3 must be installed first.'
  echo "$VOICE_STATUS"
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
  echo 'Realtime API usage requires an OpenAI API key with API billing enabled.'
  exit 1
fi

REQUIRED=(
  "$STAGED/bridge/app/realtime_voice.py"
  "$STAGED/bridge/tests/test_realtime_voice.py"
  "$STAGED/tools/patch_realtime_voice_v17_2_0_r1.py"
  "$STAGED/tests/test_patch_integration.py"
  "$STAGED/tests/test_release_package.py"
  "$STAGED/tests/RealtimeStandaloneTest.java"
  "$STAGED/android/jarvis-voice-client/app/src/main/java/com/aaron/jarvisvoice/RealtimeAudioEngine.java"
  "$STAGED/android/jarvis-voice-client/app/src/main/java/com/aaron/jarvisvoice/RealtimePlayback.java"
  "$STAGED/.github/workflows/$WORKFLOW_NAME"
  "$STAGED/release/MANIFEST_V17_2_0_R1.json"
)
for file in "${REQUIRED[@]}"; do
  if [[ ! -f "$file" ]]; then
    echo "STOP: Missing staged release file: $file"
    exit 1
  fi
done

echo '[v17.2.0-r1] Compiling staged Core source...'
python3 -m py_compile \
  "$STAGED/bridge/app/realtime_voice.py" \
  "$STAGED/bridge/tests/test_realtime_voice.py" \
  "$STAGED/tools/patch_realtime_voice_v17_2_0_r1.py" \
  "$STAGED/tests/test_patch_integration.py" \
  "$STAGED/tests/test_release_package.py"

echo '[v17.2.0-r1] Running realtime proxy tests...'
PYTHONPATH="$STAGED/bridge" python3 -S "$STAGED/bridge/tests/test_realtime_voice.py"
JARVIS_TEST_BASE_ROOT="$ROOT" python3 "$STAGED/tests/test_patch_integration.py"
python3 "$STAGED/tests/test_release_package.py"

if command -v javac >/dev/null 2>&1; then
  echo '[v17.2.0-r1] Running dependency-free Java contract tests...'
  JAVA_TMP="$(mktemp -d)"
  javac -d "$JAVA_TMP" \
    "$STAGED/android/jarvis-voice-client/app/src/main/java/com/aaron/jarvisvoice/AudioFrameSizer.java" \
    "$STAGED/android/jarvis-voice-client/app/src/main/java/com/aaron/jarvisvoice/CoreUrl.java" \
    "$STAGED/tests/RealtimeStandaloneTest.java"
  java -cp "$JAVA_TMP" RealtimeStandaloneTest
  rm -rf "$JAVA_TMP"
else
  echo '[v17.2.0-r1] Java compiler not installed; GitHub Actions will run the authoritative Android build.'
fi

mkdir -p \
  "$BACKUP_DIR/bridge/app" \
  "$BACKUP_DIR/bridge/tests" \
  "$BACKUP_DIR/android" \
  "$BACKUP_DIR/.github/workflows"
cp -a bridge/app/main_v16.py "$BACKUP_DIR/bridge/app/main_v16.py"
[[ -f bridge/app/realtime_voice.py ]] && cp -a bridge/app/realtime_voice.py "$BACKUP_DIR/bridge/app/realtime_voice.py"
[[ -f bridge/tests/test_realtime_voice.py ]] && cp -a bridge/tests/test_realtime_voice.py "$BACKUP_DIR/bridge/tests/test_realtime_voice.py"
[[ -d android/jarvis-voice-client ]] && cp -a android/jarvis-voice-client "$BACKUP_DIR/android/jarvis-voice-client"
[[ -f ".github/workflows/$WORKFLOW_NAME" ]] && cp -a ".github/workflows/$WORKFLOW_NAME" "$BACKUP_DIR/.github/workflows/$WORKFLOW_NAME"
cp -a .env "$BACKUP_DIR/.env"
MODIFIED=1

echo '[v17.2.0-r1] Installing Jarvis Core realtime proxy...'
python3 "$STAGED/tools/patch_realtime_voice_v17_2_0_r1.py" "$ROOT" --source-root "$STAGED"
mkdir -p bridge/tests android .github/workflows
cp -a "$STAGED/bridge/tests/test_realtime_voice.py" bridge/tests/test_realtime_voice.py
rm -rf android/jarvis-voice-client
cp -a "$STAGED/android/jarvis-voice-client" android/jarvis-voice-client
cp -a "$STAGED/.github/workflows/$WORKFLOW_NAME" ".github/workflows/$WORKFLOW_NAME"

MOBILE_TOKEN="$(python3 - <<'PY'
from pathlib import Path
import secrets
value = ''
for raw in Path('.env').read_text(encoding='utf-8').splitlines():
    if '=' not in raw:
        continue
    key, candidate = raw.split('=', 1)
    if key.strip() == 'JARVIS_MOBILE_VOICE_TOKEN':
        value = candidate.strip().strip('"').strip("'")
        break
print(value or secrets.token_urlsafe(32))
PY
)"
export MOBILE_TOKEN
python3 - <<'PY'
from pathlib import Path
import os

path = Path('.env')
updates = {
    'JARVIS_REALTIME_ENABLED': 'true',
    'JARVIS_REALTIME_MODEL': 'gpt-realtime',
    'JARVIS_REALTIME_VOICE': 'marin',
    'JARVIS_REALTIME_USER_ID': 'aaron',
    'JARVIS_REALTIME_USER_NAME': 'Aaron',
    'JARVIS_REALTIME_USER_IS_ADMIN': 'true',
    'JARVIS_MOBILE_VOICE_TOKEN': os.environ['MOBILE_TOKEN'],
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
result.append('# Jarvis v17.2.0-r1 realtime phone voice')
for key, value in updates.items():
    if key not in seen:
        result.append(f'{key}={value}')
path.write_text('\n'.join(result).rstrip() + '\n', encoding='utf-8')
PY

python3 -m py_compile bridge/app/main_v16.py bridge/app/realtime_voice.py bridge/tests/test_realtime_voice.py
PYTHONPATH="$ROOT/bridge" python3 -S bridge/tests/test_realtime_voice.py

echo '[v17.2.0-r1] Rebuilding Jarvis Core...'
docker compose up -d --build

READY=0
for _ in $(seq 1 75); do
  STATUS="$(curl -fsS http://localhost:8000/api/realtime/status 2>/dev/null || true)"
  if printf '%s' "$STATUS" | grep -Eq '"version"[[:space:]]*:[[:space:]]*"17\.2\.0-r1"' && \
     printf '%s' "$STATUS" | grep -Eq '"configured"[[:space:]]*:[[:space:]]*true'; then
    READY=1
    break
  fi
  sleep 1
done

if [[ "$READY" -ne 1 ]]; then
  echo 'STOP: Jarvis Realtime Voice v17.2.0-r1 did not become ready.'
  docker compose logs --tail=160 jarvis-core || true
  false
fi

echo '[v17.2.0-r1] Validating production WebSocket dependency inside Jarvis Core...'
docker compose exec -T jarvis-core python - <<'PY'
import websockets
from app.realtime_voice import DEFAULT_MODEL, VERSION
assert VERSION == '17.2.0-r1', VERSION
assert DEFAULT_MODEL == 'gpt-realtime', DEFAULT_MODEL
print({'websockets': websockets.__version__, 'model': DEFAULT_MODEL, 'version': VERSION})
PY

curl -fsS http://localhost:8000/api/realtime/status
echo
INSTALL_OK=1
trap - ERR

echo "[v17.2.0-r1] Mobile voice token: $MOBILE_TOKEN"
echo '[v17.2.0-r1] Jarvis Core URL at home: http://192.168.1.40:8000'
echo "[v17.2.0-r1] Backup: $BACKUP_DIR"
echo '[v17.2.0-r1] Installation completed'
echo 'No Home Assistant files were changed.'
echo 'Push the Android project and workflow to conversation-engine; GitHub Actions will build the v17.2.0 APK.'
