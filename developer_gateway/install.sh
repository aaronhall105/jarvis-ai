#!/usr/bin/env bash
set -euo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
runtime_dir="$HOME/.local/share/jarvis-developer"
release_root="$runtime_dir/releases"
config_dir="$HOME/.config/jarvis"
unit_dir="$HOME/.config/systemd/user"
authoritative_branch="jarvis/unified-production"

test "$(git -C "$repo_root" branch --show-current)" = "$authoritative_branch" || {
    echo "Developer deployment is restricted to $authoritative_branch" >&2
    exit 1
}
test -z "$(git -C "$repo_root" status --porcelain)" || {
    echo "Developer deployment requires a clean authoritative worktree" >&2
    exit 1
}
git -C "$repo_root" fetch origin "$authoritative_branch"
source_commit=$(git -C "$repo_root" rev-parse HEAD)
test "$source_commit" = "$(git -C "$repo_root" rev-parse "origin/$authoritative_branch")" || {
    echo "Developer deployment source does not match the authoritative remote" >&2
    exit 1
}

release_dir="$release_root/$source_commit"
install -d -m 700 "$runtime_dir" "$release_root" "$release_dir" "$release_dir/developer_gateway" "$config_dir" "$unit_dir"
for source_file in __init__.py app.py codex_client.py; do
    install -m 600 "$repo_root/developer_gateway/$source_file" "$release_dir/developer_gateway/$source_file"
done
printf '%s\n' "$source_commit" > "$release_dir/SOURCE_COMMIT"
chmod 600 "$release_dir/SOURCE_COMMIT"
ln -sfn "releases/$source_commit" "$runtime_dir/current"

python3 -m venv "$runtime_dir/venv"
"$runtime_dir/venv/bin/pip" install --disable-pip-version-check -r "$repo_root/developer_gateway/requirements.txt"

if [[ ! -s "$config_dir/developer-token" ]]; then
    umask 077
    openssl rand -hex 32 > "$config_dir/developer-token"
fi
chmod 600 "$config_dir/developer-token"
sed "s|__JARVIS_ROOT__|$repo_root|g" \
    "$repo_root/developer_gateway/jarvis-developer.service" \
    > "$unit_dir/jarvis-developer.service"
chmod 600 "$unit_dir/jarvis-developer.service"
systemctl --user daemon-reload
systemctl --user enable jarvis-developer.service
systemctl --user restart jarvis-developer.service
systemctl --user is-active --quiet jarvis-developer.service
health_commit=""
for _ in $(seq 1 30); do
    health_commit=$(curl --fail --silent http://127.0.0.1:8765/health 2>/dev/null \
        | python3 -c 'import json,sys; print(json.load(sys.stdin).get("source_commit", ""))' \
        2>/dev/null || true)
    [[ "$health_commit" == "$source_commit" ]] && break
    sleep 1
done
test "$health_commit" = "$source_commit"
echo "Jarvis Developer gateway installed and active at $source_commit."
