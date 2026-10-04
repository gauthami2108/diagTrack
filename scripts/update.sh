#!/bin/sh
# Applies a new version: takes a backup first, then rebuilds and restarts.
# Before running: copy the new files over the DiagTrack folder (keep the "data" folder and ".env").
. "$(dirname "$0")/_common.sh"
echo "Taking a backup first..."
docker compose exec backup /bin/sh /backup.sh now || { echo "Backup failed, so the update was not started."; exit 1; }
docker compose up -d --build || exit 1
echo "Update applied. Check with ./scripts/status.sh"
