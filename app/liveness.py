"""Is this job still open? A few polite page checks per run, never a flood, and never a guess.

Only a clear signal closes a job (404/410, or the page saying it is closed/expired/filled). Anything else, including a
site blocking us, leaves the job exactly as it was. Jobs are never deleted.
"""
import random
import re
import time
from datetime import timedelta
from urllib.parse import urlparse

import httpx
from sqlalchemy import func

from . import db

PER_RUN = 150
RECHECK_DAYS = 3            # an ordinary job is looked at most this often
TRACKED_RECHECK_DAYS = 1    # a job someone is tracking is looked at daily
MIN_AGE_DAYS = 2            # a job seen in the last two days is open: no need to ask
BLOCKS_BEFORE_SKIPPING_SITE = 2
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
CLOSED_PHRASES = re.compile(
    r"no longer (?:available|accepting|open)|this job (?:has )?(?:expired|closed|been (?:filled|removed))|"
    r"job (?:has )?expired|position (?:has been|is) filled|vacancy (?:has )?(?:closed|expired)|"
    r"(?:advert|listing|job) (?:is )?no longer|applications? (?:are |is )?(?:now )?closed|this job is closed", re.I)
_SEARCH_PAGE = re.compile(r"/(?:jobs|search|find-jobs)/?(?:\?|$)", re.I)


def judge(status, final_url, original_url, body):
    """('closed', reason) / ('open', '') / ('unknown', reason). Pure, so it is easy to test."""
    if status in (404, 410):
        return "closed", f"page gone ({status})"
    if status in (401, 403, 429, 451, 999) or status >= 500:
        return "unknown", f"blocked or unavailable ({status})"
    if 200 <= status < 300:
        if final_url != original_url and _SEARCH_PAGE.search(urlparse(final_url).path + "?" + urlparse(final_url).query) \
                and not _SEARCH_PAGE.search(urlparse(original_url).path):
            return "closed", "redirected to a search page"
        if CLOSED_PHRASES.search(body or ""):
            return "closed", "page says it is closed"
        return "open", ""
    return "unknown", f"unexpected answer ({status})"


REFUSING_SITES = ("indeed.", "glassdoor.")  # these answer page checks with 401/403; their jobs are checked through the employer's own link


def target(job):
    """The page to ask about, or None. Indeed and Glassdoor adverts are checked via the employer's apply link."""
    host = urlparse(job.url or "").netloc.lower()
    if any(site in host for site in REFUSING_SITES):
        return job.apply_url or None
    return job.url or None


def candidates(database, limit=PER_RUN, today=None):
    """Jobs worth checking now: tracked ones first, then the ones checked longest ago."""
    now = db.utcnow()
    tracked = {row[0] for row in database.query(db.TrackerEntry.job_id).filter(db.TrackerEntry.job_id.isnot(None)).distinct()}
    out = []
    q = database.query(db.Job).filter(db.Job.closed_at.is_(None), db.Job.url != "").order_by(
        func.coalesce(db.Job.last_checked, db.Job.first_seen).asc())
    for job in q:
        if target(job) is None:
            continue
        if job.deadline and job.deadline < (today or now.date()):
            continue  # already closed by its deadline
        if job.first_seen and (now - job.first_seen) < timedelta(days=MIN_AGE_DAYS):
            continue
        wait = timedelta(days=TRACKED_RECHECK_DAYS if job.id in tracked else RECHECK_DAYS)
        if job.last_checked and (now - job.last_checked) < wait:
            continue
        out.append((job.id in tracked, job))
    out.sort(key=lambda pair: not pair[0])  # stable: tracked first, then oldest check
    return [job for _, job in out[:limit]]


def _fetch(url):
    r = httpx.get(url, headers={"User-Agent": USER_AGENT}, timeout=15, follow_redirects=True)
    return r.status_code, str(r.url), r.text[:200_000]


def check(database, run=None, limit=PER_RUN, pause=(2.0, 5.0), today=None, fetch=None, stop=lambda: False):
    """Check a few jobs. Returns {"checked", "closed", "open", "unknown", "skipped_sites"}."""
    fetch = fetch or _fetch
    result = {"checked": 0, "closed": 0, "open": 0, "unknown": 0, "skipped_sites": []}
    blocks = {}
    for job in candidates(database, limit, today):
        if stop():
            break
        url = target(job)
        host = urlparse(url).netloc.lower()
        if blocks.get(host, 0) >= BLOCKS_BEFORE_SKIPPING_SITE:
            continue
        try:
            status, final_url, body = fetch(url)
            verdict, reason = judge(status, final_url, url, body)
        except Exception as e:  # network trouble is "unknown", never "closed"
            verdict, reason = "unknown", f"could not reach it ({type(e).__name__})"
        job.last_checked = db.utcnow()
        result["checked"] += 1
        result[verdict] += 1
        if verdict == "closed":
            job.closed_at, job.close_reason = db.utcnow(), reason
        elif verdict == "unknown":
            blocks[host] = blocks.get(host, 0) + 1
            if blocks[host] == BLOCKS_BEFORE_SKIPPING_SITE:
                result["skipped_sites"].append(host)
                if run:
                    run.event("liveness", f"{host} keeps refusing page checks ({reason}); skipping it for the rest of this run. "
                              "Those jobs are left as they are.", level="warn", source=host)
        else:
            blocks[host] = 0
        database.commit()
        time.sleep(random.uniform(*pause))
    return result
