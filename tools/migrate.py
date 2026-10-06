#!/usr/bin/env python3
"""carr-system migrations runner (record layer, scaffolded 2026-07-30).

Applies migrations/NNNN_*.sql in filename order, normally one transaction per
file, tracked in schema_migrations. A small reviewed group may share one outer
transaction when an authority-surface migration and its registry successor
must become visible atomically. Forward-only by design: a bad migration is
fixed by a NEW migration, never by editing an applied file (applied files'
sha256 is recorded and re-checked, so drift is caught).

Usage:
    DATABASE_URL=postgres://... python3 tools/migrate.py            # dry run
    DATABASE_URL=postgres://... python3 tools/migrate.py --through 0170_guidance_import_lifecycle.sql
    DATABASE_URL=postgres://... python3 tools/migrate.py --apply    # apply, confirm host
    DATABASE_URL=postgres://... python3 tools/migrate.py --apply --yes --through 0170_guidance_import_lifecycle.sql

CREDENTIAL RULE (stress-test addendum A14): build sessions run against a
NEON BRANCH credential, never the production writer. Risky changes rehearse
on a branch of production data before touching production. This runner
cannot tell a branch URL from production, so it prints the host and makes
you confirm — read what it prints.

Requires psycopg (pip install 'psycopg[binary]'); listed in requirements.txt.
"""

import argparse
import hashlib
import os
import re
import sys
from pathlib import Path
from typing import NoReturn

from migration_number_contract import (
    LEGACY_APPLIED_ALIASES,
    MigrationNumberError,
    validate_migration_names,
)

# ── MIGRATIONS THAT BIND TO ONE PRODUCTION ROW AND ARE INERT ELSEWHERE ──────
#
# WHY THIS EXISTS (2026-08-22, defect 87da6fe5). 0248 registers the conduct-stop
# control and binds it to the autonomy rule, with an insert requiring that rule
# to exist as `proposed` and a proof block that raises when the insert matched
# nothing. That row exists in Production and nowhere else. So 0248 applied
# cleanly to Production and then failed on the isolated staging project, which
# stopped at 206 applied while Production reached 209.
#
# That is not a cosmetic gap. The typed staging readback compares staging's
# schema against a release candidate's exact declared migration set, so it could
# never match; the recovery rehearsal could not complete; and Production approval
# refuses without a rehearsal bundle. Every Production release was blocked, and
# the repository could no longer reconstruct a non-production environment, which
# is Program 1's rebuild clause.
#
# Every obvious repair is closed by design. The file cannot be edited —
# validate_applied_ledger records a sha256 per applied migration and refuses an
# edited one, correctly. It cannot be re-applied to Production either, since the
# rule is now `active` rather than `proposed`. A forward repair migration cannot
# help, because migrations run in filename order and any new number runs after
# it. The rename-alias table covers historical renames only.
#
# SO THE RUNNER LEARNS THE NARROWEST POSSIBLE FACT: this exact file, with this
# exact probe, has nothing to do on a database where the probe finds no row.
#
# THIS IS NOT A SKIP-ON-FAILURE DOOR, and the distinction is the whole design.
# A general "ignore migrations that error" would let any broken migration through.
# Each entry here names ONE file and ONE precondition query that must come back
# empty, checked BEFORE the file runs rather than after it fails — a migration
# that errors for any other reason still stops the run exactly as before. The
# entry also has to say why the file is inert without its row, and that reason
# has to be checkable rather than asserted: for 0248 it is that 0272 registers
# the conduct-stop control everywhere from the repository's own declarations, so
# the only thing 0248 adds beyond it is a binding to a rule that is absent.
#
# Adding an entry here is a deliberate, reviewed act, same as the alias table
# below it. If you are reaching for it to make a red migration green, stop: the
# question to answer first is whether the migration should depend on row data at
# all (rule a8c55a47 — a manual path and an automated path doing the same job
# must be the same code, and an environment nobody can rebuild is not the same
# code).
DATA_DEPENDENT_MIGRATIONS: dict[str, tuple[str, str]] = {
    "0248_register_conduct_stop_control.sql": (
        "select 1 from rule where id = '3fa422b7-7c99-49fc-8e22-1e551a975c6f'",
        "0272 registers the conduct-stop control from ops/config/rule-enforcement-map.json "
        "on every database; all 0248 adds here is a binding to the autonomy rule, "
        "which this database does not carry",
    ),
}

# ── MIGRATIONS WHOSE INTERMEDIATE CATALOG MUST NEVER COMMIT ────────────────
#
# 0480 adds the Codex continuity writer surface. That correctly makes the
# sealed SCAC v10 live catalog cease to be current; 0481 installs and activates
# the exact v11 successor. ops.scac_policy_epoch_refresh() is a DEFERRABLE
# constraint trigger on schema_migrations, so committing 0480 by itself asks
# the old v10 snapshot to bless the intentionally-new surface and must fail.
# The two reviewed files therefore share ONE runner-owned transaction: both
# SQL bodies and both immutable ledger rows commit together, and the deferred
# trigger observes only the final v11 state. A caller may not cut this group in
# half with --through.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ops"))
from registry_chain import registry_chain

REGISTRY_CHAIN = registry_chain()
ATOMIC_MIGRATION_GROUPS = tuple(tuple(group) for group in REGISTRY_CHAIN["atomic_groups"])
STRICT_ATOMIC_MIGRATION_GROUPS = tuple(tuple(group) for group in REGISTRY_CHAIN["strict_atomic_groups"])


FORBIDDEN_MIGRATION_FILENAMES = frozenset({
    "0535_ready_plan_amendment.sql",
    "0536_ready_plan_amendment_scac_successor.sql",
})

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"
# NNNN_name.sql, plus an OPTIONAL single lowercase letter after the number:
# 0013a_name.sql. Widened 2026-08-13 for a defect that could not be fixed inside
# the old contract.
#
# THE NEED. Applying every migration to an empty database died at 0014, which
# asserts fourteen client_status rows are flagged and found three: eleven slugs
# entered production out of band and no migration creates them. The fix has to
# run BEFORE 0014 on a fresh database, and 0013 and 0014 are consecutive
# integers, so there is no number between them. Appending at the end would not
# help — on an empty database it would still run after the migration it needs to
# precede. This is the ordinary reason migration tools use timestamps or decimal
# numbering; a single letter is the smallest change that buys the same thing.
#
# WHY IT IS SAFE. The regex only WIDENS what is accepted, so every existing
# filename still matches and nothing about already-applied migrations changes.
# Ordering is unaffected: Python sorts 0013_ < 0013a_ < 0014_ ('_' is 0x5F and
# 'a' is 0x61, then '3' < '4'), which is exactly the order the fix needs. And
# migrate.py is the ONLY parser of the filename shape — v_schema_ledger (0113)
# and mcp-server/src/release.js store and display the string without extracting
# a number from it, so widening here cannot desync a second reader.
NAME_RE = re.compile(r"^\d{4}[a-z]?_[a-z0-9_]+\.sql$")
OUTER_TRANSACTION_MIGRATION = "0339_"
# Production's immutable ledger and the canonical schema snapshot already bind
# these historical artifacts byte-for-byte. They predate enforcement of the
# outer-transaction scanner on their merge lanes; allow only their exact
# recorded digests so the scanner cannot become a general bypass.
HISTORICAL_TRANSACTION_CONTROL_ARTIFACTS = {
    "0344_demote_evidence_activation_bookkeeping.sql":
        "50e2b885db6b92e0a24f0a90fad7b48449f347007d8683c27adf0a4be1a75def",
    "0345_governance_queue_projection.sql":
        "c1788dd8ee23d7a7a6dfc88b17d4a11c67a9ad5057c8bb97e3112035863be3ba",
    "0348_pr_only_main_ruleset_control.sql":
        "ab901c8e528109bb56375403f0ebb350758678079d7727c9ba24042b0d0bbcdb",
    "0349_versioned_rule_amendment.sql":
        "4fb76045cf8ce46ba793a90d0582e6b095a17ba63edf57e91c548e7850ba37f0",
    "0351_legacy_rule_lifecycle_admission.sql":
        "59439e1a12c035e61578b85c765d7bbd131bf555b95bbecd49c6d675b5c4d808",
}
# Reviewed migrations merged after the original SIEP branch was cut deliberately
# keep explicit transactions around atomic control-plane changes and receipt
# readbacks. Preserve those independently reviewed source artifacts exactly
# rather than rewriting them during SIEP integration. These are separate from
# the five Production-applied historical artifacts above: an unreviewed filename
# or one-byte change still refuses before any SQL executes.
REVIEWED_TRANSACTION_CONTROL_ARTIFACTS = {
    "0363_rule_delivery_activation_digest_repin.sql":
        "03133d0627cf63d2a0a2a7dd8a392065bc19ba17d56d0b2cfabd3dbccafdcb65",
    "0382_standing_guidance_reader_boundary.sql":
        "a6ffe5f29e9224f263b0c6a90c414b4828915a5ed3265e52e8fadbe31ef8c2bc",
    "0383_control_plane_not_configured_state.sql":
        "f0cb86f97fcd87db8412be1f4c36544fe40f1ba9e524182bb3cb3b9ad3148bfa",
    "0387_control_plane_record_queue_priority_tiers.sql":
        "ba2f9ce18e54f8ceca330a5478ad66d72b76bbc832aba9c20734ebe8a701310e",
    "0425_disable_legacy_schedule_readback_grant.sql":
        "f1b0f6677363c3a0463a30660b379544b9a7093867c8847c3453b149da17aaed",
    "0426_withdraw_a_work_request_captured_in_error.sql":
        "151eddaae36b60fd1a6f0ad43f9577c03381ebd11b17b9a9741269d93bd2d395",
    "0427_tour_rights_projection_hardening.sql":
        "00dd241ccf86bf379cc20aec22dfd0b852754bc30224bae39cb707d8e66729a2",
    "0428_tour_property_identity_jurisdiction.sql":
        "3c32933288ecf780ee3ea54bffeddb1df0a22bdf862c1487eea0f021bd682975",
    "0429_tour_domain_route_cheat_sheet.sql":
        "6b217caf48ce0742045a1d3093c5bd85727a4511dabe4e739d9d271a61bcc8e4",
    "0430_tour_delivery_data_plane.sql":
        "f04d685a6ae2ba124694ff11f6d88695bed25774290e6b2997c59ad9fd9049be",
    "0431_completion_register_schema.sql":
        "7886498e34f7874aa1f1ac2df931aefbe036eb73e013eb4e81fd02112f145f70",
    "0450_canonical_ownership_lease_kernel.sql":
        "2130de773f09f5dd8621cfe5add3f8939ddd1d48f06c5d9a6908e19375a57847",
    "0451_assurance_evidence_acceptance_persistence.sql":
        "f17f538bafd602c9d90b3b46fe3cc377b746b03b1d5fe070a5fb597af4d2013c",
}
TRANSACTION_CONTROL_RE = re.compile(
    r"(?is)^\s*(?:(?:begin|commit|end|rollback|abort)\b|"
    r"(?:start|prepare)\s+transaction\b)"
)


def contains_transaction_control(sql: str) -> bool:
    """Detect top-level SQL transaction statements, ignoring quoted bodies.

    Migrations contain PL/pgSQL ``begin``/``end`` inside dollar-quoted bodies;
    those are not transaction control.  Conversely, psycopg accepts several
    top-level statements on one line, so a line-oriented regex is unsafe.
    This small lexer splits only on top-level semicolons after removing SQL
    comments and quoted strings/identifiers.
    """
    statements: list[str] = []
    current: list[str] = []
    i = 0
    block_depth = 0
    quote: str | None = None
    dollar_tag: str | None = None
    while i < len(sql):
        if dollar_tag is not None:
            if sql.startswith(dollar_tag, i):
                i += len(dollar_tag)
                dollar_tag = None
            else:
                i += 1
            continue
        if block_depth:
            if sql.startswith("/*", i):
                block_depth += 1
                i += 2
            elif sql.startswith("*/", i):
                block_depth -= 1
                i += 2
            else:
                i += 1
            continue
        if quote is not None:
            if sql[i] == quote:
                if i + 1 < len(sql) and sql[i + 1] == quote:
                    i += 2
                else:
                    quote = None
                    i += 1
            else:
                i += 1
            continue
        if sql.startswith("--", i):
            newline = sql.find("\n", i + 2)
            i = len(sql) if newline < 0 else newline + 1
            current.append(" ")
            continue
        if sql.startswith("/*", i):
            block_depth = 1
            i += 2
            current.append(" ")
            continue
        if sql[i] in ("'", '"'):
            quote = sql[i]
            i += 1
            current.append(" ")
            continue
        if sql[i] == "$":
            match = re.match(r"\$[A-Za-z_][A-Za-z0-9_]*\$|\$\$", sql[i:])
            if match:
                dollar_tag = match.group(0)
                i += len(dollar_tag)
                current.append(" ")
                continue
        if sql[i] == ";":
            statements.append("".join(current))
            current = []
            i += 1
            continue
        current.append(sql[i])
        i += 1
    statements.append("".join(current))
    return any(TRANSACTION_CONTROL_RE.match(statement) for statement in statements)

# ── DDL TIMEOUTS (added 2026-08-02, cold-session audit) ──────────────────────
# WHY. Migrations are applied by hand against production Neon while a Cloudflare
# Worker holds live connections. An ALTER TABLE needs an ACCESS EXCLUSIVE lock,
# and Postgres queues lock requests: if the ALTER lands behind a long-running
# read it waits — and every query that arrives after it, including trivial ones
# that would not otherwise conflict, queues behind the ALTER. A change that
# takes two milliseconds of actual work can therefore stall the whole API for as
# long as one unrelated slow read runs. Without lock_timeout the runner waits
# for ever and looks like it is "still applying".
#
# 5 SECONDS, because the failure mode we are avoiding IS the wait. Every DDL in
# migrations/ is a catalog change on a database of ~67 tables and ~17k rows;
# none of them needs five seconds to ACQUIRE a lock, so anything that does is
# blocked rather than busy. Failing fast costs a re-run; waiting costs an
# outage. This is the standard online-migration posture (gitlab, strong_migrations
# and friends all sit in the 50ms–5s band); 5s is the forgiving end of it,
# chosen because a human is watching this run and a spurious abort wastes their
# attention.
#
# statement_timeout is the second half and a much blunter tool: it bounds how
# long a migration may HOLD a lock once it has one. Set too low it kills
# legitimate backfills, so it is deliberately generous at 5 minutes — roughly
# two orders of magnitude more than anything in migrations/ has ever needed on
# this data, while still guaranteeing a runaway statement cannot pin the API
# indefinitely.
#
# BOTH ARE OVERRIDABLE, because the day someone writes a genuine long backfill
# they should raise the ceiling consciously rather than delete this block:
#   CARR_MIGRATE_LOCK_TIMEOUT=30s CARR_MIGRATE_STATEMENT_TIMEOUT=30min
# A migration may also override either one for itself with `set local ...` as
# its first statement; SET LOCAL inside the transaction wins over the session
# value set here, and reverts at commit.
LOCK_TIMEOUT = os.environ.get("CARR_MIGRATE_LOCK_TIMEOUT", "5s")
STATEMENT_TIMEOUT = os.environ.get("CARR_MIGRATE_STATEMENT_TIMEOUT", "5min")

BOOTSTRAP = """
create table if not exists schema_migrations (
  filename   text primary key,
  sha256     text not null,
  applied_at timestamptz not null default now()
);
"""


def fail(msg: str) -> NoReturn:
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


def load_migrations() -> list[tuple[str, str, str]]:
    """Return [(filename, sql, sha256)] sorted by filename."""
    if not MIGRATIONS_DIR.is_dir():
        fail(f"no migrations directory at {MIGRATIONS_DIR}")
    out: list[tuple[str, str, str]] = []
    for p in sorted(MIGRATIONS_DIR.iterdir()):
        if p.suffix == ".sql":
            if p.name in FORBIDDEN_MIGRATION_FILENAMES:
                fail(f"stale WR122 migration filename is permanently refused: {p.name}")
            if not NAME_RE.match(p.name):
                fail(f"bad migration filename (want NNNN_name.sql): {p.name}")
            sql = p.read_text()
            digest = hashlib.sha256(sql.encode()).hexdigest()
            # SIEP-12 makes the migration file, its immutable ledger row, the
            # sealed mutation-registry successor, and the resulting policy
            # epoch one transaction. An internal COMMIT would expose schema
            # with the old epoch before the runner records the file hash.
            reviewed_transaction_digest = (
                HISTORICAL_TRANSACTION_CONTROL_ARTIFACTS.get(p.name)
                or REVIEWED_TRANSACTION_CONTROL_ARTIFACTS.get(p.name)
            )
            if p.name >= OUTER_TRANSACTION_MIGRATION and contains_transaction_control(sql) \
                    and reviewed_transaction_digest != digest:
                fail(
                    f"{p.name} contains explicit transaction control; migrations from "
                    f"{OUTER_TRANSACTION_MIGRATION} onward must use the runner's single transaction"
                )
            out.append((p.name, sql, digest))
    if not out:
        fail("no .sql files in migrations/")
    try:
        validate_migration_names(
            (name for name, _sql, _digest in out), require_frozen=True
        )
    except MigrationNumberError as exc:
        fail(str(exc))
    return out


def pending_migrations(
    migrations: list[tuple[str, str, str]], applied: dict[str, str]
) -> list[tuple[str, str, str]]:
    """Return files absent from the filename-keyed ledger, preserving order."""
    return [(name, sql, digest) for name, sql, digest in migrations if name not in applied]


def migrations_through(
    migrations: list[tuple[str, str, str]],
    pending: list[tuple[str, str, str]],
    through: str | None,
) -> tuple[list[tuple[str, str, str]], list[tuple[str, str, str]]]:
    """Split pending migrations into the authorized prefix and held-back tail.

    A production activation may need an already-reviewed migration without also
    taking every newer, unrelated file that merged while the activation was
    waiting.  The boundary is an exact checked-in filename, and selection is
    still the ordinary forward-only filename prefix: no gap or hand-picked
    dependency can be skipped.
    """
    if through is None:
        return pending, []
    names = [name for name, _sql, _digest in migrations]
    if through not in names:
        raise ValueError(
            f"--through target is not an exact checked-in migration filename: {through}"
        )
    selected = [item for item in pending if item[0] <= through]
    held_back = [item for item in pending if item[0] > through]
    selected_names = {item[0] for item in selected}
    held_names = {item[0] for item in held_back}
    for group in ATOMIC_MIGRATION_GROUPS:
        if selected_names.intersection(group) and held_names.intersection(group):
            raise ValueError(
                "--through target cuts reviewed atomic migration group: "
                + ", ".join(group)
                + ". Select through the final file so the intermediate authority "
                  "catalog can never commit."
            )
    return selected, held_back


def migration_batches(
    pending: list[tuple[str, str, str]],
) -> list[list[tuple[str, str, str]]]:
    """Return ordered transaction batches for an already-authorized prefix.

    A complete pending reviewed group is one batch. A suffix whose earlier
    member is already in the immutable ledger remains individually resumable;
    this is the recovery shape for a database that predated the group rule.
    """
    by_first = {group[0]: group for group in ATOMIC_MIGRATION_GROUPS}
    batches: list[list[tuple[str, str, str]]] = []
    i = 0
    while i < len(pending):
        group = by_first.get(pending[i][0])
        if group is not None:
            candidate = pending[i:i + len(group)]
            if tuple(item[0] for item in candidate) == group:
                batches.append(candidate)
                i += len(group)
                continue
        batches.append([pending[i]])
        i += 1
    return batches


class AppliedMigrationLedgerError(ValueError):
    """The database ledger cannot be reconciled to immutable files in the tree."""


def validate_applied_ledger(
    migrations: list[tuple[str, str, str]], applied: dict[str, str]
) -> None:
    """Refuse missing, edited, or reordered ledger state.

    The effective applied set accounts for the exact historical rename aliases,
    then must be an uninterrupted prefix of the current migration tree.  A
    later applied row after any earlier hole is not a harmless partial deploy:
    applying the hole now would reorder history and may violate dependencies.
    """
    current = {name: digest for name, _sql, digest in migrations}
    missing = sorted(set(applied) - set(current))
    unknown_missing = [name for name in missing if name not in LEGACY_APPLIED_ALIASES]
    if unknown_missing:
        raise AppliedMigrationLedgerError(
            "applied migration(s) missing from the tree: " + ", ".join(unknown_missing)
            + ". Restore the exact files; do not rename or delete applied migrations."
        )
    absent_targets = sorted({
        LEGACY_APPLIED_ALIASES[name]
        for name in missing
        if LEGACY_APPLIED_ALIASES[name] not in current
    })
    if absent_targets:
        raise AppliedMigrationLedgerError(
            "legacy applied migration alias target(s) missing from the tree: "
            + ", ".join(absent_targets)
        )
    for name, digest in current.items():
        if name in applied and applied[name] != digest:
            raise AppliedMigrationLedgerError(
                f"{name} was EDITED after being applied (sha mismatch). Write a new "
                "migration instead; never rewrite an applied one."
            )
    effective_applied = set(applied) & set(current)
    effective_applied.update(
        LEGACY_APPLIED_ALIASES[name]
        for name in applied
        if name in LEGACY_APPLIED_ALIASES
    )
    for group in STRICT_ATOMIC_MIGRATION_GROUPS:
        present = tuple(name for name in group if name in effective_applied)
        if present and present != group:
            raise AppliedMigrationLedgerError(
                "partial strict atomic migration group is forbidden: expected "
                + ", ".join(group) + "; found " + ", ".join(present)
            )
    first_hole: str | None = None
    later_applied: list[str] = []
    for name, _sql, _digest in migrations:
        if name not in effective_applied:
            first_hole = first_hole or name
        elif first_hole is not None:
            later_applied.append(name)
    if later_applied:
        raise AppliedMigrationLedgerError(
            "migration ledger is reordered: earlier migration is pending "
            f"({first_hole}) while later migration(s) are already applied: "
            + ", ".join(later_applied)
            + ". Stop; reconcile dependency history before applying anything."
        )


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true", help="apply pending migrations")
    ap.add_argument("--yes", action="store_true", help="skip the host confirmation")
    ap.add_argument(
        "--through",
        metavar="FILENAME",
        help="consider only the forward migration prefix through this exact checked-in filename",
    )
    args = ap.parse_args()

    url = os.environ.get("DATABASE_URL")
    if not url:
        fail("DATABASE_URL is not set. Use a Neon BRANCH credential for build work (A14).")

    try:
        import psycopg
    except ImportError:
        fail("psycopg not installed: pip install 'psycopg[binary]'")

    migrations = load_migrations()

    with psycopg.connect(url) as conn:
        host = conn.info.host
        with conn.cursor() as cur:
            cur.execute(BOOTSTRAP)
            conn.commit()
            cur.execute("select filename, sha256 from schema_migrations")
            applied: dict[str, str] = dict(cur.fetchall())

        try:
            validate_applied_ledger(migrations, applied)
        except AppliedMigrationLedgerError as exc:
            fail(str(exc))

        all_pending = pending_migrations(migrations, applied)
        try:
            pending, held_back = migrations_through(
                migrations, all_pending, args.through
            )
        except ValueError as exc:
            fail(str(exc))
        print(f"host: {host}")
        print(f"applied: {len(applied)}   pending: {len(all_pending)}")
        if args.through:
            print(
                f"authorized prefix: through {args.through}   "
                f"selected: {len(pending)}   held back: {len(held_back)}"
            )
        for name, _s, _d in pending:
            print(f"  pending: {name}")
        for name, _s, _d in held_back:
            print(f"  held back: {name}")
        if not pending:
            print("nothing to do in authorized prefix")
            return
        if not args.apply:
            print("dry run — pass --apply to run these")
            return
        if not args.yes:
            # END THE READ TRANSACTION BEFORE ASKING A HUMAN ANYTHING.
            # Reading schema_migrations above opened a transaction (psycopg is
            # not autocommit), and the prompt below asks the operator to type a
            # ~58-character hostname. That leaves the session idle IN a
            # transaction for however long they take, and Neon enforces
            # idle_in_transaction_session_timeout. On 2026-08-07 the first
            # production run of 0074 died with IdleInTransactionSessionTimeout
            # on the very first `set local` after the confirmation, having
            # applied nothing. An idle session is fine; idle-in-transaction is
            # not. A rehearsal cannot catch this, because rehearsals pass --yes
            # and --yes is precisely the path that never waits.
            conn.rollback()
            answer = input(f"Apply {len(pending)} migration(s) to host '{host}'? "
                           "Type the host name to confirm: ").strip()
            if answer != host:
                fail("confirmation did not match host; nothing applied")

        print(f"lock_timeout: {LOCK_TIMEOUT}   statement_timeout: {STATEMENT_TIMEOUT}")
        for batch in migration_batches(pending):
            staged: list[tuple[str, str]] = []
            with conn.cursor() as cur:
                for name, sql, digest in batch:
                    precondition = DATA_DEPENDENT_MIGRATIONS.get(name)
                    if precondition is not None:
                        probe, inert_because = precondition
                        cur.execute(probe)
                        if cur.fetchone() is None:
                            # RECORDED AS DISCHARGED, NOT SILENTLY SKIPPED. The
                            # row it binds does not exist here, so the file has
                            # nothing to do and its own proof would raise.
                            cur.execute(
                                "insert into schema_migrations (filename, sha256) values (%s, %s)",
                                (name, digest),
                            )
                            staged.append((
                                name,
                                f"discharged (precondition absent) — {inert_because}",
                            ))
                            continue
                    print(f"applying {name} ...", end=" ", flush=True)
                    # SET LOCAL is scoped to the current batch transaction. It
                    # is re-issued per file so a migration cannot disarm the
                    # guard for its successor inside an atomic group.
                    cur.execute(f"set local lock_timeout = '{LOCK_TIMEOUT}'")
                    cur.execute(f"set local statement_timeout = '{STATEMENT_TIMEOUT}'")
                    try:
                        cur.execute(sql)
                    except psycopg.errors.LockNotAvailable:
                        conn.rollback()
                        fail(f"{name} could not acquire its lock within {LOCK_TIMEOUT} and was "
                             "ABANDONED (its whole transaction batch was rolled back).\n"
                             "  This is the guard working, not a broken migration. Something else "
                             "is holding a lock on the tables it touches — usually a long-running "
                             "read from the Worker.\n"
                             "  Check pg_stat_activity for the blocker, then just re-run: earlier "
                             "batches are already committed and will be skipped.\n"
                             f"  To wait longer on purpose: CARR_MIGRATE_LOCK_TIMEOUT=30s")
                    except psycopg.errors.QueryCanceled:
                        conn.rollback()
                        fail(f"{name} exceeded statement_timeout ({STATEMENT_TIMEOUT}) and was "
                             "ABANDONED (its whole transaction batch was rolled back).\n"
                             "  If this migration genuinely needs longer, raise the ceiling "
                             "deliberately rather than removing it:\n"
                             f"  CARR_MIGRATE_STATEMENT_TIMEOUT=30min python3 tools/migrate.py --apply")
                    cur.execute(
                        "insert into schema_migrations (filename, sha256) values (%s, %s)",
                        (name, digest),
                    )
                    staged.append((name, "ok"))
                    if len(batch) > 1:
                        print("staged")
            try:
                conn.commit()
            except psycopg.Error as exc:
                conn.rollback()
                names = ", ".join(name for name, _sql, _digest in batch)
                fail(
                    f"commit refused for migration batch [{names}] and the whole batch was "
                    f"rolled back: {exc}"
                )
            for _name, outcome in staged:
                if len(batch) == 1 and outcome == "ok":
                    print("ok")
                elif outcome != "ok":
                    print(outcome)
            if len(batch) > 1:
                print("atomic migration group committed: "
                      + ", ".join(name for name, _sql, _digest in batch))
        print("done")


if __name__ == "__main__":
    main()
