"""Judgment requests cross the same seam with live and offline adapters."""
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch
import tempfile
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

spec = importlib.util.spec_from_file_location('judgment_test', Path(__file__).with_name('jev_semantic.py'))
assert spec and spec.loader
semantic = importlib.util.module_from_spec(spec)
spec.loader.exec_module(semantic)


class JudgmentTests(unittest.TestCase):
    def test_choice_confidence_has_one_owner_and_preserves_original_answer(self):
        watch = semantic._load('jev_session_watch')
        answer = {'answers': {'q': {'choice': 'none', 'confidence': '0.25'}}}
        request = semantic.JudgmentRequest('fixture',
            {'q': {'type': 'choice', 'criteria': {'none': 'No bug'}}},
            caller='fixture', version='v1', validate_confidence=True)
        with patch.object(semantic, 'choice_confidence', return_value=.25) as validate, \
                patch.object(watch, '_module', return_value=semantic):
            result = semantic.evaluate(request, adapter=semantic.OfflineAdapter(answer)).unwrap()
            self.assertEqual(watch._choice_value(result, 'q'), ('none', .25))
            self.assertEqual(validate.call_count, 2)
        self.assertEqual(result['answers'], answer['answers'])

    def test_choice_confidence_contract_for_both_consumers(self):
        watch = semantic._load('jev_session_watch')
        request = semantic.JudgmentRequest('fixture',
            {'q': {'type': 'choice', 'criteria': {'none': 'No bug'}}},
            caller='fixture', version='v1', validate_confidence=True)
        for confidence in (None, 0, 1, '0.25', -1, 2, float('nan'), float('inf'), 'bad'):
            answer = {'answers': {'q': {'choice': 'none', 'confidence': confidence}}}
            receipt = semantic.evaluate(request, adapter=semantic.OfflineAdapter(answer))
            with self.subTest(confidence=confidence):
                if confidence in (None, 0, 1, '0.25'):
                    result = receipt.unwrap()
                    expected = None if confidence is None else float(confidence)
                    self.assertEqual(watch._choice_value(result, 'q'), ('none', expected))
                    self.assertEqual(result['answers'], answer['answers'])
                else:
                    with self.assertRaises(ValueError):
                        receipt.unwrap()
                    with self.assertRaises(ValueError):
                        watch._choice_value(answer, 'q')

    def test_receipt_summary_is_bounded_even_for_rejected_large_requests(self):
        request = semantic.JudgmentRequest('fixture',
            {str(i).zfill(4)+'x'*200: {'type': 'noul'} for i in range(300)},
            caller='jev_session_watch', version='v1')
        receipt = semantic.evaluate(request, adapter=semantic.OfflineAdapter(
            error=RuntimeError('unavailable')))
        summary = receipt.summary()
        self.assertEqual(len(summary['questions']), 255)
        self.assertEqual(summary['questions_omitted'], 45)
        self.assertTrue(all(len(key) <= 160 for key in summary['questions']))

    def test_boundary_revalidates_cached_choice_and_completeness(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = semantic._load('jev_verdict_cache')
            request = semantic.JudgmentRequest('fixture',
                {'q': {'type': 'choice', 'criteria': {'none': 'No bug'}}},
                caller='jev_session_watch', version='tool-result-v1',
                cache_path=tmp+'/cache', validate_confidence=True)
            key = semantic.cache_key(request.state, request.questions, request.caller, request.version)
            def no_transport(*args, **kwargs):
                raise AssertionError('cache hit must not pay')
            for answers in ({'q': {'choice': 'wrong_frame', 'confidence': .9}}, {}):
                cache.put(tmp+'/cache', key, {'model': 'jev-1.13.0', 'answers': answers})
                receipt = semantic.evaluate(request,
                    adapter=semantic.LiveAdapter(transport=no_transport))
                self.assertEqual(receipt.status, 'unavailable')
                with self.assertRaises(ValueError):
                    receipt.unwrap()

    def test_hook_receipts_bind_the_shared_judgment_implementation(self):
        from lib.rule_delivery_preuse import (
            SELECTOR_SOURCE_PATHS, postwrite_reviewer_digest, semantic_selector_digest)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for relative in (*SELECTOR_SOURCE_PATHS, 'hooks/lint-gate.py',
                             'ops/jev_code_review.py', 'ops/jev_semantic.py'):
                target = root/relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text('fixture implementation')
            implementation = root/'ops/jev_semantic.py'
            implementation.write_text('original implementation')
            before = (postwrite_reviewer_digest(root), semantic_selector_digest(root))
            implementation.write_text('changed implementation')
            after = (postwrite_reviewer_digest(root), semantic_selector_digest(root))
        self.assertNotEqual(before[0], after[0])
        self.assertNotEqual(before[1], after[1])

    def test_boundary_choice_confidence_is_validated_without_transport_options(self):
        def transport(state, questions, *, model, caller, timeout, deadline, retries):
            return {'model': model, 'answers': {'q': {'choice': 'none', 'confidence': float('nan')}}}
        with tempfile.TemporaryDirectory() as tmp:
            request = semantic.JudgmentRequest('fixture',
                {'q': {'type': 'choice', 'criteria': {'none': 'No bug'}}},
                caller='jev_session_watch', version='tool-result-v1', retries=0,
                cache_path=tmp+'/cache', validate_confidence=True)
            receipt = semantic.evaluate(request, adapter=semantic.LiveAdapter(transport=transport))
            with self.assertRaisesRegex(ValueError, 'invalid choice confidence'):
                receipt.unwrap()

    def test_offline_adapter_returns_typed_answers_and_bounded_receipt(self):
        request = semantic.JudgmentRequest(
            {'code': 'except Exception: pass'},
            {'bug': {'type': 'noul', 'instructions': 'Does this swallow failures?'}},
            caller='jev_code_review', version='fixture-v1')
        answer = {'model': 'jev-1.13.0', 'answers': {'bug': {'type': 'noul', 'noul': .9}},
                  'usage': {'input_tokens': 12, 'output_tokens': 1}}
        receipt = semantic.evaluate(request, adapter=semantic.OfflineAdapter(answer))
        self.assertEqual(receipt.status, 'answered')
        self.assertEqual(receipt.unwrap()['answers'], {'bug': {'type': 'noul', 'noul': .9}})
        self.assertEqual(receipt.summary(), {
            'status': 'answered', 'reason': None, 'questions': ['bug'],
            'model': 'jev-1.13.0', 'cache_hit': False})

    def test_both_adapters_reject_incomplete_answers_and_preserve_failure(self):
        request = semantic.JudgmentRequest('text', {'q': {'type': 'noul'}},
                                           caller='jev_session_watch', version='v1')
        with tempfile.TemporaryDirectory() as tmp:
            for adapter in (semantic.OfflineAdapter({'answers': {}}),
                            semantic.LiveAdapter(transport=lambda *a, **k: {'answers': {}})):
                request.options['cache_path'] = tmp + '/cache'
                receipt = semantic.evaluate(request, adapter=adapter)
                self.assertEqual(receipt.status, 'unavailable')
                with self.assertRaisesRegex(ValueError, 'incomplete semantic answers'):
                    receipt.unwrap()
        error = TimeoutError('slow vendor')
        receipt = semantic.evaluate(request, adapter=semantic.OfflineAdapter(error=error))
        self.assertEqual(receipt.summary()['reason'], 'inspection_error')
        with self.assertRaises(TimeoutError) as caught:
            receipt.unwrap()
        self.assertIs(caught.exception, error)

    def test_offline_replay_never_imports_client_or_uses_live_cache(self):
        request = semantic.JudgmentRequest('text', {'q': {'type': 'noul'}},
                                           caller='jev_session_watch', version='v1')
        with patch.object(semantic, '_load', side_effect=AssertionError('live access')):
            receipt = semantic.evaluate(request, adapter=semantic.OfflineAdapter(
                {'answers': {'q': {'noul': .2}}}))
        self.assertEqual(receipt.unwrap()['answers'], {'q': {'noul': .2}})

    def test_live_adapter_retains_batch_attribution_cache_and_retry_budget(self):
        calls = []
        def transport(state, questions, **options):
            calls.append((state, questions, options))
            return {'model': 'jev-1.13.0', 'answers': {'q': {'noul': .2}},
                    'usage': {'input_tokens': 12, 'output_tokens': 1}}
        with tempfile.TemporaryDirectory() as tmp:
            request = semantic.JudgmentRequest({'text': 'fixture'}, {'q': {'type': 'noul'}},
                caller='jev_session_watch', version='v1', retries=0, timeout=5,
                cache_path=tmp+'/cache')
            first = semantic.evaluate(request, adapter=semantic.LiveAdapter(transport=transport))
            second = semantic.evaluate(request, adapter=semantic.LiveAdapter(transport=transport))
        self.assertEqual(first.unwrap()['usage'], {'input_tokens': 12, 'output_tokens': 1})
        self.assertEqual(second.unwrap()['usage'], {'input_tokens': 0, 'output_tokens': 0})
        self.assertTrue(second.summary()['cache_hit'])
        self.assertEqual(len(calls), 1)
        state, questions, options = calls[0]
        self.assertEqual(state, {'text': 'fixture'})
        self.assertEqual(questions, {'q': {'type': 'noul'}})
        self.assertEqual(options['caller'], 'jev_session_watch')
        self.assertEqual(options['model'], 'jev-1.13.0')
        self.assertEqual(options['retries'], 0)
        self.assertLessEqual(options['timeout'], 5)


if __name__ == '__main__':
    unittest.main()
