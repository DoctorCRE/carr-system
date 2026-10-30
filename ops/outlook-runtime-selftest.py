#!/usr/bin/env python3
"""Prove Outlook runtime stamps using only synthetic local Git history."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from git_env import fixture_env

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib import outlook_intake as intake


class RuntimeTests(unittest.TestCase):
    def test_installed_shallow_detached_runtime_uses_source_history(self):
        from lib.outlook_runtime import stamp
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, fixture_env(), clear=True):
            source, runtime = Path(temp) / 'source', Path(temp) / 'runtime'
            source.mkdir()
            def git(repo, *args):
                return subprocess.check_output(['git', '-C', str(repo), *args], text=True).strip()
            git(source, 'init', '-q', '-b', 'main')
            (source / 'bin').mkdir()
            installer = source / 'bin/install-outlook-intake.sh'
            installer.write_text('SOURCES=(lib/example.py)\n')
            (source / 'lib').mkdir()
            target = source / 'lib/example.py'
            target.write_text('VALUE = 1\n')
            def commit():
                git(source, 'add', 'bin/install-outlook-intake.sh', 'lib/example.py')
                git(source, '-c', 'user.name=Synthetic', '-c', 'user.email=synthetic@example.test',
                    '-c', 'core.hooksPath=/dev/null', 'commit', '-qm', 'synthetic runtime')
                return git(source, 'rev-parse', 'HEAD')
            installed = commit()
            git(source, 'init', '-q', str(runtime))
            git(runtime, 'fetch', '-q', '--depth=1', str(source), installed)
            git(runtime, 'checkout', '-q', '--detach', installed)
            (source / '.venv').mkdir()
            (runtime / '.venv').symlink_to(source / '.venv')
            self.assertEqual(git(runtime, 'rev-parse', '--is-shallow-repository'), 'true')
            self.assertEqual(git(runtime, 'for-each-ref'), '')
            self.assertEqual(stamp(runtime)['runtime_behind'], 0)
            target.write_text('VALUE = 2\n')
            commit()
            value = stamp(runtime)
            self.assertEqual(value['runtime_sha'], git(runtime, 'rev-parse', '--short', 'HEAD'))
            self.assertEqual(value['runtime_behind'], 1)

    def test_unexpected_stamp_failure_is_best_effort(self):
        from lib import outlook_runtime
        with patch.object(outlook_runtime, '_git', side_effect=RuntimeError('synthetic')):
            self.assertEqual(outlook_runtime.stamp(Path('/synthetic')),
                             dict(runtime_sha=None, runtime_behind=None, runtime_note='history_incomplete'))

    def test_receipt_measures_installer_sources_and_ignores_later_docs(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, fixture_env(), clear=True):
            repo = Path(temp)
            def git(*args):
                return subprocess.check_output(['git', '-C', temp, *args], text=True).strip()
            def commit(path, content):
                target = repo / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content)
                git('add', path)
                git('-c', 'user.name=Synthetic', '-c', 'user.email=synthetic@example.test',
                    '-c', 'core.hooksPath=/dev/null', 'commit', '-qm', path)
                return git('rev-parse', 'HEAD')
            git('init', '-q', '-b', 'main')
            commit('bin/install-outlook-intake.sh', 'SOURCES=(vendor/hxprobe)\npython -m lib.outlook_settings\nSOURCE="$REPO/ops/launchd/$LABEL.plist"\n')
            installed = commit('lib/outlook_settings.py', 'from lib import email_fixture\nSCRIPT = "bin/run-scheduled.sh"\n')
            git('checkout', '-qb', 'new-main')
            commit('lib/email_fixture.py', 'VALUE = 1\n')
            commit('vendor/hxprobe/src/main.rs', 'fn main() {}\n')
            commit('bin/run-scheduled.sh', 'python $REPO/tools/ops-spool.py\n')
            commit('tools/ops-spool.py', 'RECORDER = "ops-record.py"\n')
            latest = commit('tools/ops-record.py', 'VALUE = 1\n')
            commit('ops/launchd/unrelated.plist', '<plist/>\n')
            commit('docs/readme.md', 'Unrelated documentation\n')
            git('update-ref', 'refs/remotes/origin/main', 'HEAD')
            git('checkout', '-q', '--detach', installed)
            # The dependency can be newly added on main, so inspect both trees.
            receipt = json.loads(intake.receipt_line(intake.Outcome('succeeded', 'processed'), repo).split(' ', 1)[1])
            self.assertEqual(receipt['runtime_sha'], git('rev-parse', '--short', 'HEAD'))
            self.assertEqual(receipt['runtime_behind'], 5)
            git('checkout', '-q', '--detach', latest)
            receipt = json.loads(intake.receipt_line(intake.Outcome('succeeded', 'processed'), repo).split(' ', 1)[1])
            self.assertEqual(receipt['runtime_behind'], 0)
            git('update-ref', 'refs/remotes/origin/main', installed)
            git('update-ref', 'refs/heads/main', latest)
            git('checkout', '-q', '--detach', installed)
            receipt = json.loads(intake.receipt_line(intake.Outcome('succeeded', 'processed'), repo).split(' ', 1)[1])
            self.assertEqual(receipt['runtime_behind'], 5)
            git('update-ref', '-d', 'refs/remotes/origin/main')
            git('branch', '-D', 'main')
            receipt = json.loads(intake.receipt_line(intake.Outcome('succeeded', 'processed'), repo).split(' ', 1)[1])
            self.assertIsNone(receipt['runtime_behind'])
            self.assertEqual(receipt['runtime_note'], 'main_unknown_locally')


if __name__ == '__main__':
    unittest.main()
