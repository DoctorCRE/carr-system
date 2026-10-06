#!/usr/bin/env python3
"""Prove the delivery tags actually install and actually select, on a disposable
PostgreSQL that carries the committed schema and every migration.

WHY AN ACCEPTANCE RUN AND NOT ONLY A SELFTEST. ops/rule-load-layer-check.py
proves the reviewed map is internally deliverable. It cannot prove that the
backfill writes what the map says, that the constraints refuse what the check
refuses, or that ops.rule_delivery_plan hands a session the set the council
designed. Those are properties of the DATABASE, and the only honest way to know
them is to run them against one (rule 937252fb: exercise the whole path before
saying a capability is live).

WHAT IT SEEDS. One active rule per id in the reviewed map, with a uuid whose
first eight characters ARE that short id, because that is how every surface in
this system derives a short id. No rule text is invented beyond a marker
statement: this proves plumbing, never doctrine.

    CARR_LOCAL_PG_DSN=postgresql://... ops/rule-delivery-local-pg-acceptance.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lib.rule_delivery_activation import EXPECTED_IDS, load_validated  # noqa:E402

REPO = Path(__file__).resolve().parent.parent
MAP = REPO / "ops" / "config" / "rule-enforcement-map.json"
PY = REPO / ".venv" / "bin" / "python"
MIGRATION_0363 = REPO / "migrations" / "0363_rule_delivery_activation_digest_repin.sql"
RETIREMENT_REPIN = REPO / "migrations" / "0837_repin_rule_delivery_activation_after_control_retirement.sql"
PRIOR_ACTIVATION_DIGEST = "4038e097f571f73499aee79b8c9e7b5bd3cea4ca0ba0f3847873e2f720106218"
CURRENT_ACTIVATION_DIGEST = "f7bf5726d329dd240434e51f7401fac9a977a3fb710636738f379f60f565f904"
# The chain has a fourth link as of 2026-09-01. Reinstating canonical_edit into
# control_catalog (Joe's ruling 7f48abf6, Repo Hygiene Program R02) moved the
# rule-enforcement-map digest, so the REGENERATED 0471 carries the generator's
# designed, guarded rule-map repin and the eight targets end on this value.
# NAMED FOR 0471, NOT 0474: 0474 reinstates the catalog control, but it is
# 0471's repin block that actually moves ops.rule_delivery_activation_target.
# PRIOR (pre-0363) and CURRENT (post-0363) are deliberately untouched -- 0363
# still produces CURRENT, and its assertions below must keep expecting it.
POST_0471_ACTIVATION_DIGEST = "6d21c37d533a5d98debfe4991c902164cf3c1fee88e7f42a3112468268e3335c"
# A FIFTH LINK as of 2026-09-03. Tagging rule 1fcaa63a (heavy-build-protocol)
# into the reviewed map moved its digest again. 0471 is a sealed artifact inside
# the 0454-0471 generation chain and cannot be rewritten to follow, so
# migrations/0478 repins the eight targets forward -- the same shape 0363 -> 0471
# already used. POST_0471 above is deliberately NOT bumped: it is the value 0471
# actually writes, and this fixture reconstructs the real sequence rather than
# jumping to the end state.
POST_0478_ACTIVATION_DIGEST = "eebfa2d627dfbbc65ae06e623724487158b940c9376cd30dbb067aec2779e8bb"
# A SIXTH LINK as of 2026-09-05. Activating the verb-creation and isolated-tree
# rules moves the reviewed map digest without changing any of the eight pack
# cutover targets. Migration 0483 carries that guarded forward repin.
POST_0483_ACTIVATION_DIGEST = "784e05273341f5f7c16f96d1f0fb1516d8c605cb3287dec32aa37a1211dd0cb8"
POST_0554_ACTIVATION_DIGEST = "c6e89d64de575b9c6e39c8c88cd6a32e97e494b381a7ac4433026c4a3fe63c2a"
# EIGHTH LINK as of 2026-09-29. Rule ede4b241 (cloud model choice) entering the
# reviewed map moved its digest; migration 0772 carries the guarded forward repin.
POST_0772_ACTIVATION_DIGEST = "43ac7f513c173114b1723a886baf56a83ec40e7fef8b187ed3dead7d16d90ada"
ACTIVATION_TO_TEST_REF = (
    "ops/rule-pack-drift-gate-selftest.py; ops/rule-load-layer-check-selftest.py; "
    "ops/rule-pack-preuse-reselection-selftest.py"
)
RETIRED_ACTIVATION_ID = "581cb3fe"

FAILURES: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    if not ok:
        FAILURES.append(f"{label}{': ' + detail if detail else ''}")


def one(cur) -> tuple:
    """fetchone() that refuses None. A count query that returned no row at all is
    a broken query, not a zero, and reading [0] off None hides which it was."""
    row = cur.fetchone()
    if row is None:
        raise RuntimeError("a count query returned no row; the acceptance cannot judge that")
    return row


def run(script: str, dsn: str) -> subprocess.CompletedProcess:
    return subprocess.run([str(PY if PY.is_file() else sys.executable), str(REPO / script)],
                          env={**os.environ, "DATABASE_URL": dsn},
                          capture_output=True, text=True, cwd=REPO)


def uuid_for(short: str, tail: int = 1) -> str:
    return f"{short}-0000-4000-8000-{tail:012d}"


def cutover_as(cur, role: str, mode: str, reason: str, digest: str) -> tuple:
    cur.execute(f"set session authorization {role}")
    try:
        cur.execute("select * from ops.set_rule_delivery_mode(%s,%s,%s)",
                    (mode, reason, digest))
        row = one(cur)
    finally:
        cur.execute("reset session authorization")
    return row


def first_do_block(source: str) -> str:
    start = source.index("do $$")
    end = source.index("end $$;", start) + len("end $$;")
    return source[start:end]


def main() -> int:
    dsn = os.environ.get("CARR_LOCAL_PG_DSN")
    if not dsn:
        print("rule-delivery-acceptance: CARR_LOCAL_PG_DSN required", file=sys.stderr)
        return 78
    data = json.loads(MAP.read_text())
    _, overlay = load_validated()
    current_map_digest = overlay["base_map_sha256"]
    layers = data["rule_load_layers"]
    scope_by_id = {rid: scope for scope, ids in data["active_rule_ids"].items() for rid in ids}
    # Scope is durable rule state, not reviewed-map configuration. Deliberately
    # make one map-shared rule Dell-personal to prove the compiler reads
    # rule.personal_to and the audit checks the installed result.
    store_scope_by_id = dict(scope_by_id)
    synthetic_dell = next(rid for rid in data["active_rule_ids"]["shared"]
                          if rid not in EXPECTED_IDS)
    store_scope_by_id[synthetic_dell] = "dell"

    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        # Observe the actual post-migration state before this acceptance mutates
        # any fixture row. The full committed migration chain must reach the
        # current reviewed overlay, not merely the last historical fixture.
        cur.execute(
            """select count(*),
                      count(*) filter (where map_digest=%s),
                      count(*) filter (where short_id=any(%s))
                 from ops.rule_delivery_activation_target""",
            (current_map_digest, sorted(EXPECTED_IDS)),
        )
        post_migrate = one(cur)
        check("the real post-migrate state is the exact eight on the current map",
              post_migrate == (len(EXPECTED_IDS), len(EXPECTED_IDS), len(EXPECTED_IDS)),
              str(post_migrate))

        cur.execute("""do $$ begin
          if not exists(select 1 from pg_roles where rolname='carr_authority_joe') then
            create role carr_authority_joe login;
          end if;
          if not exists(select 1 from pg_roles where rolname='carr_authority_dell') then
            create role carr_authority_dell login;
          end if;
        end $$""")
        cur.execute("grant carr_authority to carr_authority_joe,carr_authority_dell")
        cur.execute(
            """insert into ops.rule_delivery_activation_target
                 (short_id,expected_scope,expected_pack,
                  from_control,from_enforcement_class,from_implementation_ref,
                  from_test_ref,to_control,to_enforcement_class,
                  to_implementation_ref,to_test_ref,map_digest)
               values
                 (%s,'shared','delegation-council',
                  'session_boot','surfacing',
                  'hooks/session-brief.py; hooks/machine-converge.py; mcp-server/src/mcp.js',
                  'command:python3 hooks/gate-integrity.py --selftest',
                  'pack_delivery','stop_gate',
                  'hooks/rule-pack-drift-gate.py; hooks/rule-pack-preuse-reselection.py',
                  %s,%s)""",
            (RETIRED_ACTIVATION_ID, ACTIVATION_TO_TEST_REF, PRIOR_ACTIVATION_DIGEST),
        )
        check("the pre-0363 fixture restores the exact retired ninth target",
              cur.rowcount == 1)
        migration_0363 = MIGRATION_0363.read_text(encoding="utf-8")
        transition_0363 = first_do_block(migration_0363)
        activation_victim = sorted(EXPECTED_IDS)[0]
        cur.execute(
            "update ops.rule_delivery_activation_target set map_digest=%s",
            (PRIOR_ACTIVATION_DIGEST,),
        )
        cur.execute("""insert into actor (slug,kind,display_name)
                       values ('joe','human','Joe') on conflict (slug) do nothing""")
        cur.execute("select id from actor where slug='joe'")
        guard_joe = one(cur)[0]

        # The migration and cutover share the singleton policy row as their
        # first serialization token. Holding it must prevent even the first
        # transition block from reaching the target rows.
        with psycopg.connect(dsn) as lock_conn, lock_conn.cursor() as lock_cur:
            lock_cur.execute("""select mode from ops.rule_delivery_policy
                                where singleton for update""")
            with psycopg.connect(dsn) as contender, contender.cursor() as contender_cur:
                contender_cur.execute("set local statement_timeout='400ms'")
                try:
                    contender_cur.execute(transition_0363, prepare=False)
                    check("0363 waits behind the cutover policy lock", False,
                          "transition passed a held policy row")
                except psycopg.errors.QueryCanceled:
                    check("0363 waits behind the cutover policy lock", True)
                finally:
                    contender.rollback()
            lock_conn.rollback()
        cur.execute("""select count(*) from ops.rule_delivery_activation_target
                        where map_digest=%s""", (PRIOR_ACTIVATION_DIGEST,))
        check("the blocked 0363 transition changes no target",
              one(cur)[0] == len(EXPECTED_IDS) + 1)

        # Reproduce the dangerous restore state: the retired short id still has
        # an active rule but no retirement receipt. The disposable owner bypass
        # is scoped to fixture insertion only; Production triggers remain live.
        retired_rule_id = uuid_for(RETIRED_ACTIVATION_ID)
        successor_rule_id = uuid_for("aa411351")
        cur.execute("alter table rule disable trigger user")
        try:
            cur.execute("""insert into rule
                         (id,statement,taught_by,status,activated_by,activated_at)
                         values (%s,'pre-retirement restore fixture',%s,'active',%s,now())""",
                        (retired_rule_id, guard_joe, guard_joe))
        finally:
            cur.execute("alter table rule enable trigger user")
        try:
            cur.execute(transition_0363, prepare=False)
            check("0363 refuses an active retired-target rule", False,
                  "transition accepted an active unreceipted rule")
        except psycopg.Error as exc:
            check("0363 refuses an active retired-target rule",
                  "not absent or exactly retired to aa411351" in str(exc),
                  str(exc).splitlines()[0])
        conn.rollback()
        cur.execute("delete from rule where id=%s", (retired_rule_id,))

        # The other permitted state is the exact immutable lifecycle receipt
        # plus its matching Joe authority receipt and named successor.
        with psycopg.connect(dsn) as receipt_conn, receipt_conn.cursor() as receipt_cur:
            retired_at = "2026-08-27T00:00:00Z"
            activated_at = "2000-01-01T00:00:00Z"
            receipt_cur.execute("""insert into rule (id,statement,taught_by,status)
                                   values (%s,'retirement successor fixture',%s,'proposed')""",
                                (successor_rule_id, guard_joe))
            receipt_cur.execute("""insert into rule
                       (id,statement,taught_by,status,version,activated_by,activated_at,
                        retired_by,retired_at)
                       values (%s,'receipted retired fixture',%s,'retired',2,%s,%s,%s,%s)""",
                                (retired_rule_id, guard_joe, guard_joe, activated_at,
                                 guard_joe, retired_at))
            receipt_cur.execute(
                """select ops.legacy_rule_admission_note(%s,'active',%s::timestamptz)""",
                (retired_rule_id, activated_at),
            )
            legacy_admission = one(receipt_cur)[0]
            check("the retirement fixture has the canonical legacy admission",
                  legacy_admission is not None)
            receipt_cur.execute(
                """select encode(public.digest(jsonb_build_object(
                       'rule_id',%s::uuid,
                       'rule_version_before',1,
                       'rule_version_after',2,
                       'statement_hash',encode(public.digest(
                           'receipted retired fixture','sha256'),'hex'),
                       'previous_status','active',
                       'actor_id',%s::uuid,
                       'reason','retirement fixture',
                       'superseded_by',%s::uuid,
                       'approval_receipt_id',null::uuid,
                       'legacy_admission',%s::text,
                       'retired_at',%s::timestamptz
                     )::text,'sha256'),'hex')""",
                (retired_rule_id, guard_joe, successor_rule_id,
                 legacy_admission, retired_at),
            )
            contract_hash = one(receipt_cur)[0]

            # Internal equality is insufficient: a made-up hash shared by both
            # ledgers must still fail because it was not derived from the exact
            # canonical Joe-authorized retirement contract.
            receipt_cur.execute("savepoint malformed_retirement")
            receipt_cur.execute("""insert into ops.rule_retirement_receipt
                       (idempotency_key,rule_id,rule_version_before,rule_version_after,
                        statement_hash,previous_status,actor_id,reason,superseded_by,
                        legacy_admission,contract_hash,retired_at,created_at)
                       values ('0363-retirement-fixture',%s,1,2,
                               encode(public.digest('receipted retired fixture','sha256'),'hex'),
                               'active',%s,'retirement fixture',%s,%s,%s,%s,%s)""",
                                (retired_rule_id, guard_joe, successor_rule_id,
                                 legacy_admission, "1" * 64, retired_at, retired_at))
            receipt_cur.execute("""insert into ops.authority_receipt
                       (idempotency_key,kind,subject_type,subject_id,actor_id,
                        decision,contract_hash,evidence_refs,created_at)
                       values ('retirement:0363-retirement-fixture','override','rule',
                               %s,%s,'retired by Joe authority: retirement fixture',%s,
                               '{}'::text[],%s)""",
                                (retired_rule_id, guard_joe, "1" * 64, retired_at))
            try:
                receipt_cur.execute(transition_0363, prepare=False)
                check("0363 refuses an internally consistent fabricated retirement hash",
                      False, "transition accepted a fabricated retirement contract")
            except psycopg.Error as exc:
                check("0363 refuses an internally consistent fabricated retirement hash",
                      "not absent or exactly retired to aa411351" in str(exc),
                      str(exc).splitlines()[0])
            receipt_cur.execute("rollback to savepoint malformed_retirement")

            receipt_cur.execute("""insert into ops.rule_retirement_receipt
                       (idempotency_key,rule_id,rule_version_before,rule_version_after,
                        statement_hash,previous_status,actor_id,reason,superseded_by,
                        legacy_admission,contract_hash,retired_at,created_at)
                       values ('0363-retirement-fixture',%s,1,2,
                               encode(public.digest('receipted retired fixture','sha256'),'hex'),
                               'active',%s,'retirement fixture',%s,%s,%s,%s,%s)""",
                                (retired_rule_id, guard_joe, successor_rule_id,
                                 legacy_admission, contract_hash, retired_at, retired_at))
            receipt_cur.execute("""insert into ops.authority_receipt
                       (idempotency_key,kind,subject_type,subject_id,actor_id,
                        decision,contract_hash,evidence_refs,created_at)
                       values ('retirement:0363-retirement-fixture','override','rule',
                               %s,%s,'retired by Joe authority: retirement fixture',%s,
                               '{}'::text[],%s)""",
                                (retired_rule_id, guard_joe, contract_hash, retired_at))
            receipt_cur.execute(transition_0363, prepare=False)
            receipt_cur.execute("""select count(*),
                           count(*) filter (where map_digest=%s),
                           count(*) filter (where short_id=%s)
                          from ops.rule_delivery_activation_target""",
                                (CURRENT_ACTIVATION_DIGEST, RETIRED_ACTIVATION_ID))
            transitioned = one(receipt_cur)
            check("0363 accepts the exact receipted retirement",
                  transitioned == (len(EXPECTED_IDS), len(EXPECTED_IDS), 0),
                  str(transitioned))
            receipt_conn.rollback()

        # 0363 must also refuse a same-ID activation row whose reviewed
        # preimage drifted, without repinning any digest.
        cur.execute(
            """update ops.rule_delivery_activation_target
                  set to_test_ref='drifted same-ID acceptance fixture'
                where short_id=%s""",
            (activation_victim,),
        )
        try:
            cur.execute(migration_0363, prepare=False)
            check("0363 refuses a drifted same-ID activation preimage", False,
                  "migration accepted the drifted row")
        except psycopg.Error as exc:
            check(
                "0363 refuses a drifted same-ID activation preimage",
                "expected the exact reviewed nine-row activation preimage" in str(exc),
                str(exc).splitlines()[0],
            )
        conn.rollback()
        cur.execute(
            """select count(*) from ops.rule_delivery_activation_target
                where map_digest=%s""",
            (PRIOR_ACTIVATION_DIGEST,),
        )
        check("a refused 0363 repin changes no target digest", one(cur)[0] == len(EXPECTED_IDS) + 1)
        cur.execute(
            "delete from ops.rule_delivery_activation_target where short_id=%s",
            (RETIRED_ACTIVATION_ID,),
        )
        check("the post-0363 fixture removes exactly the retired target",
              cur.rowcount == 1)
        cur.execute(
            """update ops.rule_delivery_activation_target
                  set map_digest=%s,
                      to_test_ref=%s
                where short_id=any(%s)""",
            (CURRENT_ACTIVATION_DIGEST, ACTIVATION_TO_TEST_REF, sorted(EXPECTED_IDS)),
        )

        check("the post-0363 fixture restores all eight current target digests",
              cur.rowcount == len(EXPECTED_IDS))
        cur.execute(
            """select count(*) from ops.rule_delivery_activation_target
                where map_digest=%s""",
            (CURRENT_ACTIVATION_DIGEST,),
        )
        check("the restored post-0363 fixture is the exact current eight",
              one(cur)[0] == len(EXPECTED_IDS))

        # FOURTH LINK: 0471's repin. The fixture above reconstructs the state as
        # of 0363; the cutover below is handed the digest read from the live
        # overlay, which is now the post-0471 value. Without this step the two
        # disagree and the database refuses with "activation map digest preimage
        # differs" -- which is exactly how this gap was found.
        cur.execute(
            """update ops.rule_delivery_activation_target
                  set map_digest=%s
                where map_digest=%s""",
            (POST_0471_ACTIVATION_DIGEST, CURRENT_ACTIVATION_DIGEST),
        )
        check("0471 repins exactly the eight current targets",
              cur.rowcount == len(EXPECTED_IDS))
        cur.execute(
            """select count(*) from ops.rule_delivery_activation_target
                where map_digest=%s""",
            (POST_0471_ACTIVATION_DIGEST,),
        )
        check("the post-0471 fixture is the exact eight on the post-0471 map",
              one(cur)[0] == len(EXPECTED_IDS))

        # FIFTH LINK: 0478's repin. The cutover below is handed the digest read
        # from the live overlay, which is now the post-0478 value. Without this
        # step the two disagree and the database refuses with "activation map
        # digest preimage differs" -- which is how the gap was found the first
        # time too, at the fourth link.
        cur.execute(
            """update ops.rule_delivery_activation_target
                  set map_digest=%s
                where map_digest=%s""",
            (POST_0478_ACTIVATION_DIGEST, POST_0471_ACTIVATION_DIGEST),
        )
        check("0478 repins exactly the eight post-0471 targets",
              cur.rowcount == len(EXPECTED_IDS))
        cur.execute(
            """select count(*) from ops.rule_delivery_activation_target
                where map_digest=%s""",
            (POST_0478_ACTIVATION_DIGEST,),
        )
        check("the post-0478 fixture is the exact eight on the post-0478 map",
              one(cur)[0] == len(EXPECTED_IDS))

        # SIXTH LINK: 0483's repin after the two Layer 0 rule additions. The
        # eight cutover contracts are unchanged; only their base-map identity
        # moves forward.
        cur.execute(
            """update ops.rule_delivery_activation_target
                  set map_digest=%s
                where map_digest=%s""",
            (POST_0483_ACTIVATION_DIGEST, POST_0478_ACTIVATION_DIGEST),
        )
        check("0483 repins exactly the eight post-0478 targets",
              cur.rowcount == len(EXPECTED_IDS))
        cur.execute(
            """select count(*) from ops.rule_delivery_activation_target
                where map_digest=%s""",
            (POST_0483_ACTIVATION_DIGEST,),
        )
        check("the post-0483 fixture is the exact eight on the current map",
              one(cur)[0] == len(EXPECTED_IDS))
        # SEVENTH LINK: 0554 binds the active 209-rule map after the reviewed
        # inventory correction. Preserve the guarded 0483 preimage above.
        cur.execute(
            """update ops.rule_delivery_activation_target
                  set map_digest=%s
                where map_digest=%s""",
            (POST_0554_ACTIVATION_DIGEST, POST_0483_ACTIVATION_DIGEST),
        )
        check("0554 repins exactly the eight post-0483 targets",
              cur.rowcount == len(EXPECTED_IDS))
        cur.execute(
            """select count(*) from ops.rule_delivery_activation_target
                where map_digest=%s""",
            (POST_0554_ACTIVATION_DIGEST,),
        )
        check("the post-0554 fixture is the exact eight on the reviewed map",
              one(cur)[0] == len(EXPECTED_IDS))
        # EIGHTH LINK: 0772 binds the 210-rule map after rule ede4b241 entered it.
        cur.execute(
            """update ops.rule_delivery_activation_target
                  set map_digest=%s
                where map_digest=%s""",
            (POST_0772_ACTIVATION_DIGEST, POST_0554_ACTIVATION_DIGEST),
        )
        check("0772 repins exactly the eight post-0554 targets",
              cur.rowcount == len(EXPECTED_IDS))
        cur.execute(
            """select count(*) from ops.rule_delivery_activation_target
                where map_digest=%s""",
            (POST_0772_ACTIVATION_DIGEST,),
        )
        check("the post-0772 fixture is the exact eight on the reviewed map",
              one(cur)[0] == len(EXPECTED_IDS))
        # Replay the real retirement repin, including its guarded preimage.
        # A hand-written fixture UPDATE would hide a missing/broken migration.
        cur.execute(RETIREMENT_REPIN.read_text(encoding="utf-8"), prepare=False)
        cur.execute(
            """select count(*), count(*) filter (where map_digest=%s),
                      array_agg(short_id order by short_id)
                 from ops.rule_delivery_activation_target""",
            (current_map_digest,),
        )
        check("retirement repins the exact targets to the current reviewed map",
              one(cur) == (len(EXPECTED_IDS), len(EXPECTED_IDS), sorted(EXPECTED_IDS)))
        cur.execute("""insert into actor (slug,kind,display_name) values ('joe','human','Joe')
                       on conflict (slug) do nothing returning id""")
        cur.execute("select id from actor where slug='joe'")
        joe = one(cur)[0]
        cur.execute("""insert into actor (slug,kind,display_name) values ('dell','human','Dell')
                       on conflict (slug) do nothing returning id""")
        cur.execute("select id from actor where slug='dell'")
        dell = one(cur)[0]
        # THE SEED TURNS OFF THE RULE LIFECYCLE TRIGGERS, ON A THROWAWAY DATABASE,
        # AND SAYS SO. Activating a rule for real requires the whole Joe approval
        # chain migration 0228 built — an admission contract, installed controls,
        # an immutable approval receipt — and retiring one requires a retirement
        # receipt. That chain is exactly what
        # ops/atomic-rule-approval-local-pg-acceptance.py exists to prove, and
        # reproducing it 218 times here would test that chain a second time while
        # testing delivery not at all. What is under test in this file is whether
        # the tags install, select and refuse. Production's triggers are untouched;
        # this statement needs table ownership, which no application role holds.
        cur.execute("alter table rule disable trigger user")
        # This older acceptance deliberately creates inconsistent delivery
        # states across separate autocommit connections so it can test the
        # backfill's refusals. SIEP-12 correctly rejects those states. Suspend
        # only its observers on this throwaway fixture; SIEP-12's own rollback-
        # only gate exercises the observers and their fail-closed semantics.
        for table, trigger in (
            ("ops.rule_pack", "scac_epoch_rule_pack"),
            ("ops.rule_load_layer", "scac_epoch_rule_load_layer"),
            ("ops.rule_delivery_policy", "scac_epoch_rule_delivery_policy"),
        ):
            cur.execute(f"alter table {table} disable trigger {trigger}")
        cur.execute("delete from ops.rule_load_layer")
        cur.execute("delete from ops.rule_pack")
        cur.execute("delete from rule where statement like 'delivery acceptance %'")
        for short, scope in sorted(store_scope_by_id.items()):
            cur.execute("""insert into rule (id,statement,taught_by,status,activated_by,
                                             personal_to)
                           values (%s,%s,%s,'active',%s,%s)
                           on conflict (id) do nothing""",
                        (uuid_for(short), f"delivery acceptance {short}", joe, joe,
                         joe if scope == "joe" else dell if scope == "dell" else None))

        # ── the backfill installs exactly what the map says ──────────────────
        result = run("tools/sync-rule-load-layers.py", dsn)
        check("the backfill runs clean", result.returncode == 0,
              result.stderr.strip()[-400:])
        cur.execute("select count(*) from ops.rule_load_layer")
        check("every reviewed tag landed", one(cur)[0] == len(layers))
        cur.execute("select count(*) from ops.rule_pack")
        check("every reviewed pack landed", one(cur)[0] == len(data["rule_packs"]))

        audit = run("ops/rule-delivery-audit.py", dsn)
        check("the delivery audit passes after the backfill", audit.returncode == 0,
              (audit.stdout + audit.stderr).strip()[-400:])

        # ── the selector hands back the set the council designed ─────────────
        cur.execute("select count(*) from ops.rule_delivery_plan('joe') where selected")
        layer0 = one(cur)[0]
        expected_layer0 = sum(1 for s, e in layers.items()
                              if e["load_layer"] == "layer0"
                              and store_scope_by_id[s] in ("shared", "joe"))
        check("an undeclared boot selects exactly Layer 0",
              layer0 == expected_layer0, f"{layer0} != {expected_layer0}")
        check("an undeclared boot is never empty", layer0 > 0)
        cur.execute("select count(*) from ops.rule_delivery_plan('joe')")
        check("the plan still reports every in-scope rule, which is what shadow "
              "mode compares against",
              one(cur)[0] == sum(store_scope_by_id[s] in ("shared", "joe") for s in layers))

        cur.execute("select count(*) from ops.rule_delivery_plan('dell')")
        check("Dell's plan contains shared plus Dell-personal rules only",
              one(cur)[0] == sum(store_scope_by_id[s] in ("shared", "dell") for s in layers))

        cur.execute("select count(*) from ops.rule_delivery_plan(null)")
        check("an unsponsored plan contains shared rules only",
              one(cur)[0] == sum(store_scope_by_id[s] == "shared" for s in layers))

        cur.execute("""select count(*) from ops.rule_delivery_plan('joe', array['engineering-git'])
                        where selected""")
        with_pack = one(cur)[0]
        check("declaring a pack adds rules and never removes any", with_pack > layer0)

        cur.execute("select count(*) from ops.rule_delivery_plan(null) where scope='joe'")
        check("an unsponsored runtime gets no partner's personal rules",
              one(cur)[0] == 0)
        cur.execute("select count(*) from ops.rule_delivery_plan('joe') where scope='dell'")
        check("Joe's plan contains no Dell-personal rules", one(cur)[0] == 0)
        cur.execute("select count(*) from ops.rule_delivery_plan('dell') where scope='joe'")
        check("Dell's plan contains no Joe-personal rules", one(cur)[0] == 0)

        cur.execute("select count(*) from ops.rule_pack_index() where rule_count = 0")
        check("no pack in the index is empty", one(cur)[0] == 0)
        cur.execute("select count(*) from ops.rule_pack_index()")
        check("the pack index names every pack", one(cur)[0] == len(data["rule_packs"]))

        expected_digest = load_validated()[1]["base_map_sha256"]
        # SIEP-02 reads Work Request prose, map currentness, and the pack index
        # in one writer snapshot. Exercise the actual runtime grant.
        cur.execute("set session authorization carr_writer")
        try:
            cur.execute("""with registry as (
              select count(distinct map_digest)::integer map_versions,
                     min(map_digest) map_digest,count(*)::integer tagged_rules
                from ops.rule_load_layer
            ), packs as (
              select coalesce(jsonb_agg(to_jsonb(p) order by p.pack),'[]'::jsonb) pack_index
                from ops.rule_pack_index() p
            )
            select registry.map_versions,registry.map_digest,registry.tagged_rules,
                   jsonb_array_length(packs.pack_index),w.title
              from registry cross join packs
              join ops.work_request w on w.ref='WR-SIEP-02'
               and w.organization_tenant_id='carr-internal'""")
            snapshot = one(cur)
        finally:
            cur.execute("reset session authorization")
        check("the writer reads one coherent SIEP-02 delivery-source snapshot",
              snapshot[0] == 1 and snapshot[1] == expected_digest
              and snapshot[2] == len(layers) and snapshot[3] == len(data["rule_packs"])
              and snapshot[4] == "Deterministic trigger classifier and scoped delivery",
              str(snapshot))

        cur.execute("select mode from ops.rule_delivery_policy")
        check("delivery starts in shadow mode", one(cur)[0] == "shadow")
        proposal_ids = [f"00000000-0000-4000-8000-{index:012d}"
                        for index in range(1, 39)]
        cur.execute("set session authorization carr_authority_joe")
        try:
            cur.execute("select * from ops.rule_delivery_cutover_preflight(%s::uuid[])",
                        (proposal_ids,))
            preflight = one(cur)
        finally:
            cur.execute("reset session authorization")
        check("Joe reads only the typed aggregate cutover preflight",
              preflight == ("shadow", len(EXPECTED_IDS), 0, 0, 0, 0), str(preflight))

        # A scope-only corruption used to pass both the backfill and audit.
        victim = synthetic_dell
        cur.execute("update ops.rule_load_layer set scope='shared' where short_id=%s", (victim,))
        audit = run("ops/rule-delivery-audit.py", dsn)
        check("the audit fails closed on a personal-scope mismatch",
              audit.returncode != 0 and "scope_mismatch=1" in audit.stdout,
              audit.stdout.strip())
        result = run("tools/sync-rule-load-layers.py", dsn)
        check("the reviewed backfill repairs a corrupted installed scope",
              result.returncode == 0, result.stderr.strip()[-300:])

        # ── the refusals are real ────────────────────────────────────────────
        for label, packs, expect_fail in (
                ("a wildcard pack is refused by the database", ["*"], True),
                ("an undefined pack is refused by the database", ["not-a-pack"], True)):
            try:
                cur.execute("""update ops.rule_load_layer set packs=%s
                                where load_layer='pack'
                                  and short_id=(select min(short_id) from ops.rule_load_layer
                                                 where load_layer='pack')""", (packs,))
                check(label, not expect_fail, "the write was accepted")
            except psycopg.Error:
                check(label, expect_fail)
        conn.rollback() if not conn.autocommit else None

        try:
            cur.execute("""update ops.rule_load_layer set packs=array['engineering-git']
                            where load_layer='layer0'
                              and short_id=(select min(short_id) from ops.rule_load_layer
                                             where load_layer='layer0')""")
            check("layer0 cannot be narrowed by a pack", False, "the write was accepted")
        except psycopg.Error:
            check("layer0 cannot be narrowed by a pack", True)

        # ── a rule taught after the map stops the backfill, it is not guessed ─
        cur.execute("""insert into rule (id,statement,taught_by,status,activated_by)
                       values (%s,'delivery acceptance newcomer',%s,'active',%s)""",
                    (uuid_for("fedcba98"), joe, joe))
        result = run("tools/sync-rule-load-layers.py", dsn)
        check("an untagged active rule refuses the backfill", result.returncode != 0)
        check("and the refusal names the rule", "fedcba98" in result.stderr,
              result.stderr.strip()[-200:])
        audit = run("ops/rule-delivery-audit.py", dsn)
        check("the audit reports the untagged rule rather than passing",
              audit.returncode != 0 and "untagged=1" in audit.stdout, audit.stdout.strip())

        # ── retiring a rule removes its tag rather than leaving dead law ─────
        cur.execute("update rule set status='retired' where id=%s", (uuid_for("fedcba98"),))
        victim = sorted(s for s, e in layers.items() if e["load_layer"] == "pack")[0]
        cur.execute("update rule set status='retired' where id=%s", (uuid_for(victim),))
        result = run("tools/sync-rule-load-layers.py", dsn)
        check("a tag for a retired rule refuses the backfill", result.returncode != 0)
        check("and names it", victim in result.stderr, result.stderr.strip()[-200:])
        cur.execute("update rule set status='active' where id=%s", (uuid_for(victim),))

        result = run("tools/sync-rule-load-layers.py", dsn)
        check("the backfill runs clean again once the store and map agree",
              result.returncode == 0, result.stderr.strip()[-300:])

        # ── policy and eight controls move in one guarded transaction ─────────
        admission = run("tools/sync-rule-admission.py", dsn)
        check("the admission sync builds the eight-control preimage",
              admission.returncode == 0, admission.stderr.strip()[-500:])
        digest = expected_digest
        for role in (None, "carr_writer", "carr_authority_dell"):
            if role:
                cur.execute(f"set session authorization {role}")
            try:
                cur.execute("select * from ops.set_rule_delivery_mode(%s,%s,%s)",
                            ("enforced", "forged authority fixture", digest))
                check(f"{role or 'owner'} cannot cut over rule delivery", False,
                      "cutover was accepted")
            except psycopg.Error as exc:
                check(f"{role or 'owner'} cannot cut over rule delivery",
                      exc.sqlstate == "42501", str(exc))
            finally:
                if role:
                    cur.execute("reset session authorization")
        for role in (None, "carr_writer", "carr_authority_dell"):
            if role:
                cur.execute(f"set session authorization {role}")
            try:
                cur.execute("select * from ops.rule_delivery_cutover_preflight(%s::uuid[])",
                            (proposal_ids,))
                check(f"{role or 'owner'} cannot read cutover preflight", False,
                      "preflight was accepted")
            except psycopg.Error as exc:
                check(f"{role or 'owner'} cannot read cutover preflight",
                      exc.sqlstate in ("42501", "42883"), str(exc))
            finally:
                if role:
                    cur.execute("reset session authorization")
        try:
            cutover = cutover_as(cur, "carr_authority_joe", "enforced",
                                "seven-day evidence fixture", digest)
        except psycopg.Error as exc:
            # psycopg appends a PL/pgSQL CONTEXT line after the useful refusal.
            # The local runner reports only the final line, so preserve the
            # database's first message without echoing a query or DSN.
            raise RuntimeError(
                "rule-delivery cutover refused: " + str(exc).splitlines()[0]
            ) from None
        check("cutover reports the exact eight", cutover[0] == "enforced" and cutover[1] == len(EXPECTED_IDS))
        cur.execute("select mode from ops.rule_delivery_policy")
        check("cutover flips policy to enforced", one(cur)[0] == "enforced")
        cur.execute("select changed_by from ops.rule_delivery_policy")
        check("cutover derives Joe attribution", one(cur)[0] == "joe")
        cur.execute("""select changed_by from ops.rule_delivery_activation_receipt
                        order by created_at desc limit 1""")
        check("cutover receipt derives Joe attribution", one(cur)[0] == "joe")
        cur.execute("""select count(*) from ops.rule_enforcement_point ep
                       join rule r on r.id=ep.rule_id
                      where left(r.id::text,8)=any(%s) and ep.control_key='pack_delivery'
                        and ep.enforcement_class='stop_gate' and ep.installed""",
                    (sorted(EXPECTED_IDS),))
        check("cutover installs pack_delivery/stop_gate on all eight", one(cur)[0] == len(EXPECTED_IDS))

        admission = run("tools/sync-rule-admission.py", dsn)
        check("a future admission sync is policy-aware", admission.returncode == 0,
              admission.stderr.strip()[-500:])
        cur.execute("""select count(*) from ops.rule_enforcement_point ep
                       join rule r on r.id=ep.rule_id
                      where left(r.id::text,8)=any(%s) and ep.control_key='pack_delivery'
                        and ep.enforcement_class='stop_gate'""", (sorted(EXPECTED_IDS),))
        check("an enforced sync does not revert the eight controls", one(cur)[0] == len(EXPECTED_IDS))

        try:
            cur.execute("update ops.rule_delivery_policy set mode='shadow' where singleton")
            check("a direct policy-only update is refused", False, "update was accepted")
        except psycopg.Error as exc:
            check("a direct policy-only update is refused",
                  exc.sqlstate == "42501" and "Joe authority" in str(exc), str(exc))

        rollback = cutover_as(cur, "carr_authority_joe", "shadow",
                             "rollback fixture", digest)
        check("rollback reports the exact eight", rollback[0] == "shadow" and rollback[1] == len(EXPECTED_IDS))
        cur.execute("""select count(*) from ops.rule_enforcement_point ep
                       join rule r on r.id=ep.rule_id
                      where left(r.id::text,8)=any(%s) and ep.control_key='session_boot'
                        and ep.enforcement_class='surfacing'""", (sorted(EXPECTED_IDS),))
        check("rollback restores all eight session_boot rows", one(cur)[0] == len(EXPECTED_IDS))
        cur.execute("select count(*) from ops.rule_delivery_activation_receipt")
        check("cutover and rollback each leave an append-only receipt", one(cur)[0] == 2)

        try:
            cutover_as(cur, "carr_authority_joe", "enforced",
                       "wrong digest", "0" * 64)
            check("a stale map digest refuses cutover", False, "cutover was accepted")
        except psycopg.Error as exc:
            check("a stale map digest refuses cutover", "digest preimage" in str(exc), str(exc))
        cur.execute("select mode from ops.rule_delivery_policy")
        check("a refused cutover leaves policy in shadow", one(cur)[0] == "shadow")
        cur.execute("alter table rule enable trigger user")
        for table, trigger in (
            ("ops.rule_pack", "scac_epoch_rule_pack"),
            ("ops.rule_load_layer", "scac_epoch_rule_load_layer"),
            ("ops.rule_delivery_policy", "scac_epoch_rule_delivery_policy"),
        ):
            cur.execute(f"alter table {table} enable trigger {trigger}")
        cur.execute("""update ops.rule_pack set updated_at=updated_at
                        where pack=(select min(pack) from ops.rule_pack)""")

    if FAILURES:
        print("rule-delivery-acceptance: FAIL", file=sys.stderr)
        for line in FAILURES:
            print(f"  {line}", file=sys.stderr)
        return 1
    print("rule-delivery-acceptance: delivery tags install, select and refuse as reviewed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
