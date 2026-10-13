"""Browser notifications (Web Push) straight to Chrome.

Admins hear when someone signs in or opens the site, when a job is added to a Tracker, and when its status changes.
Users hear about deadlines coming up, jobs assigned to them, and if they have not been on for 3 days.

Nothing to install: a person turns notifications on once from their Profile (Admin home for admins) and their
browser registers with its own push service (Google's, for Chrome). We keep that registration and send to it.

Needs a VAPID key pair (run `python scripts/make_vapid_keys.py`) in VAPID_PUBLIC_KEY and VAPID_PRIVATE_KEY. Without
them every function here does nothing, so the rest of the app is unaffected. Messages hold only a headline and a job
title, never CV text.
"""
import base64
import json
import logging
import os
import threading
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

from . import db

log = logging.getLogger("jobbuddy")

DIGEST_HOUR = 8       # local (UK) hour the daily deadline message goes out, once
DIGEST_DAYS = 3       # deadlines this many days away (or fewer) are included
INACTIVE_DAYS = 3     # a user not seen for this long gets a nudge, then again every this-many days
PUSH_HOSTS = ("fcm.googleapis.com", "push.services.mozilla.com", "notify.windows.com", "push.apple.com")  # Chrome, Firefox, Edge, Safari


def public_key():
    return os.getenv("VAPID_PUBLIC_KEY", "").strip()


def configured():
    return bool(public_key() and os.getenv("VAPID_PRIVATE_KEY", "").strip())


def _b64(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def generate_keys():
    """A new VAPID pair as (public, private) strings ready for the environment."""
    from cryptography.hazmat.primitives import serialization as ser
    from py_vapid import Vapid
    v = Vapid()
    v.generate_keys()
    private = v.private_key.private_bytes(ser.Encoding.DER, ser.PrivateFormat.PKCS8, ser.NoEncryption())
    public = v.public_key.public_bytes(ser.Encoding.X962, ser.PublicFormat.UncompressedPoint)
    return _b64(public), _b64(private)


def allowed_endpoint(url):
    """Only real browser push services: a signed-in person must not be able to aim our server at any address."""
    parts = urlparse(url or "")
    host = parts.hostname or ""
    return parts.scheme == "https" and len(url) <= 1000 and any(host == h or host.endswith("." + h) for h in PUSH_HOSTS)


# ---------- sending ----------

def _post(sub, payload):
    """One call to the push service. Kept separate so tests can replace it."""
    from pywebpush import webpush
    webpush(subscription_info={"endpoint": sub.endpoint, "keys": {"p256dh": sub.p256dh, "auth": sub.auth}},
            data=json.dumps(payload), vapid_private_key=os.environ["VAPID_PRIVATE_KEY"].strip(),
            vapid_claims={"sub": os.getenv("VAPID_SUBJECT", "mailto:admin@jobbuddy.invalid")}, ttl=60 * 60 * 24)


def deliver(person_id, payload):
    """Send to every browser the person has registered. Returns how many accepted it."""
    from pywebpush import WebPushException
    sent = 0
    with db.SessionLocal() as s:
        for sub in s.query(db.PushSubscription).filter(db.PushSubscription.person_id == person_id).all():
            try:
                _post(sub, payload)
                sent += 1
            except WebPushException as e:
                status = getattr(getattr(e, "response", None), "status_code", None)
                if status in (404, 410):  # the browser unregistered: forget it
                    s.delete(sub)
                else:
                    log.warning("Push failed (status %s)", status)
            except Exception as e:
                log.warning("Push failed (%s)", type(e).__name__)
        s.commit()
    return sent


def _spawn(fn, *args):
    threading.Thread(target=fn, args=args, daemon=True).start()


def notify(person_id, title, body, url="/", tag=""):
    """Tell a person, without making the page they are on wait. Never raises."""
    if not configured():
        return
    payload = {"title": title[:80], "body": body[:200], "url": url, "tag": tag}
    try:
        _spawn(deliver, person_id, payload)
    except Exception as e:
        log.warning("Push not started (%s)", type(e).__name__)


# ---------- telling the admins ----------

def _never_raises(fn):
    """These run inside ordinary page requests: a notification problem must never break the page."""
    def wrapper(*args, **kwargs):
        if not configured():
            return
        try:
            fn(*args, **kwargs)
        except Exception as e:
            log.warning("Notification skipped (%s)", type(e).__name__)
    wrapper.__name__, wrapper.__doc__ = fn.__name__, fn.__doc__
    return wrapper


@_never_raises
def admins(database, actor, title, body="", url="/admin", tag=""):
    """Tell every admin except the person who did it."""
    for admin in database.query(db.Person).filter(db.Person.role == "admin", db.Person.id != actor.id):
        notify(admin.id, title, body, url, tag)


@_never_raises
def new_signup(database, name):
    """Someone asked for an account on the public page: tell every admin."""
    for admin in database.query(db.Person).filter(db.Person.role == "admin"):
        notify(admin.id, "New sign-up request", f"{name} would like an account.", "/admin", tag="signup")


@_never_raises
def visit(database, person, what):
    """Someone is on the site: signed in with their password, or came back with their cookie after a break."""
    url = "/admin" if person.is_admin else f"/admin/users/{person.id}/tracker"
    admins(database, person, f"{person.first_name} {what}", "" if person.is_admin else "Tap to open their Tracker.", url, tag=f"visit-{person.id}")


@_never_raises
def tracker_change(database, actor, entry, what):
    """A job was added to someone's Tracker, or its status changed. Same tag per job, so quick changes replace each other."""
    job = db.entry_job(entry)
    admins(database, actor, f"{entry.person.first_name} {what}", f"{job.title} · {job.company}",
           f"/admin/users/{entry.person_id}/entry/{entry.id}", tag=f"entry-{entry.id}")


# ---------- the daily deadline message ----------

def _plural(n, word):
    return f"{n} {word}" + ("" if n == 1 else "s")


def digest_for(database, person, today):
    """(title, body) for a person's deadlines in the next few days, or None. Only jobs they have not applied for."""
    from . import summary
    due = [v for v in summary.entries_for(database, person, today)
           if v["entry"].status == "shortlisted" and v["days_left"] is not None and 0 <= v["days_left"] <= DIGEST_DAYS]
    if not due:
        return None
    lines = [f"{v['job'].title} · {v['job'].company}: {v['chip']['text'].lower()}" for v in due[:3]]
    if len(due) > 3:
        lines.append(f"and {len(due) - 3} more")
    return f"{_plural(len(due), 'deadline')} coming up", "\n".join(lines)


def inactivity_for(person, today):
    """(title, body) if a user has been away 3 days, 6 days, 9 days and so on, else None."""
    last = person.last_login or person.created_at
    days = (today - last.date()).days if last else 0
    if days >= INACTIVE_DAYS and days % INACTIVE_DAYS == 0:
        return f"You haven't been on Job Buddy for {days} days", "Have a look at your Tracker and the Job Board."
    return None


def refresher_alerts(database):
    """Tell the admins when a refresher that should be refreshing the Board every day has gone quiet: after a day, then every 3 days."""
    from . import refreshers
    sent = 0
    if not refreshers.schedule(database)["enabled"]:
        return 0
    now = db.utcnow()
    for row in database.query(db.Refresher):
        if not refreshers.is_stale(row, now):
            continue
        days = (now - (row.last_seen or row.created_at)).days
        if days >= 1 and (days == 1 or days % 3 == 0):
            for admin in database.query(db.Person).filter(db.Person.role == "admin"):
                notify(admin.id, f"{row.name} has gone quiet", f"It hasn't reported for {days} day{'' if days == 1 else 's'}, so the Job Board isn't being refreshed.",
                       "/admin/refresher", tag=f"refresher-stale-{row.id}")
            sent += 1
    return sent


def send_digest(database, today):
    sent = refresher_alerts(database)
    for person in database.query(db.Person).filter(db.Person.role == "user"):
        message = digest_for(database, person, today)
        if message:
            notify(person.id, message[0], message[1], "/tracker", tag="deadlines")
            sent += 1
        away = inactivity_for(person, today)
        if away:
            notify(person.id, away[0], away[1], "/", tag="inactive")
            sent += 1
    return sent


def uk_now():
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("Europe/London"))
    except Exception:
        return datetime.now(timezone.utc)


def run_daily_once(now=None):
    """Send the deadline message if it is past 8am here and it has not gone out today. True if it went."""
    now = now or uk_now()
    if now.hour < DIGEST_HOUR:
        return False
    with db.SessionLocal() as s:
        row = s.get(db.AppSetting, "last_digest")
        if row is not None and row.value == now.date().isoformat():
            return False
        s.merge(db.AppSetting(key="last_digest", value=now.date().isoformat()))
        s.commit()  # mark it first, so a restart halfway through can never send twice
        send_digest(s, now.date())
    return True


_started = False


def _loop():
    while True:
        try:
            run_daily_once()
        except Exception as e:
            log.warning("Daily notifications failed (%s)", type(e).__name__)
        time.sleep(600)


def start_scheduler():
    """One background thread that checks every ten minutes. Needs the service to stay awake (a paid Render plan)."""
    global _started
    if _started or not configured():
        return
    _started = True
    threading.Thread(target=_loop, daemon=True, name="notify-daily").start()
