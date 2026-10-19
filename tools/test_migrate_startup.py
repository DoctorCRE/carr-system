"""The migration command starts and reports handled failures without a database."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import unittest


class MigrationCommandTests(unittest.TestCase):
    def test_handled_failure_emits_exception_class_without_traceback(self):
        result = subprocess.run([sys.executable, '-c',
            "import migrate\n"
            "try:\n"
            "    raise ValueError('fixture detail')\n"
            "except ValueError:\n"
            "    migrate.fail('handled failure')\n"],
            cwd=Path(__file__).parent, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 1)
        self.assertIn('builtins.ValueError:\n', result.stderr)
        self.assertIn('ERROR: handled failure', result.stderr)
        self.assertNotIn('Traceback', result.stderr)
        self.assertNotIn('fixture detail', result.stderr)

    def test_help_starts_without_database_credentials(self):
        env = {key: value for key, value in os.environ.items() if key != 'DATABASE_URL'}
        interpreters = {sys.executable, shutil.which('python3.9')}
        if Path('/usr/bin/python3').exists():
            interpreters.add('/usr/bin/python3')
        for interpreter in sorted(path for path in interpreters if path):
            with self.subTest(interpreter=interpreter):
                result = subprocess.run([interpreter, str(Path(__file__).with_name('migrate.py')), '--help'],
                                        env=env, capture_output=True, text=True, timeout=30)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn('--through', result.stdout)


if __name__ == '__main__':
    unittest.main()
