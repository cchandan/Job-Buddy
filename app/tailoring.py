"""Core CV + job -> tailored CV.

The tailored CV may only reorder, select and reword what is in the core CV. Code cannot fully check that,
so the safeguards are this prompt, the side-by-side view, and optional admin review.
"""
from . import ai

MAX_CV = 12000
MAX_DESC = 6000

PROMPT = """You are helping someone tailor their CV for one specific job.

Rewrite the CV below so it is aimed at the job advert below.

STRICT RULES:
- Use ONLY facts that are in the CV. Never add an employer, job title, qualification, date, project or skill that
  the CV does not contain. If the advert asks for something the CV does not have, leave it out. Do not invent it.
- You may reorder sections and bullet points, choose what to put first, shorten, and reword for clarity.
- Put the experience and skills most relevant to this job first.
- Keep every employer name, job title, qualification and date exactly as written in the CV.
- Plain text only: section headings in CAPITALS on their own line, bullet points starting with "- ".
  No markdown, no tables, no commentary before or after the CV.

JOB: {title} at {company}

ADVERT:
\"\"\"
{description}
\"\"\"

CV:
\"\"\"
{cv}
\"\"\"
"""


def tailor(cv_text, job):
    """Returns the tailored CV text. Raises ai.AIUnavailable / ai.AILimitReached."""
    prompt = PROMPT.format(title=job.title or "", company=job.company or "the employer",
                           description=(job.description or "")[:MAX_DESC], cv=(cv_text or "")[:MAX_CV])
    return ai.ask_text(prompt)
