import io
import http.client
import importlib.util
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from lib import github_app_token as app


class GitHubAppTokenTests(unittest.TestCase):
    def test_helper_imports_from_repository_package(self):
        result = subprocess.run(
            [sys.executable, "-c",
             "from lib.github_app_token import gh_env, remaining_budget; "
             "print(callable(gh_env), callable(remaining_budget))"],
            cwd=ROOT, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "True True\n")

    @classmethod
    def setUpClass(cls):
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import rsa

        cls.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cls.pem = cls.key.private_bytes(serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8, serialization.NoEncryption())

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="github-app-test-")
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.config = self.home / "config.json"
        self.write_config()
        key = self.home / ".config/carr/github-app.pem"
        key.parent.mkdir(parents=True)
        key.write_bytes(self.pem)
        key.chmod(0o600)
        self.addCleanup(patch.stopall)
        patch.object(app, "CONFIG_PATH", self.config).start()
        patch.object(Path, "home", return_value=self.home).start()
        patch.object(app.time, "time", return_value=2000000000).start()
        patch.dict(os.environ, {"GH_TOKEN": "synthetic-user", "GITHUB_TOKEN": "synthetic-user",
                                "GH_HOST": "synthetic.enterprise"}).start()
        self.http = patch.object(app.urllib.request, "urlopen").start()
        self.http.side_effect = self.response
        self.requests = []

    def write_config(self, **overrides):
        config = {"app_id": 5199739, "client_id": "Iv23liV6joa2rIh9RjPj",
                  "repository": "jbookout/carr-system", "key_file": "github-app.pem",
                  "reserve": 500}
        config.update(overrides)
        self.config.write_text(json.dumps(config))

    def response(self, request, **kwargs):
        import jwt
        claims = jwt.decode(request.headers["Authorization"].split(" ", 1)[1],
                            self.key.public_key(), algorithms=["RS256"],
                            options={"verify_exp": False, "verify_iat": False})
        self.assertEqual(claims, {"iat": 1999999940, "exp": 2000000540, "iss": "5199739"})
        self.requests.append(request)
        if request.full_url.endswith("/installation"):
            result = {"id": 123}
        else:
            self.assertEqual(request.get_method(), "POST")
            result = {"token": "synthetic-app", "expires_at": "2033-05-18T04:33:20Z"}
        return io.BytesIO(json.dumps(result).encode())

    def test_mints_verified_rs256_and_reuses_private_cache(self):
        env = app.gh_env()
        self.assertTrue(env["GH_TOKEN"] == "synthetic-app")
        self.assertTrue(env["CARR_GITHUB_BUDGET_PRINCIPAL"].startswith("app:"))
        self.assertNotIn("GITHUB_TOKEN", env)
        self.assertEqual(env["GH_HOST"], "github.com")
        self.assertTrue(os.environ["GH_TOKEN"] == "synthetic-user")
        self.assertTrue(app.gh_env()["GH_TOKEN"] == "synthetic-app")
        self.assertEqual(len(self.requests), 2)
        cache = self.home / ".cache/carr/github-app-token.json"
        self.assertEqual(stat.S_IMODE(cache.stat().st_mode), 0o600)

    def test_refreshes_at_five_minute_boundary(self):
        app.gh_env()
        with patch.object(app.time, "time", return_value=2000003300):
            self.http.side_effect = lambda request, **kwargs: io.BytesIO(json.dumps(
                {"id": 123} if request.full_url.endswith("/installation") else
                {"token": "synthetic-refreshed", "expires_at": "2033-05-18T05:33:20Z"}).encode())
            self.assertTrue(app.gh_env()["GH_TOKEN"] == "synthetic-refreshed")
        self.assertEqual(self.http.call_count, 4)

    def test_cache_is_bound_to_repository_and_key(self):
        app.gh_env()
        self.write_config(repository="jbookout/doctorcre-app")
        app.gh_env()
        self.assertEqual(self.http.call_count, 4)
        with (self.home / ".config/carr/github-app.pem").open("ab") as key:
            key.write(b"\n")
        app.gh_env()
        self.assertEqual(self.http.call_count, 6)

    def test_configuration_errors_stop_calls_without_reading_credentials(self):
        for content in ("{", "{}", '{"app_id":"no"}', "[]"):
            self.config.write_text(content)
            with self.assertRaises(app.GitHubAppError):
                app.gh_env()
            self.http.assert_not_called()

    def test_missing_key_falls_back_to_stored_login_with_one_log_line(self):
        self.write_config(key_file="absent.pem")
        with self.assertLogs(app.LOG, level="WARNING") as logs:
            env = app.gh_env()
        self.assertNotIn("GH_TOKEN", env)
        self.assertNotIn("GITHUB_TOKEN", env)
        self.assertEqual(len(logs.output), 1)
        self.assertIn("private key missing; falling back to user login", logs.output[0])
        self.http.assert_not_called()

    def test_missing_or_suspended_installation_logs_fallback(self):
        for unavailable in (urllib.error.HTTPError("synthetic", 404, "missing", {}, None),
                            io.BytesIO(b'{"id":123,"suspended_at":"2033-01-01T00:00:00Z"}')):
            self.http.side_effect = unavailable if isinstance(unavailable, Exception) else None
            self.http.return_value = unavailable
            with self.assertLogs(app.LOG, level="WARNING") as logs:
                self.assertNotIn("GH_TOKEN", app.gh_env())
            self.assertEqual(len(logs.output), 1)
            self.assertIn("falling back to user login", logs.output[0])

    def test_api_failure_never_uses_personal_allowance_or_leaks_details(self):
        self.http.side_effect = urllib.error.URLError("synthetic-secret-error")
        with self.assertRaises(app.GitHubAppError) as error:
            app.gh_env()
        self.assertNotIn("synthetic-secret-error", str(error.exception))

    def test_http_protocol_and_partial_reads_stop_calls_with_sanitized_errors(self):
        class Socket:
            def makefile(self, *args):
                return io.BytesIO(b"HTTP/1.1 200 OK\r\nContent-Length: 100\r\n\r\n{}")

        for post in (False, True):
            for failure in ("partial", "status"):
                with self.subTest(post=post, failure=failure):
                    response = http.client.HTTPResponse(Socket())
                    response.begin()
                    transport = (response if failure == "partial" else
                                 http.client.BadStatusLine("synthetic-secret-status"))
                    self.http.side_effect = ([io.BytesIO(b'{"id":123}'), transport]
                                             if post else [transport])
                    with self.assertRaises(app.GitHubAppError) as error:
                        app.gh_env()
                    self.assertEqual(str(error.exception),
                                     "GitHub App API request failed; GitHub calls stopped")
                    self.assertTrue(error.exception.__suppress_context__)

    def test_loose_cache_is_refreshed_and_invalid_response_is_refused(self):
        app.gh_env()
        cache = self.home / ".cache/carr/github-app-token.json"
        cache.chmod(0o644)
        app.gh_env()
        self.assertEqual(self.http.call_count, 4)
        self.assertEqual(stat.S_IMODE(cache.stat().st_mode), 0o600)
        self.http.side_effect = lambda *args, **kwargs: io.BytesIO(b'{"id":123}')
        with patch.object(app.time, "time", return_value=2000003300):
            with self.assertRaises(app.GitHubAppError):
                app.gh_env()

    def test_token_with_less_than_five_minutes_remaining_is_refused(self):
        self.http.side_effect = lambda request, **kwargs: io.BytesIO(json.dumps(
            {"id": 123} if request.full_url.endswith("/installation") else
            {"token": "synthetic-app", "expires_at": "2033-05-18T03:38:20Z"}).encode())
        with self.assertRaises(app.GitHubAppError):
            app.gh_env()

    def test_concurrent_clients_share_one_mint(self):
        from concurrent.futures import ThreadPoolExecutor

        with ThreadPoolExecutor(max_workers=4) as pool:
            envs = list(pool.map(lambda _: app.gh_env(), range(4)))
        self.assertTrue(all(env["GH_TOKEN"] == "synthetic-app" for env in envs))
        self.assertEqual(self.http.call_count, 2)

    def test_budget_pauses_if_either_pool_is_below_floor(self):
        env = {"GH_TOKEN": "synthetic-app"}
        for core, graphql, paused, reset in ((499, 900, True, 2000000100),
                                            (900, 499, True, 2000000200),
                                            (499, 499, True, 2000000200),
                                            (500, 500, False, None)):
            payload = {"resources": {"core": {"remaining": core, "reset": 2000000100},
                                      "graphql": {"remaining": graphql, "reset": 2000000200}}}
            with patch.object(app.subprocess, "run", return_value=subprocess.CompletedProcess(
                    [], 0, json.dumps(payload), "")) as gh:
                budget = app.remaining_budget(env=env)
            self.assertEqual((budget.core, budget.graphql, budget.paused, budget.reset),
                             (core, graphql, paused, reset))
            self.assertEqual(gh.call_args.args[0], ["gh", "api", "rate_limit"])
            self.assertIs(gh.call_args.kwargs["env"], env)
            if paused:
                self.assertTrue(budget.message.startswith("paused until "))

    def test_configurable_reserve_and_budget_error_are_explicit(self):
        self.write_config(reserve=100)
        payload = {"resources": {"core": {"remaining": 100, "reset": 2000000100},
                                  "graphql": {"remaining": 100, "reset": 2000000200}}}
        with patch.object(app.subprocess, "run", return_value=subprocess.CompletedProcess(
                [], 0, json.dumps(payload), "")):
            self.assertFalse(app.remaining_budget(env={}).paused)
            self.assertTrue(app.remaining_budget(env={}, reserve=101).paused)
        for output, status in (("{}", 0), ("synthetic-secret-error", 1)):
            with patch.object(app.subprocess, "run", return_value=subprocess.CompletedProcess(
                    [], status, output, "synthetic-secret-error")):
                with self.assertRaises(app.GitHubAppError) as error:
                    app.remaining_budget(env={})
                self.assertNotIn("synthetic-secret-error", str(error.exception))

    def test_budget_rejects_unrenderable_reset_in_either_pool(self):
        for pool in ("core", "graphql"):
            for reset in (10**30, 253402300800, -1, True, "synthetic-secret-reset"):
                with self.subTest(pool=pool, reset=reset):
                    payload = {"resources": {
                        "core": {"remaining": 1, "reset": 2000000100},
                        "graphql": {"remaining": 900, "reset": 2000000200}}}
                    payload["resources"][pool]["reset"] = reset
                    with patch.object(app.subprocess, "run", return_value=subprocess.CompletedProcess(
                            [], 0, json.dumps(payload), "")):
                        with self.assertRaises(app.GitHubAppError) as error:
                            app.remaining_budget(env={})
                    self.assertEqual(str(error.exception),
                                     "GitHub budget read failed; GitHub calls stopped")

    def test_budget_can_render_supported_utc_limits(self):
        for reset, expected in ((0, "1970-01-01T00:00:00+00:00"),
                                (253402300799, "9999-12-31T23:59:59+00:00")):
            payload = {"resources": {"core": {"remaining": 1, "reset": reset},
                                      "graphql": {"remaining": 900, "reset": 2000000200}}}
            with patch.object(app.subprocess, "run", return_value=subprocess.CompletedProcess(
                    [], 0, json.dumps(payload), "")):
                self.assertEqual(app.remaining_budget(env={}).message, f"paused until {expected}")


class IntakeContractTests(unittest.TestCase):
    def test_guard_allows_named_intake_without_executing_it(self):
        spec = importlib.util.spec_from_file_location("github_app_guard", ROOT / "hooks/guard-unattended.py")
        guard = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(guard)
        for command in ("./bin/github-app-key-intake.sh", "bash bin/github-app-key-intake.sh"):
            self.assertIsNone(guard.check(command))


class IntakeBehaviorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="github-intake-test-")
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        self.home = base / "home"
        self.downloads = self.home / "Downloads"
        self.downloads.mkdir(parents=True)
        self.repo = base / "repo"
        (self.repo / "bin").mkdir(parents=True)
        (self.repo / "ops/config").mkdir(parents=True)
        shutil.copytree(ROOT / "lib", self.repo / "lib", ignore=shutil.ignore_patterns("__pycache__"))
        self.script = self.repo / "bin/github-app-key-intake.sh"
        shutil.copy2(ROOT / "bin/github-app-key-intake.sh", self.script)
        self.config = {"app_id": 123, "repository": "synthetic/repo",
                       "key_file": "synthetic.pem", "reserve": 500}
        self.config_path = self.repo / "ops/config/github-app.json"
        self.config_path.write_text(json.dumps(self.config))
        self.destination = self.home / ".config/carr/synthetic.pem"
        self.env = {**os.environ, "HOME": str(self.home)}

    def download(self, suffix="", *, mtime=2000000000):
        path = self.downloads / f"carr-watchdog-jbookout{suffix}.private-key.pem"
        path.write_text(f"synthetic-key{suffix}")
        path.chmod(0o644)
        os.utime(path, (mtime, mtime))
        return path

    def run_intake(self):
        return subprocess.run(["bash", str(self.script)], env=self.env,
                              capture_output=True, text=True, timeout=10)

    def assert_refusal(self, result):
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertTrue(result.stderr.startswith("key intake failed"), result.stderr)
        self.assertNotIn("synthetic-key", result.stderr)
        self.assertNotIn("synthetic-secret-error", result.stderr)

    def test_moves_newest_download_and_repeats_without_consuming_another(self):
        oldest = self.download("-z", mtime=1000000000)
        newest = self.download("-a", mtime=2000000000)
        unrelated = self.downloads / "unrelated.pem"
        unrelated.write_text("unrelated")
        result = self.run_intake()
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, "installed\n", ""))
        self.assertEqual(self.destination.read_text(), "synthetic-key-a")
        self.assertEqual(stat.S_IMODE(self.destination.stat().st_mode), 0o600)
        self.assertFalse(newest.exists())
        self.assertEqual(oldest.read_text(), "synthetic-key-z")
        self.assertEqual(unrelated.read_text(), "unrelated")
        self.destination.chmod(0o644)
        result = self.run_intake()
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, "installed\n", ""))
        self.assertEqual(self.destination.read_text(), "synthetic-key-a")
        self.assertEqual(stat.S_IMODE(self.destination.stat().st_mode), 0o600)
        self.assertTrue(oldest.exists())

    def test_missing_downloads_refuses_without_installing(self):
        self.downloads.rmdir()
        self.assert_refusal(self.run_intake())
        self.assertFalse(self.destination.exists())

    def test_symbolic_link_and_directory_downloads_are_not_consumed(self):
        outside = self.home / "outside.pem"
        outside.write_text("synthetic-key-outside")
        outside.chmod(0o644)
        link = self.downloads / "carr-watchdog-jbookout-link.private-key.pem"
        link.symlink_to(outside)
        directory = self.downloads / "carr-watchdog-jbookout-dir.private-key.pem"
        directory.mkdir()
        self.assert_refusal(self.run_intake())
        self.assertTrue(link.is_symlink())
        self.assertTrue(directory.is_dir())
        self.assertEqual(outside.read_text(), "synthetic-key-outside")
        self.assertEqual(stat.S_IMODE(outside.stat().st_mode), 0o644)
        self.assertFalse(self.destination.exists())

    def test_symbolic_link_and_directory_destinations_refuse(self):
        source = self.download()
        self.destination.parent.mkdir(parents=True)
        outside = self.home / "outside.pem"
        outside.write_text("synthetic-key-outside")
        self.destination.symlink_to(outside)
        self.assert_refusal(self.run_intake())
        self.assertEqual(outside.read_text(), "synthetic-key-outside")
        self.assertTrue(source.exists())
        self.destination.unlink()
        self.destination.mkdir()
        self.assert_refusal(self.run_intake())
        self.assertTrue(self.destination.is_dir())
        self.assertTrue(source.exists())

    def test_filesystem_failures_stop_without_printing_credentials(self):
        source = self.download()
        shims = self.home / "shims"
        shims.mkdir()
        self.env["PATH"] = str(shims) + os.pathsep + os.environ["PATH"]
        for command in ("mkdir", "chmod", "mv"):
            with self.subTest(command=command):
                shim = shims / command
                shim.write_text("#!/bin/sh\nprintf 'synthetic-secret-error\\n' >&2\nexit 1\n")
                shim.chmod(0o755)
                self.assert_refusal(self.run_intake())
                self.assertTrue(source.exists())
                self.assertFalse(self.destination.exists())
                shim.unlink()

    def test_intake_and_token_helper_share_configuration_validation(self):
        source = self.download()
        for field, invalid in (("app_id", "invalid"), ("repository", "../repo/invalid"),
                               ("reserve", -1), ("key_file", "../outside.pem"),
                               ("key_file", "."), ("key_file", ".."), ("key_file", 123)):
            with self.subTest(field=field, invalid=invalid):
                self.config_path.write_text(json.dumps({**self.config, field: invalid}))
                with (patch.object(app, "CONFIG_PATH", self.config_path),
                      patch.object(Path, "home", return_value=self.home)):
                    with self.assertRaises(app.GitHubAppError):
                        app.gh_env()
                self.assert_refusal(self.run_intake())
                self.assertTrue(source.exists())
                self.assertFalse(self.destination.exists())


if __name__ == "__main__":
    unittest.main()
