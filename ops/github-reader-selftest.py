#!/usr/bin/env python3
"""ops/github-reader-selftest.py — the interface test for lib/github_reader.py,
the one way an unattended job reads GitHub.

Every case drives GitHubReader through an injected runner that replays what
the gh CLI prints and exits with, and an injected sleep that records the
waits instead of taking them. No gh, no network, no credential.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from lib.github_reader import GitHubReader, GitHubUnreadable  # noqa: E402

FAILS: list[str] = []


def check(name: str, cond: bool, got: object = None) -> None:
    print(("ok    " if cond else "FAIL  ") + name + ("" if cond else f"  (got {got!r})"))
    if not cond:
        FAILS.append(name)


class Gh:
    """Replays gh results in order and remembers each argv and its options."""

    def __init__(self, *results):
        self.results = list(results)
        self.calls: list[tuple[list[str], dict]] = []

    def __call__(self, argv, **kwargs):
        self.calls.append((list(argv), kwargs))
        result = self.results.pop(0)
        if isinstance(result, BaseException):
            raise result
        rc, out, err = result
        return subprocess.CompletedProcess(argv, rc, out, err)


def reader(gh: Gh, sleeps: list | None = None, **kw) -> GitHubReader:
    waits = sleeps if sleeps is not None else []
    return GitHubReader(runner=gh, sleep=waits.append, gh="gh", **kw)


def raised(fn) -> GitHubUnreadable | None:
    try:
        fn()
    except GitHubUnreadable as exc:
        return exc
    return None


PATH = "repos/o/r/issues/1504/comments?per_page=100"
CONNECT = (1, "", "error connecting to api.github.com\n")

# ── reads ─────────────────────────────────────────────────────────────────

gh = Gh((0, '{"id": 1}', ""))
check("api returns the parsed reply", reader(gh).api("repos/o/r/pulls/1") == {"id": 1})
check("api shells to `gh api <path>`", gh.calls[0][0] == ["gh", "api", "repos/o/r/pulls/1"], gh.calls)
check("stdin is closed and the read is bounded",
      gh.calls[0][1].get("stdin") == subprocess.DEVNULL and gh.calls[0][1].get("timeout") == 30,
      gh.calls[0][1])

gh = Gh((0, json.dumps([[{"id": 1}], [{"id": 2}, {"id": 3}]]), ""))
rows = reader(gh).api(PATH, paginate=True)
check("a paginated read flattens every page", [r["id"] for r in rows] == [1, 2, 3], rows)
check("pagination asks gh to slurp pages", gh.calls[0][0][:4] == ["gh", "api", "--paginate", "--slurp"],
      gh.calls[0][0])

gh = Gh((0, "{}", ""))
reader(gh).api("repos/o/r/actions/runs", fields={"branch": "main", "per_page": 5})
check("query fields are sent as GET parameters",
      gh.calls[0][0] == ["gh", "api", "repos/o/r/actions/runs", "--method", "GET",
                         "-f", "branch=main", "-f", "per_page=5"], gh.calls[0][0])

gh = Gh((0, '[{"number": 7}]', ""))
check("json runs any gh subcommand and parses it",
      reader(gh).json(["pr", "list", "--json", "number"]) == [{"number": 7}])
check("json passes its arguments through", gh.calls[0][0] == ["gh", "pr", "list", "--json", "number"])

gh = Gh((0, "identical\n", ""))
check("text returns stdout untouched", reader(gh).text(["api", "x", "--jq", ".status"]) == "identical\n")

gh = Gh((0, "{}", ""))
reader(gh, env={"PATH": "/p", "GH_TOKEN": "t" * 20}, cwd="/somewhere", timeout=300).api("x")
check("env, cwd and timeout reach the runner",
      gh.calls[0][1]["env"] == {"PATH": "/p", "GH_TOKEN": "t" * 20}
      and gh.calls[0][1]["cwd"] == "/somewhere" and gh.calls[0][1]["timeout"] == 300, gh.calls[0][1])

# ── transient failures are retried ────────────────────────────────────────

sleeps: list = []
gh = Gh(CONNECT, CONNECT, (0, '{"ok": 1}', ""))
check("two transient failures then success returns the data",
      reader(gh, sleeps).api(PATH) == {"ok": 1} and len(gh.calls) == 3, gh.calls)
check("the waits are 5s then 15s", sleeps == [5, 15], sleeps)

noise = "x" * 500
bad_gateway = (1, "", f"{noise}\nHTTP 502: Bad Gateway (https://api.github.com/repos/o/r)\n")
gh = Gh(bad_gateway, bad_gateway, bad_gateway)
exc = raised(lambda: reader(gh).api(PATH))
check("three failures raise GitHubUnreadable", exc is not None and len(gh.calls) == 3, gh.calls)
msg = str(exc)
check("the error names the read, exit status and attempts",
      "gh api repos/o/r/issues/1504/comments exited 1 after 3 attempts" in msg, msg)
check("the error carries gh's last stderr line", "HTTP 502: Bad Gateway" in msg, msg)
check("the error is one bounded line", "\n" not in msg and len(msg) < 400, len(msg))
check("an exhausted transient failure says it was transient",
      exc is not None and exc.transient is True, exc and exc.transient)
check("a RuntimeError, so existing `except RuntimeError` callers still catch it",
      isinstance(exc, RuntimeError))

sleeps = []
gh = Gh(subprocess.TimeoutExpired("gh", 30), (0, "[]", ""))
check("a timeout is retried", reader(gh, sleeps).api("x") == [] and sleeps == [5], sleeps)

gh = Gh((1, "", "HTTP 429: rate limit exceeded\n"), (0, "{}", ""))
check("a 429 is retried", reader(gh).api("x") == {} and len(gh.calls) == 2)

gh = Gh((1, "", "HTTP 403: API rate limit exceeded for user ID 1.\n"), (0, "{}", ""))
check("a 403 rate limit is retried", reader(gh).api("x") == {} and len(gh.calls) == 2)

sleeps = []
gh = Gh(CONNECT, CONNECT, CONNECT, CONNECT, (0, "{}", ""), CONNECT, CONNECT, (0, "[]", ""))
outage = reader(gh, sleeps)
raised(lambda: outage.api("a"))
second = raised(lambda: outage.api("b"))
check("once retries are exhausted, the same reader's next read tries once and does not wait",
      second is not None and second.attempts == 1 and len(gh.calls) == 4 and sleeps == [5, 15],
      (second, len(gh.calls), sleeps))
check("a later success ends the outage", outage.api("c") == {})
check("and the read after it retries again", outage.api("d") == [] and sleeps == [5, 15, 5, 15], sleeps)

gh = Gh(CONNECT, (0, "{}", ""))
check("retries can be turned off per reader",
      raised(lambda: reader(gh, retry_delays=()).api("x")) is not None and len(gh.calls) == 1)

# ── permanent failures are not ────────────────────────────────────────────

for label, stderr in (("404", "HTTP 404: Not Found (https://api.github.com/repos/o/r/x)\n"),
                      ("401", "HTTP 401: Bad credentials\n"),
                      ("422", "HTTP 422: Validation Failed\n"),
                      ("logged out", "To get started with GitHub CLI, please run:  gh auth login\n")):
    sleeps = []
    gh = Gh((1, "", stderr), (0, "{}", ""))
    exc = raised(lambda: reader(gh, sleeps).api("x"))
    check(f"a {label} is not retried", exc is not None and exc.transient is False
          and len(gh.calls) == 1 and sleeps == [], (exc, gh.calls, sleeps))
check("a single attempt says so", "after 1 attempt:" in str(exc), str(exc))

gh = Gh(FileNotFoundError(2, "No such file or directory: 'gh'"))
exc = raised(lambda: reader(gh).api("x"))
check("a missing gh is unreadable at once, not retried",
      exc is not None and exc.transient is False and len(gh.calls) == 1, exc)

gh = Gh((0, "<html>", ""))
exc = raised(lambda: reader(gh).api("x"))
check("a reply that is not JSON is unreadable", exc is not None and "not JSON" in str(exc), exc)

# ── redaction ─────────────────────────────────────────────────────────────

token = "ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"
gh = Gh((1, "", f"Authorization: token {token}\nHTTP 401\n"))
exc = raised(lambda: reader(gh, env={"GH_TOKEN": token}).api(PATH))
check("a known credential never reaches the error", token not in str(exc) and "HTTP 401" in str(exc),
      str(exc))

fine_grained = "github" + "_pat_" + ("A" * 82)
for suffix in ("", "." * 150 + "\n"):
    gh = Gh((1, "", f"Authorization: token {fine_grained}\n{suffix}HTTP 401\n"))
    exc = raised(lambda: reader(gh, env={}).api(PATH))
    detail = exc.detail if exc else ""
    check(f"a fine-grained token is redacted even unknown ({'crossing' if suffix else 'inside'} the cut)",
          "A" * 10 not in str(exc) and "github" + "_pat_" not in str(exc)
          and "[REDACTED]" in detail and "HTTP 401" in detail, detail)

# ── resolving gh for a launchd PATH ───────────────────────────────────────

from lib import github_reader  # noqa: E402

check("gh on PATH is used by name",
      github_reader.resolve_gh({"PATH": "/usr/bin"}, which=lambda _n, path=None: "/usr/bin/gh",
                               executable=lambda _p: False) == "gh")
check("off PATH, a Homebrew gh is found",
      github_reader.resolve_gh({"PATH": "/usr/bin:/bin"}, which=lambda _n, path=None: None,
                               executable=lambda p: p == "/opt/homebrew/bin/gh") == "/opt/homebrew/bin/gh")
check("with no gh anywhere the name is kept and the read reports it missing",
      github_reader.resolve_gh({"PATH": "/usr/bin"}, which=lambda _n, path=None: None,
                               executable=lambda _p: False) == "gh")

if FAILS:
    print(f"github-reader-selftest: {len(FAILS)} FAILED")
    sys.exit(1)
print("github-reader-selftest: all checks passed")
