"""Optional first-admin creation at startup. Never changes an existing account."""
import logging
import os

from sqlalchemy.exc import IntegrityError

from . import auth, db

log = logging.getLogger("jobbuddy")


def ensure_admin():
    username = os.getenv("BOOTSTRAP_ADMIN_USERNAME", "").strip().lower()
    password = os.getenv("BOOTSTRAP_ADMIN_PASSWORD", "")
    name = os.getenv("BOOTSTRAP_ADMIN_NAME", "").strip()
    if not any((username, password, name)):
        return

    with db.SessionLocal() as database:
        # Stale bootstrap settings must never reset passwords or grant new privileges.
        if database.query(db.Person).filter(db.Person.role == "admin").first():
            return
        if username and database.query(db.Person).filter_by(username=username).first():
            log.warning("Admin bootstrap skipped: that username already belongs to an account. Use the admin recovery script if needed.")
            return
        if not auth.valid_username(username):
            raise ValueError("BOOTSTRAP_ADMIN_USERNAME must be 3–40 letters, numbers, dots, underscores or dashes.")
        if not 12 <= len(password) <= 200 or not password.strip():
            raise ValueError("BOOTSTRAP_ADMIN_PASSWORD must contain 12–200 characters and cannot be blank.")
        if len(name) > 80:
            raise ValueError("BOOTSTRAP_ADMIN_NAME must be at most 80 characters.")

        database.add(db.Person(username=username, name=name or username, role="admin",
                               password_hash=auth.hash_password(password)))
        try:
            database.commit()
        except IntegrityError:
            database.rollback()
            # Another worker may have created the same account during startup.
            if not database.query(db.Person).filter_by(username=username).first():
                raise
        else:
            log.info("First admin created from bootstrap settings.")
