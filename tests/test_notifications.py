"""Browser notifications: turning them on, who gets what, and the once-a-day deadline message."""
from datetime import date, datetime
from types import SimpleNamespace

import pytest
from pywebpush import WebPushException

from app import db, notify
from conftest import make_job, signed_in

GOOD = {"endpoint": "https://fcm.googleapis.com/fcm/send/abc123", "p256dh": "B" * 87, "auth": "a" * 22}


@pytest.fixture
def sent(monkeypatch):
    """Notifications switched on, with every would-be push recorded as (person_id, payload) instead of sent."""
    public, private = notify.generate_keys()
    monkeypatch.setenv("VAPID_PUBLIC_KEY", public)
    monkeypatch.setenv("VAPID_PRIVATE_KEY", private)
    log = []
    monkeypatch.setattr(notify, "_post", lambda sub, payload: log.append((sub.person_id, payload)))
    monkeypatch.setattr(notify, "_spawn", lambda fn, *args: fn(*args))  # no background thread in tests
    return log


def turn_on(browser, **over):
    return browser.post("/push/subscribe", {**GOOD, **over})


def test_generated_keys_can_sign(monkeypatch):
    from py_vapid import Vapid
    public, private = notify.generate_keys()
    assert len(notify._b64(b"x")) and Vapid.from_string(private).sign({"aud": "https://fcm.googleapis.com", "sub": "mailto:a@b.co", "exp": 9999999999})
    import base64
    assert len(base64.urlsafe_b64decode(public + "==")) == 65  # an uncompressed P-256 point, as browsers expect


def test_nothing_happens_until_keys_are_set(people):
    wife = signed_in("wife")
    assert "aren't set up" in wife.get("/profile").text
    assert turn_on(wife).status_code == 400
    notify.notify(people["wife"], "x", "y")  # does nothing, raises nothing
    assert signed_in("dad").get("/admin").status_code == 200


def test_turn_on_and_off(people, sent):
    wife, brother = signed_in("wife"), signed_in("brother")
    assert 'id="notify"' in wife.get("/profile").text and "Turn on notifications" in wife.get("/profile").text
    assert 'id="notify"' in signed_in("dad").get("/admin").text
    assert turn_on(wife, endpoint="https://evil.example.com/x").status_code == 400  # only real push services
    assert turn_on(wife, endpoint="http://fcm.googleapis.com/x").status_code == 400
    assert turn_on(wife, p256dh="short").status_code == 400
    assert turn_on(wife).status_code == 200 and turn_on(wife).status_code == 200  # twice is still one row
    with db.SessionLocal() as s:
        assert [r.person_id for r in s.query(db.PushSubscription)] == [people["wife"]]
    brother.post("/push/unsubscribe", {"endpoint": GOOD["endpoint"]})  # not theirs to remove
    with db.SessionLocal() as s:
        assert s.query(db.PushSubscription).count() == 1
    wife.post("/push/unsubscribe", {"endpoint": GOOD["endpoint"]})
    with db.SessionLocal() as s:
        assert s.query(db.PushSubscription).count() == 0


def test_signed_out_cannot_subscribe(browser, sent):
    assert turn_on(browser).status_code == 303  # sent to sign in


def test_test_button_sends_to_that_person_only(people, sent):
    wife = signed_in("wife")
    turn_on(wife)
    assert wife.post("/push/test").json() == {"ok": True, "sent": 1}
    assert [pid for pid, _ in sent] == [people["wife"]]


def test_service_worker_is_served_from_the_root(browser):
    r = browser.get("/sw.js")
    assert r.status_code == 200 and "javascript" in r.headers["content-type"] and "showNotification" in r.text
    assert r.headers["Service-Worker-Allowed"] == "/"


def test_assigning_a_job_notifies_that_user_once(people, sent):
    make_job("j1", title="Graduate Data Analyst", company="Kite")
    turn_on(signed_in("wife"))
    dad = signed_in("dad")
    for _ in range(2):
        dad.post(f"/admin/users/{people['wife']}/assign", {"how": "board", "job_id": "j1"})
    assert len(sent) == 1
    pid, payload = sent[0]
    assert pid == people["wife"] and payload["title"] == "New job from Dad" and "Graduate Data Analyst" in payload["body"]


def titles(sent):
    return [payload["title"] for _, payload in sent]


def subscribe(browser, name):
    return browser.post("/push/subscribe", {**GOOD, "endpoint": f"https://fcm.googleapis.com/fcm/send/{name}"})


def set_last_login(username, when):
    with db.SessionLocal() as s:
        s.query(db.Person).filter_by(username=username).one().last_login = when
        s.commit()


def test_admins_hear_about_sign_ins_and_returning_visits(people, sent):
    from datetime import timedelta
    dad = signed_in("dad")
    subscribe(dad, "dad1")
    wife = signed_in("wife")  # types the password
    assert [(pid, p["title"], p["url"]) for pid, p in sent] == [(people["dad"], "Wife signed in", f"/admin/users/{people['wife']}/tracker")]
    sent.clear()
    wife.get("/tracker")  # straight after: same visit, nothing more
    assert sent == []
    set_last_login("wife", db.utcnow() - timedelta(minutes=20))
    wife.get("/tracker")  # a short gap refreshes "last active" but is not a new visit
    assert sent == []
    set_last_login("wife", db.utcnow() - timedelta(hours=2))
    assert wife.get("/tracker").status_code == 200  # back with the cookie, no password typed
    assert titles(sent) == ["Wife is on Job Buddy"]


def test_an_admin_does_not_hear_about_themselves(people, sent):
    from datetime import timedelta
    dad = signed_in("dad")
    subscribe(dad, "dad1")
    dad.get("/admin")
    set_last_login("dad", db.utcnow() - timedelta(hours=3))
    dad.get("/admin")
    assert sent == []


def test_every_admin_but_the_actor_is_told(people, sent):
    from conftest import make_person
    mum_id = make_person("mum", "admin")
    mum, dad = signed_in("mum"), signed_in("dad")
    subscribe(mum, "mum1")
    subscribe(dad, "dad1")
    sent.clear()
    signed_in("wife")
    assert sorted(pid for pid, _ in sent) == sorted([people["dad"], mum_id])
    assert {p["title"] for _, p in sent} == {"Wife signed in"}


def test_only_new_jobs_and_status_changes_reach_the_admin(people, sent):
    make_job("j1", title="Graduate Data Analyst", company="Kite")
    dad = signed_in("dad")
    subscribe(dad, "dad1")
    wife = signed_in("wife")
    sent.clear()
    wife.post("/board/j1/shortlist")
    with db.SessionLocal() as s:
        entry_id = s.query(db.TrackerEntry).one().id
    wife.post(f"/tracker/{entry_id}/status", {"status": "applied"})
    wife.post(f"/tracker/{entry_id}/status", {"status": "rejected"})
    wife.post("/tracker/new", {"title": "By hand", "company": "Acme"})
    assert titles(sent) == ["Wife added a job to their Tracker", "Wife moved a job to Applied", "Wife moved a job to Rejected",
                            "Wife added a job to their Tracker"]
    assert all(pid == people["dad"] for pid, _ in sent)
    first = sent[0][1]
    assert "Graduate Data Analyst · Kite" in first["body"] and first["url"] == f"/admin/users/{people['wife']}/entry/{entry_id}"
    sent.clear()
    wife.post(f"/tracker/{entry_id}/status", {"status": "rejected"})  # nothing changed, nothing sent


def test_the_chatty_changes_stay_quiet(people, sent):
    make_job("j1")
    dad = signed_in("dad")
    subscribe(dad, "dad1")
    wife = signed_in("wife")
    wife.post("/board/j1/shortlist")
    with db.SessionLocal() as s:
        entry_id = s.query(db.TrackerEntry).one().id
    sent.clear()
    wife.post(f"/tracker/{entry_id}/deadline", {"deadline": "2026-11-01"})
    wife.post(f"/tracker/{entry_id}/notes", {"notes": "Phoned them"})
    wife.post(f"/tracker/{entry_id}/apply")  # opening the advert
    wife.post("/profile/cv", {"cv_text": "Jane Example. Software tester at Acme 2022 to 2025. Skills: Python, SQL. " * 3})
    wife.post("/profile/cv/delete")
    wife.post(f"/tracker/{entry_id}/remove")
    assert sent == []
    wife.post("/board/j1/shortlist")
    with db.SessionLocal() as s:
        entry_id = s.query(db.TrackerEntry).one().id
    sent.clear()
    wife.post(f"/tracker/{entry_id}/applied", {"answer": "yes"})  # the "Did you apply?" yes is a status change
    assert titles(sent) == ["Wife moved a job to Applied"]


def test_assigning_tells_the_user_and_the_other_admins(people, sent):
    from conftest import make_person
    mum_id = make_person("mum", "admin")
    make_job("j1", title="Graduate Data Analyst", company="Kite")
    mum, dad, wife = signed_in("mum"), signed_in("dad"), signed_in("wife")
    subscribe(mum, "mum1")
    turn_on(wife)
    sent.clear()
    dad.post(f"/admin/users/{people['wife']}/assign", {"how": "board", "job_id": "j1"})
    assert {(pid, p["title"]) for pid, p in sent} == {(people["wife"], "New job from Dad"),
                                                      (mum_id, "Wife had a job assigned by Dad")}


def test_a_browser_that_has_gone_is_forgotten(people, sent, monkeypatch):
    def gone(sub, payload):
        raise WebPushException("gone", response=SimpleNamespace(status_code=410))
    monkeypatch.setattr(notify, "_post", gone)
    wife = signed_in("wife")
    turn_on(wife)
    assert wife.post("/push/test").json()["ok"] is False
    with db.SessionLocal() as s:
        assert s.query(db.PushSubscription).count() == 0


def test_a_failing_push_service_never_breaks_the_page(people, sent, monkeypatch):
    make_job("j1")
    monkeypatch.setattr(notify, "_post", lambda sub, payload: (_ for _ in ()).throw(RuntimeError("push service down")))
    turn_on(signed_in("wife"))
    r = signed_in("dad").post(f"/admin/users/{people['wife']}/assign", {"how": "board", "job_id": "j1"})
    assert r.status_code == 303


def test_daily_deadline_message(people, sent):
    make_job("soon", title="Soon role", company="Acme", deadline=date(2026, 10, 6))
    make_job("far", title="Far role", deadline=date(2026, 12, 1))
    wife, brother = signed_in("wife"), signed_in("brother")
    for who, name in ((wife, "wife1"), (brother, "bro1")):
        turn_on(who, endpoint=f"https://fcm.googleapis.com/fcm/send/{name}")
    wife.post("/board/soon/shortlist")
    wife.post("/board/far/shortlist")
    brother.post("/board/far/shortlist")

    assert notify.run_daily_once(datetime(2026, 10, 5, 7, 30)) is False  # too early
    assert sent == []
    assert notify.run_daily_once(datetime(2026, 10, 5, 9, 0)) is True
    assert [pid for pid, _ in sent] == [people["wife"]]  # only the person with something due
    payload = sent[0][1]
    assert payload["title"] == "1 deadline coming up" and "Soon role · Acme" in payload["body"] and "tomorrow" in payload["body"]
    assert "Far role" not in payload["body"]
    assert notify.run_daily_once(datetime(2026, 10, 5, 18, 0)) is False  # once a day
    assert len(sent) == 1
    assert notify.run_daily_once(datetime(2026, 10, 6, 8, 5)) is True  # tomorrow it goes again ("closes today")
    assert "closes today" in sent[1][1]["body"]


def test_applied_jobs_are_not_nagged(people, sent):
    make_job("soon", title="Soon role", deadline=date(2026, 10, 6))
    wife = signed_in("wife")
    turn_on(wife)
    wife.post("/board/soon/shortlist")
    with db.SessionLocal() as s:
        entry = s.query(db.TrackerEntry).one()
        wife.post(f"/tracker/{entry.id}/status", {"status": "applied"})
    notify.run_daily_once(datetime(2026, 10, 5, 9, 0))
    assert sent == []


def test_users_are_nudged_after_three_days_away_then_every_three(people, sent):
    from datetime import timedelta
    wife = signed_in("wife")
    turn_on(wife)
    sent.clear()
    noon = datetime(2026, 10, 5, 12, 0)
    for gone, expect in ((2, False), (3, True), (4, False), (6, True)):
        sent.clear()
        set_last_login("wife", noon - timedelta(days=gone))
        with db.SessionLocal() as s:
            s.query(db.AppSetting).filter_by(key="last_digest").delete()
            s.commit()
        notify.run_daily_once(datetime(2026, 10, 5, 9, 0))
        assert (titles(sent) == [f"You haven't been on Job Buddy for {gone} days"]) is expect, gone
        if not expect:
            assert sent == []


def test_someone_who_never_signed_in_is_counted_from_when_the_account_was_made(people, sent):
    wife = signed_in("wife")
    turn_on(wife)
    with db.SessionLocal() as s:
        row = s.query(db.Person).filter_by(username="wife").one()
        row.last_login, row.created_at = None, datetime(2026, 10, 2, 10, 0)
        s.commit()
    sent.clear()
    notify.run_daily_once(datetime(2026, 10, 5, 9, 0))
    assert titles(sent) == ["You haven't been on Job Buddy for 3 days"]


def test_admins_get_no_inactivity_nudge(people, sent):
    dad = signed_in("dad")
    subscribe(dad, "dad1")
    set_last_login("dad", datetime(2026, 9, 1))
    sent.clear()
    notify.run_daily_once(datetime(2026, 10, 5, 9, 0))
    assert sent == []
