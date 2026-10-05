import os
import re
import sys
import tempfile
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Tests always use a throwaway SQLite file and never a real AI key.
os.environ["DATABASE_URL"] = f"sqlite:///{tempfile.mkdtemp()}/test.db"
for name in ("GEMINI_API_KEY", "GOOGLE_API_KEY"):
    os.environ[name] = ""
os.environ["JOBBUDDY_NO_DOTENV"] = "1"
os.environ["SESSION_SECRET"] = "test-secret"

from fastapi.testclient import TestClient  # noqa: E402

from app import auth, db, web  # noqa: E402
from app.main import app  # noqa: E402

TODAY = date(2026, 10, 5)
auth._N = 2 ** 4  # cheap password hashing so the tests are quick


@pytest.fixture(autouse=True)
def fresh_db(monkeypatch):
    for key in ("BOOTSTRAP_ADMIN_NAME", "BOOTSTRAP_ADMIN_USERNAME", "BOOTSTRAP_ADMIN_PASSWORD"):
        monkeypatch.delenv(key, raising=False)
    db.Base.metadata.drop_all(db.engine)
    db.Base.metadata.create_all(db.engine)
    monkeypatch.setattr(web, "today", lambda: TODAY)
    auth._DUMMY_HASH = auth.hash_password("dummy")
    yield


def make_person(username, role="user", name=None, password="correct-horse-1"):
    with db.SessionLocal() as s:
        person = db.Person(username=username, name=name or username.capitalize(), role=role,
                           password_hash=auth.hash_password(password))
        s.add(person)
        s.commit()
        return person.id


def make_job(job_id="j1", **fields):
    row = dict(id=job_id, title="Junior Developer", company="Acme", location="Leeds", url=f"https://example.com/{job_id}",
               description="We build things with Python and AWS. " * 8, sponsorship="unclear", work_mode="hybrid",
               seniority="junior", required_skills=["Python"], optional_skills=[])
    row.update(fields)
    with db.SessionLocal() as s:
        s.add(db.Job(**row))
        s.commit()
    return job_id


class Browser:
    """A TestClient that fills in the CSRF token the way a real form does."""

    def __init__(self):
        self.client = TestClient(app, follow_redirects=False)

    def get(self, url, **kw):
        return self.client.get(url, **kw)

    def token(self):
        page = self.client.get("/login", follow_redirects=True).text
        found = re.search(r'name="csrf" value="([^"]+)"', page)
        assert found, "no CSRF token on the page"
        return found.group(1)

    def post(self, url, data=None, **kw):
        data = dict(data or {})
        data.setdefault("csrf", self.token())
        return self.client.post(url, data=data, **kw)

    def login(self, username, password="correct-horse-1"):
        return self.post("/login", {"username": username, "password": password})


@pytest.fixture
def browser():
    return Browser()


@pytest.fixture
def people():
    """Two users and an admin, as in the family."""
    return {"wife": make_person("wife"), "brother": make_person("brother"), "dad": make_person("dad", "admin")}


def signed_in(username):
    b = Browser()
    assert b.login(username).status_code == 303
    return b
