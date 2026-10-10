"""
Encryption of the few secrets kept in the database: the mail server password and the
SMS / WhatsApp gateway headers (which carry API keys).

The key is FIELD_KEY from the secrets file. Installs without one use a key derived from
SECRET_KEY. Backups carry the key inside their encrypted part, so a restore on another
computer can still read these values.
"""
import base64
import hashlib
import logging
import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from django.conf import settings
from django.db import models

log = logging.getLogger("diagtrack.crypto")
PREFIX = "enc1:"


def _b64d(text):
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _b64e(raw):
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def field_key():
    value = os.environ.get("FIELD_KEY", "")
    if value:
        key = _b64d(value)
        if len(key) == 32:
            return key
        log.warning("FIELD_KEY is not 32 bytes; using the key derived from SECRET_KEY.")
    return hashlib.sha256(("diagtrack-field-key:" + settings.SECRET_KEY).encode("utf-8")).digest()


def field_key_text():
    return _b64e(field_key())


def new_key_text():
    return _b64e(os.urandom(32))


def encrypt(text, key=None):
    if not text:
        return text
    nonce = os.urandom(12)
    sealed = AESGCM(key or field_key()).encrypt(nonce, text.encode("utf-8"), b"diagtrack-field")
    return PREFIX + _b64e(nonce + sealed)


def decrypt(value, key=None):
    if not value or not value.startswith(PREFIX):
        return value  # stored before encryption was introduced
    raw = _b64d(value[len(PREFIX):])
    try:
        return AESGCM(key or field_key()).decrypt(raw[:12], raw[12:], b"diagtrack-field").decode("utf-8")
    except (InvalidTag, ValueError):
        log.warning("A stored secret could not be decrypted with this computer's key. Enter it again in Settings.")
        return ""


class EncryptedTextField(models.TextField):
    """Text stored encrypted, read back as plain text."""

    def from_db_value(self, value, expression, connection):
        return decrypt(value)

    def get_prep_value(self, value):
        value = super().get_prep_value(value)
        if value and not value.startswith(PREFIX):
            return encrypt(value)
        return value
