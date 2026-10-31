#!/usr/bin/env python3
"""Checkout refresh stays independent of other fetches in the same Git repository."""
import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class CheckoutPullTests(unittest.TestCase):
    def test_launchagent_does_not_share_fetch_head_with_other_fetches(self):
        import plistlib
        agent = plistlib.loads((ROOT / 'ops/launchd/com.carr.checkout-pull.plist').read_bytes())
        self.assertEqual(agent['ProgramArguments'],
                         ['{{REPO}}/.venv/bin/python', '{{REPO}}/ops/checkout-pull.py', '{{REPO}}'])

    def test_failed_fetch_never_merges_and_only_lock_errors_retry(self):
        spec = importlib.util.spec_from_file_location('checkout_pull', ROOT / 'ops/checkout-pull.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        for message, attempts in [('cannot lock ref refs/remotes/origin/main', 2),
                                  ('authentication failed', 1)]:
            calls = []
            def runner(argv, **kwargs):
                calls.append(argv)
                if 'symbolic-ref' in argv:
                    return subprocess.CompletedProcess(argv, 0, 'main\n', '')
                return subprocess.CompletedProcess(argv, 1, '', message)
            self.assertEqual(module.update(Path('/synthetic'), runner=runner), 1)
            self.assertEqual(sum('fetch' in argv for argv in calls), attempts)
            self.assertFalse(any('merge' in argv for argv in calls))

    def test_competing_fetch_is_retried_and_cannot_redirect_the_merge(self):
        spec = importlib.util.spec_from_file_location('checkout_pull', ROOT / 'ops/checkout-pull.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        update = module.update
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            upstream, checkout = root / 'upstream', root / 'checkout'
            def git(repo, *args):
                return subprocess.run(['git', '-C', str(repo), *args], check=True,
                                      capture_output=True, text=True).stdout.strip()
            upstream.mkdir()
            git(upstream, 'init', '-b', 'main')
            git(upstream, 'config', 'user.email', 'fixture@example.invalid')
            git(upstream, 'config', 'user.name', 'Fixture')
            (upstream / 'file').write_text('first')
            git(upstream, 'add', 'file')
            git(upstream, 'commit', '-m', 'first')
            old = git(upstream, 'rev-parse', 'HEAD')
            subprocess.run(['git', 'clone', '-q', str(upstream), str(checkout)], check=True)
            (upstream / 'file').write_text('second')
            git(upstream, 'commit', '-am', 'second')
            new = git(upstream, 'rev-parse', 'HEAD')
            lock = checkout / '.git/refs/remotes/origin/main.lock'
            lock.write_text('another fetch owns this ref')
            fetches = []
            def competing_fetch(argv, **kwargs):
                result = subprocess.run(argv, **kwargs)
                if 'fetch' in argv:
                    fetches.append(result.returncode)
                    if result.returncode:
                        lock.unlink()
                    else:
                        git(checkout, 'fetch', '-q', '--no-tags', '--refmap=', 'origin', old)
                return result
            self.assertEqual(update(checkout, runner=competing_fetch), 0)
            self.assertEqual(git(checkout, 'rev-parse', 'HEAD'), new)
            self.assertEqual(git(checkout, 'rev-parse', 'origin/main'), new)
            self.assertEqual(git(checkout, 'rev-parse', 'FETCH_HEAD'), old)
            self.assertEqual(fetches, [1, 0])
            git(checkout, 'switch', '-c', 'work')
            self.assertNotEqual(update(checkout), 0)


if __name__ == '__main__':
    unittest.main()
