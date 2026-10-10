#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
INTEGRATION_ROOT="$ROOT_DIR/home_assistant"
SOURCE="$INTEGRATION_ROOT/custom_components/jarvis_core_conversation"
TESTS="$INTEGRATION_ROOT/tests"
INSTALLER="$INTEGRATION_ROOT/tools/install_jarvis_home_v1_8_0.sh"
OCCUPANCY_AUTOMATION="$INTEGRATION_ROOT/config/alpha39_living_room_occupancy_automation.yaml"
DASHBOARD="$ROOT_DIR/docs/configuration/jarvis_alpha38_dashboard.yaml"
DASHBOARD_GUIDE="$ROOT_DIR/docs/configuration/ALPHA38_HOME_ASSISTANT_DASHBOARD.md"
DIST_DIR="${1:-$ROOT_DIR/dist}"
ASSET_NAME="jarvis-home-experience-v1.8.0.tar.gz"
OUTPUT="$DIST_DIR/$ASSET_NAME"
STAGE="$(mktemp -d)"
cleanup() { rm -rf "$STAGE"; }
trap cleanup EXIT

required=(
  "$SOURCE/__init__.py" "$SOURCE/config_flow.py" "$SOURCE/const.py"
  "$SOURCE/conversation.py" "$SOURCE/audio_gate.py" "$SOURCE/closure.py"
  "$SOURCE/coordinator.py" "$SOURCE/sensor.py" "$SOURCE/manifest.json"
  "$SOURCE/streaming.py" "$SOURCE/translations/en.json"
  "$TESTS/test_audio_gate.py" "$TESTS/test_streaming.py"
  "$TESTS/test_conversation_closure.py" "$TESTS/test_release_integrity.py"
  "$TESTS/test_home_experience_dashboard.py" "$INSTALLER"
  "$DASHBOARD" "$DASHBOARD_GUIDE" "$OCCUPANCY_AUTOMATION"
)
for path in "${required[@]}"; do
  [[ -f "$path" ]] || { echo "Missing required file: $path" >&2; exit 1; }
done

python3 "$TESTS/test_audio_gate.py"
python3 "$TESTS/test_streaming.py"
python3 "$TESTS/test_conversation_closure.py"
python3 "$TESTS/test_release_integrity.py" "$INTEGRATION_ROOT"
python3 - "$DASHBOARD" <<'PY'
import sys
from pathlib import Path

import yaml

dashboard = yaml.safe_load(Path(sys.argv[1]).read_text(encoding="utf-8"))
views = {item["path"] for item in dashboard["views"]}
required = {"home", "rooms", "cameras", "energy", "people", "more"}
assert required <= views, {"missing_views": sorted(required - views)}
print({"dashboard_views": len(views), "required_views": len(required)})
PY
python3 -m py_compile \
  "$SOURCE/__init__.py" "$SOURCE/config_flow.py" "$SOURCE/conversation.py" \
  "$SOURCE/audio_gate.py" "$SOURCE/closure.py" "$SOURCE/streaming.py" \
  "$SOURCE/coordinator.py" "$SOURCE/sensor.py"

mkdir -p "$STAGE/custom_components" "$STAGE/tests" "$STAGE/tools" "$STAGE/dashboard"
cp -a "$SOURCE" "$STAGE/custom_components/jarvis_core_conversation"
cp "$TESTS/test_audio_gate.py" "$STAGE/tests/test_audio_gate.py"
cp "$TESTS/test_streaming.py" "$STAGE/tests/test_streaming.py"
cp "$TESTS/test_conversation_closure.py" "$STAGE/tests/test_conversation_closure.py"
cp "$TESTS/test_release_integrity.py" "$STAGE/tests/test_release_integrity.py"
cp "$TESTS/test_home_experience_dashboard.py" "$STAGE/tests/test_home_experience_dashboard.py"
cp "$INSTALLER" "$STAGE/tools/install_jarvis_home_v1_8_0.sh"
cp "$OCCUPANCY_AUTOMATION" "$STAGE/dashboard/alpha39_living_room_occupancy_automation.yaml"
cp "$DASHBOARD" "$STAGE/dashboard/jarvis_alpha38_dashboard.yaml"
cp "$DASHBOARD_GUIDE" "$STAGE/dashboard/INSTALL_DASHBOARD.md"
chmod +x "$STAGE/tools/install_jarvis_home_v1_8_0.sh"

cat > "$STAGE/CHANGES.md" <<'CHANGES'
# Jarvis Home v1.8.0 — Room occupancy intelligence

- Adds authenticated HomeExperience coordinator and presentation sensors.
- Retains Smart Audio Gate, conversation closure, and streamed progress.
- Uses conditional refresh and never reconstructs Jarvis semantics from raw HA state.
- Exposes stable per-room occupancy states and safe automation attributes.
- Carries a disabled, opt-in Living Room lighting example; it is never auto-installed.
- Keeps config-entry version 2.
CHANGES

cat > "$STAGE/INSTALL.md" <<'INSTALL'
Run inside the Home Assistant Terminal:

```bash
chmod +x tools/install_jarvis_home_v1_8_0.sh
./tools/install_jarvis_home_v1_8_0.sh /config
```
INSTALL

find "$STAGE" -type d -name __pycache__ -prune -exec rm -rf {} +
find "$STAGE" -type f -name '*.pyc' -delete
mkdir -p "$DIST_DIR"
rm -f "$OUTPUT" "$OUTPUT.sha256"
tar --sort=name --mtime='UTC 2026-07-26' --owner=0 --group=0 --numeric-owner \
  -czf "$OUTPUT" -C "$STAGE" .
sha256sum "$OUTPUT" > "$OUTPUT.sha256"
mkdir -p "$ROOT_DIR/bridge/app/assets"
cp "$OUTPUT" "$ROOT_DIR/bridge/app/assets/$ASSET_NAME"
echo "Built: $OUTPUT"
cat "$OUTPUT.sha256"
