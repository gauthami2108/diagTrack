from django import template
from django.templatetags.static import static
from django.utils import timezone
from django.utils.html import format_html

register = template.Library()


def _span(seconds):
    seconds = int(abs(seconds))
    d, rem = divmod(seconds, 86400)
    h, rem = divmod(rem, 3600)
    m = rem // 60
    if d:
        return f"{d}d {h}h"
    if h:
        return f"{h}h {m}m"
    return f"{m}m"


@register.filter
def span(seconds):
    return _span(seconds or 0)


@register.simple_tag
def icon(name, cls=""):
    return format_html('<svg class="i {}" aria-hidden="true"><use href="{}#{}"/></svg>', cls, static("icons.svg"), name)


@register.inclusion_tag("_sla.html")
def sla(ticket, compact=False):
    """The SLA countdown: how long is left, as words and as a bar."""
    now = timezone.now()
    state = ticket.sla_state
    if state == "paused":
        label = "Paused on hold"
    elif state == "done":
        label = "Met"
    elif not ticket.is_open:
        label = "Missed"
    else:
        left = (ticket.resolution_due - now).total_seconds()
        label = f"{_span(left)} left" if left >= 0 else f"{_span(left)} overdue"
    return {"t": ticket, "state": state, "label": label, "compact": compact, "pct": ticket.sla_percent}


@register.filter
def get(d, key):
    return d.get(key) if hasattr(d, "get") else None


@register.simple_tag(takes_context=True)
def qs(context, **changes):
    """Current query string with some values replaced; keeps filters while paging or sorting."""
    params = context["request"].GET.copy()
    for k, v in changes.items():
        if v in (None, ""):
            params.pop(k, None)
        else:
            params[k] = v
    return params.urlencode()


# --------------------------------------------------------------------------
# People
# --------------------------------------------------------------------------
@register.simple_tag
def avatar(user, size="m"):
    """A person's photo, or their initials on a colour that is always the same for them."""
    if not user:
        return format_html('<span class="av av-{} av-none" aria-hidden="true"></span>', size)
    if getattr(user, "photo", None):
        return format_html('<img class="av av-{}" src="/people/{}/photo" alt="" title="{}">', size, user.pk,
                           user.display_name)
    return format_html('<span class="av av-{}" style="--h:{}" title="{}" aria-hidden="true">{}</span>',
                       size, user.hue, user.display_name, user.initials)


# --------------------------------------------------------------------------
# Comment formatting: a small, safe subset of Markdown
# --------------------------------------------------------------------------
import re as _re

from django.utils.html import escape as _escape
from django.utils.safestring import mark_safe as _safe

_CODE_BLOCK = _re.compile(r"```\n?(.*?)```", _re.S)
_INLINE_CODE = _re.compile(r"`([^`\n]+)`")
_BOLD = _re.compile(r"\*\*(.+?)\*\*")
_ITALIC = _re.compile(r"(?<![\w*])_(?!_)(.+?)(?<!_)_(?!\w)")
_LINK = _re.compile(r"\[([^\]\n]+)\]\((https?://[^\s)]+)\)")
_URL = _re.compile(r"(?<![\"=>])\b(https?://[^\s<]+[^\s<.,;:!?)\]])")
_MENTION = _re.compile(r"(?<![\w@&])@([A-Za-z0-9_.+-]{2,150})")


def _inline(text, people):
    text = _INLINE_CODE.sub(lambda m: f"<code>{m.group(1)}</code>", text)
    text = _BOLD.sub(r"<strong>\1</strong>", text)
    text = _ITALIC.sub(r"<em>\1</em>", text)
    text = _LINK.sub(r'<a href="\2" target="_blank" rel="noopener noreferrer">\1</a>', text)
    text = _URL.sub(r'<a href="\1" target="_blank" rel="noopener noreferrer">\1</a>', text)

    def mention(m):
        name = m.group(1).rstrip(".,;:!?")
        rest = m.group(1)[len(name):]
        who = people.get(name)
        return (f'<span class="mention">@{_escape(who)}</span>{rest}' if who else m.group(0))
    return _MENTION.sub(mention, text)


@register.filter
def rich(body):
    """Bold, italics, code, lists, links and @mentions. All HTML typed by people is escaped first."""
    from tickets.models import User
    if not body:
        return ""
    names = {m.rstrip(".,;:!?") for m in _MENTION.findall(body)}
    people = {u.username: u.display_name for u in User.objects.filter(username__in=names)} if names else {}
    blocks, out = [], []
    def keep(m):
        blocks.append(m.group(1))
        return f"\x00{len(blocks) - 1}\x00"
    text = _CODE_BLOCK.sub(keep, body)
    text = _escape(text)
    lines, i = text.split("\n"), 0
    while i < len(lines):
        line = lines[i]
        if _re.match(r"^\s*([-*])\s+", line):
            items = []
            while i < len(lines) and _re.match(r"^\s*[-*]\s+", lines[i]):
                items.append("<li>" + _inline(_re.sub(r"^\s*[-*]\s+", "", lines[i]), people) + "</li>")
                i += 1
            out.append("<ul>" + "".join(items) + "</ul>")
            continue
        if _re.match(r"^\s*\d+[.)]\s+", line):
            items = []
            while i < len(lines) and _re.match(r"^\s*\d+[.)]\s+", lines[i]):
                items.append("<li>" + _inline(_re.sub(r"^\s*\d+[.)]\s+", "", lines[i]), people) + "</li>")
                i += 1
            out.append("<ol>" + "".join(items) + "</ol>")
            continue
        if line.startswith("&gt; "):
            out.append("<blockquote>" + _inline(line[5:], people) + "</blockquote>")
        elif _re.fullmatch(r"\x00\d+\x00", line.strip()):
            out.append(line.strip())
        elif line.strip():
            out.append("<p>" + _inline(line, people) + "</p>")
        i += 1
    html = "".join(out)
    html = _re.sub(r"\x00(\d+)\x00", lambda m: "<pre><code>" + _escape(blocks[int(m.group(1))].strip("\n"))
                   + "</code></pre>", html)
    return _safe(html)


@register.filter
def lozenge(text):
    """CSS class for a status word in the history, so 'In progress' gets the in-progress colour."""
    return (text or "").lower().replace(" ", "_")
