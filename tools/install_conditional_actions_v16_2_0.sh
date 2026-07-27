#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STAGE_DIR="$ROOT_DIR/updates/v16.2.0"
BACKUP_DIR="$ROOT_DIR/backup/conditional-actions-v16.2.0/$(date -u +%Y%m%dT%H%M%SZ)"
PATCHER="$STAGE_DIR/tools/patch_conditional_actions_v16_2_0.py"
cd "$ROOT_DIR"
mkdir -p "$BACKUP_DIR"

required=(
  "$STAGE_DIR/bridge/app/__init__.py"
  "$STAGE_DIR/bridge/app/main_v16.py"
  "$STAGE_DIR/bridge/app/task_engine.py"
  "$STAGE_DIR/bridge/app/recurring_schedule_engine.py"
  "$STAGE_DIR/bridge/app/conditional_action_engine.py"
  "$STAGE_DIR/bridge/app/capability_grounding.py"
  "$STAGE_DIR/bridge/app/tone_engine.py"
  "$STAGE_DIR/bridge/tests/test_capability_grounding.py"
  "$STAGE_DIR/bridge/tests/test_progress_experience.py"
  "$STAGE_DIR/bridge/tests/test_task_engine.py"
  "$STAGE_DIR/bridge/tests/test_recurring_schedule_engine.py"
  "$STAGE_DIR/bridge/tests/test_conditional_action_engine.py"
  "$PATCHER"
  "$STAGE_DIR/release/CHANGES_V16_2_0.md"
  "$STAGE_DIR/release/INSTALL_V16_2_0.md"
  "$STAGE_DIR/release/MANIFEST_V16_2_0.json"
  "$STAGE_DIR/release/TESTED_V16_2_0.md"
  "$STAGE_DIR/docs/CONDITIONAL_ACTIONS_V16_2_0.md"
  "$STAGE_DIR/tests/test_release_package.py"
  "$ROOT_DIR/bridge/app/main_v16.py"
  "$ROOT_DIR/bridge/app/task_engine.py"
  "$ROOT_DIR/bridge/app/recurring_schedule_engine.py"
)
for path in "${required[@]}"; do
  [[ -f "$path" ]] || { echo "[v16.2.0] Missing package file: $path" >&2; exit 1; }
done

base_task_status="$(curl -fsS http://localhost:8000/api/tasks/status 2>/dev/null || true)"
base_schedule_status="$(curl -fsS http://localhost:8000/api/schedules/status 2>/dev/null || true)"
if ! printf '%s' "$base_task_status" | grep -Eq '"version":"16\.(1\.0|2\.0)"'; then
  echo "[v16.2.0] Jarvis Task Engine must be running v16.1.0 before this upgrade." >&2
  printf '%s\n' "$base_task_status" >&2
  exit 1
fi
if ! printf '%s' "$base_schedule_status" | grep -q '"version":"16.1.0"'; then
  echo "[v16.2.0] Recurring Schedule Engine v16.1.0 is required." >&2
  printf '%s\n' "$base_schedule_status" >&2
  exit 1
fi

for path in \
  bridge/app/main_v16.py \
  bridge/app/task_engine.py \
  bridge/app/recurring_schedule_engine.py \
  bridge/app/conditional_action_engine.py \
  bridge/tests/test_conditional_action_engine.py \
  tools/patch_conditional_actions_v16_2_0.py; do
  if [[ -e "$path" ]]; then
    mkdir -p "$BACKUP_DIR/$(dirname "$path")"
    cp -a "$path" "$BACKUP_DIR/$path"
  fi
done

rollback() {
  local code=$?
  if [[ $code -ne 0 ]]; then
    echo "[v16.2.0] Installation failed. Backups are in $BACKUP_DIR" >&2
  fi
  exit "$code"
}
trap rollback EXIT

echo "[v16.2.0] Compiling conditional action source..."
PYTHONPATH="$STAGE_DIR/bridge" python3 -m py_compile \
  "$STAGE_DIR/bridge/app/main_v16.py" \
  "$STAGE_DIR/bridge/app/task_engine.py" \
  "$STAGE_DIR/bridge/app/recurring_schedule_engine.py" \
  "$STAGE_DIR/bridge/app/conditional_action_engine.py" \
  "$PATCHER"

echo "[v16.2.0] Running 80 staged dependency-free Core tests..."
for test_file in \
  test_capability_grounding.py \
  test_progress_experience.py \
  test_task_engine.py \
  test_recurring_schedule_engine.py \
  test_conditional_action_engine.py; do
  PYTHONPATH="$STAGE_DIR/bridge" python3 \
    "$STAGE_DIR/bridge/tests/$test_file" -v
done
python3 "$STAGE_DIR/tests/test_release_package.py"
python3 "$PATCHER" \
  "$STAGE_DIR/bridge/app/main_v16.py" \
  "$STAGE_DIR/bridge/app/task_engine.py" \
  --check-only

echo "[v16.2.0] Installing cumulative Core source..."
cp "$STAGE_DIR/bridge/app/main_v16.py" bridge/app/main_v16.py
cp "$STAGE_DIR/bridge/app/task_engine.py" bridge/app/task_engine.py
cp "$STAGE_DIR/bridge/app/recurring_schedule_engine.py" bridge/app/recurring_schedule_engine.py
cp "$STAGE_DIR/bridge/app/conditional_action_engine.py" bridge/app/conditional_action_engine.py
cp "$STAGE_DIR/bridge/tests/test_conditional_action_engine.py" \
  bridge/tests/test_conditional_action_engine.py
cp "$PATCHER" tools/patch_conditional_actions_v16_2_0.py
chmod +x tools/patch_conditional_actions_v16_2_0.py

python3 tools/patch_conditional_actions_v16_2_0.py \
  bridge/app/main_v16.py \
  bridge/app/task_engine.py \
  --check-only

python3 -m py_compile \
  bridge/app/main_v16.py \
  bridge/app/task_engine.py \
  bridge/app/recurring_schedule_engine.py \
  bridge/app/conditional_action_engine.py

echo "[v16.2.0] Verifying installed conditional-action module..."
PYTHONPATH="$ROOT_DIR/bridge" python3 \
  "$ROOT_DIR/bridge/tests/test_conditional_action_engine.py" -q

cp "$STAGE_DIR/release/CHANGES_V16_2_0.md" CHANGES_V16_2_0.md
cp "$STAGE_DIR/release/INSTALL_V16_2_0.md" INSTALL_V16_2_0.md
cp "$STAGE_DIR/release/MANIFEST_V16_2_0.json" MANIFEST_V16_2_0.json
cp "$STAGE_DIR/release/TESTED_V16_2_0.md" TESTED_V16_2_0.md
mkdir -p docs
cp "$STAGE_DIR/docs/CONDITIONAL_ACTIONS_V16_2_0.md" \
  docs/CONDITIONAL_ACTIONS_V16_2_0.md

find bridge updates/v16.2.0 tools -type d -name __pycache__ \
  -prune -exec rm -rf {} + 2>/dev/null || true
find bridge updates/v16.2.0 tools -type f -name '*.pyc' -delete 2>/dev/null || true

echo "[v16.2.0] Rebuilding Jarvis Core..."
docker compose up -d --build

task_status=""
schedule_status=""
condition_status=""
for _ in $(seq 1 60); do
  task_status="$(curl -fsS http://localhost:8000/api/tasks/status 2>/dev/null || true)"
  schedule_status="$(curl -fsS http://localhost:8000/api/schedules/status 2>/dev/null || true)"
  condition_status="$(curl -fsS http://localhost:8000/api/conditions/status 2>/dev/null || true)"
  if printf '%s' "$task_status" | grep -q '"version":"16.2.0"' \
    && printf '%s' "$schedule_status" | grep -q '"version":"16.1.0"' \
    && printf '%s' "$condition_status" | grep -q '"version":"16.2.0"'; then
    break
  fi
  task_status=""
  schedule_status=""
  condition_status=""
  sleep 1
done

if [[ -z "$task_status" || -z "$schedule_status" || -z "$condition_status" ]]; then
  echo "[v16.2.0] Jarvis did not report all required engines." >&2
  docker compose logs --tail=260 jarvis-core >&2 || true
  exit 1
fi

printf '%s\n' "$task_status"
printf '%s\n' "$schedule_status"
printf '%s\n' "$condition_status"
echo "[v16.2.0] Backup: $BACKUP_DIR"
echo "[v16.2.0] Installation completed"
echo
echo "Conditional actions are ready. Assist remains on cumulative v1.5.4."
trap - EXIT
