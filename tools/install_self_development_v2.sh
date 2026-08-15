#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="$ROOT/.venv-improver/bin/python"
SERVICE="jarvis-improver.service"
DROPIN_DIR="$HOME/.config/systemd/user/${SERVICE}.d"
DROPIN="$DROPIN_DIR/self-development-v2.conf"
EXPECTED=(
  "tools/self_development_worker.py"
  "tools/install_self_development_v2.sh"
  "bridge/tests/test_self_development_worker.py"
)

cd "$ROOT"

if [[ "$(git branch --show-current)" != "conversation-engine" ]]; then
  echo "ERROR: bootstrap must be installed from conversation-engine." >&2
  exit 1
fi

if [[ ! -x "$PYTHON" ]]; then
  echo "ERROR: improvement virtualenv is missing: $PYTHON" >&2
  exit 1
fi

for path in "${EXPECTED[@]}"; do
  if [[ ! -f "$ROOT/$path" ]]; then
    echo "ERROR: bootstrap file is missing: $path" >&2
    exit 1
  fi
done

unexpected=()
while IFS= read -r line; do
  [[ -z "$line" ]] && continue
  path="${line:3}"
  allowed=false
  for expected in "${EXPECTED[@]}"; do
    if [[ "$path" == "$expected" ]]; then
      allowed=true
      break
    fi
  done
  if [[ "$allowed" != true ]]; then
    unexpected+=("$path")
  fi
done < <(git status --porcelain)

if (( ${#unexpected[@]} > 0 )); then
  printf 'ERROR: unrelated repository changes found; bootstrap refused:\n' >&2
  printf '  %s\n' "${unexpected[@]}" >&2
  exit 1
fi

echo "===== SELF-DEVELOPMENT V2 TESTS ====="
"$PYTHON" -m py_compile \
  "$ROOT/tools/self_improvement_worker.py" \
  "$ROOT/tools/self_development_worker.py"

"$PYTHON" -m pytest -q \
  bridge/tests/test_self_development_worker.py \
  bridge/tests/test_improvement_worker_policy.py \
  -p no:cacheprovider

git diff --check

git add -- "${EXPECTED[@]}"
git diff --cached --check

if ! git diff --cached --quiet; then
  git commit -m "Bootstrap autonomous Self-Development v2"
fi

git push origin HEAD:conversation-engine

systemctl --user stop "$SERVICE" || true
mkdir -p "$DROPIN_DIR"
cat > "$DROPIN" <<EOF
[Service]
ExecStart=
ExecStart=$PYTHON $ROOT/tools/self_development_worker.py daemon
EOF

systemctl --user daemon-reload

if ! systemctl --user restart "$SERVICE"; then
  echo "ERROR: v2 service start failed; restoring original worker entrypoint." >&2
  rm -f "$DROPIN"
  systemctl --user daemon-reload
  systemctl --user restart "$SERVICE" || true
  exit 1
fi

if ! systemctl --user is-active --quiet "$SERVICE"; then
  echo "ERROR: v2 worker is not active; restoring original worker entrypoint." >&2
  systemctl --user status "$SERVICE" --no-pager || true
  rm -f "$DROPIN"
  systemctl --user daemon-reload
  systemctl --user restart "$SERVICE" || true
  exit 1
fi

echo
echo "===== ACTIVE WORKER ====="
systemctl --user show "$SERVICE" -p ExecStart --no-pager

echo
echo "===== SELF-IMPROVEMENT STATUS ====="
"$PYTHON" "$ROOT/tools/self_development_worker.py" status

echo
echo "PASS: Jarvis Self-Development v2 bootstrap is active."
