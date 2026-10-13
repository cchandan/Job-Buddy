"""Profile and the core CV."""
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import Response

from .. import auth, cv_pdf, db, notify, web
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
    notify.admins(database, person, f"{person.first_name} uploaded their CV", "", f"/admin/users/{person.id}/profile", tag=f"cv-{person.id}")
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
        notify.admins(database, person, f"{person.first_name} deleted their CV", "", f"/admin/users/{person.id}/profile", tag=f"cv-{person.id}")
        web.flash(request, "Your core CV has been deleted.")
    return web.redirect("/profile")
