# Job Buddy Design Specification

Companion to `spec.md` (what we build) and `IMPLEMENTATION_PLAN.md` (how). This file covers UX, information hierarchy, navigation and interaction. No code.

## Product Summary
Job Buddy is a private workspace where a family finds, tracks and applies for jobs together. Users work through their own applications; admins keep the job list fresh and help.

**Key design principle:** every screen answers *"What needs doing next, and by when?"* Deadline and status are the two most important facts on any job, and are visible wherever a job appears.

## Who Uses It
- **Users (2):** job seekers. They use it most days, often on a phone, and are not technical.
- **Admins (2):** family members helping. They use it on a laptop, once a day for the job sync and now and then to assign jobs or review a CV.

Everyone knows each other. The tone is plain and friendly, with first names ("Assigned by Dad"), never corporate.

## Navigation
Left sidebar on desktop, bottom bar on phones.

**User**
1. Home (landing page after sign-in)
2. Job Board
3. Tracker
4. Calendar
5. Profile

**Admin**
1. Admin home (landing page after sign-in)
2. Job Board
3. Review CVs (with a count of waiting CVs)
4. Jobs sync

The sidebar footer shows the signed-in person's name and initials, their role, and Sign out.

## Visual Direction
- Keep the existing design system in `app/static/app.css` (type, colour, spacing, cards, badges, sidebar). Rename, do not redesign.
- Clean and calm; closer to a productivity tool than a recruitment portal.
- Information-dense but not cluttered. A job is one card or one table row, scannable in a second.
- Works well on a phone. The Tracker and Calendar are the screens most used on one.
- Avoid: heavy gradients, gamification, chatbot UI, walls of text.

### Status and deadline language (used identically everywhere)
**Deadline chip**
| State | Text | Tone |
|---|---|---|
| More than 7 days away | "12 Nov" | neutral |
| 7 days or fewer | "Closes in 3 days" | warning |
| Today | "Closes today" | urgent |
| Passed | "Closed 2 Nov" | muted |
| Not known | "No deadline" | muted, dashed outline |

**Listing status badge:** Open, Closing soon, Closed, Possibly closed.

**Application status badge:** Shortlisted, Applied, Interviewing, Offer, Rejected, Withdrawn. One colour each, the same on Board, Tracker, Calendar and Admin. Never rely on colour alone; the word is always shown.

**CV status badge:** No CV, Draft, In review, Approved, Changes requested.

**"Added by" line:** "Added by you · 3 Oct", "Assigned by Dad · 3 Oct". Shown on every Tracker entry.

---

## 1. Sign-in
The front door, and the only page anyone sees signed out. It should feel welcoming, not like a gate.

- Product name and one warm line saying what Job Buddy is for.
- Username and password fields, with a show/hide control on the password, and a **Sign in** button.
- "Forgotten your password? Ask Chandan or Dad for a new one." There is no sign-up link and no reset-by-email.
- **Wrong details:** one plain line, "That username or password isn't right," which never says which one.
- **Too many tries:** "Too many attempts. Try again in a few minutes."

After signing in, a user goes to Home and an admin to Admin home. A signed-out visit to any other page comes here first and then returns to that page.

## 2. Home (user landing page)
The first screen after sign-in. It is a launch pad, not a report: it says what needs doing today, most urgent first, and every block ends in one action.

- **Greeting:** "Good morning, <first name>", with one sentence that sums up the day, for example "2 deadlines this week and 1 new job from Dad."
- **Do next (up to 5):** the most pressing things, in order:
  1. deadlines within 7 days that are not yet applied to,
  2. jobs an admin has newly assigned,
  3. CV reviews that have come back (approved, or changes requested),
  4. "Did you apply?" prompts still unanswered.
  Each row shows what it is, the deadline chip, and one button (Apply, Open CV, View job).
- **Your applications:** a row of tiles, one per application status, with the count. Each tile opens the Tracker filtered to that status.
- **New on the Job Board:** "14 new jobs since you last looked", the three closing soonest, and a link to the Board.
- **This week:** a strip of the next 7 days with a dot on each day that has a deadline; it opens the Calendar.

**Nothing urgent:** "You're all caught up," with the applications tiles and the new jobs still shown.

**Brand-new user:** the blocks above are replaced by a three-step getting-started list with ticks: upload your CV, browse the Job Board, shortlist your first job.

## 3. Job Board
The shared list of live jobs. The page header shows the total and "Last updated 5 Oct by Chandan"; if the last sync is more than 3 days old, that line becomes a warning.

**Filter bar:** search box (title or company), listing status, location, work mode, sponsorship, seniority. Sort: deadline (default), newest, company. Closed jobs are off by default.

**Job card or row:**
- Title, company, location, salary if known
- **Deadline chip** and **listing status badge**
- Tags: work mode, sponsorship (with the quoted phrase on tap or hover), seniority
- "First seen 2 Oct"
- Right side: **Shortlist** button. Once shortlisted it becomes "In your Tracker" with the application status badge, linking to the entry.

Opening a job shows the full description, all tags with their evidence quotes, and the link to the original advert.

**Empty state:** "No jobs yet. An admin needs to run the first sync."

## 4. Tracker
The user's working list.

**Top:** counts by application status as tabs (All, Shortlisted, Applied, Interviewing, Offer, Closed out). An **Add job** button with two choices: "Paste a link" and "Enter by hand".

**Entry:**
- Title, company, location, salary
- **Deadline chip** (tap to edit) and **application status** (a dropdown, changed in one tap)
- CV status badge
- "Added by" line
- Notes (private, one or two lines shown, expandable)
- **Actions:** `Create tailored CV` (or `Open tailored CV`), `Apply`, and a menu with Edit and Remove

Default order: soonest deadline first, entries with no deadline last. A `Shortlisted` entry whose deadline has passed is flagged "Deadline passed" and sinks below live ones.

**Apply:** opens the advert in a new tab. When the user comes back, the entry shows a small prompt, "Did you apply?" with **Mark as Applied** and **Not yet**. Status never changes without the user saying so.

**Add by link:** a single URL box and an "Add" button, with a loading state ("Reading the advert…"). On success the new entry appears filled in, in the same format as a Board job, for the user to check. If the link is already on the Board, it is simply shortlisted. If the page cannot be read: "We couldn't read that page. Add the details by hand?" and the manual form opens with the URL filled in.

**Add by hand:** title and company required; URL, location, salary, deadline, work mode, sponsorship, description, notes optional. A hint says a description is needed to create a tailored CV.

**Empty state:** "Nothing here yet. Shortlist jobs from the Job Board, or add one with a link."

## 5. Tailored CV
Opened from a Tracker entry.

- **Header:** the job's title, company and deadline chip; the CV status badge.
- **Two panes** (stacked on a phone): the core CV on the left, read-only; the tailored CV on the right, editable text.
- **Actions:** Save, Regenerate (with a confirmation, since it replaces edits), Download PDF, Send for review.
- A short standing note above the editor: "Check every line. This should only contain what is in your core CV."
- While generating: "Writing your tailored CV…" overlay.
- After review: the admin's comment appears at the top with the outcome. Editing an approved CV shows "This will need review again."

**Blocked states**, each with one clear next step: no core CV ("Upload your CV on Profile first"), no job description ("Add the job description to this entry first"), AI unavailable ("Try again in a few minutes").

## 6. Calendar
Deadlines from the Tracker, nothing else.

- **Upcoming list first** (the priority view, and the default on a phone): grouped as This week, Next week, Later. Each item shows the date, title, company and application status badge.
- **Month view:** a dot or short label per deadline on its day; tap a day to list its jobs.
- Deadlines still to act on (Shortlisted) are solid; ones already applied to are outlined, so the eye goes to what is still to do.
- A **"No deadline set"** section lists entries with no deadline, each with "Set deadline".
- Every item links to its Tracker entry.

**Empty state:** "No deadlines yet. They appear here when a job in your Tracker has one."

## 7. Profile
- Name, username and role (read-only), and a **Change password** control.
- **Core CV card:** file name, upload date, View, Download, Replace, Delete. If there is none, a prominent upload control (PDF, or paste text).
- A plain privacy note: the CV is stored so it can be tailored, it is sent to the AI provider when a tailored CV is created, admins can see it, and it can be deleted at any time.
- Replacing the core CV does not change tailored CVs already made; a note says so.

## 8. Admin

### Admin home (admin landing page)
The first screen after an admin signs in. It answers "does anyone need me today?"

- **Greeting:** "Good morning, <first name>", with one sentence: "2 CVs to review and the Job Board is 1 day old."
- **Needs you:** only shown when there is something:
  - CVs waiting for review, oldest first, each with the user, the job and its deadline chip, and a Review button.
  - Job Board freshness: "Last updated 5 Oct by Chandan", with a **Fetch today's jobs** button. It becomes a warning when more than 3 days old.
  - Deadlines within 3 days that a user has not applied to yet.
- **One card per user:** name, initials, last sign-in, counts by application status, next three deadlines, CVs waiting for review. **Open** shows that user's view. A menu on the card holds **New password** and **Remove account**.
- **Add a person:** a small form (name, username, role). On saving, the generated password is shown once in a highlighted box with a Copy button and the line "Write this down or send it now. It won't be shown again." New password works the same way.
- **Recent activity:** the last few events across the family (synced, assigned, sent for review, applied).

**Remove account** needs a strong confirmation that names the person and says their Tracker and CVs will be deleted.

### Viewing a user
The user's own Tracker, Calendar, Profile and Board state, with a persistent coloured banner across the top: **"Viewing <user's name>'s Tracker"** and "Back to Admin". The banner cannot be dismissed, so an admin never mistakes whose data they are looking at. Controls the admin cannot use are hidden, not greyed.

**Assign job:** an "Assign to…" control on every Board job, and an "Assign a job" button on the user's Tracker (from the Board, by link, or by hand). The result appears in that user's Tracker as "Assigned by <name>".

### Review CVs
A queue of CVs In review, oldest first: user, job, deadline chip, date sent. Opening one shows three panes: job description, core CV, tailored CV. Actions: **Approve**, or **Request changes** with a required comment.

### Jobs sync
Three steps on one page.
1. **Search settings:** job titles or keywords, locations, results per search. Saved for next time.
2. **Fetch and tag:** a button with live progress ("Fetched 140 · Tagged 96 of 140"), Stop and Resume. On the live site this step is replaced by a note: "Fetching only works when Job Buddy is running on your laptop," with a link to the steps.
3. **Review and sync:** a summary (new, updated, unchanged, failed to tag) and a sample of new jobs. A **Sync to Job Board** button, then a confirmation with the counts.

Below: sync history (date, who, counts).

---

## Cross-Section Connections
- Board job → Shortlist → Tracker entry
- Tracker entry → Tailored CV → Review queue → back to the entry with the outcome
- Tracker deadline → Calendar item → back to the entry
- Admin home → a user's Tracker → assign a job → that user's Tracker → that user's Home ("1 new job from Dad")
- Home → any item → the Tracker entry, CV or Board job it is about

## Production Basics (UI)
- **Signed-in only:** every page needs sign-in. A signed-out visit to any page goes to Sign-in and returns to that page afterwards.
- **Loading states:** the slow steps (reading a link, creating a tailored CV, fetch and tag) show progress, never a frozen page.
- **Empty states:** every page says what to do next.
- **Friendly errors:** a short message and a retry button, never a raw error page. If AI is unavailable, say so; everything that does not need it keeps working.
- **Confirmations** for anything destructive: remove an entry, regenerate a CV, delete a CV, remove a person.
- **Dates** are shown in UK format and local time; a deadline is a day, not a time.
- **Phone:** all user screens work one-handed; tables become cards.

## Design Order
1. Rename and re-skin: product name, sidebar, sign-in page, the shared status and deadline chips.
2. Home, Job Board and Tracker.
3. Tailored CV and Profile.
4. Calendar.
5. Admin: Admin home with accounts, view as user, Review CVs, Jobs sync.
