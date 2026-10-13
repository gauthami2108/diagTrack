"""
Sign-in lock-out. Wrong passwords are counted per user name and per computer (network address):

  - LOGIN_MAX_FAILURES wrong passwords for one user name within LOGIN_LOCK_MINUTES pause sign-in for that
    user name for LOGIN_LOCK_MINUTES. The name is paused whether or not it exists, so the message does not
    tell a guesser which names are real.
  - LOGIN_MAX_FAILURES_PER_ADDRESS from one computer pause that computer, which stops guessing across many names.

A successful sign-in clears the user name's count. Administrators are alerted when a lock starts and can
lift it in Masters and rules (Sign-in locks, or the Unlock action on Users) or with: manage.py unlock <name>.
"""
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .models import SignInLock


def _limits():
    return (int(getattr(settings, "LOGIN_MAX_FAILURES", 5)), int(getattr(settings, "LOGIN_MAX_FAILURES_PER_ADDRESS", 20)),
            timedelta(minutes=int(getattr(settings, "LOGIN_LOCK_MINUTES", 15))))


def user_key(username):
    return "user:" + (username or "").strip().lower()[:150]


def address_key(address):
    return "ip:" + (address or "unknown")[:64]


def client_address(request):
    """The computer signing in. Behind DiagTrack's own proxy (Docker) the proxy adds the address it saw last."""
    if getattr(settings, "TRUST_X_FORWARDED_FOR", False):
        forwarded = [a.strip() for a in request.META.get("HTTP_X_FORWARDED_FOR", "").split(",") if a.strip()]
        if forwarded:
            return forwarded[-1]
    return request.META.get("REMOTE_ADDR", "") or "unknown"


def locked_until(username, address):
    """When sign-in may be tried again, and whether it is the user name or the computer that is paused."""
    now = timezone.now()
    for key, what in ((user_key(username), "user"), (address_key(address), "address")):
        lock = SignInLock.objects.filter(key=key, locked_until__gt=now).first()
        if lock:
            return lock.locked_until, what
    return None, None


def record_failure(username, address):
    """Counts one wrong password. Returns the locks that started just now."""
    max_user, max_addr, span = _limits()
    now = timezone.now()
    started = []
    for key, limit in ((user_key(username), max_user), (address_key(address), max_addr)):
        if limit <= 0:
            continue
        with transaction.atomic():
            lock, _ = SignInLock.objects.select_for_update().get_or_create(key=key)
            expired = lock.locked_until and lock.locked_until <= now
            if expired or now - lock.first_failure > span or not lock.failures:
                lock.failures, lock.first_failure, lock.locked_until = 0, now, None
            lock.failures += 1
            lock.last_address = (address or "")[:64]
            if lock.failures >= limit and not lock.locked_until:
                lock.locked_until = now + span
                started.append(lock)
            lock.save()
    for lock in started:
        _alert(lock)
    return started


def record_success(username):
    SignInLock.objects.filter(key=user_key(username)).delete()


def unlock(username=None, address=None):
    keys = ([user_key(username)] if username else []) + ([address_key(address)] if address else [])
    return SignInLock.objects.filter(key__in=keys).delete()[0]


def _alert(lock):
    from .models import User
    from .notify import notify
    admins = list(User.objects.filter(role=User.ADMIN, is_active=True)) + list(User.objects.filter(is_superuser=True, is_active=True))
    admins = list({u.pk: u for u in admins}.values())
    until = timezone.localtime(lock.locked_until).strftime("%H:%M")
    title = f"Sign-in paused: {lock.who.lower()}"
    body = (f"{lock.failures} wrong passwords in a row, last from {lock.last_address or 'an unknown computer'}. "
            f"Sign-in is paused until {until}. If this was not a person who forgot a password, check who is trying.")
    notify(admins, None, title, body, urgent=True, kind="signinlock", link="/admin/tickets/signinlock/")


# --------------------------------------------------------------------------
# Sign-in forms: the main sign-in page and the Masters and rules (admin) sign-in page
# --------------------------------------------------------------------------
from django import forms  # noqa: E402
from django.contrib.admin.forms import AdminAuthenticationForm  # noqa: E402
from django.contrib.auth.forms import AuthenticationForm  # noqa: E402


class LockoutMixin:
    def clean(self):
        username = self.cleaned_data.get("username") or ""
        address = client_address(self.request) if self.request else ""
        until, what = locked_until(username, address)
        if until:
            raise self._paused(until, what)
        try:
            cleaned = super().clean()
        except forms.ValidationError as exc:
            if getattr(exc, "code", None) == "invalid_login" or "invalid_login" in [getattr(e, "code", None) for e in exc.error_list]:
                started = record_failure(username, address)
                if started:
                    lock = started[0]
                    raise self._paused(lock.locked_until, "user" if lock.key.startswith("user:") else "address") from None
            raise
        record_success(username)
        return cleaned

    @staticmethod
    def _paused(until, what):
        at = timezone.localtime(until).strftime("%H:%M")
        if what == "address":
            text = (f"Too many wrong passwords from this computer. Sign-in from here is paused until {at}. "
                    "An administrator can lift it sooner.")
        else:
            text = (f"Too many wrong passwords for this user name. Sign-in is paused until {at}. "
                    "An administrator can lift it sooner, or use Forgot your password.")
        return forms.ValidationError(text, code="locked")


class SignInForm(LockoutMixin, AuthenticationForm):
    error_messages = {
        **AuthenticationForm.error_messages,
        "invalid_login": "That user name and password did not match. Check both and try again.",
    }


class AdminSignInForm(LockoutMixin, AdminAuthenticationForm):
    pass


# --------------------------------------------------------------------------
# Session management and invalidation
# --------------------------------------------------------------------------
def invalidate_user_sessions(user, keep_session_key=None):
    """
    Terminates active database sessions for the given user.
    If keep_session_key is provided, preserves that specific session
    (e.g., the current session when a user changes their password).
    Returns the number of sessions terminated.
    """
    if not user or not getattr(user, "is_authenticated", False):
        return 0
    from django.contrib.sessions.models import Session
    now = timezone.now()
    active_sessions = Session.objects.filter(expire_date__gte=now)
    count = 0
    user_id_str = str(user.pk)
    for s in active_sessions:
        if keep_session_key and s.session_key == keep_session_key:
            continue
        try:
            data = s.get_decoded()
            if str(data.get("_auth_user_id")) == user_id_str:
                s.delete()
                count += 1
        except Exception:
            pass
    return count
