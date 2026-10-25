#!/usr/bin/env python3
"""Exercise Studio recovery with invented state and an opaque age transport."""
from __future__ import annotations

import contextlib
import importlib.util
import io
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import types
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('studio_state', ROOT / 'tools/studio-state.py')
assert spec is not None
state = importlib.util.module_from_spec(spec)
assert spec.loader
spec.loader.exec_module(state)


class StudioState(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.repo = self.root / 'repo'
        self.home = self.root / 'home'
        self.repo.mkdir()
        self.home.mkdir()
        self.copies = {}

    def put(self, name, content):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return path

    def age(self, argv, *, input=None, **kwargs):
        self.assertEqual(kwargs['env'], {'PATH': '/usr/bin:/bin', 'LANG': 'C'})
        if argv[0] == 'git':
            return subprocess.CompletedProcess(argv, 0, stdout=b'a' * 40, stderr=b'')
        if '--encrypt' in argv:
            opaque = b'age-encryption.org/v1\nsynthetic-envelope'
            self.copies[opaque] = input
            output = opaque
        else:
            output = self.copies[input]
        return subprocess.CompletedProcess(argv, 0, stdout=output, stderr=b'')

    def call(self, *args):
        output = io.StringIO()
        with mock.patch.object(state, 'age_binary', return_value='/synthetic/age'), \
                mock.patch.object(state.subprocess, 'run', side_effect=self.age), \
                contextlib.redirect_stdout(output):
            code = state.main(list(map(str, args)))
        return code, json.loads(output.getvalue())

    def backup(self, *args):
        return self.call('backup', '--repo', self.repo, '--home', self.home,
                         '--recipient', 'synthetic-public-recipient', '--writers-fenced',
                         '--output', self.root / 'state.tar.age', *args)

    def test_round_trip_preserves_old_pending_mail_and_send_history(self):
        pending = b'{"requests":{"old":{"created_at":1,"acknowledged":false}}}'
        ledger = b'{"key":"invented-send","state":"sent"}\n'
        self.put('repo/out/outlook-intake/state.json', pending)
        self.put('repo/out/outlook-intake/needs-refresh.json', b'[{"id":"old"}]')
        self.put('home/.local/state/carr/outlook-send/ledger.jsonl', ledger)
        self.put('repo/out/calendar-archive/calendar-joe-20000101.ics', b'BEGIN:VCALENDAR\nEND:VCALENDAR\n')
        self.put('repo/out/boards/synthetic.json', b'{"cards":[]}')
        code, receipt = self.backup()
        self.assertEqual(code, 0)
        self.assertEqual(receipt['archive']['state'], 'not_requested')
        destination = self.root / 'recovered'
        code, restored = self.call('restore', '--archive', self.root / 'state.tar.age',
                                  '--identity', '/synthetic/nonexistent-identity', '--staging', destination)
        self.assertEqual(code, 0)
        self.assertEqual((destination / 'repo/out/outlook-intake/state.json').read_bytes(), pending)
        self.assertEqual((destination / 'home/.local/state/carr/outlook-send/ledger.jsonl').read_bytes(), ledger)
        self.assertTrue(restored['send_reconciliation_required'])
        self.assertFalse((self.home / '.config').exists())

    def test_manifest_reports_missing_inputs_and_excludes_unselected_siblings(self):
        self.put('repo/out/outlook-intake/state.json', b'{"requests":{}}')
        self.put('repo/out/outlook-intake/unselected.txt', b'unselected synthetic data')
        self.put('repo/out/boards/.hidden.json', b'{}')
        self.put('repo/exporters/templates/unselected.json', b'{}')
        code, receipt = self.backup()
        self.assertEqual(code, 0)
        with tarfile.open(fileobj=io.BytesIO(next(iter(self.copies.values())))) as archive:
            self.assertEqual(set(archive.getnames()), {'manifest.json', 'repo/out/outlook-intake/state.json'})
            manifest = json.load(archive.extractfile('manifest.json'))
        self.assertIn('repo/exporters/dossier-roster.local.json', manifest['missing'])
        self.assertIn('exporters/templates', manifest['excluded'])

    def test_board_projects_with_dots_are_packaged_and_omissions_are_reported(self):
        files = {'repo/out/boards/acme.v2.json': b'{"cards":[]}',
                 'repo/out/boards/acme.v2-answers.jsonl': b'{"answer":"synthetic"}\n'}
        for name, content in files.items():
            self.put(name, content)
        omitted = ['repo/out/boards/.hidden.json', 'repo/out/boards/notes.txt',
                   'repo/out/boards/nested', 'repo/out/calendar-archive/unselected.txt']
        for name in omitted:
            self.put(name + '/child.txt' if name.endswith('/nested') else name, b'unselected')
        data, manifest = state.pack(self.repo, self.home)
        self.assertEqual(set(manifest['files']), set(files))
        self.assertTrue(set(omitted) <= set(manifest['missing']))
        members, restored_manifest = state.unpack(data)
        self.assertEqual(members, files)
        self.assertEqual(restored_manifest['missing'], manifest['missing'])

    def test_existing_output_and_staging_are_never_overwritten(self):
        self.put('repo/out/outlook-intake/state.json', b'{}')
        self.assertEqual(self.backup()[0], 0)
        before = (self.root / 'state.tar.age').read_bytes()
        self.assertEqual(self.backup()[0], 1)
        self.assertEqual((self.root / 'state.tar.age').read_bytes(), before)
        destination = self.root / 'recovered'
        destination.mkdir()
        marker = self.put('recovered/keep.txt', b'untouched')
        code, _ = self.call('restore', '--archive', self.root / 'state.tar.age',
                            '--identity', '/synthetic/nonexistent-identity', '--staging', destination)
        self.assertEqual(code, 1)
        self.assertEqual(marker.read_bytes(), b'untouched')

    def test_links_and_special_files_in_selected_state_are_refused(self):
        source = self.put('repo/out/outlook-intake/state.json', b'{}')
        for kind in ('symlink', 'hardlink', 'fifo'):
            with self.subTest(kind=kind):
                selected = self.repo / 'out/outlook-intake/needs-refresh.json'
                if kind == 'symlink':
                    selected.symlink_to(source)
                elif kind == 'hardlink':
                    selected.hardlink_to(source)
                else:
                    import os
                    os.mkfifo(selected)
                self.assertEqual(self.backup()[0], 1)
                self.assertFalse((self.root / 'state.tar.age').exists())
                selected.unlink()

    def test_symlinked_roots_and_parent_directories_are_refused(self):
        self.put('repo/out/outlook-intake/state.json', b'{}')
        alias = self.root / 'alias'
        alias.symlink_to(self.repo, target_is_directory=True)
        code, _ = self.call('backup', '--repo', alias, '--home', self.home,
                            '--recipient', 'synthetic-public-recipient', '--writers-fenced',
                            '--output', self.root / 'state.tar.age')
        self.assertEqual(code, 1)
        intake = self.repo / 'out/outlook-intake'
        intake.rename(self.root / 'intake')
        intake.symlink_to(self.root / 'intake', target_is_directory=True)
        self.assertEqual(self.backup()[0], 1)

    def bad_archive(self, member_name=None, transform=None, member_type=tarfile.REGTYPE):
        self.put('repo/out/outlook-intake/state.json', b'{}')
        self.assertEqual(self.backup()[0], 0)
        opaque = (self.root / 'state.tar.age').read_bytes()
        source = self.copies[opaque]
        result = io.BytesIO()
        with tarfile.open(fileobj=io.BytesIO(source)) as old, tarfile.open(fileobj=result, mode='w') as new:
            for member in old:
                data = old.extractfile(member).read()
                if transform:
                    data = transform(member.name, data)
                member.size = len(data)
                new.addfile(member, io.BytesIO(data))
            if member_name:
                member = tarfile.TarInfo(member_name)
                member.type = member_type
                member.linkname = '../../escaped'
                new.addfile(member, io.BytesIO())
        self.copies[opaque] = result.getvalue()
        destination = self.root / 'recovered'
        code, _ = self.call('restore', '--archive', self.root / 'state.tar.age',
                            '--identity', '/synthetic/nonexistent-identity', '--staging', destination)
        self.assertEqual(code, 1)
        self.assertFalse(destination.exists())
        self.assertEqual(list(self.root.glob('.studio-state-*')), [])

    def test_modified_content_is_refused_before_staging(self):
        self.bad_archive(transform=lambda name, data: b'{"tampered":true}' if name.endswith('state.json') else data)

    def test_non_object_manifest_is_refused_before_staging(self):
        self.bad_archive(transform=lambda name, data: b'[]' if name == 'manifest.json' else data)

    def test_traversal_absolute_unknown_duplicate_and_link_members_are_refused(self):
        for name, kind in (('../escaped', tarfile.REGTYPE), ('/escaped', tarfile.REGTYPE),
                           ('repo/out/unselected.txt', tarfile.REGTYPE),
                           ('repo/out/outlook-intake/state.json', tarfile.REGTYPE),
                           ('repo/out/boards/link.json', tarfile.SYMTYPE),
                           ('repo/out/boards/link.json', tarfile.LNKTYPE)):
            with self.subTest(name=name, kind=kind):
                self.bad_archive(member_name=name, member_type=kind)
                (self.root / 'state.tar.age').unlink()

    def test_failed_age_process_publishes_no_archive_or_staging(self):
        self.put('repo/out/outlook-intake/state.json', b'{}')
        with mock.patch.object(self, 'age', return_value=subprocess.CompletedProcess([], 1, b'partial', b'private output')):
            self.assertEqual(self.backup()[0], 1)
        self.assertFalse((self.root / 'state.tar.age').exists())
        self.assertEqual(self.backup()[0], 0)
        with mock.patch.object(self, 'age', return_value=subprocess.CompletedProcess([], 1, b'partial', b'private output')):
            code, receipt = self.call('restore', '--archive', self.root / 'state.tar.age',
                                      '--identity', '/synthetic/nonexistent-identity', '--staging', self.root / 'recovered')
        self.assertEqual(code, 1)
        self.assertNotIn('private', json.dumps(receipt))
        self.assertFalse((self.root / 'recovered').exists())

    def test_manifest_cannot_hide_missing_state_or_claim_present_state_is_missing(self):
        def without_missing(name, data):
            if name == 'manifest.json':
                manifest = json.loads(data)
                manifest['missing'] = []
                return json.dumps(manifest).encode()
            return data
        self.bad_archive(transform=without_missing)
        (self.root / 'state.tar.age').unlink()

        def conflicting_missing(name, data):
            if name == 'manifest.json':
                manifest = json.loads(data)
                manifest['missing'].append('repo/out/outlook-intake/state.json')
                return json.dumps(manifest).encode()
            return data
        self.bad_archive(transform=conflicting_missing)

    def test_capture_refuses_a_writer_changing_state_before_publication(self):
        selected = self.put('repo/out/outlook-intake/state.json', b'{}')
        age = self.age
        def mutate(argv, **kwargs):
            if argv[0] == 'git':
                selected.write_bytes(b'{"changed":true}')
            return age(argv, **kwargs)
        with mock.patch.object(self, 'age', side_effect=mutate):
            self.assertEqual(self.backup()[0], 1)
        self.assertFalse((self.root / 'state.tar.age').exists())

    def test_r2_requires_digest_readback_and_keeps_failed_archives_retryable(self):
        self.put('repo/out/outlook-intake/state.json', b'{}')
        self.assertEqual(self.backup()[0], 0)
        ciphertext = (self.root / 'state.tar.age').read_bytes()
        class QuotaExceeded(Exception):
            pass
        uploader = mock.Mock(return_value={'uploaded': True})
        r2 = types.SimpleNamespace(load_ledger=lambda: {'objects': {}}, reconcile=lambda ledger: None,
                                   upload=uploader, QuotaExceeded=QuotaExceeded, BUCKET='synthetic-state',
                                   DEFAULT_QUOTA_GB=8, GB=1024 ** 3,
                                   wrangler_bin=lambda: '/synthetic/wrangler',
                                   _wrangler_env=lambda: {'PATH': '/usr/bin:/bin', 'LANG': 'C'})
        age = self.age
        readback = ciphertext
        def transport(argv, **kwargs):
            if argv[0] == '/synthetic/wrangler':
                return subprocess.CompletedProcess(argv, 0, stdout=readback, stderr=b'')
            return age(argv, **kwargs)
        with mock.patch.dict(sys.modules, r2_archive=r2), mock.patch.object(self, 'age', side_effect=transport):
            code, receipt = self.call('archive', '--archive', self.root / 'state.tar.age', '--r2-quota-bytes', '1024')
            self.assertEqual(code, 0)
            self.assertEqual(receipt['archive']['state'], 'archived')
            self.assertEqual(receipt['archive']['sha256'], hashlib.sha256(ciphertext).hexdigest())
            readback = b'different bytes'
            code, receipt = self.call('archive', '--archive', self.root / 'state.tar.age', '--r2-quota-bytes', '1024')
            self.assertEqual(code, 1)
            self.assertEqual(receipt['archive']['state'], 'owed')
            self.assertEqual(receipt['archive']['reason'], 'readback_failed')
            uploader.side_effect = QuotaExceeded()
            code, receipt = self.call('archive', '--archive', self.root / 'state.tar.age', '--r2-quota-bytes', '1024')
            self.assertEqual(code, 1)
            self.assertEqual(receipt['archive']['reason'], 'quota_refused')
            uploader.side_effect = RuntimeError('synthetic upload failure')
            code, receipt = self.call('archive', '--archive', self.root / 'state.tar.age', '--r2-quota-bytes', '1024')
            self.assertEqual(code, 1)
            self.assertEqual(receipt['archive']['reason'], 'upload_failed')
        self.assertEqual((self.root / 'state.tar.age').read_bytes(), ciphertext)

    def test_backup_archive_refusal_retains_complete_local_ciphertext(self):
        self.put('repo/out/outlook-intake/state.json', b'{}')
        with mock.patch.object(state, 'archive_r2', return_value={'state': 'owed', 'reason': 'quota_refused'}):
            code, receipt = self.backup('--archive-r2')
        self.assertEqual(code, 1)
        self.assertEqual(receipt['state'], 'encrypted')
        self.assertEqual(receipt['archive']['state'], 'owed')
        self.assertEqual((self.root / 'state.tar.age').read_bytes(), b'age-encryption.org/v1\nsynthetic-envelope')

    def test_archive_requires_an_explicit_reviewed_quota(self):
        self.put('repo/out/outlook-intake/state.json', b'{}')
        with mock.patch.object(state, 'archive_r2', wraps=state.archive_r2), \
                mock.patch.dict(sys.modules, r2_archive=types.SimpleNamespace()):
            code, receipt = self.backup('--archive-r2')
        self.assertEqual(code, 1)
        self.assertEqual(receipt['archive']['reason'], 'quota_unconfirmed')

    def test_ciphertext_inputs_refuse_special_files_before_invoking_age(self):
        import os
        source = self.root / 'fifo'
        os.mkfifo(source)
        code, _ = self.call('restore', '--archive', source, '--identity', '/synthetic/nonexistent-identity',
                            '--staging', self.root / 'recovered')
        self.assertEqual(code, 1)
        self.assertFalse((self.root / 'recovered').exists())


if __name__ == '__main__':
    unittest.main()
