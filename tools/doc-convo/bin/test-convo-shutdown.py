#!/usr/bin/env python3
"""Exercise conversation shutdown through real child processes under a watchdog."""

import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest

BIN = Path(__file__).resolve().parent


def brain_case(scratch: Path) -> None:
    sys.path.insert(0, str(BIN))
    import convo_core

    convo_core.credential_env.claude_child_env = lambda: (dict(os.environ), None)
    convo_core.SESSION_FILE = scratch / "session"
    fixture = scratch / "claude"
    fixture.write_text(
        f"#!{sys.executable}\n"
        "import os, pathlib, subprocess, sys\n"
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'], "
        "stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL)\n"
        "pathlib.Path(os.environ['DESCENDANT_PID']).write_text(str(child.pid))\n"
        "print('fixture failure', file=sys.stderr, flush=True)\n"
        "sys.exit(17)\n"
    )
    fixture.chmod(0o755)
    os.environ["PATH"] = f"{scratch}:{os.environ['PATH']}"
    brain = convo_core.BrainProcess()
    started = time.monotonic()
    result = brain.ask("fail", "test", lambda text: None)
    assert result.returncode == 17, result
    assert "fixture failure" in result.stderr, result.stderr
    assert time.monotonic() - started < 2.5
    assert brain.ask(" ", "test", lambda text: None).returncode == 0
    brain.close()
    print("failure 17 returned promptly and brain lock released", flush=True)


def shadow_case(scratch: Path) -> None:
    sys.path.insert(0, str(BIN))
    import convo_core

    entered = threading.Event()

    def blocked_router(text):
        entered.set()
        threading.Event().wait(60)
        return {}

    shadow = convo_core._SHADOW
    shadow.router = blocked_router
    shadow.log_path = scratch / "shadow.jsonl"
    shadow.submit("occupied", session_id=None, brain_returncode=0)
    assert entered.wait(1)
    for _ in range(shadow.QUEUE_MAX):
        shadow.submit("queued", session_id=None, brain_returncode=0)
    started = time.monotonic()
    shadow.close()
    assert time.monotonic() - started < 1
    shadow.close()
    shadow.submit("after close", session_id=None, brain_returncode=0)
    print("occupied shadow and full queue closed within deadline", flush=True)


class ShutdownTests(unittest.TestCase):
    def run_case(self, case: str, expected: str) -> None:
        with tempfile.TemporaryDirectory(prefix="doc-shutdown-") as directory:
            scratch = Path(directory)
            pid_file = scratch / "descendant.pid"
            child = subprocess.Popen(
                [sys.executable, __file__, case, str(scratch)],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                env={**os.environ, "HOME": directory, "DESCENDANT_PID": str(pid_file)},
                start_new_session=True,
            )
            try:
                try:
                    out, err = child.communicate(timeout=4)
                except subprocess.TimeoutExpired:
                    self.fail(f"{case} exceeded its shutdown watchdog")
                self.assertEqual(child.returncode, 0, out + err)
                self.assertIn(expected, out)
            finally:
                try:
                    os.killpg(child.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                if pid_file.exists():
                    try:
                        os.kill(int(pid_file.read_text()), signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                child.communicate(timeout=2)

    def test_inherited_stderr_does_not_block_failure_or_next_turn(self) -> None:
        self.run_case("--brain", "brain lock released")

    def test_occupied_shadow_and_full_queue_cannot_block_exit(self) -> None:
        self.run_case("--shadow", "full queue closed within deadline")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--brain":
        brain_case(Path(sys.argv[2]))
    elif len(sys.argv) > 1 and sys.argv[1] == "--shadow":
        shadow_case(Path(sys.argv[2]))
    else:
        unittest.main()
