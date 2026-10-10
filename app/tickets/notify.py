"""
Alerts.

notify() records an in-app alert and queues email / SMS / WhatsApp messages.
The background worker sends the queue (send_pending), so a slow mail server
never slows the screens down. Every channel has an on/off switch in Settings.
"""
import json
import logging
import urllib.request

from django.core.mail import EmailMessage
from django.utils import timezone

from . import wording
from .models import Notification, OutboundMessage, SiteSettings, User

log = logging.getLogger("diagtrack.notify")
MAX_ATTEMPTS = 3


def ticket_url(ticket, settings=None):
    settings = settings or SiteSettings.load()
    return f"{settings.site_url.rstrip('/')}/tickets/{ticket.number}/"


def notify(users, ticket, title, body="", urgent=False, exclude=None, kind="other", link=""):
    """Alert each user once: the bell, then email, SMS and WhatsApp as the Message templates say.
    kind is the event (raised, closed, escalation and so on). It picks the wording and decides how the
    pop-up looks. By default SMS and WhatsApp go out only for urgent alerts."""
    s = SiteSettings.load()
    seen = set()
    tpl = {}
    for ch, on in ((wording.EMAIL, s.email_enabled), (wording.SMS, s.sms_enabled), (wording.WHATSAPP, s.whatsapp_enabled)):
        if on:
            t = wording.get(kind, ch)
            if wording.should_send(t["send"], urgent):
                tpl[ch] = t
    for user in users:
        if user is None or user.pk in seen or not user.is_active:
            continue
        if exclude is not None and user.pk == exclude.pk:
            continue
        seen.add(user.pk)
        if s.inapp_enabled:
            Notification.objects.create(user=user, ticket=ticket, title=title[:200], body=body[:400],
                                        urgent=urgent, kind=kind, link=link[:200])
        if not tpl:
            continue
        ctx = wording.context(ticket, title, body, s, user, link)
        queue(tpl, ctx, email=user.email, mobile=user.mobile)


def queue(tpl, ctx, email="", mobile=""):
    """Queue one message per channel in tpl (channel -> template dict) for one person."""
    made = []
    for ch, t in tpl.items():
        to = email if ch == wording.EMAIL else mobile
        if not to:
            continue
        text = wording.render(t["body"], ctx)
        if ch in wording.LIMITS:
            text = text[:wording.LIMITS[ch]]
        made.append(OutboundMessage.objects.create(
            channel=ch, to=to, body=text, provider_template=t.get("provider_template", ""),
            subject=wording.render(t["subject"], ctx).replace("\n", " ")[:200] if ch == wording.EMAIL else ""))
    return made


def users_with_role(role, centre):
    return list(User.objects.filter(role=role, is_active=True, centres=centre))


def _parse_headers(text):
    headers = {}
    for line in text.splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            headers[k.strip()] = v.strip()
    return headers


def _http_send(url, headers_text, body_template, to, message, template=""):
    """Generic HTTP gateway call; works with most Indian SMS / WhatsApp providers."""
    if not url:
        raise ValueError("Gateway address is empty. Fill it in on the Settings screen.")
    safe = json.dumps(message)[1:-1]  # escape quotes and new lines for JSON bodies
    body = (body_template or '{"to": "{to}", "message": "{message}"}')
    body = body.replace("{to}", to).replace("{template}", json.dumps(template)[1:-1]).replace("{message}", safe)
    headers = {"Content-Type": "application/json"}
    headers.update(_parse_headers(headers_text))
    req = urllib.request.Request(url.replace("{to}", to), data=body.encode("utf-8"),
                                 headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=20) as resp:
        if resp.status >= 300:
            raise ValueError(f"Gateway answered {resp.status}")


def send_one(msg, settings=None):
    s = settings or SiteSettings.load()
    if msg.channel == OutboundMessage.EMAIL:
        if not s.smtp_host:
            raise ValueError("Mail server is empty. Fill it in on the Settings screen.")
        EmailMessage(msg.subject, msg.body, s.from_email or s.smtp_user, [msg.to]).send()
    elif msg.channel == OutboundMessage.SMS:
        _http_send(s.sms_url, s.sms_headers, s.sms_body, msg.to, msg.body, msg.provider_template)
    else:
        _http_send(s.whatsapp_url, s.whatsapp_headers, s.whatsapp_body, msg.to, msg.body, msg.provider_template)


def send_pending(limit=100):
    """Called by the worker every minute."""
    s = SiteSettings.load()
    sent = 0
    for msg in OutboundMessage.objects.filter(status=OutboundMessage.PENDING).order_by("created_at")[:limit]:
        msg.attempts += 1
        try:
            send_one(msg, s)
            msg.status, msg.sent_at, msg.error = OutboundMessage.SENT, timezone.now(), ""
            sent += 1
        except Exception as exc:  # keep going; show the reason on the Settings screen
            msg.error = str(exc)[:300]
            if msg.attempts >= MAX_ATTEMPTS:
                msg.status = OutboundMessage.FAILED
            log.warning("Could not send %s to %s: %s", msg.channel, msg.to, exc)
        msg.save()
    return sent
