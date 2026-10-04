#!/bin/sh
# Nightly backup. Runs inside the "backup" container.
#   /backup.sh          wait for BACKUP_TIME every day, then back up (normal mode)
#   /backup.sh now      back up immediately, once
#
# What it saves into the backup folder (your NAS):
#   db/diagtrack-YYYYMMDD-HHMM.dump   the whole database
#   media/                            every attachment (only new files are copied)

BACKUPS=/backups
STATUS=/data/status/backup.json
KEEP_DAYS="${BACKUP_KEEP_DAYS:-30}"
export PGPASSWORD="$DB_PASSWORD"

report() {  # report true|false "message" [size]
    printf '{"ok": %s, "finished": "%s", "message": "%s", "size": "%s"}\n' \
        "$1" "$(date -Iseconds)" "$2" "$3" > "$STATUS.tmp" && mv "$STATUS.tmp" "$STATUS"
    echo "$(date '+%Y-%m-%d %H:%M') backup: $2"
}

run_backup() {
    if [ "${BACKUP_REQUIRE_MARKER:-0}" = "1" ] && [ ! -f "$BACKUPS/.diagtrack-backup-target" ]; then
        report false "The NAS is not connected: the marker file is missing in the backup folder."
        return 1
    fi
    mkdir -p "$BACKUPS/db" "$BACKUPS/media" || { report false "Cannot write to the backup folder."; return 1; }
    file="$BACKUPS/db/diagtrack-$(date +%Y%m%d-%H%M).dump"
    if ! pg_dump -h db -U "$DB_USER" -d "$DB_NAME" -Fc -f "$file.part"; then
        rm -f "$file.part"
        report false "The database copy failed. See: docker compose logs backup"
        return 1
    fi
    mv "$file.part" "$file"
    if ! cp -a -u /data/media/. "$BACKUPS/media/"; then
        report false "The database was saved but attachments could not be copied."
        return 1
    fi
    find "$BACKUPS/db" -name 'diagtrack-*.dump' -mtime +"$KEEP_DAYS" -delete
    count=$(ls "$BACKUPS/db" | grep -c '\.dump$')
    report true "Backup completed. $count database copies kept." "$(du -h "$file" | cut -f1)"
}

if [ "$1" = "now" ]; then
    run_backup
    exit $?
fi

echo "Backup service started. Daily backup at ${BACKUP_TIME:-01:30}, keeping $KEEP_DAYS days."
while true; do
    now=$(date +%s)
    target=$(date -d "$(date +%Y-%m-%d) ${BACKUP_TIME:-01:30}" +%s)
    [ "$target" -le "$now" ] && target=$((target + 86400))
    sleep $((target - now))
    run_backup
done
