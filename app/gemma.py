"""The ONLY file that talks to Gemma (through the Gemini API).

`ask_json(prompt, schema, session_id=None)` returns a validated object, or raises
`GemmaUnavailable` / `GemmaLimitReached`. It does not rely on the API's JSON mode: it strips
code fences, parses the text, validates it against a pydantic schema, and retries once.
Prompts contain CV text, so they are never logged.
"""
import json
import os
import re
from datetime import date

from pydantic import BaseModel, ValidationError

from . import db

MODEL = os.getenv("GEMMA_MODEL", "gemma-4-26b-a4b-it")  # 31B timed out with server errors in testing; set GEMMA_MODEL to try it
TIMEOUT_SECONDS = 60
PER_VISITOR_LIMIT = int(os.getenv("GEMMA_PER_VISITOR_LIMIT", "6"))  # 2 calls + a few retries/edits
DAILY_LIMIT = int(os.getenv("GEMMA_DAILY_LIMIT", "300"))             # total across all visitors


class GemmaUnavailable(Exception):
    """No key, no network, API error, or no usable answer."""


class GemmaBadOutput(GemmaUnavailable):
    """The API answered but the answer was not valid JSON of the right shape."""


class GemmaLimitReached(Exception):
    """A per-visitor or daily call limit was hit."""


def _api_key():
    return os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY") or ""


def available():
    return bool(_api_key())


def _call_model(prompt):
    """Send one prompt, return the text. Tests replace this function."""
    key = _api_key()
    if not key:
        raise GemmaUnavailable("no API key set")
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


def _count_call(session_id):
    """Check both limits and count this call. Raises GemmaLimitReached."""
    with db.SessionLocal() as s:
        today = date.today().isoformat()
        usage = s.get(db.GemmaUsage, today)
        if usage is None:
            usage = db.GemmaUsage(day=today, calls=0)
            s.add(usage)
        if (usage.calls or 0) >= DAILY_LIMIT:
            raise GemmaLimitReached("daily")
        visitor = s.get(db.Visitor, session_id)
        if visitor is not None:
            if (visitor.gemma_calls or 0) >= PER_VISITOR_LIMIT:
                raise GemmaLimitReached("visitor")
            visitor.gemma_calls = (visitor.gemma_calls or 0) + 1
        usage.calls = (usage.calls or 0) + 1
        s.commit()


def ask_json(prompt, schema, session_id=None):
    """Ask Gemma for JSON matching a pydantic model class. `session_id=None` skips visitor limits."""
    if not available():
        raise GemmaUnavailable("no API key set")
    if session_id is not None:
        _count_call(session_id)

    full_prompt = (
        f"{prompt}\n\nReply with ONLY one JSON object, no commentary and no code fences, matching this "
        f"JSON schema:\n{json.dumps(schema.model_json_schema())}"
    )
    last_problem = "unknown"
    for attempt in range(2):  # one retry
        try:
            text = _call_model(full_prompt)
        except GemmaUnavailable:
            raise
        except Exception as e:  # network, quota, auth... never include the message (it may echo input)
            last_problem = f"API error ({type(e).__name__})"
            if _is_auth_error(e):
                break
            continue
        try:
            return schema.model_validate(parse_json(text))
        except (ValueError, ValidationError):
            last_problem = "answer was not valid JSON of the expected shape"
    if last_problem.startswith("answer"):
        raise GemmaBadOutput(last_problem)
    raise GemmaUnavailable(last_problem)


def _is_auth_error(e):
    return getattr(e, "code", None) in (400, 401, 403)
