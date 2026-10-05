"""Sign in, sign out, change password."""
from fastapi import APIRouter, Depends, Form, Request

from .. import auth, db, web

router = APIRouter()


def _signed_in(request, database):
    pid = request.session.get("pid")
    person = database.get(db.Person, pid) if pid else None
    return person if person and person.session_version == request.session.get("sv") else None


@router.get("/login")
def login_page(request: Request, database=Depends(auth.get_db), next: str = ""):
    if _signed_in(request, database):
        return web.redirect("/")
    return web.page(request, "login.html", None, next=web.safe_next(next, ""), error="", username="")


@router.post("/login")
def login(request: Request, database=Depends(auth.get_db), username: str = Form(""), password: str = Form(""),
          next: str = Form("")):
    person, problem = auth.sign_in(database, username[:60], password[:200])
    if person is None:
        error = ("Too many attempts. Try again in a few minutes." if problem == "locked"
                 else "That username or password isn't right.")
        return web.page(request, "login.html", None, status_code=401, next=web.safe_next(next, ""), error=error,
                        username=username[:60])
    auth.start_session(request, person)
    return web.redirect(web.safe_next(next, "/"))


@router.post("/logout")
def logout(request: Request):
    request.session.clear()
    return web.redirect("/login")


@router.post("/profile/password")
def change_password(request: Request, database=Depends(auth.get_db), person=Depends(auth.current_person),
                    current: str = Form(""), new: str = Form(""), again: str = Form("")):
    if not auth.check_password(current, person.password_hash):
        web.flash(request, "Your current password isn't right, so nothing was changed.", "bad")
    elif len(new) < 10:
        web.flash(request, "Please choose a new password of at least 10 characters.", "bad")
    elif new != again:
        web.flash(request, "The two new passwords didn't match, so nothing was changed.", "bad")
    else:
        auth.set_password(person, new[:200])
        database.commit()
        auth.start_session(request, person)  # keep this browser signed in; every other one is signed out
        web.flash(request, "Your password has been changed.")
    return web.redirect("/admin" if person.is_admin else "/profile")
