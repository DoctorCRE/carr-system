"""Run the real Codex business profile against credential-free loopback stand-ins."""
import http.server
import json
import os
import shutil
from pathlib import Path
import subprocess
import threading
import unittest
from unittest.mock import patch

import claude_desktop_wire
import dispatch
import record_layer


@unittest.skipUnless(shutil.which('codex'), 'requires the installed Codex CLI')
class CodexIsolationProbe(unittest.TestCase):
    def test_business_turn_and_required_carr_auth_fail_closed(self):
        requests = []
        reject_carr = False

        class StandIn(http.server.BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_GET(self):
                self.send_response(405)
                self.end_headers()

            def do_POST(self):
                data = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                requests.append((self.path, data))
                if self.path == '/mcp':
                    if reject_carr:
                        self.send_response(401)
                        self.end_headers()
                        return
                    if 'id' not in data:
                        self.send_response(202)
                        self.end_headers()
                        return
                    if data['method'] == 'tools/call':
                        result = {'content': [{'type': 'text', 'text': 'synthetic write recorded'}]}
                    elif data['method'] == 'initialize':
                        result = {'protocolVersion': '2024-11-05', 'capabilities': {'tools': {}},
                                  'serverInfo': {'name': 'example.test', 'version': '1'}}
                    else:
                        result = {'tools': [{'name': 'synthetic_write',
                                  'description': 'Write synthetic example.test record',
                                  'annotations': {'readOnlyHint': False, 'destructiveHint': True,
                                                  'idempotentHint': True, 'openWorldHint': False},
                                  'inputSchema': {'type': 'object', 'properties': {}}}]}
                    payload = json.dumps({'jsonrpc': '2.0', 'id': data['id'], 'result': result}).encode()
                    self.send_response(200)
                    self.send_header('Content-Type', 'application/json')
                    self.end_headers()
                    self.wfile.write(payload)
                    return
                if self.path != '/v1/responses':
                    self.send_response(404)
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header('Content-Type', 'text/event-stream')
                self.end_headers()
                message = {'id': 'msg_synthetic', 'type': 'message', 'role': 'assistant',
                           'status': 'completed', 'content': [{'type': 'output_text',
                           'text': 'Draft for someone@example.test. Contact the partner.', 'annotations': []}]}
                response_count = sum(path == '/v1/responses' for path, _ in requests)
                if response_count == 1:
                    message = {'type': 'function_call', 'id': 'fc_synthetic',
                               'call_id': 'call_synthetic', 'namespace': 'mcp__carr',
                               'name': 'synthetic_write', 'arguments': '{}'}
                events = [
                    {'type': 'response.created', 'response': {'id': 'resp_synthetic',
                     'object': 'response', 'status': 'in_progress', 'output': []}},
                    {'type': 'response.output_item.done', 'output_index': 0, 'item': message},
                    {'type': 'response.completed', 'response': {'id': 'resp_synthetic',
                     'object': 'response', 'status': 'completed', 'output': [message],
                     'usage': {'input_tokens': 1, 'output_tokens': 1, 'total_tokens': 2}}},
                ]
                for event in events:
                    self.wfile.write(('event: ' + event['type'] + '\ndata: ' + json.dumps(event) + '\n\n').encode())

        server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), StandIn)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(thread.join)
        self.addCleanup(server.shutdown)
        url = f'http://127.0.0.1:{server.server_port}'
        canary = record_layer.CANARY.replace("'1.1.1.1', 443",
                                            f"'127.0.0.1', {server.server_port}")
        self.enterContext(patch.object(record_layer, 'CANARY', canary))
        launches = []

        def run(argv, **options):
            if argv[1] == 'exec':
                self.assertFalse(Path(options['env']['CODEX_HOME'], 'config.toml').exists())
                Path(options['env']['CODEX_HOME'], 'config.toml').write_text('invalid user config\n')
                config = {
                    'model_provider': 'synthetic',
                    'model_providers.synthetic': {'name': 'synthetic', 'base_url': url + '/v1',
                        'wire_api': 'responses', 'requires_openai_auth': False,
                        'request_max_retries': 0, 'stream_max_retries': 0},
                    'features.enable_request_compression': False,
                }

                def toml(value):
                    if isinstance(value, dict):
                        return '{' + ', '.join(k + '=' + toml(v) for k, v in value.items()) + '}'
                    return json.dumps(value)

                argv = argv[:-1] + [arg for key, value in config.items()
                                   for arg in ('-c', key + '=' + toml(value))] + argv[-1:]
            launches.append(argv[1])
            return subprocess.run(argv, **options)

        env = {'PATH': os.environ['PATH'], 'CODEX_API_KEY': 'synthetic', 'CARR_MCP_TOKEN': 'synthetic'}
        with patch.object(claude_desktop_wire, 'CARR_MCP_URL', url + '/mcp'):
            result = dispatch._to_codex({'posture': 'record-layer', 'model': 'gpt-6.1-sol', 'effort': 'low'},
                'Draft for someone@example.test.', env=env, provider_run=run, timeout_s=30)
            self.assertEqual(result['status'], 'completed', result)
            calls = [data for path, data in requests
                     if path == '/mcp' and data.get('method') == 'tools/call']
            self.assertEqual([call['params']['name'] for call in calls], ['synthetic_write'],
                             'CARR write tool never invoked: ' + json.dumps([item for item in
                                 requests[-1][1].get('input', [])
                                 if item.get('type', '').endswith('call_output')]))
            self.assertEqual(result['canaries'], {'file_write': 'denied', 'network': 'denied'})
            self.assertIn('someone@example.test', result['result'])
            self.assertTrue(any(path == '/mcp' and data.get('method') == 'tools/list' for path, data in requests))
            inference = [data for path, data in requests if path == '/v1/responses']
            self.assertEqual(len(inference), 2)
            self.assertIn('synthetic_write', json.dumps(inference[0]))
            declared = []
            for tool in inference[0].get('tools', []):
                declared.extend(tool.get('tools', []) if tool.get('type') == 'namespace' else [tool])
            self.assertFalse({'apply_patch', 'exec', 'exec_command', 'shell', 'shell_command'} &
                             {tool.get('name') for tool in declared}, declared)
            replies = [item for item in inference[1].get('input', [])
                       if item.get('type', '').endswith('call_output')]
            self.assertIn('synthetic write recorded', json.dumps(replies))
            self.assertEqual(launches, ['sandbox', 'exec'])
            requests.clear()
            launches.clear()
            reject_carr = True
            refused = dispatch._to_codex({'posture': 'record-layer', 'model': 'gpt-6.1-sol', 'effort': 'low'},
                'Draft for someone@example.test.', env=env, provider_run=run, timeout_s=30)
            self.assertEqual(refused['status'], 'failed', refused)
            self.assertFalse(any(path == '/v1/responses' for path, _ in requests))
            self.assertEqual(launches, ['sandbox', 'exec'])
        print('Real Codex profile invoked a write tool with loopback CARR and Responses stand-ins. '
              'Both native canaries denied. Required CARR 401 refused before inference. '
              'Invalid user config ignored. No paid inference or production CARR calls.')


if __name__ == '__main__':
    unittest.main()
