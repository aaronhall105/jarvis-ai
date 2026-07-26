#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [[ ! -f bridge/app/proactive_orchestrator.py || ! -f bridge/app/main_v15.py || ! -f bridge/Dockerfile.v15 ]]; then
  echo "ERROR: v15 files are missing. Extract the package into ~/jarvis first." >&2
  exit 1
fi

mkdir -p backup/proactive-orchestrator-v15

for path in \
  bridge/Dockerfile \
  bridge/app/proactive_orchestrator.py \
  bridge/app/main_v15.py; do
  if [[ -f "$path" ]]; then
    safe_name="${path//\//_}"
    cp "$path" "backup/proactive-orchestrator-v15/${safe_name}.before-v15"
  fi
done

cp bridge/Dockerfile.v15 bridge/Dockerfile

if [[ ! -f .env ]]; then
  touch .env
  chmod 600 .env
fi

add_env_default() {
  local key="$1"
  local value="$2"
  if ! grep -qE "^${key}=" .env; then
    printf '%s=%s\n' "$key" "$value" >> .env
  fi
}

add_env_default JARVIS_PROACTIVE_ENABLED true
add_env_default JARVIS_PROACTIVE_TARGET living_room
add_env_default JARVIS_TIMEZONE Europe/London
add_env_default JARVIS_PROACTIVE_QUIET_START 22:30
add_env_default JARVIS_PROACTIVE_QUIET_END 07:00
add_env_default JARVIS_PROACTIVE_POLL_SECONDS 5
add_env_default JARVIS_PROACTIVE_COOLDOWN_SECONDS 300
add_env_default JARVIS_PROACTIVE_OPENING_DELAY_SECONDS 300
add_env_default JARVIS_PROACTIVE_CAMERA_OFFLINE_SECONDS 120
add_env_default JARVIS_PROACTIVE_CAMERA_SCAN_SECONDS 30
add_env_default JARVIS_PROACTIVE_ESCALATION_SECONDS 300
add_env_default JARVIS_PROACTIVE_MAX_ESCALATIONS 2
add_env_default JARVIS_PROACTIVE_PROCESS_EXISTING_EVENTS false

python3 -m py_compile \
  bridge/app/proactive_orchestrator.py \
  bridge/app/main_v15.py

if [[ -x .venv-improver/bin/python ]]; then
  PYTHONPATH=bridge .venv-improver/bin/python -m pytest -q \
    bridge/tests/test_proactive_orchestrator.py
elif python3 -c 'import pytest' >/dev/null 2>&1; then
  PYTHONPATH=bridge python3 -m pytest -q \
    bridge/tests/test_proactive_orchestrator.py
else
  echo "WARNING: pytest is unavailable; syntax checks passed but tests were skipped." >&2
fi

docker compose up -d --build

for _ in $(seq 1 30); do
  if curl -fsS http://localhost:8000/health >/dev/null 2>&1; then
    break
  fi
  sleep 2
done

printf '\nJarvis Core health:\n'
curl -fsS http://localhost:8000/health
printf '\n\nProactive Orchestrator status:\n'
curl -fsS http://localhost:8000/api/proactive/status
printf '\n\nInstalled Jarvis Proactive Action Orchestrator v15.\n'
printf 'The first run starts from the newest House Awareness event and will not replay old alerts.\n'
