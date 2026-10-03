"""Job ranking: pure rules and maths. NO AI, no database.

Every decision about how good a job is for a visitor, and whether it is blocked, is made here.
"""
from dataclasses import dataclass, field

LEVELS = ["graduate", "junior", "mid", "senior"]

# Score weights (they add up to 100)
W_OVERLAP = 30      # skills the visitor has, out of the skills the job asks for
W_MISSING = 15      # few missing required skills
W_DREAM = 15        # similarity to the skills their dream jobs ask for
W_SENIORITY = 15    # job level vs visitor level
W_TECH = 5          # preferred technologies
W_INDUSTRY = 5      # preferred industries
W_LOCATION = 10     # preferred locations
W_SALARY = 5        # salary vs minimum

# How well a job level fits a visitor level (job level minus visitor level)
_SENIORITY_FIT = {-3: 0.4, -2: 0.5, -1: 0.8, 0: 1.0, 1: 0.6, 2: 0.2, 3: 0.0}

SALARY_BLOCK_RATIO = 0.8  # blocked when the stated salary is more than 20% below the minimum


@dataclass
class Ranked:
    job: dict
    score: int
    blocked: bool = False
    block_reason: str = ""
    block_quote: str = ""
    strong_matches: list = field(default_factory=list)
    missing_skills: list = field(default_factory=list)      # required skills the visitor lacks
    missing_optional: list = field(default_factory=list)    # nice-to-have skills the visitor lacks
    why: str = ""


def _fmt_money(n):
    return f"£{int(n):,}"


def _block_check(job, prefs):
    """Hard constraints. Returns (blocked, reason, quote)."""
    if prefs.get("needs_sponsorship") and job.get("sponsorship") == "no_sponsorship":
        return True, "You need sponsorship and this job says it does not offer it", job.get("sponsorship_quote") or ""
    min_salary = prefs.get("min_salary")
    top = job.get("salary_max") or job.get("salary_min")
    if min_salary and top and top < min_salary * SALARY_BLOCK_RATIO:
        return True, (f"Salary of up to {_fmt_money(top)} is more than 20% below your "
                      f"{_fmt_money(min_salary)} minimum"), ""
    return False, "", ""


def _norm(values):
    return [str(v).strip().lower() for v in values or [] if str(v).strip()]


def _score(profile, job, strong, missing_req, missing_opt):
    prefs = profile.get("preferences") or {}
    req, opt = job.get("required_skills") or [], job.get("optional_skills") or []
    have = set(profile.get("skills") or [])

    # 1. skill overlap (required skills count double)
    denom = 2 * len(req) + len(opt)
    if denom:
        got = 2 * sum(s in have for s in req) + sum(s in have for s in opt)
        overlap = got / denom
    else:
        overlap = 0.5  # the advert lists nothing: unknown, not a penalty

    # 2. missing required skills: none missing = full marks, 3 or more = nothing
    missing = 1.0 - min(len(missing_req), 3) / 3

    # 3. similarity to the skills the dream jobs ask for
    dream = profile.get("dream_skills") or {}
    job_skills = list(dict.fromkeys(req + opt))
    if dream and job_skills:
        total = sum(dream.values())
        shared = sum(dream.get(s, 0) for s in job_skills if s in dream)
        dream_sim = min(1.0, (shared / total) * 2.5)
    else:
        dream_sim = 0.5

    # 4. seniority fit
    user_level = LEVELS.index(profile["level"]) if profile.get("level") in LEVELS else 0
    job_level = LEVELS.index(job["seniority"]) if job.get("seniority") in LEVELS else None
    seniority = 0.5 if job_level is None else _SENIORITY_FIT.get(job_level - user_level, 0.0)

    # 5. preferred technologies
    techs = set(profile_prefs_techs(prefs))
    tech = min(1.0, len(techs & set(job_skills)) / min(len(techs), 3)) if techs else 1.0

    # 6. preferred industries
    inds = _norm(prefs.get("industries"))
    job_ind = (job.get("industry") or "").lower()
    if not inds:
        industry = 1.0
    elif not job_ind:
        industry = 0.5
    else:
        industry = 1.0 if any(i in job_ind or job_ind in i for i in inds) else 0.0

    # 7. preferred locations (remote jobs fit anywhere)
    locs = _norm(prefs.get("locations"))
    job_loc = (job.get("location") or "").lower()
    if not locs:
        location = 1.0
    elif job.get("work_mode") == "remote":
        location = 1.0
    elif not job_loc:
        location = 0.5
    else:
        location = 1.0 if any(l in job_loc for l in locs) else 0.0

    # 8. salary (unknown = neutral)
    min_salary = prefs.get("min_salary")
    top = job.get("salary_max") or job.get("salary_min")
    if not min_salary:
        salary = 1.0
    elif not top:
        salary = 0.5
    else:
        salary = max(0.0, min(1.0, top / min_salary))

    total = (W_OVERLAP * overlap + W_MISSING * missing + W_DREAM * dream_sim + W_SENIORITY * seniority
             + W_TECH * tech + W_INDUSTRY * industry + W_LOCATION * location + W_SALARY * salary)
    return int(round(max(0, min(100, total))))


def profile_prefs_techs(prefs):
    from .skills import canonical
    return [c for c in (canonical(t) for t in prefs.get("technologies") or []) if c]


def _why(job, strong, missing_req, blocked, reason):
    parts = []
    if blocked:
        parts.append(reason + ".")
    req = job.get("required_skills") or []
    if strong:
        parts.append(f"You already have {', '.join(strong[:5])}.")
    elif req:
        parts.append("None of its listed skills are on your CV yet.")
    if missing_req:
        parts.append(f"Missing: {', '.join(missing_req[:5])}.")
    elif req:
        parts.append("You have every required skill.")
    return " ".join(parts)


def rank(profile, jobs):
    """Rank jobs for a visitor. Unblocked jobs always come first, then by score (highest first)."""
    prefs = profile.get("preferences") or {}
    have = set(profile.get("skills") or [])
    results = []
    for job in jobs:
        req, opt = job.get("required_skills") or [], job.get("optional_skills") or []
        strong = [s for s in dict.fromkeys(req + opt) if s in have]
        missing_req = [s for s in req if s not in have]
        missing_opt = [s for s in opt if s not in have and s not in missing_req]
        blocked, reason, quote = _block_check(job, prefs)
        results.append(Ranked(
            job=job,
            score=_score(profile, job, strong, missing_req, missing_opt),
            blocked=blocked, block_reason=reason, block_quote=quote,
            strong_matches=strong, missing_skills=missing_req, missing_optional=missing_opt,
            why=_why(job, strong, missing_req, blocked, reason),
        ))
    results.sort(key=lambda r: (r.blocked, -r.score))
    return results
