# Job Buddy

A private job-application workspace for a family. Find jobs on a shared Job Board, shortlist them into your own Tracker, apply, and keep track of deadlines and outcomes.

Built from the CareerOS hackathon project. `Plan/` holds the spec, design and build plan.

## What it does
- **Home**: the page you land on after signing in. It shows what needs doing next; admins get their own version.
- **Job Board**: live jobs fetched by an admin, tagged, each with a deadline and status. Shortlist the ones you want.
- **Tracker**: your shortlisted and assigned jobs, plus any you add by link or by hand. Set the status (Applied, Interviewing, Rejected and so on), and jump to the advert to apply.
- **Calendar**: every deadline from your Tracker.
- **Profile**: your core CV, kept ready to send.
- **Admin**: see each user's sections, assign jobs to them, fetch and sync the day's jobs, and create accounts.

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
| `GEMINI_API_KEY`, `GEMMA_MODEL` | The AI used to read adverts |
| `AI_DAILY_LIMIT` | Daily cap on AI calls |

Without an AI key the Job Board, Tracker, Calendar and Profile still work; only AI tagging is unavailable.

## Refresh the jobs (admins, on your own laptop)
Job sites block cloud servers, so refreshing runs on a laptop, not on the live site.

**One click:** double-click `Refresh Jobs.command`. The first time it sets itself up; after that it just runs. It searches Indeed, LinkedIn and Glassdoor (plus Reed and Adzuna if you add their free keys to `.env`), merges the same job from different sites, tags **only jobs that are new or changed**, publishes them, and checks a few older jobs are still open. Each source is isolated, so one being blocked never stops the others.

Or run the app (`uvicorn app.main:app`), open **Admin > Jobs sync** and press **Refresh jobs**. The page shows a log of what happened: which sources were used or skipped and why, what failed, which AI tool tagged, and what was published.

- **Each person has their own search** (Admin > Jobs sync > *Who we are searching for*): job titles, locations, miles around, words that rule a job out, UK-wide remote, the big UK cities, and "needs visa sponsorship". Priya's and Akanksh's starter searches are filled in the first time. Jobs go in one shared Job Board, tagged with whose search found them, and each person's Job Board opens on **For you** (their matches; for someone needing sponsorship, sponsors and licensed employers first and citizenship-only jobs hidden), with **Everything** one tap away.
- **Licensed sponsors:** the Home Office register of licensed sponsors is downloaded weekly and employers on it get a "Licensed sponsor" badge. The advert's own sponsorship wording and quote are still read for every job.
- **Nothing is capped, only paced:** LinkedIn and Glassdoor do a limited number of searches per run so they don't block us; the rest wait and go first next time. A search that comes back full is repeated with a bigger limit, and once a week every search looks back a full week in case a run was missed.
- **Tagging** uses the first tool found on your laptop: the `claude` command (your own login, no API key), `codex`, Ollama, then `GEMINI_API_KEY`, then plain keywords. AI is asked only about sponsorship, work mode and closing date, and only when the advert mentions them and code could not settle it. Jobs tagged by keywords are upgraded on a later run.
- **Publishing:** set `DATABASE_URL` to the live database, *or* (safer, no database password on your laptop) set `INGEST_URL` and the same `INGEST_TOKEN` here and on Render; then only the jobs the live site lacks are sent.
- **Open or closed:** a few jobs per run get a polite page check (tracked jobs first). Only a clear sign (page gone, "expired", "no longer available") closes a job; a blocked check changes nothing. Jobs are never deleted.

## Browser notifications
Chrome pop-ups, with nothing to install. Free: the browser's own push service does the delivery.
- **Admins** are told when someone signs in or comes back to the site, when a job is added or shortlisted in a Tracker (or assigned by another admin), and when its status changes (Applied, Interviewing, Rejected and so on). Nothing else is reported, and you are never told about your own actions.
- **Users** are told about deadlines in the next 3 days (one message at 8am UK time, only for jobs not yet applied for), a job an admin assigns them, and, if they haven't been on for 3 days, a nudge (repeated every 3 days).

Set up:
1. Run `python scripts/make_vapid_keys.py` once and put `VAPID_PUBLIC_KEY`, `VAPID_PRIVATE_KEY` and `VAPID_SUBJECT` (`mailto:` your email) in Render's environment (and `.env` locally). Redeploy.
2. Each person opens **Profile** (admins: **Admin home**) in Chrome and presses **Turn on notifications**, then **Send a test**. Do it once in each browser or phone you use; it needs a normal window, not Incognito.
- A "visit" is the first page opened after 30 minutes away, or a password sign-in. "Last active" on Admin home uses the same signal.
- The 8am message is sent by the app itself, so the Render service must stay awake (a paid plan). On a free plan that sleeps, the instant messages still work but the daily one can be missed.
- If you ever make a new key pair, everyone has to press **Turn on notifications** again.
- Messages carry only a headline and a job title. Without the keys the feature is simply off.

## Deploy
Create a Render web service from `render.yaml` and set the settings above. Use the Supabase **pooled** connection string for `DATABASE_URL`. Each deploy runs `alembic upgrade head`, which creates or updates the tables. Then run `python scripts/create_admin.py` once against the live database to make the first admin account.

## Privacy
Job Buddy stores each user's core CV in its database. Admins can see users' CVs and Trackers. A user can delete their CV at any time. Nothing is visible without signing in.

Credits: [JobSpy](https://github.com/speedyapply/JobSpy) for fetching listings.
