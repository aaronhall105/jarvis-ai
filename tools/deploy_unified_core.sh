#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
AUTHORITATIVE_BRANCH="jarvis/unified-production"
LIVE_ROOT="${JARVIS_LIVE_ROOT:-/home/aaron/.local/share/jarvis-runtime}"
PREVIOUS_LIVE_ROOT="${JARVIS_PREVIOUS_LIVE_ROOT:-/home/aaron/jarvis}"
CORE_IMAGE="jarvis-jarvis-core"
SPEAKER_IMAGE="jarvis-jarvis-speaker-verifier"
CUTOVER_ARCHIVE=""
OLD_CONTAINERS_STOPPED=false
COMPOSE_CUTOVER_STARTED=false

restart_previous_containers_on_error() {
  local status=$?
  if ((status != 0)) \
    && [[ "$OLD_CONTAINERS_STOPPED" == true ]] \
    && [[ "$COMPOSE_CUTOVER_STARTED" == false ]]; then
    echo "Cutover preparation failed; restarting the previous containers" >&2
    docker start jarvis-speaker-verifier jarvis-core >/dev/null 2>&1 || true
  fi
  exit "$status"
}

trap restart_previous_containers_on_error ERR

require_known_mount() {
  local mounts="$1" relative="$2" destination="$3"
  if ! grep -Fq "$LIVE_ROOT/$relative -> $destination" <<<"$mounts" \
    && ! grep -Fq "$PREVIOUS_LIVE_ROOT/$relative -> $destination" <<<"$mounts"; then
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

# A running SQLite service can change after the initial migration copy. When
# moving away from the historical source checkout, stop both writers and take
# one final, permission-preserving snapshot before Compose replaces them.
if [[ "$LIVE_ROOT" != "$PREVIOUS_LIVE_ROOT" ]] \
  && grep -Fq "$PREVIOUS_LIVE_ROOT/data -> /app/data" <<<"$before_mounts" \
  && grep -Fq "$PREVIOUS_LIVE_ROOT/speaker-data -> /data" <<<"$before_speaker_mounts"; then
  cutover_id="$(date -u +%Y%m%dT%H%M%SZ)-${head:0:12}"
  CUTOVER_ARCHIVE="$LIVE_ROOT/.pre-cutover-$cutover_id"
  echo "Stopping Jarvis writers for the final persistent-data cutover"
  docker stop jarvis-core jarvis-speaker-verifier >/dev/null
  OLD_CONTAINERS_STOPPED=true

  docker run --rm \
    --volume "$PREVIOUS_LIVE_ROOT:/source:ro" \
    --volume "$LIVE_ROOT:/target" \
    "$CORE_IMAGE" \
    python - "$cutover_id" <<'PY'
import os
import shutil
import sys
from pathlib import Path

cutover_id = sys.argv[1]
source_root = Path("/source")
target_root = Path("/target")
names = ("config", "data", "logs", "speaker-data")
stage = target_root / f".cutover-{cutover_id}"
archive = target_root / f".pre-cutover-{cutover_id}"

if stage.exists() or archive.exists():
    raise SystemExit("Refusing to reuse a cutover staging or archive directory")

stage.mkdir(mode=0o700)
for name in names:
    source = source_root / name
    if not source.is_dir():
        raise SystemExit(f"Required persistent source directory is missing: {name}")
    shutil.copytree(source, stage / name, symlinks=True, copy_function=shutil.copy2)
    for directory, child_directories, child_files in os.walk(source, followlinks=False):
        source_directory = Path(directory)
        paths = [source_directory]
        paths.extend(source_directory / child for child in child_directories)
        paths.extend(source_directory / child for child in child_files)
        for source_path in paths:
            relative_path = source_path.relative_to(source)
            target_path = stage / name / relative_path
            metadata = source_path.lstat()
            os.chown(
                target_path,
                metadata.st_uid,
                metadata.st_gid,
                follow_symlinks=False,
            )

archive.mkdir(mode=0o700)
archived: list[str] = []
installed: list[str] = []
try:
    for name in names:
        destination = target_root / name
        if not destination.is_dir():
            raise RuntimeError(f"Required target directory is missing: {name}")
        os.replace(destination, archive / name)
        archived.append(name)
    for name in names:
        os.replace(stage / name, target_root / name)
        installed.append(name)
except BaseException:
    for name in reversed(installed):
        installed_path = target_root / name
        if installed_path.exists():
            os.replace(installed_path, stage / name)
    for name in reversed(archived):
        archived_path = archive / name
        if archived_path.exists():
            os.replace(archived_path, target_root / name)
    raise
finally:
    if stage.exists() and not any(stage.iterdir()):
        stage.rmdir()
    if archive.exists() and not any(archive.iterdir()):
        archive.rmdir()
PY
fi

COMPOSE_CUTOVER_STARTED=true
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
if [[ -n "$CUTOVER_ARCHIVE" ]]; then
  echo "Pre-cutover target snapshot retained at $CUTOVER_ARCHIVE"
fi
echo "Jarvis unified Core deployed and verified at $head"
