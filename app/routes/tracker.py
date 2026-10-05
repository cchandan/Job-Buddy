"""The Tracker: a person's own jobs, with deadline, status and actions."""
from datetime import date

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile

from .. import auth, cv_pdf, db, summary, tracking, web

router = APIRouter()
TABS = [("", "All"), ("shortlisted", "Shortlisted"), ("applied", "Applied"), ("interviewing", "Interviewing"),
        ("offer", "Offer"), ("closed", "Closed out")]
CLOSED_OUT = ("rejected", "withdrawn")


def users_only(person=Depends(auth.current_person)):
    """The Tracker belongs to users. Admins see a user's Tracker from Admin home."""
    if person.is_admin:
        raise HTTPException(status_code=403, detail="Admins don't have a Tracker of their own. Open a user's from Admin home.")
    return person


def tab_counts(views):
    counts = summary.status_counts(views)
    return {"": len(views), "closed": sum(counts[s] for s in CLOSED_OUT), **{s: counts[s] for s in db.STATUSES}}


def filter_views(views, status, sort):
    if status == "closed":
        views = [v for v in views if v["entry"].status in CLOSED_OUT]
    elif status:
        views = [v for v in views if v["entry"].status == status]
    if sort == "added":
        views = sorted(views, key=lambda v: v["entry"].created_at, reverse=True)
    return views


@router.get("/tracker")
def tracker(request: Request, database=Depends(auth.get_db), person=Depends(users_only), status: str = "", sort: str = ""):
    views = summary.entries_for(database, person, web.today())
    response = web.page(request, "tracker.html", person, "tracker", owner=person, readonly=False, tabs=TABS,
                        counts=tab_counts(views), views=filter_views(views, status, sort), status=status, sort=sort,
                        total=len(views), base="/tracker")
    for v in views:  # assigned jobs count as "new" until the owner has seen them here
        if v["entry"].seen_at is None:
            v["entry"].seen_at = db.utcnow()
    database.commit()
    return response


@router.get("/tracker/new")
def new_form(request: Request, person=Depends(users_only)):
    return web.page(request, "entry_form.html", person, "tracker", action="/tracker/new", heading="Add a job by hand",
                    d={}, deadline="", notes="", error="", editing=False)


@router.post("/tracker/new/read")
async def read_pdf(request: Request, person=Depends(users_only), file: UploadFile = File(None)):
    """Fill the add-by-hand form from a job description PDF. Nothing is saved until the person presses Add."""
    d, deadline, note, error = {}, "", "", ""
    data = await file.read(2 * 1024 * 1024 + 1) if file is not None and file.filename else b""
    if not data:
        error = "Please choose a PDF of the job description."
    elif len(data) > 2 * 1024 * 1024:
        error = "That file is larger than 2 MB. Please choose a smaller PDF."
    else:
        try:
            d, deadline = tracking.prefill(cv_pdf.extract_text(data))
            note = "We filled in what we could find. Please check each box before adding the job."
        except ValueError as e:
            error = str(e)
    return web.page(request, "entry_form.html", person, "tracker", action="/tracker/new", heading="Add a job by hand",
                    d=d, deadline=deadline, notes="", error=error, note=note, editing=False)


@router.post("/tracker/new")
async def new_submit(request: Request, database=Depends(auth.get_db), person=Depends(users_only)):
    form = await request.form()
    try:
        entry = tracking.add_manual(database, person, form, person)
    except ValueError as e:
        return web.page(request, "entry_form.html", person, "tracker", status_code=400, action="/tracker/new",
                        heading="Add a job by hand", d=form, deadline=form.get("deadline", ""), notes=form.get("notes", ""),
                        error=str(e), editing=False)
    except tracking.TrackerFull:
        web.flash(request, "Your Tracker is full. Remove some old jobs first.", "bad")
        return web.redirect("/tracker")
    web.flash(request, f"Added {db.entry_job(entry).title} to your Tracker.")
    return web.redirect("/tracker")


@router.get("/tracker/{entry_id}")
def entry_detail(entry_id: int, request: Request, database=Depends(auth.get_db), person=Depends(users_only)):
    entry = auth.own_entry(database, person, entry_id)
    people = {p.id: p.first_name for p in database.query(db.Person)}
    return web.page(request, "entry.html", person, "tracker", v=summary.entry_view(entry, web.today()), owner=person,
                    readonly=False, people=people)


@router.get("/tracker/{entry_id}/edit")
def edit_form(entry_id: int, request: Request, database=Depends(auth.get_db), person=Depends(users_only)):
    entry = auth.own_entry(database, person, entry_id)
    if entry.job_id:
        raise HTTPException(status_code=404, detail="Jobs from the Job Board can't be edited, but you can set your own deadline and notes.")
    return web.page(request, "entry_form.html", person, "tracker", action=f"/tracker/{entry.id}/edit", heading="Edit job details",
                    d=entry.details or {}, deadline=entry.deadline_override.isoformat() if entry.deadline_override else
                    (entry.details or {}).get("deadline") or "", notes=entry.notes or "", error="", editing=True)


@router.post("/tracker/{entry_id}/edit")
async def edit_submit(entry_id: int, request: Request, database=Depends(auth.get_db), person=Depends(users_only)):
    entry = auth.own_entry(database, person, entry_id)
    if entry.job_id:
        raise HTTPException(status_code=404, detail="Jobs from the Job Board can't be edited.")
    form = await request.form()
    details, deadline = tracking.manual_details(form)
    if not details["title"] or not details["company"]:
        return web.page(request, "entry_form.html", person, "tracker", status_code=400, action=f"/tracker/{entry.id}/edit",
                        heading="Edit job details", d=form, deadline=form.get("deadline", ""), notes=form.get("notes", ""),
                        error="Please give at least a job title and a company.", editing=True)
    old = entry.details or {}
    for keep in ("sponsorship_quote", "work_mode_quote", "seniority", "industry", "required_skills", "optional_skills"):
        details[keep] = old.get(keep, details[keep])  # tags read from the page stay unless the field was edited
    if details["sponsorship"] != old.get("sponsorship"):
        details["sponsorship_quote"] = ""
    entry.details, entry.deadline_override = details, deadline
    entry.notes = str(form.get("notes") or "").strip()[:4000]
    database.commit()
    web.flash(request, "Saved.")
    return web.redirect(f"/tracker/{entry.id}")


@router.post("/tracker/{entry_id}/status")
def set_status(entry_id: int, request: Request, database=Depends(auth.get_db), person=Depends(users_only),
               status: str = Form(""), next: str = Form("/tracker")):
    entry = auth.own_entry(database, person, entry_id)
    if tracking.set_status(database, entry, status, person):
        web.flash(request, f"Marked as {web.STATUS_LABELS[status]}.")
    return web.redirect(web.safe_next(next, "/tracker"))


@router.post("/tracker/{entry_id}/deadline")
def set_deadline(entry_id: int, request: Request, database=Depends(auth.get_db), person=Depends(users_only),
                 deadline: str = Form(""), next: str = Form("")):
    entry = auth.own_entry(database, person, entry_id)
    try:
        entry.deadline_override = date.fromisoformat(deadline) if deadline.strip() else None
    except ValueError:
        web.flash(request, "That date didn't look right, so the deadline wasn't changed.", "bad")
        return web.redirect(web.safe_next(next, f"/tracker/{entry.id}"))
    database.commit()
    web.flash(request, "Deadline saved." if entry.deadline_override else "Your own deadline was cleared.")
    return web.redirect(web.safe_next(next, f"/tracker/{entry.id}"))


@router.post("/tracker/{entry_id}/notes")
def set_notes(entry_id: int, request: Request, database=Depends(auth.get_db), person=Depends(users_only), notes: str = Form("")):
    entry = auth.own_entry(database, person, entry_id)
    entry.notes = notes.strip()[:4000]
    database.commit()
    web.flash(request, "Notes saved.")
    return web.redirect(f"/tracker/{entry.id}")


@router.post("/tracker/{entry_id}/remove")
def remove(entry_id: int, request: Request, database=Depends(auth.get_db), person=Depends(users_only)):
    entry = auth.own_entry(database, person, entry_id)
    title = db.entry_job(entry).title
    database.delete(entry)
    database.commit()
    web.flash(request, f"Removed {title} from your Tracker.")
    return web.redirect("/tracker")


@router.post("/tracker/{entry_id}/apply")
def apply(entry_id: int, request: Request, database=Depends(auth.get_db), person=Depends(users_only)):
    """Open the advert. The status never changes by itself: the Tracker then asks "Did you apply?"."""
    entry = auth.own_entry(database, person, entry_id)
    url = db.entry_job(entry).url or ""
    if not url.lower().startswith(("http://", "https://")):
        raise HTTPException(status_code=404, detail="This job has no link to open. Add one by editing it.")
    if entry.status == "shortlisted":
        entry.apply_clicked_at = db.utcnow()
        database.commit()
    return web.redirect(url)


@router.post("/tracker/{entry_id}/applied")
def applied(entry_id: int, request: Request, database=Depends(auth.get_db), person=Depends(users_only),
            answer: str = Form(""), next: str = Form("/tracker")):
    entry = auth.own_entry(database, person, entry_id)
    if answer == "yes":
        tracking.set_status(database, entry, "applied", person)
        web.flash(request, "Marked as Applied. Good luck!")
    else:
        entry.apply_clicked_at = None
        database.commit()
    return web.redirect(web.safe_next(next, "/tracker"))
