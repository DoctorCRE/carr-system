"""Read-only release HTTP transport shared by smoke checks and local runtimes."""
from __future__ import annotations

from dataclasses import dataclass
import json
import http.cookiejar
from email.utils import parsedate_to_datetime
import random
import re
import sys
import time
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


def rate_limited(status: int, body: bytes) -> bool:
    return status == 429 or bool(re.search(
        rb'(?:cloudflare[\s\S]{0,200}?(?:error\s*)?1015|error\s*(?:code\s*:?\s*)?1015)',
        body, re.I)) and body.lstrip().startswith(b'<')


def retry_delay(status: int, headers: dict[str, str], body: bytes,
                attempt: int, elapsed: float, *, now: float | None = None) -> float | None:
    """None means return the original response; never shorten Retry-After."""
    if not rate_limited(status, body) or attempt >= 6:
        return None
    value = next((v.strip() for k, v in headers.items() if k.lower() == 'retry-after'), '')
    delay: float | None = None
    if re.fullmatch(r'[0-9]+', value):
        # Huge integers need no conversion to float to exceed the budget.
        digits = value.lstrip('0') or '0'
        delay = int(digits) if len(digits) < 10 else 91
    elif value:
        try:
            delay = max(0, parsedate_to_datetime(value).timestamp() - (time.time() if now is None else now))
        except (ValueError, TypeError, OverflowError):
            pass
    if delay is None:
        delay = 5 * 2 ** (attempt - 1) + random.uniform(0, 1)
    return delay if elapsed + delay < 90 else None


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
    started = time.monotonic()
    attempt = 0
    while True:
        attempt += 1
        try:
            response = opener(request, timeout=timeout)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            body = response.read() if max_bytes is None else response.read(max_bytes + 1)
            reply = Reply(response.status, dict(response.headers.items()), body)
        delay = retry_delay(reply.status, reply.headers, body, attempt, time.monotonic() - started)
        if delay is None:
            if rate_limited(reply.status, body):
                print(f'rate limit (HTTP 429 / Cloudflare 1015); attempts={attempt}', file=sys.stderr)
            return reply
        time.sleep(delay)


if __name__ == '__main__':
    call = json.load(sys.stdin)
    body = call['body'].encode()
    print(json.dumps({'limited': rate_limited(call['status'], body),
                      'delay': retry_delay(call['status'], call['headers'], body,
                                           call['attempt'], call['elapsed'])}))
