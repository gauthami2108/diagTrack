#!/bin/sh
# Shows whether each part is running, and the last backup result.
. "$(dirname "$0")/_common.sh"
docker compose ps --format "table {{.Service}}\t{{.Status}}"
echo
if [ -f data/status/backup.json ]; then
    echo "Last backup: $(cat data/status/backup.json)"
else
    echo "Last backup: none yet. Run ./scripts/backup-now.sh to make one."
fi
echo
echo "All five lines above should say 'Up'. 'web' should also say '(healthy)'."
