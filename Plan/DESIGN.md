# CareerOS Design Specification

Companion to `spec.md` (what we build) and `IMPLEMENTATION_PLAN.md` (how). This file covers UX, information hierarchy, navigation and interaction. No code.

Each section ends with an **MVP note** saying what is real, seeded or cut for the 4-hour build. Anything not listed there is built as described.

## Product Summary
CareerOS is a career development platform for early-career software graduates. It helps users understand what to do to become stronger candidates for the jobs they want. It should feel like a career operating system, not a job board.

**Key design principle:** every screen answers *"What should I do next to become a stronger candidate for the jobs I want?"*

## Primary User
An early-career software graduate or junior developer who is looking for a first or second software role, is unsure which skills to prioritise, needs help choosing projects, wants help with applications, events and networking, and wants a clear weekly plan instead of generic advice.

## Main Navigation
Left sidebar, in this order:
1. This Week (default landing page after onboarding)
2. Jobs
3. Skill Gaps
4. Project Ideas
5. Calendar
6. Network

## Visual Direction
- Modern, clean, intelligent, encouraging without being childish; closer to a productivity/SaaS tool than HR software.
- Information-dense but not cluttered. Strong typography, clear hierarchy, cards where useful.
- Subtle indicators for progress, urgency and match quality (e.g. a thin bar or ring for match %, a muted colour for urgency).
- Desktop-first, responsive.
- Avoid: recruitment-portal look, heavy gradients, gamification, chatbot UI, walls of text.

## Cross-Feature Connections
The six sections must feel like one product. Links to design in:
- Skill gap → recommended project
- Job → missing skills
- Job → existing network contacts at that company ("You know 2 people at NatWest")
- Calendar event → networking opportunity
- Project → skills it improves
- Everything → This Week to-do

---

## 1. Onboarding
Simple, guided, 4 steps with a progress indicator.

**Step 1: Upload CV.** Upload control; show file name, success state, "Replace file". Do not show extracted data yet. Show a short privacy note: the CV is sent to Google's Gemini API to be analysed, the CV itself is not stored, and the profile built from it is kept for up to 7 days and can be deleted any time.

**Step 2: Dream jobs.** Add up to 5 (show "2 of 5"). Input as pasted job description, job URL, or manual title/company. Each added job appears as a card, e.g. *Graduate Software Engineer · NatWest · Backend / Cloud*, with remove.

**Step 3: Preferences (optional).** Preferred locations, minimum salary, sponsorship required, preferred industries, preferred technologies. Hard requirements are visibly marked (e.g. "Sponsorship required: Yes" with a "must-have" badge); soft preferences look lighter.

**Step 4: Career profile summary.** Shows what the platform inferred: target roles, common technologies, preferred industries. User can confirm or edit before continuing. Primary button leads to This Week.

> **MVP note:** CV upload as PDF or pasted text. Job URLs are accepted and stored, but we do **not** fetch the page (too fragile); the user pastes the description or enters title/company. Profile summary is real (Gemma + code), editable via simple chips.

## 2. This Week
The main dashboard. It prioritises actions; it must not look like generic analytics.

**Priority Actions (3-5).** Each shows category, reason, deadline if relevant, completion checkbox. Examples: apply to a top job, finish a project milestone, follow up with a contact, register for a hackathon.

**Best Job Matches (3).** Title, company, match %, location, constraint status, short "strong fit because" list.

**Biggest Skill Gaps (3-5).** Skill name plus "Appears in 41% of relevant jobs". Clickable, goes to Skill Gaps.

**Recommended Project (1, prominent).** Title, skills, reason ("Addresses 3 of your biggest skill gaps").

> **MVP note:** The landing page. Priority Actions are a static placeholder (sample content, labelled "Preview"). Best Job Matches, Biggest Skill Gaps and Recommended Project show real data from the visitor's analysis, since that data already exists for the other pages.

## 3. Jobs
Feels different from a job board: ranking and explanation, not browsing. The page says *"These are the jobs most worth your attention."* Show a limited ranked list, not hundreds.

**Job card:** title, company, location, salary if available, closing date if available, **sponsorship status with the quoted evidence phrase**, match score, why it matches (strong matches), important gaps (missing skills), and "You know N people here" if applicable.

**Sort/filter:** match score, newest, deadline, location, sponsorship.

> **MVP note:** Sort by match score (default) and filter by sponsorship and location. Newest/deadline sorting only if the data has those fields. Closing date and salary show only when present. The evidence quote is the key differentiator and is never cut.

## 4. Skill Gaps
Answers *"What is stopping me from getting the jobs I want?"* Based on dream jobs plus relevant live jobs.

**Groups:**
- **Priority Gaps:** frequent, important, missing from the CV.
- **Developing:** some evidence, could be stronger.
- **Strong:** already demonstrated.

**Skill row/card** (e.g. AWS, High priority): required by "18 of 43 relevant jobs", current evidence ("None detected"), recommended action ("Complete AWS deployment project"), links to the jobs requiring it and the projects that demonstrate it.

> **MVP note:** Priority Gaps and Strong are real. "Developing" is a stretch, only if Gemma's CV evidence tagging makes it cheap; otherwise hide it. Gemma writes the "why this matters" sentence.

## 5. Project Ideas
Never generic; every project ties to career goals.

**Project card:** title, short description, "Why this project" (linked to target jobs), skills demonstrated, gaps addressed, estimated effort (e.g. 6-10 hours), difficulty, suggested stack, milestones, career value ("Addresses 3 priority gaps"). Status control: Interested / Started / Completed.

> **MVP note:** 1-3 real projects from Gemma. Status is stored locally. Completing a project does not yet change gap scores (stretch).

## 6. Calendar (designed after the five main screens)
Career-focused: grad scheme and application deadlines, interviews, coding assessments, hackathons, meetups, career fairs, project milestones, networking follow-ups. Clear colour per category. Include both a calendar view and an **upcoming events list** (the list is the priority view).

> **MVP note:** UI placeholder: static sample events, upcoming-events list first, labelled "Preview". No add, no discovery.

## 7. Network (designed after the five main screens)
A lightweight relationship CRM. **Contact:** name, role, company, where met, date met, topics discussed, last interaction, next follow-up, relationship status, suggested next action. Contacts link to companies and jobs.

> **MVP note:** UI placeholder: static sample contacts, labelled "Preview". The "You know N people" line on job cards appears only if cheap (match against the sample contacts). No add/edit, no integrations.

---

## Production Basics (UI)
The app is public, so design these in from the start:
- **No sign-up:** visitors go straight to onboarding. No login screens.
- **Delete my data:** a clear button in the sidebar footer or settings, with a confirmation.
- **Privacy note:** shown in onboarding Step 1 (see above).
- **Loading states:** two steps are slow (building the profile, and generating gap explanations + projects); each shows an "Analysing…" overlay when the form is submitted, not a frozen page.
- **Empty states:** every page says what to do next when there is no data yet.
- **Friendly errors:** a short message and a retry button, never a raw error page. If the AI is unavailable, say so and still show the stored jobs, a basic profile, the ranking and the gaps (only the narrative text and projects are missing).
- **Blocked jobs:** a job that fails a must-have (e.g. no sponsorship) stays visible at the bottom of the list, greyed, with the reason and quote.
- **Job pool note:** the Jobs page says the listings are UK software graduate roles collected ahead of time.
- **"Preview" label:** This Week actions, Calendar and Network show a small "Preview" tag so sample content is not mistaken for the visitor's data.
- **No "Refresh jobs" button** for visitors; jobs are loaded ahead of time.

## Design Scope and Order
1. Establish the design system (type, colour, spacing, card styles, badges, sidebar) and navigation.
2. Design the five main screens: Onboarding, This Week, Jobs, Skill Gaps, Project Ideas.
3. Then Calendar and Network.

## Consistency with other files
`spec.md`, `IMPLEMENTATION_PLAN.md` and `CLAUDE.md` have been updated to match: This Week is the landing page, Calendar and Network are placeholders, and the app is deployed publicly with anonymous sessions.
