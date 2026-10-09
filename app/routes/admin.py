"""Admin: Admin home, accounts, view as user, assign, review CVs, jobs sync. Every route here needs an admin."""
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from sqlalchemy import func, or_
from sqlalchemy.orm import defer

from .. import ai, auth, db, deadlines, ingest, runlog, sources, summary, tracking, vault, web
from . import calendar as calendar_routes
from .cv import cv_file_response
from .home import greeting
from .tracker import TABS, filter_views, tab_counts

router = APIRouter(prefix="/admin", dependencies=[Depends(auth.require_admin)])


# ---------- Admin home and accounts ----------

@router.get("")
def admin_home(request: Request, database=Depends(auth.get_db), person=Depends(auth.require_admin)):
    data = summary.admin_home(database, web.today())
    return web.page(request, "admin_home.html", person, "admin", greeting=greeting(),
                    new_account=request.session.pop("new_account", None), **data)


@router.post("/accounts")
def create_account(request: Request, database=Depends(auth.get_db), person=Depends(auth.require_admin),
                   name: str = Form(""), username: str = Form(""), role: str = Form("user")):
    name, username = name.strip()[:80], username.strip().lower()[:40]
    if not name or not auth.valid_username(username):
        web.flash(request, "Please give a name, and a username of 3 to 40 letters, numbers, dots or dashes.", "bad")
    elif database.query(db.Person).filter(db.Person.username == username).first() is not None:
        web.flash(request, f"The username “{username}” is already taken. Please choose another.", "bad")
    else:
        password = auth.new_password()
        database.add(db.Person(name=name, username=username, role="admin" if role == "admin" else "user",
                               password_hash=auth.hash_password(password), password_enc=vault.seal(password)))
        database.commit()
        # Shown once on the next page, then gone: only the hash is kept.
        request.session["new_account"] = {"name": name, "username": username, "password": password, "what": "created"}
    return web.redirect("/admin")


def _account(database, account_id):
    account = database.get(db.Person, account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="We couldn't find that account.")
    return account


@router.post("/accounts/{account_id}/password")
def new_password(account_id: int, request: Request, database=Depends(auth.get_db), person=Depends(auth.require_admin)):
    account = _account(database, account_id)
    password = auth.new_password()
    auth.set_password(account, password, issued=True)  # the old password stops working and the person is signed out everywhere
    database.commit()
    if account.id == person.id:
        auth.start_session(request, account)
    request.session["new_account"] = {"name": account.name, "username": account.username, "password": password, "what": "reset"}
    return web.redirect("/admin")


@router.get("/passwords")
def passwords_page(request: Request, person=Depends(auth.require_admin)):
    return _passwords(request, person, rows=None)


@router.post("/passwords")
def passwords_reveal(request: Request, database=Depends(auth.get_db), person=Depends(auth.require_admin),
                     password: str = Form("")):
    """Show every user's password, but only after the admin types their own again. Nothing is remembered:
    the next visit asks again."""
    problem = auth.confirm_admin(database, person, password[:200])
    if problem:
        error = ("Too many wrong attempts. Try again in a few minutes." if problem == "locked"
                 else "That isn't your password, so nothing is shown.")
        return _passwords(request, person, rows=None, error=error, status_code=401)
    users = database.query(db.Person).filter(db.Person.role != "admin").order_by(db.Person.name).all()
    rows = [{"user": u, "password": vault.unseal(u.password_enc)} for u in users]
    return _passwords(request, person, rows=rows)


def _passwords(request, person, rows, error="", status_code=200):
    response = web.page(request, "admin_passwords.html", person, "admin", rows=rows, error=error, status_code=status_code)
    response.headers["Cache-Control"] = "no-store"
    return response


@router.post("/accounts/{account_id}/remove")
def remove_account(account_id: int, request: Request, database=Depends(auth.get_db), person=Depends(auth.require_admin),
                   confirm: str = Form("")):
    account = _account(database, account_id)
    if account.id == person.id:
        web.flash(request, "You can't remove your own account. Ask the other admin to do it.", "bad")
    elif confirm.strip().lower() != account.username:
        web.flash(request, f"Nothing was removed. To remove {account.name}, type their username exactly.", "bad")
    else:
        # Remove the person and everything that is theirs: Tracker, core CV, tailored CVs.
        for entry in database.query(db.TrackerEntry).filter(db.TrackerEntry.person_id == account.id).all():
            database.delete(entry)
        cv = database.get(db.CV, account.id)
        if cv is not None:
            database.delete(cv)
        database.flush()
        for model, column in ((db.TrackerEntry, "added_by_id"), (db.TailoredCV, "reviewer_id"), (db.SyncRun, "person_id")):
            database.query(model).filter(getattr(model, column) == account.id).update({column: None})
        database.delete(account)
        database.commit()
        web.flash(request, f"{account.name}'s account and all of their data have been removed.")
    return web.redirect("/admin")


# ---------- view as user ----------

def _user(database, person, user_id):
    user = auth.target_user(database, person, user_id)
    if user.id == person.id:
        raise HTTPException(status_code=404, detail="Pick a user from Admin home.")
    return user


@router.get("/users/{user_id}")
def view_user(user_id: int):
    return web.redirect(f"/admin/users/{user_id}/tracker")


@router.get("/users/{user_id}/tracker")
def view_tracker(user_id: int, request: Request, database=Depends(auth.get_db), person=Depends(auth.require_admin),
                 status: str = "", sort: str = "", q: str = ""):
    user = _user(database, person, user_id)
    today = web.today()
    views = summary.entries_for(database, user, today)
    have = {v["entry"].job_id for v in views if v["entry"].job_id}
    found = []
    if q.strip():
        needle = q.strip().lower()
        hits = database.query(db.Job).options(defer(db.Job.description), defer(db.Job.source_urls)).filter(
            or_(func.lower(db.Job.title).contains(needle, autoescape=True),
                func.lower(db.Job.company).contains(needle, autoescape=True))).limit(200)
        for job in hits:  # the database finds matches; only closed ones are skipped here
            if deadlines.listing_status(job, today) != "closed":
                found.append({"job": job, "chip": deadlines.chip(job.deadline, today), "has": job.id in have})
                if len(found) >= 8:
                    break
    base = f"/admin/users/{user.id}/tracker"
    return web.page(request, "tracker.html", person, "admin", owner=user, viewing=user, readonly=True, tabs=TABS,
                    counts=tab_counts(views), views=filter_views(views, status, sort), status=status, sort=sort,
                    total=len(views), base=base, q=q, found=found, section="tracker")


@router.get("/users/{user_id}/entry/{entry_id}")
def view_entry(user_id: int, entry_id: int, request: Request, database=Depends(auth.get_db), person=Depends(auth.require_admin)):
    user = _user(database, person, user_id)
    entry = auth.own_entry(database, person, entry_id, allow_admin=True)
    if entry.person_id != user.id:
        raise HTTPException(status_code=404, detail="We couldn't find that job in the Tracker.")
    people = {p.id: p.first_name for p in database.query(db.Person)}
    return web.page(request, "entry.html", person, "admin", v=summary.entry_view(entry, web.today()), owner=user,
                    viewing=user, readonly=True, people=people, section="tracker")


@router.get("/users/{user_id}/calendar")
def view_calendar(user_id: int, request: Request, database=Depends(auth.get_db), person=Depends(auth.require_admin), m: str = ""):
    user = _user(database, person, user_id)
    return web.page(request, "calendar.html", person, "admin", owner=user, viewing=user, readonly=True,
                    base=f"/admin/users/{user.id}/calendar", entry_base=f"/admin/users/{user.id}/entry", section="calendar",
                    **calendar_routes.build(database, user, web.today(), m))


@router.get("/users/{user_id}/profile")
def view_profile(user_id: int, request: Request, database=Depends(auth.get_db), person=Depends(auth.require_admin)):
    user = _user(database, person, user_id)
    return web.page(request, "profile.html", person, "admin", owner=user, viewing=user, readonly=True,
                    cv=database.get(db.CV, user.id), base=f"/admin/users/{user.id}/profile", section="profile")


@router.get("/users/{user_id}/cv/download")
def view_cv_download(user_id: int, database=Depends(auth.get_db), person=Depends(auth.require_admin)):
    user = _user(database, person, user_id)
    return cv_file_response(database.get(db.CV, user.id))


@router.post("/users/{user_id}/assign")
async def assign(user_id: int, request: Request, database=Depends(auth.get_db), person=Depends(auth.require_admin)):
    """Put a job in a user's Tracker: from the Board or by hand. It is marked "Assigned by <admin>"."""
    user = _user(database, person, user_id)
    form = await request.form()
    back = web.safe_next(str(form.get("next") or ""), f"/admin/users/{user.id}/tracker")
    how = str(form.get("how") or "board")
    try:
        if how == "manual":
            entry, created = tracking.add_manual(database, user, form, person), True
        else:
            job = database.get(db.Job, str(form.get("job_id") or ""))
            if job is None:
                raise HTTPException(status_code=404, detail="We couldn't find that job.")
            entry, created = tracking.add_from_board(database, user, job, person)
    except ValueError as e:
        web.flash(request, str(e), "bad")
        return web.redirect(f"/admin/users/{user.id}/tracker?add=manual")
    except tracking.TrackerFull:
        web.flash(request, f"{user.first_name}'s Tracker is full.", "bad")
        return web.redirect(back)
    title = db.entry_job(entry).title
    web.flash(request, f"Assigned {title} to {user.first_name}." if created else f"{user.first_name} already has {title}.")
    return web.redirect(back)


@router.post("/assign")
async def assign_from_board(request: Request, database=Depends(auth.get_db), person=Depends(auth.require_admin)):
    """The Job Board's "Assign" control: the user is chosen in the form."""
    form = await request.form()
    try:
        user_id = int(str(form.get("user") or ""))
    except ValueError:
        raise HTTPException(status_code=404, detail="Please choose who to assign the job to.")
    return await assign(user_id, request, database, person)


# ---------- review CVs ----------

@router.get("/review")
def review_list(request: Request, database=Depends(auth.get_db), person=Depends(auth.require_admin)):
    return web.page(request, "admin_review.html", person, "review", queue=summary.review_queue(database, web.today()))


def _review_item(database, entry_id):
    entry = database.get(db.TrackerEntry, entry_id)
    if entry is None or entry.tailored is None:
        raise HTTPException(status_code=404, detail="We couldn't find that CV. It may have been deleted.")
    return entry


@router.get("/review/{entry_id}")
def review_one(entry_id: int, request: Request, database=Depends(auth.get_db), person=Depends(auth.require_admin)):
    entry = _review_item(database, entry_id)
    return web.page(request, "admin_review_one.html", person, "review", v=summary.entry_view(entry, web.today()),
                    owner=entry.person, core=database.get(db.CV, entry.person_id), tailored=entry.tailored)


@router.post("/review/{entry_id}")
def review_decide(entry_id: int, request: Request, database=Depends(auth.get_db), person=Depends(auth.require_admin),
                  decision: str = Form(""), comment: str = Form("")):
    entry = _review_item(database, entry_id)
    cv, comment = entry.tailored, comment.strip()[:2000]
    if cv.status != "in_review":
        web.flash(request, "That CV is no longer waiting for review.", "bad")
        return web.redirect("/admin/review")
    if decision == "changes" and not comment:
        web.flash(request, "Please say what should change, so they know what to do.", "bad")
        return web.redirect(f"/admin/review/{entry.id}")
    if decision not in ("approve", "changes"):
        return web.redirect(f"/admin/review/{entry.id}")
    cv.status = "approved" if decision == "approve" else "changes_requested"
    cv.reviewer_id, cv.review_comment, cv.reviewed_at, cv.review_seen = person.id, comment, db.utcnow(), False
    database.commit()
    web.flash(request, f"{'Approved' if decision == 'approve' else 'Changes requested'}. {entry.person.first_name} will see it on their Home page.")
    return web.redirect("/admin/review")


# ---------- jobs sync ----------

@router.get("/sync")
def sync_page(request: Request, database=Depends(auth.get_db), person=Depends(auth.require_admin)):
    history = database.query(db.SyncRun).order_by(db.SyncRun.at.desc()).limit(10).all()
    picked, skipped = sources.plan()
    return web.page(request, "admin_sync.html", person, "sync", profiles=ingest.profiles(database),
                    can_fetch=bool(picked), picked=picked, skipped=skipped, backends=[ai.backend_label(b) for b in (ai.load_tagging_order() and ai.tagging_backends())],
                    order=ai.tagging_order(), choices=[(b, ai.backend_label(b), ai.backend_ready(b)) for b in ai.ALL_BACKENDS],
                    state=dict(ingest.STATE), prog=ingest.progress(), runs=runlog.recent(database, 5), notes=runlog.attention(database, web.today()),
                    history=history, today_=web.today())


@router.get("/sync/status")
def sync_status():
    return ingest.progress()


@router.post("/sync/profile/{person_id}")
def sync_profile(person_id: int, request: Request, database=Depends(auth.get_db), keywords: str = Form(""),
                 locations: str = Form(""), avoid: str = Form(""), distance: int = Form(25), per_search: int = Form(100),
                 remote_uk: str = Form(""), uk_wide: str = Form(""), needs_sponsorship: str = Form(""), enabled: str = Form("")):
    profile = database.get(db.SearchProfile, person_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="We couldn't find that person.")
    profile.keywords, profile.locations, profile.avoid = keywords.strip()[:3000], locations.strip()[:1000], avoid.strip()[:1000]
    profile.distance, profile.per_search = min(max(distance, 1), 200), min(max(per_search, 5), 1000)
    profile.remote_uk, profile.uk_wide = bool(remote_uk), bool(uk_wide)
    profile.needs_sponsorship, profile.enabled = bool(needs_sponsorship), bool(enabled)
    database.commit()
    web.flash(request, f"Saved {profile.person.first_name}'s search.")
    return web.redirect("/admin/sync")


@router.post("/sync/tagging")
def sync_tagging(request: Request, pick0: str = Form(""), pick1: str = Form(""), pick2: str = Form("")):
    ai.save_tagging_order([pick0, pick1, pick2])
    web.flash(request, "Tagging order saved." if ai.tagging_order() else "Saved: jobs will be tagged by keywords only.")
    return web.redirect("/admin/sync")


@router.post("/sync/refresh")
def sync_refresh(request: Request, database=Depends(auth.get_db), person=Depends(auth.require_admin)):
    if not sources.plan()[0]:
        web.flash(request, "Refreshing only works when Job Buddy is running on your laptop with the fetching tools installed.", "bad")
    elif not ingest._plan(ingest.profiles(database)):
        web.flash(request, "Add at least one job title or keyword to someone's search first.", "bad")
    elif not ingest.start(person.id):
        web.flash(request, "A refresh is already running.", "bad")
    return web.redirect("/admin/sync")


@router.post("/sync/stop")
def sync_stop(request: Request):
    ingest.stop()
    web.flash(request, "Stopping after the current step. Nothing is lost; press Refresh to carry on.")
    return web.redirect("/admin/sync")
