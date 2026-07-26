#!/usr/bin/env bash
set -Eeuo pipefail

CONFIG_ROOT="${1:-/config}"
PACKAGE_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SOURCE="$PACKAGE_ROOT/custom_components/jarvis_core_conversation"
TARGET="$CONFIG_ROOT/custom_components/jarvis_core_conversation"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
BACKUP_ROOT="$CONFIG_ROOT/backups/jarvis-assist-smart-audio-gate-v1.5.4/$STAMP"
RESTORE_REQUIRED=false

log() { printf '[Assist v1.5.4] %s\n' "$*"; }

rollback() {
  local exit_code=$?
  if [[ "$RESTORE_REQUIRED" == true && -d "$BACKUP_ROOT/jarvis_core_conversation" ]]; then
    log "Installation failed; restoring the previous integration"
    rm -rf "$TARGET"
    mkdir -p "$(dirname "$TARGET")"
    cp -a "$BACKUP_ROOT/jarvis_core_conversation" "$TARGET"
  fi
  exit "$exit_code"
}
trap rollback ERR

if [[ ! -f "$TARGET/conversation.py" || ! -f "$TARGET/manifest.json" ]]; then
  printf 'Jarvis integration not found at %s\n' "$TARGET" >&2
  exit 1
fi

log "Backing up the current Home Assistant integration"
mkdir -p "$BACKUP_ROOT"
cp -a "$TARGET" "$BACKUP_ROOT/jarvis_core_conversation"
RESTORE_REQUIRED=true

log "Running audio-gate, closure, streaming and integrity tests"
python3 "$PACKAGE_ROOT/tests/test_audio_gate.py"
python3 "$PACKAGE_ROOT/tests/test_streaming.py"
python3 "$PACKAGE_ROOT/tests/test_conversation_closure.py"
python3 "$PACKAGE_ROOT/tests/test_release_integrity.py" "$PACKAGE_ROOT"

log "Installing Smart Audio Gate integration"
rm -rf "$TARGET"
mkdir -p "$(dirname "$TARGET")"
cp -a "$SOURCE" "$TARGET"
find "$TARGET" -type d -name __pycache__ -prune -exec rm -rf {} +
find "$TARGET" -type f -name '*.pyc' -delete

log "Compiling the integration"
python3 -m py_compile \
  "$TARGET/conversation.py" \
  "$TARGET/audio_gate.py" \
  "$TARGET/closure.py" \
  "$TARGET/streaming.py" \
  "$TARGET/config_flow.py" \
  "$TARGET/__init__.py"

python3 - <<PY2
import ast
import json
from pathlib import Path
root = Path("$TARGET")
manifest = json.loads((root / "manifest.json").read_text())
assert manifest.get("version") == "1.5.4", manifest
module = ast.parse((root / "config_flow.py").read_text())
version = None
for node in ast.walk(module):
    if isinstance(node, ast.ClassDef) and node.name == "JarvisCoreConfigFlow":
        for item in node.body:
            if isinstance(item, ast.Assign):
                for target in item.targets:
                    if isinstance(target, ast.Name) and target.id == "VERSION":
                        version = ast.literal_eval(item.value)
assert version == 2, {"config_entry_version": version}
print({"integration_version": manifest["version"], "config_entry_version": version})
PY2

RESTORE_REQUIRED=false
trap - ERR
log "Installation completed"
log "Backup: $BACKUP_ROOT"

if command -v ha >/dev/null 2>&1; then
  log "Checking Home Assistant configuration"
  ha core check
  log "Restarting Home Assistant Core"
  ha core restart
else
  log "Restart Home Assistant Core to load v1.5.4"
fi
