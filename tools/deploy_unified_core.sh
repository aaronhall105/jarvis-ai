#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
AUTHORITATIVE_BRANCH="jarvis/unified-production"
LIVE_ROOT="${JARVIS_LIVE_ROOT:-/home/aaron/jarvis}"
CORE_IMAGE="jarvis-jarvis-core"

verify_container_databases() {
  docker exec -i jarvis-core python - <<'PY'
import sqlite3
from pathlib import Path

paths = sorted(Path("/app/data").glob("*.db"))
if not paths:
    raise SystemExit("No persistent Jarvis databases found")
for path in paths:
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=30)
    try:
        row = connection.execute("PRAGMA quick_check").fetchone()
    finally:
        connection.close()
    result = str(row[0]) if row else "missing result"
    if result.casefold() != "ok":
        raise SystemExit(f"{path.name}: {result}")
    print(f"{path.name}: ok")
PY
}

cd "$ROOT"
test "$(git branch --show-current)" = "$AUTHORITATIVE_BRANCH" || {
  echo "Core deployment is restricted to $AUTHORITATIVE_BRANCH" >&2
  exit 1
}
test -z "$(git status --porcelain)" || {
  echo "Core deployment requires a clean authoritative worktree" >&2
  exit 1
}
git fetch origin "$AUTHORITATIVE_BRANCH"
head="$(git rev-parse HEAD)"
test "$head" = "$(git rev-parse "origin/$AUTHORITATIVE_BRANCH")" || {
  echo "Local HEAD does not match the remote authoritative branch" >&2
  exit 1
}
python3 tools/verify_product_baseline.py

test -f "$LIVE_ROOT/docker-compose.yml"
test -f "$LIVE_ROOT/.env"
test -d "$LIVE_ROOT/data"
verify_container_databases

if [[ "${1:-}" == "--verify-only" ]]; then
  echo "Authoritative Core deployment preflight verified at $head"
  exit 0
fi

before_mounts="$(docker inspect jarvis-core --format '{{range .Mounts}}{{println .Source "->" .Destination}}{{end}}')"
grep -Fq "$LIVE_ROOT/data -> /app/data" <<<"$before_mounts"
grep -Fq "$LIVE_ROOT/config -> /app/config" <<<"$before_mounts"
grep -Fq "$LIVE_ROOT/logs -> /app/logs" <<<"$before_mounts"

docker build \
  --label "org.opencontainers.image.revision=$head" \
  --label "org.opencontainers.image.source=jarvis/unified-production" \
  --tag "$CORE_IMAGE" \
  bridge
docker compose \
  --project-directory "$LIVE_ROOT" \
  --file "$LIVE_ROOT/docker-compose.yml" \
  --file "$LIVE_ROOT/docker-compose.override.yml" \
  up -d --no-build --no-deps jarvis-core

for _ in $(seq 1 45); do
  health="$(docker inspect jarvis-core --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}')"
  [[ "$health" == "healthy" ]] && break
  sleep 2
done
test "$(docker inspect jarvis-core --format '{{.State.Health.Status}}')" = "healthy"
test "$(docker inspect jarvis-core --format '{{index .Config.Labels "org.opencontainers.image.revision"}}')" = "$head"

after_mounts="$(docker inspect jarvis-core --format '{{range .Mounts}}{{println .Source "->" .Destination}}{{end}}')"
test "$before_mounts" = "$after_mounts"
verify_container_databases
curl --fail --silent --show-error http://127.0.0.1:8000/health/live >/dev/null
curl --fail --silent --show-error http://127.0.0.1:8000/health >/dev/null
if docker logs --since 5m jarvis-core 2>&1 | grep -Eqi 'traceback|migration.*(failed|error)|application startup failed'; then
  echo "Core startup logs contain a failure marker" >&2
  exit 1
fi
echo "Jarvis unified Core deployed and verified at $head"
