"""Where jobs come from. Every source returns the same plain dicts, and one failing never stops the others.

JobSpy sites (Indeed, LinkedIn, Glassdoor) need `pip install -r requirements-ingest.txt`. Reed and Adzuna are free
APIs that switch on by themselves when their keys are in .env. Everything here runs on an admin's laptop, never the server.
"""
import hashlib
import importlib.util
import os
import re
from datetime import datetime

import httpx

COUNTRY = os.getenv("JOBSPY_COUNTRY", "UK")
JOBSPY_SITES = ["indeed", "linkedin", "glassdoor"]
NOT_USED = {"bayt": "it is Middle East focused and returned non-UK jobs when tested", "zip_recruiter": "US and Canada only", "google": "Google Jobs is not working in JobSpy at the moment",
            "naukri": "India only", "bdjobs": "Bangladesh only"}
MIN_DESCRIPTION = 200
TAGGER_VERSION = "2"  # raise when the tagging rules improve: it changes every job's hash, so old tags are redone on the next refresh


def jobspy_available():
    return importlib.util.find_spec("jobspy") is not None


def plan():
    """([sources to use], [(source, why not)]). This is the first thing the run log says."""
    picked, skipped = [], [(n, why) for n, why in NOT_USED.items()]
    for site in JOBSPY_SITES:
        if jobspy_available():
            picked.append(site)
        else:
            skipped.append((site, "JobSpy is not installed"))
    for name, keys in (("reed", ("REED_API_KEY",)), ("adzuna", ("ADZUNA_APP_ID", "ADZUNA_APP_KEY"))):
        if all(os.getenv(k) for k in keys):
            picked.append(name)
        else:
            skipped.append((name, "no key in .env (optional)"))
    return picked, skipped


# ---------- helpers ----------

def _num(v):
    try:
        v = float(v)
        return None if v != v else v  # NaN -> None
    except (TypeError, ValueError):
        return None


def _text(v):
    return "" if v is None or v != v else str(v).strip()


def _norm(s):
    return re.sub(r"[^a-z0-9 ]+", "", (s or "").lower()).strip()


_NOISE = re.compile(r"\b(?:ltd|limited|plc|llp|uk|inc)\b")
_TITLE_NOISE = re.compile(r"\b(?:remote|hybrid|full time|part time|permanent|contract)\b")


def canonical_key(title, company, location):
    """The same job on two sites shares this: company + title + town, lower-cased and de-noised."""
    town = _norm((location or "").split(",")[0])
    parts = (_NOISE.sub("", _norm(company)).strip(), _TITLE_NOISE.sub("", _norm(title)).strip(), town)
    return hashlib.sha1("|".join(" ".join(p.split()) for p in parts).encode()).hexdigest()


def content_hash(raw):
    """Changes when the advert's wording changes, so unchanged jobs are never re-tagged. Case, spacing and
    punctuation are ignored, so a site reformatting the same text is not a change."""
    desc = _norm(raw.get("description"))
    blob = "|".join([TAGGER_VERSION, _norm(raw.get("title")), _norm(raw.get("company")), _norm(raw.get("location")), desc,
                     str(raw.get("salary_min")), str(raw.get("salary_max")), str(raw.get("expires") or "")])
    return hashlib.sha1(blob.encode()).hexdigest()


def job_id(url):
    return hashlib.sha1(url.strip().lower().encode()).hexdigest()[:16]


def finish(raw, site):
    """Add the fields every source shares: id, which site, de-dup key, change hash."""
    raw["site"] = site
    raw["canonical_key"] = canonical_key(raw.get("title"), raw.get("company"), raw.get("location"))
    raw["content_hash"] = content_hash(raw)
    raw["id"] = job_id(raw["url"])
    return raw


# ---------- JobSpy ----------

def _jobspy(site, term, location, hours_old, wanted, distance, remote):
    from jobspy import scrape_jobs
    kwargs = dict(site_name=[site], search_term=term, location=location, country_indeed=COUNTRY,
                  results_wanted=wanted, hours_old=hours_old, distance=distance or 50)
    if remote:
        kwargs["is_remote"] = True
    if site in ("linkedin", "glassdoor"):
        kwargs["fetch_description"] = True  # these sites only give the full advert when asked
    df = scrape_jobs(**kwargs)
    out = []
    for row in df.to_dict("records"):
        url, desc = _text(row.get("job_url")), row.get("description")
        if not url or not isinstance(desc, str) or len(desc) < MIN_DESCRIPTION:
            continue
        remote = row.get("is_remote")
        out.append(finish({
            "title": _text(row.get("title")), "company": _text(row.get("company")), "location": _text(row.get("location")),
            "description": desc, "url": url, "apply_url": _text(row.get("job_url_direct")),
            "is_remote": None if remote is None or remote != remote else bool(remote),
            "job_type": _text(row.get("job_type")), "date_posted": _text(row.get("date_posted")),
            "salary_min": _num(row.get("min_amount")), "salary_max": _num(row.get("max_amount")),
            "salary_interval": _text(row.get("interval")), "salary_currency": _text(row.get("currency")),
            "company_industry": _text(row.get("company_industry")), "job_level": _text(row.get("job_level")),
        }, site))
    return out


# ---------- Reed and Adzuna (free keys) ----------

def _dmy(text):
    try:
        return datetime.strptime(text[:10], "%d/%m/%Y").date().isoformat()
    except (TypeError, ValueError):
        return ""


def _reed(term, location, hours_old, wanted, distance, remote):
    auth = (os.environ["REED_API_KEY"], "")
    r = httpx.get("https://www.reed.co.uk/api/1.0/search", auth=auth, timeout=30,
                  params={"keywords": term, "locationName": location, "distanceFromLocation": distance or 15,
                          "resultsToTake": min(wanted, 100)})
    r.raise_for_status()
    out = []
    for j in r.json().get("results", []):
        url = _text(j.get("jobUrl"))
        if not url:
            continue
        out.append(finish({
            "title": _text(j.get("jobTitle")), "company": _text(j.get("employerName")), "location": _text(j.get("locationName")),
            "description": _text(j.get("jobDescription")), "url": url, "partial": True, "reed_id": j.get("jobId"),
            "date_posted": _dmy(j.get("date")), "expires": _dmy(j.get("expirationDate")),
            "salary_min": _num(j.get("minimumSalary")), "salary_max": _num(j.get("maximumSalary")),
            "salary_interval": "yearly", "salary_currency": _text(j.get("currency")) or "GBP",
        }, "reed"))
    return out


def _adzuna(term, location, hours_old, wanted, distance, remote):
    r = httpx.get("https://api.adzuna.com/v1/api/jobs/gb/search/1", timeout=30, params={
        "app_id": os.environ["ADZUNA_APP_ID"], "app_key": os.environ["ADZUNA_APP_KEY"], "what": term, "where": location,
        "results_per_page": min(wanted, 50), "distance": round((distance or 15) * 1.6), "max_days_old": max(1, hours_old // 24), "content-type": "application/json"})
    r.raise_for_status()
    out = []
    for j in r.json().get("results", []):
        url = _text(j.get("redirect_url"))
        if not url:
            continue
        out.append(finish({
            "title": _text(j.get("title")), "company": _text((j.get("company") or {}).get("display_name")),
            "location": _text((j.get("location") or {}).get("display_name")), "description": _text(j.get("description")),
            "url": url, "partial": True, "date_posted": _text(j.get("created"))[:10], "job_type": _text(j.get("contract_time")),
            "salary_min": _num(j.get("salary_min")), "salary_max": _num(j.get("salary_max")), "salary_interval": "yearly",
            "salary_currency": "GBP", "company_industry": _text((j.get("category") or {}).get("label")),
        }, "adzuna"))
    return out


def fetch(source, term, location, hours_old, wanted, distance=None, remote=False):
    """One search on one source -> list of job dicts. Tests replace this function."""
    if source in JOBSPY_SITES:
        return _jobspy(source, term, location, hours_old, wanted, distance, remote)
    if remote:
        return []  # Reed and Adzuna cannot filter for remote work
    return {"reed": _reed, "adzuna": _adzuna}[source](term, location, hours_old, wanted, distance, remote)


def hydrate(raw):
    """Fill in the full advert for a NEW job whose source gives only a snippet in search results (Reed)."""
    if raw.get("site") == "reed" and raw.get("reed_id") and os.getenv("REED_API_KEY"):
        r = httpx.get(f"https://www.reed.co.uk/api/1.0/jobs/{raw['reed_id']}", auth=(os.environ["REED_API_KEY"], ""), timeout=30)
        r.raise_for_status()
        j = r.json()
        raw["description"] = re.sub(r"<[^>]+>", " ", _text(j.get("jobDescription"))) or raw["description"]
        raw["apply_url"] = _text(j.get("externalUrl"))
        raw["job_type"] = "fulltime" if j.get("fullTime") else "parttime" if j.get("partTime") else raw.get("job_type", "")
        raw["content_hash"] = content_hash(raw)
    return raw
