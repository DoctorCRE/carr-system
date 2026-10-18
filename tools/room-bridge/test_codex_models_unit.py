#!/usr/bin/env python3
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import desks
import dispatch


@contextlib.contextmanager
def catalog_fixture():
    with tempfile.TemporaryDirectory() as root:
        Path(root, 'models_cache.json').write_text(json.dumps({'models': [
            {'slug': 'gpt-6.1-sol', 'display_name': 'GPT-6.1-Sol'},
            {'slug': 'gpt-6-luna', 'display_name': 'GPT-6-Luna'}]}))
        with patch.dict(os.environ, {'CODEX_HOME': root}):
            yield


class FamilyDispatchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.catalog = self.root / 'models_cache.json'
        self.catalog.write_text(json.dumps({'models': [
            {'slug': slug, 'display_name': slug.upper()} for slug in
            ['gpt-5.6-sol', 'gpt-6-sol', 'gpt-6.1-sol', 'gpt-5.6-luna', 'gpt-6-luna']]}))
        self.env = patch.dict(os.environ, {'CODEX_HOME': str(self.root)})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.reg = desks.Registry(self.root / 'desks.json')
        self.results = self.root / 'results.jsonl'

    def send(self, **kwargs):
        seen = []
        def run(argv, **options):
            seen.append(argv)
            Path(argv[argv.index('-o') + 1]).write_text('done')
            return subprocess.CompletedProcess(argv, 0, '{"type":"turn.completed"}\n', '')
        with patch.object(dispatch.subprocess, 'run', side_effect=run):
            row = dispatch.dispatch('cx', 'build it', registry=self.reg,
                                    results_path=self.results, **kwargs)
        return row, seen

    def test_stale_entries_do_not_pin_sol(self):
        self.reg.register('cx', 'codex-session', family='sol', effort='high')
        row, seen = self.send()
        self.assertEqual(row['model'], 'gpt-6.1-sol')
        self.assertEqual(row['family'], 'sol')
        self.assertEqual(seen[0][seen[0].index('-m') + 1], 'gpt-6.1-sol')

    def test_newer_sol_moves_without_registry_edit(self):
        self.reg.register('cx', 'codex-session', family='sol', effort='high')
        before = self.reg.path.read_bytes()
        self.assertEqual(self.send()[0]['model'], 'gpt-6.1-sol')
        data = json.loads(self.catalog.read_text())
        data['models'] += [{'slug': s, 'display_name': s} for s in
                           ['gpt-6.2-sol', 'gpt-6.10-sol', 'gpt-6.9-sol']]
        self.catalog.write_text(json.dumps(data))
        self.assertEqual(self.send()[0]['model'], 'gpt-6.10-sol')
        self.assertEqual(self.reg.path.read_bytes(), before)

    def test_luna_job_overrides_sol_default_and_effort(self):
        self.reg.register('cx', 'codex-session', family='sol', effort='high')
        row, seen = self.send(family='luna', effort='low')
        self.assertEqual(row['model'], 'gpt-6-luna')
        self.assertEqual(row['effort'], 'low')
        self.assertIn('model_reasoning_effort=low', seen[0])
        self.assertEqual(self.reg.entries()['cx']['family'], 'sol')

    def test_priority_order_is_not_version_order_for_either_family(self):
        self.reg.register('cx', 'codex-session', family='sol', effort='high')
        for family in ('sol', 'luna'):
            with self.subTest(family=family):
                self.catalog.write_text(json.dumps({'models': [
                    {'slug': f'gpt-{version}-{family}', 'priority': priority}
                    for priority, version in enumerate(('6.9', '6.10', '5.6', '6.1'))]}))
                self.assertEqual(self.send(family=family)[0]['model'], f'gpt-6.10-{family}')

    def test_hidden_and_retired_versions_are_not_executors(self):
        self.reg.register('cx', 'codex-session', family='sol', effort='high')
        self.catalog.write_text(json.dumps({'models': [
            {'slug': 'gpt-9-sol', 'visibility': 'hide'},
            {'slug': 'gpt-8-sol', 'retired': True},
            {'slug': 'gpt-7-sol', 'hidden': True},
            {'slug': 'gpt-6.10-sol', 'visibility': 'list'},
            {'slug': 'gpt-6.9-sol'}]}))
        self.assertEqual(self.send()[0]['model'], 'gpt-6.10-sol')

    def test_only_hidden_family_refuses_without_codex(self):
        self.reg.register('cx', 'codex-session', family='luna', effort='low')
        self.catalog.write_text(json.dumps({'models': [
            {'slug': 'gpt-6-luna', 'visibility': 'hide'}]}))
        with patch.object(dispatch.subprocess, 'run') as run:
            with self.assertRaises(desks.DeskError):
                dispatch.dispatch('cx', 'work', registry=self.reg, results_path=self.results)
            run.assert_not_called()

    def test_catalog_refusal_starts_zero_processes(self):
        self.reg.register('cx', 'codex-session', family='sol', effort='high')
        for content in (None, '{broken', '{}', '{"models":[]}', '{"models":42}'):
            with self.subTest(content=content):
                if content is None:
                    self.catalog.unlink(missing_ok=True)
                else:
                    self.catalog.write_text(content)
                with patch.object(dispatch.subprocess, 'run') as run, \
                     patch.object(dispatch.subprocess, 'Popen') as popen:
                    with self.assertRaises(desks.DeskError):
                        dispatch.dispatch('cx', 'build', registry=self.reg,
                                          results_path=self.results)
                    run.assert_not_called()
                    popen.assert_not_called()
        self.assertFalse(self.results.exists())

    def test_unreadable_catalog_refuses(self):
        self.reg.register('cx', 'codex-session', family='sol', effort='high')
        with patch.object(Path, 'read_text', side_effect=PermissionError('denied')):
            import codex_models
            with self.assertRaises(desks.DeskError):
                codex_models.resolve_model('sol')

    def test_legacy_pins_migrate_to_family_without_losing_context(self):
        for kind, pin, family, latest in [
            ('codex-session', 'gpt-5.6-sol', 'sol', 'gpt-6.1-sol'),
            ('codex-exec', 'gpt-5.6-luna', 'luna', 'gpt-6-luna'),
            ('codex-live', 'gpt-5.1-codex-mini', 'sol', 'gpt-6.1-sol')]:
            with self.subTest(kind=kind):
                self.reg.path.write_text(json.dumps({'desks': {'cx': {
                    'kind': kind, 'model': pin, 'effort': 'high', 'thread_id': 'old-thread',
                    'socket': '/tmp/cx.sock', 'room_seat': 'builder'}}}))
                with patch.object(desks, 'is_live', return_value=True), \
                     patch.object(dispatch.codex_wire, 'run_turn', return_value={'status': 'completed'}):
                    row, _ = self.send()
                self.assertEqual(row['model'], latest)
                entry = json.loads(self.reg.path.read_text())['desks']['cx']
                self.assertNotIn('model', entry)
                self.assertEqual(entry['family'], family)
                self.assertEqual(entry['thread_id'], 'old-thread')
                self.assertEqual(entry['room_seat'], 'builder')

    def test_missing_family_or_effort_refuses(self):
        self.reg.register('cx', 'codex-session')
        with self.assertRaises(desks.DeskError):
            self.send(effort='high')
        with self.assertRaises(desks.DeskError):
            self.send(family='sol')

    def test_registration_stores_no_version(self):
        self.reg.register('cx', 'codex-session', model='gpt-5.6-sol', effort='high')
        self.assertNotIn('model', self.reg.entries()['cx'])
        self.assertEqual(self.reg.entries()['cx']['family'], 'sol')

    def test_desks_lists_current_resolution(self):
        self.reg.register('cx', 'codex-session', family='luna', effort='low')
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(dispatch.main(['--registry', str(self.reg.path), 'desks']), 0)
        self.assertIn('luna', out.getvalue())
        self.assertIn('gpt-6-luna', out.getvalue())

    def test_send_cli_names_resolved_executor_before_launch(self):
        self.reg.register('cx', 'codex-session')
        out = io.StringIO()
        executor = io.StringIO()
        def execute(entry, *args, **kwargs):
            self.assertIn('gpt-6-luna', executor.getvalue())
            self.assertIn('low', executor.getvalue())
            return {'status': 'completed'}
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(executor), \
             patch.object(dispatch.seat_gate, 'check', return_value=None), \
             patch.object(dispatch, '_to_codex', side_effect=execute):
            code = dispatch.main(['--registry', str(self.reg.path), '--results', str(self.results),
                                  'send', 'cx', 'work', '--family', 'luna', '--effort', 'low'])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out.getvalue())['model'], 'gpt-6-luna')

    def test_desks_without_default_names_job_requirement(self):
        self.reg.register('cx', 'codex-session')
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(dispatch.main(['--registry', str(self.reg.path), 'desks']), 0)
        self.assertIn('job family required', out.getvalue())

    def test_job_environment_selects_its_catalog(self):
        self.reg.register('cx', 'codex-session', family='sol', effort='high')
        other = self.root / 'other'
        other.mkdir()
        (other / 'models_cache.json').write_text(json.dumps({'models': [
            {'slug': 'gpt-7-sol', 'display_name': 'GPT-7-Sol'}]}))
        row, _ = self.send(env={'CODEX_HOME': str(other)})
        self.assertEqual(row['model'], 'gpt-7-sol')


if __name__ == '__main__':
    unittest.main(verbosity=2)
