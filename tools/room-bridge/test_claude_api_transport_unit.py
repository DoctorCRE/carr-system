"""Credential and request-budget proofs using a fake upstream only."""
import http.client
import io
import json
from pathlib import Path
import sys
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))
from claude_api_transport import Transport
import claude_api_config as config


class Response(io.BytesIO):
    status = 200

    def __init__(self, body, content_type='application/json'):
        super().__init__(body)
        self.content_type = content_type

    def getheader(self, _name, default=None):
        return self.content_type


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
            return Response(b'{"content":[],"usage":{"input_tokens":0,"output_tokens":0}}')
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

    def test_stream_usage_is_cumulative_and_includes_both_cache_ttls(self):
        events = [
            {'type': 'message_start', 'message': {'usage': {
                'input_tokens': 1000, 'output_tokens': 1, 'cache_read_input_tokens': 1000,
                'cache_creation_input_tokens': 5000, 'cache_creation': {
                    'ephemeral_5m_input_tokens': 2000, 'ephemeral_1h_input_tokens': 3000}}}},
            {'type': 'message_delta', 'usage': {'output_tokens': 50}},
            {'type': 'message_delta', 'usage': {'output_tokens': 100}},
            {'type': 'message_stop'}]
        body = b''.join(b'data: ' + json.dumps(e).encode() + b'\n\n' for e in events)
        class Fragmented(Response):
            def read1(self, size):
                return super().read1(7)
        def upstream(path, *_args):
            if path.endswith('count_tokens'):
                return Response(b'{"input_tokens":10000}')
            return Fragmented(body, 'text/event-stream')
        with Transport('fixture', config.MODEL, time.monotonic() + 10, upstream) as transport:
            status, received = self.post(transport, {'model': config.MODEL, 'messages': [], 'stream': True})
            self.assertEqual(status, 200)
            self.assertEqual(received, body)
            self.assertEqual(transport.cost_usd, config.Decimal('0.0201'))
            self.assertFalse(transport.uncertain)

    def test_concurrent_requests_charge_before_next_reservation_check(self):
        calls = []
        def upstream(path, *_args):
            if path.endswith('count_tokens'):
                return Response(b'{"input_tokens":100000}')
            calls.append(path)
            time.sleep(0.03)
            return Response(b'{"usage":{"cache_creation_input_tokens":100000,"output_tokens":8192}}')
        with patch.object(config, 'RUN_ESTIMATE_USD', config.Decimal('0.60')), \
                Transport('fixture', config.MODEL, time.monotonic() + 10, upstream) as transport, \
                ThreadPoolExecutor(max_workers=2) as pool:
            statuses = list(pool.map(lambda _: self.post(transport, {'model': config.MODEL, 'messages': []})[0], range(2)))
            self.assertEqual(sorted(statuses), [200, 429])
            self.assertEqual(len(calls), 1)
            self.assertEqual(transport.cost_usd, config.Decimal('0.48192'))

    def test_json_cache_prices_for_each_model(self):
        body = json.dumps({'usage': {
            'input_tokens': 1000, 'output_tokens': 100, 'cache_read_input_tokens': 1000,
            'cache_creation_input_tokens': 5000, 'cache_creation': {
                'ephemeral_5m_input_tokens': 2000, 'ephemeral_1h_input_tokens': 3000}}}).encode()
        def upstream(path, *_args):
            return Response(b'{"input_tokens":10000}' if path.endswith('count_tokens') else body)
        for model, expected in ((config.MODEL, '0.0201'), ('claude-opus-5-5', '0.0402')):
            with self.subTest(model=model), Transport('fixture', model, time.monotonic() + 10, upstream) as transport:
                self.post(transport, {'model': model, 'messages': []})
                self.assertEqual(transport.cost_usd, config.Decimal(expected))

    def test_unknown_usage_consumes_full_reservation_and_refuses_later_requests(self):
        cases = [b'{"content":[]}', b'{"usage":{"input_tokens":1,"output_tokens":-1}}',
                 b'data: {"type":"message_start","message":{"usage":{"input_tokens":1,"output_tokens":1}}}\n\n']
        for body in cases:
            def upstream(path, *_args):
                if path.endswith('count_tokens'):
                    return Response(b'{"input_tokens":1}')
                return Response(body, 'text/event-stream' if body.startswith(b'data:') else 'application/json')
            with self.subTest(body=body), Transport('fixture', config.MODEL, time.monotonic() + 10, upstream) as transport:
                self.post(transport, {'model': config.MODEL, 'messages': []})
                self.assertTrue(transport.uncertain)
                self.assertEqual(transport.cost_usd, config.RUN_ESTIMATE_USD)
                self.assertEqual(self.post(transport, {'model': config.MODEL, 'messages': []})[0], 429)


if __name__ == '__main__':
    unittest.main()
