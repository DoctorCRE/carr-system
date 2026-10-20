#!/usr/bin/env python3
"""Synthetic transcript checks. Never runs a restore or reads backup data."""
import json
from pathlib import Path
import subprocess
import sys
import unittest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from lib.control_plane_facts import evaluate_stage
from lib.loadpy import load_module_from_path
PASS = "  RESTORE REHEARSAL: PASS\n  teardown: branch br-synthetic deleted\n"


class RestoreReportTests(unittest.TestCase):
    def run_report(self, text):
        return subprocess.run([sys.executable, '-m', 'lib.restore_rehearsal_report'],
                              cwd=REPO, input=text, text=True, capture_output=True)

    def test_completed_restore_and_cleanup_pass(self):
        result = self.run_report(PASS)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {'verdict': 'PASS'})

    def test_incomplete_conflicting_or_failed_runs_fail_closed(self):
        for text in ('', 'RESTORE REHEARSAL: PASS\n',
                     PASS.replace('PASS', 'FAIL (1 assertion(s) failed)'),
                     PASS.replace('PASS', 'PASSING'), PASS + PASS,
                     PASS.replace('RESTORE REHEARSAL:', 'quoted RESTORE REHEARSAL:'),
                     '\n'.join(reversed(PASS.splitlines())),
                     PASS.replace('deleted', 'COULD NOT DELETE'),
                     PASS.replace('branch br-synthetic deleted', 'KEEPING branch br-synthetic'),
                     PASS + 'teardown: COULD NOT DELETE branch br-synthetic\n',
                     PASS + 'REHEARSAL FAILED: interrupted\n',
                     PASS + 'RESTORE REHEARSAL: FAIL (1 assertion(s) failed)\n'):
            with self.subTest(text=text):
                result = self.run_report(text)
                self.assertEqual(result.returncode, 1)
                self.assertEqual(json.loads(result.stdout), {'verdict': 'FAIL'})

    def test_expected_snapshot_drift_is_not_failure(self):
        result = self.run_report('expected: rows postdate this dump\n(drift)\n' + PASS)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_pipeline_preserves_upstream_failure(self):
        result = subprocess.run(['zsh', '-o', 'pipefail', '-c',
            '{ print -r -- "$1"; exit 7; } | "$2" -m lib.restore_rehearsal_report',
            'synthetic', PASS, sys.executable], cwd=REPO, capture_output=True, text=True)
        self.assertEqual(result.returncode, 7)

    def test_runtime_validation_requires_the_same_completed_report(self):
        cp = load_module_from_path('restore_report_cp', str(REPO / 'tools/control-plane.py'))
        workflow = next(w for w in cp.load_manifest()['workflows']
                        if w['key'] == 'restore-rehearse-weekly')
        for text, expected in ((PASS, True), ('RESTORE REHEARSAL: PASS\n', False),
                               (PASS + 'REHEARSAL FAILED: interrupted\n', False)):
            evidence = {'entrypoint': 'run.sh', 'args': ['restore-rehearse'],
                        'mode': 'live', 'exit_code': 0, 'stdout_tail': text}
            collector = cp.RuntimeWorkflowFactCollector(workflow,
                {'scheduled_for': '2026-10-19T12:00:00Z'}, execution=evidence)
            self.assertEqual(evaluate_stage(workflow, 'validation', collector), expected)
            completed = cp.RuntimeWorkflowFactCollector(workflow, collector.payload,
                execution=evidence, receipt_ref='job:synthetic', receipt_evidence=evidence)
            self.assertEqual(evaluate_stage(workflow, 'completion', completed), expected)


if __name__ == '__main__':
    unittest.main()
