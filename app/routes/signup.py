"""Public sign-up: anyone can ask for an account, and nothing exists until an admin approves it."""
import time

from fastapi import APIRouter, Depends, Form, Request

from .. import auth, db, notify, web

router = APIRouter()
MAX_PENDING = 25       # requests waiting at once: a flood can't bury the admins
PER_IP = 5             # attempts from one address ...
WINDOW = 60 * 60       # ... per hour
MIN_PASSWORD = 10
_recent = {}           # address -> times of recent attempts (in memory: a restart simply forgets them)


def _too_many(request):
    who = request.client.host if request.client else "unknown"
    now = time.time()
    seen = [t for t in _recent.get(who, []) if now - t < WINDOW]
    seen.append(now)
    _recent[who] = seen
    if len(_recent) > 5000:  # never let the table itself grow without limit
        for key in [k for k, v in _recent.items() if not v or now - v[-1] >= WINDOW]:
            _recent.pop(key, None)
    return len(seen) > PER_IP


def _page(request, status_code=200, **ctx):
    ctx.setdefault("error", "")
    ctx.setdefault("values", {})
    ctx.setdefault("sent", False)
    return web.page(request, "signup.html", None, status_code=status_code, **ctx)


@router.get("/signup")
def signup_page(request: Request, database=Depends(auth.get_db)):
    return _page(request, closed=not auth.signups_open(database))


@router.get("/signup/sent")
def signup_sent(request: Request, database=Depends(auth.get_db)):
    return _page(request, closed=False, sent=True)


@router.post("/signup")
def signup(request: Request, database=Depends(auth.get_db), name: str = Form(""), username: str = Form(""),
           password: str = Form(""), again: str = Form(""), website: str = Form("")):
    if not auth.signups_open(database):
        return _page(request, 403, closed=True)
    if website:  # a hidden box only a bot fills in: look successful, create nothing
        return web.redirect("/signup/sent")
    if _too_many(request):
        return _page(request, 429, closed=False, error="Too many attempts from here. Please try again in an hour.")
    name, username = name.strip()[:80], username.strip().lower()[:40]
    values = {"name": name, "username": username}

    def refuse(message):
        return _page(request, 400, closed=False, error=message, values=values)

    if not name or not auth.valid_username(username):
        return refuse("Please give your name, and a username of 3 to 40 letters, numbers, dots or dashes.")
    if len(password) < MIN_PASSWORD or len(password) > 200:
        return refuse(f"Please choose a password of at least {MIN_PASSWORD} characters.")
    if password != again:
        return refuse("The two passwords didn't match.")
    if (database.query(db.Person).filter(db.Person.username == username).first() is not None
            or database.query(db.SignupRequest).filter(db.SignupRequest.username == username).first() is not None):
        return refuse(f"The username “{username}” is already taken. Please choose another.")
    if database.query(db.SignupRequest).count() >= MAX_PENDING:
        return refuse("Too many requests are waiting right now. Please try again later.")
    database.add(db.SignupRequest(name=name, username=username, password_hash=auth.hash_password(password)))
    database.commit()
    notify.new_signup(database, name)
    return web.redirect("/signup/sent")
