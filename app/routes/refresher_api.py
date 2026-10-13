"""Where a refresher checks in. Token only: no cookies, no forms. It reports how it is doing and is told what to do next."""
from fastapi import APIRouter, Depends, HTTPException, Request

from .. import auth, db, notify, refreshers

router = APIRouter(prefix="/api/refresher")


def current_refresher(request: Request, database=Depends(auth.get_db)):
    sent = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
    row = refreshers.by_token(database, sent)
    if row is None:
        raise HTTPException(status_code=401, detail="Not allowed.")
    return row


@router.post("/checkin")
async def checkin(request: Request, database=Depends(auth.get_db), row=Depends(current_refresher)):
    try:
        report = await request.json()
    except ValueError:
        raise HTTPException(status_code=400, detail="Expected JSON.")
    if not isinstance(report, dict):
        raise HTTPException(status_code=400, detail="Expected a JSON object.")
    reply, failed_now = refreshers.check_in(database, row, report)
    if failed_now:  # tell the admins once per failed run
        for admin in database.query(db.Person).filter(db.Person.role == "admin"):
            notify.notify(admin.id, "A refresh failed", f"{row.name}: " + str((report.get("last_run") or {}).get("message") or "see the logs")[:120],
                          "/admin/refresher", tag=f"refresher-{row.id}")
    return reply
