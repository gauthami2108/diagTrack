# Runbook

For the person who looks after the DiagTrack server. Every command is typed on the server, inside the DiagTrack
folder. Start each session with:

```
cd /opt/diagtrack
```

## 1. Daily check (one minute)

Sign in as an administrator and open **Settings and health**. All four boxes at the top should be green. That is the
whole daily check. If one is red, find it in section 5.

## 2. What is where

| Folder or file | What it holds | Can it be deleted? |
| --- | --- | --- |
| `data/postgres` | The database: every ticket, user and setting | **Never** |
| `data/media` | Attachments, photos and logos people uploaded | **Never** |
| `data/status` | The backup result and the secret key | **Never** |
| `data/caddy` | HTTPS certificates | Only if HTTPS is off |
| `data/backups` | Backups, if no NAS is attached | Old copies are removed automatically |
| `.env` | Your settings, if you created it | Keep a copy somewhere safe |
| Everything else | The software, from GitHub | Replaced by updates |

DiagTrack runs as five parts, called containers:

| Part | Job |
| --- | --- |
| proxy | The front door: answers the browser on port 80 (and 443 with HTTPS) |
| web | The screens |
| worker | Every minute: checks deadlines, sends escalations, closes old resolved tickets, sends emails and messages. Once a day: AMC warnings |
| db | The PostgreSQL database |
| backup | The nightly backup to the NAS |

## 3. Everyday commands

| Task | Command |
| --- | --- |
| Is everything running? | `docker compose ps` |
| Start, or apply a change to `.env` | `docker compose up -d` |
| Stop, for example before moving the server | `docker compose down` |
| Restart one part | `docker compose restart worker` (or `web`, `proxy`, `db`, `backup`) |
| Recent messages from one part | `docker compose logs --tail 100 web` |
| Back up now | `docker compose exec backup sh /backup.sh now` |
| Install the newest version | `./scripts/update.sh` (section 8) |
| Restore a backup | `./scripts/restore.sh` (section 7) |
| Set a new password for a login | `./scripts/reset-password.sh admin` |

`docker compose ps` should show all five parts as `Up`, and `web` as `(healthy)`. After a power cut or a restart of
the server, everything starts by itself.

The `scripts` folder also has `start.sh`, `stop.sh`, `status.sh` and `logs.sh`: shortcuts that do the same as the
commands above, with plain-language messages.

## 4. Backups

- A backup runs every night at 01:30. To change the time, set `BACKUP_TIME` in `.env` and run `docker compose up -d`.
- It saves the whole database to `db/` and copies new attachments to `media/` in the backup folder on the NAS.
- 30 days of database copies are kept (`BACKUP_KEEP_DAYS`); older ones are removed automatically.
- Before every update, `./scripts/update.sh` makes an extra backup.

**When the backup box is red**

| Message | What to do |
| --- | --- |
| The NAS is not connected: the marker file is missing | The NAS is off or not attached. Switch the NAS on, run `sudo mount -a`, then back up now |
| Cannot write to the backup folder | The NAS share is full or read-only. Free space or fix the share's permissions |
| The database copy failed | Run `docker compose logs --tail 50 backup` and read the last lines |
| The last backup is more than a day old | The backup part has stopped. Run `docker compose up -d` |

**Every three months, prove the backup works** by restoring it on a spare computer: install DiagTrack there (Install
guide, steps 1 to 3), point `BACKUP_DIR` at a copy of the backup folder, and follow section 7. A backup that has never
been restored has not been proven.

## 5. When something is wrong

| What you see | Likely cause | What to do |
| --- | --- | --- |
| The browser cannot reach DiagTrack | The server is off, or DiagTrack stopped | Check the server is on. Run `docker compose ps`, then `docker compose up -d` |
| Only the other centres cannot reach it | The VPN or the network between centres | Call the network vendor. DiagTrack is fine if the main site can open it |
| A part shows `Restarting` or `Exited` | That part keeps failing | `docker compose logs --tail 100 <part>`, read the last lines, then `docker compose up -d` |
| `web` never becomes `(healthy)` after the first start | `DB_PASSWORD` in `.env` was changed after the first start | Put the original password back. Never change it after the first start |
| **Background worker** box is red | The worker stopped | `docker compose up -d`. If it stops again, `docker compose logs --tail 100 worker` |
| No escalations, no alerts | The worker stopped, or alert channels are off | Check the worker box, then the switches under Alert channels |
| **Alerts sent out** box is red | Wrong mail server or gateway details | Read the reason shown, correct it in Settings, use the test button |
| Emails do not arrive, but the box is green | The person has no email address, or email alerts are off | Check their email under Users, and the Email alerts switch |
| Pop-ups do not appear | Pop-ups switched off, or the browser tab was asleep | Check "Pop-up messages on screen" in Settings. Pop-ups arrive within about 15 seconds |
| No desktop alerts outside the browser | The site is on plain `http://` | Desktop alerts need HTTPS (Install guide, step 7). Pop-ups inside DiagTrack still work |
| QR labels open the wrong address | "Address of this system" is empty or wrong | Fix it in Settings, then print the labels again. The machine's page shows the address its label opens |
| An alert's wording is wrong, or an SMS shows `{something}` | A template was changed, or a placeholder misspelt | Message templates, open the event, fix it or press **Back to built-in wording** |
| "Server Error (500)" on a screen | A fault in the software | `docker compose logs --tail 50 web`, copy the lines and send them to whoever supports the software |
| A photo will not upload | Larger than the limit | Raise "Largest attachment" in Settings, up to 50 MB |
| The disk is filling up | Attachments and the database grow | `df -h` shows space. Add disk space before it passes 80% |

## 6. Passwords

| Task | How |
| --- | --- |
| A user forgot their password | Masters and rules, Users, open the person, use the link in the password line |
| The administrator password is lost | `./scripts/reset-password.sh admin`, then type the new password twice |
| Someone left | Untick Active on their user. Do not delete them |

## 7. Restore from a backup

Use this after a disk failure, or to move DiagTrack to a new server. Everything entered after that backup was taken is
lost, so normally use the newest backup.

On a **new server**, first follow the Install guide steps 1 to 3 and step 5, so the NAS is attached. Then:

1. List the backups:

   ```
   ./scripts/restore.sh
   ```

2. Restore one, normally the newest, at the bottom of the list:

   ```
   ./scripts/restore.sh diagtrack-20261010-0130.dump
   ```

3. Type `YES` when asked and wait for "Done".
4. Sign in and check that the newest tickets are there.

Warnings that say something "does not exist" during the restore are normal. If other errors appear, run
`docker compose logs --tail 50 db` and send the lines to whoever supports the software. A failed restore does not
change the backup file.

## 8. Installing a new version

When a new version is pushed to GitHub:

```
./scripts/update.sh
```

It makes a backup, downloads the new version (`git pull`), then rebuilds and restarts. Users are interrupted for about a
minute, so do it out of hours. Your data folder and `.env` are never touched.

If the download fails with a message about local changes, someone edited a file in the folder. Run
`git status` to see which, then `git checkout -- <file>` to undo it, and run the update again.

## 9. Moving to a new server

1. On the old server: `docker compose exec backup sh /backup.sh now`, then `docker compose down`.
2. On the new server: Install guide steps 1 to 3, and step 5 pointing at the same NAS folder.
3. Restore the newest backup (section 7).
4. Ask the network vendor to give the new server the old server's IP address, or update "address of this system" in
   Settings and reprint the QR labels.

## 10. Housekeeping calendar

| When | Task |
| --- | --- |
| Every day | Look at Settings and health |
| Every month | `df -h`: keep the disk under 80% full. Install operating system updates out of hours: `sudo apt update && sudo apt upgrade`, then restart the server |
| Every 3 months | Test a restore (section 4) |
| When staff join or leave | Add the user, or untick Active |
| When a centre opens | Administrator guide, section 2 |
| When an AMC is renewed | Change the AMC expiry date on the machine |
