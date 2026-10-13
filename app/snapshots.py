"""Snapshots: a saved copy of the whole Job Board that an admin can take by hand and restore later.

A snapshot holds every column of every job (including what is in the bin) and nothing else: no Tracker, no CV, no account.
Restoring never deletes a job and never touches a Tracker. It puts every job in the snapshot back exactly as it was, and any job
that was added AFTER the snapshot goes to the bin (so a restore can itself be undone). A job someone is tracking is left on the
Board. Just before a restore, an automatic snapshot of the current Board is taken, so a restore can always be undone.
"""
import gzip
import json
from datetime import date, datetime

from sqlalchemy import Date, DateTime, func
from sqlalchemy.orm import defer

from . import db, jobbin

MAX_SNAPSHOTS = 10
COLUMNS = list(db.Job.__table__.columns)


class SnapshotError(Exception):
    """Something an admin should be told in plain words."""


def _dump(job):
    out = {}
    for c in COLUMNS:
        value = getattr(job, c.name)
        out[c.name] = value.isoformat() if isinstance(value, (date, datetime)) else value
    return out


def _load(row):
    out = {}
    for c in COLUMNS:
        value = row.get(c.name)
        if value is not None and isinstance(c.type, DateTime):
            value = datetime.fromisoformat(value)
        elif value is not None and isinstance(c.type, Date):
            value = date.fromisoformat(value)
        out[c.name] = value
    return out


def encode(database):
    jobs = [_dump(j) for j in database.query(db.Job)]
    blob = gzip.compress(json.dumps({"version": 1, "taken": db.utcnow().isoformat(), "jobs": jobs}).encode(), 6)
    return blob, len(jobs)


def decode(blob):
    return json.loads(gzip.decompress(blob).decode())["jobs"]


def count(database):
    return database.query(func.count(db.BoardSnapshot.id)).scalar()


def _make_room(database, kind):
    """Keep at most MAX_SNAPSHOTS. The oldest automatic copy goes first; a manual copy is only ever deleted by an admin."""
    while count(database) >= MAX_SNAPSHOTS:
        oldest_auto = (database.query(db.BoardSnapshot).filter(db.BoardSnapshot.kind == "auto")
                       .order_by(db.BoardSnapshot.created_at, db.BoardSnapshot.id).first())
        if oldest_auto is None:
            raise SnapshotError(f"There are already {MAX_SNAPSHOTS} snapshots. Delete one you no longer need, then try again.")
        database.delete(oldest_auto)
        database.flush()


def take(database, name="", note="", by=None, kind="manual"):
    """Save the Board as it is now. Returns the snapshot."""
    _make_room(database, kind)
    blob, n = encode(database)
    now = db.utcnow()
    snap = db.BoardSnapshot(name=(name or "").strip()[:120] or f"Snapshot {now:%d %b %Y %H:%M}", note=(note or "").strip()[:300], kind=kind,
                            created_by_id=by.id if by else None, job_count=n, size_bytes=len(blob), data=blob, created_at=now)
    database.add(snap)
    database.commit()
    return snap


def restore(database, snapshot_id, by=None):
    """Put the Board back as the snapshot had it. Returns a summary of what happened."""
    snap = database.get(db.BoardSnapshot, snapshot_id)
    if snap is None:
        raise SnapshotError("That snapshot no longer exists.")
    rows = {r["id"]: _load(r) for r in decode(snap.data)}
    safety = take(database, f"Before restoring: {snap.name}", "Taken automatically so this restore can be undone.", by, kind="auto")
    now, tracked = db.utcnow(), jobbin.tracked_ids(database)
    summary = {"updated": 0, "brought_back": 0, "binned": 0, "kept": 0, "safety": safety.name}
    existing = {j.id: j for j in database.query(db.Job)}
    for job_id, row in rows.items():
        job = existing.get(job_id)
        if job is None:
            database.add(db.Job(**row))
            summary["brought_back"] += 1
        else:
            for field, value in row.items():
                if field != "id":
                    setattr(job, field, value)
            summary["updated"] += 1
    for job_id, job in existing.items():
        if job_id in rows:
            continue
        if job_id in tracked:
            summary["kept"] += 1  # someone is tracking it: it stays on the Board
        elif job.binned_at is None:
            job.binned_at, job.bin_reason, job.bin_kept = now, f"Added after the snapshot “{snap.name}”"[:200], False
            summary["binned"] += 1
    database.commit()
    return summary


def delete(database, snapshot_id):
    snap = database.get(db.BoardSnapshot, snapshot_id)
    if snap is None:
        raise SnapshotError("That snapshot no longer exists.")
    database.delete(snap)
    database.commit()


def listing(database):
    """Newest first, without the heavy data."""
    return (database.query(db.BoardSnapshot).options(defer(db.BoardSnapshot.data))
            .order_by(db.BoardSnapshot.created_at.desc(), db.BoardSnapshot.id.desc()).all())
