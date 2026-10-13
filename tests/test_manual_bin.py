"""An admin can bin any advert by hand. It stays binned until an admin restores it, and no Tracker is touched."""
from app import db, ingest
from conftest import make_job, signed_in
from test_sync import lab, raw  # noqa: F401  (the fake world of sources)


def job(job_id):
    with db.SessionLocal() as s:
        row = s.get(db.Job, job_id)
        s.expunge(row)
        return row


def test_an_admin_bins_an_advert_with_a_reason_and_it_leaves_the_board(people):
    make_job("j1", title="Odd advert")
    make_job("j2", title="Fine advert")
    dad = signed_in("dad")
    assert dad.post("/admin/bin/add/j1", {"reason": "Not a real job", "next": "/board"}).status_code == 303
    j = job("j1")
    assert j.binned_at is not None and j.bin_reason == "Binned by hand by Dad: Not a real job" and j.bin_kept is False
    page = signed_in("wife").get("/board?status=").text
    assert "Fine advert" in page and "Odd advert" not in page
    assert "Odd advert" in dad.get("/admin/bin?kind=manual").text and "Fine advert" not in dad.get("/admin/bin?kind=manual").text
    assert "Odd advert" not in dad.get("/admin/bin?kind=expired").text


def test_the_reason_is_optional(people):
    make_job("j1")
    signed_in("dad").post("/admin/bin/add/j1", {})
    assert job("j1").bin_reason == "Binned by hand by Dad"


def test_the_buttons_are_for_admins_only_and_not_offered_for_a_binned_advert(people):
    make_job("j1", title="Odd advert")
    dad, wife = signed_in("dad"), signed_in("wife")
    assert "/admin/bin/add/j1" in dad.get("/board").text and "/admin/bin/add/j1" in dad.get("/board/j1").text
    assert "/admin/bin/add/j1" not in wife.get("/board").text and "/admin/bin/add/j1" not in wife.get("/board/j1").text
    assert wife.post("/admin/bin/add/j1", {}).status_code == 403 and job("j1").binned_at is None
    dad.post("/admin/bin/add/j1", {})
    assert "/admin/bin/add/j1" not in dad.get("/board/j1").text  # already in the bin


def test_binning_something_unknown_is_a_404_and_binning_twice_changes_nothing(people):
    make_job("j1")
    dad = signed_in("dad")
    assert dad.post("/admin/bin/add/nope", {}).status_code == 404
    dad.post("/admin/bin/add/j1", {"reason": "first"})
    first = job("j1")
    dad.post("/admin/bin/add/j1", {"reason": "second"})
    assert job("j1").bin_reason == first.bin_reason and job("j1").binned_at == first.binned_at


def test_the_redirect_only_ever_stays_on_the_site(people):
    make_job("j1")
    r = signed_in("dad").post("/admin/bin/add/j1", {"next": "https://evil.example.com"})
    assert r.status_code == 303 and r.headers["location"] == "/board"


def test_a_tracked_advert_can_be_binned_and_the_tracker_keeps_it(people):
    make_job("j1", title="Tracked advert")
    wife = signed_in("wife")
    wife.post("/board/j1/shortlist")
    signed_in("dad").post("/admin/bin/add/j1", {})
    assert job("j1").binned_at is not None
    assert "Tracked advert" in wife.get("/tracker").text
    with db.SessionLocal() as s:
        entry = s.query(db.TrackerEntry).one()
        assert entry.job_id == "j1" and entry.status == "shortlisted"


def test_a_scrape_finding_the_advert_again_does_not_undo_a_hand_bin(lab):  # noqa: F811
    lab.jobs["indeed"] = [raw(1)]
    state = dict(ingest.STATE, running=True, stop=False)
    ingest.refresh(None, state, stages=("scrape",))
    with db.SessionLocal() as s:
        job_id = s.query(db.Job).one().id
    signed_in("dad").post(f"/admin/bin/add/{job_id}", {"reason": "spam"})
    for round_ in range(2):  # once unchanged ("seen"), once with a changed advert ("updated")
        if round_:
            lab.jobs["indeed"] = [raw(1, description="A changed advert about Python and AWS. " * 8)]
        ingest.refresh(None, dict(ingest.STATE, running=True, stop=False), stages=("scrape",))
        j = job(job_id)
        assert j.binned_at is not None and j.bin_reason.startswith("Binned by hand"), round_


def test_restoring_a_hand_binned_advert_brings_it_back_for_good(people):
    make_job("j1", title="Odd advert")
    dad = signed_in("dad")
    dad.post("/admin/bin/add/j1", {})
    dad.post("/admin/bin/restore", {"job": ["j1"]})
    j = job("j1")
    assert j.binned_at is None and j.bin_kept is True
    assert "Odd advert" in signed_in("wife").get("/board").text


def test_the_live_site_is_told_why_it_was_binned(people):
    from app import push
    make_job("j1")
    signed_in("dad").post("/admin/bin/add/j1", {"reason": "spam"})
    with db.SessionLocal() as s:
        data = push.job_dict(s.get(db.Job, "j1"))
    assert data["binned_at"] and data["bin_reason"] == "Binned by hand by Dad: spam"
