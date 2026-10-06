#!/usr/bin/env python3
"""bin/build-calendar-access.sh keeps a valid bundle instead of recompiling.

A recompile changes the ad-hoc cdhash (clang output is not byte-identical run to
run), and macOS keys Joe's Calendar grant on that cdhash, so an unconditional
rebuild silently revoked the grant. clang and codesign are PATH shims here; the
test records whether a compile happened.
"""
import os
import pathlib
import shutil
import subprocess
import tempfile
import unittest

REPO = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = REPO / "bin" / "build-calendar-access.sh"
APP_REL = pathlib.Path("tools/CARR Calendar Access.app")


class BuildKeepsGrant(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = pathlib.Path(self.tmp.name)
        self.repo, self.shims, self.log = root / "repo", root / "shims", root / "calls.log"
        app = self.repo / APP_REL
        (app / "Contents" / "Resources").mkdir(parents=True)
        (app / "Contents" / "Resources" / "run.zsh").write_text("#!/bin/zsh\n")
        (self.repo / "tools" / "calendar-access-stub.c").write_text("int main(void){return 0;}\n")
        self.shims.mkdir()
        self.shim("clang", 'echo clang >> "$LOG"; out=""; while [ $# -gt 0 ]; do '
                           '[ "$1" = "-o" ] && out="$2"; shift; done; printf built > "$out"')
        self.shim("codesign", 'echo "codesign $1" >> "$LOG"; exit 0')
        self.bin = app / "Contents" / "MacOS" / "carr-calendar-access"

    def tearDown(self):
        self.tmp.cleanup()

    def shim(self, name, body):
        path = self.shims / name
        path.write_text(f"#!/bin/sh\n{body}\n")
        path.chmod(0o755)

    def run_build(self, *args):
        env = {"PATH": f"{self.shims}:/usr/bin:/bin", "CARR_REPO": str(self.repo), "LOG": str(self.log)}
        return subprocess.run(["/bin/sh", str(SCRIPT), *args], env=env, capture_output=True, text=True)

    def compiles(self):
        return self.log.read_text().count("clang") if self.log.exists() else 0

    def existing_build(self):
        self.bin.parent.mkdir(parents=True)
        self.bin.write_text("granted")
        self.bin.chmod(0o755)

    def test_missing_binary_is_built_and_stamped(self):
        result = self.run_build()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.compiles(), 1)
        self.assertTrue((self.repo / "tools" / ".calendar-access-stub.sha256").is_file())

    def test_valid_legacy_build_is_kept_and_stamped(self):
        self.existing_build()
        result = self.run_build()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.compiles(), 0)
        self.assertEqual(self.bin.read_text(), "granted")
        self.assertIn("kept", result.stdout)

    def test_second_run_after_build_is_a_no_op(self):
        self.run_build()
        self.run_build()
        self.assertEqual(self.compiles(), 1)

    def test_changed_source_rebuilds_and_says_the_grant_is_needed(self):
        self.run_build()
        (self.repo / "tools" / "calendar-access-stub.c").write_text("int main(void){return 1;}\n")
        result = self.run_build()
        self.assertEqual(self.compiles(), 2)
        self.assertIn("must grant Calendars", result.stderr)

    def test_force_rebuilds_and_warns_the_grant_is_void(self):
        self.existing_build()
        result = self.run_build("--force")
        self.assertEqual(self.compiles(), 1)
        self.assertIn("must grant Calendars", result.stderr)

    def test_kept_build_does_not_warn(self):
        self.existing_build()
        self.assertNotIn("grant Calendars", self.run_build().stderr)

    def test_invalid_signature_rebuilds(self):
        self.existing_build()
        self.shim("codesign", 'echo "codesign $1" >> "$LOG"; [ "$1" = "-v" ] && [ ! -f "$LOG.signed" ] && exit 1; '
                              '[ "$1" = "--force" ] && touch "$LOG.signed"; exit 0')
        result = self.run_build()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.compiles(), 1)

    def test_unknown_argument_is_refused(self):
        self.assertEqual(self.run_build("--bogus").returncode, 64)


if __name__ == "__main__":
    unittest.main()
