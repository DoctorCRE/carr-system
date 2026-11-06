"""Exercise proof transport admission and outgoing release request behavior."""
import importlib.util
from http.server import ThreadingHTTPServer
import threading
from pathlib import Path
import sys
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.proof_release_fixture import ReleaseFixtureHandler


def load(path):
    spec = importlib.util.spec_from_file_location(path.stem.replace('-', '_'), path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ProofTransportTest(unittest.TestCase):
    def test_guard_rejects_unapproved_capabilities(self):
        guard = load(ROOT / 'ops/proof-transport-check.py')
        cases = [
            'import urllib.request as net',
            'from urllib import request as r',
            'from urllib.request import urlopen as read',
            'import http.client as h; h.HTTPSConnection("example.test")',
            'import requests as r; r.get("https://example.test")',
            'import httpx as net', 'import aiohttp', 'import socket',
            'import ftplib', 'import arbitrary_network_helper',
            'import subprocess; subprocess.run(["curl", "https://example.test"])',
            'import subprocess; subprocess.run(["/usr/bin/curl", "--silent", "--fail", "https://example.test"], check=True)',
            'import subprocess; subprocess.run("/usr/bin/curl https://example.test", shell=True)',
            'import subprocess; subprocess.run(["sh", "-c", "exec curl https://example.test"])',
            'import subprocess; subprocess.run(["python3", "-c", "import socket"])',
            'from subprocess import run as launch; launch(["wget", "https://example.test"])',
            'import os; os.system("curl https://example.test")',
            'from os import system as launch',
            'from os import execv as launch',
            'from pathlib import os as process; process.system("curl https://example.test")',
            'import urllib.parse; urllib.request.urlopen("https://example.test")',
            '__import__("socket")', 'exec("import socket")',
            'import importlib; importlib.import_module("socket")',
            'import pathlib; pathlib.os.system("curl https://example.test")',
            'import sys; sys.modules["socket"].socket()',
            'import lib.release_reader as reader; reader.urllib.request.urlopen("https://example.test")',
        ]
        for source in cases:
            with self.subTest(source=source), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                (root / 'tools').mkdir()
                (root / 'tools/prove-new.py').write_text(source)
                self.assertTrue(guard.check(root), source)

    def test_program_execution_classes_require_audited_commands(self):
        guard = load(ROOT / 'ops/proof-transport-check.py')
        cases = [
            'import posix; posix.system("curl https://example.test")',
            'from posix import system as launch; launch("curl https://example.test")',
            'import webbrowser; webbrowser.GenericBrowser("/usr/bin/curl").open("https://example.test")',
            'from webbrowser import GenericBrowser as Browser',
            'import asyncio; asyncio.create_subprocess_exec("curl")',
            'from asyncio import create_subprocess_shell as launch; launch("curl")',
            'import pty; pty.spawn(["curl", "https://example.test"])',
            'import multiprocessing as mp; mp.set_executable("/usr/bin/curl")',
            'import ctypes; ctypes.CDLL("libcurl.so")',
            'import cffi; cffi.FFI().dlopen("libcurl.so")',
        ]
        for module in ['os', 'posix', 'nt']:
            for member in ['system', 'execv', 'execve', 'execlp', 'spawnv',
                           'spawnve', 'popen', 'posix_spawn', 'posix_spawnp']:
                cases.append(f'import {module} as process; process.{member}("curl")')
        for source in cases:
            with self.subTest(source=source):
                self.assertTrue(guard.violations(source))
                import hashlib
                path = 'tools/prove-synthetic.py'
                with patch.dict(guard.PROCESSES, {path: {
                    'sha256': hashlib.sha256(source.encode()).hexdigest(),
                    'commands': ['audited synthetic command'],
                }}):
                    self.assertEqual(guard.violations(source, path), [])
                    self.assertTrue(guard.violations(source + '\n# changed', path))

    def test_record_call_requires_pinned_process_declaration(self):
        guard = load(ROOT / 'ops/proof-transport-check.py')
        import hashlib
        source = 'from lib.record_call import call_verb, _run_process'
        path = 'tools/prove-synthetic.py'
        self.assertTrue(guard.violations(source, path))
        with patch.dict(guard.PROCESSES, {path: {
            'sha256': hashlib.sha256(source.encode()).hexdigest(),
            'commands': ['declared wrapper call read-only verbs'],
        }}):
            self.assertEqual(guard.violations(source, path), [])
            self.assertTrue(guard.violations(source + '\n# changed', path))

    def test_socket_url_and_dynamic_classes_cannot_be_process_declared(self):
        guard = load(ROOT / 'ops/proof-transport-check.py')
        import hashlib
        cases = [f'import {name}' for name in [
            'socket', 'ssl', 'http.client', 'urllib.request', 'urllib3',
            'requests', 'httpx', 'aiohttp', 'ftplib', 'smtplib',
            'telnetlib', 'xmlrpc.client', 'importlib',
        ]]
        cases += [
            'import asyncio; asyncio.open_connection("example.test", 443)',
            'import asyncio; asyncio.get_event_loop().create_connection(None, "example.test", 443)',
            'from asyncio import open_connection as connect',
            '__import__("socket")', 'exec("import socket")', 'eval("1 + 1")',
        ]
        for source in cases:
            with self.subTest(source=source):
                path = 'tools/prove-synthetic.py'
                with patch.dict(guard.PROCESSES, {path: {
                    'sha256': hashlib.sha256(source.encode()).hexdigest(),
                    'commands': ['git rev-parse HEAD'],
                }}):
                    self.assertTrue(guard.violations(source, path))

    def test_guard_accepts_shared_reader_and_pure_imports(self):
        guard = load(ROOT / 'ops/proof-transport-check.py')
        self.assertEqual(guard.violations(
            'import urllib.parse\nfrom lib.release_reader import read\n'
            'from datetime import datetime, timezone\nimport json\n'
            'import hashlib, re, signal, os, posix, asyncio\n'
            'from lib import release_reader\n'
            'hashlib.sha256(b"fixture"); re.split(",", "a,b")\n'
            'os.stat("."); posix.stat("."); asyncio.sleep(0)\n'
            'signal.pthread_sigmask(signal.SIG_BLOCK, set())'), [])

    def test_cli_rejects_bypass_and_accepts_shared_reader(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'tools').mkdir()
            proof = root / 'tools/prove-new.py'
            for source, status in [
                ('import subprocess; subprocess.run(["/usr/bin/curl", "https://example.test"])', 1),
                ('import posix; posix.system("curl https://example.test")', 1),
                ('import webbrowser; webbrowser.GenericBrowser("curl").open("https://example.test")', 1),
                ('from lib.release_reader import read; read("https://example.test")', 0),
            ]:
                proof.write_text(source)
                result = subprocess.run([sys.executable, str(ROOT / 'ops/proof-transport-check.py'),
                                         '--root', directory], capture_output=True, text=True)
                self.assertEqual(result.returncode, status, result.stdout + result.stderr)

    def test_merged_main_proof_sources_are_admitted(self):
        guard = load(ROOT / 'ops/proof-transport-check.py')
        self.assertEqual(guard.check(ROOT), [])
        for name in ['identity-domain-recorded-addresses', 'mail-policy-multiple-recorded-addresses']:
            path = f'tools/prove-{name}.py'
            self.assertEqual(guard.violations((ROOT / path).read_text(), path), [])

    def test_process_declarations_are_bound_to_entire_runner(self):
        guard = load(ROOT / 'ops/proof-transport-check.py')
        self.assertEqual(guard.check(ROOT), [])
        for name in guard.PROCESSES:
            source = (ROOT / name).read_text()
            with self.subTest(runner=name):
                self.assertEqual(guard.violations(source, name), [])
                for addition in [
                    '\nsubprocess.run(["/usr/bin/curl", "https://example.test"])',
                    '\nsubprocess.run(["sh", "-c", "exec curl https://example.test"])',
                    '\nimport socket',
                ]:
                    self.assertTrue(guard.violations(source + addition, name))
                self.assertTrue(guard.violations(source.replace("'call'", "'fetch'"), name)
                                if "'call'" in source else
                                guard.violations(source + '\nother_command = "curl"', name))

    def test_shared_reader_caps_success_and_errors_without_following_redirects(self):
        from lib.release_reader import read, USER_AGENT
        requests = []

        class Handler(ReleaseFixtureHandler):
            def release_GET(self):
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

    def test_fixture_runtime_refuses_bypasses_and_admits_reader(self):
        from lib.release_reader import read
        import urllib.error
        import urllib.request

        seen = []

        class Handler(ReleaseFixtureHandler):
            def release_GET(self):
                seen.append(self.headers)
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'fixture-release')

            def log_message(self, *_args):
                pass

        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        try:
            url = f'http://127.0.0.1:{server.server_port}/release'
            for suffix in ['', '?probe=1']:
                with self.assertRaises(urllib.error.HTTPError) as caught:
                    urllib.request.urlopen(url + suffix, timeout=2)
                with caught.exception as error:
                    self.assertEqual(error.code, 403)
                    self.assertIn(b'proof_release_reader_required', error.read())
            direct = subprocess.run([sys.executable, '-c',
                'import urllib.request,sys; urllib.request.urlopen(sys.argv[1]).read()', url],
                capture_output=True, timeout=5)
            self.assertNotEqual(direct.returncode, 0)
            self.assertIn(b'403', direct.stderr)
            for function, args in [
                ('create_subprocess_exec', repr('/usr/bin/curl') + ', ' + repr('--silent') + ', ' + repr('--fail') + ', ' + repr(url)),
                ('create_subprocess_shell', repr('/usr/bin/curl --silent --fail ' + url)),
            ]:
                source = ('import asyncio\nasync def main():\n    child = await asyncio.' + function + '(' + args + ')\n    assert await child.wait() == 0\nasyncio.run(main())\n')
                with tempfile.TemporaryDirectory() as directory:
                    runner = Path(directory) / 'prove-new.py'
                    runner.write_text(source)
                    result = subprocess.run([sys.executable, str(runner)], capture_output=True, timeout=5)
                self.assertNotEqual(result.returncode, 0, function)
                self.assertNotIn(b'fixture-release', result.stdout)
            headers = {'uSeR-aGeNt': 'caller-proof/1', 'Cookie': 'synthetic',
                       'X-DoctorCRE-Release-Reader': 'wrong', 'x-doctorcre-release-reader': 'also-wrong'}
            original = headers.copy()
            self.assertEqual(read(url, headers=headers).body, b'fixture-release')
            self.assertEqual(headers, original)
            self.assertEqual(seen[0]['User-Agent'], 'caller-proof/1')
            self.assertEqual(seen[0]['Cookie'], 'synthetic')
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
