#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STAGE_DIR="$ROOT_DIR/updates/v16.0.3"
BACKUP_DIR="$ROOT_DIR/backup/temporal-action-engine-v16.0.3/$(date -u +%Y%m%dT%H%M%SZ)"
ASSET_NAME="jarvis-assist-spoken-progress-v1.5.1.tar.gz"
ASSET_TARGET="$ROOT_DIR/bridge/app/assets/$ASSET_NAME"
ASSET_EXISTED=false

cd "$ROOT_DIR"
mkdir -p "$BACKUP_DIR"

required_live=(
  bridge/app/main.py
  bridge/app/main_v15.py
  bridge/app/main_v16.py
  bridge/app/task_engine.py
  bridge/app/tone_engine.py
  bridge/Dockerfile
)
for path in "${required_live[@]}"; do
  if [[ ! -f "$path" ]]; then
    echo "[v16.0.3] Required live file is missing: $path" >&2
    echo "[v16.0.3] Install and verify v16.0.1 first." >&2
    exit 1
  fi
done

required_stage=(
  "$STAGE_DIR/bridge/app/main_v16.py"
  "$STAGE_DIR/bridge/app/task_engine.py"
  "$STAGE_DIR/bridge/app/tone_engine.py"
  "$STAGE_DIR/bridge/app/assets/$ASSET_NAME"
  "$STAGE_DIR/bridge/tests/test_task_engine.py"
  "$STAGE_DIR/bridge/tests/test_progress_experience.py"
  "$STAGE_DIR/bridge/Dockerfile"
  "$STAGE_DIR/patch_core_progress_v16_0_3.py"
)
for path in "${required_stage[@]}"; do
  if [[ ! -f "$path" ]]; then
    echo "[v16.0.3] Package file is missing: $path" >&2
    exit 1
  fi
done

cp bridge/app/main.py "$BACKUP_DIR/main.py.before-v16.0.3"
cp bridge/app/main_v16.py "$BACKUP_DIR/main_v16.py.before-v16.0.3"
cp bridge/app/task_engine.py "$BACKUP_DIR/task_engine.py.before-v16.0.3"
cp bridge/app/tone_engine.py "$BACKUP_DIR/tone_engine.py.before-v16.0.3"
cp bridge/Dockerfile "$BACKUP_DIR/bridge_Dockerfile.before-v16.0.3"
cp .env "$BACKUP_DIR/env.before-v16.0.3" 2>/dev/null || true
if [[ -f "$ASSET_TARGET" ]]; then
  ASSET_EXISTED=true
  cp "$ASSET_TARGET" "$BACKUP_DIR/$ASSET_NAME.before-v16.0.3"
fi

rollback() {
  local code=$?
  if [[ $code -ne 0 ]]; then
    echo "[v16.0.3] Installation failed. Restoring the previous files." >&2
    cp "$BACKUP_DIR/main.py.before-v16.0.3" bridge/app/main.py || true
    cp "$BACKUP_DIR/main_v16.py.before-v16.0.3" bridge/app/main_v16.py || true
    cp "$BACKUP_DIR/task_engine.py.before-v16.0.3" bridge/app/task_engine.py || true
    cp "$BACKUP_DIR/tone_engine.py.before-v16.0.3" bridge/app/tone_engine.py || true
    cp "$BACKUP_DIR/bridge_Dockerfile.before-v16.0.3" bridge/Dockerfile || true
    if [[ "$ASSET_EXISTED" == true ]]; then
      mkdir -p "$(dirname "$ASSET_TARGET")"
      cp "$BACKUP_DIR/$ASSET_NAME.before-v16.0.3" "$ASSET_TARGET" || true
    else
      rm -f "$ASSET_TARGET" || true
    fi
    docker compose up -d --build >/dev/null 2>&1 || true
  fi
  exit "$code"
}
trap rollback EXIT

echo "[v16.0.3] Compiling staged source..."
python3 -m py_compile \
  "$STAGE_DIR/bridge/app/task_engine.py" \
  "$STAGE_DIR/bridge/app/tone_engine.py" \
  "$STAGE_DIR/bridge/app/main_v16.py" \
  "$STAGE_DIR/patch_core_progress_v16_0_3.py"

echo "[v16.0.3] Running task and spoken-progress tests..."
PYTHONPATH="$STAGE_DIR/bridge" python3 -m unittest discover \
  -s "$STAGE_DIR/bridge/tests" \
  -p 'test_*.py' \
  -v

echo "[v16.0.3] Applying voice-only progress routing..."
python3 "$STAGE_DIR/patch_core_progress_v16_0_3.py" bridge/app/main.py

echo "[v16.0.3] Installing task dialogue and phrase rotation..."
cp "$STAGE_DIR/bridge/app/task_engine.py" bridge/app/task_engine.py
cp "$STAGE_DIR/bridge/app/tone_engine.py" bridge/app/tone_engine.py
cp "$STAGE_DIR/bridge/app/main_v16.py" bridge/app/main_v16.py
cp "$STAGE_DIR/bridge/tests/test_task_engine.py" bridge/tests/test_task_engine.py
cp "$STAGE_DIR/bridge/tests/test_progress_experience.py" bridge/tests/test_progress_experience.py
cp "$STAGE_DIR/bridge/Dockerfile" bridge/Dockerfile
mkdir -p bridge/app/assets
cp "$STAGE_DIR/bridge/app/assets/$ASSET_NAME" "$ASSET_TARGET"

append_env_default() {
  local key="$1"
  local value="$2"
  touch .env
  if ! grep -qE "^${key}=" .env; then
    printf '%s=%s\n' "$key" "$value" >> .env
  fi
}

append_env_default JARVIS_TASKS_ENABLED true
append_env_default JARVIS_TASKS_POLL_SECONDS 1
append_env_default JARVIS_TASKS_MAX_FUTURE_DAYS 365
append_env_default JARVIS_TASKS_NOTIFY_COMPLETION true
append_env_default JARVIS_TIMEZONE Europe/London

python3 -m py_compile \
  bridge/app/main.py \
  bridge/app/task_engine.py \
  bridge/app/tone_engine.py \
  bridge/app/main_v16.py

echo "[v16.0.3] Rebuilding Jarvis Core..."
docker compose up -d --build

status=""
for _ in $(seq 1 60); do
  if status="$(curl -fsS http://localhost:8000/api/tasks/status 2>/dev/null)"; then
    if printf '%s' "$status" | grep -q '"version":"16.0.3"'; then
      break
    fi
  fi
  status=""
  sleep 1
done

if [[ -z "$status" ]]; then
  echo "[v16.0.3] Jarvis did not report task-engine version 16.0.3." >&2
  docker compose logs --tail=180 jarvis-core >&2 || true
  exit 1
fi

update_check="$(mktemp)"
if ! curl -fsS \
  http://localhost:8000/api/updates/jarvis-assist-spoken-progress-v1.5.1.tar.gz \
  -o "$update_check"; then
  echo "[v16.0.3] Home Assistant update download endpoint failed." >&2
  rm -f "$update_check"
  exit 1
fi
tar -tzf "$update_check" >/dev/null
rm -f "$update_check"

printf '%s\n' "$status"
echo "[v16.0.3] Phrase count: $(PYTHONPATH=bridge python3 -c 'from app.tone_engine import ToneEngine; print(ToneEngine().progress_phrase_count)')"
echo "[v16.0.3] Backup: $BACKUP_DIR"
echo "[v16.0.3] Jarvis Core installation completed"
echo
echo "Next, update Home Assistant Assist with:"
echo "  curl -fsSL http://192.168.1.40:8000/api/updates/$ASSET_NAME -o /tmp/$ASSET_NAME"
echo "  mkdir -p /tmp/jarvis-assist-v1.5.1"
echo "  tar -xzf /tmp/$ASSET_NAME -C /tmp/jarvis-assist-v1.5.1"
echo "  /tmp/jarvis-assist-v1.5.1/tools/install_assist_spoken_progress_v1_5_1.sh /config"
trap - EXIT
