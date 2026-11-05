"""Synthetic CLI coverage for the retained email census."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

CLI = Path(__file__).with_name('email-application-census.py')
SINCE = '2026-11-01T00:00:00-06:00'
UNTIL = '2026-11-03T00:00:00-06:00'
ASOF = '2026-11-03T01:00:00-06:00'
ACCOUNT = 'private-synthetic@example.test'


def request(kind='email_in', ack=True, **changes):
    payload = dict(kind=kind, ref='party:synthetic', occurred_at=SINCE,
                   summary='PRIVATE_SUBJECT', detail='PRIVATE_BODY',
                   email_identity=dict(mailbox_account=ACCOUNT, item_id='hxstore-key:one'))
    payload.update(changes.pop('payload', {}))
    return dict(verb='log-activity', payload=json.dumps(payload), acknowledged=ack,
                created_at=1793512800, attempts=3, **changes)


class CensusTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.receipts = self.root / 'receipts'
        self.journal = self.root / 'journal'
        self.receipts.mkdir()
        self.journal.mkdir()
        self.state = dict(mail_ids_account=hashlib.sha256(ACCOUNT.encode()).hexdigest(), requests={})
        self.rows = []

    def row(self, at=SINCE, counts=None, **changes):
        return dict(service='outlook-intake', started_at=at,
                    detail=dict(state='succeeded', counts=counts or
                                dict(run=dict(emails=1, logged=1), backlog=dict(admitted=9))), **changes)

    def run_cli(self, *extra):
        (self.journal / 'state.json').write_text(json.dumps(self.state))
        (self.receipts / 'runs.jsonl').write_text('\n'.join(json.dumps(r) for r in self.rows))
        before = {p.relative_to(self.root): p.read_bytes() if p.is_file() else None for p in self.root.rglob('*')}
        result = subprocess.run([sys.executable, str(CLI), '--receipts-dir', str(self.receipts),
                                 '--journal-dir', str(self.journal), '--account', ACCOUNT,
                                 '--since', SINCE, '--until', UNTIL, '--as-of', ASOF, *extra],
                                capture_output=True, text=True)
        after = {p.relative_to(self.root): p.read_bytes() if p.is_file() else None for p in self.root.rglob('*')}
        self.assertEqual(before, after)
        for secret in (ACCOUNT, 'PRIVATE_SUBJECT', 'PRIVATE_BODY', 'party:synthetic', str(self.root)):
            self.assertNotIn(secret, result.stdout + result.stderr)
        return result, json.loads(result.stdout) if result.returncode == 0 else None

    def test_two_runs_one_message_and_multi_party_effects(self):
        self.rows = [self.row(), self.row('2026-11-02T00:00:00-06:00',
                     counts=dict(run=dict(emails=1), backlog=dict(admitted=10)))]
        self.state['requests'] = dict(first=request(), second=request())
        result, report = self.run_cli()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(report['counts']['run']['emails'], 2)
        self.assertEqual(report['counts']['backlog']['admitted'], 10)
        self.assertEqual(report['counts']['retained']['acknowledged_contact_effects'], 2)
        self.assertEqual(report['counts']['retained']['source_identities'], 1)
        self.assertEqual(report['completeness']['unique_message_census'], 'incomplete')

    def test_failed_then_success_refused_blocked_and_pending(self):
        self.state['requests'] = dict(success=request(), refused=request(disposition='refused'),
                                     blocked=request(ack=False, disposition='blocked'), pending=request(ack=False))
        _, report = self.run_cli()
        kept = report['counts']['retained']
        self.assertEqual([kept[k] for k in ('requests', 'acknowledged_contact_effects', 'refused', 'blocked', 'pending')],
                         [4, 1, 1, 1, 1])
        self.assertEqual(kept['partial_source_identities'], 1)

    def test_rekey_legacy_malformed_mixed_calendar_and_window(self):
        self.rows = [self.row(counts=dict(emails=5)), self.row(counts=dict(run={'PRIVATE_SUBJECT': 9}, backlog={})),
                     self.row(service_override=1)]
        self.rows[2]['detail'] = 'PRIVATE_BODY'
        self.state['requests'] = dict(first=request(), rekey=request(payload=dict(
            email_identity=dict(mailbox_account=ACCOUNT, item_id='hxstore-key:changed'))),
            calendar=request(kind='meeting'), old=request(payload=dict(occurred_at='2020-01-01T00:00:00Z')),
            other=request(payload=dict(email_identity=dict(mailbox_account='other@example.test', item_id='other'))))
        _, report = self.run_cli()
        self.assertEqual(report['counts']['retained']['requests'], 2)
        self.assertEqual(report['counts']['retained']['source_identities'], 2)
        self.assertEqual(report['counts']['receipts']['legacy'], 1)
        self.assertEqual(report['counts']['receipts']['malformed'], 1)
        self.assertTrue(report['completeness']['rekeys_unresolved'])

    def test_retention_holes_missing_identity_and_unknown_account(self):
        self.state['requests'] = dict(legacy=request(payload=dict(email_identity=None)))
        _, report = self.run_cli('--since', '2026-01-01T00:00:00Z')
        self.assertTrue(report['completeness']['retention_window_crossed'])
        self.assertEqual(report['counts']['retained']['missing_source_identity'], 1)
        self.state.pop('mail_ids_account')
        _, report = self.run_cli()
        self.assertEqual(report['counts']['retained']['account_unresolved'], 1)
        self.assertEqual(report['counts']['retained']['requests'], 0)

    def test_invalid_dates_state_and_cli_errors_are_redacted(self):
        for extra in [('--since', 'PRIVATE_BODY'), ('--until', '2026-11-04T00:00:00Z'),
                      ('--since', '2026-11-01T00:00:00'), ('--PRIVATE_BODY',)]:
            result, _ = self.run_cli(*extra)
            self.assertEqual(result.returncode, 2)
        self.state['requests'] = []
        result, _ = self.run_cli()
        self.assertEqual(result.returncode, 2)

    def test_non_intake_and_out_of_run_window_excluded(self):
        self.rows = [self.row(at=UNTIL), self.row()]
        self.rows[1]['service'] = 'calendar-intake'
        _, report = self.run_cli()
        self.assertEqual(report['counts']['receipts']['selected'], 0)
        self.assertEqual(report['counts']['run'], {})


    def test_injected_clock_read_caps_and_real_receipt_contract(self):
        from importlib.util import module_from_spec, spec_from_file_location
        from datetime import datetime
        spec = spec_from_file_location('census_test_module', CLI)
        module = module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        from lib.outlook_intake import receipt_counts
        self.rows = [self.row(counts=receipt_counts(dict(emails=2, admitted=8, requests_blocked=1,
                                                       decoder_alarm=0, signature_recorded=1)))]
        _, report = self.run_cli()
        self.assertEqual(report['counts']['run']['emails'], 2)
        self.assertEqual(report['counts']['backlog']['admitted'], 8)
        self.assertEqual(report['counts']['receipts']['malformed'], 0)
        with self.assertRaises(ValueError):
            module.census([], self.state, ACCOUNT,
                          module.Window(module.timestamp(SINCE), module.timestamp(UNTIL), module.timestamp(ASOF)),
                          lambda: datetime.fromisoformat(SINCE))
        path = self.root / 'oversize'
        path.write_bytes(b'x' * (module.MAX_FILE_BYTES + 1))
        with self.assertRaises(ValueError):
            module.PrivateReads().read(path)
        path.unlink()
        path.symlink_to(self.journal / 'state.json')
        with self.assertRaises(ValueError):
            module.PrivateReads().read(path)
        self.state['requests'] = dict.fromkeys(range(module.MAX_REQUESTS + 1))
        result, _ = self.run_cli()
        self.assertEqual(result.returncode, 2)

    def test_malformed_rows_and_missing_directories_do_not_leak(self):
        self.rows = [None, self.row(counts=dict(run=dict(emails=True), backlog={})),
                     self.row(at='PRIVATE_BODY')]
        _, report = self.run_cli()
        self.assertEqual(report['counts']['receipts']['malformed'], 3)
        result, _ = self.run_cli('--journal-dir', str(self.root / 'PRIVATE_BODY'))
        self.assertEqual(result.returncode, 2)

    def test_file_count_limit(self):
        for i in range(1000):
            (self.receipts / f'{i}.ignored').touch()
        result, _ = self.run_cli()
        self.assertEqual(result.returncode, 2)

    def test_row_count_limit(self):
        self.rows = [None] * 100001
        result, _ = self.run_cli()
        self.assertEqual(result.returncode, 2)

    def test_aggregate_byte_limit(self):
        for i in range(4):
            (self.receipts / f'{i}.jsonl').write_bytes(b' ' * (16 * 1024 * 1024))
        result, _ = self.run_cli()
        self.assertEqual(result.returncode, 2)


if __name__ == '__main__':
    unittest.main()
