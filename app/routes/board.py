"""The Job Board: the shared list of live jobs, and Shortlist."""
from datetime import date

from fastapi import APIRouter, Depends, Form, HTTPException, Request

from .. import auth, db, deadlines, ingest, tracking, web

router = APIRouter()
PAGE_SIZE = 60


@router.get("/board")
def board(request: Request, database=Depends(auth.get_db), person=Depends(auth.current_person), q: str = "",
          status: str = "open", location: str = "", work_mode: str = "", sponsorship: str = "", seniority: str = "",
          sort: str = "deadline", page: int = 1):
    today = web.today()
    since = person.last_board_visit
    mine = {} if person.is_admin else {
        e.job_id: e for e in database.query(db.TrackerEntry).filter(db.TrackerEntry.person_id == person.id) if e.job_id}

    rows = []
    needle, place = q.strip().lower(), location.strip().lower()
    for job in database.query(db.Job):
        listing = deadlines.listing_status(job, today)
        if status == "open" and listing == "closed":
            continue  # closed jobs are hidden unless asked for
        if status in ("closing_soon", "closed", "possibly_closed") and listing != status:
            continue
        if needle and needle not in f"{job.title} {job.company}".lower():
            continue
        if place and place not in (job.location or "").lower():
            continue
        if work_mode and job.work_mode != work_mode:
            continue
        if sponsorship and job.sponsorship != sponsorship:
            continue
        if seniority and job.seniority != seniority:
            continue
        rows.append({"job": job, "listing": listing, "chip": deadlines.chip(job.deadline, today),
                     "entry": mine.get(job.id), "is_new": bool(since and job.first_seen and job.first_seen > since)})

    if sort == "newest":
        rows.sort(key=lambda r: r["job"].first_seen or db.utcnow(), reverse=True)
    elif sort == "company":
        rows.sort(key=lambda r: (r["job"].company or "").lower())
    else:  # soonest deadline first; jobs with no deadline after those with one
        rows.sort(key=lambda r: (r["job"].deadline is None, r["job"].deadline or date.max, (r["job"].title or "").lower()))

    total = len(rows)
    pages = max(1, -(-total // PAGE_SIZE))
    page = min(max(page, 1), pages)
    shown = rows[(page - 1) * PAGE_SIZE: page * PAGE_SIZE]
    filters = {"q": q, "status": status, "location": location, "work_mode": work_mode, "sponsorship": sponsorship,
               "seniority": seniority, "sort": sort}
    query = "&".join(f"{k}={v}" for k, v in filters.items() if v)
    users = database.query(db.Person).filter(db.Person.role == "user").order_by(db.Person.name).all() if person.is_admin else []
    response = web.page(request, "board.html", person, "board", database=database, rows=shown, total=total, page=page,
                        pages=pages, filters=filters, query=query, last_sync=ingest.last_sync(database),
                        board_age=ingest.board_age_days(database, today), users=users)
    person.last_board_visit = db.utcnow()
    database.commit()
    return response


def _job(database, job_id):
    job = database.get(db.Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="We couldn't find that job. It may have been removed.")
    return job


@router.get("/board/{job_id}")
def job_detail(job_id: str, request: Request, database=Depends(auth.get_db), person=Depends(auth.current_person)):
    job = _job(database, job_id)
    today = web.today()
    entry = None if person.is_admin else (database.query(db.TrackerEntry)
                                          .filter(db.TrackerEntry.person_id == person.id, db.TrackerEntry.job_id == job.id).first())
    users = database.query(db.Person).filter(db.Person.role == "user").order_by(db.Person.name).all() if person.is_admin else []
    return web.page(request, "job.html", person, "board", database=database, job=job, entry=entry, users=users,
                    listing=deadlines.listing_status(job, today), chip=deadlines.chip(job.deadline, today))


@router.post("/board/{job_id}/shortlist")
def shortlist(job_id: str, request: Request, database=Depends(auth.get_db), person=Depends(auth.current_person),
              next: str = Form("/board")):
    if person.is_admin:
        raise HTTPException(status_code=403, detail="Admins assign jobs to a user instead of shortlisting.")
    job = _job(database, job_id)
    try:
        _, created = tracking.add_from_board(database, person, job, person)
    except tracking.TrackerFull:
        web.flash(request, "Your Tracker is full. Remove some old jobs first.", "bad")
        return web.redirect("/tracker")
    web.flash(request, f"Added {job.title} to your Tracker." if created else "That job is already in your Tracker.")
    return web.redirect(web.safe_next(next, "/board"))
