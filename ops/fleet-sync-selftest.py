#!/usr/bin/env python3
"""
fleet-sync-selftest.py — acceptance test for bin/fleet-sync.sh, written the same
day as the script (rule e65efc68: write the test before the thing, especially
before a gate).

THE INCIDENT IT COMES FROM, 2026-08-18. Dell's Mac was migrated clean on
2026-08-11. Over the following week the repo grew new gates; nothing on his
machine ever fetched them, so his sessions announced the whole hook tuple as
missing and — having no escalation gate installed — sent every blocker to Joe.
He was pulled between two machines twice in one day. An audit of all nineteen
scheduled jobs that day found not one that touched a git remote, so no machine
could ever catch itself up.

WHAT THE JOB MUST DO, one assertion per line of that:

  1. FAST-FORWARD a clean checkout that is behind, then re-render the installed
     wiring from what it just pulled.
  2. REFUSE, loudly and without touching anything, when the tree has local
     changes. Dell's machine deliberately carries two uncommitted edits from his
     migration; a blind fast-forward would have destroyed them. This is the most
     important case in the file.
  3. REFUSE when the checkout is a worktree or is not on main. Worktree-per-
     session means most sessions run elsewhere, and syncing from there would
     move a branch somebody is mid-edit on.
  4. BE A CLEAN NO-OP when already current, so running it four times a day
     costs nothing and never destabilises a machine.

Every case runs against throwaway repositories under a temp dir with a stubbed
installer. Nothing here touches the real machine's config, which is the point:
a test that re-renders live settings is not a test.
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path
import tempfile

# The one scrubber (ops/git_env.py), not a local copy. GIT_DIR is exported by
# every git hook and overrides cwd for any child git process — the 2026-08-14
# incident where fixture commits landed on live main is exactly what an
# unscrubbed ENV here would reproduce, since ENV is handed to every git call
# and to fleet-sync.sh itself (which shells out to git internally).
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from git_env import fixture_env  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "tools"))
from fleet_sync_safety import submodule_tree_is_exact_patch  # noqa: E402
from health_submodule import (  # noqa: E402
    patches_dir_for,
    submodule_dirt_is_tracked_patch,
)

SCRIPT = os.path.join(REPO, "bin", "fleet-sync.sh")
RUN_SCHEDULED = os.path.join(REPO, "bin", "run-scheduled.sh")
SAFETY = os.path.join(REPO, "tools", "fleet_sync_safety.py")
HEALTH_SUBMODULE = os.path.join(REPO, "tools", "health_submodule.py")
ENV = dict(fixture_env(), GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
           GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")

STUB = (
    "import os, sys\n"
    "print('stub installer active=' + "
    "os.environ.get('CARR_CONFIG_AS_CODE_ACTIVE_LAUNCHD_LABEL', '') + "
    "' xpc=' + os.environ.get('XPC_SERVICE_NAME', ''))\n"
    "sys.exit(0)\n"
)


def git(cwd, *args):
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, env=ENV)
    if r.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed in {cwd}: {r.stderr}")
    return r.stdout.strip()


def run_sync(clone, extra_env=None):
    env = dict(ENV)
    env.update(extra_env or {})
    return subprocess.run(["zsh", os.path.join(clone, "bin", "fleet-sync.sh")],
                          cwd=clone, capture_output=True, text=True, env=env)


def launchctl_fixture_env(tmp, matches=True, mismatch_on_second=False):
    """A launchctl that binds its first caller PID as the loaded job PID."""
    stubs = os.path.join(tmp, "launchctl-stubs")
    os.makedirs(stubs, exist_ok=True)
    pid_file = os.path.join(tmp, "loaded-fleet.pid")
    count_file = os.path.join(tmp, "launchctl-count")
    launchctl = os.path.join(stubs, "launchctl")
    open(launchctl, "w").write(
        "#!/bin/zsh\n"
        "[ \"${1:-}\" = print ] || exit 64\n"
        "count=0\n"
        "[ -f \"$CARR_TEST_LAUNCHCTL_COUNT_FILE\" ] && "
        "count=\"$(cat \"$CARR_TEST_LAUNCHCTL_COUNT_FILE\")\"\n"
        "count=$((count + 1))\n"
        "print -r -- \"$count\" > \"$CARR_TEST_LAUNCHCTL_COUNT_FILE\"\n"
        "if [ -f \"$CARR_TEST_FLEET_PID_FILE\" ]; then\n"
        "  pid=\"$(cat \"$CARR_TEST_FLEET_PID_FILE\")\"\n"
        "else\n"
        "  candidate=$PPID\n"
        "  pid=$candidate\n"
        "  while [ \"$candidate\" -gt 1 ] 2>/dev/null; do\n"
        "    command=\"$(ps -o command= -p $candidate)\"\n"
        "    if [[ \"$command\" = *run-scheduled.sh* ]]; then\n"
        "      pid=$candidate\n"
        "      break\n"
        "    fi\n"
        "    candidate=\"$(ps -o ppid= -p $candidate | tr -d ' ')\"\n"
        "  done\n"
        "  print -r -- \"$pid\" > \"$CARR_TEST_FLEET_PID_FILE\"\n"
        "fi\n"
        "[ \"${CARR_TEST_FLEET_PID_MATCH:-1}\" = 1 ] || pid=$((pid + 100000))\n"
        "if [ \"${CARR_TEST_MISMATCH_ON_SECOND:-0}\" = 1 ] && [ \"$count\" -gt 1 ]; then\n"
        "  pid=$((pid + 200000))\n"
        "fi\n"
        "print -r -- \"gui/501/com.carr.fleet-sync = {\"\n"
        "print -r -- \"    pid = $pid\"\n"
        "print -r -- \"}\"\n"
    )
    os.chmod(launchctl, 0o755)
    return dict(
        ENV,
        PATH=stubs + os.pathsep + ENV.get("PATH", ""),
        CARR_TEST_FLEET_PID_FILE=pid_file,
        CARR_TEST_LAUNCHCTL_COUNT_FILE=count_file,
        CARR_TEST_FLEET_PID_MATCH="1" if matches else "0",
        CARR_TEST_MISMATCH_ON_SECOND="1" if mismatch_on_second else "0",
        CARR_RUN_SCHEDULED_STATE_DIR=os.path.join(tmp, "state"),
        XPC_SERVICE_NAME="com.carr.fleet-sync",
    )


def run_wrapped_sync(clone, tmp, run_key="fleet.sync", command_args=None,
                     launchctl_matches=True, mismatch_on_second=False):
    shutil.copy(RUN_SCHEDULED, os.path.join(clone, "bin", "run-scheduled.sh"))
    open(os.path.join(clone, "tools", "ops-spool.py"), "w").write(
        "import sys\nprint('stub receipt recorder')\nsys.exit(0)\n"
    )
    command_args = command_args or [
        "/bin/zsh", os.path.join(clone, "bin", "fleet-sync.sh")
    ]
    env = launchctl_fixture_env(
        tmp, matches=launchctl_matches, mismatch_on_second=mismatch_on_second
    )
    result = subprocess.run([
        "/bin/zsh", os.path.join(clone, "bin", "run-scheduled.sh"),
        "fleet-sync", run_key, *command_args,
    ], cwd=clone, capture_output=True, text=True, env=env)
    log_path = os.path.join(clone, "out", "run-scheduled.log")
    log = open(log_path).read() if os.path.exists(log_path) else ""
    return result, log


def build(tmp):
    """origin + clone A (pushes) + clone B (the machine under test, falls behind)."""
    origin = os.path.join(tmp, "origin")
    os.makedirs(origin)
    git(origin, "init", "-q", "--bare")
    a = os.path.join(tmp, "A")
    git(tmp, "clone", "-q", origin, "A")
    open(os.path.join(a, "f.txt"), "w").write("base\n")
    git(a, "add", "f.txt")
    git(a, "commit", "-qm", "base")
    git(a, "branch", "-M", "main")
    git(a, "push", "-q", "origin", "main")
    # THE FIXTURE MUST DECLARE ITS DEFAULT BRANCH, NOT INHERIT THE MACHINE'S.
    # `git init --bare` points the new repository's HEAD at whatever
    # init.defaultBranch says, which is "main" on this Mac and "master" on a
    # GitHub runner. Clone B below takes its branch from origin's HEAD, so on
    # the runner it landed on an unborn "master" while the only real branch was
    # "main" — leaving B detached. fleet-sync.sh then refused for the RIGHT
    # reason ("checkout is on 'HEAD', not main") and the dirty-tree assertion
    # failed against a message about something else entirely. Four cases passed
    # locally and the same four failed in CI, which is the shape of a test that
    # is measuring the host rather than the code. Setting HEAD explicitly here
    # is version-safe where `init -b` is not.
    git(origin, "symbolic-ref", "HEAD", "refs/heads/main")

    b = os.path.join(tmp, "B")
    git(tmp, "clone", "-q", origin, "B")
    os.makedirs(os.path.join(b, "bin"), exist_ok=True)
    os.makedirs(os.path.join(b, "ops"), exist_ok=True)
    os.makedirs(os.path.join(b, "tools"), exist_ok=True)
    shutil.copy(SCRIPT, os.path.join(b, "bin", "fleet-sync.sh"))
    shutil.copy(SAFETY, os.path.join(b, "tools", "fleet_sync_safety.py"))
    shutil.copy(HEALTH_SUBMODULE, os.path.join(b, "tools", "health_submodule.py"))
    open(os.path.join(b, "ops", "config-as-code.py"), "w").write(STUB)

    # origin moves ahead; B is now stale exactly as Dell's Mac was.
    open(os.path.join(a, "f.txt"), "a").write("newer\n")
    git(a, "add", "f.txt")
    git(a, "commit", "-qm", "newer")
    git(a, "push", "-q", "origin", "main")
    return b


def add_expected_quill_patch(tmp, clone):
    """Add the exact permanent Quill-style patch state to ``clone``.

    The submodule is locally seeded so the test exercises the same classifier
    path as production rather than merely faking a porcelain row.
    """
    quill_remote = os.path.join(tmp, "quill-origin")
    quill_source = os.path.join(tmp, "quill-source")
    os.makedirs(quill_remote)
    git(quill_remote, "init", "-q", "--bare")
    git(tmp, "clone", "-q", quill_remote, "quill-source")
    open(os.path.join(quill_source, "quill.txt"), "w").write("base\n")
    open(os.path.join(quill_source, "other.txt"), "w").write("base\n")
    open(os.path.join(quill_source, "blob.bin"), "wb").write(b"\x00base\n")
    open(os.path.join(quill_source, "script.sh"), "w").write("#!/bin/sh\nexit 0\n")
    git(quill_source, "add", "quill.txt", "other.txt", "blob.bin", "script.sh")
    git(quill_source, "commit", "-qm", "quill base")
    git(quill_source, "branch", "-M", "main")
    git(quill_source, "push", "-q", "origin", "main")
    git(quill_remote, "symbolic-ref", "HEAD", "refs/heads/main")

    # Rebuild the fixture's remote from a parent containing the submodule, then
    # clone it exactly as the machine under test would.
    parent = os.path.join(tmp, "parent")
    git(tmp, "clone", "-q", os.path.join(tmp, "origin"), "parent")
    os.makedirs(os.path.join(parent, "tools", "dictation-rig", "patches"), exist_ok=True)
    git(parent, "-c", "protocol.file.allow=always", "submodule", "add", "-q",
        quill_remote, "tools/dictation-rig/vendor/quill")
    patch = os.path.join(parent, "tools", "dictation-rig", "patches", "0001.patch")
    open(patch, "w").write("--- a/quill.txt\n+++ b/quill.txt\n@@ -1 +1 @@\n-base\n+patched\n")
    git(parent, "add", ".gitmodules", "tools/dictation-rig/vendor/quill", patch)
    git(parent, "commit", "-qm", "add quill patch")
    git(parent, "push", "-q", "origin", "main")

    # Advance B with the parent contents and initialise its submodule.  The
    # expected dirty state is then a real diff equal to the tracked patch.
    git(clone, "pull", "-q", "--ff-only", "origin", "main")
    git(clone, "-c", "protocol.file.allow=always", "submodule", "update", "--init")
    quill = os.path.join(clone, "tools", "dictation-rig", "vendor", "quill")
    open(os.path.join(quill, "quill.txt"), "w").write("patched\n")
    return quill, parent


def behind(clone):
    git(clone, "fetch", "-q", "origin", "main")
    return int(git(clone, "rev-list", "--count", "HEAD..origin/main"))


def line_classifier_accepts(quill):
    diff = git(quill, "diff")
    return submodule_dirt_is_tracked_patch(diff, patches_dir_for(quill))


def require_exact_refusal(repo, expected_reason="tracked Quill tree differs"):
    exact, reason = submodule_tree_is_exact_patch(repo)
    assert not exact, "exact Git tree proof accepted a non-canonical Quill tree"
    assert expected_reason in reason, reason


def add_fixture_patch(repo, name, body):
    patch = os.path.join(repo, "tools", "dictation-rig", "patches", name)
    open(patch, "w").write(body)


def test_dirty_tree_refuses():
    with tempfile.TemporaryDirectory() as tmp:
        b = build(tmp)
        open(os.path.join(b, "f.txt"), "a").write("uncommitted local work\n")
        r = run_sync(b)
        assert r.returncode == 78, f"expected skip 78, got {r.returncode}: {r.stdout}{r.stderr}"
        assert "refusing to fast-forward" in r.stdout, r.stdout
        assert "f.txt" in r.stdout, "the skip must NAME the dirty path"
        assert behind(b) == 1, "it must not have advanced the checkout"
        assert "uncommitted local work" in open(os.path.join(b, "f.txt")).read(), \
            "LOCAL WORK WAS DESTROYED — the one thing this job must never do"
    print("PASS  dirty tree refuses, names the file, destroys nothing")


def test_existing_board_agent_is_rebound_after_main_sync():
    with tempfile.TemporaryDirectory() as tmp:
        b = build(tmp)
        home = os.path.join(tmp, "home")
        agents = os.path.join(home, "Library", "LaunchAgents")
        os.makedirs(agents)
        plist = os.path.join(agents, "local.carr-progress-board.plist")
        Path(plist).write_text("installed plist must remain untouched by this stub")
        stub = os.path.join(b, "ops", "config-as-code.py")
        Path(stub).write_text(STUB.replace("sys.exit(0)", "print('installer command=' + ' '.join(sys.argv[1:]))\nsys.exit(0)"))
        result = run_sync(b, {"HOME": home})
        assert result.returncode == 0, result.stdout + result.stderr
        assert "installer command=install --apply" in result.stdout, result.stdout
        assert "installer command=install-progress-board --apply" in result.stdout, result.stdout
        assert behind(b) == 0, "board migration follows canonical main synchronization"
        assert Path(plist).read_text() == "installed plist must remain untouched by this stub"
    print("PASS  existing board agent migrates after canonical main sync")


def test_clean_tree_fast_forwards():
    with tempfile.TemporaryDirectory() as tmp:
        b = build(tmp)
        assert behind(b) == 1
        r = run_sync(b)
        assert r.returncode == 0, f"{r.returncode}: {r.stdout}{r.stderr}"
        assert "fast-forwarded" in r.stdout, r.stdout
        assert "stub installer" in r.stdout, "must re-render wiring after pulling"
        assert "stub installer active= xpc=" in r.stdout, \
            "a manual run must remain an external installer: " + repr(r.stdout)
        assert behind(b) == 0
    print("PASS  clean tree fast-forwards, then re-renders the wiring")


def test_already_current_is_a_noop():
    with tempfile.TemporaryDirectory() as tmp:
        b = build(tmp)
        run_sync(b)
        r = run_sync(b)
        assert r.returncode == 0, f"{r.returncode}: {r.stdout}{r.stderr}"
        assert "already current" in r.stdout, r.stdout
        assert "fast-forwarded" not in r.stdout
    print("PASS  second run is a clean no-op")


def test_non_fleet_xpc_identity_stays_external():
    with tempfile.TemporaryDirectory() as tmp:
        b = build(tmp)
        r = run_sync(b, {"XPC_SERVICE_NAME": "com.carr.some-other-job",
                         "CARR_CONFIG_AS_CODE_ACTIVE_LAUNCHD_LABEL": "stale-value"})
        assert r.returncode == 0, f"{r.returncode}: {r.stdout}{r.stderr}"
        assert "stub installer active= xpc=" in r.stdout, r.stdout
        assert "active=com.carr.fleet-sync" not in r.stdout, r.stdout
    print("PASS  non-fleet XPC identity cannot claim the active-self exemption")


def test_direct_handoff_injection_fails_closed():
    with tempfile.TemporaryDirectory() as tmp:
        b = build(tmp)
        env = launchctl_fixture_env(tmp, matches=False)
        env.update({
            "CARR_RUN_SCHEDULED_FLEET_SELF_CLAIM": "1",
            "CARR_RUN_SCHEDULED_XPC_SERVICE_NAME": "com.carr.fleet-sync",
            "CARR_RUN_SCHEDULED_LAUNCHD_PID": str(os.getpid()),
            "CARR_CONFIG_AS_CODE_ACTIVE_LAUNCHD_LABEL": "stale-value",
        })
        r = run_sync(b, env)
        assert r.returncode == 1, f"{r.returncode}: {r.stdout}{r.stderr}"
        assert "ACTIVE-SELF PROOF REFUSED" in r.stderr, r.stderr
        assert "stub installer" not in r.stdout, r.stdout
    print("PASS  direct ambient handoff injection fails closed before install")


def test_active_self_contract_reaches_wrapper_receipt():
    """The fleet child must return so run-scheduled can durably classify it."""
    with tempfile.TemporaryDirectory() as tmp:
        b = build(tmp)
        r, log = run_wrapped_sync(b, tmp)
        assert r.returncode == 0, f"{r.returncode}: {r.stdout}{r.stderr}\n{log}"
        assert "stub installer active=com.carr.fleet-sync" in r.stdout, r.stdout
        assert "key=fleet.sync service=fleet-sync child_exit=0 state=succeeded" in log, log
        assert "recorder_exit=0" in log, log
    print("PASS  active-self install returns through the durable wrapper receipt")


def test_active_self_change_failure_reaches_wrapper_receipt():
    """A refused self-reload is a durable failure, never false success."""
    with tempfile.TemporaryDirectory() as tmp:
        b = build(tmp)
        open(os.path.join(b, "ops", "config-as-code.py"), "w").write(
            "import os, sys\n"
            "print('SELF-RELOAD REFUSED; run config-as-code externally; active=' + "
            "os.environ.get('CARR_CONFIG_AS_CODE_ACTIVE_LAUNCHD_LABEL', ''))\n"
            "sys.exit(1)\n"
        )
        r, log = run_wrapped_sync(b, tmp)
        assert r.returncode == 1, f"{r.returncode}: {r.stdout}{r.stderr}\n{log}"
        assert "SELF-RELOAD REFUSED" in r.stdout, r.stdout
        assert "active=com.carr.fleet-sync" in r.stdout, r.stdout
        assert "key=fleet.sync service=fleet-sync child_exit=1 state=failed" in log, log
        assert "--failure-class exit_1" in log and "recorder_exit=0" in log, log
    print("PASS  refused active-self reload reaches a durable failed receipt")


def test_wrong_run_key_cannot_attest_active_self():
    with tempfile.TemporaryDirectory() as tmp:
        b = build(tmp)
        r, log = run_wrapped_sync(
            b, tmp, run_key="fleet.wrong", command_args=["/usr/bin/true"]
        )
        assert r.returncode == 1, f"{r.returncode}: {r.stdout}{r.stderr}\n{log}"
        assert "ACTIVE-SELF PROOF REFUSED" in r.stderr, r.stderr
        assert "supplied child was not executed" in r.stderr, r.stderr
        assert open(os.path.join(tmp, "launchctl-count")).read().strip() == "1"
        assert "key=fleet.wrong service=fleet-sync child_exit=1 state=failed" in log, log
        assert "recorder_exit=0" in log, log
    print("PASS  wrong run key refuses /usr/bin/true before durable failure receipt")


def test_wrong_command_cannot_attest_active_self():
    with tempfile.TemporaryDirectory() as tmp:
        b = build(tmp)
        r, log = run_wrapped_sync(b, tmp, command_args=["/usr/bin/true"])
        assert r.returncode == 1, f"{r.returncode}: {r.stdout}{r.stderr}\n{log}"
        assert "ACTIVE-SELF PROOF REFUSED" in r.stderr, r.stderr
        assert "supplied child was not executed" in r.stderr, r.stderr
        assert open(os.path.join(tmp, "launchctl-count")).read().strip() == "1"
        assert "child_exit=1 state=failed" in log and "recorder_exit=0" in log, log
    print("PASS  exact-key /usr/bin/true command is refused before durable receipt")


def test_refused_wrapper_never_executes_supplied_child():
    with tempfile.TemporaryDirectory() as tmp:
        b = build(tmp)
        marker = os.path.join(tmp, "child-executed")
        r, log = run_wrapped_sync(
            b, tmp,
            command_args=["/bin/zsh", "-c", f"print executed > {marker}"],
        )
        assert r.returncode == 1, f"{r.returncode}: {r.stdout}{r.stderr}\n{log}"
        assert not os.path.exists(marker), "refused wrapper executed the supplied child"
        assert "supplied child was not executed" in r.stderr, r.stderr
        assert "child_exit=1 state=failed" in log and "recorder_exit=0" in log, log
    print("PASS  refused active-self wrapper does not execute a marker-writing child")


def test_forged_xpc_without_loaded_pid_fails_closed():
    with tempfile.TemporaryDirectory() as tmp:
        b = build(tmp)
        r, log = run_wrapped_sync(b, tmp, launchctl_matches=False)
        assert r.returncode == 1, f"{r.returncode}: {r.stdout}{r.stderr}\n{log}"
        assert "ACTIVE-SELF PROOF REFUSED" in r.stderr, r.stderr
        assert "stub installer" not in r.stdout, r.stdout
        assert "child_exit=1 state=failed" in log and "recorder_exit=0" in log, log
    print("PASS  forged XPC identity without loaded wrapper PID fails closed")


def test_second_launchctl_mismatch_fails_closed():
    with tempfile.TemporaryDirectory() as tmp:
        b = build(tmp)
        r, log = run_wrapped_sync(b, tmp, mismatch_on_second=True)
        assert r.returncode == 1, f"{r.returncode}: {r.stdout}{r.stderr}\n{log}"
        assert "ACTIVE-SELF PROOF REFUSED" in r.stderr, r.stderr
        assert "stub installer" not in r.stdout, r.stdout
        assert "child_exit=1 state=failed" in log and "recorder_exit=0" in log, log
    print("PASS  second launchctl mismatch fails closed with durable receipt")


def test_non_main_branch_refuses():
    with tempfile.TemporaryDirectory() as tmp:
        b = build(tmp)
        git(b, "checkout", "-q", "-b", "some-session-branch")
        r = run_sync(b)
        assert r.returncode == 78, f"expected skip 78, got {r.returncode}: {r.stdout}"
        assert "not main" in r.stdout, r.stdout
        assert behind(b) == 1, "it must not have moved a session's branch"
    print("PASS  refuses on a non-main branch without moving it")


def test_untracked_scratch_is_ignored():
    with tempfile.TemporaryDirectory() as tmp:
        b = build(tmp)
        scratch = os.path.join(b, "session-scratch.txt")
        open(scratch, "w").write("untracked session artifact\n")
        r = run_sync(b)
        assert r.returncode == 0, f"{r.returncode}: {r.stdout}{r.stderr}"
        assert "fast-forwarded" in r.stdout, r.stdout
        assert open(scratch).read() == "untracked session artifact\n"
    print("PASS  intentional untracked scratch is ignored and preserved")


def test_expected_quill_patch_allows_unrelated_fast_forward():
    with tempfile.TemporaryDirectory() as tmp:
        b = build(tmp)
        _, parent = add_expected_quill_patch(tmp, b)
        # Parent moves only an unrelated tracked path beyond B's current remote.
        open(os.path.join(parent, "f.txt"), "a").write("unrelated remote change\n")
        git(parent, "add", "f.txt")
        git(parent, "commit", "-qm", "unrelated remote change")
        git(parent, "push", "-q", "origin", "main")
        r = run_sync(b)
        assert r.returncode == 0, f"{r.returncode}: {r.stdout}{r.stderr}"
        assert "fast-forwarded" in r.stdout, r.stdout
        assert open(os.path.join(b, "tools", "dictation-rig", "vendor", "quill", "quill.txt")).read() == "patched\n"
    print("PASS  exact expected Quill patch permits unrelated fast-forward")


def test_extra_quill_edit_refuses():
    with tempfile.TemporaryDirectory() as tmp:
        b = build(tmp)
        quill, parent = add_expected_quill_patch(tmp, b)
        open(os.path.join(quill, "quill.txt"), "a").write("hand edit\n")
        open(os.path.join(parent, "f.txt"), "a").write("remote advance\n")
        git(parent, "add", "f.txt")
        git(parent, "commit", "-qm", "remote advance")
        git(parent, "push", "-q", "origin", "main")
        r = run_sync(b)
        assert r.returncode == 78, f"{r.returncode}: {r.stdout}{r.stderr}"
        assert "actionable" in r.stdout, r.stdout
    print("PASS  extra Quill edit refuses")


def test_quill_trailing_space_edit_refuses():
    with tempfile.TemporaryDirectory() as tmp:
        b = build(tmp)
        quill, parent = add_expected_quill_patch(tmp, b)
        open(os.path.join(quill, "quill.txt"), "w").write("patched \n")
        open(os.path.join(parent, "f.txt"), "a").write("remote advance\n")
        git(parent, "add", "f.txt")
        git(parent, "commit", "-qm", "remote advance")
        git(parent, "push", "-q", "origin", "main")
        r = run_sync(b)
        assert r.returncode == 78, f"{r.returncode}: {r.stdout}{r.stderr}"
        assert "actionable" in r.stdout, r.stdout
    print("PASS  Quill trailing-space edit refuses")


def test_quill_leading_space_edit_refuses():
    with tempfile.TemporaryDirectory() as tmp:
        b = build(tmp)
        quill, parent = add_expected_quill_patch(tmp, b)
        open(os.path.join(quill, "quill.txt"), "w").write(" patched\n")
        open(os.path.join(parent, "f.txt"), "a").write("remote advance\n")
        git(parent, "add", "f.txt")
        git(parent, "commit", "-qm", "remote advance")
        git(parent, "push", "-q", "origin", "main")
        r = run_sync(b)
        assert r.returncode == 78, f"{r.returncode}: {r.stdout}{r.stderr}"
        assert "actionable" in r.stdout, r.stdout
    print("PASS  Quill leading-space edit refuses")


def test_changed_patch_refuses():
    with tempfile.TemporaryDirectory() as tmp:
        b = build(tmp)
        _, parent = add_expected_quill_patch(tmp, b)
        patch = os.path.join(parent, "tools", "dictation-rig", "patches", "0001.patch")
        open(patch, "a").write("# changed remotely\n")
        git(parent, "add", "tools/dictation-rig/patches/0001.patch")
        git(parent, "commit", "-qm", "change tracked Quill patch")
        git(parent, "push", "-q", "origin", "main")
        r = run_sync(b)
        assert r.returncode == 78, f"{r.returncode}: {r.stdout}{r.stderr}"
        assert "incoming update changes Quill gitlink, tracked patches, or .gitmodules" in r.stdout, repr(r.stdout + r.stderr)
    print("PASS  incoming changed tracked patch refuses")


def test_moved_quill_gitlink_refuses():
    with tempfile.TemporaryDirectory() as tmp:
        b = build(tmp)
        quill, parent = add_expected_quill_patch(tmp, b)
        open(os.path.join(quill, "quill.txt"), "w").write("locally committed move\n")
        git(quill, "add", "quill.txt")
        git(quill, "commit", "-qm", "local quill move")
        open(os.path.join(parent, "f.txt"), "a").write("remote advance\n")
        git(parent, "add", "f.txt")
        git(parent, "commit", "-qm", "remote advance")
        git(parent, "push", "-q", "origin", "main")
        r = run_sync(b)
        assert r.returncode == 78, f"{r.returncode}: {r.stdout}{r.stderr}"
        assert "actionable" in r.stdout, r.stdout
    print("PASS  moved Quill gitlink refuses")


def test_incoming_quill_gitlink_refuses():
    with tempfile.TemporaryDirectory() as tmp:
        b = build(tmp)
        _, parent = add_expected_quill_patch(tmp, b)
        remote_quill = os.path.join(parent, "tools", "dictation-rig", "vendor", "quill")
        open(os.path.join(remote_quill, "quill.txt"), "w").write("upstream moved\n")
        git(remote_quill, "add", "quill.txt")
        git(remote_quill, "commit", "-qm", "move incoming quill gitlink")
        git(remote_quill, "push", "-q", "origin", "HEAD:main")
        git(parent, "add", "tools/dictation-rig/vendor/quill")
        git(parent, "commit", "-qm", "advance incoming quill gitlink")
        git(parent, "push", "-q", "origin", "main")
        r = run_sync(b)
        assert r.returncode == 78, f"{r.returncode}: {r.stdout}{r.stderr}"
        assert "incoming update changes Quill gitlink, tracked patches, or .gitmodules" in r.stdout, repr(r.stdout + r.stderr)
    print("PASS  incoming moved Quill gitlink refuses")


def test_incoming_gitmodules_change_refuses():
    with tempfile.TemporaryDirectory() as tmp:
        b = build(tmp)
        _, parent = add_expected_quill_patch(tmp, b)
        modules = os.path.join(parent, ".gitmodules")
        open(modules, "a").write("# submodule identity review required\n")
        git(parent, "add", ".gitmodules")
        git(parent, "commit", "-qm", "change submodule configuration")
        git(parent, "push", "-q", "origin", "main")
        r = run_sync(b)
        assert r.returncode == 78, f"{r.returncode}: {r.stdout}{r.stderr}"
        assert "incoming update changes Quill gitlink, tracked patches, or .gitmodules" in r.stdout, repr(r.stdout + r.stderr)
        assert behind(b) == 1, "fleet-sync must not advance across .gitmodules changes"
    print("PASS  incoming .gitmodules change refuses")


def test_exact_tree_refuses_duplicate_changed_line():
    with tempfile.TemporaryDirectory() as tmp:
        b = build(tmp)
        quill, _ = add_expected_quill_patch(tmp, b)
        open(os.path.join(quill, "quill.txt"), "w").write("patched\npatched\n")
        assert line_classifier_accepts(quill), "precondition: line-set classifier accepts duplicate"
        require_exact_refusal(b)
    print("PASS  exact Git tree refuses duplicate changed line")


def test_exact_tree_refuses_wrong_file_relocation():
    with tempfile.TemporaryDirectory() as tmp:
        b = build(tmp)
        quill, _ = add_expected_quill_patch(tmp, b)
        open(os.path.join(quill, "quill.txt"), "w").write("base\n")
        open(os.path.join(quill, "other.txt"), "w").write("patched\n")
        assert line_classifier_accepts(quill), "precondition: line-set classifier loses paths"
        require_exact_refusal(b)
    print("PASS  exact Git tree refuses changed lines relocated to wrong file")


def test_exact_tree_refuses_binary_edit():
    with tempfile.TemporaryDirectory() as tmp:
        b = build(tmp)
        quill, _ = add_expected_quill_patch(tmp, b)
        open(os.path.join(quill, "blob.bin"), "wb").write(b"\x00changed\n")
        assert line_classifier_accepts(quill), "precondition: line-set classifier loses binary dirt"
        require_exact_refusal(b)
    print("PASS  exact Git tree refuses binary edit")


def test_exact_tree_refuses_executable_mode_edit():
    with tempfile.TemporaryDirectory() as tmp:
        b = build(tmp)
        quill, _ = add_expected_quill_patch(tmp, b)
        os.chmod(os.path.join(quill, "script.sh"), 0o755)
        assert line_classifier_accepts(quill), "precondition: line-set classifier loses mode dirt"
        require_exact_refusal(b)
    print("PASS  exact Git tree refuses executable-mode edit")


def test_exact_tree_refuses_local_submodule_head_mismatch():
    with tempfile.TemporaryDirectory() as tmp:
        b = build(tmp)
        quill, _ = add_expected_quill_patch(tmp, b)
        open(os.path.join(quill, "local-head.txt"), "w").write("local commit\n")
        git(quill, "add", "local-head.txt")
        git(quill, "commit", "-qm", "move local submodule head")
        require_exact_refusal(b, "checked-out Quill HEAD differs")
    print("PASS  exact Git tree refuses local submodule HEAD mismatch")


def test_exact_tree_ignores_untracked_submodule_scratch():
    with tempfile.TemporaryDirectory() as tmp:
        b = build(tmp)
        quill, _ = add_expected_quill_patch(tmp, b)
        scratch = os.path.join(quill, "untracked-build-output.bin")
        open(scratch, "wb").write(b"scratch")
        exact, reason = submodule_tree_is_exact_patch(b)
        assert exact, reason
    print("PASS  exact Git tree intentionally ignores untracked submodule scratch")


def test_exact_tree_refuses_deleted_file_resurrection():
    with tempfile.TemporaryDirectory() as tmp:
        b = build(tmp)
        quill, _ = add_expected_quill_patch(tmp, b)
        add_fixture_patch(
            b, "0002-delete-other.patch",
            "diff --git a/other.txt b/other.txt\n"
            "deleted file mode 100644\n"
            "--- a/other.txt\n"
            "+++ /dev/null\n"
            "@@ -1 +0,0 @@\n"
            "-base\n",
        )
        assert line_classifier_accepts(quill), "precondition: line-set classifier loses deletion path"
        require_exact_refusal(b)
    print("PASS  exact Git tree refuses deleted-file resurrection")


def test_exact_tree_refuses_rename_old_path_resurrection():
    with tempfile.TemporaryDirectory() as tmp:
        b = build(tmp)
        quill, _ = add_expected_quill_patch(tmp, b)
        add_fixture_patch(
            b, "0002-rename-other.patch",
            "diff --git a/other.txt b/renamed.txt\n"
            "similarity index 100%\n"
            "rename from other.txt\n"
            "rename to renamed.txt\n",
        )
        open(os.path.join(quill, "renamed.txt"), "w").write("base\n")
        assert line_classifier_accepts(quill), "precondition: line-set classifier loses rename paths"
        require_exact_refusal(b)
    print("PASS  exact Git tree refuses rename old-path resurrection")


def test_exact_tree_accepts_canonical_rename():
    with tempfile.TemporaryDirectory() as tmp:
        b = build(tmp)
        quill, _ = add_expected_quill_patch(tmp, b)
        add_fixture_patch(
            b, "0002-rename-other.patch",
            "diff --git a/other.txt b/renamed.txt\n"
            "similarity index 100%\n"
            "rename from other.txt\n"
            "rename to renamed.txt\n",
        )
        open(os.path.join(quill, "renamed.txt"), "w").write("base\n")
        os.unlink(os.path.join(quill, "other.txt"))
        exact, reason = submodule_tree_is_exact_patch(b)
        assert exact, reason
    print("PASS  exact Git tree accepts canonical rename")


def test_exact_tree_accepts_canonical_new_file_patch():
    with tempfile.TemporaryDirectory() as tmp:
        b = build(tmp)
        quill, _ = add_expected_quill_patch(tmp, b)
        add_fixture_patch(
            b, "0002-add-generated-source.patch",
            "diff --git a/new-source.txt b/new-source.txt\n"
            "new file mode 100644\n"
            "--- /dev/null\n"
            "+++ b/new-source.txt\n"
            "@@ -0,0 +1 @@\n"
            "+canonical addition\n",
        )
        open(os.path.join(quill, "new-source.txt"), "w").write("canonical addition\n")
        exact, reason = submodule_tree_is_exact_patch(b)
        assert exact, reason
    print("PASS  exact Git tree accepts canonical patch-added file")


def main():
    if not os.path.exists(SCRIPT):
        print(f"fleet-sync-selftest: {SCRIPT} missing", file=sys.stderr)
        return 1
    test_dirty_tree_refuses()
    test_clean_tree_fast_forwards()
    test_existing_board_agent_is_rebound_after_main_sync()
    test_already_current_is_a_noop()
    test_non_fleet_xpc_identity_stays_external()
    test_direct_handoff_injection_fails_closed()
    test_active_self_contract_reaches_wrapper_receipt()
    test_active_self_change_failure_reaches_wrapper_receipt()
    test_wrong_run_key_cannot_attest_active_self()
    test_wrong_command_cannot_attest_active_self()
    test_refused_wrapper_never_executes_supplied_child()
    test_forged_xpc_without_loaded_pid_fails_closed()
    test_second_launchctl_mismatch_fails_closed()
    test_non_main_branch_refuses()
    test_untracked_scratch_is_ignored()
    test_expected_quill_patch_allows_unrelated_fast_forward()
    test_extra_quill_edit_refuses()
    test_quill_trailing_space_edit_refuses()
    test_quill_leading_space_edit_refuses()
    test_changed_patch_refuses()
    test_moved_quill_gitlink_refuses()
    test_incoming_quill_gitlink_refuses()
    test_incoming_gitmodules_change_refuses()
    test_exact_tree_refuses_duplicate_changed_line()
    test_exact_tree_refuses_wrong_file_relocation()
    test_exact_tree_refuses_binary_edit()
    test_exact_tree_refuses_executable_mode_edit()
    test_exact_tree_refuses_local_submodule_head_mismatch()
    test_exact_tree_ignores_untracked_submodule_scratch()
    test_exact_tree_refuses_deleted_file_resurrection()
    test_exact_tree_refuses_rename_old_path_resurrection()
    test_exact_tree_accepts_canonical_rename()
    test_exact_tree_accepts_canonical_new_file_patch()
    print("fleet-sync cases passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
