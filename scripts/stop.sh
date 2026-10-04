#!/bin/sh
# Stops DiagTrack. No data is lost. Start again with ./scripts/start.sh
. "$(dirname "$0")/_common.sh"
docker compose down
