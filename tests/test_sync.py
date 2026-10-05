"""Sync adds and updates jobs and touches nothing else."""
from datetime import date

from app import db, ingest
from conftest import make_job, make_person, signed_in

LONG = "We build things with Python and AWS, and we look after our people. " * 6


def stage(job_id, **fields):
    data = {"id": job_id, "title": "Junior Developer", "company": "Acme", "location": "Leeds", "description": LONG,
            "url": f"https://example.com/{job_id}", "salary_min": None, "salary_max": None, "sponsorship": "unclear",
            "work_mode": "hybrid", "seniority": "junior", "required_skills": ["Python"], "optional_skills": [],
            "deadline": None, "deadline_quote": ""}
    data.update(fields)
    with db.SessionLocal() as s:
        s.add(db.StagedJob(id=job_id, data=data, tagged=True))
        s.commit()


def test_sync_adds_updates_and_leaves_the_tracker_alone(people):
    make_job("old", description=LONG, deadline=date(2026, 10, 20), deadline_quote="closes 20 October")
    make_job("same", description=LONG)
    wife = signed_in("wife")
    wife.post("/board/old/shortlist")
    with db.SessionLocal() as s:
        entry_id = s.query(db.TrackerEntry).one().id
    wife.post(f"/tracker/{entry_id}/deadline", {"deadline": "2026-10-15"})
    wife.post(f"/tracker/{entry_id}/status", {"status": "applied"})
    wife.post(f"/tracker/{entry_id}/notes", {"notes": "my notes"})

    stage("old", title="Junior Developer (updated)")   # deadline missing in the re-fetch
    stage("same")
    stage("fresh", title="Brand new", deadline="2026-11-02", deadline_quote="closes 2 November")
    with db.SessionLocal() as s:
        summary = ingest.staged_summary(s)
        assert (summary["new"], summary["updated"], summary["unchanged"]) == (1, 1, 1)

    dad = signed_in("dad")
    assert dad.post("/admin/sync/publish").status_code == 303
    with db.SessionLocal() as s:
        assert s.query(db.Job).count() == 3 and s.query(db.StagedJob).count() == 0
        old = s.get(db.Job, "old")
        assert old.title == "Junior Developer (updated)"
        assert old.deadline == date(2026, 10, 20)  # a known deadline is not erased by a re-fetch that misses it
        assert s.get(db.Job, "fresh").deadline == date(2026, 11, 2)
        run = s.query(db.SyncRun).one()
        assert (run.new, run.updated, run.unchanged, run.person_id) == (1, 1, 1, people["dad"])
        entry = s.get(db.TrackerEntry, entry_id)   # the user's work is untouched
        assert entry.status == "applied" and entry.notes == "my notes" and entry.deadline_override == date(2026, 10, 15)
    assert "Brand new" in wife.get("/board").text and "Last updated 5 Oct by Dad" in wife.get("/board").text
    assert "15 Oct" in wife.get("/tracker").text  # still the deadline she set by hand


def test_sync_never_deletes_jobs(people):
    make_job("kept")
    stage("other")
    with db.SessionLocal() as s:
        ingest.sync(s, s.get(db.Person, people["dad"]))
        assert s.get(db.Job, "kept") is not None


def test_fetch_and_tag_stages_jobs_and_can_resume(people, monkeypatch):
    rows = [{"id": f"f{i}", "title": f"Role {i}", "company": "Co", "location": "Leeds", "description": LONG + " Closing date: 30 October 2026.",
             "url": f"https://example.com/f{i}", "salary_min": None, "salary_max": None, "salary_interval": "", "date_posted": ""} for i in range(3)]
    monkeypatch.setattr(ingest, "_scrape", lambda term, location, per: rows)
    state = dict(ingest.STATE, running=True, stop=False)
    ingest.run(["developer"], ["Leeds"], 10, state)
    ingest.run(["developer"], ["Leeds"], 10, state)  # running again adds nothing twice
    with db.SessionLocal() as s:
        staged = s.query(db.StagedJob).all()
        assert len(staged) == 3 and all(j.tagged for j in staged)
        assert staged[0].data["deadline"] == "2026-10-30" and staged[0].data["tag_source"] == "keywords"
        assert s.query(db.Job).count() == 0  # nothing reaches the Board until Sync


def test_sync_page_explains_when_fetching_is_unavailable(people, monkeypatch):
    dad = signed_in("dad")
    monkeypatch.setattr(ingest, "jobspy_available", lambda: False)
    assert "Fetching only works when Job Buddy is running on your laptop" in dad.get("/admin/sync").text
    monkeypatch.setattr(ingest, "jobspy_available", lambda: True)
    assert "Fetch and tag" in dad.get("/admin/sync").text
    dad.post("/admin/sync/settings", {"keywords": "tester, developer", "locations": "Leeds", "per_search": "500"})
    with db.SessionLocal() as s:
        assert ingest.get_settings(s).per_search == 100 and ingest.split_list(ingest.get_settings(s).keywords) == ["tester", "developer"]
