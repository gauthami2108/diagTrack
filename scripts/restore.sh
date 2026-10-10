#!/bin/sh
# Restores DiagTrack from a backup. Normally use the Backups page instead; this is for when the screens do not open.
# Usage:  ./scripts/restore.sh                                  lists backup folders and their backups
#         ./scripts/restore.sh dest1 <file name>.dtbak         restores one backup from folder dest1
#         ./scripts/restore.sh full <file name>.dtbak          restores a full copy placed in data/import
# Everything entered after that backup was made is replaced. The script asks before it starts.
. "$(dirname "$0")/_common.sh"
if [ -z "$2" ]; then
    docker compose exec backup python manage.py backup --list
    echo
    echo "Run again with a folder id and a file name, for example:"
    echo "  ./scripts/restore.sh dest1 diagtrack-pramodini-20261020-013000-scheduled.dtbak"
    exit 0
fi
if [ "$1" = "full" ]; then
    docker compose exec backup python manage.py restore "/import/$(basename "$2")"
else
    docker compose exec backup python manage.py restore --dest "$1" --name "$(basename "$2")"
fi
