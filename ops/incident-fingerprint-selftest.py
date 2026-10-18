#!/usr/bin/env python3
"""Selftest for the incident fingerprint and the success-clears decision.

WHY THIS EXISTS. Process-audit council, 2026-08-23, recommendation 3. Two
things were measured wrong with the incident ledger and this file holds the
guard on both fixes.

THE FIRST IS THE FINGERPRINT'S FOURTH FIELD. 0116 made the signature
service|environment|run_key|failure_class and made two open incidents with one
signature impossible — that part works, and partner-ping's 89 failed runs on
2026-08-23 really were one incident. But the fourth field arrives in two
registers. Some callers name a diagnosis (pubkey_mismatch, restore_failed);
bin/nightly.sh passes the wrapper's exit code through as exit_<n>. So
nightly.vault-drift-watch was open TWICE at once — as exit_2 and as exit_69 —
and nightly.portability-mirror had failed as exit_1, exit_2 and exit_69 across
four days. One job, one remedy, three fingerprints.

THE COUNCIL'S KILL CONDITION IS THE HARD PART, and most of the cases below are
it: "distinct failure classes on the same job must NOT collapse into one row."
It is easy to make the churn go away by making the fingerprint broad, and that
trade is strictly worse than the churn — restore-rehearse-weekly's
pubkey_mismatch, restore_failed and aborted are three different pieces of work
on one job, and a fingerprint that merged them would hide two of them behind a
row somebody already looked at. So the normalization is narrow by construction:
it rewrites ONLY the exit_<n> shape, and even there it keeps every code this
codebase has given a meaning of its own.

THE SECOND IS THAT NOTHING EVER CLEARED. On 2026-08-23 five incidents were open
with twelve consecutive green runs behind them, because the repo's only close
path needs the owner credential 0117 withholds from carr_jobs, so bin/nightly.sh
prints `incident sweep (admin capability unavailable)` every night and sweeps
nothing. recovery_decision is the replacement rule, and a rule that will
rubber-stamp anything is worse than none, because then the pile only LOOKS
handled. Every refusal below is a reason the job role may be trusted with the
close at all.

WHAT THIS FILE CANNOT SEE, said out loud: the database half. The refusals are
enforced a second time inside ops.clear_recovered_incident (migration 0293),
whose own proof block runs at apply time — SEV-1, hand-opened incidents, a
short sequence, and a failure inside the sequence are each refused there too,
so a wrong answer in this Python still cannot close anything. This file is the
cheap guard that runs on every push; that one is the guarantee.

Run: python3 ops/incident-fingerprint-selftest.py
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from migration_source import migration_names, read_migration
import importlib.util
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
TOOL = os.path.join(REPO, "tools", "ops-record.py")
MIGRATION = os.path.join(
    REPO, "migrations", "0294_incident_fingerprint_and_success_clears.sql")

spec = importlib.util.spec_from_file_location("ops_record", TOOL)
assert spec is not None and spec.loader is not None, f"cannot load {TOOL}"
opsrec = importlib.util.module_from_spec(spec)
spec.loader.exec_module(opsrec)

CASES: list[tuple] = []


def case(name):
    def deco(fn):
        CASES.append((name, fn))
        return fn
    return deco


def fp(service="partner-ping", environment="production",
       operation="launchd.run", failure_class="exit_1"):
    return opsrec.incident_fingerprint(service, environment, operation,
                                       failure_class)


def incident(**over):
    """A recovering SEV-3 the collector opened — the clearable case."""
    base = {
        "ref": "INC-20260823-01",
        "state": "monitoring",
        "severity": "SEV-3",
        "source_kind": "collector",
        "signature": fp(),
        "occurrence_count": 12,
    }
    base.update(over)
    return base


# ── the fingerprint ──────────────────────────────────────────────────────────

@case("a named failure class is never rewritten — the council's kill condition")
def _(assert_):
    for named in ("pubkey_mismatch", "restore_failed", "aborted",
                  "preflight_failed", "keepalive_not_accepting",
                  "keepalive_not_running", "verb_internal_error",
                  "performance_budget_exceeded", "production_readback_mismatch"):
        assert_(opsrec.normalize_failure_class(named) == named,
                f"{named} must survive the fingerprint untouched")


@case("distinct named classes on one job stay distinct fingerprints")
def _(assert_):
    # The live case, 2026-08-23: restore-rehearse-weekly had three open
    # incidents on restore.key-recovery and all three needed different work.
    seen = {fp("restore-rehearse-weekly", "production", "restore.key-recovery", c)
            for c in ("pubkey_mismatch", "restore_failed", "aborted")}
    assert_(len(seen) == 3,
            f"three remedies must not become one row: {sorted(seen)}")


@case("bare exit codes on one job collapse to one fingerprint")
def _(assert_):
    # nightly.portability-mirror failed exit_1, exit_2 and exit_69 across four
    # days. exit_69 is EX_UNAVAILABLE and keeps its own class; 1 and 2 are the
    # same statement — "it returned nonzero" — made twice.
    one = fp("nightly-record-layer", "production", "nightly.portability-mirror", "exit_1")
    two = fp("nightly-record-layer", "production", "nightly.portability-mirror", "exit_2")
    assert_(one == two, f"exit_1 and exit_2 are one problem: {one} vs {two}")
    assert_(one.endswith("|exit_status"), f"and it should say so plainly: {one}")


@case("an exit code this codebase gives a meaning keeps its own class")
def _(assert_):
    meanings = {69: "dependency_unavailable", 78: "configuration",
                64: "usage", 124: "timed_out", 137: "killed", 143: "terminated",
                77: "permission_denied"}
    for code, expected in meanings.items():
        got = opsrec.normalize_failure_class(f"exit_{code}")
        assert_(got == expected, f"exit_{code} should read {expected}, got {got}")
        assert_(got != opsrec.GENERIC_EXIT_CLASS,
                f"exit_{code} must not fold into a plain nonzero")
    # And that is the split the live ledger needed: vault-drift-watch was open
    # as exit_2 and exit_69 at once, and those two are NOT the same work.
    plain = fp("nightly-record-layer", "production", "nightly.vault-drift-watch", "exit_2")
    dep = fp("nightly-record-layer", "production", "nightly.vault-drift-watch", "exit_69")
    assert_(plain != dep,
            "a dependency being down is not the same problem as a plain nonzero")


@case("the exit-code spellings the wrappers actually emit all normalize")
def _(assert_):
    for spelling in ("exit_1", "exit-1", "EXIT_1", "exit1"):
        assert_(opsrec.normalize_failure_class(spelling) == opsrec.GENERIC_EXIT_CLASS,
                f"{spelling} should read as a plain nonzero")
    # Not a bare exit code, so not touched — these are diagnoses that merely
    # mention a number.
    for not_a_code in ("exit_code_mismatch", "exit_1_after_retry", "exit_9999"):
        assert_(opsrec.normalize_failure_class(not_a_code) == not_a_code,
                f"{not_a_code} names something specific and must survive")


@case("a failure with no class at all gets a name, not an empty field")
def _(assert_):
    # A deployment can arrive without one. Before this, the fingerprint ended in
    # a bare bar and every classless failure on a job matched every other one.
    for empty in (None, "", "   "):
        assert_(opsrec.normalize_failure_class(empty) == opsrec.UNCLASSIFIED,
                f"{empty!r} should read as {opsrec.UNCLASSIFIED}")
    assert_(fp(failure_class=None).endswith(f"|{opsrec.UNCLASSIFIED}"),
            "and the fingerprint should say so")


@case("the fingerprint keeps 0116's four-field shape, so its index still holds")
def _(assert_):
    got = fp()
    assert_(got.count("|") == 3, f"exactly four fields: {got}")
    assert_(got == "partner-ping|production|launchd.run|exit_status", got)
    assert_(opsrec.fingerprint_job(got) == ("partner-ping", "production", "launchd.run"),
            "and the job must be readable back out of it for the recovery lookup")


@case("a fingerprint that is not four fields yields no job to check")
def _(assert_):
    for bad in (None, "", "service", "service|production", "|production|job|cls"):
        assert_(opsrec.fingerprint_job(bad) is None,
                f"{bad!r} must not be read as a job")


@case("the migration's backfill table has not drifted from the writer's rule")
def _(assert_):
    # Migration 0293 restates the exit-code meanings so a one-time backfill can
    # reach rows the writer will never touch again. Two copies of one rule is
    # safe only while something fails when they disagree. This is that thing.
    if Path(MIGRATION).name not in migration_names(Path(MIGRATION).parent.parent):
        assert_(False, f"the migration this rule is shared with is missing: {MIGRATION}")
        return
    sql = read_migration(Path(MIGRATION).parent.parent, Path(MIGRATION).name)
    block = re.search(r"values\s*(\(\s*64\b.*?)\)\s*as m\(code, class\)", sql,
                      re.S | re.I)
    assert_(block is not None, "0293 no longer carries the exit-code backfill table")
    if block is None:
        return
    pairs = {int(c): n for c, n in
             re.findall(r"\(\s*(\d+)\s*,\s*'([a-z_]+)'\s*\)", block.group(1))}
    assert_(pairs == opsrec.NAMED_EXIT_CLASSES,
            f"0293 and NAMED_EXIT_CLASSES disagree:\n  sql:    {pairs}\n"
            f"  python: {opsrec.NAMED_EXIT_CLASSES}")


# ── success-clears ───────────────────────────────────────────────────────────

@case("the defined success sequence clears a recovered SEV-3 job incident")
def _(assert_):
    action, reason = opsrec.recovery_decision(incident(), healthy_streak=3)
    assert_(action == "clear", f"three consecutive healthy runs should clear: {reason}")
    assert_("3" in reason, f"the reason should carry the count it stood on: {reason}")


@case("a partial sequence records recovery and keeps watching")
def _(assert_):
    for streak in (1, 2):
        action, reason = opsrec.recovery_decision(incident(), healthy_streak=streak)
        assert_(action == "monitor",
                f"{streak} of 3 should watch, not close: {action} {reason}")
        assert_(str(streak) in reason, f"say how far through it is: {reason}")


@case("a job whose latest run is not green is not recovering at all")
def _(assert_):
    action, reason = opsrec.recovery_decision(incident(), healthy_streak=0)
    assert_(action == "none", f"nothing to record: {action} {reason}")


@case("SEV-1 never closes on a machine's say-so, however long the streak")
def _(assert_):
    for streak in (3, 12, 500):
        action, reason = opsrec.recovery_decision(
            incident(severity="SEV-1"), healthy_streak=streak)
        assert_(action == "monitor",
                f"SEV-1 with {streak} green runs must stop at monitoring, got {action}")
        assert_("human" in reason.lower(),
                f"and the reason should say whose call it is: {reason}")
    # SEV-0 is not a severity this system issues today, and it must not become
    # closable by accident if it ever is.
    action, _ = opsrec.recovery_decision(incident(severity="SEV-0"), healthy_streak=9)
    assert_(action == "monitor", "SEV-0 must not clear either")


@case("an incident a human opened is never closed by the run ledger")
def _(assert_):
    action, reason = opsrec.recovery_decision(
        incident(source_kind="operator"), healthy_streak=9)
    assert_(action == "none", f"hand-opened must be left alone: {action}")
    assert_("hand" in reason.lower() or "human" in reason.lower(), reason)


@case("an incident with no fingerprint has no job whose health to read")
def _(assert_):
    action, reason = opsrec.recovery_decision(
        incident(signature=None), healthy_streak=9)
    assert_(action == "none", f"no fingerprint, no automatic close: {action}")


@case("an already-closed incident is left exactly as the human left it")
def _(assert_):
    for state in ("resolved", "reviewed"):
        action, reason = opsrec.recovery_decision(
            incident(state=state), healthy_streak=9)
        assert_(action == "none", f"{state} must not be touched again: {action}")


@case("the sequence length is the system's, not a caller's")
def _(assert_):
    assert_(opsrec.HEALTHY_RUNS_TO_CLEAR == 3,
            "the council's number is three consecutive healthy runs")
    # A caller may ask for more care. It may not ask for less: the database
    # function clamps below three, and this is the paired assertion on the
    # Python side so both halves stay readable together.
    action, _ = opsrec.recovery_decision(incident(), healthy_streak=5, required=5)
    assert_(action == "clear", "a longer sequence a caller asked for is allowed")
    action, _ = opsrec.recovery_decision(incident(), healthy_streak=4, required=5)
    assert_(action == "monitor", "and it is genuinely enforced")


def main():
    failures = []
    for name, fn in CASES:
        errors = []

        def assert_(cond, msg):
            if not cond:
                errors.append(msg)

        try:
            fn(assert_)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"raised {type(exc).__name__}: {exc}")
        if errors:
            print(f"[FAIL] {name}")
            for e in errors:
                print(f"    - {e}")
            failures.append(name)
        else:
            print(f"[PASS] {name}")

    print(f"\n{len(CASES) - len(failures)}/{len(CASES)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
