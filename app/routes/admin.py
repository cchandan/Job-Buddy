"""Admin: Today, People, view as user, assign, Refresh, Searches, Bin, Snapshots, Settings. Every route here needs an admin."""
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import Response
from sqlalchemy import func, or_
from sqlalchemy.orm import defer

from .. import ai, auth, db, deadlines, ingest, jobbin, notify, push, refreshers, runlog, snapshots, sources, summary, tracking, vault, web
from . import calendar as calendar_routes
from .cv import cv_file_response
from .home import greeting
from .tracker import TABS, filter_views, tab_counts

router = APIRouter(prefix="/admin", dependencies=[Depends(auth.require_admin)])


# ---------- Admin home and accounts ----------

@router.get("")
def admin_home(request: Request, database=Depends(auth.get_db), person=Depends(auth.require_admin)):
    data = summary.admin_home(database, web.today())
    return web.page(request, "admin_home.html", person, "today", greeting=greeting(), **data)


# ---------- People ----------

@router.get("/people")
def people_page(request: Request, database=Depends(auth.get_db), person=Depends(auth.require_admin),
                tab: str = "users", manage: int = 0, add: str = ""):
    return _people(request, database, person, tab=tab, manage=manage, add=bool(add))


def _people(request, database, person, tab="users", manage=0, add=False, reveal=None, error="", status_code=200):
    today = web.today()
    users = database.query(db.Person).filter(db.Person.role == "user").order_by(db.Person.name).all()
    admins = database.query(db.Person).filter(db.Person.role == "admin").order_by(db.Person.name).all()
    rows = []
    for u in users:
        views = summary.entries_for(database, u, today)
        counts = summary.status_counts(views)
        rows.append({"user": u, "total": len(views), "applied": counts["applied"] + counts["interviewing"] + counts["offer"]})
    chosen = next((a for a in users + admins if a.id == manage), None)
    profile = database.get(db.SearchProfile, chosen.id) if chosen is not None and not chosen.is_admin else None
    response = web.page(request, "admin_people.html", person, "people", users=rows, admins=admins, tab="admins" if tab == "admins" else "users",
                        signups=database.query(db.SignupRequest).order_by(db.SignupRequest.created_at).all(),
                        signups_open=auth.signups_open(database), new_account=request.session.pop("new_account", None),
                        chosen=chosen, profile=profile, add=add, reveal=reveal, error=error, status_code=status_code)
    response.headers["Cache-Control"] = "no-store"  # a revealed password must not be kept by the browser
    return response


@router.post("/people/{account_id}/reveal")
def people_reveal(account_id: int, request: Request, database=Depends(auth.get_db), person=Depends(auth.require_admin),
                  password: str = Form("")):
    """Show one person's password, but only after the admin types their own again. Nothing is remembered."""
    account = _account(database, account_id)
    problem = auth.confirm_admin(database, person, password[:200])
    if problem:
        error = ("Too many wrong attempts. Try again in a few minutes." if problem == "locked"
                 else "That isn't your password, so nothing is shown.")
        return _people(request, database, person, manage=account.id, error=error, status_code=401)
    shown = vault.unseal(account.password_enc)
    return _people(request, database, person, manage=account.id, reveal={"id": account.id, "password": shown})


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
    return web.redirect("/admin/people")


@router.post("/signups/toggle")
def signups_toggle(request: Request, database=Depends(auth.get_db), next: str = Form("")):
    turning_off = auth.signups_open(database)
    database.merge(db.AppSetting(key="signups", value="off" if turning_off else "on"))
    database.commit()
    web.flash(request, "Public sign-up is closed. Nobody new can ask for an account." if turning_off else "Public sign-up is open.")
    return web.redirect(web.safe_next(next, "/admin/settings"))


def _signup(database, request_id):
    req = database.get(db.SignupRequest, request_id)
    if req is None:
        raise HTTPException(status_code=404, detail="That sign-up request has already been dealt with.")
    return req


@router.post("/signups/{request_id}/approve")
def signup_approve(request_id: int, request: Request, database=Depends(auth.get_db), next: str = Form("")):
    req = _signup(database, request_id)
    back = web.safe_next(next, "/admin/people")
    if database.query(db.Person).filter(db.Person.username == req.username).first() is not None:
        database.delete(req)  # an admin made an account with that username in the meantime
        database.commit()
        web.flash(request, f"The username “{req.username}” was taken in the meantime, so that request was dropped.", "bad")
        return web.redirect(back)
    database.add(db.Person(name=req.name, username=req.username, role="user", password_hash=req.password_hash))
    name, username = req.name, req.username
    database.delete(req)
    database.commit()
    web.flash(request, f"Approved {name}. They can sign in now as “{username}” with the password they chose. Let them know.")
    return web.redirect(back)


@router.post("/signups/{request_id}/reject")
def signup_reject(request_id: int, request: Request, database=Depends(auth.get_db), next: str = Form("")):
    req = _signup(database, request_id)
    name = req.name
    database.delete(req)
    database.commit()
    web.flash(request, f"Declined {name}'s request.")
    return web.redirect(web.safe_next(next, "/admin/people"))


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
    return web.redirect("/admin/people")


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
    response = web.page(request, "admin_passwords.html", person, "people", rows=rows, error=error, status_code=status_code)
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
        # Remove the person and everything that is theirs: Tracker and core CV.
        for entry in database.query(db.TrackerEntry).filter(db.TrackerEntry.person_id == account.id).all():
            database.delete(entry)
        cv = database.get(db.CV, account.id)
        if cv is not None:
            database.delete(cv)
        database.query(db.PushSubscription).filter(db.PushSubscription.person_id == account.id).delete()
        database.query(db.JobDecision).filter(db.JobDecision.person_id == account.id).delete()
        database.flush()
        for model, column in ((db.TrackerEntry, "added_by_id"), (db.SyncRun, "person_id")):
            database.query(model).filter(getattr(model, column) == account.id).update({column: None})
        database.delete(account)
        database.commit()
        web.flash(request, f"{account.name}'s account and all of their data have been removed.")
    return web.redirect("/admin/people")


# ---------- view as user ----------

def _user(database, person, user_id):
    user = auth.target_user(database, person, user_id)
    if user.id == person.id:
        raise HTTPException(status_code=404, detail="Pick a user from People.")
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
            db.Job.binned_at.is_(None),
            or_(func.lower(db.Job.title).contains(needle, autoescape=True),
                func.lower(db.Job.company).contains(needle, autoescape=True))).limit(200)
        for job in hits:  # the database finds matches; only closed ones are skipped here
            if deadlines.listing_status(job, today) != "closed":
                found.append({"job": job, "chip": deadlines.chip(job.deadline, today), "has": job.id in have})
                if len(found) >= 8:
                    break
    base = f"/admin/users/{user.id}/tracker"
    return web.page(request, "tracker.html", person, "people", owner=user, viewing=user, readonly=True, tabs=TABS,
                    counts=tab_counts(views), views=filter_views(views, status, sort), status=status, sort=sort,
                    total=len(views), base=base, q=q, found=found, section="tracker")


@router.get("/users/{user_id}/entry/{entry_id}")
def view_entry(user_id: int, entry_id: int, request: Request, database=Depends(auth.get_db), person=Depends(auth.require_admin)):
    user = _user(database, person, user_id)
    entry = auth.own_entry(database, person, entry_id, allow_admin=True)
    if entry.person_id != user.id:
        raise HTTPException(status_code=404, detail="We couldn't find that job in the Tracker.")
    people = {p.id: p.first_name for p in database.query(db.Person)}
    return web.page(request, "entry.html", person, "people", v=summary.entry_view(entry, web.today()), owner=user,
                    viewing=user, readonly=True, people=people, section="tracker")


@router.get("/users/{user_id}/calendar")
def view_calendar(user_id: int, request: Request, database=Depends(auth.get_db), person=Depends(auth.require_admin), m: str = ""):
    user = _user(database, person, user_id)
    return web.page(request, "calendar.html", person, "people", owner=user, viewing=user, readonly=True,
                    base=f"/admin/users/{user.id}/calendar", entry_base=f"/admin/users/{user.id}/entry", section="calendar",
                    **calendar_routes.build(database, user, web.today(), m))


@router.get("/users/{user_id}/profile")
def view_profile(user_id: int, request: Request, database=Depends(auth.get_db), person=Depends(auth.require_admin)):
    user = _user(database, person, user_id)
    return web.page(request, "profile.html", person, "people", owner=user, viewing=user, readonly=True,
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
    if created:
        notify.notify(user.id, f"New job from {person.first_name}", f"{title} · {db.entry_job(entry).company}", "/tracker", tag="assigned")
        notify.tracker_change(database, person, entry, "had a job assigned by " + person.first_name)
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


# ---------- the bin ----------

@router.get("/bin")
def bin_page(request: Request, database=Depends(auth.get_db), person=Depends(auth.require_admin), kind: str = ""):
    query = (database.query(db.Job).options(defer(db.Job.description), defer(db.Job.source_urls))
             .filter(db.Job.binned_at.isnot(None)))
    if kind == "expired":
        query = query.filter(db.Job.bin_reason.like("Expired:%"))
    elif kind == "relevance":
        query = query.filter(db.Job.bin_reason.like("No longer relevant%"))
    elif kind == "manual":
        query = query.filter(db.Job.bin_reason.like(jobbin.MANUAL_PREFIX + "%"))
    return web.page(request, "admin_bin.html", person, "bin", cand=jobbin.candidates(database, web.today()), kind=kind,
                    total=query.count(), rows=query.order_by(db.Job.binned_at.desc(), db.Job.title).limit(300).all(),
                    all_count=jobbin.count(database), kind_counts=_bin_counts(database))


def _bin_counts(database):
    binned = database.query(func.count(db.Job.id)).filter(db.Job.binned_at.isnot(None))
    return {"expired": binned.filter(db.Job.bin_reason.like("Expired:%")).scalar(),
            "relevance": binned.filter(db.Job.bin_reason.like("No longer relevant%")).scalar(),
            "manual": binned.filter(db.Job.bin_reason.like(jobbin.MANUAL_PREFIX + "%")).scalar()}


@router.post("/bin/add/{job_id}")
def bin_add(job_id: str, request: Request, database=Depends(auth.get_db), person=Depends(auth.require_admin),
            reason: str = Form(""), next: str = Form("/board")):
    """Bin one advert by hand. It leaves the Job Board; anyone tracking it keeps it in their Tracker."""
    job = database.get(db.Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="We couldn't find that job.")
    if job.binned_at is None:
        jobbin.bin_by_hand(database, job, person, reason)
        web.flash(request, f"Moved “{job.title}” to the bin. You can restore it from Admin > Bin. Publish to send this to the live site.")
    return web.redirect(web.safe_next(next, "/board"))


@router.post("/bin/tidy")
def bin_tidy(request: Request, database=Depends(auth.get_db)):
    """Move everything that is expired or no longer relevant to the bin. Nothing is deleted and no Tracker is touched."""
    cand = jobbin.candidates(database, web.today())
    jobbin.bin_jobs(database, cand["expired"] + cand["irrelevant"])
    e, r = len(cand["expired"]), len(cand["irrelevant"])
    web.flash(request, f"Moved {e} expired and {r} no-longer-relevant job{'' if e + r == 1 else 's'} to the bin. Publish to send this to the live site."
              if e + r else "Nothing needed moving.")
    return web.redirect("/admin/bin")


@router.post("/bin/restore")
async def bin_restore(request: Request, database=Depends(auth.get_db)):
    ids = [str(v) for v in (await request.form()).getlist("job")][:500]
    n = jobbin.restore(database, ids) if ids else 0
    web.flash(request, f"Restored {n} job{'' if n == 1 else 's'} to the Job Board. They won't be binned automatically again." if n
              else "Tick the jobs you want to restore first.", "ok" if n else "bad")
    return web.redirect("/admin/bin")


# ---------- snapshots ----------

@router.get("/snapshots")
def snapshots_page(request: Request, database=Depends(auth.get_db), person=Depends(auth.require_admin)):
    return web.page(request, "admin_snapshots.html", person, "snapshots", snaps=snapshots.listing(database), room=snapshots.MAX_SNAPSHOTS,
                    jobs_now=database.query(func.count(db.Job.id)).scalar(), binned_now=jobbin.count(database))


def _snapshot_action(request, fn, *args, done=""):
    if ingest.STATE["running"]:
        web.flash(request, "A refresh is running. Wait for it to finish, then try again.", "bad")
        return web.redirect("/admin/snapshots")
    try:
        message = fn(*args)
    except snapshots.SnapshotError as e:
        web.flash(request, str(e), "bad")
    else:
        web.flash(request, message or done)
    return web.redirect("/admin/snapshots")


@router.post("/snapshots")
def snapshot_take(request: Request, database=Depends(auth.get_db), person=Depends(auth.require_admin),
                  name: str = Form(""), note: str = Form("")):
    def go():
        snap = snapshots.take(database, name, note, person)
        return f"Snapshot saved: “{snap.name}”, {snap.job_count} jobs."
    return _snapshot_action(request, go)


@router.post("/snapshots/{snapshot_id}/restore")
def snapshot_restore(snapshot_id: int, request: Request, database=Depends(auth.get_db), person=Depends(auth.require_admin)):
    def go():
        r = snapshots.restore(database, snapshot_id, person)
        return (f"Restored. {r['updated']} jobs put back as they were"
                + (f", {r['brought_back']} brought back" if r["brought_back"] else "")
                + (f", {r['binned']} newer jobs moved to the bin" if r["binned"] else "")
                + (f", {r['kept']} newer jobs someone is tracking left on the Board" if r["kept"] else "")
                + f". To undo this, restore “{r['safety']}”. Publish to send it to the live site.")
    return _snapshot_action(request, go)


@router.post("/snapshots/{snapshot_id}/delete")
def snapshot_delete(snapshot_id: int, request: Request, database=Depends(auth.get_db)):
    return _snapshot_action(request, lambda: snapshots.delete(database, snapshot_id), done="Snapshot deleted.")


@router.get("/snapshots/{snapshot_id}/download")
def snapshot_download(snapshot_id: int, database=Depends(auth.get_db)):
    snap = database.get(db.BoardSnapshot, snapshot_id)
    if snap is None:
        raise HTTPException(status_code=404, detail="That snapshot no longer exists.")
    return Response(snap.data, media_type="application/gzip",
                    headers={"Content-Disposition": f'attachment; filename="job-board-snapshot-{snap.id}.json.gz"'})


# ---------- the refresher ----------

@router.get("/refresher")
def refresher_page():
    return web.redirect("/admin/refresh")


@router.get("/refresh")
def refresh_page(request: Request, database=Depends(auth.get_db), person=Depends(auth.require_admin)):
    disabled = ingest.disabled_sources(database)
    can_fetch = bool(sources.plan()[0])
    picked, skipped = sources.plan(disabled)
    source_rows = sources.status(disabled)
    if not can_fetch:  # the fetching happens on a refresher elsewhere, so every site can be switched on or off here
        source_rows = [{**r, "reason": ""} for r in source_rows]
    return web.page(request, "admin_refresh.html", person, "refresh", cards=summary.refresher_rows(database),
                    schedule=refreshers.schedule(database), expected=refreshers.VERSION, new_refresher=request.session.pop("new_refresher", None),
                    runs=runlog.recent(database, 8), can_fetch=can_fetch, picked=picked, source_rows=source_rows, stages=stage_status(database),
                    backends=[ai.backend_label(b) for b in (ai.load_tagging_order() and ai.tagging_backends())],
                    order=ai.tagging_order(), choices=[(b, ai.backend_label(b), ai.backend_ready(b)) for b in ai.ALL_BACKENDS],
                    state=dict(ingest.STATE), prog=ingest.progress(), notes=runlog.attention(database, web.today()))


@router.post("/refresher")
def refresher_add(request: Request, database=Depends(auth.get_db), name: str = Form("")):
    name = name.strip()[:60]
    if not name:
        web.flash(request, "Give the computer a name, for example “Optiplex at home”.", "bad")
    else:
        _, token = refreshers.create(database, name)
        # Shown once on the next page, then gone: only a hash of the secret is kept.
        request.session["new_refresher"] = {"name": name, "code": refreshers.connection_code(str(request.base_url), token, name)}
    return web.redirect("/admin/refresh")


@router.post("/refresher/schedule")
def refresher_schedule(request: Request, database=Depends(auth.get_db), time: str = Form(""), enabled: str = Form("")):
    when = refreshers.valid_time(time)
    if when is None:
        web.flash(request, "Please give the time like 07:30.", "bad")
    else:
        refreshers.save_schedule(database, when, bool(enabled))
        web.flash(request, f"Saved. The refresher will run every morning at {when} (UK time)." if enabled
                  else "Saved. The morning refresh is off; you can still use Run now.")
    return web.redirect("/admin/refresh")


def _refresher(database, refresher_id):
    row = database.get(db.Refresher, refresher_id)
    if row is None:
        raise HTTPException(status_code=404, detail="We couldn't find that refresher.")
    return row


@router.post("/refresher/{refresher_id}/run")
def refresher_run(refresher_id: int, request: Request, database=Depends(auth.get_db)):
    row = _refresher(database, refresher_id)
    if row.state == "running":
        web.flash(request, f"{row.name} is already refreshing.", "bad")
    else:
        row.pending_command = "run_now"
        database.commit()
        web.flash(request, f"Asked {row.name} to refresh now. It picks this up within a minute.")
    return web.redirect("/admin/refresh")


@router.post("/refresher/{refresher_id}/stop")
def refresher_stop(refresher_id: int, request: Request, database=Depends(auth.get_db)):
    row = _refresher(database, refresher_id)
    row.pending_command = "stop"
    database.commit()
    web.flash(request, f"Asked {row.name} to stop after its current step. Nothing found so far is lost.")
    return web.redirect("/admin/refresh")


@router.post("/refresher/{refresher_id}/remove")
def refresher_remove(refresher_id: int, request: Request, database=Depends(auth.get_db)):
    row = _refresher(database, refresher_id)
    name = row.name
    database.delete(row)
    database.commit()
    web.flash(request, f"Removed {name}. Its secret no longer works, so it can't report or publish any more.")
    return web.redirect("/admin/refresh")


# ---------- jobs sync ----------

def stage_status(database):
    """For each stage: (can it run right now, and if not, why). The page shows the reason under the disabled button."""
    on_laptop = bool(sources.plan()[0])  # the fetching tools or a key exist on this machine at all
    elsewhere = "Refreshing only works when Job Buddy is running on your laptop with the fetching tools installed."
    out = {}
    if not on_laptop:
        reason = {"scrape": elsewhere, "liveness": elsewhere, "publish": elsewhere}
    else:
        disabled = ingest.disabled_sources(database)
        reason = {"scrape": "", "liveness": "", "publish": ""}
        if not sources.plan(disabled)[0]:
            reason["scrape"] = "Every source is turned off. Tick at least one under Sources."
        elif not ingest._plan(ingest.profiles(database)):
            reason["scrape"] = "Add job titles or keywords to someone's search first."
        if not push.configured():
            reason["publish"] = ("Not needed here, or not set up: it needs INGEST_URL and INGEST_TOKEN in .env. Without them this laptop "
                                 "writes straight to the live database, so there is nothing to publish.")
    for stage in ingest.STAGES:
        out[stage] = (not reason[stage], reason[stage])
    return out


@router.get("/sync")
def sync_page():
    return web.redirect("/admin/refresh")


@router.get("/searches")
def searches_page(request: Request, database=Depends(auth.get_db), person=Depends(auth.require_admin), who: int = 0):
    profiles = ingest.profiles(database)
    return web.page(request, "admin_searches.html", person, "searches", profiles=profiles, open_id=who or (profiles[0].person_id if profiles else 0))


@router.get("/settings")
def settings_page(request: Request, database=Depends(auth.get_db), person=Depends(auth.require_admin)):
    return web.page(request, "admin_settings.html", person, "settings", signups_open=auth.signups_open(database))


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
    stale = len(jobbin.candidates(database, web.today())["irrelevant"])
    web.flash(request, f"Saved {profile.person.first_name}'s search."
              + (f" {stale} job{'' if stale == 1 else 's'} on the Job Board {'was' if stale == 1 else 'were'} found only by keywords that are no longer "
                 "in anyone's search. Open the Bin to move them there." if stale else ""))
    return web.redirect(f"/admin/searches?who={person_id}")


@router.post("/sync/tagging")
def sync_tagging(request: Request, pick0: str = Form(""), pick1: str = Form(""), pick2: str = Form("")):
    ai.save_tagging_order([pick0, pick1, pick2])
    web.flash(request, "Tagging order saved." if ai.tagging_order() else "Saved: jobs will be tagged by keywords only.")
    return web.redirect("/admin/refresh")


@router.post("/sync/sources")
async def sync_sources(request: Request, database=Depends(auth.get_db)):
    """The tick-boxes: which sources the next scrape searches. Unavailable ones keep their setting."""
    form = await request.form()
    on = ingest.save_sources(database, [str(v) for v in form.getlist("source")], any_source=not sources.plan()[0])
    web.flash(request, ("Sources saved: " + ", ".join(sources.LABELS.get(n, n) for n in on) + ".") if on
              else "Sources saved: none are ticked, so a scrape cannot run until you tick one.")
    return web.redirect("/admin/refresh")


def _run(request, database, person, stages):
    """Start the given stages in the background, or say plainly why not."""
    status = stage_status(database)
    # In a full refresh, publishing is simply skipped when it is not set up, as it always has been.
    needed = [st for st in stages if not (st == "publish" and len(stages) > 1)]
    blocked = next((status[st][1] for st in needed if not status[st][0]), "")
    if blocked:
        web.flash(request, blocked, "bad")
    elif not (ingest.start(person.id) if tuple(stages) == ingest.STAGES else ingest.start(person.id, tuple(stages))):
        web.flash(request, "A refresh is already running.", "bad")
    return web.redirect("/admin/refresh")


@router.post("/sync/refresh")
def sync_refresh(request: Request, database=Depends(auth.get_db), person=Depends(auth.require_admin)):
    return _run(request, database, person, ingest.STAGES)


@router.post("/sync/run/{stage}")
def sync_run(stage: str, request: Request, database=Depends(auth.get_db), person=Depends(auth.require_admin)):
    """Run one stage on its own: scrape, liveness or publish."""
    if stage not in ingest.STAGES:
        raise HTTPException(status_code=404, detail="That isn't a step of the refresh.")
    return _run(request, database, person, (stage,))


@router.post("/sync/stop")
def sync_stop(request: Request):
    ingest.stop()
    web.flash(request, "Stopping after the current step. Nothing is lost; press Refresh to carry on.")
    return web.redirect("/admin/refresh")
