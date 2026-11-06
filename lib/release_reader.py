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


def read(url: str, opener=_OPENER.open, timeout: float = 15, *, max_bytes: int | None = None) -> Reply:
    """Read up to max_bytes plus one overflow byte when a cap is supplied."""
    request = urllib.request.Request(url, method="GET", headers={"User-Agent": USER_AGENT})
    try:
        with opener(request, timeout=timeout) as response:
            body = response.read() if max_bytes is None else response.read(max_bytes + 1)
            return Reply(response.status, dict(response.headers.items()), body)
    except urllib.error.HTTPError as error:
        # Redirects are disabled by the caller for the auth probe, but HTTP
        # errors still carry useful status/headers and should be judged.
        body = error.read() if max_bytes is None else error.read(max_bytes + 1)
        return Reply(error.code, dict(error.headers.items()), body)
