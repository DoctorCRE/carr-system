#!/usr/bin/env python3
"""
loose-work-gate-selftest.py — acceptance test for hooks/loose-work-gate.py,
written before the gate (rule e65efc68).

WHY THE GATE EXISTS, in Joe's words on 2026-08-14: "why the fuck am i having to
tell every session to commit its work? this is insane."

He was right, and the mechanism is specific. Detection already existed —
tools/health-check.py reports loose tracked files — but it runs in the NIGHTLY
CHAIN, once a day, into a report somebody has to read, long after the session
that left the work has ended. So the human noticing was the fastest detector on
the machine.

Five hooks already run at Stop: conduct, completion-evidence, ledger-sweep,
context-handoff, scheduled-run-record. Every one of them checks what a session
SAYS. None checks what it LEFT. This is that one.

WHAT IT MUST GET RIGHT, which is mostly about what it must NOT do:

  1. ONLY THIS SESSION'S FILES. Several sessions share one working tree, and on
     2026-08-14 it held three loose files belonging to three different sessions.
     A gate that flagged all of them would make every session responsible for
     every other session's mess — which is how the nine-session broadcast
     happened, and strictly worse than the problem. The transcript names what
     THIS session wrote; nothing else is its business.

  2. SILENCE WHEN THERE IS NOTHING. This runs at the end of every session. A
     gate that speaks when there is nothing to say gets muted, and then it is
     not a gate.

  3. FAIL OPEN, ALWAYS. A missing transcript, an unreadable file, git not
     answering — none of it may strand a session. The failure mode of an
     over-eager Stop hook is a session that cannot end.

  4. AN ESCAPE THAT WORKS. Leaving work loose is sometimes correct: handing a
     tree to another session, or parking mid-investigation. Same idiom as the
     gates beside it.

Runs with no repo state of its own: every case builds a throwaway git repo and a
throwaway transcript.
"""

import json
import os
import subprocess
import sys
import tempfile

# The one scrubber (ops/git_env.py), not a local copy. GIT_DIR is exported by
# every git hook and overrides cwd for any child git process — the
# 2026-08-14 incident where fixture commits landed on live main is the
# reason git() below now delegates to fixture_env() instead of hand-rolling
# its own (incomplete) list of GIT_* variables to drop.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from git_env import fixture_env  # noqa: E402

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
GATE = os.path.join(REPO, "hooks", "loose-work-gate.py")

sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, "lib"))
from selftest_harness import Checker  # noqa: E402

CHECKER = Checker()
failures = CHECKER.failures
check = CHECKER.check


def git(repo, *args):
    """git in the fixture, with any inherited GIT_* location vars REMOVED.

    Removed, not blanked. The first version set GIT_DIR="" and GIT_WORK_TREE=""
    and every git call died with `fatal: not a git repository: ''` — an empty
    GIT_DIR is not an unset one, it is a path that does not exist. The fixture
    repo was therefore never created, and ELEVEN of the fifteen checks below went
    green against it: with no repo, the gate correctly failed open and returned 0,
    which is the same 0 most of these cases assert. A test suite passing because
    its fixture is absent is the exact failure this file exists to catch in the
    gate, so it is worth the paragraph.

    Removal now comes from fixture_env() (ops/git_env.py) rather than a local
    list: that list here used to cover seven variables and git consults ten,
    so this hand-rolled version was itself an instance of the class of bug it
    was written to describe.
    """
    env = fixture_env()
    p = subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True,
                       env=env)
    if p.returncode != 0 and args and args[0] in ("init", "add", "commit", "config"):
        raise SystemExit(f"fixture setup failed: git {' '.join(args)}\n{p.stderr}")
    return p


def make_repo(tmp):
    repo = os.path.join(tmp, "repo")
    os.makedirs(repo)
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.email", "t@example.invalid")
    git(repo, "config", "user.name", "t")
    with open(os.path.join(repo, "tracked.txt"), "w") as fh:
        fh.write("seed\n")
    with open(os.path.join(repo, "other.txt"), "w") as fh:
        fh.write("seed\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "seed")
    return repo


def transcript(tmp, written_paths, name="t.jsonl"):
    """A transcript in which this session wrote each of `written_paths`."""
    p = os.path.join(tmp, name)
    with open(p, "w") as fh:
        fh.write(json.dumps({"type": "user", "message": {"content": "go"}}) + "\n")
        for path in written_paths:
            fh.write(json.dumps({
                "type": "assistant",
                "message": {"content": [
                    {"type": "tool_use", "name": "Write",
                     "input": {"file_path": path, "content": "x"}}]},
            }) + "\n")
    return p


def spoken(stdout):
    """The announcement text this gate emitted, or "" if it said nothing.

    DEMOTED 2026-08-23 (gates-audit council, Joe's Stop-gate rationing). This
    gate used to exit 2, which REOPENS the turn and costs a whole extra
    assistant message. It now announces: the finding rides into context without
    forcing another turn. So "caught" means an announcement was emitted, and an
    exit 2 from here is itself the regression — the same inversion
    ops/chat-lint-gate-selftest.py made when that gate was demoted on
    2026-08-16, for the same reason.
    """
    try:
        emitted = json.loads(stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return ""
    if "decision" in emitted:               # a block wearing an announce's clothes
        return ""
    return (emitted.get("hookSpecificOutput") or {}).get("additionalContext") or ""


def run(repo, transcript_path, env=None):
    payload = json.dumps({"session_id": "s1", "transcript_path": transcript_path,
                          "cwd": repo})
    # hooks/loose-work-gate.py shells out to git itself (status, remote,
    # rev-list) with cwd=repo and no scrub of its own — fixture_env() here is
    # what keeps THOSE calls confined to the fixture too.
    e = dict(fixture_env(), CARR_LOOSE_WORK_REPO=repo)
    if env:
        e.update(env)
    p = subprocess.run([sys.executable, GATE], input=payload, capture_output=True,
                       text=True, env=e, timeout=60)
    # `out` is what the gate SAID, in whichever register. rc is how much it
    # cost: 0 is an announcement, 2 would be a reopened turn.
    return p.returncode, (spoken(p.stdout) + p.stderr)


print("\nloose-work-gate — a session does not end leaving its own work loose")

if not os.path.exists(GATE):
    print(f"  FAIL the gate does not exist at {GATE}")
    sys.exit(1)

# 1. THE CORE CASE: this session wrote a tracked file and left it modified.
with tempfile.TemporaryDirectory() as tmp:
    repo = make_repo(tmp)
    target = os.path.join(repo, "tracked.txt")
    with open(target, "w") as fh:
        fh.write("modified by this session\n")
    rc, out = run(repo, transcript(tmp, [target]))
    check("a session that left its own tracked file modified is told", bool(out),
          f"rc={rc} said nothing")
    check("...and it is told WITHOUT the turn being reopened", rc == 0, f"rc={rc}")
    check("the refusal names the file", "tracked.txt" in out, out[:200])
    check("the refusal says how to land it", "worktree" in out or "commit" in out,
          out[:200])
    check("the refusal names its escape hatch", "CARR_ALLOW_LOOSE_WORK" in out,
          out[:200])

# 2. ANOTHER SESSION'S LOOSE FILE IS NOT THIS SESSION'S PROBLEM.
#    The failure this prevents is every session being made responsible for a
#    shared tree, which is how the nine-session broadcast happened.
with tempfile.TemporaryDirectory() as tmp:
    repo = make_repo(tmp)
    mine = os.path.join(repo, "tracked.txt")
    theirs = os.path.join(repo, "other.txt")
    with open(theirs, "w") as fh:                 # somebody else's edit
        fh.write("another session was here\n")
    rc, out = run(repo, transcript(tmp, [mine]))  # this session wrote a DIFFERENT file
    check("another session's loose file does not stop this one", rc == 0, f"rc={rc}")
    check("...and is not even mentioned", "other.txt" not in out, out[:200])

# 3. SILENCE WHEN THERE IS NOTHING TO SAY.
with tempfile.TemporaryDirectory() as tmp:
    repo = make_repo(tmp)
    target = os.path.join(repo, "tracked.txt")
    with open(target, "w") as fh:
        fh.write("modified\n")
    git(repo, "add", "tracked.txt")
    git(repo, "commit", "-q", "-m", "committed it")
    rc, out = run(repo, transcript(tmp, [target]))
    check("a session that committed its work ends silently", rc == 0, f"rc={rc}")
    check("...and prints nothing at all", out.strip() == "", repr(out[:120]))

# 4. THE ESCAPE HATCH.
with tempfile.TemporaryDirectory() as tmp:
    repo = make_repo(tmp)
    target = os.path.join(repo, "tracked.txt")
    with open(target, "w") as fh:
        fh.write("deliberately parked\n")
    rc, _ = run(repo, transcript(tmp, [target]), env={"CARR_ALLOW_LOOSE_WORK": "1"})
    check("CARR_ALLOW_LOOSE_WORK=1 lets a session park work deliberately", rc == 0,
          f"rc={rc}")

# 5. FAIL OPEN ON EVERY BROKEN INPUT. An over-eager Stop hook strands sessions.
with tempfile.TemporaryDirectory() as tmp:
    repo = make_repo(tmp)
    rc, _ = run(repo, os.path.join(tmp, "does-not-exist.jsonl"))
    check("a missing transcript never blocks", rc == 0, f"rc={rc}")

    bad = os.path.join(tmp, "bad.jsonl")
    with open(bad, "w") as fh:
        fh.write("{not json\n")
    rc, _ = run(repo, bad)
    check("a malformed transcript never blocks", rc == 0, f"rc={rc}")

    p = subprocess.run([sys.executable, GATE], input="{not json",
                       capture_output=True, text=True, timeout=60)
    check("a malformed payload never blocks", p.returncode == 0, f"rc={p.returncode}")

    rc, _ = run(os.path.join(tmp, "not-a-repo"), transcript(tmp, ["/tmp/x.txt"]))
    check("a path that is not a git repo never blocks", rc == 0, f"rc={rc}")

# 6. It must not re-fire while the session is already stopping.
with tempfile.TemporaryDirectory() as tmp:
    repo = make_repo(tmp)
    target = os.path.join(repo, "tracked.txt")
    with open(target, "w") as fh:
        fh.write("modified\n")
    payload = json.dumps({"session_id": "s1", "stop_hook_active": True,
                          "transcript_path": transcript(tmp, [target]), "cwd": repo})
    p = subprocess.run([sys.executable, GATE], input=payload, capture_output=True,
                       text=True, timeout=60,
                       env={**os.environ, "CARR_LOOSE_WORK_REPO": repo})
    check("it does not re-fire once the session is already stopping",
          p.returncode == 0, f"rc={p.returncode}")

# 7. A file written OUTSIDE the repo is not this gate's business.
with tempfile.TemporaryDirectory() as tmp:
    repo = make_repo(tmp)
    outside = os.path.join(tmp, "scratch.txt")
    with open(outside, "w") as fh:
        fh.write("scratchpad work\n")
    rc, out = run(repo, transcript(tmp, [outside]))
    check("a write outside the repo is ignored", rc == 0, f"rc={rc}: {out[:120]}")

# ════════════════════════════════════════════════════════════════════════════
# THE SECOND HALF: committed, but never pushed.
#
# Rule bc9188b4 says it in one line — "PUSH WHAT YOU COMMIT. Committed-but-
# unpushed is the same drift as deployed-but-uncommitted." Until these cases
# existed the gate stopped at uncommitted, and the ungated half cost real time
# on 2026-08-14: a correct, verified fix for a red pull request sat committed on
# a local branch for about an hour. The pull request stayed red and kept emailing
# Joe, and his read was that a session had fixed it and the fix had not worked.
#
# Reachability matters more than any single upstream: the test is whether the
# commit is reachable from ANY remote ref, so pushing under a different branch
# name still counts as landed. That is how the same fix reached origin that day.
# ════════════════════════════════════════════════════════════════════════════


def committing_transcript(tmp, name="tc.jsonl"):
    """A transcript in which this session ran `git commit` and wrote nothing."""
    p = os.path.join(tmp, name)
    with open(p, "w") as fh:
        fh.write(json.dumps({"type": "user", "message": {"content": "go"}}) + "\n")
        fh.write(json.dumps({
            "type": "assistant",
            "message": {"content": [
                {"type": "tool_use", "name": "Bash",
                 "input": {"command": "git add ops/x.py && git commit -m 'fix'"}}]},
        }) + "\n")
    return p


def add_remote(repo, tmp, name="origin"):
    bare = os.path.join(tmp, f"{name}.git")
    git(repo, "init", "--bare", "-q", "-b", "main", bare) if False else None
    subprocess.run(["git", "init", "--bare", "-q", "-b", "main", bare],
                   capture_output=True, check=True, env=fixture_env())
    git(repo, "remote", "add", name, bare)
    return bare


# 8. THE CASE THIS EXISTS FOR: the session committed and never pushed.
with tempfile.TemporaryDirectory() as tmp:
    repo = make_repo(tmp)
    add_remote(repo, tmp)
    git(repo, "push", "-q", "origin", "main")
    with open(os.path.join(repo, "tracked.txt"), "w") as fh:
        fh.write("a real fix\n")
    git(repo, "add", "tracked.txt")
    git(repo, "commit", "-q", "-m", "the fix nobody could see")
    rc, out = run(repo, committing_transcript(tmp))
    check("a session that committed and never pushed is told", bool(out),
          f"rc={rc} said nothing")
    check("...and that one does not reopen the turn either", rc == 0, f"rc={rc}")
    check("...and the refusal says the commit exists on no other machine",
          "unpushed" in out.lower() or "no other machine" in out.lower(), out[:200])

# 9. Pushed under a DIFFERENT branch name still counts as landed.
with tempfile.TemporaryDirectory() as tmp:
    repo = make_repo(tmp)
    add_remote(repo, tmp)
    git(repo, "push", "-q", "origin", "main")
    with open(os.path.join(repo, "tracked.txt"), "w") as fh:
        fh.write("a real fix\n")
    git(repo, "add", "tracked.txt")
    git(repo, "commit", "-q", "-m", "the fix")
    git(repo, "push", "-q", "origin", "HEAD:refs/heads/some-other-name")
    rc, out = run(repo, committing_transcript(tmp))
    check("a commit pushed under another branch name is NOT flagged", rc == 0,
          f"rc={rc}: {out[:200]}")

# 10. A session that committed NOTHING is not this half's business.
with tempfile.TemporaryDirectory() as tmp:
    repo = make_repo(tmp)
    add_remote(repo, tmp)
    git(repo, "push", "-q", "origin", "main")
    # Someone else's unpushed commit sits here; this session only read.
    with open(os.path.join(repo, "other.txt"), "w") as fh:
        fh.write("another session's commit\n")
    git(repo, "add", "other.txt")
    git(repo, "commit", "-q", "-m", "not mine")
    rc, out = run(repo, transcript(tmp, []))
    check("another session's unpushed commit is not flagged at my Stop", rc == 0,
          f"rc={rc}: {out[:200]}")

# 11. The parking escape hatch covers this half too.
with tempfile.TemporaryDirectory() as tmp:
    repo = make_repo(tmp)
    add_remote(repo, tmp)
    git(repo, "push", "-q", "origin", "main")
    with open(os.path.join(repo, "tracked.txt"), "w") as fh:
        fh.write("wip\n")
    git(repo, "add", "tracked.txt")
    git(repo, "commit", "-q", "-m", "wip")
    rc, out = run(repo, committing_transcript(tmp),
                  env={"CARR_ALLOW_LOOSE_WORK": "1"})
    check("deliberate parking still suppresses it", rc == 0, f"rc={rc}: {out[:120]}")

# 12. A repository with NO remote at all fails open rather than nagging.
with tempfile.TemporaryDirectory() as tmp:
    repo = make_repo(tmp)
    with open(os.path.join(repo, "tracked.txt"), "w") as fh:
        fh.write("local only\n")
    git(repo, "add", "tracked.txt")
    git(repo, "commit", "-q", "-m", "local")
    rc, out = run(repo, committing_transcript(tmp))
    check("a repository with no remote is never nagged about pushing", rc == 0,
          f"rc={rc}: {out[:200]}")

# 13. COUNCIL RECOMMENDATION 1 (2026-08-23): the gate judges the tree the session
# is STANDING IN, not the canonical checkout every hook is loaded from.
#
# hooksPath is absolute, so REPO inside the gate is always ~/carr-system whatever
# worktree the session lives in. That made this gate hand one session another
# session's unpushed commit: on 2026-08-23 a worktree that was clean and exactly
# in sync with origin was told it had "committed 1 change and never pushed",
# because canonical's HEAD (9c23a9ca, someone else's work) had not been pushed.
# The session cannot act on that report and cannot make it stop.
with tempfile.TemporaryDirectory() as tmp:
    canonical = make_repo(tmp)
    add_remote(canonical, tmp)
    git(canonical, "push", "-q", "origin", "main")

    # A registered worktree of that checkout, in the real location run.sh uses.
    wt = os.path.join(canonical, ".claude", "worktrees", "alpha")
    os.makedirs(os.path.dirname(wt), exist_ok=True)
    git(canonical, "worktree", "add", "-q", "-b", "alpha", wt)
    git(wt, "push", "-q", "origin", "alpha")

    # Canonical is left dirty AND unpushed — another session's in-flight work.
    with open(os.path.join(canonical, "tracked.txt"), "w") as fh:
        fh.write("another session's commit\n")
    git(canonical, "add", "tracked.txt")
    git(canonical, "commit", "-q", "-m", "not this session's work")

    rc, out = run(wt, committing_transcript(tmp),
                  env={"CARR_LOOSE_WORK_REPO": canonical})
    check("a clean, pushed worktree is not blamed for canonical's unpushed commit",
          rc == 0, f"rc={rc}: {out[:200]}")

    # The same session, genuinely leaving its OWN commit loose, is still caught:
    # scoping the gate must not turn it off. (Caught now means ANNOUNCED — this
    # gate was demoted 2026-08-23 and an exit 2 is itself a regression.)
    with open(os.path.join(wt, "tracked.txt"), "w") as fh:
        fh.write("this session's real fix\n")
    git(wt, "add", "tracked.txt")
    git(wt, "commit", "-q", "-m", "the fix nobody could see")
    rc, out = run(wt, committing_transcript(tmp),
                  env={"CARR_LOOSE_WORK_REPO": canonical})
    check("...but its own unpushed commit is still announced",
          rc == 0 and ("unpushed" in out.lower() or "no other machine" in out.lower()),
          f"rc={rc}: {out[:200]}")

sys.exit(CHECKER.summary())
