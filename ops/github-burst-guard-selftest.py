#!/usr/bin/env python3
"""github-burst-guard-selftest.py — fixtures for hooks/github-burst-guard.py,
written before the hook (rule e65efc68, enforcing the taught rule "never burst
GitHub").

THE INCIDENT. On 2026-10-06 one session ran a shell loop that made about 55
`gh` calls within seconds, then ran `ops/release-pipeline.py tick` by hand
three times in five minutes. GitHub's short-window (secondary) rate limit
answered 403 for the whole shared account, and an urgent production release
stopped behind the lockout.

WHAT THE HOOK MUST DO:
  1. DENY a shell loop (for / while / until / xargs / parallel) that runs `gh`
     with no `sleep N` (N >= 2) inside it. ALLOW the same loop with the sleep
     (which covers an `until gh ...; do sleep 30; done` poll), and a `for` over
     a short literal list (items x gh calls per pass <= 10).
  2. DENY a release-pipeline tick or out/orch/shepherd.sh (one shared window)
     within five minutes of the start of the last one that RAN AND DID REAL
     WORK: three sequential hand ticks in five minutes are refused, and so is
     a hand tick in a backgrounded shepherd's gap. Only PostToolUse stamps
     that window, after exit 0 and sixty seconds or more, or a background
     launch; so a tick a permission check denied, a tick that failed in
     seconds, and a short no-op tick never block the retry (2026-10-12).
     Also DENY while another process holds the pipeline's flock on
     out/release-pipeline/lock; the probe never creates, deletes or holds it.
     `--help` / `-h` and shepherd's `report` subcommand are never ticks and
     never stamped; `tick --lane report` is still a tick.
  3. PostToolUse: record a GitHub rate-limit answer seen in a GitHub-touching
     command's output. PreToolUse then DENIES every gh call (and the release
     tick) for fifteen minutes, except `gh api rate_limit`.
  Always ALLOW a single gh command, gh --paginate and gh api graphql outside
  the cooldown. Fail open on malformed input.

Spawns the REAL hook with REAL payloads; exit 2 = denied, 0 = allowed.
State, log and the pipeline lock go to a temp directory through
CARR_GITHUB_BURST_STATE, CARR_HOOK_GUARD_LOG and CARR_RELEASE_PIPELINE_LOCK,
so nothing here touches out/.

    .venv/bin/python ops/github-burst-guard-selftest.py
"""
import fcntl
import json
import os
import subprocess
import sys
import tempfile
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOOK = os.path.join(REPO, "hooks", "github-burst-guard.py")

TMP = tempfile.mkdtemp(prefix="github-burst-guard-")
STATE = os.path.join(TMP, "state.json")
LOCK = os.path.join(TMP, "release-pipeline", "lock")
ENV = dict(os.environ,
           CARR_GITHUB_BURST_STATE=STATE,
           CARR_RELEASE_PIPELINE_LOCK=LOCK,
           CARR_HOOK_GUARD_LOG=os.path.join(TMP, "hook-guard.log"))


def run(payload, raw=None):
    data = raw if raw is not None else json.dumps(payload)
    proc = subprocess.run([sys.executable, HOOK], input=data, env=ENV,
                          capture_output=True, text=True, timeout=30)
    return proc.returncode, proc.stderr


def pre(cmd, tool="Bash"):
    return run({"hook_event_name": "PreToolUse", "tool_name": tool,
                "tool_input": {"command": cmd}, "session_id": "selftest"})


def post(cmd, stdout="", stderr="", event="PostToolUse", error=None, exit_code=None):
    payload = {"hook_event_name": event, "tool_name": "Bash",
               "tool_input": {"command": cmd}, "session_id": "selftest"}
    if event == "PostToolUse":
        payload["tool_response"] = {"stdout": stdout, "stderr": stderr,
                                    "interrupted": False}
        if exit_code is not None:
            payload["tool_response"]["exit_code"] = exit_code
    else:
        payload["error"] = error or ""
    return run(payload)


def set_state(**kw):
    with open(STATE, "w", encoding="utf-8") as fh:
        json.dump(kw, fh)


def clear_state():
    if os.path.exists(STATE):
        os.remove(STATE)


def read_state():
    try:
        with open(STATE, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


# ---- class 1: loops -------------------------------------------------------
LOOP_DENY = [
    ("for-over-gh-list",
     "for pr in $(gh pr list --json number -q '.[].number'); do gh pr view $pr --json state; done"),
    ("for-literal-eleven",
     "for i in 1 2 3 4 5 6 7 8 9 10 11; do gh api repos/jbookout/carr-system/pulls/$i; done"),
    ("for-literal-six-times-two-gh",
     "for n in 1 2 3 4 5 6; do gh pr view $n; gh pr checks $n; done"),
    ("xargs-gh",
     "gh pr list --json number -q '.[].number' | xargs -I{} gh pr view {} --json mergeable"),
    ("parallel-gh", "seq 1 50 | parallel gh run view {}"),
    ("while-read-gh", 'while read n; do gh issue view "$n"; done < ids.txt'),
    ("sleep-too-short",
     "for r in $(cat runs.txt); do gh run view $r; sleep 1; done"),
    ("multiline-loop",
     "for pr in $(cat prs.txt)\ndo\n  gh pr checks $pr\ndone"),
    ("bash-c-loop", "bash -c 'for i in $(seq 9); do gh api user; done'"),
    ("cd-prefix-loop",
     "cd /tmp && for b in $(git branch -r); do gh api repos/x/y/branches/$b; done"),
    ("paginate-inside-loop",
     "for r in $(cat repos.txt); do gh api --paginate repos/x/$r/pulls; done"),
    ("xargs-sh-c-short-sleep",
     "cat ids | xargs -n1 sh -c 'gh pr view $0; sleep 1'"),
    ("until-poll-no-sleep", "until gh run view 123 --exit-status; do :; done"),
    ("gh-inside-double-quoted-substitution",
     'for n in $(cat prs.txt); do echo "pr $n $(gh pr view $n --json state -q .state)"; done'),
    ("for-over-quoted-args", 'for n in "$@"; do gh pr view $n; done'),
    ("until-poll-two-gh",
     "until gh run view 1 --exit-status; do gh run list -L 5; done"),
]

# Backgrounded gh: two or more calls where one is sent to the background with
# a single `&` run at the same time, which is a parallel burst.
BG_DENY = [
    ("three-backgrounded",
     "gh pr view 1 --json state & gh pr view 2 --json state & gh pr view 3 --json state & wait"),
    ("two-backgrounded", "gh pr view 1 & gh pr view 2"),
    ("backgrounded-multiline", "gh run view 1 &\ngh run view 2 &\nwait"),
    ("backgrounded-pipeline", "gh pr list --json number | jq . & gh run list"),
]
BG_ALLOW = [
    ("and-chain", "gh pr view 1 && gh pr view 2"),
    ("one-backgrounded-then-sleep", "gh pr view 1 & sleep 3"),
    ("other-cmd-backgrounded", "npm run dev & gh pr view 1"),
    ("single-backgrounded", "gh run watch 123 &"),
    ("redirects-not-background", "gh pr view 1 2>&1 | head; gh pr view 2 &>/dev/null"),
    ("semicolon-chain", "gh pr view 1; gh pr view 2"),
    ("or-chain", "gh pr view 1 || gh pr view 2"),
    # From the replay: the `&` is a URL query separator after a substitution.
    ("ampersand-in-url-after-substitution",
     'gh api "repos/x/y/actions/runs?head_sha=$(gh pr view 1398 --json headRefOid '
     '-q .headRefOid)&per_page=50"'),
]

LOOP_ALLOW = [
    ("for-with-sleep-2",
     "for pr in $(gh pr list --json number -q '.[].number'); do gh pr view $pr; sleep 2; done"),
    ("until-poll-sleep-30", "until gh run view 123 --exit-status; do sleep 30; done"),
    ("until-poll-sleep-1m", "until gh pr checks 12; do sleep 1m; done"),
    ("single-gh", "gh pr view 12 --json state,mergedAt"),
    ("paginate", "gh api --paginate repos/jbookout/carr-system/pulls"),
    ("graphql", "gh api graphql -f query='query { viewer { login } }'"),
    ("loop-without-gh", "for f in ops/*.py; do python3 -m py_compile $f; done"),
    ("literal-two", "for pr in 1601 1602; do gh pr view $pr --json state; done"),
    ("literal-ten",
     "for n in 1 2 3 4 5 6 7 8 9 10; do gh pr view $n --json state; done"),
    ("literal-quoted-pairs",
     'for r in "doctorcre-app 113" "carr-system 1452"; do set -- ${=r}; '
     'gh pr view $2 -R jbookout/$1 --json state; done'),
    ("commit-message-mentions-loop",
     'git commit -m "for each pr do gh pr view; done in a loop"'),
    ("xargs-without-gh", 'ls | xargs grep -l "gh-pages"'),
    ("xargs-sleep-3", "cat ids | xargs -n1 sh -c 'gh pr view $0; sleep 3'"),
    ("heredoc-mentions-loop",
     "cat > notes.txt <<'EOF'\nfor pr in $(gh pr list); do gh pr view $pr; done\nEOF"),
    ("loop-greps-for-gh-text",
     'for x in $(git ls-files ops); do grep -n "admin\\|update-branch\\|gh pr merge" "$x"; done'),
    ("ghost-word","for g in ghost ghoul spirit wraith; do echo $g; done"),
]

# ---- class 2: release tick / shepherd -------------------------------------
TICK = "python3 ops/release-pipeline.py tick"


class held_lock:
    """Hold the pipeline lock exactly as ops/release-pipeline.py does while a
    tick runs: an exclusive flock on an open file, in THIS process, so the
    hook subprocess sees it held."""

    def __enter__(self):
        os.makedirs(os.path.dirname(LOCK), exist_ok=True)
        self.fh = open(LOCK, "a+")
        fcntl.flock(self.fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return self

    def __exit__(self, *exc):
        fcntl.flock(self.fh.fileno(), fcntl.LOCK_UN)
        self.fh.close()


def remove_lock():
    if os.path.exists(LOCK):
        os.remove(LOCK)


def ran_at():
    value = read_state().get("release_tick_ran_at")
    return value if isinstance(value, (int, float)) else None


def tick_ran(cmd=TICK, seconds=90, exit_code=0, background=False):
    """A tick the hook allowed, then saw finish after `seconds`: PreToolUse,
    the recorded start moved back by `seconds`, then PostToolUse (or
    PostToolUseFailure on a non-zero exit)."""
    code, err = pre(cmd)
    if code != 0:
        return code, err
    state = read_state()
    starts = state.get("release_tick_started") or {}
    for k in starts:
        starts[k] -= seconds
    state["release_tick_started"] = starts
    with open(STATE, "w", encoding="utf-8") as fh:
        json.dump(state, fh)
    payload = {"hook_event_name": "PostToolUse", "tool_name": "Bash",
               "tool_input": {"command": cmd}, "session_id": "selftest",
               "tool_response": {"stdout": "", "stderr": "", "interrupted": False,
                                 "exit_code": exit_code}}
    if background:
        payload["tool_input"]["run_in_background"] = True
        payload["tool_response"] = {"backgroundTaskId": "bg1"}
    elif exit_code:
        payload = {"hook_event_name": "PostToolUseFailure", "tool_name": "Bash",
                   "tool_input": {"command": cmd}, "session_id": "selftest",
                   "error": f"Exit code {exit_code}\nrelease-pipeline: worker lane failed"}
    run(payload)
    return 0, ""


def tick_cases(failures):
    # THE INCIDENT SHAPE (2026-10-06): three sequential hand ticks inside five
    # minutes, each finishing before the next. The lock is free between them.
    clear_state()
    remove_lock()
    code, _ = tick_ran(seconds=90)
    expect(failures, "sequential-tick-1-allowed", code, 0)
    expect(failures, "sequential-tick-1-stamped", 0 if ran_at() is not None else 1, 0)
    expect(failures, "probe-never-creates-lock", 0 if not os.path.exists(LOCK) else 1, 0)
    code, err = pre(TICK)
    expect(failures, "sequential-tick-2-inside-5min-denied", code, 2)
    if code == 2 and "real work" not in err:
        failures.append("sequential-tick-2-inside-5min-denied: reason does not say the last tick did real work")
    code, _ = pre("cd /Users/booko/carr-system && .venv/bin/python ops/release-pipeline.py tick --verbose")
    expect(failures, "sequential-tick-3-inside-5min-denied", code, 2)
    code, _ = pre("python3 ops/release-pipeline.py tick --lane report")
    expect(failures, "tick-lane-report-is-a-tick-denied", code, 2)
    code, _ = pre("out/orch/shepherd.sh")
    expect(failures, "shepherd-inside-tick-window-denied", code, 2)
    set_state(release_tick_ran_at=time.time() - 301)
    code, _ = pre(TICK)
    expect(failures, "tick-after-5min-allowed", code, 0)

    # Shepherd's shared window: a backgrounded shepherd launch counts at once,
    # so a hand tick in its gap between its own ticks is refused.
    clear_state()
    tick_ran("out/orch/shepherd.sh", seconds=0, background=True)
    expect(failures, "background-shepherd-stamped", 0 if ran_at() is not None else 1, 0)
    code, _ = pre(TICK)
    expect(failures, "hand-tick-in-shepherd-gap-denied", code, 2)

    # 2026-10-12 case (b): a tick a permission check denied never reaches
    # PostToolUse, so it leaves no stamp and the retry runs.
    clear_state()
    code, _ = pre(TICK)                     # allowed here, then denied downstream
    expect(failures, "denied-tick-pre-allowed", code, 0)
    expect(failures, "denied-tick-leaves-no-stamp", 0 if ran_at() is None else 1, 0)
    code, _ = pre(TICK)
    expect(failures, "retry-after-denied-tick-allowed", code, 0)

    # 2026-10-12 case (c): a tick that failed in seconds does not block its retry.
    clear_state()
    tick_ran(seconds=3, exit_code=1)
    expect(failures, "fast-failed-tick-leaves-no-stamp", 0 if ran_at() is None else 1, 0)
    code, _ = pre(TICK)
    expect(failures, "retry-after-fast-failure-allowed", code, 0)
    clear_state()
    tick_ran(seconds=120, exit_code=1)
    expect(failures, "slow-failed-tick-leaves-no-stamp", 0 if ran_at() is None else 1, 0)
    clear_state()
    tick_ran(seconds=20, exit_code=0)
    expect(failures, "under-60s-success-leaves-no-stamp", 0 if ran_at() is None else 1, 0)
    code, _ = pre(TICK)
    expect(failures, "retry-after-short-tick-allowed", code, 0)
    # A stale pre-2026-10-12 stamp written at PreToolUse refuses nothing.
    set_state(release_tick_at=time.time() - 10)
    code, _ = pre(TICK)
    expect(failures, "legacy-release-tick-at-ignored", code, 0)

    # While a tick holds the lock, every tick shape and shepherd is refused,
    # whatever the window says.
    clear_state()
    with held_lock():
        code, err = pre(TICK)
        expect(failures, "tick-while-lock-held-denied", code, 2)
        if code == 2 and "running" not in err.lower():
            failures.append("tick-while-lock-held-denied: reason does not say a tick is running")
        if code == 2 and "lock" not in err.lower():
            failures.append("tick-while-lock-held-denied: reason does not name the lock")
        code, _ = pre("out/orch/shepherd.sh")
        expect(failures, "shepherd-while-lock-held-denied", code, 2)
        code, _ = pre("python3 /tmp/copy/release-pipeline-v2.py tick")
        expect(failures, "copied-tick-while-lock-held-denied", code, 2)
        code, _ = pre("cd ~/carr-system; timeout 1500 python3 ops/release-pipeline.py tick --lane worker")
        expect(failures, "timeout-wrapped-tick-while-lock-held-denied", code, 2)
        # Help and report are not ticks, even while one is running.
        for name, cmd in (("tick-help", "python3 ops/release-pipeline.py tick --help"),
                          ("tick-h", "python3 ops/release-pipeline.py tick -h"),
                          ("help-before-tick", "python3 ops/release-pipeline.py --help tick"),
                          ("report", "python3 ops/release-pipeline.py report --date 2026-10-12"),
                          ("shepherd-report", "out/orch/shepherd.sh report"),
                          ("shepherd-help", "out/orch/shepherd.sh --help")):
            code, _ = pre(cmd)
            expect(failures, f"{name}-while-lock-held-allowed", code, 0)
    expect(failures, "probe-never-deletes-lock", 0 if os.path.exists(LOCK) else 1, 0)
    code, _ = pre(TICK)
    expect(failures, "tick-after-lock-released-allowed", code, 0)

    # --help / -h / report never stamp, even after a long successful run
    # (2026-10-12 case (a)).
    for name, cmd in (("tick-help", "python3 ops/release-pipeline.py tick --help"),
                      ("tick-h", ".venv/bin/python ops/release-pipeline.py tick -h | head"),
                      ("report", "python3 ops/release-pipeline.py report"),
                      ("shepherd-report", "out/orch/shepherd.sh report")):
        clear_state()
        tick_ran(cmd, seconds=120)
        never = ran_at() is None and not read_state().get("release_tick_started")
        expect(failures, f"{name}-never-stamped", 0 if never else 1, 0)
        code, _ = pre(TICK)
        expect(failures, f"tick-after-{name}-allowed", code, 0)
    # `report` only counts in shepherd's subcommand position.
    clear_state()
    tick_ran("out/orch/shepherd.sh --lane report", seconds=0, background=True)
    expect(failures, "shepherd-report-not-subcommand-stamped", 0 if ran_at() is not None else 1, 0)

    clear_state()
    remove_lock()
    code, _ = pre("python3 ops/release-pipeline.py status")
    expect(failures, "non-tick-subcommand-allowed", code, 0)
    code, _ = pre('grep -n "def tick" ops/release-pipeline.py')
    expect(failures, "grep-for-tick-allowed", code, 0)
    code, _ = pre("cat out/orch/shepherd.sh")
    expect(failures, "reading-shepherd-allowed", code, 0)
    code, _ = pre("cp shepherd.sh shepherd.sh.prev && sed -i '' 's|zsh shepherd.sh|zsh new.sh|' cycle.sh")
    expect(failures, "editing-shepherd-allowed", code, 0)
    code, _ = pre("zsh -n out/orch/shepherd.sh && echo syntax-ok")
    expect(failures, "syntax-checking-shepherd-allowed", code, 0)
    code, _ = pre('.venv/bin/python ops/release-pipeline.py clear-failed --lane worker '
                  '--reason "the tick failed on a stale lock"')
    expect(failures, "clear-failed-mentioning-tick-allowed", code, 0)
    code, _ = pre('gh pr comment 1622 --body "verified by running\n'
                  'python3 ops/release-pipeline.py tick --dry-run"')
    expect(failures, "comment-body-mentioning-tick-allowed", code, 0)


# ---- class 3: rate-limit cooldown -----------------------------------------
def cooldown_cases(failures):
    clear_state()
    post("gh pr list -L 50", exit_code=1,
         stderr="HTTP 403: API rate limit exceeded for user ID 64207374.")
    expect(failures, "403-recorded",
           0 if isinstance(read_state().get("rate_limited_at"), (int, float)) else 1, 0)
    code, err = pre("gh pr view 12")
    expect(failures, "gh-during-cooldown-denied", code, 2)
    if code == 2 and "rate_limit" not in err:
        failures.append("gh-during-cooldown-denied: reason does not name gh api rate_limit")
    code, _ = pre("gh api graphql -f query='query { viewer { login } }'")
    expect(failures, "graphql-during-cooldown-denied", code, 2)
    code, _ = pre("gh api rate_limit")
    expect(failures, "rate-limit-probe-allowed", code, 0)
    code, _ = pre("gh api rate_limit --jq .resources.core")
    expect(failures, "rate-limit-probe-jq-allowed", code, 0)
    code, _ = pre(TICK)
    expect(failures, "tick-during-cooldown-denied", code, 2)
    code, _ = pre("ls -la out/")
    expect(failures, "non-gh-during-cooldown-allowed", code, 0)
    code, _ = pre('grep -rn "gh pr view" ops/')
    expect(failures, "grep-for-gh-text-during-cooldown-allowed", code, 0)
    code, _ = pre("gh api rate_limit && gh pr view 12")
    expect(failures, "probe-plus-real-call-during-cooldown-denied", code, 2)

    set_state(rate_limited_at=time.time() - 901)
    code, _ = pre("gh pr view 12")
    expect(failures, "cooldown-expired-allowed", code, 0)
    set_state(rate_limited_at=time.time() - 600)
    code, _ = pre("gh pr view 12")
    expect(failures, "cooldown-ten-minutes-in-denied", code, 2)

    clear_state()
    post("python3 ops/release-pipeline.py tick", event="PostToolUseFailure",
         error="Exit code 1\ngh: You have exceeded a secondary rate limit. Please wait a few minutes.")
    expect(failures, "secondary-limit-from-tick-recorded",
           0 if "rate_limited_at" in read_state() else 1, 0)

    clear_state()
    post("gh api repos/x/y", event="PostToolUseFailure",
         error="Exit code 1\nHTTP 403: API rate limit exceeded for installation")
    expect(failures, "403-from-failure-event-recorded",
           0 if "rate_limited_at" in read_state() else 1, 0)

    clear_state()
    post("cat hooks/github-burst-guard.py",
         stdout='PATTERN = "API rate limit exceeded" | "secondary rate limit"')
    expect(failures, "reading-the-guard-source-not-recorded",
           0 if "rate_limited_at" not in read_state() else 1, 0)

    clear_state()
    post("gh pr view 12", stdout='{"state":"OPEN"}')
    expect(failures, "clean-output-not-recorded",
           0 if "rate_limited_at" not in read_state() else 1, 0)

    # A successful command whose OUTPUT merely mentions the phrase (a diff of
    # this very guard, a PR body, a run log) must not start a lockout.
    clear_state()
    post("gh pr diff 1630",
         stdout='+    stderr="HTTP 403: API rate limit exceeded for user ID 1."\n'
                '+RATE = "secondary rate limit"\n')
    expect(failures, "successful-diff-mentioning-phrase-not-recorded",
           0 if "rate_limited_at" not in read_state() else 1, 0)
    # Piped gh exits 0 even when gh itself got a 403. A line that STARTS with
    # gh's own error prefix still records; diff and indented lines do not.
    clear_state()
    post("gh pr list | head", exit_code=0,
         stdout="HTTP 403: You have exceeded a secondary rate limit. Please wait.\n")
    expect(failures, "piped-exit-zero-line-start-403-recorded",
           0 if "rate_limited_at" in read_state() else 1, 0)
    clear_state()
    post("gh pr list | head",
         stdout="GraphQL: API rate limit exceeded for user ID 1.\n")
    expect(failures, "piped-no-exit-code-graphql-line-start-recorded",
           0 if "rate_limited_at" in read_state() else 1, 0)
    clear_state()
    post("gh pr view 1509 --json state",
         stdout="GraphQL: API rate limit already exceeded for user ID 1.\n")
    expect(failures, "graphql-already-exceeded-recorded",
           0 if "rate_limited_at" in read_state() else 1, 0)
    clear_state()
    post("gh pr diff 1630", exit_code=0,
         stdout="+HTTP 403: secondary rate limit\n-gh: API rate limit exceeded\n")
    expect(failures, "diff-line-plus-prefixed-not-recorded",
           0 if "rate_limited_at" not in read_state() else 1, 0)
    clear_state()
    post("gh pr view 1630 --json body -q .body", exit_code=0,
         stdout="Notes:\n    gh: API rate limit exceeded\n")
    expect(failures, "indented-body-line-not-recorded",
           0 if "rate_limited_at" not in read_state() else 1, 0)
    clear_state()
    post("gh pr view 1630 --json body -q .body", exit_code=0,
         stdout="HTTP 404: secondary rate limit docs moved\n")
    expect(failures, "exit-zero-404-line-not-recorded",
           0 if "rate_limited_at" not in read_state() else 1, 0)
    clear_state()
    post("gh run view 99 --log", event="PostToolUseFailure",
         error="Exit code 1\nstep 3: the docs say a secondary rate limit may apply\n"
               "some later failure")
    expect(failures, "failed-call-mentioning-phrase-off-error-line-not-recorded",
           0 if "rate_limited_at" not in read_state() else 1, 0)
    clear_state()
    post("gh api repos/x/y/pulls", event="PostToolUseFailure",
         error="Exit code 1\nHTTP 403: You have exceeded a secondary rate limit. "
               "Please wait a few minutes before you try again.")
    expect(failures, "failed-call-secondary-403-recorded",
           0 if "rate_limited_at" in read_state() else 1, 0)
    clear_state()
    post("gh api repos/x/y/pulls", exit_code=1,
         stderr="HTTP 429: secondary rate limit (https://api.github.com/graphql)")
    expect(failures, "nonzero-exit-code-429-recorded",
           0 if "rate_limited_at" in read_state() else 1, 0)
    clear_state()
    post("gh api repos/x/nope", event="PostToolUseFailure",
         error="Exit code 1\nHTTP 404: Not Found (https://api.github.com/repos/x/nope)")
    expect(failures, "failed-call-unrelated-404-not-recorded",
           0 if "rate_limited_at" not in read_state() else 1, 0)


def pending_install_cases(failures):
    """Between merge and `config-as-code install` the new wiring is absent
    from live settings; gate-integrity must read that as PENDING INSTALL, not
    as a GATE INTEGRITY FAILURE in every session on the machine."""
    import importlib.util
    import shutil
    spec = importlib.util.spec_from_file_location(
        "gate_integrity", os.path.join(REPO, "hooks", "gate-integrity.py"))
    gi = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gi)
    work = tempfile.mkdtemp(prefix="burst-guard-pending-")
    try:
        gi.PENDING_INSTALL_STAMP_DIR = work
        errs = ["expected 1 exact PreToolUse/Bash hook github-burst-guard.py; found 0",
                "expected 1 exact PostToolUse/Bash hook github-burst-guard.py; found 0"]
        real, pending = gi.split_pending_install(errs, {})
        if pending != ["github-burst-guard.py"] or real:
            failures.append(f"pending-install: not installed yet should be pending, got {real} {pending}")
    finally:
        shutil.rmtree(work, ignore_errors=True)


def expect(failures, name, got, want):
    if got != want:
        failures.append(f"{name}: exit {got}, expected {want}")


def main():
    if not os.path.exists(HOOK):
        print(f"FAIL: {HOOK} does not exist")
        return 1
    failures = []
    for name, cmd in LOOP_DENY:
        clear_state()
        code, err = pre(cmd)
        expect(failures, f"loop-deny/{name}", code, 2)
        if code == 2 and "sleep" not in err:
            failures.append(f"loop-deny/{name}: reason does not name the sleep alternative")
    for name, cmd in LOOP_ALLOW:
        clear_state()
        code, _ = pre(cmd)
        expect(failures, f"loop-allow/{name}", code, 0)

    for name, cmd in BG_DENY:
        clear_state()
        code, err = pre(cmd)
        expect(failures, f"bg-deny/{name}", code, 2)
        if code == 2 and "&" not in err:
            failures.append(f"bg-deny/{name}: reason does not name the backgrounding")
    for name, cmd in BG_ALLOW:
        clear_state()
        code, _ = pre(cmd)
        expect(failures, f"bg-allow/{name}", code, 0)

    tick_cases(failures)
    cooldown_cases(failures)
    pending_install_cases(failures)

    # Plumbing: non-Bash tools pass, malformed input fails open, Codex shape works.
    clear_state()
    code, _ = run({"hook_event_name": "PreToolUse", "tool_name": "Read",
                   "tool_input": {"file_path": "/x"}})
    expect(failures, "non-bash-tool-allowed", code, 0)
    code, _ = run(None, raw="{not json")
    expect(failures, "malformed-input-fails-open", code, 0)
    code, _ = run({"hook_event_name": "PreToolUse", "tool_name": "exec_command",
                   "tool_input": {"cmd": "seq 9 | xargs -I{} gh run view {}"}})
    expect(failures, "codex-exec-command-shape-denied", code, 2)
    with open(STATE, "w", encoding="utf-8") as fh:
        fh.write("garbage")
    code, _ = pre("gh pr view 1")
    expect(failures, "corrupt-state-fails-open", code, 0)

    total = len(LOOP_DENY) + len(LOOP_ALLOW) + len(BG_DENY) + len(BG_ALLOW) + 90
    if failures:
        print(f"FAIL github-burst-guard: {len(failures)} failure(s)")
        for f in failures:
            print("  -", f)
        return 1
    print(f"PASS github-burst-guard: ~{total} cases")
    return 0


if __name__ == "__main__":
    sys.exit(main())
