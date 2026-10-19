"""Credential and request-budget proofs using a fake upstream only."""
import http.client
import io
import json
from pathlib import Path
import sys
import time
import unittest
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))
from claude_api_transport import Transport
import claude_api_config as config


class Response(io.BytesIO):
    status = 200

    def getheader(self, _name, default=None):
        return 'application/json'


class TransportTests(unittest.TestCase):
    def post(self, transport, payload):
        url = urlsplit(transport.url)
        connection = http.client.HTTPConnection(url.hostname, url.port, timeout=2)
        connection.request('POST', url.path + '/v1/messages', json.dumps(payload),
                           {'content-type': 'application/json', 'x-api-key': 'dummy'})
        response = connection.getresponse()
        status, body = response.status, response.read()
        connection.close()
        return status, body

    def test_only_upstream_receives_key_and_responses_cannot_reflect_it(self):
        seen = []
        def upstream(path, body, headers):
            seen.append(headers)
            if path.endswith('count_tokens'):
                return Response(b'{"input_tokens":12}')
            return Response(b'{"content":"transport-fixture-secret"}')
        with Transport('transport-fixture-secret', 'claude-sonnet-5-5', time.monotonic() + 10,
                       upstream=upstream) as transport:
            status, body = self.post(transport, {'model': 'claude-sonnet-5-5', 'messages': []})
        self.assertEqual(status, 200)
        self.assertEqual(seen[-1]['x-api-key'], 'transport-fixture-secret')
        self.assertNotIn(b'transport-fixture-secret', body)

    def test_reservation_bounds_all_requests_even_when_client_retries(self):
        calls = []
        def upstream(path, body, headers):
            if path.endswith('count_tokens'):
                return Response(b'{"input_tokens":100000}')
            calls.append(json.loads(body))
            return Response(b'{"content":[]}')
        with Transport('transport-fixture-secret', 'claude-sonnet-5-5', time.monotonic() + 10,
                       upstream=upstream) as transport:
            statuses = [self.post(transport, {'model': 'claude-sonnet-5-5', 'max_tokens': 128000,
                                             'messages': []})[0] for _ in range(17)]
        self.assertEqual(statuses, [200] * 16 + [429])
        self.assertEqual(len(calls), 16)
        self.assertTrue(all(call['max_tokens'] == 8192 for call in calls))
        # $190 + 16 * (125k input at the 1-hour cache rate + 8192 output).
        self.assertLess(190 + 16 * (125000 * 4 + 8192 * 10) / 1000000, 200)

    def test_unpriced_modifiers_and_oversize_inputs_never_generate(self):
        calls = []
        def upstream(path, body, headers):
            calls.append(path)
            return Response(b'{"input_tokens":100001}')
        with Transport('fixture', config.MODEL, time.monotonic() + 10, upstream) as transport:
            for extra in ({'inference_geo': 'us'}, {'speed': 'fast'},
                          {'mcp_servers': [{'url': 'https://example.com'}]},
                          {'tools': [{'type': 'web_search_20250305'}]}, {}):
                with self.subTest(extra=extra):
                    status, _ = self.post(transport, {'model': config.MODEL, 'messages': [], **extra})
                    self.assertEqual(status, 400)
        self.assertEqual(calls, ['/v1/messages/count_tokens'])

    def test_billing_error_in_token_counting_stops_transport(self):
        def upstream(*args):
            response = Response(b'{"error":{"message":"insufficient credits"}}')
            response.status = 402
            return response
        with Transport('fixture', config.MODEL, time.monotonic() + 10, upstream) as transport:
            self.post(transport, {'model': config.MODEL, 'messages': []})
            self.assertTrue(transport.billing_error)

    def test_each_priced_model_has_a_bound_below_its_reservation(self):
        self.assertLess(config.run_bound_usd(config.MODEL), config.RUN_ESTIMATE_USD)
        self.assertLess(config.run_bound_usd('claude-opus-5-5'), config.RUN_ESTIMATE_USD)


if __name__ == '__main__':
    unittest.main()
