#!/bin/sh
# Starts either the web application or the background worker.
set -e

# The data folders are on the server's disk. Make sure the app user can write to them,
# then drop from root to that user.
if [ "$(id -u)" = "0" ]; then
    mkdir -p /data/media /data/status
    chown -R diagtrack:diagtrack /data/media /data/status
    exec setpriv --reuid=diagtrack --regid=diagtrack --init-groups "$0" "$@"
fi

# No SECRET_KEY given: create one on first start and keep it on disk, so nothing has to be set up by hand.
if [ -z "$SECRET_KEY" ]; then
    KEY_FILE=/data/status/.secret_key
    if [ ! -s "$KEY_FILE" ]; then
        python -c "import secrets; print(secrets.token_urlsafe(50))" > "$KEY_FILE"
        chmod 600 "$KEY_FILE"
    fi
    SECRET_KEY=$(cat "$KEY_FILE")
    export SECRET_KEY
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
  *)
    exec "$@"
    ;;
esac
