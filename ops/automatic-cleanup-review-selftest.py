import ast
import importlib.util
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'lib'), str(ROOT / 'tools'), str(ROOT / 'ops')]
import machine_cleanup
import storage_hygiene

spec = importlib.util.spec_from_file_location('fixtures', ROOT / 'ops/branch-janitor-selftest.py')
assert spec is not None and spec.loader is not None
fixtures = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixtures)


class ReviewProbes(unittest.TestCase):
    def test_interrupted_temp_effect_is_reconciled_in_next_receipt(self):
        from unittest.mock import patch
        import job_watchdog
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            scratch, clones = root / 'scratch', root / 'clones'
            scratch.mkdir()
            clones.mkdir()
            candidate = scratch / 'carr-ci-test-owned'
            candidate.mkdir()
            os.utime(candidate, (0, 0))
            config = job_watchdog.load_config(ROOT / 'ops/config/job-watchdog.json')
            options = dict(hook=None, tmp_root=clones, user_tmp_root=scratch, roots={},
                dispatch_path=root / 'absent.jsonl', provider=object(),
                process_probe=lambda paths: set(), archive_thread=lambda *a, **k: None)
            remove = storage_hygiene.shutil.rmtree
            def interrupt_after_effect(*args, **kwargs):
                remove(*args, **kwargs)
                raise KeyboardInterrupt('owned fixture interrupt after effect')
            with patch.object(storage_hygiene.shutil, 'rmtree', side_effect=interrupt_after_effect):
                with self.assertRaises(KeyboardInterrupt):
                    machine_cleanup.run(root, config, **options)
            import json
            interrupted = json.loads((root / 'out/machine-cleanup.json').read_text())
            self.assertEqual(interrupted['pending']['kind'], 'test_temp')
            self.assertFalse(candidate.exists())
            recovered = machine_cleanup.run(root, config, **options)
            self.assertTrue(any(row.get('path') == str(candidate) for row in recovered['removed'])
                or recovered['status'] == 'failed', recovered)

    def test_nested_write_during_final_probe_is_kept(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            candidate = root / 'carr-ci-test-owned'
            nested = candidate / 'nested'
            nested.mkdir(parents=True)
            payload = nested / 'payload'
            payload.write_text('old')
            for path in (payload, nested, candidate):
                os.utime(path, (0, 0))
            calls = []
            def probe(paths):
                calls.append(paths)
                if len(calls) == 2:
                    payload.write_text('just written by a test that has now exited')
                return set()
            report = storage_hygiene.cleanup_test_temps(root, now=time.time(), min_age=21600,
                max_items=1000, cursor='', process_probe=probe, deadline=lambda: False,
                dry_run=False)
            self.assertTrue(payload.exists(), report)

    def test_detached_clone_with_open_pr_is_kept(self):
        fixture = fixtures.Fixture()
        workspace = fixture.base / 'room-codex-owned'
        clone = workspace / 'checkout'
        workspace.mkdir()
        fixture.git('clone', str(fixture.remote), str(clone))
        fixture.git('switch', 'main', cwd=clone)
        fixture.git('switch', '-c', 'job', cwd=clone)
        fixture.git('config', 'user.email', 'fixture@example.invalid', cwd=clone)
        fixture.git('config', 'user.name', 'Fixture', cwd=clone)
        head = fixture.commit('pushed open PR work', cwd=clone)
        fixture.git('push', 'origin', 'job', cwd=clone)
        fixture.git('checkout', '--detach', head, cwd=clone)
        pr = fixtures.pull(1, head, fixture.git('rev-parse', 'main'))
        pr['head']['ref'] = 'job'
        provider = fixtures.FakeProvider([pr])
        receipt = machine_cleanup.clean_clone(workspace, {'status': 'completed',
            'checkout_path': str(clone), 'checkout_workspace': str(workspace),
            'dispatched_at': '2020-01-01T00:00:00+00:00'}, provider=provider,
            process_probe=lambda paths: set(), now=time.time() + 8 * 86400,
            older_than=7 * 86400, dry_run=False)
        self.assertEqual(receipt['status'], 'kept', receipt)

    def test_health_storage_reader_still_has_callable_root_discovery(self):
        tree = ast.parse((ROOT / 'tools/health-check.py').read_text())
        fn = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                  and node.name == '_storage_hygiene_row')
        scope = {'sys': sys, 'Path': Path}
        exec(compile(ast.Module(body=[fn], type_ignores=[]), 'health-check.py', 'exec'), scope)
        from unittest.mock import patch
        with patch.object(storage_hygiene, 'storage_roots', return_value=(Path('/fixture/T'), Path('/fixture/X'), Path('/fixture/R'))), \
             patch.object(storage_hygiene, 'storage_snapshot', return_value=(0, 0)):
            scope['_storage_hygiene_row']()


if __name__ == '__main__':
    unittest.main(verbosity=2)
