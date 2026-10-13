"""The website's side of the refresher: pairing, check-in, the schedule, Run now, status, alerts, and publishing with its own secret."""
import json
import re
from datetime import timedelta

import pytest

from app import db, ingest, liveness, notify, push, refreshers
from conftest import signed_in
from test_sync import lab, set_profile  # noqa: F401  (a fake world of sources)


def add(admin, name="Optiplex at home"):
    admin.post("/admin/refresher", {"name": name})
    code = re.search(r"jbr1\.[A-Za-z0-9_-]+", admin.get("/admin/refresher").text).group(0)
    _, token, _ = refreshers.parse_connection_code(code)
    return code, token


def checkin(browser, token, **report):
    return browser.client.post("/api/refresher/checkin", json={"version": "1.0.0", "state": "idle", **report},
                               headers={"Authorization": f"Bearer {token}"})


def refresher(rid=1):
    with db.SessionLocal() as s:
        row = s.get(db.Refresher, rid)
        s.expunge(row)
        return row


# ---------- pairing ----------

def test_a_connection_code_carries_the_site_address_the_secret_and_the_name():
    code = refreshers.connection_code("https://site.example/", "s3cret", "Optiplex")
    assert code.startswith("jbr1.") and refreshers.parse_connection_code(code) == ("https://site.example", "s3cret", "Optiplex")


@pytest.mark.parametrize("bad", ["", "hello", "jbr1.", "jbr1.not-base64-json!!", "jbr2.abc"])
def test_a_bad_code_is_refused_in_plain_words(bad):
    with pytest.raises(ValueError):
        refreshers.parse_connection_code(bad)


def test_adding_a_refresher_shows_its_code_once_and_keeps_only_a_hash(people):
    dad = signed_in("dad")
    dad.post("/admin/refresher", {"name": "Optiplex at home"})
    page = dad.get("/admin/refresher").text
    code = re.search(r"jbr1\.[A-Za-z0-9_-]+", page).group(0)
    assert "Connection code for Optiplex at home" in page
    site, token, name = refreshers.parse_connection_code(code)
    assert site.startswith("http") and name == "Optiplex at home"
    assert "jbr1." not in dad.get("/admin/refresher").text  # gone after one view
    row = refresher()
    assert row.token_hash == refreshers.hash_token(token) and token not in row.token_hash
    with db.SessionLocal() as s:
        assert token not in json.dumps([r.token_hash for r in s.query(db.Refresher)])


def test_a_name_is_needed(people):
    dad = signed_in("dad")
    dad.post("/admin/refresher", {"name": "  "})
    assert "Give the computer a name" in dad.get("/admin/refresher").text
    with db.SessionLocal() as s:
        assert s.query(db.Refresher).count() == 0


# ---------- checking in ----------

def test_only_a_paired_secret_can_check_in(people, browser):
    _, token = add(signed_in("dad"))
    assert checkin(browser, token).status_code == 200
    assert checkin(browser, "wrong").status_code == 401
    assert browser.client.post("/api/refresher/checkin", json={}).status_code == 401
    assert browser.client.post("/api/refresher/checkin", content="not json", headers={"Authorization": f"Bearer {token}"}).status_code == 400
    assert browser.client.post("/api/refresher/checkin", json=[1], headers={"Authorization": f"Bearer {token}"}).status_code == 400


def test_a_check_in_is_recorded_and_the_reply_carries_the_config(people, browser):
    set_profile("wife", keywords="developer, tester", locations="Leeds")
    _, token = add(signed_in("dad"))
    r = checkin(browser, token, state="running", phase="Fetching", percent=40, now="indeed: developer", found=12, new=3, host="optiplex",
                last_run={"status": "complete", "at": "2026-10-12T07:30:00", "message": "5 new"}, next_run_at="2026-10-13T06:30:00")
    body = r.json()
    assert body["command"] is None and body["expected_version"] == refreshers.VERSION
    assert body["config"]["schedule"] == {"time": "07:30", "enabled": True, "tz": "Europe/London"}
    wife = next(p for p in body["config"]["people"] if p["username"] == "wife")
    assert wife["keywords"] == "developer, tester" and wife["locations"] == "Leeds" and wife["enabled"] is True
    row = refresher()
    assert row.state == "running" and row.version == "1.0.0" and row.last_seen is not None
    assert row.status["phase"] == "Fetching" and row.status["last_run"]["message"] == "5 new" and row.status["host"] == "optiplex"


def test_sources_the_admin_unticked_are_part_of_the_config(people, browser):
    with db.SessionLocal() as s:
        s.merge(db.AppSetting(key=ingest.SOURCES_KEY, value="glassdoor,linkedin"))
        s.commit()
    _, token = add(signed_in("dad"))
    assert checkin(browser, token).json()["config"]["sources_off"] == ["glassdoor", "linkedin"]


# ---------- the schedule ----------

def test_the_morning_time_is_set_on_the_website(people, browser):
    dad = signed_in("dad")
    _, token = add(dad)
    dad.post("/admin/refresher/schedule", {"time": "6:05", "enabled": "1"})
    assert checkin(browser, token).json()["config"]["schedule"] == {"time": "06:05", "enabled": True, "tz": "Europe/London"}
    dad.post("/admin/refresher/schedule", {"time": "06:05"})  # the box unticked
    assert checkin(browser, token).json()["config"]["schedule"]["enabled"] is False
    assert "The morning refresh is off" in dad.get("/admin/refresher").text
    dad.post("/admin/refresher/schedule", {"time": "25:99", "enabled": "1"})
    assert checkin(browser, token).json()["config"]["schedule"]["time"] == "06:05"  # a bad time changes nothing
    assert "Please give the time like 07:30" in dad.get("/admin/refresher").text


# ---------- Run now and Stop ----------

def test_run_now_is_handed_over_once(people, browser):
    dad = signed_in("dad")
    _, token = add(dad)
    checkin(browser, token)
    dad.post("/admin/refresher/1/run")
    assert refresher().pending_command == "run_now"
    assert "picks this up within a minute" in dad.get("/admin/refresher").text
    assert checkin(browser, token).json()["command"] == "run_now"
    assert checkin(browser, token).json()["command"] is None and refresher().pending_command == ""


def test_run_now_needs_the_refresher_to_be_online_and_not_already_running(people, browser):
    dad = signed_in("dad")
    _, token = add(dad)
    assert 'disabled' in re.search(r'<button class="btn btn--primary"[^>]*>Run now', dad.get("/admin/refresher").text).group(0)  # never heard from
    checkin(browser, token, state="running", phase="Fetching")
    page = dad.get("/admin/refresher").text
    assert "Refreshing now" in page and ">Stop</button>" in page and "Run now" not in page
    dad.post("/admin/refresher/1/run")
    assert refresher().pending_command == "" and "already refreshing" in dad.get("/admin/refresher").text
    dad.post("/admin/refresher/1/stop")
    assert checkin(browser, token, state="running").json()["command"] == "stop"


def test_a_refresher_can_be_removed_and_its_secret_dies(people, browser):
    dad = signed_in("dad")
    _, token = add(dad)
    assert checkin(browser, token).status_code == 200
    dad.post("/admin/refresher/1/remove")
    assert checkin(browser, token).status_code == 401
    assert dad.post("/admin/refresher/1/run").status_code == 404


def test_only_admins_can_see_or_use_any_of_it(people):
    dad = signed_in("dad")
    add(dad)
    wife = signed_in("wife")
    assert wife.get("/admin/refresher").status_code == 403
    for url in ("/admin/refresher", "/admin/refresher/schedule", "/admin/refresher/1/run", "/admin/refresher/1/stop", "/admin/refresher/1/remove"):
        assert wife.post(url, {"name": "x", "time": "07:30"}).status_code == 403, url
    assert refresher() is not None


# ---------- how healthy it looks ----------

def test_health_follows_when_it_last_checked_in(people):
    now = db.utcnow()
    row = db.Refresher(name="x", token_hash="h")
    assert refreshers.health(row, now)[0] == "never"
    for ago, want in ((timedelta(minutes=1), "online"), (timedelta(hours=3), "quiet"), (timedelta(hours=30), "offline")):
        row.last_seen = now - ago
        assert refreshers.health(row, now)[0] == want
    assert refreshers.is_stale(row, now) and not refreshers.is_stale(db.Refresher(name="y", token_hash="i", last_seen=now), now)


def test_the_page_shows_status_progress_and_version(people, browser):
    dad = signed_in("dad")
    _, token = add(dad)
    checkin(browser, token, version="0.9.0", state="running", phase="Fetching", percent=40, now="indeed: developer", found=12, new=3,
            last_run={"status": "complete", "at": "2026-10-12T07:30:00", "message": "5 new, 0 updated"})
    page = dad.get("/admin/refresher").text
    assert ">Online<" in page and "Refreshing now" in page and "Fetching · indeed: developer · 12 found, 3 new" in page
    assert "Complete · 5 new, 0 updated" in page and "Out of date: this site expects 1.0.0" in page
    assert "Every day at 07:30 UK time" in page
    assert "Refresher" in dad.get("/admin").text  # the sidebar link


# ---------- alerts ----------

@pytest.fixture
def sent(monkeypatch):
    public, private = notify.generate_keys()
    monkeypatch.setenv("VAPID_PUBLIC_KEY", public)
    monkeypatch.setenv("VAPID_PRIVATE_KEY", private)
    log = []
    monkeypatch.setattr(notify, "_post", lambda sub, payload: log.append((sub.person_id, payload)))
    monkeypatch.setattr(notify, "_spawn", lambda fn, *a: fn(*a))
    return log


def test_a_failed_run_tells_the_admins_once(people, browser, sent):
    dad = signed_in("dad")
    dad.post("/push/subscribe", {"endpoint": "https://fcm.googleapis.com/fcm/send/dad1", "p256dh": "B" * 87, "auth": "a" * 22})
    _, token = add(dad)
    sent.clear()
    failed = {"status": "failed", "at": "2026-10-12T07:30:00", "message": "Nobody has job titles"}
    checkin(browser, token, last_run=failed)
    checkin(browser, token, last_run=failed)  # the same run again: no second alert
    assert [(p["title"], p["body"]) for _, p in sent] == [("A refresh failed", "Optiplex at home: Nobody has job titles")]


def test_a_refresher_that_goes_quiet_is_flagged_after_a_day_and_then_every_three(people, sent):
    dad = signed_in("dad")
    dad.post("/push/subscribe", {"endpoint": "https://fcm.googleapis.com/fcm/send/dad1", "p256dh": "B" * 87, "auth": "a" * 22})
    add(dad)
    now = db.utcnow()
    results = []
    for days in (0, 1, 2, 3, 4, 6):
        with db.SessionLocal() as s:
            row = s.get(db.Refresher, 1)
            row.last_seen = now - timedelta(days=days, hours=3)
            s.commit()
            sent.clear()
            results.append((days, notify.refresher_alerts(s)))
    assert results == [(0, 0), (1, 1), (2, 0), (3, 1), (4, 0), (6, 1)]
    assert "has gone quiet" in sent[0][1]["title"] and "isn't being refreshed" in sent[0][1]["body"]


def test_no_quiet_alert_when_the_morning_refresh_is_switched_off(people, sent):
    dad = signed_in("dad")
    add(dad)
    dad.post("/admin/refresher/schedule", {"time": "07:30"})
    with db.SessionLocal() as s:
        s.get(db.Refresher, 1).last_seen = db.utcnow() - timedelta(days=1, minutes=5)
        s.commit()
        assert notify.refresher_alerts(s) == 0


# ---------- publishing with its own secret ----------

def test_a_refresher_secret_works_on_the_publish_routes_and_the_site_token_still_does(people, browser, monkeypatch):
    _, token = add(signed_in("dad"))
    post = lambda t: browser.client.post("/api/ingest/manifest", json={"hashes": {}}, headers={"Authorization": f"Bearer {t}"}).status_code  # noqa: E731
    assert post(token) == 200 and post("nope") == 401
    monkeypatch.setenv("INGEST_TOKEN", "x" * 24)
    assert post("x" * 24) == 200 and post(token) == 200


def test_which_search_found_a_job_travels_as_usernames_and_lands_on_the_right_ids(people, browser):
    _, token = add(signed_in("dad"))
    with db.SessionLocal() as s:
        wife = s.query(db.Person).filter_by(username="wife").one().id
    from conftest import make_job
    make_job("j1", title="Sent job", found_by={str(wife): ["developer"]}, matched=[wife])
    with db.SessionLocal() as s:
        usernames = {p.id: p.username for p in s.query(db.Person)}
        payload = push.job_dict(s.get(db.Job, "j1"), usernames)
        s.delete(s.get(db.Job, "j1"))
        s.commit()
    assert payload["found_by"] == {"wife": ["developer"]}
    r = browser.client.post("/api/ingest/jobs", json={"jobs": [{**payload, "description": "x" * 300}]}, headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    with db.SessionLocal() as s:
        assert s.get(db.Job, "j1").found_by == {str(wife): ["developer"]}


# ---------- the run log ----------

def test_a_run_says_which_refresher_it_was_and_its_log_reaches_the_site_even_when_it_failed(lab, monkeypatch):  # noqa: F811
    sent_logs = []
    monkeypatch.setenv("JOBBUDDY_REFRESHER", "Optiplex at home")
    monkeypatch.setattr(push, "configured", lambda: True)
    monkeypatch.setattr(push, "send_log", lambda run_id: sent_logs.append(run_id))
    monkeypatch.setattr(liveness, "check", lambda s, run, stop=None: {"checked": 0, "closed": 0, "open": 0, "unknown": 0})
    ingest.refresh(None, dict(ingest.STATE, running=True, stop=False), stages=("liveness",))
    with db.SessionLocal() as s:
        assert s.query(db.RunLog).order_by(db.RunLog.id.desc()).first().summary["refresher"] == "Optiplex at home"
    set_profile("wife", keywords="")
    status = ingest.refresh(None, dict(ingest.STATE, running=True, stop=False), stages=("scrape", "publish"))
    assert status == "failed" and len(sent_logs) == 2  # the failed run's log was sent too
