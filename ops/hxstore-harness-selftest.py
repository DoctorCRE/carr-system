#!/usr/bin/env python3
"""Public harness commands, with generated synthetic stores only."""
import email.policy
import hashlib
import os
import shutil
from email.parser import BytesParser
import json
import importlib.util
import struct
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import zlib

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / 'ops/hxstore-harness.py'
BINARY = ROOT / 'vendor/hxprobe/target/debug/hxprobe'
spec = importlib.util.spec_from_file_location('observation_fixtures', ROOT / 'ops/hxstore-observe-selftest.py')
if spec is None or spec.loader is None:
    raise ImportError("observation fixture loader unavailable")
fixtures = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixtures)


class HarnessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        subprocess.run(['cargo', 'build', '--locked'], cwd=ROOT / 'vendor/hxprobe',
                       check=True, capture_output=True, timeout=180)

    def run_cli(self, *args):
        return subprocess.run([sys.executable, str(CLI), *map(str, args)],
                              capture_output=True, timeout=30)

    def test_study_diff_accepts_shared_copies_without_corpus_and_limits_disclosure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            before, after = root / 'PRIVATE-before.hxd', root / 'PRIVATE-after.hxd'
            secret = b'Subject: PRIVATE-TEXT\r\nTo: private@example.test\r\nATTACHMENT-BYTES'
            before.write_bytes(fixtures.store(fixtures.envelope(362, secret + b'0')))
            after.write_bytes(fixtures.store(fixtures.envelope(362, secret + b'1')))
            original = (before.read_bytes(), after.read_bytes())
            result = self.run_cli('study-diff', before, after, '--immutable-copies')
            self.assertEqual((result.returncode, result.stderr), (0, b''), result.stderr)
            report = json.loads(result.stdout)
            self.assertEqual(set(report), {'schema', 'interpretation', 'counts', 'coverage', 'records_before', 'records_after'})
            self.assertEqual(report['schema'], 'hxstore-study-diff/v1')
            self.assertEqual(report['interpretation'], 'candidates_only_no_causal_or_field_mapping_claim')
            self.assertEqual(report['counts']['changed_blocks'], 1)
            self.assertEqual(report['counts']['payload_changed_bytes'], 1)
            expected = [{'record_type': 'object.class.016a', 'record_id': f'64:0:{len(secret) + 21}'}]
            self.assertEqual(report['records_before'], expected)
            self.assertEqual(report['records_after'], expected)
            self.assertEqual(report['counts']['records_before'], 1)
            self.assertEqual(report['counts']['records_after'], 1)
            for forbidden in [secret, b'PRIVATE', b'private@example.test', str(root).encode(), secret.hex().encode(),
                              b'hits', b'match_counts', b'file_ranges', b'payload_ranges']:
                self.assertNotIn(forbidden, result.stdout + result.stderr)
            self.assertEqual((before.read_bytes(), after.read_bytes()), original)

    def test_study_diff_allows_unchanged_control_and_counts_only_file_header_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            before, after = root / 'before', root / 'after'
            before.write_bytes(fixtures.store(fixtures.envelope(362, b'CONTROL')))
            after.write_bytes(before.read_bytes())
            result = self.run_cli('study-diff', before, after, '--immutable-copies')
            self.assertEqual((result.returncode, result.stderr), (0, b''))
            report = json.loads(result.stdout)
            self.assertTrue(all(value == 0 for value in report['counts'].values()))
            self.assertEqual((report['records_before'], report['records_after']), ([], []))
            changed = bytearray(after.read_bytes())
            changed[40] = 1
            after.write_bytes(changed)
            result = self.run_cli('study-diff', before, after, '--immutable-copies')
            self.assertEqual(result.returncode, 0, result.stderr)
            counts = json.loads(result.stdout)['counts']
            self.assertEqual((counts['file_changed_ranges'], counts['file_changed_bytes'], counts['changed_blocks']), (1, 1, 0))

    def test_study_diff_preserves_old_new_partitions_and_excludes_unframed_candidates(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            before, after = root / 'before', root / 'after'
            before.write_bytes(fixtures.store(fixtures.envelope(247, b'DELETED')))
            after.write_bytes(before.read_bytes()[:64])
            result = self.run_cli('study-diff', before, after, '--immutable-copies')
            self.assertEqual(result.returncode, 0, result.stderr)
            report = json.loads(result.stdout)
            self.assertEqual(report['records_before'], [{'record_type': 'object.class.00f7', 'record_id': '64:0:27'}])
            self.assertEqual(report['records_after'], [])
            self.assertEqual(report['counts']['removed_blocks'], 1)
            reverse = self.run_cli('study-diff', after, before, '--immutable-copies')
            self.assertEqual(reverse.returncode, 0, reverse.stderr)
            self.assertEqual(json.loads(reverse.stdout)['records_after'], report['records_before'])
            self.assertEqual(json.loads(reverse.stdout)['counts']['added_blocks'], 1)
            after.write_bytes(fixtures.store(b'UNFRAMED'))
            result = self.run_cli('study-diff', before, after, '--immutable-copies')
            self.assertEqual(result.returncode, 0, result.stderr)
            report = json.loads(result.stdout)
            self.assertEqual(report['records_after'], [])
            self.assertEqual(report['counts']['unframed_payload_changed_bytes'], 8)
            self.assertEqual(report['coverage']['after']['exclusions'], {'unframed': 1})
            self.assertFalse(report['coverage']['complete'])

    def test_study_diff_excludes_damaged_spans_and_counts_unverified_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            before, after = root / 'PRIVATE-before', root / 'PRIVATE-after'
            good = fixtures.store(fixtures.envelope(362, b'CONTROL0'))
            bad = bytearray(fixtures.store(fixtures.envelope(247, b'PRIVATE'))[64:])
            bad[-1] ^= 1
            nested = fixtures.store(fixtures.envelope(113, struct.pack('<Q', 0x5d0245643b706a05)))[64:]
            unframed = fixtures.store(b'PRIVATE-UNFRAMED')[64:]
            invalid = fixtures.store(fixtures.envelope(261, b'PRIVATE')[:-1])[64:]
            original = good + bad + nested + unframed + invalid
            before.write_bytes(original)
            after.write_bytes(original)
            control = self.run_cli('study-diff', before, after, '--immutable-copies')
            self.assertEqual((control.returncode, control.stderr), (0, b''))
            report = json.loads(control.stdout)
            self.assertTrue(all(n == 0 for n in report['counts'].values()))
            self.assertFalse(report['coverage']['complete'])
            for side in ('before', 'after'):
                coverage = report['coverage'][side]
                self.assertEqual(coverage['verified_blocks'], 4)
                self.assertEqual(coverage['unverified_blocks'], 2)
                self.assertEqual(coverage['unframed_payload_bytes'], 16 + 26)
                self.assertEqual(coverage['exclusions'], {
                    'payload_crc_mismatch': 1, 'overlapping_candidate': 1,
                    'unframed': 1, 'invalid_object_bounds': 1})
            changed = bytearray(original)
            changed[len(good) + len(bad) - 1] ^= 2
            after.write_bytes(changed)
            result = self.run_cli('study-diff', before, after, '--immutable-copies')
            report = json.loads(result.stdout)
            self.assertEqual(report['counts']['unverified_file_changed_bytes'], 1)
            self.assertEqual(report['counts']['changed_blocks'], 0)
            self.assertEqual(report['records_before'], [])
            self.assertEqual(report['records_after'], [])
            changed_good = fixtures.store(fixtures.envelope(362, b'CONTROL1'))
            after.write_bytes(changed_good + changed[len(good):])
            result = self.run_cli('study-diff', before, after, '--immutable-copies')
            report = json.loads(result.stdout)
            self.assertEqual(report['counts']['changed_blocks'], 1)
            self.assertEqual(report['counts']['payload_changed_bytes'], 1)
            self.assertEqual(report['counts']['unverified_file_changed_bytes'], 1)
            expected = [{'record_type': 'object.class.016a', 'record_id': '64:0:28'}]
            self.assertEqual(report['records_before'], expected)
            self.assertEqual(report['records_after'], expected)
            self.assertFalse(report['coverage']['complete'])
            for secret in (b'PRIVATE', b'CONTROL', str(root).encode(), b'50524956415445'):
                self.assertNotIn(secret, result.stdout + result.stderr)
            self.assertEqual(before.read_bytes(), original)

    def test_study_diff_counts_unframed_mutations_and_truncated_exclusions(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            before, after = root / 'before', root / 'after'
            good = fixtures.store(fixtures.envelope(362, b'VERIFIED'))
            malformed = fixtures.envelope(261, b'PRIVATE')[:-1]
            for payload in (b'UNFRAMED0', malformed):
                before.write_bytes(good + fixtures.store(payload)[64:])
                after.write_bytes(good + fixtures.store(payload[:-1] + b'1')[64:])
                result = self.run_cli('study-diff', before, after, '--immutable-copies')
                self.assertEqual((result.returncode, result.stderr), (0, b''))
                report = json.loads(result.stdout)
                self.assertEqual(report['counts']['payload_changed_bytes'], 1)
                self.assertEqual(report['counts']['unframed_payload_changed_bytes'], 1)
                self.assertEqual(report['records_before'], [])
                self.assertEqual(report['records_after'], [])
                self.assertFalse(report['coverage']['complete'])
            before.write_bytes(good + fixtures.store(fixtures.envelope(247))[64:-1])
            after.write_bytes(before.read_bytes())
            result = self.run_cli('study-diff', before, after, '--immutable-copies')
            self.assertEqual((result.returncode, result.stderr), (0, b''))
            report = json.loads(result.stdout)
            self.assertTrue(all(n == 0 for n in report['counts'].values()))
            self.assertEqual(report['coverage']['before']['verified_blocks'], 1)
            self.assertEqual(report['coverage']['before']['exclusions'], {'truncated_payload': 1})
            self.assertFalse(report['coverage']['complete'])

    def test_study_diff_rejects_missing_assertion_and_bad_inputs_without_disclosure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            before, after = root / 'PRIVATE-before', root / 'PRIVATE-after'
            before.write_bytes(fixtures.store(fixtures.envelope(362, b'PRIVATE')))
            after.write_bytes(before.read_bytes())
            result = self.run_cli('study-diff', before, after)
            self.assertEqual((result.returncode, result.stdout, result.stderr), (2, b'', b'study harness input rejected\n'))
            for data in (b'PRIVATE@example.test', b''):
                after.write_bytes(data)
                result = self.run_cli('study-diff', before, after, '--immutable-copies')
                self.assertEqual((result.returncode, result.stdout, result.stderr), (2, b'', b'study harness input rejected\n'))
            result = self.run_cli('study-diff', before, root / 'PRIVATE-absent', '--immutable-copies')
            self.assertEqual((result.returncode, result.stdout, result.stderr), (2, b'', b'study harness input rejected\n'))

    def test_study_diff_hides_worker_failures_and_rejects_changed_copies(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cli = root / 'ops/hxstore-harness.py'
            cli.parent.mkdir()
            shutil.copyfile(CLI, cli)
            worker = root / 'vendor/hxprobe/target/debug/hxprobe'
            worker.parent.mkdir(parents=True)
            before, after = root / 'before', root / 'after'
            before.write_bytes(fixtures.store(fixtures.envelope(362, b'PRIVATE')))
            after.write_bytes(before.read_bytes())
            for mode in ('failure', 'mutation', 'timeout'):
                if mode == 'timeout':
                    # Exercise the public error path without a five-minute test wait.
                    cli.write_text(CLI.read_text().replace('timeout=300', 'timeout=0.01'))
                    body = 'import time; time.sleep(1)'
                elif mode == 'mutation':
                    body = "from pathlib import Path; p = Path(sys.argv[3]); p.write_bytes(p.read_bytes() + b'changed')"
                else:
                    body = 'sys.exit(2)'
                worker.write_text(f'#!{sys.executable}\nimport sys\n'
                                  "print('PRIVATE@example.test')\nprint('PRIVATE/PATH', file=sys.stderr)\n" + body + '\n')
                worker.chmod(0o700)
                result = subprocess.run([sys.executable, str(cli), 'study-diff', str(before), str(after),
                                         '--immutable-copies'], capture_output=True, timeout=10)
                self.assertEqual((result.returncode, result.stdout, result.stderr),
                                 (2, b'', b'study harness input rejected\n'), mode)

    def test_corpus_round_trips_every_declared_header_and_body(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self.run_cli('corpus', '--account', 'synthetic-fixture@example.test', '--out', tmp)
            self.assertEqual(result.returncode, 0, result.stderr)
            manifest = json.loads((Path(tmp) / 'manifest.json').read_bytes())
            self.assertEqual(manifest['schema'], 'hxstore-corpus/v1')
            self.assertEqual(len(manifest['cases']), 20)
            sizes, formats, markers = set(), set(), set()
            for case in manifest['cases']:
                message = BytesParser(policy=email.policy.default).parsebytes(
                    (Path(tmp) / case['file']).read_bytes())
                self.assertEqual(set(message.keys()), set(case['headers']))
                for key, value in case['headers'].items():
                    self.assertEqual(str(message[key]), value)
                self.assertEqual(message.get_payload(decode=True).decode(), case['body'])
                self.assertEqual(len(case['body'].encode()), case['body_length'])
                self.assertEqual(len(message.get_all('To', [])), int(case['to_count'] > 0))
                self.assertTrue(all(marker in case['subject'] or marker in case['body']
                                    for marker in case['markers']))
                markers.update(case['markers'])
                sizes.add(case['body_length'])
                formats.add(case['format'])
            self.assertEqual(sizes, {0, 1, 7, 31, 127, 255, 1023, 4095, 16384, 65536})
            self.assertEqual(formats, {'plain', 'html'})
            self.assertEqual(len(markers), sum(len(c['markers']) for c in manifest['cases']))

    def test_diff_requires_opt_in_and_all_markers_before_any_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertEqual(self.run_cli('corpus', '--account', 'fixture@example.test', '--out', root / 'corpus').returncode, 0)
            manifest = root / 'corpus/manifest.json'
            before, after = root / 'before.hxd', root / 'after.hxd'
            before.write_bytes(fixtures.store(fixtures.envelope(362, b'')))
            after.write_bytes(fixtures.store(fixtures.envelope(362, b'SECRET-DO-NOT-PRINT')))
            for extra in ([], ['--synthetic-store']):
                result = self.run_cli('diff', before, after, '--manifest', manifest, *extra)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, b'')
                self.assertEqual(result.stderr, b'synthetic harness input rejected\n')
                self.assertNotIn(b'SECRET', result.stderr)

    def test_diff_rejects_one_missing_campaign_marker_with_other_cases_present(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertEqual(self.run_cli('corpus', '--account', 'fixture@example.test', '--out', root / 'corpus').returncode, 0)
            manifest = root / 'corpus/manifest.json'
            markers = [m for case in json.loads(manifest.read_bytes())['cases'] for m in case['markers']]
            before, after = root / 'before', root / 'after'
            before.write_bytes(fixtures.store(fixtures.envelope(362, b'')))
            after.write_bytes(fixtures.store(fixtures.envelope(362, b'|'.join(m.encode() for m in markers[1:]))))
            result = self.run_cli('diff', before, after, '--manifest', manifest, '--synthetic-store')
            self.assertEqual((result.returncode, result.stdout, result.stderr),
                             (2, b'', b'synthetic harness input rejected\n'))

    def test_diff_reports_offsets_encodings_and_changes_without_store_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertEqual(self.run_cli('corpus', '--account', 'fixture@example.test', '--out', root / 'corpus').returncode, 0)
            manifest = root / 'corpus/manifest.json'
            all_markers = [m for c in json.loads(manifest.read_bytes())['cases'] for m in c['markers']]
            marker_bytes = b'|'.join(m.encode('utf-16le') for m in all_markers)
            # 2000-02-29 12:34:56 UTC, independently worked epoch values.
            values = (struct.pack('<Q', 125963012960000000) + struct.pack('>Q', 951827696000) +
                      struct.pack('<I', 65536) + struct.pack('>I', 65536))
            before, after = root / 'before.hxd', root / 'after.hxd'
            before.write_bytes(fixtures.store(fixtures.envelope(362, b'')))
            after.write_bytes(fixtures.store(fixtures.envelope(362, values + marker_bytes)))
            result = self.run_cli('diff', before, after, '--manifest', manifest, '--synthetic-store')
            self.assertEqual(result.returncode, 0, result.stderr)
            report = json.loads(result.stdout)
            self.assertTrue(report['file_ranges'])
            self.assertTrue(report['blocks'][0]['payload_ranges'])
            hits = [h for h in report['hits'] if h['case_id'] == 'case-00' and h['snapshot'] == 'after']
            time = next(h for h in hits if h['field'] == 'date' and h['encoding'] == 'filetime-le')
            self.assertEqual((time['block_offset'], time['record_type'], time['payload_offset'], time['record_offset']),
                             (64, 'object.class.016a', 20, 20))
            self.assertTrue(any(h['encoding'] == 'unix-ms-be' and h['payload_offset'] == 28 for h in hits))
            self.assertTrue(any(h['field'] == 'subject' and h['encoding'] == 'utf-16le' for h in hits))
            for marker in all_markers:
                self.assertNotIn(marker.encode(), result.stdout + result.stderr)
            self.assertNotIn(b'fixture@example.test', result.stdout + result.stderr)
            integers = [h for h in report['hits'] if h['case_id'] == 'case-09' and h['field'] == 'body_length']
            self.assertTrue(any(h['encoding'] == 'u32le' and h['payload_offset'] == 36 for h in integers))
            self.assertTrue(any(h['encoding'] == 'u32be' and h['payload_offset'] == 40 for h in integers))
            self.assertEqual(time['object_offset'], 10)
            corrupt = bytearray(after.read_bytes())
            corrupt[-1] ^= 1
            after.write_bytes(corrupt)
            rejected = self.run_cli('diff', before, after, '--manifest', manifest, '--synthetic-store')
            self.assertEqual((rejected.returncode, rejected.stdout, rejected.stderr),
                             (2, b'', b'synthetic harness input rejected\n'))

    def test_confirmed_hypothesis_increases_rebuilt_observe_coverage(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertEqual(self.run_cli('corpus', '--account', 'fixture@example.test', '--out', root / 'corpus').returncode, 0)
            manifest = root / 'corpus/manifest.json'
            hypotheses = root / 'hypotheses.json'
            hypothesis = {'field_id': 'object.class.016a.offset.10', 'record_type': 'object.class.016a',
                          'offset': 10, 'encoding': 'u64le', 'length': 8,
                          'confidence': 'candidate', 'evidence': ['case-00'], 'manifest_field': 'date'}
            document = {'schema': 'hxstore-decode-hypotheses/v1',
                        'corpus_sha256': hashlib.sha256(manifest.read_bytes()).hexdigest(),
                        'hypotheses': [hypothesis]}
            crate = root / 'vendor/hxprobe'
            shutil.copytree(ROOT / 'vendor/hxprobe', crate, ignore=shutil.ignore_patterns('target', '.git'))
            (root / 'ops').mkdir()
            shutil.copyfile(ROOT / 'ops/hxstore-atlas.py', root / 'ops/hxstore-atlas.py')
            atlas = root / 'spec/outlook/hxstore-atlas.json'
            atlas.parent.mkdir(parents=True)
            fixture = root / 'synthetic.hxd'
            fixture.write_bytes(fixtures.store(fixtures.envelope(362, bytes.fromhex('8877665544332211'))))
            env = dict(os.environ, CARGO_TARGET_DIR=str(ROOT / 'vendor/hxprobe/target/hypothesis-selftest'))
            reports = []
            for confidence in ['candidate', 'confirmed']:
                hypothesis['confidence'] = confidence
                hypotheses.write_text(json.dumps(document))
                result = self.run_cli('hypotheses', '--manifest', manifest, '--hypotheses', hypotheses,
                                      '--atlas', atlas if atlas.exists() else ROOT / 'spec/outlook/hxstore-atlas.json')
                self.assertEqual(result.returncode, 0, result.stderr)
                atlas.write_bytes(result.stdout)
                build = subprocess.run(['cargo', 'build', '--locked'], cwd=crate, env=env,
                                       capture_output=True, timeout=180)
                self.assertEqual(build.returncode, 0, build.stderr)
                result = subprocess.run([str(Path(env['CARGO_TARGET_DIR']) / 'debug/hxprobe'), 'observe', str(fixture)],
                                        capture_output=True, timeout=10)
                self.assertEqual(result.returncode, 0, result.stderr)
                reports.append(json.loads(result.stdout))
            self.assertEqual(reports[0]['coverage']['decoded_bytes'], 20)
            self.assertEqual(reports[1]['coverage']['decoded_bytes'], 28)
            self.assertEqual(reports[1]['coverage']['decoded_byte_percent'], 100)
            self.assertEqual(reports[1]['records'][0]['fields'][-1]['field_id'], hypothesis['field_id'])
            self.assertIsNone(reports[1]['records'][0]['wire_field_ids'])
            self.assertNotIn(b'8877665544332211', result.stdout)
            document['hypotheses'] += [
                dict(hypothesis, field_id='object.class.016a.offset.12', offset=12),
                dict(hypothesis, field_id='object.class.016a.offset.20', offset=20, encoding='utf-16le', length=2),
                dict(hypothesis, field_id='object.class.016a.offset.30', offset=30),
            ]
            hypotheses.write_text(json.dumps(document))
            imported = self.run_cli('hypotheses', '--manifest', manifest, '--hypotheses', hypotheses, '--atlas', atlas)
            self.assertEqual(imported.returncode, 0, imported.stderr)
            atlas.write_bytes(imported.stdout)
            subprocess.run(['cargo', 'build', '--locked'], cwd=crate, env=env, check=True,
                           capture_output=True, timeout=180)
            fixture.write_bytes(fixtures.store(fixtures.envelope(362, bytes.fromhex('8877665544332211aabb00d8'))))
            result = subprocess.run([str(Path(env['CARGO_TARGET_DIR']) / 'debug/hxprobe'), 'observe', str(fixture)],
                                    capture_output=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            bounded = json.loads(result.stdout)
            self.assertEqual(bounded['coverage']['verified_payload_bytes'], 32)
            self.assertEqual(bounded['coverage']['decoded_bytes'], 30)
            self.assertEqual(len(bounded['records'][0]['fields']), 8)
            fixture.write_bytes(fixtures.store(fixtures.envelope(362, b'abcd')))
            result = subprocess.run([str(Path(env['CARGO_TARGET_DIR']) / 'debug/hxprobe'), 'observe', str(fixture)],
                                    capture_output=True, timeout=10)
            self.assertEqual(json.loads(result.stdout)['coverage']['decoded_bytes'], 20)

    def test_manifest_cannot_remove_or_replace_markers(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.run_cli('corpus', '--account', 'fixture@example.test', '--out', root / 'corpus')
            manifest = root / 'corpus/manifest.json'
            original = manifest.read_bytes()
            for mutation in ('empty_cases', 'empty_markers', 'short_marker', 'wrong_schema'):
                document = json.loads(original)
                if mutation == 'empty_cases':
                    document['cases'] = []
                elif mutation == 'empty_markers':
                    document['cases'][0]['markers'] = []
                elif mutation == 'short_marker':
                    document['cases'][0]['markers'] = ['a']
                else:
                    document['schema'] = 'other'
                manifest.write_text(json.dumps(document))
                result = self.run_cli('diff', root / 'absent-before', root / 'absent-after',
                                      '--manifest', manifest, '--synthetic-store')
                self.assertEqual((result.returncode, result.stdout, result.stderr),
                                 (2, b'', b'synthetic harness input rejected\n'))

    def test_corpus_rejects_other_addresses_injection_and_overwriting(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for address in ['someone@invalid.example.test', 'fixture@example.test\r\nBcc: someone@invalid.example.test']:
                result = self.run_cli('corpus', '--account', address, '--out', root / 'invalid')
                self.assertEqual(result.returncode, 2)
                self.assertFalse((root / 'invalid').exists())
            target = root / 'existing'
            target.mkdir()
            (target / 'keep').write_bytes(b'keep')
            self.assertEqual(self.run_cli('corpus', '--account', 'fixture@example.test', '--out', target).returncode, 2)
            self.assertEqual(list(target.iterdir()), [target / 'keep'])
            self.assertEqual((target / 'keep').read_bytes(), b'keep')

    def test_diff_includes_old_and_new_records_and_header_only_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.run_cli('corpus', '--account', 'fixture@example.test', '--out', root / 'corpus')
            manifest = root / 'corpus/manifest.json'
            markers = b'|'.join(m.encode() for c in json.loads(manifest.read_bytes())['cases'] for m in c['markers'])
            before, after = root / 'before.hxd', root / 'after.hxd'
            before.write_bytes(fixtures.store(fixtures.envelope(362, markers) + fixtures.envelope(247, b'OLD')))
            after.write_bytes(fixtures.store(fixtures.envelope(362, markers)))
            result = self.run_cli('diff', before, after, '--manifest', manifest, '--synthetic-store')
            self.assertEqual(result.returncode, 0, result.stderr)
            change = json.loads(result.stdout)['blocks'][0]
            self.assertTrue(any(r['record_type'] == 'object.class.00f7' for r in change['records_before']))
            self.assertEqual(change['records_after'], [])
            before.write_bytes(after.read_bytes())
            data = bytearray(after.read_bytes())
            data[64 + 32] = 1
            struct.pack_into('<I', data, 64 + 4, zlib.crc32(data[64 + 8:]))
            struct.pack_into('<I', data, 64, zlib.crc32(data[64 + 4:64 + 32]))
            after.write_bytes(data)
            result = self.run_cli('diff', before, after, '--manifest', manifest, '--synthetic-store')
            change = json.loads(result.stdout)['blocks'][0]
            self.assertEqual(change['payload_ranges'], [])
            self.assertTrue(change['file_ranges'])

    def test_hypotheses_reject_unbound_or_invalid_evidence_without_partial_atlas(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.run_cli('corpus', '--account', 'fixture@example.test', '--out', root / 'corpus')
            manifest = root / 'corpus/manifest.json'
            path = root / 'hypotheses.json'
            valid = {'field_id': 'object.class.016a.offset.10', 'record_type': 'object.class.016a',
                     'offset': 10, 'encoding': 'u64le', 'length': 8,
                     'confidence': 'confirmed', 'evidence': ['case-00'], 'manifest_field': 'date'}
            mutations = [('offset', 0), ('length', 7), ('confidence', 'proven'), ('evidence', []),
                         ('evidence', ['case-99']), ('manifest_field', 'absent'), ('manifest_field', 'markers'), ('encoding', 'guess'), ('field_id', 'invented.wire.id')]
            for key, value in mutations + [('corpus_sha256', '0' * 64)]:
                h = dict(valid)
                digest = hashlib.sha256(manifest.read_bytes()).hexdigest()
                if key == 'corpus_sha256':
                    digest = value
                else:
                    h[key] = value
                path.write_text(json.dumps({'schema': 'hxstore-decode-hypotheses/v1',
                                            'corpus_sha256': digest, 'hypotheses': [h]}))
                result = self.run_cli('hypotheses', '--manifest', manifest, '--hypotheses', path,
                                      '--atlas', ROOT / 'spec/outlook/hxstore-atlas.json')
                self.assertEqual((result.returncode, result.stdout, result.stderr),
                                 (2, b'', b'synthetic harness input rejected\n'))


if __name__ == '__main__':
    unittest.main()
