#!/usr/bin/env bash
set -euo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
runtime_dir="$HOME/.local/share/jarvis-developer"
config_dir="$HOME/.config/jarvis"
unit_dir="$HOME/.config/systemd/user"

install -d -m 700 "$runtime_dir" "$config_dir" "$unit_dir"
python3 -m venv "$runtime_dir/venv"
"$runtime_dir/venv/bin/pip" install --disable-pip-version-check -r "$repo_root/developer_gateway/requirements.txt"

if [[ ! -s "$config_dir/developer-token" ]]; then
    umask 077
    openssl rand -hex 32 > "$config_dir/developer-token"
fi
chmod 600 "$config_dir/developer-token"
install -m 600 "$repo_root/developer_gateway/jarvis-developer.service" "$unit_dir/jarvis-developer.service"
systemctl --user daemon-reload
systemctl --user enable --now jarvis-developer.service
systemctl --user is-active --quiet jarvis-developer.service
echo "Jarvis Developer gateway installed and active."
