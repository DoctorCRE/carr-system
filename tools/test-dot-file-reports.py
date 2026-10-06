#!/usr/bin/env python3
"""Synthetic behavioral verification; never reads live Dot files or credentials."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
FAKE_VERB = '''#!/usr/bin/env python3
import json, pathlib, sys, time
root = pathlib.Path(__file__).parent
assert sys.argv[1:] == ["call", "record-finding", "-"]
payload = json.load(sys.stdin)
with (root / "requests.jsonl").open("a") as out:
    out.write(json.dumps(payload) + "\\n")
time.sleep(0.05)
if (root / "fail").exists():
    print("synthetic-private-error", file=sys.stderr)
    sys.exit(1)
if (root / "malformed").exists():
    print("{bad synthetic-private-error")
    sys.exit(0)
if (root / "change-source").exists():
    pathlib.Path(payload["source"]).write_text(payload["value"]["text"] + "changed after snapshot\\n")
print(json.dumps({"ok": not (root / "wrong-ack").exists(), "flag_id": 123 if (root / "numeric-id").exists() else "00000000-0000-4000-8000-000000000001",
                  "subject_type": "repo", "kind": "research_report", "found": True}))
'''


class FilerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "bin").mkdir()
        shutil.copy2(ROOT / "bin/dot-file-reports", self.root / "bin/dot-file-reports")
        (self.root / "run.sh").write_text(FAKE_VERB)
        (self.root / "run.sh").chmod(0o755)
        self.reports = self.root / "out/orch/dot/reports"
        self.reports.mkdir(parents=True)
        self.ledger = self.reports.parent / "filed-reports.json"

    def report(self, job="010-V-synthetic", text="# Synthetic topic\nSee https://example.org/source.\nDOT-REPORT-END\n"):
        path = self.reports / (job + ".txt")
        path.write_bytes(text.encode("utf-8"))
        os.utime(path, (1767225600, 1767225600))  # fixture: 2026-01-01 UTC
        return path

    def command(self, *args):
        return [sys.executable, str(self.root / "bin/dot-file-reports"), *map(str, args)]

    def run_filer(self, *args, ok=True):
        proc = subprocess.run(self.command(*args), capture_output=True, text=True, timeout=10)
        self.assertEqual(proc.returncode, 0 if ok else 1, proc.stderr)
        self.assertNotIn("synthetic-private-error", proc.stdout + proc.stderr)
        return proc

    def requests(self):
        path = self.root / "requests.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def entries(self):
        return json.loads(self.ledger.read_text())["jobs"]

    def test_full_multi_page_report_and_metadata(self):
        text = "# Synthetic metro research\r\n" + "Unicode: café — λ\r\n" * 15000
        text += "[Source](https://example.org/a)\r\nhttps://example.org/a\r\nhttps://example.net/b.\r\n"
        text += "DOT-REPORT-END\r\n"
        path = self.report("020-M-synthetic", text)
        self.run_filer()
        request, = self.requests()
        self.assertGreater(len(text.encode("utf-8")), 200000)
        self.assertEqual(request["value"]["text"], text)
        self.assertEqual(request["value"]["job"], "020-M-synthetic")
        self.assertEqual(request["value"]["topic"], "Synthetic metro research")
        self.assertEqual(request["value"]["date"], "2026-01-01")
        self.assertEqual(request["value"]["citations"], ["https://example.org/a", "https://example.net/b"])
        self.assertEqual(request["source"], str(path.resolve()))
        self.assertEqual(request["subject"], "repo:jbookout/doctorcre-app")
        self.assertEqual(request["epistemic_status"], "observed")
        self.assertTrue(request["internal"])
        self.assertFalse(request["value"]["claims_independently_verified"])
        self.assertEqual(self.entries()[path.stem]["status"], "filed")
        self.assertNotIn("text", self.ledger.read_text())
        self.assertEqual(self.ledger.stat().st_mode & 0o777, 0o600)

    def test_repeat_job_never_refiles_even_when_edited(self):
        path = self.report()
        self.run_filer(path)
        path.write_text("changed synthetic report")
        self.run_filer(path)
        self.assertEqual(len(self.requests()), 1)

    def test_only_selected_file_and_partial_exclusion(self):
        selected = self.report("030-UX-synthetic")
        self.report("040-synthetic")
        partial = self.reports / "050-synthetic.txt.partial"
        partial.write_text("unfinished")
        self.report("060-synthetic.partial")
        self.run_filer(selected)
        self.assertEqual(list(self.entries()), [selected.stem])
        self.run_filer(partial)
        self.run_filer()
        self.assertEqual(len(self.requests()), 2)

    def test_concurrent_runs_file_once(self):
        self.report()
        procs = [subprocess.Popen(self.command(), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                 for _ in range(3)]
        for proc in procs:
            _, err = proc.communicate(timeout=10)
            self.assertEqual(proc.returncode, 0, err)
        self.assertEqual(len(self.requests()), 1)

    def test_concurrent_first_publication_waits_for_completion(self):
        path = self.reports / "publishing.txt"
        writer_source = '''import sys
with open(sys.argv[1], "w") as report:
    report.write("# Synthetic publishing\\nFirst half\\n")
    report.flush()
    print("ready", flush=True)
    sys.stdin.readline()
    report.write("Second half\\nDOT-REPORT-END publishing\\n")
'''
        with subprocess.Popen([sys.executable, "-c", writer_source, str(path)],
                              stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True) as writer:
            try:
                self.assertEqual(writer.stdout.readline().strip(), "ready")
                self.run_filer(ok=False)
                self.assertEqual(self.requests(), [])
                self.assertFalse(self.ledger.exists())
            finally:
                writer.communicate("finish\n", timeout=10)
        self.assertEqual(writer.returncode, 0)
        self.run_filer()
        self.run_filer()
        request, = self.requests()
        self.assertEqual(request["value"]["text"],
                         "# Synthetic publishing\nFirst half\nSecond half\nDOT-REPORT-END publishing\n")
        self.assertEqual(self.entries()[path.stem]["status"], "filed")

    def test_intermediate_part_marker_is_not_completion(self):
        path = self.report(text="# Synthetic part 1 of 2\nFirst half\nDOT-REPORT-END\n")
        self.run_filer(path, ok=False)
        self.assertEqual(self.requests(), [])
        path.write_text(path.read_text() + "\n# Synthetic part 2 of 2\nSecond half\nDOT-REPORT-END\n")
        self.run_filer(path)
        self.assertEqual(len(self.requests()), 1)

    def test_marker_inside_unclosed_fence_is_not_completion(self):
        for fence in ("```text", "~~~text"):
            with self.subTest(fence=fence):
                path = self.report(text=f"# Synthetic unfinished\n{fence}\nDOT-REPORT-END\n")
                self.run_filer(path, ok=False)
                self.assertEqual(self.requests(), [])
        path.write_text(path.read_text() + "~~~\nFinal text\nDOT-REPORT-END\n")
        self.run_filer(path)

    def test_source_changed_before_acknowledgement_stays_pending(self):
        path = self.report()
        (self.root / "change-source").touch()
        self.run_filer(ok=False)
        self.assertEqual(self.entries()[path.stem]["status"], "pending")
        self.run_filer(ok=False)
        self.assertEqual(len(self.requests()), 1)

    def test_timeout_cancels_wrapper_chain_and_retains_pending(self):
        self.check_timeout_cancels_wrapper_chain(startup_delay=0)

    def test_timeout_cancellation_waits_for_slow_wrapper_startup(self):
        self.check_timeout_cancels_wrapper_chain(startup_delay=0.35)

    def check_timeout_cancels_wrapper_chain(self, startup_delay):
        path = self.report()
        filer = self.root / "bin/dot-file-reports"
        # run.sh -> Python wrapper -> Node HTTP-client stand-in, all holding
        # the inherited pipes. Cancellation must reach the delayed Node effect.
        node = '''const fs = require('node:fs');
fs.writeFileSync(process.argv[1] + '/started', String(process.pid));
setTimeout(() => fs.writeFileSync(process.argv[1] + '/late-effect', 'orphan'), 700);
'''
        child = (f"import subprocess,sys,time; time.sleep({startup_delay!r}); "
                 "subprocess.run(['node', '-e', sys.argv[1], sys.argv[2]])")
        wrapper = (
            "#!/usr/bin/env python3\nimport json,pathlib,subprocess,sys\n"
            "root = pathlib.Path(__file__).parent\n"
            "payload = json.load(sys.stdin)\n"
            "(root / 'requests.jsonl').write_text(json.dumps(payload) + '\\n')\n"
            f"subprocess.run([sys.executable, '-c', {child!r}, {node!r}, str(root)])\n"
        )
        (self.root / "run.sh").write_text(wrapper)
        # Only the synthetic transport's deadline is shortened. Send its input
        # first, then wait for Node readiness under a separate startup bound.
        # Production still handles TimeoutExpired and kills its own group.
        launcher = '''import pathlib, runpy, subprocess, sys, time
filer = pathlib.Path(sys.argv[1])
started = filer.parents[1] / "started"
class ReadyTransport(subprocess.Popen):
    def communicate(self, input=None, timeout=None):
        if input is not None:
            self.stdin.write(input)
            self.stdin.close()
            self.stdin = None
            deadline = time.monotonic() + 5
            while not started.exists():
                if self.poll() is not None or time.monotonic() >= deadline:
                    raise subprocess.TimeoutExpired(self.args, 5)
                time.sleep(0.01)
            timeout = 0.2
        return super().communicate(timeout=timeout)
subprocess.Popen = ReadyTransport
sys.argv = [str(filer)]
runpy.run_path(str(filer), run_name="__main__")
'''
        proc = subprocess.run([sys.executable, "-c", launcher, str(filer)],
                              capture_output=True, text=True, timeout=10)
        self.assertEqual(proc.returncode, 1, proc.stderr)
        self.assertIn("verb transport timed out", proc.stderr)
        self.assertEqual(self.entries()[path.stem]["status"], "pending")
        self.assertTrue((self.root / "started").exists(), "fixture must launch the Node descendant")
        time.sleep(1)
        self.assertFalse((self.root / "late-effect").exists(), "timed-out transport left a running descendant")

    def test_ambiguous_failure_reuses_exact_frozen_request(self):
        path = self.report()
        failure = self.root / "fail"
        failure.touch()
        self.run_filer(ok=False)
        self.assertEqual(self.entries()[path.stem]["status"], "pending")
        failure.rename(self.root / "_to_delete-fail")
        os.utime(path, None)  # metadata changes must not change replay arguments
        self.run_filer()
        first, second = self.requests()
        self.assertEqual(first, second)
        self.assertEqual(self.entries()[path.stem]["status"], "filed")

    def test_changed_pending_content_refuses_replay(self):
        path = self.report()
        (self.root / "fail").touch()
        self.run_filer(ok=False)
        path.write_text("different synthetic content")
        self.run_filer(ok=False)
        self.assertEqual(len(self.requests()), 1)

    def test_malformed_acknowledgement_keeps_pending(self):
        path = self.report()
        (self.root / "malformed").touch()
        self.run_filer(ok=False)
        self.assertEqual(self.entries()[path.stem]["status"], "pending")

    def test_numeric_receipt_id_is_sanitized_and_stays_pending(self):
        path = self.report()
        (self.root / "numeric-id").touch()
        proc = self.run_filer(ok=False)
        self.assertNotIn("Traceback", proc.stderr)
        self.assertIn("invalid filing receipt", proc.stderr)
        self.assertEqual(self.entries()[path.stem]["status"], "pending")

    def test_numeric_ledger_id_is_sanitized_without_send(self):
        path = self.report()
        self.run_filer()
        ledger = json.loads(self.ledger.read_text())
        ledger["jobs"][path.stem]["flag_id"] = 123
        self.ledger.write_text(json.dumps(ledger))
        proc = self.run_filer(ok=False)
        self.assertNotIn("Traceback", proc.stderr)
        self.assertIn("invalid ledger", proc.stderr)
        self.assertEqual(len(self.requests()), 1)

    def test_citations_preserve_balanced_parentheses(self):
        self.report(text="# Sources\n[Page](https://example.org/Function_(math)).\n"
                         "https://example.org/Function_(math)\nDOT-REPORT-END\n")
        self.run_filer()
        request, = self.requests()
        self.assertEqual(request["value"]["citations"], ["https://example.org/Function_(math)"])

    def test_rejected_receipt_under_optimized_python_keeps_pending(self):
        path = self.report()
        (self.root / "wrong-ack").touch()
        command = self.command()
        command.insert(1, "-O")
        proc = subprocess.run(command, capture_output=True, text=True, timeout=10)
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(self.entries()[path.stem]["status"], "pending")

    def test_corrupt_ledger_refuses_without_call(self):
        self.report()
        self.ledger.write_text("{broken")
        self.run_filer(ok=False)
        self.assertEqual(self.requests(), [])

    def test_empty_invalid_utf8_missing_and_symlink_refuse(self):
        path = self.report(text=" ")
        self.run_filer(path, ok=False)
        path.write_bytes(b"\xff")
        self.run_filer(path, ok=False)
        self.run_filer(self.reports / "missing.txt", ok=False)
        link = self.reports / "link.txt"
        link.symlink_to(path)
        self.run_filer(link, ok=False)
        self.assertEqual(self.requests(), [])


class StdinTransportTests(unittest.TestCase):
    def test_required_source_frontier_remains_sealed(self):
        proc = subprocess.run(
            ["node", "ops/scac-mutation-inventory.mjs", "--check-source-inventory-frontier"],
            cwd=ROOT, capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    def test_sanctioned_wrapper_to_node_preserves_large_stdin_and_argv_baseline(self):
        # Synthetic Node preload observes the existing client at the fetch seam,
        # before auth, network or DB. No token fixture or secret value is required.
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "tools").mkdir()
            (root / "mcp-server").mkdir()
            shutil.copy2(ROOT / "tools/call-verb.py", root / "tools/call-verb.py")
            for name in ("local-verb.mjs", "local-client-auth.mjs", "human-only-hint.mjs"):
                shutil.copy2(ROOT / "mcp-server" / name, root / "mcp-server" / name)
            # Load source up to its parsed args; zero authority side effects.
            client = root / "mcp-server/local-verb.mjs"
            source = client.read_text()
            boundary = '// ---------------------------------------------------------------------------\n// DEFAULT PATH'
            self.assertIn(boundary, source)
            client.write_text(source.split(boundary)[0] + '\nconsole.log(JSON.stringify(args));\n')
            env = {key: val for key, val in os.environ.items()
                   if key not in ("DATABASE_URL", "CARR_BREAK_GLASS", "CARR_MCP_CLIENT_PROFILE")}
            payload = {"value": {"text": "synthetic λ\r\n" * 25000}}
            proc = subprocess.run([sys.executable, str(root / "tools/call-verb.py"), "record-finding", "-"],
                                  input=json.dumps(payload), text=True, capture_output=True, env=env, timeout=10)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(json.loads(proc.stdout), payload)
            proc = subprocess.run([sys.executable, str(root / "tools/call-verb.py"), "standing-context", "{}"],
                                  text=True, capture_output=True, env=env, timeout=10)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(json.loads(proc.stdout), {})


if __name__ == "__main__":
    unittest.main()
