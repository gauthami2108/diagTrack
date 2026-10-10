#!/bin/sh
# Starts one part of DiagTrack: web, worker, backups or setup.
set -e

if [ "$1" = "setup" ]; then
    shift
    exec python /installer/setup.py --docker "$@"
fi

# Setup writes the secrets folder. Without it there is no database password: say so plainly.
if [ ! -s /run/secrets/db_password ] && [ -z "$DB_PASSWORD" ]; then
    echo "DiagTrack is not set up yet. In the DiagTrack folder run:  docker compose run --rm setup"
    exit 1
fi

# The data folders are on the server's disk. Make sure the app user can write to them,
# then drop from root to that user.
if [ "$(id -u)" = "0" ]; then
    mkdir -p /data/media /data/status
    chown -R diagtrack:diagtrack /data/media /data/status
    # Backup folders: the app user must be able to write there. A NAS mounted with
    # uid=1000 already is; a local folder is handed over here.
    for d in /backups /backups2 /import; do
        [ -d "$d" ] && chown diagtrack:diagtrack "$d" 2>/dev/null || true
    done
    exec setpriv --reuid=diagtrack --regid=diagtrack --init-groups "$0" "$@"
fi

case "$1" in
  web)
    echo "Preparing the database..."
    python manage.py migrate --noinput
    python manage.py setup_initial
    echo "Starting DiagTrack."
    exec gunicorn config.wsgi --bind 0.0.0.0:8000 --workers "${WEB_WORKERS:-3}" --threads 4 \
         --timeout 90 --access-logfile - --error-logfile -
    ;;
  worker)
    exec python manage.py runworker
    ;;
  backups)
    exec python manage.py runbackups
    ;;
  *)
    exec "$@"
    ;;
esac
