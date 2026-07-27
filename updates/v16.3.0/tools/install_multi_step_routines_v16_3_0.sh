#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STAGE_DIR="$ROOT_DIR/updates/v16.3.0"
BACKUP_DIR="$ROOT_DIR/backup/multi-step-routines-v16.3.0/$(date -u +%Y%m%dT%H%M%SZ)"
cd "$ROOT_DIR"
mkdir -p "$BACKUP_DIR"

required=(
  "$STAGE_DIR/bridge/app/__init__.py"
  "$STAGE_DIR/bridge/app/main_v16.py"
  "$STAGE_DIR/bridge/app/task_engine.py"
  "$STAGE_DIR/bridge/app/recurring_schedule_engine.py"
  "$STAGE_DIR/bridge/app/conditional_action_engine.py"
  "$STAGE_DIR/bridge/app/routine_engine.py"
  "$STAGE_DIR/bridge/app/capability_grounding.py"
  "$STAGE_DIR/bridge/app/tone_engine.py"
  "$STAGE_DIR/bridge/tests/test_capability_grounding.py"
  "$STAGE_DIR/bridge/tests/test_progress_experience.py"
  "$STAGE_DIR/bridge/tests/test_task_engine.py"
  "$STAGE_DIR/bridge/tests/test_recurring_schedule_engine.py"
  "$STAGE_DIR/bridge/tests/test_conditional_action_engine.py"
  "$STAGE_DIR/bridge/tests/test_routine_engine.py"
  "$STAGE_DIR/release/CHANGES_V16_3_0.md"
  "$STAGE_DIR/release/INSTALL_V16_3_0.md"
  "$STAGE_DIR/release/MANIFEST_V16_3_0.json"
  "$STAGE_DIR/release/TESTED_V16_3_0.md"
  "$STAGE_DIR/docs/MULTI_STEP_ROUTINES_V16_3_0.md"
  "$STAGE_DIR/tests/test_release_package.py"
  "$ROOT_DIR/bridge/app/main_v16.py"
  "$ROOT_DIR/bridge/app/task_engine.py"
  "$ROOT_DIR/bridge/app/recurring_schedule_engine.py"
)
for path in "${required[@]}"; do
  [[ -f "$path" ]] || { echo "[v16.3.0] Missing package file: $path" >&2; exit 1; }
done

base_task_status="$(curl -fsS http://localhost:8000/api/tasks/status 2>/dev/null || true)"
base_schedule_status="$(curl -fsS http://localhost:8000/api/schedules/status 2>/dev/null || true)"
base_condition_status="$(curl -fsS http://localhost:8000/api/conditions/status 2>/dev/null || true)"

if ! printf '%s' "$base_task_status" | grep -Eq '"version"[[:space:]]*:[[:space:]]*"16\.(1\.0|2\.0|3\.0)"'; then
  echo "[v16.3.0] Jarvis Task Engine v16.1.0 or later is required." >&2
  printf '%s\n' "$base_task_status" >&2
  exit 1
fi
if ! printf '%s' "$base_schedule_status" | grep -Eq '"version"[[:space:]]*:[[:space:]]*"16\.1\.0"'; then
  echo "[v16.3.0] Recurring Schedule Engine v16.1.0 is required." >&2
  printf '%s\n' "$base_schedule_status" >&2
  exit 1
fi
if [[ -n "$base_condition_status" ]] \
  && ! printf '%s' "$base_condition_status" | grep -Eq '"version"[[:space:]]*:[[:space:]]*"16\.2\.0"'; then
  echo "[v16.3.0] The existing Conditional Action Engine has an unsupported version." >&2
  printf '%s\n' "$base_condition_status" >&2
  exit 1
fi

for path in \
  bridge/app/main_v16.py \
  bridge/app/task_engine.py \
  bridge/app/recurring_schedule_engine.py \
  bridge/app/conditional_action_engine.py \
  bridge/app/routine_engine.py \
  bridge/tests/test_capability_grounding.py \
  bridge/tests/test_progress_experience.py \
  bridge/tests/test_task_engine.py \
  bridge/tests/test_recurring_schedule_engine.py \
  bridge/tests/test_conditional_action_engine.py \
  bridge/tests/test_routine_engine.py; do
  if [[ -e "$path" ]]; then
    mkdir -p "$BACKUP_DIR/$(dirname "$path")"
    cp -a "$path" "$BACKUP_DIR/$path"
  fi
done

rollback() {
  local code=$?
  if [[ $code -ne 0 ]]; then
    echo "[v16.3.0] Installation failed. Backups are in $BACKUP_DIR" >&2
  fi
  exit "$code"
}
trap rollback EXIT

echo "[v16.3.0] Compiling cumulative multi-step source..."
PYTHONPATH="$STAGE_DIR/bridge" python3 -m py_compile \
  "$STAGE_DIR/bridge/app/main_v16.py" \
  "$STAGE_DIR/bridge/app/task_engine.py" \
  "$STAGE_DIR/bridge/app/recurring_schedule_engine.py" \
  "$STAGE_DIR/bridge/app/conditional_action_engine.py" \
  "$STAGE_DIR/bridge/app/routine_engine.py"

echo "[v16.3.0] Running 102 staged dependency-free Core tests..."
for test_file in \
  test_capability_grounding.py \
  test_progress_experience.py \
  test_task_engine.py \
  test_recurring_schedule_engine.py \
  test_conditional_action_engine.py \
  test_routine_engine.py; do
  PYTHONPATH="$STAGE_DIR/bridge" python3 \
    "$STAGE_DIR/bridge/tests/$test_file" -v
done

find "$STAGE_DIR" -type d -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null || true
find "$STAGE_DIR" -type f -name '*.pyc' -delete 2>/dev/null || true
python3 "$STAGE_DIR/tests/test_release_package.py"

echo "[v16.3.0] Installing cumulative Conditional Actions and Routine source..."
cp "$STAGE_DIR/bridge/app/main_v16.py" bridge/app/main_v16.py
cp "$STAGE_DIR/bridge/app/task_engine.py" bridge/app/task_engine.py
cp "$STAGE_DIR/bridge/app/recurring_schedule_engine.py" bridge/app/recurring_schedule_engine.py
cp "$STAGE_DIR/bridge/app/conditional_action_engine.py" bridge/app/conditional_action_engine.py
cp "$STAGE_DIR/bridge/app/routine_engine.py" bridge/app/routine_engine.py

for test_file in \
  test_capability_grounding.py \
  test_progress_experience.py \
  test_task_engine.py \
  test_recurring_schedule_engine.py \
  test_conditional_action_engine.py \
  test_routine_engine.py; do
  cp "$STAGE_DIR/bridge/tests/$test_file" "bridge/tests/$test_file"
done

python3 -m py_compile \
  bridge/app/main_v16.py \
  bridge/app/task_engine.py \
  bridge/app/recurring_schedule_engine.py \
  bridge/app/conditional_action_engine.py \
  bridge/app/routine_engine.py

echo "[v16.3.0] Verifying installed multi-step engine..."
PYTHONPATH="$ROOT_DIR/bridge" python3 \
  "$ROOT_DIR/bridge/tests/test_routine_engine.py" -q

cp "$STAGE_DIR/release/CHANGES_V16_3_0.md" CHANGES_V16_3_0.md
cp "$STAGE_DIR/release/INSTALL_V16_3_0.md" INSTALL_V16_3_0.md
cp "$STAGE_DIR/release/MANIFEST_V16_3_0.json" MANIFEST_V16_3_0.json
cp "$STAGE_DIR/release/TESTED_V16_3_0.md" TESTED_V16_3_0.md
mkdir -p docs
cp "$STAGE_DIR/docs/MULTI_STEP_ROUTINES_V16_3_0.md" \
  docs/MULTI_STEP_ROUTINES_V16_3_0.md
cp "$STAGE_DIR/tools/install_multi_step_routines_v16_3_0.sh" \
  tools/install_multi_step_routines_v16_3_0.sh
chmod +x tools/install_multi_step_routines_v16_3_0.sh

find bridge updates/v16.3.0 tools -type d -name __pycache__ \
  -prune -exec rm -rf {} + 2>/dev/null || true
find bridge updates/v16.3.0 tools -type f -name '*.pyc' -delete 2>/dev/null || true

echo "[v16.3.0] Rebuilding Jarvis Core..."
docker compose up -d --build

task_status=""
schedule_status=""
condition_status=""
routine_status=""
for _ in $(seq 1 60); do
  task_status="$(curl -fsS http://localhost:8000/api/tasks/status 2>/dev/null || true)"
  schedule_status="$(curl -fsS http://localhost:8000/api/schedules/status 2>/dev/null || true)"
  condition_status="$(curl -fsS http://localhost:8000/api/conditions/status 2>/dev/null || true)"
  routine_status="$(curl -fsS http://localhost:8000/api/routines/status 2>/dev/null || true)"
  if printf '%s' "$task_status" | grep -Eq '"version"[[:space:]]*:[[:space:]]*"16\.3\.0"' \
    && printf '%s' "$schedule_status" | grep -Eq '"version"[[:space:]]*:[[:space:]]*"16\.1\.0"' \
    && printf '%s' "$condition_status" | grep -Eq '"version"[[:space:]]*:[[:space:]]*"16\.2\.0"' \
    && printf '%s' "$routine_status" | grep -Eq '"version"[[:space:]]*:[[:space:]]*"16\.3\.0"'; then
    break
  fi
  task_status=""
  schedule_status=""
  condition_status=""
  routine_status=""
  sleep 1
done

if [[ -z "$task_status" || -z "$schedule_status" || -z "$condition_status" || -z "$routine_status" ]]; then
  echo "[v16.3.0] Jarvis did not report all required engines." >&2
  docker compose logs --tail=300 jarvis-core >&2 || true
  exit 1
fi

printf '%s\n' "$task_status"
printf '%s\n' "$schedule_status"
printf '%s\n' "$condition_status"
printf '%s\n' "$routine_status"
echo "[v16.3.0] Backup: $BACKUP_DIR"
echo "[v16.3.0] Installation completed"
echo
echo "Multi-step routines and scenes are ready. Assist remains on cumulative v1.5.4."
trap - EXIT
