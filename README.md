# DiagTrack

The internal service desk for Pramodini's diagnostic centres. Staff report a problem, DiagTrack sends it to the right
department, starts a deadline clock, alerts the right people and records everything until it is fixed. It runs on
your own server in Docker; ticket data never leaves the organisation.

## What it does

- Raise, route, assign, transfer, hold, resolve and close tickets, with SLA deadlines and escalation.
- A Jira-style ticket page: status menu, activity with comments and history, formatting, @mentions, edits.
- Live pop-ups when tickets are raised, closed or escalated; in-app, email, SMS and WhatsApp alerts with on/off switches.
- Centres page: load, service levels and equipment uptime for every centre side by side.
- Reports by centre, department, category, priority, owner or equipment, as a six-sheet Excel file or a printed PDF.
- Equipment register with downtime, vendor call log and response times, AMC renewal alerts, repeat faults and QR labels.
- Board, dashboard, search and jump (Ctrl K), ticket preview, light and dark themes, Pramodini logo and colours.

## Start

On a server with Docker and git:

```
git clone https://github.com/gauthami2108/diagTrack.git
cd diagTrack
docker compose up -d
```

Open `http://<server address>`, sign in as `admin` with password `ChangeMe@123`, and change the password at once.

## Guides

| You are | Read |
| --- | --- |
| The person who installs and looks after the server | [Install guide](docs/01-INSTALL.md), then the [Runbook](docs/04-RUNBOOK.md) |
| The DiagTrack administrator | [Administrator guide](docs/02-ADMIN-MANUAL.md) |
| Staff, agents, department heads and managers | [Staff guide](docs/03-USER-GUIDES.md) |
| A software developer | [Technical reference](docs/05-TECHNICAL.md) |

## Everyday commands

Run these from the DiagTrack folder on the server.

| Task | Command |
| --- | --- |
| Is everything running? | `docker compose ps` |
| Start, or apply a settings change | `docker compose up -d` |
| Stop (no data is lost) | `docker compose down` |
| Back up now | `docker compose exec backup sh /backup.sh now` |
| Install the newest version | `./scripts/update.sh` |
| Restore a backup | `./scripts/restore.sh` |

## What is in this folder

| Item | What it is |
| --- | --- |
| `app/` | The application |
| `docker/`, `Dockerfile`, `docker-compose.yml` | How the five containers are built and run |
| `scripts/` | Update, restore, password reset and other shortcuts |
| `docs/` | The guides |
| `.env.example` | Optional settings. Copy to `.env` only to change a default |
| `data/` | **Your data**: database, attachments, backup status. Created on first start. Never delete it |
