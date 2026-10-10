"""
Screen check: opens every screen as each kind of user, at desktop and phone width, in light and dark mode,
and reports layout faults that automated tests cannot see.

    python ci/screencheck.py [output folder]

It makes its own throwaway SQLite database with the demo data, starts DiagTrack on a spare port, and needs
Playwright with Chromium (pip install playwright, then: python -m playwright install chromium).
Screenshots and report.json go to the output folder (default: screencheck-output). Exit code 1 when a fault
is found.

Faults it finds:
  - the page scrolls sideways
  - a table or board hides part of itself behind a sideways scroll on a desktop screen
  - text squeezed into a narrow column, a few characters per line
  - JavaScript errors, failed requests, and screens that fail to open for a role that should see them
"""
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "app"
OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "screencheck-output").resolve()
ADMIN_PASSWORD = "Screen-Check-2026"
DEMO_PASSWORD = "Demo@12345"

# Screens a user may not open give 403 or 404; those are not faults, so each role lists only its own screens.
COMMON = ["/", "/tickets/", "/tickets/{ticket}/", "/alerts/", "/me/", "/password/"]
RESOLVER = COMMON + ["/tickets/new/", "/tickets/board/", "/equipment/", "/equipment/{equipment}/"]
OVERSEER = RESOLVER + ["/dashboard/", "/centres/", "/reports/"]
ADMIN = OVERSEER + ["/reports/print/", "/equipment/labels/", "/settings/", "/settings/templates/", "/settings/backups/",
                    "/admin/", "/admin/tickets/user/", "/admin/tickets/user/add/", "/admin/tickets/equipment/",
                    "/admin/tickets/equipment/add/", "/admin/tickets/centre/", "/admin/tickets/department/",
                    "/admin/tickets/category/", "/admin/tickets/slapolicy/", "/admin/tickets/escalationrule/",
                    "/admin/tickets/vendor/"]
ROLES = {  # role: (login, password, screens)
    "admin": ("admin", ADMIN_PASSWORD, ADMIN),
    "head": ("srinivas", DEMO_PASSWORD, OVERSEER),
    "agent": ("ravi", DEMO_PASSWORD, RESOLVER),
    "requester": ("meena", DEMO_PASSWORD, COMMON + ["/tickets/new/"]),
    "management": ("director", DEMO_PASSWORD, ["/", "/tickets/", "/tickets/board/", "/dashboard/", "/centres/",
                                              "/reports/", "/equipment/", "/alerts/", "/me/"]),
}
VIEWS = [("desktop", 1280, 800, "light"), ("phone", 390, 844, "light"),
         ("desktop-dark", 1280, 800, "dark"), ("phone-dark", 390, 844, "dark")]
# Laid out as an A4 page on purpose: on a phone it scrolls sideways.
ALLOWED = {("/reports/print/", "phone"), ("/reports/print/", "phone-dark")}

PROBE = r"""
() => {
  const vis = el => { const s = getComputedStyle(el); return s.display !== 'none' && s.visibility !== 'hidden'; };
  const desc = el => el.tagName.toLowerCase() + (typeof el.className === 'string' && el.className.trim()
      ? '.' + el.className.trim().split(/\s+/).slice(0, 3).join('.') : '');
  const out = {sideways: document.documentElement.scrollWidth - window.innerWidth, squashed: [], hidden: []};
  for (const el of document.querySelectorAll('body *')) {
    if (!vis(el)) continue;
    const r = el.getBoundingClientRect();
    if (!r.width || !r.height) continue;
    const own = [...el.childNodes].filter(n => n.nodeType === 3).map(n => n.textContent.trim()).join(' ').trim();
    const lh = parseFloat(getComputedStyle(el).lineHeight) || 18;
    if (own.length > 14 && r.width < 70 && r.height > lh * 3.5)
      out.squashed.push(desc(el) + ' "' + own.slice(0, 40) + '" is ' + Math.round(r.width) + 'px wide');
    const ox = getComputedStyle(el).overflowX;
    if ((ox === 'auto' || ox === 'scroll') && el.scrollWidth - el.clientWidth > 4 && el.closest('main'))
      out.hidden.push(desc(el) + ' hides ' + (el.scrollWidth - el.clientWidth) + 'px');
  }
  out.squashed = out.squashed.slice(0, 5); out.hidden = out.hidden.slice(0, 3);
  return out;
}
"""


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def manage(env, *args):
    subprocess.run([sys.executable, "manage.py", *args], cwd=APP, env=env, check=True,
                   stdout=subprocess.DEVNULL)


def main():
    from playwright.sync_api import sync_playwright

    work = Path(tempfile.mkdtemp(prefix="screencheck-"))
    env = {k: v for k, v in os.environ.items() if not k.startswith(("DB_", "DIAGTRACK_HOME"))}
    env.update(DEBUG="1", SQLITE_PATH=str(work / "db.sqlite3"), STATUS_DIR=str(work / "status"),
               MEDIA_ROOT=str(work / "media"), ADMIN_PASSWORD=ADMIN_PASSWORD, SECRET_KEY="screen-check-only")
    manage(env, "migrate", "--noinput")
    manage(env, "setup_initial")
    manage(env, "seed_demo")
    # a ticket each person may open, and a machine
    logins = json.dumps([login for login, _, _ in ROLES.values()])
    out = subprocess.run([sys.executable, "manage.py", "shell", "-c",
                          "import json; from tickets import services; from tickets.models import User, Equipment;"
                          f"print(json.dumps({{u: services.visible_tickets(User.objects.get(username=u))"
                          f".order_by('-equipment_id').first().number for u in {logins}}}),"
                          " Equipment.objects.first().pk)"],
                         cwd=APP, env=env, check=True, capture_output=True, text=True).stdout.strip().splitlines()[-1]
    tickets, equipment = out.rsplit(" ", 1)
    fill = {"tickets": json.loads(tickets), "equipment": equipment}

    port = free_port()
    base = f"http://127.0.0.1:{port}"
    server = subprocess.Popen([sys.executable, "manage.py", "runserver", f"127.0.0.1:{port}", "--noreload"],
                              cwd=APP, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(60):
            try:
                urllib.request.urlopen(base + "/login/", timeout=2)
                break
            except OSError:
                time.sleep(0.5)
        shutil.rmtree(OUT, ignore_errors=True)
        (OUT / "shots").mkdir(parents=True)
        report = check(sync_playwright, base, fill)
    finally:
        server.terminate()
        shutil.rmtree(work, ignore_errors=True)

    (OUT / "report.json").write_text(json.dumps(report, indent=1))
    faults = [r for r in report if r["faults"]]
    print(f"{len(report)} screens checked, {len(faults)} with faults. Screenshots: {OUT / 'shots'}")
    for r in faults:
        print(f"- {r['role']} / {r['view']} / {r['url']}")
        for f in r["faults"]:
            print(f"    {f}")
            if os.environ.get("GITHUB_ACTIONS"):
                print(f"::error title=Screen check {r['url']} ({r['role']}, {r['view']})::{f}")
    return 1 if faults else 0


def check(sync_playwright, base, fill):
    report = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for role, (login, password, urls) in ROLES.items():
            for view, width, height, scheme in VIEWS:
                if "dark" in view and role != "admin":
                    continue  # dark mode: the administrator sees every screen, which is enough
                ctx = browser.new_context(viewport={"width": width, "height": height}, color_scheme=scheme,
                                          reduced_motion="reduce")
                page = ctx.new_page()
                errors = []
                page.on("pageerror", lambda e: errors.append(f"JavaScript error: {str(e)[:160]}"))
                page.on("response", lambda r: errors.append(f"HTTP {r.status} for {r.url[len(base):][:80]}")
                        if r.status >= 400 and r.url.startswith(base) else None)
                page.goto(base + "/login/")
                page.fill("input[name=username]", login)
                page.fill("input[name=password]", password)
                page.click("button[type=submit]")
                page.wait_for_load_state("networkidle")
                for url in urls:
                    url = url.format(ticket=fill["tickets"][login], equipment=fill["equipment"])
                    errors.clear()
                    page.goto(base + url)
                    page.wait_for_load_state("networkidle")
                    info = page.evaluate(PROBE)
                    name = f"{role}_{view}_{url.strip('/').replace('/', '_') or 'home'}.png"
                    page.screenshot(path=str(OUT / "shots" / name), full_page=True)
                    faults = list(dict.fromkeys(errors))
                    if (url, view) not in ALLOWED:
                        if info["sideways"] > 1:
                            faults.append(f"the page scrolls sideways by {info['sideways']}px")
                        if not url.startswith("/admin/"):  # Django's own screens: page width and errors only
                            faults += [f"text squeezed: {s}" for s in info["squashed"]]
                        if width >= 1200 and not url.startswith("/admin/"):
                            faults += [f"part of it is hidden behind a sideways scroll: {s}" for s in info["hidden"]]
                    report.append({"role": role, "view": view, "url": url, "shot": name, "faults": faults})
                ctx.close()
        browser.close()
    return report


if __name__ == "__main__":
    sys.exit(main())
