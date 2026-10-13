"""Refreshers: pairing a machine, recognising it, and what the website tells it to do. See refresher/README.md.

A refresher authenticates with its own secret (shown once, stored only as a hash), so it can be revoked on its own and the
website's INGEST_TOKEN never has to be handed out.
"""
import base64
import hashlib
import hmac
import json
import secrets
from datetime import timedelta

from . import db, ingest
from refresher.version import VERSION

ONLINE_MINUTES = 5          # it checks in about once a minute
STALE_HOURS = 26            # a daily job that has not reported for this long needs a look
SCHEDULE_TZ = "Europe/London"
DEFAULT_TIME = "07:30"


def hash_token(token):
    return hashlib.sha256(token.encode()).hexdigest()


def create(database, name):
    """Add a refresher. Returns (row, connection code). The code is the only time the secret is ever visible."""
    token = secrets.token_urlsafe(32)
    row = db.Refresher(name=name, token_hash=hash_token(token))
    database.add(row)
    database.commit()
    return row, token


def connection_code(site_url, token, name):
    blob = json.dumps({"u": site_url.rstrip("/"), "t": token, "n": name}, separators=(",", ":")).encode()
    return "jbr1." + base64.urlsafe_b64encode(blob).decode().rstrip("=")


def parse_connection_code(code):
    """The inverse of connection_code, for the tool and for tests. Raises ValueError on anything that is not a valid code."""
    code = (code or "").strip()
    if not code.startswith("jbr1."):
        raise ValueError("That doesn't look like a connection code. It starts with jbr1.")
    try:
        raw = code[5:] + "=" * (-len(code[5:]) % 4)
        data = json.loads(base64.urlsafe_b64decode(raw))
        return data["u"], data["t"], data.get("n") or "Refresher"
    except Exception:
        raise ValueError("That connection code is damaged. Copy it again from Admin > Refresher.")


def by_token(database, token):
    """The refresher this secret belongs to, or None."""
    if not token:
        return None
    want = hash_token(token)
    for row in database.query(db.Refresher):
        if hmac.compare_digest(row.token_hash, want):
            return row
    return None


# ---------- the schedule (set on the website, followed by the refresher) ----------

def schedule(database):
    time_row, on_row = database.get(db.AppSetting, "refresher_time"), database.get(db.AppSetting, "refresher_enabled")
    return {"time": (time_row.value if time_row and time_row.value else DEFAULT_TIME),
            "enabled": not (on_row is not None and on_row.value == "off"), "tz": SCHEDULE_TZ}


def valid_time(text):
    try:
        hour, minute = (int(part) for part in text.strip().split(":"))
        return f"{hour:02d}:{minute:02d}" if 0 <= hour < 24 and 0 <= minute < 60 else None
    except (ValueError, AttributeError):
        return None


def save_schedule(database, time_text, enabled):
    database.merge(db.AppSetting(key="refresher_time", value=valid_time(time_text) or DEFAULT_TIME))
    database.merge(db.AppSetting(key="refresher_enabled", value="on" if enabled else "off"))
    database.commit()


# ---------- what it is told ----------

def config_for(database):
    """What a refresher needs from the website: the schedule, who to search for, and which sources are switched off."""
    people = []
    for p in ingest.profiles(database):
        people.append({"username": p.person.username, "name": p.person.name, "keywords": p.keywords or "", "locations": p.locations or "",
                       "avoid": p.avoid or "", "distance": p.distance or 25, "per_search": p.per_search or 100, "remote_uk": bool(p.remote_uk),
                       "uk_wide": bool(p.uk_wide), "needs_sponsorship": bool(p.needs_sponsorship), "enabled": bool(p.enabled)})
    return {"schedule": schedule(database), "people": people, "sources_off": sorted(ingest.disabled_sources(database))}


def check_in(database, row, report):
    """Record what a refresher reported and return what the website wants it to do. Returns (reply, run_failed_just_now)."""
    previous = (row.status or {}).get("last_run") or {}
    row.last_seen, row.version = db.utcnow(), str(report.get("version") or "")[:20]
    row.state = str(report.get("state") or "")[:12]
    row.status = {k: report.get(k) for k in ("phase", "message", "percent", "now", "last_run", "next_run_at", "host", "found", "new")}
    command, row.pending_command = row.pending_command or None, ""
    database.commit()
    last = report.get("last_run") or {}
    failed_now = bool(last.get("status") == "failed" and last.get("at") and last.get("at") != previous.get("at"))
    return {"config": config_for(database), "command": command, "expected_version": VERSION}, failed_now


# ---------- how it looks on the Admin page ----------

def health(row, now=None):
    """('online' | 'quiet' | 'offline' | 'never', words) from when it last checked in."""
    now = now or db.utcnow()
    if row.last_seen is None:
        return "never", "Waiting for its first check-in"
    age = now - row.last_seen
    if age <= timedelta(minutes=ONLINE_MINUTES):
        return "online", "Online"
    if age <= timedelta(hours=STALE_HOURS):
        return "quiet", "Not heard from for a while"
    return "offline", "Offline"


def is_stale(row, now=None):
    now = now or db.utcnow()
    return row.last_seen is None or now - row.last_seen > timedelta(hours=STALE_HOURS)
