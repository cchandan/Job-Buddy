"""Web routes: one plain function per page, plus the health check.

Routes are plain `def` (not async) so a slow Gemma call does not freeze the app for other visitors.
"""
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlencode

from fastapi import Depends, FastAPI, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import cv_pdf, db, gaps, gemma, profile, projects, ranking, sessions

log = logging.getLogger("careeros")
HERE = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(HERE / "templates"))
MAX_BODY = 2 * 1024 * 1024  # ~2 MB
JOBS_SHOWN = 20        # best unblocked jobs shown by default
BLOCKED_SHOWN = 5      # plus a few blocked ones at the bottom, greyed, with reason and quote
STATUSES = ("Interested", "Started", "Completed")


@asynccontextmanager
async def lifespan(app):
    db.init_db()
    yield


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
app.mount("/static", StaticFiles(directory=str(HERE / "static")), name="static")


def get_db():
    with db.SessionLocal() as s:
        yield s


# ---------- friendly errors: never a raw error page ----------

def friendly(request, title, message, status=200, retry=True):
    return templates.TemplateResponse(
        request, "error.html", {"title": title, "message": message, "retry": retry, "active": ""}, status_code=status)


@app.middleware("http")
async def limit_body_size(request: Request, call_next):
    if int(request.headers.get("content-length") or 0) > MAX_BODY:
        return friendly(request, "That's too large", "Please keep your CV under about 2 MB.", 413,
                        retry=False)
    return await call_next(request)


@app.exception_handler(StarletteHTTPException)
async def http_error(request, exc):
    if exc.status_code == 404:
        return friendly(request, "Page not found", "We couldn't find that page.", 404, retry=False)
    return friendly(request, "Something went wrong", "Please try again in a moment.", exc.status_code)


@app.exception_handler(Exception)
async def unexpected_error(request, exc):
    log.error("Unhandled error on %s: %s", request.url.path, type(exc).__name__)  # never log request content
    return friendly(request, "Something went wrong", "Sorry, that didn't work. Please try again in a moment.", 500)


@app.get("/health")
def health():
    return {"status": "ok"}  # deliberately touches neither the database nor Gemma


# ---------- helpers ----------

def page(request, name, visitor, active, **ctx):
    return templates.TemplateResponse(request, name, {"active": active, "visitor": visitor, **ctx})


def all_jobs(database):
    return [db.job_to_dict(j) for j in database.query(db.Job).all()]


def analysis_view(database, visitor):
    """Ranked jobs, gaps and strong skills for this visitor. Computed fresh each time; no AI involved."""
    prof = profile.to_ranking_profile(visitor)
    ranked = ranking.rank(prof, all_jobs(database))
    return prof, ranked, gaps.top_gaps(prof, ranked), gaps.strong_skills(prof, ranked)


def suggestable(ranked):
    """Jobs we put in front of the visitor. Senior roles stay in the pool (they shape project ideas) but are
    never suggested to a graduate."""
    return [r for r in ranked if r.job.get("seniority") != "senior"]


def need_profile(request, database):
    """(visitor, redirect). Visitors without a profile are sent to onboarding."""
    visitor = sessions.get_visitor(database, request)
    if visitor is None or not visitor.profile_source:
        return None, RedirectResponse("/onboarding", status_code=303)
    return visitor, None


def with_cookie(response, request, sid):
    sessions.set_cookie(response, request, sid)
    return response


# ---------- onboarding & profile ----------

@app.get("/", response_class=HTMLResponse)
def home(request: Request, database=Depends(get_db)):
    visitor = sessions.get_visitor(database, request)
    if visitor and visitor.profile_source:
        return RedirectResponse("/this-week", status_code=303)
    return templates.TemplateResponse(request, "landing.html", {})


@app.get("/onboarding", response_class=HTMLResponse)
def onboarding(request: Request, database=Depends(get_db), error: str = ""):
    return page(request, "onboarding.html", sessions.get_visitor(database, request), "", error=error[:200],
                ai=gemma.available(), max_dream=profile.MAX_DREAM_JOBS, form={})


@app.post("/onboarding")
def onboarding_submit(request: Request, database=Depends(get_db), cv_text: str = Form(""),
                      cv_file: UploadFile | None = File(None),
                      dj_title: list[str] = Form([]), dj_company: list[str] = Form([]),
                      dj_url: list[str] = Form([]), dj_description: list[str] = Form([]),
                      locations: str = Form(""), min_salary: str = Form(""), needs_sponsorship: str = Form(""),
                      industries: str = Form(""), technologies: str = Form("")):
    pdf_problem = ""
    if cv_file is not None and cv_file.filename:  # an uploaded PDF wins over pasted text; it is never saved
        try:
            cv_text = cv_pdf.extract_text(cv_file.file.read(MAX_BODY))
        except ValueError as e:
            pdf_problem = str(e)
    form = {"cv_text": cv_text, "locations": locations, "min_salary": min_salary, "industries": industries,
            "technologies": technologies, "needs_sponsorship": needs_sponsorship}
    visitor = sessions.get_visitor(database, request)

    def again(message):
        return page(request, "onboarding.html", visitor, "", error=message, ai=gemma.available(),
                    max_dream=profile.MAX_DREAM_JOBS, form=form,
                    dreams=list(zip(dj_title, dj_company, dj_url, dj_description)))

    if pdf_problem:
        return again(pdf_problem)
    if len(cv_text.strip()) < 40:
        return again("Please upload a PDF or paste your CV text (at least a few lines) so we can build your profile.")
    rows = [{"title": t, "company": c, "url": u, "description": d}
            for t, c, u, d in zip(dj_title, dj_company, dj_url, dj_description)]
    try:
        dreams = profile.clean_dream_jobs(rows)
    except ValueError as e:
        return again(str(e))
    if not dreams:
        return again("Please add at least one dream job: a title is enough.")

    sid = visitor.session_id if visitor else sessions.new_session_id()
    if visitor is None:
        visitor = db.Visitor(session_id=sid)
        database.add(visitor)
        database.commit()  # the row must exist so Gemma call limits can count against it

    notice = ""
    try:
        built = profile.build_profile(cv_text, dreams, all_jobs(database), sid)
    except gemma.GemmaLimitReached:
        built = profile.basic_profile(cv_text[:profile.MAX_CV_CHARS], dreams, all_jobs(database))
        notice = "limit"
    if built["source"] == "basic" and not notice:
        notice = "basic"

    visitor.skills, visitor.level, visitor.domains = built["skills"], built["level"], built["domains"]
    visitor.target_roles, visitor.summary, visitor.profile_source = built["target_roles"], built["summary"], built["source"]
    visitor.preferences = profile.parse_preferences(form)
    visitor.dream_jobs = [{"title": d["title"], "company": d["company"], "url": d["url"], "skills": built["dream_skills"][i]}
                          for i, d in enumerate(dreams)]  # descriptions are dropped here, never stored
    visitor.analysis, visitor.project_status = None, {}
    database.commit()
    response = RedirectResponse("/profile" + (f"?notice={notice}" if notice else ""), status_code=303)
    return with_cookie(response, request, sid)


@app.get("/profile", response_class=HTMLResponse)
def profile_page(request: Request, database=Depends(get_db), notice: str = "", saved: str = ""):
    visitor, redirect = need_profile(request, database)
    if redirect:
        return redirect
    prof = profile.to_ranking_profile(visitor)
    shared = sorted(prof["dream_skills"].items(), key=lambda kv: (-kv[1], kv[0]))
    shared = [(s, n) for s, n in shared if n >= 2] or shared[:6]
    return page(request, "profile.html", visitor, "profile", notice=notice, saved=bool(saved),
                shared=shared, levels=profile.LEVELS)


@app.post("/profile/edit")
def profile_edit(request: Request, database=Depends(get_db), skills_text: str = Form(""), level: str = Form("graduate"),
                 add_skill: str = Form("")):
    visitor, redirect = need_profile(request, database)
    if redirect:
        return redirect
    from . import skills
    names = [p for p in skills_text.replace("\n", ",").split(",")] + [add_skill]
    visitor.skills = skills.canonical_list(names, limit=60)
    visitor.level = level if level in profile.LEVELS else visitor.level
    visitor.analysis = None  # the saved analysis no longer matches: regenerate
    database.commit()
    return RedirectResponse("/profile?saved=1", status_code=303)


@app.post("/analyse")
def analyse(request: Request, database=Depends(get_db), next: str = Form("/this-week")):
    """Gemma call B: gap explanations + projects. Falls back to showing the page without them."""
    visitor, redirect = need_profile(request, database)
    if redirect:
        return redirect
    target = next if next in ("/this-week", "/projects", "/skill-gaps") else "/this-week"
    prof, ranked, gap_list, _ = analysis_view(database, visitor)
    problem = ""
    if not gap_list:
        problem = "nogaps"
    elif not gemma.available():
        problem = "unavailable"
    else:
        try:
            senior = gaps.senior_skills(prof, ranked, exclude=[g["skill"] for g in gap_list[:projects.TOP_N]])
            visitor.analysis = projects.generate(prof, gap_list, visitor.session_id, senior)
            database.commit()
        except gemma.GemmaLimitReached:
            problem = "limit"
        except gemma.GemmaUnavailable:
            problem = "unavailable"
    sep = "&" if "?" in target else "?"
    return RedirectResponse(target + (f"{sep}{urlencode({'ai': problem})}" if problem else ""), status_code=303)


@app.post("/delete")
def delete_my_data(request: Request, database=Depends(get_db)):
    response = RedirectResponse("/deleted", status_code=303)
    sessions.delete_visitor(database, request, response)
    return response


@app.get("/deleted", response_class=HTMLResponse)
def deleted(request: Request):
    return page(request, "deleted.html", None, "")


# ---------- the main pages ----------

def ai_message(code):
    return {
        "unavailable": "The AI helper isn't available right now, so the written explanations and project ideas are "
                       "missing. Your jobs and skill gaps below are unaffected.",
        "limit": "The AI helper has reached its usage limit for now. Your jobs and skill gaps are unaffected; "
                 "please try again later.",
        "nogaps": "No skill gaps found: you already have the skills these jobs ask for.",
    }.get(code, "")


@app.get("/this-week", response_class=HTMLResponse)
def this_week(request: Request, database=Depends(get_db), ai: str = ""):
    visitor, redirect = need_profile(request, database)
    if redirect:
        return redirect
    prof, ranked, gap_list, _ = analysis_view(database, visitor)
    top_jobs = [r for r in suggestable(ranked) if not r.blocked][:3]
    project = (visitor.analysis or {}).get("projects", [None])[0] if visitor.analysis else None
    top_skills = {g["skill"] for g in gap_list[:5]}
    helped = len(top_skills & set(project["skills_demonstrated"])) if project else 0
    return page(request, "this_week.html", visitor, "this-week", top_jobs=top_jobs, gaps=gap_list[:5],
                project=project, helped=helped, ai_note=ai_message(ai))


@app.get("/jobs", response_class=HTMLResponse)
def jobs_page(request: Request, database=Depends(get_db), sponsorship: str = "", location: str = "",
              show: str = ""):
    visitor, redirect = need_profile(request, database)
    if redirect:
        return redirect
    prof, ranked, _, _ = analysis_view(database, visitor)
    ranked = suggestable(ranked)
    locations = sorted({(r.job["location"] or "").split(",")[0].strip() for r in ranked if r.job["location"]})
    shown = ranked
    if sponsorship in ("sponsors", "unclear", "no_sponsorship"):
        shown = [r for r in shown if r.job["sponsorship"] == sponsorship]
    if location:
        shown = [r for r in shown if location.lower() in (r.job["location"] or "").lower()]
    total = len(shown)
    if show != "all":
        shown = [r for r in shown if not r.blocked][:JOBS_SHOWN] + [r for r in shown if r.blocked][:BLOCKED_SHOWN]
    return page(request, "jobs.html", visitor, "jobs", ranked=shown, total=total, locations=locations,
                sponsorship=sponsorship, location=location, show_all=show == "all",
                unblocked=sum(1 for r in shown if not r.blocked))


@app.get("/skill-gaps", response_class=HTMLResponse)
def skill_gaps(request: Request, database=Depends(get_db), ai: str = ""):
    visitor, redirect = need_profile(request, database)
    if redirect:
        return redirect
    prof, ranked, gap_list, strong = analysis_view(database, visitor)
    analysis = visitor.analysis or {}
    related = {}
    for g in gap_list[:5]:
        related[g["skill"]] = [p["title"] for p in analysis.get("projects", []) if g["skill"] in p["skills_demonstrated"]]
    return page(request, "skill_gaps.html", visitor, "skill-gaps", gaps=gap_list[:8], strong=strong,
                explanations=analysis.get("explanations", {}), related=related, has_analysis=bool(analysis),
                ai_note=ai_message(ai), total_relevant=gap_list[0]["total_relevant"] if gap_list else 0)


@app.get("/projects", response_class=HTMLResponse)
def project_ideas(request: Request, database=Depends(get_db), ai: str = ""):
    visitor, redirect = need_profile(request, database)
    if redirect:
        return redirect
    analysis = visitor.analysis or {}
    return page(request, "projects.html", visitor, "projects", projects=analysis.get("projects", []),
                top_gaps=analysis.get("gap_skills", []), statuses=STATUSES, status=visitor.project_status or {},
                ai_note=ai_message(ai))


@app.post("/projects/status")
def project_status(request: Request, database=Depends(get_db), title: str = Form(""), status: str = Form("")):
    visitor, redirect = need_profile(request, database)
    if redirect:
        return redirect
    known = {p["title"] for p in (visitor.analysis or {}).get("projects", [])}
    if title in known and status in STATUSES:
        visitor.project_status = {**(visitor.project_status or {}), title: status}
        database.commit()
    return RedirectResponse("/projects", status_code=303)


@app.get("/calendar", response_class=HTMLResponse)
def calendar(request: Request, database=Depends(get_db)):
    return page(request, "calendar.html", sessions.get_visitor(database, request), "calendar")


@app.get("/network", response_class=HTMLResponse)
def network(request: Request, database=Depends(get_db)):
    return page(request, "network.html", sessions.get_visitor(database, request), "network")
