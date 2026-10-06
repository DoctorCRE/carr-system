#!/usr/bin/env python3
"""One-time parity: apply the two heavy-build migrations to the ISOLATED
STAGING project, exactly as production already ran them.

WHY (2026-08-26, heavy-build rollout). Production applied
0320_heavy_build_admission.sql and 0321_rule_delivery_policy_seed_repair.sql
through bin/migrate-prod.sh. The typed staging readback refuses the release
rehearsal until staging's ledger equals the declared candidate migration set,
so staging must carry the same two migrations. tools/staging-ledger-repair-0315.py
already reconciled the earlier ledger hole; this tool finishes the catch-up.

THE SAFETY PROPERTY, encoded rather than promised: staging may only receive
what production's ledger already records. The exact sha256 production recorded
for each file is EMBEDDED below, and the tool refuses unless the checked-in
migration bytes hash to exactly those values — so it can never carry staging
past production, never apply an edited file, and never apply anything else.
Like tools/ledger-repair.py and tools/staging-ledger-repair-0315.py it is
marker-verified, repairs exactly one named situation, and then does nothing
at all, forever.

WHY IT CANNOT TOUCH PRODUCTION. It refuses unless the target ledger shows the
exact staging state: 0315 through 0319 recorded, 0320/0321 absent, and zero
ops.heavy_build_* objects. Production records 0320/0321, so this tool refuses
there by construction. It prints the host either way.

Usage (through the documented staging door):
    .venv/bin/python tools/db-tap.py --project staging run \
        tools/staging-heavy-build-parity.py            # dry run
    CARR_BREAK_GLASS=1 .venv/bin/python tools/db-tap.py --project staging \
        --reason "heavy-build staging parity" run \
        tools/staging-heavy-build-parity.py --apply
"""

import hashlib
import os
import sys
from pathlib import Path
from typing import Any, NoReturn

import psycopg

MIGRATIONS = Path(__file__).resolve().parent.parent / "migrations"

# (filename, sha256 production recorded in its schema_migrations ledger when
# bin/migrate-prod.sh applied it on 2026-08-26 02:22 UTC). Nothing on trust:
# the checked-in file must still hash to exactly this before anything runs.
PRODUCTION_APPLIED = [
    ("0320_heavy_build_admission.sql",
     "f3db5160c88db372fd58fc8e73eaa2671c7c8997dd3bee2753d5b636cd074465"),
    ("0321_rule_delivery_policy_seed_repair.sql",
     "98494c4715a09ed735524a97f8bd3a1d0323df4adda690f5f06abcc6e971b482"),
]
PRIOR_APPLIED_REQUIRED = [
    "0315_program5_forward_fix_rehearsal.sql",
    "0316_rule_delivery_audit_counts.sql",
    "0317_atomic_rule_delivery_cutover.sql",
    "0318_tour_operations_foundation.sql",
    "0319_engineering_envelope_writer_successor.sql",
]


def fail(msg: str) -> NoReturn:
    sys.exit(f"staging-heavy-build-parity: {msg}")


def one(cur: Any) -> tuple[Any, ...]:
    row = cur.fetchone()
    if row is None:
        fail("query returned no row")
    return row


def main() -> None:
    apply = "--apply" in sys.argv
    url = os.environ.get("DATABASE_URL")
    if not url:
        fail("DATABASE_URL is not set")

    texts: dict[str, str] = {}
    for name, expected_sha in PRODUCTION_APPLIED:
        text = (MIGRATIONS / name).read_text()
        got = hashlib.sha256(text.encode()).hexdigest()
        if got != expected_sha:
            fail(f"{name} no longer hashes to what production ran "
                 f"(got {got[:12]}…, production recorded {expected_sha[:12]}…); refusing")
        texts[name] = text

    with psycopg.connect(url) as conn:
        cur = conn.cursor()
        print("host:", conn.info.host)

        cur.execute("select filename from schema_migrations")
        applied = {r[0] for r in cur.fetchall()}
        todo = [(n, s) for n, s in PRODUCTION_APPLIED if n not in applied]
        if not todo:
            print("both heavy-build migrations already recorded — nothing to do, ever.")
            return
        missing_prior = [n for n in PRIOR_APPLIED_REQUIRED if n not in applied]
        if missing_prior:
            fail("target does not show the reconciled staging state this tool expects "
                 f"(prior migrations not recorded: {', '.join(missing_prior)}); refusing")
        if len(todo) != len(PRODUCTION_APPLIED):
            fail("target has one of the pair but not the other — reconcile by hand, "
                 "not with this tool")

        cur.execute(
            "select count(*) from pg_proc p join pg_namespace n on n.oid=p.pronamespace "
            "where n.nspname='ops' and p.proname like %s", ("%heavy_build%",))
        if one(cur)[0] != 0:
            fail("marker failed: heavy_build objects already present while unrecorded; "
                 "reconcile by hand, not with this tool")

        print("staging state confirmed: reconciled prefix present, heavy-build pair absent")
        if not apply:
            print("dry run — pass --apply")
            return

        for name, sha in todo:
            cur.execute(texts[name])
            cur.execute(
                "insert into schema_migrations (filename, sha256) values (%s, %s)",
                (name, sha))
            conn.commit()
            print(f"applied and recorded {name}")

        # Verify each migration's own effects after commit, loudly.
        cur.execute(
            "select count(*) from pg_proc p join pg_namespace n on n.oid=p.pronamespace "
            "where n.nspname='ops' and p.proname like %s", ("%heavy_build%",))
        if one(cur)[0] == 0:
            fail("post-apply verification failed: heavy_build functions absent")
        cur.execute("select count(*) from ops.rule_delivery_policy")
        if one(cur)[0] != 1:
            fail("post-apply verification failed: rule_delivery_policy is not one row")
        cur.execute("select count(*) from ops.rule_delivery_activation_target")
        if one(cur)[0] != 9:
            fail("post-apply verification failed: activation targets are not nine rows")
        print("verified — staging now matches production's applied heavy-build set")


main()
