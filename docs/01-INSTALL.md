# Installation guide

This takes about an hour. You type commands into the server's terminal exactly as shown.
Lines in grey boxes are commands: type one line, press Enter, wait for it to finish.

## What you need

| Item | Requirement |
| --- | --- |
| Server | A PC or server that stays on: 4 CPU cores, 8 GB RAM, 200 GB SSD, on a UPS |
| Operating system | Ubuntu Server 24.04 LTS (other Linux systems with Docker also work) |
| Network | A fixed IP address inside your network, for example 192.168.1.50 |
| Internet | Needed during installation and updates, to download the building blocks. Not needed for daily use, except for SMS and WhatsApp alerts |
| NAS | A shared folder on the NAS for backups |

## Step 1. Copy DiagTrack to the server

Copy the `diagtrack` folder to the server, for example to `/opt/diagtrack`. Then open a terminal and go there:

```
cd /opt/diagtrack
```

Every later command is run from this folder.

## Step 2. Install Docker

Docker runs the five parts of DiagTrack. Install it once:

```
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER
```

Log out and log in again, then check:

```
docker compose version
```

It should print a version number.

## Step 3. Start

```
docker compose up -d
```

That is the only command. The first start downloads and builds everything and takes 5 to 10 minutes.
No settings file is needed: a secret key is created automatically and kept in `data/status`.

## Step 4. Check

```
docker compose ps
```

All five parts (proxy, web, worker, db, backup) should say `Up`, and `web` should say `(healthy)`.
From now on DiagTrack keeps running by itself, including after a power cut or reboot.

## Step 5. Sign in

On any computer in the same network, open a browser and go to `http://<server IP address>`, for example `http://192.168.1.50`.

Sign in with user name `admin` and password `ChangeMe@123`. Then:

0. Change the administrator password at once: top right, your name, **Change password**.

1. Open **Settings and health**. Set the organisation name and the address of this system (the same address you typed in the browser).
2. Open **Masters and rules** and add your centres, users and equipment. The administrator manual explains each.

## Step 6. Connect the NAS for backups

Until you do this, backups are kept on the server itself in `data/backups`, which does not protect you if the server's disk fails.

1. On the NAS, create a shared folder, for example `diagtrack-backup`.
2. On the server, create a place to attach it and attach it. For a Windows-style (SMB) share:

   ```
   sudo apt install -y cifs-utils
   sudo mkdir -p /mnt/nas-diagtrack
   sudo nano /etc/fstab
   ```

   Add one line at the end (replace the NAS address, share name, user and password), save with Ctrl+O, Enter, Ctrl+X:

   ```
   //192.168.1.20/diagtrack-backup  /mnt/nas-diagtrack  cifs  username=NASUSER,password=NASPASSWORD,uid=0,gid=0,nofail  0  0
   ```

   For an NFS share the line is instead:

   ```
   192.168.1.20:/volume1/diagtrack-backup  /mnt/nas-diagtrack  nfs  defaults,nofail  0  0
   ```

   Then attach it and create the marker file that proves the NAS is connected:

   ```
   sudo mount -a
   sudo touch /mnt/nas-diagtrack/.diagtrack-backup-target
   ```

3. Tell DiagTrack to use it. Create the settings file from the example and open it:

   ```
   cp .env.example .env
   nano .env
   ```

   Change these two lines, then save:

   ```
   BACKUP_DIR=/mnt/nas-diagtrack
   BACKUP_REQUIRE_MARKER=1
   ```

4. Apply and test:

   ```
   docker compose up -d
   docker compose exec backup sh /backup.sh now
   ```

   It should print `Backup completed`. The **Settings and health** screen now shows the backup in green.
   If the NAS is ever disconnected, the next backup is reported as failed in red on that screen.

## Step 7. Let the other centres reach the server

Recommended: a **site-to-site VPN** from each centre's router or firewall to the site where the server is.
The centres then open `http://<server IP address>` just like the main site, and DiagTrack is never exposed to the internet.
Your network vendor sets this up on the routers; nothing changes in DiagTrack.

If a centre's router cannot do this, install a WireGuard-based mesh VPN client on that centre's computers and on the server instead.

Do not forward the server's port from the internet router without HTTPS and without discussing it with your network vendor.

## Step 8. Optional: HTTPS

Inside a VPN, plain `http://` is acceptable. To encrypt traffic inside your network as well:

1. Choose a name for the server, for example `diagtrack.yourcompany.local`, and make it point to the server's IP address in your network's DNS (or router).
2. In `.env` set:

   ```
   SITE_ADDRESS=https://diagtrack.yourcompany.local
   HTTPS_MODE=tls internal
   ```

3. Run `docker compose up -d`.

Browsers will show a certificate warning until you install DiagTrack's own certificate on each computer.
The certificate file is `data/caddy/caddy/pki/authorities/local/root.crt`. Your IT vendor can install it on the computers.

## Step 9. Optional: try it with demo data first

On a **new, empty** installation you can load three demo centres, 20 staff logins and about 50 tickets:

```
docker compose exec web python manage.py seed_demo
```

Every demo login uses the password `Demo@12345` (for example `srinivas`, a department head, or `meena`, a requester).
To go live afterwards, stop DiagTrack, delete the `data/postgres` and `data/media` folders, and start again for a clean system.

## Start automatically after a power cut

Docker starts with the server, and each DiagTrack part is set to restart by itself. Nothing more is needed.
