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
import sys
from unittest.mock import patch
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from lib import outlook_send as send

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
            in_reply_to=None, conversation_index=None, inline=False, row_sender='wrong@example.test', runs=()):
    """A class 00c9 message object with its expanded 00ca header at +480.

    `None` leaves the sender or subject slot unset, as a draft revision does. Inline rows carry `runs`
    as extra text after the anchor."""
    obj = bytearray(BASE)
    struct.pack_into('<H', obj, 0, 0x00c9)
    struct.pack_into('<Q', obj, 10, key)
    struct.pack_into('<Q', obj, 30, key)
    struct.pack_into('<H', obj, 480, 0x00ca)
    for at in (490, 510, 518):
        struct.pack_into('<Q', obj, at, key)
    struct.pack_into('<H', obj, 888, 0x004d)
    struct.pack_into('<QIQQQ', obj, 898, 50, 2, 7, 50, 7)
    values = {'sender': sender and utf16(sender), 'item_class': utf16('IPM.Note'),
              'message_id': utf16(f'<{key}@example.test>'), 'subject': subject is not None and utf16(subject),
              'topic': subject is not None and utf16(subject.removeprefix('Re: ')),
              'conversation_index': conversation_index or index(guid, replies)}
    values = {name: value for name, value in values.items() if value}
    if inline:
        values = {'row_sender': utf16(row_sender), 'item_class': values.pop('item_class'),
                  'sent': struct.pack('<Q', 1_793_000_000 * 10_000_000 + 621_355_968_000_000_000),
                  'message_id': values.pop('message_id'), 'duplicate_id': utf16(f'<{key}@example.test>'),
                  'html': b'<html><body>Synthetic inline message body with enough words.</body></html>',
                  **{f'run{i}': utf16(run) for i, run in enumerate(runs)}, **values}
    if in_reply_to:
        values['in_reply_to'] = utf16(f'<{in_reply_to}@example.test>')
    data = b''
    for name, value in values.items():
        if name in SLOTS:
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
                sender = 'sender' if 'sender' in {r[1] for r in db.execute('PRAGMA table_info(message_objects)')} else 'NULL'
                return {
                    'recipients': db.execute('SELECT local_key, kind, kind_code, position, address, name '
                                             'FROM recipients ORDER BY local_key, kind_code, position').fetchall(),
                    'objects': db.execute('SELECT local_key, conversation_id, conversation_depth, in_reply_to, '
                                          'subject, recipients_status FROM message_objects '
                                          'ORDER BY local_key').fetchall(),
                    'senders': db.execute(f'SELECT local_key, {sender} FROM message_objects ORDER BY local_key').fetchall(),
                    'messages': db.execute('SELECT sender, subject, recipients, copies FROM messages ORDER BY id').fetchall(),
                    'sent': tuple(send.SentRow(*r) for r in db.execute(
                        'SELECT sender, recipients, subject, sent_unix, message_id FROM messages')),
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

    def test_inline_sent_row_decodes_object_fields_and_merges_revisions(self):
        out = self.export(message(0xc101, sender='owner@example.test', subject='Synthetic sent topic', inline=True),
                          message(0xc101, sender='owner@example.test', subject='Synthetic sent topic'),
                          recipient(0xc101, 0xc111, TO, 'target@example.test'))
        self.assertEqual(out['messages'], [('owner@example.test', 'Synthetic sent topic', 'target@example.test', 2)])

    def test_confirmed_mail_matches_decoded_inline_sent_row(self):
        out = self.export(message(0xc101, sender='owner@example.test', subject='Synthetic sent topic', inline=True),
                          recipient(0xc101, 0xc111, TO, 'target@example.test'))
        mail = send.Mail(('target@example.test',), (), (), 'Synthetic sent topic', 'Synthetic body')
        with patch.object(send, 'account', return_value='owner@example.test'):
            self.assertEqual(send.confirmed_mail(send.Snapshot(out['sent'], ()), mail, 1_793_000_000 - 5),
                             f'{0xc101}@example.test')

    def test_a_draft_revision_with_unset_fields_does_not_hide_the_sent_ones(self):
        # A recent Sent Items row keeps its unsent draft revision, with no sender or subject yet, while
        # the row text holds the recipient and the account address glued into one run (WR-000589).
        out = self.export(message(0xc201, sender='owner@example.test', subject='Synthetic sent topic', inline=True,
                                  row_sender='target@example.test' + 'owner@example.test'),
                          message(0xc201, sender=None, subject=None),
                          recipient(0xc201, 0xc211, TO, 'target@example.test'))
        self.assertEqual(out['senders'], [(0xc201, 'owner@example.test')])
        self.assertEqual({row[:3] for row in out['messages']},
                         {('owner@example.test', 'Synthetic sent topic', 'target@example.test')})
        mail = send.Mail(('target@example.test',), (), (), 'Synthetic sent topic', 'Synthetic body')
        with patch.object(send, 'account', return_value='owner@example.test'):
            self.assertEqual(send.confirmed_mail(send.Snapshot(out['sent'], ()), mail, 1_793_000_000 - 5),
                             f'{0xc201}@example.test')

    def test_text_addresses_are_undoubled_and_glued_pairs_are_not_addresses(self):
        out = self.export(message(0xc301, sender=None, inline=True, row_sender='owner@example.test' * 2,
                                  runs=('cy@example.test' * 2, 'amy@example.test' + 'max@example.test')))
        self.assertEqual([row[:3] for row in out['messages']],
                         [('owner@example.test', 'Synthetic thread', 'cy@example.test')])

    def test_a_glued_sender_run_leaves_the_sender_unknown_and_keeps_the_recipient(self):
        out = self.export(message(0xc401, sender=None, inline=True,
                                  row_sender='target@example.test' + 'owner@example.test', runs=('cy@example.test',)))
        self.assertEqual([row[:3] for row in out['messages']], [(None, 'Synthetic thread', 'cy@example.test')])

    def test_keyless_row_takes_the_subject_pair_just_before_its_anchor(self):
        # Layout B: the header precedes the anchor. These subjects fail the prose checks (mostly digits, a
        # file name), but the stored Topic/NormalizedSubject pair beside the anchor identifies them.
        for subject, topic in (('RE: 2026-10-14 #4471', '2026-10-14 #4471'), ('Scan_0001.pdf', 'Scan_0001.pdf')):
            with self.subTest(subject=subject):
                row = (utf16('owner@example.test') + utf16('Synthetic preview text for a keyless row here')
                       + utf16(subject) + utf16(topic) + utf16('IPM.Note')
                       + struct.pack('<Q', 1_793_000_000 * 10_000_000 + 621_355_968_000_000_000))
                out = self.export(row)
                self.assertEqual([row[:2] for row in out['messages']], [('owner@example.test', subject)])

    def test_a_fragment_of_the_next_run_is_not_a_subject_pair(self):
        row = (utf16('owner@example.test') + utf16('Synthetic preview text for a keyless row here')
               + utf16('2026') + utf16('Invoice batch 2026') + utf16('IPM.Note')
               + struct.pack('<Q', 1_793_000_000 * 10_000_000 + 621_355_968_000_000_000))
        self.assertEqual([row[:2] for row in self.export(row)['messages']], [('owner@example.test', None)])

    def test_sender_is_exported_only_when_cached_revisions_agree(self):
        out = self.export(message(0xc001, sender='actual@example.test'),
                          message(0xc001, sender='actual@example.test'),
                          message(0xc002, sender='one@example.test'),
                          message(0xc002, sender='two@example.test'))
        self.assertEqual(out['senders'], [(0xc001, 'actual@example.test'), (0xc002, None)])

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
