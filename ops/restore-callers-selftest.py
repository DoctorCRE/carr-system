#!/usr/bin/env python3
"""Offline contract checks for every tracked restore invocation and schedule."""
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ops"))
from git_env import fixture_env, scrubbed_env

CALL = re.compile(r'(?:run\.sh["\x27]?\s+restore-rehearse\b|restore-rehearse\.sh["\x27]?\s+(?=--))')


def caller_violations(root):
    paths = subprocess.check_output(
        ['git', 'ls-files', '-z', '--', '*.sh', '*.py', '*.json', '*.md', 'run.sh'],
        cwd=root, env=scrubbed_env()).decode().split('\0')
    found, violations = [], []
    for name in filter(None, paths):
        path = Path(name)
        if 'selftest' in path.name or path.name.startswith(('test_', 'test-')):
            continue
        text = (root / name).read_text()
        if path.suffix == '.json':
            def walk(value):
                if isinstance(value, dict):
                    for item in value.values():
                        walk(item)
                elif isinstance(value, list):
                    if value and value[0] in ('restore-rehearse', 'bin/restore-rehearse.sh'):
                        found.append(name)
                        if '--backup-run-id' not in value or value.index('--backup-run-id') + 1 >= len(value):
                            violations.append(name)
                    for item in value:
                        walk(item)
            walk(json.loads(text))
            continue
        for number, line in enumerate(text.replace('\\\n', ' ').splitlines(), 1):
            if path.suffix == '.py' and line.lstrip().startswith('#'):
                continue
            for match in CALL.finditer(line):
                if re.search(r"--source-ref\s+\S*$", line[:match.start()]):
                    continue
                command = line[match.end():]
                found.append(f'{name}:{number}')
                if ('--backup-run-id' not in command
                        or re.search(r'--(?:dump|date)\b', command)):
                    violations.append(f'{name}:{number}')
    return found, violations


class RestoreCallers(unittest.TestCase):
    def test_all_tracked_callers_identify_the_backup_run(self):
        found, violations = caller_violations(ROOT)
        for required in ('bin/key-recovery-test.sh', 'bin/restore-rehearse.sh',
                         'ops/config/control-plane-workflows.v1.json',
                         'ops/scheduled-tasks/restore-rehearse-weekly.SKILL.md',
                         'run.sh', 'RECOVERY.md'):
            self.assertTrue(any(item == required or item.startswith(required + ':')
                                for item in found), required)
        self.assertEqual(violations, [], '\n'.join(violations))

    def test_inventory_rejects_new_unbound_callers(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env = fixture_env()
            subprocess.run(['git', 'init', '-q', str(root)], env=env, check=True)
            for name, text in {
                'future.sh': './run.sh restore-rehearse\n',
                'future.md': './run.sh restore-rehearse --backup-run-id 123 --dump local.sql\n',
                'direct.sh': './bin/restore-rehearse.sh --preflight\n',
                'future.json': '{"execution":{"args":["restore-rehearse"]}}',
            }.items():
                (root / name).write_text(text)
            subprocess.run(['git', 'add', 'future.sh', 'future.md', 'direct.sh', 'future.json'],
                           cwd=root, env=env, check=True)
            _, violations = caller_violations(root)
            self.assertEqual({item.split(':')[0] for item in violations},
                             {'future.sh', 'future.md', 'direct.sh', 'future.json'})

    def test_recovery_argument_parser_accepts_run_ids_and_refuses_local_backups(self):
        source = (ROOT / 'bin/key-recovery-test.sh').read_text()
        parser = source[source.index('BACKUP_RUN_ID=latest'):source.index('say()')]
        for args, expected in (([], 'latest'), (['--backup-run-id', '123'], '123'),
                               (['--backup-run-id', 'latest'], 'latest'),
                               (['--backup', 'local.sql'], None),
                               (['--backup-run-id'], None),
                               (['--backup-run-id', 'invalid'], None)):
            result = subprocess.run(['zsh', '-c', parser + '\nprint -r -- "$BACKUP_RUN_ID"',
                                     'fixture', *args], capture_output=True, text=True)
            with self.subTest(args=args):
                self.assertEqual(result.returncode, 0 if expected else 2, result.stderr)
                if expected:
                    self.assertEqual(result.stdout.strip(), expected)

    def test_latest_selection_is_pinned_once_and_failure_stops_before_restore(self):
        source = (ROOT / 'bin/restore-rehearse.sh').read_text()
        start = source.index('if [ "$BACKUP_RUN_ID" = latest ]; then')
        selection = source[start:source.index('\nfi', start) + 3]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            selector = root / 'metadata-selector'
            selector.write_text('#!/bin/sh\n[ "$SELECTOR_FAIL" = 1 ] && exit 2\necho 123\n')
            selector.chmod(0o755)
            env = dict(scrubbed_env(), PY=str(selector), REPO=str(root),
                       BACKUP_REPOSITORY='jbookout/carr-system')
            command = 'die() { exit 1; }\n' + selection + '\nprint -r -- "$BACKUP_RUN_ID"'
            for run_id, failure, expected in (('latest', '0', '123'),
                                               ('456', '1', '456'),
                                               ('latest', '1', None)):
                with self.subTest(run_id=run_id, failure=failure):
                    result = subprocess.run(['zsh', '-c', command],
                        env={**env, 'BACKUP_RUN_ID': run_id, 'SELECTOR_FAIL': failure},
                        capture_output=True, text=True)
                    self.assertEqual(result.returncode, 0 if expected else 1)
                    self.assertEqual(result.stdout.strip(), expected or '')

    def test_recovery_handoff_passes_the_run_id_without_opening_an_identity(self):
        source = (ROOT / 'bin/key-recovery-test.sh').read_text()
        command = next(line for line in source.splitlines()
                       if line.startswith('"$REPO/run.sh" restore-rehearse'))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'run.sh').write_text('#!/bin/sh\nprintf "%s\\n" "$@"\n')
            (root / 'run.sh').chmod(0o755)
            env = dict(scrubbed_env(), REPO=str(root), BACKUP_RUN_ID='123',
                       IDENTITY_FILE='/synthetic/nonexistent-identity',
                       RESTORE_OUTPUT=str(root / 'transcript'))
            result = subprocess.run(['zsh', '-c', command], env=env,
                                    capture_output=True, text=True, check=True)
            self.assertEqual(result.stdout.splitlines(),
                             ['restore-rehearse', '--backup-run-id', '123',
                              '--identity', '/synthetic/nonexistent-identity'])

    def test_usage_does_not_advertise_the_removed_local_path(self):
        usage = next(line for line in (ROOT / 'run.sh').read_text().splitlines()
                     if 'usage: run.sh' in line).split('|restore-rehearse', 1)[1].split('|', 1)[0]
        self.assertIn('--backup-run-id', usage)
        self.assertNotIn('--date', usage)
        self.assertNotIn('--dump', usage)


if __name__ == '__main__':
    unittest.main()
