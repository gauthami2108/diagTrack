# Technical reference

For a developer who needs to change or extend DiagTrack.

## Stack

| Layer | Choice |
| --- | --- |
| Language and framework | Python 3.13, Django 6.1 (server-rendered templates) |
| Database | PostgreSQL 17 |
| Web server | Gunicorn behind Caddy 2 |
| Front end | Hand-written CSS and plain JavaScript. No build step, no CDN: fonts (IBM Plex) and icons (Lucide) are bundled, so it works without internet |
| Background jobs | One Django management command in a loop (`runworker`), once a minute |

## Containers (docker-compose.yml)

| Service | Image | Role | Data |
| --- | --- | --- | --- |
| proxy | caddy:2-alpine | Port 80/443, forwards to web | `data/caddy` |
| web | built from `Dockerfile` | Runs migrations and `setup_initial`, then Gunicorn | `data/media`, `data/status` |
| worker | same image | `manage.py runworker` | same |
| db | postgres:17-alpine | Database | `data/postgres` |
| backup | postgres:17-alpine | `docker/backup.sh`: nightly `pg_dump` and attachment copy | `BACKUP_DIR` |

All state is in bind mounts under `data/` plus the backup folder.

## Layout of `app/`

| Path | Contents |
| --- | --- |
| `config/settings.py` | Settings, all from environment variables |
| `config/urls.py` | Every URL |
| `tickets/models.py` | Data model |
| `tickets/services.py` | **All business rules**: visibility, permissions, routing, assignment, status changes, SLA clock, escalation. Views never change a ticket directly |
| `tickets/notify.py` | In-app alerts and the outbound queue for email, SMS, WhatsApp |
| `tickets/reports.py` | Home and dashboard numbers |
| `tickets/views.py` | Screens |
| `tickets/admin.py` | Masters (Django admin) |
| `tickets/management/commands/` | `runworker`, `setup_initial` (idempotent defaults), `seed_demo` |
| `tickets/tests.py` | Automated tests of the rules |
| `templates/`, `static/` | Screens, CSS, JavaScript, fonts, icons |

## Data model

| Model | Purpose |
| --- | --- |
| Centre | Code is the ticket prefix; `next_seq` is the per-centre counter (row-locked when a number is issued) |
| Department | Head, assignment mode, `handles_equipment`, round-robin pointer |
| Category, CategoryField | Routing, default priority, extra questions per category |
| User | Custom user: role, mobile, centres (M2M), departments (M2M) |
| Vendor, Equipment, DowntimeLog | Equipment register and downtime per ticket |
| SlaPolicy | Minutes per priority; department null = default |
| EscalationRule, EscalationLog | Rules by percent of response or resolution time; the log stops a rule firing twice for a ticket |
| Ticket | Includes SLA fields: `sla_started_at`, `response_due`, `resolution_due`, `hold_started_at`, `hold_seconds`, breach flags |
| TicketComment, TicketAttachment, TicketHistory | Replies and internal notes, files, audit trail |
| Notification, OutboundMessage | In-app alerts; queued email, SMS, WhatsApp with status and error |
| SiteSettings | Single row: channel switches, mail server, gateways, auto-close and reopen days |
| Heartbeat | Worker's last run, shown on the health screen |

## SLA rules as implemented

- 24x7 clock. `resolution_due = sla_started_at + minutes + time on hold`.
- Hold pauses the clock; resume adds the paused time to both deadlines.
- A transfer restarts the clock for the new department and clears its escalation log.
- A priority change recalculates the deadlines from the original start.
- First response = the agent starts work or sends a public reply.
- Response missed while unaccepted: the ticket is reassigned to the department head.
- Reopen: the time spent resolved is treated as paused time.
- Auto-close: `auto_close_days` after resolution. Reopen allowed for `reopen_days` after resolution.

## Developing

```
cd app
pip install -r requirements.txt
DEBUG=1 python manage.py migrate
DEBUG=1 python manage.py setup_initial
DEBUG=1 python manage.py seed_demo        # optional demo data
DEBUG=1 python manage.py runserver
DEBUG=1 python manage.py test             # run after every change
```

Without `DB_HOST` set, it uses a local SQLite file. Set `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, `DB_PASSWORD` to use PostgreSQL.
After changing `models.py`, run `python manage.py makemigrations` and commit the new migration file; the web container applies migrations at start.

## Environment variables

| Variable | Used by | Meaning |
| --- | --- | --- |
| `SECRET_KEY` | web, worker | Django secret |
| `DB_PASSWORD` | db, web, worker, backup | Database password. Fixed at first start |
| `ADMIN_USERNAME`, `ADMIN_PASSWORD` | web | First administrator, created only if none exists |
| `SITE_ADDRESS`, `HTTPS_MODE`, `HTTP_PORT`, `HTTPS_PORT` | proxy | Address and TLS |
| `BACKUP_DIR`, `BACKUP_TIME`, `BACKUP_KEEP_DAYS`, `BACKUP_REQUIRE_MARKER` | backup | Backup target and schedule |
| `TIME_ZONE` | all | Default Asia/Kolkata |
| `WEB_WORKERS` | web | Gunicorn processes, default 3 |

## Not in phase 1

Planned for later phases: vendor call log and AMC expiry alerts, uptime and repeat-fault reports, scheduled email reports, saved replies, child tickets, duplicate warning, calendar view, saved filters, @mentions, QR-code raising, preventive maintenance schedules, knowledge articles, archiving of tickets older than one year.
