# Technical reference

For a software developer who needs to change or extend DiagTrack. The other guides are for non-programmers.

## Stack

| Layer | Choice |
| --- | --- |
| Language and framework | Python 3.13, Django 6.1, server-rendered templates |
| Database | PostgreSQL 17 (SQLite when `DB_HOST` is not set, for local work) |
| Web server | Docker: Gunicorn behind Caddy 2. Native Windows: Waitress inside `manage.py serve` (with optional Caddy TLS reverse proxy) |
| Front end | Hand-written CSS (`static/css/app.css`), plain JavaScript (`static/js/app.js`), PWA Service Worker (`static/sw.js`), and Web App Manifest (`static/manifest.json`). No build step and no CDN: fonts (IBM Plex) and icons (Lucide) are bundled, so it works without internet. Includes mini sidebar hover tooltips and multi-line pill tab navigation (`.tabs-pills`) |
| Background jobs | `tickets.worker.Worker` once a minute (`runworker` in Docker, a thread of `serve` on Windows) evaluates SLA deadlines, escalations, weekly report schedules, PM schedules, and outbound dispatch; the backup runner every 5 seconds (`runbackups` in Docker, a thread of `serve` on Windows) |
| Encryption | `cryptography`: AES-256-GCM for backups and stored secrets, scrypt for the backup key, Windows DPAPI for the secrets file |
| Live updates | Server-Sent Events (SSE) via `/api/live/stream/` with automatic 15-second polling fallback (`/api/live/`); no external messaging broker required |
| Excel and QR | `openpyxl` and `segno` |

Python packages are pinned in `app/requirements.txt`.

## Two kinds of install

| | Docker (`docker-compose.yml`) | Native Windows (`installer/`) |
| --- | --- | --- |
| Screens | `web`: `docker/entrypoint.sh web` (migrate, `setup_initial`, Gunicorn), behind `proxy` (Caddy) | Windows service **DiagTrack** (WinSW wrapper `DiagTrackService.exe`) running `manage.py serve` as `NT SERVICE\DiagTrack`. Optional Caddy reverse proxy for native HTTPS (`installer/windows/setup-https.ps1`) |
| Worker | `worker`: `manage.py runworker` | thread in `serve` |
| Backups | `backup`: same image, `manage.py runbackups`, folders mounted at `/backups`, `/backups2`, `/import` | thread in `serve`; Task Scheduler safety net runs `backup --if-overdue` daily |
| Database | `db`: postgres:17-alpine, `POSTGRES_PASSWORD_FILE` | EDB PostgreSQL 17 service `postgresql-x64-17`, role `diagtrack` with CREATEDB |
| Settings | `.env` (no secrets) | `C:\ProgramData\DiagTrack\diagtrack.conf` |
| Secrets | `./secrets/<name>` mounted read-only at `/run/secrets` | `secrets.dat`, JSON encrypted with DPAPI (machine scope) |
| Setup | `docker compose run --rm setup` (profile `setup`, runs `installer/setup.py --docker`) | Single-click Inno Setup executable (`installer/windows/diagtrack_installer.iss`) or `Setup.cmd` -> `installer/windows/bootstrap.ps1` -> `installer/setup.py --windows` |

`config/runtime.py` fills the environment before Django settings load: the settings file and secrets file named by
`DIAGTRACK_HOME`, then `/run/secrets/<lower-case name>` (or `<NAME>_FILE`). Real environment variables always win.
When `DIAGTRACK_HOME` is set, both files must be readable: otherwise DiagTrack stops with a message pointing to
setup's Repair, instead of falling back to development defaults. Setup gives the data folder to SYSTEM,
Administrators and `NT SERVICE\DiagTrack` (modify) and resets everything inside it to inherit that.
Secrets: `SECRET_KEY`, `DB_PASSWORD`, `FIELD_KEY`, `BACKUP_PASSPHRASE`, `ADMIN_PASSWORD_HASH` (used once by
`setup_initial`); Windows also keeps `PG_SUPERPASSWORD`.

## Layout of `app/`

| Path | Contents |
| --- | --- |
| `config/settings.py` | Settings, all from environment variables |
| `config/runtime.py` | Loads the settings file, the secrets file and `/run/secrets` into the environment |
| `config/urls.py` | Every URL |
| `tickets/models.py` | The data model |
| `tickets/services.py` | **All business rules**: visibility, permissions, routing, assignment, status changes, SLA clock and database-level breach evaluations, escalation, vendor calls, AMC checks, PM checks, mentions. Views never change a ticket directly |
| `tickets/notify.py` | In-app alerts and the outbound queue for email, SMS, WhatsApp and Telegram |
| `tickets/worker.py` | One round of background work: SLA evaluations, weekly management reports, PM auto-tickets, escalations, alert queue; skipped during maintenance, reduced to sending alerts when read-only |
| `tickets/backup/` | `container.py` the encrypted file format, `engine.py` backup/verify/restore/test-restore/retention, `destinations.py` folders, schedules, retention rules, NAS logon, `runner.py` jobs, schedule, checks, alerts |
| `tickets/state.py` | Shared JSON state in `STATUS_DIR`: jobs, maintenance and read-only flags, backup state and catalog, a file lock |
| `tickets/middleware.py` | Maintenance page during restores (no database access), read-only mode during a move |
| `tickets/crypto.py` | `EncryptedTextField` (AES-256-GCM) for the mail password and gateway headers |
| `tickets/views_backup.py` | The Backups page; it only queues jobs and reads state |
| `tickets/signin.py` | Wrong-password lock-out, active session invalidation (`invalidate_user_sessions`), the sign-in forms for main and admin login |
| `tickets/bulk_import.py`, `tickets/views_import.py` | Import from Excel: the workbook with current data, row checks across sheets, one-transaction apply; the upload, preview and import screen |
| `Equipment.value`, `SiteSettings.insurance_min_value` | `views_insurance.uninsured(user, centres, cfg)` returns active machines in view with `value >= insurance_min_value` that `covered_machine_ids(today)` does not contain: those listed in a current (not cancelled, not renewed, unexpired) policy's `equipment`, plus every active machine at the centres of a current policy with `covers_all_machines` (all machines when it has no centres). The coverage grid and `machine_policies` use the same rule. Used by the Coverage grid tab, `home_card` and `insurance_summary.build` |
| `ClaimDocument` (`claim_doc`, `claim_doc_delete`) | Optional files on a claim, stored under `claims/<id>/`; served by `serve_document`, scoped through the claim's policy with `_scoped`; files are deleted with the claim or the policy |
| `PremiumInstalment` | Instalment schedule of a policy. `InsurancePolicy.sync_premium_due()` sets `premium_due` to the earliest unpaid instalment (None when all are paid) and is called by `instalment_add/generate/paid/delete` and the Excel import, so `insurance_checks`, `home_card` and the summary are unchanged. `PolicyForm` disables `premium_due` while a schedule exists |
| `views_insurance.insurance_print` | `/insurance/print/`, template `insurance_print.html` in the `report_print` style (A4 landscape, auto-opens the print dialog unless `#noprint`). Uses `_filtered`, `licence _scoped`, open claims and `uninsured`, all through the person's scope; money columns follow `_show_money` |
| `views_insurance.insurance_access` (+ `_export`) | Administrator-only audit page `/insurance/access/`: one row per active user from `User.insurance_level`, `insurance_sees_all`, `can_manage_insurance`, `_show_money`, email present, count of current policies where responsible. Read-only; access is still edited under Masters, Users |
| `RenewalQuote`, `InsurancePolicy.quote_alert_for`, `SiteSettings.insurance_quote_days` | Quotes on the policy being renewed (`quote_add/choose/delete`, one `chosen`). `insurance_new` starts from the chosen quote's insurer and, for money viewers, premium and sum insured. `services.insurance_checks` sends one "Ask for renewal quotes" alert per expiry date when `0 <= days left <= insurance_quote_days` and the policy has no quote; the weekly summary has the same list |
| `tickets/insurance_summary.py` | Monday email (`send_weekly`, called from the worker on Mondays): `build(user)` collects premiums, expired and ending policies, licences, open claims and coverage gaps through the same `_scoped` rules, `render` makes the text, one `OutboundMessage` per person with something to report. `SiteSettings.insurance_weekly_enabled`, `last_insurance_summary_at` (once a day) |
| `tickets/views_licence.py` | Licences and certificates (`Licence`, `LicenceDocument`; `/licences/`): same access levels as insurance (`_scoped`, `viewer`, `manager`), renewals, documents served by `views_insurance.serve_document`, export. `services.licence_checks` sends the daily reminders (first reminder own number or 90 days, 30, 7, 0, -1; stored +2 in `alert_stage`), kind `insurance`, to the same people as insurance via `_insurance_people`. `home_card` adds `lic_expired`, `lic_expiring` and `lic_items` from `views_licence._scoped`. `LicenceLog` and `insurance_log.licence_snapshot/licence_record(_changes)` give the change history (add, edit, renew, documents, Excel import) |
| `views_insurance.home_card` | The Insurance card on Home: counts and the six most urgent items from `_scoped`; shown when `can_view_insurance`, never with money |
| `services.insurance_checks` | Daily insurance reminders, run by the worker next to `amc_checks`: stages from `insurance_stages(first)` (first reminder, 30, 7, 0 and -1 days) and `PREMIUM_STAGES` (7, 0, then a single overdue alert once the date has passed; stored 9, 2, 1) for the next premium due date, once each per expiry date (`alert_for`, `alert_stage`, stored +2); to administrators and users whose `insurance_access` lets them see the policy (all/manage, or own with a shared centre or a policy that lists no centres), plus each responsible person who has access; kind `insurance` |
| `tickets/views_insurance.py` | The Insurance register (Oversee > Insurance): `machine_policies` for the equipment page panel, list and filters (grouped query, so ordering is explicit), coverage grid (`_gaps`: centres by `InsuranceCoverType.core`, best live policy per cell), add, edit, renew, documents, cover types, Excel export; file checks and who-sees-what rules |
| `tickets/wording.py` | Events, placeholders, built-in wording and the template renderer for email, SMS, WhatsApp and Telegram |
| `tickets/reports.py` | Home, dashboard, centre matrix, equipment figures, PM schedule status, CAMC penalty calculation, and weekly management summary report generation |
| `tickets/exports.py` | The Reports screen and the Excel workbook |
| `tickets/views.py` | Screens, bulk actions, duplicate detection, SSE stream, and JSON feeds |
| `tickets/admin.py` | Masters and rules (Django admin) |
| `tickets/templatetags/dt.py` | SLA bar, avatars, icons, `abs` filter, the safe comment formatter (`rich`) |
| `tickets/mail.py` | Email backend that reads the SMTP details from Settings |
| `tickets/management/commands/` | `serve`, `runworker`, `runbackups`, `backup`, `restore`, `verifybackup`, `setup_initial`, `setupcheck`, `siteconfig`, `unlock`, `seed_demo`, `archive_tickets`, `send_weekly_report` |
| `tickets/tests.py` | Automated tests of the rules, screens, backups, bulk actions, duplicates, SSE, PM lifecycle, CAMC penalties, and alerts |
| `templates/`, `static/` | Screens (including `masters_hub.html`), styles, script, service worker (`sw.js`), manifest, fonts, icons, logo |

The setup program lives outside `app/`, in `installer/`: `setup.py` and the `diagtrack_setup` package (`main`, `ui`,
`checks`, `common`, `windows`, `docker_mode`), `versions.json` (pinned Python and PostgreSQL downloads),
`windows/bootstrap.ps1`, `windows/diagtrack_installer.iss`, `windows/setup-https.ps1`, `windows/Caddyfile.windows`, and the WinSW service wrapper.

## Data model

| Model | Purpose |
| --- | --- |
| Centre | `code` is the ticket prefix (2 to 5 characters); `next_seq` is the per-centre counter, row-locked when a number is issued |
| Department | Head, assignment mode, `handles_equipment`, round-robin pointer |
| Category, CategoryField | Routing, default priority, extra questions per category |
| User | Custom user: role, mobile, `telegram_chat_id`, photo, centres (M2M), departments (M2M) |
| Vendor, Equipment | Equipment register with contractual `uptime_guarantee_percent`, `penalty_rate_per_day`, and `annual_camc_cost`; `amc_alert_for` and `amc_alert_stage` make each AMC warning go out once |
| InsuranceCoverType, InsurancePolicy, InsuranceDocument, InsuranceLog, InsuranceClaim (status, incident and filing dates, optional machine, amounts shown only under the money rule; add/edit need `can_manage_insurance`, delete is administrator only) | The insurance register. A policy has a cover type, any number of centres (none = all centres or head office), insurer, policy number, start and expiry dates, optional `sum_insured` and `premium`, optional responsible people (many-to-many `responsible`; the Excel column takes user names separated by commas), an optional `covers` text, optional `premium_due` (with `premium_alert_for` / `premium_alert_stage`: reminders 7 and 0 days before, once each, restarting when the date changes), optional `remind_days` (first reminder; empty uses the Settings number), optional covered machines, and `renewed` / `cancelled` flags. Renewing creates the next policy with `renewed_from` set and marks the old one `renewed`. Bulk: `insurance_bulk` (POST; `next` is checked with `url_has_allowed_host_and_scheme`); Premiums: `_premium_data` groups by `_fy(start_date)`; Home: `home_card` adds `claims_open`, `claims_stuck`, `needs`. Claims: `InsuranceClaim.status_changed_on` / `claim_alerted_on` (migration 0029) drive the chase loop at the end of `insurance_checks`; `progress` / `waiting_days` feed `_claim_steps.html`; `SiteSettings.insurance_claim_days` sets the period. UI: `_summary()` builds the policy page strip; the `inr` filter in `templatetags/dt.py` formats amounts; folded panels are `<details class="fold">`. Activity: `insurance_activity` / `insurance_activity_export` merge `InsuranceLog` and `LicenceLog` (kind is read from the text prefix: Claim, Instalment, Quote), scoped by `_scoped`; no purge job exists. History: `tickets/history.py` (`build_series` groups by `renewed_from` links plus cover type and centres, or licence name, with union-find; `series_for`, `relink`); `insurance_history` / `insurance_history_export` serve the tab and Excel. Policies and licences are never deleted in normal use: `archived_at` / `archived_by` take them out of lists, reminders and checks (`_scoped(user, archived=False|True|None)`); `insurance_purge` / `licence_purge` (administrator, archived only, typed confirmation) delete for good. `InsuranceLog.policy` and `LicenceLog.licence` are SET_NULL with a `label`, so history survives purging. Every change is written to `InsuranceLog` by `tickets/insurance_log.py` (`snapshot` before, `record_changes` after; money and notes are logged without values). Documents (PDF, JPG, PNG, WebP, 20 MB each) are stored under `insurance/` in the media folder, so backups include them |
| PreventiveMaintenance | Recurring calibration and PM schedules linked to equipment; tracks `frequency_months`, `last_performed_at`, `next_due_date`, and auto-generated `last_ticket` |
| DowntimeLog | Downtime per equipment ticket |
| VendorCall | Vendor called for a ticket: called and attended times, reference, engineer |
| SlaPolicy | Minutes per priority; department null means the default |
| EscalationRule, EscalationLog | Rules by percent of response or resolution time; the log stops a rule firing twice for a ticket |
| Ticket | Includes the SLA fields `sla_started_at`, `response_due`, `resolution_due`, `hold_started_at`, `hold_seconds`, breach flags, and `archived_at` |
| TicketComment, TicketAttachment, TicketHistory | Comments and internal notes (with `edited_at`), files, audit trail |
| Notification | In-app alert with a `kind` (raised, closed, escalation, mention and others) that decides how the pop-up looks |
| OutboundMessage | Queued email, SMS, WhatsApp and Telegram with status and error, sent by the worker; carries `provider_template` |
| MessageTemplate | Wording per event and channel: `send` (always, urgent, never), subject, body, provider template. No row means the built-in wording in `wording.py` |
| SiteSettings | Single row: alert switches (including Telegram), mail server, gateways, Telegram bot credentials, weekly report configuration, branding, auto-close, reopen and AMC days |
| Heartbeat | The worker's last run, shown on the health screen |
| SignInLock | Wrong passwords per `user:<name>` or `ip:<address>`: count, first failure, paused until |

Backup jobs, schedules and history are deliberately not in the database: they live as JSON files in `STATUS_DIR`, so
they survive a restore.

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
*Database-level SLA evaluation:* In addition to on-demand checks, `services.run_sla_checks()` executes via the background
worker once a minute, evaluating un-responded and un-resolved open tickets directly in bulk database queries.

**Duplicate detection.** `/api/duplicates/` evaluates draft ticket titles and equipment selections against open tickets in the same centre/category. Returns similarity matches so requesters can avoid creating duplicate tickets.

**Bulk ticket actions.** `/tickets/bulk/` permits authorized agents and department heads to batch-assign, batch-transition status, or batch-update priority across multiple tickets in a single atomic transaction.

**Historical ticket archiving.** `manage.py archive_tickets` flags resolved or closed tickets older than a retention threshold (default 365 days) with `archived_at`. `services.visible_tickets(user, include_archived=False)` excludes them from active queues by default while preserving them for audit and reports.

**Active session invalidation.** `signin.invalidate_user_sessions(user)` purges all active session keys from the database, triggered on password reset or explicit user request from My Profile.

**Weekly management email reports.** Every Monday at 07:00, the worker calls `reports.check_and_send_weekly_reports()` to compile and dispatch a 7-day executive summary email covering volume, resolution rates, SLA breaches, and downtime to configured management recipients. Can be run manually with `manage.py send_weekly_report [--force]`.

**Preventive Maintenance (PM) automation.** `PreventiveMaintenance` schedules define recurring servicing intervals (monthly, quarterly, half-yearly, annual). The daily worker checks upcoming due dates via `services.run_pm_checks()` and auto-generates tickets under category `Preventive Maintenance` when within `advance_notice_days` (default 7 days). When a PM ticket is resolved, `services.resolve()` automatically stamps `last_performed_at` and advances `next_due_date` by the schedule's frequency months.

**CAMC Uptime Guarantee & Penalty Calculator.** Contractual parameters on `Equipment` (`uptime_guarantee_percent`, `penalty_rate_per_day`, `annual_camc_cost`) are evaluated against actual downtime in `DowntimeLog` via `reports.camc_uptime_penalties()`. The engine calculates permissible downtime, excess hours, and computes financial deduction penalties either via configured per-day excess rates or pro-rata annual CAMC contract value.

**Masters and rules native hub.** `/masters/` delivers a unified, comprehensive administration interface covering all 13 operational and master entities across dedicated native multi-line pill tabs (`.tabs-pills`): Centres, Departments, Categories & questions, SLA policies, Escalation rules, Users & access, Sign-in locks, Vendors, Equipment register, Preventive maintenance schedules, Sent alerts log, Ticket history audit trail, and Vendor calls. Every table features auto-submitting filters, multi-column sorting, text search, pagination, and modal dialogs for rapid record creation and editing without requiring raw database admin tools.

**Permissions.** `services.allowed_actions(user, ticket)` returns the set of actions a person may take; every screen
and every action checks it. `services.visible_tickets(user)` is the only way tickets are listed or fetched.

**Comments.** Stored as typed. `rich` escapes all HTML first, then applies bold, italics, code, lists, links (http and
https only) and `@mentions`. Authors may edit for 24 hours (`services.EDIT_WINDOW`).

**Alerts.** `notify.notify(users, ticket, title, body, urgent, kind=...)` writes the bell alert and live SSE event, then for each
channel that is switched on (Email, SMS, WhatsApp, Telegram) looks up `wording.get(kind, channel)`; if its `send` rule
matches `urgent`, the template is rendered per recipient and queued in `OutboundMessage`. By default, all communication
channels are configured to send `always` for all ticket lifecycle events (creation, assignment, replies, notes, mentions,
work started, hold, resume, resolve, close, reopen, priority shifts, and vendor visits). If an individual user lacks a Telegram Chat ID,
alerts route to `telegram_default_chat_id`. The background worker dispatches the queue, routing Telegram messages through the
Telegram Bot API (`_telegram_send`).

**QR codes.** `/equipment/<id>/qr.svg` returns a standalone SVG (with its namespace, so it works in `<img>`), or a
PNG with `?format=png`. The code opens `<site_url>/tickets/new/?equipment=<id>`.

**Ticket actions.** `templates/_ticket_actions.html` draws one button per action in `allowed_actions`; `primary` in
the view picks the highlighted next step.

**Backups.** A `.dtbak` file is `DTBAK\x01`, a 4-byte header length, a JSON header (cipher, scrypt salt and cost,
nonce prefix, and a readable summary), then AES-256-GCM chunks of 1 MiB. Each chunk's associated data is the header's
SHA-256, its index and a final flag, so changes, reordering and truncation are refused. Inside is a tar stream:
`manifest.json` (versions, applied migrations, counts, checksums, name clashes), `keys.json` (the field key),
`attachments.json`, `database.dump` (`pg_dump -Fc`), and for full copies `attachments/<sha256>`. In a backup folder,
attachments are stored once as `attachments/ab/<sha256>.dta`; `store.json` holds the folder's salt and a passphrase
check. Restore loads the dump into `<db>_incoming`, migrates it in a subprocess, compares counts with the manifest,
then renames databases (live becomes `<db>_previous`), copies attachments (renaming ones that clash on the target
system and updating the rows), and re-encrypts stored secrets for this server's field key. Jobs and state live in
`STATUS_DIR`, not the database, so they survive a restore.

**Sign-in lock-out.** `signin.LockoutMixin.clean()` refuses a sign-in while the user name or the address is paused,
counts a failure only on Django's `invalid_login`, and clears the user name's count on success. Unknown names are
counted the same way, so the message never tells a guesser which names exist. The address is `REMOTE_ADDR`, or the
last `X-Forwarded-For` entry when `TRUST_X_FORWARDED_FOR=1` (set in `docker-compose.yml`, where Caddy is in front;
never on Windows, where Waitress faces the network). `admin.site.login_form` uses the same mixin. The password reset
confirm view lifts the pause.

**Import from Excel.** `bulk_import.read()` matches headings loosely (case, `*` and brackets ignored; a `Users`
sheet is accepted for People), skips empty rows, and checks every row against the database plus the rows of the same
file, so references across sheets work. Each row becomes new, change, same or error. `apply()` runs only when there
are no errors, inside one transaction, in the order centres, vendors, departments, people, department heads,
equipment, insurance policies (matched by insurer, policy number and start date; centres `All` or empty on a new row means
no centres, i.e. all centres; documents are not imported). Uploads wait in `STATUS_DIR/imports/<token>.xlsx` between the check and the import, are checked again on
import, and are removed after it or after a day.

**Uploaded files** are served by Django only to people who may see the ticket, with
`Content-Security-Policy: default-src 'none'` and `nosniff`. Profile photos accept JPG, PNG or WEBP only.

## JSON feeds & API endpoints (signed-in users only)

| URL | Returns |
| --- | --- |
| `/insurance/` | Insurance register, `?tab=types` for cover types (administrators), `/insurance/export.xlsx`, `/insurance/<id>/`, `/insurance/files/<id>/` for documents (`?download=1` forces a download; a PDF shown inline gets `Content-Security-Policy: frame-ancestors 'self'` and `X-Frame-Options: SAMEORIGIN` instead of the strict `default-src 'none'` so the in-app viewer frame can show it, everything else keeps the strict rule). Access is `User.insurance_access` (`''`, `own`, `all`, `manage`; administrators always full), independent of role: properties `can_view_insurance`, `can_manage_insurance`, `insurance_sees_all`, `insurance_money`. Delete, cover types and money Settings are administrator only |
| `/masters/` | Dedicated native Masters and rules management hub with quick-add dialogs and quick statistics (administrators) |
| `/api/live/stream/` | Server-Sent Events (SSE) event stream for instant ticket and notification pushes |
| `/api/live/?after=<id>` | New alerts for the signed-in person since that id, the unread count and the newest id (polling fallback) |
| `/api/duplicates/` | JSON list of matching open tickets during ticket drafting |
| `/tickets/bulk/` | Endpoint for bulk assign, status, and priority updates |
| `/metrics` | Prometheus metrics exporter for centralized observability |
| `/api/search/?q=` | Up to 8 tickets the person can see |
| `/api/people/?q=` | Up to 8 people, for @mentions |
| `/settings/backups/jobs/<id>.json` | Progress of a backup, check or restore job (administrators) |
| `/maintenance/status.json` | Whether a restore is running; used by the maintenance page (no sign-in needed) |
| `/healthz` | `{"ok": true}` when the web part can reach the database |
| `/sw.js` | Progressive Web App Service Worker script |

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
DEBUG=1 python manage.py test --noinput   # run after every change
```

Without `DB_HOST` set, it uses a local SQLite file. Set `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER` and `DB_PASSWORD` to
use PostgreSQL; the tests pass on both, except the backup tests, which need PostgreSQL. After changing `models.py`, run
`python manage.py makemigrations` and commit the new migration; the web container and the Windows service apply
migrations when they start.

## Settings

| Variable | Meaning |
| --- | --- |
| `DIAGTRACK_HOME` | Native Windows: folder with `diagtrack.conf` and `secrets.dat`; sets `MEDIA_ROOT` and `STATUS_DIR` under it |
| `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER` | Database. Without `DB_HOST`, SQLite (development only; backups need PostgreSQL) |
| `PG_BIN` | Folder of `pg_dump` and `pg_restore` (Windows: `C:\Program Files\PostgreSQL\17\bin`) |
| `MEDIA_ROOT`, `STATUS_DIR`, `STATIC_ROOT` | Attachments, shared state, collected static files |
| `HTTP_PORT`, `LISTEN_HOST` | `serve` (native) listening address |
| `SITE_URL` | Address of this system, applied by `setup_initial` when empty |
| `ADMIN_USERNAME` | First administrator |
| `BACKUP_DIR`, `BACKUP_DIR_2` | Docker: host folders mounted at `/backups` and `/backups2` |
| `SITE_ADDRESS`, `HTTPS_MODE`, `HTTPS_PORT` | Docker proxy |
| `TIME_ZONE` | Default Asia/Kolkata |
| `LOGIN_MAX_FAILURES`, `LOGIN_MAX_FAILURES_PER_ADDRESS`, `LOGIN_LOCK_MINUTES` | Sign-in lock-out: 5 per user name, 20 per address, 15 minutes |
| `TRUST_X_FORWARDED_FOR` | `1` only behind DiagTrack's own proxy (Docker) |
| `WEB_WORKERS` | Gunicorn processes (Docker), default 3 |

Secrets are not in this table: they come from the secrets file or `/run/secrets`, never from `.env` or `diagtrack.conf`.

## Testing

`python manage.py test tickets --noinput` against PostgreSQL (the backup tests run real `pg_dump` and `pg_restore`;
the role needs CREATEDB). `python -m unittest discover -s installer/tests` for the setup program.
`python ci/screencheck.py` opens every screen in Chromium as each kind of user, at desktop and phone width, in light
and dark mode, on a throwaway SQLite database with the demo data, and fails on sideways scrolling, columns hidden
behind a sideways scroll on a desktop, text squeezed into a narrow column, JavaScript errors and failed requests
(it needs `pip install playwright` and `python -m playwright install chromium`).
Before pushing, run `ruff check --select E9,F63,F7,F82,F401,F811 app --exclude app/tickets/migrations` (`pip install ruff`): it
finds syntax errors, undefined names and unused imports, and once caught a missing `import time` that would have broken the live
alert stream. `CodeHygieneTests` fails the suite when a model is changed without its migration, so no hand-stripping of stray
migration operations is needed any more.
`.github/workflows/ci.yml` runs all three and the code check, cancels the older run when a newer push arrives, plus a Docker setup with verify and test restore, a native install, update and
removal on Windows Server 2022, and a move from Docker to native Windows. Unattended runs use `--answers file.json`
with secrets in `DT_<KEY>` variables (see `ci/`). GitHub's job logs may be unreachable from some networks, so each
job also reports its failures as annotations on the run. Windows Server 2016 and Windows 11 are not offered by GitHub and are
first tested on the real machines.

## Not built yet

HTML (designed) emails, a cloud backup destination, code signing for the Windows installer executable, changing the
backup passphrase in place, saved replies, child tickets, a calendar view, saved
filters, knowledge articles, and two-step login.
