# Administrator guide

For the person who sets DiagTrack up for the organisation and keeps its lists up to date. Everything here is done on
screen in the browser; no programming is needed.

Sign in as an administrator. The side menu then shows extra items under **Administer**:

- **Masters and rules**: the lists DiagTrack works from (centres, departments, categories, users, SLA times,
  escalation rules, equipment, vendors). Every list has an **Add** button at the top right. Click a row to change it,
  then press **Save** at the bottom.
- **Import from Excel**: add or change centres, vendors, departments, people and equipment from one workbook.
- **Backups**: backup folders, schedules, retention, Back up now, restores and moving to another server.
- **Message templates**: the wording of email, SMS, WhatsApp and Telegram alerts, and which alerts go out on each channel.
- **Settings and health**: alerts, mail server, Telegram bot, scheduled reports, branding, and a check that the system is healthy.

## 1. First-day setup

Do these in order. Departments, categories, SLA times and escalation rules are already filled in with sensible
starting values, so most of the work is centres, people and equipment. With more than a handful of each, steps 2 to
5 are quickest with **Import from Excel** (section 14): one workbook for all of them.

1. **Settings and health:** check the organisation name, fill in "address of this system", and save.
2. **Centres:** add every centre (section 2).
3. **Users:** add the department heads, agents, managers and staff (section 4). Tick the right centres and
   departments on each.
4. **Departments:** set the head of each department (section 3).
5. **Vendors and Equipment:** add the service vendors, then each machine with its AMC expiry date (section 6).
6. **Mail server:** fill it in and send yourself a test email, then switch on email alerts (section 7).
7. **Backups:** check that the backup folder shows "Up to date", press **Check folder**, and tick who else should
   be alerted about backups (section 13).
8. Ask one person at one centre to raise a test ticket and follow it through to closed.

## 2. Centres

*Masters and rules, Centres*

| Field | What to enter |
| --- | --- |
| Name | The centre's name, for example Kukatpally |
| Short code | 2 to 5 capital letters, for example KKP. It starts every ticket number of that centre: KKP-000123. It cannot be changed once the centre has tickets |
| City | Optional |
| Active | Untick when a centre closes. Its old tickets stay and can still be reported on |

**When a new centre opens:** add the centre, add its machines (section 6), then open each person who works there or
looks after it (section 4) and tick the new centre.

## 3. Departments and categories

*Masters and rules, Departments*

| Field | Meaning |
| --- | --- |
| Name | IT, Biomedical, Facilities and so on |
| Head | Receives tickets nobody has been assigned, and escalations. Set this for every department |
| How tickets are assigned | **Round robin:** each agent in turn. **Least loaded:** the agent with the fewest open tickets. **Manual:** the head assigns every ticket |
| Receives equipment tickets | Tick for Biomedical only. Any ticket where a machine is chosen goes here, whatever the category |
| Active | Untick to stop new tickets going to this department |

The starting departments are IT, Biomedical, Facilities, Stores and purchase, HR and admin, Finance and billing, and
Quality.

*Masters and rules, Categories*: a category is what staff pick under "What is it about?" when they raise a ticket.
The category decides which department gets the ticket.

| Field | Meaning |
| --- | --- |
| Name | For example "Computer or printer" |
| Department | Tickets in this category go to this department |
| Default priority | The priority a new ticket starts with, P1 to P4 |
| Ask which equipment | Tick to show the machine list for this category |
| Extra questions | Further boxes shown for this category, for example "Error code" or "Room number". Each can be text, a number or a pick list, and can be made compulsory |

**Adding a new department:** add the department and its head, add its categories, then tick the department on each
of its agents (section 4).

## 4. Users and what each role can do

*Masters and rules, Users.* Give each person a user name and a first password. They can change the password and add a
photo themselves, under their name at the top right.

| Role | Can do | Sees |
| --- | --- | --- |
| Requester | Raise tickets, reply, raise the priority once, confirm a fix or reopen, cancel before work starts | Own tickets |
| Agent | Everything a requester can, plus start work, hold, resolve, transfer, log vendor calls, write internal notes, bulk ticket actions | Tickets of their departments at their centres |
| Department head | Everything an agent can, plus assign and reassign, change priority, cancel, bulk reassign/status | All tickets of their departments |
| Centre manager | Raise, reply, internal notes, dashboards, centres, reports, equipment | All tickets of their centres |
| Area manager | Same as centre manager, plus transfer tickets between departments | All tickets of their centres |
| Management | View only: dashboards, centres, reports, equipment, every ticket | Everything |
| Administrator | Everything, plus Masters and rules and Settings | Everything |

For each person:

- **Centres:** where they work (requesters, managers) or which centres they cover (agents). An agent with no centre
  ticked covers every centre.
- **Departments:** only for agents and department heads.
- **Email, mobile, and Telegram Chat ID:** needed for email, SMS, WhatsApp and Telegram alerts. Write the mobile number with the country code (e.g. 919876543210).
- **When someone leaves:** untick **Active**. Do not delete people; their name stays on the tickets they handled.
- **Forgotten password:** open the person and use the link in the password line to set a new one. Changing a password automatically invalidates all existing active sessions on other devices.
- **Sign out other sessions:** users can click "Sign out other sessions" on My Profile to revoke all other active sign-in sessions immediately.
- **Too many wrong passwords:** after 5 wrong passwords in 15 minutes, sign-in for that user name pauses for 15
  minutes, and administrators get an alert. 20 wrong passwords from one computer pause that computer too. The
  **Sign-in** column on the Users list shows who is paused. To let someone in sooner: tick them, choose **Unlock
  sign-in for the selected people** and press **Go**. Setting a new password through **Forgot your password** also
  lifts the pause. **Masters and rules, Sign-in locks** lists every current pause and count.

## 5. SLA times and escalation

*Masters and rules, SLA times.* Times are in minutes on a 24-hour, 7-day clock and can be changed directly in the
list.

| Priority | First response within | Resolve within |
| --- | --- | --- |
| P1 Critical | 15 minutes | 240 minutes (4 hours) |
| P2 High | 30 minutes | 480 minutes (8 hours) |
| P3 Medium | 120 minutes (2 hours) | 1,440 minutes (24 hours) |
| P4 Low | 240 minutes (4 hours) | 4,320 minutes (3 days) |

The four rows with no department are the default for everyone. To give one department its own times, add a row and
choose that department. Changes apply to tickets raised afterwards.

How the clock behaves:

- It runs day and night, and pauses while a ticket is on hold.
- A transfer to another department starts a new clock for that department.
- The background worker evaluates SLA response and resolution deadlines at the database level on every round.
- If nobody starts work within the response time, the ticket moves to the department head.
- A ticket marked "Patient services are stopped" becomes P1 and alerts the department head, centre manager and area
  manager at once.

*Masters and rules, Escalation rules.* Each rule says: when this much of the response or resolution time has been
used, alert these people. The starting rules:

| Rule | When | Who is alerted |
| --- | --- | --- |
| Resolution deadline close | 75% of the resolution time used | Owner and department head |
| Response time missed | 100% of the response time, and nobody has started | Department head |
| Resolution time missed | 100% of the resolution time | Department head and centre manager |
| Still open well past the deadline | 150% of the resolution time | Department head, centre manager and area manager |

Untick **Active** to switch a rule off. Put P1,P2 in "Priorities" to make a rule apply only to those.

## 6. Equipment, vendors, AMC and QR labels

*Masters and rules, Vendors:* the service companies, with contact person and phone.

*Masters and rules, Equipment:* one row per machine per centre, with name, make, model, serial number, vendor and
**AMC expiry date**.

What DiagTrack does with them:

| Feature | Where | What it does |
| --- | --- | --- |
| Downtime | Equipment, Machines | When staff tick "It is not working at all right now", the downtime clock starts. It stops when the ticket is resolved. Uptime is shown for 30 and 90 days |
| Vendor calls | Equipment ticket page | The team logs when they called the vendor and when the engineer arrived |
| Vendor response | Equipment, Vendor response | Each vendor's calls and average time to arrive, last 90 days |
| AMC renewals | Equipment, AMC renewals | Contracts ending soon. The equipment department head and administrators are alerted 60, 30 and 7 days before, and on the day it ends |
| Repeat faults | Equipment, Repeat faults | Machines with 3 or more tickets in 90 days, worth a root-cause review |
| QR labels | Equipment, Print QR labels | A printable sheet, one label per machine. Scanning a label with a phone opens Raise a ticket with the centre and machine already chosen |

For the QR labels, first fill in "address of this system" in Settings, then choose a centre on the Machines tab and
press **Print QR labels**. Print on A4, cut along the dashed lines and stick one on each machine. Each machine's own
page also shows its QR code, the address it opens, a **Print label** button and **Download PNG** for a sticker
printer.

## 7. Alerts

*Settings and health, Alert channels.* Switches for each:

| Switch | What it does |
| --- | --- |
| In-app alerts (bell) | The bell at the top of every screen |
| Email alerts | Emails for every alert. Needs the mail server below |
| SMS alerts | Instant SMS text alerts. Needs an SMS provider and internet |
| WhatsApp alerts | Instant WhatsApp alerts. Needs a WhatsApp Business provider and internet |
| Telegram alerts | Instant messages sent via Telegram bot to individual users or default group |

These switches turn a whole channel on or off. By default, all enabled communication channels send alerts for every ticket update (configured under Message templates in section 8).
| Alert the area manager on every new ticket | When off, area managers hear only about P1 tickets and escalations |
| Alert centre and area managers when a ticket is closed | Managers see each closure at their centres |
| Pop-up messages on screen | New tickets, closures, escalations, mentions and AMC warnings slide in at the bottom right of the screen |

Who gets which alert:

| Event | Who is alerted |
| --- | --- |
| New ticket | The owner, or the department head if nobody is assigned yet; the area manager. For P1 also the department head and centre manager |
| Assigned / Reassigned | The newly assigned owner and the ticket requester |
| Work started / resumed | The ticket requester |
| On hold | The ticket requester |
| Reply (public comment) | The requester, the owner (or the head if unassigned) |
| Internal note | The owner and the department head |
| @mention | The person mentioned, if they can see the ticket |
| Resolved | The requester (asked to confirm) and the owner |
| Closed | The owner, department head, requester, and centre and area managers |
| Reopened | The owner, department head, and requester |
| Cancelled | The owner and requester |
| Transferred | The new department head / new owner, and the requester |
| Priority changed | The owner, department head, and requester |
| Vendor call / visit | The owner and department head |
| Escalation | As set in the escalation rules |
| AMC ending | Equipment department head and administrators |

**Mail server.** Get these from whoever provides your email: server name, port, security, user name, password, and
the "send from" address. Save, then press **Send me a test email**. The test goes to the email address on your own
user record.

**SMS and WhatsApp.** DiagTrack sends through a provider you subscribe to. From the provider's API page you need three
things:

| Setting | What it is | Example shape |
| --- | --- | --- |
| Gateway address | The web address messages are sent to | `https://api.provider.example/v1/send` |
| Headers | The key that identifies your account, one per line | `Authorization: Bearer YOUR-KEY` |
| Message format | The text the provider expects, with `{to}` for the mobile number and `{message}` for the text | `{"to": "{to}", "text": "{message}", "sender": "PRMDNI"}` |

Save, then press **Send me a test SMS** or **Send me a test WhatsApp**. If it fails, the provider's reason is shown.
In India, SMS needs a registered sender ID and approved templates (DLT), and WhatsApp needs an approved Business
account; the provider guides you through both.

**Telegram.** DiagTrack sends alerts via a Telegram bot:
1. Message `@BotFather` on Telegram to create a bot and copy the bot token.
2. In Settings, paste the **Telegram bot token** (saved visibly so you can verify it anytime).
3. Enter the **Default Chat ID** (the group chat ID or default recipient chat ID; for groups this typically begins with `-100`). If an individual user doesn't have a personal Chat ID in their profile, alerts route directly to this group/channel.
4. Turn on the **Telegram alerts** switch, save, and press **Send me a test Telegram**.
5. *Note on Telegram anti-spam policy:* A Telegram bot cannot message a user or group until the user opens the bot in Telegram and clicks **Start** (or sends `/start`). For group chats, add the bot as a group member first.
6. Individual users can also specify their personal Telegram Chat ID in **My profile** and test delivery using the **Test Telegram alert** button.

## 8. Message templates

*Side menu, Message templates.* One page per event: New ticket, Assigned, Work started, On hold, Resolved, Closed,
Reopened, Cancelled, Transferred, Priority changed, New reply, Internal note, Mentioned, Escalation, AMC ending, and
Anything else. By default, every active channel (Email, Telegram, SMS, WhatsApp) is set to **Always** send for ticket events so no updates are missed.

On an event's page there is a tab for each channel:

| Item | What it does |
| --- | --- |
| Send | **Always**, **Only urgent** (P1 tickets, escalations and AMC contracts ending within 7 days) or **Never** |
| Subject | Email only |
| Message | The text. Type normally and add placeholders |
| Provider template | SMS and WhatsApp only, and only if the provider asks for one (section 7) |
| Preview | Shows the message with a sample ticket as you type. The SMS tab also counts characters |

**Placeholders** are words in curly brackets that DiagTrack fills in when it sends. Click one under the message to
put it where the cursor is.

| Placeholder | Becomes |
| --- | --- |
| `{title}` | The alert's headline, as shown on the bell |
| `{details}` | The comment, reason or resolution note |
| `{recipient}` | The name of the person receiving it |
| `{ticket}`, `{subject}` | Ticket number and title |
| `{priority}`, `{status}` | For example P1 Critical, In progress |
| `{centre}`, `{department}`, `{category}`, `{equipment}` | Where it is and what it is about |
| `{requester}`, `{owner}` | Who raised it, who is working on it |
| `{due}` | The resolution deadline |
| `{link}` | Link to the ticket (or to the machine for AMC). Needs "address of this system" in Settings |
| `{org}` | Organisation name |

A line whose placeholders all come out empty is left out. For example "Machine: {equipment}" simply disappears on
a ticket without a machine, so one template works for every ticket. A misspelt placeholder is sent as typed, and
the screen warns you when you save.

Buttons at the bottom:

- **Save**.
- **Save and send me a test**: sends this event's messages, filled with the sample ticket, to your own email and
  mobile, on every channel that is switched on. The result shows under *Masters and rules, Sent alerts*.
- **Back to built-in wording**: undoes your changes for this event.

## 9. General settings and scheduled reports

*Settings and health*

| Setting | Default | Meaning |
| --- | --- | --- |
| Organisation name | Pramodini Medicare Limited | Shown in the menu, emails and reports |
| Address of this system | empty | The address staff type in the browser. Used in email links and QR labels |
| Close resolved tickets after | 3 days | If the requester does not confirm |
| Allow reopening for | 7 days | After the ticket was resolved |
| Largest attachment | 10 MB | Per file |
| Warn about AMC expiry | 60 days before | The first AMC warning |
| Weekly management email summary | Off | Automatically sends a performance summary every Monday morning at 07:00 |
| Weekly report recipients | empty | Comma-separated email addresses of leadership and managers |

**Brand.** The Pramodini logo and colours are built in: the P mark in the menu and browser tab, the full logo on the
sign-in page and on printed reports, navy `#274a99` for buttons and sky blue `#24aee8` for highlights. To change them,
upload a new full logo (wide) or logo mark (square), or pick new colours, and save. Tick "Go back to the built-in
logo" to undo an upload. Keep the main colour medium-dark so white text on buttons stays readable.

## 10. The health strip

The four boxes at the top of Settings and health. All should be green.

| Box | If it is red |
| --- | --- |
| Background worker | Deadlines are not being checked and alerts are not going out. Runbook, section 5 |
| Backups | A backup failed, is overdue, or a folder cannot be reached. The Backups page shows which and why. Runbook, section 4 |
| Alerts sent out | Some emails or messages failed. The reason is shown, usually a wrong mail server detail |
| In use | Always green: the number of tickets and active users |

For centralized system monitoring via Prometheus, DiagTrack exposes an authenticated `/metrics` endpoint (see Runbook, section 11).

## 11. Records you can look up

| What | Where |
| --- | --- |
| Every change to every ticket: who, what, when | The ticket page, Activity, History. Also *Masters and rules, Ticket history*, which cannot be edited or deleted |
| Every email, SMS, WhatsApp and Telegram sent, and any failure | *Masters and rules, Sent alerts* |
| Every vendor call | *Masters and rules, Vendor calls* |

## 12. Common tasks

| Task | How |
| --- | --- |
| A new centre opens | Section 2, then tick the centre on its people |
| A new person joins | Section 4, or Import from Excel for several (section 14) |
| Someone leaves | Open them under Users, untick Active |
| Someone moves to another centre | Open them under Users, change the ticked centres |
| Change a department head | Open the department, choose the new head |
| Make deadlines longer or shorter | Section 5, SLA times |
| A new kind of problem | Add a category under the right department |
| A new machine | Add it under Equipment, print its QR label |
| Many people, machines or a new centre at once | Import from Excel (section 14) |
| Someone cannot sign in after wrong passwords | Users, tick them, **Unlock sign-in** (section 4) |
| Change what an alert says, or send it by WhatsApp or Telegram | Message templates (section 8) |
| Configure Telegram alerts | Section 7, Telegram alerts in Settings |
| Schedule weekly management report | Section 9, Settings -> Scheduled reports |
| Archive old closed tickets | Run `python manage.py archive_tickets --days 365` (section 13) |
| Perform bulk actions on tickets | Select checkboxes on ticket list, use bulk action bar (Assign, Status, Priority) |
| An AMC is renewed | Change the AMC expiry date on the machine. Warnings start again for the new date |
| Add a second backup place, such as another centre's NAS | Backups page, **Add a backup destination** (section 13) |
| Copy everything to a USB drive | Backups page, **Save a full copy** |
| Go back to how things were yesterday | Backups page, **Restore** (Runbook, section 7) |
| Move DiagTrack to another server | Backups page, **Prepare to move** (Runbook, section 9) |

## 13. Backups and archiving

*Side menu, Backups.* Everything about backups is on this page. The runner that does the work is part of the
DiagTrack service on Windows, and the backup container in Docker; the page asks it to act and shows the progress.

**At the top:** the last good backup, the next one due, and the **last proven restore** (the monthly test restore).
Below them, **Tasks** shows what is running and the results of recent backups, checks and restores.

**Buttons**

| Button | What it does |
| --- | --- |
| Back up now | Backs up to every folder that is switched on. Progress shows under Tasks; you can leave the page |
| Save a full copy | Writes one self-contained encrypted file to a folder you type, for example a USB drive (`E:\DiagTrack-Copies`) |
| Check folder | Writes, reads and deletes a test file, and shows the free space |
| Back up here now | Backs up to that one folder |
| Check | Reads a backup back and compares every checksum |
| Test restore | Restores a backup into a scratch database, compares it, then removes it. Live data is not touched |
| Restore | Replaces all current data with that backup, after you type RESTORE (Runbook, section 7) |

**Archiving historical tickets:** Tickets closed or resolved more than 365 days ago can be archived to keep daily ticket queues fast and uncluttered while retaining all records in database backups and reports:
- Run `python manage.py archive_tickets --days 365` (or `--dry-run` to preview).
- Archived tickets are excluded from default active list queries (`visible_tickets(include_archived=False)`) but remain searchable when requested.

## 14. Import from Excel

*Administer, Import from Excel.* For adding or changing many centres, vendors, departments, people and machines at
once, for example when setting up all the centres, or when a new centre opens with its staff and equipment.

1. Press **Download the workbook**. It already holds everything in DiagTrack: one sheet each for Centres, Vendors,
   Departments, People and Equipment, and a **Read me** sheet that explains every column.
2. In Excel, add new rows at the bottom of a sheet, or change existing rows. Columns marked * must be filled.
   Drop-down lists offer the roles, the assignment modes and Yes/No.
3. Save the file and upload it on the same page, then press **Check the file**. DiagTrack lists, for each sheet,
   what will be added and what will change, and every row that needs fixing, with the reason. Nothing is saved yet.
4. If rows need fixing, correct them in Excel and upload again. When the page says **Ready to import**, press
   **Import**. Everything is saved together; if anything went wrong, nothing is saved.

| Rule | Detail |
| --- | --- |
| Empty cells | Leave the current value as it is. A new row with an empty cell starts empty |
| Matching | Centres by code, vendors and departments by name, people by user name, machines by centre and serial number (or centre and name when there is no serial number) |
| Across sheets | A person may be given a centre or department added in the same file; a department's head may be a person added in the same file |
| Lists | A person's centres are codes separated by commas, for example `KKP, BJH`, or `All`. Departments likewise, by name |
| Removing | Rows cannot be deleted by import. Set **Active** to **No** |
| New people | Use **Initial password**, or leave it empty: DiagTrack makes a password and shows it once, after the import. Print that page or copy the passwords, and ask each person to change theirs under **My profile** |
| Existing people | Initial password is ignored. Set a new password under Masters and rules, Users |
| Dates | Day-month-year, for example 31-03-2027, or an Excel date |
