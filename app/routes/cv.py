"""Profile, the core CV, and tailored CVs."""
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import Response

from .. import ai, auth, cv_pdf, db, summary, tailoring, web
from .tracker import users_only

router = APIRouter()
MAX_CV_BYTES = 2_000_000
MAX_CV_TEXT = 30_000
MIN_CV_TEXT = 80


@router.get("/profile")
def profile(request: Request, database=Depends(auth.get_db), person=Depends(users_only)):
    return web.page(request, "profile.html", person, "profile", owner=person, readonly=False,
                    cv=database.get(db.CV, person.id), base="/profile")


@router.post("/profile/cv")
async def upload_cv(request: Request, database=Depends(auth.get_db), person=Depends(users_only),
                    cv_file: UploadFile | None = File(None), cv_text: str = Form("")):
    filename, data, text = "", None, cv_text.strip()
    if cv_file is not None and cv_file.filename:
        data = await cv_file.read(MAX_CV_BYTES + 1)
        if len(data) > MAX_CV_BYTES:
            web.flash(request, "That file is larger than 2 MB. Please upload a smaller PDF, or paste the text.", "bad")
            return web.redirect("/profile")
        try:
            text = cv_pdf.extract_text(data)
        except ValueError as e:
            web.flash(request, str(e), "bad")
            return web.redirect("/profile")
        filename = cv_file.filename[:200]
    if len(text) < MIN_CV_TEXT:
        web.flash(request, "Please choose a PDF, or paste the text of your CV.", "bad")
        return web.redirect("/profile")
    row = database.get(db.CV, person.id)
    if row is None:
        row = db.CV(person_id=person.id)
        database.add(row)
    row.filename, row.data, row.text, row.uploaded_at = filename, data, text[:MAX_CV_TEXT], db.utcnow()
    database.commit()
    web.flash(request, "Your core CV has been saved.")
    return web.redirect("/profile")


def cv_file_response(row):
    if row is None:
        raise HTTPException(status_code=404, detail="There is no CV to download yet.")
    if row.data:
        return Response(row.data, media_type="application/pdf",
                        headers={"Content-Disposition": 'attachment; filename="core-cv.pdf"'})
    return Response(row.text, media_type="text/plain; charset=utf-8",
                    headers={"Content-Disposition": 'attachment; filename="core-cv.txt"'})


@router.get("/profile/cv/download")
def download_cv(database=Depends(auth.get_db), person=Depends(users_only)):
    return cv_file_response(database.get(db.CV, person.id))


@router.post("/profile/cv/delete")
def delete_cv(request: Request, database=Depends(auth.get_db), person=Depends(users_only)):
    row = database.get(db.CV, person.id)
    if row is not None:
        database.delete(row)
        database.commit()
        web.flash(request, "Your core CV has been deleted. Tailored CVs you already made are kept.")
    return web.redirect("/profile")


# ---------- tailored CVs ----------

def blocked_reason(core, job):
    if core is None:
        return "cv"
    if len((job.description or "").strip()) < 80:
        return "description"
    return ""


@router.get("/tracker/{entry_id}/cv")
def tailored_page(entry_id: int, request: Request, database=Depends(auth.get_db), person=Depends(users_only)):
    entry = auth.own_entry(database, person, entry_id)
    v = summary.entry_view(entry, web.today())
    core = database.get(db.CV, person.id)
    response = web.page(request, "tailored.html", person, "tracker", v=v, core=core, tailored=entry.tailored,
                        blocked=blocked_reason(core, v["job"]), ai_ready=ai.available())
    if entry.tailored is not None and not entry.tailored.review_seen:
        entry.tailored.review_seen = True  # the review outcome has now been seen
        database.commit()
    return response


@router.post("/tracker/{entry_id}/cv/create")
def create_tailored(entry_id: int, request: Request, database=Depends(auth.get_db), person=Depends(users_only)):
    """Create or regenerate. Regenerating replaces the text, so the page asks first."""
    entry = auth.own_entry(database, person, entry_id)
    job, core = db.entry_job(entry), database.get(db.CV, person.id)
    if blocked_reason(core, job):
        return web.redirect(f"/tracker/{entry.id}/cv")
    try:
        text = tailoring.tailor(core.text, job)
    except ai.AILimitReached:
        web.flash(request, "Today's limit for AI help has been reached. Please try again tomorrow.", "bad")
        return web.redirect(f"/tracker/{entry.id}/cv")
    except ai.AIUnavailable:
        web.flash(request, "The AI service isn't available right now. Try again in a few minutes.", "bad")
        return web.redirect(f"/tracker/{entry.id}/cv")
    row = entry.tailored
    if row is None:
        row = db.TailoredCV(entry_id=entry.id)
        database.add(row)
    row.text, row.status, row.updated_at, row.review_seen = text[:MAX_CV_TEXT], "draft", db.utcnow(), True
    database.commit()
    web.flash(request, "Your tailored CV is ready. Check every line before you use it.")
    return web.redirect(f"/tracker/{entry.id}/cv")


def _tailored(database, person, entry_id):
    entry = auth.own_entry(database, person, entry_id)
    if entry.tailored is None:
        raise HTTPException(status_code=404, detail="There is no tailored CV for this job yet.")
    return entry, entry.tailored


@router.post("/tracker/{entry_id}/cv/save")
def save_tailored(entry_id: int, request: Request, database=Depends(auth.get_db), person=Depends(users_only),
                  text: str = Form(""), then: str = Form("")):
    entry, row = _tailored(database, person, entry_id)
    text = text.replace("\r\n", "\n").strip()[:MAX_CV_TEXT]
    if len(text) < MIN_CV_TEXT:
        web.flash(request, "The CV can't be empty. Nothing was saved.", "bad")
        return web.redirect(f"/tracker/{entry.id}/cv")
    changed = text != row.text
    row.text, row.updated_at = text, db.utcnow()
    if changed and row.status in ("approved", "in_review"):
        row.status = "draft"  # an edited CV needs review again
    if then == "send":
        row.status, row.sent_at, row.review_comment, row.review_seen = "in_review", db.utcnow(), "", True
        web.flash(request, "Sent for review. You'll see the outcome on your Home page.")
    else:
        web.flash(request, "Saved.")
    database.commit()
    return web.redirect(f"/tracker/{entry.id}/cv")


@router.post("/tracker/{entry_id}/cv/delete")
def delete_tailored(entry_id: int, request: Request, database=Depends(auth.get_db), person=Depends(users_only)):
    entry, row = _tailored(database, person, entry_id)
    database.delete(row)
    database.commit()
    web.flash(request, "The tailored CV has been deleted.")
    return web.redirect(f"/tracker/{entry.id}")


@router.get("/tracker/{entry_id}/cv/print")
def print_tailored(entry_id: int, request: Request, database=Depends(auth.get_db), person=Depends(auth.current_person)):
    """A clean page to save as PDF from the browser. Admins may open it for review."""
    entry = auth.own_entry(database, person, entry_id, allow_admin=True)
    if entry.tailored is None:
        raise HTTPException(status_code=404, detail="There is no tailored CV for this job yet.")
    return web.templates.TemplateResponse(request, "cv_print.html", {"text": entry.tailored.text, "job": db.entry_job(entry)})
