#!/usr/bin/env python3
"""Synthetic decoded-input regression tests for the copy producer."""
from contextlib import closing
import json
import hashlib
from unittest.mock import patch
from pathlib import Path
import sqlite3
import sys
import tempfile
from types import SimpleNamespace
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib.outlook_triage_producer import project, produce, ProducerError
from lib.outlook_triage_snapshot import read_snapshot
from lib.outlook_triage_plan import plan_triage


class ProducerTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.db = self.root / 'store.db'
        with closing(sqlite3.connect(self.db)) as con, con:
            con.executescript('''
                CREATE TABLE folders(id INTEGER,account_id INTEGER,name TEXT,parent_status TEXT,parent_id INTEGER);
                CREATE TABLE messages(id INTEGER,sender TEXT,subject TEXT,body TEXT,recipients TEXT,
                                      local_keys TEXT,folder_links_unresolved INTEGER);
                CREATE TABLE message_folders(message_id INTEGER,folder_id INTEGER);
                INSERT INTO folders VALUES(1,7,'Inbox','top',NULL),(2,7,'Sent Items','top',NULL),
                                          (3,8,'Inbox','top',NULL);
                INSERT INTO messages VALUES(1,'worker@example.test','private subject','private body',
                                            'reader@example.test','123',0),
                                           (2,'owner@example.test','sent','text','a@example.test','124',0),
                                           (3,'outsider@example.test','other','text','a@example.test','125',0);
                INSERT INTO message_folders VALUES(1,1),(2,2),(3,3);
            ''')
        self.settings = SimpleNamespace(account='owner@example.test',folder_account_id=7,
                                        deal_prefix='Deals/',vendor_prefix='Vendors/')

    def test_actual_decoded_shape_roundtrips_with_honest_holds(self):
        snapshot, counts = project(self.db, self.settings, 'Deals/Test', 'a'*64, 'b'*64)
        path = self.root / 'snapshot.json'
        path.write_text(json.dumps(snapshot)); path.chmod(0o600)
        observations, routing, catalog, policy, effect = read_snapshot(path, self.settings, 150)
        plan = plan_triage(observations, routing, catalog, policy)
        self.assertEqual(len(observations), 2)
        self.assertEqual(len(plan.moves), 0)
        self.assertEqual({x.reason for x in plan.leaves}, {'identity_unverified','not_inbox'})
        self.assertEqual(counts['outside_account'], 1)
        self.assertTrue(all(x.revision == '' and x.read_state.value == 'unknown' for x in observations))
        self.assertFalse(routing.complete)
        self.assertEqual(effect, 'none')
        self.assertNotIn('private', json.dumps(counts))

    def test_account_binding_is_required(self):
        self.settings.account = 'unbound@example.test'
        with self.assertRaisesRegex(ProducerError, 'account_binding_unverified'):
            project(self.db, self.settings, 'Deals/Test', 'a'*64, 'b'*64)

    def test_message_cap_refuses(self):
        with self.assertRaisesRegex(ProducerError, 'message_cap'):
            project(self.db, self.settings, 'Deals/Test', 'a'*64, 'b'*64, max_messages=1)

    def test_ambiguous_ownership_is_omitted(self):
        with closing(sqlite3.connect(self.db)) as con, con:
            con.execute('INSERT INTO message_folders VALUES(1,3)')
        value, counts = project(self.db, self.settings, 'Deals/Test', 'a'*64, 'b'*64)
        self.assertEqual(counts['ownership_unverified'], 1)
        self.assertEqual(len(value['observations']), 1)

    def prepare_copy(self):
        source = self.root / 'copy'
        source.mkdir(mode=0o700)
        raw = self.db.read_bytes()
        (source / 'HxStore.hxd').write_bytes(raw)
        (source / 'manifest.json').write_text(json.dumps(dict(
            schema='outlook-research-snapshot/v1', files=[dict(path='HxStore.hxd',
            size=len(raw),sha256=hashlib.sha256(raw).hexdigest())])))
        vendor = self.root / 'vendor'
        vendor.mkdir()
        decoder = vendor / 'decoder'
        decoder.write_text('#!' + sys.executable + '\nimport shutil,sys\n'
                           'shutil.copyfile(sys.argv[2],sys.argv[3])\n')
        decoder.chmod(0o700)
        (vendor / '.carr-build.json').write_text(json.dumps(dict(
            binary_sha256=hashlib.sha256(decoder.read_bytes()).hexdigest(),source_digest='c'*64)))
        guide = self.root / 'guide'
        guide.write_text('Deals/Test')
        self.settings.vendor = vendor
        self.settings.decoder = decoder
        self.settings.folder_guide = guide
        return source, self.root / 'output' / 'snapshot.json'

    def test_produce_atomic_private_and_real_consumer(self):
        source, output = self.prepare_copy()
        before = (source / 'HxStore.hxd').read_bytes()
        counts = produce(source,output,self.settings).counts
        self.assertEqual(counts['observed'],2)
        self.assertEqual(output.stat().st_mode & 0o777,0o600)
        self.assertEqual(output.parent.stat().st_mode & 0o777,0o700)
        self.assertEqual((source / 'HxStore.hxd').read_bytes(),before)
        self.assertEqual(list(output.parent.iterdir()),[output])
        observations, routing, catalog, policy, _ = read_snapshot(output,self.settings,150)
        self.assertEqual(len(plan_triage(observations,routing,catalog,policy).moves),0)
        value=json.loads(output.read_text())
        self.assertEqual(value['provenance']['decoder_binary_sha256'],value['decoder_pin'])
        self.assertEqual(value['availability']['provider_identity'],'unavailable')

    def test_refused_cli_invalidates_eligible_snapshot_for_real_runner(self):
        import copy
        import io
        from contextlib import redirect_stdout
        from lib import outlook_triage as triage, outlook_triage_producer as producer
        from lib.outlook_ui import Window
        source, unused = self.prepare_copy()
        repo = self.root / 'runner'
        output = repo / triage.OUT / 'snapshot.json'
        output.parent.mkdir(mode=0o700, parents=True)
        produce(source, output, self.settings)
        original = json.loads(output.read_text())
        eligible = copy.deepcopy(original)
        route = json.loads((Path(__file__).resolve().parent /
                            'fixtures/outlook-triage/routing.json').read_text())
        row = eligible['observations'][0]
        row.update(message_id='provider-synthetic-1', revision='revision-synthetic-1',
                   read_state='unread', content_complete=True,
                   sender='client-001 folder-A', recipients=['synthetic@example.test'])
        eligible['observations'] = [row]
        eligible['routing'].update(guide=route['guide'], find_results=route['find'], complete=True)
        eligible['catalog']['folders'] = [dict(account=self.settings.account,
            folder_id='destination-synthetic-' + str(i), path=path)
            for i, path in enumerate(route['paths'])]
        output.write_text(json.dumps(eligible)); output.chmod(0o600)
        observations, routing, catalog, policy, _ = read_snapshot(output, self.settings, 150)
        self.assertEqual(len(plan_triage(observations, routing, catalog, policy).moves), 1)
        contract = repo / 'spec/outlook/triage-executor-contract.v1.json'
        contract.parent.mkdir(parents=True)
        contract.write_bytes((Path(__file__).resolve().parents[1] /
                              'spec/outlook/triage-executor-contract.v1.json').read_bytes())
        good_source = (source / 'HxStore.hxd').read_bytes()
        (source / 'HxStore.hxd').write_bytes(b'invalid')
        with patch.object(producer, 'load_settings', return_value=self.settings), \
                redirect_stdout(io.StringIO()) as captured:
            result = producer.main(['--copy', str(source), '--output', str(output)])
        self.assertEqual(result, 78)
        self.assertEqual(json.loads(captured.getvalue())['state'], 'refused')
        with patch.object(triage, 'load_settings', return_value=self.settings), \
                patch.object(Window, 'move', side_effect=AssertionError('mailbox effect')), \
                patch('lib.outlook_ui.prepare_outlook', side_effect=AssertionError('desktop')):
            outcome, report = triage.triage(repo, self.root, triage.Waits(),
                                            triage.Limits(), lambda: False)
        self.assertEqual(report.planning['snapshot'], 'snapshot_producer_unavailable')
        self.assertEqual(report.planning['planned'], 0)
        self.assertEqual(report.planning['moved'], 0)
        retained = json.loads(output.read_text())
        self.assertEqual(retained['observations'], eligible['observations'])
        self.assertEqual(retained['effect_state'], eligible['effect_state'])
        (source / 'HxStore.hxd').write_bytes(good_source)
        produce(source, output, self.settings)
        self.assertEqual(len(read_snapshot(output, self.settings, 150)[0]), 2)

    def test_configuration_refusal_preserves_previous_evidence(self):
        import io
        from contextlib import redirect_stdout
        from lib import outlook_triage_producer as producer
        source, output = self.prepare_copy()
        produce(source, output, self.settings)
        prior = json.loads(output.read_text())
        with patch.object(producer, 'load_settings', side_effect=ValueError('fixture')), \
                redirect_stdout(io.StringIO()) as captured:
            self.assertEqual(producer.main(['--copy', str(source), '--output', str(output)]), 78)
        self.assertEqual(json.loads(captured.getvalue())['state'], 'refused')
        self.assertEqual(json.loads(output.read_text()),
                         prior)
        self.assertEqual(len(read_snapshot(output, self.settings, 150)[0]), 2)

    def test_interrupted_attempt_preserves_data_but_blocks_planning(self):
        source, output = self.prepare_copy()
        produce(source, output, self.settings)
        prior = json.loads(output.read_text())
        with patch('lib.outlook_triage_producer.subprocess.run', side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                produce(source, output, self.settings)
        self.assertEqual(json.loads(output.read_text()),
                         prior)
        self.assertEqual(len(read_snapshot(output, self.settings, 150)[0]), 2)
        self.assertEqual(list(output.parent.iterdir()), [output])

    def test_source_race_preserves_previous_output(self):
        source, output = self.prepare_copy()
        produce(source,output,self.settings)
        old = output.read_bytes()
        import subprocess
        run = subprocess.run
        def change_source(*args,**kwargs):
            result = run(*args,**kwargs)
            with (source/'HxStore.hxd').open('ab') as handle:
                handle.write(b'changed')
            return result
        with patch('lib.outlook_triage_producer.subprocess.run',side_effect=change_source):
            with self.assertRaises(ProducerError):
                produce(source,output,self.settings)
        retained = json.loads(output.read_text())
        self.assertEqual(retained, json.loads(old))
        self.assertEqual(len(read_snapshot(output, self.settings, 150)[0]), 2)
        self.assertEqual(list(output.parent.iterdir()),[output])

    def test_wrong_pin_and_manifest_traversal_refuse(self):
        source, output = self.prepare_copy()
        receipt=self.settings.vendor/'.carr-build.json'
        old=receipt.read_bytes()
        receipt.write_text(json.dumps(dict(binary_sha256='x'*64,source_digest='c'*64)))
        with self.assertRaisesRegex(ProducerError,'decoder_pin_mismatch'):
            produce(source,output,self.settings)
        receipt.write_bytes(old)
        manifest=source/'manifest.json'
        value=json.loads(manifest.read_text())
        value['files'][0]['path']='../store.db'
        manifest.write_text(json.dumps(value))
        with self.assertRaisesRegex(ProducerError,'manifest_path_invalid'):
            produce(source,output,self.settings)
        self.assertFalse(output.exists())

    def test_deadline_and_bytes_refuse_without_output(self):
        source, output = self.prepare_copy()
        with patch('lib.outlook_triage_producer.DEADLINE_SECONDS',0):
            with self.assertRaisesRegex(ProducerError,'deadline_exceeded'):
                produce(source,output,self.settings)
        with patch('lib.outlook_triage_producer.SOURCE_BYTES',1):
            with self.assertRaises(ProducerError):
                produce(source,output,self.settings)
        self.assertFalse(output.exists())

    def test_cross_account_folder_ancestor_not_catalogued(self):
        with closing(sqlite3.connect(self.db)) as con, con:
            con.execute("INSERT INTO folders VALUES(4,7,'Private','folder',3)")
        value,_=project(self.db,self.settings,'Deals/Test','a'*64,'b'*64)
        self.assertFalse(any('Private' in f['path'] for f in value['catalog']['folders']))

    def test_previous_uncertain_effect_cannot_be_erased(self):
        source,output=self.prepare_copy()
        produce(source,output,self.settings)
        value=json.loads(output.read_text()); value['effect_state']='uncertain'
        output.write_text(json.dumps(value)); old=output.read_bytes()
        with self.assertRaisesRegex(ProducerError,'reconciliation_required'):
            produce(source,output,self.settings)
        self.assertEqual(output.read_bytes(),old)

    def test_real_runner_consumes_produced_observations_without_effects(self):
        from lib import outlook_triage as triage
        from lib.outlook_ui import Window
        source,output=self.prepare_copy()
        produce(source,output,self.settings)
        repo=self.root/'runner'
        path=repo/triage.OUT/'snapshot.json'
        path.parent.mkdir(mode=0o700, parents=True)
        path.write_bytes(output.read_bytes()); path.chmod(0o600)
        contract=repo/'spec/outlook/triage-executor-contract.v1.json'
        contract.parent.mkdir(parents=True)
        contract.write_bytes((Path(__file__).resolve().parents[1]/
            'spec/outlook/triage-executor-contract.v1.json').read_bytes())
        with patch.object(triage,'load_settings',return_value=self.settings), \
                patch.object(Window,'move',side_effect=AssertionError('move attempted')), \
                patch('lib.outlook_ui.prepare_outlook',side_effect=AssertionError('UI attempted')):
            outcome,report=triage.observe_and_triage(repo,self.root,source,triage.Waits(),triage.Limits(),lambda:False)
        self.assertEqual(outcome.state,'skipped')
        self.assertEqual(report.planning['snapshot'],'validated')
        self.assertEqual(report.planning['observed'],2)
        self.assertEqual(report.planning['held'],2)
        self.assertEqual(report.planning['moved'],0)
        self.assertEqual(report.left['identity_unverified'],1)

    def test_symlink_and_decoder_failure_refuse(self):
        source,output=self.prepare_copy()
        original=source/'HxStore.hxd'
        original.unlink(); original.symlink_to(self.db)
        with self.assertRaisesRegex(ProducerError,'manifest_path_invalid'):
            produce(source,output,self.settings)
        self.assertFalse(output.exists())
        original.unlink(); original.write_bytes(self.db.read_bytes())
        import subprocess
        with patch('lib.outlook_triage_producer.subprocess.run',
                   return_value=subprocess.CompletedProcess([],1)):
            with self.assertRaisesRegex(ProducerError,'decoder_failed'):
                produce(source,output,self.settings)
        self.assertFalse(output.exists())


    def test_file_folder_decoded_and_output_caps(self):
        source, output = self.prepare_copy()
        for name, cap in [('FILE_CAP', 0), ('MAX_ROWS', 1), ('DECODED_BYTES', 1)]:
            with self.subTest(cap=name), patch('lib.outlook_triage_producer.' + name, cap):
                with self.assertRaises(ProducerError):
                    produce(source, output, self.settings)
            self.assertFalse(output.exists())
        with patch('lib.outlook_triage_producer.MAX_BYTES', 1024):
            with self.assertRaisesRegex(ProducerError, 'output_byte_cap'):
                produce(source, output, self.settings)
        self.assertFalse(output.exists())

    def test_duplicate_inbox_and_unresolved_source_fail_closed(self):
        with closing(sqlite3.connect(self.db)) as con, con:
            con.execute('UPDATE messages SET folder_links_unresolved=1 WHERE id=1')
        value, counts = project(self.db, self.settings, 'Deals/Test', 'a'*64, 'b'*64)
        self.assertEqual(counts['ownership_unverified'], 1)
        self.assertEqual(len(value['observations']), 1)
        with closing(sqlite3.connect(self.db)) as con, con:
            con.execute("INSERT INTO folders VALUES(4,7,'Inbox','top',NULL)")
        with self.assertRaisesRegex(ProducerError, 'inbox_unresolved'):
            project(self.db, self.settings, 'Deals/Test', 'a'*64, 'b'*64)


class InvocationTest(unittest.TestCase):
    setUp = ProducerTest.setUp
    prepare_copy = ProducerTest.prepare_copy

    def combined(self, source, repo, **kwargs):
        from lib import outlook_triage as runner
        from lib.outlook_ui import Window
        with patch.object(runner, 'load_settings', return_value=self.settings), \
                patch.object(Window, 'move', side_effect=AssertionError('mailbox effect')), \
                patch('lib.outlook_ui.prepare_outlook', side_effect=AssertionError('desktop')):
            return runner.observe_and_triage(repo, self.root, source, runner.Waits(),
                                             runner.Limits(), lambda: False, **kwargs)

    def seed_eligible(self, output):
        value, _ = project(self.db, self.settings, 'Deals/Test', 'a'*64, 'b'*64)
        route = json.loads((Path(__file__).parent / 'fixtures/outlook-triage/routing.json').read_text())
        row = value['observations'][0]
        row.update(message_id='synthetic-provider', revision='synthetic-revision',
                   read_state='unread', content_complete=True, sender='client-001 folder-A',
                   recipients=['synthetic@example.test'])
        value['observations'] = [row]
        value['routing'].update(guide=route['guide'], find_results=route['find'], complete=True)
        value['catalog']['folders'] = [dict(account=self.settings.account,
            folder_id='synthetic-destination-' + str(i), path=path) for i,path in enumerate(route['paths'])]
        output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        output.write_text(json.dumps(value))
        output.chmod(0o600)
        parsed = read_snapshot(output,self.settings,150)
        self.assertEqual(len(plan_triage(*parsed[:4]).moves),1)

    def test_current_attempt_and_independent_retry(self):
        from lib import outlook_triage as runner
        source, _ = self.prepare_copy()
        repo = self.root / 'runner'
        outcome, report = self.combined(source, repo)
        self.assertEqual(report.planning['observed'], 2)
        self.assertEqual(report.planning['planned'], 0)
        self.assertEqual(report.planning['held'], 2)
        with patch.object(runner, 'load_settings', return_value=self.settings):
            outcome, report = runner.triage(repo, self.root, runner.Waits(), runner.Limits(), lambda: False)
        self.assertEqual(outcome.reason, 'snapshot_producer_unavailable')
        self.assertEqual(report.planning['observed'], 0)

    def test_copy_contains_output_refuses_without_mutation(self):
        source, _ = self.prepare_copy()
        repo = source
        from lib import outlook_triage as runner
        self.seed_eligible(repo / runner.OUT / 'snapshot.json')
        before = {str(p.relative_to(source)):p.read_bytes() for p in source.rglob('*') if p.is_file()}
        outcome, report = self.combined(source, repo)
        self.assertEqual(report.planning['planned'], 0)
        self.assertEqual(report.planning['observed'], 0)
        self.assertEqual(before, {str(p.relative_to(source)):p.read_bytes() for p in source.rglob('*') if p.is_file()})

    def test_symlink_parent_refuses_without_target_mutation(self):
        from lib import outlook_triage as runner
        source, output = self.prepare_copy()
        produce(source, output, self.settings)
        repo = self.root / 'runner'
        path = repo / runner.OUT
        path.parent.mkdir(mode=0o700, parents=True)
        path.symlink_to(output.parent, target_is_directory=True)
        self.seed_eligible(output)
        before = output.read_bytes()
        outcome, report = self.combined(source, repo)
        self.assertEqual(report.planning['observed'], 0)
        self.assertEqual(output.read_bytes(), before)

    def test_retained_stale_invalidated_effects_require_reconciliation(self):
        from lib import outlook_triage as runner
        source, _ = self.prepare_copy()
        repo = self.root / 'runner'
        self.combined(source, repo)
        output = repo / runner.OUT / 'snapshot.json'
        value = json.loads(output.read_text())
        for effect in ('uncertain', 'interrupted'):
            value['effect_state'] = effect
            value['publication_state'] = 'invalidated'
            value['observed_at'] = '2000-01-01T00:00:00+00:00'
            for row in value['observations']:
                row['observed_at'] = value['observed_at']
            output.write_text(json.dumps(value))
            before = output.read_bytes()
            outcome, report = self.combined(source, repo)
            self.assertEqual(outcome.reason, 'reconciliation_required')
            self.assertEqual(report.planning['observed'], 0)
            self.assertEqual(output.read_bytes(), before)
            with patch.object(runner, 'load_settings', return_value=self.settings):
                self.assertEqual(runner.triage(repo, self.root, runner.Waits(), runner.Limits(),
                                              lambda: False)[0].reason, 'reconciliation_required')

    def test_malformed_and_unreadable_evidence_refuses_replacement(self):
        from lib import outlook_triage as runner
        source, _ = self.prepare_copy()
        repo = self.root / 'runner'
        self.combined(source, repo)
        output = repo / runner.OUT / 'snapshot.json'
        for raw in (b'{', b'[]', b'{"effect_state":"none"}'):
            output.write_bytes(raw)
            self.assertEqual(self.combined(source, repo)[1].planning['observed'], 0)
            self.assertEqual(output.read_bytes(), raw)
        output.chmod(0o644)
        self.assertEqual(self.combined(source, repo)[1].planning['observed'], 0)
        self.assertEqual(output.read_bytes(), raw)

    def test_failed_publication_and_settings_never_admit_then_republish(self):
        from lib import outlook_triage as runner, outlook_triage_producer as producer
        source, _ = self.prepare_copy()
        repo = self.root / 'runner'
        self.combined(source, repo)
        output = repo / runner.OUT / 'snapshot.json'
        before = output.read_bytes()
        with patch.object(producer.os, 'replace', side_effect=OSError('synthetic first write')):
            self.assertEqual(self.combined(source, repo)[1].planning['observed'], 0)
        self.assertEqual(output.read_bytes(), before)
        with patch.object(runner, 'load_settings', side_effect=ValueError('settings')):
            outcome, report = runner.observe_and_triage(repo,self.root,source,runner.Waits(),runner.Limits(),lambda:False)
        self.assertEqual(report.planning['observed'], 0)
        self.assertEqual(output.read_bytes(), before)
        with patch.object(producer.subprocess, 'run', side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.combined(source, repo)
        self.assertEqual(output.read_bytes(), before)
        self.assertEqual(self.combined(source, repo)[1].planning['observed'], 2)
        self.assertEqual(list(output.parent.iterdir()), [output])

    def test_path_replacement_does_not_substitute_admitted_value(self):
        from lib import outlook_triage_producer as producer, outlook_triage as runner
        source, _ = self.prepare_copy()
        repo = self.root / 'runner'
        publish = producer._publish
        def substitute(output, raw, fd):
            publish(output, raw, fd)
            value = json.loads(output.read_bytes())
            value['observations'] = []
            output.write_text(json.dumps(value))
        with patch.object(producer, '_publish', side_effect=substitute):
            self.assertEqual(self.combined(source, repo)[1].planning['observed'], 2)
        self.assertEqual(json.loads((repo / runner.OUT / 'snapshot.json').read_text())['observations'], [])

    def test_directory_identity_change_refuses_publication(self):
        from lib import outlook_triage_producer as producer, outlook_triage as runner
        source, _ = self.prepare_copy()
        repo = self.root / 'runner'
        self.combined(source, repo)
        output = repo / runner.OUT / 'snapshot.json'
        before = output.read_bytes()
        project = producer.project
        def replace_directory(*args, **kwargs):
            result = project(*args, **kwargs)
            output.parent.rename(output.parent.with_name('retained'))
            output.parent.mkdir(mode=0o700)
            return result
        with patch.object(producer, 'project', side_effect=replace_directory):
            self.assertEqual(self.combined(source, repo)[1].planning['observed'], 0)
        self.assertFalse(output.exists())
        self.assertEqual((output.parent.with_name('retained') / output.name).read_bytes(), before)

    def test_source_nested_beside_output_remains_immutable(self):
        import shutil
        from lib import outlook_triage as runner
        source, _ = self.prepare_copy()
        repo = self.root / 'runner'
        parent = repo / runner.OUT
        parent.mkdir(mode=0o700, parents=True)
        nested = parent / 'research-copy'
        shutil.copytree(source, nested)
        before = {p.name:p.read_bytes() for p in nested.iterdir()}
        self.assertEqual(self.combined(nested, repo)[1].planning['observed'], 2)
        self.assertEqual(before, {p.name:p.read_bytes() for p in nested.iterdir()})

    def test_real_combined_cli_and_standalone_export(self):
        import io
        from contextlib import redirect_stdout
        from datetime import datetime
        from zoneinfo import ZoneInfo
        from lib import outlook_triage as runner, outlook_triage_producer as producer
        source, _ = self.prepare_copy()
        repo = self.root / 'runner'
        self.settings.timezone = ZoneInfo('America/Chicago')
        self.settings.require_repo = lambda path: None
        self.settings.require_service = lambda name: None
        moment = datetime.now(self.settings.timezone).replace(hour=19, minute=15)
        with patch.object(runner, 'load_settings', return_value=self.settings), \
                patch.object(runner, '__file__', str(repo / 'lib/outlook_triage.py')), \
                patch.object(runner.Path, 'home', return_value=self.root), \
                patch.object(runner, 'datetime', wraps=datetime) as clock, \
                patch('lib.outlook_runtime.stamp', return_value={'runtime_sha':'a'*40}):
            clock.now.return_value = moment
            with redirect_stdout(io.StringIO()) as capture:
                self.assertEqual(runner.main(['--copy',str(source)]), 78)
            self.assertIn('observed=2 planned=0 held=2 moved=0', capture.getvalue())
            with redirect_stdout(io.StringIO()) as capture:
                self.assertEqual(runner.main([]), 78)
            self.assertIn('snapshot_producer_unavailable', capture.getvalue())
            before = {str(p.relative_to(repo)):p.read_bytes() for p in repo.rglob('*') if p.is_file()}
            with redirect_stdout(io.StringIO()) as capture:
                self.assertEqual(runner.main(['--copy',str(repo)]), 78)
            self.assertIn('observed=0 planned=0 held=0 moved=0',capture.getvalue())
            self.assertEqual(before,{str(p.relative_to(repo)):p.read_bytes() for p in repo.rglob('*') if p.is_file()})
            folder = repo / runner.OUT
            retained = folder.with_name('retained')
            folder.rename(retained)
            folder.symlink_to(retained,target_is_directory=True)
            before = {p.name:p.read_bytes() for p in retained.iterdir()}
            with redirect_stdout(io.StringIO()) as capture:
                self.assertEqual(runner.main(['--copy',str(source)]), 78)
            self.assertIn('observed=0 planned=0 held=0 moved=0',capture.getvalue())
            self.assertEqual(before,{p.name:p.read_bytes() for p in retained.iterdir()})
            folder.unlink()
            retained.rename(folder)
            with patch.object(runner, 'load_settings', side_effect=ValueError('settings')), redirect_stdout(io.StringIO()) as capture:
                self.assertEqual(runner.main(['--copy',str(source)]),78)
            self.assertIn('snapshot_producer_unavailable',capture.getvalue())
        with patch.object(producer, 'load_settings', return_value=self.settings), redirect_stdout(io.StringIO()) as capture:
            self.assertEqual(producer.main(['--copy',str(source),'--output',str(repo/runner.OUT/'snapshot.json')]),78)
        self.assertEqual(json.loads(capture.getvalue())['state'],'observed_held')


if __name__ == '__main__':
    unittest.main()
