#!/usr/bin/env python3
"""canonical-edit-gate-selftest.py — fixtures for hooks/canonical-edit-gate.py.

R02 PROPOSED. Reinstates and extends the suite deleted with the gate in commit
65014000. Same discipline as the original and as the rest of this family: it
SPAWNS THE REAL HOOK with a REAL PreToolUse payload and reads its exit code. No
mocking of the hook's own logic — a test that reimplements the gate to check the
gate proves nothing about drift between the two.

VERDICT CONTRACT, and it is the whole reason the fail-open rows below mean
anything: exit 2 is DENY and NOTHING ELSE IS. Any other exit code, an
exception, a cancelled hook or a process the runner kills at the hooks.json
timeout is read as ALLOW. So "fails open" is proven by showing the hook does not
exit 2, not by showing it exits 0 specifically.

TWO FIXTURE WORLDS, because the two halves need different things.

  A. IN-PLACE, against whatever checkout this file lives in — the original
     suite's shape. Proves the tracked/untracked/outside decisions against a
     real index. REPO is script-relative here exactly as it is in the hook, so
     the two always agree on what "canonical" means for a given run.

  B. A HERMETIC FIXTURE REPO in a private mkdtemp root, built with real
     `git worktree add` calls. This one exists because R02's widened exemption
     is about worktrees GIT HAS REGISTERED, and the only honest way to test
     that is against worktrees git really did register — including one that
     deliberately sits OUTSIDE .claude/worktrees/, which is the case the
     original prefix test misses. The hook's bytes are copied into the fixture
     unchanged (REPO is script-relative, so a copy is how you re-root it); it
     is the same file, not a reimplementation. A private root, never a
     fixed-name path under out/, because concurrent ci.sh runs sharing a
     fixture path cross-wire each other and it looks exactly like a regression.

Usage:
    python3 ops/canonical-edit-gate-selftest.py
    python3 ops/canonical-edit-gate-selftest.py --json            # machine-readable
    python3 ops/canonical-edit-gate-selftest.py --hook <path>     # test another copy
"""
import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# ops/git-isolation-check: a selftest that builds a git repo must confine git
# so an exported GIT_DIR (as in a hook) cannot redirect the fixture at live main.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from git_env import fixture_env  # noqa: E402
FIXTURE_ENV = fixture_env()
DEFAULT_HOOK = os.path.join(REPO, "hooks", "canonical-edit-gate.py")
HOOKS_JSON = os.path.join(REPO, "ops", "config", "hooks.json")

RESULTS: list[dict] = []


def fire(hook, tool, path, env_extra=None, session="selftest",
         kill_after=None, path_prefix=None):
    """Run the hook once. Returns (verdict, exit_code, stdout, stderr, killed)."""
    env = dict(os.environ)
    env.pop("CARR_ALLOW_CANONICAL_EDIT", None)
    if env_extra:
        env.update(env_extra)
    if path_prefix:
        env["PATH"] = path_prefix + os.pathsep + env["PATH"]
    proc = subprocess.Popen(
        [sys.executable, hook],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, env=env, start_new_session=True,
    )
    payload = json.dumps({"tool_name": tool,
                          "tool_input": {"file_path": path},
                          "session_id": session})
    killed = False
    try:
        out, err = proc.communicate(payload, timeout=kill_after or 60)
    except subprocess.TimeoutExpired:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        out, err = proc.communicate()
        killed = True
    code = proc.returncode
    return ("DENY" if code == 2 else "ALLOW"), code, out, err, killed


def check(name, want, got, detail=""):
    ok = want == got
    RESULTS.append({"case": name, "want": want, "got": got, "ok": ok,
                    "detail": detail})
    print(f"  {'ok  ' if ok else 'FAIL'} {name:38} want={want:<7} got={got}"
          + (f"   [{detail}]" if detail else ""))
    return ok


def run(cmd, cwd):
    subprocess.run(cmd, cwd=cwd, check=True, env=FIXTURE_ENV,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def wiring_groups(hooks_json, script="canonical-edit-gate.py"):
    """Every PreToolUse group naming `script`, as (matcher, tools) pairs.

    Returns (groups, error). Scanning ALL groups rather than the first
    Write|Edit one matters: a separate NotebookEdit-only group naming this gate
    would be real coverage, and a check that only looked at one matcher would
    miss it and keep reporting a gap that had been closed.
    """
    try:
        cfg = json.load(open(hooks_json))
    except Exception as exc:
        return None, f"{hooks_json}: {exc}"
    found = []
    for group in cfg.get("PreToolUse", []) or []:
        for h in group.get("hooks", []) or []:
            if script in (h.get("command") or ""):
                found.append(((group.get("matcher") or ""),
                              [t for t in (group.get("matcher") or "").split("|") if t]))
                break
    return found, None


def delivered_tools(hooks_json):
    """(tools, label, error) — the tool names that can actually reach this gate.

    Two cases, because this suite has to be honest both BEFORE and AFTER the
    gate is wired:

      wired      groups naming canonical-edit-gate.py exist -> union of their
                 matchers. The real answer once the package has landed.
      proposed   the gate is not wired yet (the situation on origin/main, and
                 the whole subject of the package's blocker document). Fall
                 back to the group the wiring diff TARGETS — identified by the
                 sibling gate one-repo-gate.py, which shares it — because that
                 is the matcher this gate would inherit.

    Either way the question answered is the same: can a NotebookEdit call reach
    this gate? An unreadable or unrecognisable config returns an error rather
    than an empty set, so "cannot tell" never silently reads as "absent".
    """
    groups, err = wiring_groups(hooks_json)
    if err:
        return None, None, err
    if groups:
        return sorted({t for _, tools in groups for t in tools}), "wired", None
    groups, err = wiring_groups(hooks_json, "one-repo-gate.py")
    if err:
        return None, None, err
    if groups:
        return (sorted({t for _, tools in groups for t in tools}),
                "proposed target group (this gate is not wired yet)", None)
    return None, None, (f"{hooks_json}: no PreToolUse group names "
                        f"canonical-edit-gate.py or one-repo-gate.py")


def set_mode(root, value):
    """Write ops/config/gate-lifecycle.json in the fixture with this gate's mode.

    `value is None` writes a file with the gate ABSENT, which is the
    "nobody staged this gate" case and must default to enforcing.
    """
    path = os.path.join(root, "ops", "config", "gate-lifecycle.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    gates = {} if value is None else {"canonical-edit-gate.py": {"mode": value}}
    with open(path, "w") as fh:
        json.dump({"gates": gates}, fh)
    return path


def build_fixture(hook_src):
    """A real git repo with two real registered worktrees, one of them outside
    .claude/worktrees/. Returns (root, paths dict)."""
    # realpath: on macOS mkdtemp hands back /var/... which is a symlink to
    # /private/var/..., and the hook compares a realpath'd target against REPO.
    root = os.path.realpath(tempfile.mkdtemp(prefix="canonical-edit-gate-fixture-"))
    os.makedirs(os.path.join(root, "hooks"))
    shutil.copyfile(hook_src, os.path.join(root, "hooks",
                                           "canonical-edit-gate.py"))
    with open(os.path.join(root, "tracked.py"), "w") as fh:
        fh.write("# a file the fixture repo really tracks\n")
    run(["git", "init", "-q", "-b", "main"], root)
    run(["git", "config", "user.email", "selftest@example.invalid"], root)
    run(["git", "config", "user.name", "selftest"], root)
    run(["git", "add", "tracked.py", "hooks/canonical-edit-gate.py"], root)
    run(["git", "commit", "-qm", "fixture"], root)
    inside = os.path.join(root, ".claude", "worktrees", "inside")
    outside = os.path.join(root, ".codex-worktrees", "outside")
    run(["git", "worktree", "add", "-q", "-b", "wt-inside", inside], root)
    run(["git", "worktree", "add", "-q", "-b", "wt-outside", outside], root)
    return root, {
        "hook": os.path.join(root, "hooks", "canonical-edit-gate.py"),
        "tracked": os.path.join(root, "tracked.py"),
        "untracked": os.path.join(root, "out", "fresh.log"),
        "wt_inside": os.path.join(inside, "tracked.py"),
        "wt_outside": os.path.join(outside, "tracked.py"),
        "unregistered": os.path.join(root, ".claude", "worktrees",
                                     "never-registered", "tracked.py"),
    }


def slow_git_shim():
    """A PATH shim whose `git` never returns, so the hook's own subprocess
    timeout fires. Used for both fail-open rows."""
    d = tempfile.mkdtemp(prefix="canonical-edit-gate-slowgit-")
    p = os.path.join(d, "git")
    with open(p, "w") as fh:
        fh.write("#!/bin/sh\nsleep 600\n")
    os.chmod(p, 0o755)
    return d


def first_tracked_file():
    out = subprocess.run(["git", "-C", REPO, "ls-files", "README.md"],
                         capture_output=True, text=True, timeout=15).stdout.strip()
    if out:
        return "README.md"
    lines = subprocess.run(["git", "-C", REPO, "ls-files"],
                           capture_output=True, text=True,
                           timeout=15).stdout.splitlines()
    return lines[0] if lines else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hook", default=DEFAULT_HOOK)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--skip-in-place", action="store_true",
                    help="fixture world only; for running against a copy that "
                         "does not sit in a git checkout")
    ap.add_argument("--hooks-json", default=HOOKS_JSON,
                    help="wiring config the NotebookEdit tripwire scans. "
                         "Defaults to this checkout's ops/config/hooks.json, "
                         "which is correct once the suite sits at ops/. Point "
                         "it at a real checkout when running the suite from "
                         "outside one, so the tripwire scans real wiring "
                         "instead of failing on a missing file.")
    args = ap.parse_args()

    if not os.path.exists(args.hook):
        print(f"FAIL: hook not found at {args.hook}")
        return 1

    # ---------------- world B: hermetic fixture, real registered worktrees ----
    print("fixture repo (real `git worktree add`):")
    root, p = build_fixture(args.hook)
    shim = slow_git_shim()
    try:
        v, _, _, err, _ = fire(p["hook"], "Edit", p["tracked"])
        check("tracked-canonical-deny", "DENY", v)
        check("refusal-names-worktree-command", True,
              "./run.sh worktree <name> --from origin/main" in err,
              "the refusal must be runnable as written")
        check("refusal-offers-no-env-bypass", True,
              "CARR_ALLOW_CANONICAL_EDIT=1" not in err.replace(
                  "The former CARR_ALLOW_CANONICAL_EDIT hatch", ""),
              "disposition 3: no scoped-edit allowance")

        v, _, _, _, _ = fire(p["hook"], "Edit", p["wt_inside"])
        check("worktree-inside-claude-allow", "ALLOW", v)

        # THE WIDENING — and read the second row before trusting the first.
        #
        # The verdict row is a TRUE ANSWER THAT PROVES NOTHING ABOUT THE
        # WIDENING, and saying so here is cheaper than someone re-deriving it.
        # A registered worktree's files are never entries in the OUTER repo's
        # index, so `git ls-files` calls this path untracked and allowance 2
        # allows it whether or not the exemption ever reached it. Deleting the
        # widening entirely leaves this row green (verified by mutation M1).
        # It is kept because it is the behaviour a session actually experiences.
        v, _, _, _, _ = fire(p["hook"], "Edit", p["wt_outside"])
        check("worktree-registered-elsewhere-allow", "ALLOW", v,
              "true, but allowance 2 alone would also produce it")

        # THIS is the row that can tell the widening apart from its absence: a
        # white-box assertion on the exemption's REACH rather than on a verdict.
        # No verdict-level test can distinguish them while allowance 2 stands,
        # which is itself the finding — see the manifest's disposition-1 note.
        reach = None
        try:
            import importlib.util
            spec = importlib.util.spec_from_file_location("cegate", p["hook"])
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            reach = mod.in_worktree(os.path.dirname(p["wt_outside"]))
        except Exception as exc:
            print(f"    (could not load the hook as a module: {exc})")
        check("exemption-reaches-registered-tree-outside-wt-root", True, reach,
              "in_worktree() itself, via git's own worktree registry")

        # The kept prefix test: a directory under .claude/worktrees that git has
        # NOT registered is still exempt, because the widening is additive only.
        v, _, _, _, _ = fire(p["hook"], "Edit", p["unregistered"])
        check("unregistered-under-wt-root-allow", "ALLOW", v,
              "prefix test kept verbatim; widening adds, never narrows")

        v, _, _, _, _ = fire(p["hook"], "Write", p["untracked"])
        check("untracked-allow", "ALLOW", v, "disposition 2: receipts and out/")

        v, _, _, _, _ = fire(p["hook"], "Edit", "/tmp/not-in-any-repo.py")
        check("outside-repo-allow", "ALLOW", v)

        v, _, _, _, _ = fire(p["hook"], "Read", p["tracked"])
        check("non-edit-tool-ignored", "ALLOW", v)

        v, _, _, _, _ = fire(p["hook"], "Bash", p["tracked"])
        check("bash-not-covered", "ALLOW", v,
              "edit-tools-only: the named Codex/shell gap; R04 is the PLANNED "
              "compensating control and is not live yet")

        v, _, _, _, _ = fire(p["hook"], "MultiEdit", p["tracked"])
        check("multiedit-tracked-deny", "DENY", v)

        # NOT A COVERAGE CLAIM. This proves only that IF a NotebookEdit payload
        # ever reached this gate it would be handled. It cannot reach it: no
        # matcher in ops/config/hooks.json names NotebookEdit, so the harness
        # never dispatches one here. See the tripwire row in the in-place world
        # and the COVERAGE section of the gate's docstring.
        v, _, _, _, _ = fire(p["hook"], "NotebookEdit", p["tracked"])
        check("notebookedit-payload-denied-if-ever-delivered", "DENY", v,
              "NOT end-to-end coverage — the matcher does not deliver it")

        # THE TRIPWIRE, and it lives HERE — in the hermetic world that every
        # captured run exercises — rather than in the in-place block below.
        # v2 of this package put it in the in-place block, captured the suite
        # with --skip-in-place, and then cited a passing tripwire that the
        # captured result never ran. A guard nobody executes is not a guard.
        #
        # It scans EVERY group that wires this gate, not one matcher: a separate
        # NotebookEdit-only group naming this gate would be real coverage, and a
        # single-matcher check would miss it and keep reporting a gap that had
        # been closed.
        #
        # A FAILURE HERE IS GOOD NEWS THAT NEEDS FOLLOW-UP: it means the matcher
        # was widened, so delete the "NotebookEdit is not covered" paragraph
        # from the gate's COVERAGE section, update the matrix, and flip this row.
        delivered, label, err = delivered_tools(args.hooks_json)
        if err:
            # "Cannot tell" must NOT read as "NotebookEdit absent". Fail loudly.
            check("notebookedit-absent-from-every-wiring-matcher", True, False,
                  f"could not determine the wiring: {err}")
        else:
            check("notebookedit-absent-from-every-wiring-matcher", True,
                  "NotebookEdit" not in delivered,
                  f"delivered via {label}: {delivered}")

        # DISPOSITION 3, the removal itself: the env var must no longer buy a
        # tracked-file edit. This is the row that fails if the hatch comes back.
        v, _, _, _, _ = fire(p["hook"], "Edit", p["tracked"],
                             env_extra={"CARR_ALLOW_CANONICAL_EDIT": "1"})
        check("escape-hatch-no-longer-allows", "DENY", v,
              "disposition 3: the scoped-edit allowance is removed")

        # ROLLOUT MODES. gate-lifecycle.json's `mode` is documentary for every
        # gate but conduct-stop-gate.py, which reads its own key directly; this
        # gate copies that one exception. Offering a staged rollout the code
        # cannot honour would be worse than not offering it, so each mode is
        # exercised against the tracked-file case that enforcing refuses.
        set_mode(root, "shadow")
        v, code, out, err, _ = fire(p["hook"], "Edit", p["tracked"])
        check("mode-shadow-allows", "ALLOW", v, f"exit={code}")
        check("mode-shadow-is-silent", True, out.strip() == "" and err.strip() == "",
              "shadow records to the audit log and says nothing to the session")

        set_mode(root, "announce")
        v, code, out, err, _ = fire(p["hook"], "Edit", p["tracked"])
        check("mode-announce-allows", "ALLOW", v, f"exit={code}")
        announced = False
        try:
            doc = json.loads(out)
            announced = (doc["hookSpecificOutput"]["permissionDecision"] == "allow"
                         and "announced, not refused" in doc["systemMessage"])
        except Exception:
            pass
        check("mode-announce-emits-structured-allow", True, announced,
              "same announce shape gate-edit-gate.py uses")
        check("mode-announce-still-names-the-remedy", True,
              "--from origin/main" in out)

        set_mode(root, "enforcing")
        v, _, _, _, _ = fire(p["hook"], "Edit", p["tracked"])
        check("mode-enforcing-denies", "DENY", v)

        # The default direction, and it is chosen: a gate nobody staged
        # enforces. Defaulting to shadow would mean an unreadable or renamed
        # config silently disables enforcement, which is the 2026-08-08
        # five-gates-off-for-a-day failure.
        set_mode(root, None)
        v, _, _, _, _ = fire(p["hook"], "Edit", p["tracked"])
        check("mode-absent-defaults-to-enforcing", "DENY", v)

        lifecycle = os.path.join(root, "ops", "config", "gate-lifecycle.json")
        with open(lifecycle, "w") as fh:
            fh.write("{ this is not json")
        v, _, _, _, _ = fire(p["hook"], "Edit", p["tracked"])
        check("mode-unreadable-defaults-to-enforcing", "DENY", v,
              "a corrupt config must not silently disable the gate")
        os.unlink(lifecycle)
        v, _, _, _, _ = fire(p["hook"], "Edit", p["tracked"])
        check("mode-missing-file-defaults-to-enforcing", "DENY", v)

        # FAIL OPEN, both directions.
        t0 = time.time()
        v, code, _, err, _ = fire(p["hook"], "Edit", p["tracked"],
                                  path_prefix=shim)
        check("failopen-internal-timeout", "ALLOW", v,
              f"git hung; hook's own subprocess timeout fired, exit={code}, "
              f"{time.time() - t0:.1f}s")
        check("failopen-internal-emits-no-refusal", True, "refused" not in err.lower())

        v, code, _, err, killed = fire(p["hook"], "Edit", p["tracked"],
                                       path_prefix=shim, kill_after=3)
        check("failopen-harness-timeout", "ALLOW", v,
              f"process killed at the runner ceiling, exit={code}, "
              f"killed={killed} — anything but exit 2 is ALLOW")
        check("failopen-harness-emits-no-refusal", True, "refused" not in err.lower())
    finally:
        shutil.rmtree(root, ignore_errors=True)
        shutil.rmtree(shim, ignore_errors=True)

    # ---------------- world A: in place, against the real index --------------
    if not args.skip_in_place:
        print("in-place, against this checkout's real index:")
        # WHICH VERDICT IS CORRECT HERE DEPENDS ON WHAT THIS CHECKOUT IS, and
        # an earlier revision of this suite got that wrong. In the canonical
        # checkout, or a CI clone, .git is a DIRECTORY and a tracked file must
        # be REFUSED. In a linked worktree .git is a FILE, the whole tree is a
        # registered worktree, and the gate must ALLOW -- that is the exemption
        # working exactly as designed, not a failure. Asserting DENY
        # unconditionally passed on CI and failed in every worktree, which is
        # where the suite is actually run by hand.
        in_linked_worktree = os.path.isfile(os.path.join(REPO, ".git"))
        tracked_rel = first_tracked_file()
        if tracked_rel:
            v, _, _, _, _ = fire(args.hook, "Edit",
                                 os.path.join(REPO, tracked_rel))
            want = "ALLOW" if in_linked_worktree else "DENY"
            check("in-place-tracked-verdict", want, v,
                  f"{tracked_rel}; this checkout is "
                  + ("a linked worktree, so the worktree exemption applies"
                     if in_linked_worktree
                     else "not a worktree, so the canonical refusal applies"))
        probe = os.path.join(REPO, "out",
                             "_selftest_probe_canonical_edit_gate.log")
        v, _, _, _, _ = fire(args.hook, "Write", probe)
        check("in-place-untracked-out-allow", "ALLOW", v)
        try:
            os.unlink(probe)          # never leave temp files in the checkout
        except OSError:
            pass

        # A gate nobody wires is not a gate. Read the SOURCE config, not the
        # live ~/.claude/settings.json, which a worktree has no copy of.
        # The NotebookEdit tripwire is NOT here — it runs in the hermetic world
        # above, so every captured run exercises it. This row asks the narrower
        # in-place question: is the gate wired under a group that delivers the
        # three tools it actually relies on?
        groups, err = wiring_groups(args.hooks_json)
        wired = bool(groups) and any(
            {"Write", "Edit", "MultiEdit"} <= set(tools) for _, tools in groups)
        if err:
            print(f"    (could not read {args.hooks_json}: {err})")
        check("registration-wired", True, wired,
              "a PreToolUse Write|Edit|MultiEdit group names canonical-edit-gate.py")

    passed = sum(1 for r in RESULTS if r["ok"])
    total = len(RESULTS)
    if args.json:
        print(json.dumps({"hook": args.hook, "passed": passed, "total": total,
                          "results": RESULTS}, indent=2))
    print()
    print(f"canonical-edit-gate-selftest: {passed}/{total} passed")
    bad = [r["case"] for r in RESULTS if not r["ok"]]
    if bad:
        print("FAILURES: " + ", ".join(bad))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
