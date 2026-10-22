#!/usr/bin/env python3
"""Synthetic-only tests at the version monitor and guarded-consumer seams."""
import copy
from contextlib import redirect_stdout
import io
import json
import hashlib
import importlib.util
import plistlib
import subprocess
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib import outlook_version_tripwire as tripwire

PIN = 'a' * 64
VERSION = {'version': '16.102', 'build': '25101416'}
NEXT = {'version': '16.103', 'build': '25102001'}


def observation(count=1):
    return {
        'schema': 'hxprobe-observation/v1',
        'blocks': [{'block_type': 8, 'occurrences': count, 'payload_bytes': 20 * count}],
        'records': [{'record_type': 'mail', 'occurrences': count, 'total_bytes': 20 * count,
                     'decoded_bytes': 16 * count, 'decoded_byte_percent': 80,
                     'wire_field_ids': None, 'decoded_field_id_percent': None,
                     'fields': [{'field_id': 'mail.selector.ItemClass',
                                 'identifier_kind': 'string_run_selector',
                                 'wire_type': 'utf16le-run', 'occurrences': count}]}],
        'unknown_fields': [{'record_type': 'mail', 'field_id': None, 'wire_type': 'unknown',
                            'occurrences': count, 'unknown_bytes': 4 * count}],
        'coverage': {'block_signatures': count, 'verified_blocks': count, 'unverified_blocks': 0,
                     'undecodable_blocks': 0, 'verified_payload_bytes': 20 * count, 'decoded_bytes': 16 * count,
                     'decoded_byte_percent': 80},
        'issues': [], 'issue_counts': []}


class TripwireTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'private'
        self.calls = []
        self.report = observation()
        self.monitor = tripwire.Tripwire(self.root, PIN, {'mail'})

    def snapshot(self, work):
        self.calls.append('snapshot')
        (work / 'store.hxd').write_bytes(b'SyntheticOnly')

    def observe(self, path):
        self.calls.append('observe')
        self.assertEqual(path.read_bytes(), b'SyntheticOnly')
        return copy.deepcopy(self.report)

    def poll(self, version=VERSION):
        return self.monitor.check(version, self.snapshot, self.observe)

    def test_changed_version_snapshots_once_and_never_silently_accepts(self):
        first = self.poll()
        self.assertEqual(first['status'], 'review_required')
        self.assertEqual(self.calls, ['snapshot', 'observe'])
        self.assertEqual(self.poll(), first)
        self.assertEqual(self.calls, ['snapshot', 'observe'])
        with self.assertRaises(tripwire.IntakeBlocked):
            tripwire.require_accepted(self.root, VERSION, PIN)
        self.monitor.accept(VERSION)
        tripwire.require_accepted(self.root, VERSION, PIN)
        changed = self.poll(NEXT)
        self.assertEqual(changed['status'], 'review_required')
        self.assertEqual(self.calls, ['snapshot', 'observe', 'snapshot', 'observe'])
        with self.assertRaises(tripwire.IntakeBlocked):
            tripwire.require_accepted(self.root, NEXT, PIN)
        self.assertEqual(len(list((self.root / 'alerts').glob('*.json'))), 2)

    def test_inconsistent_or_extra_observation_fields_cannot_be_accepted(self):
        for mutate in (
            lambda r: r['coverage'].update(verified_payload_bytes=999),
            lambda r: r.update(unexpected='SyntheticNeverEmit'),
            lambda r: r['records'][0].update(decoded_bytes=21),
            lambda r: r['records'][0]['fields'][0].update(occurrences=-1),
        ):
            with self.subTest(mutate=mutate):
                report = observation()
                mutate(report)
                with self.assertRaises(ValueError):
                    tripwire.structural_map(report)

    def test_normal_mail_volume_is_not_a_format_change(self):
        self.poll()
        self.monitor.accept(VERSION)
        self.report = observation(200)
        result = self.poll(NEXT)
        self.assertEqual(set(result['differences'].values()), {0})

    def test_unknown_classes_and_unframed_regions_stay_blocked(self):
        for kind in ('016a', '00f7', '0071', '0105'):
            with self.subTest(kind=kind):
                report = observation()
                report['records'][0]['record_type'] = 'object.class.' + kind
                report['unknown_fields'][0]['record_type'] = 'object.class.' + kind
                self.report = report
                version = {'version': '16.103', 'build': str(int(kind, 16))}
                result = self.poll(version)
                self.assertEqual(result['reason'], 'unknown_record_type')
                alert = json.loads((self.root / 'alerts' / (result['id'] + '.json')).read_text())
                self.assertEqual(alert['unknown_record_types'], ['object.class.' + kind])
                with self.assertRaises(tripwire.IntakeBlocked):
                    self.monitor.accept(version)

    def test_local_integrity_issue_never_counts_as_complete(self):
        self.report['issues'] = [{'stage': 'block', 'reason_code': 'payload_crc_mismatch', 'block_ordinal': 1}]
        result = self.poll()
        self.assertEqual(result['status'], 'blocked')
        self.assertNotIn('observation_receipt', result)
        with self.assertRaises(tripwire.IntakeBlocked):
            self.monitor.accept(VERSION)

    def test_decoder_failure_retains_accepted_version_and_alerts_once(self):
        self.poll()
        self.monitor.accept(VERSION)
        def fail(_):
            raise RuntimeError('SyntheticNeverEmit private/path')
        result = self.monitor.check(NEXT, self.snapshot, fail)
        self.assertEqual(result['reason'], 'decoder_failed')
        tripwire.require_accepted(self.root, VERSION, PIN)
        self.assertEqual(self.poll(NEXT), result)
        self.assertEqual(self.calls.count('snapshot'), 2)
        alert = (self.root / 'alerts' / (result['id'] + '.json')).read_text()
        self.assertNotIn('SyntheticNeverEmit', alert)
        self.assertNotIn('private/path', alert)

    def test_process_death_does_not_repeat_snapshot(self):
        def interrupt(work):
            self.snapshot(work)
            raise KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):
            self.monitor.check(VERSION, interrupt, self.observe)
        result = self.poll()
        self.assertEqual(result['reason'], 'interrupted_requires_reconciliation')
        self.assertEqual(self.calls, ['snapshot'])
        self.assertEqual(self.poll(), result)
        self.assertEqual(len(list((self.root / 'alerts').glob('*.json'))), 1)

    def test_source_gate_stops_consumer_effects_until_acceptance(self):
        consumed = []
        def consume(pin=PIN):
            tripwire.require_accepted(self.root, VERSION, pin)
            consumed.append(1)
        with self.assertRaises(tripwire.IntakeBlocked):
            consume()
        self.assertEqual(consumed, [])
        self.poll()
        self.monitor.accept(VERSION)
        consume()
        self.assertEqual(consumed, [1])
        with self.assertRaises(tripwire.IntakeBlocked):
            consume('b' * 64)
        self.assertEqual(consumed, [1])

    def test_missing_or_modified_receipt_fails_closed(self):
        result = self.poll()
        self.monitor.accept(VERSION)
        receipt = self.root / 'snapshots' / result['id'] / 'observation.json'
        receipt.write_text('{}')
        with self.assertRaises(tripwire.IntakeBlocked):
            tripwire.require_accepted(self.root, VERSION, PIN)

    def test_changed_wire_type_is_only_a_candidate_difference(self):
        self.poll()
        self.monitor.accept(VERSION)
        self.report['records'][0]['fields'][0]['wire_type'] = 'u64le'
        result = self.poll(NEXT)
        self.assertEqual(result['differences']['changed_record_layouts'], 1)
        self.assertEqual(result['baseline']['version'], VERSION)
        self.assertEqual(result['candidate_types']['changed'], ['mail'])
        with self.assertRaises(tripwire.IntakeBlocked):
            tripwire.require_accepted(self.root, NEXT, PIN)

    def test_preflight_failure_has_one_durable_fixed_alert(self):
        tripwire.preflight_alert(self.root)
        tripwire.preflight_alert(self.root)
        alerts = list((self.root / 'alerts').glob('*.json'))
        self.assertEqual(len(alerts), 1)
        self.assertEqual(json.loads(alerts[0].read_text())['reason'], 'preflight_failed')
        self.assertFalse((self.root / 'accepted.json').exists())

    def test_operational_observer_pin_and_version_checks_use_only_synthetic_files(self):
        from lib.outlook_version_ports import OperationalPorts, source_binding
        app = Path(self.temp.name) / 'Synthetic.app'
        (app / 'Contents').mkdir(parents=True)
        info = app / 'Contents/Info.plist'
        info.write_bytes(plistlib.dumps({'CFBundleShortVersionString': VERSION['version'],
                                        'CFBundleVersion': VERSION['build']}))
        binary = Path(self.temp.name) / 'synthetic-observer'
        binary.write_text('#!/usr/bin/env python3\nimport json\nprint(' + repr(json.dumps(observation())) + ')\n')
        binary.chmod(0o700)
        digest = hashlib.sha256(binary.read_bytes()).hexdigest()
        pin, manifest = source_binding(binary, digest)
        self.assertEqual(manifest['observer_sha256'], digest)
        self.assertEqual(len(pin), 64)
        ports = OperationalPorts(Path(self.temp.name), app, binary, digest)
        store = Path(self.temp.name) / 'store.hxd'
        store.write_bytes(b'SyntheticOnly')
        self.assertEqual(ports.observe(store), observation())
        with self.assertRaises(tripwire.IntakeBlocked):
            source_binding(binary, '0' * 64)
        info.write_bytes(plistlib.dumps({'CFBundleShortVersionString': NEXT['version'],
                                        'CFBundleVersion': NEXT['build']}))
        with self.assertRaises(tripwire.IntakeBlocked):
            ports.observe(store)

    def test_operational_snapshot_honors_the_fixed_helper_directory_and_cleans_sidecars(self):
        import lib.outlook_version_ports as ports
        from lib.outlook_intake import COPY_HELPER, OUT
        root = Path(self.temp.name).resolve()
        app = root / 'Synthetic.app'
        (app / 'Contents').mkdir(parents=True)
        (app / 'Contents/Info.plist').write_bytes(plistlib.dumps({
            'CFBundleShortVersionString': VERSION['version'], 'CFBundleVersion': VERSION['build']}))
        observer = root / 'observer'
        observer.write_bytes(b'synthetic observer')
        digest = hashlib.sha256(observer.read_bytes()).hexdigest()
        _, manifest = ports.source_binding(observer, digest)
        repo = root / 'repo'
        for name in manifest['sources']:
            target = repo / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes((ports.ROOT / name).read_bytes())
        helper = root / COPY_HELPER
        helper.parent.mkdir(parents=True)
        helper.write_text('#!' + sys.executable + '\n' +
                          'from pathlib import Path\nimport stat\n' +
                          'work = Path.cwd()\n' +
                          'assert work.parent == Path(' + repr(str(repo / OUT)) + ')\n' +
                          'assert work.name.startswith("work-")\n' +
                          'assert stat.S_IMODE(work.stat().st_mode) == 0o700\n' +
                          'Path("store.hxd").write_bytes(b"synthetic store")\n' +
                          'Path("Files").mkdir()\n' +
                          'Path("Files/sidecar").write_bytes(b"synthetic sidecar")\n')
        helper.chmod(0o700)
        destination = root / 'receipt'
        destination.mkdir(mode=0o700)
        with patch.object(ports, 'ROOT', repo):
            adapter = ports.OperationalPorts(root, app, observer, digest)
            adapter.snapshot(destination)
        self.assertEqual((destination / 'store.hxd').read_bytes(), b'synthetic store')
        self.assertFalse((destination / 'Files').exists())
        self.assertEqual(list((repo / OUT).iterdir()), [])

    def test_cli_errors_do_not_echo_private_arguments(self):
        cli = Path(__file__).with_name('outlook-version-tripwire.py')
        result = subprocess.run([sys.executable, str(cli), 'SyntheticNeverEmit/private'], capture_output=True)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, b'')
        self.assertNotIn(b'SyntheticNeverEmit', result.stderr)

    def test_existing_observe_executable_drives_version_review_without_another_parser(self):
        from lib.outlook_version_ports import OperationalPorts, known_record_types
        root = Path(__file__).resolve().parents[1]
        spec = importlib.util.spec_from_file_location('existing_observation_fixtures', root / 'ops/hxstore-observe-selftest.py')
        fixtures = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(fixtures)
        binary = fixtures.BINARY
        if not binary.exists():
            subprocess.run(['cargo', 'build', '--quiet', '--locked', '--manifest-path',
                            str(root / 'vendor/hxprobe/Cargo.toml')], check=True, capture_output=True)
        app = Path(self.temp.name) / 'Synthetic.app'
        (app / 'Contents').mkdir(parents=True)
        (app / 'Contents/Info.plist').write_bytes(plistlib.dumps({
            'CFBundleShortVersionString': VERSION['version'], 'CFBundleVersion': VERSION['build']}))
        ports = OperationalPorts(Path(self.temp.name), app, binary, hashlib.sha256(binary.read_bytes()).hexdigest())
        monitor = tripwire.Tripwire(self.root, ports.source_pin, known_record_types())
        def synthetic_snapshot(work):
            (work / 'store.hxd').write_bytes(fixtures.store(fixtures.envelope(77, b'')))
        result = monitor.check(ports.version, synthetic_snapshot, ports.observe)
        self.assertEqual(result['status'], 'review_required')
        self.assertIn('object.class.004d', result['map']['records'])
        monitor.accept(ports.version)
        tripwire.require_accepted(self.root, ports.version, ports.source_pin)

    def test_accepted_version_status_is_truthful_and_does_not_repeat_work(self):
        self.poll()
        self.monitor.accept(VERSION)
        result = self.poll()
        self.assertEqual(result['status'], 'accepted')
        self.assertEqual(self.calls, ['snapshot', 'observe'])
        self.assertEqual(len(list((self.root / 'alerts').glob('*.json'))), 1)

    def test_operational_atlas_rejects_missing_scope_and_invalid_confidence(self):
        import lib.outlook_version_ports as ports
        atlas = json.loads((ports.ROOT / 'spec/outlook/hxstore-atlas.json').read_text())
        root = Path(self.temp.name)
        destination = root / 'spec/outlook/hxstore-atlas.json'
        destination.parent.mkdir(parents=True)
        validator = root / 'ops/hxstore-atlas.py'
        validator.parent.mkdir()
        validator.write_bytes((ports.ROOT / 'ops/hxstore-atlas.py').read_bytes())
        for mutate in (lambda a: a.pop('versioned_map'),
                       lambda a: a['entries'][0].update(confidence='unsupported')):
            candidate = copy.deepcopy(atlas)
            mutate(candidate)
            destination.write_text(json.dumps(candidate))
            with patch.object(ports, 'ROOT', root):
                with self.assertRaises((tripwire.IntakeBlocked, ValueError, KeyError)):
                    ports.known_record_types()

    def test_cli_check_exits_nonzero_until_reviewed_acceptance(self):
        root = Path(__file__).resolve().parents[1]
        spec = importlib.util.spec_from_file_location('version_tripwire_cli', root / 'ops/outlook-version-tripwire.py')
        cli = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cli)
        app = Path(self.temp.name) / 'Synthetic.app'
        (app / 'Contents').mkdir(parents=True)
        (app / 'Contents/Info.plist').write_bytes(plistlib.dumps({
            'CFBundleShortVersionString': VERSION['version'], 'CFBundleVersion': VERSION['build']}))
        binary = Path(self.temp.name) / 'synthetic-observer'
        binary.write_text('#!/usr/bin/env python3\nprint(' + repr(json.dumps(observation())) + ')\n')
        binary.chmod(0o700)
        arguments = ['--app', str(app), '--home', self.temp.name, '--state', str(self.root),
                     '--observer', str(binary), '--observer-sha256', hashlib.sha256(binary.read_bytes()).hexdigest()]
        for command, expected in [('check', 3), ('accept', 0), ('check', 0), ('gate', 0)]:
            output = io.StringIO()
            with patch.object(sys, 'argv', [str(root / 'ops/outlook-version-tripwire.py'), command, *arguments]), \
                 patch('lib.outlook_version_ports._snapshot', lambda home, work: self.snapshot(work)), \
                 redirect_stdout(output):
                self.assertEqual(cli.main(), expected)
            self.assertNotIn(self.temp.name, output.getvalue())


if __name__ == '__main__':
    unittest.main()
