"""The migration command must start before any database connection is needed."""
import os
from pathlib import Path
import subprocess
import sys
import unittest


class MigrationStartupTests(unittest.TestCase):
    def test_help_starts_without_database_credentials(self):
        env = {key: value for key, value in os.environ.items() if key != 'DATABASE_URL'}
        result = subprocess.run([sys.executable, str(Path(__file__).with_name('migrate.py')), '--help'],
                                env=env, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('--through', result.stdout)


if __name__ == '__main__':
    unittest.main()
