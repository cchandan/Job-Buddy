"""The refresher program that runs on the home computer: its config, schedule, website client, agent loop, command line and installer."""
import json
import os
import stat
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest

from app import db, ingest, refreshers
from conftest import people  # noqa: F401  (the family fixture)
from refresher import agent as agent_mod, config, schedule
from refresher.client import Client, ClientError
from refresher.version import VERSION

ROOT = Path(__file__).resolve().parent.parent
UK = {"enabled": True, "time": "07:30", "tz": "Europe/London"}


def utc(*a):
    return datetime(*a, tzinfo=timezone.utc)


# ---------- config ----------

def test_the_connection_code_from_the_website_is_understood_by_the_tool():
    code = refreshers.connection_code("https://site.example/", "s3cret", "Optiplex at home")
    assert config.parse_connection_code(code) == ("https://site.example", "s3cret", "Optiplex at home")
    assert config.parse_connection_code(f"  {code}\n") == config.parse_connection_code(code)


@pytest.mark.parametrize("bad,words", [("", "starts with jbr1"), ("hello", "starts with jbr1"), ("jbr1.@@@", "damaged")])
def test_a_bad_code_says_what_to_do(bad, words):
    with pytest.raises(ValueError, match=words):
        config.parse_connection_code(bad)


def test_the_secret_is_saved_for_this_user_only(tmp_path):
    config.save("https://site.example", "s3cret", "Optiplex", tmp_path)
    assert config.load(tmp_path) == {"site_url": "https://site.example", "token": "s3cret", "name": "Optiplex"}
    assert stat.S_IMODE((tmp_path / "refresher.json").stat().st_mode) == 0o600
    assert config.load(tmp_path / "nowhere") is None
    config.save_state({"attempts": 2}, tmp_path)
    assert config.load_state(tmp_path) == {"attempts": 2} and config.load_state(tmp_path / "nowhere") == {}


def test_the_job_buddy_code_is_pointed_at_this_computers_own_database_and_the_website(tmp_path, monkeypatch):
    keys = ("DATABASE_URL", "JOBBUDDY_NO_DOTENV", "JOBBUDDY_NO_SEED", "INGEST_URL", "INGEST_TOKEN", "JOBBUDDY_REFRESHER")
    for key in keys:
        monkeypatch.setenv(key, os.environ.get(key, ""))  # so every one is put back afterwards
    config.setup_environment({"site_url": "https://site.example", "token": "tok", "name": "Optiplex"}, tmp_path)
    assert os.environ["DATABASE_URL"] == f"sqlite:///{tmp_path}/data/refresher.db" and os.environ["JOBBUDDY_NO_SEED"] == "1"
    assert os.environ["INGEST_URL"] == "https://site.example" and os.environ["INGEST_TOKEN"] == "tok"
    assert os.environ["JOBBUDDY_REFRESHER"] == "Optiplex" and (tmp_path / "data").is_dir() and (tmp_path / "logs").is_dir()


# ---------- the schedule ----------

def test_the_morning_refresh_is_due_once_a_day_after_the_set_time():
    assert not schedule.due(UK, {}, utc(2026, 10, 13, 5, 0))                       # 06:00 UK, too early
    assert schedule.due(UK, {}, utc(2026, 10, 13, 6, 30))                          # 07:30 UK (BST): on time
    assert schedule.due(UK, {}, utc(2026, 10, 13, 14, 0))                          # the computer was off at 07:30: catch up
    done = {"attempt_date": "2026-10-13", "last_status": "done", "attempts": 1}
    assert not schedule.due(UK, done, utc(2026, 10, 13, 15, 0))                    # already refreshed today
    assert schedule.due(UK, done, utc(2026, 10, 14, 6, 31))                        # and again tomorrow
    assert not schedule.due({**UK, "enabled": False}, {}, utc(2026, 10, 13, 12, 0))


def test_a_failed_refresh_is_retried_an_hour_later_up_to_three_times():
    failed = lambda n, at: {"attempt_date": "2026-10-13", "last_status": "failed", "attempts": n, "last_attempt_at": at.isoformat()}  # noqa: E731
    first = utc(2026, 10, 13, 6, 30)
    assert not schedule.due(UK, failed(1, first), first + timedelta(minutes=30))
    assert schedule.due(UK, failed(1, first), first + timedelta(hours=1))
    assert schedule.due(UK, failed(2, first), first + timedelta(hours=1))
    assert not schedule.due(UK, failed(3, first), first + timedelta(hours=5))      # three goes is enough for one day


def test_the_next_run_follows_summer_and_winter_time():
    assert schedule.next_run(UK, utc(2026, 10, 13, 8, 0)) == utc(2026, 10, 14, 6, 30)   # BST: 07:30 is 06:30 UTC
    assert schedule.next_run(UK, utc(2026, 10, 13, 5, 0)) == utc(2026, 10, 13, 6, 30)
    assert schedule.next_run(UK, utc(2026, 12, 1, 5, 0)) == utc(2026, 12, 1, 7, 30)     # GMT: the same
    assert schedule.next_run({**UK, "enabled": False}) is None


# ---------- the website client ----------

def client_with(handler):
    return Client("https://site.example", "tok", transport=httpx.MockTransport(handler))


def test_the_client_sends_its_secret_and_returns_the_reply():
    seen = {}

    def handler(request):
        seen["auth"], seen["body"] = request.headers["authorization"], json.loads(request.content)
        return httpx.Response(200, json={"config": {}, "command": None})
    assert client_with(handler).checkin({"version": "1"}) == {"config": {}, "command": None}
    assert seen == {"auth": "Bearer tok", "body": {"version": "1"}}


@pytest.mark.parametrize("response,words", [(httpx.Response(401), "doesn't recognise this computer"), (httpx.Response(500), "answered 500"),
                                              (httpx.Response(200, text="not json"), "wasn't understood")])
def test_a_problem_is_explained_in_words(response, words):
    with pytest.raises(ClientError, match=words):
        client_with(lambda request: response).checkin({})


def test_no_internet_is_a_clear_error_not_a_crash():
    def down(request):
        raise httpx.ConnectError("no route")
    with pytest.raises(ClientError, match="could not reach the website"):
        client_with(down).checkin({})


# ---------- the agent ----------

class FakeRunner:
    def __init__(self):
        self.is_running, self.started, self.stopped, self.config, self.outcome = False, 0, 0, None, {"status": "complete", "message": "5 new"}

    def start(self):
        self.started += 1
        self.is_running = True
        return True

    def running(self):
        return self.is_running

    def stop(self):
        self.stopped += 1

    def progress(self):
        return {"running": self.is_running, "phase": "Fetching" if self.is_running else "Done", "message": "", "percent": 40 if self.is_running else 0,
                "now": "indeed: developer" if self.is_running else "", "found": 12, "new": 3}

    def result(self):
        return self.outcome

    def apply_config(self, cfg):
        self.config = cfg

    def finish(self, **outcome):
        self.is_running = False
        self.outcome = outcome or self.outcome


class FakeClient:
    def __init__(self):
        self.reports, self.reply, self.error = [], {"config": {"schedule": UK, "people": []}, "command": None, "expected_version": VERSION}, None

    def checkin(self, report):
        self.reports.append(report)
        if self.error:
            raise self.error
        return dict(self.reply)


@pytest.fixture
def rig(tmp_path):
    runner, client = FakeRunner(), FakeClient()
    agent = agent_mod.Agent({"site_url": "https://site.example", "token": "t", "name": "Optiplex"}, client, runner, tmp_path)
    return agent, client, runner


def test_a_check_in_reports_how_things_are_and_applies_the_websites_settings(rig):
    agent, client, runner = rig
    runner.is_running = True
    assert agent.tick(utc(2026, 10, 13, 5, 0)) is True
    r = client.reports[0]
    assert r["version"] == VERSION and r["state"] == "running" and r["phase"] == "Fetching" and r["percent"] == 40
    assert r["now"] == "indeed: developer" and r["found"] == 12 and r["next_run_at"] == "2026-10-13T06:30:00+00:00" and r["host"]
    assert runner.config == client.reply["config"]


def test_the_morning_refresh_starts_once_and_is_remembered(rig):
    agent, client, runner = rig
    agent.tick(utc(2026, 10, 13, 5, 0))
    assert runner.started == 0                          # too early
    agent.tick(utc(2026, 10, 13, 6, 31))
    assert runner.started == 1 and runner.running()
    agent.tick(utc(2026, 10, 13, 6, 32))
    assert runner.started == 1                          # still running: not started twice
    runner.finish(status="complete", message="5 new, 0 updated")
    agent.tick(utc(2026, 10, 13, 7, 0))
    assert runner.started == 1                          # done for the day
    assert client.reports[-1]["last_run"]["status"] == "complete" and client.reports[-1]["last_run"]["message"] == "5 new, 0 updated"
    agent.tick(utc(2026, 10, 14, 6, 31))
    assert runner.started == 2                          # and again tomorrow


def test_run_now_starts_at_once_and_counts_as_todays_refresh(rig):
    agent, client, runner = rig
    client.reply = {**client.reply, "command": "run_now"}
    agent.tick(utc(2026, 10, 13, 3, 0))                 # in the middle of the night
    assert runner.started == 1
    runner.finish()
    client.reply = {**client.reply, "command": None}
    agent.tick(utc(2026, 10, 13, 6, 40))
    assert runner.started == 1                          # the 07:30 refresh is not repeated


def test_run_now_while_running_does_nothing_and_stop_stops(rig):
    agent, client, runner = rig
    runner.is_running = True
    client.reply = {**client.reply, "command": "run_now"}
    agent.tick(utc(2026, 10, 13, 8, 0))
    assert runner.started == 0
    client.reply = {**client.reply, "command": "stop"}
    agent.tick(utc(2026, 10, 13, 8, 1))
    assert runner.stopped == 1


def test_a_failed_refresh_is_tried_again_an_hour_later(rig):
    agent, client, runner = rig
    agent.tick(utc(2026, 10, 13, 6, 31))
    runner.finish(status="failed", message="No source is available")
    agent.tick(utc(2026, 10, 13, 6, 40))
    assert runner.started == 1 and client.reports[-1]["last_run"]["status"] == "failed"
    agent.tick(utc(2026, 10, 13, 7, 0))
    assert runner.started == 1                          # too soon
    agent.tick(utc(2026, 10, 13, 7, 32))
    assert runner.started == 2


def test_the_morning_refresh_still_happens_when_the_website_cannot_be_reached(rig):
    agent, client, runner = rig
    agent.tick(utc(2026, 10, 13, 5, 0))                 # learns the schedule
    client.error = ClientError("could not reach the website")
    assert agent.tick(utc(2026, 10, 13, 6, 35)) is False
    assert runner.started == 1


def test_a_problem_applying_settings_never_stops_the_loop(rig):
    agent, client, runner = rig
    runner.apply_config = lambda cfg: 1 / 0
    assert agent.tick(utc(2026, 10, 13, 6, 31)) is True and runner.started == 1


def test_run_forever_survives_an_unexpected_error_and_keeps_going(rig):
    agent, client, runner = rig
    calls = []
    agent.tick = lambda: calls.append(1) or (1 / 0)
    agent.run_forever(sleep=lambda s: None, should_stop=lambda: len(calls) >= 3)
    assert len(calls) == 3


# ---------- settings copied from the website ----------

def test_the_websites_searches_and_sources_are_copied_into_the_local_database(people):
    from refresher import local
    local.apply_config({"people": [{"username": "wife", "name": "Wife", "keywords": "developer, tester", "locations": "Leeds", "avoid": "senior",
                                    "distance": 30, "per_search": 50, "remote_uk": True, "uk_wide": False, "needs_sponsorship": True, "enabled": True},
                                   {"username": "newcomer", "name": "New Comer", "keywords": "analyst", "enabled": True}],
                        "sources_off": ["glassdoor", "linkedin"]})
    with db.SessionLocal() as s:
        wife = s.query(db.Person).filter_by(username="wife").one()
        p = s.get(db.SearchProfile, wife.id)
        assert p.keywords == "developer, tester" and p.distance == 30 and p.remote_uk and p.needs_sponsorship and p.enabled
        new = s.query(db.Person).filter_by(username="newcomer").one()
        assert new.role == "user" and s.get(db.SearchProfile, new.id).keywords == "analyst"
        assert ingest.disabled_sources(s) == {"glassdoor", "linkedin"}
        brother = s.query(db.Person).filter_by(username="brother").one()  # not in the list any more
        assert s.get(db.SearchProfile, brother.id) is None or not s.get(db.SearchProfile, brother.id).enabled


# ---------- the command line ----------

def test_pairing_checks_the_code_with_the_website_and_saves_it(tmp_path, monkeypatch, capsys):
    from refresher import __main__ as cli
    seen = []
    monkeypatch.setattr(Client, "checkin", lambda self, report: seen.append(report) or {})
    code = refreshers.connection_code("https://site.example", "s3cret", "Optiplex")
    cli.main(["--home", str(tmp_path), "pair", code])
    assert "Connected to https://site.example as “Optiplex”" in capsys.readouterr().out
    assert config.load(tmp_path)["token"] == "s3cret" and seen[0]["version"] == VERSION


def test_pairing_with_a_bad_code_or_a_refusing_website_says_why_and_saves_nothing(tmp_path, monkeypatch):
    from refresher import __main__ as cli
    with pytest.raises(SystemExit, match="starts with jbr1"):
        cli.main(["--home", str(tmp_path), "pair", "nonsense"])

    def refuse(self, report):
        raise ClientError("the website doesn't recognise this computer any more")
    monkeypatch.setattr(Client, "checkin", refuse)
    with pytest.raises(SystemExit, match="Couldn't connect"):
        cli.main(["--home", str(tmp_path), "pair", refreshers.connection_code("https://s", "t", "n")])
    assert config.load(tmp_path) is None


def test_status_works_before_and_after_pairing(tmp_path, capsys):
    from refresher import __main__ as cli
    cli.main(["--home", str(tmp_path), "status"])
    assert "Not connected yet" in capsys.readouterr().out
    config.save("https://site.example", "tok", "Optiplex", tmp_path)
    config.save_state({"last_run": {"status": "complete", "at": "2026-10-13T06:30:00+00:00", "message": "5 new"}}, tmp_path)
    cli.main(["--home", str(tmp_path), "status"])
    out = capsys.readouterr().out
    assert "https://site.example" in out and "“Optiplex”" in out and "complete at 2026-10-13 06:30 UTC. 5 new" in out and "JobSpy" in out


def test_the_module_runs_from_the_command_line():
    out = subprocess.run([sys.executable, "-m", "refresher", "--help"], cwd=ROOT, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0 and "run" in out.stdout and "pair" in out.stdout and "status" in out.stdout


# ---------- the installer ----------

def test_the_scripts_are_valid_bash_and_executable():
    for name in ("install.sh", "uninstall.sh"):
        path = ROOT / "refresher" / name
        assert subprocess.run(["bash", "-n", str(path)]).returncode == 0, name
        assert os.access(path, os.X_OK), name


def test_the_installer_covers_every_step_a_brother_needs():
    text = (ROOT / "refresher" / "install.sh").read_text()
    for needle in ("python3 -V", "python3-venv", "pip", "pair", "systemctl --user", "enable-linger", "Restart=always", "WantedBy=default.target",
                   "ln -sf", "jbr", "set -euo pipefail"):
        assert needle in text, needle


def run_installer(tmp_path, *args, source=ROOT):
    env = {**os.environ, "JBR_HOME": str(tmp_path / "home"), "JBR_SKIP_PIP": "1", "JBR_SKIP_SERVICE": "1", "HOME": str(tmp_path)}
    return subprocess.run(["bash", str(source / "refresher" / "install.sh"), *args], capture_output=True, text=True, env=env, timeout=120)


def test_the_installer_copies_the_program_and_rejects_a_bad_code_without_leaving_a_half_connection(tmp_path):
    out = run_installer(tmp_path, "not-a-code")
    assert out.returncode == 1 and "couldn't connect with that code" in out.stdout + out.stderr
    home = tmp_path / "home"
    assert (home / "app" / "db.py").exists() and (home / "refresher" / "agent.py").exists() and (home / "venv" / "bin" / "python").exists()
    assert not (home / "refresher.json").exists() and not (home / "jbr").exists()


def test_the_installer_says_plainly_when_the_download_is_incomplete(tmp_path):
    partial = tmp_path / "partial"
    (partial / "refresher").mkdir(parents=True)
    (partial / "refresher" / "install.sh").write_text((ROOT / "refresher" / "install.sh").read_text())
    out = run_installer(tmp_path, "x", source=partial)
    assert out.returncode == 1 and "'app' is missing" in out.stdout + out.stderr and "Download the whole Job Buddy ZIP again" in out.stdout + out.stderr


def test_the_readme_for_the_brother_has_the_steps_and_the_commands():
    text = (ROOT / "refresher" / "README.md").read_text()
    for needle in ("Download ZIP", "bash refresher/install.sh", "jbr status", "jbr logs", "jbr refresh", "jbr uninstall", "Out of date", "jbr1.", "Admin → Refresher"):
        assert needle in text, needle
