#!/usr/bin/env bash
# Nightly backup of Jarvis (memory, knowledge, programs, config) and Hermes (memory, skills, sessions,
# config). Keeps 14 days in ~/backups. Secrets (.env) are included, so keep ~/backups private
# (chmod 700). To restore: stop the services, untar over $HOME, start them again.
set -euo pipefail
DEST="$HOME/backups"; mkdir -p "$DEST"; chmod 700 "$DEST"
STAMP="$(date +%Y%m%d-%H%M)"
tar -czf "$DEST/akjarvis-$STAMP.tgz" -C "$HOME" \
  --exclude='.hermes/hermes-agent' --exclude='.hermes/cache' --exclude='.hermes/logs' \
  --exclude='.jarvis/browser-profile' --exclude='*.log' \
  $( [[ -d "$HOME/.jarvis" ]] && echo .jarvis ) $( [[ -d "$HOME/.hermes" ]] && echo .hermes ) 2>/dev/null
chmod 600 "$DEST/akjarvis-$STAMP.tgz"
find "$DEST" -name 'akjarvis-*.tgz' -mtime +14 -delete
echo "$DEST/akjarvis-$STAMP.tgz"
