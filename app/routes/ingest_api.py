"""The laptop's door into the live site. Token only: no cookies, no forms, and it can only add or update jobs.

It never deletes anything and never touches a Tracker entry, a status, a hand-set deadline or a CV.
"""
import hmac
import os
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request

from .. import auth, db, ingest, push

router = APIRouter(prefix="/api/ingest")


def require_token(request: Request):
    want = os.getenv("INGEST_TOKEN") or ""
    sent = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
    if len(want) < 20 or not hmac.compare_digest(sent, want):
        raise HTTPException(status_code=401, detail="Not allowed.")


router.dependencies = [Depends(require_token)]


@router.post("/manifest")
async def manifest(request: Request, database=Depends(auth.get_db)):
    """Which of the laptop's jobs does the site lack, or hold in a different version?"""
    hashes = (await request.json()).get("hashes") or {}
    names = {p.id: p.username for p in database.query(db.Person)}
    have = {j.id: push.signature(j.content_hash, [names[i] for i in (j.matched or []) if i in names])
            for j in database.query(db.Job.id, db.Job.content_hash, db.Job.matched)}
    return {"need": [i for i, h in hashes.items() if i not in have or have[i] != h]}


@router.post("/jobs")
async def jobs(request: Request, database=Depends(auth.get_db)):
    rows = (await request.json()).get("jobs") or []
    now, counts = db.utcnow(), {"new": 0, "updated": 0}
    ids = {p.username: p.id for p in database.query(db.Person)}
    for row in rows[:200]:
        if not row.get("id") or not row.get("title"):
            continue
        row["matched"] = [ids[u] for u in row.get("matched") or [] if u in ids]  # usernames -> this site's ids
        counts[ingest._upsert(database, row, {}, now)] += 1
    database.commit()
    database.add(db.SyncRun(person_id=None, new=counts["new"], updated=counts["updated"], unchanged=0, failed=0))
    database.commit()
    return counts


@router.post("/seen")
async def seen(request: Request, database=Depends(auth.get_db)):
    ids = ((await request.json()).get("ids") or [])[:1000]
    database.query(db.Job).filter(db.Job.id.in_(ids)).update(
        {"last_seen": db.utcnow(), "closed_at": None, "close_reason": ""}, synchronize_session=False)
    database.commit()
    return {"ok": len(ids)}


@router.post("/closed")
async def closed(request: Request, database=Depends(auth.get_db)):
    n = 0
    for item in ((await request.json()).get("items") or [])[:1000]:
        job = database.get(db.Job, str(item.get("id")))
        if job is not None and job.closed_at is None:
            job.closed_at, job.close_reason = db.utcnow(), str(item.get("reason") or "")[:60]
            n += 1
    database.commit()
    return {"closed": n}


@router.post("/log")
async def log(request: Request, database=Depends(auth.get_db)):
    body = await request.json()
    run = db.RunLog(status=str(body.get("status") or "complete")[:12], summary=body.get("summary") or {},
                    started=datetime.fromisoformat(body["started"]) if body.get("started") else db.utcnow(), finished=db.utcnow())
    database.add(run)
    database.flush()
    for e in (body.get("events") or [])[:500]:
        database.add(db.RunEvent(run_id=run.id, level=str(e.get("level") or "info")[:6], stage=str(e.get("stage") or "")[:12],
                                 source=str(e.get("source") or "")[:30], message=str(e.get("message") or "")[:500],
                                 data=e.get("data"),
                                 at=datetime.fromisoformat(e["at"]) if e.get("at") else db.utcnow()))
    database.commit()
    return {"ok": True}
