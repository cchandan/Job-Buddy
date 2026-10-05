# Job Buddy

A private job-application workspace for a family. Find jobs on a shared Job Board, shortlist them into your own Tracker, create a CV tailored to each one, apply, and keep track of deadlines and outcomes.

Built from the CareerOS hackathon project. `Plan/` holds the spec, design and build plan.

## What it does
- **Home**: the page you land on after signing in. It shows what needs doing next; admins get their own version.
- **Job Board**: live jobs fetched by an admin, tagged, each with a deadline and status. Shortlist the ones you want.
- **Tracker**: your shortlisted and assigned jobs, plus any you add by link or by hand. Set the status (Applied, Interviewing, Rejected and so on), create a tailored CV, and jump to the advert to apply.
- **Calendar**: every deadline from your Tracker.
- **Profile**: your core CV, the base for each tailored CV.
- **Admin**: see each user's sections, assign jobs to them, review their tailored CVs, fetch and sync the day's jobs, and create accounts.

## Who can use it
Sign-in is with a username and password. There is no sign-up: an admin creates each account, and Job Buddy generates the password for the admin to pass on. There are two roles: **users** (job seekers) and **admins**.

## Run locally
    python -m venv .venv && source .venv/bin/activate
    pip install -r requirements-dev.txt
    cp .env.example .env        # fill in the settings below
    python scripts/create_admin.py   # first time only: makes the first admin account and prints its password
    uvicorn app.main:app --reload
    pytest

## Settings
| Name | Purpose |
|---|---|
| `DATABASE_URL` | Postgres connection string. Leave unset to use a local SQLite file (`jobbuddy.db`) for local play and tests |
| `SESSION_SECRET` | Signs the sign-in cookie; any long random string |
| `GEMINI_API_KEY`, `GEMMA_MODEL` | The AI used to read adverts and write tailored CVs |
| `AI_DAILY_LIMIT` | Daily cap on AI calls |

Without an AI key the Job Board, Tracker, Calendar and Profile still work; only tailored CVs and AI tagging are unavailable.

## Refresh the jobs (admins, on your own laptop)
Job sites block cloud servers, so fetching runs on a laptop, not on the live site.

    pip install -r requirements-ingest.txt
    uvicorn app.main:app --reload

Sign in, open **Admin > Jobs sync**, set what to look for, press **Fetch and tag**, check the summary, then press **Sync to Job Board**. Your `.env` must point `DATABASE_URL` at the live database for the sync to reach everyone.

The same steps work from the command line: `python scripts/ingest_jobs.py fetch`, then `python scripts/ingest_jobs.py sync --as <your username>`.

## Deploy
Create a Render web service from `render.yaml` and set the settings above. Use the Supabase **pooled** connection string for `DATABASE_URL`. Each deploy runs `alembic upgrade head`, which creates or updates the tables. Then run `python scripts/create_admin.py` once against the live database to make the first admin account.

## Privacy
Job Buddy stores each user's core CV and tailored CVs in its database so they can be reused. CV text is sent to the AI provider when a tailored CV is created. Admins can see users' CVs and Trackers. A user can delete their CVs at any time. Nothing is visible without signing in.

Credits: [JobSpy](https://github.com/speedyapply/JobSpy) for fetching listings.
