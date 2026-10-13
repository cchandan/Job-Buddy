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

Or run the app (`uvicorn app.main:app`), open **Admin > Refresh** and press **Refresh jobs**. The page shows a log of what happened: which sources were used or skipped and why, what failed, which AI tool tagged, and what was published.

- **Steps and sources** (Admin > Refresh): a refresh is three steps, **Scrape** (search and tag, saved here as it goes), **Check liveness** (look at older jobs' pages and close the ones that are gone) and **Publish** (send new, changed and closed jobs to the live site). Run all three with **Run all steps**, or any one on its own. Publish needs `INGEST_URL` and `INGEST_TOKEN`; without them this laptop writes straight to the live database and there is nothing to publish. Under **Sources**, tick or untick Indeed, LinkedIn, Glassdoor, Reed and Adzuna; unticked sites are skipped and the choice is remembered. A site that can't run here (JobSpy not installed, no key) is greyed out and says why. From the command line: `python scripts/ingest_jobs.py refresh --stages liveness` (any of `scrape,liveness,publish`).
- **The bin** (Admin > Bin): expired jobs (a page check found them gone, or their closing date passed) are moved to the bin automatically after each scrape or page check. Jobs found **only** by search terms you have since removed are listed on the Bin page with a **Move to the bin** button, and nothing moves until you press it. Binned jobs are hidden from the Job Board but never deleted, and **nobody's Tracker is ever touched**; a job someone is tracking is never binned for being off-topic. **Restore** puts a job back and it is never binned automatically again. Admins can also **bin any advert by hand** (a **Move to bin** button on the Job Board and on the advert's page, with an optional reason); a hand-binned advert stays binned even if a later scrape finds it again, until an admin restores it, and it has its own **Binned by hand** filter on the Bin page. A job found again by a current search comes out of the bin by itself. From now on each job records which search term found it; jobs scraped before that have no record, so they are counted but never judged. Publishing sends the bin to the live site, so manage it on the machine you scrape from.
- **Snapshots** (Admin > Snapshots): take a saved copy of the whole Job Board by hand (up to 10), then **Restore** it later if a refresh or a keyword change goes wrong. Only the Job Board is saved (including what is in the bin): no Trackers, CVs or accounts. Restoring puts every job in the snapshot back exactly as it was; jobs added since go to the bin (nothing is deleted), jobs someone is tracking stay on the Board, and **no Tracker is ever changed**. An automatic snapshot of the current Board is taken just before every restore, so a restore can be undone. You can also download a snapshot as a `.json.gz` file. Restore on the machine you scrape from, then Publish to update the live site.
- **Each person has their own search** (Admin > Searches): job titles, locations, miles around, words that rule a job out, UK-wide remote, the big UK cities, and "needs visa sponsorship". Priya's and Akanksh's starter searches are filled in the first time. Jobs go in one shared Job Board, tagged with whose search found them, and each person's Job Board opens on **For you** (their matches; for someone needing sponsorship, sponsors and licensed employers first and citizenship-only jobs hidden), with **Everything** one tap away.
- **Licensed sponsors:** the Home Office register of licensed sponsors is downloaded weekly and employers on it get a "Licensed sponsor" badge. The advert's own sponsorship wording and quote are still read for every job.
- **Nothing is capped, only paced:** LinkedIn and Glassdoor do a limited number of searches per run so they don't block us; the rest wait and go first next time. A search that comes back full is repeated with a bigger limit, and once a week every search looks back a full week in case a run was missed.
- **Tagging** uses the first tool found on your laptop: the `claude` command (your own login, no API key), `codex`, Ollama, then `GEMINI_API_KEY`, then plain keywords. AI is asked only about sponsorship, work mode and closing date, and only when the advert mentions them and code could not settle it. Jobs tagged by keywords are upgraded on a later run.
- **Publishing:** set `DATABASE_URL` to the live database, *or* (safer, no database password on your laptop) set `INGEST_URL` and the same `INGEST_TOKEN` here and on Render; then only the jobs the live site lacks are sent.
- **Open or closed:** a few jobs per run get a polite page check (tracked jobs first). Only a clear sign (page gone, "expired", "no longer available") closes a job; a blocked check changes nothing. Jobs are never deleted.

## The user pages

People who use Job Buddy get the same layout: a sidebar with **Home**, **Job Board**, **Tracker**, **Calendar** and **Profile**, and small counts beside them (things to do, new jobs on the Board, new jobs assigned to you). On a phone the sidebar is the menu behind the button beside "Job Buddy". **Home** leads with **Do next** and a tappable strip of status counts. The **Job Board** has *For you* and *Everything* tabs, one search bar, a **Filters** button and removable chips for the filters in force. The **Tracker** is one row per job with the status changed in place, and a "Did you apply?" prompt above the list. The **Job page** puts what the advert says beside the description. **Profile** holds the CV, notifications and (folded away) the password.

## Triage

**Triage** (sidebar, under Jobs) deals a person's best-matching Board jobs one at a time. **Shortlist** (button, right arrow, or swipe right) adds the job to the Tracker; **Skip** (button, left arrow, or swipe left) opens a sheet asking why, which can be ignored. The deck holds only open, untracked, undecided jobs that match the person's search, and never jobs they can't take (citizenship-only, for someone who needs sponsorship). Order is plain rules: matches first, sponsors first for someone who needs a visa, close deadlines up. A skip with a reason teaches a small rule (a place, a level, a company, a kind of job, a pay level) that ranks similar jobs **lower, never hides them**. **Undo** takes back the last swipe (a shortlisted job leaves the Tracker again only if nothing was done to it). **Skipped jobs** lists everything skipped and why, with **Bring back** and a button to forget each learned rule. No AI is used yet. Migration `0010` adds the `job_decisions` table.

## The admin pages

The admin sidebar is grouped: **Today** (what needs you, at-a-glance tiles, people and recent activity), **People** (accounts, sign-up requests, and a Manage panel for passwords and removal), then under Jobs: **Job Board**, **Refresh** (status, schedule, sources, history and the computers that refresh), **Searches** (each person's keywords and places), **Bin** and **Snapshots**, and **Settings** (notifications, public sign-up, your own password). On a phone the sidebar is a menu behind the button beside "Job Buddy". The old Jobs sync and Refresher pages now lead to Refresh.

## The home refresher
Instead of refreshing jobs by hand from a laptop, an always-on Linux computer at home can do it every morning. A small program (`refresher/`) runs there, scrapes, checks old jobs and publishes, and reports to **Admin > Refresh** on the website: online or offline, progress, last and next refresh, version, the logs, a **Run now** button, and the morning time. Searches and sources are still edited on the website. Add the computer on Admin > Refresh to get a one-time connection code, then follow `refresher/README.md` (written for whoever sets up the computer): download this repo as a ZIP, run `bash refresher/install.sh`, paste the code. It only calls out to the website, so nothing at home is exposed. Admins get a Chrome notification if a refresh fails or the computer goes quiet for a day.

## Sign-up
The public page has a **Request an account** form. A request creates nothing: it waits under **Needs you** on Today (and on People) until an admin presses **Approve** (the person then signs in with the username and password they chose; there is no email, so tell them) or **Decline**. Approved accounts are always users, never admins.
- Until approved, signing in with the right password says "waiting for an admin to approve".
- Settings has a **Close sign-up** switch. Requests already waiting stay there.
- Limits: 5 attempts per hour per address, 25 requests waiting at once, and a hidden trap field for bots. Admins also get a notification when a request arrives (if browser notifications are on).
- Usernames already in use are refused, which tells a visitor that name exists. Choose non-guessable admin usernames if that matters.

## Browser notifications
Chrome pop-ups, with nothing to install. Free: the browser's own push service does the delivery.
- **Admins** are told when someone signs in or comes back to the site, when a job is added or shortlisted in a Tracker (or assigned by another admin), and when its status changes (Applied, Interviewing, Rejected and so on). Nothing else is reported, and you are never told about your own actions.
- **Users** are told about deadlines in the next 3 days (one message at 8am UK time, only for jobs not yet applied for), a job an admin assigns them, and, if they haven't been on for 3 days, a nudge (repeated every 3 days).

Set up:
1. Run `python scripts/make_vapid_keys.py` once and put `VAPID_PUBLIC_KEY`, `VAPID_PRIVATE_KEY` and `VAPID_SUBJECT` (`mailto:` your email) in Render's environment (and `.env` locally). Redeploy.
2. Each person opens **Profile** (admins: **Settings**) in Chrome and presses **Turn on notifications**, then **Send a test**. Do it once in each browser or phone you use; it needs a normal window, not Incognito.
- **iPhone:** Apple only allows notifications from a website added to the Home Screen as an app, on iOS 16.4 or newer, and not from Chrome or a Safari tab. Open Job Buddy in **Safari**, tap **Share**, then **Add to Home Screen**, open it from the new "jb." icon, then press **Turn on notifications** on Profile. Job Buddy ships a web app manifest (`/manifest.webmanifest`) and icons (`app/static/icons/`) so this works.
- A "visit" is the first page opened after 30 minutes away, or a password sign-in. "Last active" on Today and People uses the same signal.
- The 8am message is sent by the app itself, so the Render service must stay awake (a paid plan). On a free plan that sleeps, the instant messages still work but the daily one can be missed.
- If you ever make a new key pair, everyone has to press **Turn on notifications** again.
- Messages carry only a headline and a job title. Without the keys the feature is simply off.

## Deploy
Create a Render web service from `render.yaml` and set the settings above. Use the Supabase **pooled** connection string for `DATABASE_URL`. Each deploy runs `alembic upgrade head`, which creates or updates the tables. Then run `python scripts/create_admin.py` once against the live database to make the first admin account.

## Privacy
Job Buddy stores each user's core CV in its database. Admins can see users' CVs and Trackers. A user can delete their CV at any time. Nothing is visible without signing in.

Credits: [JobSpy](https://github.com/speedyapply/JobSpy) for fetching listings.
