"""Read-only release HTTP transport shared by smoke checks and local runtimes."""
from __future__ import annotations

from dataclasses import dataclass
import urllib.error
import urllib.request

USER_AGENT = "doctorcre-production-smoke/1 (+ops/doctorcre-production-smoke.py)"


@dataclass
class Reply:
    status: int
    headers: dict[str, str]
    body: bytes


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_OPENER = urllib.request.build_opener(NoRedirect)


def read(url: str, opener=_OPENER.open, timeout: int = 15) -> Reply:
    request = urllib.request.Request(url, method="GET", headers={"User-Agent": USER_AGENT})
    try:
        with opener(request, timeout=timeout) as response:
            return Reply(response.status, dict(response.headers.items()), response.read())
    except urllib.error.HTTPError as error:
        # Redirects are disabled by the caller for the auth probe, but HTTP
        # errors still carry useful status/headers and should be judged.
        return Reply(error.code, dict(error.headers.items()), error.read())
