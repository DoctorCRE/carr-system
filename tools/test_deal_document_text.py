"""Test deterministic deal text retention and idempotent backfill."""
import hashlib
from pathlib import Path
import sys
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib.deal_document_text import derivative, backfill

class TextTests(unittest.TestCase):
    def test_complete_pages_and_hash(self):
        doc = {'document_ref': 'doc', 'entity_ref': 'deal', 'content_hash': hashlib.sha256(b'pdf').hexdigest(), 'mime': 'application/pdf', 'bytes': 3}
        value = derivative(doc, b'pdf', lambda _: ['first\n\nlast\n', 'second'])
        self.assertEqual(value['pages'], ['first\n\nlast\n', 'second'])
        self.assertEqual(value['text_format_version'], 'page-text.v1')
        self.assertEqual(value['status'], 'produced')
        with self.assertRaisesRegex(ValueError, 'hash'):
            derivative(doc, b'new', lambda _: [])

    def test_limits_and_no_text_are_explicit(self):
        doc = dict(document_ref='doc', entity_ref='deal', content_hash=hashlib.sha256(b'x').hexdigest(), bytes=1)
        self.assertEqual(derivative(doc, b'x', lambda _: ['x' * 500001])['status'], 'unavailable')
        self.assertEqual(derivative(doc, b'x', lambda _: [''] * 1001)['pages'], [])
        self.assertEqual(derivative(doc, b'x', lambda _: [' \n'])['status'], 'no_text')

    def test_backfill_dry_run_rerun_mixed_and_superseding_hash(self):
        docs = [{'document_ref': str(n), 'entity_ref': 'deal', 'content_hash': hashlib.sha256(data).hexdigest(), 'mime': 'application/pdf', 'bytes': len(data), 'data': data} for n, data in enumerate([b'text', b''])]
        saved = {}
        def record(value):
            saved[(value['document_ref'], value['content_hash'])] = value
        def scan(after, limit):
            return [{**d, 'retained': (d['document_ref'], d['content_hash']) in saved} for d in docs if after is None or d['document_ref'] > after][:limit]
        args = dict(scan=scan, read_bytes=lambda d: d['data'], text_pages=lambda data: [data.decode()], record=record, max_documents=100)
        dry = backfill(**args, dry_run=True)
        self.assertEqual((dry['missing'], len(saved)), (2, 0))
        result = backfill(**args)
        self.assertEqual((result['produced'], result['no_text']), (1, 1))
        self.assertEqual(backfill(**args)['skipped'], 2)
        docs[0].update(data=b'new', bytes=3, content_hash=hashlib.sha256(b'new').hexdigest())
        self.assertEqual(backfill(**args)['produced'], 1)
        self.assertEqual(len(saved), 3)

if __name__ == '__main__':
    unittest.main()
