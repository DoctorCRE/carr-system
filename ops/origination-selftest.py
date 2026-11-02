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


def florida_fixture(**changes):
    """Synthetic corporate record using the official 1-based starts and widths."""
    fields = {
        'document_number': (1, 12, 'L26000000001'),
        'name': (13, 192, 'Synthetic Dental LLC'),
        'status': (205, 1, 'A'), 'filing_type': (206, 15, 'FLAL'),
        'address': (221, 42, 'SYNTHETIC_LOCATION_A'),
        'address_2': (263, 42, 'SYNTHETIC_SUITE'),
        'city': (305, 28, 'Pensacola'), 'state': (333, 2, 'FL'),
        'postal_code': (335, 10, '32501'), 'country': (345, 2, 'US'),
        'filing_on': (473, 8, '20261019'),
        'registered_agent_address': (588, 42, 'SYNTHETIC_AGENT_LOCATION'),
    }
    raw = [' '] * 1440
    for key, (start, width, default) in fields.items():
        value = changes.get(key, default)
        assert len(value) <= width
        raw[start - 1:start - 1 + width] = value.ljust(width)
    return ''.join(raw)


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

    def test_florida_fixed_width_document_identity_and_filing_date(self):
        from tools.import_practice_origination import florida_rows
        line = florida_fixture()
        row = florida_rows(line, published_on=date(2026, 10, 21), line_number=1)[0]
        self.assertEqual(row['record_key'], 'L26000000001')
        self.assertEqual(row['document_number'], 'L26000000001')
        self.assertNotIn('npi', row)
        self.assertEqual(row['filing_on'], '2026-10-19')
        self.assertEqual(row['evidence_date'], '2026-10-19')
        self.assertEqual(row['date_basis'], 'filing')
        self.assertEqual(row['city'], 'Pensacola')
        self.assertEqual(row['state'], 'FL')
        self.assertEqual(row['facts']['address'], 'SYNTHETIC_LOCATION_A')
        self.assertEqual(row['facts']['registered_agent_address'], 'SYNTHETIC_AGENT_LOCATION')
        self.assertEqual(row['facts']['postal_code'], '32501')
        self.assertEqual(florida_rows(line.replace('32501'.ljust(10), '33101'.ljust(10)),
                                    published_on=date(2026, 10, 21), line_number=2), [])
        self.assertEqual(florida_rows(line.replace('Dental', 'Retail'),
                                    published_on=date(2026, 10, 21), line_number=3), [])
        with self.assertRaisesRegex(ValueError, 'line 4.*width'):
            florida_rows(line[:-1], published_on=date(2026, 10, 21), line_number=4)

    def test_florida_mixed_file_skips_out_of_scope_and_counts_rows(self):
        from tools.import_practice_origination import import_file
        line = florida_fixture()
        variants = [line, florida_fixture(name='Synthetic Retail LLC'),
                    florida_fixture(postal_code=''), florida_fixture(state='ON', postal_code='M5V 2T6'),
                    florida_fixture(state=''), florida_fixture(postal_code='33101'),
                    florida_fixture(state='NY')]
        batches = []

        def call(verb, args):
            if verb == 'list-practice-origination':
                return {'coverage': [{'source': 'formation-fl', 'enabled': True, 'checkpoint': None}]}
            batches.append(args)
            return {'inserted': len(args['records']), 'checkpoint': args['checkpoint']}

        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'synthetic.txt'
            path.write_text('\n'.join(variants) + '\n')
            result = import_file(path, source='formation-fl', published_on=date(2026, 10, 21), call=call)
        self.assertEqual(result['inserted'], 1)
        self.assertEqual(result['processed'], 7)
        self.assertEqual(result['skipped'], 6)
        self.assertEqual(len(batches), 1)
        self.assertEqual(batches[0]['status'], 'available')
        self.assertEqual(batches[0]['records'][0]['document_number'], 'L26000000001')
        self.assertIn('6 out-of-scope rows skipped', batches[0]['detail'])

    def test_florida_zero_matches_imports_empty_and_counted(self):
        from tools.import_practice_origination import import_file
        for lines in [[], [florida_fixture(name='Synthetic Retail LLC')],
                      [florida_fixture(state='NY', postal_code='10001')] * 3]:
            batches = []

            def call(verb, args):
                if verb == 'list-practice-origination':
                    return {'coverage': [{'source': 'formation-fl', 'enabled': True, 'checkpoint': None}]}
                batches.append(args)
                return {'inserted': len(args['records']), 'checkpoint': args['checkpoint']}

            with self.subTest(rows=len(lines)), tempfile.TemporaryDirectory() as temp:
                path = Path(temp) / 'synthetic.txt'
                path.write_text(''.join(line + '\n' for line in lines))
                result = import_file(path, source='formation-fl', published_on=date(2026, 10, 21), call=call)
                self.assertEqual(result['inserted'], 0)
                self.assertEqual(result['processed'], len(lines))
                self.assertEqual(result['skipped'], len(lines))
                self.assertEqual(len(batches), 1)
                self.assertEqual(batches[0]['status'], 'available')
                self.assertEqual(batches[0]['records'], [])
                self.assertTrue(result['checkpoint'].endswith(':' + str(len(lines))))
                self.assertIn(f"{len(lines)} out-of-scope rows skipped", batches[0]['detail'])

    def test_florida_document_number_accepts_only_six_or_twelve_characters(self):
        from tools.import_practice_origination import florida_rows
        for document in ['A00001', '000001', 'L26000000001', '      A00001']:
            with self.subTest(document=document):
                row = florida_rows(florida_fixture(document_number=document),
                                   published_on=date(2026, 10, 21), line_number=1)[0]
                self.assertEqual(row['document_number'], document.strip())
                self.assertEqual(row['record_key'], document.strip())
        for document in ['', 'A0000', 'A000001', 'L2600000000', 'A!0001', '   A00001   ', '\tA00001']:
            with self.subTest(document=document), self.assertRaisesRegex(ValueError, 'document identity'):
                florida_rows(florida_fixture(document_number=document), published_on=date(2026, 10, 21), line_number=2)

    def test_florida_unreadable_rows_still_fail_even_out_of_scope(self):
        from tools.import_practice_origination import florida_rows
        line = florida_fixture(name='Synthetic Retail LLC', city='', state='', postal_code='')
        self.assertEqual(florida_rows(line, published_on=date(2026, 10, 21), line_number=1), [])
        for bad, error in [(line[:-1], 'width'), ('!' + line[1:], 'document identity'),
                           (line[:472] + '20260230' + line[480:], 'filing date')]:
            with self.subTest(error=error), self.assertRaisesRegex(ValueError, error):
                florida_rows(bad, published_on=date(2026, 10, 21), line_number=2)

    def test_florida_import_disabled_and_bad_batch_preserves_checkpoint(self):
        from tools.import_practice_origination import import_file, FLORIDA_SOURCE
        state = {'checkpoint': None, 'status': 'available', 'enabled': False}
        batches = []

        def call(verb, args):
            if verb == 'list-practice-origination':
                return {'coverage': [{'source': 'formation-fl', **state}]}
            self.assertEqual(args['expected_checkpoint'], state['checkpoint'])
            batches.append(args)
            if args['status'] == 'available':
                state['checkpoint'] = args['checkpoint']
            return {'inserted': len(args['records']), **state}

        self.assertFalse(FLORIDA_SOURCE['enabled'])
        from tools.routines.lead_signals import PREFIXES
        self.assertTrue(set(FLORIDA_SOURCE['territory_zip_prefixes']).issubset(PREFIXES))
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'synthetic.txt'
            line = florida_fixture()
            path.write_text(line + '\n')
            with self.assertRaisesRegex(ValueError, 'disabled'):
                import_file(path, source='formation-fl', published_on=date(2026, 10, 21), call=call)
            self.assertEqual(batches, [])
            state['enabled'] = True
            result = import_file(path, source='formation-fl', published_on=date(2026, 10, 21), call=call)
            self.assertEqual(result['inserted'], 1)
            import_file(path, source='formation-fl', published_on=date(2026, 10, 21), call=call)
            self.assertEqual(len(batches), 1)
            saved = state['checkpoint']
            path.write_text(line + '\ninvalid row\n')
            with self.assertRaisesRegex(ValueError, 'line 2.*width'):
                import_file(path, source='formation-fl', published_on=date(2026, 10, 21), call=call)
            self.assertEqual(batches[-1]['records'], [])
            self.assertEqual(batches[-1]['status'], 'blocked')
            self.assertEqual(state['checkpoint'], saved)

    def test_florida_date_zip_and_padding_variants_are_explicit(self):
        from tools.import_practice_origination import florida_rows
        for filed in ['20261019', '10192026']:
            for postal in ['32501', '325011234', '32501-1234']:
                row = florida_rows(florida_fixture(filing_on=filed, postal_code=postal),
                                   published_on=date(2026, 10, 21), line_number=1)[0]
                self.assertEqual(row['filing_on'], '2026-10-19')
                self.assertEqual(row['facts']['postal_code'], '32501' if postal == '32501' else '32501-1234')
        for filed in ['20260230', '02302026', '20261022', '2026 019', '10111213', '']:
            with self.subTest(filed=filed), self.assertRaisesRegex(ValueError, 'filing date'):
                florida_rows(florida_fixture(filing_on=filed),
                             published_on=date(2026, 10, 21), line_number=1)
        for postal in ['32501-123', '32501 1234', 'ABCDE', '3250112345']:
            with self.subTest(postal=postal), self.assertRaisesRegex(ValueError, 'ZIP'):
                florida_rows(florida_fixture(postal_code=postal),
                             published_on=date(2026, 10, 21), line_number=1)

    def test_florida_file_preflight_blocks_before_any_candidate_batch(self):
        from tools.import_practice_origination import import_file
        line = florida_fixture()
        skipped = florida_fixture(name='Synthetic Retail LLC')
        shifted = line[:332] + line[333:] + ' '
        cases = [([], 'all 0 rows skipped'), ([skipped] * 3, 'all 3 rows skipped'),
                 ([line] * 100 + [line[:336]], 'width'),
                 ([line[:332] + 'NY' + line[334:]] * 3, 'all 3 rows skipped'),
                 ([line] * 100 + [shifted], 'filing date'),
                 ([line] + [skipped] * 1000, 'implausible skip share')]
        for lines, error in cases:
            batches = []

            def call(verb, args):
                if verb == 'list-practice-origination':
                    return {'coverage': [{'source': 'formation-fl', 'enabled': True,
                                          'checkpoint': None}]}
                batches.append(args)
                return {'inserted': len(args['records']), 'checkpoint': None}

            with self.subTest(error=error), tempfile.TemporaryDirectory() as temp:
                path = Path(temp) / 'synthetic.txt'
                path.write_text(''.join(raw + '\n' for raw in lines))
                with self.assertRaisesRegex(ValueError, error):
                    import_file(path, source='formation-fl', published_on=date(2026, 10, 21), call=call)
                self.assertEqual(len(batches), 1)
                self.assertEqual(batches[0]['status'], 'blocked')
                self.assertEqual(batches[0]['records'], [])

    def test_florida_skip_threshold_and_resume_count_the_whole_file(self):
        from tools.import_practice_origination import import_file
        state = {'checkpoint': None, 'status': 'available'}
        batches = []
        fail_once = True

        def call(verb, args):
            nonlocal fail_once
            if verb == 'list-practice-origination':
                return {'coverage': [{'source': 'formation-fl', 'enabled': True, **state}]}
            if len(batches) == 1 and fail_once:
                fail_once = False
                raise RuntimeError('Synthetic interruption')
            batches.append(args)
            state['checkpoint'] = args['checkpoint']
            return {'inserted': len(args['records']), **state}

        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'synthetic.txt'
            path.write_text((florida_fixture() + '\n') +
                            (florida_fixture(name='Synthetic Retail LLC') + '\n') * 999)
            with self.assertRaisesRegex(RuntimeError, 'interruption'):
                import_file(path, source='formation-fl', published_on=date(2026, 10, 21), call=call)
            result = import_file(path, source='formation-fl', published_on=date(2026, 10, 21), call=call)
            self.assertEqual(result['processed'], 1000)
            self.assertEqual(result['skipped'], 999)
            self.assertEqual(sum(len(batch['records']) for batch in batches), 1)
            self.assertIn('999 out-of-scope rows skipped through checkpoint in this file', batches[-1]['detail'])
            count = len(batches)
            import_file(path, source='formation-fl', published_on=date(2026, 10, 21), call=call)
            self.assertEqual(len(batches), count)

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
