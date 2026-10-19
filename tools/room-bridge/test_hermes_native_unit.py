"""Native Hermes ownership and a shared dollar ceiling, without paid inference."""
from contextlib import contextmanager
from decimal import Decimal
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import hermes_native


PRICE = {'input_usd_per_token': '0.000002', 'output_usd_per_token': '0.000006'}


def request(**overrides):
    body = {'model': 'glm-current', 'messages': [{'role': 'user', 'content': 'fix it'}],
            'max_tokens': 4096, **overrides}
    return SimpleNamespace(method='POST', url='https://inference.example/v1/chat/completions',
                           content=json.dumps(body).encode())


class NativeTests(unittest.TestCase):
    def guard(self, allowance='1.00'):
        guard = hermes_native.CallBudget(allowance, PRICE, 'glm-current')
        guard.bind('https://inference.example/v1', 'nous', 'glm-current')
        return guard

    def test_worst_case_tokens_are_reserved_before_every_send_including_retries(self):
        guard = self.guard('0.05')
        sent = []
        for _ in range(2):
            guard.admit(request())
            sent.append('sent')
        with self.assertRaises(hermes_native.SpendStop):
            guard.admit(request())
            sent.append('sent')
        self.assertEqual(len(sent), 2)
        self.assertLessEqual(guard.reserved, Decimal('0.05'))

    def test_no_other_paid_provider_or_model_can_receive_inference(self):
        for req in (request(model='other'), request(max_tokens=0), request(max_tokens=100000),
                    request(max_completion_tokens=100000), request(n=2)):
            with self.subTest(req=req):
                guard = self.guard()
                with self.assertRaises(hermes_native.SpendStop):
                    guard.admit(req)
        guard = self.guard()
        req = request()
        req.url = 'https://other.example/v1/chat/completions'
        with self.assertRaises(hermes_native.SpendStop):
            guard.admit(req)
        self.assertEqual(guard.reserved, 0)

    def test_unpriced_and_multimodal_calls_are_refused(self):
        with self.assertRaises(ValueError):
            hermes_native.CallBudget('1.00', {}, 'glm-current')
        with self.assertRaises(hermes_native.SpendStop):
            self.guard().admit(request(messages=[{'role': 'user', 'content': [
                {'type': 'image_url', 'image_url': {'url': 'https://example.org/image'}}]}]))

    def test_sync_and_async_auxiliary_requests_share_the_same_allowance(self):
        import asyncio
        guard = self.guard('0.03')
        sent = []
        class Client:
            def send(self, req, **kwargs):
                sent.append(kwargs)
        class AsyncClient:
            async def send(self, req, **kwargs):
                sent.append(kwargs)
        httpx = SimpleNamespace(Client=Client, AsyncClient=AsyncClient)
        with patch.dict('sys.modules', {'httpx': httpx}), guard.enforce():
            Client().send(request())
            with self.assertRaises(hermes_native.SpendStop):
                asyncio.run(AsyncClient().send(request()))
        self.assertEqual(len(sent), 1)
        self.assertIs(sent[0].get('follow_redirects'), False)

    def test_native_runner_never_contacts_the_fake_persistent_gateway(self):
        calls, built = [], []
        class Agent:
            def __init__(self, **kwargs):
                built.append(kwargs)
        run_agent = SimpleNamespace(AIAgent=Agent)
        def oneshot(*args, **kwargs):
            run_agent.AIAgent(base_url='https://inference.example/v1', provider='nous', model='glm-current')
            return 0
        fake_gateway = SimpleNamespace(connect_gateway=lambda: calls.append('gateway'))
        fake_oneshot = SimpleNamespace(run_oneshot=oneshot)
        @contextmanager
        def no_http():
            yield
        guard = self.guard()
        with patch.dict('sys.modules', {'run_agent': run_agent,
                'hermes_cli.oneshot': fake_oneshot, 'hermes_cli.gateway_client': fake_gateway}), \
                patch.object(guard, 'enforce', no_http):
            code = hermes_native.run_native('fix it', 'glm-current', 'high', 'usage.json', guard)
        self.assertEqual(code, 0)
        self.assertEqual(calls, [])
        self.assertEqual(built[0]['max_iterations'], hermes_native.MAX_TURNS)
        self.assertEqual(built[0]['max_tokens'], hermes_native.MAX_OUTPUT_TOKENS)
        self.assertIsNone(built[0]['fallback_model'])
        self.assertTrue(built[0]['skip_background_review'])
        self.assertIs(run_agent.AIAgent, Agent)


if __name__ == '__main__':
    unittest.main()
