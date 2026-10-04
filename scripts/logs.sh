#!/bin/sh
# Shows recent messages. Usage: ./scripts/logs.sh          (everything)
#                               ./scripts/logs.sh worker   (one part: proxy web worker db backup)
. "$(dirname "$0")/_common.sh"
docker compose logs --tail 200 "$@"
