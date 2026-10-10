# Technical reference

For a software developer who needs to change or extend DiagTrack. The other guides are for non-programmers.

## Stack

| Layer | Choice |
| --- | --- |
| Language and framework | Python 3.13, Django 6.1, server-rendered templates |
| Database | PostgreSQL 17 (SQLite when `DB_HOST` is not set, for local work) |
| Web server | Gunicorn behind Caddy 2 |
| Front end | Hand-written CSS (`static/css/app.css`) and plain JavaScript (`static/js/app.js`). No build step and no CDN: fonts (IBM Plex) and icons (Lucide) are bundled, so it works without internet |
| Background jobs | `manage.py runworker`: a loop that runs once a minute; AMC checks once a day |
| Live updates | The browser polls `/api/live/` every 15 seconds; no extra server |
| Excel and QR | `openpyxl` and `segno` |

Python packages are pinned in `app/requirements.txt`.

## Containers (`docker-compose.yml`)

| Service | Image | Role | Data |
| --- | --- | --- | --- |
| proxy | caddy:2-alpine | Ports 80 and 443, forwards to web | `data/caddy` |
| web | built from `Dockerfile` | `docker/entrypoint.sh web`: migrate, `setup_initial`, then Gunicorn | `data/media`, `data/status` |
| worker | same image | `docker/entrypoint.sh worker`: `manage.py runworker` | same |
| db | postgres:17-alpine | Database | `data/postgres` |
| backup | postgres:17-alpine | `docker/backup.sh`: nightly `pg_dump -Fc` and attachment copy, writes `data/status/backup.json` | `BACKUP_DIR` |

The entrypoint starts as root, fixes ownership of the data folders, then drops to user `diagtrack` (uid 1000). If
`SECRET_KEY` is empty it creates one in `data/status/.secret_key`.

## Layout of `app/`

| Path | Contents |
| --- | --- |
| `config/settings.py` | Settings, all from environment variables |
| `config/urls.py` | Every URL |
| `tickets/models.py` | The data model |
| `tickets/services.py` | **All business rules**: visibility, permissions, routing, assignment, status changes, SLA clock, escalation, vendor calls, AMC checks, mentions. Views never change a ticket directly |
| `tickets/notify.py` | In-app alerts and the outbound queue for email, SMS and WhatsApp |
| `tickets/wording.py` | Events, placeholders, built-in wording and the template renderer for email, SMS and WhatsApp |
| `tickets/reports.py` | Home, dashboard, centre matrix, equipment figures |
| `tickets/exports.py` | The Reports screen and the Excel workbook |
| `tickets/views.py` | Screens and JSON feeds |
| `tickets/admin.py` | Masters and rules (Django admin) |
| `tickets/templatetags/dt.py` | SLA bar, avatars, icons, the safe comment formatter (`rich`) |
| `tickets/mail.py` | Email backend that reads the SMTP details from Settings |
| `tickets/management/commands/` | `runworker`, `setup_initial` (idempotent defaults), `seed_demo` |
| `tickets/tests.py` | Automated tests of the rules and screens |
| `templates/`, `static/` | Screens, styles, script, fonts, icons, logo |

## Data model

| Model | Purpose |
| --- | --- |
| Centre | `code` is the ticket prefix; `next_seq` is the per-centre counter, row-locked when a number is issued |
| Department | Head, assignment mode, `handles_equipment`, round-robin pointer |
| Category, CategoryField | Routing, default priority, extra questions per category |
| User | Custom user: role, mobile, photo, centres (M2M), departments (M2M) |
| Vendor, Equipment | Equipment register; `amc_alert_for` and `amc_alert_stage` make each AMC warning go out once |
| DowntimeLog | Downtime per equipment ticket |
| VendorCall | Vendor called for a ticket: called and attended times, reference, engineer |
| SlaPolicy | Minutes per priority; department null means the default |
| EscalationRule, EscalationLog | Rules by percent of response or resolution time; the log stops a rule firing twice for a ticket |
| Ticket | Includes the SLA fields `sla_started_at`, `response_due`, `resolution_due`, `hold_started_at`, `hold_seconds` and the breach flags |
| TicketComment, TicketAttachment, TicketHistory | Comments and internal notes (with `edited_at`), files, audit trail |
| Notification | In-app alert with a `kind` (raised, closed, escalation, mention and others) that decides how the pop-up looks |
| OutboundMessage | Queued email, SMS and WhatsApp with status and error, sent by the worker; carries `provider_template` |
| MessageTemplate | Wording per event and channel: `send` (always, urgent, never), subject, body, provider template. No row means the built-in wording in `wording.py` |
| SiteSettings | Single row: alert switches, mail server, gateways, branding, auto-close, reopen and AMC days |
| Heartbeat | The worker's last run, shown on the health screen |

## Rules as implemented

**Routing.** The category decides the department. If a machine is chosen, the ticket goes to the department with
`handles_equipment`. Within the department, the assignment mode picks an agent covering the ticket's centre (an agent
with no centres covers all). With no candidate, or in manual mode, the ticket stays New with the head alerted.

**SLA clock.** 24x7. `resolution_due = sla_started_at + minutes + time on hold`. Hold pauses the clock; resume adds the
paused time to both deadlines. A transfer restarts the clock for the new department and clears its escalation log. A
priority change recalculates both deadlines from the original start. First response = the agent starts work or sends
a public reply. If the response deadline passes with nobody started, the ticket moves to the head. Reopening treats
the time spent resolved as paused time. Auto-close runs `auto_close_days` after resolution; reopening is allowed for
`reopen_days` after resolution.

**Permissions.** `services.allowed_actions(user, ticket)` returns the set of actions a person may take; every screen
and every action checks it. `services.visible_tickets(user)` is the only way tickets are listed or fetched.

**Comments.** Stored as typed. `rich` escapes all HTML first, then applies bold, italics, code, lists, links (http and
https only) and `@mentions`. Authors may edit for 24 hours (`services.EDIT_WINDOW`).

**Alerts.** `notify.notify(users, ticket, title, body, urgent, kind=...)` writes the bell alert, then for each
channel that is switched on looks up `wording.get(kind, channel)`; if its `send` rule matches `urgent`, the template
is rendered per recipient with `wording.context()` and queued. `wording.render()` leaves out a line whose known
placeholders are all empty, and leaves unknown placeholders as typed. The gateway message format may use `{template}`
for the provider template. The editing screen is `views.message_templates` at `/settings/templates/`.

**QR codes.** `/equipment/<id>/qr.svg` returns a standalone SVG (with its namespace, so it works in `<img>`), or a
PNG with `?format=png`. The code opens `<site_url>/tickets/new/?equipment=<id>`.

**Ticket actions.** `templates/_ticket_actions.html` draws one button per action in `allowed_actions`; `primary` in
the view picks the highlighted next step.

**Uploaded files** are served by Django only to people who may see the ticket, with
`Content-Security-Policy: default-src 'none'` and `nosniff`. Profile photos accept JPG, PNG or WEBP only.

## JSON feeds (signed-in users only)

| URL | Returns |
| --- | --- |
| `/api/live/?after=<id>` | New alerts for the signed-in person since that id, the unread count and the newest id |
| `/api/search/?q=` | Up to 8 tickets the person can see |
| `/api/people/?q=` | Up to 8 people, for @mentions |
| `/healthz` | `{"ok": true}` when the web part can reach the database |

## Reports and exports

`exports.build_report(user, params)` reads `period` (7, 30, 90, month, lastmonth, custom with `from` and `to`), `group`
(centre, department, centre_department, category, priority, owner, equipment), `state` (any, open, done) and repeated
`centre`, `department` and `priority` values. All figures are about tickets created in the period and visible to the
user. `exports.to_excel()` writes six sheets with openpyxl: Summary, Centre x Department, Tickets, Missed deadlines,
Equipment downtime, Vendor calls. `/reports/print/` renders an A4 landscape page for the browser's print or Save as PDF.

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

Without `DB_HOST` set, it uses a local SQLite file. Set `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER` and `DB_PASSWORD` to
use PostgreSQL; the tests pass on both. After changing `models.py`, run `python manage.py makemigrations` and commit the
new migration; the web container applies migrations when it starts.

## Environment variables

| Variable | Used by | Meaning |
| --- | --- | --- |
| `SECRET_KEY` | web, worker | Django secret. Created automatically when empty |
| `DB_PASSWORD` | db, web, worker, backup | Database password. Fixed at the first start |
| `ADMIN_USERNAME`, `ADMIN_PASSWORD` | web | First administrator, created only if none exists |
| `SITE_ADDRESS`, `HTTPS_MODE`, `HTTP_PORT`, `HTTPS_PORT` | proxy | Address and TLS |
| `BACKUP_DIR`, `BACKUP_TIME`, `BACKUP_KEEP_DAYS`, `BACKUP_REQUIRE_MARKER` | backup | Backup target and schedule |
| `TIME_ZONE` | all | Default Asia/Kolkata |
| `WEB_WORKERS` | web | Gunicorn processes, default 3 |

## Not built yet

HTML (designed) emails, scheduled email reports, saved replies, bulk actions, child tickets, a duplicate warning, a calendar view, saved
filters, preventive maintenance schedules, knowledge articles, login lock-out and two-step login, bulk import from
Excel, and archiving of tickets older than one year.
