#!/usr/bin/env python3
"""Exercise the LATEST rule-delivery activation repin against PostgreSQL and the current overlay.

The latest forward repin (highest-numbered migrations/NNNN_repin_rule_delivery_activation_after_*.sql)
must start from its predecessor's v_new, end at the overlay's base_map_sha256, and change only the
digest. Chain-generic since 0865 (held-control tests), so the next repin needs no edit here.
"""
import importlib.util
import os
from pathlib import Path
import re
import subprocess
import sys

import psycopg

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from lib.disposable_pg_fixture import DisposablePostgres, postgres_fixture_group
from lib.rule_delivery_activation import EXPECTED_IDS, load_validated


FROM_IMPLEMENTATION_REF = "hooks/session-brief.py; hooks/machine-converge.py; mcp-server/src/mcp.js"
FROM_TEST_REF = "command:python3 hooks/gate-integrity.py --selftest"
TO_IMPLEMENTATION_REF = "hooks/rule-pack-drift-gate.py; hooks/rule-pack-preuse-reselection.py"
TO_TEST_REF = "ops/rule-pack-drift-gate-selftest.py; ops/rule-load-layer-check-selftest.py; ops/rule-pack-preuse-reselection-selftest.py"


def main():
    chain = sorted(ROOT.glob("migrations/[0-9][0-9][0-9][0-9]_repin_rule_delivery_activation_after_*.sql"))
    assert len(chain) >= 2, "the activation repin chain needs a predecessor"
    repin = chain[-1].read_text()
    prior = chain[-2].read_text()
    contract_fields = (
        "short_id", "expected_scope", "expected_pack", "from_control",
        "from_enforcement_class", "from_implementation_ref", "from_test_ref",
        "to_control", "to_enforcement_class", "to_implementation_ref", "to_test_ref",
        "map_digest",
    )
    for field in contract_fields:
        assert re.search(rf"\b{field}\b", repin), f"repin does not guard {field}"
    old_digest = re.search(r"v_new constant text := '([0-9a-f]{64})'", prior)[1]
    assert re.search(r"v_old constant text := '([0-9a-f]{64})'", repin)[1] == old_digest, \
        f"{chain[-1].name} does not start where {chain[-2].name} ended"
    _, overlay = load_validated()
    new_digest = overlay["base_map_sha256"]
    assert re.search(r"v_new constant text := '([0-9a-f]{64})'", repin)[1] == new_digest, \
        f"{chain[-1].name} does not end at the overlay's base_map_sha256"
    spec = importlib.util.spec_from_file_location("local_pg_ci", ROOT / "ops/local-pg-ci.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    binaries = module.find_postgres_binaries()
    env = module.scrub_cloud_environment(os.environ)
    env['LC_ALL'] = 'C'
    schema = (ROOT / "db/schema.sql").read_text()
    with postgres_fixture_group(), DisposablePostgres("carr-activation-repin-", binaries.pg_ctl, env=env) as fixture:
        root = fixture.root
        data, socket = root / "data", root / "socket"
        socket.mkdir()
        fixture.run([binaries.initdb, "-D", data, "-A", "trust", "-U", "fixture",
                        "--encoding=UTF8", "--no-locale"],
                       check=True, capture_output=True, env=env, timeout=20)
        fixture.run([binaries.pg_ctl, "-D", data, "-l", root / "pg.log", "-o",
                        f"-k {socket} -c listen_addresses=''", "-w", "start"],
                       check=True, capture_output=True, env=env, timeout=20)
        with psycopg.connect(host=str(socket), user="fixture", dbname="postgres", autocommit=True) as conn:
            assert conn.execute("show server_encoding").fetchone() == ('UTF8',)
            conn.execute("create schema ops")
            for name in ("rule_delivery_activation_target", "rule_delivery_policy"):
                conn.execute(re.search(rf"CREATE TABLE ops\.{name}\b.*?;", schema, re.S)[0])
            conn.execute("insert into ops.rule_delivery_policy (mode) values ('enforced')")
            for target in overlay["targets"]:
                conn.execute("""insert into ops.rule_delivery_activation_target values
                  (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                  (target["short_id"], target["scope"], target["pack"], target["from_control"],
                   target["from_enforcement_class"], FROM_IMPLEMENTATION_REF, FROM_TEST_REF,
                   target["to_control"], target["to_enforcement_class"], TO_IMPLEMENTATION_REF,
                   TO_TEST_REF, old_digest))

            def rows():
                return conn.execute("select * from ops.rule_delivery_activation_target order by short_id").fetchall()

            baseline = rows()
            conn.execute(repin, prepare=False)
            assert rows() == [(*row[:-1], new_digest) for row in baseline], "repin must change only the digest"
            assert conn.execute("select mode from ops.rule_delivery_policy").fetchone() == ('enforced',)
            print("PASS exact activation ids reach the current map without changing contracts or mode")

            for label, mutation in (
                ("mixed digest", "update ops.rule_delivery_activation_target set map_digest='" + '0' * 64 + "' where short_id='25fcddee'"),
                ("missing target", "delete from ops.rule_delivery_activation_target where short_id='25fcddee'"),
                ("extra target", "insert into ops.rule_delivery_activation_target select '00000000',expected_scope,expected_pack,from_control,from_enforcement_class,from_implementation_ref,from_test_ref,to_control,to_enforcement_class,to_implementation_ref,to_test_ref,map_digest from ops.rule_delivery_activation_target limit 1"),
                ("wrong id at matching cardinality", "update ops.rule_delivery_activation_target set short_id='00000000' where short_id='25fcddee'"),
            ):
                with conn.transaction(force_rollback=True):
                    conn.execute("update ops.rule_delivery_activation_target set map_digest=%s", (old_digest,))
                    conn.execute(mutation)
                    before = rows()
                    try:
                        with conn.transaction():
                            conn.execute(repin, prepare=False)
                    except psycopg.errors.RaiseException as error:
                        assert "REFUSED" in str(error), str(error)
                    else:
                        raise AssertionError(f"repin accepted {label}")
                    assert rows() == before, f"refused {label} changed a target"
                print(f"PASS {label} refuses atomically")

            contract_mutations = (
                ("expected_scope", "dell"),
                ("expected_pack", "wrong-pack"),
                ("from_control", "wrong-from-control"),
                ("from_enforcement_class", "wrong-from-class"),
                ("from_implementation_ref", "wrong-from-implementation"),
                ("from_test_ref", "wrong-from-test"),
                ("to_control", "wrong-to-control"),
                ("to_enforcement_class", "wrong-to-class"),
                ("to_implementation_ref", "wrong-to-implementation"),
                ("to_test_ref", "wrong-to-test"),
            )
            for column, wrong_value in contract_mutations:
                with conn.transaction(force_rollback=True):
                    conn.execute("update ops.rule_delivery_activation_target set map_digest=%s", (old_digest,))
                    conn.execute(
                        f"update ops.rule_delivery_activation_target set {column}=%s where short_id='25fcddee'",
                        (wrong_value,),
                    )
                    before = rows()
                    try:
                        with conn.transaction():
                            conn.execute(repin, prepare=False)
                    except psycopg.errors.RaiseException as error:
                        assert "REFUSED" in str(error), str(error)
                    else:
                        raise AssertionError(f"repin accepted changed {column}")
                    assert rows() == before, f"refused changed {column} changed a target"
                print(f"PASS changed {column} refuses atomically")
            assert set(row[0] for row in rows()) == EXPECTED_IDS
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
