#!/usr/bin/env python3
"""The not-this-host marker (2026-09-26): the Worker accepts one controller token, so one Mac runs the controller and
every other Mac says so explicitly instead of failing each bridge cycle. Runs the real launcher against temporary
marker and credential paths; the bridge half reads a fake launcher's readback."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import bridge  # noqa: E402

LAUNCHER = HERE.parents[1] / "bin" / "run-engineering-dispatch.sh"
ZSH = shutil.which("zsh")
FAILURES: list[str] = []


def check(label, fn):
    try:
        fn()
    except AssertionError as exc:
        FAILURES.append(label)
        print(f"  FAIL  {label}: {exc}")
    else:
        print(f"  ok    {label}")


def launch(marker: Path, env_file: Path):
    env = {**os.environ, "CARR_ENGINEERING_CONTROLLER_NOT_HOST_MARKER": str(marker),
           "CARR_ENGINEERING_CONTROLLER_ENV_FILE": str(env_file)}
    assert ZSH is not None
    return subprocess.run([ZSH, str(LAUNCHER)], env=env, capture_output=True, text=True, timeout=60)


def test_marked_mac_without_credential_answers_no_claim():
    with tempfile.TemporaryDirectory() as d:
        marker = Path(d) / "marker"
        marker.write_text("controller runs on the Mac Studio\n")
        p = launch(marker, Path(d) / "absent.env")
        assert p.returncode == 0, p.stderr
        assert '"host":"not_controller_host"' in p.stdout and '"claimed":0' in p.stdout, p.stdout


def test_marked_mac_holding_the_credential_is_refused():
    with tempfile.TemporaryDirectory() as d:
        marker, env_file = Path(d) / "marker", Path(d) / "controller.env"
        marker.write_text("x\n")
        env_file.write_text("")
        p = launch(marker, env_file)
        assert p.returncode == 78 and "remove one" in p.stderr, (p.returncode, p.stderr)


def test_unmarked_mac_without_credential_still_fails():
    with tempfile.TemporaryDirectory() as d:
        p = launch(Path(d) / "no-marker", Path(d) / "absent.env")
        assert p.returncode != 0 and "not_controller_host" not in p.stdout, (p.returncode, p.stdout)


def test_bridge_keeps_the_host_state_visible():
    with tempfile.TemporaryDirectory() as d:
        fake = Path(d) / "fake.sh"
        fake.write_text("#!/bin/sh\necho '{\"ok\":true,\"claimed\":0,\"host\":\"not_controller_host\"}'\n")
        fake.chmod(0o755)
        out = bridge.run_engineering_dispatch(command=fake)
        assert out == {"claimed": 0, "completed": 0, "results": [], "host": "not_controller_host"}, out
        fake.write_text("#!/bin/sh\necho '{\"ok\":true,\"claimed\":1,\"completed\":1,\"results\":[]}'\n")
        assert "host" not in bridge.run_engineering_dispatch(command=fake)


def main() -> int:
    if ZSH:
        check("marked Mac without credential answers no-claim", test_marked_mac_without_credential_answers_no_claim)
        check("marked Mac holding the credential is refused", test_marked_mac_holding_the_credential_is_refused)
        check("unmarked Mac without credential still fails", test_unmarked_mac_without_credential_still_fails)
    else:
        print("  skip  launcher cases: zsh is not installed here")
    check("bridge keeps the host state visible", test_bridge_keeps_the_host_state_visible)
    if FAILURES:
        print(f"{len(FAILURES)} engineering host marker test(s) failed", file=sys.stderr)
        return 1
    print("engineering host marker: every assertion held")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
