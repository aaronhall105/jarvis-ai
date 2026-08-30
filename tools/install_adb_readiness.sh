#!/usr/bin/env bash
set -Eeuo pipefail

SOURCE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/jarvis-adb-readiness.service"
TARGET="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user/jarvis-adb-readiness.service"

mkdir -p "$(dirname "$TARGET")"
sed "s|__JARVIS_ROOT__|$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)|g" \
  "$SOURCE" > "$TARGET"
chmod 0644 "$TARGET"
systemctl --user daemon-reload
systemctl --user enable --now jarvis-adb-readiness.service
systemctl --user --no-pager --full status jarvis-adb-readiness.service
