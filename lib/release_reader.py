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


def read(url: str, opener=_OPENER.open, timeout: float = 15, *,
         headers: dict[str, str] | None = None, max_bytes: int | None = None) -> Reply:
    request = urllib.request.Request(url, method="GET", headers={**(headers or {}), "User-Agent": USER_AGENT})
    try:
        with opener(request, timeout=timeout) as response:
            return Reply(response.status, dict(response.headers.items()),
                         response.read() if max_bytes is None else response.read(max_bytes + 1))
    except urllib.error.HTTPError as error:
        with error:
            return Reply(error.code, dict(error.headers.items()),
                         error.read() if max_bytes is None else error.read(max_bytes + 1))
