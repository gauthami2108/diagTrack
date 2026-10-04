#!/bin/sh
# Restores the database and attachments from a backup.
# Usage:  ./scripts/restore.sh                      (lists the backups)
#         ./scripts/restore.sh diagtrack-20261004-0130.dump
# Everything entered after that backup was taken is lost. The script asks before it starts.
. "$(dirname "$0")/_common.sh"
if [ -z "$1" ]; then
    echo "Available backups, newest last:"
    docker compose exec backup sh -c 'ls -1 /backups/db | grep "\.dump$"' || echo "(none found)"
    echo
    echo "Run again with one name, for example:  ./scripts/restore.sh diagtrack-20261004-0130.dump"
    exit 0
fi
NAME=$(basename "$1")
docker compose exec backup test -f "/backups/db/$NAME" || { echo "Backup $NAME was not found."; exit 1; }
echo "This replaces ALL current DiagTrack data with the backup $NAME."
printf "Type YES to continue: "
read -r ANSWER
[ "$ANSWER" = "YES" ] || { echo "Cancelled. Nothing changed."; exit 0; }

echo "Stopping the screens and the worker..."
docker compose stop proxy web worker
echo "Restoring the database..."
docker compose exec backup sh -c "PGPASSWORD=\"\$DB_PASSWORD\" pg_restore -h db -U \"\$DB_USER\" -d \"\$DB_NAME\" --clean --if-exists --no-owner /backups/db/$NAME" \
    || { echo "The restore reported errors. Read the lines above, then see docs/04-RUNBOOK.md, section 6."; }
echo "Restoring attachments..."
docker compose run --rm --no-deps -v "$(pwd)/data/media:/restore-media" --entrypoint sh backup -c 'cp -a /backups/media/. /restore-media/ 2>/dev/null || true'
echo "Starting DiagTrack again..."
docker compose up -d
echo "Done. Sign in and check that the latest tickets are what you expect."
