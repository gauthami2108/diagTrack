# Administrator guide

For the person who sets DiagTrack up for the organisation and keeps its lists up to date. Everything here is done on
screen in the browser; no programming is needed.

Sign in as an administrator. The side menu then shows two extra items:

- **Masters and rules**: the lists DiagTrack works from (centres, departments, categories, users, SLA times,
  escalation rules, equipment, vendors). Every list has an **Add** button at the top right. Click a row to change it,
  then press **Save** at the bottom.
- **Backups**: backup folders, schedules, retention, Back up now, restores and moving to another server.
- **Message templates**: the wording of email, SMS and WhatsApp alerts, and which alerts go out on each channel.
- **Settings and health**: alerts, mail server, branding, and a check that the system is healthy.

## 1. First-day setup

Do these in order. Departments, categories, SLA times and escalation rules are already filled in with sensible
starting values, so most of the work is centres, people and equipment.

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
| Agent | Everything a requester can, plus start work, hold, resolve, transfer, log vendor calls, write internal notes | Tickets of their departments at their centres |
| Department head | Everything an agent can, plus assign and reassign, change priority, cancel | All tickets of their departments |
| Centre manager | Raise, reply, internal notes, dashboards, centres, reports, equipment | All tickets of their centres |
| Area manager | Same as centre manager, plus transfer tickets between departments | All tickets of their centres |
| Management | View only: dashboards, centres, reports, equipment, every ticket | Everything |
| Administrator | Everything, plus Masters and rules and Settings | Everything |

For each person:

- **Centres:** where they work (requesters, managers) or which centres they cover (agents). An agent with no centre
  ticked covers every centre.
- **Departments:** only for agents and department heads.
- **Email and mobile:** needed for email, SMS and WhatsApp alerts. Write the mobile number with the country code,
  for example 919876543210.
- **When someone leaves:** untick **Active**. Do not delete people; their name stays on the tickets they handled.
- **Forgotten password:** open the person and use the link in the password line to set a new one. For the `admin`
  login itself, see the Runbook, section 6.

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

*Settings and health, Alert channels.* One switch for each:

| Switch | What it does |
| --- | --- |
| In-app alerts (bell) | The bell at the top of every screen |
| Email alerts | Emails for every alert. Needs the mail server below |
| SMS alerts | Out of the box, only for urgent alerts. Needs an SMS provider and internet |
| WhatsApp alerts | Out of the box, only for urgent alerts. Needs a WhatsApp Business provider and internet |

These switches turn a whole channel on or off. Which events use each channel, and what the messages say, are set
under Message templates (section 8).
| Alert the area manager on every new ticket | When off, area managers hear only about P1 tickets and escalations |
| Alert centre and area managers when a ticket is closed | Managers see each closure at their centres |
| Pop-up messages on screen | New tickets, closures, escalations, mentions and AMC warnings slide in at the bottom right of the screen |

Who gets which alert:

| Event | Who is alerted |
| --- | --- |
| New ticket | The owner, or the department head if nobody is assigned yet; the area manager. For P1 also the department head and centre manager |
| Assigned to someone | The new owner |
| Reply | The requester, the owner (or the head if unassigned) |
| Internal note | The owner (or the head if unassigned) |
| @mention | The person mentioned, if they can see the ticket |
| Resolved | The requester, asked to confirm |
| Closed | The owner, department head, and centre and area managers |
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
account; the provider guides you through both. DiagTrack has not yet been tried with a specific provider, so allow
time for this.

If the provider gives you a template name (WhatsApp) or a DLT template ID (SMS), put `{template}` in the message
format here, and type the name or ID on the matching event under Message templates.

## 8. Message templates

*Side menu, Message templates.* One page per event: New ticket, Assigned, Work started, On hold, Resolved, Closed,
Reopened, Cancelled, Transferred, Priority changed, New reply, Internal note, Mentioned, Escalation, AMC ending, and
Anything else. The list on the left shows for each event whether email, SMS and WhatsApp go out: dark blue is
**Always**, amber is **Only urgent**, outline is **Never**. A blue dot after a name means its wording was changed.

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
- **Back to built-in wording**: undoes your changes for this event, on all three channels.

The bell and the pop-ups always use the headline and detail; templates change only email, SMS and WhatsApp.

## 9. General settings and branding

*Settings and health*

| Setting | Default | Meaning |
| --- | --- | --- |
| Organisation name | Pramodini Medicare Limited | Shown in the menu, emails and reports |
| Address of this system | empty | The address staff type in the browser. Used in email links and QR labels |
| Close resolved tickets after | 3 days | If the requester does not confirm |
| Allow reopening for | 7 days | After the ticket was resolved |
| Largest attachment | 10 MB | Per file |
| Warn about AMC expiry | 60 days before | The first AMC warning |

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

## 11. Records you can look up

| What | Where |
| --- | --- |
| Every change to every ticket: who, what, when | The ticket page, Activity, History. Also *Masters and rules, Ticket history*, which cannot be edited or deleted |
| Every email, SMS and WhatsApp sent, and any failure | *Masters and rules, Sent alerts* |
| Every vendor call | *Masters and rules, Vendor calls* |

## 12. Common tasks

| Task | How |
| --- | --- |
| A new centre opens | Section 2, then tick the centre on its people |
| A new person joins | Section 4 |
| Someone leaves | Open them under Users, untick Active |
| Someone moves to another centre | Open them under Users, change the ticked centres |
| Change a department head | Open the department, choose the new head |
| Make deadlines longer or shorter | Section 5, SLA times |
| A new kind of problem | Add a category under the right department |
| A new machine | Add it under Equipment, print its QR label |
| Change what an alert says, or send it by WhatsApp too | Message templates (section 8) |
| An AMC is renewed | Change the AMC expiry date on the machine. Warnings start again for the new date |
| Add a second backup place, such as another centre's NAS | Backups page, **Add a backup destination** (section 13) |
| Copy everything to a USB drive | Backups page, **Save a full copy** |
| Go back to how things were yesterday | Backups page, **Restore** (Runbook, section 7) |
| Move DiagTrack to another server | Backups page, **Prepare to move** (Runbook, section 9) |

## 13. Backups

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

**Each backup folder** has its own settings, under **Change schedule, retention or folder**:

| Setting | Meaning |
| --- | --- |
| Name, Folder | For example "Main NAS" and `\\nas01\backups\diagtrack`. In Docker: `/backups` or `/backups2` |
| Frequency, Time | Every few hours, daily, weekly (choose the day) or monthly (choose the date), at the time given |
| Daily, weekly and monthly copies to keep | For example 14, 8 and 12: the newest backup of each of the last 14 days, the last 8 weeks and the last 12 months |
| Keep manual backups for | Days to keep backups made with Back up now |
| Alert if no backup for | Hours without a good backup before an overdue alert |
| NAS user name and password | Windows network shares only, when the share needs a login |
| Treat a missing marker file as "NAS not connected" | Put an empty file named `.diagtrack-backup-target` in the NAS folder first. Then a disconnected NAS is reported instead of filling the local disk |
| Switched on | Untick to pause a folder without removing it |

**Who is alerted.** Administrators always are. Tick others, for example the IT head. Alerts use the **Backup
failed** and **Backup overdue** wording on the Message templates page, on the channels switched on there.

**Move to another server.** See the Runbook, section 9.

