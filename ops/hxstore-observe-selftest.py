#!/usr/bin/env python3
"""Exercise the real count-only CLI with synthetic bytes, never a private store."""
import json
from pathlib import Path
import struct
import subprocess
import tempfile
import unittest
import zlib

ROOT = Path(__file__).resolve().parents[1]
CRATE = ROOT / 'vendor/hxprobe'
BINARY = CRATE / 'target/debug/hxprobe'
SECRET = b'SyntheticNeverEmitPayload987654321'


def envelope(kind, body=SECRET):
    obj = struct.pack('<H', kind) + bytes(8) + body
    return struct.pack('<HHIH', 5, 0x04c2, len(obj) + 10, 0) + obj


def store(data, compressed=None, inflated=None):
    if compressed is None:
        extra = len(data) - 15
        compressed = (bytes([len(data) << 4]) + data if extra < 0 else
                      b'\xf0' + b'\xff' * (extra // 255) + bytes([extra % 255]) + data)
    block = bytearray(40) + compressed
    struct.pack_into('<QIIII', block, 8, 0x5d0245643b706a05, 8,
                     len(compressed), len(data) if inflated is None else inflated, 4)
    struct.pack_into('<I', block, 4, zlib.crc32(block[8:]))
    struct.pack_into('<I', block, 0, zlib.crc32(block[4:32]))
    header = bytearray(64)
    header[:8] = b'Nostromo'
    header[8] = ord('i')
    struct.pack_into('<Q', header, 56, 4096)
    return header + block


class ObservationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        subprocess.run(['cargo', 'build', '--locked'], cwd=CRATE, check=True,
                       capture_output=True, timeout=180)

    def observe(self, data, *args):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'SyntheticNeverEmitPath.hxd'
            path.write_bytes(data)
            result = subprocess.run([str(BINARY), 'observe', str(path), *args],
                                    capture_output=True, timeout=10)
            self.assertNotIn(str(path).encode(), result.stdout + result.stderr)
            self.assertEqual(path.read_bytes(), data)
            return result

    def test_counts_and_coverage_are_content_independent(self):
        data = envelope(77) * 2 + envelope(999, SECRET * 2)
        result = self.observe(store(data))
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(result.stderr, b'')
        self.assertEqual(report['blocks'], [{'block_type': 8, 'occurrences': 1,
                                            'payload_bytes': len(data)}])
        records = report['records']
        self.assertEqual([r['record_type'] for r in records], ['object.class.004d', 'object.class.03e7'])
        self.assertEqual([r['occurrences'] for r in records], [2, 1])
        self.assertEqual([r['decoded_bytes'] for r in records], [40, 20])
        self.assertEqual(sum(r['total_bytes'] for r in records), len(data))
        self.assertEqual(records[0]['decoded_byte_percent'], 100 * 40 / (2 * len(envelope(77))))
        self.assertIsNone(records[0]['decoded_field_id_percent'])
        self.assertIsNone(records[0]['wire_field_ids'])
        self.assertEqual([u['occurrences'] for u in report['unknown_fields']], [2, 1])
        self.assertTrue(all(u['field_id'] is None for u in report['unknown_fields']))
        self.assertEqual(len(records[0]['fields']), 6)
        for marker in (SECRET, SECRET.decode().encode('utf-16le'), SECRET.hex().encode(),
                       b'SyntheticNeverEmitPath'):
            self.assertNotIn(marker, result.stdout + result.stderr)
        changed = self.observe(store(data.replace(SECRET, b'X' * len(SECRET))))
        self.assertEqual(result.stdout, changed.stdout)

    def test_corrupt_block_does_not_abort_or_dilute_verified_coverage(self):
        good = store(envelope(77, b''))
        corrupt = bytearray(store(SECRET)[64:])
        corrupt[-1] ^= 1
        result = self.observe(good + corrupt + good[64:])
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report['coverage'], {
            'block_signatures': 3, 'verified_blocks': 2, 'unverified_blocks': 1,
            'undecodable_blocks': 0, 'verified_payload_bytes': 40,
            'decoded_bytes': 40, 'decoded_byte_percent': 100,
        })
        self.assertEqual(report['issues'], [
            {'stage': 'block', 'reason_code': 'payload_crc_mismatch', 'block_ordinal': 2},
        ])
        self.assertEqual(report['issue_counts'], [
            {'stage': 'block', 'reason_code': 'payload_crc_mismatch', 'occurrences': 1},
        ])
        self.assertEqual(report['records'][0]['occurrences'], 2)

    def test_rejects_inexact_lz4_without_partial_output(self):
        cases = [
            store(b'hello', b'\x50helloTRAILING'),
            store(b'xxxxx', b'\x1fx\x01\x00\x00'),
        ]
        for data in cases:
            with self.subTest(data_length=len(data)):
                result = self.observe(data)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, b'')
                self.assertEqual(result.stderr, b'{"error":"observation_failed"}\n')

    def test_unknown_frequency_counts_only_records_with_unframed_bytes(self):
        data = envelope(77, b'') + envelope(77) + envelope(78)
        report = json.loads(self.observe(store(data)).stdout)
        self.assertEqual([(r['record_type'], r['occurrences']) for r in report['unknown_fields']],
                         [('object.class.004d', 1), ('object.class.004e', 1)])

    def test_mail_regions_and_envelopes_do_not_double_count(self):
        anchor = 'IPM.Note'.encode('utf-16le')
        payload = b'prefix' + anchor + SECRET + envelope(77, anchor + SECRET) + anchor + SECRET
        report = json.loads(self.observe(store(payload)).stdout)
        records = {r['record_type']: r for r in report['records']}
        self.assertEqual(records['mail']['occurrences'], 2)
        self.assertEqual(records['mail']['decoded_bytes'], 32)
        self.assertEqual(records['object.class.004d']['decoded_bytes'], 20)
        self.assertEqual(records['unframed']['total_bytes'], 6)
        self.assertEqual(sum(r['total_bytes'] for r in records.values()), len(payload))
        self.assertEqual([r['record_type'] for r in report['unknown_fields']],
                         ['mail', 'object.class.004d', 'unframed'])
        self.assertEqual(report['unknown_fields'][0]['field_id'], None)

    def test_malformed_inputs_fail_closed_with_fixed_errors(self):
        valid = store(envelope(77))
        header_crc = bytearray(valid)
        header_crc[64] ^= 1
        body_crc = bytearray(valid)
        body_crc[-1] ^= 1
        unknown_version = bytearray(valid)
        unknown_version[8] = ord('z')
        cases = [
            b'SyntheticNeverEmitBadMagic' + bytes(100), bytes(8), header_crc, body_crc,
            unknown_version, valid[:-1], store(b'x', b'\x10x\x99\x00', 5),
            store(envelope(77)[:-1]), store(struct.pack('<HHIH', 5, 1218, 19, 0)),
            store(SECRET) + store(envelope(77))[64:-1],
        ]
        for data in cases:
            with self.subTest(length=len(data)):
                result = self.observe(data)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, b'')
                self.assertEqual(result.stderr, b'{"error":"observation_failed"}\n')

    def test_empty_observation_has_no_invented_coverage(self):
        report = json.loads(self.observe(store(SECRET)[:64]).stdout)
        self.assertEqual(report, {'schema': 'hxprobe-observation/v1', 'blocks': [],
                                  'records': [], 'unknown_fields': []})

    def test_argument_and_io_errors_never_echo_arguments(self):
        for args in ([], ['/SyntheticNeverEmitMissingPath'], ['a', 'SyntheticNeverEmitArgument']):
            result = subprocess.run([str(BINARY), 'observe', *args], capture_output=True)
            self.assertEqual(result.returncode, 2)
            self.assertEqual(result.stdout, b'')
            self.assertEqual(result.stderr, b'{"error":"observation_failed"}\n')

    def test_fixture_strings_and_byte_runs_never_reach_either_stream(self):
        strings = ['NoEmitSubjectFixture54321', 'nobody-fixture@example.test',
                   '<NoEmitIdentityFixture54321>', 'NoEmitPreviewFixture54321']
        runs = [s.encode() for s in strings] + [s.encode('utf-16le') for s in strings]
        runs += [bytes(range(160, 224))]
        data = envelope(77, b'|'.join(runs))
        result = self.observe(store(data))
        self.assertEqual(result.returncode, 0, result.stderr)
        for run in runs:
            self.assertNotIn(run, result.stdout + result.stderr)
            self.assertNotIn(run.hex().encode(), result.stdout + result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(set(report), {'schema', 'blocks', 'records', 'unknown_fields'})
        self.assertEqual(set(report['records'][0]), {
            'record_type', 'occurrences', 'total_bytes', 'decoded_bytes', 'decoded_byte_percent',
            'wire_field_ids', 'decoded_field_id_percent', 'fields'})


if __name__ == '__main__':
    unittest.main()
