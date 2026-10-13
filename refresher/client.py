"""Talking to the Job Buddy website. The refresher only ever calls out; nothing at home has to accept a connection."""
import httpx

from .version import VERSION


class ClientError(Exception):
    """A problem reaching the website, in words a person can act on."""


class Client:
    def __init__(self, site_url, token, transport=None):
        self.http = httpx.Client(base_url=site_url.rstrip("/"), timeout=60, transport=transport,
                                 headers={"Authorization": f"Bearer {token}", "User-Agent": f"job-buddy-refresher/{VERSION}"})

    def checkin(self, report):
        """Report how things are and get the config and any command back."""
        try:
            r = self.http.post("/api/refresher/checkin", json=report)
        except httpx.HTTPError as e:
            raise ClientError(f"could not reach the website ({type(e).__name__}); will try again") from e
        if r.status_code == 401:
            raise ClientError("the website doesn't recognise this computer any more. It may have been removed on Admin > Refresher; "
                              "add it again and run the installer with the new code")
        if r.status_code >= 400:
            raise ClientError(f"the website answered {r.status_code}; will try again")
        try:
            return r.json()
        except ValueError as e:
            raise ClientError("the website's answer wasn't understood; will try again") from e
