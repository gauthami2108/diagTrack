# Administrator manual

Everything here is done on screen. No programming is needed.
Sign in as an administrator; the side menu then shows **Masters and rules** and **Settings and health**.

In Masters and rules, every list has an **Add** button at the top right. Click a row to change it. Press **Save** at the bottom.

## 1. Centres

*Masters and rules, Centres.*

| Field | What to enter |
| --- | --- |
| Name | The centre's name, for example Kukatpally |
| Short code | 2 to 5 capital letters, for example KKP. It becomes the ticket prefix: KKP-000123. It cannot be changed once the centre has tickets |
| City | Optional |
| Active | Untick when a centre closes. Its old tickets stay |

**Adding a new centre:** add the centre, add its equipment (section 5), then tick the new centre on each user who works there or looks after it (section 3).

## 2. Departments and categories

*Masters and rules, Departments.*

| Field | Meaning |
| --- | --- |
| Head | Receives unassigned tickets and escalations. Set this for every department |
| How tickets are assigned | **Round robin**: each agent in turn. **Least loaded**: the agent with the fewest open tickets. **Manual**: the head assigns each one |
| Receives equipment tickets | Tick for Biomedical only. Any ticket where equipment is selected goes here, whatever the category |

*Masters and rules, Categories.* A category is what the requester picks under "What is it about?". It decides where the ticket goes.

| Field | Meaning |
| --- | --- |
| Department | Tickets in this category are sent here |
| Default priority | The starting priority (P1 to P4) |
| Ask which equipment | Tick to show the equipment list for this category |
| Extra questions | Further fields shown for this category, for example "Error code". Each can be text, a number, or a pick list, and can be made compulsory |

**Adding a new department:** add the department with its head, add its categories, then tick the department on its agents (section 3).

## 3. Users

*Masters and rules, Users.* Give each person a user name and a first password; they change it after signing in (top right, Change password).

| Role | Can do | Sees |
| --- | --- | --- |
| Requester | Raise, reply, raise priority once, confirm or reopen | Own tickets |
| Agent | Start, hold, transfer, resolve | Tickets of their departments at their centres |
| Department head | Assign, reassign, change priority, cancel, plus everything an agent can | All tickets of their departments |
| Centre manager | Raise, reply | All tickets of their centres |
| Area manager | Raise, reply, transfer | All tickets of their centres |
| Management | View only | Everything |
| Administrator | Everything, plus these screens | Everything |

- **Centres:** tick where the person works (requesters, managers) or which centres they cover (agents). An agent with no centre ticked covers every centre.
- **Departments:** only for agents and department heads.
- **Email and mobile:** needed for email, SMS and WhatsApp alerts. Mobile with country code, for example 919876543210.
- **When someone leaves:** untick **Active**. Do not delete them; their name stays on old tickets.
- **Forgotten password:** open the user, click the link in the password line to set a new one. For the `admin` login itself, see the runbook, section 5.

## 4. SLA times and escalation

*Masters and rules, SLA times.* Times are in minutes on a 24x7 clock, and can be edited straight in the list.

| Priority | Respond within | Resolve within |
| --- | --- | --- |
| P1 Critical | 15 | 240 (4 hours) |
| P2 High | 30 | 480 (8 hours) |
| P3 Medium | 120 | 1440 (24 hours) |
| P4 Low | 240 | 4320 (72 hours) |

The four rows with an empty department are the default. To give one department different times, add a row and choose the department.
Changes apply to tickets raised afterwards.

*Masters and rules, Escalation rules.* Each rule says: when this much of the response or resolution time is used, alert these people.
The four starting rules:

| Rule | When | Who is alerted |
| --- | --- | --- |
| Resolution deadline close | 75% of resolution time used | Agent and department head |
| Response time missed | 100% of response time, nobody has started | Department head. The ticket also moves to the head |
| Resolution time missed | 100% of resolution time | Department head and centre manager |
| Still open well past the deadline | 150% of resolution time | Department head, centre manager and area manager |

Untick **Active** to switch a rule off. The clock does not run while a ticket is on hold.

A **P1** ticket also alerts the department head, centre manager and area manager the moment it is raised.

## 5. Equipment and vendors

*Masters and rules, Equipment.* One row per machine per centre: name, make, model, serial number, service vendor and AMC expiry date.
Staff pick from this list when they raise an equipment ticket. Downtime and uptime are calculated from tickets where the requester ticked "It is not working at all right now".

*Masters and rules, Vendors.* The service companies, with contact details.

## 6. Settings and health

*Settings and health* in the side menu.

**Health strip (top).** Four boxes; all should be green.

| Box | If red |
| --- | --- |
| Background worker | SLA checks and alerts have stopped. Runbook, section 4 |
| Backup to NAS | Last night's backup failed or the NAS is disconnected. Runbook, section 3 |
| Alerts sent out | Some emails or messages failed. The reason is shown. Usually a wrong mail server detail |

**Alert channels.** One switch each for in-app, email, SMS and WhatsApp, and one for "Alert the area manager on every new ticket".
SMS and WhatsApp are used only for P1 tickets and escalations, to keep message costs down.

**General.** Organisation name, the address of this system (used for links in emails), days until auto-close (3), days reopening is allowed (7), largest attachment (10 MB).

**Mail server.** Get these five values from whoever provides your email: server name, port, security, user name, password. Save, then press **Send me a test email**.

## 7. SMS and WhatsApp gateways

DiagTrack sends SMS and WhatsApp through a provider you subscribe to. It was built to work with any provider that accepts a web request, but it has not been tested against a specific provider, so allow time to try it with yours.

From the provider's "API" or "developer" page you need three things:

| Setting | What it is | Example shape |
| --- | --- | --- |
| Gateway address | The web address messages are sent to | `https://api.provider.example/v1/send` |
| Headers | The key that identifies your account, one per line | `Authorization: Bearer YOUR-KEY` |
| Message format | The text the provider expects, with `{to}` where the mobile number goes and `{message}` where the text goes | `{"to": "{to}", "text": "{message}", "sender": "DGTRAK"}` |

Save, then press **Send me a test SMS** (your own user record must have a mobile number). If it fails, the reason from the provider is shown; send that message to the provider's support.

Notes for India: SMS providers require a registered sender ID and approved message templates (DLT). WhatsApp requires an approved WhatsApp Business account and templates. Your provider guides you through both.

## 8. The audit trail

Every change to a ticket is recorded with who, what and when, and shown on the ticket's timeline.
The full list is under *Masters and rules, Ticket history*. It cannot be edited or deleted.
