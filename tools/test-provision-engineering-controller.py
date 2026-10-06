#!/usr/bin/env python3
"""Offline checks for tools/provision-engineering-controller.py. No Cloudflare, no controller run: wrangler and the
dispatch launcher are replaced by recording fakes, and the env file goes to a temporary folder."""
from __future__ import annotations

import importlib.util
import json
import os
import stat
import sys
import tempfile
import types

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("provision", os.path.join(HERE, "provision-engineering-controller.py"))
assert spec and spec.loader
pv = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pv)
FAILED: list[str] = []


def check(label, fn):
    try:
        fn()
        print(f"  ok    {label}")
    except AssertionError as exc:
        FAILED.append(label)
        print(f"  FAIL  {label}: {exc}")


def done(code=0, out="", err=""):
    return types.SimpleNamespace(returncode=code, stdout=out, stderr=err)


def test_secret_goes_on_stdin_as_the_codex_map_never_argv():
    calls = []
    pv.put_secret("tok-123", run=lambda argv, **kw: calls.append((argv, kw)) or done())
    argv, kw = calls[0]
    assert argv[-3:] == ["secret", "put", "ENGINEERING_CONTROLLER_TOKENS"], argv
    assert "tok-123" not in " ".join(argv)
    assert json.loads(kw["input"]) == {"codex": "tok-123"}


def test_env_file_is_private_and_in_the_launchers_literal_form():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "carr", "engineering-controller.env")
        pv.write_env("tok-abc", path)
        assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
        lines = open(path).read().splitlines()
        assert lines == ["CARR_ENGINEERING_WORKER_URL=https://api.doctorcre.com/mcp",
                         "CARR_ENGINEERING_CONTROLLER_TOKEN=tok-abc"], lines
        assert pv.env_state(path) == "present, mode 600"


def test_env_state_flags_a_readable_file():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "e.env")
        pv.write_env("t", path)
        os.chmod(path, 0o644)
        assert "INVALID" in pv.env_state(path)
        assert pv.env_state(os.path.join(d, "none")) == "missing"


def test_failed_put_reports_one_line_without_the_value():
    try:
        pv.put_secret("tok-secret", run=lambda argv, **kw: done(1, err="Authentication error\nmore"))
    except RuntimeError as exc:
        assert "tok-secret" not in str(exc) and "Authentication error" in str(exc)
    else:
        raise AssertionError("expected a failure")


def test_verify_reads_the_controller_readback():
    ok, detail = pv.verify(run=lambda argv, **kw: done(0, out='{"ok": true, "claimed": 0}'))
    assert ok and "claimed 0" in detail
    ok, detail = pv.verify(run=lambda argv, **kw: done(78, err="engineering-dispatch: X is required"))
    assert not ok and "exit 78" in detail


class FakeWrangler:
    """Records every wrangler/launcher call; `put_fails` makes `secret put` fail."""
    def __init__(self, put_fails=False):
        self.calls, self.put_fails, self.inputs = [], put_fails, []

    def __call__(self, argv, **kw):
        self.calls.append(argv)
        if "list" in argv:
            return done(0, out='[{"name": "ENGINEERING_CONTROLLER_TOKENS"}]')
        if "put" in argv:
            self.inputs.append(kw.get("input", ""))
            return done(1, err="Authentication error") if self.put_fails else done()
        return done(0, out='{"ok": true, "claimed": 0}')


def run_main(argv, fake, d, marker=False):
    import contextlib
    import io
    env_file, marker_file = os.path.join(d, "controller.env"), os.path.join(d, "not-this-host")
    if marker:
        open(marker_file, "w").write("controller runs on the Mac Studio\n")
    saved = (pv.ENV_FILE, pv.NOT_HOST_MARKER, pv.RUN)
    pv.ENV_FILE, pv.NOT_HOST_MARKER, pv.RUN = env_file, marker_file, fake
    out, err = io.StringIO(), io.StringIO()
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = pv.main(argv)
    finally:
        pv.ENV_FILE, pv.NOT_HOST_MARKER, pv.RUN = saved
    return code, out.getvalue() + err.getvalue(), env_file


def test_a_mac_marked_not_the_host_refuses_before_touching_the_worker():
    with tempfile.TemporaryDirectory() as d:
        fake = FakeWrangler()
        code, text, env_file = run_main([], fake, d, marker=True)
        assert code == 1 and "not-this-host" in text, (code, text)
        assert not any("put" in c for c in fake.calls), fake.calls
        assert not os.path.exists(env_file)


def test_check_reports_the_host_marker():
    with tempfile.TemporaryDirectory() as d:
        code, text, _ = run_main(["--check"], FakeWrangler(), d, marker=True)
        assert code == 0 and "marked not-this-host" in text, text


def test_failed_put_leaves_no_file_and_says_the_worker_is_unchanged():
    with tempfile.TemporaryDirectory() as d:
        code, text, env_file = run_main(["--no-verify"], FakeWrangler(put_fails=True), d)
        assert code == 1 and "unchanged" in text, text
        assert not os.path.exists(env_file) and os.listdir(d) == [], os.listdir(d)


def test_a_full_run_never_prints_the_token():
    with tempfile.TemporaryDirectory() as d:
        fake = FakeWrangler()
        code, text, env_file = run_main([], fake, d)
        token = json.loads(fake.inputs[0])["codex"]
        assert code == 0 and token not in text, text
        assert f"CARR_ENGINEERING_CONTROLLER_TOKEN={token}" in open(env_file).read()
        assert stat.S_IMODE(os.stat(env_file).st_mode) == 0o600


def test_missing_wrangler_is_a_clean_stop_not_a_traceback():
    def missing(argv, **kw):
        raise FileNotFoundError(argv[0])
    with tempfile.TemporaryDirectory() as d:
        code, text, _ = run_main(["--check"], missing, d)
        assert code == 1 and "Traceback" not in text, text


def test_tokens_are_long_and_fresh():
    a, b = pv.new_token(), pv.new_token()
    assert len(a) >= 40 and a != b


def main():
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            check(name[5:].replace("_", " "), fn)
    print("provision-engineering-controller: " + (f"{len(FAILED)} failed" if FAILED else "every assertion held"))
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
