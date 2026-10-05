"""One-off: create the first admin account, or rescue a locked-out admin.

    python scripts/create_admin.py                       # asks for a name and username
    python scripts/create_admin.py --name "Chandan" --username chandan

If the username already exists, that account gets a new password, is unlocked, and is made an admin.
The password is printed once here and only its hash is stored. Uses DATABASE_URL from .env.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import auth, db  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description="Create or rescue a Job Buddy admin account.")
    ap.add_argument("--name")
    ap.add_argument("--username")
    args = ap.parse_args()
    username = (args.username or input("Username (letters, numbers, dots or dashes): ")).strip().lower()
    if not auth.valid_username(username):
        sys.exit("That username won't work: use 3 to 40 letters, numbers, dots or dashes.")

    db.init_db()
    password = auth.new_password()
    with db.SessionLocal() as s:
        person = s.query(db.Person).filter(db.Person.username == username).first()
        if person is None:
            name = (args.name or input("Their name: ")).strip() or username
            s.add(db.Person(name=name[:80], username=username, role="admin", password_hash=auth.hash_password(password)))
            print(f"\nCreated admin account for {name}.")
        else:
            auth.set_password(person, password)
            person.role = "admin"
            print(f"\n{person.name}'s account already existed: it has a new password and is an admin.")
        s.commit()
    print(f"  Username: {username}\n  Password: {password}\n\nWrite the password down now. It is not stored and won't be shown again.")


if __name__ == "__main__":
    main()
