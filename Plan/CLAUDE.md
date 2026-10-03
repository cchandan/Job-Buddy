# CareerOS Claude Instructions

Read these before making changes:

* `spec.md`
* `DESIGN.md`
* `IMPLEMENTATION_PLAN.md`

Project rules:

* Keep the solution simple and hackathon-friendly.
* Do not add unnecessary frameworks, services, infrastructure or dependencies.
* Backend/app language: Python.
* Web app: FastAPI + Jinja templates, server-rendered. No separate JavaScript frontend.
* Database: SQLAlchemy reading `DATABASE_URL`. SQLite locally and in tests, Postgres (Supabase) in production.
* Hosting: Render for the app, deployed from GitHub. Deploy every slice, not just at the end.
* Visitors use an anonymous session cookie, no login. Per-visitor data is stored against `session_id`; the jobs table is shared.
* Gemma access must go through `app/gemma.py`, including call limits and JSON validation. A visitor triggers at most two Gemma calls (profile; gaps + projects).
* Never trust Gemma output: every skill name goes through `app/skills.py`, and every evidence quote must appear word-for-word in the source text.
* Never store raw CV text; only the extracted profile is saved.
* AI should interpret unstructured data, but deterministic code should handle ranking rules, hard constraints and scoring.
* Do not move ranking logic into prompts.
* Job ingestion and tagging use JobSpy and run locally via `scripts/ingest_jobs.py`, never on the server. The output, `data/jobs_tagged.json`, is committed and is the only source of jobs; the app loads it at startup when the jobs table is empty.
* JobSpy goes in `requirements-ingest.txt`, not `requirements.txt`.
* Preserve a reliable demo path even if live ingestion or AI calls fail. Ranking and gaps are computed on each page view and must work without Gemma.
* Never log CV text. Never commit secrets; all keys come from environment variables (`.env` locally, host settings in production).
* Show friendly error, empty and loading states; never a raw error page.
* Implement one slice at a time from `IMPLEMENTATION_PLAN.md`.
* Do not add features outside the MVP unless explicitly asked.
* Keep changes small and easy for a non-technical user to understand.
* Before finishing a slice, run the relevant tests and check the acceptance criteria.
