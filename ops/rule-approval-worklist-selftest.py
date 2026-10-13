#!/usr/bin/env python3
"""ops/rule-approval-worklist-selftest.py — the rule-approval worklist, hermetic.

Fake database rows (injected `db=`) and a fake verb door (injected `call=`)
stand in for production and `./run.sh call`; nothing here reads a database,
touches the network, or writes outside a temporary directory.

What it proves:
  * every readiness class is reached — ready, held, class_not_approvable,
    catalog_missing, unmapped, gone — and each row carries the reason;
  * the dry run (the default) calls no verb at all;
  * --apply calls ONLY approve-rule, ONLY for ready rows, one at a time, each
    with a fresh UUID idempotency key, policy_kind machine_enforceable, the
    rule's mapped control, and a reason citing decision cbe91287;
  * every result is appended to the log: the receipt id on success, the
    refusal VERBATIM on a refusal, plus one summary line with the counts;
  * --apply runs ops/rule-admission-audit.py --preflight --rule-id for each
    ready row first and calls approve-rule only when it answers `ready`: a
    non-ready status, a preflight that cannot connect (the real runner over a
    stub script that exits 2) and a preflight that raises each refuse the call
    and log `preflight_refused` with the reason;
  * source mutants must fail these expectations: one that approves a GONE
    rule, one that approves a held-control rule, one that drops the
    idempotency key, one that approves a rule the map records as only
    surfaced, one that drops the preflight gate, and one that reads a
    preflight exception as ready;
  * lib/rule_approval_worklist.py stays a library (no shebang, no __main__),
    wired through run.sh, and ops/ci.sh's ops/*-selftest.py glob collects
    this file.
"""
from __future__ import annotations

import io
import json
import re
import sys
import tempfile
import types
import unittest
import uuid
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from lib import rule_approval_worklist as w  # noqa: E402

SOURCE_PATH = ROOT / "lib" / "rule_approval_worklist.py"


def rid(short: str) -> str:
    return f"{short}-0000-4000-8000-000000000000"


# Short ids chosen so the fake map and triage can name them.
READY_A, READY_B = "aaaa0001", "aaaa0002"
HELD = "bbbb0001"
SURFACING = "cccc0001"
NOT_IN_CATALOG = "dddd0001"
UNMAPPED = "eeee0001"
GONE_INSTALLED = "ffff0001"   # GONE, and its control IS installed: only the GONE flag stops it
SURFACED = "abab0001"         # installed deny control, but the map says the RULE is only surfaced

DB = {
    "rules": [{"rule_id": rid(s), "policy_status": "active", "enforcement_class": "machine_enforceable",
               "enforcement_status": "blocked", "control_bound": False}
              for s in (READY_A, READY_B, HELD, SURFACING, NOT_IN_CATALOG, UNMAPPED, GONE_INSTALLED,
                        SURFACED)],
    "catalog": [
        {"control_key": "deny_ok", "installed": True, "verified": True, "enforcement_class": "deny_gate"},
        {"control_key": "stop_ok", "installed": True, "verified": True, "enforcement_class": "stop_gate"},
        {"control_key": "session_boot", "installed": False, "verified": False, "enforcement_class": "stop_gate"},
        {"control_key": "chat_lint", "installed": True, "verified": True, "enforcement_class": "surfacing"},
    ],
    "status_counts": [],
}
RULE_CONTROLS = {
    READY_A: {"control": "deny_ok", "enforcement_class": "deny_gate"},
    READY_B: {"control": "stop_ok", "enforcement_class": "stop_gate"},
    SURFACED: {"control": "deny_ok", "enforcement_class": "surfacing"},
    HELD: {"control": "session_boot"},
    SURFACING: {"control": "chat_lint"},
    NOT_IN_CATALOG: {"control": "never_registered"},
    GONE_INSTALLED: {"control": "deny_ok"},
}
GONE = {GONE_INSTALLED}
EXPECTED = {
    READY_A: "ready", READY_B: "ready", HELD: "held", SURFACING: "class_not_approvable",
    NOT_IN_CATALOG: "catalog_missing", UNMAPPED: "unmapped", GONE_INSTALLED: "gone",
    SURFACED: "rule_only_surfaced",
}
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
REFUSAL = ('TOOL ERROR {\n  "error": "db_error",\n  "message": "active rule '
           + rid(READY_B) + ' lacks its immutable exact control binding"\n}')


class FakeDoor:
    """Approves READY_A, refuses everything else verbatim."""

    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, verb: str, payload: dict) -> dict:
        self.calls.append((verb, json.loads(json.dumps(payload))))
        if payload.get("rule_id") == rid(READY_A):
            return {"ok": True, "result": {"ok": True, "approval_receipt_id": "11111111-1111-4111-8111-111111111111",
                                           "enforcement_status": "hard_enforced", "policy_status": "active"}}
        return {"ok": False, "refusal": REFUSAL}


class FakePreflight:
    """Answers `ready` unless the rule id maps to another status (or an exception)."""

    def __init__(self, answers: dict[str, object] | None = None):
        self.answers = answers or {}
        self.calls: list[str] = []

    def __call__(self, rule_id: str) -> dict:
        self.calls.append(rule_id)
        answer = self.answers.get(rule_id, "ready")
        if isinstance(answer, BaseException):
            raise answer
        if isinstance(answer, dict):
            return answer
        return {"status": answer, "detail": {"status": answer}}


def run_apply(module, tmp: Path, preflight=None):
    rows = module.build_worklist(DB, RULE_CONTROLS, GONE)
    door = FakeDoor()
    log = tmp / "rule-approvals.jsonl"
    summary = module.apply(rows, door, log, preflight=preflight or FakePreflight())
    lines = [json.loads(ln) for ln in log.read_text().splitlines()]
    return rows, door, lines, summary


def check_apply(tc: unittest.TestCase, module) -> None:
    """The expectations every mutant must break."""
    with tempfile.TemporaryDirectory() as td:
        rows, door, lines, summary = run_apply(module, Path(td))
    called = [p["rule_id"] for _, p in door.calls]
    tc.assertEqual({v for v, _ in door.calls}, {"approve-rule"})
    tc.assertEqual(called, [rid(READY_A), rid(READY_B)], "only ready rows, in worklist order")
    keys = [p.get("idempotency_key") for _, p in door.calls]
    for key in keys:
        tc.assertIsInstance(key, str)
        tc.assertRegex(key, UUID_RE)
    tc.assertEqual(len(set(keys)), len(keys), "a fresh key per rule")
    planned = {r["approve_rule"]["idempotency_key"] for r in rows if r["approve_rule"]}
    tc.assertFalse(planned & set(keys), "--apply mints fresh keys, never the dry-run's")
    for _, p in door.calls:
        tc.assertEqual(p["policy_kind"], "machine_enforceable")
        tc.assertEqual(p["control_keys"], [RULE_CONTROLS[p["rule_id"][:8]]["control"]])
        tc.assertIn(w.DECISION_ID, p["reason"])
    tc.assertEqual(summary["approved"], 1)
    tc.assertEqual(summary["refused"], 1)


def check_preflight_gate(tc: unittest.TestCase, module) -> None:
    """A non-ready preflight and a raising preflight each stop approve-rule for that rule."""
    for answer in ("delivery_projection_incomplete", RuntimeError("connection refused")):
        with tempfile.TemporaryDirectory() as td:
            pre = FakePreflight({rid(READY_B): answer})
            _rows, door, lines, summary = run_apply(module, Path(td), preflight=pre)
        tc.assertEqual(pre.calls, [rid(READY_A), rid(READY_B)], "every ready row is preflighted")
        tc.assertEqual([p["rule_id"] for _, p in door.calls], [rid(READY_A)],
                       "approve-rule is never called after a non-ready preflight")
        refused = next(x for x in lines[:-1] if x["rule_id"] == rid(READY_B))
        tc.assertEqual(refused["outcome"], "preflight_refused")
        tc.assertIsNone(refused["idempotency_key"])
        tc.assertIn("approve-rule not called", refused["refusal"])
        tc.assertEqual(summary["preflight_refused"], 1)
        tc.assertEqual(summary["refused"], 0)


def mutant(old: str, new: str) -> types.ModuleType:
    src = SOURCE_PATH.read_text()
    assert src.count(old) == 1, f"mutation anchor not unique: {old!r}"
    module = types.ModuleType("rule_approval_worklist_mutant")
    module.__file__ = str(SOURCE_PATH)
    exec(compile(src.replace(old, new), str(SOURCE_PATH), "exec"), module.__dict__)
    return module


class WorklistTest(unittest.TestCase):
    def test_every_readiness_class_is_reached_with_a_reason(self):
        rows = w.build_worklist(DB, RULE_CONTROLS, GONE)
        got = {r["short_id"]: r["readiness"] for r in rows}
        self.assertEqual(got, EXPECTED)
        self.assertEqual(set(got.values()), set(w.READINESS))
        for r in rows:
            self.assertTrue(r["why"])
            self.assertEqual(r["approve_rule"] is not None, r["readiness"] == "ready")
        tot = w.totals(rows)
        self.assertEqual(tot["blocked"], 8)
        self.assertEqual(tot["ready"], 2)
        self.assertEqual(tot["held_by_control"], {"session_boot": 1})

    def test_ready_row_carries_the_exact_approve_rule_json(self):
        rows = {r["short_id"]: r for r in w.build_worklist(DB, RULE_CONTROLS, GONE)}
        payload = rows[READY_A]["approve_rule"]
        self.assertEqual(set(payload), {"idempotency_key", "rule_id", "policy_kind", "control_keys", "reason"})
        self.assertEqual(payload["rule_id"], rid(READY_A))
        self.assertEqual(payload["control_keys"], ["deny_ok"])
        self.assertRegex(payload["idempotency_key"], UUID_RE)
        self.assertIn(w.DECISION_ID, payload["reason"])

    def test_dry_run_is_the_default_and_calls_nothing(self):
        door = FakeDoor()
        orig = (w.load_map, w.load_gone)
        w.load_map = lambda path=w.MAP: RULE_CONTROLS  # type: ignore[assignment]
        w.load_gone = lambda path=w.TRIAGE: GONE  # type: ignore[assignment]
        try:
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = w.main(["--json"], db=DB, call=door)
        finally:
            w.load_map, w.load_gone = orig
        self.assertEqual(rc, 0)
        self.assertEqual(door.calls, [])
        report = json.loads(buf.getvalue())
        self.assertNotIn("applied", report)
        self.assertEqual(report["totals"]["ready"], 2)

    def test_apply_calls_only_ready_rows_with_fresh_keys(self):
        check_apply(self, w)

    def test_apply_logs_receipts_and_verbatim_refusals(self):
        with tempfile.TemporaryDirectory() as td:
            _rows, door, lines, summary = run_apply(w, Path(td))
        results, tail = lines[:-1], lines[-1]
        self.assertEqual(len(results), 2)
        ok = next(x for x in results if x["rule_id"] == rid(READY_A))
        self.assertEqual(ok["outcome"], "approved")
        self.assertEqual(ok["approval_receipt_id"], "11111111-1111-4111-8111-111111111111")
        self.assertIsNone(ok["refusal"])
        no = next(x for x in results if x["rule_id"] == rid(READY_B))
        self.assertEqual(no["outcome"], "refused")
        self.assertEqual(no["refusal"], REFUSAL, "the refusal is recorded verbatim")
        self.assertEqual(no["idempotency_key"], door.calls[1][1]["idempotency_key"])
        self.assertEqual(tail["kind"], "summary")
        self.assertEqual(tail["not_attempted"], 0)
        self.assertFalse(tail["halted_after_refusals"])
        self.assertEqual(tail["skipped"], {"catalog_missing": 1, "class_not_approvable": 1,
                                           "gone": 1, "held": 1, "rule_only_surfaced": 1,
                                           "unmapped": 1})
        self.assertEqual(summary, tail)

    def test_refusal_ceiling_halts_and_counts_the_rest(self):
        with tempfile.TemporaryDirectory() as td:
            rows = w.build_worklist(DB, RULE_CONTROLS, GONE)
            door = FakeDoor()
            # READY_A is approved, READY_B refused; a ceiling of 1 stops after B.
            summary = w.apply(list(reversed(rows)), door, Path(td) / "log.jsonl",
                              preflight=FakePreflight(), max_refusals=1)
        self.assertEqual([p["rule_id"] for _, p in door.calls], [rid(READY_B)])
        self.assertEqual(summary["attempted"], 1)
        self.assertEqual(summary["not_attempted"], 1)
        self.assertTrue(summary["halted_after_refusals"])

    def test_limit_bounds_the_attempts(self):
        with tempfile.TemporaryDirectory() as td:
            rows = w.build_worklist(DB, RULE_CONTROLS, GONE)
            door = FakeDoor()
            summary = w.apply(rows, door, Path(td) / "log.jsonl", preflight=FakePreflight(), limit=1)
        self.assertEqual(len(door.calls), 1)
        self.assertEqual(summary["attempted"], 1)

    def test_non_ready_or_raising_preflight_refuses_the_call(self):
        check_preflight_gate(self, w)

    def test_ready_preflight_proceeds_and_is_logged(self):
        with tempfile.TemporaryDirectory() as td:
            pre = FakePreflight()
            _rows, door, lines, summary = run_apply(w, Path(td), preflight=pre)
        self.assertEqual(pre.calls, [rid(READY_A), rid(READY_B)])
        self.assertEqual(len(door.calls), 2)
        self.assertEqual({x["preflight_status"] for x in lines[:-1]}, {"ready"})
        self.assertEqual(summary["preflight_refused"], 0)

    def test_preflight_that_cannot_connect_refuses(self):
        # The REAL runner over a stub audit script that behaves like the real
        # one with no credential: message on stderr, exit 2, no JSON.
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "ops").mkdir()
            (root / "ops" / "rule-admission-audit.py").write_text(
                "import sys\n"
                "print('rule-admission-audit: DATABASE_URL or DATABASE_URL_READER required', file=sys.stderr)\n"
                "sys.exit(2)\n")
            runner = w.preflight_runner(root)
            answer = runner(rid(READY_A))
            self.assertEqual(answer["status"], "error")
            self.assertIn("exit 2", answer["error"])
            self.assertIn("DATABASE_URL", answer["error"])
            rows = w.build_worklist(DB, RULE_CONTROLS, GONE)
            door = FakeDoor()
            summary = w.apply(rows, door, root / "log.jsonl", preflight=runner)
            lines = [json.loads(ln) for ln in (root / "log.jsonl").read_text().splitlines()]
        self.assertEqual(door.calls, [], "no approve-rule call without a ready preflight")
        self.assertEqual(summary["preflight_refused"], 2)
        self.assertEqual({x["outcome"] for x in lines[:-1]}, {"preflight_refused"})
        self.assertEqual({x["preflight_status"] for x in lines[:-1]}, {"error"})

    def test_preflight_runner_passes_the_rule_id_and_reads_ready(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "ops").mkdir()
            (root / "ops" / "rule-admission-audit.py").write_text(
                "import json, sys\n"
                "assert sys.argv[1:3] == ['--preflight', '--rule-id'], sys.argv\n"
                "print(json.dumps({'status': 'ready', 'rule': sys.argv[3]}))\n")
            answer = w.preflight_runner(root)(rid(READY_A))
        self.assertEqual(answer["status"], "ready")
        self.assertEqual(answer["detail"]["rule"], rid(READY_A))

    def test_real_map_and_triage_load(self):
        controls = w.load_map()
        self.assertGreater(len(controls), 100)
        self.assertIsInstance(w.load_gone(), set)

    # ── mutants: each must break check_apply ──
    def assert_mutant_fails(self, module):
        # A mutant fails by breaking an expectation or by crashing the apply
        # path; either way it must not pass the check the real source passes.
        with self.assertRaises(Exception):
            check_apply(self, module)

    def test_mutant_that_approves_a_gone_rule_fails(self):
        self.assert_mutant_fails(mutant("    if gone:\n", "    if False:\n"))

    def test_mutant_that_approves_a_held_control_rule_fails(self):
        self.assert_mutant_fails(mutant(
            '    if not (cat.get("installed") and cat.get("verified")):\n',
            "    if False:\n"))

    def test_mutant_that_drops_the_idempotency_key_fails(self):
        self.assert_mutant_fails(mutant(
            '        "idempotency_key": idempotency_key,\n', ""))

    def test_mutant_that_approves_a_surfacing_only_rule_fails(self):
        self.assert_mutant_fails(mutant('    if map_class == "surfacing":\n', "    if False:\n"))

    def test_mutant_that_drops_the_preflight_gate_fails(self):
        with self.assertRaises(Exception):
            check_preflight_gate(self, mutant("            if pre_status != PREFLIGHT_READY:\n",
                                              "            if False:\n"))

    def test_mutant_that_reads_a_preflight_exception_as_ready_fails(self):
        with self.assertRaises(Exception):
            check_preflight_gate(self, mutant(
                '                pre = {"status": "error", "error": f"preflight raised {type(exc).__name__}: {exc}"}\n',
                '                pre = {"status": "ready"}\n'))

    def test_unmutated_source_passes_the_same_check(self):
        module = mutant("SCHEMA = ", "SCHEMA  = ")
        check_apply(self, module)
        check_preflight_gate(self, module)

    # ── wiring ──
    def test_library_shape_and_wiring(self):
        src = SOURCE_PATH.read_text()
        self.assertFalse(src.startswith("#!"), "a library carries no shebang")
        self.assertIsNone(re.search(r"^if __name__", src, re.M), "a library has no __main__ block")
        run_sh = (ROOT / "run.sh").read_text()
        self.assertIn("rule-approvals)", run_sh)
        self.assertIn("from lib import rule_approval_worklist", run_sh)
        ci = (ROOT / "ops" / "ci.sh").read_text()
        self.assertIn("ops/*-selftest.py", ci)
        self.assertTrue(Path(__file__).resolve().match("ops/*-selftest.py"))
        self.assertIn('"run.sh"), "call", verb', src, "writes go through the ./run.sh call door")
        self.assertNotIn('"call-verb"', src, "never the generic passthrough verb")
        self.assertIn('"rule-admission-audit.py"', src, "the preflight is the AGENTS.md readback")
        self.assertIn('"--preflight", "--rule-id", rule_id', src)


if __name__ == "__main__":
    unittest.main(verbosity=1)
