"""Selftests for the automatic Durable Object migration deploy check."""
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
    def run_check(self, migration='new_sqlite_classes = ["Budget"]', domains=None, workers_dev='false', preview_urls='false', response_ok=True, applied='', metadata=True, malformed=False, extra_config='', pages=None, routes_extra=None):
        domains = ['api.example.test'] if domains is None else domains
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                data = {'success': response_ok, 'result': [{'service': 'carr-mcp', 'environment': 'production', 'hostname': domain} for domain in domains]}
                if pages is not None:
                    from urllib.parse import parse_qs, urlparse
                    page = int(parse_qs(urlparse(self.path).query)['page'][0])
                    data = pages(page)
                if metadata and pages is None:
                    data['result_info'] = {'page': 1, 'per_page': 100, 'count': len(data['result']), 'total_count': len(data['result'])}
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
                route_suffix = f', {routes_extra}' if routes_extra else ''
                config.write_text(f'workers_dev = {workers_dev}\npreview_urls = {preview_urls}\nroutes = [{{pattern = "api.example.test", custom_domain = true}}{route_suffix}]\n{extra_config}\n[[migrations]]\ntag = "v1"\n{migration}\n')
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

    def test_single_page_without_metadata_is_refused(self):
        self.assertNotEqual(self.run_check(metadata=False).returncode, 0)

    def test_unsupported_deploy_surfaces_are_refused(self):
        for extra in ('route = "example.test/*"', '[triggers]\ncrons = ["* * * * *"]'):
            with self.subTest(extra=extra):
                self.assertNotEqual(self.run_check(extra_config=extra).returncode, 0)
        for route in ('"example.test/*"', '{pattern = "example.test/*", zone_name = "example.test"}'):
            with self.subTest(route=route):
                self.assertNotEqual(self.run_check(extra_config='', routes_extra=route).returncode, 0)

    def test_live_pagination_checks_all_pages_and_filters(self):
        def pages(page):
            entries = [
                {'service': 'other-worker', 'environment': 'production', 'hostname': 'other.test'},
                {'service': 'carr-mcp', 'environment': 'staging', 'hostname': 'staging.test'},
                {'service': 'carr-mcp', 'environment': 'production', 'hostname': 'api.example.test'},
                {'service': 'carr-mcp', 'environment': 'production', 'hostname': 'extra.test'},
            ]
            result = entries[(page - 1) * 2:page * 2]
            return {'success': True, 'result': result, 'result_info': {'page': page, 'per_page': 2, 'count': len(result), 'total_count': 4}}
        result = self.run_check(pages=pages)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('extra.test', result.stderr)
        self.assertNotIn('other.test', result.stderr)
        self.assertNotIn('staging.test', result.stderr)

    def test_other_workers_and_environments_are_ignored(self):
        def pages(page):
            entries = [
                {'service': 'other-worker', 'environment': 'production', 'hostname': 'other.test'},
                {'service': 'carr-mcp', 'environment': 'staging', 'hostname': 'staging.test'},
                {'service': 'carr-mcp', 'environment': 'production', 'hostname': 'api.example.test'},
            ]
            return {'success': True, 'result': entries, 'result_info': {'page': 1, 'per_page': 100, 'count': 3, 'total_count': 3}}
        result = self.run_check(pages=pages)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_incomplete_pagination_is_refused(self):
        def pages(page):
            return {'success': True, 'result': [{'service': 'carr-mcp', 'environment': 'production', 'hostname': 'api.example.test'}] if page == 1 else [], 'result_info': {'page': page, 'per_page': 1, 'count': 1 if page == 1 else 0, 'total_count': 2}}
        self.assertNotEqual(self.run_check(pages=pages).returncode, 0)

    def test_pagination_limit_is_refused(self):
        def pages(page):
            return {'success': True, 'result': [{'service': 'other', 'environment': 'production', 'hostname': 'other.test'}], 'result_info': {'page': page, 'per_page': 1, 'count': 1, 'total_count': 11}}
        result = self.run_check(pages=pages)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('pagination', result.stderr)

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
