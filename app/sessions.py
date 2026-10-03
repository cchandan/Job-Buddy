"""Anonymous session cookie, "delete my data". No login."""
import secrets

from . import db

COOKIE = "careeros_session"
MAX_AGE = 7 * 24 * 3600  # matches the 7-day data lifetime


def new_session_id():
    return secrets.token_urlsafe(32)  # unguessable, so no signing key is needed


def get_session_id(request):
    sid = request.cookies.get(COOKIE, "")
    return sid if 20 <= len(sid) <= 64 else ""


def get_visitor(database, request):
    """The visitor row for this browser, or None if there isn't one yet."""
    sid = get_session_id(request)
    return database.get(db.Visitor, sid) if sid else None


def set_cookie(response, request, sid):
    secure = request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"
    response.set_cookie(COOKIE, sid, max_age=MAX_AGE, httponly=True, secure=secure, samesite="lax")


def delete_visitor(database, request, response):
    sid = get_session_id(request)
    if sid:
        database.query(db.Visitor).filter(db.Visitor.session_id == sid).delete()
        database.commit()
    response.delete_cookie(COOKIE)
