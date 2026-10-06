#!/usr/bin/env python3
"""The continuity spool delivers, drains in order, and archives what can never apply.

The fake store below enforces the Worker envelope's real contract: one
idempotency key binds one request body, and a different body under a used key
is refused with key_reuse.  That refusal is what filled the live spool.
"""
from __future__ import annotations

import hashlib
import hmac
import importlib.util
import ast
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor
import threading
import shutil
from datetime import datetime, timedelta, timezone

ROOT = pathlib.Path(__file__).resolve().parents[1]
HOOK = ROOT / "ops/claude-continuity-hook.py"
sys.path.insert(0, str(ROOT))
from lib import claude_continuity_spool as spool  # noqa: E402

FAKE_STORE = r"""#!/usr/bin/env python3
import hashlib, json, os, sys
name, args = sys.argv[1], json.loads(sys.argv[2])
with open(os.environ['CALL_LOG'], 'a', encoding='utf-8') as out:
    out.write(json.dumps({'name': name, 'args': args}) + '\n')
mode = os.environ.get('SERVER_MODE', 'ok')
if mode == 'down':
    sys.stderr.write('could not reach the deployed Worker\n')
    sys.exit(1)
if mode == 'refuse':
    sys.stderr.write('local-verb identity -> test\nTOOL ERROR ' + json.dumps(
        {'error': os.environ['SERVER_REFUSAL'], 'cause': os.environ.get('SERVER_CAUSE', '')}, indent=2) + '\n')
    sys.exit(1)
if name == 'claude-read-recovery':
    print(json.dumps({'ok': True, 'found': False, 'checkpoint': None, 'capsule': None}))
    sys.exit(0)
body = {k: v for k, v in args.items() if k != 'idempotency_key'}
digest = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()
store = os.environ['SERVER_STORE']
try:
    db = json.load(open(store))
except FileNotFoundError:
    db = {}
key = args['idempotency_key']
if key in db and db[key] != digest:
    sys.stderr.write('local-verb identity -> test\nTOOL ERROR ' + json.dumps({'error': 'key_reuse'}, indent=2) + '\n')
    sys.exit(1)
replayed = key in db
db[key] = digest
json.dump(db, open(store, 'w'))
print(json.dumps({'ok': True, 'replayed': replayed} if replayed else {'ok': True}, indent=2))
"""


def load_hook():
    spec = importlib.util.spec_from_file_location("claude_continuity_hook_spool", HOOK)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class SpoolTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="carr-claude-spool-")
        self.root = pathlib.Path(self.temp.name)
        self.transcript = self.root / "session-1.jsonl"
        self.transcript.write_text('{"type":"user"}\n', encoding="utf-8")
        self.spool = self.root / "spool"
        self.archive = self.root / "spool-archive.jsonl"
        self.calls = self.root / "calls.jsonl"
        self.store = self.root / "store.json"
        caller = self.root / "store.py"
        caller.write_text(FAKE_STORE, encoding="utf-8")
        caller.chmod(0o755)
        mode = self.root / "mode.json"
        self.env = {
            "CARR_CLAUDE_CONTINUITY_MODE_FILE": str(mode),
            "CARR_CLAUDE_CONTINUITY_SPOOL_DIR": str(self.spool),
            "CARR_CLAUDE_CONTINUITY_SPOOL_ARCHIVE": str(self.archive),
            "CARR_CLAUDE_CONTINUITY_SESSION_DIR": str(self.root / "session-state"),
            "CARR_CLAUDE_CONTINUITY_CALL": str(caller),
            "CARR_CLAUDE_TRANSCRIPT_ROOTS": str(self.root),
            "CARR_CLAUDE_CONTINUITY_AUDIT": str(self.root / "audit.jsonl"),
            "CARR_CLAUDE_RULE_DEDUPE_DIR": str(self.root / "dedupe"),
            "CARR_CLAUDE_RULE_DEDUPE_AUDIT": str(self.root / "dedupe-audit.jsonl"),
            "CALL_LOG": str(self.calls),
            "SERVER_STORE": str(self.store),
        }
        self.saved_env = {key: os.environ.get(key) for key in self.env}
        os.environ.update(self.env)
        mode.write_text(json.dumps({"schema_version": 1, "mode": "checkpoint",
                                    "config_digest": load_hook().expected_config_digest()}))

    def tearDown(self):
        for key, value in self.saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self.temp.cleanup()

    def run_hook(self, event, **extra):
        payload = {"hook_event_name": event, "session_id": "session-1",
                   "transcript_path": str(self.transcript), "cwd": str(self.root), **extra}
        return subprocess.run([sys.executable, str(HOOK)], input=json.dumps(payload), text=True,
                              capture_output=True, env=dict(os.environ), timeout=20, check=False)

    def calls_made(self, name="claude-record-event"):
        if not self.calls.exists():
            return []
        rows = [json.loads(line) for line in self.calls.read_text().splitlines()]
        return [row["args"] for row in rows if row["name"] == name]

    def spooled(self):
        return sorted(self.spool.glob("*.json"))

    def archived(self):
        if not self.archive.exists():
            return []
        return [json.loads(line) for line in self.archive.read_text().splitlines()]

    def outage(self, *events):
        os.environ["SERVER_MODE"] = "down"
        try:
            for event in events:
                self.run_hook(event)
        finally:
            os.environ.pop("SERVER_MODE")


class RefusalTest(SpoolTest):
    def test_repeat_prompts_at_one_transcript_offset_are_all_accepted(self):
        # The live failure: blocked or queued prompts fire UserPromptSubmit
        # again before the transcript grows.  Every repeat used to reuse the
        # first one's key with a new observed_at and was refused as key_reuse.
        for _ in range(3):
            self.assertEqual(self.run_hook("UserPromptSubmit").returncode, 0)
        self.assertEqual(self.spooled(), [], "no receipt may be refused into the spool")
        keys = [args["idempotency_key"] for args in self.calls_made()]
        self.assertEqual(len(set(keys)), 3, "each firing is its own receipt")

    def test_a_byte_identical_resend_replays_under_the_same_key(self):
        hook = load_hook()
        args = {"runtime": "claude", "event_type": "stop", "observed_at": "2026-10-05T00:00:00+00:00"}
        self.assertEqual(hook.receipt_key(args), hook.receipt_key(dict(args)))
        self.assertNotEqual(hook.receipt_key(args), hook.receipt_key({**args, "observed_at": "later"}))

    def test_a_server_refusal_is_archived_with_its_reason_not_spooled(self):
        os.environ.update(SERVER_MODE="refuse", SERVER_REFUSAL="claude_continuity_binding_conflict")
        try:
            self.run_hook("Stop")
        finally:
            os.environ.pop("SERVER_MODE")
        self.assertEqual(self.spooled(), [], "a refused write can never apply; it does not wait in the spool")
        [row] = self.archived()
        self.assertEqual(row["outcome"], "refused")
        self.assertEqual(row["reason"], "claude_continuity_binding_conflict")
        self.assertEqual(row["receipt"]["args"]["event_type"], "stop")

    def test_an_unreachable_store_spools_with_the_failure_kind(self):
        self.outage("Stop")
        [path] = self.spooled()
        body = json.loads(path.read_text())
        self.assertEqual(body["failure"], {"kind": "transport", "exit": 1})

    def test_the_cap_archives_the_oldest_receipt_instead_of_deleting_it(self):
        directory = self.spool
        for index in range(3):
            spool.spool_receipt("claude-record-event", {"idempotency_key": f"k{index}"},
                                {"kind": "timeout"}, max_files=2)
            time.sleep(0.01)
        self.assertEqual(len(self.spooled()), 2)
        [row] = self.archived()
        self.assertEqual((row["outcome"], row["reason"]), ("overflow", "spool_cap_reached"))
        self.assertEqual(row["receipt"]["args"]["idempotency_key"], "k0")
        self.assertTrue(directory.is_dir())


class DrainTest(SpoolTest):
    def test_drain_delivers_in_order_with_original_keys(self):
        self.outage("UserPromptSubmit", "PreCompact", "Stop")
        original = [json.loads(path.read_text())["args"]["idempotency_key"] for path in self.spooled()]
        self.calls.unlink()

        result = spool.drain()

        self.assertEqual(result["delivered"], 3)
        self.assertEqual([args["idempotency_key"] for args in self.calls_made()], original)
        self.assertEqual(self.spooled(), [])
        self.assertEqual([row["outcome"] for row in self.archived()], ["delivered"] * 3)

    def test_a_receipt_the_store_already_holds_counts_as_delivered(self):
        self.outage("Stop")
        args = json.loads(self.spooled()[0].read_text())["args"]
        subprocess.run([os.environ["CARR_CLAUDE_CONTINUITY_CALL"], "claude-record-event", json.dumps(args)],
                       check=True, capture_output=True)
        result = spool.drain()
        self.assertEqual(result["already_recorded"], 1)
        self.assertEqual(self.archived()[0]["outcome"], "already_recorded")

    def test_a_receipt_that_can_no_longer_apply_is_archived_with_the_reason(self):
        self.outage("UserPromptSubmit", "Stop")
        first = self.spooled()[0]
        args = json.loads(first.read_text())["args"]
        # The store already holds a different body under this key: the pre-fix
        # duplicate-offset case, or a stale expected_version on a checkpoint.
        clash = {**args, "observed_at": "2000-01-01T00:00:00+00:00"}
        subprocess.run([os.environ["CARR_CLAUDE_CONTINUITY_CALL"], "claude-record-event", json.dumps(clash)],
                       check=True, capture_output=True)

        result = spool.drain()

        self.assertEqual((result["archived"], result["delivered"]), (1, 1))
        self.assertEqual(self.spooled(), [])
        rows = self.archived()
        self.assertEqual((rows[0]["outcome"], rows[0]["reason"]), ("refused", "key_reuse"))
        self.assertEqual(rows[0]["receipt"]["args"], args, "archived intact, never rewritten")
        self.calls.unlink()
        spool.drain()
        self.assertEqual(self.calls_made(), [], "an archived receipt is never retried")

    def test_an_unreachable_store_stops_the_drain_and_retries_are_bounded(self):
        self.outage("UserPromptSubmit", "Stop")
        os.environ["SERVER_MODE"] = "down"
        try:
            for attempt in range(1, spool.MAX_DRAIN_ATTEMPTS):
                result = spool.drain()
                self.assertEqual(result["transient"], 1, "the first failure stops the drain in order")
                self.assertEqual(len(self.spooled()), 2)
            spool.drain()
        finally:
            os.environ.pop("SERVER_MODE")
        rows = self.archived()
        self.assertEqual(rows[0]["outcome"], "retry_budget_exhausted")
        self.assertEqual(rows[0]["reason"], "transport")
        self.assertEqual(len(self.spooled()), 1, "later receipts keep their own budget")

    def test_drain_never_replays_anything_but_a_lifecycle_receipt(self):
        key = spool.spool_key()
        body = {"schema_version": 1, "verb": "claude-checkpoint", "spooled_at": "2026-10-05T00:00:00+00:00",
                "args": {"idempotency_key": "x", "state": {"pending_external_effects": [
                    {"text": "send the LOI", "refs": ["r"]}]}}}
        body["hmac_sha256"] = hmac.new(key, spool.canonical(body), hashlib.sha256).hexdigest()
        self.spool.mkdir(parents=True, exist_ok=True)
        (self.spool / f"{time.time_ns()}-aaaa.json").write_bytes(spool.canonical(body))
        forged = {"schema_version": 1, "verb": "claude-record-event", "args": {"idempotency_key": "y"},
                  "spooled_at": "2026-10-05T00:00:00+00:00", "hmac_sha256": "0" * 64}
        (self.spool / f"{time.time_ns()}-bbbb.json").write_bytes(spool.canonical(forged))

        spool.drain()

        self.assertEqual(self.calls_made("claude-checkpoint") + self.calls_made(), [])
        self.assertEqual([(row["outcome"], row["reason"]) for row in self.archived()],
                         [("not_replayable", "claude-checkpoint"), ("not_replayable", "signature_invalid")])


class HealthRowTest(SpoolTest):
    def setUp(self):
        super().setUp()
        self.loop_state = self.root / "loop.json"
        self.verbs = []

    def run_verb(self, name, payload):
        self.verbs.append((name, payload))
        if name == "add-loop":
            return {"ok": True, "loop_id": "loop-1"}
        if name == "read-loop":
            return {"loop": {"loop_id": "loop-1", "version": 2}}
        return {"ok": True}

    def row(self, now=None):
        return spool.health_row(run_verb=self.run_verb, state_path=self.loop_state,
                                now=now or datetime.now(timezone.utc)).line

    def test_row_names_size_age_and_its_bound_action(self):
        line = self.row()
        self.assertTrue(line.startswith("OK claude continuity spool — 0 unsent"), line)
        self.assertIn("on breach:", line)
        self.assertEqual(self.verbs, [])

    def test_growth_files_one_deduplicated_loop_and_clears_it(self):
        self.outage("Stop")
        later = datetime.now(timezone.utc) + timedelta(hours=spool.WARN_AGE_HOURS + 1)
        first = self.row(later)
        second = self.row(later)
        self.assertTrue(first.startswith("WARN claude continuity spool — 1 unsent"), first)
        self.assertIn("on breach:", first)
        self.assertEqual([name for name, _ in self.verbs], ["add-loop"], second)
        payload = self.verbs[0][1]
        self.assertEqual((payload["kind"], payload["blocker"]), ("open_loop", "capability"))
        for path in self.spooled():
            os.replace(path, self.root / path.name)
        self.row(later)
        self.assertEqual([name for name, _ in self.verbs], ["add-loop", "read-loop", "close-loop"])

    def test_the_nightly_section_drains_then_reports(self):
        self.outage("UserPromptSubmit", "Stop")
        result = subprocess.run([sys.executable, str(ROOT / "tools/health-check.py"),
                                 "--section", "claude-continuity-spool"],
                                cwd=ROOT, env=dict(os.environ), capture_output=True, text=True,
                                timeout=60, check=False)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        line = result.stdout.strip().splitlines()[-1]
        self.assertTrue(line.startswith("OK claude continuity spool — 0 unsent"), line)
        self.assertIn("this run delivered 2, archived 0", line)
        self.assertIn("on breach:", line)
        self.assertEqual(self.spooled(), [])


class BlockingFindingsTest(HealthRowTest):
    def test_producer_tolerates_a_disappearing_observation(self):
        self.outage("Stop")
        old = self.spooled()[0]
        original = spool.spooled
        def disappearing():
            files = original()
            old.unlink(missing_ok=True)
            return files
        with patch.object(spool, "spooled", disappearing):
            spool.spool_receipt("claude-record-event", {"idempotency_key": "new"}, {"kind": "timeout"})
        self.assertEqual(len(self.spooled()), 1)

    def test_unknown_server_failures_remain_retryable_and_redacted(self):
        secret = "fixture-service-secret-value"
        with patch.dict(os.environ, SERVER_MODE="refuse", SERVER_REFUSAL="unhandled_verb_failure",
                        SERVER_CAUSE="temporary connection loss " + secret, CARR_TEST_TOKEN=secret):
            self.run_hook("Stop")
        self.assertEqual(len(self.spooled()), 1)
        saved = self.spooled()[0].read_text()
        self.assertIn("temporary connection loss", saved)
        self.assertNotIn(secret, saved)
        self.assertEqual(self.archived(), [])
        spool.drain()
        self.assertEqual(len(self.calls_made()), 2)

    def test_archived_failure_keeps_incident_open(self):
        self.outage("Stop")
        later = datetime.now(timezone.utc) + timedelta(hours=40)
        self.row(later)
        with patch.dict(os.environ, SERVER_MODE="refuse", SERVER_REFUSAL="key_reuse"):
            spool.drain()
        line = self.row(later)
        self.assertTrue(line.startswith("WARN"), line)
        self.assertNotIn("close-loop", [name for name, _ in self.verbs])

    def test_a_repairable_principal_refusal_remains_retryable(self):
        with patch.dict(os.environ, SERVER_MODE="refuse", SERVER_REFUSAL="claude_native_principal_required"):
            result = spool.invoke("claude-record-event", {"idempotency_key": "retry"})
        self.assertIsInstance(result, spool.Failed)
        self.assertEqual(result.failure["kind"], "server")

    def test_immediate_refusal_is_unresolved_health_failure(self):
        spool.archive({"args": {"idempotency_key": "refused"}}, "refused", "key_reuse")
        self.assertTrue(self.row().startswith("WARN"))

    def test_recurring_breach_uses_a_new_incident_key(self):
        self.outage("Stop")
        later = datetime.now(timezone.utc) + timedelta(hours=40)
        self.row(later)
        spool.drain()
        self.row(later)
        self.outage("Stop", "UserPromptSubmit")
        self.row(later)
        adds = [payload for name, payload in self.verbs if name == "add-loop"]
        self.assertEqual(len(adds), 2)
        self.assertNotEqual(adds[0]["idempotency_key"], adds[1]["idempotency_key"])

    def test_uncertain_add_retries_immutable_body(self):
        self.outage("Stop")
        later = datetime.now(timezone.utc) + timedelta(hours=40)
        requests = []
        def uncertain(name, payload):
            requests.append((name, dict(payload)))
            raise RuntimeError("timeout after apply")
        for when in (later, later + timedelta(hours=1)):
            spool.health_row(run_verb=uncertain, state_path=self.loop_state, now=when)
        self.assertEqual(requests[0], requests[1])

    def test_concurrent_health_opens_one_incident(self):
        self.outage("Stop")
        later = datetime.now(timezone.utc) + timedelta(hours=40)
        started = threading.Event()
        release = threading.Event()
        def delayed(name, payload):
            if name == "add-loop":
                started.set()
                release.wait(3)
            return self.run_verb(name, payload)
        with ThreadPoolExecutor(max_workers=2) as executor:
            first = executor.submit(spool.health_row, run_verb=delayed, state_path=self.loop_state, now=later)
            self.assertTrue(started.wait(3))
            second = executor.submit(spool.health_row, run_verb=delayed, state_path=self.loop_state, now=later)
            time.sleep(.05)
            release.set()
            first.result()
            second.result()
        self.assertEqual([name for name, _ in self.verbs], ["add-loop"])

    def test_failed_incident_clear_has_structured_failure_status(self):
        self.loop_state.write_text(json.dumps({"loop_id": "loop-1"}))
        def failing(*_):
            raise RuntimeError("down")
        result = spool.health_row(run_verb=failing, state_path=self.loop_state,
                                  now=datetime.now(timezone.utc))
        self.assertEqual(result.status, 1)

    def test_failed_clear_reaches_the_canonical_health_exit_status(self):
        self.loop_state.write_text(json.dumps({"loop_id": "loop-1"}))
        tree = ast.parse((ROOT / "tools/health-check.py").read_text())
        function = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                        and node.name == "_claude_continuity_spool_row")
        scope = {"sys": sys, "REPO_ROOT": str(ROOT)}
        exec(compile(ast.Module(body=[function], type_ignores=[]), "health-check.py", "exec"), scope)
        with patch.object(spool, "loop_state_path", return_value=self.loop_state), \
                patch.object(spool, "run_verb", side_effect=RuntimeError("down")):
            line, status = scope["_claude_continuity_spool_row"](False)
        self.assertIn("loop action FAILED", line)
        self.assertEqual(status, 1)

    def test_a_different_body_under_the_same_key_does_not_resolve_a_failure(self):
        original = {"verb": "claude-record-event", "args": {"idempotency_key": "collision", "event_type": "stop"}}
        other = {"verb": "claude-record-event", "args": {"idempotency_key": "collision", "event_type": "start"}}
        spool.archive(original, "refused", "key_reuse")
        spool.archive(other, "already_recorded", "store_confirmed")
        self.assertTrue(self.row().startswith("WARN"))
        spool.archive(original, "already_recorded", "store_confirmed")
        self.assertTrue(self.row().startswith("OK"))

    def test_slow_delivery_does_not_block_a_producer_persisting_its_receipt(self):
        self.outage("Stop")
        original = json.loads(self.spooled()[0].read_bytes())
        entered = threading.Event()
        release = threading.Event()
        def delayed(*_):
            entered.set()
            release.wait(3)
            return spool.Delivered({"ok": True})
        with ThreadPoolExecutor(max_workers=2) as executor:
            draining = executor.submit(spool.drain, call=delayed)
            self.assertTrue(entered.wait(3))
            producing = executor.submit(spool.spool_receipt, "claude-record-event",
                                        {"idempotency_key": "new"}, {"kind": "timeout"}, max_files=1)
            try:
                producing.result(timeout=1)
            finally:
                release.set()
            self.assertEqual(draining.result()["delivered"], 1)
            producing.result()
        self.assertEqual(len(self.spooled()), 1)
        self.assertEqual(json.loads(self.spooled()[0].read_bytes())["args"]["idempotency_key"], "new")
        self.assertEqual([row["outcome"] for row in self.archived()], ["overflow", "delivered"])
        self.assertTrue(all(row["receipt"] == original for row in self.archived()))
        self.assertTrue(self.row().startswith("OK"))

    def test_short_archive_writes_preserve_complete_receipt(self):
        self.outage("Stop")
        path = self.spooled()[0]
        before = json.loads(path.read_bytes())
        write = os.write
        with patch.object(os, "write", lambda fd, data: write(fd, data[:10])):
            spool._move_to_archive(path, "delivered", "store_confirmed")
        self.assertEqual(self.archived()[0]["receipt"], before)
        self.assertFalse(path.exists())

    def test_failed_archive_sync_retains_source(self):
        self.outage("Stop")
        path = self.spooled()[0]
        with patch.object(os, "fsync", side_effect=OSError("disk failure")):
            with self.assertRaises(OSError):
                spool._move_to_archive(path, "delivered", "store_confirmed")
        self.assertTrue(path.exists())

    def test_malformed_receipt_bytes_are_preserved(self):
        self.spool.mkdir()
        raw = b'{"truncated":\xff'
        path = self.spool / f"{time.time_ns()}-bad.json"
        path.write_bytes(raw)
        spool.drain()
        copies = [p for p in self.root.rglob("*") if p.is_file() and p.read_bytes() == raw]
        self.assertTrue(copies, "original source bytes must survive quarantine")

    def test_non_ascii_signature_does_not_poison_later_receipts(self):
        self.outage("Stop", "UserPromptSubmit")
        first = self.spooled()[0]
        body = json.loads(first.read_bytes())
        body["hmac_sha256"] = "é" * 64
        first.write_bytes(spool.canonical(body))
        result = spool.drain()
        self.assertEqual(result["delivered"], 1)
        self.assertEqual(self.archived()[0]["reason"], "signature_invalid")

    def test_replay_respects_disabled_shadow_and_stale_pin(self):
        self.outage("Stop")
        mode_path = pathlib.Path(os.environ["CARR_CLAUDE_CONTINUITY_MODE_FILE"])
        for mode, digest in (("disabled", load_hook().expected_config_digest()),
                             ("shadow", load_hook().expected_config_digest()), ("inject", "stale")):
            with self.subTest(mode=mode):
                mode_path.write_text(json.dumps({"schema_version": 1, "mode": mode, "config_digest": digest}))
                self.calls.unlink(missing_ok=True)
                spool.drain()
                self.assertEqual(self.calls_made(), [])
                self.assertEqual(len(self.spooled()), 1)

    def test_runtime_library_changes_invalidate_installation_pin(self):
        from lib import claude_continuity_config as config
        repo = self.root / "copy"
        for name in ("ops/config/claude-continuity-hooks.json", "ops/claude-continuity-hook.py",
                     "mcp-server/continuity-stdio-proxy.mjs", "lib/claude_continuity_spool.py",
                     "lib/health_incident.py"):
            target = repo / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(ROOT / name, target)
        before = config.load(repo)
        source = repo / "lib/claude_continuity_spool.py"
        source.write_bytes(source.read_bytes() + b"\n# changed runtime\n")
        self.assertNotEqual(before.config_digest, config.load(repo).config_digest)

    def test_incident_primitives_are_shared_with_spend_health(self):
        sys.path.insert(0, str(ROOT / "ops"))
        import jev_spend_health as spend
        self.assertIs(spool._save_state, spend._save_state)
        self.assertIs(spool._loop_lock, spend._loop_lock)
        self.assertIs(spool.confirmed_reply, spend.confirmed_reply)
        sys.path.insert(0, str(ROOT / "tools"))
        import system_costs as costs
        self.assertIs(spool._loop_record, costs.loop_record)


if __name__ == "__main__":
    unittest.main(verbosity=2)
