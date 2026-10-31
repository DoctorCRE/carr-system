#!/usr/bin/env python3
"""Verify public-source origination adapters using synthetic records only."""
import csv
import tempfile
import copy
import importlib.util
import sys
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.routines.lead_signals import origination_rows


class OriginationTests(unittest.TestCase):
    def test_npi_delta_keeps_evidence_date_without_claiming_practice_opening(self):
        row = {'NPI': '1234567890', 'Entity Type Code': '2',
               'Provider Enumeration Date': '01/01/2020', 'Last Update Date': '10/20/2026',
               'Provider Organization Name (Legal Business Name)': 'Synthetic Dental',
               'Provider Business Practice Location Address City Name': 'Pensacola',
               'Provider Business Practice Location Address State Name': 'FL',
               'Provider Business Practice Location Address Postal Code': '32501',
               'Healthcare Provider Taxonomy Code_1': '1223G0001X'}
        output = origination_rows([row], as_of=date(2026, 10, 21))
        self.assertEqual(len(output), 1)
        self.assertEqual(output[0]['evidence_date'], '2026-10-20')
        self.assertEqual(output[0]['date_basis'], 'registry_update')
        self.assertEqual(output[0]['record_key'], '1234567890')
        self.assertNotIn('opening_date', output[0])
        drift = copy.deepcopy(row)
        del drift['Last Update Date']
        with self.assertRaisesRegex(ValueError, 'schema drift'):
            origination_rows([drift], as_of=date(2026, 10, 21))

    def test_pecos_correction_is_observed_by_publication_not_identifier_date(self):
        spec = importlib.util.spec_from_file_location("pecos", ROOT / "pipelines/radar/build-pecos-pool.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        row = dict.fromkeys(module.REQUIRED_COLUMNS, "")
        row.update(NPI="1234567890", ENRLMT_ID="I20200101000100", STATE_CD="AL",
                   ORG_NAME="Synthetic Dental", PROVIDER_TYPE_DESC="Dentist")
        first = module.origination_rows([row], published_on=date(2026, 10, 1))[0]
        row["PROVIDER_TYPE_DESC"] = "Group Practice"
        correction = module.origination_rows([row], published_on=date(2026, 10, 22))[0]
        self.assertEqual(first["record_key"], correction["record_key"])
        self.assertEqual(correction["evidence_date"], "2026-10-22")
        self.assertEqual(correction["date_basis"], "publication")
        self.assertNotIn("enrollment_date", correction)
        self.assertNotEqual(first["facts"], correction["facts"])

    def test_import_recovery_replays_only_unacknowledged_batches_and_reports_drift(self):
        from tools.import_practice_origination import import_file
        row = {'NPI': '1234567890', 'Entity Type Code': '2',
               'Provider Enumeration Date': '10/20/2026', 'Last Update Date': '10/20/2026',
               'Provider Organization Name (Legal Business Name)': 'Synthetic Dental',
               'Provider Business Practice Location Address City Name': 'Pensacola',
               'Provider Business Practice Location Address State Name': 'FL',
               'Provider Business Practice Location Address Postal Code': '32501'}
        state = {'checkpoint': None, 'status': 'available'}
        batches = []
        fail_once = True

        def call(verb, args):
            nonlocal fail_once
            if verb == 'list-practice-origination':
                return {'coverage': [{'source': 'nppes', **state}]}
            if len(batches) == 1 and fail_once and args['status'] == 'available':
                fail_once = False
                raise RuntimeError('Synthetic interruption')
            self.assertEqual(args['expected_checkpoint'], state['checkpoint'])
            batches.append(args)
            if args['status'] == 'available':
                state['checkpoint'] = args['checkpoint']
            return {'inserted': len(args['records']), **state}

        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'synthetic.csv'
            with path.open('w', newline='') as stream:
                writer = csv.DictWriter(stream, fieldnames=list(row))
                writer.writeheader()
                for index in range(101):
                    writer.writerow({**row, 'NPI': str(1234567800 + index)})
            with self.assertRaisesRegex(RuntimeError, 'interruption'):
                import_file(path, source='nppes', published_on=date(2026, 10, 21), call=call)
            result = import_file(path, source='nppes', published_on=date(2026, 10, 21), call=call)
            self.assertEqual(result['inserted'], 1)
            self.assertEqual([len(b['records']) for b in batches], [100, 1])
            import_file(path, source='nppes', published_on=date(2026, 10, 21), call=call)
            self.assertEqual(len(batches), 2)
            saved = state['checkpoint']
            path.write_text('NPI,wrong_column\n1234567890,value\n')
            with self.assertRaisesRegex(ValueError, 'schema drift'):
                import_file(path, source='nppes', published_on=date(2026, 10, 21), call=call)
            self.assertEqual(batches[-1]['status'], 'blocked')
            self.assertEqual(state['checkpoint'], saved)
            path.unlink()
            with self.assertRaises(FileNotFoundError):
                import_file(path, source='nppes', published_on=date(2026, 10, 21), call=call)
            self.assertEqual(batches[-1]['status'], 'unavailable')
            self.assertEqual(state['checkpoint'], saved)


if __name__ == '__main__':
    unittest.main()
