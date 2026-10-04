# Runbook

For the person who looks after DiagTrack. All commands are run in a terminal on the server, from the DiagTrack folder:

```
cd /opt/diagtrack
```

## 1. Daily check (one minute)

Sign in as administrator and open **Settings and health**. All boxes at the top should be green.
That is the whole daily check. If one is red, find it in section 4.

## 2. Start, stop, status

| Task | Command |
| --- | --- |
| Is everything running? | `./scripts/status.sh` |
| Start (also safe if already running) | `./scripts/start.sh` |
| Stop, for example before moving the server | `./scripts/stop.sh` |
| See recent messages from all parts | `./scripts/logs.sh` |
| See messages from one part | `./scripts/logs.sh worker` (or `web`, `db`, `proxy`, `backup`) |

After a power cut or restart, DiagTrack starts by itself.

## 3. Backups

- A backup runs every night at 01:30 (change `BACKUP_TIME` in `.env`, then run `./scripts/start.sh`).
- It saves the database to `db/` and attachments to `media/` in the backup folder on the NAS.
- 30 days of database copies are kept (`BACKUP_KEEP_DAYS`).
- Make an extra backup at any time: `./scripts/backup-now.sh`

**Backup box is red**

| Message | What to do |
| --- | --- |
| The NAS is not connected: the marker file is missing | The NAS is off or not attached. Check the NAS is on, then run `sudo mount -a`, then `./scripts/backup-now.sh` |
| Cannot write to the backup folder | The NAS share is full or read-only. Free space or fix the share's permissions |
| The database copy failed | Run `./scripts/logs.sh backup` and read the last lines |
| The last backup is more than a day old | The backup part is not running. Run `./scripts/start.sh` |

**Once every three months, test a restore** on a spare computer: install DiagTrack there (install guide, steps 1 to 4), point `BACKUP_DIR` at a copy of the backup folder, and follow section 6. A backup that has never been restored is not yet proven.

## 4. When something is wrong

| What you see | Likely cause | What to do |
| --- | --- | --- |
| Browser says the site cannot be reached | Server off, or DiagTrack stopped | Check the server is on. Run `./scripts/status.sh`, then `./scripts/start.sh` |
| Only other centres cannot reach it | VPN or network between centres | Call the network vendor. DiagTrack itself is fine if the main site can open it |
| `status.sh` shows a part as `Restarting` or `Exited` | That part is failing | `./scripts/logs.sh <part>` and read the last lines. Then `./scripts/start.sh` |
| **Background worker** box is red | The worker stopped | `./scripts/start.sh`. If it stops again: `./scripts/logs.sh worker` |
| Tickets are not escalating, no alerts arrive | Worker stopped, or channels switched off | Check the worker box, then the switches under Alert channels |
| **Alerts sent out** box is red | Mail server or gateway details wrong | Read the reason shown. Fix it in Settings, then use the Send me a test button |
| Emails do not arrive but the box is green | Email address missing on the user, or email switched off | Check the user's email under Masters and rules, Users, and the Email alerts switch |
| "Server Error (500)" on a screen | A fault in the application | `./scripts/logs.sh web`, copy the last 30 lines, and send them to whoever supports the software |
| A photo will not upload | Larger than the limit | Raise "Largest attachment" in Settings (up to about 50 MB) |
| Disk is filling up | Attachments and backups grow | Check with `df -h`. Attachments are in `data/media`, the database in `data/postgres` |
| `web` never becomes healthy on first start | The database password in `.env` was changed after the first start | Put the original password back in `.env`. Never change `DB_PASSWORD` after the first start |

## 5. Passwords

| Task | How |
| --- | --- |
| A user forgot their password | Masters and rules, Users, open the user, use the link in the password line |
| The administrator password is lost | `./scripts/reset-password.sh admin` and type a new one twice |

## 6. Restore from a backup

Use this after a disk failure, or to move DiagTrack to a new server.

On a **new server**, first do the install guide steps 1 to 4, then step 6 so the NAS is attached. Then:

1. List the backups:

   ```
   ./scripts/restore.sh
   ```

2. Restore one (normally the newest, at the bottom of the list):

   ```
   ./scripts/restore.sh diagtrack-20261004-0130.dump
   ```

3. Type `YES` when asked. Wait for "Done".
4. Sign in and check the newest tickets.

Everything entered after that backup was taken is lost, so the newest backup is normally the right one.
Warnings that mention "does not exist" during the restore are normal. If the script reports other errors, run `./scripts/logs.sh db` and send the last lines to whoever supports the software. Your backup file is not changed by a failed restore.

## 7. Updating to a new version

1. Copy the new files over the DiagTrack folder. **Keep** the `data` folder and the `.env` file.
2. Run:

   ```
   ./scripts/update.sh
   ```

It takes a backup first, then rebuilds and restarts. Users are interrupted for about a minute.

## 8. Housekeeping

| When | Task |
| --- | --- |
| Daily | Look at Settings and health |
| Monthly | Run `df -h` and check the disk is under 80% full. Install operating system updates: `sudo apt update && sudo apt upgrade` and restart the server out of hours |
| Every 3 months | Test a restore (section 3) |
| When staff join or leave | Add the user, or untick Active |
| When a centre opens | Admin manual, section 1 |
