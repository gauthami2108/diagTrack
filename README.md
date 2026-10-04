# DiagTrack

Internal ticketing and SLA tracking for diagnostic centres. Runs on your own server
in Docker, with all data on local disk and a nightly backup to your NAS.

**Phase 1 (this package):** raise, route, assign, transfer, hold, resolve and close tickets;
SLA timers and escalation; in-app, email, SMS and WhatsApp alerts with on/off switches;
equipment register with downtime; board, dashboard and Excel export; administration screens.

## Start here

| I want to... | Read |
| --- | --- |
| Install it on the server | [docs/01-INSTALL.md](docs/01-INSTALL.md) |
| Add centres, departments, users, SLA times | [docs/02-ADMIN-MANUAL.md](docs/02-ADMIN-MANUAL.md) |
| Show staff how to use it | [docs/03-USER-GUIDES.md](docs/03-USER-GUIDES.md) |
| Do daily checks, fix a problem, restore a backup | [docs/04-RUNBOOK.md](docs/04-RUNBOOK.md) |
| Understand how it is built (for a developer) | [docs/05-TECHNICAL.md](docs/05-TECHNICAL.md) |

## Start it

On a server with Docker installed, from this folder:

```
docker compose up -d
```

That is the only step. The first run builds everything (5 to 10 minutes). Then open `http://<server address>` and sign in as `admin` with the password `ChangeMe@123`, and change that password straight away (top right, Change password).

It keeps running by itself: every part restarts after a crash, a power cut or a server reboot.

| Docker command | What it does |
| --- | --- |
| `docker compose up -d` | Start, or apply changes |
| `docker compose up -d --build` | Rebuild and restart after an update |
| `docker compose ps` | Is every part running? |
| `docker compose logs --tail 200 web` | Recent messages (or `worker`, `db`, `proxy`, `backup`) |
| `docker compose down` | Stop. No data is lost |
| `docker compose exec backup sh /backup.sh now` | Back up immediately |

The `scripts/` folder holds optional shortcuts for the same commands, plus `restore.sh` for restoring a backup.

## What is in this folder

| Item | What it is |
| --- | --- |
| `app/` | The application (do not edit) |
| `docker/`, `Dockerfile`, `docker-compose.yml` | How the containers are built and run (do not edit) |
| `scripts/` | The commands above |
| `docs/` | The guides |
| `.env` | Optional. Only needed to change a default such as the NAS folder (copy `.env.example`) |
| `data/` | **Your data**: database, attachments, backup status. Created on first start. Never delete |
