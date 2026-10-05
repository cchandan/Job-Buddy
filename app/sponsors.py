"""The Home Office register of licensed Skilled Worker sponsors, as a "can sponsor" badge. Downloaded weekly, matched by name."""
import csv
import re
import time

import httpx

from . import db, sources

FILE = db.ROOT / "data" / "sponsors.csv"
PAGE = "https://www.gov.uk/api/content/government/publications/register-of-licensed-sponsors-workers"
_names = None


ALIASES = {"pwc": "pricewaterhousecoopers", "ey": "ernst young"}  # sites use the short name, the register the legal one


def _key(name):
    key = " ".join(sources._NOISE.sub("", sources._norm(name)).split())
    return ALIASES.get(key, key)


def is_licensed(company):
    global _names
    if _names is None:
        _names = set()
        if FILE.exists():
            with FILE.open(newline="", encoding="utf-8", errors="ignore") as f:
                _names = {_key(row[0]) for row in csv.reader(f) if row}
    return bool(company) and _key(company) in _names


def refresh(max_age_days=7):
    """Download the register if ours is older than a week. Returns a sentence for the run log."""
    global _names
    if FILE.exists() and time.time() - FILE.stat().st_mtime < max_age_days * 86400:
        return "Licensed-sponsor list: up to date."
    page = httpx.get(PAGE, timeout=30, follow_redirects=True).text
    url = re.search(r"https://assets[^\"\\ ]+Worker[^\"\\ ]*\.csv", page).group(0)  # the file name changes with each publish
    data = httpx.get(url, timeout=120, follow_redirects=True)
    data.raise_for_status()
    FILE.parent.mkdir(exist_ok=True)
    FILE.write_bytes(data.content)
    _names = None
    return f"Licensed-sponsor list: downloaded ({data.text.count(chr(10)):,} employers)."
