#!/usr/bin/env bash
set -Eeuo pipefail

SOURCE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/jarvis-adb-readiness.service"
TARGET="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user/jarvis-adb-readiness.service"

install -D -m 0644 "$SOURCE" "$TARGET"
systemctl --user daemon-reload
systemctl --user enable --now jarvis-adb-readiness.service
systemctl --user --no-pager --full status jarvis-adb-readiness.service
