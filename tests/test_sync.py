"""One-click refresh: only new or changed jobs are tagged, nothing of anyone's is touched, failures are contained."""
import json
from datetime import date, timedelta

import pytest

from app import ai, db, ingest, liveness, push, sources, sponsors
from conftest import make_job, make_person, signed_in

LONG = "We build things with Python and AWS, and we look after our people. " * 6
WITH_SPONSOR_CUE = LONG + " Candidates should check their visa position before applying."


def raw(i=1, site="indeed", **fields):
    row = {"title": f"Role {i}", "company": f"Co{i}", "location": "Leeds", "description": LONG, "url": f"https://{site}.example/{i}",
           "apply_url": "", "is_remote": None, "job_type": "fulltime", "date_posted": "", "salary_min": None, "salary_max": None,
           "salary_interval": "", "salary_currency": "", "company_industry": "", "job_level": ""}
    row.update(fields)
    return sources.finish(row, site)


@pytest.fixture
def lab(monkeypatch, people):
    """A fake world of sources. `lab.jobs[site]` is what that site returns; `lab.fail` lists sites that break."""
    class Lab:
        jobs, fail, calls = {}, set(), []
    lab = Lab()
    lab.jobs, lab.fail, lab.calls = {}, set(), []
    monkeypatch.setattr(sources, "jobspy_available", lambda: True)
    monkeypatch.setattr(sources, "JOBSPY_SITES", ["indeed", "linkedin"])
    monkeypatch.setattr(sources, "fetch", lambda site, term, loc, hours, n, distance=None, remote=False: _fake(lab, site, term, loc, hours, remote))
    monkeypatch.setattr(ai, "tagging_backends", lambda: [])
    monkeypatch.setattr(sponsors, "refresh", lambda: "Licensed-sponsor list: up to date.")
    monkeypatch.setattr(ingest, "PACE_SECONDS", {})
    set_profile("wife", keywords="developer", locations="Leeds")
    return lab


def set_profile(username, **fields):
    with db.SessionLocal() as s:
        person = s.query(db.Person).filter_by(username=username).one()
        row = s.get(db.SearchProfile, person.id) or db.SearchProfile(person_id=person.id)
        for k, v in fields.items():
            setattr(row, k, v)
        s.merge(row)
        s.commit()
        return person.id


def _fake(lab, site, term, loc, hours, remote=False):
    lab.calls.append((site, hours))
    if site in lab.fail:
        raise RuntimeError("429 too many requests")
    return [dict(r) for r in lab.jobs.get(site, [])]


def refresh(**kw):
    state = dict(ingest.STATE, running=True, stop=False)
    status = ingest.refresh(None, state, check_pages=False, **kw)
    return status, state


def test_second_refresh_tags_nothing_and_changes_nothing(lab, monkeypatch):
    lab.jobs["indeed"] = [raw(i) for i in range(5)]
    status, _ = refresh()
    assert status == "complete"
    with db.SessionLocal() as s:
        assert s.query(db.Job).count() == 5
        first_hash = {j.id: j.content_hash for j in s.query(db.Job)}
    tagged = []
    real = ingest.tagging.tag_batch
    monkeypatch.setattr(ingest.tagging, "tag_batch", lambda jobs, **kw: tagged.extend(jobs) or real(jobs, **kw))
    refresh()
    assert tagged == []  # nothing new or changed: nothing tagged
    with db.SessionLocal() as s:
        assert s.query(db.Job).count() == 5 and {j.id: j.content_hash for j in s.query(db.Job)} == first_hash
        run = s.query(db.SyncRun).order_by(db.SyncRun.id.desc()).first()
        assert (run.new, run.updated, run.seen) == (0, 0, 5)


def test_only_the_new_and_changed_jobs_are_tagged(lab, monkeypatch):
    lab.jobs["indeed"] = [raw(i) for i in range(4)]
    refresh()
    lab.jobs["indeed"] = [raw(0), raw(1), raw(2, description=LONG + " A brand new paragraph about our new team and tools."),
                          raw(3, title="Renamed role"), raw(9)]
    seen = []
    real = ingest.tagging.tag_batch
    monkeypatch.setattr(ingest.tagging, "tag_batch", lambda jobs, **kw: seen.extend(j["title"] for j in jobs) or real(jobs, **kw))
    refresh()
    assert sorted(seen) == ["Renamed role", "Role 2", "Role 9"]


def test_the_same_job_on_two_sites_is_one_job(lab):
    lab.jobs["indeed"] = [raw(1, "indeed", title="Junior Developer", company="Acme Ltd", is_remote=None)]
    lab.jobs["linkedin"] = [raw(1, "linkedin", title="Junior Developer", company="ACME", job_level="Entry level", is_remote=True)]
    refresh()
    with db.SessionLocal() as s:
        job = s.query(db.Job).one()
        assert job.sources == ["indeed", "linkedin"] and len(job.source_urls) == 2
        assert job.seniority == "junior" and job.work_mode == "remote" and job.is_remote is True


def test_one_failing_source_never_stops_the_others(lab):
    lab.jobs["linkedin"] = [raw(1, "linkedin")]
    lab.fail = {"indeed"}
    set_profile("wife", keywords="developer, tester, analyst, designer")
    status, _ = refresh()
    assert status == "partial"
    assert [c for c in lab.calls if c[0] == "indeed"].__len__() == ingest.BREAKER  # then it was skipped for the run
    with db.SessionLocal() as s:
        assert s.query(db.Job).count() == 1
        run = s.query(db.RunLog).one()
        messages = " ".join(e.message for e in s.query(db.RunEvent).filter_by(run_id=run.id))
        assert "indeed" in messages and "rate limited" in messages and "skipped for the rest of this run" in messages
        assert run.summary["sources"]["indeed"]["failed"] is True and run.summary["sources"]["linkedin"]["failed"] is False


def test_the_run_log_says_what_was_picked_and_skipped(lab):
    lab.jobs["indeed"] = [raw(1)]
    refresh()
    with db.SessionLocal() as s:
        text = "\n".join(e.message for e in s.query(db.RunEvent).order_by(db.RunEvent.id))
    assert "Using 2 sources: indeed, linkedin" in text and "Not using zip_recruiter: US and Canada only" in text
    assert "Not using reed: no key in .env" in text and "keywords only (no AI tool found)" in text


def test_refresh_asks_each_source_only_for_what_is_newer(lab):
    lab.jobs["indeed"] = [raw(1)]
    refresh()
    refresh()
    first, second = [h for s, h in lab.calls if s == "indeed"]
    assert first == ingest.FIRST_RUN_HOURS and second <= 48


def test_refresh_leaves_the_users_work_alone(lab):
    make_job("old", title="Role 1", company="Co1", location="Leeds", url="https://indeed.example/1", description=LONG,
             deadline=date(2026, 10, 20), deadline_quote="closes 20 October")
    wife = signed_in("wife")
    wife.post("/board/old/shortlist")
    with db.SessionLocal() as s:
        entry_id = s.query(db.TrackerEntry).one().id
    wife.post(f"/tracker/{entry_id}/deadline", {"deadline": "2026-10-15"})
    wife.post(f"/tracker/{entry_id}/notes", {"notes": "my notes"})
    lab.jobs["indeed"] = [raw(1, description=LONG + " Updated text for the role, with more detail about the team.")]
    refresh()
    with db.SessionLocal() as s:
        assert s.query(db.Job).count() == 1  # matched the old job, not duplicated
        job = s.get(db.Job, "old")
        assert job.deadline == date(2026, 10, 20)  # a known deadline survives a re-fetch that misses it
        assert "Updated text" in job.description
        entry = s.get(db.TrackerEntry, entry_id)
        assert entry.notes == "my notes" and entry.deadline_override == date(2026, 10, 15)


# ---------- AI only where needed ----------

def _fake_claude(monkeypatch, answers, record):
    monkeypatch.setattr(ai, "tagging_backends", lambda: ["claude"])
    monkeypatch.setattr(ai, "backend_ready", lambda name: name == "claude")

    def call(prompt):
        record.append(prompt)
        payload = json.loads(prompt.split("Jobs (JSON):")[1].split("\n\nReply with ONLY")[0])
        return json.dumps({"results": [dict(id=p["id"], **answers(p)) for p in payload]})
    monkeypatch.setitem(ai._CALLERS, "claude", call)


def test_ai_is_called_only_for_jobs_with_something_unsettled(lab, monkeypatch):
    prompts = []
    _fake_claude(monkeypatch, lambda p: {"sponsorship": "no_sponsorship",
                                         "sponsorship_quote": "Candidates should check their visa position before applying."}, prompts)
    lab.jobs["indeed"] = [raw(1), raw(2), raw(3, description=WITH_SPONSOR_CUE), raw(4, description=WITH_SPONSOR_CUE + " x")]
    refresh()
    assert len(prompts) == 1  # one batched call, for the two jobs that mention visas; the other two needed none
    assert prompts[0].count('"need": ["sponsorship"]') == 2
    with db.SessionLocal() as s:
        by = {j.title: j for j in s.query(db.Job)}
        assert by["Role 3"].sponsorship == "no_sponsorship" and by["Role 3"].tag_source == "ai"
        assert by["Role 1"].sponsorship == "unclear" and by["Role 1"].tag_source == "keywords" and not by["Role 1"].needs_ai


def test_an_invented_quote_is_rejected(lab, monkeypatch):
    prompts = []
    _fake_claude(monkeypatch, lambda p: {"sponsorship": "sponsors", "sponsorship_quote": "We always sponsor everyone"}, prompts)
    lab.jobs["indeed"] = [raw(3, description=WITH_SPONSOR_CUE)]
    refresh()
    with db.SessionLocal() as s:
        assert s.query(db.Job).one().sponsorship == "unclear"


def test_when_ai_is_down_keywords_are_used_and_upgraded_later(lab, monkeypatch):
    lab.jobs["indeed"] = [raw(3, description=WITH_SPONSOR_CUE)]
    refresh()  # no AI tool at all
    with db.SessionLocal() as s:
        job = s.query(db.Job).one()
        assert job.needs_ai is True and job.tag_source == "keywords"
    prompts = []
    _fake_claude(monkeypatch, lambda p: {"sponsorship": "no_sponsorship",
                                         "sponsorship_quote": "Candidates should check their visa position before applying."}, prompts)
    refresh()  # same advert, but AI is back: it is upgraded, not left weak
    with db.SessionLocal() as s:
        job = s.query(db.Job).one()
        assert len(prompts) == 1 and job.needs_ai is False and job.sponsorship == "no_sponsorship"


def test_a_cli_that_hits_its_limit_is_skipped_for_the_rest_of_the_run(monkeypatch):
    ai.reset_backends()
    monkeypatch.setattr(ai.shutil, "which", lambda name: "/bin/" + name if name == "claude" else None)
    monkeypatch.setattr(ai, "_ollama_up", lambda: False)

    class Done:
        returncode, stdout, stderr = 1, "", "Claude usage limit reached"
    monkeypatch.setattr(ai.subprocess, "run", lambda *a, **k: Done())
    assert ai.tagging_backends() == ["claude"]
    with pytest.raises(ai.AIUnavailable):
        ai.ask_json("x", ingest.tagging.BatchOut, backends=("claude",))
    assert ai.tagging_backends() == []
    ai.reset_backends()


def test_free_fields_need_no_ai():
    tags, need = ingest.tagging.free_tags(
        {"title": "Graduate Developer", "company": "Acme", "description": LONG + " This is a fully remote position.",
         "is_remote": True, "job_level": "Internship", "company_industry": "Fintech", "salary_min": 30000, "salary_max": 40000,
         "salary_interval": "yearly", "expires": "2026-11-01"}, date(2026, 10, 5))
    assert need == [] and tags["work_mode"] == "remote" and tags["seniority"] == "graduate" and tags["industry"] == "Fintech"
    assert tags["deadline"] == "2026-11-01" and tags["salary_min"] == 30000


# ---------- liveness ----------

def test_only_clear_signals_close_a_job():
    assert liveness.judge(404, "u", "u", "")[0] == "closed"
    assert liveness.judge(200, "u", "u", "Sorry, this job has expired")[0] == "closed"
    assert liveness.judge(200, "https://x.com/jobs?q=dev", "https://x.com/job/123", "<html>")[0] == "closed"
    assert liveness.judge(200, "u", "u", "Apply now for this great role")[0] == "open"
    assert liveness.judge(403, "u", "u", "")[0] == "unknown" and liveness.judge(429, "u", "u", "")[0] == "unknown"


def _old_job(job_id, url, days=10, **fields):
    make_job(job_id, url=url, **fields)
    with db.SessionLocal() as s:
        job = s.get(db.Job, job_id)
        job.first_seen = db.utcnow() - timedelta(days=days)
        s.commit()


def test_liveness_closes_gone_jobs_and_leaves_blocked_ones_alone(people):
    _old_job("gone", "https://a.example/1")
    _old_job("blocked1", "https://b.example/1")
    _old_job("blocked2", "https://b.example/2")
    _old_job("blocked3", "https://b.example/3")
    _old_job("fresh", "https://c.example/1", days=0)
    answers = {"https://a.example/1": (404, "https://a.example/1", "")}
    asked = []

    def fetch(url):
        asked.append(url)
        return answers.get(url, (403, url, ""))
    with db.SessionLocal() as s:
        result = liveness.check(s, fetch=fetch, pause=(0, 0))
        assert result["closed"] == 1 and result["skipped_sites"] == ["b.example"]
        assert s.get(db.Job, "gone").closed_at is not None
        assert all(s.get(db.Job, i).closed_at is None for i in ("blocked1", "blocked2", "blocked3", "fresh"))
    assert "https://c.example/1" not in asked and len([u for u in asked if "b.example" in u]) == 2  # stopped hammering b.example


def test_tracked_jobs_are_checked_first_and_a_closed_job_shows_as_closed(people):
    for i in range(3):
        _old_job(f"j{i}", f"https://s.example/{i}", days=20 - i)
    wife = signed_in("wife")
    wife.post("/board/j2/shortlist")
    with db.SessionLocal() as s:
        assert liveness.candidates(s, 1)[0].id == "j2"
        job = s.get(db.Job, "j2")
        job.closed_at = db.utcnow()
        s.commit()
        from app import deadlines
        assert deadlines.listing_status(job, date(2026, 10, 5)) == "closed"


def test_a_closed_job_that_is_listed_again_reopens(lab):
    lab.jobs["indeed"] = [raw(1)]
    refresh()
    with db.SessionLocal() as s:
        job = s.query(db.Job).one()
        job.closed_at, job.close_reason = db.utcnow(), "page gone (404)"
        s.commit()
    refresh()
    with db.SessionLocal() as s:
        assert s.query(db.Job).one().closed_at is None


# ---------- publishing to the live site ----------

def test_the_api_needs_the_token(browser, monkeypatch):
    monkeypatch.setenv("INGEST_TOKEN", "x" * 30)
    assert browser.client.post("/api/ingest/manifest", json={"hashes": {}}).status_code == 401
    assert browser.client.post("/api/ingest/manifest", json={"hashes": {}}, headers={"Authorization": "Bearer nope"}).status_code == 401
    monkeypatch.delenv("INGEST_TOKEN")
    assert browser.client.post("/api/ingest/manifest", json={"hashes": {}}, headers={"Authorization": "Bearer "}).status_code == 401


def test_publishing_sends_only_the_difference(lab, browser, monkeypatch):
    monkeypatch.setenv("INGEST_TOKEN", "t" * 30)
    monkeypatch.setenv("INGEST_URL", "https://live.example")
    sent, fresh_site = [], [True]

    def post(path, payload):  # the "live site" is this same app, reached with its token
        sent.append(path)
        if path == "manifest" and fresh_site[0]:  # a live site that has none of the jobs yet
            return {"need": list(payload["hashes"])}
        r = browser.client.post("/api/ingest/" + path, json=payload, headers={"Authorization": "Bearer " + "t" * 30})
        assert r.status_code == 200, r.text
        return r.json()
    monkeypatch.setattr(push, "_post", post)
    monkeypatch.setattr(push, "wake", lambda: True)
    lab.jobs["indeed"] = [raw(i) for i in range(3)]
    refresh()
    assert sent.count("jobs") == 1 and "manifest" in sent and sent[-1] == "log"
    sent.clear()
    fresh_site[0] = False  # now the live site has them all
    refresh()
    assert "jobs" not in sent  # nothing new or changed: no job rows are sent, only "still listed"
    assert "seen" in sent


def test_the_api_never_overwrites_a_known_deadline_or_touches_the_tracker(browser, monkeypatch, people):
    monkeypatch.setenv("INGEST_TOKEN", "t" * 30)
    make_job("a", deadline=date(2026, 10, 20), deadline_quote="closes 20 October", content_hash="old")
    wife = signed_in("wife")
    wife.post("/board/a/shortlist")
    row = push.job_dict(type("J", (), {f: None for f in db.JOB_FIELDS})())
    row.update(id="a", title="Junior Developer (new)", company="Acme", location="Leeds", description=LONG, url="https://example.com/a",
               content_hash="new", deadline=None)
    r = browser.client.post("/api/ingest/jobs", json={"jobs": [row]}, headers={"Authorization": "Bearer " + "t" * 30})
    assert r.status_code == 200 and r.json() == {"new": 0, "updated": 1, "failed": []}
    with db.SessionLocal() as s:
        job = s.get(db.Job, "a")
        assert job.title.endswith("(new)") and job.deadline == date(2026, 10, 20) and s.query(db.TrackerEntry).count() == 1


# ---------- the admin page ----------

def test_sync_page_and_refresh_button(lab, monkeypatch):
    dad = signed_in("dad")
    page = dad.get("/admin/sync").text
    assert "Refresh jobs" in page and "2 sources ready" in page
    monkeypatch.setattr(ingest, "start", lambda person_id: True)
    assert dad.post("/admin/sync/refresh").status_code == 303
    monkeypatch.setattr(sources, "jobspy_available", lambda: False)
    assert "Refreshing only works when Job Buddy is running on your laptop" in dad.get("/admin/sync").text
    wife_id = set_profile("wife")
    dad.post(f"/admin/sync/profile/{wife_id}", {"keywords": "tester, developer", "locations": "Leeds", "per_search": "5000", "enabled": "1"})
    with db.SessionLocal() as s:
        row = s.get(db.SearchProfile, wife_id)
        assert row.per_search == 1000 and ingest.split_list(row.keywords) == ["tester", "developer"] and row.enabled


def test_the_log_shows_on_the_sync_page(lab):
    lab.jobs["indeed"] = [raw(1)]
    lab.fail = {"linkedin"}
    refresh()
    page = signed_in("dad").get("/admin/sync").text
    assert "What happened" in page and "Partial" in page and "linkedin" in page and "rate limited" in page


def test_the_deadline_cue_needs_a_date_nearby():
    cue = ingest.tagging.CUES["deadline"]
    assert not cue.search("Join us. We will work with you until you feel confident, and closing the loop on bugs is key.")
    assert cue.search("Applications close at midnight on Friday 17th of October.")
    assert cue.search("Deadline: end of October 2026")


def test_indeed_jobs_are_checked_through_the_employer_link(people):
    _old_job("viaindeed", "https://uk.indeed.com/viewjob?jk=1", apply_url="https://acme.example/careers/1")
    _old_job("noindeedlink", "https://uk.indeed.com/viewjob?jk=2")
    asked = []
    with db.SessionLocal() as s:
        result = liveness.check(s, pause=(0, 0), fetch=lambda url: asked.append(url) or (404, url, ""))
        assert asked == ["https://acme.example/careers/1"] and result["closed"] == 1
        assert s.get(db.Job, "noindeedlink").closed_at is None  # no way to check it kindly, so it is left alone


def test_the_board_shows_when_a_job_was_added(people):
    make_job("fresh1")
    with db.SessionLocal() as s:
        s.get(db.Job, "fresh1").first_seen = db.utcnow()
        s.commit()
    wife = signed_in("wife")
    assert "Added to the Board today" in wife.get("/board").text
    assert "Added to the Job Board today" in wife.get("/board/fresh1").text
    assert "Recently added" in wife.get("/board").text


# ---------- two people, two searches ----------

def test_each_search_tags_jobs_with_who_it_was_for(lab):
    wife, brother = set_profile("wife"), set_profile("brother", keywords="graduate engineer", locations="Leeds", avoid="senior")
    lab.jobs["indeed"] = [raw(1, title="Graduate Engineer"), raw(2, title="Senior Graduate Engineer")]
    refresh()
    with db.SessionLocal() as s:
        by = {j.title: j.matched for j in s.query(db.Job)}
    assert by["Graduate Engineer"] == sorted([wife, brother])  # one job, both searches found it
    assert "Senior Graduate Engineer" in by and by["Senior Graduate Engineer"] == [wife]  # his avoid-word drops it for him only


def test_a_job_nobody_wants_is_not_stored(lab):
    set_profile("wife", avoid="role")
    lab.jobs["indeed"] = [raw(1)]  # title "Role 1"
    refresh()
    with db.SessionLocal() as s:
        assert s.query(db.Job).count() == 0


def test_for_you_shows_only_your_matches_and_hides_citizenship_only_jobs(lab):
    wife, brother = set_profile("wife"), set_profile("brother", keywords="engineer", locations="Leeds", needs_sponsorship=True)
    set_profile("wife", keywords="")  # she has no search of her own: sees everything
    lab.jobs["indeed"] = [raw(1, title="Graduate Engineer"),
                          raw(2, title="Graduate Developer", description=LONG + " Applicants must be a British citizen."),
                          raw(3, title="Associate Engineer", company="Deloitte LLP")]
    refresh()
    with db.SessionLocal() as s:
        s.get(db.Job, [j.id for j in s.query(db.Job) if j.title == "Graduate Developer"][0]).matched = [brother]
        s.commit()
    page = signed_in("brother").get("/board").text
    assert "Graduate Engineer" in page and "Associate Engineer" in page
    assert "Graduate Developer" not in page          # citizenship-only: hidden for someone who needs sponsorship
    assert "Licensed sponsor" in page
    assert page.index("Associate Engineer") < page.index("Graduate Engineer")  # licensed sponsor ranks above unclear
    assert "Graduate Developer" in signed_in("brother").get("/board?view=all").text


def test_the_register_badges_licensed_employers_and_keeps_the_keyword_scan(lab, monkeypatch):
    monkeypatch.setattr(sponsors, "is_licensed", lambda company: company == "Deloitte LLP")
    lab.jobs["indeed"] = [raw(1, company="Deloitte LLP", description=LONG + " We do not offer visa sponsorship for this role."),
                          raw(2, company="Nobody Ltd")]
    refresh()
    with db.SessionLocal() as s:
        by = {j.company: j for j in s.query(db.Job)}
    assert by["Deloitte LLP"].licensed_sponsor and not by["Nobody Ltd"].licensed_sponsor
    assert by["Deloitte LLP"].sponsorship == "no_sponsorship" and "sponsorship" in by["Deloitte LLP"].sponsorship_quote  # quote kept


def test_the_register_matches_legal_names(monkeypatch, tmp_path):
    f = tmp_path / "s.csv"
    f.write_text("Organisation Name,Town\n PricewaterhouseCoopers LLP ,London\nAcme Limited,Leeds\n")
    monkeypatch.setattr(sponsors, "FILE", f)
    monkeypatch.setattr(sponsors, "_names", None)
    assert sponsors.is_licensed("PwC") and sponsors.is_licensed("ACME Ltd") and not sponsors.is_licensed("Other Co")
    monkeypatch.setattr(sponsors, "_names", None)


def test_starter_searches_exist_for_priya_and_akanksh(people):
    make_person("priya", name="Priya")
    make_person("akanksh", name="Akanksh")
    with db.SessionLocal() as s:
        rows = {r.person.first_name: r for r in ingest.profiles(s)}
    assert "business analyst" in rows["Priya"].keywords and rows["Priya"].locations == "Birmingham" and not rows["Priya"].needs_sponsorship
    assert "tax technology" in rows["Akanksh"].keywords and rows["Akanksh"].needs_sponsorship and "senior" in rows["Akanksh"].avoid


def test_a_search_that_fills_up_asks_for_more_and_slow_sites_carry_over(lab, monkeypatch):
    asked = []
    monkeypatch.setattr(sources, "fetch", lambda site, term, loc, hours, n, distance=None, remote=False:
                        asked.append((site, term, n)) or [raw(i, site) for i in range(min(n, 250))])
    set_profile("wife", keywords="a, b, c", per_search=100)
    monkeypatch.setattr(ingest, "BUDGET", {"linkedin": 2})
    refresh()
    assert ("indeed", "a", 100) in asked and ("indeed", "a", 200) in asked and ("indeed", "a", 400) in asked  # kept doubling until it stopped filling
    assert len({t for s, t, n in asked if s == "linkedin"}) == 2  # budget of 2: the third waits
    with db.SessionLocal() as s:
        assert s.query(db.RunEvent).filter(db.RunEvent.message.like("%wait for the next refresh%")).count() == 1
    asked.clear()
    refresh()
    assert [t for s, t, n in asked if s == "linkedin"][0] == "c"  # the one that waited goes first


def test_every_search_looks_back_a_week_once_a_week(lab):
    lab.jobs["indeed"] = [raw(1)]
    refresh()
    refresh()
    hours = [h for s, h in lab.calls if s == "indeed"]
    assert hours[0] == ingest.FIRST_RUN_HOURS and hours[1] < 100  # an ordinary day
    with db.SessionLocal() as s:
        s.merge(db.FetchState(key="sweep", last_ok=db.utcnow() - ingest.timedelta(days=8)))
        s.commit()
    lab.calls.clear()
    refresh()
    assert max(h for s_, h in lab.calls) >= ingest.SWEEP_HOURS


# ---------- a bad source, a crash or a Stop never loses what was already found ----------

def _done_keys():
    with db.SessionLocal() as s:
        return {r.key.split("|")[0] for r in s.query(db.FetchState) if r.last_ok and r.key != "sweep"}


def test_a_hanging_source_does_not_lose_the_ones_that_worked(lab, monkeypatch):
    import time
    monkeypatch.setattr(ingest, "SEARCH_TIMEOUT", 0.2)
    lab.jobs["indeed"] = [raw(i) for i in range(3)]
    monkeypatch.setattr(sources, "fetch", lambda site, term, loc, hours, n, distance=None, remote=False:
                        (time.sleep(1) or []) if site == "linkedin" else _fake(lab, site, term, loc, hours, remote))
    status, _ = refresh()
    assert status == "partial"
    with db.SessionLocal() as s:
        assert s.query(db.Job).count() == 3  # Indeed's jobs are saved even though LinkedIn hung
        assert any("timed out" in e.message for e in s.query(db.RunEvent))
    assert "indeed" in _done_keys() and "linkedin" not in _done_keys()  # only the search that worked is marked done; LinkedIn's is retried next time


def test_a_search_is_only_marked_done_after_its_jobs_are_saved(lab, monkeypatch):
    lab.jobs["indeed"] = [raw(1)]
    real = ingest._upsert
    monkeypatch.setattr(ingest, "_upsert", lambda *a, **k: 1 / 0)
    refresh()
    assert "indeed" not in _done_keys()  # could not save: not done, so the next run looks back over the same period
    monkeypatch.setattr(ingest, "_upsert", real)
    refresh()
    with db.SessionLocal() as s:
        assert s.query(db.Job).count() == 1


def test_stop_keeps_what_was_saved_and_still_publishes(lab, monkeypatch):
    published = []
    monkeypatch.setattr(push, "configured", lambda: True)
    monkeypatch.setattr(push, "publish", lambda s, run, stop=None: published.append(1) or {})
    monkeypatch.setattr(push, "send_log", lambda run_id: None)
    state = dict(ingest.STATE, running=True, stop=False)

    def fetch(site, term, loc, hours, n, distance=None, remote=False):
        state["stop"] = True  # the admin presses Stop during the first search
        return [raw(1)]
    monkeypatch.setattr(sources, "fetch", fetch)
    status = ingest.refresh(None, state, check_pages=False)
    assert status == "partial" and published == [1]
    with db.SessionLocal() as s:
        assert s.query(db.Job).count() == 1


def test_a_crash_part_way_keeps_earlier_searches_and_still_publishes(lab, monkeypatch):
    published = []
    monkeypatch.setattr(push, "configured", lambda: True)
    monkeypatch.setattr(push, "publish", lambda s, run, stop=None: published.append(1) or {})
    monkeypatch.setattr(push, "send_log", lambda run_id: None)
    set_profile("wife", keywords="a, b")
    calls = []

    def fetch(site, term, loc, hours, n, distance=None, remote=False):
        calls.append(term)
        if len(calls) == 2:
            raise KeyboardInterrupt  # not an ordinary error: the whole run dies
        return [raw(len(calls), site)]
    monkeypatch.setattr(sources, "fetch", fetch)
    state = dict(ingest.STATE, running=True, stop=False)
    with pytest.raises(KeyboardInterrupt):
        ingest.refresh(None, state, check_pages=False)
    with db.SessionLocal() as s:
        assert s.query(db.Job).count() == 1  # the first search was already saved


# ---------- the live progress panel ----------

def test_progress_reports_each_source_a_percentage_and_an_eta(lab):
    lab.jobs["indeed"] = [raw(1)]
    lab.fail = {"linkedin"}
    seen = []
    real = ingest._save
    ingest._save = lambda *a, **k: seen.append(ingest.progress(a[3])) or real(*a, **k)
    try:
        refresh()
    finally:
        ingest._save = real
    first = seen[0]
    assert set(first["plan"]) == {"indeed", "linkedin"} and first["plan"]["indeed"]["status"] == "running"
    assert first["now"].startswith("indeed:") and 0 <= first["percent"] < 100 and first["found"] == 0


def test_a_failed_source_completes_its_bar_and_the_reason_reaches_the_panel(lab):
    lab.fail = {"linkedin"}
    state = dict(ingest.STATE, running=True, stop=False)
    set_profile("wife", keywords="a, b, c")
    ingest.refresh(None, state, check_pages=False)
    p = ingest.progress(state)
    assert p["plan"]["linkedin"]["status"] == "failed" and p["plan"]["linkedin"]["done"] == p["plan"]["linkedin"]["total"]
    assert p["percent"] == 100 and any("linkedin" in n for n in p["notes"])


def test_status_endpoint_and_panel_render_while_running(lab, monkeypatch):
    dad = signed_in("dad")
    monkeypatch.setitem(ingest.STATE, "running", True)
    monkeypatch.setitem(ingest.STATE, "plan", {"indeed": {"total": 10, "done": 4, "found": 37, "status": "running"}})
    monkeypatch.setitem(ingest.STATE, "started", __import__("time").time() - 60)
    body = dad.get("/admin/sync/status").json()
    assert body["percent"] == 40 and body["eta_seconds"] and body["plan"]["indeed"]["found"] == 37
    page = dad.get("/admin/sync").text
    assert 'id="live"' in page and "Stopping is safe" in page and '"percent": 40' in page.replace('":40', '": 40')
    assert dad.get("/admin/sync/status").status_code == 200 and signed_in("wife").get("/admin/sync/status").status_code == 403


def test_on_site_parking_is_not_a_work_mode():
    assert ingest.tagging.keyword_work_mode("Benefits: On-site parking and a gym.")[0] == "unclear"
    assert ingest.tagging.keyword_work_mode("This is an on-site role in Leeds.")[0] == "onsite"


def test_right_to_work_is_not_ranked_as_a_refusal_for_someone_who_needs_sponsorship(lab):
    brother = set_profile("brother", keywords="engineer", locations="Leeds", needs_sponsorship=True)
    lab.jobs["indeed"] = [raw(1, title="Graduate Engineer A", description=LONG + " Candidates must have the right to work in the UK."),
                          raw(2, title="Graduate Engineer B", description=LONG + " We are unable to offer visa sponsorship."),
                          raw(3, title="Graduate Engineer C")]
    refresh()
    with db.SessionLocal() as s:
        for j in s.query(db.Job):
            j.matched = [brother]
        s.commit()
    page = signed_in("brother").get("/board").text
    assert page.index("Engineer A") < page.index("Engineer B") and page.index("Engineer C") < page.index("Engineer B")


def test_codex_runs_the_cheapest_model_and_any_cli_failure_switches_it_off(monkeypatch):
    ai.reset_backends()
    seen = []
    monkeypatch.setattr(ai.shutil, "which", lambda name: "/bin/" + name if name == "codex" else None)
    monkeypatch.setattr(ai, "_ollama_up", lambda: False)

    class Fail:
        returncode, stdout, stderr = 1, "", "model not found for your plan"
    monkeypatch.setattr(ai.subprocess, "run", lambda cmd, **k: seen.append(cmd) or Fail())
    assert ai.tagging_backends() == ["codex"]
    with pytest.raises(ai.AIUnavailable):
        ai.ask_json("x", ingest.tagging.BatchOut, backends=("codex",))
    cmd = seen[0]
    assert cmd[:3] == ["codex", "exec", "--skip-git-repo-check"] and cmd[cmd.index("-m") + 1] == ai.CODEX_MODEL == "gpt-6-luna"
    assert "--ephemeral" in cmd and "read-only" in cmd and 'model_reasoning_effort="low"' in cmd
    assert ai.tagging_backends() == []  # not retried on every batch
    ai.reset_backends()


def test_the_admin_can_filter_the_board_by_who_a_job_was_searched_for(people):
    make_job("a", title="For wife only")
    make_job("b", title="For brother only")
    with db.SessionLocal() as s:
        s.get(db.Job, "a").matched = [people["wife"]]
        s.get(db.Job, "b").matched = [people["brother"]]
        s.commit()
    dad = signed_in("dad")
    both = dad.get("/board").text
    assert "For wife only" in both and "For brother only" in both and "For Wife" in both
    only = dad.get(f"/board?who={people['wife']}").text
    assert "For wife only" in only and "For brother only" not in only


def test_admin_board_search_accepts_everyone_filter(people):
    make_job("a", title="Graduate Developer")

    response = signed_in("dad").get("/board?who=&q=Graduate")

    assert response.status_code == 200
    assert "Graduate Developer" in response.text


def test_a_job_whose_page_is_still_open_is_not_left_to_drift_to_possibly_closed(people):
    _old_job("openone", "https://c.example/open", days=40)
    with db.SessionLocal() as s:
        s.get(db.Job, "openone").last_seen = db.utcnow() - ingest.timedelta(days=40)
        s.commit()
        liveness.check(s, pause=(0, 0), fetch=lambda url: (200, url, "Apply now"))
        from app import deadlines
        assert deadlines.listing_status(s.get(db.Job, "openone"), date.today()) == "open"


def test_the_admin_page_names_the_tagging_models(lab, monkeypatch):
    monkeypatch.setattr(ai, "tagging_backends", lambda: ["claude", "codex"])
    page = signed_in("dad").get("/admin/sync").text
    assert "claude (haiku)" in page and "codex (gpt-6-luna)" in page


def test_a_completed_refresh_clearly_names_the_exact_model_used(lab, monkeypatch):
    lab.jobs["indeed"] = [raw(1)]
    monkeypatch.setattr(ai, "tagging_backends", lambda: ["codex"])
    real_tag_batch = ingest.tagging.tag_batch

    def tagged_with_codex(jobs, **kwargs):
        tags, stats = real_tag_batch(jobs, use_ai=False)
        stats.update(ai_calls=1, backends=["codex"])
        return tags, stats

    monkeypatch.setattr(ingest.tagging, "tag_batch", tagged_with_codex)
    refresh()

    page = signed_in("dad").get("/admin/sync").text
    assert "Model used:" in page
    assert "codex (gpt-6-luna)" in page


def test_who_a_job_is_for_travels_as_a_username_so_different_ids_still_match(browser, monkeypatch, people):
    monkeypatch.setenv("INGEST_TOKEN", "t" * 30)
    auth = {"Authorization": "Bearer " + "t" * 30}
    laptop_ids = {99: "wife", 98: "nobody-here"}  # on the laptop she is id 99; on the live site she has a different id
    make_job("m1", title="Matched job")
    with db.SessionLocal() as s:
        job = s.get(db.Job, "m1")
        job.matched = [99, 98]
        s.commit()
        row = push.job_dict(job, laptop_ids)
    assert row["matched"] == ["nobody-here", "wife"]
    # the live site: the manifest asks for this job because who it is for differs, then maps usernames to its own ids
    sig = push.signature("", row["matched"])
    assert "m1" in browser.client.post("/api/ingest/manifest", json={"hashes": {"m1": sig}}, headers=auth).json()["need"]
    with db.SessionLocal() as s:  # the live site's copy of the job does not carry the laptop's ids
        s.get(db.Job, "m1").matched = []
        s.commit()
    assert browser.client.post("/api/ingest/jobs", json={"jobs": [row]}, headers=auth).status_code == 200
    with db.SessionLocal() as s:
        assert s.get(db.Job, "m1").matched == [people["wife"]]  # "nobody-here" does not exist there, so it is dropped
    assert "m1" not in browser.client.post("/api/ingest/manifest", json={"hashes": {"m1": push.signature("", ["wife"])}}, headers=auth).json()["need"]


def test_push_waits_for_a_sleeping_site_and_says_what_it_answered(monkeypatch):
    monkeypatch.setenv("INGEST_TOKEN", "t" * 30)
    monkeypatch.setenv("INGEST_URL", "https://live.example")
    monkeypatch.setattr(push.time, "sleep", lambda s: None)

    class R:
        def __init__(self, code, body="{}"):
            self.status_code, self.text = code, body
        def json(self):
            return {"need": []}
    answers = iter([R(502), R(502), R(200)])
    monkeypatch.setattr(push.httpx, "post", lambda *a, **k: next(answers))
    assert push._post("manifest", {}) == {"need": []}  # two 502s while waking, then it works
    monkeypatch.setattr(push.httpx, "post", lambda *a, **k: R(500, "boom"))
    with pytest.raises(push.PushError, match="answered 500 to /manifest"):
        push._post("manifest", {})
    monkeypatch.setattr(push.httpx, "post", lambda *a, **k: R(401))
    with pytest.raises(PermissionError, match="INGEST_TOKEN"):
        push._post("manifest", {})


def test_stop_during_publishing_ends_the_upload_cleanly(lab, monkeypatch):
    monkeypatch.setenv("INGEST_URL", "https://live.example")
    monkeypatch.setenv("INGEST_TOKEN", "t" * 30)
    monkeypatch.setattr(push, "wake", lambda: True)
    monkeypatch.setattr(push, "BATCH", 1)
    sent = []

    def post(path, payload):
        if path == "manifest":
            return {"need": list(payload["hashes"])}
        if path == "jobs":
            sent.append(1)
            state_ref["stop_publish"] = True  # the admin presses Stop after the first batch
        return {}
    monkeypatch.setattr(push, "_post", post)
    lab.jobs["indeed"] = [raw(i) for i in range(3)]
    state_ref = dict(ingest.STATE, running=True, stop=False)
    monkeypatch.setattr(push, "send_log", lambda run_id: None)
    ingest.refresh(None, state_ref, check_pages=False)
    assert len(sent) == 1  # it stopped after the first batch, not all three


def test_the_admin_chooses_and_orders_the_tagging_tools(lab, monkeypatch):
    monkeypatch.undo()  # use the real chain here, not the lab's stub
    monkeypatch.setattr(ai, "backend_ready", lambda name: name in ("claude", "codex"))
    dad = signed_in("dad")
    try:
        dad.post("/admin/sync/tagging", {"pick0": "codex", "pick1": "claude", "pick2": ""})
        assert ai.load_tagging_order() == ("codex", "claude") and ai.tagging_backends() == ["codex", "claude"]  # saved, in that order
        page = dad.get("/admin/sync").text
        assert '<option value="codex" selected>' in page and "Third choice" in page
        dad.post("/admin/sync/tagging", {"pick0": "", "pick1": "", "pick2": ""})
        assert ai.load_tagging_order() == () and ai.tagging_backends() == []  # none: keywords only
    finally:
        ai._order[:] = list(ai.ALL_BACKENDS)
        with db.SessionLocal() as s:
            s.query(db.AppSetting).delete()
            s.commit()


def test_routine_plan_lines_are_hidden_but_warnings_still_show(lab):
    lab.jobs["indeed"] = [raw(1)]
    lab.fail = {"linkedin"}
    refresh()
    page = signed_in("dad").get("/admin/sync").text
    assert "Not using zip_recruiter" not in page and "different searches" not in page  # routine plan lines are gone
    assert "rate limited" in page  # a real problem is still shown


def test_the_bulk_copy_script_copies_jobs_by_username_and_is_safe_to_repeat(people, monkeypatch, tmp_path):
    import importlib.util
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    target_url = f"sqlite:///{tmp_path}/live.db"
    live_engine = create_engine(target_url)
    db.Base.metadata.create_all(live_engine)
    with sessionmaker(bind=live_engine)() as s:  # the "live site": her account has a different id, and one job is already there
        s.add(db.Person(id=77, username="wife", name="Wife", role="user", password_hash="x"))
        s.add(db.Job(id="j0", title="Old title", company="Co", url="u", description="d", deadline=date(2026, 12, 1)))
        s.commit()
    for i in range(3):
        make_job(f"j{i}", title=f"Job {i}")
    with db.SessionLocal() as s:
        s.get(db.Job, "j1").matched = [people["wife"]]
        s.commit()
    spec = importlib.util.spec_from_file_location("copy_jobs", "scripts/copy_jobs_to_live.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setenv("LIVE_DATABASE_URL", target_url)
    mod.main()
    mod.main()  # again: nothing is duplicated
    with sessionmaker(bind=live_engine)() as s:
        rows = {j.id: j for j in s.query(db.Job)}
        assert set(rows) == {"j0", "j1", "j2"} and rows["j0"].title == "Job 0"
        assert rows["j1"].matched == [77]                # her id on the live site, found by username
        assert rows["j0"].deadline == date(2026, 12, 1)  # a deadline the live site already had is kept


def test_a_board_page_view_never_reads_the_advert_text(people):
    from sqlalchemy import event
    make_job("big", title="Searchable role", description="HEAVY ADVERT TEXT " * 400)
    statements = []
    listener = lambda conn, cursor, statement, *a: statements.append(statement)  # noqa: E731
    event.listen(db.engine, "before_cursor_execute", listener)
    try:
        page = signed_in("dad").get("/board?q=searchable&work_mode=hybrid").text
    finally:
        event.remove(db.engine, "before_cursor_execute", listener)
    assert "Searchable role" in page
    board_selects = [s for s in statements if "FROM jobs" in s and s.lstrip().upper().startswith("SELECT")]
    assert board_selects and not any("jobs.description" in s for s in board_selects)  # heavy column never loaded for the list
    assert any("work_mode" in s and "WHERE" in s for s in board_selects)  # and the filter ran in the database


def test_one_bad_row_does_not_fail_the_batch_on_the_live_site(browser, monkeypatch):
    monkeypatch.setenv("INGEST_TOKEN", "t" * 30)
    real = ingest._upsert

    def upsert(database, row, tags, now):
        if row["id"] == "bad":
            database.add(db.Job(id="bad", title="x" * 5, company="c", url="u", description="d", sponsorship="s" * 1000))
            database.flush()  # the database rejects it (or would, on Postgres)
            raise ValueError("rejected")
        return real(database, row, tags, now)
    monkeypatch.setattr(ingest, "_upsert", upsert)
    rows = [push.job_dict(type("J", (), {f: None for f in db.JOB_FIELDS})()) for _ in range(3)]
    for r, i in zip(rows, ("a", "bad", "c")):
        r.update(id=i, title=f"Job {i}", company="Co", location="Leeds", description="d" * 50, url=f"https://x/{i}", content_hash=i)
    r = browser.client.post("/api/ingest/jobs", json={"jobs": rows}, headers={"Authorization": "Bearer " + "t" * 30})
    assert r.status_code == 200 and r.json()["failed"] == ["bad"] and r.json()["new"] == 2
    with db.SessionLocal() as s:
        assert {j.id for j in s.query(db.Job)} == {"a", "c"}  # the good rows are in, the bad one is not


def test_a_refused_batch_is_retried_one_job_at_a_time_so_one_bad_job_cannot_block_the_rest(monkeypatch, people):
    monkeypatch.setattr(push, "wake", lambda: True)
    for i in range(3):
        make_job(f"p{i}", title=f"Job {i}")
    sent = []

    def post(path, payload):
        if path == "manifest":
            return {"need": [j for j in payload["hashes"]]}
        if path == "jobs":
            ids = [j["id"] for j in payload["jobs"]]
            if len(ids) > 1 or ids == ["p1"]:  # the batch is refused, and p1 is refused even on its own
                raise push.PushError("the live site answered 500 to /jobs: 'boom'")
            sent.extend(ids)
        return {}
    monkeypatch.setattr(push, "_post", post)

    class Run:
        events = []
        def event(self, stage, message, level="info", **k):
            self.events.append((level, message))
    run = Run()
    with db.SessionLocal() as s:
        result = push.publish(s, run)
    assert sorted(sent) == ["p0", "p2"] and result["sent"] == 2  # everyone but the bad job
    assert any(level == "warn" and "skipped" in msg and "Job 1" in msg for level, msg in run.events)
