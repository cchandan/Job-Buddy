"""End-to-end checks through the real web app, with Gemma replaced by a stub."""
import json

import pytest
from fastapi.testclient import TestClient

from app import db, gemma, profile, projects, skills, tagging
from app.main import app

CV = """Jane Doe. BSc Computer Science (final year), University of Bradford.
Projects: built a REST API with Python and Flask backed by PostgreSQL, deployed with Docker.
Built a React.js front end. Uses Git and GitHub daily. Wrote unit tests with pytest."""

FORM = {
    "cv_text": CV, "dj_title": ["Graduate Software Engineer", "Backend Engineer"],
    "dj_company": ["NatWest", ""], "dj_url": ["", ""],
    "dj_description": ["We use Python, AWS, Docker, Kubernetes and SQL. Agile team.", ""],
    "needs_sponsorship": "on",
}


@pytest.fixture()
def client():
    with TestClient(app, follow_redirects=False) as c:
        yield c


def onboard(c, **over):
    return c.post("/onboarding", data={**FORM, **over})


def make_pdf(lines):
    """A minimal one-page PDF holding the given lines of text."""
    text = "BT /F1 11 Tf 40 780 Td 14 TL " + " ".join(
        "(" + ln.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)") + ") Tj T*" for ln in lines) + " ET"
    objs = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents 4 0 R "
        "/Resources << /Font << /F1 5 0 R >> >> >>",
        f"<< /Length {len(text)} >>\nstream\n{text}\nendstream",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out, offsets = "%PDF-1.4\n", []
    for i, body in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n{body}\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n" + "".join(f"{o:010d} 00000 n \n" for o in offsets)
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n"
    return out.encode("latin-1")


def onboard_pdf(c, data, name="cv.pdf", **over):
    form = {k: v for k, v in {**FORM, **over}.items() if k != "cv_text"}
    return c.post("/onboarding", data=form, files={"cv_file": (name, data, "application/pdf")})


# ---------- jobs / startup ----------

def test_health_and_jobs_load_from_snapshot_without_gemma(client):
    assert client.get("/health").json() == {"status": "ok"}
    with db.SessionLocal() as s:
        assert s.query(db.Job).count() >= 30  # AC3


def test_unknown_page_is_friendly_not_raw(client):
    r = client.get("/nope")
    assert r.status_code == 404 and "find that page" in r.text


# ---------- onboarding (AC1, AC2, AC14) ----------

def test_onboarding_without_gemma_builds_basic_profile_and_ranks_jobs(client):
    r = onboard(client)
    assert r.status_code == 303 and r.headers["location"].startswith("/profile")
    page = client.get("/profile")
    assert page.status_code == 200 and "Python" in page.text and "React" in page.text  # "React.js" -> "React"
    jobs = client.get("/jobs")
    assert jobs.status_code == 200 and "Blocked:" in jobs.text  # needs sponsorship -> flagged jobs shown
    assert client.get("/this-week").status_code == 200
    assert client.get("/skill-gaps").status_code == 200
    projects_page = client.get("/projects")
    assert projects_page.status_code == 200 and "Generate project ideas" in projects_page.text
    # reload keeps data
    assert "Python" in client.get("/profile").text


def test_sixth_dream_job_is_rejected(client):
    titles = [f"Job {i}" for i in range(6)]
    r = onboard(client, dj_title=titles, dj_company=[""] * 6, dj_url=[""] * 6, dj_description=[""] * 6)
    assert r.status_code == 200 and "up to 5" in r.text


def test_empty_cv_is_rejected_with_a_friendly_message(client):
    r = onboard(client, cv_text="hi")
    assert r.status_code == 200 and "paste your CV" in r.text


def test_pages_without_a_profile_send_you_to_onboarding(client):
    for path in ("/this-week", "/jobs", "/skill-gaps", "/projects", "/profile"):
        r = client.get(path)
        assert r.status_code == 303 and r.headers["location"] == "/onboarding"


def test_raw_cv_and_job_descriptions_are_never_stored(client):
    onboard(client)
    with db.SessionLocal() as s:
        for v in s.query(db.Visitor).all():
            blob = json.dumps({c.name: getattr(v, c.name) for c in db.Visitor.__table__.columns}, default=str)
            assert "Bradford" not in blob and "Agile team" not in blob


# ---------- isolation & delete (AC12, AC13) ----------

def test_two_browsers_see_only_their_own_data_and_delete_works():
    with TestClient(app, follow_redirects=False) as a, TestClient(app, follow_redirects=False) as b:
        onboard(a, cv_text="Skilled in Rust and Go and Kotlin. Final year graduate student.")
        onboard(b, cv_text="Skilled in Swift and Ruby and PHP. Final year graduate student.")
        pa, pb = a.get("/profile").text, b.get("/profile").text
        assert "Rust" in pa and "Swift" not in pa
        assert "Swift" in pb and "Rust" not in pb
        a.post("/delete")
        assert a.get("/profile").status_code == 303  # profile gone
        assert "Swift" in b.get("/profile").text      # other visitor untouched


# ---------- Gemma paths (stubbed) ----------

def stub_gemma(monkeypatch, answers):
    monkeypatch.setattr(gemma, "available", lambda: True)
    queue = list(answers)

    def fake(prompt):
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item if isinstance(item, str) else json.dumps(item)
    monkeypatch.setattr(gemma, "_call_model", fake)


def test_gemma_profile_is_used_but_invented_cv_skills_are_dropped(client, monkeypatch):
    answer = {"cv_skills": ["ReactJS", "Python", "Haskell"], "level": "graduate", "domains": ["web"],
              "target_roles": ["Backend Engineer"], "summary": "You are a strong graduate.",
              "dream_jobs": [{"index": 0, "skills": ["python", "aws"]}, {"index": 1, "skills": ["Golang"]}]}
    stub_gemma(monkeypatch, ["```json\n" + json.dumps(answer) + "\n```"])  # code fences are tolerated
    onboard(client)
    page = client.get("/profile").text
    assert "You are a strong graduate." in page
    assert "Haskell" not in page  # not in the CV, so never trusted
    assert "React" in page and "ReactJS" not in page


def test_bad_json_twice_falls_back_to_basic_profile(client, monkeypatch):
    stub_gemma(monkeypatch, ["not json", "still not json"])
    onboard(client)
    page = client.get("/profile")
    assert page.status_code == 200 and "Python" in page.text


def top_gap_skills():
    from app.main import analysis_view
    with db.SessionLocal() as s:
        visitor = s.query(db.Visitor).order_by(db.Visitor.created_at.desc()).first()
        return [g["skill"] for g in analysis_view(s, visitor)[2][:5]]


def test_call_b_saves_validated_projects_and_removes_skills_outside_gap_list(client, monkeypatch):
    onboard(client)
    assert "Priority gaps" in client.get("/skill-gaps").text
    gap, other = top_gap_skills()[:2]
    analysis = {
        "gap_explanations": [{"skill": gap.lower(), "why": f"{gap} matters a lot."}, {"skill": "Cobol", "why": "invented"}],
        "projects": [
            {"title": "Real gap project", "description": "Ship a service.", "skills_demonstrated": [gap, "Cobol"],
             "suggested_stack": ["Python"], "scope": "2 weeks", "difficulty": "Intermediate",
             "milestones": ["a", "b", "c"], "why_this_project": "Fits backend roles."},
            {"title": "Only invented skills", "description": "x", "skills_demonstrated": ["Cobol"], "milestones": []},
        ],
    }
    stub_gemma(monkeypatch, [analysis])
    r = client.post("/analyse", data={"next": "/projects"})
    assert r.status_code == 303 and "ai=" not in r.headers["location"]
    page = client.get("/projects").text
    assert "Real gap project" in page and "Only invented skills" not in page and "Cobol" not in page
    assert f"{gap} matters a lot." in client.get("/skill-gaps").text
    assert "Real gap project" in client.get("/this-week").text


def test_call_b_failure_shows_friendly_message_and_gaps_still_work(client, monkeypatch):
    onboard(client)
    stub_gemma(monkeypatch, [RuntimeError("boom"), RuntimeError("boom")])
    r = client.post("/analyse", data={"next": "/projects"})
    page = client.get(r.headers["location"])
    assert page.status_code == 200 and "available right now" in page.text and "Try again" in page.text
    assert client.get("/skill-gaps").status_code == 200


def test_visitor_call_limit_gives_a_friendly_message(client, monkeypatch):
    onboard(client)
    with db.SessionLocal() as s:
        for v in s.query(db.Visitor).all():
            v.gemma_calls = gemma.PER_VISITOR_LIMIT
        s.commit()
    monkeypatch.setattr(gemma, "available", lambda: True)
    r = client.post("/analyse", data={"next": "/projects"})
    assert "limit" in client.get(r.headers["location"]).text


# ---------- tagging checks (AC4) ----------

JOB = {"title": "Graduate Developer", "company": "Acme", "description":
       "We build things in Python and SQL. Unfortunately we are unable to offer visa sponsorship for this role. "
       "Hybrid working, two days in the office."}


def test_made_up_sponsorship_quote_becomes_unclear():
    tags = tagging.JobTags(sponsorship="sponsors", sponsorship_quote="We happily sponsor everyone", work_mode="hybrid",
                           seniority="graduate", industry="Tech", required_skills=["python"])
    out = tagging.finalize({**JOB, "description": "Plain advert about Python."}, tags, "gemma")
    assert out["sponsorship"] == "unclear" and out["sponsorship_quote"] == ""


def test_real_quote_is_kept_and_keyword_net_overrides_unclear():
    quote = "we are unable to offer visa sponsorship for this role"
    good = tagging.JobTags(sponsorship="no_sponsorship", sponsorship_quote=quote, seniority="graduate")
    assert tagging.finalize(JOB, good, "gemma")["sponsorship_quote"] == quote
    unclear = tagging.JobTags(sponsorship="unclear", seniority="graduate")
    out = tagging.finalize(JOB, unclear, "gemma")
    assert out["sponsorship"] == "no_sponsorship" and tagging.quote_in_text(out["sponsorship_quote"], JOB["description"])


def test_snapshot_quotes_all_appear_word_for_word():
    jobs = json.loads(db.JOBS_FILE.read_text())
    assert len(jobs) >= 30
    for j in jobs:
        if j["sponsorship"] != "unclear":
            assert tagging.quote_in_text(j["sponsorship_quote"], j["description"]), j["title"]


# ---------- skill names ----------

def test_skill_names_are_cleaned_to_one_spelling():
    assert {skills.canonical(x) for x in ["ReactJS", "react.js", "React", " REACT "]} == {"React"}
    assert skills.canonical("postgres") == "PostgreSQL" and skills.canonical("k8s") == "Kubernetes"
    assert skills.canonical_list(["python3", "Python", ""]) == ["Python"]


# ---------- CV upload as PDF ----------

def test_onboarding_accepts_a_pdf_cv(client):
    r = onboard_pdf(client, make_pdf(CV.splitlines()))
    assert r.status_code == 303 and r.headers["location"].startswith("/profile")
    page = client.get("/profile").text
    assert "Python" in page and "Docker" in page


def test_pdf_wins_over_pasted_text(client):
    r = client.post("/onboarding", data={**FORM, "cv_text": "x" * 60},
                    files={"cv_file": ("cv.pdf", make_pdf(CV.splitlines()), "application/pdf")})
    assert r.status_code == 303
    assert "Flask" in client.get("/profile").text


def test_a_file_that_is_not_a_pdf_is_refused_with_a_clear_message(client):
    r = onboard_pdf(client, b"PK\x03\x04 this is a word document", name="cv.docx")
    assert r.status_code == 200 and "isn&#39;t a PDF" in r.text


def test_a_pdf_without_text_asks_for_pasted_text(client):
    r = onboard_pdf(client, make_pdf([]))
    assert r.status_code == 200 and "find any text" in r.text


def test_a_damaged_pdf_is_refused_without_a_raw_error(client):
    r = onboard_pdf(client, b"%PDF-1.4\nthis is not really a pdf")
    assert r.status_code == 200 and ("read that PDF" in r.text or "find any text" in r.text)


def test_no_cv_at_all_still_asks_for_one(client):
    r = onboard(client, cv_text="")
    assert r.status_code == 200 and "upload a PDF or paste your CV" in r.text


# ---------- senior roles shape project ideas ----------

def test_senior_skills_come_from_mid_and_senior_jobs_the_visitor_lacks():
    from types import SimpleNamespace as R
    from app import gaps

    def job(level, *req):
        return R(blocked=False, job={"seniority": level, "required_skills": list(req), "optional_skills": []})

    ranked = [job("senior", "Kafka", "Python", "Terraform"), job("mid", "Kafka", "Terraform"),
              job("senior", "Kafka", "Rust"), job("graduate", "Elixir", "Elixir"), job("junior", "Elixir")]
    found = gaps.senior_skills({"skills": ["Python"]}, ranked, exclude=["Terraform"])
    assert [(s["skill"], s["jobs_asking"], s["total_senior"]) for s in found] == [("Kafka", 3, 3)]
    # Python is already on the CV, Terraform is already a gap, Rust is asked for once, Elixir is junior-only


def test_projects_keep_only_senior_skills_from_the_list_we_gave():
    out = projects.AnalysisOut(projects=[projects.ProjectOut(
        title="Event pipeline", description="Stream events.", skills_demonstrated=["Docker"],
        senior_skills=["Kafka", "Cobol", "Docker"], milestones=["a"])])
    _, kept = projects.validate(out, ["Docker"], ["Kafka"])
    assert kept[0]["senior_skills"] == ["Kafka"]


def test_project_prompt_names_senior_skills_only_when_there_are_some(monkeypatch):
    seen = []
    monkeypatch.setattr(gemma, "ask_json", lambda prompt, schema, sid=None: seen.append(prompt) or projects.AnalysisOut(
        projects=[projects.ProjectOut(title="T", description="D", skills_demonstrated=["Docker"])]))
    gap = [{"skill": "Docker", "jobs_asking": 4, "total_relevant": 25, "in_dream": True}]
    projects.generate({"skills": ["Python"]}, gap, None, [{"skill": "Kafka", "jobs_asking": 9, "total_senior": 30}])
    projects.generate({"skills": ["Python"]}, gap, None)
    assert "Kafka: asked for by 9 of 30 senior roles" in seen[0] and "senior_skills" in seen[0]
    assert "senior" not in seen[1].lower()


def test_senior_roles_are_never_suggested_as_jobs(client):
    with db.SessionLocal() as s:
        s.add(db.Job(id="senior-test-1", title="Zebra Senior Staff Engineer", company="Acme", location="London",
                     description="x", url="https://example.com/s", seniority="senior", sponsorship="sponsors",
                     work_mode="hybrid", industry="Tech", required_skills=["Python", "Docker", "Git"],
                     optional_skills=[]))
        s.commit()
    try:
        onboard(client)
        assert "Zebra Senior Staff Engineer" not in client.get("/jobs?show=all").text
        assert "Zebra Senior Staff Engineer" not in client.get("/this-week").text
    finally:
        with db.SessionLocal() as s:
            s.delete(s.get(db.Job, "senior-test-1"))
            s.commit()
