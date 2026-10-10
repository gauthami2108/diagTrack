from .models import Notification, SiteSettings


def shell(request):
    """Values every page needs: organisation settings, unread alerts, the newest alert id for pop-ups."""
    ctx = {"cfg": SiteSettings.load()}
    user = getattr(request, "user", None)
    if user is not None and user.is_authenticated:
        mine = Notification.objects.filter(user=user)
        ctx["unread_count"] = mine.filter(read=False).count()
        ctx["recent_alerts"] = mine.select_related("ticket")[:8]
        ctx["last_alert_id"] = mine.order_by("-pk").values_list("pk", flat=True).first() or 0
    return ctx
