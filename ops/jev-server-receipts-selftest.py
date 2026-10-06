#!/usr/bin/env python3
"""Offline Worker transport regressions; retired prompt gates must stay retired."""
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

REPO = str(Path(__file__).resolve().parents[1])
sys.path.insert(0, REPO)
NOW = datetime.now(timezone.utc)
_FIXTURE_STORAGE = tempfile.TemporaryDirectory(prefix="jev-server-receipts-selftest-")


def tearDownModule():
    _FIXTURE_STORAGE.cleanup()


def ts(offset_seconds=0):
    return (NOW + timedelta(seconds=offset_seconds)).strftime("%Y-%m-%dT%H:%M:%S.000Z")

def _write(obj, suffix):
    fh = tempfile.NamedTemporaryFile(mode="w", suffix=suffix, delete=False)
    if suffix == ".jsonl":
        for row in obj:
            fh.write(json.dumps(row) + "\n")
    else:
        json.dump(obj, fh)
    fh.close()
    return fh.name

def report(ok, label):
    print(f"{'PASS' if ok else 'FAIL'}  {label}")
    return ok

def _client():
    spec = importlib.util.spec_from_file_location(
        "tsc_server_selftest", os.path.join(REPO, "ops", "typesafe_client.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    # Production-shaped fake transports exercise real reservation code, but
    # their synthetic attempts must never read or change the live counter.
    mod.JEV_DAILY_CAP_LOG = Path(tempfile.mkdtemp(dir=_FIXTURE_STORAGE.name)) / "jev-calls.jsonl"
    # Admission (registry, attribution, fixture refusal) is covered by
    # ops/jev-call-sites-selftest.py; this suite tests the transport beneath it.
    site = {"caller": "*", "trigger": "selftest", "runs_in": "selftest",
            "attribution": "session_or_job", "unattended": "allowed",
            "hourly_budget": 10**9, "daily_budget": 10**9, "owner": "selftest",
            "value": "selftest", "sources": ["ops/typesafe_client.py"]}
    mod.load_call_sites = lambda path=None: {"hourly_paid_call_cap": 10**9, "sites": {"*": site}}
    mod.call_site = lambda caller, registry: site
    # Hermetic credentials, as on a hosted runner: the Worker route signs with
    # a fixture admission secret and no local vendor key exists to fall back on.
    mod.read_admission_secret = lambda: "fixture-admission-secret"
    def no_local_vendor_key(*_args):
        raise mod.TypeSafeError("fixture holds no local vendor key")
    mod.read_api_key = no_local_vendor_key
    return mod


for _name in ("CARR_JEV_OFFLINE", "CARR_HOOK_FIXTURE", "CARR_JEV_WORKER"):
    os.environ.pop(_name, None)
os.environ["CARR_JEV_JOB"] = "jev-server-receipts-selftest"

class _Proc:
    def __init__(self, code, out="", err=""):
        self.returncode, self.stdout, self.stderr = code, out, err

def client_routes_through_the_worker():
    tsc = _client()
    seen = {}

    def runner(argv, **_kw):
        if json.loads(argv[3]).get("transport_mode") == "cache_only":
            return _Proc(1, "", '{"error":"jev_cache_miss"}')
        seen["verb"] = argv[2]
        seen["args"] = json.loads(argv[3])
        return _Proc(0, json.dumps({
            "ok": True, "receipt_id": "srv-1", "recorded_at": ts(0), "purpose": "call",
            "session_id": "s", "model": "jev-1.13.0", "state_sha256": "a" * 64,
            "prompt_sha256": None, "usage": {"input_tokens": 3, "output_tokens": 1},
            "answers": {"diagnosis_q": {"type": "noul", "noul": 0.7}}}))
    tsc.read_api_key = lambda *a: (_ for _ in ()).throw(AssertionError("server path read a local credential"))
    log = _write([], ".jsonl")
    try:
        result = tsc.ask({"x": 1}, {"diagnosis_q": tsc.noul("is it?")}, facets=["diagnosis"],
                         calls_log=log, server_runner=runner, cache_ttl_seconds=0)
        with open(log) as fh:
            rows = [json.loads(line) for line in fh if line.strip()]
    finally:
        os.unlink(log)
    ok = (seen.get("verb") == "ask-jev" and seen["args"]["purpose"] == "call"
          and seen["args"]["facets"] == ["diagnosis"]
          and seen["args"]["idempotency_key"].startswith("jev1.")
          and "fixture-admission-secret" not in json.dumps(seen)
          and result["server_receipt"]["receipt_id"] == "srv-1"
          and result["answers"]["diagnosis_q"]["noul"] == 0.7
          and result["usage"] == {"input_tokens": 3, "output_tokens": 1}
          and rows and rows[0]["server_receipt_id"] == "srv-1"
          and rows[0]["ok"] is True and rows[0]["usable"] is True)
    return report(ok, "client: ask() goes through the Worker's ask-jev verb and records the "
                      "server receipt id locally")

def client_falls_back_visibly():
    tsc = _client()

    def runner(argv, **_kw):
        return _Proc(1, "", 'TOOL ERROR {"error": "unknown_tool", "name": "ask-jev"}')

    class _Resp(io.BytesIO):
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False
    def fake_urlopen(req, timeout=None):
        return _Resp(json.dumps(
            {"model": "jev-1.13.0", "usage": {"input_tokens": 3, "output_tokens": 1},
             "answers": {"q": {"type": "noul", "noul": 0.2}}}).encode())
    original = urllib.request.urlopen
    setattr(urllib.request, "urlopen", fake_urlopen)
    original_key = tsc.read_api_key
    tsc.read_api_key = lambda path=None: "not-a-real-key"
    log = _write([], ".jsonl")
    try:
        result = tsc.ask("s", {"q": tsc.noul("?")}, calls_log=log, server_runner=runner, cache_ttl_seconds=0)
        with open(log) as fh:
            rows = [json.loads(line) for line in fh if line.strip()]
    finally:
        setattr(urllib.request, "urlopen", original)
        tsc.read_api_key = original_key
        os.unlink(log)
    ok = ("server_receipt" not in result and rows and rows[0]["server_receipt_id"] is None
          and rows[0]["server_error"] == "verb_not_deployed"
          and "not-a-real-key" not in json.dumps(rows))
    return report(ok, "client: with the verb not deployed, ask() still answers directly but the "
                      "local row says server_receipt_id null and why (so the gates treat it as "
                      "unverified); the key never reaches the row")

def malformed_server_answer_is_not_credited():
    tsc = _client()

    def runner(argv, **_kw):
        if json.loads(argv[3]).get("transport_mode") == "cache_only":
            return _Proc(1, "", '{"error":"jev_cache_miss"}')
        return _Proc(0, json.dumps({
            "ok": True, "receipt_id": "srv-malformed", "model": "jev-1.13.0",
            "usage": {"input_tokens": 3, "output_tokens": 1},
            "answers": {"q": {"type": "noul", "noul": 1.5}}}))

    log = _write([], ".jsonl")
    raised = False
    try:
        try:
            tsc.ask("synthetic", {"q": tsc.noul("Is the fixture valid?")},
                    calls_log=log, server_runner=runner)
        except tsc.TypeSafeError:
            raised = True
        with open(log) as fh:
            rows = [json.loads(line) for line in fh if line.strip()]
    finally:
        os.unlink(log)
    return report(raised and len(rows) == 1 and rows[0]["ok"] is False
                  and rows[0]["schema_valid"] is False,
                  "malformed server answers retain main's schema validation and no local credit")

def server_cache_answer_preserves_no_spend():
    tsc = _client()

    def runner(argv, **_kw):
        return _Proc(0, json.dumps({
            "ok": True, "receipt_id": "srv-cached", "model": "jev-1.13.0",
            "cache_hit": True, "usage": None,
            "answers": {"q": {"type": "noul", "noul": 0.7}}}))

    log = _write([], ".jsonl")
    try:
        result = tsc.ask("synthetic", {"q": tsc.noul("Is the fixture valid?")},
                         calls_log=log, server_runner=runner)
        with open(log) as fh:
            rows = [json.loads(line) for line in fh if line.strip()]
    finally:
        os.unlink(log)
    return report(result.get("cache_hit") is True and result["usage"] is None
                  and result["server_receipt"]["receipt_id"] == "srv-cached"
                  and rows[0]["cache_hit"] is True and rows[0]["ok"] is False,
                  "Worker cache hits return typed answers and a bound receipt without claiming spend")

def client_server_attempt_shares_the_budget():
    tsc = _client()
    seen = {}

    def slow_fail(argv, timeout=None, **_kw):
        seen["timeout"] = timeout
        return _Proc(1, "", "could not reach the deployed Worker")
    raised = False
    try:
        tsc.ask("s", {"q": tsc.noul("?")}, timeout=1.0, calls_log=os.devnull,
                server_runner=slow_fail)
    except tsc.TypeSafeError:
        raised = True
    ok = (raised and seen.get("timeout") is not None
          and seen["timeout"] <= 1.0 * tsc.SERVER_SHARE_OF_TIMEOUT)
    return report(ok, f"F7: the server attempt gets at most {tsc.SERVER_SHARE_OF_TIMEOUT:.0%} of "
                      f"the caller's timeout and the direct fallback only what is left "
                      f"(server got {seen.get('timeout')}s of 1.0s; out of time raised={raised})")

def in_hook_calls_skip_the_server_but_the_advisory_does_not():
    tsc = _client()
    calls = []

    def runner(argv, **_kw):
        if json.loads(argv[3]).get("transport_mode") == "cache_only":
            return _Proc(1, "", '{"error":"jev_cache_miss"}')
        calls.append(json.loads(argv[3])["purpose"])
        return _Proc(0, json.dumps({
            "ok": True, "receipt_id": "srv-h", "recorded_at": ts(0), "purpose": "build_advisory",
            "session_id": "s", "model": "jev-1.13.0", "state_sha256": "a" * 64,
            "prompt_sha256": "b" * 64, "usage": {"input_tokens": 3, "output_tokens": 1},
            "answers": {"q": {"type": "noul", "noul": 0.7}}}))

    class _Resp(io.BytesIO):
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=None):
        return _Resp(json.dumps(
            {"model": "jev-1.13.0", "usage": {"input_tokens": 3, "output_tokens": 1},
             "answers": {"q": {"type": "noul", "noul": 0.2}}}).encode())
    original = urllib.request.urlopen
    setattr(urllib.request, "urlopen", fake_urlopen)
    original_key = tsc.read_api_key
    tsc.read_api_key = lambda path=None: "not-a-real-key"
    saved = os.environ.get(tsc.IN_HOOK_ENV)
    os.environ[tsc.IN_HOOK_ENV] = "1"
    log = _write([], ".jsonl")
    try:
        direct = tsc.ask("s", {"q": tsc.noul("?")}, calls_log=log, server_runner=runner, cache_ttl_seconds=0)
        advisory = tsc.ask({"partner_request": "x"}, {"q": tsc.noul("?")}, calls_log=log,
                           server_runner=runner, purpose="build_advisory")
        with open(log) as fh:
            rows = [json.loads(line) for line in fh if line.strip()]
    finally:
        setattr(urllib.request, "urlopen", original)
        tsc.read_api_key = original_key
        if saved is None:
            os.environ.pop(tsc.IN_HOOK_ENV, None)
        else:
            os.environ[tsc.IN_HOOK_ENV] = saved
        os.unlink(log)
    ok = (calls == ["build_advisory"] and "server_receipt" not in direct
          and advisory.get("server_receipt", {}).get("receipt_id") == "srv-h"
          and rows and rows[0]["server_error"] == "in_hook_direct")
    return report(ok, "a hook's own Jev call goes direct and is marked uncredited "
                      "(in_hook_direct); other explicit callers still take the server path")


class ReviewRegressions(unittest.TestCase):
    def test_retired_stop_and_agent_cannot_load_transcript_facet_enforcement(self):
        # Findings 1-3: malformed IDs, duplicate IDs and ambiguous anchors
        # belonged to the retired prompt-facet checks, not boundary judgments.
        for path in ("hooks/completion-evidence-gate.py", "hooks/executor-tier-gate.py"):
            with self.subTest(path=path):
                source = (Path(REPO) / path).read_text()
                self.assertFalse("from lib.jev_required_actions import" in source, path)
                self.assertFalse("from lib.jev_server_receipts import" in source, path)

    def test_stale_advisories_and_replays_have_no_server_facet_credit_path(self):
        # Findings 4-5: retain main's historical envelope fixtures, remove
        # this PR's unused server enforcement extension rather than repair it.
        source = (Path(REPO) / "lib/jev_required_actions.py").read_text()
        self.assertFalse("def binding_advisory_rows(" in source)
        self.assertFalse("def server_credited_calls(" in source)
        self.assertTrue(server_cache_answer_preserves_no_spend())

    def test_transport_has_no_unused_duplicate_reader(self):
        # Finding 8: after retirement no production caller reads these rows.
        self.assertFalse((Path(REPO) / "lib/jev_server_receipts.py").exists())

    def test_runtime_uses_its_pinned_vendor_route_when_system_provider_differs(self):
        # Finding 7: exercise ask() without the opener/key overrides which
        # skipped the production-shaped server hop in the earlier tests.
        client = _client()
        server_attempts, vendor_calls = [], []
        def wrong_class_server(argv, **kwargs):
            server_attempts.append(argv)
            return _Proc(1, err='{"error":"decisions_unavailable"}')
        class Response(io.BytesIO):
            status = 200
        def vendor(request, **kwargs):
            vendor_calls.append(json.loads(request.data))
            return Response(json.dumps({"model":"jev-1.13.0",
                "answers":{"q":{"type":"noul","noul":0.7}},
                "usage":{"input_tokens":3,"output_tokens":1}}).encode())
        log = _write([], ".jsonl")
        try:
            with patch.dict(os.environ, {"CARR_JEV_IN_HOOK":"0"}), \
                 patch.object(client.JUDGE, "provider_for", side_effect=lambda cls, config=None: "decisions" if cls == "system_work" else "jev"), \
                 patch.object(client, "read_api_key", return_value="offline-fixture"), \
                 patch.object(client.urllib.request, "urlopen", side_effect=vendor):
                with self.assertRaisesRegex(client.TypeSafeError, "decisions"):
                    client.ask("code", {"q": client.noul("valid?")}, calls_log=log,
                               server_runner=wrong_class_server, cache_ttl_seconds=0)
                result = client.ask("deal", {"q": client.noul("valid?")},
                    work_class="app_runtime", calls_log=log,
                    server_runner=wrong_class_server, cache_ttl_seconds=0)
            self.assertEqual(server_attempts, [])
            self.assertEqual(len(vendor_calls), 1)
            self.assertEqual(result["answers"]["q"]["noul"], 0.7)
            rows = [json.loads(line) for line in Path(log).read_text().splitlines()]
            self.assertTrue(rows[0]["ok"])
            self.assertIsNone(rows[0]["server_receipt_id"])
        finally:
            os.unlink(log)

    def test_real_server_result_and_no_answer_negative(self):
        # Finding 6: assert the result/receipt, not an allowed hook which may
        # merely have returned unavailable after a fabricated prompt boundary.
        self.assertTrue(client_routes_through_the_worker())
        self.assertTrue(malformed_server_answer_is_not_credited())

    def test_existing_server_budget_and_fallback_contracts(self):
        self.assertTrue(client_falls_back_visibly())
        self.assertTrue(client_server_attempt_shares_the_budget())
        self.assertTrue(in_hook_calls_skip_the_server_but_the_advisory_does_not())

if __name__ == "__main__":
    unittest.main()
