import unittest
from lib.email_critical_dates import extract
from lib.outlook_intake import email_context

class CriticalDates(unittest.TestCase):
    def test_absolute_and_relative_obligations_survive_for_server_review(self):
        source = {'activity_id': 'source', 'deal': 'deal', 'body': 'The agreed LOI expiration date is 2026-11-01.\nThe contingency deadline is next Friday.'}
        rows = extract(source)
        self.assertEqual([r['obligation_kind'] for r in rows], ['loi_expiry', 'contingency_deadline'])
        self.assertTrue(all(r['mode'] == 'critical_date' for r in rows))
        self.assertEqual(extract({**source, 'deal': None}), [])
    def test_tasks_are_not_legal_dates(self):
        self.assertEqual(extract({'deal': 'deal', 'body': 'Please call me on 2026-11-01.'}), [])

    def test_attachment_evidence_is_counted_without_content(self):
        mail = {'sender': 'fixture@example.test'}
        self.assertIsNone(email_context(mail, 'example.test')['attachment_count'])
        self.assertEqual(email_context({**mail, 'attachments': []}, 'example.test')['attachment_count'], 0)
        self.assertEqual(email_context({**mail, 'attachments': [{}]}, 'example.test')['attachment_count'], 1)

if __name__ == '__main__': unittest.main()
