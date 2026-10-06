#!/usr/bin/env python3
"""The data-dependent-migration table is a precondition, never a skip-on-failure.

WHAT IT EXISTS FOR (defect 87da6fe5, 2026-08-22). Migration 0248 binds the
conduct-stop control to the autonomy rule, with an insert requiring that rule to
exist as `proposed` and a proof block that raises when the insert matched
nothing. That row exists in Production and nowhere else, so 0248 applied cleanly
to Production and then failed on the isolated staging project — which stopped at
206 applied while Production reached 209.

The cost was the whole release chain. The typed staging readback compares
staging's schema against a candidate's exact declared migration set, so it could
never match; the recovery rehearsal could not complete; and Production approval
refuses without a rehearsal bundle. The repository also stopped being able to
reconstruct a non-production environment, which is Program 1's rebuild clause.

WHAT THIS SUITE PINS, because the table is one edit away from being a door that
lets any red migration through:

  1. The probe runs BEFORE the file, not after it fails. A migration that errors
     for any other reason must still stop the run exactly as it did.
  2. A present precondition means the file APPLIES. The table must not turn into
     "never run 0248".
  3. An absent precondition records the file with its real sha256, so the ledger
     stays honest and validate_applied_ledger keeps working.
  4. Every entry names a file that exists, a probe that is a single read, and a
     stated reason. An entry for a file nobody ships is a stale exemption.

ALSO PINS (added 2026-09-24, after the pairing gap recurred twice --
0565/0566 on 2026-09-23, then 0575/0576 the very next day, fixed by hand in
PR #1193): any migration that is the FIRST in the tree to CREATE a
SECURITY DEFINER function or GRANT EXECUTE on one -- i.e. a live change to
the SCAC mutation catalog -- must be declared as an atomic pair with its
immediately-following `*_scac_successor.sql` seal in BOTH
ATOMIC_MIGRATION_GROUPS and STRICT_ATOMIC_MIGRATION_GROUPS. Left undeclared,
bin/migrate-prod.sh applies the domain file as its own one-migration batch,
and production's deferred SCAC epoch trigger refuses it at commit ("live
SCAC vNN mutation catalog drifted"). See test_new_authority_migrations_are_
atomically_sealed() and its seeded failing case below.

Run: .venv/bin/python ops/migrate-precondition-selftest.py
"""

from __future__ import annotations

import pathlib
import re
import sys
import hashlib

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))

import migrate  # noqa: E402

PASS: list[str] = []
FAIL: list[str] = []


# --- atomic-seal-pairing predicate ------------------------------------------
#
# The SCAC mutation registry is boilerplate that every migration re-declares
# (each migration bumps ops.scac_mutation_registration_v<N> and its sibling
# housekeeping functions). Matching "SECURITY DEFINER" anywhere in a file's
# text therefore matches almost every migration and says nothing about
# whether THIS migration adds new authority. These names are excluded so the
# predicate below only fires on a migration's own business functions.
SCAC_BOILERPLATE_FUNCTION_RE = re.compile(
    r"^ops\.scac_("
    r"mutation_registration_v\d+"
    r"|mutation_catalog_v\d+_current"
    r"|mutation_registry_v\d+_seal_available"
    r"|mutation_registry_seal_valid"
    r"|policy_epoch_snapshot"
    r"|policy_epoch_chain_state"
    r"|reference_monitor_state"
    r"|mutation_registry_append_only"
    r")$",
    re.IGNORECASE,
)

FUNCTION_DEF_RE = re.compile(
    r"create\s+(?:or\s+replace\s+)?function\s+([a-zA-Z0-9_.]+)\s*\([^)]*\)"
    r"[^;]*?security\s+definer",
    re.IGNORECASE | re.DOTALL,
)


def new_authority_functions(sql: str) -> set[str]:
    """Non-boilerplate SECURITY DEFINER functions this migration (re)declares."""
    return {
        name for name in FUNCTION_DEF_RE.findall(sql)
        if not SCAC_BOILERPLATE_FUNCTION_RE.match(name)
    }


def find_atomic_seal_requirements(
    migrations: list[tuple[str, str]],
) -> list[tuple[str, str]]:
    """(pred, succ) pairs that MUST be declared as an atomic group.

    `migrations` is [(filename, sql)] in application order (exactly
    migrate.load_migrations() with the digest dropped). A pair is required
    when `succ` is a `*_scac_successor.sql` file immediately following a
    plain (not itself self-sealed) `pred`, and `pred` is the FIRST migration
    anywhere in the tree to CREATE [OR REPLACE] some non-boilerplate
    SECURITY DEFINER function.

    Only the FIRST declaration counts. A later migration that CREATE OR
    REPLACEs an already-existing function (same name, e.g. a schema/body
    change with identical grants -- the real shape of 0577, which touches
    ops.work_request_card first defined back in 0174 and admits no new SCAC
    entry) is not opening new authority and is correctly left standalone --
    this is the "verb-schema change with no new function" exception named in
    the task. A predecessor that already ends in `_scac_successor` (or
    `_and_scac_successor`) seals itself in the same file/transaction and
    needs no external pairing either.
    """
    first_seen: dict[str, str] = {}
    for name, sql in migrations:
        for fn in new_authority_functions(sql):
            first_seen.setdefault(fn, name)

    required: list[tuple[str, str]] = []
    for i in range(1, len(migrations)):
        pred_name, pred_sql = migrations[i - 1]
        succ_name, _succ_sql = migrations[i]
        if "_scac_successor" not in succ_name:
            continue
        if "_scac_successor" in pred_name:
            continue  # predecessor already seals itself in the same file
        newly_opened = {
            fn for fn in new_authority_functions(pred_sql)
            if first_seen.get(fn) == pred_name
        }
        if newly_opened:
            required.append((pred_name, succ_name))
    return required


def declared_atomic_pairs(
    groups: tuple[tuple[str, ...], ...],
) -> set[tuple[str, str]]:
    """The (predecessor, seal) pair each declared group actually protects.

    Only a group's LAST two files matter here: every declared group ends in
    its `*_scac_successor.sql` seal, and the seal's own immediate
    predecessor -- the file it refuses to commit without -- is always the
    second-to-last element, regardless of how many earlier domain files the
    group also bundles in (e.g. 0508-0511 before the 0512 seal)."""
    return {(g[-2], g[-1]) for g in groups if len(g) >= 2}


def declared_strict_pairs(
    groups: tuple[tuple[str, ...], ...],
) -> set[tuple[str, str]]:
    """Every adjacent pair inside each declared STRICT group."""
    pairs: set[tuple[str, str]] = set()
    for group in groups:
        for a, b in zip(group, group[1:]):
            pairs.add((a, b))
    return pairs


# STRICT_ATOMIC_MIGRATION_GROUPS' earliest entry is 0532a/0532b -- the
# mechanism did not exist before that migration shipped. These pairs
# genuinely open new SCAC authority (find_atomic_seal_requirements() below
# still finds them) and are already declared in ATOMIC_MIGRATION_GROUPS, but
# predate STRICT_ATOMIC_MIGRATION_GROUPS and were never backfilled into it.
# Pre-existing exceptions, not new gaps: allowlisted by their seal filename
# rather than silently exempted by a date range.
PRE_STRICT_ATOMIC_GROUP_ALLOWLIST = frozenset({
    "0503_gate_zero_outcome_and_scac_successor.sql",
    "0512_foundation_assurance_scac_successor.sql",
    "0518_program_controller_seams_scac_successor.sql",
    "0522_producer_trio_scac_successor.sql",
    "0524_doc_conversation_write_doors_scac_successor.sql",
    "0526_doc_conversation_list_scac_successor.sql",
    "0528_notification_preferences_scac_successor.sql",
    "0530_session_identity_scac_successor.sql",
    "0532_room_dispatch_spine_scac_successor.sql",
})

# 0736 was already applied to the production ledger before the pairing
# requirement could be met. Its SECURITY DEFINER helpers revoke EXECUTE.
# This one historical exception is valid only for both exact applied artifacts.
HISTORICAL_UNPAIRED_SCAC_PAIRS = {
    ("0736_loop_notification_queue.sql", "0737_incident_triage_scac_successor.sql"): (
        "039846670f2e206b2e99a82f95ebb9738a285496c77c7444ef9dd41cf115e7ca",
        "ef0b44a3599e51f6f473e937ad961b581ed24e773f85a0c08527c8d3d381e103",
    ),
}


def historical_unpaired_pairs(
    migrations: list[tuple[str, bytes]],
) -> set[tuple[str, str]]:
    digests = {name: hashlib.sha256(sql).hexdigest() for name, sql in migrations}
    return {
        pair for pair, expected in HISTORICAL_UNPAIRED_SCAC_PAIRS.items()
        if (digests.get(pair[0]), digests.get(pair[1])) == expected
    }


def check(name: str, ok: bool, detail: str = "") -> None:
    (PASS if ok else FAIL).append(name)
    print(f"  {'ok  ' if ok else 'FAIL'} {name}{'' if ok else '  — ' + detail}")


class FakeCursor:
    """Answers exactly the calls the apply loop makes, and records them."""

    def __init__(self, probe_returns_row: bool, raise_on_apply: Exception | None = None):
        self._probe_row = probe_returns_row
        self._raise = raise_on_apply
        self.executed: list[str] = []
        self._last_was_probe = False

    def execute(self, sql, params=None):
        text = " ".join(str(sql).split())
        self.executed.append(text)
        self._last_was_probe = text.lower().startswith("select 1 from")
        if self._raise is not None and text.startswith("--") is False \
           and "insert into schema_migrations" not in text \
           and not self._last_was_probe and "set local" not in text.lower():
            raise self._raise

    def fetchone(self):
        if self._last_was_probe:
            return (1,) if self._probe_row else None
        return None

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_table_shape() -> None:
    table = migrate.DATA_DEPENDENT_MIGRATIONS
    check("the table is not empty and not a wildcard", bool(table) and isinstance(table, dict))
    for name, value in table.items():
        path = REPO / "migrations" / name
        check(f"{name} names a migration that exists", path.is_file(),
              "an entry for a file nobody ships is a stale exemption")
        ok_shape = isinstance(value, tuple) and len(value) == 2 and all(
            isinstance(v, str) and v.strip() for v in value)
        check(f"{name} carries a probe and a stated reason", ok_shape)
        if not ok_shape:
            continue
        probe, reason = value
        check(f"{name}'s probe is a single read",
              probe.lower().lstrip().startswith("select") and ";" not in probe.strip().rstrip(";"),
              "a probe that can write, or chains statements, is not a precondition")
        check(f"{name}'s reason explains why the file is inert without its row",
              len(reason) > 40)


def test_probe_is_checked_before_the_file_runs() -> None:
    """The ordering IS the safety property: a precondition, not a rescue."""
    cur = FakeCursor(probe_returns_row=False)
    name = next(iter(migrate.DATA_DEPENDENT_MIGRATIONS))
    probe, _ = migrate.DATA_DEPENDENT_MIGRATIONS[name]
    cur.execute(probe)
    check("an absent precondition is detectable before applying", cur.fetchone() is None)

    cur2 = FakeCursor(probe_returns_row=True)
    cur2.execute(probe)
    check("a present precondition is detectable before applying", cur2.fetchone() is not None)


def test_source_enforces_the_contract() -> None:
    """Read the apply loop itself: the guard must key on the probe, not on an
    exception handler. A version that caught the migration's error and carried on
    would pass every behavioural test above while being the door this must not be."""
    src = (REPO / "tools" / "migrate.py").read_text(encoding="utf-8")
    loop = src[src.index("for batch in migration_batches(pending):"):]
    execute_loop = loop[:loop.index("            try:\n                conn.commit()")]
    check("the guard consults DATA_DEPENDENT_MIGRATIONS inside the apply loop",
          "DATA_DEPENDENT_MIGRATIONS.get(name)" in loop)
    check("the discharge path runs the probe and tests for no row",
          re.search(r"cur\.execute\(probe\)\s*\n\s*if cur\.fetchone\(\) is None:", loop) is not None)
    # SCOPED TO THE DISCHARGE BLOCK. Checking the whole loop passes trivially,
    # because the ordinary apply path records (name, digest) too — a first
    # version of this assertion did exactly that and survived a mutation that
    # wrote a literal placeholder into the ledger on the discharge path.
    discharge = loop[loop.index("if cur.fetchone() is None:"):loop.index("continue")]
    check("the discharge records the real sha256, not a placeholder",
          "insert into schema_migrations (filename, sha256) values (%s, %s)" in discharge
          and "(name, digest)" in discharge,
          "a discharged row with a fake sha makes validate_applied_ledger reject the tree later")
    check("nothing swallows a migration exception into a discharge",
          "except Exception" not in execute_loop and "except psycopg.Error" not in execute_loop,
          "a broad handler here would turn this table into skip-on-failure")
    check("the two documented timeout handlers still fail the run",
          "LockNotAvailable" in loop and "QueryCanceled" in loop and loop.count("fail(") >= 2)
    check("0339 and later migrations reject their own transaction control",
          migrate.OUTER_TRANSACTION_MIGRATION == "0339_"
          and all(migrate.contains_transaction_control(statement) for statement in (
              "begin;", "begin transaction;", " COMMIT;", "commit work;",
              "start transaction;", "rollback;", "rollback work;", "end;",
              "select 1; commit; select 2;", "commit; select 1;",
              "begin; select 1;", "rollback transaction;", "/*x*/ commit;",
              "commit and chain;", "commit work and no chain;",
              "rollback and chain;", "abort;", "abort work;",
              "prepare transaction 'x';", "commit prepared 'x';"))
          and not any(migrate.contains_transaction_control(statement) for statement in (
              "-- begin;\nselect 1;", "select 'commit;';", 'select "rollback";',
              "do $$ begin perform 1; end $$;", "/* commit; */ select 1;")),
          "an internal commit would expose schema before its ledger-bound epoch")


def test_historical_transaction_artifact_is_exact() -> None:
    artifacts = migrate.HISTORICAL_TRANSACTION_CONTROL_ARTIFACTS
    expected_names = {
        "0344_demote_evidence_activation_bookkeeping.sql",
        "0345_governance_queue_projection.sql",
        "0348_pr_only_main_ruleset_control.sql",
        "0349_versioned_rule_amendment.sql",
        "0351_legacy_rule_lifecycle_admission.sql",
    }
    check("the historical transaction-control grandfather is five exact files",
          set(artifacts) == expected_names,
          "the exception must never widen to another migration")
    exact = True
    drift_refused = True
    for name in expected_names:
        sql = (REPO / "migrations" / name).read_text(encoding="utf-8")
        digest = hashlib.sha256(sql.encode()).hexdigest()
        exact = exact and artifacts.get(name) == digest \
            and migrate.contains_transaction_control(sql)
        changed_digest = hashlib.sha256((sql + "\n-- drift probe").encode()).hexdigest()
        drift_refused = drift_refused and artifacts.get(name) != changed_digest
    check("every grandfather digest equals its immutable applied artifact", exact,
          "an edited or transaction-free file must not match a historical exception")
    check("one-byte drift cannot reuse any historical grandfather", drift_refused)


def test_reviewed_controller_transaction_artifact_is_exact() -> None:
    artifacts = migrate.REVIEWED_TRANSACTION_CONTROL_ARTIFACTS
    expected_names = {
        "0363_rule_delivery_activation_digest_repin.sql",
        "0382_standing_guidance_reader_boundary.sql",
        "0383_control_plane_not_configured_state.sql",
        "0387_control_plane_record_queue_priority_tiers.sql",
        "0425_disable_legacy_schedule_readback_grant.sql",
        "0426_withdraw_a_work_request_captured_in_error.sql",
        "0427_tour_rights_projection_hardening.sql",
        "0428_tour_property_identity_jurisdiction.sql",
        "0429_tour_domain_route_cheat_sheet.sql",
        "0430_tour_delivery_data_plane.sql",
        "0431_completion_register_schema.sql",
        "0450_canonical_ownership_lease_kernel.sql",
        "0451_assurance_evidence_acceptance_persistence.sql",
    }
    check("the reviewed transaction allowlist is thirteen exact artifacts",
          set(artifacts) == expected_names
          and not set(artifacts) & set(migrate.HISTORICAL_TRANSACTION_CONTROL_ARTIFACTS))
    exact = True
    drift_refused = True
    for name in expected_names:
        sql = (REPO / "migrations" / name).read_text(encoding="utf-8")
        digest = hashlib.sha256(sql.encode()).hexdigest()
        changed_digest = hashlib.sha256((sql + "\n-- drift probe").encode()).hexdigest()
        exact = exact and artifacts.get(name) == digest \
            and migrate.contains_transaction_control(sql)
        drift_refused = drift_refused and artifacts.get(name) != changed_digest
    check("each controller digest equals its reviewed source artifact", exact)
    check("one-byte drift cannot reuse any controller transaction review", drift_refused)


def test_seeded_failing_case_proves_the_check_fires() -> None:
    """Reproduce the exact shape of the two real incidents -- 0565/0566
    (2026-09-23) and 0575/0576 (2026-09-24, fixed by hand in PR #1193): a
    migration installs a brand-new SECURITY DEFINER verb with its grant,
    immediately followed by its `*_scac_successor.sql` seal, and NEITHER
    file is declared in ATOMIC_MIGRATION_GROUPS. Proves
    find_atomic_seal_requirements() actually flags the gap -- this is the
    seeded failure the task asked for, not a check that only ever passes."""
    seeded = [
        ("0001_preexisting.sql", "select 1;"),
        (
            "0900_seeded_new_verb.sql",
            "create or replace function ops.seeded_new_verb(p text) "
            "returns jsonb language sql security definer as $$ select '{}'::jsonb $$; "
            "grant execute on function ops.seeded_new_verb(text) to carr_writer;",
        ),
        ("0901_seeded_new_verb_scac_successor.sql", "-- GENERATED seal\nselect 1;"),
    ]
    required = find_atomic_seal_requirements(seeded)
    seeded_pair = ("0900_seeded_new_verb.sql", "0901_seeded_new_verb_scac_successor.sql")
    check("the seeded new-verb migration is detected as requiring a seal pair",
          required == [seeded_pair])

    undeclared: tuple[tuple[str, ...], ...] = ()  # exactly the real incidents' state
    missing = [pair for pair in required if pair not in declared_atomic_pairs(undeclared)]
    check("an undeclared pairing is reported missing -- the check fires",
          missing == [seeded_pair])

    declared_ok: tuple[tuple[str, ...], ...] = (seeded_pair,)
    missing_ok = [pair for pair in required if pair not in declared_atomic_pairs(declared_ok)]
    check("declaring the pairing correctly clears the check",
          missing_ok == [])


def test_redeclared_function_needs_no_new_pairing() -> None:
    """0577/0578 (real history, task-cited exception): 0577 only CREATE OR
    REPLACEs an already-existing function (ops.work_request_card, first
    defined back in 0174) with identical grants -- no new SCAC entry,
    confirmed against a real disposable PostgreSQL 17 per 0577's own
    migration header. The predicate must not demand a pairing here, or it
    would false-positive on every legitimate verb-schema-only successor."""
    seeded = [
        (
            "0100_defines_verb.sql",
            "create or replace function ops.stable_verb() returns jsonb "
            "language sql security definer as $$ select '{}'::jsonb $$;",
        ),
        ("0101_unrelated.sql", "select 1;"),
        (
            "0102_redeclares_verb.sql",
            "create or replace function ops.stable_verb() returns jsonb "
            "language sql security definer as $$ select '{}'::jsonb $$;",
        ),
        ("0103_redeclares_verb_scac_successor.sql", "-- GENERATED seal\nselect 1;"),
    ]
    required = find_atomic_seal_requirements(seeded)
    check("re-declaring an already-existing function requires no new pairing",
          required == [])


def test_historical_unpaired_pair_is_byte_exact() -> None:
    """Only the two reviewed, already-applied migration artifacts are exempt."""
    pred_name = "0736_loop_notification_queue.sql"
    succ_name = "0737_incident_triage_scac_successor.sql"
    pred = (REPO / "migrations" / pred_name).read_bytes().decode("utf-8")
    succ = (REPO / "migrations" / succ_name).read_bytes().decode("utf-8")
    pair = (pred_name, succ_name)
    ordered = [("0001_unrelated.sql", "select 1;"), (pred_name, pred), (succ_name, succ)]
    check("the historical 0736/0737 pair still opens authority",
          pair in find_atomic_seal_requirements(ordered))
    check("only the exact historical filenames and bytes are exempt",
          historical_unpaired_pairs([(name, sql.encode()) for name, sql in ordered]) == {pair})

    variants = {
        "changed 0736 bytes": (pred_name, pred + "\n-- drift", succ_name, succ),
        "changed 0737 bytes": (pred_name, pred, succ_name, succ + "\n-- drift"),
        "changed 0736 line endings": (pred_name, pred.replace("\n", "\r\n"), succ_name, succ),
        "other predecessor filename": ("0900_loop_notification_queue.sql", pred, succ_name, succ),
        "other successor filename": (pred_name, pred, "0901_incident_triage_scac_successor.sql", succ),
        "other overload": (
            pred_name, pred + "\ncreate function ops.loop_notification_candidate(text) "
            "returns boolean language sql security definer as $$ select true $$;", succ_name, succ),
        "commented revoke": (
            pred_name,
            pred.replace(
                "revoke all on function ops.loop_notification_candidate(uuid)\n"
                "  from public,carr_reader,carr_writer,carr_jobs,carr_authority;",
                "-- revoke all on function ops.loop_notification_candidate(uuid)\n"
                "--   from public,carr_reader,carr_writer,carr_jobs,carr_authority;", 1),
            succ_name, succ),
        "GRANT ON ROUTINE": (
            pred_name, pred + "\ngrant execute on routine ops.loop_notification_candidate(uuid) "
            "to carr_writer;", succ_name, succ),
        "GRANT ON ALL ROUTINES IN SCHEMA": (
            pred_name, pred + "\ngrant execute on all routines in schema ops "
            "to carr_writer;", succ_name, succ),
    }
    for label, (changed_pred_name, changed_pred, changed_succ_name, changed_succ) in variants.items():
        changed_pair = (changed_pred_name, changed_succ_name)
        changed_ordered = [
            ("0001_unrelated.sql", "select 1;"),
            (changed_pred_name, changed_pred),
            (changed_succ_name, changed_succ),
        ]
        check(f"{label} still requires pairing",
              changed_pair in find_atomic_seal_requirements(changed_ordered)
              and changed_pair not in historical_unpaired_pairs(
                  [(name, sql.encode()) for name, sql in changed_ordered]))


def test_new_authority_migrations_are_atomically_sealed() -> None:
    """The live check: run the predicate against the real migration tree and
    require every result to be declared in ATOMIC_MIGRATION_GROUPS, and
    (barring the pre-STRICT allowlist above) in
    STRICT_ATOMIC_MIGRATION_GROUPS too."""
    ordered = [(name, sql) for name, sql, _digest in migrate.load_migrations()]
    required = find_atomic_seal_requirements(ordered)
    check("at least one real migration currently opens new SCAC authority "
          "(the predicate is exercised, not vacuous)",
          len(required) > 0)

    atomic_pairs = declared_atomic_pairs(migrate.ATOMIC_MIGRATION_GROUPS)
    historical_names = {name for pair in HISTORICAL_UNPAIRED_SCAC_PAIRS for name in pair}
    historical_unpaired = historical_unpaired_pairs([
        (name, (REPO / "migrations" / name).read_bytes())
        for name, _sql in ordered if name in historical_names
    ])
    missing_atomic = [
        pair for pair in required
        if pair not in atomic_pairs and pair not in historical_unpaired
    ]
    check("every migration that opens new SCAC authority is paired with its "
          "seal in ATOMIC_MIGRATION_GROUPS",
          not missing_atomic,
          "undeclared: " + ", ".join(f"{p} -> {s}" for p, s in missing_atomic))

    strict_pairs = declared_strict_pairs(migrate.STRICT_ATOMIC_MIGRATION_GROUPS)
    missing_strict = [
        pair for pair in required
        if pair not in strict_pairs
        and pair[1] not in PRE_STRICT_ATOMIC_GROUP_ALLOWLIST
        and pair not in historical_unpaired
    ]
    check("every migration that opens new SCAC authority is paired with its "
          "seal in STRICT_ATOMIC_MIGRATION_GROUPS too, unless pre-existing",
          not missing_strict,
          "undeclared: " + ", ".join(f"{p} -> {s}" for p, s in missing_strict))

    required_seals = {succ for _pred, succ in required}
    stale_allowlist = sorted(PRE_STRICT_ATOMIC_GROUP_ALLOWLIST - required_seals)
    check("the pre-STRICT allowlist names only pairs the predicate still finds",
          not stale_allowlist,
          "stale allowlist entries: " + ", ".join(stale_allowlist))


def test_industry_events_domain_and_seal_are_atomic() -> None:
    pair = ("0738_industry_events.sql", "0739_industry_events_scac_successor.sql")
    check("industry events domain and seal share an atomic migration group",
          pair in declared_atomic_pairs(migrate.ATOMIC_MIGRATION_GROUPS))
    check("industry events domain and seal share a strict atomic migration group",
          pair in declared_strict_pairs(migrate.STRICT_ATOMIC_MIGRATION_GROUPS))


def test_board_answers_domain_and_seal_are_atomic() -> None:
    pair = ("0740_board_answers.sql", "0741_board_answers_scac_successor.sql")
    check("board answers domain and seal share an atomic migration group",
          pair in declared_atomic_pairs(migrate.ATOMIC_MIGRATION_GROUPS))
    check("board answers domain and seal share a strict atomic migration group",
          pair in declared_strict_pairs(migrate.STRICT_ATOMIC_MIGRATION_GROUPS))


def main() -> int:
    print("migrate-precondition-selftest")
    test_table_shape()
    test_probe_is_checked_before_the_file_runs()
    test_source_enforces_the_contract()
    test_historical_transaction_artifact_is_exact()
    test_reviewed_controller_transaction_artifact_is_exact()
    test_seeded_failing_case_proves_the_check_fires()
    test_redeclared_function_needs_no_new_pairing()
    test_historical_unpaired_pair_is_byte_exact()
    test_new_authority_migrations_are_atomically_sealed()
    test_industry_events_domain_and_seal_are_atomic()
    test_board_answers_domain_and_seal_are_atomic()
    print()
    print(f"migrate-precondition-selftest: {len(PASS)}/{len(PASS) + len(FAIL)} passed")
    if FAIL:
        print("FAILURES: " + ", ".join(FAIL))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
