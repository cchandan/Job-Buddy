"""The story of one refresh, for Admin > Jobs sync. Never holds advert text, keys or passwords."""
from datetime import timedelta

from . import db

KEEP_RUNS = 30


class Run:
    """Write events as the refresh goes. Each write is its own short transaction, so a crash loses nothing."""

    def __init__(self, hook=None):
        self.hook = hook  # called with (level, message) for warnings and errors, so the live panel can show them
        with db.SessionLocal() as s:
            row = db.RunLog(status="running", summary={})
            s.add(row)
            s.commit()
            self.id = row.id
        self.worst = "info"
        self.summary = {}

    def event(self, stage, message, level="info", source="", **data):
        if self.hook and level != "info":
            self.hook(level, message)
        if level == "error" or (level == "warn" and self.worst != "error"):
            self.worst = level
        with db.SessionLocal() as s:
            s.add(db.RunEvent(run_id=self.id, level=level, stage=stage, source=source, message=message[:500],
                              data=data or None))
            s.commit()

    def finish(self, status=None, **summary):
        self.summary.update(summary)
        status = status or ("partial" if self.worst != "info" else "complete")
        with db.SessionLocal() as s:
            row = s.get(db.RunLog, self.id)
            row.status, row.finished, row.summary = status, db.utcnow(), dict(self.summary)
            s.commit()
            old = [r.id for r in s.query(db.RunLog).order_by(db.RunLog.id.desc()).offset(KEEP_RUNS)]
            if old:
                s.query(db.RunEvent).filter(db.RunEvent.run_id.in_(old)).delete(synchronize_session=False)
                s.query(db.RunLog).filter(db.RunLog.id.in_(old)).delete(synchronize_session=False)
                s.commit()
        return status


def recent(database, limit=5):
    """[(RunLog, [RunEvent...])] newest first."""
    out = []
    for run in database.query(db.RunLog).order_by(db.RunLog.id.desc()).limit(limit):
        events = database.query(db.RunEvent).filter_by(run_id=run.id).order_by(db.RunEvent.id).all()
        out.append((run, events))
    return out


def attention(database, today):
    """Plain-English things the admin should know about, from the latest runs."""
    notes = []
    runs = database.query(db.RunLog).order_by(db.RunLog.id.desc()).limit(3).all()
    if not runs:
        return notes
    last = runs[0]
    if last.finished and (db.utcnow() - last.finished) > timedelta(days=3):
        notes.append("The last refresh was more than 3 days ago.")
    bad = {}
    for run in runs:
        for name, info in ((run.summary or {}).get("sources") or {}).items():
            if info.get("failed"):
                bad[name] = bad.get(name, 0) + 1
    for name, count in bad.items():
        if count >= 2:
            notes.append(f"{name} has failed in {count} of the last {len(runs)} refreshes.")
    if (last.summary or {}).get("keyword_only"):
        notes.append(f"{last.summary['keyword_only']} jobs were tagged by keywords only; they will be upgraded when AI is available.")
    return notes
