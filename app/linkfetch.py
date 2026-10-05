"""Fetch a job page for "add by link" and pull out its text, safely.

Only http(s); never a private, local or link-local address (checked for every redirect hop);
size, time and redirect limits. Raises LinkUnreadable for anything it cannot use.
"""
import html
import ipaddress
import json
import re
import socket
from urllib.parse import urljoin, urlparse

import httpx

MAX_BYTES = 2_000_000
MAX_REDIRECTS = 4
TIMEOUT = 12
MIN_TEXT = 200
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "en-GB,en;q=0.9",
}


class LinkUnreadable(Exception):
    """The page could not be fetched or had no usable text."""


def check_url(url):
    """Refuse anything that is not a public http(s) address. Returns the cleaned URL."""
    url = (url or "").strip()
    parts = urlparse(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise LinkUnreadable("not a web address")
    try:
        infos = socket.getaddrinfo(parts.hostname, parts.port or (443 if parts.scheme == "https" else 80))
    except (socket.gaierror, UnicodeError):
        raise LinkUnreadable("address not found")
    for info in infos:
        ip = ipaddress.ip_address(info[4][0].split("%")[0])
        if not ip.is_global:
            raise LinkUnreadable("not a public address")
    return url


def _get(url):
    """Follow redirects by hand so every hop is checked. Tests replace this function."""
    with httpx.Client(timeout=TIMEOUT, headers=_HEADERS, follow_redirects=False) as client:
        for _ in range(MAX_REDIRECTS + 1):
            check_url(url)
            with client.stream("GET", url) as r:
                if r.status_code in (301, 302, 303, 307, 308) and r.headers.get("location"):
                    url = urljoin(url, r.headers["location"])
                    continue
                if r.status_code != 200:
                    raise LinkUnreadable(f"status {r.status_code}")
                if "html" not in r.headers.get("content-type", "html"):
                    raise LinkUnreadable("not a web page")
                body = b""
                for chunk in r.iter_bytes():
                    body += chunk
                    if len(body) > MAX_BYTES:
                        break
                return body[:MAX_BYTES].decode(r.encoding or "utf-8", errors="replace")
    raise LinkUnreadable("too many redirects")


def _strip(markup):
    markup = re.sub(r"(?is)<(script|style|noscript|svg|head)\b.*?</\1>", " ", markup)
    markup = re.sub(r"(?i)<(br|/p|/div|/li|/h[1-6]|/tr)\s*/?>", "\n", markup)
    text = html.unescape(re.sub(r"(?s)<[^>]+>", " ", markup))
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    return re.sub(r"\n\s*\n\s*", "\n\n", text).strip()


def _job_posting(markup):
    """schema.org JobPosting data, which many job sites embed, or {}."""
    for block in re.findall(r'(?is)<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', markup):
        try:
            data = json.loads(block.strip())
        except ValueError:
            continue
        items = data if isinstance(data, list) else data.get("@graph", [data]) if isinstance(data, dict) else []
        for item in items:
            kind = item.get("@type") if isinstance(item, dict) else None
            if kind == "JobPosting" or (isinstance(kind, list) and "JobPosting" in kind):
                return item
    return {}


def _name(value):
    if isinstance(value, list):
        value = value[0] if value else ""
    if isinstance(value, dict):
        address = value.get("address")
        if isinstance(address, dict):
            return ", ".join(str(address[k]) for k in ("addressLocality", "addressRegion") if address.get(k))
        return str(value.get("name") or "")
    return str(value or "")


def fetch_job(url):
    """{"url", "title", "company", "location", "description", "valid_through"} for a job page."""
    try:
        markup = _get(url)
    except LinkUnreadable:
        raise
    except Exception as e:  # timeouts, TLS, connection errors
        raise LinkUnreadable(type(e).__name__)
    posting = _job_posting(markup)
    title_tag = re.search(r"(?is)<title[^>]*>(.*?)</title>", markup)
    title = _name(posting.get("title")) or (html.unescape(title_tag.group(1)).strip() if title_tag else "")
    description = _strip(str(posting.get("description") or "")) if posting.get("description") else ""
    if len(description) < MIN_TEXT:
        description = _strip(markup)
    if len(description) < MIN_TEXT:
        raise LinkUnreadable("no readable text")  # login walls and pages built by JavaScript end up here
    return {
        "url": url.strip(),
        "title": re.sub(r"\s+", " ", title)[:300],
        "company": _name(posting.get("hiringOrganization"))[:200],
        "location": _name(posting.get("jobLocation"))[:200],
        "description": description[:20000],
        "valid_through": str(posting.get("validThrough") or "")[:10],
    }
