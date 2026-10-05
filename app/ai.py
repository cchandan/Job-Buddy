"""The ONLY file that talks to the AI provider (today: Gemma 4 through the Gemini API).

`ask_json(prompt, schema)` returns a validated object; `ask_text(prompt)` returns plain text. Both raise
`AIUnavailable` / `AILimitReached`. Nothing outside this file may depend on which provider is used.
Prompts contain CV text, so they are never logged.
"""
import json
import os
import re
from datetime import date

from pydantic import ValidationError

from . import db

MODEL = os.getenv("GEMMA_MODEL", "gemma-4-26b-a4b-it")  # 31B timed out with server errors in testing; set GEMMA_MODEL to try it
TIMEOUT_SECONDS = 60
DAILY_LIMIT = int(os.getenv("AI_DAILY_LIMIT", "200"))  # calls made by the live app; job tagging runs are not counted


class AIUnavailable(Exception):
    """No key, no network, API error, or no usable answer."""


class AIBadOutput(AIUnavailable):
    """The API answered but the answer was not usable."""


class AILimitReached(Exception):
    """The daily call limit was hit."""


def _api_key():
    return os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY") or ""


def available():
    return bool(_api_key())


def _call_model(prompt):
    """Send one prompt, return the text. Tests replace this function."""
    key = _api_key()
    if not key:
        raise AIUnavailable("no API key set")
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=key, http_options=types.HttpOptions(timeout=TIMEOUT_SECONDS * 1000))
    response = client.models.generate_content(
        model=MODEL, contents=prompt, config=types.GenerateContentConfig(
            temperature=0.2, thinking_config=types.ThinkingConfig(thinking_level="MINIMAL")))  # much faster
    return response.text or ""


def parse_json(text):
    """Pull a JSON object out of model text (code fences and chatter around it are tolerated)."""
    text = re.sub(r"```(?:json)?", "", text or "")
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("no JSON object found")
    return json.loads(text[start:end + 1])


def _count_call():
    """Check the daily limit and count this call. Raises AILimitReached."""
    with db.SessionLocal() as s:
        today = date.today().isoformat()
        usage = s.get(db.AIUsage, today)
        if usage is None:
            usage = db.AIUsage(day=today, calls=0)
            s.add(usage)
        if (usage.calls or 0) >= DAILY_LIMIT:
            raise AILimitReached("daily")
        usage.calls = (usage.calls or 0) + 1
        s.commit()


def _ask(prompt, use, count):
    """Call the model up to twice; `use(text)` turns the answer into the result or raises ValueError."""
    if not available():
        raise AIUnavailable("no API key set")
    if count:
        _count_call()
    last_problem = "unknown"
    for attempt in range(2):  # one retry
        try:
            text = _call_model(prompt)
        except AIUnavailable:
            raise
        except Exception as e:  # network, quota, auth... never include the message (it may echo input)
            last_problem = f"API error ({type(e).__name__})"
            if _is_auth_error(e):
                break
            continue
        try:
            return use(text)
        except (ValueError, ValidationError):
            last_problem = "answer was not usable"
    if last_problem.startswith("answer"):
        raise AIBadOutput(last_problem)
    raise AIUnavailable(last_problem)


def ask_json(prompt, schema, count=True):
    """Ask for JSON matching a pydantic model class. `count=False` (the tagging run) skips the daily limit."""
    full_prompt = (
        f"{prompt}\n\nReply with ONLY one JSON object, no commentary and no code fences, matching this "
        f"JSON schema:\n{json.dumps(schema.model_json_schema())}"
    )
    return _ask(full_prompt, lambda text: schema.model_validate(parse_json(text)), count)


def ask_text(prompt, count=True):
    """Ask for plain text (the tailored CV)."""
    def use(text):
        text = re.sub(r"^```\w*\n|\n```\s*$", "", (text or "").strip()).strip()
        if len(text) < 80:
            raise ValueError("too short")
        return text
    return _ask(prompt, use, count)


def _is_auth_error(e):
    return getattr(e, "code", None) in (400, 401, 403)
