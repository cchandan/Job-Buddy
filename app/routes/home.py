"""The user landing page. Admins land on Admin home instead."""
from datetime import datetime

from fastapi import APIRouter, Depends, Request

from .. import auth, summary, web

router = APIRouter()


def greeting(now=None):
    hour = (now or datetime.now()).hour
    return "Good morning" if hour < 12 else "Good afternoon" if hour < 18 else "Good evening"


@router.get("/")
def home(request: Request, database=Depends(auth.get_db), person=Depends(auth.current_person)):
    if person.is_admin:
        return web.redirect("/admin")
    data = summary.user_home(database, person, web.today())
    return web.page(request, "home.html", person, "home", greeting=greeting(), **data)
