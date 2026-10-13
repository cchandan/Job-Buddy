"""Where the refresher keeps things, and the connection to the website. Plain files in one folder (default ~/job-buddy-refresher)."""
import json
import os
from pathlib import Path

CODE_PREFIX = "jbr1."


def home():
    return Path(os.getenv("JBR_HOME") or Path.home() / "job-buddy-refresher")


def paths(base=None):
    base = Path(base) if base else home()
    return {"home": base, "config": base / "refresher.json", "state": base / "state.json", "data": base / "data", "logs": base / "logs",
            "db": base / "data" / "refresher.db"}


def parse_connection_code(code):
    """(site url, secret, name) from the code copied off Admin > Refresher. Raises ValueError with words a person can act on."""
    import base64
    code = (code or "").strip()
    if not code.startswith(CODE_PREFIX):
        raise ValueError("That doesn't look like a connection code. It starts with jbr1.")
    try:
        raw = code[len(CODE_PREFIX):]
        data = json.loads(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)))
        return data["u"].rstrip("/"), data["t"], data.get("n") or "Refresher"
    except Exception:
        raise ValueError("That connection code is damaged. Copy it again from Admin > Refresher on the website.")


def save(site_url, token, name, base=None):
    p = paths(base)
    p["home"].mkdir(parents=True, exist_ok=True)
    p["config"].write_text(json.dumps({"site_url": site_url, "token": token, "name": name}, indent=2))
    p["config"].chmod(0o600)  # the secret: only this user may read it


def load(base=None):
    """The saved connection, or None if this computer has not been paired yet."""
    p = paths(base)["config"]
    if not p.exists():
        return None
    data = json.loads(p.read_text())
    return {"site_url": data["site_url"], "token": data["token"], "name": data.get("name") or "Refresher"}


def load_state(base=None):
    p = paths(base)["state"]
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError):
        return {}


def save_state(state, base=None):
    p = paths(base)
    p["home"].mkdir(parents=True, exist_ok=True)
    p["state"].write_text(json.dumps(state, indent=2))


def setup_environment(connection, base=None):
    """Point the Job Buddy code at this computer's own small database and at the website. Must run before `app` is imported."""
    p = paths(base)
    p["data"].mkdir(parents=True, exist_ok=True)
    p["logs"].mkdir(parents=True, exist_ok=True)
    os.environ.update({
        "DATABASE_URL": f"sqlite:///{p['db']}", "JOBBUDDY_NO_DOTENV": "1", "JOBBUDDY_NO_SEED": "1",
        "INGEST_URL": connection["site_url"], "INGEST_TOKEN": connection["token"], "JOBBUDDY_REFRESHER": connection["name"],
    })
