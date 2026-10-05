"""One-click refresh: fetch from every source -> tag only what is new or changed -> publish -> check a few old jobs.

Runs on an admin's laptop, never the server. Every step is saved as it goes, so a stop, a rate limit or a closed laptop
loses nothing: press Refresh again and it carries on, and jobs it already knows are not tagged again.

Writes ONLY jobs, sync_runs, fetch_state and the run log. It never deletes a job and never touches a Tracker entry, a
status, a hand-set deadline or a CV.
"""
import logging
import re
import threading
import time
from datetime import date, datetime, timedelta

from . import ai, db, liveness, push, runlog, sources, sponsors, tagging
from .sources import job_id  # noqa: F401  (also used by tracking.py)

log = logging.getLogger("jobbuddy")
FIRST_RUN_HOURS = 24 * 30
MIN_HOURS, BUFFER_HOURS = 24, 24
SWEEP_DAYS, SWEEP_HOURS = 7, 168  # once a week every search looks back a full week, in case a run was missed
SEARCH_TIMEOUT = 420  # seconds: a search that hangs (LinkedIn does) is given up on, so one site cannot stall the whole run
MAX_WANTED = 1000  # JobSpy's own ceiling per search
BUDGET = {"linkedin": 40, "glassdoor": 25}  # searches per run for the sites that rate-limit; the rest wait for the next run
PACE_SECONDS = {"linkedin": 3.0, "glassdoor": 2.0}
DEEPEST = {"linkedin": 100, "glassdoor": 100}  # these load every advert page, so asking for more gets very slow; Indeed is quick
BREAKER = 2  # a source that fails this many searches in a row is skipped for the rest of the run

STATE = {"running": False, "stop": False, "phase": "", "fetched": 0, "tagged": 0, "total": 0, "message": "", "run_id": None,
         "plan": {}, "now": "", "started": 0.0, "new": 0, "seen": 0, "notes": []}
_lock = threading.Lock()


def progress(state=STATE):
    """What the live panel shows: overall percent and time left (for the searches), per-source bars, what it is doing now."""
    plan = {k: dict(v) for k, v in state["plan"].items()}
    total, done = sum(p["total"] for p in plan.values()), sum(p["done"] for p in plan.values())
    elapsed = time.time() - state["started"] if state["started"] else 0
    eta = elapsed / done * (total - done) if done >= 3 and total > done else None  # a rough guess: searches are uneven
    return {"running": state["running"], "phase": state["phase"], "message": state["message"], "now": state["now"],
            "percent": round(100 * done / total) if total else 0, "eta_seconds": eta, "elapsed": elapsed,
            "plan": plan, "found": state["fetched"], "new": state["new"], "seen": state["seen"], "notes": state["notes"][-3:],
            "stopping": state["stop"]}


def jobspy_available():
    return sources.jobspy_available()


def split_list(text):
    return [part.strip() for part in (text or "").replace("\n", ",").split(",") if part.strip()]


UK_CITIES = ["London", "Manchester", "Leeds", "Bristol", "Edinburgh", "Glasgow", "Reading", "Cambridge", "Leamington Spa",
             "Guildford", "Sheffield"]
# ponytail: starter searches live here, keyed by first name; the admin edits them on Admin > Jobs sync. Move to a file if more people join.
PRESETS = {
    "priya": dict(
        keywords="business analyst, technical business analyst, Appian business analyst, functional consultant, product owner, "
                 "solutions consultant, sales development representative, technical account manager, customer success manager, "
                 "sales engineer, sales executive, NHS business analyst, HMRC, Birmingham City Council, project support officer, "
                 "digital delivery",
        locations="Birmingham", distance=25, remote_uk=True, uk_wide=False, needs_sponsorship=False,
        avoid="director, head of, vice president, software developer, software engineer"),
    "akanksh": dict(
        keywords="graduate software developer, graduate software engineer, associate software engineer, junior software developer, "
                 "graduate scheme technology, graduate cloud engineer, graduate devops engineer, tax technology, "
                 "tax technology consultant, tax technology analyst, junior game developer, graduate games programmer, "
                 "junior unity developer, graduate technology analyst",
        locations="Birmingham", distance=30, remote_uk=True, uk_wide=False, needs_sponsorship=True,
        avoid="senior, lead, principal, staff, manager, director, head of, 5+ years"),
}


def profiles(database):
    """One search profile per user, created (from the starter searches above, if their name matches) the first time."""
    out = []
    for person in database.query(db.Person).filter(db.Person.role == "user").order_by(db.Person.name):
        row = database.get(db.SearchProfile, person.id)
        if row is None:
            row = db.SearchProfile(person_id=person.id, **PRESETS.get(person.first_name.lower(), {}))
            database.add(row)
            database.commit()
        out.append(row)
    return out


def _plan(rows):
    """{(term, location, distance, remote): [person ids]}: the same search for two people runs once."""
    plan = {}
    for p in rows:
        if not p.enabled:
            continue
        places = split_list(p.locations) + (UK_CITIES if p.uk_wide else [])
        for term in split_list(p.keywords):
            for place in places:
                plan.setdefault((term, place, p.distance, False), []).append(p.person_id)
            if p.remote_uk:
                plan.setdefault((term, "United Kingdom", p.distance, True), []).append(p.person_id)
    return plan


def _avoids(title, words):
    return any(re.search(r"(?<!\w)" + re.escape(w.lower()) + r"(?!\w)", (title or "").lower()) for w in words)


# ---------- merging the same job seen on several sites ----------

def _merge(into, raw):
    for f in ("sources", "source_urls"):
        into.setdefault(f, [])
    if raw["site"] not in into["sources"]:
        into["sources"].append(raw["site"])
        into["source_urls"].append(raw["url"])
    if len(raw.get("description") or "") > len(into.get("description") or ""):
        into["description"] = raw["description"]
        into["partial"] = raw.get("partial", False)
    for f in ("apply_url", "job_type", "date_posted", "salary_min", "salary_max", "salary_interval", "salary_currency",
              "company_industry", "job_level", "expires", "reed_id"):
        if not into.get(f) and raw.get(f):
            into[f] = raw[f]
    if raw.get("is_remote") is True or into.get("is_remote") is None:
        into["is_remote"] = raw.get("is_remote", into.get("is_remote"))
    into["matched"] = sorted(set(into.get("matched") or []) | set(raw.get("matched") or []))
    into["content_hash"] = sources.content_hash(into)
    return into


def _first(raw):
    raw = dict(raw)
    raw["sources"], raw["source_urls"] = [raw["site"]], [raw["url"]]
    return raw


# ---------- saving ----------

def _keep_deadline(job, row):
    """A re-fetch that no longer finds the deadline does not erase one we already know."""
    if row["deadline"] is None and job.deadline is not None:
        row["deadline"], row["deadline_quote"] = job.deadline, job.deadline_quote
    return row


def _upsert(database, raw, tags, now):
    """Add or update one job from a raw dict plus its tags. Returns "new" or "updated"."""
    row = db.job_row({**raw, **tags})
    job = database.get(db.Job, raw["id"])
    if job is None:
        database.add(db.Job(**row, first_seen=now, last_seen=now))
        return "new"
    row = _keep_deadline(job, row)
    row["sources"] = sorted(set(job.sources or []) | set(row["sources"]))
    known_urls = list(job.source_urls or [])
    row["source_urls"] = known_urls + [u for u in row["source_urls"] if u not in known_urls]
    row["matched"] = sorted(set(job.matched or []) | set(row["matched"]))
    for field, value in row.items():
        if field != "id":
            setattr(job, field, value)
    job.last_seen, job.closed_at, job.close_reason = now, None, ""  # found again: it is open
    return "updated"


def _backfill_keys(database):
    """Jobs from before this version get their de-dup key, so they match what the sites return."""
    todo = database.query(db.Job).filter(db.Job.canonical_key.is_(None)).all()
    for job in todo:
        job.canonical_key = sources.canonical_key(job.title, job.company, job.location)
    if todo:
        database.commit()


def _known_index(database):
    by_key, by_id = {}, {}
    for j in database.query(db.Job.id, db.Job.canonical_key, db.Job.content_hash, db.Job.needs_ai):
        entry = {"id": j.id, "hash": j.content_hash or "", "needs_ai": bool(j.needs_ai)}
        by_key.setdefault(j.canonical_key, entry)
        by_id[j.id] = entry
    return by_key, by_id


# ---------- the run ----------

def _hours_old(last_ok, now):
    if last_ok is None:
        return FIRST_RUN_HOURS
    hours = int((now - last_ok).total_seconds() // 3600) + BUFFER_HOURS
    return min(max(hours, MIN_HOURS), FIRST_RUN_HOURS)


def _fetch_all(run, picked, plan, avoid, per_search, state, on_batch):
    """Every source x planned search; each search's jobs are saved (on_batch) BEFORE it counts as done. Returns {source: stats}.
    Searches that fail, time out or run out of budget keep their old "last worked" time, so the next run looks back further."""
    stats = {s: {"searches": 0, "failed": 0, "returned": 0, "error": ""} for s in picked}
    state["plan"] = {src: {"total": min(sum(1 for (_, _, _, r) in plan if not (r and src in ("reed", "adzuna"))), BUDGET.get(src, 10**9)),
                           "done": 0, "found": 0, "status": "waiting"} for src in picked}
    now = db.utcnow()
    with db.SessionLocal() as s:
        last_ok = {r.key: r.last_ok for r in s.query(db.FetchState)}
    sweep = last_ok.get("sweep")
    sweep_due = sweep is None or (now - sweep) > timedelta(days=SWEEP_DAYS)
    for source in picked:
        in_a_row, done, skipped = 0, 0, 0
        card = state["plan"][source]
        card["status"] = "running"
        queue = sorted(plan.items(), key=lambda kv: last_ok.get(f"{source}|{kv[0]}") or datetime.min)  # longest-waiting first
        for (term, location, distance, remote), people in queue:
            if state["stop"]:
                return stats
            if in_a_row >= BREAKER:
                break
            if remote and source in ("reed", "adzuna"):
                continue
            if done >= BUDGET.get(source, 10**9):
                skipped += 1
                continue
            key = f"{source}|{(term, location, distance, remote)}"
            hours = _hours_old(last_ok.get(key), now)
            if sweep_due:
                hours = max(hours, SWEEP_HOURS)
            started, wanted = time.time(), per_search
            state["now"] = f"{source}: “{term}” in {location}{' (remote)' if remote else ''}"
            try:
                rows = _with_timeout(lambda: sources.fetch(source, term, location, hours, wanted, distance, remote), SEARCH_TIMEOUT)
                limit = DEEPEST.get(source, MAX_WANTED)
                while len(rows) >= wanted and wanted < limit:  # it filled up, so there are probably more: ask for more
                    wanted = min(wanted * 2, limit)
                    rows = _with_timeout(lambda: sources.fetch(source, term, location, hours, wanted, distance, remote), SEARCH_TIMEOUT)
            except Exception as e:
                stats[source]["failed"] += 1
                stats[source]["error"] = type(e).__name__
                in_a_row += 1
                card["done"] += 1
                run.event("fetch", f"{source}: “{term}” in {location} failed ({_plain(e)}).", level="warn", source=source)
                if in_a_row >= BREAKER:
                    card["status"] = "failed"
                    card["done"] = card["total"]  # the rest are skipped, so the bar completes
                    run.event("fetch", f"{source} failed {BREAKER} searches in a row, so it is skipped for the rest of this run. "
                              "The other sources carry on.", level="warn", source=source)
                continue
            in_a_row, done = 0, done + 1
            stats[source]["searches"] += 1
            stats[source]["returned"] += len(rows)
            batch = {}
            for raw in rows:
                raw["matched"] = [pid for pid in people if not _avoids(raw["title"], avoid.get(pid, []))]
                if not raw["matched"]:
                    continue  # every person this search was for rules it out by title
                ckey = raw["canonical_key"]
                batch[ckey] = _merge(batch[ckey], raw) if ckey in batch else _first(raw)
            try:
                result = on_batch(list(batch.values()))  # saved first...
            except Exception as e:
                stats[source]["failed"] += 1
                stats[source]["error"] = type(e).__name__
                run.event("fetch", f"{source}: “{term}” in {location}: could not save its jobs ({_plain(e)}); it will be tried again next time.",
                          level="error", source=source)
                continue
            with db.SessionLocal() as s:  # ...and only then is the search marked done
                s.merge(db.FetchState(key=key, last_ok=db.utcnow()))
                s.commit()
            last_ok[key] = db.utcnow()
            state["fetched"] += len(batch)
            card["done"] += 1
            card["found"] += len(batch)
            run.event("fetch", f"{source}: “{term}” in {location}{' (remote)' if remote else ''}: {len(rows)} jobs, {len(batch)} kept "
                      f"({result}) in {time.time() - started:.0f}s.", source=source)
            time.sleep(PACE_SECONDS.get(source, 0))
        if card["status"] == "running":
            card["status"] = "done"
        if skipped:
            run.event("fetch", f"{source}: {skipped} searches wait for the next refresh (pacing, so the site does not block us). "
                      "They go first next time.", source=source)
    if not state["stop"] and sweep_due:
        with db.SessionLocal() as s:
            s.merge(db.FetchState(key="sweep", last_ok=db.utcnow()))
            s.commit()
    return stats


def _with_timeout(fn, seconds):
    """Run fn() but stop waiting after `seconds`. The abandoned call finishes (or dies) in the background."""
    box = {}

    def work():
        try:
            box["value"] = fn()
        except BaseException as e:  # noqa: BLE001  (handed back to the caller below)
            box["error"] = e
    t = threading.Thread(target=work, daemon=True)
    t.start()
    t.join(seconds)
    if t.is_alive():
        raise TimeoutError("timed out")
    if "error" in box:
        raise box["error"]
    return box["value"]


def _plain(e):
    name = type(e).__name__
    text = str(e).lower()
    if "429" in text or "rate" in text or "too many" in text:
        return "rate limited"
    if "403" in text or "blocked" in text or "captcha" in text:
        return "blocked by the site"
    if "timeout" in name.lower() or "timed out" in text:
        return "timed out"
    return name


def refresh(person_id=None, state=STATE, check_pages=True):
    """The whole job. Returns the run's status: complete / partial / failed. Whatever was fetched is already saved, so even a
    crash or a Stop still publishes what there is and writes the log."""
    state.update(phase="Planning", message="", fetched=0, tagged=0, total=0, plan={}, now="", started=time.time(), new=0, seen=0, notes=[])
    run = runlog.Run(hook=lambda level, message: state["notes"].append(message))
    state["run_id"] = run.id
    ai.reset_backends()
    summary, crashed = {}, False
    try:
        summary = _refresh(run, person_id, state, check_pages) or {}
    except Exception as e:
        crashed = True
        log.exception("refresh crashed")
        run.event("plan", f"The refresh stopped unexpectedly ({type(e).__name__}). Jobs found before that are saved; press Refresh to carry on.",
                  level="error")
    pushed = None
    if push.configured():
        state["phase"] = "Publishing"
        try:
            with db.SessionLocal() as s:
                pushed = push.publish(s, run)
        except Exception as e:
            run.event("push", f"Publishing to the online site failed ({_plain(e)}). Your jobs are safe here; press Refresh to try again.",
                      level="error")
    with db.SessionLocal() as s:
        s.add(db.SyncRun(person_id=person_id, new=summary.get("new", 0), updated=summary.get("updated", 0),
                         unchanged=summary.get("seen", 0), failed=0, seen=summary.get("seen", 0), closed=summary.get("closed", 0)))
        s.commit()
    stopped = state["stop"]
    failed = crashed or not summary
    status = run.finish("failed" if failed else "partial" if stopped else None, pushed=pushed, **summary)
    if push.configured():
        try:
            push.send_log(run.id)
        except Exception:
            pass  # the log stays on this laptop; the next refresh sends the jobs again anyway
    state.update(phase="Stopped" if stopped or failed else "Done", running=False, now="",
                 message=(f"{summary.get('new', 0)} new, {summary.get('updated', 0)} updated, {summary.get('seen', 0)} unchanged, "
                          f"{summary.get('closed', 0)} closed.") if summary else "Something went wrong. Press Refresh to carry on.")
    return status


def _save(run, raws, backends, state, totals):
    """Tag and store one search's jobs. Known jobs that did not change are only marked as still listed. Returns a short summary."""
    with db.SessionLocal() as s:
        by_key, by_id = _known_index(s)
    todo, seen = [], []
    for raw in raws:
        known = by_key.get(raw["canonical_key"]) or by_id.get(raw["id"])
        if known is None:
            raw["_new"] = True
            todo.append(raw)
            continue
        raw["id"] = known["id"]
        if known["hash"] != raw["content_hash"] or (known["needs_ai"] and backends):
            todo.append(raw)
        else:
            seen.append(raw)
    state["total"] += len(todo)
    new = updated = 0
    for start in range(0, len(todo), tagging.BATCH_SIZE):
        chunk = todo[start:start + tagging.BATCH_SIZE]
        for raw in chunk:
            if raw["site"] == "reed" and raw.get("partial"):
                try:
                    sources.hydrate(raw)
                except Exception as e:
                    run.event("fetch", f"reed: could not load the full advert ({_plain(e)}); using the short version.", level="warn", source="reed")
        tags, stats = tagging.tag_batch(chunk, today=date.today(), use_ai=bool(backends))
        for k in ("ai_calls", "avoided", "keyword_only", "ai_failed_calls"):
            totals["tag"][k] += stats[k]
        totals["tag"]["backends"].update(stats["backends"])
        if stats["note"]:
            run.event("tag", f"AI was not available for some jobs ({stats['note']}). Keywords were used; they will be upgraded next run.", level="warn")
        now = db.utcnow()
        with db.SessionLocal() as s:
            for raw in chunk:
                outcome = _upsert(s, raw, tags[raw["id"]], now)
                new, updated = new + (outcome == "new"), updated + (outcome == "updated")
            s.commit()
        state["tagged"] += len(chunk)
    now = db.utcnow()
    with db.SessionLocal() as s:
        for r in seen:  # a job one person has may now match another
            job = s.get(db.Job, r["id"])
            if job is not None and set(r.get("matched") or []) - set(job.matched or []):
                job.matched = sorted(set(job.matched or []) | set(r["matched"]))
        ids = [r["id"] for r in seen]
        for i in range(0, len(ids), 500):
            s.query(db.Job).filter(db.Job.id.in_(ids[i:i + 500])).update(
                {"last_seen": now, "closed_at": None, "close_reason": ""}, synchronize_session=False)
        s.commit()
    totals["new"] += new
    totals["updated"] += updated
    totals["seen"] += len(seen)
    state["new"], state["seen"] = totals["new"], totals["seen"]
    return f"{new} new, {updated} updated, {len(seen)} already known"


def _refresh(run, person_id, state, check_pages):
    with db.SessionLocal() as s:
        rows = profiles(s)
        plan = _plan(rows)
        avoid = {p.person_id: split_list(p.avoid) for p in rows}
        per_search = max([p.per_search or 100 for p in rows] or [100])
        names = {p.person_id: p.person.first_name for p in rows if p.enabled and split_list(p.keywords)}
    if not plan:
        run.event("plan", "Nobody has job titles or keywords set. Add some in Admin > Jobs sync first.", level="error")
        state.update(phase="Stopped", message="Add some job titles or keywords first.")
        return run.finish("failed")

    picked, skipped = sources.plan()
    run.event("plan", f"Using {len(picked)} sources: {', '.join(picked) or 'none'}.")
    for name, why in skipped:
        run.event("plan", f"Not using {name}: {why}.", source=name)
    run.event("plan", f"Searching for {', '.join(names.values())}: {len(plan)} different searches on each source.")
    try:
        run.event("plan", sponsors.refresh())
    except Exception as e:
        run.event("plan", f"Could not get the licensed-sponsor list ({_plain(e)}); carrying on without it.", level="warn")
    backends = ai.tagging_backends()
    run.event("plan", "Tagging with: " + (", ".join(backends) if backends else "keywords only (no AI tool found)") + ".")
    if not picked:
        run.event("plan", "No source is available. Install the fetching tools (see the README) and try again.", level="error")
        state.update(phase="Stopped", message="No job source is available.")
        return run.finish("failed")

    with db.SessionLocal() as s:
        _backfill_keys(s)

    state.update(phase="Fetching", total=0, tagged=0)
    totals = {"new": 0, "updated": 0, "seen": 0,
              "tag": {"ai_calls": 0, "avoided": 0, "keyword_only": 0, "backends": set(), "ai_failed_calls": 0}}
    source_stats = _fetch_all(run, picked, plan, avoid, per_search, state, lambda raws: _save(run, raws, backends, state, totals))
    summary = {"sources": {n: {**st, "failed_searches": st["failed"], "failed": st["failed"] > 0 and st["searches"] == 0}
                           for n, st in source_stats.items()}}
    tag = totals["tag"]
    run.event("tag", f"{totals['new']} new and {totals['updated']} changed jobs tagged: AI was avoided for {tag['avoided']}, "
              f"{tag['ai_calls']} AI call(s) via {', '.join(sorted(tag['backends'])) or 'no AI tool'}.")
    if tag["keyword_only"]:
        run.event("tag", f"{tag['keyword_only']} jobs were tagged by keywords only and will be upgraded when AI is available.", level="warn")

    closed = checked = 0
    if check_pages and not state["stop"]:
        state["phase"] = "Checking old jobs"
        with db.SessionLocal() as s:
            result = liveness.check(s, run, stop=lambda: state["stop"])
        closed, checked = result["closed"], result["checked"]
        run.event("liveness", f"Checked {checked} old jobs: {result['closed']} closed, {result['open']} still open, "
                  f"{result['unknown']} could not be told (left unchanged).")

    summary.update(new=totals["new"], updated=totals["updated"], seen=totals["seen"], closed=closed, checked=checked,
                   ai_calls=tag["ai_calls"], ai_avoided=tag["avoided"], keyword_only=tag["keyword_only"],
                   backends=sorted(tag["backends"]))
    return summary


def start(person_id=None):
    """Start a refresh in the background. Returns False if one is already running."""
    with _lock:
        if STATE["running"]:
            return False
        STATE.update(running=True, stop=False, phase="Starting", fetched=0, tagged=0, total=0, message="", plan={}, now="", started=time.time(), notes=[])
    threading.Thread(target=refresh, args=(person_id, STATE), daemon=True).start()
    return True


def stop():
    STATE["stop"] = True


def last_sync(database):
    return database.query(db.SyncRun).order_by(db.SyncRun.at.desc()).first()


def board_age_days(database, today=None):
    last = last_sync(database)
    return None if last is None else ((today or date.today()) - last.at.date()).days
