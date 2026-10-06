#!/usr/bin/env python3
"""release-smoke-selftest.py — offline proof of ops/release-smoke.py.

Production is never contacted: the HTTP reader, the probe-token MCP door and
the browser-journey runner are in-memory fakes. What is pinned:
  pass         every journey green against a healthy production
  sign-in gate an app page that answers 5xx, or redirects anywhere but its own
               /auth/login?return_to=<page>, fails that journey
  verbs        after a release every verb the released SHA carries is served;
               before it, the missing ones are reported as the new verbs
  identity     after a release the lane's live SHA must be the released SHA
  read-only    every HTTP request is a GET, every MCP call is a read verb
  evidence     timings and response SHAPES are recorded, never record values
  credential   no probe token is a failure of the authenticated journeys, and
               the token never appears in the summary
  only         a retry runs exactly the named journeys
  CLI          writes summary.json and probes.jsonl under --out
"""
from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
_SPEC = importlib.util.spec_from_file_location("release_smoke", HERE / "release-smoke.py")
assert _SPEC is not None and _SPEC.loader is not None
rs = importlib.util.module_from_spec(_SPEC)
sys.modules["release_smoke"] = rs
_SPEC.loader.exec_module(rs)

SHA = "a" * 40
OLD = "b" * 40
API = "https://api.doctorcre.com"
APP = "https://app.doctorcre.com"
TOKEN = "probe-token-must-never-be-echoed-0123456789"


class FakeProduction:
    """A healthy production: /release and /app-release serve SHA, every gated
    page redirects to its own sign-in, /status renders, every read verb answers."""

    def __init__(self, *, worker_sha=SHA, app_sha=SHA, live_verbs=("deal-board", "lead-board"),
                 gate_override=None, verb_errors=None):
        self.worker_sha, self.app_sha = worker_sha, app_sha
        self.live_verbs = list(live_verbs)
        self.gate_override = gate_override or {}
        self.verb_errors = verb_errors or {}
        self.requests: list[str] = []
        self.calls: list[tuple[str, dict]] = []

    def http(self, url: str, timeout: int = 15):
        self.requests.append(url)
        if url == f"{API}/release":
            return rs.Reply(200, {}, json.dumps({"ok": True, "git_sha": {"value": self.worker_sha},
                                                 "env": {"value": "production"},
                                                 "worker_version": {"id": "12345678-1234-1234-1234-123456789abc"}}).encode())
        if url == f"{APP}/app-release":
            return rs.Reply(200, {}, json.dumps({"service": "doctorcre-app", "environment": "production",
                                                 "source_commit": self.app_sha}).encode())
        if url == f"{APP}/status":
            return rs.Reply(200, {"Content-Type": "text/html; charset=utf-8"}, b"<!doctype html><title>s</title>")
        path = url[len(APP):]
        if path in self.gate_override:
            return self.gate_override[path]
        return rs.Reply(302, {"Location": f"{APP}/auth/login?return_to={path}"}, b"")

    def mcp(self, verb: str, args: dict):
        self.calls.append((verb, args))
        if verb in self.verb_errors:
            return False, self.verb_errors[verb]
        if verb == "list-verbs":
            return True, {"ok": True, "verbs": [{"name": n} for n in self.live_verbs]}
        row = {"name": "Synthetic Orchard Clinic", "phase": "LOI"}
        answers = {
            "deal-board": {"deals": [row]},
            "lead-board": {"leads": [], "stages": [], "metrics": {}, "generated_at": "2026-10-05T00:00:00Z"},
            "read-invoice-tracker": {"schema_version": "invoice-tracker.v1", "actor": "synthetic", "entries": [], "observed_at": "2026-10-05T00:00:00Z"},
            "list-progress-boards": {"ok": True, "schema": "progress-board-directory.v1", "boards": []},
            "list-doc-conversations": {"ok": True, "conversations": []},
        }
        return True, answers[verb]


def smoke(prod: FakeProduction, **kw):
    args = {"lane": "worker", "sha": SHA, "phase": "post", "api": API, "app": APP,
            "http": prod.http, "mcp": prod.mcp, "expected_verbs": None, "browser": None}
    args.update(kw)
    return rs.run_smoke(**args)


def probe(summary: dict, pid: str) -> dict:
    return next(p for p in summary["probes"] if p["id"] == pid)


class Pass(unittest.TestCase):
    def test_a_healthy_production_passes_every_journey(self):
        summary = smoke(FakeProduction())
        self.assertTrue(summary["ok"], summary["failed"])
        self.assertEqual(summary["failed"], [])
        self.assertEqual([p["id"] for p in summary["probes"]], [*rs.JOURNEYS, "browser-journeys"])
        for p in summary["probes"]:
            self.assertIsInstance(p["ms"], int)
        self.assertEqual(len(rs.JOURNEYS), 8)
        # No app checkout given: the browser journeys are skipped and say why, not passed.
        self.assertEqual(probe(summary, "browser-journeys")["status"], "skip")
        self.assertIn("--app-dir", probe(summary, "browser-journeys")["detail"])


class SignInGate(unittest.TestCase):
    def test_a_page_answering_5xx_fails_the_gate_journey_only(self):
        prod = FakeProduction(gate_override={"/invoices": rs.Reply(503, {}, b"DoctorCRE unavailable")})
        summary = smoke(prod)
        self.assertEqual(summary["failed"], ["sign-in-gate"])
        self.assertIn("/invoices answered HTTP 503", probe(summary, "sign-in-gate")["detail"])

    def test_a_redirect_that_loses_the_page_fails(self):
        prod = FakeProduction(gate_override={"/leads": rs.Reply(302, {"Location": f"{APP}/auth/login"}, b"")})
        self.assertIn("/leads redirected", probe(smoke(prod), "sign-in-gate")["detail"])

    def test_a_redirect_off_origin_fails(self):
        prod = FakeProduction(gate_override={
            "/deals": rs.Reply(302, {"Location": "https://evil.example/auth/login?return_to=/deals"}, b"")})
        self.assertEqual(smoke(prod)["failed"], ["sign-in-gate"])


class Verbs(unittest.TestCase):
    RELEASED = ["deal-board", "lead-board", "read-invoice-tracker"]

    def test_after_a_release_every_released_verb_must_be_served(self):
        summary = smoke(FakeProduction(), expected_verbs=self.RELEASED)
        self.assertEqual(summary["failed"], ["verb-registry"])
        self.assertEqual(probe(summary, "verb-registry")["evidence"]["missing"], ["read-invoice-tracker"])

    def test_before_a_release_the_missing_verbs_are_the_new_ones_and_do_not_fail(self):
        summary = smoke(FakeProduction(worker_sha=OLD), phase="baseline", expected_verbs=self.RELEASED)
        self.assertTrue(summary["ok"], summary["failed"])
        self.assertEqual(probe(summary, "verb-registry")["evidence"]["new_verbs"], ["read-invoice-tracker"])

    def test_all_released_verbs_served_passes(self):
        prod = FakeProduction(live_verbs=self.RELEASED + ["list-verbs"])
        self.assertTrue(smoke(prod, expected_verbs=self.RELEASED)["ok"])

    def test_a_verb_that_errors_fails_its_journey(self):
        prod = FakeProduction(verb_errors={"read-invoice-tracker": "unknown_tool"})
        summary = smoke(prod)
        self.assertEqual(summary["failed"], ["invoices-list"])
        self.assertIn("mcp_read_failed", probe(summary, "invoices-list")["detail"])


class Identity(unittest.TestCase):
    def test_after_a_worker_release_the_worker_must_serve_the_released_sha(self):
        summary = smoke(FakeProduction(worker_sha=OLD))
        self.assertEqual(summary["failed"], ["release-identity"])

    def test_the_app_lane_reads_the_app_sha_not_the_worker_sha(self):
        self.assertTrue(smoke(FakeProduction(worker_sha=OLD), lane="app", only=["release-identity"])["ok"])
        self.assertEqual(smoke(FakeProduction(app_sha=OLD), lane="app", only=["release-identity"])["failed"], ["release-identity"])

    def test_the_identity_journey_records_the_versions_a_rollback_needs(self):
        evidence = probe(smoke(FakeProduction()), "release-identity")["evidence"]
        self.assertEqual(evidence["worker_version_id"], "12345678-1234-1234-1234-123456789abc")


class ReadOnly(unittest.TestCase):
    def test_only_read_verbs_are_called_and_no_record_value_is_kept(self):
        prod = FakeProduction()
        summary = smoke(prod, expected_verbs=["deal-board"])
        self.assertEqual({v for v, _ in prod.calls},
                         set(rs.READ_VERBS.values()) | {"list-verbs"})
        text = json.dumps(summary)
        self.assertNotIn("Synthetic Orchard Clinic", text)
        self.assertEqual(probe(summary, "deal-board")["evidence"]["shape"],
                         {"deals": "list[1]"})

    def test_the_read_verbs_are_reads_in_the_registry(self):
        # Pinned by name here; the CLI refuses a write verb at import (see Cli).
        self.assertEqual(sorted(rs.READ_VERBS.values()), sorted([
            "deal-board", "lead-board", "read-invoice-tracker", "list-progress-boards",
            "list-doc-conversations"]))


class Only(unittest.TestCase):
    def test_a_retry_runs_exactly_the_named_journeys(self):
        prod = FakeProduction()
        summary = smoke(prod, only=["invoices-list", "sign-in-gate"])
        self.assertEqual([p["id"] for p in summary["probes"]], ["sign-in-gate", "invoices-list"])
        self.assertEqual([v for v, _ in prod.calls], ["read-invoice-tracker"])


class Browser(unittest.TestCase):
    def test_a_failed_browser_journey_fails_with_its_title(self):
        outcome = {"tests": [{"title": "deal board gate", "status": "passed"},
                             {"title": "status page renders", "status": "failed"}]}
        summary = smoke(FakeProduction(), browser=lambda: outcome)
        self.assertEqual(summary["failed"], ["browser-journeys"])
        self.assertEqual(probe(summary, "browser-journeys")["detail"], "browser_proof_failed_or_incomplete")

    def test_an_empty_browser_run_is_not_a_pass(self):
        self.assertEqual(smoke(FakeProduction(), browser=lambda: {"tests": []})["failed"], ["browser-journeys"])


class FakeResponse:
    def __init__(self, body: dict):
        self.body = json.dumps(body).encode()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return self.body


class McpDoor(unittest.TestCase):
    def test_a_read_verb_goes_as_a_json_rpc_tools_call_with_the_probe_bearer(self):
        sent = []

        def opener(request, timeout):
            sent.append(request)
            return FakeResponse({"jsonrpc": "2.0", "id": 1, "result": {
                "content": [{"type": "text", "text": json.dumps({"ok": True, "deals": []})}]}})
        ok, answer = rs.McpProbe(API, TOKEN, opener=opener)("deal-board", {})
        self.assertEqual((ok, answer), (True, {"ok": True, "deals": []}))
        request = sent[0]
        self.assertEqual((request.full_url, request.get_method()), (f"{API}/mcp", "POST"))
        self.assertEqual(request.get_header("Authorization"), f"Bearer {TOKEN}")
        body = json.loads(request.data)
        self.assertEqual((body["method"], body["params"]["name"]), ("tools/call", "deal-board"))

    def test_an_error_answer_is_a_failure_and_never_echoes_the_token(self):
        def opener(request, timeout):
            return FakeResponse({"jsonrpc": "2.0", "id": 1, "result": {
                "isError": True, "content": [{"type": "text", "text": "not_in_profile"}]}})
        ok, answer = rs.McpProbe(API, TOKEN, opener=opener)("deal-board", {})
        self.assertFalse(ok)
        self.assertIn("mcp_tool_failed", answer)
        self.assertNotIn(TOKEN, answer)

    def test_a_write_verb_is_refused_before_any_request(self):
        def opener(request, timeout):
            raise AssertionError("a write verb reached the network")
        ok, answer = rs.McpProbe(API, TOKEN, opener=opener)("add-loop", {})
        self.assertFalse(ok)
        self.assertIn("not a read journey", answer)


class Cli(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.cred = self.tmp / "cred"
        self.cred.mkdir()

    def tearDown(self):
        self._tmp.cleanup()

    def main(self, *extra, prod=None):
        prod = prod or FakeProduction()
        out = self.tmp / "out"
        rc = rs.main(["--lane", "worker", "--sha", SHA, "--phase", "post", "--out", str(out),
                      "--credential-dir", str(self.cred), *extra],
                     http=prod.http, mcp_factory=lambda api, token: prod.mcp, out_line=lambda _s: None)
        return rc, out

    def test_writes_the_summary_and_one_line_per_journey(self):
        (self.cred / "mcp-tokens.env").write_text(f"CARR_MCP_PROBE_TOKEN={TOKEN}\n")
        rc, out = self.main()
        self.assertEqual(rc, 0)
        summary = json.loads((out / "summary.json").read_text())
        self.assertTrue(summary["ok"])
        lines = (out / "probes.jsonl").read_text().splitlines()
        self.assertEqual([json.loads(line)["id"] for line in lines], [*rs.JOURNEYS, "browser-journeys"])
        self.assertNotIn(TOKEN, (out / "summary.json").read_text())

    def test_a_failure_exits_1(self):
        (self.cred / "mcp-tokens.env").write_text(f"CARR_MCP_PROBE_TOKEN={TOKEN}\n")
        rc, out = self.main(prod=FakeProduction(worker_sha=OLD))
        self.assertEqual(rc, 1)
        self.assertEqual(json.loads((out / "summary.json").read_text())["failed"], ["release-identity"])

    def test_no_probe_token_fails_every_authenticated_journey_and_says_why(self):
        rc, out = self.main()
        self.assertEqual(rc, 1)
        summary = json.loads((out / "summary.json").read_text())
        self.assertEqual(summary["failed"], [*rs.READ_VERBS, "verb-registry"])
        self.assertIn("mcp_read_failed", next(p for p in summary["probes"]
                                                   if p["id"] == "deal-board")["detail"])

    def test_only_is_comma_separated(self):
        (self.cred / "mcp-tokens.env").write_text(f"CARR_MCP_PROBE_TOKEN={TOKEN}\n")
        rc, out = self.main("--only", "deal-board,dr-cre-chat")
        self.assertEqual([p["id"] for p in json.loads((out / "summary.json").read_text())["probes"]],
                         ["deal-board", "dr-cre-chat"])


class BlockingRegressions(unittest.TestCase):
    def test_business_reads_reject_partial_and_malformed_payloads(self):
        for value in ({}, None, 'arbitrary text', {'ok': True}, {'deals': None}):
            result = smoke(FakeProduction(), mcp=lambda v, a: (True, value), only=list(rs.READ_VERBS))
            self.assertEqual(result['failed'], list(rs.READ_VERBS))

    def test_mcp_door_rejects_invalid_rpc_and_non_json_content(self):
        for body in ({}, None, {'jsonrpc': '2.0', 'id': 1, 'result': {}},
                     {'jsonrpc': '2.0', 'id': 9, 'result': {'content': [{'type': 'text', 'text': '{"deals":[]}'}]}},
                     {'jsonrpc': '2.0', 'id': 1, 'result': {'content': [{'type': 'text', 'text': 'not JSON'}]}}):
            ok, _ = rs.McpProbe(API, TOKEN, opener=lambda *a, **k: FakeResponse(body))('deal-board', {})
            self.assertFalse(ok)

    def test_empty_business_collections_are_valid_and_partial_reads_fail(self):
        prod = FakeProduction()
        for verb in rs.READ_VERBS.values():
            valid = prod.mcp(verb, {})[1]
            for key, value in valid.items():
                if isinstance(value, list):
                    valid[key] = []
            def read(name, args):
                return (True, valid) if name == verb else prod.mcp(name, args)
            self.assertTrue(smoke(prod, mcp=read)['ok'])
            for key in rs.READ_CONTRACTS[verb]:
                partial = {k: v for k, v in valid.items() if k != key}
                self.assertFalse(smoke(prod, mcp=lambda n, a: (True, partial), only=list(rs.READ_VERBS))['ok'])

    def test_registry_rejects_partial_rows_and_failed_envelopes(self):
        for answer in ({'ok': False, 'verbs': [{'name': 'deal-board'}]},
                       {'ok': True, 'verbs': [{'name': 'deal-board'}, {}]},
                       {'verbs': [{'name': 'deal-board'}]}):
            self.assertFalse(smoke(FakeProduction(), mcp=lambda v, a: (True, answer), only=['verb-registry'])['ok'])

    def test_identity_requires_http_success_and_envelope_in_baseline_too(self):
        prod = FakeProduction()
        for phase in ('baseline', 'post'):
            def down(url):
                r = prod.http(url)
                return rs.Reply(503, r.headers, r.body)
            self.assertEqual(smoke(prod, phase=phase, http=down, only=['release-identity'])['failed'], ['release-identity'])
            self.assertFalse(smoke(prod, phase=phase, http=lambda u: rs.Reply(200, {}, b'{}'), only=['release-identity'])['ok'])

    def test_browser_exit_and_missing_required_journeys_fail(self):
        for outcome in ({'exit': 1, 'tests': [{'title': 'synthetic', 'status': 'passed'}]},
                        {'exit': 0, 'required': ['missing'], 'tests': [{'title': 'synthetic', 'status': 'passed'}]}):
            self.assertEqual(smoke(FakeProduction(), browser=lambda: outcome)['failed'], ['browser-journeys'])

    def test_failure_paths_keep_no_response_values(self):
        canary = 'synthetic-sensitive-canary'
        for mcp in (lambda v, a: (False, canary), lambda v, a: (True, {'ok': False, 'error': canary})):
            summary = smoke(FakeProduction(), mcp=mcp)
            self.assertNotIn(canary, json.dumps(summary))
        def crashed(*a):
            raise RuntimeError(canary)
        self.assertNotIn(canary, json.dumps(smoke(FakeProduction(), mcp=crashed, browser=crashed)))
        self.assertNotIn(canary, json.dumps(smoke(FakeProduction(), browser=lambda: {'exit': 1, 'tests': [{'title': canary, 'status': 'failed'}], 'error': canary})))
        self.assertNotIn(canary, json.dumps(smoke(FakeProduction(), browser=lambda: {'exit': canary, 'tests': []})))

    def test_app_lane_requires_browser_proof(self):
        self.assertIn('browser-journeys', smoke(FakeProduction(), lane='app')['failed'])

    def test_configured_app_checkout_never_silently_skips(self):
        with tempfile.TemporaryDirectory() as directory:
            runner = rs.browser_runner(Path(directory), Path(directory) / 'out')
            self.assertIsNotNone(runner)
            self.assertFalse(smoke(FakeProduction(), browser=runner)['ok'])

    def test_fixture_is_wholly_synthetic(self):
        answer = FakeProduction().mcp('deal-board', {})[1]
        self.assertIn('Synthetic', json.dumps(answer))


class InstalledBrowserContract(unittest.TestCase):
    def test_current_process_and_required_native_coverage_are_both_required(self):
        from unittest.mock import patch
        import subprocess
        with tempfile.TemporaryDirectory() as directory:
            app = Path(directory)
            for name in ('scripts/browser-product-proof.mjs', 'e2e.config.ts', 'node_modules/e2e/dist/cli/bin.js'):
                path = app / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.touch()
            manifest = app / 'tests/journeys/required-coverage.json'
            manifest.parent.mkdir(parents=True)
            manifest.write_text(json.dumps({'tests': [{'file': 'tests/journeys/synthetic.e2e.ts', 'title': 'synthetic journey'}]}))
            key = 'tests/journeys/synthetic.e2e.ts::synthetic%20journey'
            proof = app / '.e2e/proof'
            proof.mkdir(parents=True)
            def produce(rc=0, tests=True):
                packet = {'schema': 'browser-product-proof.v1', 'binding': {'repo': 'jbookout/doctorcre-app', 'sourceCommit': SHA, 'runId': 'synthetic-run'}}
                report = {'run': {'id': 'synthetic-run', 'vcs': {'commit': SHA, 'dirty': False}, 'exitCode': 0, 'status': 'passed',
                    'results': [{'testId': key, 'selected': True, 'status': 'passed', 'attempts': [{}]}] if tests else []}}
                (proof / 'packet.json').write_text(json.dumps(packet))
                (proof / 'native-report.json').write_text(json.dumps(report))
                for name in ('checkpoint.png', 'video.webm', 'trace.zip'):
                    (proof / name).write_bytes(b'synthetic artifact')
                return subprocess.CompletedProcess([], rc, '', '')
            runner = rs.browser_runner(app, app / 'out', expected_sha=SHA)
            with patch.object(rs.subprocess, 'check_output', return_value=SHA):
                with patch.object(rs.subprocess, 'run', side_effect=lambda *a, **k: produce()):
                    summary = smoke(FakeProduction(), browser=runner)
                    self.assertTrue(summary['ok'])
                    self.assertEqual(probe(summary, 'browser-journeys')['evidence']['source_commit'], SHA)
                with patch.object(rs.subprocess, 'run', side_effect=lambda *a, **k: produce(rc=1)):
                    self.assertFalse(smoke(FakeProduction(), browser=runner)['ok'])
                with patch.object(rs.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, '', '')):
                    self.assertFalse(smoke(FakeProduction(), browser=runner)['ok'])
                with patch.object(rs.subprocess, 'run', side_effect=lambda *a, **k: produce(tests=False)):
                    self.assertFalse(smoke(FakeProduction(), browser=runner)['ok'])
            with patch.object(rs.subprocess, 'check_output', return_value=OLD):
                self.assertFalse(smoke(FakeProduction(), browser=runner)['ok'])


if __name__ == "__main__":
    unittest.main()
