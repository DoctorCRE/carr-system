import json
import subprocess
import sys
import tempfile
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from threading import Thread

SCRIPT = Path(__file__).with_name('deploy-do-migration-check.py')

class Check(unittest.TestCase):
    def run_check(self, migration='new_sqlite_classes = ["Budget"]', domains=None, workers_dev='false', preview_urls='false', response_ok=True, applied='', metadata=True, malformed=False):
        domains = ['api.example.test'] if domains is None else domains
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                data = {'success': response_ok, 'result': [{'service': 'carr-mcp', 'environment': 'production', 'hostname': domain} for domain in domains]}
                if metadata:
                    data['result_info'] = {'page': 1, 'total_pages': 1}
                if malformed:
                    data['result'].append({'hostname': 'extra.example.test'})
                body = json.dumps(data).encode()
                self.send_response(200)
                self.end_headers()
                self.wfile.write(body)
            def log_message(self, *args):
                pass
        server = HTTPServer(('127.0.0.1', 0), Handler)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as directory:
                config = Path(directory) / 'wrangler.toml'
                config.write_text(f'workers_dev = {workers_dev}\npreview_urls = {preview_urls}\nroutes = [{{pattern = "api.example.test", custom_domain = true}}]\n[[migrations]]\ntag = "v1"\n{migration}\n')
                return subprocess.run([sys.executable, str(SCRIPT), str(config), applied, 'account', f'http://127.0.0.1:{server.server_port}'], capture_output=True, text=True)
        finally:
            server.shutdown()
            thread.join()
            server.server_close()

    def test_additive_and_matching_domains_are_allowed(self):
        result = self.run_check()
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_new_classes_are_allowed(self):
        self.assertEqual(self.run_check(migration='new_classes = ["Budget"]').returncode, 0)

    def test_destructive_migrations_are_refused(self):
        for field in ('deleted_classes', 'renamed_classes', 'transferred_classes'):
            with self.subTest(field=field):
                result = self.run_check(migration=f'{field} = []')
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(field, result.stderr)

    def test_extra_and_missing_domains_are_refused(self):
        for domains, message in [([], 'missing from production'), (['api.example.test', 'extra.example.test'], 'extra in production')]:
            with self.subTest(domains=domains):
                result = self.run_check(domains=domains)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(message, result.stderr)

    def test_public_urls_are_refused(self):
        for setting in ('workers_dev', 'preview_urls'):
            with self.subTest(setting=setting):
                result = self.run_check(**{setting: 'true'})
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(setting, result.stderr)

    def test_single_page_without_metadata_is_allowed(self):
        self.assertEqual(self.run_check(metadata=False).returncode, 0)

    def test_malformed_domain_is_refused(self):
        result = self.run_check(malformed=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('malformed domain', result.stderr)

    def test_api_error_is_refused(self):
        result = self.run_check(response_ok=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('API', result.stderr)

    def test_already_applied_destructive_migration_is_ignored(self):
        result = self.run_check(migration='deleted_classes = ["Old"]\n[[migrations]]\ntag = "v2"\nnew_sqlite_classes = ["Budget"]', applied='v1')
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_unknown_applied_tag_is_refused(self):
        result = self.run_check(applied='unknown')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('not declared', result.stderr)

if __name__ == '__main__':
    unittest.main()
