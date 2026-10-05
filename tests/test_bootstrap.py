"""First-admin setup must never reset or elevate an existing account."""
import pytest
from fastapi.testclient import TestClient

from app import auth, db
from app.main import app
from conftest import Browser, make_person


@pytest.fixture
def settings(monkeypatch):
    monkeypatch.setenv("BOOTSTRAP_ADMIN_NAME", "Chandan")
    monkeypatch.setenv("BOOTSTRAP_ADMIN_USERNAME", " Chandan ")
    monkeypatch.setenv("BOOTSTRAP_ADMIN_PASSWORD", "a-long-test-password")


def test_startup_creates_admin_who_can_sign_in(settings, caplog):
    with TestClient(app):
        pass
    browser = Browser()
    assert browser.login("chandan", "a-long-test-password").status_code == 303
    assert browser.get("/admin").status_code == 200
    with db.SessionLocal() as s:
        person = s.query(db.Person).filter_by(username="chandan").one()
        assert person.name == "Chandan" and person.is_admin
        assert person.password_hash.startswith("scrypt$")
        assert "a-long-test-password" not in person.password_hash
    assert "a-long-test-password" not in caplog.text


def test_restart_preserves_changed_password_and_session(settings):
    with TestClient(app):
        pass
    with db.SessionLocal() as s:
        person = s.query(db.Person).filter_by(username="chandan").one()
        auth.set_password(person, "my-new-password")
        s.commit()
        stored, version = person.password_hash, person.session_version
    with TestClient(app):
        pass
    with db.SessionLocal() as s:
        person = s.query(db.Person).one()
        assert (person.password_hash, person.session_version) == (stored, version)


def test_existing_user_is_never_promoted(settings):
    person_id = make_person("chandan")
    with TestClient(app):
        pass
    with db.SessionLocal() as s:
        person = s.get(db.Person, person_id)
        assert person.role == "user"
        assert auth.check_password("correct-horse-1", person.password_hash)


def test_no_second_bootstrap_admin(settings):
    make_person("existing-admin", role="admin")
    with TestClient(app):
        pass
    with db.SessionLocal() as s:
        assert s.query(db.Person).count() == 1


@pytest.mark.parametrize("key,value", [
    ("BOOTSTRAP_ADMIN_USERNAME", ""),
    ("BOOTSTRAP_ADMIN_USERNAME", "invalid user"),
    ("BOOTSTRAP_ADMIN_PASSWORD", ""),
    ("BOOTSTRAP_ADMIN_PASSWORD", "short"),
    ("BOOTSTRAP_ADMIN_PASSWORD", " " * 15),
    ("BOOTSTRAP_ADMIN_PASSWORD", "x" * 201),
])
def test_invalid_setup_fails_without_leaking_values(settings, monkeypatch, key, value):
    monkeypatch.setenv(key, value)
    with pytest.raises(ValueError, match="BOOTSTRAP_ADMIN") as error:
        with TestClient(app):
            pass
    assert "a-long-test-password" not in str(error.value)
    with db.SessionLocal() as s:
        assert s.query(db.Person).count() == 0


def test_without_bootstrap_settings_no_account_is_created():
    with TestClient(app):
        pass
    with db.SessionLocal() as s:
        assert s.query(db.Person).count() == 0
