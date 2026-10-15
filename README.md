# DiagTrack

The internal service desk for Pramodini's diagnostic centres. Staff report a problem, DiagTrack sends it to the right
department, starts a deadline clock, alerts the right people and records everything until it is fixed. It runs on
your own server, natively on Windows or in Docker; ticket data never leaves the organisation.

## What it does

- Raise, route, assign, transfer, hold, resolve and close tickets, with SLA deadlines and escalation.
- A Jira-style ticket page: status menu, activity with comments and history, formatting, @mentions, edits.
- Live duplicate ticket detection when creating tickets, with direct links to existing open issues.
- Bulk ticket operations in queue and list views (assign to me, start work, priority changes, close, cancel).
- Phone home-screen app (PWA) with offline shell caching and service worker (`/sw.js`).
- Live updates via Server-Sent Events (SSE) and pop-ups; in-app, email, SMS, WhatsApp, and Telegram alerts with on/off switches.
- Scheduled Monday management email summaries (missed deadlines, equipment downtime, and centre backlogs).
- Centralized system observability via Prometheus `/metrics` exporter.
- Centres page: load, service levels and equipment uptime for every centre side by side.
- Reports by centre, department, category, priority, owner or equipment, as a six-sheet Excel file or a printed PDF.
- Equipment register with downtime, vendor call log and response times, AMC renewal alerts, repeat faults, QR labels, preventive maintenance (PM) schedules with auto-ticket creation, and CAMC uptime guarantee & penalty calculations.
- Dedicated native Masters and rules management hub (`/masters/`) with multi-line pill navigation across all 13 admin tables, auto-submitting filters, multi-column sorting, and modal dialogs.
- Board, dashboard, search and jump (Ctrl K), ticket preview, light and dark themes, Pramodini logo and colours.
- Import from Excel: centres, vendors, departments, people and equipment from one workbook, checked before saving.
- Sign-in pauses after repeated wrong passwords, and active session termination across other devices on password change or from profile.
- Historical ticket archiving (`manage.py archive_tickets`) for long-term data retention.
- Encrypted backups to one or more folders on a schedule, checks and monthly test restores, one-click restore, and
  moving between Windows and Docker servers.

## Start

Two ways to install; both ask every password at the start, install, check and report. Details: [Install guide](docs/01-INSTALL.md).

| Windows Server 2016 to 2025, Windows 11 | Linux or Mac with Docker already installed |
| --- | --- |
| Download the zip from GitHub, extract it, double-click **Setup.cmd** | `git clone https://github.com/gauthami2108/diagTrack.git && cd diagTrack` |
| Setup installs Python and PostgreSQL 17, the DiagTrack Windows service, the firewall rule and a backup safety net | `docker compose run --rm setup`, then `docker compose up -d`, then `docker compose run --rm setup verify` |

Open the address you gave during setup and sign in with the administrator password you chose.

## Guides

| You are | Read |
| --- | --- |
| The person who installs and looks after the server | [Install guide](docs/01-INSTALL.md), then the [Runbook](docs/04-RUNBOOK.md) |
| The DiagTrack administrator | [Administrator guide](docs/02-ADMIN-MANUAL.md) |
| Staff, agents, department heads and managers | [Staff guide](docs/03-USER-GUIDES.md) |
| A software developer | [Technical reference](docs/05-TECHNICAL.md) |

## Everyday tasks

Backups, restores and moving to another server are buttons on the **Backups** page. Everything else is in the
[Runbook](docs/04-RUNBOOK.md), section 3, for both kinds of install.

## What is in this folder

| Item | What it is |
| --- | --- |
| `app/` | The application |
| `Setup.cmd`, `installer/` | Setup for Windows (native) and Docker |
| `docker/`, `Dockerfile`, `docker-compose.yml` | How the containers are built and run |
| `scripts/` | Update, restore, password reset and other shortcuts |
| `docs/` | The guides |
| `.env.example` | Docker settings; setup writes `.env` |
| `data/`, `secrets/` | Docker only: **your data** and passwords. Created by setup. Never delete them |
