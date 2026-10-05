"""The Tracker: four ways in, statuses with history, deadlines, Apply, and the Calendar built from it."""
from datetime import date

from app import ai, db, linkfetch
from conftest import make_job, signed_in

PAGE = {"url": "https://jobs.example.org/role/42", "title": "Platform Engineer", "company": "Widgets Ltd", "location": "Leeds",
        "description": "We need someone who knows Python and AWS. Closing date: 25 October 2026. " + "More about the role. " * 12,
        "valid_through": ""}


def entries(person_id=None):
    with db.SessionLocal() as s:
        q = s.query(db.TrackerEntry)
        rows = (q.filter(db.TrackerEntry.person_id == person_id) if person_id else q).all()
        for row in rows:
            row.job, row.tailored  # load before the session closes
        return rows


def test_shortlist_adds_one_entry_however_often(people):
    make_job("j1", deadline=date(2026, 10, 8))
    wife = signed_in("wife")
    assert "Shortlist" in wife.get("/board").text
    for _ in range(2):
        assert wife.post("/board/j1/shortlist").status_code == 303
    rows = entries()
    assert len(rows) == 1 and rows[0].source == "shortlisted" and rows[0].added_by_id == people["wife"]
    page = wife.get("/tracker").text
    assert "Added by you" in page and "Closes in 3 days" in page
    assert "In your Tracker" in wife.get("/board").text


def test_add_by_hand_needs_only_title_and_company(people):
    wife = signed_in("wife")
    assert wife.post("/tracker/new", {"title": "", "company": "Co"}).status_code == 400
    assert wife.post("/tracker/new", {"title": "Tester", "company": "Co"}).status_code == 303
    page = wife.get("/tracker").text
    assert "Tester" in page and "No deadline" in page and "Added by you by hand" in page


def test_status_changes_are_kept_with_their_date(people):
    wife = signed_in("wife")
    wife.post("/tracker/new", {"title": "Tester", "company": "Co"})
    entry_id = entries()[0].id
    for status in ("applied", "interviewing", "rejected", "bogus"):
        wife.post(f"/tracker/{entry_id}/status", {"status": status})
    entry = entries()[0]
    assert entry.status == "rejected"
    assert [h["status"] for h in entry.status_history] == ["shortlisted", "applied", "interviewing", "rejected"]
    assert all(h["at"] for h in entry.status_history)


def test_apply_opens_the_advert_without_changing_status(people):
    make_job("j1")
    wife = signed_in("wife")
    wife.post("/board/j1/shortlist")
    entry_id = entries()[0].id
    r = wife.post(f"/tracker/{entry_id}/apply")
    assert r.status_code == 303 and r.headers["location"] == "https://example.com/j1"
    assert entries()[0].status == "shortlisted"
    assert "Did you apply?" in wife.get("/tracker").text
    wife.post(f"/tracker/{entry_id}/applied", {"answer": "yes"})
    assert entries()[0].status == "applied" and "Did you apply?" not in wife.get("/tracker").text


def test_hand_set_deadline_overrides_the_adverts(people):
    make_job("j1", deadline=date(2026, 10, 20))
    wife = signed_in("wife")
    wife.post("/board/j1/shortlist")
    entry_id = entries()[0].id
    wife.post(f"/tracker/{entry_id}/deadline", {"deadline": "2026-10-09"})
    assert "Closes in 4 days" in wife.get("/tracker").text
    wife.post(f"/tracker/{entry_id}/deadline", {"deadline": ""})  # cleared: back to the advert's
    assert "20 Oct" in wife.get("/tracker").text


def test_add_by_link_uses_the_board_format(people, monkeypatch):
    monkeypatch.setattr(linkfetch, "check_url", lambda url: url)
    monkeypatch.setattr(linkfetch, "_get", lambda url: 1 / 0)  # must not be reached: fetch_job is replaced
    monkeypatch.setattr(linkfetch, "fetch_job", lambda url: dict(PAGE))
    wife = signed_in("wife")
    r = wife.post("/tracker/add-link", {"url": PAGE["url"]})
    assert r.status_code == 303
    entry = entries()[0]
    assert entry.source == "link" and entry.job_id is None
    assert entry.details["title"] == "Platform Engineer" and entry.details["deadline"] == "2026-10-25"
    assert "Python" in entry.details["required_skills"]
    page = wife.get("/tracker").text
    assert "Platform Engineer" in page and "from a link" in page
    assert "Platform Engineer" not in wife.get("/board?status=all").text  # private: not on the shared Board


def test_link_already_on_the_board_makes_no_duplicate(people, monkeypatch):
    from app import ingest
    monkeypatch.setattr(linkfetch, "check_url", lambda url: url)
    make_job(ingest.job_id(PAGE["url"]), url=PAGE["url"], title="Already here")
    wife = signed_in("wife")
    wife.post("/tracker/add-link", {"url": PAGE["url"]})
    wife.post("/tracker/add-link", {"url": PAGE["url"]})
    rows = entries()
    assert len(rows) == 1 and rows[0].job_id == ingest.job_id(PAGE["url"]) and rows[0].details is None


def test_unreadable_link_falls_back_to_the_manual_form(people, monkeypatch):
    def unreadable(url):
        raise linkfetch.LinkUnreadable("blocked")
    monkeypatch.setattr(linkfetch, "check_url", lambda url: url)
    monkeypatch.setattr(linkfetch, "fetch_job", unreadable)
    wife = signed_in("wife")
    r = wife.post("/tracker/add-link", {"url": "https://jobs.example.org/x"})
    assert r.status_code == 303 and r.headers["location"].startswith("/tracker/new?url=https%3A%2F%2Fjobs.example.org")
    assert entries() == []  # nothing half-saved
    form = wife.get(r.headers["location"]).text
    assert "couldn&#39;t read that page" in form and 'value="https://jobs.example.org/x"' in form


def test_calendar_follows_the_tracker(people):
    make_job("j1", title="Dated job", company="DatedCo", deadline=date(2026, 10, 23))
    wife = signed_in("wife")
    wife.post("/board/j1/shortlist")
    wife.post("/tracker/new", {"title": "Undated job", "company": "Co"})
    page = wife.get("/calendar").text
    assert "October 2026" in page and "DatedCo" in page and "Fri 23 Oct" in page
    assert "No deadline set" in page and "Undated job" in page
    dated = [e for e in entries() if e.job_id][0]
    wife.post(f"/tracker/{dated.id}/deadline", {"deadline": "2026-11-12"})
    assert "DatedCo" in wife.get("/calendar?m=2026-11").text
    assert "Thu 12 Nov" in wife.get("/calendar").text and "Fri 23 Oct" not in wife.get("/calendar").text


def test_home_matches_the_tracker(people):
    wife = signed_in("wife")
    assert "Getting started" in wife.get("/").text  # brand-new user
    make_job("j1", title="Urgent job", deadline=date(2026, 10, 8))
    make_job("j2", title="Later job", deadline=date(2026, 11, 20))
    wife.post("/board/j1/shortlist")
    wife.post("/board/j2/shortlist")
    page = wife.get("/").text
    assert "Getting started" not in page and "1 deadline this week" in page
    assert "Deadline coming up" in page and "Urgent job" in page
    later = [e for e in entries() if e.job_id == "j1"][0]
    wife.post(f"/tracker/{later.id}/status", {"status": "applied"})
    assert "Nothing urgent today" in wife.get("/").text


def test_tailored_cv_and_review(people, monkeypatch):
    make_job("j1", title="CV job")
    wife = signed_in("wife")
    wife.post("/board/j1/shortlist")
    entry_id = entries()[0].id
    assert "Upload your CV first" in wife.get(f"/tracker/{entry_id}/cv").text  # blocked with a reason
    wife.post("/profile/cv", {"cv_text": "Jane Example. Software tester at Acme 2022 to 2025. Skills: Python, SQL. " * 3})
    assert "Uploaded" in wife.get("/profile").text
    assert "Tailoring isn't available right now" in wife.get(f"/tracker/{entry_id}/cv").text  # no AI key in tests

    monkeypatch.setattr(ai, "available", lambda: True)
    monkeypatch.setattr(ai, "_call_model", lambda prompt: "JANE EXAMPLE\n\nEXPERIENCE\n- Software tester at Acme 2022 to 2025\n\nSKILLS\n- Python, SQL")
    assert wife.post(f"/tracker/{entry_id}/cv/create").status_code == 303
    page = wife.get(f"/tracker/{entry_id}/cv").text
    assert "JANE EXAMPLE" in page and "CV: Draft" in page
    wife.post(f"/tracker/{entry_id}/cv/save", {"text": "JANE EXAMPLE\n\nEXPERIENCE\n- Software tester at Acme 2022 to 2025, edited\n\nSKILLS\n- Python", "then": "send"})
    assert entries()[0].tailored.status == "in_review"

    dad = signed_in("dad")
    assert "CV job" in dad.get("/admin/review").text and "1 CV to review" in dad.get("/admin").text
    dad.post(f"/admin/review/{entry_id}", {"decision": "changes", "comment": ""})
    assert entries()[0].tailored.status == "in_review"  # a comment is needed to ask for changes
    dad.post(f"/admin/review/{entry_id}", {"decision": "changes", "comment": "Shorten the summary"})
    assert entries()[0].tailored.status == "changes_requested"
    assert "Dad asked for changes to your CV" in wife.get("/").text
    assert "Shorten the summary" in wife.get(f"/tracker/{entry_id}/cv").text
    assert "asked for changes to your CV" not in wife.get("/").text  # seen now

    wife.post(f"/tracker/{entry_id}/cv/save", {"text": "JANE EXAMPLE\n\nEXPERIENCE\n- Software tester at Acme 2022 to 2025\n\nSKILLS\n- Python, SQL, short", "then": "send"})
    dad.post(f"/admin/review/{entry_id}", {"decision": "approve"})
    assert entries()[0].tailored.status == "approved"
    wife.post(f"/tracker/{entry_id}/cv/save", {"text": "JANE EXAMPLE\n\nEXPERIENCE\n- Software tester at Acme 2022 to 2025\n\nSKILLS\n- Python, SQL, changed again"})
    assert entries()[0].tailored.status == "draft"      # editing after approval needs review again
    assert wife.get(f"/tracker/{entry_id}/cv/print").status_code == 200
    assert wife.get("/profile/cv/download").status_code == 200
    assert dad.get(f"/admin/users/{people['wife']}/profile").status_code == 200


def test_board_filters_and_hides_closed(people):
    make_job("open1", title="Open role", deadline=date(2026, 11, 1), work_mode="remote")
    make_job("shut1", title="Shut role", deadline=date(2026, 10, 1))
    wife = signed_in("wife")
    page = wife.get("/board").text
    assert "Open role" in page and "Shut role" not in page
    assert "Shut role" in wife.get("/board?status=closed").text
    assert "Open role" not in wife.get("/board?work_mode=onsite").text
    assert "Open role" in wife.get("/board?q=open").text
    assert wife.get("/board/open1").status_code == 200 and wife.get("/board/nope").status_code == 404
