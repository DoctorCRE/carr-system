#!/usr/bin/env python3
"""Check message-object recipients and conversations through the real decoder (WR-000440).

Every value is synthetic; the object builders live in hxstore-local-key-selftest.py."""
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
FIXTURE = runpy.run_path(str(ROOT / 'ops/hxstore-local-key-selftest.py'))
STORE, SLOTS, utf16, recipient = FIXTURE['STORE'], FIXTURE['SLOTS'], FIXTURE['utf16'], FIXTURE['recipient']
TO, CC, BCC, OTHER = 204, 205, 206, 413


def index(guid, replies=0):
    return b'\x01' + bytes(range(1, 6)) + guid + b'\x02' * 5 * replies


def message(key, guid=b'\x11' * 16, replies=0, subject='Synthetic thread', in_reply_to=None,
            conversation_index=None, **fields):
    """A class 00c9 message revision in a thread. `None` leaves the sender or subject slot unset."""
    return FIXTURE['message'](key, subject=subject, conversation_index=conversation_index or index(guid, replies),
                              in_reply_to=in_reply_to and f'<{in_reply_to}@example.test>', **fields)


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

    def test_to_cc_and_bcc_keep_their_stored_order(self):
        out = self.export(
            message(0x7000),
            recipient(0x7000, 0x7003, TO, 'zed@example.test', 'Zed Example'),
            recipient(0x7000, 0x7001, TO, 'amy@example.test'),
            recipient(0x7000, 0x7004, CC, 'cy@example.test'),
            recipient(0x7000, 0x7002, TO, 'max@example.test'),
            recipient(0x7000, 0x7006, BCC, 'bea@example.test'),
            recipient(0x7000, 0x7005, OTHER, 'reply@example.test'))
        self.assertEqual(out['recipients'], [
            (0x7000, 'to', TO, 1, 'amy@example.test', ''),
            (0x7000, 'to', TO, 2, 'max@example.test', ''),
            (0x7000, 'to', TO, 3, 'zed@example.test', 'Zed Example'),
            (0x7000, 'cc', CC, 1, 'cy@example.test', ''),
            (0x7000, 'bcc', BCC, 1, 'bea@example.test', ''),
            (0x7000, None, OTHER, 1, 'reply@example.test', '')])
        self.assertEqual(out['messages'][0][2],
                         'amy@example.test, max@example.test, zed@example.test, cy@example.test, bea@example.test')
        self.assertEqual(out['objects'][0][5], 'listed')
        self.assertEqual((out['meta']['message_objects'], out['meta']['recipients']), (1, 6))

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
        self.assertEqual(out['recipients'], [(0x9001, 'to', TO, 1, 'amy@example.test', '')])
        self.assertEqual(out['meta']['recipient_conflicts'], 1)

    def test_confirmed_mail_matches_the_exact_to_cc_and_bcc_of_a_sent_row(self):
        # Sent Items keeps the Bcc recipients (CARRTEST-CC1..3, 2026-10-30), so the full set must match.
        out = self.export(message(0xc101, sender='owner@example.test', subject='Synthetic sent topic'),
                          recipient(0xc101, 0xc111, TO, 'target@example.test'),
                          recipient(0xc101, 0xc112, CC, 'copy@example.test'),
                          recipient(0xc101, 0xc113, BCC, 'blind@example.test'))
        with patch.object(send, 'account', return_value='owner@example.test'):
            for bcc, found in (((), None), (('blind@example.test',), f'{0xc101}@example.test')):
                mail = send.Mail(('target@example.test',), ('copy@example.test',), bcc, 'Synthetic sent topic', 'Body')
                self.assertEqual(send.confirmed_mail(send.Snapshot(out['sent'], ()), mail, 1_793_000_000 - 5), found)

    def test_a_draft_revision_with_unset_fields_does_not_hide_the_sent_ones(self):
        # A Sent Items message keeps its older draft revision, with no sender or subject yet (WR-000589).
        out = self.export(message(0xc201, sender='owner@example.test', subject='Synthetic sent topic'),
                          message(0xc201, sender=None, subject=None, modified=1_700_000_000),
                          recipient(0xc201, 0xc211, TO, 'target@example.test'))
        self.assertEqual(out['senders'], [(0xc201, 'owner@example.test')])
        self.assertEqual({row[:3] for row in out['messages']},
                         {('owner@example.test', 'Synthetic sent topic', 'target@example.test')})
        mail = send.Mail(('target@example.test',), (), (), 'Synthetic sent topic', 'Synthetic body')
        with patch.object(send, 'account', return_value='owner@example.test'):
            self.assertEqual(send.confirmed_mail(send.Snapshot(out['sent'], ()), mail, 1_793_000_000 - 5),
                             f'{0xc201}@example.test')

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
