# CareerOS

A career platform for early-career software graduates. **JobSpy fetches, Gemma 4 understands, plain code decides.**

Paste a CV and up to 5 dream jobs. CareerOS builds a profile, ranks UK graduate jobs for you (with the sponsorship wording quoted from the advert), shows your skill gaps, and suggests portfolio projects. This Week, Calendar and Network are labelled previews.

## Run locally
    python -m venv .venv && source .venv/bin/activate
    pip install -r requirements-dev.txt
    cp .env.example .env        # add GEMINI_API_KEY (optional: the app works without it)
    uvicorn app.main:app --reload
    pytest

Without a Gemini key you still get the stored jobs, a basic profile, ranking and gaps. Gemma adds the profile summary, gap explanations and projects.

## Refresh the jobs (your laptop only)
    pip install -r requirements-ingest.txt
    python scripts/ingest_jobs.py fetch   # JobSpy -> data/jobs_raw.json
    python scripts/ingest_jobs.py tag     # -> data/jobs_tagged.json (commit this file)

## Deploy
Push to a public GitHub repo, create a Render web service from `render.yaml`, set `GEMINI_API_KEY` (and `DATABASE_URL` for Supabase; leave it unset to use SQLite).

## Privacy
The CV is sent to Google's Gemini API, never stored; only the extracted profile is kept for 7 days. "Delete my data" removes it.

Credits: [JobSpy](https://github.com/speedyapply/JobSpy) for fetching listings. MIT licensed.
