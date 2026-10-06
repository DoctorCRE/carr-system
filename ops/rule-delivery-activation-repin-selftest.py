#!/usr/bin/env python3
"""Exercise the retirement repin against PostgreSQL and the current overlay."""
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


def main():
    migrations = list(ROOT.glob("migrations/[0-9][0-9][0-9][0-9]_repin_rule_delivery_activation_after_control_retirement.sql"))
    assert len(migrations) == 1, "control retirement needs one forward activation repin"
    repin = migrations[0].read_text()
    prior = (ROOT / "migrations/0772_repin_rule_delivery_activation_after_model_choice_rule.sql").read_text()
    old_digest = re.search(r"v_new constant text := '([0-9a-f]{64})'", prior)[1]
    _, overlay = load_validated()
    new_digest = overlay["base_map_sha256"]
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
                  (%s,%s,%s,%s,%s,'boot-ref','boot-test',%s,%s,'pack-ref','pack-test',%s)""",
                  (target["short_id"], target["scope"], target["pack"], target["from_control"],
                   target["from_enforcement_class"], target["to_control"],
                   target["to_enforcement_class"], old_digest))

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
            assert set(row[0] for row in rows()) == EXPECTED_IDS
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
