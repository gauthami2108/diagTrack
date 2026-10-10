#!/bin/sh
# Installs the newest version from GitHub: takes a backup first, downloads the changes,
# then rebuilds and restarts. The data folder, .env and the secrets folder are never touched.
. "$(dirname "$0")/_common.sh"
echo "1 of 3: taking a backup first..."
if [ -s secrets/db_password ]; then
    docker compose exec backup python manage.py backup || { echo "The backup failed, so the update was not started."; exit 1; }
else
    echo "   Skipped: this DiagTrack was installed by an earlier version, which made its own nightly backups."
fi
echo "2 of 3: downloading the new version..."
if [ -d .git ]; then
    git pull --ff-only || { echo "Could not download the update. See docs/04-RUNBOOK.md, section 8."; exit 1; }
else
    echo "This folder was not installed with git. Copy the new files over it, keeping 'data', 'secrets' and '.env'."
fi
require_setup
echo "3 of 3: rebuilding and restarting (a few minutes)..."
docker compose up -d --build || exit 1
echo "Update applied. Check with:  docker compose run --rm setup verify"
