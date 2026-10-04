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
