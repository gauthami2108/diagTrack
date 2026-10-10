# Install guide

For the person who sets up the server. It takes about an hour. You do not need to know programming:
type each command exactly as shown, one line at a time, and press Enter after each.

## What you need

| Item | Requirement |
| --- | --- |
| Server | A PC or server that stays on all the time: 4 CPU cores, 8 GB memory, 200 GB SSD, connected to a UPS |
| Operating system | Ubuntu Server 24.04 LTS. Other Linux systems that run Docker also work |
| Network | A fixed IP address inside your network, for example 192.168.1.50. Your network vendor sets this |
| Internet | Needed while installing and updating. Daily use works without internet, except SMS and WhatsApp alerts |
| NAS | A shared folder on your NAS for the nightly backup |
| GitHub access | The repository `gauthami2108/diagTrack` is private, so you need a GitHub login that can read it |

## Step 1. Install Docker and git

Docker runs DiagTrack. Git downloads it and its updates. On the server:

```
curl -fsSL https://get.docker.com | sudo sh
sudo apt install -y git
sudo usermod -aG docker $USER
```

Log out and log in again, then check that both work:

```
docker compose version
git --version
```

Each prints a version number.

## Step 2. Download DiagTrack

```
cd /opt
sudo git clone https://github.com/gauthami2108/diagTrack.git diagtrack
sudo chown -R $USER /opt/diagtrack
cd /opt/diagtrack
```

Git asks for a user name and password. Use the GitHub user name and, as the password, a **personal access token**:
on github.com, open your picture, Settings, Developer settings, Personal access tokens, Fine-grained tokens,
Generate new token, give it read access to the `diagTrack` repository, and copy the token it shows.

Every command from now on is typed inside the `/opt/diagtrack` folder.

## Step 3. Start

```
docker compose up -d
```

The first start downloads and builds everything: 5 to 10 minutes. Nothing has to be set up first; a secret key is
created automatically. Then check:

```
docker compose ps
```

You should see five lines, `proxy`, `web`, `worker`, `db` and `backup`, each saying `Up`, and `web` also saying
`(healthy)`. If `web` says `(health: starting)`, wait a minute and check again.

From now on DiagTrack starts by itself whenever the server starts, and restarts any part that stops.

## Step 4. Sign in for the first time

1. On any computer in the same network, open a browser and go to `http://<server IP address>`, for example
   `http://192.168.1.50`.
2. Sign in with user name `admin` and password `ChangeMe@123`.
3. Change that password straight away: top right, your name, **Change password**.
4. Open **Settings and health** and fill in "address of this system" with the same address you typed in the
   browser. Emails and the equipment QR labels use it.
5. Continue with the Administrator guide, section 1, "First-day setup".

## Step 5. Connect the NAS for backups

Until you do this, the nightly backup is kept on the server itself, in `data/backups`. That does not protect you if
the server's disk fails, so do this step on the first day.

1. On the NAS, create a shared folder, for example `diagtrack-backup`, and a NAS user that can write to it.
2. On the server, create a place to attach it:

   ```
   sudo mkdir -p /mnt/nas-diagtrack
   sudo nano /etc/fstab
   ```

3. Add one line at the end of that file. For a Windows-style (SMB) share, replace the NAS address, share name,
   user and password with yours:

   ```
   //192.168.1.20/diagtrack-backup  /mnt/nas-diagtrack  cifs  username=NASUSER,password=NASPASSWORD,uid=0,gid=0,nofail  0  0
   ```

   For an NFS share the line is instead:

   ```
   192.168.1.20:/volume1/diagtrack-backup  /mnt/nas-diagtrack  nfs  defaults,nofail  0  0
   ```

   Save with Ctrl+O, Enter, then close with Ctrl+X.

4. Attach it, and leave a marker file that proves the NAS is the one attached:

   ```
   sudo apt install -y cifs-utils nfs-common
   sudo mount -a
   sudo touch /mnt/nas-diagtrack/.diagtrack-backup-target
   ```

5. Tell DiagTrack to use it. Create the settings file from the example and open it:

   ```
   cp .env.example .env
   nano .env
   ```

   Change these two lines, then save and close:

   ```
   BACKUP_DIR=/mnt/nas-diagtrack
   BACKUP_REQUIRE_MARKER=1
   ```

6. Apply the change and make a first backup:

   ```
   docker compose up -d
   docker compose exec backup sh /backup.sh now
   ```

   It prints `Backup completed`. In DiagTrack, **Settings and health** now shows the backup box in green.
   If the NAS is ever disconnected, the next backup is reported in red on that screen.

## Step 6. Let the other centres reach the server

The recommended way is a **site-to-site VPN** from each centre's router to the site where the server is. Staff at
every centre then open the same `http://<server address>`, nothing is installed on their computers, and DiagTrack is
never exposed to the internet. Your network vendor sets this up on the routers; DiagTrack needs no change.

If a centre's router cannot do a site-to-site VPN, install a WireGuard-based VPN client on that centre's computers and
on the server instead.

Do not open the server to the internet through the router without HTTPS (step 7) and without agreeing it with your
network vendor.

## Step 7. Optional: HTTPS

Inside a VPN, plain `http://` is acceptable. HTTPS encrypts traffic inside your own network too, and lets browsers
show DiagTrack alerts on the desktop even when the DiagTrack tab is in the background.

1. Choose a name for the server, for example `diagtrack.pramodini.local`, and ask your network vendor to point it
   to the server's IP address.
2. In `.env` (create it from `.env.example` if you have not yet) set:

   ```
   SITE_ADDRESS=https://diagtrack.pramodini.local
   HTTPS_MODE=tls internal
   ```

3. Run `docker compose up -d`.
4. Browsers warn about the certificate until DiagTrack's own certificate is installed on each computer. The file is
   `data/caddy/caddy/pki/authorities/local/root.crt`; your IT vendor can install it on the computers.
5. Change "address of this system" in Settings to the new `https://` address.

## Step 8. Optional: try it with demo data first

On a new, empty installation you can load three demo centres, about 20 staff logins and 50 tickets:

```
docker compose exec web python manage.py seed_demo
```

Every demo login uses the password `Demo@12345`, for example `srinivas` (department head), `ravi` (agent),
`meena` (requester), `kavitha` (area manager) and `director` (management).

To go live afterwards with a clean system:

```
docker compose down
sudo rm -rf data/postgres data/media
docker compose up -d
```

## The settings file

DiagTrack works without a settings file. Create `.env` from `.env.example` only to change one of these:

| Setting | Default | What it does |
| --- | --- | --- |
| `BACKUP_DIR` | `./data/backups` | Where backups are written: your NAS folder |
| `BACKUP_REQUIRE_MARKER` | `0` | Set to `1` once the NAS is attached, so a disconnected NAS is reported |
| `BACKUP_TIME` | `01:30` | Time of the nightly backup, 24-hour clock |
| `BACKUP_KEEP_DAYS` | `30` | How many days of database copies to keep |
| `SITE_ADDRESS`, `HTTPS_MODE` | plain http | Address and HTTPS, see step 7 |
| `HTTP_PORT`, `HTTPS_PORT` | `80`, `443` | Change only if another program on the server uses these ports |
| `ADMIN_USERNAME`, `ADMIN_PASSWORD` | `admin`, `ChangeMe@123` | The first administrator. Used only on the very first start |
| `DB_PASSWORD` | `diagtrack` | Database password. Set it before the first start, never change it afterwards |
| `TIME_ZONE` | `Asia/Kolkata` | Time zone for deadlines and reports |

After changing `.env`, run `docker compose up -d` to apply it.
