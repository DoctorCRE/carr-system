"""Read-only release HTTP transport shared by smoke checks and local runtimes."""
from __future__ import annotations

from dataclasses import dataclass
import json
import http.cookiejar
from pathlib import Path
import urllib.error
import urllib.request

MARKER = json.loads(Path(__file__).with_name("release-reader-marker.json").read_text())

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
         headers: dict[str, str] | None = None, max_bytes: int | None = None,
         cookie_jar: http.cookiejar.CookieJar | None = None) -> Reply:
    """Read up to max_bytes plus one overflow byte when a cap is supplied."""
    if cookie_jar is not None:
        opener = urllib.request.build_opener(NoRedirect, urllib.request.HTTPCookieProcessor(cookie_jar)).open
    request_headers = {key.lower(): value for key, value in (headers or {}).items()}
    request_headers.setdefault("user-agent", USER_AGENT)
    request_headers[MARKER["header"]] = MARKER["value"]
    request = urllib.request.Request(url, method="GET", headers=request_headers)
    try:
        with opener(request, timeout=timeout) as response:
            body = response.read() if max_bytes is None else response.read(max_bytes + 1)
            return Reply(response.status, dict(response.headers.items()), body)
    except urllib.error.HTTPError as error:
        with error:
            body = error.read() if max_bytes is None else error.read(max_bytes + 1)
            return Reply(error.code, dict(error.headers.items()), body)
