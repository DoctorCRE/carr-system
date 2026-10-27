#!/usr/bin/env python3
"""Synthetic tests for lib/outlook_deal_files.py: filing matched-mail attachments on a deal."""
from __future__ import annotations

import hashlib
import io
from pathlib import Path
import sys
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from lib import outlook_attachments as att
from lib import outlook_deal_files as files
from lib import r2_archive

PDF = b'%PDF-1.7\n1 0 obj<<>>endobj\n%%EOF\n'
PNG = b'\x89PNG\r\n\x1a\n' + b'\0' * 16
SHA = hashlib.sha256(PDF).hexdigest()


def docx() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as z:
        z.writestr('[Content_Types].xml', '<Types/>')
        z.writestr('word/document.xml', '<x/>')
    return buf.getvalue()


class Fakes:
    def __init__(self, upload_error: Exception | None = None, verb_error: Exception | None = None, existing: bool = False):
        self.puts: list[tuple[str, str, bytes]] = []
        self.calls: list[tuple[str, dict]] = []
        self.upload_error, self.verb_error, self.existing = upload_error, verb_error, existing

    def put(self, key, sha, data):
        if self.upload_error:
            raise self.upload_error
        self.puts.append((key, sha, data))
        return {'uploaded': True}

    def carr(self, verb, args):
        self.calls.append((verb, args))
        if self.verb_error:
            raise self.verb_error
        return {'ok': True, 'attachment_id': 'att-1', 'existing': self.existing}

    def file(self, data=PDF, meta=None, deal='Synthetic Deal A'):
        meta = {'id': 7, 'name': 'Lease.pdf'} if meta is None else meta
        return files.file_attachment(deal, meta, data, message_id='msg-9', client_ref='C-1',
                                     put_object=self.put, carr=self.carr)


class FileAttachmentTest(unittest.TestCase):
    def test_pdf_is_uploaded_then_recorded_on_the_deal(self):
        fake = Fakes()
        result = fake.file()
        self.assertEqual(result, {'status': 'stored', 'sha256': SHA, 'attachment_id': 'att-1'})
        key = f'documents/C-1/{SHA[:16]}/Lease.pdf'
        self.assertEqual(fake.puts, [(key, SHA, PDF)])
        verb, args = fake.calls[0]
        self.assertEqual(verb, 'attach-deal-file')
        self.assertEqual({k: v for k, v in args.items() if k != 'idempotency_key'},
                         {'deal': 'Synthetic Deal A', 'r2_key': key, 'filename': 'Lease.pdf', 'mime': 'application/pdf',
                          'sha256': SHA, 'bytes': len(PDF), 'source': 'msg-9/7'})

    def test_rerun_sends_the_same_idempotency_key(self):
        a, b = Fakes(), Fakes(existing=True)
        a.file(), b.file()
        self.assertEqual(a.calls[0][1]['idempotency_key'], b.calls[0][1]['idempotency_key'])
        self.assertEqual(b.file()['status'], 'existing')
        other = Fakes()
        other.file(meta={'id': 8, 'name': 'Lease.pdf'})
        self.assertNotEqual(a.calls[0][1]['idempotency_key'], other.calls[0][1]['idempotency_key'])

    def test_type_comes_from_the_bytes_not_the_name(self):
        fake = Fakes()
        self.assertEqual(fake.file(PNG, {'id': 1, 'name': 'Lease.pdf'}), {'status': 'not_deal_type'})
        self.assertEqual(fake.file(b'', {'id': 1, 'name': 'x.pdf'}), {'status': 'not_deal_type'})
        self.assertEqual((fake.puts, fake.calls), ([], []))
        fake.file(docx(), {'id': 2, 'name': 'Terms'})
        self.assertTrue(fake.calls[0][1]['filename'].endswith('Terms.docx'))
        self.assertEqual(fake.calls[0][1]['mime'], att.DEAL_TYPES['docx'])

    def test_oversize_is_refused_before_upload(self):
        fake = Fakes()
        big = PDF + b'0' * att.CAP
        self.assertEqual(fake.file(big), {'status': 'too_large'})
        self.assertEqual(fake.puts, [])

    def test_hostile_names_cannot_escape_the_key(self):
        fake = Fakes()
        fake.file(meta={'id': 1, 'name': '..\\..\\etc/pass wd\x00.pdf'})
        key = fake.puts[0][0]
        self.assertRegex(key, rf'^documents/C-1/{SHA[:16]}/[A-Za-z0-9._-]+$')
        self.assertNotIn('..', key.split('/')[1:3])
        fake.file(meta={'id': 2, 'name': '..'})
        self.assertRegex(fake.puts[1][0], rf'/{SHA[:16]}/attachment\.pdf$')

    def test_quota_and_failures_are_statuses_and_leak_nothing(self):
        quota = Fakes(upload_error=r2_archive.QuotaExceeded('over: Lease.pdf', {}))
        self.assertEqual(quota.file(), {'status': 'quota_refused', 'sha256': SHA})
        self.assertEqual(quota.calls, [])
        broken = Fakes(upload_error=OSError('Lease.pdf failed'))
        self.assertEqual(broken.file(), {'status': 'upload_failed', 'sha256': SHA, 'error': 'OSError'})
        refused = Fakes(verb_error=RuntimeError('deal Synthetic Deal A refused'))
        result = refused.file()
        self.assertEqual(result, {'status': 'verb_failed', 'sha256': SHA, 'error': 'RuntimeError'})
        self.assertNotIn('Lease', str(result))


if __name__ == '__main__':
    unittest.main()
