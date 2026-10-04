from .models import Notification, SiteSettings


def shell(request):
    """Values every page needs: organisation name, unread alerts, theme."""
    ctx = {"cfg": SiteSettings.load()}
    user = getattr(request, "user", None)
    if user is not None and user.is_authenticated:
        unread = Notification.objects.filter(user=user, read=False)
        ctx["unread_count"] = unread.count()
        ctx["recent_alerts"] = Notification.objects.filter(user=user).select_related("ticket")[:8]
    return ctx
