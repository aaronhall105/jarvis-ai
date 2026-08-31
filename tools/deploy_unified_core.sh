#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
AUTHORITATIVE_BRANCH="jarvis/unified-production"
LIVE_ROOT="${JARVIS_LIVE_ROOT:-/home/aaron/.local/share/jarvis-runtime}"
CORE_IMAGE="jarvis-jarvis-core"
SPEAKER_IMAGE="jarvis-jarvis-speaker-verifier"

require_known_mount() {
  local mounts="$1" relative="$2" destination="$3"
  if ! grep -Fq "$LIVE_ROOT/$relative -> $destination" <<<"$mounts"; then
    echo "Unexpected persistent mount for $destination" >&2
    exit 1
  fi
}

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

verify_speaker_database() {
  docker exec -i jarvis-speaker-verifier python - <<'PY'
import sqlite3
from pathlib import Path

path = Path("/data/jarvis_speakers.db")
if not path.is_file():
    raise SystemExit("Speaker identity database is missing")
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
test -f "$LIVE_ROOT/docker-compose.override.yml"
test -f "$LIVE_ROOT/.env"
for directory in data config logs speaker-data; do test -d "$LIVE_ROOT/$directory"; done
verify_container_databases
verify_speaker_database

if [[ "${1:-}" == "--verify-only" ]]; then
  echo "Authoritative Core deployment preflight verified at $head"
  exit 0
fi

before_mounts="$(docker inspect jarvis-core --format '{{range .Mounts}}{{println .Source "->" .Destination}}{{end}}')"
before_speaker_mounts="$(docker inspect jarvis-speaker-verifier --format '{{range .Mounts}}{{println .Source "->" .Destination}}{{end}}')"
require_known_mount "$before_mounts" data /app/data
require_known_mount "$before_mounts" config /app/config
require_known_mount "$before_mounts" logs /app/logs
require_known_mount "$before_speaker_mounts" speaker-data /data

docker build \
  --build-arg "JARVIS_SOURCE_SHA=$head" \
  --label "org.opencontainers.image.revision=$head" \
  --label "org.opencontainers.image.source=jarvis/unified-production" \
  --tag "$CORE_IMAGE" \
  bridge
docker build \
  --label "org.opencontainers.image.revision=$head" \
  --label "org.opencontainers.image.source=jarvis/unified-production" \
  --tag "$SPEAKER_IMAGE" \
  speaker-verifier

docker compose \
  --project-name jarvis \
  --project-directory "$LIVE_ROOT" \
  --file "$LIVE_ROOT/docker-compose.yml" \
  --file "$LIVE_ROOT/docker-compose.override.yml" \
  up -d --no-build jarvis-speaker-verifier jarvis-core

for _ in $(seq 1 90); do
  core_health="$(docker inspect jarvis-core --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}')"
  speaker_health="$(docker inspect jarvis-speaker-verifier --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}')"
  [[ "$core_health" == "healthy" && "$speaker_health" == "healthy" ]] && break
  sleep 2
done
test "$(docker inspect jarvis-core --format '{{.State.Health.Status}}')" = "healthy"
test "$(docker inspect jarvis-speaker-verifier --format '{{.State.Health.Status}}')" = "healthy"
test "$(docker inspect jarvis-core --format '{{index .Config.Labels "org.opencontainers.image.revision"}}')" = "$head"
test "$(docker inspect jarvis-speaker-verifier --format '{{index .Config.Labels "org.opencontainers.image.revision"}}')" = "$head"

after_mounts="$(docker inspect jarvis-core --format '{{range .Mounts}}{{println .Source "->" .Destination}}{{end}}')"
after_speaker_mounts="$(docker inspect jarvis-speaker-verifier --format '{{range .Mounts}}{{println .Source "->" .Destination}}{{end}}')"
grep -Fq "$LIVE_ROOT/data -> /app/data" <<<"$after_mounts"
grep -Fq "$LIVE_ROOT/config -> /app/config" <<<"$after_mounts"
grep -Fq "$LIVE_ROOT/logs -> /app/logs" <<<"$after_mounts"
grep -Fq "$LIVE_ROOT/speaker-data -> /data" <<<"$after_speaker_mounts"
verify_container_databases
verify_speaker_database
curl --fail --silent --show-error http://127.0.0.1:8000/health/live >/dev/null
health_commit="$(curl --fail --silent --show-error http://127.0.0.1:8000/health \
  | python3 -c 'import json,sys; print(json.load(sys.stdin).get("source_commit", ""))')"
test "$health_commit" = "$head"
if docker logs --since 5m jarvis-core 2>&1 | grep -Eqi 'traceback|migration.*(failed|error)|application startup failed'; then
  echo "Core startup logs contain a failure marker" >&2
  exit 1
fi
echo "Jarvis unified Core deployed and verified at $head"
