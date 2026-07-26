#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
INTEGRATION_ROOT="$ROOT_DIR/home_assistant"
SOURCE="$INTEGRATION_ROOT/custom_components/jarvis_core_conversation"
TESTS="$INTEGRATION_ROOT/tests"
INSTALLER="$INTEGRATION_ROOT/tools/install_assist_smart_audio_gate_v1_5_4.sh"
DIST_DIR="${1:-$ROOT_DIR/dist}"
ASSET_NAME="jarvis-assist-smart-audio-gate-v1.5.4.tar.gz"
OUTPUT="$DIST_DIR/$ASSET_NAME"
STAGE="$(mktemp -d)"
cleanup() { rm -rf "$STAGE"; }
trap cleanup EXIT

required=(
  "$SOURCE/__init__.py" "$SOURCE/config_flow.py" "$SOURCE/const.py"
  "$SOURCE/conversation.py" "$SOURCE/audio_gate.py" "$SOURCE/closure.py"
  "$SOURCE/manifest.json" "$SOURCE/streaming.py" "$SOURCE/translations/en.json"
  "$TESTS/test_audio_gate.py" "$TESTS/test_streaming.py"
  "$TESTS/test_conversation_closure.py" "$TESTS/test_release_integrity.py"
  "$INSTALLER"
)
for path in "${required[@]}"; do
  [[ -f "$path" ]] || { echo "Missing required file: $path" >&2; exit 1; }
done

python3 "$TESTS/test_audio_gate.py"
python3 "$TESTS/test_streaming.py"
python3 "$TESTS/test_conversation_closure.py"
python3 "$TESTS/test_release_integrity.py" "$INTEGRATION_ROOT"
python3 -m py_compile \
  "$SOURCE/__init__.py" "$SOURCE/config_flow.py" "$SOURCE/conversation.py" \
  "$SOURCE/audio_gate.py" "$SOURCE/closure.py" "$SOURCE/streaming.py"

mkdir -p "$STAGE/custom_components" "$STAGE/tests" "$STAGE/tools"
cp -a "$SOURCE" "$STAGE/custom_components/jarvis_core_conversation"
cp "$TESTS/test_audio_gate.py" "$STAGE/tests/test_audio_gate.py"
cp "$TESTS/test_streaming.py" "$STAGE/tests/test_streaming.py"
cp "$TESTS/test_conversation_closure.py" "$STAGE/tests/test_conversation_closure.py"
cp "$TESTS/test_release_integrity.py" "$STAGE/tests/test_release_integrity.py"
cp "$INSTALLER" "$STAGE/tools/install_assist_smart_audio_gate_v1_5_4.sh"
chmod +x "$STAGE/tools/install_assist_smart_audio_gate_v1_5_4.sh"

cat > "$STAGE/CHANGES.md" <<'CHANGES'
# Jarvis Assist v1.5.4 — Smart Audio Gate

- Makes Smart follow-up mode the safe default.
- Migrates the legacy always-open default to Smart once.
- Rejects expired, echoed, filler-only and likely unrelated follow-up speech locally.
- Accepts expected confirmations, choices, concise answers and explicit new commands.
- Preserves explicit conversation-closing phrases and spoken progress.
- Keeps config-entry version 2.
CHANGES

cat > "$STAGE/INSTALL.md" <<'INSTALL'
Run inside the Home Assistant Terminal:

```bash
chmod +x tools/install_assist_smart_audio_gate_v1_5_4.sh
./tools/install_assist_smart_audio_gate_v1_5_4.sh /config
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
