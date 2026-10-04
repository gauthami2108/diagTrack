#!/bin/sh
# Starts DiagTrack (or applies changes after an update). Safe to run at any time.
. "$(dirname "$0")/_common.sh"
docker compose up -d --build || exit 1
echo
echo "DiagTrack is starting. It takes about a minute the first time."
echo "Check with:  ./scripts/status.sh"
