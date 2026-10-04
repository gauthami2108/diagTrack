"""Email backend that reads the mail server details saved on the Settings screen."""
from django.core.mail.backends.smtp import EmailBackend


class DbEmailBackend(EmailBackend):
    def __init__(self, **kwargs):
        from .models import SiteSettings
        s = SiteSettings.load()
        kwargs.update(
            host=s.smtp_host or "localhost",
            port=s.smtp_port,
            username=s.smtp_user,
            password=s.smtp_password,
            use_tls=s.smtp_security == "tls",
            use_ssl=s.smtp_security == "ssl",
            timeout=20,
        )
        super().__init__(**kwargs)
