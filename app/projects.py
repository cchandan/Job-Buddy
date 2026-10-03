"""Gemma call B: explain the top gaps and suggest 1-3 portfolio projects. The result is saved."""
from pydantic import BaseModel

from . import gemma, skills

TOP_N = 5


class GapWhy(BaseModel):
    skill: str
    why: str


class ProjectOut(BaseModel):
    title: str
    description: str
    skills_demonstrated: list[str] = []
    suggested_stack: list[str] = []
    scope: str = ""
    difficulty: str = ""
    milestones: list[str] = []
    why_this_project: str = ""


class AnalysisOut(BaseModel):
    gap_explanations: list[GapWhy] = []
    projects: list[ProjectOut] = []


PROMPT = """You are a career coach for an early-career software graduate in the UK.
Their top skill gaps (skills that jobs they want ask for, which their CV lacks), most important first:
{gaps}

Their level: {level}
Their dream roles: {roles}
Skills they already have: {have}

Return:
- gap_explanations: for EACH gap above, 1-2 sentences ("why this matters for you") that mention how often jobs ask
  for it.
- projects: 1 to 3 portfolio projects that close these gaps. Every project needs: title, description (2 sentences),
  skills_demonstrated (ONLY skills from the gap list above, spelled exactly as above), suggested_stack, scope (such
  as "weekend" or "2 weeks"), difficulty ("Beginner" or "Intermediate"), 3 to 5 milestones, and why_this_project
  (one sentence tying it to their dream roles).
"""


def _clip(text, n):
    return (text or "").strip()[:n]


def validate(out, gap_skills):
    """Never trust Gemma: only keep explanations and project skills that belong to the gap list."""
    allowed = set(gap_skills)
    explanations = {}
    for g in out.gap_explanations:
        s = skills.canonical(g.skill)
        if s in allowed and g.why.strip() and s not in explanations:
            explanations[s] = _clip(g.why, 400)
    projects = []
    for p in out.projects:
        demo = [s for s in skills.canonical_list(p.skills_demonstrated) if s in allowed]
        if not demo or not p.title.strip() or not p.description.strip():
            continue  # a project that closes none of the gaps is dropped
        projects.append({
            "title": _clip(p.title, 100), "description": _clip(p.description, 500),
            "skills_demonstrated": demo, "suggested_stack": skills.canonical_list(p.suggested_stack, limit=8),
            "scope": _clip(p.scope, 40), "difficulty": _clip(p.difficulty, 30),
            "milestones": [_clip(m, 160) for m in p.milestones if m.strip()][:5],
            "why_this_project": _clip(p.why_this_project, 300),
        })
    return explanations, projects[:3]


def generate(profile, gaps, session_id):
    """Run call B. Returns the analysis dict to save, or raises GemmaUnavailable/GemmaLimitReached."""
    top = gaps[:TOP_N]
    if not top:
        raise gemma.GemmaBadOutput("no gaps to explain")
    gap_lines = "\n".join(
        f"- {g['skill']}: asked for by {g['jobs_asking']} of {g['total_relevant']} relevant jobs"
        f"{', and by your dream jobs' if g['in_dream'] else ''}" for g in top)
    prompt = PROMPT.format(
        gaps=gap_lines, level=profile.get("level", "graduate"),
        roles=", ".join(profile.get("dream_titles") or []) or "software engineer",
        have=", ".join((profile.get("skills") or [])[:20]) or "none listed")
    out = gemma.ask_json(prompt, AnalysisOut, session_id)
    gap_skills = [g["skill"] for g in top]
    explanations, projects = validate(out, gap_skills)
    if not projects:
        raise gemma.GemmaBadOutput("no valid projects")
    return {"gap_skills": gap_skills, "explanations": explanations, "projects": projects}
