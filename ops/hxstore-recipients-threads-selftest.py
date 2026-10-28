#!/usr/bin/env python3
"""Check message-object recipients and conversations through the real decoder (WR-000440).

Every value is synthetic. Objects follow the class 00c9 message and class 0055 recipient layouts in
spec/outlook/hxstore-atlas.json."""
import runpy
from pathlib import Path
import sqlite3
import struct
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
CRATE = ROOT / 'vendor/hxprobe'
STORE = runpy.run_path(str(ROOT / 'ops/hxstore-observe-selftest.py'))['store']
BASE = 1700
SLOTS = {'in_reply_to': 1202, 'message_id': 1218, 'item_class': 1226, 'topic': 1250,
         'conversation_index': 1166, 'sender': 1386, 'subject': 1422}


def utf16(text):
    return (text + '\0').encode('utf-16le')


def envelope(tag, obj):
    return struct.pack('<HHIH', 5, tag, len(obj) + 10, 0) + obj


def index(guid, replies=0):
    return b'\x01' + bytes(range(1, 6)) + guid + b'\x02' * 5 * replies


def message(key, guid=b'\x11' * 16, replies=0, subject='Synthetic thread', sender='pat@example.test',
            in_reply_to=None, conversation_index=None):
    """A class 00c9 message object with its expanded 00ca header at +480."""
    obj = bytearray(BASE)
    struct.pack_into('<H', obj, 0, 0x00c9)
    struct.pack_into('<Q', obj, 10, key)
    struct.pack_into('<Q', obj, 30, key)
    struct.pack_into('<H', obj, 480, 0x00ca)
    for at in (490, 510, 518):
        struct.pack_into('<Q', obj, at, key)
    struct.pack_into('<H', obj, 888, 0x004d)
    struct.pack_into('<QIQQQ', obj, 898, 50, 2, 7, 50, 7)
    values = {'sender': utf16(sender), 'item_class': utf16('IPM.Note'),
              'message_id': utf16(f'<{key}@example.test>'), 'subject': utf16(subject),
              'topic': utf16(subject.removeprefix('Re: ')),
              'conversation_index': conversation_index or index(guid, replies)}
    if in_reply_to:
        values['in_reply_to'] = utf16(f'<{in_reply_to}@example.test>')
    data = b''
    for name, value in values.items():
        struct.pack_into('<II', obj, SLOTS[name], len(data), len(value) | 0x8000_0000)
        data += value
    return envelope(0x060f, bytes(obj) + data + utf16('Synthetic preview text for one message object'))


def recipient(owner, rid, kind, address, name=None):
    obj = bytearray(340)
    struct.pack_into('<H', obj, 0, 0x0055)
    struct.pack_into('<QIQQQ', obj, 10, rid, kind, owner, rid, owner)
    sort_key = b'\x0e\x35' * 4 + b'\x01\x01\x01\x01\x00'
    name, address = utf16(name or address), utf16(address)
    struct.pack_into('<7I', obj, 282, len(sort_key) + len(address), len(name) | 0x8000_0000,
                     len(sort_key), len(address) | 0x8000_0000, 0, len(sort_key) | 0x8000_0000, 1)
    return envelope(0x015e, bytes(obj) + sort_key + address + name)


TO, CC, OTHER = 204, 205, 413


class RecipientThreadExportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        subprocess.run(['cargo', 'build', '--locked'], cwd=CRATE, check=True, timeout=180)

    def export(self, *objects):
        with tempfile.TemporaryDirectory() as tmp:
            source, target = Path(tmp) / 'synthetic.hxd', Path(tmp) / 'synthetic.db'
            source.write_bytes(STORE(b''.join(objects)))
            before = source.read_bytes()
            result = subprocess.run([str(CRATE / 'target/debug/hxprobe'), 'db', str(source), str(target)],
                                    capture_output=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(source.read_bytes(), before)
            with sqlite3.connect(target) as db:
                return {
                    'recipients': db.execute('SELECT local_key, kind, kind_code, position, address, name '
                                             'FROM recipients ORDER BY local_key, kind_code, position').fetchall(),
                    'objects': db.execute('SELECT local_key, conversation_id, conversation_depth, in_reply_to, '
                                          'subject, recipients_status FROM message_objects '
                                          'ORDER BY local_key').fetchall(),
                    'threads': db.execute('SELECT conversation_id, messages, latest_local_key '
                                          'FROM threads ORDER BY conversation_id').fetchall(),
                    'meta': dict(db.execute('SELECT key, value FROM meta')),
                }

    def test_to_and_cc_keep_their_stored_order(self):
        out = self.export(
            message(0x7000),
            recipient(0x7000, 0x7003, TO, 'zed@example.test', 'Zed Example'),
            recipient(0x7000, 0x7001, TO, 'amy@example.test'),
            recipient(0x7000, 0x7004, CC, 'cy@example.test'),
            recipient(0x7000, 0x7002, TO, 'max@example.test'),
            recipient(0x7000, 0x7005, OTHER, 'reply@example.test'))
        self.assertEqual(out['recipients'], [
            (0x7000, 'to', TO, 1, 'amy@example.test', 'amy@example.test'),
            (0x7000, 'to', TO, 2, 'max@example.test', 'max@example.test'),
            (0x7000, 'to', TO, 3, 'zed@example.test', 'Zed Example'),
            (0x7000, 'cc', CC, 1, 'cy@example.test', 'cy@example.test'),
            (0x7000, None, OTHER, 1, 'reply@example.test', 'reply@example.test')])
        self.assertEqual(out['objects'][0][5], 'listed')
        self.assertEqual((out['meta']['message_objects'], out['meta']['recipients']), (1, 5))

    def test_a_reply_joins_its_parent_thread_and_becomes_its_latest_state(self):
        parent, reply, other = b'\x11' * 16, b'\x11' * 16, b'\x22' * 16
        out = self.export(message(0x8001, parent, subject='Synthetic thread'),
                          message(0x8002, reply, replies=1, subject='Re: Synthetic thread', in_reply_to=0x8001),
                          message(0x8003, other, subject='Synthetic thread'))
        self.assertEqual(out['objects'], [
            (0x8001, '11' * 16, 0, None, 'Synthetic thread', 'none'),
            (0x8002, '11' * 16, 1, f'{0x8001}@example.test', 'Re: Synthetic thread', 'none'),
            (0x8003, '22' * 16, 0, None, 'Synthetic thread', 'none')])
        self.assertEqual(out['threads'], [('11' * 16, 2, 0x8002), ('22' * 16, 1, 0x8003)])
        self.assertEqual(out['meta']['message_objects_with_conversation'], 3)

    def test_matching_revisions_collapse_and_disagreeing_ones_stay_unknown(self):
        out = self.export(message(0x9001), message(0x9001),
                          message(0x9002, b'\x33' * 16), message(0x9002, b'\x44' * 16, subject='Other'),
                          recipient(0x9001, 0x9011, TO, 'amy@example.test'),
                          recipient(0x9001, 0x9011, TO, 'amy@example.test'),
                          recipient(0x9002, 0x9012, TO, 'amy@example.test'),
                          recipient(0x9002, 0x9012, CC, 'amy@example.test'))
        self.assertEqual(out['objects'], [(0x9001, '11' * 16, 0, None, 'Synthetic thread', 'listed'),
                                          (0x9002, None, None, None, None, 'conflict')])
        self.assertEqual(out['recipients'], [(0x9001, 'to', TO, 1, 'amy@example.test', 'amy@example.test')])
        self.assertEqual(out['meta']['recipient_conflicts'], 1)

    def test_an_empty_stored_subject_is_kept_apart_from_an_unknown_one(self):
        out = self.export(message(0xa001, subject=''))
        self.assertEqual(out['objects'][0][4], '')

    def test_malformed_objects_are_skipped(self):
        bad_index = message(0xb001, conversation_index=b'\x02' + b'\x00' * 21)
        short_index = message(0xb002, conversation_index=b'\x01' * 23)
        outside = bytearray(message(0xb003))
        struct.pack_into('<I', outside, 10 + SLOTS['subject'], 0xffff)
        orphan = bytearray(recipient(0xb001, 0xb011, TO, 'amy@example.test'))
        struct.pack_into('<Q', orphan, 10 + 38, 0xdead)
        out = self.export(bad_index, short_index, bytes(outside), bytes(orphan))
        self.assertEqual([row[:3] for row in out['objects']], [(0xb001, None, None), (0xb002, None, None)])
        self.assertEqual(out['recipients'], [])


if __name__ == '__main__':
    unittest.main()
