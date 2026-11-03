#!/usr/bin/env python3
"""Synthetic Osa ground-truth comparisons through the report and CLI interfaces."""
import gzip
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from lib.outlook_sync_truth import agreement_report

HEADER = '''<MessageHeader><InternetMessageId>&lt;one@example.test&gt;</InternetMessageId>
<Topic>Synthetic subject</Topic><Preview>Preview text</Preview>
<From><Name>Sender</Name><Address>sender@example.test</Address></From>
<SentTime>2026-01-01T00:00:00Z</SentTime></MessageHeader>'''


class AgreementTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.db = self.root / 'decoded.db'
        with closing(sqlite3.connect(self.db)) as db, db:
            db.execute('CREATE TABLE messages (message_id TEXT, subject TEXT, preview TEXT, '
                       'sender TEXT, sender_name TEXT, sent_unix INTEGER)')
            db.execute('INSERT INTO messages VALUES (?,?,?,?,?,?)',
                       ('<one@example.test>', 'Synthetic subject', 'Preview text',
                        'sender@example.test', 'Sender', 1767225600))
        self.log = self.root / 'sync.GetMessage.res.xmlgz'
        self.write(HEADER)

    def write(self, headers):
        self.log.write_bytes(gzip.compress(('<Response>' + headers + '</Response>').encode()))

    def report(self):
        return agreement_report(self.db, [self.log])

    def test_agreement_per_field(self):
        report = self.report()
        self.assertEqual(report['status'], 'complete')
        self.assertEqual(set(report['fields']), {'subject', 'preview', 'sender', 'sender_name', 'sent_unix'})
        for counts in report['fields'].values():
            self.assertEqual(counts['agreed'], 1)
            self.assertEqual(sum(counts.values()), 1)

    def test_disagreement_is_not_success(self):
        self.write(HEADER.replace('Synthetic subject', 'Other subject'))
        report = self.report()
        self.assertEqual(report['fields']['subject']['disagreed'], 1)
        self.assertEqual(report['status'], 'disagreement')

    def test_redaction_is_not_agreement(self):
        self.write(HEADER.replace('Synthetic subject', 'pii:140D5125557024F9'))
        report = self.report()
        self.assertEqual(report['fields']['subject']['redacted'], 1)
        self.assertEqual(report['status'], 'incomplete')

    def test_redacted_identity_never_joins(self):
        self.write(HEADER.replace('&lt;one@example.test&gt;', 'pii:140D5125557024F9'))
        report = self.report()
        self.assertEqual(report['headers_unjoined'], 1)
        self.assertTrue(all(c['unjoined'] == 1 for c in report['fields'].values()))
        self.assertEqual(report['status'], 'incomplete')

    def test_reused_message_id_is_ambiguous(self):
        with closing(sqlite3.connect(self.db)) as db, db:
            db.execute('INSERT INTO messages SELECT * FROM messages')
        report = self.report()
        self.assertEqual(report['headers_ambiguous'], 1)
        self.assertEqual(report['fields']['subject']['ambiguous'], 1)
        self.assertEqual(report['status'], 'incomplete')

    def test_repeated_log_headers_do_not_inflate_counts(self):
        self.write(HEADER * 2)
        report = self.report()
        self.assertEqual(report['headers_read'], 2)
        self.assertEqual(report['duplicate_headers'], 1)
        self.assertEqual(report['fields']['subject']['agreed'], 1)

    def test_conflicting_log_revisions_are_ambiguous(self):
        self.write(HEADER + HEADER.replace('Synthetic subject', 'Other subject'))
        report = self.report()
        self.assertEqual(report['fields']['subject']['ambiguous'], 2)
        self.assertEqual(report['status'], 'incomplete')

    def test_missing_log_and_decoded_fields_are_distinct(self):
        self.write(HEADER.replace('<Topic>Synthetic subject</Topic>', ''))
        with closing(sqlite3.connect(self.db)) as db, db:
            db.execute('UPDATE messages SET sender_name=NULL WHERE message_id=?', ('<one@example.test>',))
        report = self.report()
        self.assertEqual(report['fields']['subject']['missing_log'], 1)
        self.assertEqual(report['fields']['sender_name']['missing_decoded'], 1)

    def test_missing_log_set_is_not_success(self):
        report = agreement_report(self.db, [])
        self.assertEqual(report['status'], 'incomplete')
        self.assertEqual(report['decoded_uncompared'], 1)

    def test_bad_input_is_counted_without_content(self):
        self.log.write_bytes(gzip.compress(b'<secret-mailbox>'))
        report = self.report()
        self.assertEqual(report['input_failures'], {'invalid_xml': 1})
        self.assertEqual(report['status'], 'failed')
        self.assertNotIn('secret-mailbox', json.dumps(report))

    def test_entities_are_rejected(self):
        self.log.write_bytes(gzip.compress(b'<!DOCTYPE a [<!ENTITY e "secret">]><a>&e;</a>'))
        self.assertEqual(self.report()['input_failures'], {'unsafe_xml': 1})

    def test_namespaces_and_dotnet_ticks(self):
        self.write(HEADER.replace('<MessageHeader>', '<MessageHeader xmlns="urn:osa">')
                   .replace('2026-01-01T00:00:00Z', '639028224000000000'))
        self.assertEqual(self.report()['status'], 'complete')

    def test_invalid_timestamp_is_not_equal(self):
        self.write(HEADER.replace('2026-01-01T00:00:00Z', 'garbage'))
        self.assertEqual(self.report()['fields']['sent_unix']['invalid_log'], 1)

    def test_empty_and_wrong_response_do_not_succeed(self):
        self.write('')
        self.assertEqual(self.report()['input_failures'], {'no_message_headers': 1})

    def test_truncated_gzip_and_missing_file_are_counted(self):
        self.log.write_bytes(self.log.read_bytes()[:20])
        report = agreement_report(self.db, [self.log, self.root / 'absent'])
        self.assertEqual(report['input_failures'], {'unreadable_gzip': 2})
        self.assertEqual(report['status'], 'failed')

    def test_schema_error_does_not_echo_path_or_sql(self):
        report = agreement_report(self.root / 'secret-path', [self.log])
        self.assertEqual(report['input_failures'], {'decoded_unreadable_or_schema': 1})
        self.assertNotIn('secret-path', json.dumps(report))

    def test_absent_id_and_absent_decoded_message_are_unjoined(self):
        for identity in ('', '&lt;missing@example.test&gt;'):
            self.write(HEADER.replace('&lt;one@example.test&gt;', identity))
            self.assertEqual(self.report()['headers_unjoined'], 1)

    def test_duplicate_field_is_not_arbitrarily_selected(self):
        self.write(HEADER.replace('<Topic>', '<Topic>Another</Topic><Topic>'))
        self.assertEqual(self.report()['input_failures'], {'duplicate_field': 1})

    def test_cli_has_only_aggregate_output_and_read_only_inputs(self):
        before = self.db.read_bytes(), self.log.read_bytes()
        command = [sys.executable, str(ROOT / 'tools/outlook-sync-truth.py'),
                   '--decoded', str(self.db), '--logs', str(self.log)]
        done = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(json.loads(done.stdout)['status'], 'complete')
        for secret in ('example.test', 'Synthetic subject', 'Preview text', str(self.root)):
            self.assertNotIn(secret, done.stdout + done.stderr)
        self.assertEqual(before, (self.db.read_bytes(), self.log.read_bytes()))
        self.write(HEADER.replace('Synthetic subject', 'pii:123'))
        done = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(done.returncode, 1)
        self.assertEqual(json.loads(done.stdout)['status'], 'incomplete')


if __name__ == '__main__':
    unittest.main()
