"""Passwords, sign-in, lockout, the session, and the three guards every route uses.

Accounts are created only by an admin. The app generates each password, shows it once, and stores only a
salted scrypt hash. Passwords are never logged.
"""
import hashlib
import hmac
import secrets
from datetime import timedelta

from fastapi import Depends, HTTPException, Request

from . import db

MAX_FAILURES = 5
LOCK_MINUTES = 10
_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"  # no look-alikes (0/o, 1/l/i)
_N, _R, _P = 2 ** 14, 8, 1


class NeedLogin(Exception):
    """Raised when a page needs a signed-in person; main.py turns it into a redirect to /login."""


# ---------- passwords ----------

def new_password():
    """A random password, readable aloud: four groups of four."""
    return "-".join("".join(secrets.choice(_ALPHABET) for _ in range(4)) for _ in range(4))


def hash_password(password):
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P)
    return f"scrypt${salt.hex()}${digest.hex()}"


def check_password(password, stored):
    try:
        _, salt, digest = stored.split("$")
        got = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=_N, r=_R, p=_P)
    except (ValueError, AttributeError):
        return False
    return hmac.compare_digest(got.hex(), digest)


_DUMMY_HASH = hash_password(secrets.token_hex(8))  # so a missing username takes as long as a real one


def valid_username(username):
    return 3 <= len(username) <= 40 and all(c.isalnum() or c in "._-" for c in username)


# ---------- sign-in ----------

def sign_in(database, username, password):
    """Returns (person, None) or (None, reason) where reason is "wrong" or "locked"."""
    person = database.query(db.Person).filter(db.Person.username == username.strip().lower()).first()
    now = db.utcnow()
    if person is None:
        check_password(password, _DUMMY_HASH)
        return None, "wrong"
    if person.locked_until and person.locked_until > now:
        return None, "locked"
    if not check_password(password, person.password_hash):
        person.failed_count = (person.failed_count or 0) + 1
        if person.failed_count >= MAX_FAILURES:
            person.locked_until = now + timedelta(minutes=LOCK_MINUTES)
            person.failed_count = 0
        database.commit()
        return None, "locked" if person.locked_until and person.locked_until > now else "wrong"
    person.failed_count, person.locked_until, person.last_login = 0, None, now
    database.commit()
    return person, None


def set_password(person, password):
    """Replace the password and sign the person out everywhere."""
    person.password_hash = hash_password(password)
    person.session_version = (person.session_version or 1) + 1
    person.failed_count, person.locked_until = 0, None


def start_session(request, person):
    csrf = request.session.get("csrf")
    request.session.clear()
    request.session.update({"pid": person.id, "sv": person.session_version, "csrf": csrf or secrets.token_urlsafe(24)})


# ---------- CSRF ----------

def csrf_token(request):
    if "csrf" not in request.session:
        request.session["csrf"] = secrets.token_urlsafe(24)
    return request.session["csrf"]


async def check_csrf(request: Request):
    """Every form that changes data is a POST carrying this token."""
    if request.method != "POST" or request.url.path.startswith("/api/ingest/"):
        return  # the laptop's API has no cookies to forge: it needs the secret token instead
    form = await request.form()
    sent, want = str(form.get("csrf") or ""), request.session.get("csrf") or ""
    if not want or not hmac.compare_digest(sent, want):
        raise HTTPException(status_code=403, detail="This form has expired. Please go back, reload the page and try again.")


# ---------- the three guards ----------

def get_db():
    with db.SessionLocal() as s:
        yield s


def current_person(request: Request, database=Depends(get_db)):
    """Who is signed in, or a redirect to sign-in."""
    pid = request.session.get("pid")
    person = database.get(db.Person, pid) if pid else None
    if person is None or person.session_version != request.session.get("sv"):
        request.session.pop("pid", None)
        raise NeedLogin()
    return person


def require_admin(person=Depends(current_person)):
    if not person.is_admin:
        raise HTTPException(status_code=403, detail="That page is for admins only.")
    return person


def target_user(database, person, user_id=None):
    """Whose data this request is about. A user only ever targets themselves; an admin may target any user."""
    if user_id is None or user_id == person.id:
        return person
    if not person.is_admin:
        raise HTTPException(status_code=403, detail="You can only see your own pages.")
    user = database.get(db.Person, user_id)
    if user is None or user.is_admin:
        raise HTTPException(status_code=404, detail="We couldn't find that person.")
    return user


def own_entry(database, person, entry_id, allow_admin=False):
    """A Tracker entry the person may touch: their own, or (read-only routes) any user's for an admin."""
    entry = database.get(db.TrackerEntry, entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="We couldn't find that job in the Tracker.")
    if entry.person_id != person.id and not (allow_admin and person.is_admin):
        raise HTTPException(status_code=403, detail="You can only see your own pages.")
    return entry
