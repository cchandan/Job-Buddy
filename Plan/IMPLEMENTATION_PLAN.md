# Job Buddy Implementation Plan

Companion to `spec.md` and `DESIGN.md`. This plan turns the existing CareerOS code into Job Buddy. It is a rebuild of a working app, not a fresh start: keep what works, delete what is gone, add what is new.

## 1. Tech stack

| Choice | What it is | Status |
|---|---|---|
| **Python, FastAPI + Jinja templates** | The web app; pages built on the server | Kept |
| **Existing CSS** (`app/static/app.css`) | The design system | Kept |
| **SQLAlchemy** | Talks to the database | Kept |
| **Postgres (Supabase)** | The production database | Kept, and now **required** |
| **SQLite** | Database for tests and quick local work | Kept for those only |
| **Alembic** | Applies database changes without losing data | **New** |
| **Password hashing** (Python's built-in `hashlib.scrypt`) | Stores passwords safely | **New**, no extra dependency |
| **Signed session cookie** (Starlette `SessionMiddleware`) | Remembers who is signed in | **New** |
| **httpx** | Fetches a job page for add-by-link | **New** |
| **google-genai SDK** | Calls Gemma 4 via the Gemini API | Kept for now |
| **Pydantic** | Checks the shape of AI output | Kept |
| **JobSpy** | Fetches job listings | Kept, laptop only |
| **pypdf** | Reads text from a PDF CV | Kept |
| **pytest** | Tests | Kept, more of them |
| **Render** | Hosts the app | Kept |

Not used: a separate JavaScript frontend, Docker, LangChain, vector databases, background workers or queues.

**Why Postgres is now required.** CareerOS could run on SQLite on Render because losing visitor data on a restart did not matter. Job Buddy holds Trackers and CVs that must never be lost, and Render's free disk is wiped on every restart.

**Why Alembic.** CareerOS created its tables at startup and could throw the database away. Job Buddy's database holds real data, so every change to its shape must be applied without losing anything.

**Tailored CV as PDF.** The tailored CV is shown on a clean print-styled page and saved with the browser's "Save as PDF". This needs no PDF library. If a real file download or a Word file is needed later, add a library then.

**Two requirements files stay.** `requirements.txt` is what the server installs. `requirements-ingest.txt` adds JobSpy for admin laptops.

**AI provider.** Gemma 4 through the Gemini API today; this will be reconsidered. `app/ai.py` (renamed from `gemma.py`) is the only file that knows which provider is used.

## 2. Architecture overview

```
 ADMIN'S LAPTOP (once a day)                         LIVE SITE
 ┌──────────────────────────────┐                   ┌─────────┐ HTML ┌──────────────────┐
 │ Job Buddy running locally    │                   │ Browser │ <──> │ FastAPI on Render│
 │ Admin > Jobs sync            │                   └─────────┘      └───┬──────────┬───┘
 │  1 Fetch (JobSpy)            │                                        │          │
 │  2 Tag (AI + checks in code) │                                        │          │ tailor CV,
 │  3 Sync ─────────────────────┼──────────┐                             │          │ read a link
 └──────────────────────────────┘          │                             │          ▼
                                           ▼                             ▼     ┌──────────┐
                                  ┌──────────────────────────────────────────┐ │ AI       │
                                  │ Postgres (Supabase), one database        │ │ provider │
                                  │  jobs (shared) · people · tracker        │ └──────────┘
                                  │  cvs · tailored cvs · sync history       │
                                  └──────────────────────────────────────────┘
```

One web app, one database. The same app runs in two places: on Render for everyone, and on an admin's laptop for the daily fetch. Both talk to the same database.

## 3. How fetch and sync work (assumption A9)
1. The admin starts Job Buddy on their laptop, with `DATABASE_URL` set to the production database and JobSpy installed.
2. They sign in as usual and open Admin > Jobs sync.
3. **Fetch and tag** writes to a staging table (`staged_jobs`), not to the Job Board. It saves as it goes, so it can be stopped and resumed.
4. **Sync** copies staged jobs into `jobs`, matched by URL: new ones are added, existing ones updated. It records a row in `sync_runs` and empties the staging table.
5. Users see the new jobs on their next page load.

The app decides whether to show the Fetch button by checking whether JobSpy can be imported. On Render it cannot, so the button is replaced by a note.

**Why this way:** it adds no extra moving parts. There is no upload step, no file to commit, and no second copy of the jobs to keep in step.

**The cost:** the admin's laptop holds the production database password in its `.env`. For a family tool run by the two admins this is acceptable.

**The alternative**, if that is not acceptable: the laptop keeps its own local database, and Sync sends the tagged jobs to a protected address on the live site with a secret token. The laptop then never holds the database password, at the cost of one more route and one more secret. Choose before slice 6.

`data/jobs_tagged.json` is no longer the source of jobs. It is seed data: a database with no jobs at all starts with the jobs in this file, so the Job Board is not empty before the first sync.

## 4. Sign-in and access

```
 Browser ──▶ /login (username + password)
                 │ too many recent failures?            ── yes ──▶ short lockout message
                 │ account exists and password matches? ── no ───▶ same page, one plain error
                 ▼ yes
          set the session cookie ──▶ Home (user) or Admin home (admin)
```

- Accounts live in the `people` table. There is no sign-up route; only an admin can create an account.
- The app generates each password (long and random) and shows it to the admin once. Only a salted scrypt hash is stored.
- "New password" replaces the hash and raises that person's session version, which signs them out everywhere.
- The first admin is created with a one-off command, `python scripts/create_admin.py`. The same command rescues a locked-out admin.
- Failed sign-ins are counted per username. After 5 in a row the account is locked for 10 minutes. The error never says whether the username exists, and a sign-in for a missing username takes as long as a real one.
- The session cookie is signed with `SESSION_SECRET`, `HttpOnly`, `Secure`, `SameSite=Lax`. It holds the person's id and session version, nothing else.
- Three guards, used by every route:
  - `current_person(request)`: who is signed in, or redirect to sign-in.
  - `require_admin(person)`: refuse with a friendly page if not an admin.
  - `target_user(person, user_id)`: whose data this request is about. A user may only ever target themselves; an admin may target any user. Every Home, Tracker, Calendar, Profile and CV query goes through this, so access is decided in one place.
- Forms that change data, including sign-in, are POSTs and carry a CSRF token.

## 5. Data

| Table | Holds |
|---|---|
| `people` | id, username, name, role, password hash, session version, failed sign-in count and time, created, last sign-in, last Job Board visit |
| `jobs` | The shared Job Board. id (hash of the URL), title, company, location, description, URL, salary, date posted, **deadline + quote**, sponsorship + quote, work mode + quote, seniority, industry, skills, first seen, last seen |
| `staged_jobs` | Fetched and tagged jobs waiting for Sync. Same shape as `jobs` |
| `tracker_entries` | One per user per job. person, job (if from the Board), own job details (if added by link or by hand), **source** (shortlisted / assigned / link / manual), **added by** (a person), **application status**, status history, **hand-set deadline**, notes, created |
| `cvs` | One core CV per person: file name, file bytes, extracted text, uploaded date |
| `tailored_cvs` | One per Tracker entry: text, **CV status**, reviewer, review comment, updated |
| `sync_runs` | Who synced, when, counts |
| `search_settings` | Keywords, locations, results per search |
| `ai_usage` | Calls per day, for the daily limit |

Notes:
- A Tracker entry either points to a Board job or carries its own details. One helper returns "the job for this entry" in a single shape, so templates never need to know which.
- The deadline shown is the hand-set one if present, otherwise the job's.
- Listing status is not a column. It is worked out by `deadlines.listing_status(job, today)`.
- CV files are stored in the database. They are small, there are few of them, and Render has no lasting disk.
- A unique rule on (person, job) stops a job being shortlisted or assigned twice.
- The old `visitors` and `gemma_usage` tables are dropped by the first migration.

## 6. Code layout

```
app/
  main.py          # app setup, error pages, health check; mounts the route files
  routes/
    auth.py        # /login, /logout, change password
    home.py        # user Home
    board.py       # Job Board, shortlist
    tracker.py     # Tracker, add by link, add by hand, status, deadline, notes
    cv.py          # Profile, core CV, tailored CV, send for review
    calendar.py    # Calendar
    admin.py       # Admin home, accounts, view as user, assign, review, jobs sync
  auth.py          # passwords, sign-in, lockout, session, the three guards (section 4)
  summary.py       # what Home and Admin home show; pure code over the Tracker, CVs and jobs
  db.py            # tables and the database connection
  web.py           # shared by the route files: templates, page rendering, flash messages, formatters
  tracking.py      # putting jobs into a Tracker (shortlist, assign, link, by hand) and changing status
  ai.py            # the ONLY file that talks to the AI provider (was gemma.py)
  tagging.py       # advert -> tags, keyword safety net, quote and date checks
  skills.py        # one spelling per skill
  deadlines.py     # listing status and deadline rules; pure code, no AI
  linkfetch.py     # fetch a job page and pull out its text, safely
  tailoring.py     # core CV + job -> tailored CV
  ingest.py        # fetch (JobSpy), tag, stage, sync (moved from scripts/ingest_jobs.py)
  cv_pdf.py        # PDF -> text
  templates/
  static/
migrations/        # Alembic
scripts/
  ingest_jobs.py   # thin command-line wrapper around app/ingest.py, kept as a backup to the Admin button
  create_admin.py  # one-off: create the first admin, or rescue a locked-out one
data/
  jobs_tagged.json # seed data for tests and a fresh local database
tests/
```

Rule: **AI reads and writes text; code decides.** Access, statuses, deadlines, de-duplication and sync are plain code with tests.

### What happens to each existing file
| File | Action |
|---|---|
| `app/gemma.py` | Rename to `ai.py`; drop the per-visitor limit, keep the daily one |
| `app/tagging.py` | Keep; add deadline extraction and the date check; drop the software-jobs-only filter (admins now choose what is fetched); revisit tags when A2 is answered |
| `app/skills.py`, `app/cv_pdf.py` | Keep |
| `app/db.py` | Rewrite the tables (section 5); remove the 7-day cleanup. Tables are created at startup only on SQLite (tests and local play); Postgres changes only through migrations |
| `app/main.py` | Split into `routes/`; remove onboarding, analyse, this-week, skill-gaps, projects, network |
| `app/sessions.py` | Delete; replaced by `auth.py` |
| `app/ranking.py`, `app/gaps.py`, `app/projects.py`, `app/profile.py` | Delete (assumption A1) |
| Templates `landing`, `onboarding`, `this_week`, `skill_gaps`, `projects`, `network`, `deleted` | Delete |
| Templates `base`, `_macros`, `jobs`, `calendar`, `profile`, `error` | Rework |
| `app/static/landing.css` | Delete |
| `scripts/ingest_jobs.py` | Move the logic to `app/ingest.py`; keep a thin wrapper |
| `tests/test_ranking.py` | Delete; `tests/test_app.py` is rewritten slice by slice |
| `render.yaml`, `.env.example` | Rename the service; add the new settings |

### Contracts between the pieces
- `ai.ask_json(prompt, schema)` → a validated object, or raises `AIUnavailable` / `AILimitReached`. Strips code fences, parses, validates, retries once, has a timeout. `ai.ask_text(prompt)` does the same for the tailored CV.
- `tagging.tag_job(job)` → tags, with every quote checked against the description and the deadline checked as a real date. Falls back to keyword tags when AI is unavailable.
- `deadlines.listing_status(job, today)` → Open / Closing soon / Closed / Possibly closed.
- `deadlines.effective_deadline(entry)` → the hand-set deadline, else the job's, else none.
- `linkfetch.fetch_text(url)` → page text, or raises `LinkUnreadable`. Only `http` and `https`; refuses private and local network addresses; size and time limits; a fixed number of redirects.
- `tailoring.tailor(cv_text, job)` → tailored CV text.
- `summary.user_home(person, today)` and `summary.admin_home(today)` → the items each landing page shows. They read existing tables and store nothing.
- `auth.new_password()` → a random password, returned once; `auth.hash_password` / `auth.check_password` use scrypt with a per-password salt.
- `ingest.sync(db, admin)` → counts. Writes only `jobs`, `sync_runs` and `staged_jobs`.

### Checks that code makes on AI output
- A sponsorship, work-mode or deadline quote must appear word-for-word in the advert; if not, the tag becomes `unclear` or "Unknown" and the quote is dropped.
- A deadline must parse as a real date. A date well in the past at fetch time is treated as "Unknown" rather than trusted.
- Keyword safety net for sponsorship.
- All output is shown through Jinja's automatic escaping, so text from a CV or advert cannot inject HTML.
- The tailored CV cannot be fully checked by code. The safeguards are the prompt, the side-by-side view and optional review.

## 7. Before building
- Confirm the Supabase project and its pooled connection string; check that backups are on.
- Decide the four usernames.
- Answer the assumptions in `spec.md` section 13. A1, A2 and A9 change what gets built; the rest can be changed cheaply later.

## 8. Build slices
Each slice ends with something working on the live URL, tests passing, and a commit. Order matters: sign-in and the database come first because everything else stands on them.

| # | Slice | Done when |
|---|---|---|
| 0 | **Rename and clear out.** Rename to Job Buddy (README, templates, `render.yaml`). Delete the removed features, their templates and tests. Add Alembic with a first migration to the new tables. | App starts; the Job Board lists the existing jobs; no dead links; tests pass |
| 1 | **Sign-in and accounts.** `auth.py`, password hashing, lockout, the three guards, sign-in page, sign-out, the first-admin command, creating accounts and new passwords (a plain version of Admin home), change password, sidebar by role. | AC1 and AC2 pass as automated tests; all four people can sign in on the live URL |
| 2 | **Job Board.** Deadline and listing status (`deadlines.py`, written test-first), filters, sort, job detail, Shortlist. | AC3, AC4 |
| 3 | **Tracker.** Entries, "added by", application status with history, hand-set deadline, notes, Apply with the "Did you apply?" prompt, add by hand, remove. | AC6, AC7, AC8 |
| 4 | **Add by link.** `linkfetch.py`, tagging one advert, the duplicate check, the fallback to the manual form. | AC5 |
| 5 | **Calendar and Home.** Upcoming list, month view, "No deadline set"; then the user Home page (`summary.py`), built from the same data, including the getting-started state. | AC9, AC1a |
| 6 | **Jobs sync.** Move ingestion into `app/ingest.py`, staging table, the Admin page with progress, Sync, history, search settings. Deadline added to tagging. | AC14, AC15 |
| 7 | **Profile and core CV.** Upload, view, download, replace, delete, privacy note. | AC10 |
| 8 | **Tailored CV.** `tailoring.py`, the two-pane editor, save, regenerate, print to PDF, blocked states. | AC11 |
| 9 | **Admin.** The full Admin home (needs-you list, user cards, activity), view as user with the banner, assign a job, review queue with approve and request changes, remove account. | AC12, AC13, AC1a |
| 10 | **Hardening.** Limits, CSRF, no-index, friendly errors everywhere, AI-down behaviour, phone layout check, removing a person and their data. | AC16, AC17, AC18 |

Slices 2 to 5 give a user a fully working Board, Tracker and Calendar on the existing 233 jobs, before any AI work. Slice 6 can move earlier if fresh jobs are needed sooner.

## 9. Tests
Written first for the rules that must never break:
- **Access:** a user requesting another user's Tracker, CV or Profile is refused; a user requesting any Admin route is refused; a signed-out request is redirected.
- **Sign-in:** a wrong password is refused; the error is the same for a missing username; repeated failures lock out; a new password ends the old sessions; the stored value is a hash, never the password.
- **Home:** every item on Home and Admin home matches the Tracker, CVs and jobs it came from.
- **Listing status:** each of the four states, including the 7-day and 30-day edges.
- **Deadlines:** a hand-set deadline wins and survives a sync.
- **Sync:** adds and updates jobs; leaves every Tracker entry, status and CV untouched; matches by URL.
- **Shortlist and assign:** doing either twice creates one entry.
- **Quote and date checks:** a quote not in the advert is dropped; an impossible date becomes "Unknown".
- **Link fetching:** local and private addresses are refused.

Tests run on SQLite with a fake signed-in person and a fake AI, so they need no network and no keys.

## 10. Settings (environment variables)
| Name | Purpose |
|---|---|
| `DATABASE_URL` | Postgres connection string. Unset means SQLite, for tests and local play only |
| `SESSION_SECRET` | Signs the session cookie |
| `GEMINI_API_KEY`, `GEMMA_MODEL` | The AI provider, for now |
| `AI_DAILY_LIMIT` | Daily cap on AI calls |

## 11. Production basics
- AI failure or bad JSON: retry once, then fall back; never crash.
- The health check touches neither the database nor the AI.
- Database changes go through Alembic migrations, run on deploy.
- Backups: confirm Supabase's are on; take a manual export before any migration that changes existing tables.
- Logs never contain CV text, tokens or keys.
- Free-tier sleep: the first page load after a quiet spell is slow. Acceptable for four people; move to a paid instance if it annoys.

## 12. Risks
The biggest technical risks, in order:
1. **Access control mistakes.** One guard, used everywhere, tested first (slice 1).
2. **Migrating a live database.** Alembic from slice 0; export before risky changes.
3. **Add by link.** Many job pages block robots or need JavaScript. The manual fallback is part of the feature, not an afterthought.
4. **Fetch and sync from a laptop.** Depends on the laptop being set up correctly; the steps go in the README and the command-line script stays as a backup.
5. **Tailored CV quality.** Judged by the family, not by tests; expect to adjust the prompt after real use.
