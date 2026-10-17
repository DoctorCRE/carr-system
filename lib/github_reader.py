"""The one way an unattended job reads GitHub.

Scheduled scripts read GitHub through the authenticated gh CLI. Each used to
carry its own wrapper, so a fix for one incident class landed in one place
and nowhere else: the release pipeline learned to retry a transient `gh api`
failure (fc5fe401) while the progress board, the PR actor, the recovery-point
reader and the Jev value report kept failing on the first blip.

GitHubReader owns how a read is done; callers keep their domain queries.

  retry      network failures retry after 5s and 15s. Provider rate limits
             stop immediately and retain their reset/Retry-After deadline in
             shared state. A local hold returns without a request or renewal.
  paging     GET pagination makes one budgeted request per page and refuses
             incomplete results after twenty pages. Other CLI subcommands can
             make hidden requests; pacing counts invocations, not those calls.
  redaction  gh's stderr is redacted in full before its tail is kept, so a
             cut can never leave half a token the patterns no longer match.
  one error  every unreadable read raises GitHubUnreadable, a RuntimeError
             carrying one bounded line, whether the last failure was
             transient, and how many attempts were made.
  gh on PATH launchd starts jobs with PATH=/usr/bin:/bin:/usr/sbin:/sbin, where
             Homebrew's gh is invisible; resolve_gh() falls back to the
             Homebrew locations.

The runner (subprocess.run's signature) and the sleep are injectable, so a
test replays gh's output instead of patching subprocess.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit

from lib.github_rate_limit import GitHubReadBudget, GitHubReadPaused, resource_for, split_response
from lib.secret_redaction import redact_text, sensitive_env_values

SOURCE_BYTES = Path(__file__).read_bytes()

GH_FALLBACKS = ("/opt/homebrew/bin/gh", "/usr/local/bin/gh")
RETRY_DELAYS = (5, 15)
TAIL_LIMIT = 200

# A 4xx is the request's fault, so asking again cannot help -- except a rate
# limit (429, or GitHub's 403 "rate limit exceeded") and a request timeout.
_CLIENT_ERROR = re.compile(r"\bHTTP 4\d\d\b")
_RETRYABLE_CLIENT_ERROR = re.compile(r"\bHTTP (?:408|429)\b|rate limit", re.I)
_AUTHENTICATION = re.compile(r"\bHTTP 401\b|gh auth login", re.I)
_RATE_LIMIT = re.compile(r"\bHTTP 429\b|rate limit|abuse detection", re.I)


class GitHubUnreadable(RuntimeError):
    """A GitHub read that could not be completed. `detail` is the redacted
    tail of gh's error; `transient` says whether the last failure might pass
    on a later attempt. `kind` classifies the full redacted diagnostic before
    its tail is cut."""

    def __init__(self, message: str, *, detail: str = "", transient: bool = False,
                 attempts: int = 1, kind: str = "github_unreadable"):
        super().__init__(message)
        self.detail, self.transient, self.attempts = detail, transient, attempts
        self.kind = kind


def resolve_gh(env: Mapping[str, str] | None = None, *,
               which: Callable[..., str | None] = shutil.which,
               executable: Callable[[str], bool] = lambda p: os.access(p, os.X_OK)) -> str:
    """`gh` when it is on the job's PATH, else a Homebrew install, else `gh`
    (so the read fails as gh-missing rather than silently)."""
    path = (env if env is not None else os.environ).get("PATH")
    found = which("gh", path=path)
    wrapper = Path(__file__).resolve().parents[1] / "ops/github-gh.py"
    if found and Path(found).resolve() != wrapper:
        return "gh"
    return next((p for p in GH_FALLBACKS if executable(p)), "gh")


class GitHubReader:
    def __init__(self, *, env: Mapping[str, str] | None = None, cwd: str | None = None,
                 timeout: float = 30, retry_delays: Iterable[float] = RETRY_DELAYS,
                 gh: str | None = None, runner: Callable[..., Any] | None = None,
                 sleep: Callable[[float], Any] | None = None,
                 budget: GitHubReadBudget | None = None):
        self.env = dict(env) if env is not None else None
        self.cwd, self.timeout = cwd, timeout
        self.retry_delays = tuple(retry_delays)
        self.gh = gh or resolve_gh(self.env)
        self._runner, self._sleep = runner, sleep
        self._outage = False
        self.budget = budget or (GitHubReadBudget(self.env) if runner is None else None)
        self._next_page: bool | None = None

    def api(self, path: str, *, paginate: bool = False,
            fields: Mapping[str, object] | None = None, max_pages: int = 20,
            slurp: bool = False) -> Any:
        """Read GET pages individually; never return a truncated list as complete."""
        if type(max_pages) is not int or not 1 <= max_pages <= 160:
            raise ValueError("max_pages must be between 1 and 160")
        rows, pages = [], []
        for page in range(1, max_pages + 1):
            endpoint = path
            if paginate:
                parts = urlsplit(path)
                query = dict(parse_qsl(parts.query))
                query.update({"per_page": "100", "page": str(page)})
                endpoint = parts.path + "?" + urlencode(query)
            args = ["api", endpoint]
            if fields:
                args += ["--method", "GET"]
                for key, value in fields.items():
                    if paginate and key in ("per_page", "page"):
                        continue
                    args += ["-f", f"{key}={value}"]
            text, attempts = self._read(args)
            data = self._parse(text, args, attempts=attempts)
            if not paginate:
                return data
            if not isinstance(data, list) and not (slurp and isinstance(data, dict)):
                raise GitHubUnreadable(f"gh api {path.split('?')[0]} returned no page list", kind="invalid_response")
            if isinstance(data, list):
                rows.extend(data)
            pages.append(data)
            if self._next_page is None and isinstance(data, dict):
                raise GitHubUnreadable('Object pagination requires response headers; no partial result returned', kind='invalid_response')
            if self._next_page is False or (self._next_page is None and len(data) < 100):
                return pages if slurp else rows
        raise GitHubUnreadable(f"gh api {path.split('?')[0]} exceeded {max_pages} pages; no partial result returned", kind="pagination_limit")

    def json(self, args: list[str]) -> Any:
        """Any gh subcommand whose output is JSON (`pr list --json ...`), parsed."""
        text, attempts = self._read(args)
        return self._parse(text, args, attempts=attempts)

    def text(self, args: list[str]) -> str:
        """Any gh subcommand's stdout, after retrying transient failures."""
        return self._read(args)[0]

    def _read(self, args: list[str]) -> tuple[str, int]:
        if args and args[0] == "api" and "--paginate" in args:
            raise GitHubUnreadable("Native pagination bypasses page budgets; use api(paginate=True)",
                                   kind="invalid_response", attempts=0)
        what = _what(args)
        delays = [] if self._outage else list(self.retry_delays)
        attempts = 0
        while True:
            attempts += 1
            resource = resource_for(args)
            try:
                if self.budget:
                    delay = self.budget.reserve(resource)
                    if delay:
                        (self._sleep or time.sleep)(delay)
                    self.budget.check(resource)
            except GitHubReadPaused as exc:
                raise GitHubUnreadable(str(exc), kind="rate_limit", transient=True, attempts=attempts - 1) from None
            except (RuntimeError, OSError) as exc:
                raise GitHubUnreadable(str(exc), kind="budget_unreadable", attempts=attempts - 1) from None
            observed_at = self.budget.clock() if self.budget else time.time()
            include = bool(args and args[0] == "api" and "--include" not in args and "-i" not in args)
            invocation = [*args, "--include"] if include else args
            try:
                proc = (self._runner or subprocess.run)(
                    [self.gh, *invocation], env=self.env, cwd=self.cwd, stdin=subprocess.DEVNULL,
                    capture_output=True, text=True, timeout=self.timeout)
            except subprocess.TimeoutExpired:
                failure = f"timed out after {self.timeout:g}s"
                transient, detail, kind = True, "", "github_unreadable"
            except OSError as exc:
                raise GitHubUnreadable(f"gh {what} could not start: {type(exc).__name__}",
                                       attempts=attempts) from exc
            else:
                try:
                    headers, body = split_response(proc.stdout or "")
                except ValueError as exc:
                    raise GitHubUnreadable(str(exc), kind="invalid_response", attempts=attempts) from None
                diagnostic = self._redact(getattr(proc, "stderr", "") or "")
                retry_at = None
                if self.budget:
                    try:
                        retry_at = self.budget.observe(resource, headers, diagnostic, observed_at)
                    except (RuntimeError, ValueError, OverflowError):
                        raise GitHubUnreadable("GitHub budget response invalid; reads stopped", kind="budget_unreadable", attempts=attempts) from None
                if proc.returncode == 0:
                    self._next_page = bool(re.search(r';\s*rel="next"', headers.get("link", ""))) if (proc.stdout or '').startswith('HTTP/') else None
                    self._outage = False
                    return (body if include else proc.stdout or ""), attempts
                detail = diagnostic[-TAIL_LIMIT:]
                failure = f"exited {proc.returncode}"
                transient = _transient(diagnostic)
                kind = _failure_kind(diagnostic)
            if kind == "rate_limit":
                deadline = f"; retry at {retry_at:.3f}" if retry_at is not None else ""
                raise GitHubUnreadable(
                    f"gh {what} rate limited after {attempts} attempt{deadline}; {detail}",
                    detail=detail, transient=True, attempts=attempts, kind=kind)
            if not transient or not delays:
                self._outage = self._outage or (transient and attempts > 1)
                plural = "s" if attempts > 1 else ""
                raise GitHubUnreadable(
                    f"gh {what} {failure} after {attempts} attempt{plural}"
                    + (f": {detail}" if detail else ""),
                    detail=detail, transient=transient, attempts=attempts, kind=kind)
            (self._sleep or time.sleep)(delays.pop(0))

    def _redact(self, stderr: str) -> str:
        # Redact the whole stderr before keeping its tail, so a cut can never
        # leave half a token that the patterns no longer match.
        known = sensitive_env_values(self.env if self.env is not None else os.environ)
        return " ".join(redact_text(stderr, known_secrets=known).split())

    @staticmethod
    def _parse(text: str, args: list[str], *, attempts: int = 1) -> Any:
        try:
            return json.loads(text or "null")
        except ValueError as exc:
            raise GitHubUnreadable(f"gh {_what(args)} returned output that is not JSON",
                                   attempts=attempts) from exc


def _what(args: list[str]) -> str:
    """The read's name for an error line: `api <path without query>` or the subcommand."""
    if args and args[0] == "api":
        path = next((a for a in args[1:] if not a.startswith("-")), "")
        return f"api {path.split('?')[0]}"
    return " ".join(args[:2])


def _transient(detail: str) -> bool:
    if _AUTHENTICATION.search(detail):
        return False
    return not (_CLIENT_ERROR.search(detail) and not _RETRYABLE_CLIENT_ERROR.search(detail))


def _failure_kind(detail: str) -> str:
    if _AUTHENTICATION.search(detail):
        return "authentication"
    return "rate_limit" if _RATE_LIMIT.search(detail) else "github_unreadable"
