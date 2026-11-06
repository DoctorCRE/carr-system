"""Exercise proof transport admission and outgoing release request behavior."""
import importlib.util
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def load(path):
    spec = importlib.util.spec_from_file_location(path.stem.replace('-', '_'), path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ProofTransportTest(unittest.TestCase):
    def test_guard_rejects_direct_transports_and_aliases(self):
        guard = load(ROOT / 'ops/proof-transport-check.py')
        for source in ['import urllib.request as net', 'from urllib import request',
                       'from urllib.request import urlopen as read', 'import requests',
                       'import httpx as net', 'from http.client import HTTPSConnection',
                       'import aiohttp', 'import socket',
                       'import subprocess; subprocess.run(["curl", "https://example.test"])']:
            with self.subTest(source=source):
                with tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    (root / 'tools').mkdir()
                    (root / 'tools/prove-new.py').write_text(source)
                    self.assertTrue(guard.check(root))
        self.assertEqual(guard.violations('import urllib.parse\nfrom lib.release_reader import read'), [])

    def test_shared_reader_caps_success_and_errors_without_following_redirects(self):
        from lib.release_reader import read, USER_AGENT
        requests = []

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                requests.append((self.path, self.headers))
                status = {'/redirect': 302, '/refused': 403}.get(self.path, 200)
                self.send_response(status)
                if status == 302:
                    self.send_header('Location', '/forbidden')
                self.end_headers()
                self.wfile.write(b'0123456789')

            def log_message(self, *_args):
                pass

        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        try:
            origin = f'http://127.0.0.1:{server.server_port}'
            for path, status in [('/release', 200), ('/refused', 403), ('/redirect', 302)]:
                response = read(origin + path, timeout=1, max_bytes=4, headers={'Cookie': 'synthetic'})
                self.assertEqual(response.status, status)
                self.assertEqual(response.body, b'01234')
            self.assertEqual([path for path, _ in requests], ['/release', '/refused', '/redirect'])
            for _, headers in requests:
                self.assertEqual(headers['User-Agent'], USER_AGENT)
                self.assertEqual(headers['Cookie'], 'synthetic')
        finally:
            server.shutdown()
            thread.join()
            server.server_close()

    def test_named_runners_send_shared_user_agent(self):
        from lib.release_reader import USER_AGENT
        for name in ['duplicate-candidates-address-membership', 'notification-feed-evidence-integrity']:
            requests = []

            def reply(_opener, request):
                requests.append(request)
                raise OSError('stop after observing outgoing request')

            runner = load(ROOT / f'tools/prove-{name}.py')
            with patch('urllib.request.HTTPSHandler.https_open', new=reply):
                code, _ = runner.observe('https://example.test', '/unavailable-wrapper', 1)
            with self.subTest(runner=name):
                self.assertEqual(code, 78)
                self.assertEqual(len(requests), 1)
                self.assertEqual(requests[0].full_url, 'https://example.test/release')
                self.assertEqual(requests[0].get_method(), 'GET')
                self.assertEqual(requests[0].get_header('User-agent'), USER_AGENT)


if __name__ == '__main__':
    unittest.main()
