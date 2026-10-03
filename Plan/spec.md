# CareerOS Hackathon MVP

## 1. Product Summary
CareerOS is a career development platform for early-career software graduates (not just a job board). The user uploads a CV and picks up to 5 dream jobs. The platform has six sections:
1. **This Week**: the landing page; what to do next.
2. **Jobs**: live listings ranked against the user, with reasons.
3. **Skill Gaps**: what the user is missing, prioritised.
4. **Project Ideas**: portfolio projects that close the top gaps.
5. **Calendar**: relevant dates (grad scheme deadlines, hackathons, events).
6. **Network**: track people/contacts and follow-ups.

Core differentiator: a normal job board filters on structured fields. CareerOS uses an LLM to **read the fine print in descriptions** ("we will consider sponsorship", "UK residents only", "onsite 4 days", real tech stack) and turns it into searchable, rankable tags.

Hackathon depth: Jobs, Skill Gaps, Project Ideas are **real** (built end to end). This Week, Calendar and Network are **UI placeholders** with static sample content (see Scope). CareerOS is a **deployed web app** at a public URL that other people can use, not a local script.

## 2. Hackathon Goal
Demoable end-to-end loop in 4 hours: **CV + dream jobs → profile → AI-tagged jobs → rank → gaps → projects**, running at a public URL. Secondary goal: learn the spec → design → plan → test → build → review workflow. Targets prizes: Best Use of Gemma 4 and Best Open-Source AI Project (public GitHub repo + open-source licence required; submission rules still to be checked on the MLH submission page). Being deployed means judges and friends can use it without installing anything.

## 3. Target User
Early-career software graduate (UK-focused for demo, e.g. Bradford/Birmingham), possibly needing visa sponsorship, unsure what to learn next.

## 4. Core User Journey
0. Open the public URL. No sign-up; an anonymous session keeps each visitor's data separate.
1. Onboard: paste/upload CV, add up to 5 dream jobs (paste job descriptions or just titles), optional preferences.
2. App builds a career profile (skills, level, target role types, recurring skills across dream jobs).
3. App shows ~30–100 AI-tagged jobs from the shared job database (ingested ahead of time by the developer).
4. This Week (landing page): top jobs, top skill gaps, recommended project, sample priority actions.
5. Jobs page: ranked cards with "why".
6. Browse Skill Gaps, Project Ideas, and the Calendar and Network placeholder pages.

## 5. MVP Scope
**Real:** anonymous sessions, onboarding, career profile, job ingestion + AI tagging, ranking with explanations, skill gaps, 1–3 project recommendations, the Jobs / Skill Gaps / Project Ideas pages, privacy basics, public deployment.

**UI placeholders only (static sample content, no working logic, each labelled "Preview"):**
- This Week: landing page showing sample priority actions next to the real top jobs, gaps and project where cheap.
- Calendar: sample deadlines and events as a list/month view. No add, no discovery.
- Network: sample contacts. No add/edit, no integrations.

They exist so the product looks like the full platform.

## 6. Out of Scope
- Login/accounts (an anonymous session cookie is used instead; real sign-in is future work)
- Continuous/scheduled scraping and alerts (shown as a "Watchlist (coming soon)" mock); users cannot trigger scraping, ingestion is a script the developer runs
- Working Calendar, Network CRM and weekly planning logic
- Chatbot UI, payments, email, mobile app, a separate JavaScript frontend
- Cover letters, auto-apply, interview prep
- Perfect scraping coverage

## 7. Functional Requirements

### CV & Dream Job Input
- FR1: Upload PDF or paste text for CV.
- FR2: Add 1–5 dream jobs (pasted description or just a title). Hard cap of 5.
- FR3: Optional: location, min salary, needs sponsorship (yes/no), industries, preferred technologies.
- FR4: Each visitor's data is saved in the database against an anonymous session cookie, so reloading keeps it and different visitors never see each other's data. The raw CV text is used once to build the profile and is **not stored**; only the extracted profile is.

### Career Profile
- FR5: Gemma extracts from CV: skills, experience level, domains.
- FR6: Gemma extracts skills from each dream job; code counts recurring skills across them. The CV and all dream jobs are sent in **one** Gemma call.
- FR6a: Every skill name from Gemma (CV, dream jobs, job tags, projects) is cleaned by code to one agreed spelling (e.g. "ReactJS", "react.js" → "React") before it is stored or compared. Matching depends on this, so it is code, not a prompt.
- FR6b: If Gemma is unavailable, code builds a basic profile by scanning the CV for skill names already seen in the tagged jobs, so ranking and gaps still work.
- FR7: Profile page shows: current skills, target roles, "skills your dream jobs share", short narrative summary.

### Job Ingestion
- FR8: An ingestion script, run by the developer on their own machine, pulls ~30–100 listings via open-source JobSpy (title, company, location, description, salary if present, URL) and writes them, once tagged, to a snapshot file (`data/jobs_tagged.json`) that is committed to the repo. Job sites often block cloud servers, so the deployed app never scrapes live.
- FR9: Listings are stored in one shared jobs table (the same for all users); duplicates removed.
- FR10: The tagged snapshot file is the single source of jobs: the app loads it into the jobs table at startup when the table is empty. The deployed demo, and anyone who clones the repo, never depends on live scraping or on a Gemma key to see jobs. If JobSpy fails, the snapshot is built by hand from real listings.
- FR11: Each listing is AI-tagged once, at ingestion time (stored and shared by all users, never re-tagged on view) with:
  - required skills / nice-to-have skills
  - sponsorship: `sponsors` / `no_sponsorship` / `unclear`, **plus the quoted phrase as evidence**
  - work mode: remote / hybrid / onsite / unclear (plus quote if restrictive, e.g. "UK residents only")
  - seniority: graduate / junior / mid / senior
  - industry
  - salary range if stated in the text
- FR12: A cheap keyword scan runs alongside as a safety net; if Gemma says "unclear" but the text contains "no sponsorship", code overrides.
- FR12a: Code checks that every evidence quote appears word-for-word in the job description. If it does not, the tag is set to `unclear` and the quote is dropped, so a made-up quote is never shown.

### Job Ranking (deterministic code, no AI)
- FR13: Score 0–100 from weighted: skill overlap, missing required skills, similarity to dream-job skills, seniority fit, preferred tech, industry, location, salary.
- FR14: **Hard constraints** mark a job as *blocked*: the visitor needs sponsorship and the job is tagged `no_sponsorship`, or the stated salary is more than 20% below the visitor's minimum. Blocked jobs are still shown, flagged with the reason and quote, but always sorted below every unblocked job. `unclear` sponsorship is not blocked; it is shown as "unclear". Location, industry and preferred tech are soft preferences that only nudge the score (reliable location blocking needs geocoding, which is out of scope).
- FR15: Each job shows: match %, strong matches, missing skills, constraint status with evidence quote.

### Skill Gap Analysis
- FR16: Missing skills = skills in dream jobs/live jobs not in the CV.
- FR17: Priority score (code) from: frequency across live jobs, required vs optional, appearance in dream jobs, number of *relevant* jobs requesting it. A *relevant* job is one that is not blocked and is graduate or junior level.
- FR18: Gemma writes a 1–2 sentence "why this matters for you" per top gap (top 5). This and the project recommendations (FR19) come from **one** Gemma call, and the result is saved so it is not regenerated on every page view.

### Project Recommendations
- FR19: Gemma gets (top gaps + dream roles + CV level) and returns 1–3 projects: title, description, skills demonstrated, suggested stack, rough scope (e.g. "weekend / 2 weeks"), 3–5 milestones, which gaps/jobs it helps.
- FR20: Output must be structured (so the UI can render cards) and every demonstrated skill must come from the gap list. Code enforces this: skills not in the gap list are removed, and a project left with none is dropped.

### Privacy, Safety & Reliability (production basics)
- FR21: Anyone can open the public URL and use the app with no install and no sign-up.
- FR22: A "Delete my data" button removes the visitor's profile, dream jobs, gap explanations and projects. Visitor data older than 7 days is removed automatically.
- FR23: Onboarding shows a short privacy note (the CV is sent to Google's Gemini API to be analysed, the CV itself is not stored, the profile built from it is kept for up to 7 days, delete anytime). CV text and API keys are never written to logs or committed to the repo.
- FR24: Input limits: PDF or text only, ~2 MB max, max 5 dream jobs, capped text lengths.
- FR25: Basic cost protection: a per-visitor limit and an overall daily limit on Gemma calls, with a friendly message when reached. A normal visit uses two calls.
- FR26: Friendly error, empty and loading states everywhere; never a raw error page.
- FR27: A health-check route for the host; all secrets come from environment variables.

## 8. Gemma Usage
Simplest useful integration: **Gemma 4 through the Gemini API** (one hosted API, no local GPU setup). One small wrapper; every call asks for structured JSON.

| Task | Why Gemma |
|---|---|
| Normalise CV and dream-job skills | messy free text |
| Tag each job description (sponsorship + evidence, work mode, seniority, industry, skills) | the "hidden in the text" value |
| One-line gap explanations | natural language |
| Project recommendations | creative but grounded in the gap list |
| Career profile summary | short narrative |

Not Gemma: ranking math, hard constraints, salary/location filters, dedupe, counting skill frequency. Gemma is asked to cite the exact phrase for sponsorship so users can trust it.
Cost/speed control: jobs are tagged once at ingestion and stored, so each visitor triggers only **two** Gemma calls (one for CV + dream jobs → profile, one for gap explanations + projects), and those are rate-limited. Gemma's output is never trusted blindly: code validates the JSON shape, cleans skill names, and checks quotes against the source text.
Gemma 4 via the Gemini API is the open-weight AI core. Stretches only: (a) upload a screenshot of a job ad as a dream job, with Gemma 4 extracting the text and skills; (b) run Gemma locally via Ollama as an alternative backend with the same prompts.

## 9. Open Source Usage
- **JobSpy (python-jobspy)**: used only for the "fetch raw listings" step. It is the pipe, not the product. Anyone can fetch listings; nobody else reads descriptions for sponsorship wording, ranks against your CV, or turns gaps into projects. **Our original work:** AI tagging pipeline, evidence-quoted sponsorship detection, hard-constraint ranking, gap prioritisation, project recommender, the app. JobSpy is credited openly in the README and demo, used as a library under its licence, not copied.
- **Gemma 4** (open-weights) is the AI core, accessed through the Gemini API. Optional local Gemma via Ollama (stretch) strengthens the open-weight story.
- Repo published publicly on GitHub with an open-source licence (MIT) to meet the "Best Open-Source AI Project" rule.
- Keep other libraries minimal. (See `IMPLEMENTATION_PLAN.md` for the stack.)

## 10. Acceptance Criteria
- AC1 Onboarding: with a CV and 5 dream jobs the profile is created; a 6th dream job is rejected; preferences are optional.
- AC2 Profile: shows ≥5 normalised CV skills and recurring dream-job skills; reloading keeps data.
- AC3 Ingestion: the committed snapshot holds ≥30 de-duplicated, tagged jobs, and a freshly started app with an empty database shows them without scraping or calling Gemma.
- AC4 Tagging: ≥90% of jobs have all tag fields filled; every `sponsors`/`no_sponsorship` tag has a quote that appears word-for-word in the description; hand-check 10 jobs, ≥8 correct.
- AC5 Ranking: jobs sorted by score; for a user needing sponsorship, a "no sponsorship" job never ranks above a compatible job (and is visibly flagged); each job's explanation lists matches, missing skills, constraints.
- AC6 Gaps: top 5 gaps shown with explanations; a skill the CV already has never appears as a gap.
- AC7 Projects: 1–3 projects with all required fields; each links to ≥1 top-5 gap.
- AC8 UI: This Week (landing), Jobs, Skill Gaps and Project Ideas work with real data. Calendar and Network show clearly labelled sample content.
- AC9 Reliability: the full demo runs from stored jobs (no live scraping) in under ~2 minutes after onboarding.
- AC10 One automated test (the sponsorship ranking rule from AC5) exists and passes.
- AC11 Deployed: the app runs at a public URL on free hosting; a fresh browser with no sign-in can complete the whole journey.
- AC12 Isolation: two different browsers see only their own profile and data.
- AC13 Privacy: "Delete my data" removes the visitor's data; no keys or CV text are in the repo, the logs or the database.
- AC14 Resilience: with the Gemma key removed or the API failing, pages show a friendly message, the pre-tagged jobs still display, and onboarding still produces a basic profile and ranked jobs (FR6b).

## 11. Constraints
- 4 hours total; runs locally with one command AND deploys to free hosting from the public GitHub repo (auto-deploy on push).
- Minimal dependencies; no microservices, user accounts or Kubernetes. Infrastructure is limited to one web service plus one Postgres database on free tiers.
- Claude Code Pro token budget: small vertical slices, concise prompts, subagents only where useful.
- API keys kept out of the repo; set as environment variables on the host.
- Free-tier limits (cold starts, database pausing) are accepted; warm the app before the demo.
- Respect job-site terms: small volume, demo/educational use, snapshot saved.

## 12. Risks & Assumptions

| Risk | Mitigation |
|---|---|
| Live scraping blocked/rate-limited (LinkedIn/Indeed) | Ingestion runs locally by the developer, never on the server; save snapshot early; prefer Indeed; modest volume |
| Gemma returns inconsistent JSON | Strict JSON schema in prompt, validate, retry once, skip bad rows |
| Gemma mis-tags sponsorship | Require quote evidence + keyword safety net; show "unclear" honestly |
| API rate limits/slow tagging | Tag once at ingestion and store; the tagging script saves as it goes and can be resumed; start it early so it runs while other slices are built |
| Skill names don't match ("React.js" vs "React") so ranking is silently wrong | One code function cleans every skill name before storing or comparing |
| Gemma API has no JSON mode for this model | The wrapper parses and validates JSON from plain text; does not rely on JSON mode |
| Supabase connection trouble on the day | Jobs load from the snapshot file, so the app runs unchanged on SQLite; 10-minute limit on fighting it |
| Job pool doesn't suit a visitor's dream jobs | Jobs are UK software graduate roles; say so on the Jobs page |
| Strangers burn the Gemma quota | Per-visitor and overall call limits; job tagging happens offline once |
| Free-tier cold start / database pausing | Deploy a hello-world first; open the URL before the demo; keep a screen recording as backup |
| Personal data (CVs) | Raw CV never stored; profile is session-scoped and removed after 7 days; delete button, privacy note, nothing logged |
| Scope creep (placeholders, extras) | Placeholders are static; built late |
| Salary/location often missing | Treat as "unknown", not a penalty |
| Non-technical operator | Plain-language explanation at each step; small slices |

Assumptions: Gemini API key with Gemma 4 access is available (**exact model name to be confirmed at build time**); Python is available; Render, Supabase and GitHub accounts exist before the event; demo uses UK software graduate roles; one demo CV is prepared in advance.

## 13. Demo Success Criteria
1. Open the public URL on a phone or fresh browser. Upload a real-looking CV, add 5 dream jobs, set "needs sponsorship".
2. Show the profile, then the tagged jobs.
3. Show a job where Gemma found sponsorship wording a normal filter would miss, with the quote.
4. Show ranked jobs with explanations; show a "no sponsorship" job pushed down.
5. Show top gaps and 1–3 tailored projects.
6. Walk through This Week, Calendar and Network as the rest of the platform.
7. Say clearly: "JobSpy fetches, Gemma 4 understands, plain code decides."
