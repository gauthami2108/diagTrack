# Runbook

For the person who looks after the DiagTrack server. Most tasks are buttons on the **Backups** and **Settings and
health** pages. This guide covers the rest, for both kinds of install:

- **Windows (native)**: commands are typed in **Command Prompt opened as administrator** (Start, type `cmd`,
  right-click, Run as administrator). The `diagtrack` command works in any such window.
- **Docker**: commands are typed in a terminal inside the DiagTrack folder (`cd /opt/diagtrack`).

## 1. Daily check (one minute)

Sign in as an administrator and open **Settings and health**. All four boxes at the top should be green. That is the
whole daily check. If one is red, find it in section 5.

## 2. What is where

| | Windows native | Docker |
| --- | --- | --- |
| Program | `C:\Program Files\DiagTrack` | the DiagTrack folder |
| Settings (no passwords) | `C:\ProgramData\DiagTrack\diagtrack.conf` | `.env` |
| Passwords | `C:\ProgramData\DiagTrack\secrets.dat`, encrypted for this computer | `secrets/` folder |
| Attachments | `C:\ProgramData\DiagTrack\media` | `data/media` |
| Database | Windows service `postgresql-x64-17` | `data/postgres` |
| Backup state, task progress | `C:\ProgramData\DiagTrack\status` | `data/status` |
| Logs | `C:\ProgramData\DiagTrack\logs` | `docker compose logs <part>` |

**Never delete** the data folders, `secrets.dat` or the `secrets` folder. Keep the recovery sheet safe: it holds the
backup passphrase.

On Windows, DiagTrack is one Windows service, **DiagTrack**, that runs the screens, the background worker and the
backups. It starts with Windows, after PostgreSQL, and restarts itself if it stops. In Docker the same work is split
into five containers: proxy, web, worker, db and backup.

## 3. Everyday tasks

| Task | Windows native | Docker |
| --- | --- | --- |
| Is everything running? | Services: **DiagTrack** and **postgresql-x64-17** say Running | `docker compose ps` |
| Restart | `sc stop DiagTrack` then `sc start DiagTrack` (or Services, right-click, Restart) | `docker compose restart` |
| Stop / start | `sc stop DiagTrack` / `sc start DiagTrack` | `docker compose down` / `docker compose up -d` |
| Recent messages | Open the newest file in `C:\ProgramData\DiagTrack\logs` | `docker compose logs --tail 100 web` (or worker, backup) |
| Back up now | Backups page, **Back up now**, or `diagtrack backup` | Backups page, or `./scripts/backup-now.sh` |
| List backups | Backups page, or `diagtrack backup --list` | Backups page, or `./scripts/restore.sh` |
| Check a backup | Backups page, **Check**, or `diagtrack verifybackup --dest dest1` | Backups page |
| Test restore | Backups page, **Test restore**, or `diagtrack verifybackup --dest dest1 --test-restore` | Backups page |
| Restore | Backups page, **Restore** (section 7) | Backups page (section 7) |
| New password for a login | `diagtrack changepassword admin` | `./scripts/reset-password.sh admin` |
| Install a new version | section 8 | section 8 |
| Check the whole install | Run **Setup.cmd**, choose **Repair** | `docker compose run --rm setup verify` |

## 4. Backups

**How they work.** Every backup holds the whole database and every attachment, is encrypted with the backup
passphrase, and is checked as it is made. Attachments are stored once in each backup folder, so a daily backup only
adds new photos. The Backups page shows each folder, its schedule, what it keeps and every backup in it.

| Feature | Detail |
| --- | --- |
| Folders | One or more: the NAS, another centre's NAS, a second disk. Each has its own schedule and retention |
| Schedule | Every few hours, daily, weekly or monthly, at a chosen time. A backup missed while the server was off runs within minutes of start-up |
| Retention | How many daily, weekly and monthly copies to keep, and how many days to keep manual ones. Older copies are removed only after a new backup has succeeded. The newest is never removed |
| Alerts | Administrators (and anyone ticked on the Backups page) are alerted when a backup fails, when none has succeeded for longer than allowed, and when a check fails |
| Weekly check | The newest backup is read back and its checksums compared; a sample of attachments is decrypted |
| Monthly test restore | The newest backup is restored into a scratch database, compared with its record, then removed. The page shows **Last proven restore** |
| Safety net (Windows) | A Task Scheduler task, **DiagTrack backup safety net**, runs every day at 06:15 and backs up any folder that has missed its schedule, even if the DiagTrack service was stopped |

**Manual backup.** **Back up now** on the Backups page, the **DiagTrack Backups** shortcut on the server's desktop,
or the commands in section 3. **Save a full copy** writes one self-contained file to a folder the server can reach,
for example a USB drive. It opens only with the backup passphrase.

**The passphrase.** It cannot be recovered and cannot be changed in place, because existing backups were encrypted
with it. If it may have leaked, ask whoever supports DiagTrack to set a new one and start new, empty backup folders;
remove the old folders once the new ones hold a month of backups.

**When the Backups status is red**

| Message | What to do |
| --- | --- |
| The NAS is not connected: the marker file is missing | The NAS is off or not attached. Switch the NAS on; on Docker run `sudo mount -a`. Then **Check folder** |
| Cannot write to `<folder>` | The share is full, read-only, or the login is wrong. Free space, fix the share's permissions, or retype the NAS password on the Backups page |
| Could not log on to `\\nas...` (Windows error 86 or 1326) | Wrong NAS user name or password. Fix it under **Change schedule, retention or folder** |
| This backup folder was started with a different backup passphrase | The folder belongs to another passphrase. Use the passphrase from the recovery sheet, or choose an empty folder |
| No backup passphrase is set | Run setup and choose **Repair**; it asks for it |
| pg_dump was not found | Windows: run setup, **Repair**. Docker: `docker compose up -d --build` |
| The last backup is more than a day old | The DiagTrack service (Windows) or the backup container (Docker) is stopped. Start it; the missed backup runs within minutes |

**Every three months, prove it on another computer.** Install DiagTrack on a spare computer using setup's
**Move in** option with a recent full copy. Sign in and look at a few tickets and photos. A backup that has never been
restored somewhere else has not been proven.

## 5. When something is wrong

| What you see | Likely cause | What to do |
| --- | --- | --- |
| The browser cannot reach DiagTrack | Server off, DiagTrack stopped, or the firewall | Check the server is on and the DiagTrack service (or `docker compose ps`) is running. Windows: run setup, **Repair** |
| Only the other centres cannot reach it | The VPN between centres | Call the network vendor |
| Every page says "DiagTrack is back shortly" | A restore is running | Wait; the page refreshes itself. If it stays for more than an hour, see the logs, then restart DiagTrack |
| A yellow bar says DiagTrack is read-only | A move to a new server was started | Backups page, **End move**, if the move is not going ahead |
| **Background worker** box is red | The service or worker container stopped | Restart DiagTrack. If it stops again, read the newest log |
| **Alerts sent out** box is red | Wrong mail server or gateway details | Read the reason shown, correct it in Settings, use the test button |
| Pop-ups or emails do not arrive | Alerts switched off, or the person has no email address | Check the switches in Settings and the person's details under Users |
| QR labels open the wrong address | "Address of this system" is empty or wrong | Fix it in Settings, then print the labels again |
| "Server Error (500)" on a screen | A fault in the software | Copy the last lines of the newest log and send them to whoever supports DiagTrack |
| A photo will not upload | Larger than the limit | Raise "Largest attachment" in Settings, up to 50 MB |
| The disk is filling up | Attachments, database, local backups | Add disk space before it passes 80% full. The Backups page shows free space in each backup folder |
| Windows: the DiagTrack service stops right after starting | PostgreSQL not running, or a damaged setting | Start **postgresql-x64-17** in Services, then DiagTrack. Otherwise run setup, **Repair** |
| Setup stopped with a code such as `DT-S04` | See the message: it names the cause and what to do | Fix it and run setup again; it continues where it stopped. The full log is `setup.log` |

## 6. Passwords

| Task | How |
| --- | --- |
| A user forgot their password | Masters and rules, Users, open the person, use the link in the password line |
| The administrator password is lost | Windows: `diagtrack changepassword admin`. Docker: `./scripts/reset-password.sh admin` |
| Someone left | Untick Active on their user. Do not delete them |
| "Sign-in is paused" after wrong passwords | It ends by itself after 15 minutes. Sooner: Masters and rules, Users, tick the person, **Unlock sign-in**. If the administrator is the one paused: Windows `diagtrack unlock admin`; Docker `docker compose exec web python manage.py unlock admin` (`--all` lifts every pause) |
| Repeated "Sign-in paused" alerts for names nobody uses | Someone is guessing passwords. Note the computer address in the alert and ask the network vendor to find it |

## 7. Restore from a backup

Use this after data was lost or damaged. Everything entered after the chosen backup was made is replaced.

1. Backups page: find the backup by date, press **Restore**, type `RESTORE`.
2. Staff see "DiagTrack is back shortly" for a few minutes. The backup is loaded **beside** the live database and
   checked against its record first; only then is it switched in. If anything fails, the live data is not changed.
3. Everyone signs in again afterwards. The database as it was just before is kept until the next restore, as a
   fallback for whoever supports DiagTrack.

When the screens cannot open at all:

- Windows: `diagtrack restore --dest dest1` (the newest backup), or add `--name <file>` for a specific one, or give
  the path of a full copy: `diagtrack restore E:\Copies\<file>.dtbak`.
- Docker: `./scripts/restore.sh` lists them; `./scripts/restore.sh dest1 <file>` restores one.

The restore asks you to type `YES`.

## 8. Installing a new version

| Windows native | Docker |
| --- | --- |
| 1. Download the new zip from GitHub and extract it, as in the Install guide, A2. | `./scripts/update.sh` |
| 2. Double-click **Setup.cmd** in the new folder and choose **Update**. | It backs up, downloads the new version with git, rebuilds and restarts |
| Setup backs up first, installs the new version beside the old, updates the database and starts DiagTrack. If the new version does not start, it puts the old version and the backup back by itself. | Check afterwards with `docker compose run --rm setup verify` |

Users are interrupted for a few minutes, so update out of hours. A Docker install made by the first version needs
setup once after the update: `docker compose run --rm --build setup` (Install guide, B6).

## 9. Moving to another server

Works in any direction: Windows Server 2016 to 2022, Windows to Docker, Docker to Windows.

1. **Old server, Backups page, Prepare to move.** Choose a folder (for example a USB drive) and type `MOVE`.
   DiagTrack becomes read-only for everyone and saves the move file. Note the checksum shown.
2. Copy the move file to the new server.
3. **New server: install with Move in.**
   - Windows: Setup.cmd, **Move in from another DiagTrack server**, give the file's path and the old backup
     passphrase.
   - Docker: put the file in `data/import`, run `docker compose run --rm setup`, choose **Move in**, then
     `docker compose up -d`.
   Setup checks the file, installs, restores, checks every count against the file, and reports.
4. Open the new server's address, sign in with your usual login, look at recent tickets and photos.
5. Point staff to the new server: keep the old address (ask the network vendor to move the name or IP address), or
   reprint the QR labels with the new address.
6. **Old server, Backups page, Retire**, once the new one has run well for a day or two. It stays read-only and stops
   its backups. Keep it for 30 days, then switch it off.

**If the move goes wrong** before you retire the old server: on the old server press **End move**, and everything
continues there as before. Nothing on the old server was changed.

## 10. Housekeeping calendar

| When | Task |
| --- | --- |
| Every day | Look at Settings and health |
| Every week | Glance at the Backups page: green, recent, weekly check passed |
| Every month | Disk under 80% full. Install Windows (or Linux) updates out of hours, then restart the server |
| Every 3 months | Restore a full copy on a spare computer (section 4) |
| When staff join or leave | Add the user, or untick Active |
| When an AMC is renewed | Change the AMC expiry date on the machine |
| **Before 12 January 2027** | Windows Server 2016 loses security updates: move to Server 2022 or 2025 (section 9) |
