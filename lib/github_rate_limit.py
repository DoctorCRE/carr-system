"""Shared provider deadlines and pacing for GitHub reads, including launchd callers."""
from __future__ import annotations

import fcntl
import json
import math
import os
import re
import tempfile
import time
from contextlib import contextmanager
from email.utils import parsedate_to_datetime
from datetime import datetime
from pathlib import Path


class GitHubReadPaused(RuntimeError):
    def __init__(self, until: float, reason: str = "provider rate limit"):
        self.until = until
        super().__init__(f"CARR_GITHUB_LOCAL_HOLD: {reason}; retry at {until:.3f}")


class GitHubBudgetLockTimeout(RuntimeError):
    pass


def resource_for(args: list[str]) -> str:
    endpoint = args[1].lstrip('/') if len(args) > 1 and args[0] == 'api' else ''
    return ('graphql' if endpoint == 'graphql' else 'code_search' if endpoint.startswith('search/code')
            else 'search' if endpoint.startswith('search/') else 'core' if endpoint and not endpoint.startswith('-') else 'unknown')


def split_response_bytes(output: bytes) -> tuple[dict[str, str], bytes]:
    if not output.startswith(b'HTTP/'):
        return {}, output
    match = re.search(rb'\r?\n\r?\n', output)
    if match is None:
        raise ValueError('GitHub response headers are incomplete')
    head = output[:match.start()].decode('latin-1')
    headers = {}
    for line in head.splitlines()[1:]:
        key, separator, value = line.partition(':')
        if separator:
            headers[key.lower()] = value.strip()
    return headers, output[match.end():]


def split_response(output: str) -> tuple[dict[str, str], str]:
    if not output.startswith("HTTP/"):
        return {}, output
    head, separator, body = output.replace("\r\n", "\n").partition("\n\n")
    if not separator:
        raise ValueError("GitHub response headers are incomplete")
    headers = {}
    for line in head.splitlines()[1:]:
        key, separator, value = line.partition(":")
        if separator:
            headers[key.lower()] = value.strip()
    return headers, body


def retry_deadline(headers: dict[str, str], diagnostic: str, observed_at: float) -> float | None:
    if "CARR_GITHUB_LOCAL_HOLD:" in diagnostic and not headers:
        return None
    if re.search(r"HTTP 401|gh auth login", diagnostic, re.I):
        return None
    limited = bool(re.search(r"rate limit|abuse detection|HTTP 429", diagnostic, re.I))
    exhausted = headers.get("x-ratelimit-remaining") == "0"
    if not limited and not exhausted:
        return None
    deadlines = []
    if (exhausted or "secondary" not in diagnostic.lower()) and "x-ratelimit-reset" in headers:
        deadlines.append(float(headers["x-ratelimit-reset"]))
    retry = headers.get("retry-after")
    if retry:
        try:
            seconds = float(retry)
        except ValueError:
            deadlines.append(parsedate_to_datetime(retry).timestamp())
        else:
            if not math.isfinite(seconds) or seconds < 0:
                raise ValueError("GitHub Retry-After is invalid")
            # An old cached response must retain its original provider deadline.
            anchor = parsedate_to_datetime(headers["date"]).timestamp() if headers.get("date") else observed_at
            deadlines.append(anchor + seconds)
    if deadlines:
        if any(not math.isfinite(value) or value < 0 for value in deadlines):
            raise ValueError("GitHub retry deadline is invalid")
        return max(deadlines)
    match = re.search(r"(?:retry|wait) (?:again )?(?:after|until) (\d\d:\d\d:\d\d) UTC", diagnostic, re.I)
    if match:
        day = time.strftime("%Y-%m-%d", time.gmtime(observed_at))
        deadline = datetime.fromisoformat(f"{day}T{match[1]}+00:00").timestamp()
        return deadline + 86400 if deadline < observed_at - 43200 else deadline
    return observed_at + (60 if "secondary" in diagnostic.lower() or "HTTP 429" in diagnostic else 900)


class GitHubReadBudget:
    def __init__(self, env=None, *, path: Path | None = None, clock=time.time, spacing=2.0,
                 lock_timeout=5.0, cancel=None):
        env = os.environ if env is None else env
        self.path = path or Path(env.get("CARR_GITHUB_READ_BUDGET", str(Path.home() / ".cache/carr/github-read-budget.json")))
        # Unidentified configured tokens share a conservative pool. No credential is read or logged.
        principal = env.get("CARR_GITHUB_BUDGET_PRINCIPAL", "configured-token" if "GH_TOKEN" in env or "GITHUB_TOKEN" in env else "stored-login")
        self.scope = f"{env.get('GH_HOST', 'github.com')}:{principal}"
        self.shared = f"{env.get('GH_HOST', 'github.com')}:shared"
        self.clock, self.spacing = clock, spacing
        if not math.isfinite(lock_timeout) or lock_timeout <= 0:
            raise ValueError('GitHub budget lock timeout must be positive and finite')
        self.lock_timeout, self.cancel = lock_timeout, cancel or (lambda: None)
        legacy_dir = env.get('GH_LIMITER_DIR', str(Path(__file__).resolve().parents[1] / 'out/orch/gh-limiter'))
        self.legacy = Path(legacy_dir) / 'cooldown'
        self.legacy_cooldown = float(env.get('GH_LIMITER_COOLDOWN', 900))

    @contextmanager
    def _lock(self, path, timeout, *, cancel):
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError('GitHub budget lock timeout must be positive and finite')
        path.parent.mkdir(parents=True, exist_ok=True)
        with os.fdopen(os.open(path, os.O_RDWR | os.O_CREAT, 0o600), "r+") as lock:
            deadline = time.monotonic() + timeout
            while True:
                cancel()
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise GitHubBudgetLockTimeout('GitHub budget lock deadline expired; calls stopped') from None
                    time.sleep(min(.05, remaining))
            yield

    @contextmanager
    def state(self):
        with self._state(cancel=self.cancel) as data:
            yield data

    @contextmanager
    def _state(self, *, cancel):
        with self._lock(Path(str(self.path) + ".lock"), self.lock_timeout, cancel=cancel):
            try:
                data = json.loads(self.path.read_text()) if self.path.exists() else {}
                if not isinstance(data, dict):
                    raise ValueError("invalid budget state")
                for row in data.values():
                    if not isinstance(row, dict) or not isinstance(row.get("holds", {}), dict):
                        raise ValueError("invalid budget row")
                    values = [row.get("next_start", 0), *row.get("holds", {}).values()]
                    if any(type(v) not in (int, float) or not math.isfinite(v) or v < 0 for v in values):
                        raise ValueError("invalid budget deadline")
                yield data
                with tempfile.NamedTemporaryFile(mode="w", dir=self.path.parent, delete=False) as output:
                    json.dump(data, output)
                    output.flush()
                    os.fsync(output.fileno())
                    temporary = output.name
                os.replace(temporary, self.path)
            except (OSError, ValueError, TypeError):
                raise RuntimeError("GitHub budget state unreadable; reads stopped") from None

    @contextmanager
    def call_slot(self, *, timeout):
        with self._lock(Path(str(self.path) + ".call.lock"), timeout, cancel=self.cancel):
            started = False

            def mark_started():
                nonlocal started
                started = True

            try:
                yield mark_started
            finally:
                if started:
                    end = time.monotonic() + self.spacing
                    try:
                        with self._state(cancel=lambda: None) as data:
                            row = data.setdefault(self.shared, {})
                            row["next_start"] = max(float(row.get("next_start", 0)),
                                                    self.clock() + self.spacing)
                    except Exception:
                        # Cleanup must preserve the dispatched command's outcome.
                        # With no durable deadline, keep peers behind the call lock.
                        while time.monotonic() < end:
                            time.sleep(min(.2, max(0, end - time.monotonic())))

    def _check(self, data, resource):
        now = self.clock()
        try:
            legacy_until = float(self.legacy.read_text().split()[0]) + self.legacy_cooldown
        except FileNotFoundError:
            legacy_until = 0
        except (OSError, ValueError, IndexError):
            raise RuntimeError('Legacy GitHub hold unreadable; calls stopped') from None
        if not math.isfinite(legacy_until) or legacy_until < 0:
            raise RuntimeError('Legacy GitHub deadline invalid; calls stopped')
        row = data.get(self.scope, {})
        pools = (row.get("holds", {}), data.get(self.shared, {}).get("holds", {}))
        until = max((float(v) for holds in pools for k, v in holds.items()
                     if resource == "unknown" or k in (resource, "unknown")), default=0)
        until = max(until, legacy_until)
        if until > now:
            raise GitHubReadPaused(until)
        return row

    def check(self, resource):
        with self.state() as data:
            self._check(data, resource)

    def reserve(self, resource, *, with_slot=False):
        """Return the reserved delay, optionally paired with its absolute wall slot."""
        with self.state() as data:
            self._check(data, resource)
            row = data.setdefault(self.shared, {})
            now = self.clock()
            slot = max(now, float(row.get("next_start", 0)))
            row["next_start"] = slot + self.spacing
            data[self.shared] = row
            delay = max(0, slot - now)
            return (slot, delay) if with_slot else delay

    def observe(self, resource, headers, diagnostic, observed_at):
        until = retry_deadline(headers, diagnostic, observed_at)
        if until is None or until <= self.clock():
            return until
        resource = headers.get("x-ratelimit-resource", resource)
        with self.state() as data:
            secondary = bool(re.search(r"secondary rate limit|abuse detection", diagnostic, re.I))
            secondary = secondary or (headers.get("x-ratelimit-remaining") != "0"
                                      and ("retry-after" in headers or "HTTP 429" in diagnostic))
            scope = self.shared if secondary else self.scope
            if scope == self.shared:
                resource = "unknown"
            row = data.setdefault(scope, {})
            holds = row.setdefault("holds", {})
            holds[resource] = max(float(holds.get(resource, 0)), until)
        return until
