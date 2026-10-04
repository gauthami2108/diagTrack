#!/bin/sh
# Sets a new password for any login. Usage: ./scripts/reset-password.sh admin
. "$(dirname "$0")/_common.sh"
[ -n "$1" ] || { echo "Usage: ./scripts/reset-password.sh <user name>"; exit 1; }
docker compose exec web python manage.py changepassword "$1"
