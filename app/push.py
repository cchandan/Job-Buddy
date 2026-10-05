"""Publish a laptop refresh to the online site over HTTPS, sending only what the site does not already have.

Set INGEST_URL (the live site) and INGEST_TOKEN (the same secret as on the live site) in the laptop's .env. If they are not
set, the laptop is writing straight to the live database (DATABASE_URL) and there is nothing to push.
"""
import os
import time
from datetime import timedelta

import httpx

from . import db

BATCH = 100


def configured():
    return bool(os.getenv("INGEST_URL") and os.getenv("INGEST_TOKEN"))


def _post(path, payload):
    url = os.environ["INGEST_URL"].rstrip("/") + "/api/ingest/" + path
    headers = {"Authorization": "Bearer " + os.environ["INGEST_TOKEN"]}
    for attempt in range(3):
        try:
            r = httpx.post(url, json=payload, headers=headers, timeout=60)
            if r.status_code in (401, 403):
                raise PermissionError("the live site refused the token")
            r.raise_for_status()
            return r.json()
        except (httpx.TransportError, httpx.HTTPStatusError):
            if attempt == 2:
                raise
            time.sleep(2 ** attempt * 2)


def job_dict(job):
    """A job as plain JSON, using the same fields the Job table holds."""
    out = {f: getattr(job, f) for f in db.JOB_FIELDS}
    out["deadline"] = job.deadline.isoformat() if job.deadline else None
    return out


def publish(database, run):
    """manifest -> only the rows the site lacks -> who is still listed -> who closed. Returns counts."""
    jobs = database.query(db.Job).all()
    need = set(_post("manifest", {"hashes": {j.id: j.content_hash or "" for j in jobs}})["need"])
    by_id = {j.id: j for j in jobs}
    sent = 0
    ordered = [by_id[i] for i in need if i in by_id]
    for i in range(0, len(ordered), BATCH):
        _post("jobs", {"jobs": [job_dict(j) for j in ordered[i:i + BATCH]]})
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
