"""The Job Board: the shared list of live jobs, and Shortlist."""
from datetime import date

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from sqlalchemy import func, or_
from sqlalchemy.orm import defer

from .. import auth, db, deadlines, ingest, tracking, web

router = APIRouter()
PAGE_SIZE = 60


@router.get("/board")
def board(request: Request, database=Depends(auth.get_db), person=Depends(auth.current_person), q: str = "",
          status: str = "open", location: str = "", work_mode: str = "", sponsorship: str = "", seniority: str = "",
          sort: str = "", view: str = "", who: str = "", page: int = 1):
    today = web.today()
    who_id = int(who) if who.isdecimal() else 0
    since = person.last_board_visit
    mine = {} if person.is_admin else {
        e.job_id: e for e in database.query(db.TrackerEntry).filter(db.TrackerEntry.person_id == person.id) if e.job_id}

    profile = None if person.is_admin else database.get(db.SearchProfile, person.id)
    has_profile = bool(profile and profile.enabled and (profile.keywords or "").strip())
    forme = has_profile and view != "all"  # their own matches by default; "Everything" is one tap away
    sort = sort or ("best" if forme else "deadline")
    rows = []
    needle, place = q.strip().lower(), location.strip().lower()
    # The database narrows the list and the heavy advert text is never read: a list page does not show it. What is left
    # (closing status, "for you" matches, ranking) is cheap Python over a small, light set.
    # ponytail: ceiling is a few tens of thousands of jobs; past that, move status and matches into SQL too.
    query = database.query(db.Job).options(defer(db.Job.description), defer(db.Job.source_urls))
    if needle:
        query = query.filter(or_(func.lower(db.Job.title).contains(needle, autoescape=True),
                                 func.lower(db.Job.company).contains(needle, autoescape=True)))
    if place:
        query = query.filter(func.lower(db.Job.location).contains(place, autoescape=True))
    if work_mode:
        query = query.filter(db.Job.work_mode == work_mode)
    if sponsorship:
        query = query.filter(db.Job.sponsorship == sponsorship)
    if seniority:
        query = query.filter(db.Job.seniority == seniority)
    for job in query:
        listing = deadlines.listing_status(job, today)
        if status == "open" and listing == "closed":
            continue  # closed jobs are hidden unless asked for
        if status in ("closing_soon", "closed", "possibly_closed") and listing != status:
            continue
        if forme and (person.id not in (job.matched or []) or (profile.needs_sponsorship and job.citizenship_required)):
            continue
        if person.is_admin and who_id and who_id not in (job.matched or []):
            continue  # the admin can look at one person's matches
        rows.append({"job": job, "listing": listing, "chip": deadlines.chip(job.deadline, today),
                     "entry": mine.get(job.id), "is_new": bool(since and job.first_seen and job.first_seen > since)})

    if sort == "best":  # sponsors first for someone who needs a visa, then the most recently added
        def tier(job):
            if not (forme and profile.needs_sponsorship):
                return 0
            if job.sponsorship == "sponsors":
                return 0
            if job.licensed_sponsor:
                return 1
            # "must have the right to work in the UK" is not a refusal to sponsor, and the Graduate visa gives that right
            refuses = job.sponsorship == "no_sponsorship" and "sponsor" in (job.sponsorship_quote or "").lower()
            return 3 if refuses else 2
        rows.sort(key=lambda r: (tier(r["job"]), -(r["job"].first_seen or db.utcnow()).timestamp()))
    elif sort == "newest":
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
               "seniority": seniority, "sort": sort, "view": "all" if has_profile and not forme else "", "who": who_id or ""}
    query = "&".join(f"{k}={v}" for k, v in filters.items() if v)
    users = database.query(db.Person).filter(db.Person.role == "user").order_by(db.Person.name).all() if person.is_admin else []
    names = {u.id: u.first_name for u in users}
    response = web.page(request, "board.html", person, "board", database=database, rows=shown, total=total, page=page,
                        pages=pages, filters=filters, query=query, has_profile=has_profile, forme=forme, names=names, last_sync=ingest.last_sync(database),
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
    names = {u.id: u.first_name for u in users}
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
