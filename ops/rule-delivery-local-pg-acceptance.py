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

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

REPO = Path(__file__).resolve().parent.parent
MAP = REPO / "ops" / "config" / "rule-enforcement-map.json"
PY = REPO / ".venv" / "bin" / "python"
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


def main() -> int:
    dsn = os.environ.get("CARR_LOCAL_PG_DSN")
    if not dsn:
        print("rule-delivery-acceptance: CARR_LOCAL_PG_DSN required", file=sys.stderr)
        return 78
    data = json.loads(MAP.read_text())
    layers = data["rule_load_layers"]
    scope_by_id = {rid: scope for scope, ids in data["active_rule_ids"].items() for rid in ids}
    # Scope is durable rule state, not reviewed-map configuration. Deliberately
    # make one map-shared rule Dell-personal to prove the compiler reads
    # rule.personal_to and the audit checks the installed result.
    store_scope_by_id = dict(scope_by_id)
    synthetic_dell = data["active_rule_ids"]["shared"][0]
    store_scope_by_id[synthetic_dell] = "dell"

    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
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

        expected_digest = hashlib.sha256(MAP.read_bytes()).hexdigest()
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
