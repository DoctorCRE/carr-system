#!/usr/bin/env python3
"""Incremental `hxprobe db --cache` equals a full decode, using generated stores only."""
import os
from pathlib import Path
import re
import runpy
import sqlite3
import stat
import struct
import subprocess
import tempfile
import unittest
import zlib

ROOT = Path(__file__).resolve().parents[1]
CRATE = ROOT / 'vendor/hxprobe'
BINARY = CRATE / 'target/debug/hxprobe'
CALENDAR = runpy.run_path(str(ROOT / 'ops/hxstore-calendar-selftest.py'))['candidate']
TABLES = {
    'folders': 'id', 'messages': 'id', 'message_folders': 'message_id, folder_id',
    'message_keys': 'message_id, local_key', 'attachments': 'id', 'events': 'id',
    'event_attendees': 'event_id, name, email', 'meta': 'key',
}


def block(data):
    extra = len(data) - 15
    compressed = (bytes([len(data) << 4]) + data if extra < 0 else
                  b'\xf0' + b'\xff' * (extra // 255) + bytes([extra % 255]) + data)
    raw = bytearray(40) + compressed
    struct.pack_into('<QIIII', raw, 8, 0x5d0245643b706a05, 8, len(compressed), len(data), 4)
    struct.pack_into('<I', raw, 4, zlib.crc32(raw[8:]))
    struct.pack_into('<I', raw, 0, zlib.crc32(raw[4:32]))
    return bytes(raw)


def store(*payloads):
    header = bytearray(64)
    header[:8] = b'Nostromo'
    header[8] = ord('i')
    struct.pack_into('<Q', header, 56, 4096)
    # Each block starts on its own page, the way Outlook lays them out.
    pages = [bytes(header) + bytes(4096 - 64)]
    for payload in payloads:
        raw = block(payload)
        pages.append(raw + bytes(-len(raw) % 4096))
    return b''.join(pages)


def text(value):
    return value.encode('utf-16le') + b'\0\0'


def typed(kind, key, owner, size, reference_kind=0):
    data = bytearray(size)
    struct.pack_into('<H', data, 0, kind)
    struct.pack_into('<QIQQQ', data, 10, key, reference_kind, owner, key, owner)
    return data


def wrapped(tag, obj):
    return struct.pack('<HHIH', 5, tag, len(obj) + 10, 0) + bytes(obj)


def folder(key, name, parent=None):
    obj = typed(0x4d, key, 7, 840, 2)
    if parent is not None:
        obj[792:838] = typed(0x4d, parent, 7, 46, 2)
    obj.extend(b'\1\1\1\1\0' + text(name))
    return wrapped(0x04d8, obj)


def message(key, sender, opening):
    obj = typed(0xca, key, key, 500)
    obj[408:454] = typed(0x4d, 51, 7, 46, 2)
    obj.extend(text(sender) + text('Synthetic Sender'))
    obj.extend(text('IPM.Note') + text(f'<{key}@example.test>'))
    obj.extend(f'<html><body>{opening} about the synthetic suite tour.</body></html>'.encode())
    return wrapped(0x074e, obj)


def attachment(key, message_key, name):
    obj = typed(0x16a, key, 99, 600)
    obj[360:406] = typed(0xca, message_key, message_key, 46)
    struct.pack_into('<Q', obj, 558, 4096)
    obj.extend(text('application/pdf') + b'\1\0')
    obj.extend(text(f'~/Files/S0/42/Attachments/0/{name}[123].pdf'))
    obj.extend(b'\1\1\1\1\0' + text(f'{name}.pdf') + text('part-1@example.test'))
    return wrapped(0x0318, obj)


def blocks(first_sender='first@example.test', lease='Lease'):
    return [
        folder(50, 'Active Deals') + folder(51, 'Deal A', 50),
        message(70, first_sender, 'Synthetic opening one'),
        attachment(80, 70, lease),
        message(71, 'second@example.test', 'Synthetic opening two'),
        CALENDAR([('Bo Example', 'bo@example.test')]),
    ]


class IncrementalDecodeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        subprocess.run(['cargo', 'build', '--locked'], cwd=CRATE, check=True, timeout=300)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.cache = self.dir / 'decoder.cache'

    def decode(self, data, name, cache=True):
        source, db = self.dir / f'{name}.hxd', self.dir / f'{name}.db'
        source.write_bytes(data)
        result = subprocess.run([str(BINARY), 'db', str(source), str(db), str(self.dir / 'Files'),
                                 *(['--cache', str(self.cache)] if cache else [])],
                                capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(source.read_bytes(), data)
        reuse = re.search(rb'incremental: (\d+) blocks reused, (\d+) decoded', result.stdout)
        with sqlite3.connect(db) as con:
            rows = {table: con.execute(f'SELECT * FROM {table} ORDER BY {order}').fetchall()
                    for table, order in TABLES.items()}
        return rows, reuse and (int(reuse[1]), int(reuse[2]))

    def test_unchanged_blocks_are_reused_and_output_equals_a_full_decode(self):
        before = store(*blocks())
        full, none = self.decode(before, 'full', cache=False)
        self.assertIsNone(none)
        self.assertEqual(len(full['messages']), 2)
        self.assertEqual(len(full['attachments']), 1)
        self.assertEqual(len(full['events']), 1)
        self.assertEqual(len(full['message_folders']), 2)
        cold, counts = self.decode(before, 'cold')
        self.assertEqual((cold, counts), (full, (0, 5)))
        self.assertEqual(stat.S_IMODE(self.cache.stat().st_mode), 0o600)
        warm, counts = self.decode(before, 'warm')
        self.assertEqual((warm, counts), (full, (5, 0)))

        after = store(*blocks(first_sender='changed@example.test', lease='Amended'))
        expected, _ = self.decode(after, 'after-full', cache=False)
        self.assertNotEqual(expected, full)
        changed, counts = self.decode(after, 'after-incremental')
        self.assertEqual((changed, counts), (expected, (3, 2)))

    def test_a_block_moved_to_another_offset_is_decoded_again(self):
        parts = blocks()
        self.decode(store(*parts), 'first')
        moved = store(parts[0], b'\0' * 64, *parts[1:])
        expected, _ = self.decode(moved, 'moved-full', cache=False)
        got, counts = self.decode(moved, 'moved')
        self.assertEqual(got, expected)
        self.assertEqual(counts, (1, 5))

    def test_damaged_or_foreign_cache_falls_back_to_a_full_decode(self):
        data = store(*blocks())
        expected, _ = self.decode(data, 'full', cache=False)
        self.decode(data, 'seed')
        cache = bytearray(self.cache.read_bytes())
        for at in range(len(cache) - 1, 64, -97):
            cache[at] ^= 0x5a
        self.cache.write_bytes(bytes(cache))
        got, counts = self.decode(data, 'damaged')
        self.assertEqual(got, expected)
        self.assertEqual(sum(counts), 5)
        for garbage in (b'', b'not a cache', os.urandom(4096)):
            self.cache.write_bytes(garbage)
            got, counts = self.decode(data, 'garbage')
            self.assertEqual((got, counts), (expected, (0, 5)))

    def test_a_corrupt_block_is_unverified_whether_or_not_it_was_cached(self):
        data = bytearray(store(*blocks()))
        self.decode(bytes(data), 'seed')
        data[4096 * 2 + 60] ^= 0xff  # inside the first message block's compressed payload
        expected, _ = self.decode(bytes(data), 'corrupt-full', cache=False)
        got, counts = self.decode(bytes(data), 'corrupt')
        self.assertEqual(got, expected)
        self.assertEqual(counts, (4, 0))
        meta = dict(got['meta'])
        self.assertEqual((meta['blocks_found'], meta['blocks_verified']), (5, 4))


if __name__ == '__main__':
    unittest.main()
