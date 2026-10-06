"""The one way an unattended job reads GitHub.

Scheduled scripts read GitHub through the authenticated gh CLI. Each used to
carry its own wrapper, so a fix for one incident class landed in one place
and nowhere else: the release pipeline learned to retry a transient `gh api`
failure (fc5fe401) while the progress board, the PR actor, the recovery-point
reader and the Jev value report kept failing on the first blip.

GitHubReader owns how a read is done; callers keep their domain queries.

  retry      a failed read is retried after 5s and then 15s, unless gh's
             error is one a retry cannot fix (a 4xx other than a rate limit,
             gh logged out, gh missing). Once a read has exhausted its retries
             on a transient failure, the same reader's next reads try once
             until one succeeds: an outage costs one retry cycle per run, not
             one per read.
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
from typing import Any

from lib.secret_redaction import redact_text, sensitive_env_values

GH_FALLBACKS = ("/opt/homebrew/bin/gh", "/usr/local/bin/gh")
RETRY_DELAYS = (5, 15)
TAIL_LIMIT = 200

# A 4xx is the request's fault, so asking again cannot help -- except a rate
# limit (429, or GitHub's 403 "rate limit exceeded") and a request timeout.
_CLIENT_ERROR = re.compile(r"\bHTTP 4\d\d\b")
_RETRYABLE_CLIENT_ERROR = re.compile(r"\bHTTP (?:408|429)\b|rate limit", re.I)
_LOGGED_OUT = re.compile(r"gh auth login")


class GitHubUnreadable(RuntimeError):
    """A GitHub read that could not be completed. `detail` is the redacted
    tail of gh's error; `transient` says whether the last failure might pass
    on a later attempt."""

    def __init__(self, message: str, *, detail: str = "", transient: bool = False,
                 attempts: int = 1):
        super().__init__(message)
        self.detail, self.transient, self.attempts = detail, transient, attempts


def resolve_gh(env: Mapping[str, str] | None = None, *,
               which: Callable[..., str | None] = shutil.which,
               executable: Callable[[str], bool] = lambda p: os.access(p, os.X_OK)) -> str:
    """`gh` when it is on the job's PATH, else a Homebrew install, else `gh`
    (so the read fails as gh-missing rather than silently)."""
    path = (env if env is not None else os.environ).get("PATH")
    if which("gh", path=path):
        return "gh"
    return next((p for p in GH_FALLBACKS if executable(p)), "gh")


class GitHubReader:
    def __init__(self, *, env: Mapping[str, str] | None = None, cwd: str | None = None,
                 timeout: float = 30, retry_delays: Iterable[float] = RETRY_DELAYS,
                 gh: str | None = None, runner: Callable[..., Any] | None = None,
                 sleep: Callable[[float], Any] | None = None):
        self.env = dict(env) if env is not None else None
        self.cwd, self.timeout = cwd, timeout
        self.retry_delays = tuple(retry_delays)
        self.gh = gh or resolve_gh(self.env)
        self._runner, self._sleep = runner, sleep
        self._outage = False

    def api(self, path: str, *, paginate: bool = False,
            fields: Mapping[str, object] | None = None) -> Any:
        """`gh api <path>` parsed. paginate=True reads every page and flattens them."""
        args = ["api", *(["--paginate", "--slurp"] if paginate else []), path]
        if fields:
            args += ["--method", "GET"]
            for key, value in fields.items():
                args += ["-f", f"{key}={value}"]
        data = self._parse(self.text(args), args)
        if paginate:   # --slurp yields one list per page
            return [item for page in (data or []) for item in (page or [])]
        return data

    def json(self, args: list[str]) -> Any:
        """Any gh subcommand whose output is JSON (`pr list --json ...`), parsed."""
        return self._parse(self.text(args), args)

    def text(self, args: list[str]) -> str:
        """Any gh subcommand's stdout, after retrying transient failures."""
        what = _what(args)
        delays = [] if self._outage else list(self.retry_delays)
        attempts = 0
        while True:
            attempts += 1
            try:
                proc = (self._runner or subprocess.run)(
                    [self.gh, *args], env=self.env, cwd=self.cwd, stdin=subprocess.DEVNULL,
                    capture_output=True, text=True, timeout=self.timeout)
            except subprocess.TimeoutExpired:
                failure = f"timed out after {self.timeout:g}s"
                transient, detail = True, ""
            except OSError as exc:
                raise GitHubUnreadable(f"gh {what} could not start: {type(exc).__name__}",
                                       attempts=attempts) from exc
            else:
                if proc.returncode == 0:
                    self._outage = False
                    return proc.stdout or ""
                detail = self._tail(getattr(proc, "stderr", "") or "")
                failure = f"exited {proc.returncode}"
                transient = _transient(detail)
            if not transient or not delays:
                self._outage = self._outage or (transient and attempts > 1)
                plural = "s" if attempts > 1 else ""
                raise GitHubUnreadable(
                    f"gh {what} {failure} after {attempts} attempt{plural}"
                    + (f": {detail}" if detail else ""),
                    detail=detail, transient=transient, attempts=attempts)
            (self._sleep or time.sleep)(delays.pop(0))

    def _tail(self, stderr: str) -> str:
        # Redact the whole stderr before keeping its tail, so a cut can never
        # leave half a token that the patterns no longer match.
        known = sensitive_env_values(self.env if self.env is not None else os.environ)
        return " ".join(redact_text(stderr, known_secrets=known).split())[-TAIL_LIMIT:]

    @staticmethod
    def _parse(text: str, args: list[str]) -> Any:
        try:
            return json.loads(text or "null")
        except ValueError as exc:
            raise GitHubUnreadable(f"gh {_what(args)} returned output that is not JSON") from exc


def _what(args: list[str]) -> str:
    """The read's name for an error line: `api <path without query>` or the subcommand."""
    if args and args[0] == "api":
        path = next((a for a in args[1:] if not a.startswith("-")), "")
        return f"api {path.split('?')[0]}"
    return " ".join(args[:2])


def _transient(detail: str) -> bool:
    if _LOGGED_OUT.search(detail):
        return False
    return not (_CLIENT_ERROR.search(detail) and not _RETRYABLE_CLIENT_ERROR.search(detail))
