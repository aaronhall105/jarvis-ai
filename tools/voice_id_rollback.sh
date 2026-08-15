#!/usr/bin/env bash
set -Eeuo pipefail
REPO="${JARVIS_REPO:-$HOME/jarvis}"; POINTER="$REPO/backup/voice-id-production-latest"
[ -f "$POINTER" ] || { echo "No Voice ID rollback snapshot found."; exit 1; }
BACKUP="$(cat "$POINTER")"; [ -d "$BACKUP" ] || { echo "Rollback snapshot missing: $BACKUP"; exit 1; }
cd "$REPO"; echo "===== JARVIS VOICE ID ROLLBACK ====="
[ -z "$(git status --porcelain)" ] || { echo "Main worktree has changes. Rollback refused to avoid data loss."; exit 1; }
PREV="$(cat "$BACKUP/preinstall-head.txt")"; INSTALLED=""; [ -f "$BACKUP/installed-commit.txt" ] && INSTALLED="$(cat "$BACKUP/installed-commit.txt")"
if [ -n "$INSTALLED" ] && [ "$(git rev-parse HEAD)" = "$INSTALLED" ]; then git reset --hard "$PREV"; else
  [ ! -d "$BACKUP/files" ] || cp -a "$BACKUP/files/." "$REPO/"
  while IFS= read -r p; do [ -z "$p" ] || rm -f -- "$p"; done < "$BACKUP/absent-before.txt"
fi
docker rm -f jarvis-speaker-verifier >/dev/null 2>&1 || true
docker compose up -d --build
echo "Rollback complete. speaker-data/jarvis_speakers.db was preserved."
