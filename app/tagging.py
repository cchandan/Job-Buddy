"""Job advert -> tags. Used when jobs are fetched (on an admin's laptop) and when a job is added by link.

AI reads the fine print; code then checks everything it said:
  * every evidence quote must appear word-for-word in the description, or the tag becomes "unclear"
  * a keyword safety net overrides "unclear" when the text plainly says "no sponsorship" etc.
  * a deadline must have a quote that is in the advert and must be a real, plausible date, or it is "unknown"
  * every skill name goes through skills.canonical
If AI is unavailable, a keyword-only tagger produces the same fields (tag_source = "keywords").
"""
import re
from datetime import date
from typing import Literal

from pydantic import BaseModel

from . import ai, deadlines, skills

MAX_DESC_FOR_GEMMA = 6000

SPONSORSHIP = ("sponsors", "no_sponsorship", "unclear")
WORK_MODES = ("remote", "hybrid", "onsite", "unclear")


class JobTags(BaseModel):
    required_skills: list[str] = []
    optional_skills: list[str] = []
    sponsorship: Literal["sponsors", "no_sponsorship", "unclear"] = "unclear"
    sponsorship_quote: str = ""
    work_mode: Literal["remote", "hybrid", "onsite", "unclear"] = "unclear"
    work_mode_quote: str = ""
    seniority: Literal["graduate", "junior", "mid", "senior"] = "junior"
    industry: str = ""
    deadline: str = ""
    deadline_quote: str = ""


# ---------- quote checking ----------

def _squash(text):
    return re.sub(r"\s+", " ", text or "").strip()


def quote_in_text(quote, text):
    """True if the quote appears word-for-word in the text (whitespace differences ignored)."""
    q = _squash(quote)
    return bool(q) and q in _squash(text)


# ---------- keyword safety net ----------

NO_SPONSOR = [
    # a negative word within a few words of "sponsor": "unable to offer Skilled Worker Sponsorship",
    # "is not providing sponsorship", "will not pursue visa sponsorship", "no sponsorship"
    r"\b(?:no|not|unable|cannot|can't|won't|never|without)\b(?:\W+\w+){0,6}?\W+(?:visa\W+|skilled worker\W+)?sponsor",
    r"(?:must|should) (?:already )?(?:have|hold) (?:the )?(?:existing |full |unrestricted )?right to work in the uk",
    r"uk residents only",
]
SPONSOR = [
    r"(?:visa|skilled worker|work permit)\W+sponsorship (?:is |may be |will be )?(?:available|offered|provided|possible|considered)",
    r"sponsorship (?:is |may be |will be )?(?:available|offered|provided|possible|considered)",
    r"(?:we|will|can|able to|happy to|may)\s+(?:also\s+)?(?:offer|provide|consider|support)\s+(?:visa\s+)?sponsorship",
    r"(?:will|can|may|able to)\s+sponsor\s+(?:a\s+)?(?:visa|your|skilled)",
    r"skilled worker visa sponsorship",
]
WORK_PATTERNS = [
    ("remote", [r"fully remote", r"100% remote", r"remote[- ]first", r"work (?:fully )?from home", r"remote position"]),
    ("hybrid", [r"hybrid"]),
    ("onsite", [r"on[- ]?site", r"in[- ]the[- ]office", r"office[- ]based", r"days? (?:a|per) week in (?:the|our) office"]),
]
INDUSTRIES = [
    ("Fintech & Banking", r"\b(?:bank|banking|fintech|payments?|insurance|financial|finance|trading)\b"),
    ("Healthcare", r"\b(?:healthcare|health care|nhs|medical|clinical|pharma)\b"),
    ("Defence & Aerospace", r"\b(?:defence|defense|aerospace|maritime|military)\b"),
    ("Retail & E-commerce", r"\b(?:retail|e-?commerce|marketplace)\b"),
    ("Consulting", r"\b(?:consultancy|consulting)\b"),
    ("Education", r"\b(?:education|university|edtech)\b"),
    ("Gaming", r"\b(?:gaming|games?)\b"),
    ("Public Sector", r"\b(?:government|public sector|council)\b"),
    ("Energy & Utilities", r"\b(?:energy|utilities|renewable)\b"),
    ("Telecoms", r"\b(?:telecoms?|telecommunications)\b"),
]


def _sentence_around(text, start, end, cap=220):
    """A verbatim snippet of `text` containing the match: its sentence, trimmed to `cap` characters."""
    left = max(text.rfind(c, 0, start) for c in ".!?\n") + 1
    right_candidates = [i for i in (text.find(c, end) for c in ".!?\n") if i != -1]
    right = (min(right_candidates) + 1) if right_candidates else len(text)
    if right - left > cap:
        left = max(left, start - (cap - (end - start)) // 2)
        right = min(right, left + cap)
    return text[left:right].strip(" \t*-#>•")


def keyword_sponsorship(description):
    """(label, quote) from plain phrase matching. 'no sponsorship' wins when both appear."""
    for label, patterns in (("no_sponsorship", NO_SPONSOR), ("sponsors", SPONSOR)):
        for p in patterns:
            m = re.search(p, description, re.IGNORECASE)
            if m:
                return label, _sentence_around(description, m.start(), m.end())
    return "unclear", ""


def keyword_work_mode(description):
    for mode, patterns in WORK_PATTERNS:
        for p in patterns:
            m = re.search(p, description, re.IGNORECASE)
            if m:
                return mode, _sentence_around(description, m.start(), m.end())
    return "unclear", ""


def keyword_seniority(title, description):
    t = title.lower()
    if re.search(r"\b(?:senior|lead|principal|staff|head of|manager)\b", t):
        return "senior"
    if re.search(r"\b(?:graduate|intern|internship|placement|apprentice|apprenticeship|trainee|entry[- ]level|year in industry)\b", t):
        return "graduate"
    if re.search(r"\b(?:junior|jr|associate)\b", t):
        return "junior"
    if re.search(r"\b(?:mid[- ]level|intermediate)\b", t):
        return "mid"
    m = re.search(r"(\d+)\+?\s*(?:years|yrs)", description, re.IGNORECASE)
    if m:
        years = int(m.group(1))
        return "senior" if years >= 5 else "mid" if years >= 3 else "junior"
    return "junior"


def keyword_industry(title, company, description):
    blob = f"{title} {company} {description[:1500]}"
    for name, pat in INDUSTRIES:
        if re.search(pat, blob, re.IGNORECASE):
            return name
    return "Technology"


def keyword_skills(description):
    """(required, optional): skills mentioned after a 'nice to have' style heading count as optional."""
    marker = re.search(r"nice[- ]to[- ]have|desirable|bonus points|preferred (?:skills|qualifications|experience)|a plus|"
                       r"would be an advantage|beneficial",
                       description, re.IGNORECASE)
    cut = marker.start() if marker else len(description)
    required = [n for n, _ in skills.find_skills(description[:cut])]
    optional = [n for n, _ in skills.find_skills(description[cut:]) if n not in required]
    if not required:  # everything sat under a "nice to have" heading: treat it as the main list
        required, optional = optional, []
    return required, optional


# ---------- salary (plain code) ----------

_PER_YEAR = {"yearly": 1, "annual": 1, "monthly": 12, "weekly": 52, "daily": 260, "hourly": 1950}


def salary_range(job):
    """(min, max) per year in pounds, from JobSpy's fields or a '£25,000 - £30,000' in the text."""
    lo, hi = job.get("salary_min"), job.get("salary_max")
    if lo or hi:
        factor = _PER_YEAR.get((job.get("salary_interval") or "yearly").lower(), 1)
        lo, hi = (lo or hi) * factor, (hi or lo) * factor
    else:
        lo = hi = None
        text = job.get("description") or ""
        m = re.search(r"£\s?(\d{2,3}(?:,\d{3})|\d{2,3}\s?k)\s*(?:-|–|to)\s*£?\s?(\d{2,3}(?:,\d{3})|\d{2,3}\s?k)", text, re.I)
        if m:
            lo, hi = (_money(m.group(1)), _money(m.group(2)))
        else:
            m = re.search(r"£\s?(\d{2,3}(?:,\d{3})|\d{2,3}\s?k)", text, re.I)
            if m:
                lo = hi = _money(m.group(1))
    if lo and hi and 10000 <= lo <= 400000 and 10000 <= hi <= 400000:
        return int(min(lo, hi)), int(max(lo, hi))
    return None, None


def _money(s):
    s = s.lower().replace(",", "").replace(" ", "")
    return float(s[:-1]) * 1000 if s.endswith("k") else float(s)


# ---------- deadline ----------

_DEADLINE_RE = re.compile(
    r"(?:closing date|closes on|closing on|deadline|apply by|apply before|applications? (?:close|closes|must be received by|by)|"
    r"application deadline|close date)[^.\n]{0,40}?(" + deadlines.DATE_RE + ")", re.IGNORECASE)


def keyword_deadline(description, today):
    """(date, quote) when the advert plainly states a closing date, else (None, "")."""
    text = re.sub(r"[*_]{1,3}", "", description or "")  # adverts often arrive as markdown: **23****rd** October
    for m in _DEADLINE_RE.finditer(text):
        found = deadlines.parse_date(m.group(1), today)
        if found:
            return found, m.group(0)
    return None, ""


# ---------- the tagger ----------

PROMPT = """You are tagging a job advert for a job-tracking tool. Read the advert and fill in the JSON. Today is {today}.

Rules:
- required_skills / optional_skills: technical skills and tools only, short names (e.g. "Python", "React", "AWS").
- sponsorship: "sponsors" if the advert says visa sponsorship is offered or considered; "no_sponsorship" if it says it
  is not offered or that candidates must already have the right to work in the UK; otherwise "unclear".
- sponsorship_quote: the EXACT words from the advert that justify it, copied character for character. Empty if unclear.
- work_mode: remote, hybrid, onsite or unclear. work_mode_quote: the exact words, only if the advert states it.
- seniority: graduate, junior, mid or senior. industry: a short phrase such as "Fintech & Banking".
- deadline: the closing date for applications as YYYY-MM-DD, ONLY if the advert states one. Empty if it does not.
  Never guess, and never use the date the advert was posted.
- deadline_quote: the EXACT words from the advert that state the closing date. Empty if there is none.

Job title: {title}
Company: {company}
Advert:
\"\"\"
{description}
\"\"\"
"""


def finalize(job, tags, source, today=None):
    """Check and clean tags (from AI or keywords) and return the fields stored on the job."""
    desc = job.get("description") or ""
    today = today or date.today()

    deadline, d_quote = deadlines.parse_date(tags.deadline, today), tags.deadline_quote
    if deadline is None or not quote_in_text(d_quote, desc):
        deadline, d_quote = keyword_deadline(desc, today)  # made-up quote or impossible date: never shown

    sponsorship, s_quote = tags.sponsorship, tags.sponsorship_quote
    if sponsorship != "unclear" and not quote_in_text(s_quote, desc):
        sponsorship, s_quote = "unclear", ""  # made-up or missing quote: never shown
    if sponsorship == "unclear":
        sponsorship, s_quote = keyword_sponsorship(desc)  # safety net

    work_mode, w_quote = tags.work_mode, tags.work_mode_quote
    if w_quote and not quote_in_text(w_quote, desc):
        work_mode, w_quote = "unclear", ""
    if work_mode == "unclear":
        work_mode, w_quote = keyword_work_mode(desc)

    required = skills.canonical_list(tags.required_skills)
    optional = [s for s in skills.canonical_list(tags.optional_skills) if s not in required]
    lo, hi = salary_range(job)
    return {
        "required_skills": required, "optional_skills": optional,
        "sponsorship": sponsorship, "sponsorship_quote": _squash(s_quote),
        "work_mode": work_mode, "work_mode_quote": _squash(w_quote),
        "seniority": tags.seniority, "industry": (tags.industry or "Technology").strip()[:80],
        "salary_min": lo, "salary_max": hi, "tag_source": source,
        "deadline": deadline.isoformat() if deadline else None, "deadline_quote": _squash(d_quote) if deadline else "",
    }


def keyword_tags(job):
    desc, title = job.get("description") or "", job.get("title") or ""
    required, optional = keyword_skills(desc)
    sponsorship, s_quote = keyword_sponsorship(desc)
    work_mode, w_quote = keyword_work_mode(desc)
    return JobTags(
        required_skills=required, optional_skills=optional, sponsorship=sponsorship, sponsorship_quote=s_quote,
        work_mode=work_mode, work_mode_quote=w_quote, seniority=keyword_seniority(title, desc),
        industry=keyword_industry(title, job.get("company") or "", desc))


def tag_job(job, use_ai=True, count=False, today=None):
    """Tag one job. Raises AIUnavailable when AI cannot be reached (the caller decides what to do)."""
    today = today or date.today()
    if use_ai:
        prompt = PROMPT.format(today=today.isoformat(), title=job.get("title", ""), company=job.get("company", ""),
                               description=(job.get("description") or "")[:MAX_DESC_FOR_GEMMA])
        try:
            return finalize(job, ai.ask_json(prompt, JobTags, count=count), "ai", today)
        except ai.AIBadOutput:
            pass  # one odd answer should not stop the run: use keywords for this job only
    return finalize(job, keyword_tags(job), "keywords", today)
