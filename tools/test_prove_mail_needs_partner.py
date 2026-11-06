import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'tools/prove-mail-needs-partner.py'
spec = importlib.util.spec_from_file_location('proof', SCRIPT)
proof = importlib.util.module_from_spec(spec)
spec.loader.exec_module(proof)


class MailProofTests(unittest.TestCase):
    def setUp(self):
        self.bundle = json.loads((ROOT / 'tools/fixtures/mail-needs-partner.json').read_text())

    def test_pass_and_failure_cli_exits(self):
        command = [sys.executable, str(SCRIPT), '--fixture', str(ROOT / 'tools/fixtures/mail-needs-partner.json')]
        self.assertEqual(subprocess.run(command, capture_output=True).returncode, 0)
        self.assertEqual(subprocess.run(command + ['--fixture-failure'], capture_output=True).returncode, 78)

    def test_leak_resolved_unknown_and_unavailable_refuse(self):
        for defect in ['leak', 'resolved', 'unknown', 'unavailable']:
            bundle = copy.deepcopy(self.bundle)
            if defect == 'leak':
                bundle['other']['items'] = bundle['primary']['items']
                bundle['other']['state'] = 'ready'
            elif defect == 'resolved':
                bundle['canonical']['proposals'][0]['status'] = 'confirmed'
            elif defect == 'unknown':
                bundle['primary']['items'][0]['source']['version'] = None
            else:
                bundle['primary'].update(state='unavailable', items=[])
            with self.subTest(defect=defect), self.assertRaises(ValueError):
                proof.observe(bundle)
