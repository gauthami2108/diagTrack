#!/bin/sh
# Makes a backup immediately (in addition to the nightly one).
. "$(dirname "$0")/_common.sh"
docker compose exec backup sh /backup.sh now
