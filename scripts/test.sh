#!/usr/bin/env bash
. "$(dirname "$0")/_common.sh"
require_setup
docker compose run --rm web python manage.py test "${@:-tickets}"
