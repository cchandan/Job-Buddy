"""Skill gap scoring. Pure code, no AI."""
from collections import Counter

from .skills import SOFT_SKILLS

RELEVANT_LEVELS = ("graduate", "junior")
SENIOR_LEVELS = ("mid", "senior")


def relevant_jobs(ranked):
    """Jobs worth learning for: not blocked, graduate or junior level (falls back to all unblocked)."""
    rel = [r for r in ranked if not r.blocked and r.job.get("seniority") in RELEVANT_LEVELS]
    return rel or [r for r in ranked if not r.blocked] or list(ranked)


def top_gaps(profile, ranked, limit=8):
    """Skills the visitor lacks, best first.

    Each gap: skill, priority (0-100), jobs_asking, required_count, total_relevant, in_dream, example_jobs.
    priority = 50% how many relevant jobs ask for it + 20% how many of those say "required"
             + 30% how many of the dream jobs ask for it.
    """
    have = set(profile.get("skills") or [])
    dream = profile.get("dream_skills") or {}
    n_dream = max(1, profile.get("dream_count") or 1)
    rel = relevant_jobs(ranked)

    asking, required, examples = Counter(), Counter(), {}
    for r in rel:
        j = r.job
        for s in dict.fromkeys((j.get("required_skills") or []) + (j.get("optional_skills") or [])):
            asking[s] += 1
            if s in (j.get("required_skills") or []):
                required[s] += 1
            examples.setdefault(s, []).append({"id": j["id"], "title": j["title"], "company": j["company"]})

    candidates = {s for s in set(asking) | set(dream) if s not in have and s not in SOFT_SKILLS}
    if not candidates:
        return []
    max_freq = max(asking[s] for s in candidates) or 1
    gaps = []
    for s in candidates:
        freq = asking[s] / max_freq
        req_share = required[s] / asking[s] if asking[s] else 0
        dream_share = min(1.0, dream.get(s, 0) / n_dream)
        gaps.append({
            "skill": s, "priority": int(round(100 * (0.5 * freq + 0.2 * req_share + 0.3 * dream_share))),
            "jobs_asking": asking[s], "required_count": required[s], "total_relevant": len(rel),
            "in_dream": s in dream, "example_jobs": examples.get(s, [])[:3],
        })
    gaps.sort(key=lambda g: (-g["priority"], -g["jobs_asking"], g["skill"]))
    return gaps[:limit]


def senior_skills(profile, ranked, exclude=(), limit=6):
    """What mid and senior roles ask for that the visitor lacks: [{skill, jobs_asking, total_senior}].

    Junior adverts often leave out the tools the team actually uses; the senior adverts for the same kind of
    work name them. These shape project ideas only: they never change the gap list or the ranking.
    """
    have = set(profile.get("skills") or []) | set(exclude)
    senior = [r for r in ranked if r.job.get("seniority") in SENIOR_LEVELS]
    asking = Counter()
    for r in senior:
        for s in set((r.job.get("required_skills") or []) + (r.job.get("optional_skills") or [])):
            if s not in have and s not in SOFT_SKILLS:
                asking[s] += 1
    ranked_skills = sorted(asking.items(), key=lambda kv: (-kv[1], kv[0]))
    return [{"skill": s, "jobs_asking": n, "total_senior": len(senior)} for s, n in ranked_skills[:limit] if n >= 2]


def strong_skills(profile, ranked, limit=10):
    """Skills the visitor has that relevant jobs ask for: [(skill, jobs_asking)], most asked first."""
    have = set(profile.get("skills") or [])
    asking = Counter()
    for r in relevant_jobs(ranked):
        for s in set((r.job.get("required_skills") or []) + (r.job.get("optional_skills") or [])):
            if s in have:
                asking[s] += 1
    return asking.most_common(limit)
