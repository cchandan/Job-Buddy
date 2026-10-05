# Job Buddy Specification

## 1. Product Summary
Job Buddy is a private job-application workspace for a family. A small number of job seekers ("users") find jobs on a shared Job Board, shortlist them into a personal Tracker, create a CV tailored to each job, apply, and track the outcome. Admins keep the Job Board fresh, assign jobs to users and review their tailored CVs.

It is a real product in daily use, not a demo. It was rebuilt from the CareerOS hackathon project (see section 14 for what changed).

The app has six sections:
1. **Home**: the landing page after sign-in, one for users and one for admins. It shows what needs doing next.
2. **Job Board**: every live job collected for the family, tagged, with deadline and status. Jobs are shortlisted from here.
3. **Tracker**: the user's shortlisted, assigned and manually added jobs, with deadline, status and actions.
4. **Calendar**: the deadlines of the jobs in the Tracker.
5. **Profile**: the user's core CV, the base for every tailored CV.
6. **Admin** (admins only): view any user's sections, assign jobs, review CVs, fetch and sync jobs, manage accounts.

## 2. Roles and Accounts
There are two roles. Every person has an account with a username and password that an admin created for them. The role is chosen when the account is created.

| Role | Who (today) | Can do |
|---|---|---|
| **User** | Chandan's wife, Chandan's brother | Use their own Home, Job Board, Tracker, Calendar and Profile |
| **Admin** | Chandan, Chandan's dad | Everything in section 7 "Admin"; see every user's sections |

- There is no sign-up. The only way in is an account an admin has created.
- Admins create accounts, issue new passwords and remove accounts from Admin home.

## 3. Core Journeys

**User**
1. Sign in with the username and password an admin gave them, and land on Home.
2. Upload a core CV on Profile (once; replace it any time).
3. Browse the Job Board, filter, and shortlist jobs.
4. Open the Tracker: shortlisted jobs, jobs an admin assigned, and jobs added by link or by hand.
5. For a job: create a tailored CV, send it for review if wanted, press Apply (opens the advert), then set the status to Applied.
6. Keep the status up to date (Interviewing, Offer, Rejected, Withdrawn).
7. Check the Calendar for upcoming deadlines.

**Admin**
1. Sign in and land on Admin home.
2. Once a day, on their own laptop: fetch and tag new jobs, check the result, sync it to the Job Board.
3. Pick a user and look at their Tracker, Calendar and Profile.
4. Assign a job to a user; it appears in that user's Tracker marked with who assigned it.
5. Review a tailored CV: approve it or ask for changes with a comment.

## 4. Scope
**In scope:** username and password sign-in with admin-created accounts, two roles, a Home page for each role, Job Board, shortlisting, Tracker (shortlist, add by link, add by hand, assigned jobs), deadlines and statuses, tailored CVs, CV review, working Calendar, Profile with a stored core CV, Admin panel, local job fetching and tagging, daily sync.

**Removed from CareerOS:** Project Ideas, Network, anonymous cookie sessions, the public marketing landing page, the 7-day automatic deletion of visitor data, and all hackathon constraints. Skill Gaps, This Week, dream jobs and match-score ranking are also removed (assumption A1, section 13).

## 5. Out of Scope
- Open sign-up, Google or any other third-party login, and password reset by email. A forgotten password is replaced by an admin.
- Applying on the user's behalf (auto-apply). Apply only opens the advert.
- Cover letters, interview prep, chat UI, email or push reminders, a mobile app.
- Job fetching on the server. Job sites block cloud servers, so fetching always runs on an admin's laptop.
- Payments, multi-family or multi-tenant use.

## 6. Key Concepts

### Listing status (a fact about the advert; the same for everyone)
| Status | Meaning |
|---|---|
| Open | Deadline is in the future, or no deadline is known and the job is still being found |
| Closing soon | Deadline is within 7 days |
| Closed | Deadline has passed |
| Possibly closed | No known deadline and the job has not appeared in a fetch for 30 days |

Listing status is worked out by code from the deadline and the fetch dates. It is never stored by hand and never decided by AI.

### Application status (a fact about one user and one job; set by the user)
`Shortlisted` → `Applied` → `Interviewing` → `Offer` / `Rejected` / `Withdrawn`

A user may move a job to any status at any time (people correct mistakes). Each change is recorded with the date.

### Tailored CV status
`None` → `Draft` → `In review` → `Approved` / `Changes requested`

Review is optional: a user can apply with a Draft.

### Deadline
- Taken from the advert when stated. The words it came from are kept as evidence.
- If the advert states none, the deadline is "Unknown".
- A user or admin can set or correct the deadline on a Tracker entry. A hand-set deadline wins over the advert's.

## 7. Functional Requirements

### Sign-in and roles
- FR1: Sign-in is by username and password. There is no anonymous use and no sign-up; every page except the sign-in page and the health check needs a signed-in person.
- FR2: Accounts are created by an admin, who enters the person's name, a username and a role. The app generates a strong password and shows it to the admin once, to pass on. Passwords are stored only as a salted hash and are never shown again, logged or emailed.
- FR3: An admin can generate a new password for any account (the old one stops working at once, and that person is signed out) and can remove an account. A person can change their own password on Profile. The first admin account is created by a one-off setup command.
- FR3a: A wrong username or password gives one plain message that does not say which was wrong. After 5 wrong attempts in a row the account is locked for 10 minutes.
- FR4: Sign-out is available on every page.
- FR5: Access is checked on the server for every request. A user can never read or change another user's Tracker, CVs or Profile, whatever URL they type. Admin pages refuse non-admins.

### Home (landing pages)
- FR5a: After sign-in a user lands on **Home**. It answers "what needs doing next" from their own data: deadlines in the next 7 days not yet applied to, jobs an admin has newly assigned, tailored CVs with a review outcome waiting, counts by application status, and how many jobs are new on the Job Board since their last visit. Every item links to the place where it is dealt with.
- FR5b: A user with nothing yet sees a short getting-started list instead: upload your CV, browse the Job Board, shortlist a job.
- FR5c: After sign-in an admin lands on **Admin home**. It answers "does anyone need me today": CVs waiting for review, how fresh the Job Board is, deadlines close with no application yet, one card per user (status counts, next deadlines, last sign-in), and account management (FR39).
- FR5d: Home pages hold no data of their own. Everything on them is worked out from the Tracker, CVs and jobs, so they can never disagree with the other pages.

### Job Board
- FR6: The Job Board lists every job in the shared jobs table. It is the same list for every user.
- FR7: Each job shows: title, company, location, salary if known, **deadline**, **listing status**, the tags (FR24), date first seen, and the signed-in user's own state for that job (not shortlisted / shortlisted / application status).
- FR8: A **Shortlist** button adds the job to the user's Tracker. Shortlisting twice does nothing. A shortlisted job shows "In your Tracker" with a link.
- FR9: Filter by listing status, location, work mode, sponsorship and seniority; search by title or company. Sort by deadline (soonest first, the default), newest, or company.
- FR10: Closed jobs are hidden by default and can be shown with a filter. They are never deleted while any Tracker entry points to them.

### Tracker
- FR11: The Tracker lists the user's entries. An entry comes from one of four routes:
  1. shortlisted from the Job Board,
  2. assigned by an admin,
  3. added by link,
  4. added by hand.
- FR12: Every entry shows **who added it** ("Added by you", "Assigned by Dad") and when.
- FR13: Every entry shows the same job details as a Job Board card, plus **deadline**, **application status**, tailored-CV status and private notes.
- FR14: **Add by link.** The user pastes a job URL. The app fetches the page, reads it with the same tagging used for the Job Board, and creates the entry in the same format.
  - If the link is already on the Job Board, the entry points to that job; no duplicate is made.
  - If the page cannot be fetched or read, the user is told plainly and offered the manual form with the URL already filled in. Nothing is half-saved.
- FR15: **Add by hand.** A form with: title and company (required), URL, location, salary, deadline, work mode, sponsorship, description, notes.
- FR16: Jobs added by link or by hand are private to that user's Tracker. They do not appear on the shared Job Board.
- FR17: **Actions on an entry:**
  - **Create tailored CV** (FR28).
  - **Apply**: opens the advert in a new tab. It does not change the status by itself; the app then asks "Did you apply?" with a one-click "Mark as Applied". Hidden when the entry has no URL.
  - **Set status**: any application status from section 6.
  - Edit deadline, edit notes, remove from Tracker (with confirmation).
- FR18: Filter the Tracker by application status; sort by deadline (default) or date added. Entries with a passed deadline that are still `Shortlisted` are flagged "Deadline passed".

### Calendar
- FR19: The Calendar shows the deadline of every Tracker entry that has one, in a month view and an upcoming list.
- FR20: Each item shows title, company and application status, and links to the Tracker entry. Deadlines already applied to are shown differently from ones still to do.
- FR21: Entries with an unknown deadline are listed under "No deadline set" so they are not forgotten.
- FR22: Changing a deadline in the Tracker changes the Calendar immediately; there is no separate calendar data.

### Profile and core CV
- FR23: The user uploads one core CV (PDF, or pasted text). The original file and the text read from it are stored. The user can view, download, replace or delete it. The core CV is the base for every tailored CV.

### Job tagging
- FR24: Each job is tagged once, when it is fetched or added by link. Tags (**provisional**, see A2):
  - deadline, with the quoted phrase
  - sponsorship: `sponsors` / `no_sponsorship` / `unclear`, with the quoted phrase
  - work mode: remote / hybrid / onsite / unclear, with a quote if restrictive
  - seniority, industry, required skills, salary range if stated
- FR25: AI reads the advert; code checks the result. Every quote must appear word-for-word in the advert, or the tag becomes `unclear` and the quote is dropped. A stated deadline must parse as a real date, or it becomes "Unknown".
- FR26: A keyword scan runs alongside. If AI says `unclear` but the text says "no sponsorship", code overrides. If AI is unavailable, the keyword scan alone tags the job.
- FR27: Skill names are cleaned to one spelling by code before they are stored.

### Tailored CVs
- FR28: **Create tailored CV** takes the user's core CV and the job's description and produces a version aimed at that job. One tailored CV per Tracker entry; it can be regenerated.
- FR29: The tailored CV only reorders, selects and rewords what is in the core CV. It must never add an employer, job title, qualification, date or skill that the core CV does not contain. The prompt states this, and the screen shows the core CV next to the tailored one so the user can check.
- FR30: The user can edit the tailored CV as text, save it, and download it as a PDF.
- FR31: The user can send it for review. An admin then approves it or asks for changes with a comment; the user sees the outcome on the Tracker entry. Editing an approved CV returns it to Draft.
- FR32: Create tailored CV is unavailable, with a clear reason, when there is no core CV or the entry has no description.

### Admin
- FR33: **View as user.** An admin picks a user and sees that user's Job Board state, Tracker, Calendar and Profile. A banner always shows whose data is on screen.
- FR34: **Assign a job.** From the Job Board, or by link or by hand, an admin adds a job to a user's Tracker. It is marked "Assigned by <admin name>". Assigning a job the user already has does nothing.
- FR35: **Review CVs.** A list of every tailored CV that is In review, across all users, with the job, the core CV and the tailored CV side by side, and Approve / Request changes with a comment.
- FR36: **Fetch and tag jobs.** A button that runs JobSpy and the tagging on the admin's laptop, using the search settings (FR38). It shows progress and can be stopped and resumed without losing work. It is only available when the app is running on a laptop with JobSpy installed; on the live site the button is replaced by a note explaining this.
- FR37: **Sync.** After a fetch, the admin sees a summary (new, updated, unchanged, failed to tag) and presses Sync to publish to the shared jobs table, which every user's Job Board reads.
  - New jobs are added; existing jobs are updated; jobs are matched by URL.
  - Sync never deletes a job and never touches Tracker entries, statuses, hand-set deadlines or CVs.
  - Each sync is recorded: who ran it, when, and the counts. The Job Board shows "Last updated <date>".
- FR38: **Search settings.** Admins set what is fetched: job titles or keywords, locations, and how many results per search (see A3).
- FR39: **Accounts.** From Admin home an admin creates an account, generates a new password for one, or removes one (FR2, FR3). There is no separate People page. The sync history is on the Jobs sync page.

### Privacy, Safety and Reliability
- FR40: The app is private: nothing is visible without signing in, and pages are marked not to be indexed by search engines.
- FR41: CVs are personal data. They are stored only in the database, never in the repo and never in logs. The Profile page says plainly that the CV is stored and is sent to the AI provider when a tailored CV is created.
- FR42: A user can delete their core CV and any tailored CV. An admin can remove a person and all of their data.
- FR43: Input limits: PDF or text only, about 2 MB per CV, capped text lengths, capped number of Tracker entries per user.
- FR44: A daily limit on AI calls across the app, with a friendly message when reached.
- FR45: Friendly error, empty and loading states everywhere; never a raw error page.
- FR46: A health-check route for the host. All secrets come from environment variables.
- FR47: The database is backed up by the host; losing it would lose every Tracker and CV, so a temporary database is not acceptable in production.

## 8. AI Usage
AI is used for exactly three jobs, all through one wrapper file:

| Task | When | Why AI |
|---|---|---|
| Tag a job advert (deadline, sponsorship, work mode, skills, and so on) | At fetch time, on the admin's laptop | The facts are buried in free text |
| Read a job page added by link | When a user or admin adds a link | Same, for one advert |
| Write a tailored CV | When the user asks | Rewording for a specific job |

Not AI: listing status, application status, deadlines arithmetic, filtering, sorting, access control, de-duplication, sync.

The model today is **Gemma 4 through the Gemini API**. This is expected to be reconsidered. Because every call goes through one wrapper that returns validated JSON or text, changing the model or provider is a change to that one file and its settings.

AI output is never trusted blindly: code validates the shape, checks quotes against the source text, and checks dates.

## 9. Open Source Usage
- **JobSpy (python-jobspy)** fetches the raw listings. It is installed only on admin laptops, not on the server. Credited in the README.
- Other libraries are kept minimal (see `IMPLEMENTATION_PLAN.md`).
- The repo's licence and whether it stays public are open (A8).

## 10. Acceptance Criteria
- AC1 Sign-in: an admin creates an account and the generated password works; a wrong username or password is refused with a message that does not say which was wrong; repeated failures are locked out; a new password from an admin stops the old one working; no page except sign-in and health loads without signing in.
- AC1a Home: a user lands on Home and an admin on Admin home; every item shown matches the Tracker, CVs and Job Board; a brand-new user sees the getting-started list.
- AC2 Isolation: user A cannot open user B's Tracker, CV or Profile by any URL; a user cannot open any Admin page.
- AC3 Job Board: every synced job appears for both users with deadline and listing status; filters and sorting work; closed jobs are hidden by default.
- AC4 Shortlist: shortlisting adds one Tracker entry marked "Added by you"; shortlisting again adds nothing.
- AC5 Add by link: a link to a readable advert creates an entry in the Job Board format; a link already on the Board creates no duplicate; an unreadable link falls back to the manual form with nothing saved.
- AC6 Add by hand: an entry with only title and company saves and shows "Unknown" deadline.
- AC7 Status: a user can set every application status; the change and its date are kept; Apply opens the advert and does not change status by itself.
- AC8 Deadlines: a stated deadline shows on Board and Tracker with its quote; a hand-set deadline overrides it and survives the next sync.
- AC9 Calendar: every Tracker entry with a deadline appears on the right day; changing the deadline moves it; entries without one appear under "No deadline set".
- AC10 Core CV: upload, view, download, replace and delete all work; the CV survives a restart of the app.
- AC11 Tailored CV: generated from the core CV and the job; editable; downloadable as PDF; blocked with a reason when there is no core CV. Hand-check 5: none contains an employer, title, qualification or date absent from the core CV.
- AC12 Review: a CV sent for review appears in the admin queue; approve and request-changes both reach the user; editing after approval returns it to Draft.
- AC13 Assign: an admin assigns a job to a user; it appears in that user's Tracker as "Assigned by <name>" and not in the other user's.
- AC14 Fetch and sync: on a laptop, fetch and tag produces a summary; Sync adds new jobs and updates existing ones; no Tracker entry, status, hand-set deadline or CV changes; the sync is recorded; the Board shows the new "Last updated" date.
- AC15 Tagging quality: every sponsorship and deadline quote appears word-for-word in its advert; hand-check 10 jobs, at least 8 correct.
- AC16 Resilience: with the AI key removed, the Board, Tracker, Calendar and Profile all work; only tailoring and AI tagging show a friendly "unavailable" message, and add-by-link falls back to keyword tags or the manual form.
- AC17 Privacy: no CV text, token or key is in the repo or the logs.
- AC18 Tests: automated tests cover access control (AC1, AC2), listing status rules, sync safety (AC14) and the quote check (AC15), and pass.

## 11. Constraints
- Runs locally with one command and deploys from GitHub to one web service plus one Postgres database.
- Minimal dependencies; no microservices, queues or separate JavaScript frontend.
- Small scale by design: four people, hundreds to low thousands of jobs. Do not build for scale that does not exist.
- Secrets stay out of the repo.
- Respect job-site terms: modest volume, personal use.

## 12. Risks

| Risk | Mitigation |
|---|---|
| A user sees another user's CV or Tracker | Access checked on the server on every request; automated tests for it (AC2) |
| Tailored CV invents experience | Prompt forbids it; core CV shown side by side; optional admin review; hand-check (AC11) |
| Sync overwrites a user's work | Sync only writes the shared jobs table; tested (AC14) |
| Job sites block or rate-limit fetching | Fetch runs on a laptop, modest volume, resumable |
| Add-by-link cannot read the page (blocked, login wall, heavy JavaScript) | Clear message and fall back to the manual form |
| Deadline missing or mis-read | Quote and date checks; "Unknown" shown honestly; user can set it by hand |
| AI returns bad JSON or is down | Validate, retry once, fall back to keyword tags; nothing crashes |
| Database lost | Hosted Postgres with backups; no temporary database in production |
| Weak, shared or leaked password | Passwords are generated by the app, stored only as hashes; failed attempts are locked out; an admin can issue a new one at once |
| Both admins lose their passwords | The one-off setup command can create or rescue an admin account |
| Daily fetch is forgotten | Board shows "Last updated"; a warning when it is more than 3 days old |
| AI provider changes | One wrapper file; prompts kept provider-neutral |

## 13. Assumptions to Confirm
These were not stated outright. Each is written into the docs as shown; change the answer and the docs follow.

| # | Assumption | Why it matters |
|---|---|---|
| A1 | Skill Gaps, This Week, dream jobs and match-score ranking are removed, because they are not in the new list of sections. The Job Board is a filterable list, not ranked against the CV. | These were CareerOS's core; removing them deletes `ranking.py`, `gaps.py` and most of `profile.py` |
| A2 | The new tagging criteria are not yet defined. FR24 keeps the CareerOS tags and adds deadline. | Decides what the Board shows and filters on |
| A3 | What to fetch (job titles, locations) is set by admins and is one shared set for the family, not per user. | The two users may want different kinds of job |
| A4 | Admins have no Tracker of their own; they only view users'. | Decides whether admins get the user sections |
| A5 | Admins can view everything, assign jobs and review CVs, but do not change a user's application status or edit their CV. | Decides how much "view as user" can do |
| A6 | Review is optional; a user can apply without approval. | Could instead be required before Apply |
| A7 | Tailored CVs are edited as text and downloaded as PDF. No Word file. | Some employers ask for .docx |
| A8 | The repo becomes private and the MIT licence is dropped, since this is a family tool holding personal data. | Currently public and MIT |
| A10 | With the People page removed, accounts are created and managed from Admin home. | Admins still need somewhere to do it |
| A11 | A person can change their own password on Profile; a forgotten one is replaced by an admin. | Could instead be admin-only |
| A9 | Sync works by the admin's laptop copy of the app writing to the same database as the live site. | See `IMPLEMENTATION_PLAN.md` section 3 for the alternative |

## 14. What Changed from CareerOS

| Area | CareerOS | Job Buddy |
|---|---|---|
| Purpose | Public hackathon demo for any graduate | Private daily tool for one family |
| Access | Anonymous cookie, no sign-up | Username and password; accounts created by an admin |
| Landing pages | Public marketing page | Home for users and Admin home for admins, shown after sign-in |
| Roles | None | User and Admin |
| Jobs | Ranked against the CV with a match score | Job Board list with deadline, status, filters, shortlist |
| Tracker | None | New |
| Tailored CVs and review | None | New |
| Calendar | Static placeholder | Real, driven by Tracker deadlines |
| Profile | Extracted skills; raw CV never stored | Core CV stored as the base for tailoring |
| Admin | None | New: view users, assign, review, fetch, sync |
| Job refresh | Script, then commit a file to the repo | Admin panel: fetch and tag locally, then sync |
| Data lifetime | Deleted after 7 days | Kept until the person deletes it |
| Removed | | Project Ideas, Network, Skill Gaps, This Week, dream jobs, landing page |
