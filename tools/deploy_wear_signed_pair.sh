#!/usr/bin/env bash
set -Eeuo pipefail

# Safe physical deployment helper. It never relies on a remembered wireless
# debugging port and never allows an empty serial to select the default device.
ADB_BIN="${ADB_BIN:-/home/aaron/Android/Sdk/platform-tools/adb}"
PHONE_APK="${1:-}"
WATCH_APK="${2:-}"

if [[ ! -x "$ADB_BIN" || ! -f "$PHONE_APK" || ! -f "$WATCH_APK" ]]; then
  echo "Usage: $0 PHONE_RELEASE_APK WATCH_RELEASE_APK" >&2
  exit 2
fi

echo "Current wireless-debugging discovery:"
"$ADB_BIN" mdns services || true
echo "Connected targets:"
"$ADB_BIN" devices -l

serial_for_model() {
  local wanted="$1"
  local matches
  matches="$("$ADB_BIN" devices -l | awk -v wanted="$wanted" '
    $2 == "device" {
      for (i = 3; i <= NF; i++) {
        if ($i == "model:" wanted) print $1
      }
    }')"
  if [[ -z "$matches" || "$(printf '%s\n' "$matches" | sed '/^$/d' | wc -l)" -ne 1 ]]; then
    echo "Expected exactly one connected $wanted target; refusing deployment" >&2
    return 1
  fi
  printf '%s' "$matches"
}

PHONE_SERIAL="$(serial_for_model SM_G996B)"
WATCH_SERIAL="$(serial_for_model SM_L315F)"
[[ -n "$PHONE_SERIAL" && -n "$WATCH_SERIAL" ]]

PHONE_MODEL="$("$ADB_BIN" -s "$PHONE_SERIAL" shell getprop ro.product.model | tr -d '\r')"
WATCH_MODEL="$("$ADB_BIN" -s "$WATCH_SERIAL" shell getprop ro.product.model | tr -d '\r')"
[[ "$PHONE_MODEL" == "SM-G996B" || "$PHONE_MODEL" == "SM_G996B" ]]
[[ "$WATCH_MODEL" == "SM-L315F" || "$WATCH_MODEL" == "SM_L315F" ]]

echo "Updating phone $PHONE_MODEL at $PHONE_SERIAL (data-preserving install -r)"
"$ADB_BIN" -s "$PHONE_SERIAL" install -r "$PHONE_APK"
echo "Updating watch $WATCH_MODEL at $WATCH_SERIAL (data-preserving install -r)"
"$ADB_BIN" -s "$WATCH_SERIAL" install -r "$WATCH_APK"

"$ADB_BIN" -s "$PHONE_SERIAL" shell dumpsys package com.aaron.jarvisvoice \
  | sed -nE '/versionCode=|versionName=/p'
"$ADB_BIN" -s "$WATCH_SERIAL" shell dumpsys package com.aaron.jarvisvoice \
  | sed -nE '/versionCode=|versionName=/p'
"$ADB_BIN" -s "$WATCH_SERIAL" shell cmd package resolve-activity --brief \
  -a android.intent.action.MAIN -c android.intent.category.LAUNCHER \
  com.aaron.jarvisvoice
