"""The ONLY file that talks to the AI provider (today: Gemma 4 through the Gemini API).

`ask_json(prompt, schema)` returns a validated object; `ask_text(prompt)` returns plain text. Both raise
`AIUnavailable` / `AILimitReached`. Nothing outside this file may depend on which provider is used.
Prompts contain CV text, so they are never logged.
"""
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
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


# ---------- backends for job tagging (laptop only) ----------
# Tagging tries these in order and uses the first that works, so no paid API is needed. CV tailoring on the live site
# keeps using Gemini only. A backend that hits a limit is skipped for the rest of the run.
TAGGING_BACKENDS = ("claude", "codex", "ollama", "gemini")
CLAUDE_MODEL = os.getenv("TAGGING_CLAUDE_MODEL", "haiku")
CODEX_MODEL = os.getenv("TAGGING_CODEX_MODEL", "gpt-6-luna")  # OpenAI's smallest, cheapest model, made for high-volume extraction
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "gemma3")
CLI_TIMEOUT = 240
_down = set()
LAST = {"backend": ""}


def reset_backends():
    _down.clear()
    LAST["backend"] = ""


def _ollama_up():
    try:
        import httpx
        return httpx.get("http://localhost:11434/api/tags", timeout=1.5).status_code == 200
    except Exception:
        return False


def backend_ready(name):
    if name in _down:
        return False
    if name == "gemini":
        return bool(_api_key())
    if name == "ollama":
        return _ollama_up()
    return shutil.which(name) is not None


def backend_label(name):
    """'claude (haiku)': which tool and which model, for the admin page and the run log."""
    model = {"claude": CLAUDE_MODEL, "codex": CODEX_MODEL, "ollama": OLLAMA_MODEL, "gemini": MODEL}.get(name, "")
    return f"{name} ({model})" if model else name


def tagging_backends():
    return [b for b in TAGGING_BACKENDS if backend_ready(b)]


def _run_cli(cmd, prompt, name):
    try:
        done = subprocess.run(cmd, input=prompt, capture_output=True, text=True, timeout=CLI_TIMEOUT,
                              cwd=tempfile.gettempdir())  # a neutral folder: no project files or settings are read
    except subprocess.TimeoutExpired:
        raise AIUnavailable(f"{name} timed out")
    if done.returncode != 0:
        _down.add(name)  # any failure (a limit, a bad model name, not signed in) switches this tool off for the rest of the run
        blob = (done.stderr + done.stdout).lower()
        if "limit" in blob or "quota" in blob or "usage" in blob or "credit" in blob:
            raise AIUnavailable(f"{name} usage limit reached")
        raise AIUnavailable(f"{name} failed")
    return done.stdout


def _call_claude(prompt):
    return _run_cli(["claude", "-p", "--model", CLAUDE_MODEL, "--tools", "", "--no-session-persistence",
                     "--setting-sources", "", "--output-format", "text"], prompt, "claude")


def _call_codex(prompt):
    with tempfile.NamedTemporaryFile("r+", suffix=".txt") as out:
        _run_cli(["codex", "exec", "--skip-git-repo-check", "--sandbox", "read-only", "--ephemeral", "-m", CODEX_MODEL,
                  "-c", 'model_reasoning_effort="low"',  # extraction needs no deep thinking: cheaper and faster
                  "-o", out.name, "-"], prompt, "codex")
        return out.read()


def _call_ollama(prompt):
    import httpx
    try:
        r = httpx.post("http://localhost:11434/api/generate", timeout=CLI_TIMEOUT,
                       json={"model": OLLAMA_MODEL, "prompt": prompt, "stream": False})
        r.raise_for_status()
        return r.json().get("response", "")
    except Exception:
        raise AIUnavailable("ollama failed")


_CALLERS = {"claude": _call_claude, "codex": _call_codex, "ollama": _call_ollama}


def _ask_backends(prompt, use, backends):
    """Try each ready backend; the first that gives a usable answer wins. Raises AIUnavailable if none do."""
    last = "no AI tool available"
    for name in backends:
        if not backend_ready(name):
            continue
        for attempt in range(2):
            try:
                text = _call_model(prompt) if name == "gemini" else _CALLERS[name](prompt)
            except AIUnavailable as e:
                last = str(e)
                break
            except Exception as e:
                last = f"{name} error ({type(e).__name__})"
                if getattr(e, "code", None) == 429:
                    _down.add(name)
                    break
                if _is_auth_error(e):
                    _down.add(name)
                    break
                time.sleep(2 + 3 * attempt)
                continue
            try:
                result = use(text)
            except (ValueError, ValidationError):
                last = f"{name} answer was not usable"
                continue
            LAST["backend"] = name
            return result
    raise AIUnavailable(last)


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


def ask_json(prompt, schema, count=True, backends=None):
    """Ask for JSON matching a pydantic model class. `count=False` (the tagging run) skips the daily limit.
    `backends` (a tuple of names) lets the laptop tagging run use Claude/Codex/Ollama as well as Gemini."""
    full_prompt = (
        f"{prompt}\n\nReply with ONLY one JSON object, no commentary and no code fences, matching this "
        f"JSON schema:\n{json.dumps(schema.model_json_schema())}"
    )
    use = lambda text: schema.model_validate(parse_json(text))  # noqa: E731
    if backends is not None:
        return _ask_backends(full_prompt, use, backends)
    return _ask(full_prompt, use, count)


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
