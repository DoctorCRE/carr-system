#!/usr/bin/env python3
"""breakglass_selftest.py — Artifact C clone test harness (WR-000046, acceptance (3)/(3b)/(3c)/(4)).

Stands up a DISPOSABLE, LOOPBACK-ONLY local PostgreSQL cluster (never a
Neon branch, never production — see the HARD RULES this build was done
under), builds a small fixture schema, and drives breakglass_run.py and
gen_breakglass_run.py against it exactly as F01's execution path describes,
through real subprocesses with DATABASE_URL in the environment — the same
shape db-tap.py's `run` mode hands the driver, and the shape this driver
promises to also support standalone.

Every test appends one line (test name, expectation, observed, pass/fail) to
out/frontier-finding/build-receipts/slice-c/test-receipts.jsonl. Run:

    .venv/bin/python docs/frontier-finding/breakglass_selftest.py

Exits nonzero if any test failed.

CLAIM DISCIPLINE (Section T): this suite tests what the instrument must
catch on ITS reviewed surface, not a completeness the plan does not claim.
One item from the plan's acceptance list is deliberately NOT a test here,
by the plan's own words: "a run whose digests do not match the recorded
approval note is refused by procedure with the refusal recorded" — the
approval oracle is the WR-000046 record layer, not this file (Section 2,
"Approval oracle (named)"); the driver's job is only to print the three
digests for a human or auditor to compare, which it does. See RESULT.md for
the full account of this and every other scoping choice below.
"""
from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
PYTHON = str(REPO / ".venv" / "bin" / "python")

# breakglass_run.py and gen_breakglass_run.py are deliberately import-only
# libraries now (see README.md: no shebang, no bare __main__ block, so the
# sealed SCAC mutation registry never sees them as script entrypoints). A
# real subprocess still has to reach their main() somehow, so this drives
# them through the exact same 3-line shim the README documents for
# break-glass sessions (sys.path.insert -> import -> main()) rather than
# execing the .py file directly.
DRIVER_MODULE = "breakglass_run"
GENERATOR_MODULE = "gen_breakglass_run"
_LAUNCH_SHIM = "import sys; sys.path.insert(0, sys.argv[1]); import {module} as m; sys.exit(m.main(sys.argv[2:]))"

RECEIPTS_DIR = REPO / "out" / "frontier-finding" / "build-receipts" / "slice-c"
WORK_DIR = RECEIPTS_DIR / "work"
TEST_RECEIPTS_PATH = RECEIPTS_DIR / "test-receipts.jsonl"

sys.path.insert(0, str(HERE))
import importlib.util
_spec = importlib.util.spec_from_file_location("breakglass_run", HERE / "breakglass_run.py")
if _spec is None or _spec.loader is None:
    sys.exit("breakglass_selftest: breakglass_run.py is unavailable")
breakglass_run = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(breakglass_run)

try:
    import psycopg
except ImportError:
    sys.exit("breakglass_selftest: psycopg is not installed — run through .venv/bin/python")


RESULTS: list[dict] = []


def record(name: str, expectation: str, observed: str, ok: bool) -> None:
    RESULTS.append({
        "test": name,
        "expectation": expectation,
        "observed": observed,
        "pass": ok,
        "recorded_at": time.time(),
    })
    print(f"  {'ok  ' if ok else 'FAIL'} {name} — {observed if not ok else 'as expected'}")


def check(name: str, expectation: str, ok: bool, observed: str) -> bool:
    record(name, expectation, observed, ok)
    return ok


def test_duplicate_snapshot_identities_are_lossless() -> None:
    """A logical census identity may have multiple physical catalog rows."""

    rows = [
        ("db-role-membership:carr_exporter:neondb_owner",
         {"admin_option": False, "inherit_option": True, "set_option": True}),
        ("db-role-membership:carr_exporter:neondb_owner",
         {"admin_option": True, "inherit_option": True, "set_option": True}),
        ("db-role-membership:carr_exporter:neondb_owner",
         {"admin_option": False, "inherit_option": True, "set_option": True}),
        ("db-role:carr_exporter", {"login": True}),
    ]

    class FakeCursor:
        def __init__(self, result_rows):
            self.result_rows = result_rows

        def fetchall(self):
            return self.result_rows

    observed = breakglass_run._rows_to_map(FakeCursor(rows))
    reversed_observed = breakglass_run._rows_to_map(FakeCursor(list(reversed(rows))))
    duplicate_keys = sorted(
        key for key in observed
        if key.startswith("db-role-membership:carr_exporter:neondb_owner#duplicate:")
    )
    ok = (
        len(observed) == 4
        and len(duplicate_keys) == 3
        and "db-role-membership:carr_exporter:neondb_owner" not in observed
        and observed == reversed_observed
        and observed["db-role:carr_exporter"] == {"login": True}
    )
    check(
        "duplicate_snapshot_identities_are_lossless",
        "duplicate logical identities survive under deterministic receipt-only keys",
        ok,
        f"keys={sorted(observed)}",
    )


# ── disposable local PostgreSQL (never a Neon branch, never production) ────


class Cluster:
    def __init__(self, port: int, root: Path, bin_dir: Path):
        self.port = port
        self.root = root
        self.data = root / "data"
        self.bin_dir = bin_dir
        self.env = {**os.environ, "LC_ALL": "C"}

    def _bin(self, name: str) -> str:
        return str(self.bin_dir / name)

    def run(self, args: list[str], check_rc: bool = True) -> subprocess.CompletedProcess:
        result = subprocess.run(args, env=self.env, capture_output=True, text=True)
        if check_rc and result.returncode != 0:
            raise RuntimeError(f"command failed ({args!r}): {result.stderr.strip()[-2000:]}")
        return result

    def start(self) -> None:
        self.run([self._bin("initdb"), "-D", str(self.data), "-U", "carr_ci",
                  "--auth=trust", "--encoding=UTF8", "--no-locale"])
        self.run([self._bin("pg_ctl"), "-D", str(self.data), "-l", str(self.root / "postgres.log"),
                  "-o", f"-h 127.0.0.1 -p {self.port}", "-w", "start"])
        self.run([self._bin("createdb"), "-h", "127.0.0.1", "-p", str(self.port), "-U", "carr_ci", "carr_ci"])
        self.run([self._bin("createdb"), "-h", "127.0.0.1", "-p", str(self.port), "-U", "carr_ci", "carr_ci_other"])

    def stop(self) -> None:
        try:
            self.run([self._bin("pg_ctl"), "-D", str(self.data), "-m", "fast", "-w", "stop"], check_rc=False)
        finally:
            shutil.rmtree(self.root, ignore_errors=True)

    def dsn(self, database: str = "carr_ci") -> str:
        return f"postgres://carr_ci@127.0.0.1:{self.port}/{database}"


def find_pg_bin_dir() -> Path:
    for candidate in (
        Path("/opt/homebrew/opt/postgresql@17/bin"),
        Path("/opt/homebrew/opt/postgresql@16/bin"),
        Path("/usr/local/opt/postgresql@17/bin"),
        Path("/usr/local/opt/postgresql@16/bin"),
    ):
        if (candidate / "initdb").is_file():
            return candidate
    located = shutil.which("initdb")
    if located:
        return Path(located).resolve().parent
    sys.exit("breakglass_selftest: no local PostgreSQL server binaries found (install postgresql@17)")


def port_is_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def pick_port() -> int:
    # NEVER 55432 — memory note: that is almost always another session's
    # disposable cluster on this Mac, not a problem with a private choice.
    base = int(os.environ.get("CARR_BREAKGLASS_TEST_PG_PORT", "55471"))
    for candidate in range(base, base + 40, 4):
        if port_is_free(candidate):
            return candidate
    sys.exit("breakglass_selftest: no free local port found for the disposable cluster")


# ── fixture schema ───────────────────────────────────────────────────────


FIXTURE_SQL = """
create schema bgtest;

create table bgtest.control_row (
  id int primary key,
  val text,
  note text
);
insert into bgtest.control_row values (1, 'old-value', 'row one'), (2, 'untouched', 'row two');

create table bgtest.plain_table (
  id int primary key,
  name text
);
insert into bgtest.plain_table values (1, 'alpha');

create table bgtest.rls_table (
  id int primary key,
  name text
);

create role bgtest_grantee;

create function bgtest.secdef_fn() returns int
language sql security definer as $$ select 1 $$;

create view bgtest.v1 as select 1 as x;

create type bgtest.mood as enum ('happy', 'sad');

create index idx_partial on bgtest.control_row (val) where val = 'old-value';

create function bgtest.trig_fn() returns trigger language plpgsql as $$
begin
  return new;
end;
$$;
create trigger bgtest_trig before update on bgtest.control_row
  for each row execute function bgtest.trig_fn();

create sequence bgtest.seq1 start 1 increment 1;
select setval('bgtest.seq1', 5, true);

create sequence bgtest.seq2 start 1 increment 1;

create sequence bgtest.seq3 start 1 increment 1;
select setval('bgtest.seq3', 1, true);
"""


def install_fixture(dsn: str) -> None:
    with psycopg.connect(dsn, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(FIXTURE_SQL)


# ── helpers shared by many tests ─────────────────────────────────────────


def fetch_family_row(dsn: str, family: str, identity_key: str) -> dict | None:
    blocks = breakglass_run.load_snapshot_blocks()
    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(blocks[family])
            rows: dict[str, Any] = dict(cur.fetchall())
    return rows.get(identity_key)


def fetch_row_image(dsn: str, table: str, key: dict) -> dict | None:
    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            return breakglass_run.fetch_row_target_image(cur, table, key)


def write_text(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def write_json(path: Path, obj) -> Path:
    return write_text(path, json.dumps(obj, indent=2))


def gen(*args: str, expect_ok: bool = True) -> subprocess.CompletedProcess:
    result = subprocess.run(
        [PYTHON, "-c", _LAUNCH_SHIM.format(module=GENERATOR_MODULE), str(HERE), *args],
        capture_output=True, text=True,
    )
    if expect_ok and result.returncode != 0:
        raise RuntimeError(f"gen_breakglass_run failed unexpectedly: {result.stderr}")
    return result


def drive(approved: Path, receipt: Path, dsn: str, extra_env: dict | None = None) -> subprocess.CompletedProcess:
    env = {**os.environ, "DATABASE_URL": dsn}
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [PYTHON, "-c", _LAUNCH_SHIM.format(module=DRIVER_MODULE), str(HERE),
         "--approved", str(approved), "--receipt", str(receipt)],
        capture_output=True, text=True, env=env,
    )


def load_receipt(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def row_target(table: str, key: dict, expected_pre, expected_post) -> dict:
    return {"kind": "row", "table": table, "key": key, "expected_pre": expected_pre, "expected_post": expected_post}


def definition_target(family: str, identity_key: str, expected_pre, expected_post) -> dict:
    return {"kind": "definition", "family": family, "identity_key": identity_key,
            "expected_pre": expected_pre, "expected_post": expected_post}


def gen_and_run(tag: str, candidate_sql: str, targets: list[dict], dsn: str, extra_env: dict | None = None):
    candidate_path = write_text(WORK_DIR / f"{tag}.candidate.sql", candidate_sql)
    manifest_path = write_json(WORK_DIR / f"{tag}.manifest.json", {"targets": targets})
    bundle_path = WORK_DIR / f"{tag}.run.sql"
    gen("--candidate", str(candidate_path), "--manifest", str(manifest_path),
        "--wr-note-ref", f"test:{tag}", "--out", str(bundle_path))
    receipt_path = WORK_DIR / f"{tag}.receipt.json"
    result = drive(bundle_path, receipt_path, dsn, extra_env=extra_env)
    receipt = load_receipt(receipt_path) if receipt_path.is_file() else None
    return result, receipt, bundle_path, receipt_path


# ── acceptance (3): instrument tests on a clone ─────────────────────────


def test_undeclared_secdef_grant_aborts(dsn: str) -> None:
    result, receipt, *_ = gen_and_run(
        "secdef-grant",
        "GRANT EXECUTE ON FUNCTION bgtest.secdef_fn() TO bgtest_grantee;",
        [], dsn,
    )
    ok = receipt is not None and receipt.get("verdict") == "rolled_back" and any(
        v["family"] == "pg_proc" and v["identity_key"] == "bgtest.secdef_fn()"
        for v in receipt.get("undeclared_surface_changes", [])
    )
    check("undeclared_secdef_grant_aborts", "verdict=rolled_back, pg_proc undeclared change reported", ok,
          f"rc={result.returncode} verdict={receipt.get('verdict') if receipt else None}")


def test_wrapper_mediated_control_row_mutation_aborts(dsn: str) -> None:
    result, receipt, *_ = gen_and_run(
        "control-row-mutation",
        "UPDATE bgtest.control_row SET val = 'sneaky' WHERE id = 2;",
        [], dsn,
    )
    ok = receipt is not None and receipt.get("verdict") == "rolled_back" and any(
        v["table"] == "bgtest.control_row" for v in receipt.get("undeclared_table_digest_changes", [])
    )
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("select val from bgtest.control_row where id = 2")
        row = cur.fetchone()
        assert row is not None, "bgtest.control_row id=2 is seeded by FIXTURE_SQL and must exist"
        (val,) = row
    ok = ok and val == "untouched"
    check("wrapper_mediated_control_row_mutation_aborts",
          "verdict=rolled_back, row 2 unchanged in the database", ok,
          f"rc={result.returncode} verdict={receipt.get('verdict') if receipt else None} live_val={val}")


def test_view_body_change_aborts(dsn: str) -> None:
    result, receipt, *_ = gen_and_run(
        "view-body-change",
        "CREATE OR REPLACE VIEW bgtest.v1 AS SELECT 2 AS x;",
        [], dsn,
    )
    ok = receipt is not None and receipt.get("verdict") == "rolled_back" and any(
        v["family"] == "pg_rewrite" and v["identity_key"] == "bgtest.v1._RETURN"
        for v in receipt.get("undeclared_surface_changes", [])
    )
    check("view_body_change_aborts", "verdict=rolled_back, pg_rewrite bgtest.v1._RETURN reported", ok,
          f"rc={result.returncode} verdict={receipt.get('verdict') if receipt else None}")


def test_rls_enable_on_undeclared_table_aborts(dsn: str) -> None:
    result, receipt, *_ = gen_and_run(
        "rls-enable",
        "ALTER TABLE bgtest.rls_table ENABLE ROW LEVEL SECURITY;",
        [], dsn,
    )
    ok = receipt is not None and receipt.get("verdict") == "rolled_back" and any(
        v["family"] == "pg_class" and v["identity_key"] == "bgtest.rls_table"
        for v in receipt.get("undeclared_surface_changes", [])
    )
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("select relrowsecurity from pg_class where oid = 'bgtest.rls_table'::regclass")
        row = cur.fetchone()
        assert row is not None, "bgtest.rls_table is seeded by FIXTURE_SQL and must exist"
        (rls,) = row
    ok = ok and rls is False
    check("rls_enable_on_undeclared_table_aborts", "verdict=rolled_back, RLS not actually enabled", ok,
          f"rc={result.returncode} verdict={receipt.get('verdict') if receipt else None} live_relrowsecurity={rls}")


def test_trigger_disable_on_undeclared_table_aborts(dsn: str) -> None:
    result, receipt, *_ = gen_and_run(
        "trigger-disable",
        "ALTER TABLE bgtest.control_row DISABLE TRIGGER bgtest_trig;",
        [], dsn,
    )
    ok = receipt is not None and receipt.get("verdict") == "rolled_back" and any(
        v["family"] == "pg_trigger" and v["identity_key"] == "bgtest.control_row.bgtest_trig"
        for v in receipt.get("undeclared_surface_changes", [])
    )
    check("trigger_disable_on_undeclared_table_aborts", "verdict=rolled_back, pg_trigger tgenabled reported", ok,
          f"rc={result.returncode} verdict={receipt.get('verdict') if receipt else None}")


def test_undeclared_enum_value_addition_aborts(dsn: str) -> None:
    result, receipt, *_ = gen_and_run(
        "enum-add",
        "ALTER TYPE bgtest.mood ADD VALUE 'meh';",
        [], dsn,
    )
    ok = receipt is not None and receipt.get("verdict") == "rolled_back" and any(
        v["family"] == "pg_enum" and v["identity_key"] == "bgtest.mood.meh"
        for v in receipt.get("undeclared_surface_changes", [])
    )
    check("undeclared_enum_value_addition_aborts", "verdict=rolled_back, new pg_enum row reported", ok,
          f"rc={result.returncode} verdict={receipt.get('verdict') if receipt else None}")


def test_undeclared_index_predicate_change_aborts(dsn: str) -> None:
    result, receipt, *_ = gen_and_run(
        "index-predicate-change",
        "DROP INDEX bgtest.idx_partial;\n"
        "CREATE INDEX idx_partial ON bgtest.control_row (val) WHERE val = 'new-predicate-value';",
        [], dsn,
    )
    ok = receipt is not None and receipt.get("verdict") == "rolled_back" and any(
        v["family"] == "pg_index" and v["identity_key"] == "bgtest.idx_partial"
        for v in receipt.get("undeclared_surface_changes", [])
    )
    check("undeclared_index_predicate_change_aborts", "verdict=rolled_back, pg_index indexdef reported", ok,
          f"rc={result.returncode} verdict={receipt.get('verdict') if receipt else None}")


def test_backward_setval_reported(dsn: str) -> None:
    result, receipt, *_ = gen_and_run(
        "backward-setval",
        "SELECT setval('bgtest.seq1', 2, true);",
        [], dsn,
    )
    residual = next((r for r in (receipt or {}).get("sequence_residuals", [])
                      if r["identity_key"] == "bgtest.seq1"), None)
    ok = (receipt is not None and receipt.get("verdict") == "committed"
          and residual is not None and "backward_setval" in residual["observed_kinds"])
    check("backward_setval_reported",
          "verdict=committed (sequences never gate), backward_setval reported, not auto-classified benign", ok,
          f"rc={result.returncode} verdict={receipt.get('verdict') if receipt else None} residual={residual}")


def test_alter_sequence_config_change_reported(dsn: str) -> None:
    result, receipt, *_ = gen_and_run(
        "sequence-config-change",
        "ALTER SEQUENCE bgtest.seq2 INCREMENT BY 5;",
        [], dsn,
    )
    residual = next((r for r in (receipt or {}).get("sequence_residuals", [])
                      if r["identity_key"] == "bgtest.seq2"), None)
    ok = (receipt is not None and receipt.get("verdict") == "committed"
          and residual is not None and "configuration_change" in residual["observed_kinds"])
    check("alter_sequence_config_change_reported",
          "verdict=committed, configuration_change reported", ok,
          f"rc={result.returncode} verdict={receipt.get('verdict') if receipt else None} residual={residual}")


def test_is_called_flip_reported(dsn: str) -> None:
    result, receipt, *_ = gen_and_run(
        "is-called-flip",
        "SELECT setval('bgtest.seq3', 1, false);",
        [], dsn,
    )
    residual = next((r for r in (receipt or {}).get("sequence_residuals", [])
                      if r["identity_key"] == "bgtest.seq3"), None)
    ok = (receipt is not None and receipt.get("verdict") == "committed"
          and residual is not None and "is_called_flip" in residual["observed_kinds"])
    check("is_called_flip_reported", "verdict=committed, is_called_flip reported", ok,
          f"rc={result.returncode} verdict={receipt.get('verdict') if receipt else None} residual={residual}")


def test_declared_view_target_wrong_body_fails_side3(dsn: str) -> None:
    pre_row = fetch_family_row(dsn, "pg_rewrite", "bgtest.v1._RETURN")
    target = definition_target("pg_rewrite", "bgtest.v1._RETURN", expected_pre=pre_row, expected_post=pre_row)
    result, receipt, *_ = gen_and_run(
        "view-wrong-expected-post",
        "CREATE OR REPLACE VIEW bgtest.v1 AS SELECT 3 AS x;",
        [target], dsn,
    )
    ok = (receipt is not None and receipt.get("verdict") == "rolled_back"
          and any(f["target"].startswith("definition:pg_rewrite:") for f in receipt.get("postcheck_failures", [])))
    check("declared_view_target_wrong_body_fails_side3",
          "verdict=rolled_back via postcheck_failures (assertion 3), not the undeclared-change path", ok,
          f"rc={result.returncode} verdict={receipt.get('verdict') if receipt else None} "
          f"postcheck_failures={receipt.get('postcheck_failures') if receipt else None}")


def test_expected_pre_mismatch_refused_before_execution(dsn: str) -> None:
    pre_image = fetch_row_image(dsn, "bgtest.control_row", {"id": 1})
    assert pre_image is not None, "bgtest.control_row id=1 is seeded by FIXTURE_SQL and must exist"
    wrong_pre = {**pre_image, "val": "not-the-real-value"}
    target = row_target("bgtest.control_row", {"id": 1}, expected_pre=wrong_pre, expected_post=pre_image)
    result, receipt, *_ = gen_and_run(
        "wrong-expected-pre",
        "UPDATE bgtest.control_row SET val = 'attempted-change' WHERE id = 1;",
        [target], dsn,
    )
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("select val from bgtest.control_row where id = 1")
        row = cur.fetchone()
        assert row is not None, "bgtest.control_row id=1 is seeded by FIXTURE_SQL and must exist"
        (val,) = row
    ok = (receipt is not None and receipt.get("verdict") == "refused_precheck"
          and "post_snapshot" not in receipt and val == pre_image["val"])
    check("expected_pre_mismatch_refused_before_execution",
          "verdict=refused_precheck, candidate never executed (no post_snapshot, row unchanged)", ok,
          f"rc={result.returncode} verdict={receipt.get('verdict') if receipt else None} live_val={val}")


def test_end_refused_by_lexer(dsn: str) -> None:
    candidate_path = write_text(WORK_DIR / "end-candidate.sql", "UPDATE bgtest.control_row SET val='x' WHERE id=1;\nEND;")
    manifest_path = write_json(WORK_DIR / "end-manifest.json", {"targets": []})
    result = gen("--candidate", str(candidate_path), "--manifest", str(manifest_path),
                 "--out", str(WORK_DIR / "end.run.sql"), expect_ok=False)
    ok = result.returncode != 0 and "transaction control" in result.stderr
    check("end_refused_by_lexer",
          "gen_breakglass_run refuses (contains_transaction_control, imported verbatim from tools/migrate.py)", ok,
          f"rc={result.returncode} stderr={result.stderr.strip()[-300:]}")


def test_do_block_passes_lexer(dsn: str) -> None:
    pre_image = fetch_row_image(dsn, "bgtest.control_row", {"id": 1})
    assert pre_image is not None, "bgtest.control_row id=1 is seeded by FIXTURE_SQL and must exist"
    expected_post = {**pre_image, "note": "touched-by-do-block"}
    target = row_target("bgtest.control_row", {"id": 1}, expected_pre=pre_image, expected_post=expected_post)
    do_block = (
        "do $$\n"
        "begin\n"
        "  update bgtest.control_row set note = 'touched-by-do-block' where id = 1;\n"
        "end\n"
        "$$;"
    )
    candidate_path = write_text(WORK_DIR / "do-block-candidate.sql", do_block)
    manifest_path = write_json(WORK_DIR / "do-block-manifest.json", {"targets": [target]})
    gen_result = gen("--candidate", str(candidate_path), "--manifest", str(manifest_path),
                      "--out", str(WORK_DIR / "do-block.run.sql"))
    receipt_path = WORK_DIR / "do-block.receipt.json"
    run_result = drive(WORK_DIR / "do-block.run.sql", receipt_path, dsn)
    receipt = load_receipt(receipt_path) if receipt_path.is_file() else None
    ok = gen_result.returncode == 0 and receipt is not None and receipt.get("verdict") == "committed"
    check("do_block_passes_lexer",
          "0473-shaped do $$ begin ... end $$ is NOT refused, and the declared change commits", ok,
          f"gen_rc={gen_result.returncode} run_rc={run_result.returncode} "
          f"verdict={receipt.get('verdict') if receipt else None}")


def test_categorical_floor_refuses_scac_target(dsn: str) -> None:
    """Bonus coverage beyond the named acceptance list (F01 condition (d)'s
    categorical floor): a target inside ops.scac_* is refused a generated
    run entirely, outside --restore."""
    target = row_target("ops.scac_mutation_registry_version", {"registry_version": "x"}, None, None)
    candidate_path = write_text(WORK_DIR / "floor-candidate.sql", "SELECT 1;")
    manifest_path = write_json(WORK_DIR / "floor-manifest.json", {"targets": [target]})
    result = gen("--candidate", str(candidate_path), "--manifest", str(manifest_path),
                 "--out", str(WORK_DIR / "floor.run.sql"), expect_ok=False)
    ok = result.returncode != 0 and "categorical floor" in result.stderr
    check("categorical_floor_refuses_scac_target",
          "gen_breakglass_run refuses a target inside ops.scac_* outside --restore", ok,
          f"rc={result.returncode} stderr={result.stderr.strip()[-300:]}")


# ── acceptance (3b): durability fault ────────────────────────────────────


def test_durability_fault_prephase_receipt_survives_kill(dsn: str) -> None:
    pre_image = fetch_row_image(dsn, "bgtest.control_row", {"id": 1})
    assert pre_image is not None, "bgtest.control_row id=1 is seeded by FIXTURE_SQL and must exist"
    target = row_target("bgtest.control_row", {"id": 1}, expected_pre=pre_image,
                         expected_post={**pre_image, "val": "durability-test"})
    candidate_path = write_text(WORK_DIR / "durability-candidate.sql",
                                 "UPDATE bgtest.control_row SET val = 'durability-test' WHERE id = 1;")
    manifest_path = write_json(WORK_DIR / "durability-manifest.json", {"targets": [target]})
    gen("--candidate", str(candidate_path), "--manifest", str(manifest_path),
        "--out", str(WORK_DIR / "durability.run.sql"))
    receipt_path = WORK_DIR / "durability.receipt.json"
    if receipt_path.exists():
        receipt_path.unlink()
    result = drive(WORK_DIR / "durability.run.sql", receipt_path, dsn,
                    extra_env={"CARR_BREAKGLASS_TEST_KILL_AFTER_PREPHASE": "1"})
    killed_as_expected = result.returncode == 137
    receipt_present = receipt_path.is_file()
    receipt = load_receipt(receipt_path) if receipt_present else None
    prephase_shape_ok = (
        receipt is not None
        and "verdict" not in receipt
        and "candidate_sql" in receipt
        and "manifest" in receipt
        and "identity_tuple" in receipt
        and "pre_snapshot" in receipt
        and "pre_images" in receipt
    )
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("select val from bgtest.control_row where id = 1")
        row = cur.fetchone()
        assert row is not None, "bgtest.control_row id=1 is seeded by FIXTURE_SQL and must exist"
        (val,) = row
    db_untouched = val == pre_image["val"]
    ok = killed_as_expected and receipt_present and prephase_shape_ok and db_untouched
    check("durability_fault_prephase_receipt_survives_kill",
          "killed right after the pre-phase fsync: pre-phase receipt present/readable, DB unaffected", ok,
          f"killed={killed_as_expected} present={receipt_present} shape_ok={prephase_shape_ok} "
          f"db_untouched={db_untouched} live_val={val}")


# ── acceptance (3c): double-apply ────────────────────────────────────────


def test_double_apply(dsn: str) -> None:
    pre_alpha = fetch_row_image(dsn, "bgtest.plain_table", {"id": 1})
    assert pre_alpha is not None, "bgtest.plain_table id=1 is seeded by FIXTURE_SQL and must exist"
    post_beta = {**pre_alpha, "name": "beta"}
    target_pass_one = row_target("bgtest.plain_table", {"id": 1}, expected_pre=pre_alpha, expected_post=post_beta)
    candidate = "UPDATE bgtest.plain_table SET name = 'beta' WHERE id = 1;"
    candidate_path = write_text(WORK_DIR / "double-apply-candidate.sql", candidate)
    manifest_one_path = write_json(WORK_DIR / "double-apply-pass1-manifest.json", {"targets": [target_pass_one]})
    gen("--candidate", str(candidate_path), "--manifest", str(manifest_one_path),
        "--out", str(WORK_DIR / "double-apply-pass1.run.sql"))
    receipt1_path = WORK_DIR / "double-apply-pass1.receipt.json"
    run1 = drive(WORK_DIR / "double-apply-pass1.run.sql", receipt1_path, dsn)
    receipt1 = load_receipt(receipt1_path) if receipt1_path.is_file() else None
    pass1_ok = receipt1 is not None and receipt1.get("verdict") == "committed"

    # Pass two: companion POST -> POST manifest (empty expected transactional delta).
    now_beta = fetch_row_image(dsn, "bgtest.plain_table", {"id": 1})
    target_pass_two = row_target("bgtest.plain_table", {"id": 1}, expected_pre=now_beta, expected_post=now_beta)
    manifest_two_path = write_json(
        WORK_DIR / "double-apply-pass2-manifest.json",
        {"targets": [target_pass_two], "expected_transactional_delta": "empty"},
    )
    gen("--candidate", str(candidate_path), "--manifest", str(manifest_two_path),
        "--out", str(WORK_DIR / "double-apply-pass2.run.sql"))
    receipt2_path = WORK_DIR / "double-apply-pass2.receipt.json"
    run2 = drive(WORK_DIR / "double-apply-pass2.run.sql", receipt2_path, dsn)
    receipt2 = load_receipt(receipt2_path) if receipt2_path.is_file() else None
    pass2_ok = receipt2 is not None and receipt2.get("verdict") == "committed"

    ok = pass1_ok and pass2_ok
    check("double_apply_pass_two_companion_manifest_passes",
          "pass one commits, pass two under the companion POST->POST manifest also commits", ok,
          f"pass1_verdict={receipt1.get('verdict') if receipt1 else None} "
          f"pass2_verdict={receipt2.get('verdict') if receipt2 else None}")

    # Pass two mistakenly run under pass one's PRE -> POST manifest: world is
    # already at POST ('beta'), so the precheck must refuse.
    receipt3_path = WORK_DIR / "double-apply-pass2-wrong-manifest.receipt.json"
    run3 = drive(WORK_DIR / "double-apply-pass1.run.sql", receipt3_path, dsn)
    receipt3 = load_receipt(receipt3_path) if receipt3_path.is_file() else None
    ok3 = receipt3 is not None and receipt3.get("verdict") == "refused_precheck"
    check("double_apply_pass_two_under_pre_to_post_manifest_refused",
          "re-running pass one's PRE->POST bundle after pass one already committed is refused by the precheck", ok3,
          f"rc={run3.returncode} verdict={receipt3.get('verdict') if receipt3 else None}")


# ── acceptance (4): restore tests ────────────────────────────────────────


def commit_declared_update(dsn: str, tag: str, table: str, key: dict, new_val: dict) -> tuple[dict, Path]:
    pre_image = fetch_row_image(dsn, table, key)
    assert pre_image is not None, f"commit_declared_update contract: {table} {key} must identify an existing row"
    post_image = {**pre_image, **new_val}
    set_clause = ", ".join(f"{col} = '{val}'" for col, val in new_val.items())
    where = " AND ".join(f"{col} = {val!r}" if isinstance(val, str) else f"{col} = {val}" for col, val in key.items())
    candidate = f"UPDATE {table} SET {set_clause} WHERE {where};"
    target = row_target(table, key, expected_pre=pre_image, expected_post=post_image)
    result, receipt, bundle_path, receipt_path = gen_and_run(tag, candidate, [target], dsn)
    if receipt is None or receipt.get("verdict") != "committed":
        raise RuntimeError(f"setup commit for {tag} did not commit: {receipt}")
    return receipt, receipt_path


def test_restore_success_commits(dsn: str) -> tuple[dict, Path] | None:
    pre_before = fetch_row_image(dsn, "bgtest.control_row", {"id": 1})
    assert pre_before is not None, "bgtest.control_row id=1 is seeded by FIXTURE_SQL and must exist"
    receipt, receipt_path = commit_declared_update(
        dsn, "restore-setup-a", "bgtest.control_row", {"id": 1}, {"val": "restore-target-value"}
    )
    restore_bundle = WORK_DIR / "restore-a.run.sql"
    gen_result = gen("--restore", str(receipt_path), "--out", str(restore_bundle))
    restore_receipt_path = WORK_DIR / "restore-a.receipt.json"
    run_result = drive(restore_bundle, restore_receipt_path, dsn)
    restore_receipt = load_receipt(restore_receipt_path) if restore_receipt_path.is_file() else None
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("select val from bgtest.control_row where id = 1")
        row = cur.fetchone()
        assert row is not None, "bgtest.control_row id=1 is seeded by FIXTURE_SQL and must exist"
        (val,) = row
    ok = (gen_result.returncode == 0 and restore_receipt is not None
          and restore_receipt.get("verdict") == "committed" and val == pre_before["val"])
    check("restore_success_commits",
          "restore run commits and the row's val is back to its pre-incident value", ok,
          f"gen_rc={gen_result.returncode} verdict={restore_receipt.get('verdict') if restore_receipt else None} "
          f"live_val={val} expected={pre_before['val']}")
    return receipt, receipt_path


def test_restore_identity_mismatch_both_axes(dsn: str, other_dsn: str, receipt_path: Path) -> None:
    # Axis 1: same cluster, different database.
    bundle_db = WORK_DIR / "restore-b-db.run.sql"
    gen("--restore", str(receipt_path), "--out", str(bundle_db))
    receipt_db_path = WORK_DIR / "restore-b-db.receipt.json"
    result_db = drive(bundle_db, receipt_db_path, other_dsn)
    receipt_db = load_receipt(receipt_db_path) if receipt_db_path.is_file() else None
    ok_db = (receipt_db is not None and receipt_db.get("verdict") == "refused_identity_mismatch"
             and "database_name" in receipt_db.get("identity_mismatch_fields", []))
    check("restore_identity_mismatch_different_database",
          "refused_identity_mismatch naming database_name", ok_db,
          f"rc={result_db.returncode} verdict={receipt_db.get('verdict') if receipt_db else None} "
          f"fields={receipt_db.get('identity_mismatch_fields') if receipt_db else None}")

    # Axis 2: same database, different endpoint host (simulated by editing
    # the generated bundle's embedded manifest — the plan's own words for
    # how this axis is tested off a local disposable that has no real Neon
    # endpoint host).
    bundle_host = WORK_DIR / "restore-b-host.run.sql"
    gen("--restore", str(receipt_path), "--out", str(bundle_host))
    text = bundle_host.read_text(encoding="utf-8")
    manifest, candidate_sql = breakglass_run.parse_bundle(text)
    manifest["identity_requirement"]["endpoint_host"] = "ep-imposter-host.neon.tech"
    bundle_host.write_text(breakglass_run.build_bundle(manifest, candidate_sql), encoding="utf-8")
    receipt_host_path = WORK_DIR / "restore-b-host.receipt.json"
    result_host = drive(bundle_host, receipt_host_path, dsn)
    receipt_host = load_receipt(receipt_host_path) if receipt_host_path.is_file() else None
    ok_host = (receipt_host is not None and receipt_host.get("verdict") == "refused_identity_mismatch"
               and "endpoint_host" in receipt_host.get("identity_mismatch_fields", []))
    check("restore_identity_mismatch_different_endpoint_host",
          "refused_identity_mismatch naming endpoint_host (edited receipt copy)", ok_host,
          f"rc={result_host.returncode} verdict={receipt_host.get('verdict') if receipt_host else None} "
          f"fields={receipt_host.get('identity_mismatch_fields') if receipt_host else None}")


def test_restore_wrong_row_receipt_refused() -> None:
    malformed = write_json(WORK_DIR / "wrong-row-receipt.json", {"verdict": "rolled_back"})
    result = gen("--restore", str(malformed), "--out", str(WORK_DIR / "wrong-row.run.sql"), expect_ok=False)
    ok = result.returncode != 0 and "wrong-row receipt" in result.stderr
    check("restore_wrong_row_receipt_refused",
          "gen_breakglass_run --restore refuses a malformed/non-committed receipt, naming it a wrong-row receipt",
          ok, f"rc={result.returncode} stderr={result.stderr.strip()[-300:]}")


def test_restore_compare_and_swap_refusal(dsn: str, receipt_path: Path) -> None:
    # receipt_path's incident already moved the world from X to
    # 'restore-target-value', and test_restore_success_commits already moved
    # it BACK to X — so the world no longer matches this receipt's recorded
    # POST-state, exactly the "target changed after the incident" case.
    bundle = WORK_DIR / "restore-d-cas.run.sql"
    gen("--restore", str(receipt_path), "--out", str(bundle))
    receipt_out_path = WORK_DIR / "restore-d-cas.receipt.json"
    result = drive(bundle, receipt_out_path, dsn)
    receipt = load_receipt(receipt_out_path) if receipt_out_path.is_file() else None
    ok = receipt is not None and receipt.get("verdict") == "refused_precheck"
    check("restore_compare_and_swap_refusal",
          "restoring an incident whose target has since moved on is refused by the expected-pre precheck", ok,
          f"rc={result.returncode} verdict={receipt.get('verdict') if receipt else None}")


def test_restore_collateral_undeclared_effect_aborts(dsn: str) -> None:
    receipt, receipt_path = commit_declared_update(
        dsn, "restore-setup-e", "bgtest.control_row", {"id": 1}, {"val": "restore-collateral-value"}
    )
    bundle = WORK_DIR / "restore-e.run.sql"
    gen("--restore", str(receipt_path), "--out", str(bundle))
    text = bundle.read_text(encoding="utf-8")
    manifest, candidate_sql = breakglass_run.parse_bundle(text)
    tampered_candidate = candidate_sql + "\nUPDATE bgtest.plain_table SET name = 'collateral-damage' WHERE id = 1;"
    bundle.write_text(breakglass_run.build_bundle(manifest, tampered_candidate), encoding="utf-8")
    receipt_out_path = WORK_DIR / "restore-e.receipt.json"
    result = drive(bundle, receipt_out_path, dsn)
    restore_receipt = load_receipt(receipt_out_path) if receipt_out_path.is_file() else None
    ok = (restore_receipt is not None and restore_receipt.get("verdict") == "rolled_back"
          and any(v["table"] == "bgtest.plain_table" for v in restore_receipt.get("undeclared_table_digest_changes", [])))
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("select val from bgtest.control_row where id = 1")
        row = cur.fetchone()
        assert row is not None, "bgtest.control_row id=1 is seeded by FIXTURE_SQL and must exist"
        (val,) = row
    ok = ok and val == "restore-collateral-value"  # the declared restore itself must ALSO have rolled back
    check("restore_collateral_undeclared_effect_aborts",
          "a restore candidate with a tampered-in collateral edit aborts entirely (atomic rollback)", ok,
          f"rc={result.returncode} verdict={restore_receipt.get('verdict') if restore_receipt else None} live_val={val}")


# ── main ──────────────────────────────────────────────────────────────────


def main() -> int:
    if WORK_DIR.exists():
        shutil.rmtree(WORK_DIR)
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    if TEST_RECEIPTS_PATH.exists():
        TEST_RECEIPTS_PATH.unlink()

    print("breakglass_selftest: receipt-map regression tests")
    test_duplicate_snapshot_identities_are_lossless()

    bin_dir = find_pg_bin_dir()
    port = pick_port()
    root = Path(tempfile.mkdtemp(prefix="carr-breakglass_selftest."))
    cluster = Cluster(port=port, root=root, bin_dir=bin_dir)
    print(f"breakglass_selftest: starting disposable PostgreSQL on 127.0.0.1:{port} (never Neon, never production)")
    cluster.start()
    try:
        dsn = cluster.dsn("carr_ci")
        other_dsn = cluster.dsn("carr_ci_other")
        install_fixture(dsn)

        print("breakglass_selftest: acceptance (3) — instrument tests")
        test_undeclared_secdef_grant_aborts(dsn)
        test_wrapper_mediated_control_row_mutation_aborts(dsn)
        test_view_body_change_aborts(dsn)
        test_rls_enable_on_undeclared_table_aborts(dsn)
        test_trigger_disable_on_undeclared_table_aborts(dsn)
        test_undeclared_enum_value_addition_aborts(dsn)
        test_undeclared_index_predicate_change_aborts(dsn)
        test_backward_setval_reported(dsn)
        test_alter_sequence_config_change_reported(dsn)
        test_is_called_flip_reported(dsn)
        test_declared_view_target_wrong_body_fails_side3(dsn)
        test_expected_pre_mismatch_refused_before_execution(dsn)
        test_end_refused_by_lexer(dsn)
        test_do_block_passes_lexer(dsn)
        test_categorical_floor_refuses_scac_target(dsn)

        print("breakglass_selftest: acceptance (3b) — durability fault")
        test_durability_fault_prephase_receipt_survives_kill(dsn)

        print("breakglass_selftest: acceptance (3c) — double-apply")
        test_double_apply(dsn)

        print("breakglass_selftest: acceptance (4) — restore tests")
        setup_a = test_restore_success_commits(dsn)
        if setup_a is not None:
            _receipt_a, receipt_a_path = setup_a
            test_restore_identity_mismatch_both_axes(dsn, other_dsn, receipt_a_path)
            test_restore_compare_and_swap_refusal(dsn, receipt_a_path)
        test_restore_wrong_row_receipt_refused()
        test_restore_collateral_undeclared_effect_aborts(dsn)
    finally:
        with open(TEST_RECEIPTS_PATH, "w", encoding="utf-8") as fh:
            for row in RESULTS:
                fh.write(json.dumps(row) + "\n")
        print(f"breakglass_selftest: stopping disposable PostgreSQL on 127.0.0.1:{port}")
        cluster.stop()

    failures = [r for r in RESULTS if not r["pass"]]
    print(f"breakglass_selftest: {len(RESULTS) - len(failures)}/{len(RESULTS)} passed")
    if failures:
        print("breakglass_selftest: FAILURES:")
        for f in failures:
            print(f"  - {f['test']}: {f['observed']}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
