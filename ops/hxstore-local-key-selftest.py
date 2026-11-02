#!/usr/bin/env python3
"""Check hxprobe exports one slot-read row per message key, using synthetic bytes.

Every value is synthetic; the real decoder runs. The builders here lay objects out as spec/outlook/hxstore-atlas.json records them
(class 00c9 message, 0055 recipient, 00ca body, 004f view row, 004d folder); other selftests import them."""
import atexit
import gzip
import json
import stat
import time
from contextlib import redirect_stderr
from io import StringIO
from types import SimpleNamespace
from unittest.mock import patch
import runpy
import shutil
from pathlib import Path
import sqlite3
import struct
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from lib import outlook_intake as intake

CRATE = ROOT / 'vendor/hxprobe'
STORE = runpy.run_path(str(ROOT / 'ops/hxstore-observe-selftest.py'))['store']
EPOCH_TICKS = 621_355_968_000_000_000
OUTBOX = 3_155_378_975_999_999_999  # DateTime.MaxValue: last-modified time of a message in the Outbox
TO, CC, BCC = 204, 205, 206
SLOTS = {'conversation_index': 1166, 'in_reply_to': 1202, 'message_id': 1218, 'item_class': 1226,
         'preview': 1294, 'sender_name': 1378, 'sender': 1386, 'subject': 1422}


def utf16(text):
    return (text + '\0').encode('utf-16le')


def ticks(unix):
    return OUTBOX if unix == OUTBOX else unix * 10_000_000 + EPOCH_TICKS


def envelope(tag, obj):
    return struct.pack('<HHIH', 5, tag, len(obj) + 10, 0) + bytes(obj)


def header(obj, at, cls, key, kind, owner):
    struct.pack_into('<H', obj, at, cls)
    struct.pack_into('<QIQQQ', obj, at + 10, key, kind, owner, key, owner)


def slotted(fixed, values, offsets):
    """Set each descriptor at its offset to its value in a heap after a four-byte inline region."""
    struct.pack_into('<H', fixed, 94, 4)
    heap = b''
    for name, value in values.items():
        struct.pack_into('<II', fixed, offsets[name], len(heap), len(value) | 0x8000_0000)
        heap += value
    return fixed + b'\xee' * 4 + heap


def folder(key, name, account=7):
    obj = bytearray(840)
    header(obj, 0, 0x004d, key, 2, account)
    return envelope(0x04d8, obj + b'\1\1\1\1\0' + utf16(name))


def message(key, sender='pat@example.test', sent=1_793_000_000, preview='Synthetic preview text for one message',
            subject=None, account=7, folder=50, modified=None, sender_name=None, message_id=None,
            in_reply_to=None, conversation_index=None, item_class='IPM.Note', received=None):
    """One revision of a class 00c9 message. A falsy value leaves its slot unset, as a draft revision does."""
    obj = bytearray(1541)
    header(obj, 0, 0x00c9, key, 212, account)
    struct.pack_into('<Q', obj, 366, ticks(modified if modified is not None else sent or 1_700_000_000))
    if sent:
        struct.pack_into('<Q', obj, 718, ticks(sent))
        struct.pack_into('<Q', obj, 278, ticks(received or sent))
    if folder:
        header(obj, 888, 0x004d, folder, 2, account)
    values = {'conversation_index': conversation_index, 'item_class': item_class and utf16(item_class),
              'message_id': utf16(message_id if message_id is not None else f'<{key}@example.test>'),
              'in_reply_to': in_reply_to and utf16(in_reply_to), 'preview': preview and utf16(preview),
              'sender_name': sender_name and utf16(sender_name), 'sender': sender and utf16(sender),
              'subject': subject is not None and utf16(subject)}
    return envelope(0x060f, slotted(obj, {k: v for k, v in values.items() if v}, SLOTS))


def recipient(owner, rid, kind, address, name=None):
    obj = bytearray(340)
    header(obj, 0, 0x0055, rid, kind, owner)
    values = {'address': utf16(address), **({'name': utf16(name)} if name else {})}
    return envelope(0x015e, slotted(obj, values, {'name': 282, 'address': 290}))


def body(key, html=None, sidecar=None, account=7):
    """A class 00ca body: inline UTF-8 HTML, or the profile-relative path of a gzip EFMData sidecar."""
    obj = bytearray(1860)
    struct.pack_into('<H', obj, 0, 0x00ca)
    for at in (10, 30, 38):
        struct.pack_into('<Q', obj, at, key)
    header(obj, 48, 0x00c9, key, 212, account)
    value = html.encode() if html else utf16(sidecar)
    out = slotted(obj, {'body': value}, {'body': 1650})
    if sidecar:
        struct.pack_into('<I', out, 1654, len(value) | 0x8200_0000)
    return envelope(0x074e, out)


def view_row(key, sender='pat@example.test', subject='Synthetic ghost subject', sent=1_793_000_500):
    """A class 004f folder view row: display copies of a message's fields beside an IPM.Note run."""
    obj = bytearray(1062)
    struct.pack_into('<H', obj, 0, 0x004f)
    header(obj, 272, 0x00c9, key, 212, 7)
    obj += (utf16('IPM.Note') + utf16(sender) + struct.pack('<Q', ticks(sent)) + utf16(subject)
            + utf16('Synthetic view row preview with enough words to look like mail'))
    return envelope(0x0430, obj)


def decode(*objects, files=None):
    """Decode `objects` with the real binary; the database lives until the process exits."""
    tmp = Path(tempfile.mkdtemp())
    atexit.register(shutil.rmtree, tmp, True)
    source, target = tmp / 'synthetic.hxd', tmp / 'synthetic.db'
    source.write_bytes(STORE(b''.join(objects)))
    args = [str(CRATE / 'target/debug/hxprobe'), 'db', str(source), str(target)] + ([str(files)] if files else [])
    result = subprocess.run(args, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
    return target


class LocalKeyExportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        subprocess.run(['cargo', 'build', '--locked'], cwd=CRATE, check=True, timeout=180)

    def export(self, *objects, columns='sender, sent_unix, copies, local_keys', files=None):
        db = sqlite3.connect(decode(folder(50, 'Inbox'), *objects, files=files))
        try:
            return (db.execute(f'SELECT {columns} FROM messages ORDER BY id').fetchall(),
                    dict(db.execute('SELECT key, value FROM meta')))
        finally:
            db.close()

    def census(self, *objects, files=None):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / 'copy.hxd'
            source.write_bytes(STORE(b''.join(objects)))
            cache = Path(files) if files else root / 'Files'
            cache.mkdir(exist_ok=True)
            result = subprocess.run([str(CRATE / 'target/debug/hxprobe'), 'census', '--immutable-copy',
                                     str(source), str(cache), '7'], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, 'census rejected synthetic input')
            for secret in ('PRIVATE-SUBJECT', 'private@example.test', 'PRIVATE-BODY', 'PRIVATE-NAME'):
                self.assertNotIn(secret, result.stdout + result.stderr)
            self.assertEqual(result.stderr, '')
            return json.loads(result.stdout)

    def test_census_distinguishes_absent_rejected_and_conflicting_children(self):
        bad_body = bytearray(body(102, html='PRIVATE-BODY'))
        struct.pack_into('<I', bad_body, 10 + 1650, 0xffffffff)
        bad_recipient = recipient(102, 202, TO, 'not-an-address')
        got = self.census(folder(50, 'Inbox'),
                          message(100, subject='PRIVATE-SUBJECT', sender='private@example.test', sender_name='PRIVATE-NAME'),
                          message(101), body(101, html='<p>PRIVATE-BODY</p>'),
                          recipient(101, 201, TO, 'private@example.test', 'PRIVATE-NAME'),
                          recipient(101, 201, TO, 'other@example.test'),
                          message(102, folder=999), bytes(bad_body), bad_recipient,
                          message(103, account=8))
        rows = got['messages']
        self.assertEqual(set(rows), {'100', '101', '102'})
        self.assertEqual((rows['100']['body_objects'], rows['100']['recipient_objects']), (0, 0))
        self.assertEqual((rows['101']['body_inline'], rows['101']['recipients_conflict'],
                          rows['101']['recipient_children']), (1, 1, 1))
        self.assertEqual((rows['102']['body_objects'], rows['102']['body_rejected'],
                          rows['102']['recipient_rejected'], rows['102']['folder_uncatalogued']), (1, 1, 1, 1))
        self.assertEqual(got['totals']['messages'], 3)
        self.assertEqual(got['totals']['body_complete'], 1)
        self.assertEqual(got['totals']['body_absent'], 1)
        self.assertEqual(got['totals']['recipients_none'], 2)
        self.assertEqual(got['totals']['recipient_children'], 1)
        self.assertEqual(got['totals']['recipient_objects'], 2)
        self.assertEqual(got['totals']['unknown_folder_messages'], 1)
        self.assertTrue(all(type(v) is int for row in rows.values() for v in row.values()))

    def test_census_counts_children_whose_owner_was_never_observed(self):
        got = self.census(message(100), body(998, html='PRIVATE-BODY'),
                          recipient(999, 201, TO, 'private@example.test'))
        self.assertEqual(got['totals']['body_orphaned_all_accounts'], 1)
        self.assertEqual(got['totals']['recipient_orphaned_all_accounts'], 1)
        self.assertEqual(got['totals']['body_unlinked_all_accounts'], 0)
        self.assertEqual(got['totals']['recipient_unlinked_all_accounts'], 0)

    def test_intake_census_uses_real_binary_and_removes_copy_on_decoder_failure(self):
        with tempfile.TemporaryDirectory() as temp:
            out = Path(temp)
            (out / 'coverage-census.request').touch(mode=0o600)
            def snapshot(home, work):
                (work / 'store.hxd').write_bytes(STORE(message(100, subject='PRIVATE-SUBJECT',
                    sender='private@example.test', sender_name='PRIVATE-NAME') + body(100, html='PRIVATE-BODY')
                    + body(998, html='PRIVATE-BODY') + recipient(999, 201, TO, 'private@example.test')))
                (work / 'Files').mkdir()
            def fail_decode(*args):
                raise intake.DecodeError('synthetic')
            with patch.object(intake, '_snapshot', snapshot), patch.object(intake, 'load_settings',
                    return_value=SimpleNamespace(decoder=CRATE / 'target/debug/hxprobe')):
                with self.assertRaises(intake.DecodeError), intake._decode(out, out, fail_decode, 7, 'synthetic@example.test'):
                    self.fail('decoder must fail')
            self.assertFalse(list(out.glob('work-*')))
            receipt = out / 'coverage-census.json'
            totals = json.loads(receipt.read_text())['totals']
            self.assertEqual(totals['body_orphaned_all_accounts'], 1)
            self.assertEqual(totals['recipient_orphaned_all_accounts'], 1)
            self.assertEqual(stat.S_IMODE(receipt.stat().st_mode), 0o600)
            self.assertFalse((out / 'coverage-census.request').exists())
            for path in out.rglob('*'):
                if path.is_file():
                    for token in (b'PRIVATE-SUBJECT', b'PRIVATE-BODY', b'PRIVATE-NAME', b'private@example.test'):
                        self.assertNotIn(token, path.read_bytes())

    def test_intake_census_kills_slow_process_and_cleans_copy(self):
        with tempfile.TemporaryDirectory() as temp:
            out = Path(temp)
            decoder = out / 'slow-decoder'
            decoder.write_text('#!' + sys.executable + '\nimport time\ntime.sleep(30)\n')
            decoder.chmod(0o700)
            (out / 'coverage-census.request').touch(mode=0o600)
            def snapshot(home, work):
                (work / 'store.hxd').write_bytes(b'synthetic')
                (work / 'Files').mkdir()
            def fail_decode(*args):
                raise intake.DecodeError('synthetic')
            log = StringIO()
            started = time.monotonic()
            with patch.object(intake, '_snapshot', snapshot), patch.object(intake, 'load_settings',
                    return_value=SimpleNamespace(decoder=decoder)), patch.object(intake, 'CENSUS_TIMEOUT', 0.05), redirect_stderr(log):
                with self.assertRaises(intake.DecodeError), intake._decode(out, out,
                        fail_decode, 7, 'synthetic@example.test'):
                    self.fail('decoder must fail')
            self.assertLess(time.monotonic() - started, 2)
            self.assertEqual(log.getvalue(), 'coverage_census_failed=1\n')
            self.assertFalse(list(out.glob('work-*')))
            self.assertFalse((out / 'coverage-census.request').exists())

    def test_census_names_sidecar_failure_reasons_without_paths_or_content(self):
        with tempfile.TemporaryDirectory() as temp:
            files = Path(temp)
            efm = files / 'S0/3/EFMData'
            efm.mkdir(parents=True)
            (efm / 'ok.dat').write_bytes(gzip.compress(b'<p>PRIVATE-BODY</p>'))
            (efm / 'bad.dat').write_bytes(b'PRIVATE-BODY')
            got = self.census(folder(50, 'Inbox'), *(obj for key, name in
                              [(100, 'ok'), (101, 'missing'), (102, 'bad')] for obj in
                              (message(key), body(key, sidecar=f'~/Files/S0/3/EFMData/{name}.dat'))), files=files)
        self.assertEqual(got['totals']['body_sidecar'], 3)
        self.assertEqual(got['totals']['sidecar_linked'], 1)
        self.assertEqual(got['totals']['sidecar_missing'], 1)
        self.assertEqual(got['totals']['sidecar_decode_failed'], 1)
        self.assertNotIn('EFMData', json.dumps(got))

    def test_census_rejects_invalid_inputs_without_echoing_paths_or_bytes(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / 'PRIVATE-SUBJECT.hxd'
            source.write_bytes(b'PRIVATE-BODY')
            command = [str(CRATE / 'target/debug/hxprobe'), 'census', '--immutable-copy',
                       str(source), str(root), '7']
            bad = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual((bad.returncode, bad.stdout, bad.stderr), (2, '', 'census input rejected\n'))
            source.write_bytes(STORE(message(100)))
            (root / 'HxStore.lock').touch()
            live = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual((live.returncode, live.stdout, live.stderr), (2, '', 'census input rejected\n'))

    def test_revisions_of_one_message_export_one_row_with_its_key(self):
        rows, meta = self.export(message(0x1001), message(0x1001, modified=1_793_000_060))
        self.assertEqual(rows, [('pat@example.test', 1_793_000_000, 2, str(0x1001))])
        self.assertEqual((meta['messages_with_key'], meta['local_key_conflicts']), (1, 0))

    def test_identical_template_twins_export_separately(self):
        # Synthetic reproduction of the TEST-account W1/W2 observation: same sender, time and opening.
        rows, _ = self.export(message(0x2002), message(0x3003))
        self.assertEqual(rows, [('pat@example.test', 1_793_000_000, 1, str(0x2002)),
                                ('pat@example.test', 1_793_000_000, 1, str(0x3003))])

    def test_a_folder_view_row_is_never_a_message(self):
        rows, _ = self.export(message(0x1001, subject='Synthetic real subject'), view_row(0x1001), view_row(0x4004),
                              columns='subject, sent_unix, local_keys')
        self.assertEqual(rows, [('Synthetic real subject', 1_793_000_000, str(0x1001))])

    def test_a_body_stored_as_a_sidecar_is_read_from_its_file(self):
        with tempfile.TemporaryDirectory() as files:
            efm = Path(files) / 'S0' / '3' / 'EFMData'
            efm.mkdir(parents=True)
            (efm / '1.dat').write_bytes(gzip.compress(b'<html><body>Synthetic sidecar body text.</body></html>'))
            rows, meta = self.export(message(0x1001), body(0x1001, sidecar='~/Files/S0/3/EFMData/1.dat'),
                                     message(0x2002), body(0x2002, html='<p>Synthetic inline body text.</p>'),
                                     columns='local_keys, body_kind, body_source, body', files=files)
        self.assertEqual(rows, [(str(0x1001), 'full', 'efm', 'Synthetic sidecar body text.'),
                                (str(0x2002), 'full', 'inline', 'Synthetic inline body text.')])
        self.assertEqual((meta['sidecar_linked'], meta['sidecar_rejected']), (1, 0))

    def test_a_header_only_message_keeps_its_slots_and_its_preview(self):
        rows, _ = self.export(message(0x1001, subject='Synthetic header only', sender_name='Pat Example'),
                              columns='sender, sender_name, subject, recipients, body_kind, body, local_keys')
        self.assertEqual(rows, [('pat@example.test', 'Pat Example', 'Synthetic header only', None, 'preview',
                                 'Synthetic preview text for one message', str(0x1001))])

    def test_a_sent_copy_and_its_inbox_copy_are_two_rows_and_only_the_sent_one_has_bcc(self):
        # The Sent copy is one key from compose to send: Drafts, then the Outbox (undated), then Sent Items.
        common = dict(sender='owner@example.test', subject='Synthetic pair', message_id='<pair@example.test>')
        db = sqlite3.connect(decode(
            folder(50, 'Inbox', account=8), folder(51, 'Drafts'), folder(52, 'Sent Items'),
            message(0x1001, folder=51, sent=1_793_000_000, **common),
            message(0x1001, folder=53, modified=OUTBOX, sent=1_793_000_000, **common),
            message(0x1001, folder=52, modified=1_793_000_120, sent=1_793_000_000, **common),
            message(0x2002, folder=50, account=8, sent=1_793_000_100, received=1_793_000_110, **common),
            *(recipient(key, key + n, kind, address) for key in (0x1001, 0x2002)
              for n, (kind, address) in enumerate(((TO, 'to@example.test'), (CC, 'cc@example.test')), 1)),
            recipient(0x1001, 0x1009, BCC, 'bcc@example.test')))
        try:
            rows = db.execute('SELECT m.local_keys, f.name, m.sent_unix, m.received_unix, m.recipients, m.message_id '
                              'FROM messages m JOIN message_folders mf ON mf.message_id=m.id '
                              'JOIN folders f ON f.id=mf.folder_id ORDER BY m.local_keys').fetchall()
            kinds = db.execute('SELECT local_key, kind, address FROM recipients WHERE kind_code=?', (BCC,)).fetchall()
        finally:
            db.close()
        self.assertEqual(rows, [
            (str(0x1001), 'Sent Items', 1_793_000_000, 1_793_000_000,
             'to@example.test, cc@example.test, bcc@example.test', 'pair@example.test'),
            (str(0x2002), 'Inbox', 1_793_000_100, 1_793_000_110, 'to@example.test, cc@example.test',
             'pair@example.test')])
        self.assertEqual(kinds, [(0x1001, 'bcc', 'bcc@example.test')])

    def test_a_typed_current_folder_proves_account_without_a_catalogue_entry(self):
        def observed_header(key, account, current_folder):
            # The four copied-store misses use 00c9/060f with a 150-byte inline region.
            obj = bytearray(message(key, folder=current_folder, account=account, subject='Synthetic header'))
            obj[10 + 1541 + 4:10 + 1541 + 4] = b'\xee' * 146
            struct.pack_into('<I', obj, 4, len(obj))
            struct.pack_into('<H', obj, 10 + 94, 150)
            return bytes(obj)
        target = decode(folder(50, 'Inbox'),
                        observed_header(0x1001, 7, 999),
                        observed_header(0x2002, 8, 999),
                        observed_header(0x3003, 7, 0))
        meta, mails, _ = intake._load(target, account_id=7)
        self.assertEqual([m['local_keys'] for m in mails], [str(0x1001)])
        self.assertEqual((meta['stored'], meta['admitted'], meta['stored_unknown']), (1, 1, 1))
        self.assertEqual(meta['mail_account_unknown_not_stored'], 1)
        self.assertEqual(meta['mail_outside_selected_account'], 1)

    def test_a_message_needs_a_typed_current_folder_of_its_account(self):
        # The newest revision decides; an older revision's folder is history, not ownership doubt.
        # An unset or foreign-account current reference is unresolved too, never the older folder.
        for current, account, admitted in ((50, 7, 1), (999, 7, 1), (0, 7, 0), (50, 8, 0)):
            with self.subTest(current=current, account=account):
                newer = bytearray(message(0x1001, folder=current, modified=1_793_000_060))
                if account != 7:
                    struct.pack_into('<Q', newer, 10 + 888 + 22, account)
                    struct.pack_into('<Q', newer, 10 + 888 + 38, account)
                target = decode(folder(50, 'Inbox'), message(0x1001, folder=999 if current == 50 else 50), bytes(newer))
                meta, mails, _ = intake._load(target, account_id=7)
                self.assertEqual((len(mails), meta['mail_account_unresolved']), (admitted, 1 - admitted))

    def test_rejected_messages_are_counted_and_fail_coverage(self):
        # A moved ItemClass slot and an out-of-bounds subject: two keys the decoder cannot place.
        moved = bytearray(message(0x2002))
        struct.pack_into('<II', moved, 10 + SLOTS['item_class'] + 8, *struct.unpack_from('<II', moved, 10 + SLOTS['item_class']))
        struct.pack_into('<I', moved, 10 + SLOTS['item_class'] + 4, 0)
        broken = bytearray(message(0x3003, subject='Synthetic subject'))
        struct.pack_into('<I', broken, 10 + SLOTS['subject'], 0xffff_ffff)
        target = decode(folder(50, 'Inbox'), message(0x1001), bytes(moved), bytes(broken))
        meta, mails, _ = intake._load(target, account_id=7)
        self.assertEqual((len(mails), meta['messages_rejected'], meta['decoder_rejected']), (1, 2, 2))
        self.assertEqual(intake.coverage_verdict({**meta, 'subject_decoded': 1, 'body_complete': 1})
                         ['coverage_messages_shortfall'], 2)
        meta['health_mails'] = meta['health_store_mails'] = mails * 20
        self.assertIn('message_layout', intake._decoder_health(meta, 0, intake.datetime.now(intake.timezone.utc), {})['failing'])

    def test_intake_preserves_subjects_of_distinct_decoder_items(self):
        subjects = [prefix + f'Synthetic suite review {group}' for group in range(125)
                    for prefix in ('', 'Re: ', 'Fw: ', 'Fwd: ')]
        target = decode(folder(50, 'Inbox'), *(message(0x2000 + i, sender='owner@example.test',
                                                       sent=1_793_000_000 - i * 30, subject=subject)
                                               for i, subject in enumerate(subjects)))
        _, mails, _ = intake._load(target, account_id=7)
        self.assertCountEqual([m['subject'] for m in mails], subjects)
        self.assertCountEqual([intake.email_context(m, 'example.test')['subject']
                               for m in intake._best_copies(mails)], subjects)


if __name__ == '__main__':
    unittest.main()
