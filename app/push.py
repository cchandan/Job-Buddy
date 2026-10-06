"""Publish a laptop refresh to the online site over HTTPS, sending only what the site does not already have.

Set INGEST_URL (the live site) and INGEST_TOKEN (the same secret as on the live site) in the laptop's .env. If they are not
set, the laptop is writing straight to the live database (DATABASE_URL) and there is nothing to push.
"""
import os
import time
from datetime import timedelta

import httpx

from . import db

BATCH = 25  # small requests: a free Render instance has little memory and a short patience


def configured():
    return bool(os.getenv("INGEST_URL") and os.getenv("INGEST_TOKEN"))


class PushError(RuntimeError):
    """A failure to publish, with a message already written for the admin."""


WAITS = (5, 15, 30, 60)  # seconds between tries when the site is down or still waking up


def wake():
    """A free Render service sleeps when idle and answers 502 until it is awake: ask it to wake and wait up to ~2 minutes."""
    url = os.environ["INGEST_URL"].rstrip("/") + "/health"
    for wait in (0,) + WAITS:
        time.sleep(wait)
        try:
            if httpx.get(url, timeout=60).status_code == 200:
                return True
        except httpx.TransportError:
            pass
    return False


def _post(path, payload):
    url = os.environ["INGEST_URL"].rstrip("/") + "/api/ingest/" + path
    headers = {"Authorization": "Bearer " + os.environ["INGEST_TOKEN"]}
    for attempt, wait in enumerate((0,) + WAITS):
        time.sleep(wait)
        try:
            r = httpx.post(url, json=payload, headers=headers, timeout=120)
        except httpx.TransportError as e:
            if attempt == len(WAITS):
                raise PushError(f"could not reach the live site ({type(e).__name__})")
            continue
        if r.status_code in (401, 403):
            raise PermissionError("the live site refused the token: check INGEST_TOKEN matches on Render and in .env")
        if r.status_code in (429, 502, 503, 504) and attempt < len(WAITS):
            continue  # down or waking up: try again
        if r.status_code >= 400:
            raise PushError(f"the live site answered {r.status_code} to /{path}: {r.text[:150]!r}")
        return r.json()


def signature(content_hash, matched_usernames):
    """What the manifest compares: the advert's version plus who it is for, so a new match is sent even if the advert is unchanged."""
    return f"{content_hash or ''}|{','.join(sorted(matched_usernames))}"


def job_dict(job, usernames=None):
    """A job as plain JSON, using the same fields the Job table holds. `matched` travels as usernames, because the
    numeric ids of people differ between this laptop's database and the live site's."""
    out = {f: getattr(job, f) for f in db.JOB_FIELDS}
    out["matched"] = sorted(usernames[i] for i in (job.matched or []) if usernames and i in usernames)
    out["deadline"] = job.deadline.isoformat() if job.deadline else None
    return out


def publish(database, run):
    """manifest -> only the rows the site lacks -> who is still listed -> who closed. Returns counts."""
    if not wake():
        raise PushError("the live site did not wake up (still answering errors after about 2 minutes)")
    jobs = database.query(db.Job).all()
    usernames = {p.id: p.username for p in database.query(db.Person)}
    need = set(_post("manifest", {"hashes": {j.id: signature(j.content_hash, job_dict(j, usernames)["matched"]) for j in jobs}})["need"])
    by_id = {j.id: j for j in jobs}
    sent = 0
    ordered = [by_id[i] for i in need if i in by_id]
    for i in range(0, len(ordered), BATCH):
        _post("jobs", {"jobs": [job_dict(j, usernames) for j in ordered[i:i + BATCH]]})
        sent += len(ordered[i:i + BATCH])
    recent = db.utcnow() - timedelta(hours=6)
    seen = [j.id for j in jobs if j.id not in need and j.last_seen and j.last_seen >= recent and not j.closed_at]
    for i in range(0, len(seen), 500):
        _post("seen", {"ids": seen[i:i + 500]})
    closed = [{"id": j.id, "reason": j.close_reason or "", "at": j.closed_at.isoformat()} for j in jobs if j.closed_at]
    if closed:
        _post("closed", {"items": closed})
    run.event("push", f"Published to the online site: {sent} new or changed jobs sent, {len(seen)} marked still listed, "
              f"{len(closed)} closed. Nothing else was sent.")
    return {"sent": sent, "seen": len(seen), "closed": len(closed)}


def send_log(run_id):
    with db.SessionLocal() as s:
        run = s.get(db.RunLog, run_id)
        events = s.query(db.RunEvent).filter_by(run_id=run_id).order_by(db.RunEvent.id).all()
        payload = {"status": run.status, "started": run.started.isoformat(), "summary": run.summary or {},
                   "events": [{"at": e.at.isoformat(), "level": e.level, "stage": e.stage, "source": e.source,
                               "message": e.message, "data": e.data} for e in events]}
    _post("log", payload)
