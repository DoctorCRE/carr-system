#!/usr/bin/env python3
"""Offline checks for tools/flash-script.py: a scripted Flash and a fixed Jev drive the real loop, with real script
runs in a real throwaway folder. No Flash server, no Jev, no network. The live runs are recorded in the PR."""
from __future__ import annotations

import atexit
import functools
import importlib.util
import json
import os
import shutil
import socket
import sys
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("flash_script", os.path.join(HERE, "flash-script.py"))
assert spec and spec.loader
fs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fs)

FAILURES: list[str] = []
SANDBOXED = os.path.exists(fs.SANDBOX_EXEC)
# The loop tests run real scripts. On the Mac they run inside the real sandbox; where there is no sandbox (Linux CI)
# they opt out in code, never through the environment, because the CLI has no way to reach sandbox=False.
RUNNER = fs.run_code if SANDBOXED else functools.partial(fs.run_code, sandbox=False)
TEMPS: list[str] = []
atexit.register(lambda: [shutil.rmtree(d, ignore_errors=True) for d in TEMPS])


def solve(*args, **kw):
    return fs.solve(*args, runner=kw.pop("runner", RUNNER), **kw)


def tmpdir(prefix="fs-test-"):
    d = tempfile.mkdtemp(prefix=prefix)
    TEMPS.append(d)
    return d


def check(label, fn):
    try:
        fn()
    except AssertionError as exc:
        FAILURES.append(label)
        print(f"  FAIL  {label}: {exc}")
    else:
        print(f"  ok    {label}")


class FixedJev:
    def __init__(self, flags=None, pre=None, covers=0.0):
        self.flag_rows, self.pre_row, self.covers, self.errors = list(flags or []), pre or {}, covers, 0

    def pre(self, q, prev):
        return self.pre_row

    def flags(self, q, out, prev):
        return self.flag_rows.pop(0) if self.flag_rows else {}

    def covers_all(self, q, answer, total):
        return self.covers


class ScriptedFlash:
    """Replies in order; records every call's messages, token cap and thinking switch."""
    def __init__(self, replies):
        self.replies, self.calls = list(replies), []

    def __call__(self, msgs, max_tokens=None, think=True):
        self.calls.append({"msgs": [dict(m) for m in msgs], "max_tokens": max_tokens, "think": think})
        r = self.replies.pop(0)
        return (r, "stop", 10, "") if isinstance(r, str) else r


def folder(lines):
    work = tmpdir()
    with open(os.path.join(work, "rows.txt"), "w") as fh:
        fh.write("\n".join(lines) + "\n")
    return work


def fence(code):
    return "```python\n" + code + "\n```"


def test_script_runs_in_the_folder_and_a_printed_answer_is_grounded():
    work = folder(["a 3", "b 4", "c 5"])
    flash = ScriptedFlash([fence("print('total', sum(int(l.split()[1]) for l in open('rows.txt')))"), "FINAL: 12"])
    answer, log = solve("Sum the numbers?", work, ["rows.txt"], chat_fn=flash, jev=FixedJev())
    assert answer == "12" and log[-1]["support"] == "printed", log
    assert "rows.txt (" in flash.calls[0]["msgs"][1]["content"]  # the preview, never the whole file


def test_every_script_turn_is_capped():
    flash = ScriptedFlash([fence("print('total', 7)"), "FINAL: 7"])
    solve("q", folder(["x"]), ["rows.txt"], chat_fn=flash, jev=FixedJev())
    assert all(c["max_tokens"] == fs.COMPUTE_TOKENS for c in flash.calls), flash.calls


def test_a_runaway_turn_is_followed_by_one_without_thinking():
    flash = ScriptedFlash([("", "length", 12288, "thinking..."), fence("print('total', 7)"), "FINAL: 7"])
    answer, log = solve("q", folder(["x"]), ["rows.txt"], chat_fn=flash, jev=FixedJev())
    assert flash.calls[1]["think"] is False and flash.calls[2]["think"] is True, [c["think"] for c in flash.calls]
    assert log[1]["no_think_next"] and answer == "7"


def test_a_made_up_answer_is_marked_invented():
    flash = ScriptedFlash([fence("print('lines 1500')"), 'FINAL: {"a": 300, "b": 300, "c": 300}'])
    answer, log = solve("q", folder(["x"]), ["rows.txt"], chat_fn=flash, jev=FixedJev())
    assert log[-1]["support"] == "invented", log[-1]


def test_jev_flag_is_logged_as_review_advice():
    flash = ScriptedFlash([fence("print('matched 1')"), fence("print('matched 900')"), "FINAL: 900"])
    _, log = solve("q", folder(["x"]), ["rows.txt"], chat_fn=flash, jev=FixedJev(flags=[{"misparsed": 0.9}]))
    assert any(e.get("jev_flags", {}).get("misparsed") == .9 for e in log)
    assert "Reviewer note" not in flash.calls[1]["msgs"][-1]["content"]


def test_answer_ready_cannot_refuse_another_script():
    flash = ScriptedFlash([fence("print('total 5')"), fence("print('again')"), "FINAL: 5"])
    answer, log = solve("q", folder(["x"]), ["rows.txt"], chat_fn=flash,
                           jev=FixedJev(flags=[{"answer_ready": 0.9}]))
    assert answer == "5" and not any(e.get("focus_enforced") for e in log), log
    assert sum(1 for e in log if "run_s" in e) == 2


def test_final_file_answer_is_read_from_the_working_folder_only():
    flash = ScriptedFlash([fence("import json; json.dump(['A', 'B'], open('final_answer.json', 'w')); print('wrote 2')"),
                           "FINAL: @final_answer.json"])
    answer, log = solve("q", folder(["x"]), ["rows.txt"], chat_fn=flash, jev=FixedJev())
    assert json.loads(answer) == ["A", "B"], answer


def test_final_file_followed_by_code_runs_the_code_first():
    code = "import json; json.dump([1, 2], open('out.json', 'w'))"
    flash = ScriptedFlash([fence("print('rows 2')"), "FINAL: @out.json\n" + fence(code)])
    answer, log = solve("q", folder(["x"]), ["rows.txt"], chat_fn=flash, jev=FixedJev())
    assert json.loads(answer) == [1, 2] and any(e.get("final_script_run") for e in log), log


def test_count_gap_semantics_routes_to_review_without_extra_script():
    flash = ScriptedFlash([fence("print('lines: 10')"), 'FINAL: {"a": 4, "b": 4}', fence("print('a 5 b 5')"),
                           'FINAL: {"a": 5, "b": 5}'])
    answer, log = solve("q", folder(["x"]), ["rows.txt"], chat_fn=flash, jev=FixedJev(covers=0.9))
    assert json.loads(answer) == {"a": 4, "b": 4}, answer
    assert any(e.get("count_gap") == [10, 8] or e.get("count_gap") == (10, 8) for e in log), log


def test_repeated_identical_script_stops():
    s = fence("print(1)")
    answer, log = solve("q", folder(["x"]), ["rows.txt"], chat_fn=ScriptedFlash([s, s]), jev=FixedJev())
    assert answer is None and log[-1].get("stuck"), log


def test_semantic_question_does_not_inject_uncalibrated_hint():
    flash = ScriptedFlash(["FINAL: 1"])
    solve("q", folder(["x"]), ["rows.txt"], chat_fn=flash, jev=FixedJev(pre={"semantic": 0.9}))
    assert "labels depend on the meaning" not in flash.calls[0]["msgs"][1]["content"]


# --- isolation of model-written scripts (independent review of PR #1250) ---------------------------------------

def sandboxed(fn):
    fn.needs_sandbox = True
    return fn


def run(code, work=None):
    work = work or tmpdir()
    fs.stage([], work)
    return fs.run_code(code, work, "t")[0], work


def test_script_inherits_no_credential():  # the env allowlist holds with or without a sandbox
    # (runs everywhere, including Linux CI)
    os.environ["CARR_DB_PASSWORD"] = "planted-secret-value"
    try:
        work = tmpdir()
        fs.stage([], work)
        out = RUNNER("import os; print('seen', os.environ.get('CARR_DB_PASSWORD'))", work, "t")[0]
    finally:
        del os.environ["CARR_DB_PASSWORD"]
    assert "planted-secret-value" not in out and "seen None" in out, out


@sandboxed
def test_script_cannot_read_the_home_folder():
    home = os.path.realpath(os.path.expanduser("~"))
    out, _ = run(f"import os\ntry:\n    print('listed', len(os.listdir({home!r})))\nexcept OSError as e:\n"
                 "    print('refused', type(e).__name__)")
    assert "refused" in out and "listed" not in out, out


@sandboxed
def test_script_cannot_write_outside_its_folder():
    outside = tmpdir("fs-outside-")
    target = os.path.join(outside, "escape.txt")
    out, work = run(f"open('inside.txt', 'w').write('ok')\ntry:\n    open({target!r}, 'w').write('x')\n"
                    "    print('wrote outside')\nexcept OSError:\n    print('refused')")
    assert "refused" in out and not os.path.exists(target), out
    assert os.path.exists(os.path.join(work, "inside.txt")), out


def _listener():
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(4)
    def serve():
        try:
            for _ in range(4):
                srv.accept()[0].close()
        except OSError:  # closed by the test once it has its answer
            pass
    threading.Thread(target=serve, daemon=True).start()
    return srv, srv.getsockname()[1]


@sandboxed
def test_network_is_closed_except_the_flash_port():
    flash_srv, flash_port = _listener()
    other_srv, other_port = _listener()
    saved = fs.FLASH_URL
    fs.FLASH_URL = f"http://127.0.0.1:{flash_port}"
    try:
        probe = ("import socket\nfor port in ({f}, {o}):\n    try:\n"
                 "        socket.create_connection(('127.0.0.1', port), timeout=3).close(); print(port, 'open')\n"
                 "    except OSError:\n        print(port, 'refused')").format(f=flash_port, o=other_port)
        out, _ = run(probe)
    finally:
        fs.FLASH_URL = saved
        flash_srv.close()
        other_srv.close()
    assert f"{flash_port} open" in out and f"{other_port} refused" in out, out


@sandboxed
def test_timeout_kills_the_scripts_children_too():
    saved = fs.RUN_TIMEOUT
    fs.RUN_TIMEOUT = 2
    try:
        out, work = run("import subprocess, sys, time\n"
                        "p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
                        "open('child.pid', 'w').write(str(p.pid))\ntime.sleep(60)")
    finally:
        fs.RUN_TIMEOUT = saved
    assert "[timed out after 2s]" in out, out
    pid = int(open(os.path.join(work, "child.pid")).read())
    time.sleep(0.5)
    try:
        os.kill(pid, 0)
        alive = True
    except ProcessLookupError:
        alive = False
    assert not alive, f"child {pid} outlived the timeout"


@sandboxed
def test_script_can_start_no_program_but_the_interpreter():
    # `open` hands a URL to the unsandboxed browser, `security` reaches the keychain, `osascript` sends Apple Events:
    # each is an exit around the network and file rules, so none may start.
    out, _ = run("import subprocess\n"
                 "for argv in (['/usr/bin/open', '-g', '-a', 'Finder', '.'], ['/usr/bin/security', 'list-keychains'],\n"
                 "             ['/usr/bin/osascript', '-e', 'return 1'], ['/bin/launchctl', 'list']):\n"
                 "    try:\n"
                 "        rc = subprocess.run(argv, capture_output=True, timeout=20).returncode\n"
                 "        print(argv[0], 'ran' if rc == 0 else 'failed', rc)\n"
                 "    except OSError as e:\n"
                 "        print(argv[0], 'refused', type(e).__name__)\n"
                 "import sys\n"
                 "print('python child', subprocess.run([sys.executable, '-c', 'print(7)'], capture_output=True,\n"
                 "      text=True).stdout.strip())")
    assert " ran " not in out + " " and "python child 7" in out, out
    for tool in ("/usr/bin/open", "/usr/bin/security", "/usr/bin/osascript", "/bin/launchctl"):
        assert f"{tool} refused" in out or f"{tool} failed" in out, out


@sandboxed
def test_script_cannot_read_the_shared_temp_folders():
    out, _ = run("import os\nfor d in ('/private/tmp', '/Users/Shared'):\n    try:\n"
                 "        os.listdir(d); print(d, 'listed')\n    except OSError:\n        print(d, 'refused')")
    assert "listed" not in out and out.count("refused") == 2, out


@sandboxed
def test_script_cannot_signal_other_processes():
    import subprocess
    decoy = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        out, _ = run(f"import os, signal\ntry:\n    os.kill({decoy.pid}, signal.SIGTERM); print('signalled')\n"
                     "except OSError as e:\n    print('refused', type(e).__name__)")
        time.sleep(0.3)
        assert "refused" in out and decoy.poll() is None, (out, decoy.poll())
    finally:
        decoy.kill()
        decoy.wait()


@sandboxed
def test_script_cannot_read_homebrew_state():
    probe = ("import os\nfor d in ('/opt/homebrew/var', '/opt/homebrew/etc'):\n"
             "    if not os.path.isdir(d):\n        print(d, 'absent'); continue\n    try:\n"
             "        os.listdir(d); print(d, 'listed')\n    except OSError:\n        print(d, 'refused')")
    out, _ = run(probe)
    assert "listed" not in out, out


@sandboxed
def test_script_cannot_read_the_ssh_config_by_either_name():  # /etc is a link to /private/etc on macOS
    probe = ("import os\nfor d in ('/etc/ssh', '/private/etc/ssh'):\n"
             "    if not os.path.isdir(d):\n        print(d, 'absent'); continue\n    try:\n"
             "        os.listdir(d); print(d, 'listed')\n    except OSError:\n        print(d, 'refused')")
    out, _ = run(probe)
    assert "listed" not in out, out


def test_no_sandbox_means_refusal_not_an_unsandboxed_run():
    saved = fs.SANDBOX_EXEC
    fs.SANDBOX_EXEC = "/nonexistent/sandbox-exec"
    try:
        out, work = run("open('ran.txt', 'w').write('x')")
    finally:
        fs.SANDBOX_EXEC = saved
    assert out.startswith("[refused") and not os.path.exists(os.path.join(work, "ran.txt")), out


def test_cli_refuses_to_start_without_a_sandbox():
    saved = fs.SANDBOX_EXEC
    fs.SANDBOX_EXEC = "/nonexistent/sandbox-exec"
    try:
        rc = fs.main(["q", os.path.join(folder(["x"]), "rows.txt")])
    finally:
        fs.SANDBOX_EXEC = saved
    assert rc == 2, rc


def test_a_non_loopback_flash_url_gets_no_port():
    saved = fs.FLASH_URL
    fs.FLASH_URL = "http://flash.example.invalid:8000"
    try:
        assert fs.flash_port() is None
    finally:
        fs.FLASH_URL = saved


def test_file_answer_of_constants_the_script_wrote_is_not_grounded():
    flash = ScriptedFlash([fence("import json; json.dump({'dental': 318, 'eye care': 319}, open('ans.json', 'w'))"),
                           "FINAL: @ans.json"])
    answer, log = solve("q", folder(["x"]), ["rows.txt"], chat_fn=flash, jev=FixedJev())
    assert log[-1]["support"] == "invented", log


def test_file_answer_the_script_computed_is_grounded():
    work = folder(["dental 318", "eye 319"])
    flash = ScriptedFlash([fence("import json\nd = {l.split()[0]: int(l.split()[1]) for l in open('rows.txt')}\n"
                                 "json.dump(d, open('ans.json', 'w'))"), "FINAL: @ans.json"])
    answer, log = solve("q", work, ["rows.txt"], chat_fn=flash, jev=FixedJev())
    assert json.loads(answer) == {"dental": 318, "eye": 319} and log[-1]["support"] != "invented", log


def test_malformed_flash_reply_is_no_answer_not_a_crash():
    def broken(msgs, max_tokens=None, think=True):
        raise KeyError("choices")
    answer, log = solve("q", folder(["x"]), ["rows.txt"], chat_fn=broken, jev=FixedJev())
    assert answer is None and any(str(e.get("finish", "")).startswith("malformed") for e in log), log


def test_two_inputs_with_one_name_are_refused():
    a, b = tmpdir(), tmpdir()
    for d in (a, b):
        with open(os.path.join(d, "rows.txt"), "w") as fh:
            fh.write("x\n")
    try:
        fs.stage([os.path.join(a, "rows.txt"), os.path.join(b, "rows.txt")], tmpdir())
    except ValueError:
        return
    raise AssertionError("duplicate input names were staged")


def test_preview_skips_a_subfolder_listed_first():
    work = tmpdir()
    os.makedirs(os.path.join(work, "data", "a-sub"))
    with open(os.path.join(work, "data", "b.txt"), "w") as fh:
        fh.write("line1\n")
    text = fs.preview(work, ["data/"])
    assert "b.txt" in text and "line1" in text, text


def main():
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            if getattr(fn, "needs_sandbox", False) and not SANDBOXED:
                print(f"  skip  {name[5:].replace('_', ' ')}: no macOS sandbox here")
                continue
            check(name[5:].replace("_", " "), fn)
    if FAILURES:
        print(f"flash-script: {len(FAILURES)} failed")
        return 1
    print("flash-script: every assertion held")
    return 0


if __name__ == "__main__":
    sys.exit(main())
