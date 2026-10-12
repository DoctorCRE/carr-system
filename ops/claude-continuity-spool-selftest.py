#!/usr/bin/env python3
"""The continuity spool delivers, drains in order, and archives what can never apply.

The fake store below enforces the Worker's two real contracts.  The envelope:
one idempotency key binds one request body, and a different body under a used
key is refused with key_reuse (that refusal is what filled the live spool).
The principal: claude-record-event is refused with
claude_native_principal_required unless the caller selected the
claude-continuity client profile, which is how the hook and the unattended
drain both reach the Worker as the Claude continuity actor rather than the
local machine actor.
"""
from __future__ import annotations

import hashlib
import hmac
import importlib.util
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import time
import unittest
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
if os.environ.get('CARR_MCP_CLIENT_PROFILE') != 'claude-continuity':
    sys.stderr.write('local-verb identity -> local machine actor\nTOOL ERROR ' + json.dumps(
        {'error': 'claude_native_principal_required'}, indent=2) + '\n')
    sys.exit(1)
if mode == 'down':
    sys.stderr.write('could not reach the deployed Worker\n')
    sys.exit(1)
if mode == 'refuse':
    sys.stderr.write('local-verb identity -> test\nTOOL ERROR ' + json.dumps(
        {'error': os.environ['SERVER_REFUSAL']}, indent=2) + '\n')
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

    def as_continuity_actor(self):
        return {**os.environ, "CARR_MCP_CLIENT_PROFILE": "claude-continuity"}

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

    def test_a_transient_server_fault_is_spooled_not_archived(self):
        # unhandled_verb_failure is what the Worker returns for any untyped
        # throw: a Neon connection drop, a statement timeout, a serialization
        # failure.  The write rolled back and is retryable, so it waits.
        for code in ("unhandled_verb_failure", "unparseable_tool_error", "a_code_nobody_has_seen"):
            with self.subTest(code=code):
                os.environ.update(SERVER_MODE="refuse", SERVER_REFUSAL=code)
                try:
                    self.run_hook("Stop")
                finally:
                    os.environ.pop("SERVER_MODE")
                self.assertEqual(self.archived(), [], f"{code} is delivery-unknown, never a refusal")
                [path] = self.spooled()
                body = json.loads(path.read_text())
                self.assertEqual(body["failure"], {"kind": "unknown", "error": code})
                path.unlink()

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
                       check=True, capture_output=True, env=self.as_continuity_actor())
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
                       check=True, capture_output=True, env=self.as_continuity_actor())

        result = spool.drain()

        self.assertEqual((result["archived"], result["delivered"]), (1, 1))
        self.assertEqual(self.spooled(), [])
        rows = self.archived()
        self.assertEqual((rows[0]["outcome"], rows[0]["reason"]), ("refused", "key_reuse"))
        self.assertEqual(rows[0]["receipt"]["args"], args, "archived intact, never rewritten")
        self.calls.unlink()
        spool.drain()
        self.assertEqual(self.calls_made(), [], "an archived receipt is never retried")

    def test_a_transient_server_fault_stops_the_drain_and_keeps_the_receipt(self):
        self.outage("UserPromptSubmit", "Stop")
        os.environ.update(SERVER_MODE="refuse", SERVER_REFUSAL="unhandled_verb_failure")
        try:
            result = spool.drain()
        finally:
            os.environ.pop("SERVER_MODE")
        self.assertEqual((result["transient"], result["archived"], result["delivered"]), (1, 0, 0))
        self.assertEqual(len(self.spooled()), 2, "a store fault during the drain empties nothing")
        self.assertEqual(self.archived(), [], "nothing is written off as refused")
        # Once the store answers again the same receipts deliver under their keys.
        result = spool.drain()
        self.assertEqual((result["delivered"], len(self.spooled())), (2, 0))

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

    def test_a_partial_drain_with_a_fake_door_delivers_archives_and_keeps_the_rest(self):
        self.outage("UserPromptSubmit", "PreCompact", "Stop", "UserPromptSubmit")
        paths = self.spooled()
        self.assertEqual(len(paths), 4)
        answers = iter([({"ok": True}, None),
                        (None, {"kind": "refused", "error": "claude_continuity_binding_conflict"}),
                        (None, {"kind": "timeout"})])
        sent = []

        def door(verb, args):
            sent.append((verb, args["idempotency_key"]))
            return next(answers)

        result = spool.drain(call=door)

        self.assertEqual((result["delivered"], result["archived"], result["transient"]), (1, 1, 1))
        self.assertEqual(len(sent), 3, "the first unknown outcome stops the drain")
        self.assertEqual({verb for verb, _ in sent}, {"claude-record-event"})
        self.assertEqual(self.spooled(), paths[2:], "the timed-out receipt and everything after it wait")
        self.assertEqual([(row["outcome"], row["reason"]) for row in self.archived()],
                         [("delivered", "store_confirmed"),
                          ("refused", "claude_continuity_binding_conflict")])

    def test_drain_reaches_the_store_as_the_claude_continuity_principal(self):
        self.outage("Stop")
        # An ambient profile (an orchestrator shell, a launchd job) must not
        # decide which actor the drain authenticates as.
        os.environ["CARR_MCP_CLIENT_PROFILE"] = "local"
        try:
            result = spool.drain()
        finally:
            os.environ.pop("CARR_MCP_CLIENT_PROFILE")
        self.assertEqual(result["delivered"], 1)
        self.assertEqual(self.archived()[0]["outcome"], "delivered")

    def test_the_hook_archives_a_principal_refusal_instead_of_spooling_it(self):
        os.environ["CARR_CLAUDE_CONTINUITY_CALL"] = "env -u CARR_MCP_CLIENT_PROFILE " + self.env["CARR_CLAUDE_CONTINUITY_CALL"]
        try:
            self.run_hook("Stop")
        finally:
            os.environ["CARR_CLAUDE_CONTINUITY_CALL"] = self.env["CARR_CLAUDE_CONTINUITY_CALL"]
        self.assertEqual(self.spooled(), [])
        [row] = self.archived()
        self.assertEqual((row["outcome"], row["reason"]), ("refused", "claude_native_principal_required"))

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
                                now=now or datetime.now(timezone.utc))

    def test_row_names_size_age_and_its_bound_action(self):
        line = self.row()
        self.assertTrue(line.startswith("OK continuity spool depth — 0 unsent"), line)
        self.assertIn("on breach:", line)
        self.assertEqual(self.verbs, [])

    def test_growth_files_one_deduplicated_loop_and_clears_it(self):
        self.outage("Stop")
        later = datetime.now(timezone.utc) + timedelta(hours=spool.WARN_AGE_HOURS + 1)
        first = self.row(later)
        second = self.row(later)
        self.assertTrue(first.startswith("WARN continuity spool depth — 1 unsent"), first)
        self.assertIn("on breach:", first)
        self.assertEqual([name for name, _ in self.verbs], ["add-loop"], second)
        payload = self.verbs[0][1]
        # Internal repair, owner claude: other_lane. A capability loop must name
        # Joe or Dell (needsDecider) and a residue (add-loop RESIDUE_CLASSES).
        self.assertEqual((payload["kind"], payload["blocker"]), ("open_loop", "other_lane"))
        for path in self.spooled():
            os.replace(path, self.root / path.name)
        self.row(later)
        self.assertEqual([name for name, _ in self.verbs], ["add-loop", "read-loop", "close-loop"])

    def fill(self, count):
        for index in range(count):
            spool.spool_receipt("claude-record-event", {"idempotency_key": f"k{index}"}, {"kind": "timeout"})

    def test_row_goes_red_at_the_depth_threshold_and_green_at_zero(self):
        self.fill(spool.WARN_COUNT - 1)
        below = self.row()
        self.assertTrue(below.startswith(f"OK continuity spool depth — {spool.WARN_COUNT - 1} unsent"), below)
        self.assertEqual(self.verbs, [], "a few in-flight receipts open no loop")
        self.fill(1)
        at = self.row()
        self.assertTrue(at.startswith(f"WARN continuity spool depth — {spool.WARN_COUNT} unsent"), at)
        self.assertIn("owner orchestrator", at)
        self.assertIn("remediation run the drain", at)
        self.assertIn("verify depth falls", at)
        self.assertIn("auto-clear at 0", at)
        self.assertEqual([name for name, _ in self.verbs], ["add-loop"])
        # Half-drained: still red, and the loop stays open.
        for path in self.spooled()[:-1]:
            path.unlink()
        half = self.row()
        self.assertTrue(half.startswith("WARN continuity spool depth — 1 unsent"), half)
        self.assertEqual([name for name, _ in self.verbs], ["add-loop"])
        for path in self.spooled():
            path.unlink()
        zero = self.row()
        self.assertTrue(zero.startswith("OK continuity spool depth — 0 unsent"), zero)
        self.assertEqual([name for name, _ in self.verbs], ["add-loop", "read-loop", "close-loop"])
        self.assertEqual(json.loads(self.loop_state.read_text()), {})

    def test_a_failed_loop_write_is_printed_not_hidden(self):
        self.fill(spool.WARN_COUNT)

        def broken(name, payload):
            raise RuntimeError("add-loop returned 1")

        line = spool.health_row(run_verb=broken, state_path=self.loop_state, now=datetime.now(timezone.utc))
        self.assertTrue(line.startswith("WARN continuity spool depth"), line)
        self.assertIn("loop action FAILED (RuntimeError)", line)
        self.assertFalse(self.loop_state.exists())

    def test_the_nightly_section_drains_then_reports(self):
        self.outage("UserPromptSubmit", "Stop")
        result = subprocess.run([sys.executable, str(ROOT / "tools/health-check.py"),
                                 "--section", "claude-continuity-spool"],
                                cwd=ROOT, env=dict(os.environ), capture_output=True, text=True,
                                timeout=60, check=False)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        line = result.stdout.strip().splitlines()[-1]
        self.assertTrue(line.startswith("OK continuity spool depth — 0 unsent"), line)
        self.assertIn("this run delivered 2, archived 0", line)
        self.assertIn("on breach:", line)
        self.assertEqual(self.spooled(), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
