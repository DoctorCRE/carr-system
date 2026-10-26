#!/usr/bin/env python3
"""Qualify citation verdicts through the CLI with portable synthetic evidence."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

CHECK = Path(__file__).with_name("citation-trace-check.py").resolve()
EVIDENCE = "out/doctorcre-program/"


class CitationTraceTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory(prefix="citation-")))
        self.bundle = self.root / EVIDENCE / "build"
        self.bundle.mkdir(parents=True)
        self.trace = self.root / "trace.jsonl"
        self.trace.write_text("")

    def qualify(self, citations, calls, unopened):
        (self.bundle / "findings.md").write_text("\n".join(citations))
        self.trace.write_text("\n".join(json.dumps(call) for call in calls))
        for bundle in (self.bundle, self.bundle.relative_to(self.root)):
            with self.subTest(bundle=str(bundle)):
                result = subprocess.run(
                    [sys.executable, str(CHECK), str(bundle), str(self.trace)],
                    cwd=self.root, capture_output=True, text=True, timeout=10)
                self.assertEqual(result.returncode, 1 if unopened else 0, result.stderr)
                self.assertRegex(result.stdout, rf"(?m)^UNOPENED:\s+{len(unopened)} ")
                paths = [line.strip() for line in result.stdout.splitlines()
                         if line.startswith("  " + EVIDENCE)]
                self.assertEqual(paths, sorted(unopened))

    def test_sibling_outputs_and_metadata_commands_leave_three_unopened(self):
        paths = [EVIDENCE + name for name in
                 ("prior/findings.md", "prior/unknowns.md", "prior/self-audit.md")]
        calls = [{"name": "Bash", "input": {"command": f"{command} {path}"}}
                 for command in ("ls", "find", "stat", "shasum", "sha256sum", "wc", "du")
                 for path in paths]
        self.qualify(paths + [EVIDENCE + "build/findings.md"], calls, paths)

    def test_listing_then_read_leaves_two_upstream_registries_unopened(self):
        paths = [EVIDENCE + name for name in
                 ("registry/one.json", "registry/two.json", "prior/findings.md")]
        self.qualify(paths, [{"name": "Bash", "input": {
            "command": f"ls {' '.join(paths)} && cat {paths[2]}"}}], paths[:2])

    def test_rebuild_reads_all_citations(self):
        paths = [EVIDENCE + name for name in
                 ("prior/findings.md", "registry/one.json", "registry/two.json")]
        calls = [{"name": name, "input": {"file_path": path}}
                 for name, path in zip(("Read", "NotebookRead"), paths)]
        calls.append({"name": "Bash", "input": {"command": f"cat {paths[2]}"}})
        self.qualify(paths, calls, [])

    def test_prose_claims_are_outside_the_documented_detection_scope(self):
        self.qualify(["The prior graph and prose were read."], [], [])

    def test_missing_inputs_are_errors(self):
        for missing in (self.bundle, self.trace):
            with self.subTest(missing=str(missing)):
                result = subprocess.run(
                    [sys.executable, str(CHECK),
                     str(self.bundle) + ("-missing" if missing == self.bundle else ""),
                     str(self.trace) + ("-missing" if missing == self.trace else "")],
                    capture_output=True, text=True, timeout=10)
                self.assertEqual(result.returncode, 2)
                self.assertNotIn("UNOPENED:", result.stdout)


if __name__ == "__main__":
    unittest.main()
