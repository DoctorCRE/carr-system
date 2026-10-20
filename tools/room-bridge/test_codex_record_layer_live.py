"""Credential-free Codex capability probe. No inference or CARR calls.

Run directly. A loopback Responses stand-in rejects the first request after
capturing the CLI's tool declarations. This does not certify execution.
"""
import http.server
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest


class CodexIsolationProbe(unittest.TestCase):
    def test_read_only_shell_disabled_candidate_still_advertises_native_patch(self):
        requests = []

        class RejectInference(http.server.BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_POST(self):
                requests.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
                self.send_response(401)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(b'{"error":{"message":"example.test probe refuses inference"}}')

        server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), RejectInference)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(thread.join)
        self.addCleanup(server.shutdown)
        with tempfile.TemporaryDirectory(prefix='codex-record-layer-probe-') as tmp:
            root = Path(tmp)
            home, codex_home, work = (root / name for name in ('home', 'codex', 'work'))
            for directory in (home, codex_home, work):
                directory.mkdir()
            env = {'PATH': os.environ['PATH'], 'HOME': str(home),
                   'CODEX_HOME': str(codex_home), 'TMPDIR': tmp}
            version = subprocess.check_output(['codex', '--version'], env=env, text=True).strip()
            catalog = json.loads(subprocess.check_output(
                ['codex', 'debug', 'models', '--bundled'], env=env, text=True, timeout=15))
            model = next(m['slug'] for m in catalog['models'] if m.get('apply_patch_tool_type'))
            (codex_home / 'config.toml').write_text('deliberately invalid user config\n')
            argv = ['codex', 'exec', '--ignore-user-config', '--ignore-rules', '--ephemeral',
                    '--skip-git-repo-check', '--json', '-C', str(work), '-s', 'read-only', '-m', model]
            overrides = {
                'approval_policy': 'never', 'model_provider': 'synthetic',
                'model_providers.synthetic': {
                    'name': 'synthetic', 'base_url': f'http://127.0.0.1:{server.server_port}/v1',
                    'wire_api': 'responses', 'requires_openai_auth': False,
                    'request_max_retries': 0, 'stream_max_retries': 0},
                'features.shell_tool': False, 'features.apply_patch_freeform': False,
                'features.view_image': False, 'features.multi_agent': False,
                'features.apps': False, 'features.plugins': False, 'features.hooks': False,
                'features.enable_request_compression': False, 'web_search': 'disabled',
            }

            def toml(value):
                if isinstance(value, dict):
                    return '{' + ', '.join(k + '=' + toml(v) for k, v in value.items()) + '}'
                return json.dumps(value)

            for key, value in overrides.items():
                argv += ['-c', key + '=' + toml(value)]
            argv.append('Draft a reply to someone@example.test. Synthetic isolation probe.')
            proc = subprocess.run(argv, env=env, capture_output=True, text=True,
                                  stdin=subprocess.DEVNULL, timeout=30)
            self.assertNotEqual(proc.returncode, 0, 'synthetic authentication must fail')
            self.assertEqual(len(requests), 1, 'no permissive retry after authentication failure')
            request = requests[0]
            prompt = json.dumps(request.get('input', []))
            tools = request.get('tools', [])
            patch_declared = ('### `apply_patch`' in prompt or any(
                tool.get('name') == 'apply_patch' for tool in tools))
            self.assertTrue(patch_declared, 'native tool behavior changed; review the refusal')
            self.assertNotIn('### `exec_command`', prompt)
            self.assertFalse(any(tool.get('name') in ('exec_command', 'shell') for tool in tools))
            self.assertEqual(list(work.iterdir()), [])
            print(f'{version}; model={model}; native apply_patch declared; '
                  'shell declaration absent; isolated user config ignored; '
                  'synthetic 401 attempted once; no inference. '
                  'CARR-only execution remains unverified.')


if __name__ == '__main__':
    unittest.main()
