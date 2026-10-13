"""The refresher's loop: check in with the website about once a minute, follow what it says, refresh every morning, report back.

It never lets one failure stop it: no internet, a website restart, a scrape that falls over. It logs the problem and tries again.
"""
import logging
import socket
import time
from datetime import datetime, timezone

from . import config, schedule
from .client import ClientError
from .version import VERSION

log = logging.getLogger("jobbuddy.refresher")
INTERVAL = 60  # seconds between check-ins


class IngestRunner:
    """Runs the real Job Buddy refresh (scrape, check old jobs, publish) in the background, on this computer."""

    def __init__(self):
        from app import db, ingest  # imported late: the environment must be set first
        self.db, self.ingest = db, ingest
        db.init_db()

    def start(self):
        return self.ingest.start(None)

    def running(self):
        return bool(self.ingest.STATE["running"])

    def stop(self):
        self.ingest.stop()

    def progress(self):
        p = self.ingest.progress()
        return {"running": p["running"], "phase": p["phase"], "message": p["message"], "percent": p["percent"], "now": p["now"],
                "found": p["found"], "new": p["new"]}

    def result(self):
        """How the run that just ended went: {"status", "message"}."""
        from app import runlog
        with self.db.SessionLocal() as s:
            recent = runlog.recent(s, 1)
            status = recent[0][0].status if recent else "failed"
        return {"status": status, "message": self.ingest.STATE.get("message") or ""}

    def apply_config(self, cfg):
        from . import local
        local.apply_config(cfg)


class Agent:
    def __init__(self, connection, client, runner, base=None, interval=INTERVAL):
        self.connection, self.client, self.runner, self.base, self.interval = connection, client, runner, base, interval
        self.schedule = {"enabled": True, "time": "07:30", "tz": "Europe/London"}
        self.was_running = False
        self.expected_version = VERSION

    # ----- what it reports -----

    def state(self):
        return config.load_state(self.base)

    def report(self, now=None):
        progress = self.runner.progress()
        state = self.state()
        nxt = schedule.next_run(self.schedule, now)
        return {"version": VERSION, "state": "running" if progress["running"] else "idle", "phase": progress["phase"] if progress["running"] else "",
                "message": progress["message"], "percent": progress["percent"], "now": progress["now"], "found": progress["found"],
                "new": progress["new"], "last_run": state.get("last_run"), "next_run_at": nxt.isoformat() if nxt else None,
                "host": socket.gethostname()}

    # ----- one turn of the loop -----

    def _finish_if_done(self, now):
        """Notice a run that has just ended and remember how it went."""
        if self.was_running and not self.runner.running():
            self.was_running = False
            result = self.runner.result()
            state = self.state()
            state["last_status"] = "failed" if result["status"] == "failed" else "done"
            state["last_run"] = {"status": result["status"], "at": now.isoformat(), "message": result["message"]}
            config.save_state(state, self.base)
            log.info("Refresh finished: %s. %s", result["status"], result["message"])

    def _start(self, now, reason):
        state = self.state()
        today = schedule.local_now(self.schedule.get("tz", "Europe/London"), now).date().isoformat()
        if reason == "requested":  # asked for on the website: counts as today's refresh
            state["attempts"] = 0
        else:
            state["attempts"] = state.get("attempts", 0) + 1 if state.get("attempt_date") == today else 1
        state["attempt_date"], state["last_attempt_at"], state["last_status"] = today, now.isoformat(), "running"
        config.save_state(state, self.base)
        if self.runner.start():
            self.was_running = True
            log.info("Refresh started (%s).", reason)
        else:
            log.warning("A refresh was due but one is already running.")

    def tick(self, now=None):
        now = now or datetime.now(timezone.utc)
        self._finish_if_done(now)
        try:
            reply = self.client.checkin(self.report(now))
        except ClientError as e:
            # The website can't be reached right now. Carry on with the last schedule it gave us, so the morning refresh still happens.
            log.warning("Check-in failed: %s", e)
            if not self.runner.running() and schedule.due(self.schedule, self.state(), now):
                self._start(now, "scheduled")
            return False
        cfg = reply.get("config") or {}
        self.schedule = cfg.get("schedule") or self.schedule
        self.expected_version = reply.get("expected_version") or VERSION
        try:
            self.runner.apply_config(cfg)
        except Exception:
            log.exception("Could not apply the website's settings; carrying on with the last ones.")
        command = reply.get("command")
        if command == "stop" and self.runner.running():
            log.info("Stop requested on the website.")
            self.runner.stop()
        elif command == "run_now" and not self.runner.running():
            self._start(now, "requested")
        elif not self.runner.running() and schedule.due(self.schedule, self.state(), now):
            self._start(now, "scheduled")
        return True

    def run_forever(self, sleep=time.sleep, should_stop=lambda: False):
        log.info("Job Buddy Refresher %s started for %s.", VERSION, self.connection["site_url"])
        while not should_stop():
            try:
                self.tick()
            except Exception:  # whatever happens, the next turn still comes
                log.exception("Unexpected problem; carrying on.")
            sleep(self.interval)
