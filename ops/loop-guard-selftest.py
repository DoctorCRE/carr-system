#!/usr/bin/env python3
"""ops/loop-guard-selftest.py — the loop_guard control, exercised.

loop_guard is the deferral gate: an open_loop must name WHY the session cannot
do the work now, from a closed list of states of the world outside it. The
control catalog names two implementations and this proves both:

  * migrations/0081_loop_blocker.sql, applied VERBATIM to a private throwaway
    PostgreSQL cluster over the minimal loop tables it alters. The migration's
    own probes run, and then this file's: an unknown class ("later") is a
    check_violation on loop_item_blocker_class_known, a throwaway detail
    ("tbd") is a check_violation on loop_item_blocker_detail_present, a real
    class with a real detail is accepted (a gate that refuses everything
    proves nothing), and v_loop_no_blocker lists exactly the open open_loops
    with no blocker. That is the "transaction refusal" failure mode the
    catalog records.
  * mcp-server/src/tools.js's add-loop gate: its BLOCKER_CLASSES list and its
    12-character detail floor must equal the database's, so the verb cannot
    accept what the table refuses or refuse what it accepts.
  * a mutant of the migration that admits "later" must FAIL to apply — the
    probes bite.

Before this file the catalog's only test reference was
`external:migration probes`, which ops/sync_control_catalog.py rightly holds:
nothing CI runs. ops/ci.sh's ops/*-selftest.py glob collects this one.
"""
from __future__ import annotations

import importlib.util
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from lib.disposable_pg_fixture import DisposablePostgres, postgres_fixture_group  # noqa: E402

MIGRATION = ROOT / "migrations" / "0081_loop_blocker.sql"
TOOLS = ROOT / "mcp-server" / "src" / "tools.js"

ROLES = "create role carr_reader; create role carr_writer; create role carr_exporter;"
BASE = """
create table loop_block (id serial primary key, block_key text not null);
create table loop_item (
  id uuid primary key default gen_random_uuid(), number text, kind text not null, domain text, owner text,
  marker text, since_text text, block_id int not null references loop_block(id),
  body text, title text, status text not null default 'open',
  created_at timestamptz not null default now());
insert into loop_block (block_key) values ('hot');
insert into loop_item (number, kind, block_id, body, status, created_at) values
  ('1', 'open_loop', 1, 'inherited open loop', 'open', '2026-08-01'),
  ('2', 'open_loop', 1, 'closed loop',         'closed', '2026-08-01'),
  ('3', 'team_loop', 1, 'handoff to Dell',     'open', '2026-08-01');
"""


def sql_classes() -> list[str]:
    text = MIGRATION.read_text()
    m = re.search(r"blocker_class in\s*\(([^)]*)\)", text)
    assert m, "0081 carries no blocker_class list"
    return re.findall(r"'([a-z_]+)'", m.group(1))


def js_classes() -> list[str]:
    text = TOOLS.read_text()
    m = re.search(r"const BLOCKER_CLASSES = Object\.freeze\(\[(.*?)\]\);", text, re.S)
    assert m, "tools.js carries no BLOCKER_CLASSES"
    body = "\n".join(line.split("//", 1)[0] for line in m.group(1).splitlines())
    return re.findall(r'"([a-z_]+)"', body)


def check_parity() -> None:
    assert sql_classes() == js_classes(), (sql_classes(), js_classes())
    assert "length(btrim(blocker_detail)) >= 12" in MIGRATION.read_text()
    tools = TOOLS.read_text()
    assert "if (detail.length < 12)" in tools, "add-loop's detail floor moved"
    assert 'const blockerGated = args.kind === "open_loop";' in tools
    assert 'error: "blocker_required"' in tools and 'error: "unknown_blocker_class"' in tools
    print("PASS add-loop's classes and detail floor equal the database's")


class Cluster:
    def __init__(self, fixture, binaries, env):
        self.fixture, self.bin, self.env = fixture, binaries, env
        root = fixture.root
        self.data, self.socket = root / "data", root / "socket"
        self.socket.mkdir()
        fixture.run([binaries["initdb"], "-D", str(self.data), "-A", "trust", "-U", "fixture"],
                    check=True, capture_output=True, env=env, timeout=60)
        started = fixture.run([binaries["pg_ctl"], "-D", str(self.data), "-l", str(root / "pg.log"),
                               "-o", f"-k {self.socket} -c listen_addresses=''", "-w", "start"],
                              capture_output=True, env=env, timeout=60)
        if started.returncode != 0:
            log = (root / "pg.log").read_text() if (root / "pg.log").exists() else ""
            raise SystemExit(f"disposable PostgreSQL did not start: {log[-800:] or started.stderr}")

    def psql(self, db: str, text: str, check: bool = True) -> subprocess.CompletedProcess:
        return subprocess.run([self.bin["psql"], "-X", "-At", "-v", "ON_ERROR_STOP=1",
                               "-h", str(self.socket), "-U", "fixture", "-d", db],
                              input=text, text=True, capture_output=True, check=check,
                              env=self.env, timeout=60)

    def fresh(self, name: str) -> str:
        self.psql("postgres", f"create database {name};")
        self.psql(name, BASE)
        return name


def violated(c: Cluster, db: str, stmt: str) -> str | None:
    r = c.psql(db, f"begin; {stmt}; rollback;", check=False)
    if r.returncode == 0:
        return None
    m = re.search(r'violates check constraint "([a-z_]+)"', r.stderr)
    assert m, f"refused for a reason other than a check: {r.stderr.strip()[:300]}"
    return m.group(1)


def main() -> int:
    check_parity()
    spec = importlib.util.spec_from_file_location("local_pg_ci", ROOT / "ops/local-pg-ci.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    found = module.find_postgres_binaries()
    binaries = {n: str(getattr(found, n)) for n in ("initdb", "pg_ctl", "psql")}
    # LC_ALL=C: on macOS a postmaster started with an unset locale goes
    # multithreaded during startup and refuses ("set LC_ALL to a valid locale").
    env = {**module.scrub_cloud_environment(os.environ), "LC_ALL": "C"}
    migration = MIGRATION.read_text()
    with postgres_fixture_group(), DisposablePostgres("carr-loop-guard-", binaries["pg_ctl"], env=env) as fx:
        c = Cluster(fx, binaries, env)
        c.psql("postgres", ROLES)  # roles are cluster-wide: once, for every database
        db = c.fresh("guard")
        applied = c.psql(db, migration, check=False)
        assert applied.returncode == 0, f"0081 did not apply: {applied.stderr[-600:]}"
        assert "0081 ok" in applied.stderr, "0081's own probes did not report"
        print("PASS 0081 applies verbatim and its own three probes behave")

        assert violated(c, db, "update loop_item set blocker_class='later', "
                        "blocker_detail='waiting until there is more time' where number='1'") \
            == "loop_item_blocker_class_known"
        assert violated(c, db, "update loop_item set blocker_class='ruling', blocker_detail='tbd' "
                        "where number='1'") == "loop_item_blocker_detail_present"
        assert violated(c, db, "update loop_item set blocker_class='ruling', blocker_detail=null "
                        "where number='1'") == "loop_item_blocker_detail_present"
        assert violated(c, db, "insert into loop_item (kind, block_id, blocker_class, blocker_detail) "
                        "values ('open_loop', 1, 'someday', 'when there is more time to look')") \
            == "loop_item_blocker_class_known"
        print("PASS an unknown class and a throwaway detail are refused by name")
        for cls in sql_classes():
            assert violated(c, db, f"update loop_item set blocker_class='{cls}', "
                            "blocker_detail='Sanders, listing broker on C-112' where number='1'") is None, cls
        print("PASS every listed class with a real detail is accepted")

        listed = c.psql(db, "select string_agg(number, ',' order by number) from v_loop_no_blocker;").stdout.strip()
        assert listed == "1", f"v_loop_no_blocker listed {listed!r}, expected only the open open_loop"
        c.psql(db, "update loop_item set blocker_class='counterparty', "
                   "blocker_detail='Sanders, listing broker on C-112' where number='1';")
        listed = c.psql(db, "select count(*) from v_loop_no_blocker;").stdout.strip()
        assert listed == "0", listed
        print("PASS v_loop_no_blocker lists exactly the open open_loops with no blocker")

        mutant = migration.replace("('human_only','counterparty',", "('later','human_only','counterparty',", 1)
        assert mutant != migration, "mutation anchor moved"
        mdb = c.fresh("mutant")
        bad = c.psql(mdb, mutant, check=False)
        assert bad.returncode != 0 and 'accepted "later"' in bad.stderr, \
            "a migration that admits 'later' applied cleanly: the probes do not bite"
        print("PASS a mutant admitting 'later' is refused by 0081's own probe")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
