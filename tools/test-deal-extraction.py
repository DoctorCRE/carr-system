"""Test cited deal extraction through the typed job interface with synthetic sources."""
import hashlib
import json
from pathlib import Path
import sys
import unittest
from uuid import UUID

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib.deal_extraction import ExtractionJob, run_job

DOC = '00000000-0000-4000-8000-000000000039'
ENTITY = '00000000-0000-4000-8000-000000000040'
KEY = '00000000-0000-4000-8000-000000000041'
TEXT = b'Base rent is $24 per SF per year NNN. Term is 60 months.'


def output(rent='24'):
    return {'fields': [
        {'fact_key': 'base_rent_per_sf', 'status': 'observed', 'value': rent,
         'unit': 'USD/SF/year', 'expense_basis': 'nnn', 'page': 1,
         'cited_text': 'Base rent is $24 per SF per year NNN.', 'region': 'scalar'},
        {'fact_key': 'term_months', 'status': 'observed', 'value': '60',
         'unit': 'months', 'page': 1, 'cited_text': 'Term is 60 months.', 'region': 'scalar'},
        {'fact_key': 'landlord_ti_per_sf', 'status': 'not_present', 'reason': 'absent'},
        {'fact_key': 'free_rent_months', 'status': 'not_present', 'reason': 'absent'},
    ]}


class Source:
    def __init__(self):
        self.receipt = None
        self.submissions = []

    def __call__(self, verb, args):
        if verb == 'read-deal-extraction-source':
            return {'ok': True, 'document': {'document_ref': DOC, 'entity_ref': ENTITY,
                    'organization_tenant_id': 'carr-internal', 'content_hash': hashlib.sha256(TEXT).hexdigest(),
                    'document_family_id': 'sha256:' + hashlib.sha256(TEXT).hexdigest(),
                    'lineage_verified': False, 'r2_key': 'documents/synthetic/file.pdf',
                    'mime': 'application/pdf', 'bytes': len(TEXT)}, 'receipt': self.receipt}
        assert verb == 'record-deal-extraction'
        self.submissions.append(args)
        self.receipt = {'ok': True, 'job_id': KEY, 'batch_id': KEY, 'canonical': False}
        return self.receipt


class ExtractionTests(unittest.TestCase):
    def test_grounding_corrects_wrong_rent_and_retry_returns_one_batch(self):
        store = Source()
        replies = iter([output('29'), output()])
        def model(*args, **kwargs):
            return {'output': next(replies), 'cost_usd': '0.01', 'model': 'synthetic-model'}
        job = ExtractionJob(UUID(KEY), UUID(ENTITY), UUID(DOC), 'lease', 'synthetic-model')
        result = run_job(job, call=store, read_bytes=lambda doc: TEXT,
                         text_pages=lambda data: [TEXT.decode()], model=model)
        self.assertFalse(result['canonical'])
        submitted = store.submissions[0]
        self.assertEqual(submitted['receipt']['grounding_changes'], ['base_rent_per_sf'])
        self.assertEqual(submitted['observations'][0]['value'], '24')
        self.assertEqual(submitted['observations'][0]['confidence_tier'], 'human_review')
        self.assertEqual(len(submitted['receipt']['abstentions']), 2)
        self.assertEqual(submitted['receipt']['cost_usd'], '0.02')
        run_job(job, call=store, read_bytes=lambda doc: self.fail('retry read source'),
                text_pages=lambda data: [], model=model)
        self.assertEqual(len(store.submissions), 1)

    def test_all_document_types_have_schema_valid_bound_observations(self):
        for kind in ('loi', 'lease', 'counter', 'amendment'):
            store = Source()
            run_job(ExtractionJob(UUID(KEY), UUID(ENTITY), UUID(DOC), kind, 'synthetic-model'),
                    call=store, read_bytes=lambda doc: TEXT, text_pages=lambda data: [TEXT.decode()],
                    model=lambda *a, **k: {'output': output(), 'cost_usd': '0.01', 'model': 'synthetic-model'})
            receipt = store.submissions[0]
            self.assertEqual(receipt['document_type'], kind)
            self.assertEqual(receipt['observations'][0]['source']['document_ref'], DOC)
            self.assertEqual(receipt['observations'][0]['source']['page_start'], 1)
            self.assertFalse(receipt['receipt']['source']['lineage_verified'])

    def test_source_hash_mismatch_never_calls_model_or_records(self):
        store = Source()
        with self.assertRaisesRegex(ValueError, 'hash mismatch'):
            run_job(ExtractionJob(UUID(KEY), UUID(ENTITY), UUID(DOC), 'loi', 'synthetic-model'),
                    call=store, read_bytes=lambda doc: b'changed', text_pages=lambda data: [],
                    model=lambda *a, **k: self.fail('model called for unbound source'))
        self.assertEqual(store.submissions, [])

    def test_no_text_layer_is_unreadable_instead_of_not_present(self):
        store = Source()
        run_job(ExtractionJob(UUID(KEY), UUID(ENTITY), UUID(DOC), 'amendment', 'synthetic-model'),
                call=store, read_bytes=lambda doc: TEXT, text_pages=lambda data: ['', ''],
                model=lambda *a, **k: self.fail('model called for image only source'))
        receipt = store.submissions[0]
        self.assertEqual(receipt['observations'], [])
        self.assertEqual({a['status'] for a in receipt['receipt']['abstentions']}, {'unreadable'})

    def test_schedule_and_ungrounded_quotes_go_to_review(self):
        for reply, pages, reason in [
            (output(), ['Schedule A. ' + TEXT.decode()], 'unsupported_schedule'),
            (output('99'), [TEXT.decode()], 'ungrounded'),
        ]:
            store = Source()
            run_job(ExtractionJob(UUID(KEY), UUID(ENTITY), UUID(DOC), 'counter', 'synthetic-model'),
                    call=store, read_bytes=lambda doc: TEXT, text_pages=lambda data: pages,
                    model=lambda *a, **k: {'output': reply, 'cost_usd': '0', 'model': 'synthetic-model'})
            abstention = next(a for a in store.submissions[0]['receipt']['abstentions'] if a['fact_key'] == 'base_rent_per_sf')
            self.assertEqual(abstention['reason'], reason)

    def test_invalid_fields_or_unaccounted_cost_never_record(self):
        for reply, cost in [(dict(fields=output()['fields'][:3]), '0'), (output(), 'NaN')]:
            store = Source()
            with self.assertRaises(ValueError):
                run_job(ExtractionJob(UUID(KEY), UUID(ENTITY), UUID(DOC), 'lease', 'synthetic-model'),
                        call=store, read_bytes=lambda doc: TEXT, text_pages=lambda data: [TEXT.decode()],
                        model=lambda *a, **k: {'output': reply, 'cost_usd': cost, 'model': 'synthetic-model'})
            self.assertEqual(store.submissions, [])

    def test_real_pdf_text_adapter_preserves_page_numbers(self):
        import runpy
        adapter = runpy.run_path(str(Path(__file__).resolve().parents[1] / 'bin/deal-extraction-job.py'))
        stream = b'BT /F1 12 Tf 30 700 Td (Synthetic base rent $24 per SF per year NNN.) Tj ET'
        objects = [b'<< /Type /Catalog /Pages 2 0 R >>',
                   b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
                   b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>',
                   b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',
                   b'<< /Length ' + str(len(stream)).encode() + b' >>\nstream\n' + stream + b'\nendstream']
        data = b'%PDF-1.4\n'
        offsets = [0]
        for n, obj in enumerate(objects, 1):
            offsets.append(len(data))
            data += str(n).encode() + b' 0 obj\n' + obj + b'\nendobj\n'
        xref = len(data)
        data += b'xref\n0 6\n0000000000 65535 f \n'
        data += b''.join(f'{o:010} 00000 n \n'.encode() for o in offsets[1:])
        data += b'trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n' + str(xref).encode() + b'\n%%EOF\n'
        pages = adapter['text_pages'](data)
        self.assertEqual(len(pages), 1)
        self.assertIn('Synthetic base rent $24 per SF per year NNN.', pages[0])

    def test_model_usage_keeps_existing_call_shape_and_constrained_command(self):
        from unittest.mock import patch
        from subprocess import CompletedProcess
        from lib.model_call import ask, ask_with_usage
        reply = json.dumps({'structured_output': output(), 'total_cost_usd': 0.02,
                            'modelUsage': {'synthetic-model': {'costUSD': 0.02}}})
        with patch('subprocess.run', return_value=CompletedProcess([], 0, reply)) as run:
            self.assertEqual(ask('synthetic', model='synthetic', schema={}, system_prompt='proposals'), output())
            self.assertEqual(ask_with_usage('synthetic', model='synthetic', schema={}, system_prompt='proposals')['cost_usd'], '0.02')
            command = run.call_args.args[0]
            self.assertEqual(command[command.index('--tools') + 1], '')
            self.assertIn('--strict-mcp-config', command)
            self.assertIn('--no-session-persistence', command)

    def test_smoke_scorer_never_establishes_promotion_support(self):
        import runpy
        scorer = runpy.run_path(str(Path(__file__).resolve().parents[1] / 'evals/deal-extraction/score.py'))
        corpus = json.loads((Path(__file__).resolve().parents[1] / 'evals/deal-extraction/fixtures.json').read_text())
        predictions = [{'case_id': c['id'], 'fact_key': k, 'value': v}
                       for c in corpus['cases'] for k, v in c['expected'].items()]
        result = scorer['score'](predictions)
        self.assertEqual(result['recall'], 1)
        self.assertFalse(result['automatic_promotion_eligible'])
        predictions[0]['value'] = '99'
        self.assertLess(scorer['score'](predictions)['precision'], 0.99)
        self.assertEqual(scorer['score']([])['abstention_rate'], 1)

    def test_transient_model_retry_retains_unknown_failed_cost(self):
        store = Source()
        replies = iter([RuntimeError('synthetic transient'), output(), output()])
        def model(*args, **kwargs):
            result = next(replies)
            if isinstance(result, RuntimeError):
                raise result
            return {'output': result, 'cost_usd': '0.01', 'model': 'synthetic-model'}
        run_job(ExtractionJob(UUID(KEY), UUID(ENTITY), UUID(DOC), 'lease', 'synthetic-model'),
                call=store, read_bytes=lambda doc: TEXT, text_pages=lambda data: [TEXT.decode()], model=model)
        receipt = store.submissions[0]['receipt']
        self.assertEqual(len(receipt['attempts']), 3)
        self.assertIsNone(receipt['attempts'][0]['cost_usd'])
        self.assertFalse(receipt['cost_complete'])


if __name__ == '__main__':
    unittest.main()
