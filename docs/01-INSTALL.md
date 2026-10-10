# Install guide

For the person who sets up the server. You do not need to know programming. There are two ways to install
DiagTrack; choose one:

| | A. Windows, native install | B. Docker |
| --- | --- | --- |
| Runs on | Windows Server 2016, 2019, 2022, 2025 or Windows 11 | Any Linux server or Mac with Docker installed |
| Prerequisites | Installed by setup: Python and PostgreSQL 17 | Docker, installed by you beforehand |
| Start | Double-click `Setup.cmd` | `docker compose run --rm setup`, then `docker compose up -d` |
| Pramodini | **The main server (Windows Server 2016)** | Available for a Linux server later |

Both ask every password and setting at the start, then install without stopping, check that everything works, and
finish with a report and a **recovery sheet**. Backups from one kind of install restore on the other.

## What you need for either way

| Item | Requirement |
| --- | --- |
| Server | Stays on all the time, on a UPS. Main server: 4 CPU cores, 8 GB memory, 100 GB SSD. Minimum: 2 cores, 4 GB, 20 GB free |
| Network | A fixed IP address inside your network, for example 192.168.1.50. Your network vendor sets this |
| Internet | Needed while installing and updating. Daily use works without internet, except SMS and WhatsApp alerts |
| Backup place | A folder on a NAS (recommended), a second disk or a USB drive. Not only the server's own disk |
| Passwords to decide | The DiagTrack administrator password (10+ characters, letters and a digit), and the **backup passphrase** (12+ characters; a short sentence is best). Setup creates every other password itself |

---

## A. Windows: native install

### A1. Before you start

- **Windows Server 2016.** Microsoft stops security updates on **12 January 2027**. Plan the move to Server 2022 or
  2025 before then; DiagTrack's move feature (Runbook, section 9) makes that a backup and restore.
- **First install on a server that GitHub's automatic tests do not cover** (Server 2016, Windows 11): take a full
  system backup or a VM snapshot of the server first, and run setup in a quiet hour.
- Sign in to the server as a member of the Administrators group.
- If the backup folder is on a NAS, have the share address (for example `\\nas01\backups\diagtrack`) and, if it needs
  one, a NAS user name and password with write access.
- Port 80: if IIS or another program already uses it, setup suggests 8080 instead.

### A2. Get DiagTrack onto the server

1. On github.com, open the `gauthami2108/diagTrack` repository, press **Code**, then **Download ZIP**.
2. Copy the zip to the server, right-click it, **Properties**, tick **Unblock** if it is shown, **OK**.
3. Right-click the zip, **Extract All**, for example to `C:\DiagTrack-Setup`.

### A3. Run setup

1. In the extracted folder, double-click **Setup.cmd**.
   - If Windows shows "Windows protected your PC", press **More info**, then **Run anyway**. (The file is not
     code-signed yet.)
   - Windows asks "Do you want to allow this app to make changes?". Press **Yes**.
2. Setup installs its own Python (about 2 minutes) and then asks: **New install** or **Move in from another DiagTrack
   server**. Choose **New install**.
3. Answer the questions:

   | Question | What to answer |
   | --- | --- |
   | Administrator user name and password | Usually `admin`, and a password of your choice, typed twice |
   | Web port | 80, or the suggested port if 80 is in use |
   | Address staff will type | For example `http://diagtrack.pramodini.local` or `http://192.168.1.50`. Used in emails and on QR labels |
   | Backup folder | The NAS share, for example `\\nas01\backups\diagtrack`, or a folder on a second disk |
   | NAS user name and password | Only if the share needs a login |
   | Second backup folder | Optional, for example another centre's NAS |
   | Daily backup time | 01:30 unless you prefer another time |
   | Backup passphrase | A sentence you will not forget, typed twice. It encrypts every backup |

4. Check the summary and answer **Y**. Setup now works on its own for about 10 to 15 minutes:

   | Step | What happens |
   | --- | --- |
   | PostgreSQL 17 | Downloaded from EnterpriseDB, its signature checked, installed as a Windows service |
   | DiagTrack | Program copied to `C:\Program Files\DiagTrack`, data folder `C:\ProgramData\DiagTrack` created |
   | Database | Tables created, starting departments and the administrator added |
   | Service | The **DiagTrack** Windows service is registered to start with Windows and restart if it stops. The web port is opened in Windows Firewall. A daily safety-net backup task is added to Task Scheduler |
   | Check | The screens answer, the background worker runs, the administrator can sign in, and a first backup is written and checked |

5. At the end setup shows a report (also saved as `C:\ProgramData\DiagTrack\setup-report.txt`) and offers the
   **recovery sheet**: print it, or write it down. It holds the backup passphrase and the database password. Keep it
   in a locked drawer; tell two people where it is.

If setup stops, it says what failed, the likely cause, what to do, and an error code such as `DT-S04`. Fix the cause
and run **Setup.cmd** again: it continues where it stopped.

### A4. Sign in

1. On any computer in the network, open a browser at the address you gave, for example `http://192.168.1.50`.
2. Sign in with the administrator user name and password from setup.
3. Continue with the Administrator guide, section 1, "First-day setup".

On the server's desktop setup also leaves **Open DiagTrack** and **DiagTrack Backups** shortcuts.

---

## B. Docker (Linux or Mac)

### B1. Install Docker yourself

Follow Docker's own guide for your system: Docker Engine on Linux, Docker Desktop on a Mac. Then check, in a terminal:

```
docker compose version
```

It must print version 2.24 or newer. Also install `git` if you will download with it.

### B2. Download DiagTrack

```
sudo git clone https://github.com/gauthami2108/diagTrack.git /opt/diagtrack
sudo chown -R $USER /opt/diagtrack
cd /opt/diagtrack
```

Git asks for a GitHub user name and, as the password, a personal access token with read access to the repository
(github.com, your picture, Settings, Developer settings, Personal access tokens, Fine-grained tokens).
Every command from now on is typed inside `/opt/diagtrack`.

### B3. Attach the NAS (recommended, before setup)

1. Create a shared folder on the NAS and a NAS user that can write to it.
2. On the server:

   ```
   sudo mkdir -p /mnt/nas-diagtrack
   sudo nano /etc/fstab
   ```

   Add one line (SMB share; replace address, share, user and password):

   ```
   //192.168.1.20/diagtrack-backup  /mnt/nas-diagtrack  cifs  username=NASUSER,password=NASPASSWORD,uid=1000,gid=1000,nofail  0  0
   ```

   `uid=1000` lets DiagTrack's backup container write there. Save (Ctrl+O, Enter) and close (Ctrl+X), then:

   ```
   sudo apt install -y cifs-utils
   sudo mount -a
   ```

### B4. Run setup

```
docker compose run --rm setup
```

The first time this builds DiagTrack (5 to 10 minutes). Then it asks the same questions as the Windows setup.
For the backup folder, type the NAS mount, for example `/mnt/nas-diagtrack`. Setup writes:

- `.env`: settings, no passwords;
- `secrets/`: one file per password, read only by the DiagTrack containers. **Never delete this folder**; keep it out
  of reach of other users of this computer;
- the backup schedule.

### B5. Start and check

```
docker compose up -d
docker compose run --rm setup verify
```

`verify` checks the screens, the database, the worker and makes a first backup. It ends with "Everything works".
From now on DiagTrack starts by itself with the computer.

### B6. Upgrading a DiagTrack installed with the first Docker version

The first version kept its passwords in `.env`. The new version keeps them in the `secrets` folder, so after
downloading it, `./scripts/start.sh` stops and asks you to run setup once:

```
docker compose run --rm --build setup
./scripts/start.sh
docker compose run --rm setup verify
```

Setup notices the existing database and keeps every ticket, user and attachment, and the logins stay the same. It
moves the database password and session key out of `.env` into `secrets`, and offers the port and backup settings
already in `.env` as its answers. It asks two new things: the address staff type and the backup passphrase. Backup files
made by the first version stay in the backup folder; delete them once the new backups have run for a month.

---

## Let the other centres reach the server (both ways)

The recommended way is a **site-to-site VPN** from each centre's router to the site where the server is. Staff at
every centre then open the same address, nothing is installed on their computers, and DiagTrack is never exposed to
the internet. Your network vendor sets this up on the routers; DiagTrack needs no change.

## Optional: HTTPS (Docker)

Inside a VPN, plain `http://` is acceptable and is the current choice. For Docker, HTTPS can be switched on later:

1. Ask your network vendor to point a name, for example `diagtrack.pramodini.local`, to the server.
2. In `.env` set `SITE_ADDRESS=https://diagtrack.pramodini.local` and `HTTPS_MODE=tls internal`, then run
   `docker compose up -d`.
3. Install DiagTrack's certificate (`data/caddy/caddy/pki/authorities/local/root.crt`) on each computer, and change
   "address of this system" in Settings.

## Optional: try it with demo data first

On a new, empty installation you can load three demo centres, about 20 staff logins and 50 tickets:

- Windows: open **Command Prompt as administrator** and type `diagtrack seed_demo`
- Docker: `docker compose exec web python manage.py seed_demo`

Every demo login uses the password `Demo@12345`, for example `srinivas` (department head), `ravi` (agent),
`meena` (requester), `kavitha` (area manager) and `director` (management). Do not load demo data on the live system.

## Where things are

| | Windows native | Docker |
| --- | --- | --- |
| Program | `C:\Program Files\DiagTrack` | the DiagTrack folder |
| Settings | `C:\ProgramData\DiagTrack\diagtrack.conf` | `.env` |
| Passwords | `C:\ProgramData\DiagTrack\secrets.dat`, encrypted for this computer | `secrets/` |
| Attachments | `C:\ProgramData\DiagTrack\media` | `data/media` |
| Database | PostgreSQL 17 Windows service `postgresql-x64-17` | `data/postgres` |
| Logs | `C:\ProgramData\DiagTrack\logs` | `docker compose logs` |
| Setup log and report | `C:\ProgramData\DiagTrack\setup.log`, `setup-report.txt` | `data/status/setup.log` |
