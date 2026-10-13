"""Triage: one job at a time, best match first. Shortlist sends it to the Tracker; Skip remembers why."""
from fastapi import APIRouter, Depends, Form, HTTPException, Request

from .. import auth, db, deadlines, notify, tracking, triage, web
from .tracker import users_only

router = APIRouter()


def _job(database, job_id):
    job = database.get(db.Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="We couldn't find that job. It may have been removed.")
    return job


@router.get("/triage")
def triage_page(request: Request, database=Depends(auth.get_db), person=Depends(users_only)):
    today = web.today()
    cards = triage.deck(database, person, today)
    profile = database.get(db.SearchProfile, person.id)
    first = cards[0] if cards else None
    return web.page(request, "triage.html", person, "triage", card=first, upnext=cards[1:6], remaining=len(cards),
                    deadline_chip=deadlines.chip(first.deadline, today) if first else None,
                    why=triage.why(first, person, profile, today) if first else "", reasons=triage.REASONS,
                    counts=triage.today_counts(database, person, today), rules=[(r, triage.rule_label(r)) for r in triage.rules_for(database, person)],
                    last=request.session.get("triage_last"), skipped_count=len(triage.skipped(database, person)))


@router.post("/triage/{job_id}/shortlist")
def shortlist(job_id: str, request: Request, database=Depends(auth.get_db), person=Depends(users_only)):
    job = _job(database, job_id)
    try:
        entry, created = tracking.add_from_board(database, person, job, person)
    except tracking.TrackerFull:
        web.flash(request, "Your Tracker is full. Remove some old jobs first.", "bad")
        return web.redirect("/triage")
    triage.record(database, person, job, "shortlist")
    if created:
        notify.tracker_change(database, person, entry, "added a job to their Tracker")
    request.session["triage_last"] = {"job": job.id, "kind": "shortlist", "title": job.title, "made": created}
    return web.redirect("/triage")


@router.post("/triage/{job_id}/skip")
def skip(job_id: str, request: Request, database=Depends(auth.get_db), person=Depends(users_only), reason: str = Form("")):
    job = _job(database, job_id)
    row = triage.record(database, person, job, "skip", reason)
    request.session["triage_last"] = {"job": job.id, "kind": "skip", "title": job.title, "taught": triage.rule_label(row.rule) if row.rule else ""}
    return web.redirect("/triage")


@router.post("/triage/undo")
def undo(request: Request, database=Depends(auth.get_db), person=Depends(users_only)):
    """Take back the last swipe. A shortlisted job leaves the Tracker again only if it was added by that swipe and not touched since."""
    last = request.session.pop("triage_last", None)
    if last:
        row = database.query(db.JobDecision).filter(db.JobDecision.person_id == person.id, db.JobDecision.job_id == last["job"]).first()
        if row is not None:
            database.delete(row)
        if last["kind"] == "shortlist" and last.get("made"):
            entry = (database.query(db.TrackerEntry).filter(db.TrackerEntry.person_id == person.id, db.TrackerEntry.job_id == last["job"]).first())
            if entry is not None and entry.status == "shortlisted" and not entry.notes and entry.apply_clicked_at is None:
                database.delete(entry)
        database.commit()
        web.flash(request, f"Undone: {last['title']} is back in your deck.")
    return web.redirect("/triage")


@router.get("/triage/skipped")
def skipped_page(request: Request, database=Depends(auth.get_db), person=Depends(users_only)):
    return web.page(request, "triage_skipped.html", person, "triage", rows=triage.skipped(database, person), labels=triage.REASON_LABELS,
                    rules=[(r, triage.rule_label(r)) for r in triage.rules_for(database, person)])


@router.post("/triage/skipped/{job_id}/restore")
def restore(job_id: str, request: Request, database=Depends(auth.get_db), person=Depends(users_only)):
    row = database.query(db.JobDecision).filter(db.JobDecision.person_id == person.id, db.JobDecision.job_id == job_id,
                                                db.JobDecision.decision == "skip").first()
    if row is not None:
        database.delete(row)
        database.commit()
        web.flash(request, "It is back in your Triage deck.")
    return web.redirect("/triage/skipped")


@router.post("/triage/rules/forget")
def forget(request: Request, database=Depends(auth.get_db), person=Depends(users_only), rule: str = Form(""), next: str = Form("")):
    database.query(db.JobDecision).filter(db.JobDecision.person_id == person.id, db.JobDecision.rule == rule).update({"rule": ""})
    database.commit()
    web.flash(request, "Forgotten. Jobs like that no longer rank lower.")
    return web.redirect(web.safe_next(next, "/triage/skipped"))
