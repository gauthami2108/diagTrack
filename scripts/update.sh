#!/bin/sh
# Installs the newest version from GitHub: takes a backup first, downloads the changes,
# then rebuilds and restarts. Your data folder and .env file are never touched.
. "$(dirname "$0")/_common.sh"
echo "1 of 3: taking a backup first..."
docker compose exec backup /bin/sh /backup.sh now || { echo "The backup failed, so the update was not started."; exit 1; }
echo "2 of 3: downloading the new version..."
if [ -d .git ]; then
    git pull --ff-only || { echo "Could not download the update. See docs/04-RUNBOOK.md, section 8."; exit 1; }
else
    echo "This folder was not installed with git. Copy the new files over it, keeping 'data' and '.env'."
fi
echo "3 of 3: rebuilding and restarting (about a minute)..."
docker compose up -d --build || exit 1
echo "Update applied. Check with: docker compose ps"
