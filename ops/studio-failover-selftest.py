#!/usr/bin/env python3
"""Behavior tests for host inventory and manual failover."""
import importlib.util
import json
import plistlib
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from outlook_test_support import write_settings


class InventoryTests(unittest.TestCase):
    def test_regeneration_preserves_recovery_prerequisites_and_disabled_roles(self):
        spec = importlib.util.spec_from_file_location('inventory_test', ROOT / 'ops/studio-inventory.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / 'ops/config').mkdir(parents=True)
            shutil.copyfile(ROOT / 'ops/config/studio-failover.v1.json', root / 'ops/config/studio-failover.v1.json')
            labels = ['com.carr.nightly-record-layer', 'com.carr.outlook-intake',
                      'com.carr.outlook-triage']
            (root / 'ops/launchd').mkdir()
            for label in labels:
                shutil.copyfile(ROOT / ('ops/launchd/' + label + '.plist'), root / ('ops/launchd/' + label + '.plist'))
            folder = root / 'out/studio-failover'
            folder.mkdir(parents=True)
            for host in ('studio', 'macbook'):
                (folder / (host + '.json')).write_text(json.dumps({'host': host, 'launchd': [
                    {'label': label, 'disabled': label == 'com.carr.nightly-record-layer'} for label in labels]}))
            for _ in range(2):
                module.compare(root)
                config = json.loads((root / 'ops/config/studio-failover.v1.json').read_text())
                jobs = {j['label']: j for j in config['jobs']}
                self.assertEqual(jobs['com.carr.nightly-record-layer']['state'], ['~/carr-system/backups'])
                for label in labels:
                    self.assertFalse(jobs[label]['enabled'])
                for label in labels[1:2]:
                    self.assertEqual(jobs[label]['state'], ['~/carr-system/out/outlook-intake/state.json'])
                self.assertEqual(jobs[labels[2]]['state'], ['~/.config/carr/outlook-nightly-sweep.folders.md'])
                self.assertIn('Outlook permission and outbound reconciliation',
                              {r['name'] for r in config['manual_roles']})

    def test_requested_dependencies_are_checked_independently_of_launchd_installation(self):
        with tempfile.TemporaryDirectory() as raw:
            paths = ['/bin/sh', '~/missing-failover-file']
            result = subprocess.run([sys.executable, str(ROOT / 'ops/studio-inventory.py'),
                                     '--home', raw, '--paths', json.dumps(paths)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            observed = json.loads(result.stdout)['path_presence']
            self.assertTrue(observed['/bin/sh'])
            self.assertFalse(observed['~/missing-failover-file'])

    def test_inventory_never_exports_credential_or_argument_values(self):
        with tempfile.TemporaryDirectory() as raw:
            home = Path(raw)
            agents = home / 'Library/LaunchAgents'
            agents.mkdir(parents=True)
            (agents / 'com.carr.test.plist').write_bytes(plistlib.dumps({
                'Label': 'com.carr.test', 'ProgramArguments': ['/bin/sh', '--token', 'SENTINEL_SECRET'],
                'EnvironmentVariables': {'TOKEN': 'SENTINEL_SECRET'}, 'StartInterval': 60}))
            creds = home / '.config/carr'
            creds.mkdir(parents=True)
            (creds / 'db.env').write_text('PASSWORD=SENTINEL_SECRET')
            result = subprocess.run([sys.executable, str(ROOT / 'ops/studio-inventory.py'),
                                     '--home', raw], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotIn('SENTINEL_SECRET', result.stdout)
            data = json.loads(result.stdout)
            self.assertIn('~/.config/carr/db.env', data['credential_paths'])
            self.assertEqual(data['launchd'][0]['executables'], ['/bin/sh'])

class FailoverTests(unittest.TestCase):
    def test_takeover_leaves_outlook_disabled_until_manual_reconciliation(self):
        module = self.host_module()
        config = json.loads((ROOT / 'ops/config/studio-failover.v1.json').read_text())
        config['jobs'] = [j for j in config['jobs'] if j['label'].startswith('com.carr.outlook-')]
        self.assertEqual(len(config['jobs']), 2)
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            write_settings(root, ROOT, services=['intake'])
            commands = []
            def launchctl(argv, **kwargs):
                commands.append(argv)
                disabled = ['launchctl', 'disable', argv[-1]] in commands
                return Mock(returncode=int(argv[1] == 'print' and disabled), stdout='last exit code = 78')
            with patch.object(module.Path, 'home', return_value=root), \
                 patch.object(module, 'command', side_effect=launchctl), patch.object(module, 'write_marker'), \
                 patch.object(module, 'Leader') as leader, patch.object(module, 'connection'):
                host = module.Host(ROOT, config, 'macbook')
                agents = root / 'Library/LaunchAgents'
                agents.mkdir(parents=True)
                for job in config['jobs']:
                    (agents / (job['label'] + '.plist')).write_text(host.body(job))
                leader.return_value.read.return_value = ('macbook', 1)
                host.start()
                for job in config['jobs']:
                    self.assertIn(['launchctl', 'disable', host.domain + '/' + job['label']], commands)
                self.assertFalse(any(c[1] in ('bootstrap', 'kickstart', 'enable') for c in commands))
                self.assertTrue(host.verify())

    def test_replacement_host_requires_intake_refresh_and_actual_backup_state(self):
        from lib.studio_failover import plan
        config = json.loads((ROOT / 'ops/config/studio-failover.v1.json').read_text())
        result = plan(config, {}, 'macbook')
        installed = {step.get('label') for step in result['steps'] if step['action'] == 'install'}
        self.assertTrue({'com.carr.outlook-intake', 'com.carr.outlook-triage'} <= installed)
        missing = '\n'.join(result['missing'])
        self.assertIn('~/carr-system/backups', missing)
        self.assertNotIn('~/carr-system/out/backups', missing)
        for state in ('state.json',):
            self.assertIn('~/carr-system/out/outlook-intake/' + state, missing)
        self.assertIn('Outlook permission and outbound reconciliation', missing)
        self.assertFalse(result['ready'])

    @staticmethod
    def host_module():
        spec = importlib.util.spec_from_file_location('failover_host_test', ROOT / 'ops/studio-failover.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_current_manifest_contract_has_only_retained_job_templates(self):
        from lib.studio_failover import contract_hash
        from lib.studio_failover import plan
        digest = contract_hash(ROOT)
        self.assertRegex(digest, r'^[a-f0-9]{64}$')
        config = json.loads((ROOT / 'ops/config/studio-failover.v1.json').read_text())
        installed = {step.get('label') for step in plan(config, {}, 'macbook')['steps']
                     if step['action'] == 'install'}
        self.assertNotIn('com.carr.cutover-watch', installed)
        self.assertNotIn('com.carr.timebomb-audit', installed)

    def test_intake_launchd_program_keeps_its_full_disk_access_identity(self):
        from lib.studio_failover import managed_body
        module = self.host_module()
        config = json.loads((ROOT / 'ops/config/studio-failover.v1.json').read_text())
        config['jobs'] = [j for j in config['jobs'] if j['label'] == 'com.carr.outlook-intake']
        with tempfile.TemporaryDirectory() as raw:
            home = Path(raw).resolve()
            helper = home / 'custom-bin/granted-copy'
            write_settings(home, ROOT, copy_helper=str(helper), executable_path='/custom/bin:/usr/bin:/bin')
            with patch.object(module.Path, 'home', return_value=home), \
                 patch.object(module, 'command', return_value=Mock(returncode=1, stdout='')):
                host = module.Host(ROOT, config, 'macbook')
                host.install()
            path = home / 'Library/LaunchAgents/com.carr.outlook-intake.plist'
            installed = path.read_text()
            expected = [str(helper), '--run-intake']
            self.assertEqual(plistlib.loads(installed.encode())['EnvironmentVariables']['PATH'], '/custom/bin:/usr/bin:/bin')
            self.assertEqual(plistlib.loads(installed.encode())['ProgramArguments'], expected)
            reconciled = managed_body(installed, ROOT, home)
            self.assertEqual(plistlib.loads(reconciled.encode())['ProgramArguments'], expected)

    def test_missing_canonical_template_does_not_hide_missing_executable(self):
        module = self.host_module()
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            source = root / 'source'
            source.mkdir()
            (source / 'job.plist').write_bytes(plistlib.dumps({
                'Label': 'job', 'ProgramArguments': ['/missing/failover-executable']}))
            config = {'jobs': [{'label': 'job', 'source': 'job.plist'}],
                      'hosts': {'macbook': {'hostname': 'fixture'}}}
            command = Mock(return_value=Mock(stdout='', returncode=1))
            with patch.object(module, 'ROOT', source), patch.object(module.Path, 'home', return_value=root), \
                 patch.object(module, 'command', command), patch.object(module, 'connection', side_effect=RuntimeError()):
                snapshot = module.Host(root / 'canonical', config, 'macbook').snapshot()
            self.assertFalse(snapshot['paths']['job.plist'])
            self.assertFalse(snapshot['paths']['/missing/failover-executable'])

    def test_dry_run_requires_transfer_authority_even_when_jobs_can_read_leader(self):
        module = self.host_module()
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            conn = Mock()
            conn.execute.return_value.fetchone.return_value = ('studio', 1)
            config = {'jobs': [], 'hosts': {'macbook': {'hostname': 'fixture'}}}
            with patch.object(module.Path, 'home', return_value=root), \
                 patch.object(module, 'command', return_value=Mock(stdout='', returncode=1)), \
                 patch.object(module, 'connection', side_effect=[conn, RuntimeError('missing authority')]):
                snapshot = module.Host(root, config, 'macbook').snapshot()
            self.assertFalse(snapshot['leader_ready'])
            self.assertIn('authority prerequisite: RuntimeError', snapshot['errors'])

    def test_leader_connection_refuses_host_local_and_pooled_endpoints(self):
        from lib.studio_failover import connection
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / '.config/carr/failover.env'
            path.parent.mkdir(parents=True)
            for host in ['localhost', 'mac-studio.tailc8cc93.ts.net', 'ep-fixture-pooler.neon.tech']:
                path.write_text('CARR_FAILOVER_JOBS_URL=postgresql://carr_jobs:fixture@' + host + '/fixture?sslmode=require\n')  # ci-secret-scan: allow -- owned fixture
                path.chmod(0o600)
                with self.assertRaisesRegex(RuntimeError, 'direct off-host'):
                    connection(home=raw)
            path.write_text('CARR_FAILOVER_JOBS_URL=postgresql://carr_jobs:fixture@ep-fixture.neon.tech/fixture?sslmode=require&hostaddr=127.0.0.1\n')  # ci-secret-scan: allow -- owned fixture
            with self.assertRaisesRegex(RuntimeError, 'direct off-host'):
                connection(home=raw)
            path.write_text('CARR_FAILOVER_JOBS_URL=postgresql://carr_jobs:fixture@ep-fixture.neon.tech/fixture?sslmode=require\n')  # ci-secret-scan: allow -- owned fixture
            conn = Mock()
            conn.execute.return_value.fetchone.return_value = ('carr_jobs', 'carr_jobs')
            with patch('psycopg.connect', return_value=conn):
                self.assertIs(connection(home=raw), conn)

    def test_demote_apply_refuses_wrong_host_before_mutation(self):
        module = self.host_module()
        host = Mock()
        host.repo = module.ROOT
        host.snapshot.return_value = {'canonical': True, 'gui': True, 'errors': ['wrong target host']}
        with patch.object(module, 'Host', return_value=host), patch.object(module, 'plan', return_value={'missing': []}), \
             patch.object(sys, 'argv', ['failover', 'demote', '--apply']):
            with self.assertRaisesRegex(RuntimeError, 'apply_requires_target_host_and_gui'): module.main()
        host.demote.assert_not_called()

    def test_offline_fence_binds_target_revision_even_if_dead_source_is_older(self):
        from datetime import datetime, timezone
        module = self.host_module()
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            receipt = root / 'power.json'
            evidence = {'kind': 'powered-off', 'source': 'studio', 'target': 'macbook',
                        'source_sha': 'older-source', 'target_sha': 'current-target',
                        'verified_at': datetime.now(timezone.utc).isoformat(),
                        'keep_off_until_failback': True}
            receipt.write_text(json.dumps(evidence))
            with patch.object(module, 'command', return_value=Mock(stdout='current-target', returncode=0)):
                host = module.Host(root, {'jobs': []}, 'macbook', receipt)
                self.assertTrue(host.fence('studio'))
                evidence['target_sha'] = 'different-target'
                receipt.write_text(json.dumps(evidence))
                self.assertFalse(host.fence('studio'))

    def test_abort_reports_job_still_registered_and_disarms(self):
        module = self.host_module()
        with tempfile.TemporaryDirectory() as raw:
            with patch.object(module.Path, 'home', return_value=Path(raw)), \
                 patch.object(module, 'write_marker'), \
                 patch.object(module, 'command', return_value=Mock(returncode=0, stdout='')):
                host = module.Host(Path(raw), {'jobs': [{'label': 'job'}]}, 'macbook')
                with self.assertRaisesRegex(RuntimeError, 'still registered'): host.abort()
                self.assertFalse(json.loads(host.marker.read_text())['armed'])

    def test_dry_run_lists_all_missing_paths_and_every_transfer_step(self):
        from lib.studio_failover import plan
        config = {'jobs': [{'label': 'com.carr.a', 'source': 'ops/launchd/a.plist',
                            'credentials': ['~/.config/carr/db.env'], 'state': ['~/state.db']}],
                  'manual_roles': [{'name': 'model server', 'prerequisite': 'build model runtime'}]}
        result = plan(config, {'paths': {}, 'host': 'macbook', 'canonical': False,
                               'leader_ready': False, 'gui': False}, 'macbook')
        self.assertFalse(result['ready'])
        self.assertIn('~/.config/carr/db.env', str(result))
        self.assertIn('~/state.db', str(result))
        self.assertEqual([s['action'] for s in result['steps']],
                         ['preflight', 'fence-source', 'transfer-leader', 'install', 'start', 'verify'])

    def test_job_cannot_start_on_nonleader_or_when_job_lock_is_busy(self):
        from lib.studio_failover import run_guarded
        class Authority:
            def __init__(self, owner, busy=False): self.owner, self.busy, self.closed = owner, busy, False
            def acquire(self, host, label): return self.owner == host and not self.busy
            def healthy(self, host): return True
            def close(self): self.closed = True
        for owner, busy in [('studio', False), ('macbook', True)]:
            authority = Authority(owner, busy)
            launched = []
            self.assertEqual(run_guarded(authority, 'macbook', 'job', lambda: launched.append(True)), 75)
            self.assertEqual(launched, [])
            self.assertTrue(authority.closed)

    def test_transfer_refuses_unfenced_source_and_does_not_install(self):
        from lib.studio_failover import transfer
        class Host:
            def __init__(self): self.actions=[]
            def fence(self, source): self.actions.append('fence'); return False
            def claim(self, source, target): self.actions.append('claim')
            def install(self): self.actions.append('install')
            def start(self): self.actions.append('start')
            def verify(self): self.actions.append('verify'); return True
        h = Host()
        with self.assertRaisesRegex(RuntimeError, 'source_not_fenced'):
            transfer(h, 'studio', 'macbook')
        self.assertEqual(h.actions, ['fence'])

    def test_config_render_preserves_opt_in_guard_without_wrapping_settings(self):
        from lib.studio_failover import managed_body
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / 'ops/config').mkdir(parents=True)
            (root / '.config/carr').mkdir(parents=True)
            (root / 'ops/config/studio-failover.v1.json').write_text(json.dumps({'jobs': [{'label': 'job'}]}))
            (root / '.config/carr/failover-host.json').write_text(json.dumps({'host': 'studio', 'armed': True}))
            body = plistlib.dumps({'Label': 'job', 'ProgramArguments': ['/bin/true']}).decode()
            rendered = managed_body(body, root, root)
            self.assertIn('studio-job-guard.py', rendered)
            self.assertEqual(managed_body(rendered, root, root), rendered)



    def test_guard_stops_process_group_and_disarms_after_connection_loss(self):
        from lib.studio_failover import run_guarded
        import os
        import time
        authority = Mock()
        authority.acquire.return_value = True
        authority.healthy.side_effect = RuntimeError('SENTINEL_SECRET')
        child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'], start_new_session=True)
        disarm = Mock()
        self.assertEqual(run_guarded(authority, 'studio', 'job', lambda: child, disarm), 76)
        self.assertIsNotNone(child.poll())
        disarm.assert_called_once()
        authority.close.assert_called_once()

    def test_guard_retains_lock_after_launcher_exits_with_live_descendant(self):
        from lib.studio_failover import run_guarded
        authority = Mock()
        authority.acquire.return_value = True
        authority.healthy.side_effect = [True, True, RuntimeError('connection lost')]
        child = subprocess.Popen([sys.executable, '-c',
            'import subprocess,sys; subprocess.Popen([sys.executable,"-c","import time; time.sleep(60)"])'],
            start_new_session=True)
        disarm = Mock()
        self.assertEqual(run_guarded(authority, 'studio', 'job', lambda: child, disarm), 76)
        self.assertEqual(child.returncode, 0)
        disarm.assert_called_once()
        authority.close.assert_called_once()

    def test_successful_transfer_order_and_abort_after_partial_start(self):
        from lib.studio_failover import transfer
        events = []
        h = Mock()
        h.fence.side_effect = lambda source: events.append('fence') or True
        h.claim.side_effect = lambda source, target: events.append('claim')
        h.install.side_effect = lambda: events.append('install')
        h.start.side_effect = lambda: events.append('start')
        h.verify.side_effect = lambda: events.append('verify') or True
        transfer(h, 'studio', 'macbook')
        self.assertEqual(events, ['fence', 'claim', 'install', 'start', 'verify'])
        h.start.side_effect = RuntimeError('start_failed')
        with self.assertRaisesRegex(RuntimeError, 'start_failed'): transfer(h, 'studio', 'macbook')
        h.abort.assert_called_once()



if __name__ == '__main__':
    unittest.main()
