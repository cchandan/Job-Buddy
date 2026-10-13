"""Admin > Jobs sync: tick which sources to search, and run scrape, liveness and publish separately."""
import pytest

from app import db, ingest, liveness, push, sources
from conftest import signed_in
from test_sync import lab, raw, set_profile  # noqa: F401  (the fake world of sources and the helpers around it)


@pytest.fixture
def spy(monkeypatch, lab):  # noqa: F811
    """Liveness and publishing are replaced with recorders, and `order` says which stages actually ran."""
    order = []
    real_fetch = sources.fetch

    def fetch(*a, **k):
        if "scrape" not in order:
            order.append("scrape")
        return real_fetch(*a, **k)
    monkeypatch.setattr(sources, "fetch", fetch)
    monkeypatch.setattr(liveness, "check", lambda s, run, stop=None: order.append("liveness") or
                        {"checked": 3, "closed": 1, "open": 2, "unknown": 0})
    monkeypatch.setattr(push, "configured", lambda: True)
    monkeypatch.setattr(push, "publish", lambda s, run, stop=None: order.append("publish") or {"sent": 2, "seen": 0, "closed": 0})
    monkeypatch.setattr(push, "send_log", lambda run_id: None)
    lab.jobs["indeed"] = [raw(1)]
    return order


def run(stages):
    state = dict(ingest.STATE, running=True, stop=False)
    return ingest.refresh(None, state, stages=stages), state


def sync_runs():
    with db.SessionLocal() as s:
        return s.query(db.SyncRun).count()


def last_summary():
    with db.SessionLocal() as s:
        return s.query(db.RunLog).order_by(db.RunLog.id.desc()).first().summary


# ---------- running one stage at a time ----------

def test_scrape_alone_does_not_check_pages_or_publish(spy):
    status, state = run(("scrape",))
    assert status == "complete" and spy == ["scrape"]
    with db.SessionLocal() as s:
        assert s.query(db.Job).count() == 1
    assert sync_runs() == 1 and last_summary()["stages"] == ["scrape"]
    assert state["message"] == "1 new, 0 updated, 0 unchanged, 0 closed."


def test_liveness_alone_searches_nothing_and_does_not_count_as_a_sync(spy, lab):  # noqa: F811
    status, state = run(("liveness",))
    assert status == "complete" and spy == ["liveness"] and lab.calls == []
    assert sync_runs() == 0  # "the Job Board was last updated" only moves when jobs are scraped
    assert last_summary()["checked"] == 3 and last_summary()["closed"] == 1 and last_summary()["stages"] == ["liveness"]
    assert state["message"] == "Checked 3 old jobs: 1 closed."


def test_publish_alone_sends_what_is_here_and_searches_nothing(spy, lab):  # noqa: F811
    status, state = run(("publish",))
    assert status == "complete" and spy == ["publish"] and lab.calls == []
    assert sync_runs() == 0 and last_summary()["pushed"]["sent"] == 2
    assert state["message"] == "Published 2 new or changed jobs."


def test_every_step_runs_in_order_when_asked(spy):
    status, _ = run(ingest.STAGES)
    assert status == "complete" and spy == ["scrape", "liveness", "publish"]
    assert sync_runs() == 1


def test_the_old_calls_still_mean_what_they_did(spy):
    state = dict(ingest.STATE, running=True, stop=False)
    ingest.refresh(None, state, check_pages=False)  # no stages given: scrape and publish, no page checks
    assert spy == ["scrape", "publish"]


def test_stages_always_run_in_the_same_order(spy):
    run(("publish", "scrape"))
    assert spy == ["scrape", "publish"]


def test_a_stage_run_that_cannot_start_says_why_and_fails(spy):
    set_profile("wife", keywords="")
    status, state = run(("scrape", "publish"))
    assert status == "failed" and "Nobody has job titles" in state["message"] and sync_runs() == 0
    assert "publish" not in spy  # nothing was scraped, so nothing is sent on


def test_progress_tells_the_page_which_stages_are_running(spy):
    state = dict(ingest.STATE, stages=["liveness"])
    assert ingest.progress(state)["stages"] == ["liveness"]


# ---------- ticking sources ----------

def test_unticked_sources_are_never_searched(spy, lab):  # noqa: F811
    lab.jobs["linkedin"] = [raw(2, "linkedin")]
    dad = signed_in("dad")
    dad.post("/admin/sync/sources", {"source": ["indeed"]})
    with db.SessionLocal() as s:
        assert ingest.disabled_sources(s) == {"linkedin"}
    run(("scrape",))
    assert {site for site, _ in lab.calls} == {"indeed"}
    with db.SessionLocal() as s:
        text = "\n".join(e.message for e in s.query(db.RunEvent).order_by(db.RunEvent.id))
    assert "Using 1 sources: indeed" in text and "Not using linkedin: turned off in Admin > Refresh" in text


def test_the_choice_survives_and_shows_on_the_page(lab):  # noqa: F811
    dad = signed_in("dad")
    dad.post("/admin/sync/sources", {"source": ["linkedin"]})
    page = dad.get("/admin/refresh").text
    assert "Sources saved: LinkedIn." in page
    assert 'value="linkedin" checked' in page and 'value="indeed" checked' not in page
    assert "1 sources ready" in page
    dad.post("/admin/sync/sources", {"source": ["indeed", "linkedin"]})
    assert "2 sources ready" in dad.get("/admin/refresh").text


def test_a_source_that_cannot_run_here_is_greyed_out_and_keeps_its_setting(lab, monkeypatch):  # noqa: F811
    dad = signed_in("dad")
    page = dad.get("/admin/refresh").text
    assert "not available: no key in .env (optional)" in page  # reed and adzuna have no keys
    import re
    assert re.search(r'value="reed"[^>]*disabled', page) and not re.search(r'value="reed"[^>]*checked', page)
    with db.SessionLocal() as s:
        ingest.save_sources(s, ["indeed"])  # linkedin off
    monkeypatch.setattr(sources, "jobspy_available", lambda: False)  # now nothing from JobSpy can run
    with db.SessionLocal() as s:
        ingest.save_sources(s, [])  # saving with everything greyed out must not flip them
        assert ingest.disabled_sources(s) == {"linkedin"}


def test_with_every_source_unticked_a_scrape_is_refused_clearly(lab, monkeypatch):  # noqa: F811
    started = []
    monkeypatch.setattr(ingest, "start", lambda person_id, stages=ingest.STAGES: started.append(stages) or True)
    dad = signed_in("dad")
    dad.post("/admin/sync/sources", {})
    page = dad.get("/admin/refresh").text
    assert "none are ticked" in page and "Every source is turned off" in page
    dad.post("/admin/sync/run/scrape")
    dad.post("/admin/sync/refresh")
    assert started == []
    assert "Every source is turned off" in dad.get("/admin/refresh").text


# ---------- the buttons ----------

def test_each_stage_has_its_own_button_and_starts_only_that_stage(lab, monkeypatch):  # noqa: F811
    started = []
    monkeypatch.setattr(ingest, "start", lambda person_id, stages=ingest.STAGES: started.append(tuple(stages)) or True)
    monkeypatch.setattr(push, "configured", lambda: True)
    dad = signed_in("dad")
    page = dad.get("/admin/refresh").text
    for label in ("Run scrape only", "Run check liveness only", "Run publish only", "Run all steps"):
        assert label in page
    for stage in ingest.STAGES:
        assert dad.post(f"/admin/sync/run/{stage}").status_code == 303
    assert dad.post("/admin/sync/refresh").status_code == 303
    assert started == [("scrape",), ("liveness",), ("publish",), ingest.STAGES]


def test_publish_alone_is_refused_when_publishing_is_not_set_up_but_a_full_run_just_skips_it(lab, monkeypatch):  # noqa: F811
    started = []
    monkeypatch.setattr(ingest, "start", lambda person_id, stages=ingest.STAGES: started.append(tuple(stages)) or True)
    monkeypatch.setattr(push, "configured", lambda: False)
    dad = signed_in("dad")
    assert "INGEST_URL and INGEST_TOKEN" in dad.get("/admin/refresh").text
    dad.post("/admin/sync/run/publish")
    assert started == []
    dad.post("/admin/sync/refresh")
    assert started == [ingest.STAGES]  # as before: publishing is simply skipped


def test_nothing_can_run_on_the_live_site(lab, monkeypatch):  # noqa: F811
    started = []
    monkeypatch.setattr(ingest, "start", lambda person_id, stages=ingest.STAGES: started.append(stages) or True)
    monkeypatch.setattr(sources, "jobspy_available", lambda: False)
    dad = signed_in("dad")
    page = dad.get("/admin/refresh").text
    assert "Refreshing only works when Job Buddy is running on your laptop" in page
    for stage in ingest.STAGES:
        dad.post(f"/admin/sync/run/{stage}")
    assert started == []


def test_a_second_run_while_one_is_going_is_refused(lab, monkeypatch):  # noqa: F811
    monkeypatch.setattr(ingest, "start", lambda person_id, stages=ingest.STAGES: False)
    dad = signed_in("dad")
    dad.post("/admin/sync/run/scrape")
    assert "A refresh is already running" in dad.get("/admin/refresh").text


def test_only_admins_can_use_them_and_unknown_steps_are_404(lab):  # noqa: F811
    wife = signed_in("wife")
    for url in ("/admin/sync/run/scrape", "/admin/sync/sources"):
        assert wife.post(url).status_code == 403
    assert signed_in("dad").post("/admin/sync/run/bogus").status_code == 404


def test_the_log_says_which_steps_a_partial_run_did(spy):
    run(("liveness",))
    page = signed_in("dad").get("/admin/refresh").text
    assert "liveness only" in page and "3 checked" in page
    run(("scrape",))
    assert "scrape only" in signed_in("dad").get("/admin/refresh").text


def test_the_running_panel_only_lists_the_steps_in_play(lab, monkeypatch):  # noqa: F811
    monkeypatch.setitem(ingest.STATE, "running", True)
    monkeypatch.setitem(ingest.STATE, "stages", ["liveness"])
    body = signed_in("dad").get("/admin/sync/status").json()
    assert body["stages"] == ["liveness"]
