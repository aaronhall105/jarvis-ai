#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ "${EUID}" -eq 0 ]]; then
  echo "Run this as the normal Jarvis user, not root." >&2
  exit 1
fi

if [[ ! -d .git ]]; then
  echo "ERROR: $ROOT is not a Git repository." >&2
  exit 1
fi

if [[ ! -f bridge/app/self_improvement.py ]]; then
  echo "ERROR: Extract the v14 package into $ROOT before running this installer." >&2
  exit 1
fi

for command in git docker curl python3 systemctl; do
  if ! command -v "$command" >/dev/null 2>&1; then
    echo "ERROR: Required command is missing: $command" >&2
    exit 1
  fi
done

if ! docker info >/dev/null 2>&1; then
  echo "ERROR: The current user cannot access Docker. Run this as Aaron from the same account that manages Jarvis." >&2
  exit 1
fi

mkdir -p data logs config .jarvis-improver/worktrees .jarvis-improver/artifacts
chmod 700 .jarvis-improver

touch .env .gitignore
for pattern in ".env" ".venv-improver/" ".jarvis-improver/" "data/" "logs/" "backup/" "*.tar.gz" "*.backup" "*.before-*" "*.pyc" "__pycache__/" ".pytest_cache/"; do
  grep -qxF "$pattern" .gitignore || printf '%s\n' "$pattern" >> .gitignore
done

if [[ ! -d .venv-improver ]]; then
  if ! python3 -m venv .venv-improver; then
    echo "ERROR: Python venv support is missing. Install python3-venv, then rerun this installer." >&2
    exit 1
  fi
fi

.venv-improver/bin/python -m pip install --upgrade pip wheel
.venv-improver/bin/python -m pip install -r requirements-improver.txt

if ! grep -q '^JARVIS_SELF_IMPROVEMENT_ADMIN_TOKEN=' .env 2>/dev/null; then
  TOKEN="$(python3 - <<'PY'
import secrets
print(secrets.token_urlsafe(32))
PY
)"
  printf '\nJARVIS_SELF_IMPROVEMENT_ADMIN_TOKEN=%s\n' "$TOKEN" >> .env
fi

append_default() {
  local key="$1"
  local value="$2"
  if ! grep -q "^${key}=" .env 2>/dev/null; then
    printf '%s=%s\n' "$key" "$value" >> .env
  fi
}

append_default JARVIS_SELF_IMPROVEMENT_ENABLED true
append_default JARVIS_SELF_IMPROVEMENT_AUTO_PREPARE true
append_default JARVIS_SELF_IMPROVEMENT_REPEAT_THRESHOLD 2
append_default JARVIS_SELF_IMPROVEMENT_LATENCY_FAILURE_MS 7000
append_default JARVIS_IMPROVEMENT_MODEL gpt-5.1-codex
append_default JARVIS_IMPROVEMENT_POLL_SECONDS 15
append_default JARVIS_IMPROVEMENT_MAX_ATTEMPTS_PER_DAY 3
append_default JARVIS_IMPROVEMENT_MAX_PATCH_LINES 450
append_default JARVIS_IMPROVEMENT_MAX_CHANGED_FILES 5
append_default JARVIS_IMPROVEMENT_GITHUB_ENABLED false
append_default JARVIS_IMPROVEMENT_AI_REVIEW_ENABLED true
append_default JARVIS_IMPROVEMENT_NOTIFY_ENABLED true
append_default JARVIS_IMPROVEMENT_NOTIFY_SERVICE notify.mobile_app_aaron_s_phone
append_default JARVIS_IMPROVEMENT_AUTO_DEPLOY_LOW_RISK false
CURRENT_BRANCH="$(git branch --show-current)"
append_default JARVIS_IMPROVEMENT_BASE_BRANCH "${CURRENT_BRANCH:-main}"

if ! git config user.name >/dev/null; then
  git config user.name "Jarvis Improvement Worker"
fi
if ! git config user.email >/dev/null; then
  git config user.email "jarvis@localhost"
fi

# Commit the current working Jarvis baseline. The worker deliberately refuses to
# operate on a dirty production tree. Stage tracked changes plus source files,
# while the ignore rules above keep backups, archives, data and secrets out.
git add -u
find bridge/app bridge/tests -type f -name '*.py' -print0 2>/dev/null | \
  xargs -0 -r git add --
[ -f bridge/app/static/chat.html ] && git add -- bridge/app/static/chat.html || true

git add -- \
  bridge/requirements.txt \
  docker-compose.yml \
  requirements-improver.txt \
  pyproject.toml \
  config/self_improvement_policy.json \
  tools/self_improvement_worker.py \
  tools/jarvis-improve \
  tools/install_self_improvement_v14.sh \
  systemd/jarvis-improver.service \
  .github/workflows/jarvis-ci.yml \
  .github/workflows/codeql.yml \
  .github/PULL_REQUEST_TEMPLATE.md \
  .github/CODEOWNERS \
  docs/SELF_IMPROVEMENT.md \
  .gitignore 2>/dev/null || true

if ! git diff --cached --quiet; then
  git commit -m "Install Jarvis Self-Improvement Engine v14"
fi

if [[ -n "$(git status --porcelain --untracked-files=no)" ]]; then
  echo "ERROR: Tracked files are still modified after creating the baseline commit." >&2
  git status --short >&2
  exit 1
fi

# Build Core first so it creates/updates the shared improvement database.
docker compose up -d --build

for _ in $(seq 1 30); do
  if curl -fsS http://127.0.0.1:8000/health >/dev/null 2>&1; then
    break
  fi
  sleep 2
done
curl -fsS http://127.0.0.1:8000/health
printf '\n'

mkdir -p "$HOME/.config/systemd/user"
sed "s|__JARVIS_ROOT__|$ROOT|g" \
  systemd/jarvis-improver.service \
  > "$HOME/.config/systemd/user/jarvis-improver.service"

systemctl --user daemon-reload
systemctl --user enable --now jarvis-improver.service

printf '\nSelf-improvement worker status:\n'
systemctl --user --no-pager --full status jarvis-improver.service || true
printf '\nJarvis improvement status:\n'
.venv-improver/bin/python tools/self_improvement_worker.py status

cat <<EOF

Installed Jarvis Self-Improvement Engine v14.

To keep the user service running after logout, run once:
  sudo loginctl enable-linger $USER

Useful commands:
  ./tools/jarvis-improve status
  systemctl --user status jarvis-improver
  journalctl --user -u jarvis-improver -f

Python source changes are never deployed automatically. A tested candidate must
be approved with its six-digit code before deployment.
EOF
