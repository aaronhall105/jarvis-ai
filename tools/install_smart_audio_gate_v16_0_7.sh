#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STAGE_DIR="$ROOT_DIR/updates/v16.0.7"
BACKUP_DIR="$ROOT_DIR/backup/smart-audio-gate-v16.0.7/$(date -u +%Y%m%dT%H%M%SZ)"
ASSET_NAME="jarvis-assist-smart-audio-gate-v1.5.4.tar.gz"
cd "$ROOT_DIR"
mkdir -p "$BACKUP_DIR"

required=(
  "$STAGE_DIR/bridge/app/capability_grounding.py"
  "$STAGE_DIR/bridge/app/main_v16.py"
  "$STAGE_DIR/bridge/app/task_engine.py"
  "$STAGE_DIR/bridge/app/tone_engine.py"
  "$STAGE_DIR/bridge/tests/test_capability_grounding.py"
  "$STAGE_DIR/bridge/tests/test_task_engine.py"
  "$STAGE_DIR/bridge/tests/test_progress_experience.py"
  "$STAGE_DIR/home_assistant/custom_components/jarvis_core_conversation/audio_gate.py"
  "$STAGE_DIR/home_assistant/custom_components/jarvis_core_conversation/closure.py"
  "$STAGE_DIR/home_assistant/custom_components/jarvis_core_conversation/manifest.json"
  "$STAGE_DIR/home_assistant/tests/test_audio_gate.py"
  "$STAGE_DIR/home_assistant/tests/test_streaming.py"
  "$STAGE_DIR/home_assistant/tests/test_conversation_closure.py"
  "$STAGE_DIR/home_assistant/tests/test_release_integrity.py"
  "$STAGE_DIR/home_assistant/tools/install_assist_smart_audio_gate_v1_5_4.sh"
  "$STAGE_DIR/.github/workflows/jarvis-ci.yml"
  "$STAGE_DIR/tests/test_release_package.py"
  "$ROOT_DIR/tools/build_assist_package.sh"
)
for path in "${required[@]}"; do
  [[ -f "$path" ]] || { echo "[v16.0.7] Missing package file: $path" >&2; exit 1; }
done

for path in \
  bridge/app/capability_grounding.py bridge/app/main_v16.py \
  bridge/app/task_engine.py bridge/app/tone_engine.py \
  bridge/tests/test_capability_grounding.py .github/workflows/jarvis-ci.yml \
  home_assistant/custom_components/jarvis_core_conversation home_assistant/tests \
  home_assistant/tools tools/build_assist_package.sh; do
  if [[ -e "$path" ]]; then
    mkdir -p "$BACKUP_DIR/$(dirname "$path")"
    cp -a "$path" "$BACKUP_DIR/$path"
  fi
done

rollback() {
  local code=$?
  if [[ $code -ne 0 ]]; then
    echo "[v16.0.7] Installation failed. Backups are in $BACKUP_DIR" >&2
  fi
  exit "$code"
}
trap rollback EXIT

echo "[v16.0.7] Compiling staged source..."
python3 -m py_compile \
  "$STAGE_DIR/bridge/app/capability_grounding.py" \
  "$STAGE_DIR/bridge/app/main_v16.py" \
  "$STAGE_DIR/bridge/app/task_engine.py" \
  "$STAGE_DIR/bridge/app/tone_engine.py" \
  "$STAGE_DIR/home_assistant/custom_components/jarvis_core_conversation/audio_gate.py"

echo "[v16.0.7] Running Core, capability and audio-gate tests..."
PYTHONPATH="$STAGE_DIR/bridge" python3 -m unittest discover \
  -s "$STAGE_DIR/bridge/tests" -p 'test_*.py' -v
python3 "$STAGE_DIR/home_assistant/tests/test_audio_gate.py"
python3 "$STAGE_DIR/home_assistant/tests/test_streaming.py"
python3 "$STAGE_DIR/home_assistant/tests/test_conversation_closure.py"
python3 "$STAGE_DIR/home_assistant/tests/test_release_integrity.py" "$STAGE_DIR/home_assistant"
python3 "$STAGE_DIR/tests/test_release_package.py"

echo "[v16.0.7] Installing cumulative tracked source..."
cp "$STAGE_DIR/bridge/app/capability_grounding.py" bridge/app/capability_grounding.py
cp "$STAGE_DIR/bridge/app/main_v16.py" bridge/app/main_v16.py
cp "$STAGE_DIR/bridge/app/task_engine.py" bridge/app/task_engine.py
cp "$STAGE_DIR/bridge/app/tone_engine.py" bridge/app/tone_engine.py
cp "$STAGE_DIR/bridge/tests/test_capability_grounding.py" bridge/tests/test_capability_grounding.py
cp "$STAGE_DIR/bridge/tests/test_task_engine.py" bridge/tests/test_task_engine.py
cp "$STAGE_DIR/bridge/tests/test_progress_experience.py" bridge/tests/test_progress_experience.py
mkdir -p .github/workflows
cp "$STAGE_DIR/.github/workflows/jarvis-ci.yml" .github/workflows/jarvis-ci.yml
rm -rf home_assistant/custom_components/jarvis_core_conversation
mkdir -p home_assistant/custom_components home_assistant/tests home_assistant/tools
cp -a "$STAGE_DIR/home_assistant/custom_components/jarvis_core_conversation" home_assistant/custom_components/
cp "$STAGE_DIR/home_assistant/tests/test_audio_gate.py" home_assistant/tests/test_audio_gate.py
cp "$STAGE_DIR/home_assistant/tests/test_streaming.py" home_assistant/tests/test_streaming.py
cp "$STAGE_DIR/home_assistant/tests/test_conversation_closure.py" home_assistant/tests/test_conversation_closure.py
cp "$STAGE_DIR/home_assistant/tests/test_release_integrity.py" home_assistant/tests/test_release_integrity.py
cp "$STAGE_DIR/home_assistant/tools/install_assist_smart_audio_gate_v1_5_4.sh" home_assistant/tools/
chmod +x home_assistant/tools/install_assist_smart_audio_gate_v1_5_4.sh tools/build_assist_package.sh
cp "$STAGE_DIR/release/CHANGES_V16_0_7.md" CHANGES_V16_0_7.md
cp "$STAGE_DIR/release/INSTALL_V16_0_7.md" INSTALL_V16_0_7.md
cp "$STAGE_DIR/release/MANIFEST_V16_0_7.json" MANIFEST_V16_0_7.json
cp "$STAGE_DIR/release/TESTED_V16_0_7.md" TESTED_V16_0_7.md
mkdir -p docs
cp "$STAGE_DIR/docs/SMART_AUDIO_GATE_V16_0_7.md" docs/SMART_AUDIO_GATE_V16_0_7.md

find bridge home_assistant updates/v16.0.7 -type d -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null || true
find bridge home_assistant updates/v16.0.7 -type f -name '*.pyc' -delete 2>/dev/null || true

echo "[v16.0.7] Building cumulative Assist v1.5.4 package..."
./tools/build_assist_package.sh "$ROOT_DIR/dist"
mkdir -p bridge/app/assets
cp "$ROOT_DIR/dist/$ASSET_NAME" "bridge/app/assets/$ASSET_NAME"

python3 -m py_compile bridge/app/capability_grounding.py bridge/app/main_v16.py \
  bridge/app/task_engine.py bridge/app/tone_engine.py

echo "[v16.0.7] Rebuilding Jarvis Core..."
docker compose up -d --build

status=""
for _ in $(seq 1 60); do
  if status="$(curl -fsS http://localhost:8000/api/tasks/status 2>/dev/null)"; then
    if printf '%s' "$status" | grep -q '"version":"16.0.7"'; then break; fi
  fi
  status=""
  sleep 1
done
if [[ -z "$status" ]]; then
  echo "[v16.0.7] Jarvis did not report task-engine version 16.0.7." >&2
  docker compose logs --tail=180 jarvis-core >&2 || true
  exit 1
fi

update_check="$(mktemp)"
curl -fsS "http://localhost:8000/api/updates/$ASSET_NAME" -o "$update_check"
tar -tzf "$update_check" >/dev/null
rm -f "$update_check"

printf '%s\n' "$status"
echo "[v16.0.7] Assist asset: $ROOT_DIR/bridge/app/assets/$ASSET_NAME"
echo "[v16.0.7] Backup: $BACKUP_DIR"
echo "[v16.0.7] Installation completed"
echo
echo "Next, install Assist v1.5.4 in the Home Assistant Terminal using INSTALL_V16_0_7.md."
trap - EXIT
