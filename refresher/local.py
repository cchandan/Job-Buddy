"""Copies the website's settings (who to search for, which sources are off) into this computer's own small database."""


def apply_config(config):
    from app import db, ingest  # imported late: the environment must be set first (see config.setup_environment)
    wanted = set()
    with db.SessionLocal() as s:
        for p in config.get("people") or []:
            username = str(p["username"]).strip().lower()
            wanted.add(username)
            person = s.query(db.Person).filter(db.Person.username == username).first()
            if person is None:
                person = db.Person(username=username, name=p.get("name") or username, role="user", password_hash="!")  # never signs in here
                s.add(person)
                s.flush()
            else:
                person.name = p.get("name") or person.name
            row = s.get(db.SearchProfile, person.id)
            if row is None:
                row = db.SearchProfile(person_id=person.id)
                s.add(row)
            row.keywords, row.locations, row.avoid = p.get("keywords", ""), p.get("locations", ""), p.get("avoid", "")
            row.distance, row.per_search = int(p.get("distance") or 25), int(p.get("per_search") or 100)
            row.remote_uk, row.uk_wide = bool(p.get("remote_uk")), bool(p.get("uk_wide"))
            row.needs_sponsorship, row.enabled = bool(p.get("needs_sponsorship")), bool(p.get("enabled", True))
        for person in s.query(db.Person):  # someone removed on the website stops being searched for
            if person.username not in wanted:
                profile = s.get(db.SearchProfile, person.id)
                if profile is not None:
                    profile.enabled = False
        s.merge(db.AppSetting(key=ingest.SOURCES_KEY, value=",".join(config.get("sources_off") or [])))
        s.commit()
