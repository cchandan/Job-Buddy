"""The bin: expired jobs and jobs from removed searches leave the Job Board, nothing is deleted, no Tracker is touched."""
from datetime import date, datetime, timedelta

from app import db, ingest, jobbin, liveness, push
from conftest import TODAY, make_job, signed_in
from test_sync import lab, raw, set_profile  # noqa: F401  (the fake world of sources)


def job(job_id):
    with db.SessionLocal() as s:
        row = s.get(db.Job, job_id)
        s.expunge(row)
        return row


def scrape():
    state = dict(ingest.STATE, running=True, stop=False)
    return ingest.refresh(None, state, stages=("scrape",)), state


def wife_id():
    with db.SessionLocal() as s:
        return s.query(db.Person).filter_by(username="wife").one().id


# ---------- expired jobs ----------

def test_expired_jobs_are_binned_and_open_ones_are_not(people):
    make_job("open1", title="Open role", deadline=date(2026, 12, 1))
    make_job("shut1", title="Closed by a page check", closed_at=datetime(2026, 10, 1), close_reason="page gone (404)")
    make_job("late1", title="Past its closing date", deadline=date(2026, 9, 20))
    with db.SessionLocal() as s:
        assert jobbin.sweep_expired(s, TODAY) == 2
    assert job("open1").binned_at is None
    assert job("shut1").bin_reason == "Expired: page gone (404)"
    assert job("late1").bin_reason.startswith("Expired: the closing date (20 Sep) has passed")


def test_binned_jobs_leave_the_board_but_the_page_still_opens(people):
    make_job("shut1", title="Gone role", closed_at=datetime(2026, 10, 1), close_reason="page gone (404)")
    make_job("open1", title="Still here")
    with db.SessionLocal() as s:
        jobbin.sweep_expired(s, TODAY)
    for who in ("wife", "dad"):
        page = signed_in(who).get("/board?status=").text
        assert "Still here" in page and "Gone role" not in page, who
    assert "Gone role" not in signed_in("wife").get("/board?status=closed").text
    page = signed_in("wife").get("/board/shut1")
    assert page.status_code == 200 and "This job is in the bin" in page.text and "page gone (404)" in page.text


def test_a_tracked_job_stays_in_the_tracker_when_it_is_binned(people):
    make_job("shut1", title="Tracked role")
    wife = signed_in("wife")
    wife.post("/board/shut1/shortlist")
    with db.SessionLocal() as s:
        j = s.get(db.Job, "shut1")
        j.closed_at, j.close_reason = datetime(2026, 10, 1), "page gone (404)"
        s.commit()
        jobbin.sweep_expired(s, TODAY)
    assert job("shut1").binned_at is not None
    assert "Tracked role" in wife.get("/tracker").text  # the Tracker still shows it
    with db.SessionLocal() as s:
        entry = s.query(db.TrackerEntry).one()
        assert entry.job_id == "shut1" and entry.status == "shortlisted"
    assert wife.get(f"/tracker/{entry.id}").status_code == 200


def test_a_page_check_that_finds_a_job_gone_puts_it_in_the_bin(people):
    make_job("old1", title="Old role", url="https://example.com/old1", first_seen=datetime(2026, 9, 1), last_seen=datetime(2026, 9, 1))
    with db.SessionLocal() as s:
        result = liveness.check(s, fetch=lambda url: (404, url, ""), pause=(0, 0), today=TODAY)
    assert result["closed"] == 1
    assert job("old1").binned_at is not None and job("old1").bin_reason == "Expired: page gone (404)"


def test_a_scrape_tidies_expired_jobs_into_the_bin_and_says_so(lab):  # noqa: F811
    make_job("late1", title="Past its date", deadline=date(2026, 9, 1))
    lab.jobs["indeed"] = [raw(1)]
    status, _ = scrape()
    assert status == "complete" and job("late1").binned_at is not None
    with db.SessionLocal() as s:
        assert any("moved to the bin" in e.message for e in s.query(db.RunEvent))


# ---------- which search found a job ----------

def test_a_scrape_records_which_search_term_found_each_job(lab):  # noqa: F811
    lab.jobs["indeed"] = [raw(1)]
    scrape()
    with db.SessionLocal() as s:
        found = s.query(db.Job).one().found_by
    assert found == {str(wife_id()): ["developer"]}


def test_two_searches_finding_the_same_job_are_both_remembered(lab):  # noqa: F811
    set_profile("wife", keywords="developer, engineer")
    lab.jobs["indeed"] = [raw(1)]
    scrape()
    with db.SessionLocal() as s:
        assert s.query(db.Job).one().found_by == {str(wife_id()): ["developer", "engineer"]}


# ---------- jobs found only by removed keywords ----------

def test_removing_a_keyword_makes_its_jobs_candidates_for_the_bin(lab):  # noqa: F811
    lab.jobs["indeed"] = [raw(1)]
    scrape()
    with db.SessionLocal() as s:
        assert jobbin.candidates(s, TODAY)["irrelevant"] == []  # the search is still there
    set_profile("wife", keywords="tester")
    with db.SessionLocal() as s:
        found = jobbin.candidates(s, TODAY)["irrelevant"]
    assert len(found) == 1 and "found only by “developer”" in found[0][1]


def test_a_job_another_keyword_still_finds_is_not_a_candidate(lab):  # noqa: F811
    set_profile("wife", keywords="developer, engineer")
    lab.jobs["indeed"] = [raw(1)]
    scrape()
    set_profile("wife", keywords="engineer")  # "developer" removed, but "engineer" found it too
    with db.SessionLocal() as s:
        assert jobbin.candidates(s, TODAY)["irrelevant"] == []


def test_a_job_found_by_anothers_search_stays_when_one_person_drops_it(lab):  # noqa: F811
    set_profile("brother", keywords="developer", locations="Leeds")  # the family fixture already has a brother
    lab.jobs["indeed"] = [raw(1)]
    scrape()
    set_profile("wife", keywords="tester")  # wife dropped it, brother still searches for it
    with db.SessionLocal() as s:
        assert jobbin.candidates(s, TODAY)["irrelevant"] == []


def test_jobs_someone_tracks_are_never_binned_for_being_off_topic(lab):  # noqa: F811
    lab.jobs["indeed"] = [raw(1)]
    scrape()
    with db.SessionLocal() as s:
        job_id = s.query(db.Job).one().id
    signed_in("wife").post(f"/board/{job_id}/shortlist")
    set_profile("wife", keywords="tester")
    with db.SessionLocal() as s:
        assert jobbin.candidates(s, TODAY)["irrelevant"] == []


def test_old_jobs_with_no_record_are_counted_but_never_judged(lab):  # noqa: F811
    make_job("legacy1", title="Found before the record existed")
    set_profile("wife", keywords="tester")
    with db.SessionLocal() as s:
        found = jobbin.candidates(s, TODAY)
    assert found["irrelevant"] == [] and found["unknown"] == 1


def test_the_button_moves_only_what_the_admin_confirms_and_restore_keeps_it_back(lab):  # noqa: F811
    lab.jobs["indeed"] = [raw(1)]
    scrape()
    with db.SessionLocal() as s:
        job_id = s.query(db.Job).one().id
    dad = signed_in("dad")
    set_profile("wife", keywords="tester")
    assert job(job_id).binned_at is None  # nothing moves until the button is pressed
    page = dad.get("/admin/bin").text
    assert "1 no longer relevant job" in page and "Move 1 job to the bin" in page
    dad.post("/admin/bin/tidy")
    assert job(job_id).binned_at is not None and job(job_id).bin_reason.startswith("No longer relevant")
    assert "Role 1" not in signed_in("wife").get("/board?view=all").text
    dad.post("/admin/bin/restore", {"job": [job_id]})
    assert job(job_id).binned_at is None and job(job_id).bin_kept is True
    with db.SessionLocal() as s:
        assert jobbin.candidates(s, TODAY)["irrelevant"] == []  # a restored job is left alone


def test_a_job_found_again_by_a_current_search_comes_out_of_the_bin(lab):  # noqa: F811
    lab.jobs["indeed"] = [raw(1)]
    scrape()
    with db.SessionLocal() as s:
        j = s.query(db.Job).one()
        jobbin.bin_jobs(s, [(j, "No longer relevant: test")])
        job_id = j.id
    lab.jobs["indeed"] = [raw(1, description="A changed advert about Python and AWS. " * 8)]
    scrape()
    assert job(job_id).binned_at is None


def test_saving_a_search_says_how_many_jobs_the_removed_keywords_leave_behind(lab):  # noqa: F811
    lab.jobs["indeed"] = [raw(1)]
    scrape()
    dad = signed_in("dad")
    dad.post(f"/admin/sync/profile/{wife_id()}", {"keywords": "tester", "locations": "Leeds", "enabled": "1"})
    page = dad.get("/admin/sync").text
    assert "1 job on the Job Board was found only by keywords that are no longer in anyone" in page.replace("&#39;", "'")


# ---------- the Bin page ----------

def test_the_bin_page_is_for_admins_and_lists_what_is_inside(people):
    make_job("shut1", title="Gone role", closed_at=datetime(2026, 10, 1), close_reason="page gone (404)")
    with db.SessionLocal() as s:
        jobbin.sweep_expired(s, TODAY)
    dad = signed_in("dad")
    page = dad.get("/admin/bin").text
    assert "Gone role" in page and "Expired: page gone (404)" in page and "Restore selected" in page
    assert "Gone role" in dad.get("/admin/bin?kind=expired").text and "Gone role" not in dad.get("/admin/bin?kind=relevance").text
    assert '<span class="count">1</span>' in dad.get("/admin").text  # the sidebar count
    wife = signed_in("wife")
    assert wife.get("/admin/bin").status_code == 403
    assert wife.post("/admin/bin/tidy").status_code == 403 and wife.post("/admin/bin/restore", {"job": ["shut1"]}).status_code == 403
    assert job("shut1").binned_at is not None


def test_restoring_an_expired_job_reopens_it(people):
    make_job("shut1", title="Gone role", closed_at=datetime(2026, 10, 1), close_reason="page gone (404)")
    with db.SessionLocal() as s:
        jobbin.sweep_expired(s, TODAY)
    signed_in("dad").post("/admin/bin/restore", {"job": ["shut1"]})
    j = job("shut1")
    assert j.binned_at is None and j.closed_at is None and j.bin_kept is True
    assert "Gone role" in signed_in("wife").get("/board").text
    with db.SessionLocal() as s:
        assert jobbin.sweep_expired(s, TODAY) == 0  # not binned again


# ---------- the live site ----------

def test_the_bin_reaches_the_live_site_through_the_normal_publish(people):
    make_job("shut1", title="Gone role", closed_at=datetime(2026, 10, 1), close_reason="page gone (404)")
    plain = push.signature("abc", ["wife"])
    assert push.signature("abc", ["wife"], binned=True) != plain and plain == "abc|wife"  # the old signature is unchanged
    with db.SessionLocal() as s:
        jobbin.sweep_expired(s, TODAY)
        data = push.job_dict(s.get(db.Job, "shut1"))
    assert data["binned_at"] and data["bin_reason"] == "Expired: page gone (404)"


def test_the_live_site_stores_the_bin_state_it_is_sent_and_a_plain_update_unbins(browser, monkeypatch, people):
    monkeypatch.setenv("INGEST_TOKEN", "x" * 24)
    headers = {"Authorization": "Bearer " + "x" * 24}
    base = {"id": "j9", "title": "Sent job", "company": "Co", "location": "Leeds", "url": "https://e.example/9", "description": "x" * 300,
            "content_hash": "h1", "matched": []}
    send = lambda row: browser.client.post("/api/ingest/jobs", json={"jobs": [row]}, headers=headers)  # noqa: E731
    assert send({**base, "binned_at": "2026-10-04T09:00:00", "bin_reason": "Expired: page gone (404)"}).status_code == 200
    assert job("j9").binned_at == datetime(2026, 10, 4, 9) and job("j9").bin_reason == "Expired: page gone (404)"
    need = browser.client.post("/api/ingest/manifest", json={"hashes": {"j9": push.signature("h1", [], binned=True)}}, headers=headers).json()
    assert need["need"] == []  # the live site holds the same version
    assert send({**base, "binned_at": None, "bin_reason": ""}).status_code == 200
    assert job("j9").binned_at is None
    send({**base, "binned_at": "2026-10-04T09:00:00"})
    send({**base, "content_hash": "h2"})  # an update that says nothing about the bin is a scrape: the job is open again
    assert job("j9").binned_at is None
