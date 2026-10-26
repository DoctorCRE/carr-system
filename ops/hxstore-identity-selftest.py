#!/usr/bin/env python3
"""Compare selected identity candidates through hxprobe using synthetic stores only."""
import importlib.util
import json
from pathlib import Path
import struct
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
CRATE = ROOT / 'vendor/hxprobe'
BINARY = CRATE / 'target/debug/hxprobe'
spec = importlib.util.spec_from_file_location('observe_fixture', ROOT / 'ops/hxstore-observe-selftest.py')
assert spec is not None and spec.loader is not None
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)
SECRET = b'SyntheticIdentityNeverEmitPayload'


def object_bytes(key=9876543210987654321, kind=0x00ca, text=SECRET):
    data = bytearray(46)
    struct.pack_into('<H', data, 0, kind)
    for offset in (10, 30, 38):
        struct.pack_into('<Q', data, offset, key)
    return data + text


def metadata(key=9876543210987654321, tail=b'7' * 46, text=SECRET):
    data = object_bytes(key, kind=0x00bf, text=text)
    data += b'\x00\x09\x00\x2e\x00' + tail + bytes(8)
    return struct.pack('<HHIH', 5, 0x02c8, len(data) + 10, 0) + data


class IdentityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        subprocess.run(['cargo', 'build', '--locked'], cwd=CRATE, check=True, timeout=180)

    def compare(self, before, after, coordinates=('64:0', '64:0'), flag='--immutable-copies'):
        with tempfile.TemporaryDirectory() as tmp:
            paths = [Path(tmp) / f'NeverEmitPath-{i}.hxd' for i in range(2)]
            for path, data in zip(paths, (before, after)):
                path.write_bytes(data)
            result = subprocess.run([str(BINARY), 'identity-compare', flag,
                                     str(paths[0]), coordinates[0], str(paths[1]), coordinates[1]],
                                    capture_output=True, timeout=10)
            for path, data in zip(paths, (before, after)):
                self.assertEqual(path.read_bytes(), data)
            for value in (SECRET, b'NeverEmitPath', b'9876543210987654321', b'7' * 46, (b'7' * 46).hex().encode(),
                          struct.pack('<Q', 9876543210987654321)):
                self.assertNotIn(value, result.stdout + result.stderr)
            return result

    def test_same_candidate_key_with_changed_payload_is_equal(self):
        result = self.compare(fixture.store(object_bytes()), fixture.store(object_bytes(text=b'changed')))
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report['schema'], 'hxstore-identity-comparison/v1')
        self.assertEqual(report['interpretation'], 'candidates_only_no_logical_identity_claim')
        self.assertEqual(report['object_class'], '00ca')
        self.assertTrue(report['local_key_equal'])
        self.assertIsNone(report['metadata_tail_equal'])
        self.assertEqual(report['selectors']['local_key']['object_offsets'], [10, 30, 38])
        self.assertEqual(result.stderr, b'')

    def test_metadata_tail_comparison_is_independent_of_repeated_key(self):
        result = self.compare(fixture.store(metadata()), fixture.store(metadata(tail=b'8' * 46)),
                              coordinates=('64:10', '64:10'))
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report['object_class'], '00bf')
        self.assertTrue(report['local_key_equal'])
        self.assertFalse(report['metadata_tail_equal'])
        self.assertEqual(report['selectors']['metadata_tail'],
                         {'offset_from_object_end': 59, 'length': 51})

    def rejected(self, result):
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, b'')
        self.assertEqual(result.stderr, b'identity comparison input rejected\n')

    def test_distinct_keys_remain_distinct_with_identical_payload(self):
        result = self.compare(fixture.store(object_bytes()), fixture.store(object_bytes(key=55)))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(json.loads(result.stdout)['local_key_equal'])

    def test_metadata_tail_can_stay_equal_when_local_key_changes(self):
        result = self.compare(fixture.store(metadata()), fixture.store(metadata(key=55)),
                              coordinates=('64:10', '64:10'))
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertFalse(report['local_key_equal'])
        self.assertTrue(report['metadata_tail_equal'])

    def test_unframed_metadata_tail_has_no_equality_claim(self):
        before = metadata()
        after = metadata().replace(b'\x00\x09\x00\x2e\x00', b'\x00\x09\x00\x2f\x00')
        result = self.compare(fixture.store(before), fixture.store(after),
                              coordinates=('64:10', '64:10'))
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertTrue(report['local_key_equal'])
        self.assertIsNone(report['metadata_tail_equal'])

    def test_bad_repetition_zero_signature_and_truncated_candidates_are_rejected(self):
        wrong_key = object_bytes()
        struct.pack_into('<Q', wrong_key, 30, 1)
        bad_signature = object_bytes()
        bad_signature[2] = 1
        for candidate in (wrong_key, bad_signature, object_bytes(key=0), object_bytes(kind=77),
                          object_bytes()[:45]):
            with self.subTest(candidate_length=len(candidate)):
                self.rejected(self.compare(fixture.store(object_bytes()), fixture.store(candidate)))

    def test_metadata_requires_its_exact_bounded_envelope(self):
        bad_tag = bytearray(metadata())
        bad_tag[2] ^= 1
        bad_guard = bytearray(metadata())
        bad_guard[8] = 1
        bad_length = bytearray(metadata())
        struct.pack_into('<I', bad_length, 4, len(bad_length) + 1)
        short_length = bytearray(metadata())
        struct.pack_into('<I', short_length, 4, 55)
        for candidate in (bad_tag, bad_guard, bad_length, short_length):
            with self.subTest(candidate_length=len(candidate)):
                self.rejected(self.compare(fixture.store(metadata()), fixture.store(candidate),
                                           coordinates=('64:10', '64:10')))

    def test_different_classes_are_not_compared(self):
        self.rejected(self.compare(fixture.store(object_bytes()), fixture.store(metadata()),
                                   coordinates=('64:0', '64:10')))

    def test_coordinates_are_checked_without_echoing_input(self):
        good = fixture.store(object_bytes())
        for coord in ('NeverEmitPath', '-1:0', '64:+0', '64:0:1', '64:', ':0',
                      '64:99999', '65:0', '999999999999999999999999999999999:0'):
            with self.subTest(coordinate=coord):
                self.rejected(self.compare(good, good, coordinates=('64:0', coord)))

    def test_selected_unverified_block_or_unsupported_header_is_rejected(self):
        good = fixture.store(object_bytes())
        corrupt = bytearray(good)
        corrupt[-1] ^= 1
        unknown_version = bytearray(good)
        unknown_version[8] = ord('z')
        for bad in (corrupt, unknown_version, b'', bytes(64)):
            with self.subTest(input_length=len(bad)):
                self.rejected(self.compare(good, bad))

    def test_unrelated_bad_block_does_not_disqualify_selected_verified_block(self):
        good = fixture.store(object_bytes())
        bad = bytearray(good[64:])
        bad[-1] ^= 1
        result = self.compare(good, good + bad)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(json.loads(result.stdout)['local_key_equal'])

    def test_nested_block_signature_cannot_select_an_overlapping_candidate(self):
        inner = fixture.store(object_bytes())[64:]
        outer = fixture.store(b'prefix' + inner)
        offset = outer.index(inner)
        self.rejected(self.compare(fixture.store(object_bytes()), outer,
                                   coordinates=('64:0', f'{offset}:0')))

    def test_immutable_assertion_and_argument_count_are_required(self):
        good = fixture.store(object_bytes())
        self.rejected(self.compare(good, good, flag='NeverEmitPath'))
        for args in ([], ['--immutable-copies'], ['NeverEmitPath'] * 9):
            result = subprocess.run([str(BINARY), 'identity-compare', *args], capture_output=True, timeout=10)
            self.rejected(result)


if __name__ == '__main__':
    unittest.main()
