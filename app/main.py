"""Job Buddy: app setup, error pages and the health check. The pages live in app/routes/."""
import logging
import os
import secrets
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.sessions import SessionMiddleware

from . import auth, bootstrap, db, web
from .routes import admin, auth as auth_routes, board, calendar, cv, home, tracker

log = logging.getLogger("jobbuddy")


@asynccontextmanager
async def lifespan(app):
    db.init_db()
    bootstrap.ensure_admin()
    yield


app = FastAPI(title="Job Buddy", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan,
              dependencies=[Depends(auth.check_csrf)])
app.mount("/static", StaticFiles(directory=str(web.HERE / "static")), name="static")

_secret = os.getenv("SESSION_SECRET")
if not _secret:
    _secret = secrets.token_urlsafe(32)  # fine for local play; everyone is signed out on each restart
    log.warning("SESSION_SECRET is not set: using a temporary one.")
ON_HOST = bool(os.getenv("RENDER") or os.getenv("COOKIE_SECURE"))


@app.middleware("http")
async def private_pages(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Robots-Tag"] = "noindex, nofollow"  # the app is private; keep it out of search engines
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "same-origin"
    if not request.url.path.startswith("/static"):
        response.headers["Cache-Control"] = "no-store"
    return response


# Added last so it wraps everything above: the session must be readable in the handlers below.
app.add_middleware(SessionMiddleware, secret_key=_secret, session_cookie="jobbuddy", max_age=60 * 60 * 24 * 30,
                   same_site="lax", https_only=ON_HOST)


def _person(request):
    """The signed-in person for an error page, or None. Never raises."""
    try:
        with db.SessionLocal() as s:
            pid = request.session.get("pid")
            person = s.get(db.Person, pid) if pid else None
            return person if person and person.session_version == request.session.get("sv") else None
    except Exception:
        return None


def friendly(request, title, message, status=200, retry=True):
    return web.page(request, "error.html", _person(request), status_code=status, title=title, message=message, retry=retry)


@app.exception_handler(auth.NeedLogin)
async def need_login(request: Request, exc):
    target = request.url.path + (f"?{request.url.query}" if request.url.query else "")
    return web.redirect("/login" if request.method != "GET" or target == "/" else f"/login?next={target}")


@app.exception_handler(StarletteHTTPException)
async def http_error(request: Request, exc):
    if exc.status_code == 404:
        return friendly(request, "Page not found", exc.detail if exc.detail != "Not Found" else "That page doesn't exist.", 404, retry=False)
    if exc.status_code == 403:
        return friendly(request, "Not allowed", exc.detail or "You can't open that page.", 403, retry=False)
    return friendly(request, "Something went wrong", "Please go back and try again.", exc.status_code)


@app.exception_handler(Exception)
async def any_error(request: Request, exc):
    log.error("Unhandled error on %s: %s", request.url.path, type(exc).__name__)  # never the message: it may hold CV text
    return friendly(request, "Something went wrong", "Sorry, that didn't work. Please go back and try again.", 500)


@app.get("/health")
def health():
    """Touches neither the database nor the AI, so the host sees the app as alive if either is down."""
    return JSONResponse({"ok": True})


for module in (auth_routes, home, board, tracker, cv, calendar, admin):
    app.include_router(module.router)
