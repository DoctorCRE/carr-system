import importlib.util
import contextlib
import io
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch


REPO = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("e2e_staging_session", REPO / "tools/e2e-staging-session.py")
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
SHA = "a" * 40
TOKEN = "synthetic-provider-token"
SECRET = "synthetic-e2e-session-secret-for-tests-only"


class StagingSessionTests(unittest.TestCase):
    def test_timeout_stops_children_before_refusal_and_suppresses_output(self):
        for detached in (False, True):
            with self.subTest(detached=detached), tempfile.TemporaryDirectory() as directory:
                marker = Path(directory) / "late-effect"
                ready = Path(directory) / "ready"
                child = (
                    "import pathlib, signal, time; "
                    "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
                    f"pathlib.Path({str(ready)!r}).write_text('ready'); "
                    "time.sleep(0.8); "
                    f"pathlib.Path({str(marker)!r}).write_text('synthetic')"
                )
                launcher = (
                    "import subprocess, sys, time; "
                    f"p = subprocess.Popen([sys.executable, '-c', {child!r}], "
                    f"start_new_session={detached!r}); "
                    f"print({SECRET!r}, flush=True); "
                    f"print({TOKEN!r}, file=sys.stderr, flush=True); "
                    "p.wait()"
                )

                def short_run(args, **kwargs):
                    if str(REPO / "bin/with-timeout.py") in args:
                        args = [*args[:2], "0.2", *args[3:]]
                        kwargs["timeout"] = 3
                    else:
                        kwargs["timeout"] = 0.2
                    return subprocess.run(args, **kwargs)

                output, errors = io.StringIO(), io.StringIO()
                with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
                    with self.assertRaises(MODULE.StagingRefusal) as caught:
                        MODULE.checked_run([sys.executable, "-c", launcher], run=short_run)
                self.assertEqual(ready.read_text(), "ready")
                self.assertNotIn(SECRET, str(caught.exception) + output.getvalue() + errors.getvalue())
                self.assertNotIn(TOKEN, str(caught.exception) + output.getvalue() + errors.getvalue())
                time.sleep(1)
                self.assertFalse(marker.exists(), "child performed an effect after timeout refusal")

    def test_checked_run_preserves_output_input_and_exit_failure(self):
        command = [sys.executable, "-c", "import sys; print(sys.stdin.read().upper(), end='')"]
        self.assertEqual(MODULE.checked_run(command, input="synthetic\n"), "SYNTHETIC\n")
        with self.assertRaisesRegex(MODULE.StagingRefusal, "command failed; output suppressed"):
            MODULE.checked_run([sys.executable, "-c", "raise SystemExit(7)"])

    def test_apply_requires_the_exact_carr_source_before_loading_credentials(self):
        with patch.object(MODULE, "source_identity", return_value=SHA):
            for arguments in [["--apply"], ["--apply", "--source-sha", "b" * 40]]:
                with self.assertRaises(MODULE.StagingRefusal):
                    MODULE.main(arguments)

    def test_config_refuses_production_and_shared_kv(self):
        source = (REPO / "mcp-server/wrangler.toml").read_text()
        MODULE.validate_config(source)
        for changed in [
            source.replace('name = "carr-mcp-staging"', 'name = "carr-mcp"'),
            source.replace('routes = []', 'routes = [{pattern = "app.doctorcre.com", custom_domain = true}]'),
            source.replace('CARR_ENV = "staging"', 'CARR_ENV = "production"'),
            source.replace('id = "faa513fd07ec45d88abc5949f323aec5"', 'id = "b033d3b5c454445080e9d0ab51ff8f3c"'),
        ]:
            with self.subTest(changed=changed[-250:]):
                with self.assertRaises(MODULE.StagingRefusal):
                    MODULE.validate_config(changed)

    def test_provider_operations_are_fixed_staging_and_do_not_print_secrets(self):
        calls = []
        def run(args, **kwargs):
            calls.append((list(map(str, args[3:])), kwargs))
            return subprocess.CompletedProcess(args, 0, "provider output containing a secret", "")
        child_env = MODULE.provider_environment({"PATH": "/bin", "DATABASE_URL_WRITER": "forbidden", "CLOUDFLARE_API_TOKEN": "wrong"}, TOKEN)
        self.assertEqual(child_env["CLOUDFLARE_API_TOKEN"], TOKEN)
        self.assertNotIn("DATABASE_URL_WRITER", child_env)
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            MODULE.provision_and_deploy(REPO, SHA, SECRET, child_env, run=run)
        self.assertNotIn(TOKEN, output.getvalue())
        self.assertNotIn(SECRET, output.getvalue())
        wrangler = [call for call in calls if "wrangler" in Path(call[0][0]).name]
        self.assertEqual(len(wrangler), 2)
        self.assertEqual(wrangler[0][0][1:], ["secret", "put", "E2E_SESSION_SECRET", "--env", "staging"])
        self.assertEqual(wrangler[0][1]["input"], SECRET + "\n")
        self.assertIn("GIT_SHA:" + SHA, wrangler[1][0])
        for args, kwargs in wrangler:
            self.assertEqual(args[args.index("--env") + 1], "staging")
            self.assertNotIn(SECRET, " ".join(args))
            self.assertTrue(kwargs["capture_output"])
            self.assertEqual(kwargs["env"]["CI"], "true")

    def test_failed_provider_output_is_suppressed(self):
        def run(args, **kwargs):
            return subprocess.CompletedProcess(args, 1, SECRET, TOKEN)
        with self.assertRaises(MODULE.StagingRefusal) as caught:
            MODULE.provision_and_deploy(REPO, SHA, SECRET, MODULE.provider_environment({}, TOKEN), run=run)
        self.assertNotIn(TOKEN, str(caught.exception))
        self.assertNotIn(SECRET, str(caught.exception))

    def test_source_requires_a_clean_exact_committed_candidate(self):
        def run(args, **kwargs):
            output = SHA + "\n" if "rev-parse" in args else " M mcp-server/src/dealroom-web.js\n"
            return subprocess.CompletedProcess(args, 0, output, "")
        with self.assertRaises(MODULE.StagingRefusal):
            MODULE.source_identity(REPO, run=run)

    def test_smoke_accepts_only_the_dedicated_normal_cookie_and_exact_source(self):
        calls = []
        def fetch(path, **kwargs):
            calls.append((path, kwargs))
            if path == "/release":
                return 200, {}, {"env": {"value": "staging"}, "git_sha": {"value": SHA}}
            if path == "/auth/e2e-session":
                return 200, {"set-cookie": "__Host-dealroom_session=synthetic-cookie; Path=/; Secure; HttpOnly; SameSite=Lax"}, {"ok": True}
            return 200, {}, {"actor": {"slug": "joe", "display": "E2E Joe"}, "e2e_principal": "e2e-joe", "csrf_token": "synthetic-csrf"}
        MODULE.smoke(SHA, SECRET, fetch=fetch)
        self.assertEqual([call[0] for call in calls], ["/release", "/auth/e2e-session", "/auth/session"])
        self.assertEqual(calls[1][1]["headers"]["authorization"], "Bearer " + SECRET)
        self.assertEqual(calls[2][1]["headers"]["cookie"], "__Host-dealroom_session=synthetic-cookie")
        with self.assertRaises(MODULE.StagingRefusal):
            MODULE.smoke("b" * 40, SECRET, fetch=fetch)

    def test_staging_fetch_identifies_the_browser_compatible_qa_client(self):
        calls = []
        class Response(io.BytesIO):
            status = 200
            headers = {"content-type": "application/json"}
        class Opener:
            def open(self, request, timeout):
                calls.append(request)
                if not request.get_header("User-agent", "").startswith("Mozilla/5.0"):
                    raise MODULE.urllib.error.HTTPError(request.full_url, 403, "edge browser integrity", {}, None)
                return Response(b'{"ok":true}')
        with patch.object(MODULE.urllib.request, "build_opener", return_value=Opener()):
            status, _, body = MODULE.staging_fetch("/auth/e2e-session", method="POST", headers={"authorization": "Bearer " + SECRET})
        self.assertEqual((status, body), (200, {"ok": True}))
        self.assertEqual(calls[0].full_url, "https://" + MODULE.CARR_HOST + "/auth/e2e-session")
        self.assertIn("DoctorCRE-Staging-E2E", calls[0].get_header("User-agent"))
        self.assertEqual(calls[0].get_header("Authorization"), "Bearer " + SECRET)


if __name__ == "__main__":
    unittest.main()
