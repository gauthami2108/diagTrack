"""
DiagTrack settings.

Everything a maintainer may need to change is read from the .env file
(see .env.example in the project folder). Nothing here needs editing.
"""
import os
from pathlib import Path

from . import runtime

BASE_DIR = Path(__file__).resolve().parent.parent
runtime.load()  # settings file, secrets file, /run/secrets (see config/runtime.py)


def env(name, default=""):
    return os.environ.get(name, default)


def env_bool(name, default=False):
    return env(name, "1" if default else "0").strip().lower() in ("1", "true", "yes", "on")


def env_list(name):
    return [v.strip() for v in env(name).split(",") if v.strip()]


SECRET_KEY = env("SECRET_KEY", "dev-only-change-me")
DEBUG = env_bool("DEBUG", False)
ALLOWED_HOSTS = env_list("ALLOWED_HOSTS") or ["*"]
CSRF_TRUSTED_ORIGINS = env_list("CSRF_TRUSTED_ORIGINS")
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
USE_X_FORWARDED_HOST = True

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.humanize",
    "tickets",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "tickets.middleware.MaintenanceMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "tickets.middleware.ReadOnlyMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "tickets.context.shell",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

# Database: PostgreSQL when DB_HOST is set (Docker), SQLite otherwise (tests).
if env("DB_HOST"):
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "HOST": env("DB_HOST"),
            "PORT": env("DB_PORT", "5432"),
            "NAME": env("DB_NAME", "diagtrack"),
            "USER": env("DB_USER", "diagtrack"),
            "PASSWORD": env("DB_PASSWORD"),
            "CONN_MAX_AGE": 60,
            "CONN_HEALTH_CHECKS": True,  # reconnect quietly after a restore or a database restart
        }
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": Path(env("SQLITE_PATH", str(BASE_DIR / "db.sqlite3"))),
        }
    }

AUTH_USER_MODEL = "tickets.User"
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
     "OPTIONS": {"min_length": 8}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
]
LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "home"
# Sign-in lock-out (tickets/signin.py): wrong passwords per user name and per computer, and the pause.
LOGIN_MAX_FAILURES = int(env("LOGIN_MAX_FAILURES", "5"))
LOGIN_MAX_FAILURES_PER_ADDRESS = int(env("LOGIN_MAX_FAILURES_PER_ADDRESS", "20"))
LOGIN_LOCK_MINUTES = int(env("LOGIN_LOCK_MINUTES", "15"))
# Docker: the proxy in front of DiagTrack reports the real computer in X-Forwarded-For. Never set on Windows.
TRUST_X_FORWARDED_FOR = env("TRUST_X_FORWARDED_FOR", "0") == "1"
LOGOUT_REDIRECT_URL = "login"

# Sessions end after 8 hours without activity.
SESSION_COOKIE_AGE = 8 * 60 * 60
SESSION_SAVE_EVERY_REQUEST = True
SESSION_COOKIE_HTTPONLY = True

LANGUAGE_CODE = "en-in"
TIME_ZONE = env("TIME_ZONE", "Asia/Kolkata")
USE_I18N = False
USE_TZ = True

STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = Path(env("STATIC_ROOT", str(BASE_DIR / "staticfiles")))
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"
        if DEBUG else "whitenoise.storage.CompressedManifestStaticFilesStorage"
    },
}

# Attachments live on local disk. They are served only to logged-in users
# who are allowed to see the ticket (see tickets.views.attachment).
MEDIA_ROOT = Path(env("MEDIA_ROOT", str(BASE_DIR / "media")))
# Backup state, job progress, maintenance and read-only flags. Shared by every DiagTrack process.
STATUS_DIR = Path(env("STATUS_DIR", str(BASE_DIR / "status")))
# Where pg_dump and pg_restore live (native Windows: C:\Program Files\PostgreSQL\17\bin). Empty = on PATH.
PG_BIN = env("PG_BIN")
# Software version, recorded in every backup.
DIAGTRACK_VERSION = (BASE_DIR / "VERSION").read_text().strip() if (BASE_DIR / "VERSION").exists() else "dev"

DATA_UPLOAD_MAX_MEMORY_SIZE = 60 * 1024 * 1024
FILE_UPLOAD_MAX_MEMORY_SIZE = 5 * 1024 * 1024

# Email goes through the SMTP details saved on the Settings screen.
EMAIL_BACKEND = "tickets.mail.DbEmailBackend"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"plain": {"format": "%(asctime)s %(levelname)s %(name)s: %(message)s"}},
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "plain"}},
    "root": {"handlers": ["console"], "level": "INFO"},
}
