"""The handoff runner must cold-import with the configured Python interpreter."""
from pathlib import Path
import subprocess
import sys
import unittest


class ImportTests(unittest.TestCase):
    def test_cold_import_exposes_the_existing_review_contract(self):
        program = """
import work_handoffs
body = 'APPROVE\\nReviewed-SHA: ' + 'a' * 40
assert work_handoffs.REVIEW.reviewed_header_sha(body) == 'a' * 40
"""
        result = subprocess.run([sys.executable, '-c', program],
                                cwd=Path(__file__).resolve().parent,
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()
