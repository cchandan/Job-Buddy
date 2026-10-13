"""A reversible copy of the passwords an admin hands out, so an admin can look one up again.

The sign-in hash in `Person.password_hash` is still the only thing used to check a password. This copy is
encrypted with a key from PASSWORD_VAULT_KEY (or, failing that, SESSION_SECRET) and is only ever read by the
admin "View passwords" page, after the admin types their own password again. It is dropped as soon as the
person chooses their own password, so only passwords the app generated for them can be looked up.
"""
import base64
import hashlib
import os

from cryptography.fernet import Fernet, InvalidToken


def _fernet():
    secret = os.getenv("PASSWORD_VAULT_KEY") or os.getenv("SESSION_SECRET")
    if not secret:
        return None  # no stable key, so nothing could be read back after a restart: store nothing
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(("vault:" + secret).encode()).digest()))


def seal(password):
    f = _fernet()
    return f.encrypt(password.encode()).decode() if f else None


def unseal(token):
    """The password, or None if there is no copy or it can't be read with the current key."""
    f = _fernet()
    if not f or not token:
        return None
    try:
        return f.decrypt(token.encode()).decode()
    except (InvalidToken, ValueError):
        return None
