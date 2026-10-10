"""
Bulk import from Excel: centres, vendors, departments, people and equipment in one workbook.

The workbook that DiagTrack hands out (workbook()) already holds the current data, so the same file is used to add
new rows and to change existing ones. read() checks every row of every sheet before anything is saved: a
reference such as a centre code may point at something already in DiagTrack or at a row elsewhere in the file.
apply() saves the whole file in one transaction, so a file is either imported completely or not at all.

Rules for each cell:
  - a filled cell sets the value; an empty cell leaves an existing value as it is (and is empty on a new row)
  - Yes/No columns accept Yes, No, Y, N, True, False, 1, 0
  - lists (a person's centres or departments) are separated by commas; "All" for centres means every centre
  - rows are matched to what exists by: centre code, vendor name, department name, user name, and for
    equipment the centre plus serial number (or the centre plus name when there is no serial number)
"""
import io
import re
import secrets
from dataclasses import dataclass, field
from datetime import date, datetime

from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import transaction

from .models import Centre, Department, Equipment, User, Vendor

ROLE_NAMES = {
    "requester": User.REQUESTER, "agent": User.AGENT, "department head": User.DEPT_HEAD, "head": User.DEPT_HEAD,
    "centre manager": User.CENTRE_MANAGER, "center manager": User.CENTRE_MANAGER,
    "area manager": User.AREA_MANAGER, "management": User.MANAGEMENT, "administrator": User.ADMIN,
    "admin": User.ADMIN,
}
ROLE_LABEL = {User.REQUESTER: "Requester", User.AGENT: "Agent", User.DEPT_HEAD: "Department head",
              User.CENTRE_MANAGER: "Centre manager", User.AREA_MANAGER: "Area manager",
              User.MANAGEMENT: "Management", User.ADMIN: "Administrator"}
MODE_NAMES = {"round robin": Department.ROUND_ROBIN, "least loaded": Department.LEAST_LOADED,
              "manual": Department.MANUAL}
MODE_LABEL = {v: k.capitalize() for k, v in MODE_NAMES.items()}
YES = {"yes", "y", "true", "1", "active"}
NO = {"no", "n", "false", "0", "inactive"}

# sheet: [(column heading, key, required, note for the Read me sheet)]
SHEETS = {
    "Centres": [
        ("Code", "code", True, "2 to 5 capital letters or digits. The ticket number prefix, for example KKP"),
        ("Name", "name", True, "For example Kukatpally"),
        ("City", "city", False, ""),
        ("Active", "active", False, "Yes or No. No hides a closed centre; its tickets stay"),
    ],
    "Vendors": [
        ("Name", "name", True, "The service company, for example MediServe Engineering"),
        ("Contact person", "contact_person", False, ""),
        ("Phone", "phone", False, ""),
        ("Email", "email", False, ""),
        ("Notes", "notes", False, ""),
    ],
    "Departments": [
        ("Name", "name", True, "For example Biomedical"),
        ("Head (user name)", "head", False, "The user name of the department head, from the People sheet or DiagTrack"),
        ("How tickets are assigned", "assignment_mode", False, "Round robin, Least loaded or Manual"),
        ("Receives equipment tickets", "handles_equipment", False, "Yes for the department that repairs machines"),
        ("Active", "active", False, "Yes or No"),
    ],
    "People": [
        ("User name", "username", True, "What they type to sign in. Letters, digits and . _ - only"),
        ("First name", "first_name", False, ""),
        ("Last name", "last_name", False, ""),
        ("Email", "email", False, "Needed for email alerts and Forgot your password"),
        ("Mobile", "mobile", False, "With country code, for SMS and WhatsApp: 919876543210"),
        ("Role", "role", True, "Requester, Agent, Department head, Centre manager, Area manager, Management "
                               "or Administrator"),
        ("Centres", "centres", False, "Centre codes separated by commas, for example KKP, BJH. All for every centre"),
        ("Departments", "departments", False, "Only for agents and heads: department names separated by commas"),
        ("Active", "is_active", False, "No when someone has left. Never delete people who have tickets"),
        ("Initial password", "password", False, "New people only. Leave empty and DiagTrack makes one and shows "
                                                "it once after the import"),
    ],
    "Equipment": [
        ("Centre code", "centre", True, "The centre the machine is at"),
        ("Name", "name", True, "For example MRI 1.5T, CT 128 slice"),
        ("Make", "make", False, "For example Siemens"),
        ("Model", "model", False, ""),
        ("Serial number", "serial_no", False, "Recommended: it tells two machines with the same name apart"),
        ("Vendor", "vendor", False, "A vendor name, from the Vendors sheet or DiagTrack"),
        ("AMC expiry date", "amc_expiry", False, "A date, for example 31-03-2027"),
        ("Active", "active", False, "Yes or No"),
    ],
}
ORDER = ["Centres", "Vendors", "Departments", "People", "Equipment"]


class ImportFileError(Exception):
    """The file cannot be read at all."""


@dataclass
class Row:
    sheet: str
    line: int
    values: dict
    action: str = ""  # new, change, same, error
    changes: list = field(default_factory=list)
    errors: list = field(default_factory=list)
    notes: list = field(default_factory=list)
    obj: object = None
    label: str = ""


@dataclass
class Plan:
    rows: dict = field(default_factory=lambda: {s: [] for s in ORDER})
    missing_sheets: list = field(default_factory=list)

    @property
    def errors(self):
        return [r for s in ORDER for r in self.rows[s] if r.errors]

    def counts(self, sheet):
        rs = self.rows[sheet]
        return {k: sum(1 for r in rs if r.action == k) for k in ("new", "change", "same", "error")}

    @property
    def empty(self):
        return not any(r.action in ("new", "change") for s in ORDER for r in self.rows[s])


# --------------------------------------------------------------------------
# Cell values
# --------------------------------------------------------------------------
def _text(v):
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        v = int(v)  # numbers typed into Excel: mobiles, serial numbers
    if isinstance(v, (datetime, date)):
        return v.strftime("%d-%m-%Y")
    return re.sub(r"\s+", " ", str(v)).strip()


def _yes_no(text):
    t = text.lower()
    if t in YES:
        return True
    if t in NO:
        return False
    raise ValueError("Use Yes or No.")


def _date(v):
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    t = _text(v)
    for fmt in ("%d-%m-%Y", "%d/%m/%Y", "%Y-%m-%d", "%d.%m.%Y", "%d-%b-%Y", "%d %b %Y", "%d %B %Y"):
        try:
            return datetime.strptime(t, fmt).date()
        except ValueError:
            pass
    raise ValueError(f"'{t}' is not a date. Use day-month-year, for example 31-03-2027.")


def _list(text):
    return [p.strip() for p in re.split(r"[,;\n]", text) if p.strip()]


def _heading_key(heading):
    return re.sub(r"[^a-z]", "", re.sub(r"\(.*?\)", "", str(heading or "").lower()))


# --------------------------------------------------------------------------
# Reading and checking
# --------------------------------------------------------------------------
def read(data):
    """Reads and checks a workbook (bytes). Returns a Plan; nothing is saved."""
    from openpyxl import load_workbook
    try:
        wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception as exc:  # zip, xml or format errors from openpyxl
        raise ImportFileError("This is not an Excel workbook (.xlsx) that DiagTrack can read. "
                              "Save it from Excel as 'Excel Workbook (.xlsx)' and try again.") from exc
    plan = Plan()
    sheets = {_heading_key(n): n for n in wb.sheetnames}
    for sheet in ORDER:
        name = sheets.get(_heading_key(sheet)) or (sheets.get("users") if sheet == "People" else None)
        if not name:
            plan.missing_sheets.append(sheet)
            continue
        ws = wb[name]
        it = ws.iter_rows(values_only=True)
        header = next(it, None) or ()
        wanted = {_heading_key(h): key for h, key, _, _ in SHEETS[sheet]}
        cols = {i: wanted[_heading_key(h)] for i, h in enumerate(header) if _heading_key(h) in wanted}
        missing = [h for h, key, req, _ in SHEETS[sheet] if req and key not in cols.values()]
        if missing:
            row = Row(sheet, 1, {}, action="error")
            row.errors.append(f"The heading row has no column {', '.join(missing)}.")
            plan.rows[sheet].append(row)
            continue
        for n, values in enumerate(it, start=2):
            raw = {key: values[i] if i < len(values) else None for i, key in cols.items()}
            if all(_text(v) == "" for v in raw.values()):
                continue
            plan.rows[sheet].append(Row(sheet, n, raw))
    wb.close()
    _check(plan)
    return plan


def _check(plan):
    centres = {c.code: c for c in Centre.objects.all()}
    vendors = {v.name.lower(): v for v in Vendor.objects.all()}
    depts = {d.name.lower(): d for d in Department.objects.all()}
    users = {u.username.lower(): u for u in User.objects.all()}
    # names that rows in this file will create, so other sheets may refer to them
    new_centres = {_text(r.values.get("code")).upper() for r in plan.rows["Centres"]}
    new_vendors = {_text(r.values.get("name")).lower() for r in plan.rows["Vendors"]}
    new_depts = {_text(r.values.get("name")).lower() for r in plan.rows["Departments"]}
    new_users = {_text(r.values.get("username")).lower() for r in plan.rows["People"]}

    def seen_twice(sheet, key_of):
        seen = {}
        for r in plan.rows[sheet]:
            k = key_of(r)
            if k and k in seen:
                r.errors.append(f"Same as row {seen[k]}: each one may appear only once.")
            seen.setdefault(k, r.line)

    for r in plan.rows["Centres"]:
        if r.action == "error":
            continue
        v = {k: _text(x) for k, x in r.values.items()}
        code = v.get("code", "").upper()
        r.label = code
        if not re.fullmatch(r"[A-Z0-9]{2,5}", code):
            r.errors.append("Code must be 2 to 5 capital letters or digits, for example KKP.")
        if not v.get("name") and code not in centres:
            r.errors.append("Name is empty.")
        _compare(r, centres.get(code), {"name": v.get("name"), "city": v.get("city")}, v, ("active",))
    seen_twice("Centres", lambda r: _text(r.values.get("code")).upper())

    for r in plan.rows["Vendors"]:
        if r.action == "error":
            continue
        v = {k: _text(x) for k, x in r.values.items()}
        r.label = v.get("name", "")
        if not r.label:
            r.errors.append("Name is empty.")
        if v.get("email"):
            _check_email(r, v["email"])
        _compare(r, vendors.get(r.label.lower()), {k: v.get(k) for k in ("contact_person", "phone", "email", "notes")}, v)
    seen_twice("Vendors", lambda r: _text(r.values.get("name")).lower())

    for r in plan.rows["Departments"]:
        if r.action == "error":
            continue
        v = {k: _text(x) for k, x in r.values.items()}
        r.label = v.get("name", "")
        if not r.label:
            r.errors.append("Name is empty.")
        fields = {}
        if v.get("assignment_mode"):
            mode = MODE_NAMES.get(v["assignment_mode"].lower().split(":")[0].strip()) or \
                (v["assignment_mode"] if v["assignment_mode"] in MODE_LABEL else None)
            if not mode:
                r.errors.append("How tickets are assigned: use Round robin, Least loaded or Manual.")
            fields["assignment_mode"] = mode
        head = v.get("head", "").lower()
        if head and head not in users and head not in new_users:
            r.errors.append(f"Head: there is nobody with the user name '{v['head']}' in DiagTrack or on the People sheet.")
        r.values["_head"] = head
        _compare(r, depts.get(r.label.lower()), fields, v, ("handles_equipment", "active"))
        existing = depts.get(r.label.lower())
        if head and not r.errors and (not existing or not existing.head or existing.head.username.lower() != head):
            r.changes.append(f"head: {v['head']}")
            if r.action == "same":
                r.action = "change"
    seen_twice("Departments", lambda r: _text(r.values.get("name")).lower())

    for r in plan.rows["People"]:
        if r.action == "error":
            continue
        v = {k: _text(x) for k, x in r.values.items()}
        username = v.get("username", "")
        r.label = username
        existing = users.get(username.lower())
        if not re.fullmatch(r"[\w.@+-]{1,150}", username):
            r.errors.append("User name: letters, digits and . _ - @ + only, no spaces.")
        fields = {k: v.get(k) for k in ("first_name", "last_name", "mobile")}
        if v.get("email"):
            _check_email(r, v["email"])
            fields["email"] = v["email"]
        if v.get("mobile") and not re.fullmatch(r"\+?\d{8,15}", v["mobile"].replace(" ", "")):
            r.errors.append("Mobile: digits only, with the country code, for example 919876543210.")
        else:
            fields["mobile"] = v.get("mobile", "").replace(" ", "").lstrip("+")
        role = ROLE_NAMES.get(v.get("role", "").lower().split(":")[0].strip())
        if v.get("role") and not role:
            r.errors.append("Role: use Requester, Agent, Department head, Centre manager, Area manager, Management "
                            "or Administrator.")
        elif not role and not existing:
            r.errors.append("Role is empty.")
        fields["role"] = role
        if v.get("centres"):
            codes = [c.upper() for c in _list(v["centres"])]
            if [c.lower() for c in codes] == ["all"]:
                codes = []
            bad = [c for c in codes if c not in centres and c not in new_centres]
            if bad:
                r.errors.append(f"Centres: no centre with the code {', '.join(bad)}.")
            r.values["_centres"] = codes
        if v.get("departments"):
            names = _list(v["departments"])
            bad = [d for d in names if d.lower() not in depts and d.lower() not in new_depts]
            if bad:
                r.errors.append(f"Departments: no department called {', '.join(bad)}.")
            r.values["_departments"] = [d.lower() for d in names]
        if v.get("password"):
            if existing:
                r.notes.append("Initial password ignored: this person already exists. Use Masters and rules to "
                               "set a new one.")
            else:
                try:
                    validate_password(v["password"], User(username=username, first_name=v.get("first_name", "")))
                except ValidationError as exc:
                    r.errors.append("Initial password: " + " ".join(exc.messages))
        r.values["_password"] = v.get("password", "") if not existing else ""
        _compare(r, existing, fields, v, ("is_active",))
        if existing and not r.errors:
            if "_centres" in r.values and sorted(c.code for c in existing.centres.all()) != sorted(r.values["_centres"]):
                r.changes.append("centres")
            if "_departments" in r.values and sorted(d.name.lower() for d in existing.departments.all()) != \
                    sorted(r.values["_departments"]):
                r.changes.append("departments")
            if r.changes and r.action == "same":
                r.action = "change"
    seen_twice("People", lambda r: _text(r.values.get("username")).lower())

    machines = list(Equipment.objects.select_related("centre"))
    for r in plan.rows["Equipment"]:
        if r.action == "error":
            continue
        v = {k: _text(x) for k, x in r.values.items()}
        code = v.get("centre", "").upper()
        r.label = f"{v.get('name', '')} at {code}"
        if code not in centres and code not in new_centres:
            r.errors.append(f"Centre code: no centre with the code '{v.get('centre', '')}'.")
        if not v.get("name"):
            r.errors.append("Name is empty.")
        if v.get("vendor") and v["vendor"].lower() not in vendors and v["vendor"].lower() not in new_vendors:
            r.errors.append(f"Vendor: no vendor called '{v['vendor']}' in DiagTrack or on the Vendors sheet.")
        fields = {k: v.get(k) for k in ("name", "make", "model", "serial_no")}
        if v.get("amc_expiry"):
            try:
                fields["amc_expiry"] = _date(r.values["amc_expiry"])
            except ValueError as exc:
                r.errors.append(f"AMC expiry date: {exc}")
        serial = v.get("serial_no", "").lower()
        name = v.get("name", "").lower()
        here = [m for m in machines if m.centre.code == code]
        existing = (next((m for m in here if serial and m.serial_no.lower() == serial), None)
                    # no serial match: the same name with no serial yet (a serial number being added), or no serial given
                    or next((m for m in here if m.name.lower() == name and (not serial or not m.serial_no)), None))
        r.values["_centre"], r.values["_vendor"] = code, v.get("vendor", "").lower()
        _compare(r, existing, fields, v, ("active",))
        if not r.errors and v.get("vendor") and (not existing or not existing.vendor or
                                                 existing.vendor.name.lower() != v["vendor"].lower()):
            r.changes.append(f"vendor: {v['vendor']}")
            if r.action == "same":
                r.action = "change"
    seen_twice("Equipment", lambda r: (_text(r.values.get("centre")).upper(),
                                       _text(r.values.get("serial_no")).lower() or _text(r.values.get("name")).lower()))

    for s in ORDER:
        for r in plan.rows[s]:
            if r.errors:
                r.action = "error"


def _check_email(row, email):
    try:
        validate_email(email)
    except ValidationError:
        row.errors.append(f"Email: '{email}' is not an email address.")


def _compare(row, existing, fields, v, flags=()):
    """Works out new / change / same, and the list of changed fields. Empty cells leave values alone."""
    values = {k: x for k, x in fields.items() if x not in (None, "")}
    for flag in flags:
        if v.get(flag):
            try:
                values[flag] = _yes_no(v[flag])
            except ValueError as exc:
                row.errors.append(f"{flag.replace('_', ' ').replace('is ', '').capitalize()}: {exc}")
    row.values["_fields"] = values
    row.obj = existing
    if row.errors:
        row.action = "error"
        return
    if not existing:
        row.action = "new"
        return
    row.changes = [k.replace("_", " ") for k, x in values.items() if getattr(existing, k) != x]
    row.action = "change" if row.changes else "same"


# --------------------------------------------------------------------------
# Saving
# --------------------------------------------------------------------------
@transaction.atomic
def apply(plan):
    """Saves a checked plan. Returns (counts per sheet, [(user name, generated password)])."""
    if plan.errors:
        raise ValueError("The file has errors; nothing was saved.")
    passwords = []
    centres = {c.code: c for c in Centre.objects.all()}
    for r in plan.rows["Centres"]:
        f = r.values["_fields"]
        code = _text(r.values["code"]).upper()
        if r.action == "new":
            centres[code] = Centre.objects.create(code=code, **f)
        elif r.action == "change":
            _update(r.obj, f)
    vendors = {v.name.lower(): v for v in Vendor.objects.all()}
    for r in plan.rows["Vendors"]:
        f = r.values["_fields"]
        if r.action == "new":
            vendors[r.label.lower()] = Vendor.objects.create(name=r.label, **f)
        elif r.action == "change":
            _update(r.obj, f)
    depts = {d.name.lower(): d for d in Department.objects.all()}
    for r in plan.rows["Departments"]:
        f = r.values["_fields"]
        if r.action == "new":
            depts[r.label.lower()] = Department.objects.create(name=r.label, **f)
        elif r.action == "change":
            _update(r.obj, f)
    users = {u.username.lower(): u for u in User.objects.all()}
    for r in plan.rows["People"]:
        f = dict(r.values["_fields"])
        if r.action == "new":
            password = r.values["_password"]
            if not password:
                password = _new_password()
                passwords.append((r.label, password))
            u = User(username=r.label, **f)
            u.set_password(password)
        elif r.action == "change":
            u = r.obj
            for k, x in f.items():
                setattr(u, k, x)
        else:
            continue
        u.is_staff = u.is_superuser = u.role == User.ADMIN
        u.save()
        users[u.username.lower()] = u
        if "_centres" in r.values:
            u.centres.set([centres[c] for c in r.values["_centres"]])
        if "_departments" in r.values:
            u.departments.set([depts[d] for d in r.values["_departments"]])
    for r in plan.rows["Departments"]:  # heads last: they may be people from this file
        if r.values.get("_head") and r.action in ("new", "change"):
            d = depts[r.label.lower()]
            d.head = users[r.values["_head"]]
            d.save(update_fields=["head"])
    for r in plan.rows["Equipment"]:
        f = dict(r.values["_fields"])
        if r.values.get("_vendor"):
            f["vendor"] = vendors[r.values["_vendor"]]
        if r.action == "new":
            Equipment.objects.create(centre=centres[r.values["_centre"]], **f)
        elif r.action == "change":
            _update(r.obj, f)
    return {s: plan.counts(s) for s in ORDER}, passwords


def _update(obj, fields):
    for k, x in fields.items():
        setattr(obj, k, x)
    obj.save()


def _new_password():
    words = ["lamp", "river", "cloud", "maple", "stone", "tiger", "piano", "lotus", "amber", "cedar", "delta", "ember",
             "fable", "grape", "harbor", "indigo", "jasmine", "kite", "lemon", "mango", "nectar", "orbit", "pepper"]
    return f"{secrets.choice(words).capitalize()}-{secrets.choice(words)}-{secrets.randbelow(900) + 100}"


# --------------------------------------------------------------------------
# The workbook DiagTrack hands out, with the current data
# --------------------------------------------------------------------------
def workbook():
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.worksheet.datavalidation import DataValidation

    wb = Workbook()
    readme = wb.active
    readme.title = "Read me"
    lines = [
        ("DiagTrack bulk import", True),
        ("Fill in the sheets, then upload this file on DiagTrack's Import from Excel page. Nothing is saved until "
         "you have seen what will change and pressed Import.", False),
        ("", False),
        ("This file already holds what is in DiagTrack. Add new rows at the bottom, or change existing ones.", False),
        ("An empty cell leaves the current value as it is. Rows cannot be deleted here: untick Active instead.", False),
        ("Order does not matter: a person may belong to a centre added on the Centres sheet of this same file.", False),
        ("Columns marked * must be filled on every row.", False),
        ("", False),
    ]
    for sheet in ORDER:
        lines.append((sheet, True))
        for heading, _, required, note in SHEETS[sheet]:
            lines.append((f"{heading}{' *' if required else ''}: {note}" if note else f"{heading}{' *' if required else ''}",
                          False))
        lines.append(("", False))
    for i, (text, bold) in enumerate(lines, start=1):
        c = readme.cell(row=i, column=1, value=text)
        c.font = Font(bold=bold, size=13 if i == 1 else 11)
        c.alignment = Alignment(wrap_text=True, vertical="top")
    readme.column_dimensions["A"].width = 110

    head_fill = PatternFill("solid", fgColor="16283F")
    data = _current_rows()
    for sheet in ORDER:
        ws = wb.create_sheet(sheet)
        cols = SHEETS[sheet]
        for j, (heading, _, required, _) in enumerate(cols, start=1):
            c = ws.cell(row=1, column=j, value=heading + (" *" if required else ""))
            c.font = Font(bold=True, color="FFFFFF")
            c.fill = head_fill
            ws.column_dimensions[c.column_letter].width = max(14, min(40, len(heading) + 8))
        for i, row in enumerate(data[sheet], start=2):
            for j, (_, key, _, _) in enumerate(cols, start=1):
                ws.cell(row=i, column=j, value=row.get(key))
        ws.freeze_panes = "A2"
        last = max(500, len(data[sheet]) + 300)
        for j, (_, key, _, _) in enumerate(cols, start=1):
            letter = ws.cell(row=1, column=j).column_letter
            choices = None
            if key in ("active", "is_active", "handles_equipment"):
                choices = '"Yes,No"'
            elif key == "role":
                choices = '"' + ",".join(ROLE_LABEL.values()) + '"'
            elif key == "assignment_mode":
                choices = '"Round robin,Least loaded,Manual"'
            if choices:
                dv = DataValidation(type="list", formula1=choices, allow_blank=True)
                dv.add(f"{letter}2:{letter}{last}")
                ws.add_data_validation(dv)
            if key == "amc_expiry":
                for i in range(2, len(data[sheet]) + 2):
                    ws.cell(row=i, column=j).number_format = "DD-MM-YYYY"
            if key in ("mobile", "serial_no", "phone", "code"):
                for i in range(2, last + 1):
                    ws.cell(row=i, column=j).number_format = "@"  # text, so Excel keeps leading zeros
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


def _yn(b):
    return "Yes" if b else "No"


def _current_rows():
    return {
        "Centres": [{"code": c.code, "name": c.name, "city": c.city, "active": _yn(c.active)}
                    for c in Centre.objects.order_by("code")],
        "Vendors": [{"name": v.name, "contact_person": v.contact_person, "phone": v.phone, "email": v.email,
                     "notes": v.notes} for v in Vendor.objects.order_by("name")],
        "Departments": [{"name": d.name, "head": d.head.username if d.head else "",
                         "assignment_mode": MODE_LABEL.get(d.assignment_mode, ""),
                         "handles_equipment": _yn(d.handles_equipment), "active": _yn(d.active)}
                        for d in Department.objects.select_related("head").order_by("name")],
        "People": [{"username": u.username, "first_name": u.first_name, "last_name": u.last_name, "email": u.email,
                    "mobile": u.mobile, "role": ROLE_LABEL.get(u.role, u.role),
                    "centres": ", ".join(c.code for c in u.centres.all()),
                    "departments": ", ".join(d.name for d in u.departments.all()), "is_active": _yn(u.is_active)}
                   for u in User.objects.prefetch_related("centres", "departments").order_by("username")],
        "Equipment": [{"centre": e.centre.code, "name": e.name, "make": e.make, "model": e.model,
                       "serial_no": e.serial_no, "vendor": e.vendor.name if e.vendor else "",
                       "amc_expiry": e.amc_expiry, "active": _yn(e.active)}
                      for e in Equipment.objects.select_related("centre", "vendor").order_by("centre__code", "name")],
    }
