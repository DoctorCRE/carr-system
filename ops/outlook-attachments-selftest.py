#!/usr/bin/env python3
"""Synthetic tests for lib/outlook_attachments.py: type detection, caps, paths and storage."""
from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path
import sqlite3
import stat
import subprocess
import sys
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from lib import outlook_attachments as att

PDF = b'%PDF-1.7\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n'
OLE = b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1' + b'\0' * 504
PNG = b'\x89PNG\r\n\x1a\n' + b'\0' * 16


def ooxml(part: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as z:
        z.writestr('[Content_Types].xml', '<Types/>')
        z.writestr(part, '<x/>')
    return buf.getvalue()


def plain_zip() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as z:
        z.writestr('a.txt', 'a')
    return buf.getvalue()


class DetectTest(unittest.TestCase):
    def test_magic_bytes_decide_the_type(self):
        cases = {PDF: 'pdf', OLE: 'ole', PNG: 'png', b'\xff\xd8\xff\xe0': 'jpeg', b'GIF89a..': 'gif',
                 ooxml('word/document.xml'): 'docx', ooxml('xl/workbook.xml'): 'xlsx',
                 ooxml('ppt/presentation.xml'): 'pptx', plain_zip(): 'zip',
                 b'PK\x03\x04broken': None, b'MZ\x90\x00': None, b'': None}
        for data, kind in cases.items():
            self.assertEqual(att.detect(data), kind, data[:8])


class ExtractTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.files = self.root / 'Files'
        self.db = self.root / 'store.db'
        con = sqlite3.connect(self.db)
        con.executescript('''
            CREATE TABLE message_keys (message_id INTEGER NOT NULL, local_key INTEGER NOT NULL);
            CREATE TABLE attachments (id INTEGER PRIMARY KEY, message_key INTEGER NOT NULL, name TEXT NOT NULL,
              name_verified INTEGER NOT NULL, content_type TEXT, size INTEGER NOT NULL, path TEXT);
        ''')
        con.close()
        self.next = 1

    def add(self, name, data, message=1, declared='application/pdf', path=None, write=True):
        aid = self.next
        self.next += 1
        stem, _, ext = name.rpartition('.')
        path = path or f'~/Files/S0/42/Attachments/0/{stem}[{aid}].{ext}'
        if write and data is not None:
            target = self.files / path.removeprefix('~/Files/')
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        con = sqlite3.connect(self.db)
        con.execute('INSERT INTO message_keys VALUES (?,?)', (message, 1000 + message))
        con.execute('INSERT INTO attachments VALUES (?,?,?,?,?,?,?)',
                    (aid, 1000 + message, name, 1, declared, len(data or b''), path))
        con.commit()
        con.close()
        return aid

    def by_name(self, results):
        return {r['name']: r for r in results}

    def test_deal_documents_are_stored_by_digest_with_private_modes(self):
        self.add('LOI.pdf', PDF)
        self.add('Lease.docx', ooxml('word/document.xml'), declared='application/msword')
        out = self.root / 'out'
        found = self.by_name(att.extract(self.db, self.files, out))
        loi = found['LOI.pdf']
        self.assertEqual((loi['status'], loi['detected_type'], loi['byte_length']), ('stored', 'pdf', len(PDF)))
        self.assertEqual(loi['sha256'], hashlib.sha256(PDF).hexdigest())
        stored = Path(loi['stored'])
        self.assertEqual(stored, out / f'{loi["sha256"]}.pdf')
        self.assertEqual(stored.read_bytes(), PDF)
        self.assertEqual(stat.S_IMODE(stored.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(out.stat().st_mode), 0o700)
        self.assertEqual(found['Lease.docx']['detected_type'], 'docx')
        self.assertEqual(found['Lease.docx']['declared_type'], 'application/msword')

    def test_declared_type_and_extension_never_decide(self):
        self.add('site-plan.pdf', b'MZ\x90\x00not a pdf')
        self.add('logo.pdf', PNG)
        found = self.by_name(att.extract(self.db, self.files, self.root / 'out'))
        self.assertEqual(found['site-plan.pdf']['status'], 'type_refused')
        self.assertEqual(found['logo.pdf']['detected_type'], 'png')
        self.assertEqual(found['logo.pdf']['status'], 'type_refused')
        self.assertFalse((self.root / 'out').exists())

    def test_size_cap_and_uncached_files(self):
        self.add('big.pdf', PDF + b'x' * 100)
        self.add('later.pdf', PDF, write=False)
        found = self.by_name(att.extract(self.db, self.files, self.root / 'out', cap=len(PDF)))
        self.assertEqual(found['big.pdf']['status'], 'too_large')
        self.assertIsNone(found['big.pdf']['sha256'])
        self.assertEqual(found['later.pdf']['status'], 'not_cached')

    def test_paths_cannot_leave_the_files_copy(self):
        outside = self.root / 'secret.pdf'
        outside.write_bytes(PDF)
        self.add('a.pdf', None, path='~/Files/S0/42/Attachments/0/../../../../secret.pdf')
        self.add('b.pdf', None, path='/etc/hosts')
        link = self.files / 'S0/42/Attachments/1/c[9].pdf'
        link.parent.mkdir(parents=True)
        link.symlink_to(outside)
        self.add('c.pdf', None, path='~/Files/S0/42/Attachments/1/c[9].pdf')
        (self.files / 'S0/43').mkdir(parents=True)
        (self.files / 'S0/43/Attachments').symlink_to(self.root)
        (self.root / '0').mkdir()
        (self.root / '0' / 'd[1].pdf').write_bytes(PDF)
        self.add('d.pdf', None, path='~/Files/S0/43/Attachments/0/d[1].pdf')
        found = self.by_name(att.extract(self.db, self.files, self.root / 'out'))
        self.assertEqual(found['a.pdf']['status'], 'path_refused')
        self.assertEqual(found['b.pdf']['status'], 'path_refused')
        self.assertEqual(found['c.pdf']['status'], 'unreadable')
        self.assertEqual(found['d.pdf']['status'], 'path_refused')
        self.assertFalse((self.root / 'out').exists())

    def test_only_requested_messages_are_read(self):
        self.add('mine.pdf', PDF, message=1)
        self.add('other.pdf', PDF, message=2)
        found = att.extract(self.db, self.files, self.root / 'out', messages={1})
        self.assertEqual([r['name'] for r in found], ['mine.pdf'])
        self.assertEqual(len(list((self.root / 'out').iterdir())), 1)

    def test_a_key_shared_by_two_message_rows_links_nothing(self):
        self.add('mine.pdf', PDF, message=1)
        con = sqlite3.connect(self.db)
        con.execute('INSERT INTO message_keys VALUES (?,?)', (2, 1001))
        con.commit()
        con.close()
        self.assertEqual(att.extract(self.db, self.files, self.root / 'out'), [])

    def test_older_decoders_have_no_attachments(self):
        old = self.root / 'old.db'
        sqlite3.connect(old).execute('CREATE TABLE messages (id INTEGER)').connection.close()
        self.assertEqual(att.extract(old, self.files, self.root / 'out'), [])

    def test_cli_prints_one_json_object_per_attachment(self):
        self.add('LOI.pdf', PDF)
        done = subprocess.run([sys.executable, '-m', 'lib.outlook_attachments', str(self.db), str(self.files)],
                              cwd=ROOT, capture_output=True, text=True, check=True)
        [row] = [json.loads(line) for line in done.stdout.splitlines()]
        self.assertEqual((row['name'], row['status'], row['detected_type']), ('LOI.pdf', 'read', 'pdf'))


if __name__ == '__main__':
    unittest.main()
