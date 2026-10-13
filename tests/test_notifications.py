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


def test_cv_review_notifications_go_both_ways(people, sent):
    make_job("j1", title="CV job")
    wife, dad = signed_in("wife"), signed_in("dad")
    turn_on(wife)
    dad.post("/push/subscribe", {**GOOD, "endpoint": "https://fcm.googleapis.com/fcm/send/dad1"})
    wife.post("/board/j1/shortlist")
    with db.SessionLocal() as s:
        entry = s.query(db.TrackerEntry).one()
        entry_id = entry.id
        s.add(db.TailoredCV(entry_id=entry_id, text="JANE EXAMPLE\n\nEXPERIENCE\n- Tester at Acme 2022 to 2025\n\nSKILLS\n- Python, SQL"))
        s.commit()
    wife.post(f"/tracker/{entry_id}/cv/save", {"text": "JANE EXAMPLE\n\nEXPERIENCE\n- Tester at Acme 2022 to 2025\n\nSKILLS\n- Python, SQL, more", "then": "send"})
    assert [(pid, p["title"]) for pid, p in sent] == [(people["dad"], "CV to review")]
    assert "Wife sent a CV for CV job" in sent[0][1]["body"]
    sent.clear()
    dad.post(f"/admin/review/{entry_id}", {"decision": "approve"})
    assert [(pid, p["title"]) for pid, p in sent] == [(people["wife"], "Your CV was approved")]
    assert sent[0][1]["url"] == f"/tracker/{entry_id}/cv"


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
