#!/usr/bin/env python3
"""Conflicting jobs stop before launch, including paths not created yet."""
import ast
import contextlib
from datetime import datetime, timedelta, timezone
import io
import json
import os
from pathlib import Path
import sys
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import desks
import dispatch
import write_ownership
from test_codex_models_unit import catalog_fixture
from test_codex_live_unit import FakeAppServer
from test_dispatch_unit import Listener


class OwnershipTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.reg = desks.Registry(self.root / 'desks.json')
        self.reg.register('sol', 'codex-session', family='sol', effort='high', cwd=str(self.root))
        self.results = self.root / 'results.jsonl'
        self.ledger = self.root / 'claims.jsonl'
        authority = patch.object(write_ownership, 'LEDGER', self.ledger)
        authority.start()
        self.addCleanup(authority.stop)
        self.catalog = catalog_fixture()
        self.catalog.__enter__()
        self.addCleanup(self.catalog.__exit__, None, None, None)
        # Isolate adapter unit tests from the child transport. Gate/crash tests
        # below use real processes and stop this fixture explicitly when needed.
        self.transport = patch.object(write_ownership, '_run_gated', side_effect=self.run_adapter)
        self.transport.start()
        self.addCleanup(self.transport.stop)

    def run_adapter(self, msg_id, request, on_bound=None):
        def bind(identity):
            row = write_ownership.bind_executor(msg_id, identity)
            if on_bound:
                on_bound(row)
        return dispatch._execute(request, on_executor=bind)

    def completed(self, *args, **kwargs):
        if kwargs.get('on_executor'):
            kwargs['on_executor']({**write_ownership.process_owner(), 'kind': 'process_group',
                               'pid': 2147483647, 'pgid': 2147483647, 'start_time': 'fixture'})
        return {'status': 'completed', 'termination_confirmed': True}

    def send(self, brief='build', **kwargs):
        return dispatch.dispatch('sol', brief, registry=self.reg, results_path=self.results, **kwargs)

    def active(self, **kwargs):
        self.ledger.write_text(json.dumps({'msg_id': 'other', 'repo': 'owner/repo',
            'desk': 'other-sol', 'status': 'running', 'writes': ['tools/*.py'], 'own_pr': 42,
            'ownership_state': 'held', 'launch_marker': 'fixture', 'executor': {'kind': 'unconfirmed'},
            **kwargs}) + '\n')

    def test_writes_none_declares_no_files(self):
        self.assertEqual([], write_ownership.declaration('Writes: none\nreview PR 240', None))
        self.assertEqual([], write_ownership.declaration('Writes: None', None))

    def test_matching_machine_recovers_dead_executor_after_hostname_rename(self):
        machine = '11111111-1111-4111-8111-111111111111'
        self.active(executor={'kind': 'process_group', 'host': 'before-rename',
            'machine_id': machine, 'pid': 1234, 'pgid': 1234, 'start_time': 'old'})
        with patch.object(write_ownership, 'machine_id', return_value=machine, create=True), \
             patch.object(write_ownership.socket, 'gethostname', return_value='after-rename'), \
             patch.object(write_ownership.os, 'kill', side_effect=ProcessLookupError), \
             patch.object(write_ownership.os, 'killpg', side_effect=ProcessLookupError):
            self.assertEqual(write_ownership.reconcile('other')[0]['ownership_state'], 'released')

    def test_foreign_machine_matching_hostname_cannot_release(self):
        self.active(executor={'kind': 'process_group', 'host': 'same-name',
            'machine_id': '22222222-2222-4222-8222-222222222222',
            'pid': 1234, 'pgid': 1234, 'start_time': 'old'})
        with patch.object(write_ownership, 'machine_id',
                          return_value='11111111-1111-4111-8111-111111111111', create=True), \
             patch.object(write_ownership.socket, 'gethostname', return_value='same-name'), \
             patch.object(write_ownership.os, 'kill', side_effect=ProcessLookupError) as kill, \
             patch.object(write_ownership.os, 'killpg', side_effect=ProcessLookupError):
            self.assertEqual(write_ownership.reconcile('other')[0]['ownership_state'], 'held')
            kill.assert_not_called()

    def test_legacy_observed_hostname_recovers_after_rename(self):
        with patch.object(write_ownership, 'machine_id',
                          return_value='11111111-1111-4111-8111-111111111111', create=True), \
             patch.object(write_ownership.socket, 'gethostname', return_value='observed-before'):
            write_ownership.process_owner()
        self.active(executor={'kind': 'process_group', 'host': 'observed-before',
                              'pid': 1234, 'pgid': 1234, 'start_time': 'old'})
        with patch.object(write_ownership, 'machine_id',
                          return_value='11111111-1111-4111-8111-111111111111', create=True), \
             patch.object(write_ownership.socket, 'gethostname', return_value='after-rename'), \
             patch.object(write_ownership.os, 'kill', side_effect=ProcessLookupError), \
             patch.object(write_ownership.os, 'killpg', side_effect=ProcessLookupError):
            self.assertEqual(write_ownership.reconcile('other')[0]['ownership_state'], 'released')

    def test_legacy_unobserved_hostname_stays_held_even_if_current(self):
        self.active(executor={'kind': 'process_group', 'host': 'never-observed',
                              'pid': 1234, 'pgid': 1234, 'start_time': 'old'})
        with patch.object(write_ownership, 'machine_id',
                          return_value='11111111-1111-4111-8111-111111111111', create=True), \
             patch.object(write_ownership.socket, 'gethostname', return_value='never-observed'), \
             patch.object(write_ownership.os, 'kill', side_effect=ProcessLookupError) as kill, \
             patch.object(write_ownership.os, 'killpg', side_effect=ProcessLookupError):
            self.assertEqual(write_ownership.reconcile('other')[0]['ownership_state'], 'held')
            kill.assert_not_called()

    def test_machine_id_macos_lookup_is_cached_and_parses_ioreg(self):
        write_ownership.machine_id.cache_clear()
        self.addCleanup(write_ownership.machine_id.cache_clear)
        uuid = '11111111-1111-4111-8111-111111111111'
        result = subprocess.CompletedProcess([], 0,
            '+-o IOPlatformExpertDevice\n    "IOPlatformUUID" = "' + uuid.upper() + '"\n', '')
        with patch.object(write_ownership.sys, 'platform', 'darwin'), \
             patch.object(write_ownership.subprocess, 'run', return_value=result) as run:
            self.assertEqual(write_ownership.machine_id(), uuid)
            self.assertEqual(write_ownership.machine_id(), uuid)
        run.assert_called_once_with(['/usr/sbin/ioreg', '-r', '-d', '1', '-c',
            'IOPlatformExpertDevice'], capture_output=True, text=True, check=True, timeout=2)

    def test_machine_id_linux_lookup_and_unavailable_id(self):
        self.addCleanup(write_ownership.machine_id.cache_clear)
        for raw, expected in (('a' * 32 + '\n', 'a' * 32), ('garbled', None)):
            write_ownership.machine_id.cache_clear()
            with patch.object(write_ownership.sys, 'platform', 'linux'), \
                 patch.object(Path, 'read_text', return_value=raw) as read:
                self.assertEqual(write_ownership.machine_id(), expected)
                read.assert_called_once_with()
        write_ownership.machine_id.cache_clear()
        with patch.object(write_ownership.sys, 'platform', 'darwin'), \
             patch.object(write_ownership.subprocess, 'run', side_effect=OSError):
            self.assertIsNone(write_ownership.machine_id())

    def test_present_invalid_machine_id_never_uses_observed_hostname(self):
        local = write_ownership.process_owner()
        for invalid in (None, '', 'malformed', 123, {}, []):
            with self.subTest(machine_id=invalid), \
                 patch.object(write_ownership.os, 'kill', side_effect=ProcessLookupError) as kill:
                self.assertFalse(write_ownership.process_terminated({**local,
                    'machine_id': invalid, 'pid': 1234, 'start_time': 'old'}))
                kill.assert_not_called()

    def test_alias_corruption_and_io_failure_only_block_legacy_identity(self):
        local = {**write_ownership.process_owner(), 'pid': 1234, 'start_time': 'old'}
        legacy = {k: v for k, v in local.items() if k != 'machine_id'}
        write_ownership._alias_path().write_text('{broken\n')
        with patch.object(write_ownership.os, 'kill', side_effect=ProcessLookupError):
            self.assertTrue(write_ownership.process_terminated(local))
            self.assertFalse(write_ownership.process_terminated(legacy))
        with patch.object(write_ownership, '_aliases', side_effect=OSError), \
             patch.object(write_ownership.os, 'kill', side_effect=ProcessLookupError):
            self.assertEqual(write_ownership.process_owner()['machine_id'], local['machine_id'])
            self.assertTrue(write_ownership.process_terminated(local))
            self.assertFalse(write_ownership.process_terminated(legacy))

    def test_alias_shared_by_two_machines_is_ambiguous(self):
        first, second = '1' * 32, '2' * 32
        for machine in (first, second):
            with patch.object(write_ownership, 'machine_id', return_value=machine), \
                 patch.object(write_ownership.socket, 'gethostname', return_value='shared-host'):
                write_ownership.process_owner()
        with patch.object(write_ownership, 'machine_id', return_value=first), \
             patch.object(write_ownership.os, 'kill', side_effect=ProcessLookupError) as kill:
            self.assertFalse(write_ownership.process_terminated(
                {'host': 'shared-host', 'pid': 1234, 'start_time': 'old'}))
            kill.assert_not_called()

    def test_busy_alias_lock_does_not_delay_modern_process_identity(self):
        alias = write_ownership._alias_path()
        self.assertTrue(write_ownership.machine_id())
        with Path(str(alias) + '.lock').open('a') as lock:
            write_ownership.fcntl.flock(lock, write_ownership.fcntl.LOCK_EX)
            started = time.monotonic()
            local = write_ownership.process_owner()
            self.assertLess(time.monotonic() - started, 1)
            self.assertTrue(local['machine_id'])
            self.assertFalse(alias.exists())
            with patch.object(write_ownership.os, 'kill', side_effect=ProcessLookupError):
                self.assertTrue(write_ownership.process_terminated(
                    {**local, 'pid': 1234, 'start_time': 'old'}))
            write_ownership.fcntl.flock(lock, write_ownership.fcntl.LOCK_UN)

    def test_rename_allows_launch_and_provider_identity_refinement(self):
        machine = '1' * 32
        with patch.object(write_ownership, 'machine_id', return_value=machine), \
             patch.object(write_ownership.socket, 'gethostname', return_value='first-name'), \
             patch.object(write_ownership, 'open_prs', return_value=('owner/repo', [])):
            reserved = write_ownership.reserve({'msg_id': 'rename', 'task': 'build'}, '.', ['src/*'])
        self.assertEqual(reserved['executor'], {'kind': 'reservation',
            'host': 'first-name', 'machine_id': machine})
        with patch.object(write_ownership, 'machine_id', return_value=machine), \
             patch.object(write_ownership.socket, 'gethostname', return_value='second-name'):
            launched = write_ownership.launch('rename')
            first = write_ownership.bind_executor('rename', {'kind': 'codex_turn',
                'socket': '/fixture.sock', 'thread_id': 'thread', 'turn_id': None})
        self.assertEqual(launched['executor'], {'kind': 'unconfirmed',
            'host': 'second-name', 'machine_id': machine})
        self.assertEqual(first['executor']['machine_id'], machine)
        with patch.object(write_ownership, 'machine_id', return_value=machine), \
             patch.object(write_ownership.socket, 'gethostname', return_value='third-name'):
            final = write_ownership.bind_executor('rename', {'kind': 'codex_turn',
                'socket': '/fixture.sock', 'thread_id': 'thread', 'turn_id': 'turn'})
        self.assertEqual(final['executor']['host'], 'third-name')
        self.assertEqual(final['executor']['machine_id'], machine)
        self.assertEqual(final['executor']['turn_id'], 'turn')

    def test_bind_stamps_all_executor_kinds_and_rejects_foreign_machine(self):
        with patch.object(write_ownership, 'open_prs', return_value=('owner/repo', [])):
            write_ownership.reserve({'msg_id': 'binding', 'task': 'build'}, '.', ['src/*'])
        write_ownership.launch('binding')
        local = write_ownership.process_owner()
        with self.assertRaisesRegex(desks.DeskError, 'not local'):
            write_ownership.bind_executor('binding', {'kind': 'codex_turn',
                'machine_id': 'f' * 32, 'host': local['host']})
        self.assertEqual(write_ownership._claim('binding')['executor']['kind'], 'unconfirmed')
        with write_ownership._locked(self.ledger):
            row = write_ownership._claim('binding')
            write_ownership._persist({**row, 'executor': {'kind': 'unconfirmed',
                'host': local['host'], 'machine_id': 'f' * 32}})
        with self.assertRaisesRegex(desks.DeskError, 'not local'):
            write_ownership.bind_executor('binding', {'kind': 'no_launch'})
        for kind in ('unconfirmed', 'no_launch', 'codex_turn', 'codex_desktop', 'process_group'):
            with self.subTest(kind=kind):
                with write_ownership._locked(self.ledger):
                    row = write_ownership._claim('binding')
                    write_ownership._persist({**row, 'executor': {
                        'kind': 'unconfirmed', 'host': local['host'], 'machine_id': local['machine_id']}})
                bound = write_ownership.bind_executor('binding', {'kind': kind})
                self.assertEqual(bound['executor'], {'kind': kind,
                    'host': local['host'], 'machine_id': local['machine_id']})
        alias_rows = [json.loads(line) for line in write_ownership._alias_path().read_text().splitlines()]
        self.assertEqual(alias_rows, [{'host': local['host'], 'machine_id': local['machine_id']}])

    def test_supplied_hostname_cannot_teach_legacy_alias(self):
        with patch.object(write_ownership, 'open_prs', return_value=('owner/repo', [])):
            write_ownership.reserve({'msg_id': 'binding', 'task': 'build'}, '.', ['src/*'])
        write_ownership.launch('binding')
        local = write_ownership.process_owner()
        bound = write_ownership.bind_executor('binding', {'kind': 'codex_turn',
            'machine_id': local['machine_id'], 'host': 'caller-supplied'})
        self.assertEqual(bound['executor']['host'], local['host'])
        with patch.object(write_ownership.os, 'kill', side_effect=ProcessLookupError) as kill:
            self.assertFalse(write_ownership.process_terminated(
                {'host': 'caller-supplied', 'pid': 1234, 'start_time': 'old'}))
            kill.assert_not_called()

    def test_open_pr_overlap_refuses_before_launch(self):
        with patch('dispatch.write_ownership.open_prs', return_value=('owner/repo', [
                {'number': 42, 'title': 'the other builder', 'files': ['tools/new.py']}])) , \
             patch.object(dispatch, '_to_codex') as run:
            with self.assertRaisesRegex(desks.DeskError, 'PR 42.*build on top of PR 42'):
                self.send(writes=['tools/*.py'])
            run.assert_not_called()
        self.assertFalse(self.results.exists())

    def test_held_draft_overlap_warns_without_blocking(self):
        # A parked do_not_merge draft has no live builder; 2026-10-09 two of them
        # claimed every staging-harness file and froze all V1 harness fixes.
        with patch('dispatch.write_ownership.open_prs', return_value=('owner/repo', [
                {'number': 197, 'title': 'parked design', 'files': ['tools/new.py'], 'held': True}])), \
             patch.object(dispatch, '_to_codex', side_effect=self.completed) as run, \
             patch('sys.stderr', new_callable=io.StringIO) as err:
            self.send(writes=['tools/*.py'])
        run.assert_called_once()
        self.assertIn('held draft PR 197', err.getvalue())

    def test_held_needs_both_draft_and_do_not_merge_label(self):
        class Reader:
            def __init__(self, **kwargs): pass
            def json(self, args): return {'nameWithOwner': 'owner/repo'}
            def api(self, path, paginate=False):
                if path.endswith('/files'):
                    return [{'filename': 'tools/new.py'}]
                return [
                    {'number': 5, 'title': 'parked', 'draft': True, 'labels': [{'name': 'do_not_merge'}]},
                    {'number': 6, 'title': 'ready but held', 'draft': False, 'labels': [{'name': 'do_not_merge'}]},
                    {'number': 7, 'title': 'live draft', 'draft': True, 'labels': []},
                ]
        with patch.object(write_ownership, 'GitHubReader', Reader):
            _, prs = write_ownership.open_prs('.')
        self.assertEqual({pr['number']: pr['held'] for pr in prs}, {5: True, 6: False, 7: False})

    def test_open_pr_scan_runs_outside_the_ledger_lock(self):
        # The scan paginates every open PR's files at ~2 s per gh call; holding
        # the ledger lock across it refused every other desk for minutes.
        observed = []
        def scan(cwd):
            with open(str(self.ledger) + '.lock', 'a') as lock:
                try:
                    write_ownership.fcntl.flock(lock, write_ownership.fcntl.LOCK_EX | write_ownership.fcntl.LOCK_NB)
                    write_ownership.fcntl.flock(lock, write_ownership.fcntl.LOCK_UN)
                    observed.append('free')
                except BlockingIOError:
                    observed.append('held')
            return ('owner/repo', [])
        with patch('dispatch.write_ownership.open_prs', side_effect=scan), \
             patch.object(dispatch, '_to_codex', side_effect=self.completed):
            self.send(writes=['tools/*.py'])
        self.assertEqual(observed, ['free'])

    def test_inflight_overlap_catches_not_yet_created_paths(self):
        self.active(writes=['tools/new-*.py'])
        with patch('dispatch.write_ownership.open_prs', return_value=('owner/repo', [])), \
             patch.object(dispatch, '_to_codex') as run:
            with self.assertRaisesRegex(desks.DeskError, 'other-sol.*build on top of PR 42'):
                self.send(writes=['tools/*-worker.py'])
            run.assert_not_called()

    def test_own_pr_is_allowed_and_claim_exists_before_launch(self):
        def run(*args, **kwargs):
            rows = [json.loads(line) for line in self.results.read_text().splitlines()]
            self.assertEqual(rows[-1]['status'], 'running')
            self.assertEqual(rows[-1]['writes'], ['tools/*.py'])
            return self.completed(*args, **kwargs)
        with patch('dispatch.write_ownership.open_prs', return_value=('owner/repo', [
                {'number': 42, 'title': 'mine', 'files': ['tools/new.py']}])), \
             patch.object(dispatch, '_to_codex', side_effect=run):
            row = self.send('Own PR: #42\nWrites: tools/*.py')
        self.assertEqual(row['own_pr'], 42)
        self.assertEqual(row['repo'], 'owner/repo')

    def test_no_write_set_warns_and_preserves_delivery(self):
        warning = io.StringIO()
        with contextlib.redirect_stderr(warning), \
             patch('dispatch.write_ownership.open_prs') as prs, \
             patch.object(dispatch, '_to_codex', side_effect=self.completed):
            self.assertEqual(self.send()['status'], 'completed')
        prs.assert_not_called()
        self.assertIn('no write set', warning.getvalue())
        self.assertEqual(len(self.results.read_text().splitlines()), 1)

    def test_completed_claim_released_but_async_delivery_stays_owned(self):
        self.active(status='delivered_live')
        with patch('dispatch.write_ownership.open_prs', return_value=('owner/repo', [])), \
             patch.object(dispatch, '_to_codex', side_effect=self.completed):
            with self.assertRaises(desks.DeskError):
                self.send(writes=['tools/a.py'])
            # A completion label alone cannot release a claim.
            with self.results.open('a') as fh:
                fh.write(json.dumps({'msg_id': 'other', 'status': 'completed'}) + '\n')
            with self.assertRaises(desks.DeskError):
                self.send(writes=['tools/a.py'])
            with patch.object(write_ownership, 'termination_evidence', return_value='verified fixture termination'):
                write_ownership.reconcile('other')
            self.assertEqual(self.send(writes=['tools/a.py'])['status'], 'completed')
            self.assertEqual(self.send(writes=['tools/a.py'])['status'], 'completed')

    def test_cli_repeatable_writes_are_combined_with_brief(self):
        with patch('dispatch.write_ownership.open_prs', return_value=('owner/repo', [])), \
             patch.object(dispatch, '_to_codex', side_effect=self.completed), \
             patch.object(dispatch.seat_gate, 'check', return_value=None), \
             contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(dispatch.main(['--registry', str(self.reg.path), '--results', str(self.results),
                'send', 'sol', 'Writes: src/*.py', '--writes', 'tests/*', '--writes', 'docs/*']), 0)
        row = json.loads(self.results.read_text().splitlines()[-1])
        self.assertEqual(row['writes'], ['tests/*', 'docs/*', 'src/*.py'])

    def test_catalog_or_github_failure_starts_zero_executors(self):
        with patch('dispatch.write_ownership.open_prs', side_effect=desks.DeskError('ownership_unreadable', 'no PR read')), \
             patch.object(dispatch, '_to_codex') as run:
            with self.assertRaises(desks.DeskError):
                self.send(writes=['tools/*'])
            run.assert_not_called()

    def test_other_repo_claim_does_not_block_and_invalid_ledger_refuses(self):
        self.active(repo='different/repo')
        with patch('dispatch.write_ownership.open_prs', return_value=('owner/repo', [])), \
             patch.object(dispatch, '_to_codex', side_effect=self.completed) as run:
            self.send(writes=['tools/a.py'])
            self.ledger.write_text('{broken\n')
            run.reset_mock()
            with self.assertRaises(desks.DeskError):
                self.send(writes=['tools/a.py'])
            run.assert_not_called()

    def test_concurrent_claim_is_visible_to_second_dispatch(self):
        entered = threading.Event()
        release = threading.Event()
        errors = []
        def execute(*args, **kwargs):
            entered.set()
            release.wait(5)
            return self.completed(*args, **kwargs)
        def first():
            try:
                self.send(writes=['new/*.py'])
            except Exception as exc:
                errors.append(exc)
        with patch('dispatch.write_ownership.open_prs', return_value=('owner/repo', [])), \
             patch.object(dispatch, '_to_codex', side_effect=execute) as run:
            worker = threading.Thread(target=first)
            worker.start()
            try:
                self.assertTrue(entered.wait(5))
                with self.assertRaises(desks.DeskError):
                    self.send(writes=['new/*'])
            finally:
                release.set()
                worker.join(5)
            self.assertEqual(run.call_count, 1)
        self.assertFalse(errors)

    def test_glob_intersection_handles_disjoint_and_unicode_classes(self):
        for left, right, expected in [
                ('src/*.py', 'src/*.js', False), ('src/a*', 'src/*z', True),
                ('src/[a-f].py', 'src/[d-z].py', True),
                ('src/[a-c].py', 'src/[d-z].py', False),
                ('src/[!a-z].py', 'src/a.py', False),
                ('src/[α-ω].py', 'src/[λ-ψ].py', True),
                ('src/?.py', 'src/new.py', False)]:
            with self.subTest(left=left, right=right):
                self.assertEqual(write_ownership.overlaps(left, right), expected)

    def test_executor_exception_without_termination_keeps_claim(self):
        with patch('dispatch.write_ownership.open_prs', return_value=('owner/repo', [])), \
             patch.object(dispatch, '_to_codex', side_effect=RuntimeError('failed')):
            with self.assertRaises(RuntimeError):
                self.send(writes=['src/*'])
        with patch('dispatch.write_ownership.open_prs', return_value=('owner/repo', [])), \
             patch.object(dispatch, '_to_codex') as run:
            with self.assertRaisesRegex(desks.DeskError, 'stuck'):
                self.send(writes=['src/*'])
            run.assert_not_called()

    def test_crash_after_reservation_recovers_without_a_timer(self):
        code = '''
import os, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import write_ownership
write_ownership.open_prs = lambda cwd: ('owner/repo', [])
write_ownership.LEDGER = Path(sys.argv[2])
write_ownership.reserve(
    {'msg_id': 'crashed', 'desk': 'sol', 'task': 'build'}, sys.argv[3], ['src/*'])
os._exit(17)
'''
        crashed = subprocess.run([sys.executable, '-c', code,
            str(Path(dispatch.__file__).parent), str(self.ledger), str(self.root)],
            capture_output=True, text=True)
        self.assertEqual(crashed.returncode, 17, crashed.stderr)
        write_ownership.reconcile('crashed')
        with patch('dispatch.write_ownership.open_prs', return_value=('owner/repo', [])), \
             patch.object(dispatch, '_to_codex', side_effect=self.completed):
            self.assertEqual(self.send(writes=['src/*'])['status'], 'completed')
        recovered = [json.loads(line) for line in self.ledger.read_text().splitlines()
                     if json.loads(line).get('msg_id') == 'crashed'][-1]
        self.assertEqual(recovered['ownership_state'], 'released')
        self.assertIn('terminated', recovered['ownership_detail'])

    def test_timeout_without_termination_keeps_claim_and_blocks_second_turn(self):
        self.transport.stop()
        sock = str(self.root / 'live.sock')
        server = FakeAppServer(sock, silent_turn=True)
        self.addCleanup(server.close)
        self.reg.register('live', 'codex-live', socket=sock, family='sol',
                          effort='high', cwd=str(self.root))
        with patch('dispatch.write_ownership.open_prs', return_value=('owner/repo', [])):
            row = dispatch.dispatch('live', 'Writes: src/*\nbuild', registry=self.reg,
                results_path=self.results, codex_timeout_s=0.1)
            self.assertEqual(row['status'], 'timed_out')
            self.assertFalse(row.get('termination_confirmed', True))
            self.assertEqual(row['ownership_state'], 'held')
            self.assertIn('stuck', row['ownership_detail'])
            self.assertIn('turn/interrupt', server.methods())
            with self.assertRaisesRegex(desks.DeskError, 'stuck'):
                dispatch.dispatch('live', 'Writes: src/*\nbuild again', registry=self.reg,
                    results_path=self.results, codex_timeout_s=0.1)
        self.assertEqual(server.methods().count('turn/start'), 1)

    def test_a_live_reservation_is_not_recovered_even_if_old(self):
        with patch('dispatch.write_ownership.open_prs', return_value=('owner/repo', [])):
            write_ownership.reserve({'msg_id': 'alive', 'desk': 'sol',
                'task': 'build', 'dispatched_at': '1999-01-01T00:00:00Z'}, str(self.root), ['src/*'])
            with patch.object(dispatch, '_to_codex') as run:
                with self.assertRaises(desks.DeskError):
                    self.send(writes=['src/*'])
                run.assert_not_called()

    def test_process_group_recovery_waits_for_surviving_child(self):
        proc = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'],
                                start_new_session=True)
        def cleanup():
            if proc.poll() is None:
                proc.kill()
            proc.wait()
        self.addCleanup(cleanup)
        identity = {**write_ownership.process_owner(), 'pid': proc.pid, 'pgid': proc.pid, 'start_time': write_ownership.process_start(proc.pid),
                    'kind': 'process_group'}
        self.active(writes=['src/*'], ownership_state='held', executor=identity)
        with patch('dispatch.write_ownership.open_prs', return_value=('owner/repo', [])), \
             patch.object(dispatch, '_to_codex', side_effect=self.completed) as run:
            with self.assertRaises(desks.DeskError):
                self.send(writes=['src/*'])
            run.assert_not_called()
            cleanup()
            write_ownership.reconcile('other')
            self.assertEqual(self.send(writes=['src/*'])['status'], 'completed')

    def test_async_turn_reconciles_only_its_verified_terminal_status(self):
        sock = str(self.root / 'async.sock')
        server = FakeAppServer(sock, silent_turn=True)
        self.addCleanup(server.close)
        self.active(writes=['src/*'], status='delivered_live', ownership_state='held',
                    executor={'kind': 'codex_turn', 'socket': sock,
                              'thread_id': 'thread-live-0001', 'turn_id': 'turn-1'})
        with patch('dispatch.write_ownership.open_prs', return_value=('owner/repo', [])), \
             patch.object(dispatch, '_to_codex', side_effect=self.completed) as run:
            with self.assertRaises(desks.DeskError):
                self.send(writes=['src/*'])
            run.assert_not_called()
            server.silent_turn = False
            write_ownership.reconcile('other')
            self.assertEqual(self.send(writes=['src/*'])['status'], 'completed')
        self.assertEqual(server.methods().count('thread/read'), 1)

    def test_desktop_delivery_binds_marker_and_reconciles_terminal_history(self):
        self.reg.remember_thread('sol', 'desktop-thread')
        with patch('dispatch.write_ownership.open_prs', return_value=('owner/repo', [])), \
             patch.object(dispatch.codex_ipc, 'thread_owner', return_value='desktop-owner'), \
             patch.object(dispatch.codex_ipc, 'start_turn', return_value={'status': 'delivered'}) as start:
            row = self.send(writes=['src/*'], live_desktop=True)
        self.assertEqual(row['status'], 'delivered_live')
        self.assertEqual(row['ownership_state'], 'held')
        marker = row['executor']['marker']
        self.assertIn(row['msg_id'], marker)
        self.assertIn(marker, start.call_args.args[1])
        with patch('dispatch.write_ownership.open_prs', return_value=('owner/repo', [])), \
             patch('codex_wire.desktop_turn_terminated', return_value=False) as probe, \
             patch.object(dispatch, '_to_codex') as run:
            with self.assertRaises(desks.DeskError):
                self.send(writes=['src/*'])
            run.assert_not_called()
            write_ownership.reconcile(row['msg_id'])
            probe.assert_called_once_with('desktop-thread', marker)
        with patch('dispatch.write_ownership.open_prs', return_value=('owner/repo', [])), \
             patch('codex_wire.desktop_turn_terminated', return_value=True), \
             patch.object(dispatch, '_to_codex', side_effect=self.completed):
            write_ownership.reconcile(row['msg_id'])
            self.assertEqual(self.send(writes=['src/*'])['status'], 'completed')

    def test_pid_probe_denied_or_from_another_machine_keeps_claim(self):
        identity = write_ownership.process_owner()
        self.active(writes=['src/*'], ownership_state='held', owner_process=identity,
                    executor={'kind': 'reservation'})
        with patch('dispatch.write_ownership.open_prs', return_value=('owner/repo', [])), \
             patch('write_ownership.os.kill', side_effect=PermissionError), \
             patch.object(dispatch, '_to_codex') as run:
            with self.assertRaises(desks.DeskError):
                self.send(writes=['src/*'])
            run.assert_not_called()
        identity['machine_id'] = 'f' * 32
        with patch.object(write_ownership.os, 'kill', side_effect=ProcessLookupError) as kill:
            self.assertFalse(write_ownership.process_terminated(identity))
            kill.assert_not_called()

    def test_headless_codex_records_a_dedicated_group_and_releases_after_exit(self):
        self.transport.stop()
        binary = self.root / 'bin'
        binary.mkdir()
        probe = self.root / 'process.json'
        codex = binary / 'codex'
        codex.write_text(f'''#!{sys.executable}
import json, os, sys
from pathlib import Path
Path({str(probe)!r}).write_text(json.dumps({{'pid': os.getpid(), 'pgid': os.getpgrp()}}))
Path(sys.argv[sys.argv.index('-o') + 1]).write_text('built')
print(json.dumps({{'type': 'thread.started', 'thread_id': 'fake-thread'}}))
print(json.dumps({{'type': 'turn.completed'}}))
''')
        codex.chmod(0o755)
        with patch('dispatch.write_ownership.open_prs', return_value=('owner/repo', [])), \
             patch.object(write_ownership.socket, 'gethostname', return_value='renamed-dispatcher'):
            row = self.send(writes=['src/*'], env={**os.environ, 'PATH': str(binary)})
            self.assertEqual(row['executor']['host'], 'renamed-dispatcher')
            self.assertEqual(row['executor']['machine_id'], row['owner_process']['machine_id'])
            self.assertEqual(row['status'], 'completed', row)
            self.assertTrue(row['termination_confirmed'], row)
            self.assertEqual(row['ownership_state'], 'released')
            process = json.loads(probe.read_text())
            self.assertEqual(process['pid'], process['pgid'])
            self.assertEqual(row['executor']['pgid'], process['pgid'])
            self.assertEqual(row['executor']['kind'], 'process_group')
            self.assertEqual(self.send(writes=['src/*'], env={**os.environ, 'PATH': str(binary)})['status'], 'completed')

    def test_different_results_paths_cannot_split_authority(self):
        with patch.object(write_ownership, 'open_prs', return_value=('owner/repo', [])), \
             patch.object(dispatch, '_to_codex', return_value={'status': 'completed',
                                                            'termination_confirmed': True}) as run:
            first = self.send(writes=['src/*'])
            self.assertEqual(first['ownership_state'], 'held')
            with self.assertRaisesRegex(desks.DeskError, 'write set owned'):
                dispatch.dispatch('sol', 'build second', registry=self.reg,
                    results_path=self.root / 'different.jsonl', writes=['src/a.py'])
            self.assertEqual(run.call_count, 1)
        self.assertFalse((self.root / 'different.jsonl').exists())

    def test_remote_owned_work_refuses_without_launch_or_claim(self):
        self.reg.register('remote', 'claude-remote', host='host.test',
                          model='claude-opus-5-5', effort='max')
        with patch.object(write_ownership, 'reserve') as reserve, \
             patch.object(dispatch.claude_remote_wire, 'run_task') as run:
            with self.assertRaises(desks.DeskError) as refused:
                dispatch.dispatch('remote', 'repair', registry=self.reg,
                                  results_path=self.results, writes=['src/*'])
            self.assertEqual(refused.exception.code, 'unsupported_owned_adapter')
            reserve.assert_not_called()
            run.assert_not_called()
        self.assertFalse(self.ledger.exists())

    def test_crashed_unconfirmed_before_launch_reconciles_idempotently(self):
        code = """
import os, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import write_ownership as w
w.LEDGER = Path(sys.argv[2])
w.open_prs = lambda cwd: ('owner/repo', [])
row = w.reserve({'msg_id': 'crashed', 'desk': 'sol', 'task': 'build'}, '.', ['src/*'])
w._persist({**row, 'executor': {'kind': 'unconfirmed'}})
os._exit(17)
"""
        crashed = subprocess.run([sys.executable, '-c', code,
            str(Path(dispatch.__file__).parent), str(self.ledger)], capture_output=True, text=True)
        self.assertEqual(crashed.returncode, 17, crashed.stderr)
        row = write_ownership.reconcile('crashed')[0]
        self.assertEqual(row['ownership_state'], 'released')
        self.assertIn('no executor launch', row['ownership_detail'])
        before = self.ledger.read_bytes()
        self.assertEqual(write_ownership.reconcile('crashed')[0], row)
        self.assertEqual(self.ledger.read_bytes(), before)

    def test_dispatcher_death_after_launch_before_executor_recovers(self):
        code = """
import os, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import write_ownership as w
w.LEDGER = Path(sys.argv[2])
w.open_prs = lambda cwd: ('owner/repo', [])
w.reserve({'msg_id': 'crashed', 'desk': 'sol', 'task': 'build'}, '.', ['src/*'])
w.launch('crashed')
os._exit(17)
"""
        crashed = subprocess.run([sys.executable, '-c', code,
            str(Path(dispatch.__file__).parent), str(self.ledger)], capture_output=True, text=True)
        self.assertEqual(crashed.returncode, 17, crashed.stderr)
        row = write_ownership.reconcile('crashed')[0]
        self.assertTrue(row['launch_marker'])
        self.assertEqual(row['executor'], {'kind': 'unconfirmed',
            'host': row['owner_process']['host'],
            'machine_id': row['owner_process']['machine_id']})
        self.assertTrue(write_ownership.process_terminated(row['owner_process']))
        self.assertEqual(row['ownership_state'], 'released')
        before = self.ledger.read_bytes()
        self.assertEqual(write_ownership.reconcile('crashed')[0], row)
        self.assertEqual(self.ledger.read_bytes(), before)
        with patch.object(write_ownership, 'open_prs', return_value=('owner/repo', [])):
            replacement = write_ownership.reserve(
                {'msg_id': 'replacement', 'desk': 'sol', 'task': 'build'}, '.', ['src/*'])
        self.assertEqual(replacement['ownership_state'], 'held')

    def test_crash_after_launch_marker_with_unknown_executor_keeps_claim(self):
        self.active(writes=['src/*'], owner_process={**write_ownership.process_owner(),
                     'pid': 2147483647, 'start_time': 'dead-dispatcher'})
        row = write_ownership.reconcile('other')[0]
        self.assertEqual(row['ownership_state'], 'held')
        self.assertIn('stuck', row['ownership_detail'])

    def test_dispatcher_death_after_spawn_before_gate_never_runs_executor(self):
        marker = self.root / 'executor-wrote'
        binary = self.root / 'bin'
        binary.mkdir()
        codex = binary / 'codex'
        codex.write_text(f'#!{sys.executable}\nfrom pathlib import Path\nPath({str(marker)!r}).write_text("ran")\n')
        codex.chmod(0o755)
        identity_path = self.root / 'gated-child.json'
        code = """
import json, os, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import write_ownership as w
w.LEDGER = Path(sys.argv[2])
w.open_prs = lambda cwd: ('owner/repo', [])
w.reserve({'msg_id': 'crashed', 'desk': 'sol', 'task': 'build'}, '.', ['src/*'])
original_bind = w.bind_executor
def die_before_binding(msg_id, identity):
    Path(sys.argv[4]).write_text(json.dumps(identity))
    if sys.argv[5] == 'after-bind':
        original_bind(msg_id, identity)
    os._exit(17)
w.bind_executor = die_before_binding
w.launch('crashed', {'entry': {'kind': 'codex-session', 'name': 'sol',
    'model': 'fixture', 'effort': 'high'}, 'task': 'build',
    'env': {**os.environ, 'PATH': sys.argv[3]}})
"""
        for boundary in ('before-bind', 'after-bind'):
            with self.subTest(boundary=boundary):
                ledger = self.root / (boundary + '.jsonl')
                crashed = subprocess.run([sys.executable, '-c', code,
                    str(Path(dispatch.__file__).parent), str(ledger), str(binary),
                    str(identity_path), boundary], capture_output=True, text=True, timeout=10)
                self.assertEqual(crashed.returncode, 17, crashed.stderr)
                self.assertFalse(marker.exists())
                identity = json.loads(identity_path.read_text())
                self.assertTrue(identity['start_time'])
                deadline = time.monotonic() + 5
                while not write_ownership.process_terminated(identity, group=True) and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertTrue(write_ownership.process_terminated(identity, group=True))
                with patch.object(write_ownership, 'LEDGER', ledger):
                    row = write_ownership.reconcile('crashed')[0]
                self.assertEqual(row['ownership_state'], 'released')
                self.assertFalse(marker.exists())

    def test_recorded_executor_alive_keeps_claim(self):
        code = """
import os, subprocess, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import write_ownership as w
w.LEDGER = Path(sys.argv[2])
w.open_prs = lambda cwd: ('owner/repo', [])
w.reserve({'msg_id': 'live', 'desk': 'sol', 'task': 'build'}, '.', ['src/*'])
w.launch('live')
child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'],
    start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
w.bind_executor('live', {**w.process_owner(), 'kind': 'process_group',
    'pid': child.pid, 'pgid': child.pid, 'start_time': w.process_start(child.pid)})
os._exit(17)
"""
        crashed = subprocess.run([sys.executable, '-c', code,
            str(Path(dispatch.__file__).parent), str(self.ledger)], capture_output=True, text=True)
        self.assertEqual(crashed.returncode, 17, crashed.stderr)
        row = write_ownership.reconcile('live')[0]
        self.addCleanup(os.kill, row['executor']['pid'], 9)
        self.assertTrue(write_ownership.process_terminated(row['owner_process']))
        self.assertFalse(write_ownership.process_terminated(row['executor'], group=True))
        self.assertEqual(row['ownership_state'], 'held')

    def nightly_reconcile(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = dispatch.main(['reconcile', '--older-than-hours', '6'])
        return code, json.loads(out.getvalue())['claims']

    def test_nightly_sweep_leaves_a_young_live_claim_alone(self):
        with patch.object(write_ownership, 'open_prs', return_value=('owner/repo', [])):
            write_ownership.reserve({'msg_id': 'live', 'desk': 'sol', 'task': 'build'}, '.', ['src/*'])
        write_ownership.launch('live')
        before = self.ledger.read_bytes()
        self.assertEqual(self.nightly_reconcile(), (0, []))
        self.assertEqual(self.ledger.read_bytes(), before)
        self.assertEqual(write_ownership._claim('live')['status'], 'running')
        # The unfiltered sweep is the behaviour the filter exists to avoid.
        self.assertEqual(write_ownership.reconcile()[0]['status'], 'stuck')

    def test_nightly_sweep_releases_a_dead_writer_claim(self):
        code = """
import os, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import write_ownership as w
w.LEDGER = Path(sys.argv[2])
w.open_prs = lambda cwd: ('owner/repo', [])
row = w.reserve({'msg_id': 'crashed', 'desk': 'sol', 'task': 'build'}, '.', ['src/*'])
w._persist({**row, 'executor': {'kind': 'unconfirmed'}})
os._exit(17)
"""
        crashed = subprocess.run([sys.executable, '-c', code,
            str(Path(dispatch.__file__).parent), str(self.ledger)], capture_output=True, text=True)
        self.assertEqual(crashed.returncode, 17, crashed.stderr)
        code, claims = self.nightly_reconcile()
        self.assertEqual(code, 0)
        self.assertEqual([(c['msg_id'], c['ownership_state']) for c in claims], [('crashed', 'released')])

    def test_nightly_sweep_examines_an_over_age_claim_with_a_live_writer(self):
        with patch.object(write_ownership, 'open_prs', return_value=('owner/repo', [])):
            row = write_ownership.reserve({'msg_id': 'old', 'desk': 'sol', 'task': 'build'}, '.', ['src/*'])
        write_ownership.launch('old')
        aged = write_ownership._claim('old')
        old = (datetime.now(timezone.utc) - timedelta(hours=7)).isoformat()
        write_ownership._persist({**aged, 'claimed_at': old, 'launch_marker': old})
        self.assertEqual(row['ownership_state'], 'held')
        code, claims = self.nightly_reconcile()
        self.assertEqual(code, 1)
        self.assertEqual([(c['msg_id'], c['status']) for c in claims], [('old', 'stuck')])

    def test_older_than_hours_is_a_sweep_only_positive_bound(self):
        for kwargs in ({'older_than_hours': 0}, {'older_than_hours': -1}):
            with self.assertRaises(desks.DeskError):
                write_ownership.reconcile(**kwargs)
        with self.assertRaises(desks.DeskError):
            write_ownership.reconcile('x', older_than_hours=6)

    def test_launch_marker_with_live_dispatcher_keeps_claim(self):
        with patch.object(write_ownership, 'open_prs', return_value=('owner/repo', [])):
            write_ownership.reserve({'msg_id': 'live', 'desk': 'sol', 'task': 'build'}, '.', ['src/*'])
        write_ownership.launch('live')
        self.assertEqual(write_ownership.reconcile('live')[0]['ownership_state'], 'held')

    def test_non_codex_delivery_keeps_claim_after_dispatcher_death(self):
        sock = str(self.root / 'claude.sock')
        listener = Listener(sock)
        self.addCleanup(listener.close)
        code = """
import os, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import write_ownership as w
w.LEDGER = Path(sys.argv[2])
w.open_prs = lambda cwd: ('owner/repo', [])
w.reserve({'msg_id': 'remote', 'desk': 'claude', 'task': 'repair'}, '.', ['src/*'])
result = w.launch('remote', {'entry': {'kind': 'claude-session',
    'name': 'claude', 'socket': sys.argv[3]}, 'task': 'repair', 'msg_id': 'remote'})
assert result['status'] == 'delivered', result
os._exit(17)
"""
        crashed = subprocess.run([sys.executable, '-c', code,
            str(Path(dispatch.__file__).parent), str(self.ledger), sock],
            capture_output=True, text=True, timeout=10)
        self.assertEqual(crashed.returncode, 17, crashed.stderr)
        self.assertTrue(any('repair' in line for line in listener.lines))
        row = write_ownership.reconcile('remote')[0]
        self.assertTrue(write_ownership.process_terminated(row['owner_process']))
        self.assertTrue(write_ownership.process_terminated(row['executor'], group=True))
        self.assertEqual(row['executor']['kind'], 'unconfirmed')
        self.assertEqual(row['ownership_state'], 'held')
        with patch.object(write_ownership, 'open_prs', return_value=('owner/repo', [])):
            with self.assertRaisesRegex(desks.DeskError, 'write set owned'):
                write_ownership.reserve({'msg_id': 'overlap', 'desk': 'sol', 'task': 'build'},
                                        '.', ['src/a.py'])

    def test_pid_reuse_is_checked_but_live_process_group_still_blocks_release(self):
        identity = {**write_ownership.process_owner(), 'start_time': 'old-start', 'pgid': 987}
        with patch.object(write_ownership, 'process_start', return_value='new-start'), \
             patch.object(write_ownership.os, 'kill', return_value=None), \
             patch.object(write_ownership.os, 'killpg', return_value=None):
            self.assertTrue(write_ownership.process_terminated(identity))
            self.assertFalse(write_ownership.process_terminated(identity, group=True))
        with patch.object(write_ownership, 'process_start', return_value='new-start'), \
             patch.object(write_ownership.os, 'kill', return_value=None), \
             patch.object(write_ownership.os, 'killpg', side_effect=ProcessLookupError):
            self.assertTrue(write_ownership.process_terminated(identity, group=True))
        identity['start_time'] = None
        with patch.object(write_ownership.os, 'kill', side_effect=ProcessLookupError):
            self.assertFalse(write_ownership.process_terminated(identity, group=True))

    def test_reconcile_cli_is_bounded_and_logs_held_claim(self):
        self.active()
        with contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(dispatch.main(['reconcile', '--claim', 'other']), 1)
        self.assertIn(str(self.ledger), output.getvalue())
        row = json.loads(self.ledger.read_text().splitlines()[-1])
        self.assertEqual(row['reconcile_reason'], 'explicit reconcile')
        for limit in (0, 101):
            with self.assertRaises(desks.DeskError):
                write_ownership.reconcile(limit=limit)

    def test_results_cannot_be_the_authority_or_its_lock(self):
        for path in (self.ledger, Path(str(self.ledger) + '.lock')):
            with self.assertRaisesRegex(desks.DeskError, 'results cannot'):
                dispatch.dispatch('sol', 'build', registry=self.reg,
                                  results_path=path, writes=['src/*'])
        self.assertFalse(self.ledger.exists())
        alias = write_ownership._alias_path()
        for path in (alias, Path(str(alias) + '.lock')):
            with self.assertRaisesRegex(desks.DeskError, 'results cannot'):
                write_ownership.record(path, {'host': 'forged', 'machine_id': '1' * 32})
        self.assertFalse(alias.exists())
        self.assertFalse(Path(str(alias) + '.lock').exists())

    def test_alias_results_path_rejection_leaves_no_held_reservation(self):
        alias = write_ownership._alias_path()
        for path in (alias, Path(str(alias) + '.lock')):
            with patch.object(write_ownership, 'open_prs', return_value=('owner/repo', [])), \
                 patch.object(dispatch, '_to_codex') as run:
                with self.assertRaisesRegex(desks.DeskError, 'results cannot'):
                    dispatch.dispatch('sol', 'build', registry=self.reg,
                                      results_path=path, writes=['src/*'])
                run.assert_not_called()
            latest = {}
            if self.ledger.exists():
                for line in self.ledger.read_text().splitlines():
                    row = json.loads(line)
                    latest[row['msg_id']] = row['ownership_state']
            self.assertNotIn('held', latest.values())
        # The same write set is claimable afterwards: nothing was left held.
        with patch.object(write_ownership, 'open_prs', return_value=('owner/repo', [])), \
             patch.object(dispatch, '_to_codex', return_value={'status': 'completed',
                                                            'termination_confirmed': True}):
            self.assertEqual(self.send(writes=['src/*'])['status'], 'completed')

    def test_home_environment_cannot_redirect_authority(self):
        code = "import sys; sys.path.insert(0, sys.argv[1]); import write_ownership; print(write_ownership.LEDGER)"
        proc = subprocess.run([sys.executable, '-c', code, str(Path(dispatch.__file__).parent)],
                              capture_output=True, text=True, env={**os.environ, 'HOME': str(self.root)})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        import pwd
        expected = Path(pwd.getpwuid(os.getuid()).pw_dir) / '.config/carr/hermes-write-ownership.jsonl'
        self.assertEqual(proc.stdout.strip(), str(expected))

    def test_denied_group_probe_keeps_recorded_dead_executor_held(self):
        identity = {**write_ownership.process_owner(), 'kind': 'process_group',
                    'pid': 2147483647, 'pgid': 2147483647, 'start_time': 'dead'}
        self.active(executor=identity)
        with patch.object(write_ownership.os, 'killpg', side_effect=PermissionError):
            row = write_ownership.reconcile('other')[0]
        self.assertEqual(row['ownership_state'], 'held')
        self.assertIn('stuck', row['ownership_detail'])

    def test_inconsistent_missing_marker_never_releases_a_bound_writer(self):
        self.active(launch_marker=None, owner_process={**write_ownership.process_owner(),
            'pid': 2147483647, 'start_time': 'dead'}, executor={**write_ownership.process_owner(),
            'kind': 'process_group', 'pgid': os.getpgrp()})
        self.assertEqual(write_ownership.reconcile('other')[0]['ownership_state'], 'held')

    def test_released_claim_cannot_be_rebound_or_launched(self):
        with patch.object(write_ownership, 'open_prs', return_value=('owner/repo', [])), \
             patch.object(dispatch, '_to_codex', side_effect=self.completed):
            row = self.send(writes=['src/*'])
        self.assertEqual(row['ownership_state'], 'released')
        with self.assertRaises(desks.DeskError):
            write_ownership.launch(row['msg_id'])
        with self.assertRaises(desks.DeskError):
            write_ownership.bind_executor(row['msg_id'], row['executor'])

    def test_only_release_can_write_released_state_and_no_path_deletes_claims(self):
        # Scan production source, including every ledger persistence call.
        here = Path(dispatch.__file__).parent
        writers = []
        released_writes = []
        adapter_calls = []
        execute_calls = []
        gate_calls = []
        for path in here.glob('*.py'):
            if path.name.startswith('test_'):
                continue
            tree = ast.parse(path.read_text())
            parents = {}
            for parent in ast.walk(tree):
                for child in ast.iter_child_nodes(parent):
                    parents[child] = parent
            def owner(node):
                while node in parents:
                    node = parents[node]
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        return node.name
                return '<module>'
            for node in ast.walk(tree):
                if isinstance(node, ast.Dict):
                    for key, value in zip(node.keys, node.values):
                        if (isinstance(key, ast.Constant) and key.value == 'ownership_state'
                                and isinstance(value, ast.Constant) and value.value == 'released'):
                            released_writes.append((path.name, owner(node)))
                if isinstance(node, ast.keyword) and node.arg == 'ownership_state':
                    if isinstance(node.value, ast.Constant) and node.value.value == 'released':
                        released_writes.append((path.name, owner(node)))
                if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant) and node.value.value == 'released':
                    released_writes.append((path.name, owner(node)))
                if isinstance(node, ast.Call):
                    name = ast.unparse(node.func)
                    if path.name == 'dispatch.py' and name in (
                            '_to_codex', '_to_claude', '_to_claude_desktop',
                            'codex_wire.run_turn', 'claude_remote_wire.run_task',
                            'grok_wire.run_task', 'flash_wire.run_task'):
                        adapter_calls.append(owner(node))
                    if name in ('_execute', 'dispatch._execute'):
                        execute_calls.append((path.name, owner(node)))
                        if path.name == 'dispatch.py':
                            branch = parents[node]
                            while branch in parents and not isinstance(branch, ast.If):
                                branch = parents[branch]
                            self.assertEqual(ast.unparse(branch.test), 'ownership')
                            self.assertIn(parents[node], branch.orelse)
                            self.assertIn('write_ownership.launch', ast.unparse(branch.body[0]))
                    if name == '_run_gated':
                        gate_calls.append((path.name, owner(node)))
                    if name == '_persist':
                        writers.append((path.name, owner(node)))
                    if name == '_append' and node.args and ast.unparse(node.args[0]) == 'LEDGER':
                        self.assertEqual((path.name, owner(node)), ('write_ownership.py', '_persist'))
                if isinstance(node, ast.Delete):
                    self.assertNotIn('claim', ast.unparse(node).lower())
        self.assertEqual(released_writes, [('write_ownership.py', 'release')])
        self.assertTrue(adapter_calls)
        self.assertEqual(set(adapter_calls), {'_execute'})
        self.assertEqual(set(execute_calls), {('dispatch.py', 'dispatch'),
                                            ('ownership_executor.py', 'main')})
        self.assertEqual(gate_calls, [('write_ownership.py', 'launch')])
        worker = ast.parse((here / 'ownership_executor.py').read_text())
        main = next(node for node in worker.body if isinstance(node, ast.FunctionDef) and node.name == 'main')
        gated = next(node for node in main.body if isinstance(node, ast.If)
                     and ast.unparse(node.test) == 'dispatching')
        statements = [ast.unparse(node) for node in gated.body]
        initial = statements.index('bind(identity)')
        uncertain = statements.index("bind({**identity, 'kind': 'unconfirmed'})")
        invoke = next(i for i, statement in enumerate(statements) if 'dispatch._execute' in statement)
        self.assertLess(initial, uncertain)
        self.assertLess(uncertain, invoke)
        binding = next(node for node in gated.body if isinstance(node, ast.FunctionDef) and node.name == 'bind')
        permission = next(node for node in binding.body if isinstance(node, ast.If))
        self.assertEqual(ast.unparse(permission.test), "os.read(gate, 1) != b'1'")
        self.assertEqual(ast.unparse(permission.body[0]), 'raise SystemExit(125)')
        self.assertEqual(set(writers), {('write_ownership.py', name) for name in
                                       ('reserve', 'launch', 'bind_executor', 'release')})
        source = Path(write_ownership.__file__).read_text()
        for forbidden in ('.unlink(', '.remove(', 'claims.pop(', 'row.pop(', '.write_text(', '.write_bytes('):
            self.assertNotIn(forbidden, source)

    def test_every_adapter_refuses_launch_when_identity_cannot_be_recorded(self):
        self.transport.stop()
        for kind in desks.KINDS:
            with self.subTest(kind=kind):
                with patch.object(write_ownership, 'open_prs', return_value=('owner/repo', [])):
                    write_ownership.reserve({'msg_id': kind, 'desk': kind, 'task': 'build'},
                                            '.', [kind + '/*'])
                with patch.object(write_ownership, 'bind_executor', side_effect=RuntimeError('cannot record identity')) as bind:
                    with self.assertRaisesRegex(RuntimeError, 'cannot record identity'):
                        write_ownership.launch(kind, {'entry': {'kind': kind}, 'task': 'build'})
                    identity = bind.call_args.args[1]
                self.assertTrue(write_ownership.process_terminated(identity, group=True))
                self.assertEqual(write_ownership.reconcile(kind)[0]['ownership_state'], 'held')

    def test_executor_cannot_write_before_durable_binding(self):
        marker = self.root / 'executor-wrote'
        code = f'from pathlib import Path; Path({str(marker)!r}).write_text("started")'
        def reject(identity):
            self.assertTrue(identity['start_time'])
            self.assertEqual(identity['pid'], identity['pgid'])
            self.assertFalse(marker.exists())
            raise RuntimeError('durable identity recording failed')
        with self.assertRaisesRegex(RuntimeError, 'durable identity'):
            dispatch._run_codex_process([sys.executable, '-c', code], None, 2, reject, False)
        self.assertFalse(marker.exists())

    def test_pr_reader_pages_files_and_preserves_renamed_path(self):
        reader = unittest.mock.Mock()
        reader.json.return_value = {'nameWithOwner': 'owner/repo'}
        reader.api.side_effect = [[{'number': 42, 'title': 'owner'}],
                                  [{'filename': 'src/new.py', 'previous_filename': 'src/old.py'}]]
        with patch.object(write_ownership, 'GitHubReader', return_value=reader):
            repo, prs = write_ownership.open_prs(str(self.root))
        self.assertEqual(repo, 'owner/repo')
        self.assertEqual(prs[0]['files'], ['src/new.py', 'src/old.py'])
        self.assertTrue(all(call.kwargs['paginate'] for call in reader.api.call_args_list))


if __name__ == '__main__':
    unittest.main(verbosity=2)
