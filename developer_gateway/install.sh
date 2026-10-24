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
for source_file in __init__.py app.py codex_client.py backends.py api_backend.py state.py policy.py tools.py autonomy.py autonomy_api.py autonomy_host.py observer.py; do
    install -m 600 "$repo_root/developer_gateway/$source_file" "$release_dir/developer_gateway/$source_file"
done
printf '%s\n' "$source_commit" > "$release_dir/SOURCE_COMMIT"
chmod 600 "$release_dir/SOURCE_COMMIT"
python3 -m venv "$release_dir/venv"
"$release_dir/venv/bin/pip" install --disable-pip-version-check -r "$repo_root/developer_gateway/requirements.txt"

if [[ ! -s "$config_dir/developer-token" ]]; then
    umask 077
    openssl rand -hex 32 > "$config_dir/developer-token"
fi
chmod 600 "$config_dir/developer-token"
if [[ ! -e "$config_dir/developer-api-key" ]]; then
    install -m 600 /dev/null "$config_dir/developer-api-key"
fi
common_dir=$(git -C "$repo_root" rev-parse --path-format=absolute --git-common-dir)
command -v docker >/dev/null || { echo "Docker is required for Developer tools" >&2; exit 1; }
# Build from dependency manifests only, never send source/configuration as context.
build_context=$(mktemp -d "$runtime_dir/tool-build.XXXXXX")
install -m 600 "$repo_root/developer_gateway/Dockerfile.tools" "$build_context/Dockerfile"
install -m 600 "$repo_root/bridge/requirements.txt" "$build_context/bridge-requirements.txt"
install -m 600 "$repo_root/requirements-improver.txt" "$build_context/requirements-improver.txt"
install -m 600 "$repo_root/developer_gateway/requirements.txt" "$build_context/developer-requirements.txt"
tool_image="jarvis-developer-tools:$source_commit"
docker build -t "$tool_image" "$build_context"
ln -s "releases/$source_commit" "$runtime_dir/current-next-$$"
mv -Tf "$runtime_dir/current-next-$$" "$runtime_dir/current"
sed -e "s|__JARVIS_ROOT__|$repo_root|g" -e "s|__JARVIS_GIT_COMMON__|$common_dir|g" \
    -e "s|__JARVIS_TOOL_IMAGE__|$tool_image|g" \
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
