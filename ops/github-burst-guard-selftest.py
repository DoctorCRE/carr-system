#!/usr/bin/env python3
"""Exercise GitHub burst, merge, and rate-limit safeguards without network calls."""
import json
import os
import subprocess
import sys
import tempfile
import time

from pathlib import Path
sys.path.append(str(Path(__file__).resolve().parents[1] / "lib"))
from selftest_harness import temporary_directory

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOOK = os.path.join(REPO, "hooks", "github-burst-guard.py")

TMP = temporary_directory(prefix="github-burst-guard-")
STATE = os.path.join(TMP, "state.json")
ENV = dict(os.environ,
           CARR_GITHUB_BURST_STATE=STATE,
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
     "for i in 1 2 3 4 5 6 7 8 9 10 11; do gh api repos/DoctorCRE/carr-system/pulls/$i; done"),
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
    ("paginate", "gh api --paginate repos/DoctorCRE/carr-system/pulls"),
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
     'for x in $(git ls-files ops); do grep -n "admin\\|update-branch\\|gh pr view" "$x"; done'),
    ("ghost-word","for g in ghost ghoul spirit wraith; do echo $g; done"),
]

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
    post("gh pr list", event="PostToolUseFailure",
         error="Exit code 1\ngh: You have exceeded a secondary rate limit. Please wait a few minutes.")
    expect(failures, "secondary-limit-from-gh-recorded",
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


def expect(failures, name, got, want):
    if got != want:
        failures.append(f"{name}: exit {got}, expected {want}")


def main():
    if not os.path.exists(HOOK):
        print(f"FAIL: {HOOK} does not exist")
        return 1
    failures = []
    sha = 'a' * 40
    merge_cases = [
        ('direct merge', 'gh pr merge 12 --squash', 2),
        ('missing pin', 'gh pr merge 12 --repo example/repo --auto --squash', 2),
        ('missing auto', 'gh pr merge 12 --repo example/repo --squash --match-head-commit ' + sha, 2),
        ('pinned auto', 'gh pr merge 12 --repo example/repo --auto --squash --match-head-commit ' + sha, 0),
        ('admin bypass', 'gh pr merge 12 --repo example/repo --auto --squash --match-head-commit ' + sha + ' --admin', 2),
        ('approved entry point', 'bash tools/approve-merge.sh example/repo 12 ' + sha, 0),
        ('branch update', 'gh pr update-branch 12 --repo example/repo', 0),
        ('REST merge', 'gh api -X PUT repos/example/repo/pulls/12/merge', 2),
        ('REST branch update', 'gh api -X PUT repos/example/repo/pulls/12/update-branch', 0),
        ('GraphQL merge', "gh api graphql -f query='mutation{mergePullRequest(input:{pullRequestId:\"x\"}){clientMutationId}}'", 2),
    ]
    for name, cmd, want in merge_cases:
        clear_state()
        code, _ = pre(cmd)
        expect(failures, 'merge/' + name, code, want)
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

    cooldown_cases(failures)

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

    total = len(LOOP_DENY) + len(LOOP_ALLOW) + len(BG_DENY) + len(BG_ALLOW) + len(merge_cases) + 90
    if failures:
        print(f"FAIL github-burst-guard: {len(failures)} failure(s)")
        for f in failures:
            print("  -", f)
        return 1
    print(f"PASS github-burst-guard: ~{total} cases")
    return 0


if __name__ == "__main__":
    sys.exit(main())
