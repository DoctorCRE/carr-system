#!/usr/bin/env python3
"""Offline acceptance tests. Only loopback and recorded vendor responses run."""
import io
import hashlib
import json
import os
from contextlib import closing
from pathlib import Path
import sys
import tempfile
import socket
import threading
import time
from urllib.parse import urlsplit
from datetime import datetime, timezone
import subprocess
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import typesafe_client as ts
import jevlint_review as review

RECORDING = json.loads((Path(__file__).parent / "fixtures/jevlint/systemone-recording.json").read_text())
PAYLOAD = RECORDING["payload"]
RESPONSE = RECORDING["response"]


class VendorResponse(io.BytesIO):
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


class AdmissionTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.log = self.root / "calls.jsonl"
        registry = self.root / "sites.json"
        entry = {"caller": "jevlint_review", "trigger": "fixture", "runs_in": "fixture",
                 "attribution": "session", "unattended": "allowed", "hourly_budget": 1,
                 "daily_budget": 1, "owner": "orchestrator", "value": "fixture",
                 "sources": ["ops/jevlint_review.py"]}
        registry.write_text(json.dumps({"schema": "carr-jev-call-sites/v1",
                                       "hourly_paid_call_cap": 10, "sites": [entry]}))
        self.enterContext(patch.object(ts, "JEV_DAILY_CAP_LOG", str(self.log)))
        self.enterContext(patch.object(ts, "JEV_CALL_SITES_PATH", str(registry)))
        self.enterContext(patch.dict(ts.ask.__kwdefaults__, calls_log=str(self.log)))
        self.enterContext(patch.dict(ts.JEV_COST_CONFIG, daily_paid_call_cap=10))
        self.enterContext(patch.object(ts, "read_api_key", return_value="offline-recording"))
        self.server_ask = ts.server_ask
        self.enterContext(patch.object(ts, "server_ask", return_value=(None, "offline")))
        self.enterContext(patch.object(ts, "_launch_spend_alert_worker", return_value=None))
        env = {k: v for k, v in os.environ.items() if k not in ts.SESSION_ID_ENV_KEYS and
               k not in ("CARR_JEV_OFFLINE", "CARR_HOOK_FIXTURE", "CARR_JEV_WORKER", "CARR_JEV_JOB")}
        self.enterContext(patch.dict(os.environ, env, clear=True))
        self.enterContext(patch.dict(os.environ, CARR_JEV_SEMANTIC_CACHE=str(self.root / "semantic-cache.json")))
        self.transport = self.enterContext(patch.object(ts.urllib.request, "urlopen",
            side_effect=lambda *a, **kw: VendorResponse(json.dumps(RESPONSE).encode())))

    def rows(self):
        return [json.loads(row) for row in self.log.read_text().splitlines()]

    def test_refusal_reaches_http_contract_without_second_vendor_call(self):
        shim = review.Shim("fixture-session", "pr:1537:head")
        self.assertEqual(shim.evaluate(PAYLOAD), (200, RESPONSE))
        other = {**PAYLOAD, "state": {**PAYLOAD["state"], "name": "other"}}
        status, body = shim.evaluate(other)
        self.assertEqual(status, 403)
        self.assertIn(body["error"], ("site_hourly_budget", "site_daily_budget"))
        self.assertEqual(self.transport.call_count, 1)
        self.assertEqual(self.rows()[-1]["caller"], "jevlint_review")
        self.assertEqual(self.rows()[-1]["error"], body["error"])

    def test_call_has_session_pr_attribution_and_shared_daily_cap_receipt(self):
        status, body = review.Shim("fixture-session", "pr:1537:head").evaluate(PAYLOAD)
        self.assertEqual(status, 200)
        self.assertEqual(body, RESPONSE)
        row = self.rows()[-1]
        self.assertEqual(row["session"], "fixture-session")
        self.assertEqual(row["caller"], "jevlint_review")
        self.assertIn("pr:1537:head", row["facets"])
        self.assertTrue(row["ok"])
        with closing(ts.sqlite3.connect(ts._cap_db_path())) as db:
            self.assertEqual(db.execute("SELECT attempts FROM daily_cap").fetchone()[0], 1)

    def test_missing_attribution_makes_zero_vendor_calls(self):
        status, body = review.Shim(None, "pr:1537:head").evaluate(PAYLOAD)
        self.assertEqual(status, 403)
        self.assertEqual(body["error"], "missing_attribution")
        self.transport.assert_not_called()

    def test_retries_reuse_answer_instead_of_buying_again(self):
        shim = review.Shim("fixture-session", "pr:1537:head")
        self.assertEqual(shim.evaluate(PAYLOAD), shim.evaluate(PAYLOAD))
        self.assertEqual(self.transport.call_count, 1)

    def test_separate_shims_reuse_complete_request_without_paid_reservation(self):
        first = review.Shim("fixture-session", "pr:1531:head")
        second = review.Shim("fixture-session", "pr:1531:head")
        self.assertEqual(first.evaluate(PAYLOAD), (200, RESPONSE))
        status, body = second.evaluate(PAYLOAD)
        self.assertEqual(status, 200)
        self.assertEqual(body["answers"], RESPONSE["answers"])
        self.assertEqual(self.transport.call_count, 1)
        self.assertEqual(first.paid_attempts, 1)
        self.assertEqual(second.paid_attempts, 0)
        self.assertEqual(second.cached, 1)
        self.assertEqual(body["usage"], {"input_tokens": 0, "output_tokens": 0})

    def test_payload_cannot_choose_another_provider(self):
        status, _ = review.Shim("fixture-session", "pr:1537:head").evaluate(
            {**PAYLOAD, "model": "another-model", "endpoint": "https://api.typesafe.ai"})
        self.assertEqual(status, 400)
        self.transport.assert_not_called()

    def test_wrong_response_model_is_unavailable(self):
        with patch.object(ts, "ask", return_value={**RESPONSE, "model": "other-model"}):
            status, body = review.Shim("fixture-session", "pr:1537:head").evaluate(PAYLOAD)
        self.assertEqual((status, body), (424, {"error": "jev_unavailable"}))

    def test_daily_cap_refuses_before_transport(self):
        with patch.dict(ts.JEV_COST_CONFIG, daily_paid_call_cap=0):
            status, body = review.Shim("fixture-session", "pr:1537:head").evaluate(PAYLOAD)
        self.assertEqual((status, body), (403, {"error": "daily_paid_call_cap"}))
        self.transport.assert_not_called()

    def test_http_and_review_runner_refusal_exit_two(self):
        binary = self.root / "jevlint"
        binary.write_text('''#!/usr/bin/env python3
import json, os, sys, urllib.request, urllib.error
assert sys.argv[1] == 'check' and '--changed' in sys.argv
assert os.environ['JEVLINT_PROVIDER'] == 'typesafe'
assert os.environ['TYPESAFE_API_KEY'] == 'carr-jevlint-loopback'
payload = json.loads(os.environ['FIXTURE_PAYLOAD'])
for name in ('_exact', 'other'):
    payload['state']['name'] = name
    request = urllib.request.Request(os.environ['TYPESAFE_ENDPOINT'], data=json.dumps(payload).encode(),
        headers={'Authorization':'Bearer carr-jevlint-loopback', 'Content-Type':'application/json'})
    try:
        with urllib.request.urlopen(request) as response: json.load(response)
    except urllib.error.HTTPError as error:
        assert error.code == 403
        sys.exit(2)
print(json.dumps({'findings':[]}))
''')
        binary.chmod(0o700)
        with patch.dict(os.environ, FIXTURE_PAYLOAD=json.dumps(PAYLOAD),
                        JEVLINT_PROVIDER="openrouter", TYPESAFE_ENDPOINT="https://invalid.example"):
            shim = review.Shim("fixture-session", "pr:1537:head")
            code, report = review.run_jevlint(binary, self.root, shim, port=0)
        self.assertEqual(code, 2)
        self.assertEqual(report["refused"], 1)
        self.assertEqual(self.transport.call_count, 1)
        self.assertEqual(shim.paid_attempts, 1)

    def test_real_pinned_cli_with_recorded_response_when_installed(self):
        binary = Path.home() / "go/bin/jevlint"
        if not binary.exists():
            self.skipTest("optional upstream binary; HTTP runner contract is always tested")
        from git_env import fixture_env
        env = fixture_env()
        subprocess.run(["git", "init", "-q", str(self.root)], env=env, check=True)
        (self.root / "sample.py").write_text(PAYLOAD["state"]["source"])
        (self.root / "jevlint.json").write_text(json.dumps({"languages":{"python":{}},
            "rules":[{"id":"wrapper-without-value", "description":"Do not merely forward arguments.",
                      "kinds":["function"], "severity":"warning", "include":["**/*"]}]}))
        with patch.dict(os.environ, env, clear=True):
            code, report = review.run_jevlint(binary, self.root,
                review.Shim("fixture-session", "pr:fixture:head"), port=0)
        self.assertEqual(code, 1, report)
        self.assertEqual(report["evaluations"], 1)
        self.assertEqual(report["findings"][0]["ruleId"], "wrapper-without-value")
        self.assertEqual(self.transport.call_count, 1)

    def test_paid_receipt_counts_each_utc_day_across_midnight(self):
        shim = review.Shim('fixture-session', 'pr:fixture:head')
        with patch.object(ts, 'datetime') as clock:
            clock.now.return_value = datetime(2026, 10, 4, 23, 59, tzinfo=timezone.utc)
            self.assertEqual(shim.evaluate(PAYLOAD)[0], 200)
            clock.now.return_value = datetime(2026, 10, 5, tzinfo=timezone.utc)
            other = {**PAYLOAD, 'state':{**PAYLOAD['state'], 'name':'other'}}
            self.assertEqual(shim.evaluate(other)[0], 200)
        self.assertEqual(shim.paid_attempts, 2)
        self.assertEqual(shim.paid_attempts_by_utc_day, {'2026-10-04':1, '2026-10-05':1})

    def test_retained_yesterday_and_foreign_runs_do_not_change_paid_receipt(self):
        # Admission prunes old site buckets; neither that nor another caller's
        # session can change the receipt of a run with no requests.
        ts._reserve_paid_call({}, [], "jevlint_review", "fixture", "seed")
        with closing(ts.sqlite3.connect(ts._cap_db_path())) as db:
            db.execute("UPDATE site_usage SET day='2000-01-01', count=200")
            db.commit()
        shim = review.Shim("fixture-session", "pr:fixture:head")
        def other_run(*args, **kwargs):
            ts._reserve_paid_call({}, [], "jevlint_review", "fixture", "foreign")
            return subprocess.CompletedProcess(args, 0, '{"findings":[]}', '')
        with patch.object(review.subprocess, "run", side_effect=other_run):
            code, _ = review.run_jevlint("unused", self.root, shim, port=0)
        self.assertEqual(code, 0)
        self.assertEqual(shim.paid_attempts, 0)

    def test_paid_receipt_includes_failed_worker_and_direct_fallback_only_once_each(self):
        registry = ts.load_call_sites()
        registry['sites']['jevlint_review'].update(hourly_budget=10, daily_budget=10)
        shim = review.Shim("fixture-session", "pr:fixture:head")
        calls = []
        def worker(argv, **kwargs):
            mode = json.loads(argv[3])['transport_mode']
            calls.append(mode)
            error = 'jev_cache_miss' if mode == 'cache_only' else 'jev_upstream_failed'
            return subprocess.CompletedProcess(argv, 1, '', json.dumps({'error': error}))
        with patch.object(ts, 'load_call_sites', return_value=registry), \
                patch.object(ts, 'server_ask', self.server_ask), \
                patch.object(ts, 'read_admission_secret', return_value='offline-admission'), \
                patch.object(ts.subprocess, 'run', side_effect=worker):
            self.assertEqual(shim.evaluate(PAYLOAD), (200, RESPONSE))
            self.assertEqual(shim.evaluate(PAYLOAD), (200, RESPONSE))
        self.assertEqual(calls, ['cache_only', 'paid_once'])
        self.assertEqual(shim.paid_attempts, 2)
        self.assertEqual(shim.cached, 1)
        self.assertEqual(self.transport.call_count, 1)

    def test_worker_cache_hit_and_refusal_have_no_paid_reservation(self):
        shim = review.Shim("fixture-session", "pr:fixture:head")
        with patch.object(ts, 'server_ask', return_value=({**RESPONSE, 'cache_hit':True}, None)):
            self.assertEqual(shim.evaluate(PAYLOAD), (200, {**RESPONSE,
                'usage': {'input_tokens': 0, 'output_tokens': 0}}))
        self.assertEqual(shim.paid_attempts, 0)
        with patch.dict(ts.JEV_COST_CONFIG, daily_paid_call_cap=0):
            refused = review.Shim("fixture-session", "pr:fixture:head")
            changed = {**PAYLOAD, 'state': {**PAYLOAD['state'], 'name': 'uncached'}}
            self.assertEqual(refused.evaluate(changed)[0], 403)
        self.assertEqual(refused.paid_attempts, 0)

    def test_concurrent_same_caller_and_session_reservations_stay_in_their_run(self):
        barrier = threading.Barrier(2)
        receipts = []
        failures = []
        def reserve(run, attempts):
            try:
                with ts.capture_paid_reservations(caller='jevlint_review', session_id='same-session', run_id=run) as receipt:
                    barrier.wait(2)
                    for _ in range(attempts):
                        ts._reserve_paid_call({}, [], 'jevlint_review', 'fixture', run, session='same-session')
                    # Same thread, foreign session: also excluded.
                    ts._reserve_paid_call({}, [], 'jevlint_review', 'fixture', run, session='foreign-session')
                receipts.append(receipt)
            except Exception as error:
                failures.append(error)
        threads = [threading.Thread(target=reserve, args=('first', 1)),
                   threading.Thread(target=reserve, args=('second', 3))]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(5)
            self.assertFalse(thread.is_alive())
        self.assertEqual(failures, [])
        self.assertEqual({r['run_id']:sum(r['utc_days'].values()) for r in receipts}, {'first':1, 'second':3})

    def test_stalled_http_peer_makes_runner_unavailable(self):
        peers = []
        def upstream(*args, **kwargs):
            endpoint = urlsplit(kwargs['env']['TYPESAFE_ENDPOINT'])
            peer = socket.create_connection((endpoint.hostname, endpoint.port))
            peers.append(peer)
            peer.sendall(b'POST /v1/systemone HTTP/1.1\r\nContent-Length: 100\r\nAuthorization: Bearer carr-jevlint-loopback\r\n\r\n{')
            time.sleep(.05)
            return subprocess.CompletedProcess(args, 0, '{"findings":[]}', '')
        start = time.monotonic()
        try:
            with patch.object(review.subprocess, 'run', side_effect=upstream):
                code, report = review.run_jevlint('unused', self.root, review.Shim('fixture', 'pr:fixture:head'), port=0)
            self.assertEqual(code, 2)
            self.assertEqual(report['error'], 'jevlint_unavailable')
            self.assertLess(time.monotonic() - start, 1)
        finally:
            for peer in peers:
                peer.close()


class ServerTests(unittest.TestCase):
    def test_dripping_header_has_an_absolute_deadline(self):
        shim = review.Shim('fixture', 'pr:fixture:head')
        with patch.object(review, 'REQUEST_TIMEOUT_SECONDS', .2):
            with review.serve(shim, port=0) as url:
                endpoint = urlsplit(url)
                with socket.create_connection((endpoint.hostname, endpoint.port)) as peer:
                    peer.sendall(b'POST /v1/systemone HTTP/1.1\r\nX-Drip: ')
                    for _ in range(6):
                        time.sleep(.025)
                        peer.sendall(b'x')
                    peer.settimeout(.5)
                    self.assertEqual(peer.recv(1024), b'')
        self.assertGreater(shim.errors, 0)
    def test_stalled_headers_and_body_cannot_block_teardown(self):
        for request in (b'POST /v1/systemone HTTP/1.1\r\n',
                        b'POST /v1/systemone HTTP/1.1\r\nAuthorization: Bearer carr-jevlint-loopback\r\nContent-Length: 100\r\n\r\n{'):
            with self.subTest(request=request):
                shim = review.Shim('fixture', 'pr:fixture:head')
                context = review.serve(shim, port=0)
                endpoint = urlsplit(context.__enter__())
                peer = socket.create_connection((endpoint.hostname, endpoint.port))
                peer.sendall(request)
                time.sleep(.05)
                done = threading.Event()
                def exit_context():
                    context.__exit__(None, None, None)
                    done.set()
                closer = threading.Thread(target=exit_context)
                closer.start()
                finished = done.wait(1)
                peer.close()  # release the old implementation even on failure
                closer.join(2)
                self.assertTrue(finished, 'stalled peer blocked context teardown')
                self.assertGreater(shim.errors, 0)

    def test_stalled_peer_expires_and_next_request_succeeds(self):
        shim = review.Shim('fixture', 'pr:fixture:head')
        with patch.object(review, 'REQUEST_TIMEOUT_SECONDS', .1, create=True):
            with review.serve(shim, port=0) as url:
                endpoint = urlsplit(url)
                with socket.create_connection((endpoint.hostname, endpoint.port)) as peer:
                    peer.sendall(b'POST /v1/systemone HTTP/1.1\r\n')
                    peer.settimeout(1)
                    self.assertEqual(peer.recv(1024), b'')
                req = ts.urllib.request.Request(url, data=b'{}', headers={
                    'Authorization':'Bearer carr-jevlint-loopback'})
                with self.assertRaises(ts.urllib.error.HTTPError) as error:
                    ts.urllib.request.urlopen(req, timeout=1)
                self.assertEqual(error.exception.code, 400)
                error.exception.close()
        self.assertGreater(shim.errors, 0)


class DiffTests(unittest.TestCase):
    def test_literal_filenames_and_invalid_tree_entry_follow_cli_contract(self):
        from git_env import fixture_env
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); repo = root / 'repo'; repo.mkdir()
            env = fixture_env()
            def git(*args):
                return subprocess.check_output(['git', '-C', str(repo), *args], env=env).decode().strip()
            git('init', '-q')
            git('-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '--allow-empty', '-qm', 'base')
            base = git('rev-parse', 'HEAD')
            names = [':(literal)x.py', 'white space.py', 'tab\tname.py', 'unicode-ñ.py', '[wild].py']
            for name in names:
                (repo / name).write_text('def added(): return 2\n')
            git('--literal-pathspecs', 'add', '--', *names)
            git('-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '-qm', 'head')
            scratch = root / 'scratch'; scratch.mkdir()
            config = root / 'config.json'; config.write_text('{}')
            self.assertEqual(set(review.materialize(repo, base, 'HEAD', scratch, config)), set(names))
            for name in names:
                self.assertEqual((scratch / name).read_bytes(), (repo / name).read_bytes())
            # Missing/malformed ls-tree output must produce JSON/2 through main.
            real_git = review.git
            original_output = subprocess.check_output
            def missing_entry(repo, *args):
                if 'ls-tree' in args:
                    return b''
                return real_git(repo, *args)
            with patch.object(sys, 'argv', ['jevlint_review.py', '--repo', str(repo), '--base', base,
                                          '--pr', 'fixture', '--session', 'fixture']), \
                 patch.object(review, 'workspace_parent', return_value=root / 'cache'), \
                 patch.object(review, 'git', side_effect=missing_entry), \
                 patch.object(review.subprocess, 'check_output', wraps=subprocess.check_output) as output, \
                 patch.object(sys, 'stdout', new_callable=io.StringIO) as stdout:
                output.side_effect = lambda args, **kw: review.INSTALL['go_module_build'] if args[:2] == ['go','version'] else original_output(args, **kw)
                self.assertEqual(review.main(), 2)
                self.assertEqual(json.loads(stdout.getvalue())['exit_code'], 2)

    def test_clean_committed_pr_becomes_only_changed_source_in_scratch(self):
        from git_env import fixture_env
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); repo = root / "repo"; repo.mkdir()
            env = fixture_env()
            def git(*args):
                return subprocess.check_output(["git", "-C", str(repo), *args], env=env).decode().strip()
            git("init", "-q")
            (repo / "unchanged.py").write_text("def untouched(): return 1\n")
            git("add", "unchanged.py")
            git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-qm", "base")
            base = git("rev-parse", "HEAD")
            (repo / "changed.py").write_text("def added(): return 2\n")
            (repo / "deleted.py").write_text("def dirty_untracked(): return 3\n")
            git("add", "changed.py")
            git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-qm", "head")
            config = root / "config.json"; config.write_text('{}')
            scratch = root / "scratch"; scratch.mkdir()
            with patch.dict(os.environ, {**env, "GIT_DIR": str(repo / ".git"),
                                        "GIT_INDEX_FILE": str(repo / ".git/index")}, clear=True):
                selected = review.materialize(repo, base, "HEAD", scratch, config)
            self.assertEqual(selected, ["changed.py"])
            self.assertEqual((scratch / "changed.py").read_text(), "def added(): return 2\n")
            self.assertFalse((scratch / "unchanged.py").exists())
            self.assertFalse((scratch / "deleted.py").exists())
            self.assertTrue((scratch / ".git").is_dir())
            self.assertEqual(git("status", "--porcelain"), "?? deleted.py")
            peer = root / "peer"
            git("worktree", "add", "--detach", "-q", str(peer), "HEAD")
            with patch.dict(os.environ, env, clear=True):
                self.assertEqual(review.workspace_parent(repo), review.workspace_parent(peer))


class EvidenceTests(unittest.TestCase):
    def test_one_canonical_forwarding_predicate(self):
        config = json.loads((review.ROOT / 'jevlint.json').read_text())
        self.assertEqual([r['id'] for r in config['rules']], ['wrapper-without-value'])
        cases = json.loads((review.ROOT / 'jevlint-evals.json').read_text())['cases']
        self.assertTrue(all(c['rule'] == 'wrapper-without-value' for c in cases))
    def test_shared_budget_obeys_brief(self):
        cap = json.loads((review.ROOT / "ops/config/jev-cost-guard.v1.json").read_text())["daily_paid_call_cap"]
        self.assertEqual(cap, 500)
        self.assertLessEqual(cap, 1000)

    def test_real_fixture_sources_are_unchanged(self):
        folder = review.ROOT / "ops/fixtures/jevlint"
        provenance = json.loads((folder / "provenance.json").read_text())
        for source in provenance["sources"]:
            with self.subTest(file=source["file"]):
                self.assertEqual(hashlib.sha256((folder / source["file"]).read_bytes()).hexdigest(), source["sha256"])

    def test_calibration_and_measurements_bind_active_configuration(self):
        folder = review.ROOT / "ops/fixtures/jevlint"
        digest = hashlib.sha256((review.ROOT / "jevlint.json").read_bytes()).hexdigest()
        calibration = json.loads((folder / "calibration.json").read_text())
        measurements = json.loads((folder / "pr-measurements.json").read_text())
        self.assertEqual(calibration["active_configuration"]["config_sha256"], digest)
        # Historical paid runs used the original two-rule configuration; never
        # relabel their output or count source as a newly measured run.
        self.assertEqual(measurements["config_sha256"], calibration["config_sha256"])
        self.assertTrue(measurements["limitations"])
        self.assertEqual(calibration["upstream"], review.PIN)
        final = calibration["active_configuration"]["report"]
        cases = json.loads((review.ROOT / "jevlint-evals.json").read_text())["cases"]
        self.assertEqual(final["total"], len(cases))
        self.assertEqual(final["matched"], final["total"])
        self.assertEqual(final["inconclusive"], 0)
        historical = next(r["report"] for r in calibration["rounds"] if r["round"] == "calibrated")
        observed = [historical["cases"][i] for i in calibration["active_configuration"]["case_indices"]]
        self.assertEqual(len(observed), final["total"])
        self.assertTrue(all(c["matched"] and c["rule"] == 'wrapper-without-value' for c in observed))
        self.assertEqual(len(measurements["samples"]), 3)
        for sample in measurements["samples"]:
            self.assertEqual(sample["shim"]["paid_attempts"], sample["shim"]["answered"])
            self.assertEqual(sample["shim"]["refused"], 0)
            self.assertEqual(sample["shim"]["errors"], 0)


if __name__ == "__main__":
    unittest.main()
