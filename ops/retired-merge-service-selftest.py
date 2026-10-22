#!/usr/bin/env python3
"""Keep the retired merge executor absent and prove each refusal with fixtures."""
import json
from pathlib import Path
import plistlib
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
RETIRED = 'merge' + '-queue'
DECISION = 'Joe decision-history: Merge-queue daemon deleted; merges use GitHub auto-merge bound to the approved head SHA.'


def violations(root):
    found = []
    if (root / 'tools' / RETIRED.replace('-', '_')).exists():
        found.append('retired executor directory exists')
    labels = subprocess.run(['git', '-C', str(root), 'grep', '-lF', 'com.carr.' + RETIRED,
                             '--', '.', ':!migrations/**', ':!db/**', ':!ops/evidence/**',
                             ':!audits/**', ':!tools/ci/**', ':!docs/agents/*audit*',
                             ':!**/.env*', ':!**/*credentials*', ':!**/*tokens*',
                             ':!**/*keys*', ':!**/*keychain*'], capture_output=True, text=True)
    if labels.returncode not in (0, 1):
        raise RuntimeError(labels.stderr)
    found.extend('retired launchd label in ' + path for path in labels.stdout.splitlines())
    for service in json.loads((root / 'ops/config/services.json').read_text())['services']:
        identity = ' '.join(str(service.get(key, '')) for key in ('key', 'name', 'repo_path')).lower()
        description = identity + ' ' + service.get('purpose', '').lower()
        if ('merg' in identity and service.get('runtime') == 'launchd') or ('merg' in description and 'daemon' in description):
            found.append(f'long-running merge executor registered: {service["key"]}')
    return found


class AbsenceTests(unittest.TestCase):
    def test_checkout_keeps_retired_executor_absent(self):
        self.assertEqual(violations(ROOT), [], DECISION)

    def test_reintroduction_is_rejected_at_each_surface(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / 'ops/config/services.json'
            config.parent.mkdir(parents=True)
            config.write_text('{"services": []}')
            subprocess.run(['git', 'init', '-q', str(root)], check=True)
            self.assertEqual(violations(root), [])
            retired = root / 'tools' / RETIRED.replace('-', '_')
            retired.mkdir(parents=True)
            self.assertEqual(len(violations(root)), 1, DECISION)
            retired.rmdir()
            label = root / 'renamed.txt'
            label.write_bytes(plistlib.dumps({'Label': 'com.carr.' + RETIRED}))
            subprocess.run(['git', '-C', str(root), 'add', '--', label.name], check=True)
            self.assertEqual(len(violations(root)), 1, DECISION)
            label.unlink()
            subprocess.run(['git', '-C', str(root), 'add', '--', label.name], check=True)
            config.write_text(json.dumps({'services': [dict(key='renamed', name='Long-running merge daemon', runtime='launchd')]}))
            self.assertEqual(len(violations(root)), 1, DECISION)
            config.write_text(json.dumps({'services': [dict(key='merge-worker', name='Merge worker', runtime='launchd')]}))
            self.assertEqual(len(violations(root)), 1, DECISION)


if __name__ == '__main__':
    unittest.main()
