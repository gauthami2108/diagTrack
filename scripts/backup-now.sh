#!/bin/sh
# Makes a backup immediately to every backup folder that is switched on (in addition to the schedule).
. "$(dirname "$0")/_common.sh"
docker compose exec backup python manage.py backup
