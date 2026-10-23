#!/usr/bin/env python3
"""Synthetic transport deadlines through the real process and Node interfaces."""
from __future__ import annotations

import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from lib import record_call


class RecordDeadlineTests(unittest.TestCase):
    def test_timeout_stops_descendant_that_ignores_termination(self):
        self.run_descendant_fixture(interrupt=False)

    def test_stage_interruption_stops_descendants_before_propagating(self):
        self.run_descendant_fixture(interrupt=True)

    def run_descendant_fixture(self, *, interrupt):
        class StageExpired(BaseException):
            pass

        def expire(_signum, _frame):
            raise StageExpired()

        with tempfile.TemporaryDirectory(prefix="rcd-", dir="/tmp") as directory:
            root = Path(directory)
            child = root / "child.py"
            child.write_text("""import os,signal,socket,sys,time
from pathlib import Path
signal.signal(signal.SIGTERM, signal.SIG_IGN)
sock=socket.socket(socket.AF_UNIX)
sock.bind(sys.argv[1])
sock.listen()
Path(sys.argv[2]).write_text(str(os.getpid()))
time.sleep(20)
""")
            address = root / "listener"
            ready = root / "ready"
            parent = root / "run.sh"
            parent.write_text(f"#!{sys.executable}\n" +
                              "import subprocess,sys,time\n" +
                              f"subprocess.Popen([sys.executable,{str(child)!r},{str(address)!r},{str(ready)!r}], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n" +
                              "time.sleep(20)\n")
            parent.chmod(0o700)
            started = time.monotonic()
            previous_handler = signal.signal(signal.SIGALRM, expire)
            try:
                with patch.object(record_call, "RUN_SH", parent):
                    if interrupt:
                        signal.setitimer(signal.ITIMER_REAL, 0.4)
                        with self.assertRaises(StageExpired):
                            record_call.call_verb("synthetic-read", {}, timeout=10,
                                                  env={"HOME": directory, "PATH": "/usr/bin:/bin"})
                    else:
                        result = record_call.call_verb("synthetic-read", {}, timeout=0.4,
                                                       env={"HOME": directory, "PATH": "/usr/bin:/bin"})
                        self.assertEqual(result.kind, record_call.TRANSIENT)
                self.assertLess(time.monotonic() - started, 2)
                self.assertTrue(ready.exists(), "descendant started before timeout")
                stopped = False
                until = time.monotonic() + 0.5
                while time.monotonic() < until:
                    with socket.socket(socket.AF_UNIX) as probe:
                        probe.settimeout(0.05)
                        if probe.connect_ex(str(address)) != 0:
                            stopped = True
                            break
                    time.sleep(0.01)
                self.assertTrue(stopped, "timed-out record call left its descendant listening")
            finally:
                signal.setitimer(signal.ITIMER_REAL, 0)
                signal.signal(signal.SIGALRM, previous_handler)
                if ready.exists():
                    try:
                        os.kill(int(ready.read_text()), signal.SIGKILL)
                    except ProcessLookupError:
                        pass

    def test_node_deadline_aborts_response_body(self):
        node = shutil.which("node")
        self.assertIsNotNone(node)
        with tempfile.TemporaryDirectory(prefix="record-http-deadline-") as directory:
            root = Path(directory)
            marker = root / "aborted"
            preload = root / "http-fixture.mjs"
            preload.write_text("""import fs from 'node:fs';
const keepAlive = setInterval(() => {}, 1000);
globalThis.fetch = async (_url, options) => ({
  ok: true, status: 200,
  text: () => new Promise((resolve, reject) => {
    const aborted = () => {
      clearInterval(keepAlive);
      fs.writeFileSync(MARKER, 'cancelled');
      reject(new Error('fixture body cancelled'));
    };
    if (options.signal?.aborted) aborted();
    else options.signal?.addEventListener('abort', aborted, {once: true});
  }),
});
""".replace("MARKER", json.dumps(str(marker))))
            fixture_env = {"HOME": directory, "PATH": str(Path(node).parent) + ":/usr/bin:/bin",
                           "CARR_MCP_LOCAL_TOKEN": "synthetic-fixture-bearer",
                           "CARR_MCP_ENV": str(root / "absent-file"),
                           "CARR_MCP_URL": "http://fixture.invalid/never-contacted",
                           "CARR_RECORD_DEADLINE_MS": str(int(time.time() * 1000) + 200)}
            started = time.monotonic()
            try:
                proc = subprocess.run([node, "--import", str(preload),
                                       str(REPO / "mcp-server/local-verb.mjs"), "synthetic-read", "{}"],
                                      env=fixture_env, capture_output=True, text=True, timeout=2)
            except subprocess.TimeoutExpired:
                self.fail("Node response body ignored the transport deadline")
            self.assertNotEqual(proc.returncode, 0)
            self.assertLess(time.monotonic() - started, 1.5)
            self.assertTrue(marker.exists(), "HTTP body received cancellation")
            self.assertIn("record call deadline exceeded", proc.stderr)

    def test_timeout_owns_deadline_instead_of_inheriting_it(self):
        selected = []

        def runner(argv, **kwargs):
            selected.append(kwargs["env"]["CARR_RECORD_DEADLINE_MS"])
            return subprocess.CompletedProcess(argv, 0, "{}", "")

        before = time.time() * 1000
        record_call.call_verb("synthetic-read", {}, timeout=5,
                             env={"CARR_RECORD_DEADLINE_MS": "1"}, runner=runner)
        self.assertGreaterEqual(int(selected[0]), before + 4999)
        self.assertLessEqual(int(selected[0]), time.time() * 1000 + 5000)
        record_call.call_verb("synthetic-read", {}, timeout=None,
                             env={"CARR_RECORD_DEADLINE_MS": "1"}, runner=runner)
        self.assertEqual(selected[1], "0")


if __name__ == "__main__":
    unittest.main(verbosity=2)
