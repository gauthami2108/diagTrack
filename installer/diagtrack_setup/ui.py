"""Console screens for setup: headings, numbered steps, questions, and a log with secrets removed."""
import getpass
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

_secrets = set()
_log_path = None
ANSWERS = {}  # unattended mode: answers by key, from --answers file.json (secrets from DT_<KEY> variables)


def preset(key):
    if not key:
        return None
    env = os.environ.get(f"DT_{key.upper()}")
    if env is not None:
        return env
    value = ANSWERS.get(key)
    return None if value is None else str(value)
COLOR = sys.stdout.isatty()
if os.name == "nt":
    os.system("")  # switches on colour codes in the Windows console


def _c(code, text):
    return f"\033[{code}m{text}\033[0m" if COLOR else text


def bold(t):
    return _c("1", t)


def dim(t):
    return _c("2", t)


def green(t):
    return _c("32", t)


def red(t):
    return _c("31", t)


def amber(t):
    return _c("33", t)


def blue(t):
    return _c("36", t)


def hide(value):
    """Remember a secret so it never reaches the log."""
    if value and len(value) >= 4:
        _secrets.add(value)


def redact(text):
    for s in sorted(_secrets, key=len, reverse=True):
        text = text.replace(s, "****")
    return text


def start_log(path):
    global _log_path
    _log_path = Path(path)
    _log_path.parent.mkdir(parents=True, exist_ok=True)
    log(f"=== DiagTrack setup started {datetime.now():%Y-%m-%d %H:%M:%S} ===")


def log(text):
    if _log_path:
        try:
            with open(_log_path, "a", encoding="utf-8") as f:
                f.write(f"{datetime.now():%H:%M:%S} {redact(str(text))}\n")
        except OSError:
            pass  # a log that cannot be written never stops setup


def say(text=""):
    print(text)
    log(text)


def banner(title, subtitle=""):
    line = "=" * 64
    say("")
    say(blue(line))
    say(bold(f"  {title}"))
    if subtitle:
        say(f"  {subtitle}")
    say(blue(line))


def section(title):
    say("")
    say(bold(title))
    say(dim("-" * len(title)))


class Steps:
    def __init__(self, titles):
        self.titles = titles
        self.n = 0

    def start(self, key, minutes=None):
        self.n += 1
        title = self.titles[key]
        extra = f" (about {minutes} minute{'s' if minutes != 1 else ''})" if minutes else ""
        say("")
        say(bold(f"Step {self.n} of {len(self.titles)}: {title}{extra}"))
        self.t0 = time.time()

    def ok(self, text="Done"):
        say(green(f"  OK  {text}"))

    def info(self, text):
        say(f"      {text}")

    def warn(self, text):
        say(amber(f"  !   {text}"))


class Failed(Exception):
    """A step failed. code like DT-S05, what failed, likely cause, what to do."""

    def __init__(self, code, what, cause="", todo=""):
        super().__init__(what)
        self.code, self.what, self.cause, self.todo = code, what, cause, todo


def show_failure(f, log_file):
    say("")
    say(red(bold(f"Setup stopped: {f.what}")))
    if f.cause:
        say(f"  Likely cause: {f.cause}")
    if f.todo:
        say(f"  What to do:   {f.todo}")
    say(f"  Error code:   {f.code}")
    say(f"  Log file:     {log_file}")
    say("  Running setup again continues from where it stopped.")


def ask(question, default="", check=None, hint="", key=None):
    given = preset(key)
    if given is not None:
        problem = check(given) if check else None
        if problem:
            raise Failed("DT-Q01", f"The unattended answer for {key} is not valid: {problem}")
        log(f"Q: {question} -> {given} (unattended)")
        return given
    while True:
        prompt = f"{question}" + (f" [{default}]" if default else "") + ": "
        if hint:
            print(dim(f"  {hint}"))
        answer = input(prompt).strip() or default
        log(f"Q: {question} -> {answer if answer not in _secrets else '****'}")
        problem = check(answer) if check else None
        if not problem:
            return answer
        say(red(f"  {problem}"))


def ask_secret(question, check=None, confirm=True, hint="", key=None):
    given = preset(key)
    if given is not None:
        problem = check(given) if check else (None if given else "empty")
        if problem:
            raise Failed("DT-Q02", f"The unattended secret DT_{key.upper()} is not valid: {problem}")
        hide(given)
        return given
    while True:
        if hint:
            print(dim(f"  {hint}"))
        first = getpass.getpass(f"{question}: ")
        problem = check(first) if check else (None if first else "This cannot be empty.")
        if problem:
            say(red(f"  {problem}"))
            continue
        if confirm and getpass.getpass("Type it again: ") != first:
            say(red("  The two entries do not match. Try again."))
            continue
        hide(first)
        log(f"Q: {question} -> ****")
        return first


def yes(question, default=True, key=None):
    given = preset(key)
    if given is not None:
        return given.lower() in ("1", "true", "yes", "y")
    d = "Y/n" if default else "y/N"
    while True:
        answer = input(f"{question} [{d}]: ").strip().lower()
        log(f"Q: {question} -> {answer or ('y' if default else 'n')}")
        if not answer:
            return default
        if answer in ("y", "yes"):
            return True
        if answer in ("n", "no"):
            return False


def choose(question, options, default=1, key=None):
    """options: list of (key, label). Returns the key."""
    given = preset(key)
    if given is not None:
        if given not in [k for k, _ in options]:
            raise Failed("DT-Q03", f"The unattended answer for {key} must be one of {[k for k, _ in options]}.")
        return given
    say("")
    say(bold(question))
    for i, (_, label) in enumerate(options, 1):
        say(f"  {i}. {label}")
    while True:
        answer = input(f"Choose 1-{len(options)} [{default}]: ").strip() or str(default)
        if answer.isdigit() and 1 <= int(answer) <= len(options):
            log(f"Q: {question} -> {options[int(answer) - 1][0]}")
            return options[int(answer) - 1][0]


def password_problem(pw, min_len=10):
    common = {"password", "password123", "admin123", "welcome123", "changeme", "changeme@123", "qwerty123",
              "pramodini", "diagtrack", "1234567890", "p@ssw0rd", "admin@123"}
    if len(pw) < min_len:
        return f"Use at least {min_len} characters."
    if pw.lower() in common or re.fullmatch(r"(.)\1+", pw):
        return "That password is too easy to guess."
    if not (re.search(r"[A-Za-z]", pw) and re.search(r"\d", pw)):
        return "Use letters and at least one digit."
    return None


def pause_exit(code=0):
    if os.name == "nt" and sys.stdin.isatty() and not ANSWERS:
        input("\nPress Enter to close this window.")
    return code
