#!/usr/bin/env python3
"""Verify the Flash desk with local fixtures and no model or network traffic."""

from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import urllib.error
from pathlib import Path
from unittest.mock import patch

sys.path.append(str(Path(__file__).resolve().parents[2] / "lib"))
from selftest_harness import temporary_directory

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import desks  # noqa: E402
import flash_wire  # noqa: E402

FAILURES: list[str] = []


def check(label, fn):
    try:
        fn()
    except AssertionError as exc:
        FAILURES.append(f"{label}: {exc}")
        print(f"  FAIL  {label}\n          {exc}")
    except Exception as exc:  # noqa: BLE001
        FAILURES.append(f"{label}: unexpected {exc!r}")
        print(f"  FAIL  {label}\n          unexpected {exc!r}")
    else:
        print(f"  ok    {label}")






class Reply:
    def __init__(self, payload):
        self.body = io.BytesIO(json.dumps(payload).encode())
        self.status = 200

    def __enter__(self):
        return self.body

    def __exit__(self, *_):
        return False


def opener_returning(payload):
    def opener(req, timeout=None):
        opener.sent = json.loads(req.data)
        return Reply(payload)
    return opener


def test_wire_answers_with_thinking_off():
    opener = opener_returning({"choices": [{"message": {"content": " 42 "}, "finish_reason": "stop"}]})
    out = flash_wire.run_turn("6 x 7?", opener=opener)
    assert out == {"status": "completed", "result": "42", "finish": "stop"}, out
    assert opener.sent["chat_template_kwargs"] == {"enable_thinking": False}
    assert opener.sent["messages"] == [{"role": "user", "content": "6 x 7?"}]


def test_wire_empty_reply_is_no_answer_not_completed():
    out = flash_wire.run_turn("q", opener=opener_returning(
        {"choices": [{"message": {"content": ""}, "finish_reason": "length"}]}))
    assert out["status"] == "failed" and out["detail"] == "no_answer" and out["finish"] == "length", out


def test_wire_unreachable_and_malformed_fail_cleanly():
    def down(req, timeout=None):
        raise urllib.error.URLError("refused")
    assert flash_wire.run_turn("q", opener=down)["status"] == "failed"
    assert flash_wire.run_turn("q", opener=opener_returning({"nope": 1}))["detail"] == "flash reply was malformed"


def test_registry_takes_a_flash_local_desk_naming_model_and_effort():
    with tempfile.TemporaryDirectory() as d:
        reg = desks.Registry(Path(d) / "desks.json")
        entry = reg.register("flash-model", "flash-local")
        assert entry["model"] == "flash" and entry["effort"], entry
        assert reg.resolve("flash-model")["kind"] == "flash-local"










# ── script tasks: a queued task that names its data runs Flash's script protocol (tools/flash-script.py) ──────────
import os  # noqa: E402



def data_root():
    root = temporary_directory(prefix="flash-inputs-")
    with open(os.path.join(root, "sales.csv"), "w") as fh:
        fh.write("a,b\n1,2\n")
    os.mkdir(os.path.join(root, "leases"))
    with open(os.path.join(root, "leases", "one.txt"), "w") as fh:
        fh.write("rent 10\n")
    return os.path.realpath(root)


def test_script_inputs_takes_named_data_under_an_allowed_root():
    root = data_root()
    text = f"Total column b\ndata: {root}/sales.csv\ndata: {root}/leases\nper month"
    paths, question, err = flash_wire.script_inputs(text, roots=[root])
    assert err is None and paths == [f"{root}/sales.csv", f"{root}/leases"], (paths, err)
    assert "data:" not in question and "Total column b" in question and "per month" in question, question


def test_script_inputs_refuses_data_outside_the_roots_or_through_a_link():
    root = data_root()
    outside = temporary_directory(prefix="flash-outside-")
    for text in (f"q\ndata: {outside}", f"q\ndata: {root}/../{os.path.basename(outside)}",
                 f"q\ndata: {root}/missing.csv", f"q\ndata: {root}"):
        paths, _, err = flash_wire.script_inputs(text, roots=[root])
        assert paths is None and err, (text, paths, err)
    os.symlink(outside, os.path.join(root, "leases", "escape"))
    paths, _, err = flash_wire.script_inputs(f"q\ndata: {root}/leases", roots=[root])
    assert paths is None and "link" in err, (paths, err)
    os.symlink(os.path.join(outside), os.path.join(root, "alias"))
    paths, _, err = flash_wire.script_inputs(f"q\ndata: {root}/alias", roots=[root])
    assert paths is None and err, (paths, err)


def test_script_inputs_with_no_data_lines_is_not_a_script_task():
    assert flash_wire.script_inputs("just a question", roots=["/nowhere"]) == (None, "just a question", None)


def test_a_script_run_ends_before_the_queue_claim_expires():
    assert flash_wire.SCRIPT_TIMEOUT_S < 900 and flash_wire.SCRIPT_TIMEOUT_S <= flash_wire.TIMEOUT_S


def test_flash_desk_without_data_keeps_the_direct_protocol():
    opener = opener_returning({"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}]})
    out = flash_wire.run_task("6 x 7?", roots=["/nowhere"], opener=opener)
    assert out["status"] == "completed" and opener.sent["messages"][0]["content"] == "6 x 7?", out


# ── code tasks: a queued task that names a project and a test runs flash-run on a new branch of that project ──────
import subprocess  # noqa: E402
import time  # noqa: E402





def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def code_root():
    """A folder of projects: one git project (`app`, with one test script), one plain folder and one link."""
    root = os.path.realpath(temporary_directory(prefix="flash-projects-"))
    app = os.path.join(root, "app")
    os.makedirs(os.path.join(app, "tests"))
    with open(os.path.join(app, "calc.py"), "w") as fh:
        fh.write("def add(a, b):\n    return a - b\n")
    with open(os.path.join(app, "tests", "check.py"), "w") as fh:
        fh.write("import sys\nsys.path.insert(0, '.')\nimport calc\nassert calc.add(2, 2) == 4\n")
    _git(app, "init", "-q", "-b", "main")
    _git(app, "add", "-A")
    _git(app, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "base")
    os.makedirs(os.path.join(root, "plain"))
    os.symlink(app, os.path.join(root, "alias"))
    return root


def code_body(root, project="app", test="python3 tests/check.py", extra="Fix add so the test passes."):
    lines = [extra, f"project: {root}/{project}"]
    if test is not None:
        lines.append(f"test: {test}")
    return "\n".join(lines)


def test_code_inputs_takes_an_allowed_git_project_and_a_bounded_test():
    root = code_root()
    spec, question, err = flash_wire.code_inputs(code_body(root), roots=[root])
    assert err is None and spec["project"] == f"{root}/app", (spec, err)
    assert spec["test_argv"] == ["python3", "tests/check.py"], spec
    assert "project:" not in question and "test:" not in question and "Fix add" in question, question
    spec, _, err = flash_wire.code_inputs(code_body(root, test="pytest -q -x tests/check.py::test_one"),
                                          roots=[root])
    assert err is None and spec["test_argv"] == ["python3", "-m", "pytest", "-q", "-x",
                                                 "tests/check.py::test_one"], (spec, err)
    spec, _, err = flash_wire.code_inputs(code_body(root, test="node --test tests"), roots=[root])
    assert err is None and spec["test_argv"] == ["node", "--test", "tests"], (spec, err)


def test_code_inputs_refuses_a_disallowed_project():
    root = code_root()
    other = code_root()
    for body in (code_body(other), code_body(root, project="../" + os.path.basename(other) + "/app"),
                 code_body(root, project="missing"), f"x\nproject: {root}\ntest: pytest"):
        spec, _, err = flash_wire.code_inputs(body, roots=[root])
        assert spec is None and err, (body, spec, err)
    spec, _, err = flash_wire.code_inputs(code_body(root), roots=[])
    assert spec is None and "configured" in err, err
    spec, _, err = flash_wire.code_inputs(code_body(root) + f"\nproject: {root}/app", roots=[root])
    assert spec is None and "one project" in err, err


def test_code_inputs_refuses_a_symlinked_project():
    root = code_root()
    spec, _, err = flash_wire.code_inputs(code_body(root, project="alias"), roots=[root])
    assert spec is None and "link" in err, err


def test_code_inputs_refuses_a_folder_that_is_not_a_git_work_tree():
    root = code_root()
    spec, _, err = flash_wire.code_inputs(code_body(root, project="plain"), roots=[root])
    assert spec is None and "git" in err, err
    # a subfolder of a git project is not that project's own work tree either
    spec, _, err = flash_wire.code_inputs(code_body(root, project="app/tests"), roots=[root])
    assert spec is None and "git" in err, err


INJECTIONS = [
    "pytest; rm -rf ~", "pytest && id", "pytest | sh", "pytest $(whoami)", "pytest `id`",
    "python3 tests/check.py > /etc/passwd", "bash -c id", "sh tests/check.py", "npm test", "make test",
    "python3 -c print(1)", "python3 /etc/hosts.py", "python3 ../other/check.py", "python3 tests/../../x.py",
    "pytest --basetemp=/Users", "pytest -p evil", "pytest -c /etc/hosts", "pytest /etc", "pytest ~/secrets",
    "node -e process.exit(0)", "node --test --require=/tmp/x.js", "pytest 'tests/check.py'",
    "pytest tests/check.py\\", "python3 tests/check.py --out=$HOME", "", "pytest -k",
    "python3 tests/missing.py", "env X=1 pytest", "/usr/bin/python3 tests/check.py", "python3 -m http.server",
]


def test_injection_attempts_in_the_test_line_are_refused():
    root = code_root()
    for test in INJECTIONS:
        spec, _, err = flash_wire.code_inputs(code_body(root, test=test), roots=[root])
        assert spec is None and err, (test, spec)
    spec, _, err = flash_wire.code_inputs(code_body(root, test=None), roots=[root])
    assert spec is None and "test" in err, err
    spec, _, err = flash_wire.code_inputs(code_body(root) + "\ntest: pytest", roots=[root])
    assert spec is None and err, err


def test_a_test_script_that_is_a_link_is_refused():
    root = code_root()
    os.symlink("/etc/hosts", os.path.join(root, "app", "tests", "link.py"))
    spec, _, err = flash_wire.code_inputs(code_body(root, test="python3 tests/link.py"), roots=[root])
    assert spec is None and err, (spec, err)


def test_a_task_naming_both_data_and_a_project_is_refused_on_both_sides():
    root = code_root()
    body = code_body(root) + "\ndata: /tmp/x.csv"
    assert flash_wire.code_inputs(body, roots=[root])[2]
    assert flash_wire.script_inputs(body, roots=[root])[2]




FAKE_FLASH_RUN = """import os, sys
args = sys.argv[1:]
cwd = args[args.index("--cwd") + 1]
assert args[args.index("--escalate") + 1] == "suggest", args
assert args[args.index("--test") + 1] == "python3 tests/check.py", args
assert args[args.index("--sandbox") + 1] == "on" and "--no-learn" not in args, args
mode = os.environ.get("FAKE_FLASH_MODE", "fix")
if mode == "swapgit":
    # model code that escaped would swap the worktree's .git pointer for a directory whose config runs a hook
    with open(os.path.join(cwd, "calc.py"), "w") as fh:
        fh.write("def add(a, b):\\n    return a + b\\n")
    os.unlink(os.path.join(cwd, ".git"))
    os.makedirs(os.path.join(cwd, ".git"))
    with open(os.path.join(cwd, ".git", "config"), "w") as fh:
        fh.write("[core]\\n\\tfsmonitor = " + os.environ["FAKE_HOOK"] + "\\n")
    sys.exit(0)
if mode == "sleep":
    import subprocess, time
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    open(os.environ["FAKE_FLASH_PID"], "w").write(str(child.pid))
    time.sleep(60)
if mode == "fix":
    with open(os.path.join(cwd, "calc.py"), "w") as fh:
        fh.write("def add(a, b):\\n    return a + b\\n")
    sys.exit(0)
sys.exit(5)
"""


def fake_flash_run():
    path = os.path.join(temporary_directory(prefix="fake-flash-run-"), "flash-run.py")
    with open(path, "w") as fh:
        fh.write(FAKE_FLASH_RUN)
    return [sys.executable, path]


def _out(project, *args):
    return subprocess.run(["git", *args], cwd=project, capture_output=True, text=True).stdout


def _run_with(mode, spec, **kw):
    saved = dict(os.environ)
    os.environ["FAKE_FLASH_MODE"] = mode
    try:
        return flash_wire._run_flash_code("Fix add", spec, "t_code0001", command=fake_flash_run(), **kw)
    finally:
        os.environ.clear()
        os.environ.update(saved)


def test_code_run_lands_on_a_new_branch_and_never_touches_the_project_tree():
    root = code_root()
    project = f"{root}/app"
    spec, _, err = flash_wire.code_inputs(code_body(root), roots=[root])
    assert err is None, err
    row = _run_with("fix", spec)
    assert row["outcome"] == "success", row
    branch = row["branch"]
    assert branch.startswith("flash/queue-t_code0001") and branch in _out(project, "branch").split(), row
    assert _out(project, "symbolic-ref", "--short", "HEAD").strip() == "main"
    with open(os.path.join(project, "calc.py")) as fh:
        assert "a - b" in fh.read(), "the real tree must be unchanged"
    assert "a + b" in _out(project, "show", f"{branch}:calc.py")
    assert len(_out(project, "log", "--oneline", "main").splitlines()) == 1
    assert len(_out(project, "worktree", "list").strip().splitlines()) == 1
    assert _out(project, "status", "--porcelain") == ""
    assert "calc.py" in row["text"] and "+    return a + b" in row["text"], row
    assert branch in row["summary"], row


def test_code_run_desk_git_never_trusts_a_swapped_git_in_the_worktree():
    # Re-review of #1324, finding 1 (layer b), end to end: even if model code swapped the worktree's .git for a
    # directory whose config names a hook, the desk's git reads the worktree's real admin dir, so the hook never runs
    # and the change still lands on the new branch.
    root = code_root()
    project = f"{root}/app"
    spec, _, err = flash_wire.code_inputs(code_body(root), roots=[root])
    assert err is None, err
    hookdir = temporary_directory(prefix="flash-canary-")
    canary = os.path.join(hookdir, "CANARY")
    hook = os.path.join(hookdir, "hook.sh")
    with open(hook, "w") as fh:
        fh.write(f"#!/bin/sh\ntouch '{canary}'\n")
    os.chmod(hook, 0o755)
    os.environ["FAKE_HOOK"] = hook
    try:
        row = _run_with("swapgit", spec)
    finally:
        os.environ.pop("FAKE_HOOK", None)
    assert not os.path.exists(canary), f"the desk's git ran a hook planted in the worktree: {row}"
    assert row["outcome"] == "success", row
    assert "a + b" in _out(project, "show", f"{row['branch']}:calc.py"), row
    assert _out(project, "symbolic-ref", "--short", "HEAD").strip() == "main"


def test_a_code_tree_edited_after_the_recheck_pin_is_never_committed():
    # Third review of #1324, finding 2: something that escaped every kill could edit the tree between the desk's
    # re-check and its commit. Here the re-check's own test script does exactly that (it passes, then rewrites
    # calc.py); the desk's pinned snapshot no longer matches what would be staged, so it blocks and keeps no branch.
    root = code_root()
    project = f"{root}/app"
    with open(os.path.join(project, "tests", "sneaky.py"), "w") as fh:
        fh.write("import sys\nsys.path.insert(0, '.')\nimport calc\nassert calc.add(2, 2) == 4\n"
                 "open('calc.py', 'a').write('# slipped in after the check\\n')\n")
    _git(project, "add", "tests/sneaky.py")
    _git(project, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "sneaky")
    argv = ["python3", "tests/sneaky.py"]
    spec = {"project": project, "test_argv": argv, "test": "python3 tests/check.py"}
    row = _run_with("fix", spec)
    assert row["outcome"] == "blocked" and "changed between" in row["summary"], row
    assert _out(project, "branch").split() == ["*", "main"], row


def test_a_failed_code_run_leaves_no_branch_and_no_worktree():
    root = code_root()
    project = f"{root}/app"
    spec, _, _ = flash_wire.code_inputs(code_body(root), roots=[root])
    row = _run_with("fail", spec)
    assert row["outcome"] == "blocked" and _out(project, "branch").split() == ["*", "main"], row
    assert len(_out(project, "worktree", "list").strip().splitlines()) == 1


def test_a_code_run_ends_before_the_queue_claim_expires():
    assert flash_wire.CODE_TIMEOUT_S < 900 and flash_wire.CODE_TIMEOUT_S <= flash_wire.SCRIPT_TIMEOUT_S


def test_a_code_run_timeout_kills_the_whole_process_group():
    root = code_root()
    project = f"{root}/app"
    spec, _, _ = flash_wire.code_inputs(code_body(root), roots=[root])
    pidfile = os.path.join(temporary_directory(), "pid")
    os.environ["FAKE_FLASH_PID"] = pidfile
    try:
        row = _run_with("sleep", spec, timeout=3)
    finally:
        os.environ.pop("FAKE_FLASH_PID", None)
    assert row["outcome"] == "blocked" and "3s" in row["summary"], row
    child = int(open(pidfile).read())
    time.sleep(0.3)
    state = subprocess.run(["ps", "-o", "stat=", "-p", str(child)], capture_output=True, text=True).stdout.strip()
    assert not state or state.startswith("Z"), f"grandchild {child} survived ({state})"
    assert _out(project, "branch").split() == ["*", "main"]
    assert len(_out(project, "worktree", "list").strip().splitlines()) == 1


# ── the origin gate holds on EVERY route to the code path, not only target=auto (re-review of #1324, finding 2) ──
def _must_not_run(*_a):
    raise AssertionError("the code runner must not run")


def test_a_direct_room_turn_naming_a_project_never_reaches_the_code_path():
    root = code_root()
    out = flash_wire.run_task(code_body(root), roots=[], code_roots=[root], code_runner=_must_not_run,
                              code_origins=["browser-human:joe"])
    assert out["status"] == "completed" and "refused" in out["result"], out


def main() -> int:
    grant = patch.object(flash_wire.flashlib, "is_switched_off", return_value=False)
    grant.start()
    check("wire answers with thinking off", test_wire_answers_with_thinking_off)
    check("wire empty reply is no_answer", test_wire_empty_reply_is_no_answer_not_completed)
    check("wire unreachable and malformed fail cleanly", test_wire_unreachable_and_malformed_fail_cleanly)
    check("registry takes a flash-local desk", test_registry_takes_a_flash_local_desk_naming_model_and_effort)
    for name, fn in list(globals().items()):
        if name.startswith("test_") and any(k in name for k in ("script", "flash_desk", "protocol_mark", "code",
                                                                "project", "injection")):
            check(name[5:].replace("_", " "), fn)
    if FAILURES:
        print(f"{len(FAILURES)} flash desk test(s) failed", file=sys.stderr)
        return 1
    print("all flash desk unit tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
