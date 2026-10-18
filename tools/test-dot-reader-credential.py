#!/usr/bin/env python3
"""Private credential publication, recovery, redaction and revoke contract."""
import contextlib
import importlib.util
import io
from pathlib import Path
import secrets
import stat
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("rotate", ROOT / "tools/dot-reader-access.py")
if SPEC is None or SPEC.loader is None:
    raise SystemExit("dot-reader credential test helper unavailable")
rotate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(rotate)


class CredentialTests(unittest.TestCase):
    def test_private_publication_and_interruption_reuses_same_value(self):
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "dot-reader.connection"
            material = secrets.token_urlsafe(32)
            with patch.object(rotate, "DOT_CONNECTION_PATH", str(destination)):
                rotate.write_dot_pending(material)
                self.assertTrue(rotate.read_dot_pending() == material)
                self.assertEqual(stat.S_IMODE(destination.with_suffix(".connection.pending").stat().st_mode), 0o600)
                rotate.publish_dot_connection(material)
                self.assertTrue(destination.read_text() == material + "\n")
                self.assertEqual(stat.S_IMODE(destination.stat().st_mode), 0o600)
                self.assertIsNone(rotate.read_dot_pending())

    def test_symlink_and_public_pending_file_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "dot-reader.connection"
            pending = Path(str(destination) + ".pending")
            target = Path(directory) / "other"
            target.write_text("untouched")
            pending.symlink_to(target)
            with patch.object(rotate, "DOT_CONNECTION_PATH", str(destination)):
                with self.assertRaises(SystemExit):
                    rotate.read_dot_pending()
                self.assertEqual(target.read_text(), "untouched")
                pending.unlink()
                pending.write_text("invalid")
                pending.chmod(0o644)
                with self.assertRaises(SystemExit):
                    rotate.read_dot_pending()

    def test_database_error_never_rendered(self):
        from psycopg import OperationalError
        material = secrets.token_urlsafe(32)
        output = io.StringIO()
        with tempfile.TemporaryDirectory() as directory:
            env_path = str(Path(directory) / "db.env")
            # Exercise the production lock without touching the user's config.
            with patch.object(rotate.credential, "ENV_PATH", env_path), \
                    patch.object(rotate, "_dot_reader_action", side_effect=OperationalError(material)) as action:
                with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
                    self.assertEqual(rotate.dot_reader_action(revoke=False), 1)
                action.assert_called_once_with(revoke=False)
            lock = Path(env_path + ".rotate-credential.lock")
            self.assertEqual(stat.S_IMODE(lock.stat().st_mode), 0o600)
        self.assertIn("operation not confirmed", output.getvalue())
        self.assertNotIn(material, output.getvalue())


if __name__ == "__main__":
    unittest.main()
