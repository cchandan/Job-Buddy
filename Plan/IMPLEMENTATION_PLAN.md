# CareerOS Implementation Plan

Companion to `spec.md` and `DESIGN.md`. Times add up to ~3h45, leaving ~15 min buffer.

## 0. Cut for today's hackathon demo
The build finishes at 3 PM, so the demo MVP drops the items below. Everything else in this plan stands, and each cut item is finished after the hackathon. Where this section disagrees with a later one, this section wins for today.

- **Supabase:** run on SQLite. Visitor data resets when Render restarts, which is fine for a demo, and it is one setting (`DATABASE_URL`) to switch later.
- **PDF upload:** paste the CV as text only.
- **Separate mockup round:** the real pages are built straight away, with one round of feedback around 2:25 PM.
- **Calendar and Network:** one plain static page each, no polish.
- **Hardening:** friendly errors on the demo path only; call limits and the 7-day cleanup wait until later.
- **Job count:** tag 30 jobs, not 100.

## 1. Tech stack (and why, in plain English)

| Choice | What it is | Why |
|---|---|---|
| **Python** for everything | One programming language | JobSpy is Python, so one language means one thing to learn |
| **FastAPI + Jinja templates** | The web app; pages built on the server | One deployable thing; no separate frontend project |
| **Tailwind (CDN)** | Ready-made styling classes | No build step |
| **SQLAlchemy** | A layer that talks to the database | Same code works with SQLite on your laptop and Postgres online |
| **SQLite (local/tests) → Postgres (production)** | The database | Chosen by one setting, `DATABASE_URL`. If it is not set, the app uses SQLite |
| **Render** (free web service) | Hosts the app at a public URL, auto-deploys from GitHub | Simplest free host for a Python server |
| **Supabase** (free Postgres) | Hosts the production database | Used only as a database; we don't use its login or other features |
| **google-genai SDK** | Official library to call Gemma 4 via the Gemini API | One small wrapper file |
| **Pydantic** (comes with FastAPI) | Checks that Gemma's JSON has the right shape | No extra dependency |
| **JobSpy** | Open-source job fetcher | Ingestion only, installed on your laptop only |
| **pypdf** | Reads text out of a PDF CV | Tiny, reliable |
| **pytest** | Test runner | For the one TDD example |

Not used: React/Next.js, a separate frontend, Docker, LangChain, vector databases, login/auth, background workers or queues.

**Two requirements files.** `requirements.txt` is what Render installs (no JobSpy). `requirements-ingest.txt` adds JobSpy for your laptop. JobSpy pulls in heavy libraries the server never needs, and the free Render service has little memory.

**Why not Vercel?** Vercel is built for JavaScript frontends. Using it would mean building a second app (React) plus a JSON API plus cross-site settings, roughly 1.5-2 extra hours, for no benefit the user can see. Our server-rendered pages are still a real, public app.

**Check before the event:**
- Current free-tier limits on Render and Supabase (free services sleep when idle; Render's free web service takes up to a minute to wake).
- Use Supabase's **pooled** connection string for `DATABASE_URL`; the direct one often fails from Render.
- The Gemma 4 model name on the Gemini API, its free-tier limits (requests and tokens per minute), and whether it supports JSON output mode. The design below works either way.

## 2. Architecture overview
One web app, one database, one data file. Scraping and job tagging happen on your laptop and produce a file that is committed to the repo. The server never scrapes and never tags jobs.

```
 YOUR LAPTOP (before the demo)                      PRODUCTION (public URL)
 ┌───────────────────────────────┐                  ┌─────────┐  HTML  ┌──────────────────┐
 │ scripts/ingest_jobs.py        │                  │ Browser │ <────> │ FastAPI on Render│
 │ JobSpy → raw jobs → Gemma tags│                  └─────────┘        └───┬─────────┬────┘
 └──────────────┬────────────────┘                                        │         │
                ▼                                     loads at startup    │         │ max 2 Gemma
     data/jobs_tagged.json  ── git push ──────────▶  if jobs table empty  │         │ calls / visitor
     (the single source of truth for jobs)                                ▼         ▼
                                                     ┌─────────────────────┐   ┌──────────┐
                                                     │ Postgres (Supabase) │   │ Gemma 4  │
                                                     │  jobs (shared)      │   │ via      │
                                                     │  visitors (private) │   │ Gemini   │
                                                     └─────────────────────┘   └──────────┘
```

Why the tagged file is the source of truth (instead of the laptop writing straight into the online database):
- The laptop never needs the production database password.
- Every deploy, and anyone who clones the open-source repo, gets a working demo with no scraping and no Gemma key.
- There is one job-loading path, not two (live + fallback).
- If the online database is wiped or swapped for SQLite, the jobs come back by themselves.

**Database fallback.** Because jobs reload from the file, the app works identically on SQLite. If Supabase is not connected within 10 minutes in slice 0, leave `DATABASE_URL` unset on Render and carry on. Cost: visitor data resets when Render restarts the app (after ~15 idle minutes or a deploy). Acceptable for a demo; switch to Supabase later by setting one variable.

## 3. End-to-end data flow

```
 AHEAD OF TIME (laptop)
   JobSpy ──▶ data/jobs_raw.json ──▶ tagging.py (Gemma + checks in code) ──▶ data/jobs_tagged.json

 PER VISITOR (online)
   CV + dream jobs + preferences
        │  GEMMA CALL A (one call): extract CV skills, level, dream-job skills, summary
        │  skills.py: clean every skill name to one canonical spelling
        ▼
   Profile saved against the session (raw CV text is thrown away)
        │
        │  ranking.py + gaps.py: computed fresh on every page view (fast, no AI, nothing stored)
        ▼
   ranked jobs + "why"  ·  top gaps
        │  GEMMA CALL B (one call): explain top 5 gaps + suggest 1-3 projects; result saved
        ▼
   This Week / Jobs / Skill Gaps / Project Ideas pages
```

Only the two Gemma results are stored. Ranking and gaps are cheap rules over ~100 jobs, so they are recalculated on each page view; this means nothing can go stale when the visitor edits their profile.

**If Gemma is down:**
- Call A fails → code scans the CV text for known skill names (the list of skills seen in the tagged jobs). The profile has no narrative summary, but ranking and gaps still work.
- Call B fails → gaps still show (they come from code) without the "why this matters" sentence; Project Ideas shows a friendly message and a retry button.
- Jobs always show, because they were tagged ahead of time.

## 4. Code layout

```
app/
  main.py          # web routes (one plain function per page) + health check
  db.py            # tables; reads DATABASE_URL; loads jobs_tagged.json at startup if jobs table is empty
  sessions.py      # anonymous session cookie, "delete my data", removal of old visitor rows
  gemma.py         # the ONLY file that talks to Gemma: JSON parsing, validation, retry, timeout, call limits
  skills.py        # one spelling per skill ("ReactJS", "react.js" -> "React"); plain code, no AI
  profile.py       # Gemma call A: CV + dream jobs -> profile (with the no-AI fallback)
  ranking.py       # pure rules and maths, NO AI (the tested file)
  gaps.py          # gap scoring (no AI)
  projects.py      # Gemma call B: gap explanations + project recommendations
  tagging.py       # job description -> tags, keyword safety net, quote check (used by the ingest script only)
  templates/       # HTML pages (static placeholders for This Week actions / Calendar / Network)
scripts/
  ingest_jobs.py   # run on the laptop: fetch (JobSpy) -> tag -> write data/jobs_tagged.json
data/
  jobs_raw.json        # raw listings as fetched
  jobs_tagged.json     # tagged listings; what the app actually loads
  sample_cv.txt, dream_jobs.json   # demo inputs
tests/test_ranking.py
requirements.txt          # server
requirements-ingest.txt   # laptop only (adds JobSpy)
```

Rule: **AI lives in `gemma.py` and the files that call it; everything that decides lives in `ranking.py`, `gaps.py` and `skills.py`.**

### Contracts between the pieces
- `gemma.ask_json(prompt, schema, session_id=None)` → a validated object, or raises `GemmaUnavailable` / `GemmaLimitReached`. It does not rely on the API's JSON mode: it strips any code fences, parses the text, validates against the schema, retries once, and has a ~40 second timeout. `session_id=None` (the ingest script) skips the visitor limits.
- `skills.canonical(name)` → the one agreed spelling. **Every** skill from Gemma (CV, dream jobs, job tags, projects) passes through it before being stored or compared. Without this, "React.js" on a CV and "React" in a job would not match and ranking would be silently wrong.
- `ranking.rank(profile, jobs)` → list of `(job, score 0-100, blocked yes/no, block reason + quote, strong matches, missing skills)`, sorted with all unblocked jobs first, then by score. Blocked jobs therefore can never outrank a compatible job.
- `gaps.top_gaps(profile, ranked_jobs)` → list of `(skill, priority, number of relevant jobs asking for it, total relevant jobs, in dream jobs yes/no)`. "Relevant" = not blocked and graduate/junior level.

### Checks that code makes on Gemma's output (never trust, always verify)
- A sponsorship or work-mode quote must appear word-for-word in the job description; if not, the tag becomes `unclear` and the quote is dropped.
- Keyword safety net: if Gemma says `unclear` but the text contains a phrase like "no sponsorship", code overrides.
- Project skills not in the visitor's gap list are removed; a project left with none is dropped.
- All output is shown through Jinja's automatic escaping, so text from a CV or job advert cannot inject HTML.

## 5. Data (three small tables)
- `jobs` (shared): id (hash of the URL, used for de-duplication), title, company, location, description, URL, salary, date posted, required skills, optional skills, sponsorship + quote, work mode + quote, seniority, industry.
- `visitors` (one row per session): `session_id`, created date, CV skills, level, domains, summary, preferences, dream jobs (a list of up to 5: title, company, URL, skills), saved analysis (gap explanations + projects), Gemma call count. **No raw CV text and no pasted job descriptions are stored.**
- `gemma_usage`: one row per day with the total number of visitor-triggered calls (the overall limit).

Lists are stored as JSON columns, which work the same on SQLite and Postgres. "Delete my data" deletes one `visitors` row and clears the cookie. Visitor rows older than 7 days are removed at startup. Editing the profile clears the saved analysis so it is regenerated.

Placeholders (This Week actions, Calendar, Network): hard-coded sample content in templates, no tables.

### Session and request details
- Cookie: a long random id (`secrets.token_urlsafe`), `HttpOnly`, `Secure`, `SameSite=Lax`. It is unguessable, so no signing key is needed.
- Everything that changes data (onboarding, edit profile, delete) is a form POST, never a link.
- Routes are plain `def` functions so a slow Gemma call does not freeze the app for other visitors.
- Loading state: a few lines of inline script show an "Analysing your CV…" overlay when a slow form is submitted. No JavaScript framework.
- The health check does not touch the database or Gemma, so the host still sees the app as alive if either is down.

## 6. Before the hackathon starts (not part of the 4 hours)
Create accounts and keys so the clock isn't spent on sign-ups: GitHub (public repo), Render, Supabase (create a project, copy the pooled connection string), Gemini API key. Put keys in your local `.env` only (never in chat or the repo). Install Python 3.11+. Prepare one demo CV and 5 dream jobs.

## 7. Build slices (each ends with something working, then a git commit and push)

| # | Slice | Time | Done when |
|---|---|---|---|
| 0 | **Setup + deploy skeleton:** repo with MIT licence and `.gitignore`, hello-world FastAPI page + health check, connect Render to GitHub, set env vars on Render, connect to Supabase (10-minute limit, else SQLite), 3-line Gemma model test | 25m | Public URL shows the page; database connects; Gemma replies |
| 1 | **Ingestion spike (go/no-go):** JobSpy fetch on your laptop into `data/jobs_raw.json` | 15m | ≥30 real listings in the file |
| 2 | **Gemma wrapper + job tagging:** `gemma.py`, `skills.py`, `tagging.py`; start the tagging run (it can be stopped and resumed), write `jobs_tagged.json`; app loads it at startup and lists jobs on a plain page | 30m | ≥30 tagged jobs visible online; you hand-check 10 |
| 3 | **Onboarding + profile + sessions:** CV upload/paste, up to 5 dream jobs, preferences, privacy note, anonymous session, Gemma call A, profile page | 35m | Two browsers see different profiles online |
| 4 | **Ranking (TDD):** write the sponsorship test FIRST, watch it fail, then build scoring and hard constraints | 20m | Test passes; "why" text produced |
| 5 | **Gaps + projects:** gap scoring in code; Gemma call B for explanations and 1-3 projects | 20m | Top gaps and projects generated |
| 6 | **UI:** This Week, Jobs, Skill Gaps, Project Ideas styled from `DESIGN.md`; static placeholder content for Calendar and Network | 35m | Pages match the mockup roughly, online |
| 7 | **Hardening + polish:** input limits, Gemma call limits, "Delete my data", friendly error/empty/loading states, visual polish | 30m | Nothing shows a raw error on the demo path |
| 8 | **Spec review + demo prep:** review against `spec.md`, README, warm up the app, rehearse twice, screen-record a backup | 15m | Demo works from the public URL |

**Why tagging comes before onboarding:** tagging ~100 jobs is slow (rate limits), so it should be running in a terminal while onboarding is being built. It also produces the list of known skills that the profile step uses.

**Tagging must be resumable:** the script saves each job's tags as it goes and skips jobs already tagged, so a rate-limit error or a closed laptop loses nothing. Start with 30 jobs; add more if time allows.

**Deploy early, deploy often:** every push to GitHub auto-deploys, so each slice is tested online, not just locally.

**Ingestion go/no-go (slice 1):** if JobSpy has not returned usable jobs within ~15 minutes, stop fighting it. Use a realistic hand-built `jobs_raw.json` of 30+ listings, keep the JobSpy code path in the repo, and move on.

Slices 1 and 2 need no design, so they can be built while `DESIGN.md` is still being turned into mockups. Slice 6 needs the design.

## 8. The one TDD example
Test first, in `tests/test_ranking.py`: *a user who needs sponsorship, one job tagged `no_sponsorship` with a higher skill match, one tagged `sponsors` → the sponsoring job ranks higher and the other is flagged.* No AI or database is involved, so it runs instantly. Workflow: write test → run it, see red → build `ranking.py` until green.

## 9. Production basics (slice 7 checklist)
- Gemma failure or bad JSON: retry once, then the fallbacks in section 3; never crash.
- No API key or no network: friendly message; pre-tagged jobs still show.
- Limits: ~2 MB upload, PDF/text only, capped text lengths, max 5 dream jobs. Gemma: a small number of calls per visitor (enough for 2 calls plus a few retries/edits) and a daily total across all visitors. The daily total is the real protection, since a visitor can clear their cookie.
- Privacy: note on onboarding, raw CV text never stored or logged, "Delete my data", old visitor rows removed after 7 days, secrets only in environment variables.
- Health-check route; app wakes cleanly after sleeping (jobs reload from the file if needed).
- Empty states for every page; loading overlay on the two slow steps.

## 10. How to use Claude Code to save tokens and learn
- Keep `CLAUDE.md` short; it is read automatically.
- One slice per session; `/clear` between slices; commit after each.
- Plan mode only for slices 2 and 4. Others, just ask directly.
- **Subagents (selective):** one reviewer at slice 8 that compares the app to `spec.md` and lists gaps. Optionally one to hand-check tags in slice 2.
- Skills/Superpowers: skip.
- Don't paste whole job datasets into chat; point Claude at the files.

## 11. Gemma usage per slice
Slice 2: tag jobs (core, offline, one call per job) · Slice 3: call A (CV + dream jobs → profile) · Slice 5: call B (gap explanations + projects). All via `gemma.py`.

Per visitor that is **two calls**, not one per dream job and one per gap. Fewer calls means a faster onboarding, simpler limits, and less chance of hitting the free-tier per-minute cap during the demo.

## 12. Risks and cut-list
Biggest technical risks: **ingestion** (early spike), **deployment surprises** (deploy in slice 0), **Gemma rate limits during tagging** (resumable script, start early), then the Gemma model name/access (slice 0).
If behind schedule, cut in this order: (1) placeholder polish, (2) project milestone detail, (3) PDF upload (paste text only), (4) Supabase (run on SQLite), (5) styling polish. Never cut: tagging, skill-name cleaning, the ranking test, deployment, session isolation, "Delete my data", error handling on the demo path.
Stretch only if finished early: screenshot-a-job-ad, local Gemma via Ollama.

## 13. Final-hour checklist
README (what it does, live URL, "JobSpy fetches, Gemma 4 understands, plain code decides", run and deploy steps) · MIT licence · no keys committed · public GitHub repo · app warmed up and demo rehearsed twice · backup screen recording · submit on MLH.
