#!/bin/sh
# Loads demo centres, users and tickets for a trial. Only works on an empty system.
# Every demo login uses the password Demo@12345. Do not use on the live system.
. "$(dirname "$0")/_common.sh"
docker compose exec web python manage.py seed_demo
