"""Run on YOUR LAPTOP, never on the server.

    python scripts/ingest_jobs.py fetch            # JobSpy -> data/jobs_raw.json
    python scripts/ingest_jobs.py tag              # tag each job -> data/jobs_tagged.json (resumable)
    python scripts/ingest_jobs.py tag --limit 30   # tag only the first 30

Tagging uses Gemma when a key works, and falls back to the keyword tagger otherwise
(each job records which one tagged it). Re-running skips jobs that are already tagged
with Gemma, so a rate-limit error or a closed laptop loses nothing.
"""
import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

RAW = ROOT / "data" / "jobs_raw.json"
TAGGED = ROOT / "data" / "jobs_tagged.json"

SEARCHES = [
    "graduate software engineer",
    "junior software developer",
    "graduate software developer",
    "junior python developer",
    "junior full stack developer",
    "graduate backend engineer",
    "software engineering intern 2027",
    "graduate technology programme software",
    "software developer apprentice",
    "junior frontend developer",
    "graduate java developer",
    "associate software engineer",
    "entry level software engineer",
    "trainee software developer",
    "junior backend developer",
    "junior web developer",
    "junior react developer",
    "junior c# developer",
    "junior .net developer",
    "junior devops engineer",
    "graduate cloud engineer",
    "junior data engineer",
    "junior software tester",
]


def job_id(url):
    return hashlib.sha1(url.strip().lower().encode()).hexdigest()[:16]


def unique(jobs):
    """Drop repeats: the same title at the same company is one job, whatever the URL."""
    seen, out = set(), []
    for j in jobs:
        key = (j["title"].strip().lower(), j["company"].strip().lower())
        if key not in seen:
            seen.add(key)
            out.append(j)
    return out


def fetch(per_search):
    try:
        from jobspy import scrape_jobs
    except ImportError:
        sys.exit("JobSpy is not installed. Run: pip install -r requirements-ingest.txt")

    jobs = {j["id"]: j for j in json.loads(RAW.read_text())} if RAW.exists() else {}  # keep earlier runs
    for term in SEARCHES:
        print(f"Searching: {term}")
        try:
            df = scrape_jobs(
                site_name=["indeed"],
                search_term=term,
                location="United Kingdom",
                country_indeed="UK",
                results_wanted=per_search,
                hours_old=24 * 30,
            )
        except Exception as e:  # a blocked search should not lose the others
            print(f"  failed: {type(e).__name__}: {str(e)[:120]}")
            continue
        for row in df.to_dict("records"):
            url = row.get("job_url") or ""
            desc = row.get("description")
            if not url or not isinstance(desc, str) or len(desc) < 200:
                continue
            jid = job_id(url)
            if jid in jobs:
                continue
            jobs[jid] = {
                "id": jid,
                "title": str(row.get("title") or "").strip(),
                "company": str(row.get("company") or "").strip(),
                "location": str(row.get("location") or "").strip(),
                "description": desc,
                "url": url,
                "salary_min": _num(row.get("min_amount")),
                "salary_max": _num(row.get("max_amount")),
                "salary_interval": str(row.get("interval") or ""),
                "date_posted": str(row.get("date_posted") or ""),
            }
        print(f"  total so far: {len(jobs)}")
    kept = unique(jobs.values())
    RAW.write_text(json.dumps(kept, indent=1))
    print(f"Wrote {len(kept)} jobs to {RAW}")


def _num(v):
    try:
        v = float(v)
        return None if v != v else v  # NaN -> None
    except (TypeError, ValueError):
        return None


def tag(limit):
    from app import gemma, tagging

    raw = [j for j in unique(json.loads(RAW.read_text())) if tagging.is_software_job(j)]
    print(f"{len(raw)} software jobs (others skipped)")
    done = {j["id"]: j for j in json.loads(TAGGED.read_text())} if TAGGED.exists() else {}
    gemma_ok = True
    count = 0
    for job in raw[: limit or None]:
        old = done.get(job["id"])
        if old and (old.get("tag_source") == "gemma" or not gemma_ok):
            continue
        tags = None
        for attempt in range(4):  # a rate limit usually clears within a minute: wait, don't give up on Gemma
            try:
                tags = tagging.tag_job(job, use_gemma=gemma_ok)
                break
            except gemma.GemmaUnavailable as e:
                if attempt < 3:
                    print(f"  Gemma busy ({e}); waiting 30s then retrying...")
                    time.sleep(30)
        if tags is None:
            print("Gemma is not responding; using the keyword tagger for this job.")
            tags = tagging.tag_job(job, use_gemma=False)
        done[job["id"]] = {**job, **tags}
        TAGGED.write_text(json.dumps(list(done.values()), indent=1))  # save as we go
        count += 1
        print(f"[{count}] {tags['tag_source']:8} {job['title'][:50]} | {job['company'][:25]}")
    print(f"Done. {len(done)} tagged jobs in {TAGGED}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("step", choices=["fetch", "tag"])
    ap.add_argument("--per-search", type=int, default=15)
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()
    fetch(a.per_search) if a.step == "fetch" else tag(a.limit)
