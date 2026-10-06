"""gen_frontier_manifest.py — WR-000046 Artifact A, OBSERVED effect manifest.

Builds a disposable local PostgreSQL 17, loads db/schema.sql from the pinned
commit (0985dcc70764d888d70004641e210f3730ef9d2a), applies the pre-frontier
pending prefix (0427-0453) with tools/migrate.py, snapshots the full catalog
surface, then applies each of the eighteen frontier files (0454-0471) ONE AT
A TIME, snapshotting after each so every observed effect is attributed to its
file. Emits frontier-touched-objects.v1.json: the created/altered/removed
targets (identity strings per docs/frontier-finding/target-identity-contract.md
— actually build-specs/target-identity-contract.md), the by_file attribution
map, and a detail section (definition digests/texts, ACL deltas, row deltas,
a best-effort pg_depend closure).

CLAIM DISCIPLINE (gated-register-plan.md Section T): this generator makes NO
claim of mechanical completeness. Its snapshot surface is exactly Artifact
C's reviewed, extendable surface (docs/frontier-finding/breakglass-snapshot.sql)
plus pg_depend, pg_description (comments — an explicit, named extension: the
Artifact C surface does not include pg_description, and the target-identity
contract needs a `comment:` form; adding it here is documented, not implied
by the plan text) and per-table row content. Gaps found later are named, not
hidden.

DETERMINISM. The observed target/by_file/detail sections must be byte-identical
across two independent fresh disposables. Two classes of value are NOT
reproducible across separate cluster instances and are excluded from any
digest or stored content, replaced with the literal placeholder string
"<volatile:normalized>":
  (1) any column whose DEFAULT expression (captured from pg_attrdef) invokes a
      nondeterministic function — now(), clock_timestamp(), current_timestamp,
      statement_timestamp(), transaction_timestamp(), localtimestamp,
      gen_random_uuid(), uuid_generate_v4(), random() — detected by regex
      against the captured default text, not hand-maintained per table;
  (2) raw pg_catalog OID-typed columns (relnamespace, proowner, atttypid, ...)
      — cluster-instance-assigned counters, never reproducible across two
      separately-initdb'd clusters even loading byte-identical DDL. These are
      never used for identity or digest computation in the first place
      (identities are name-based; digests are computed over pg_get_*def()
      canonical text, which is itself name-based) but the raw per-family
      snapshot rows captured verbatim from breakglass-snapshot.sql DO carry
      them, and this generator's own supplemental detail rendering strips or
      resolves them to names rather than emitting raw oids into the artifact.
This list is reviewed and extendable (Section T); it is not claimed complete.
A second, empirical safety net (--reconcile) diffs two independent runs field
by field and FAILS LOUDLY if anything differs that this list does not explain
-- it does not silently paper over an unexplained difference.

USAGE:
    # one full run (build disposable, apply, snapshot, diff, emit a run manifest)
    python3 gen_frontier_manifest.py run --port 55901 --out /path/run1.json \\
        --apply-log /path/apply-log-run1.txt

    # a second independent run
    python3 gen_frontier_manifest.py run --port 55902 --out /path/run2.json \\
        --apply-log /path/apply-log-run2.txt

    # reconcile: assert byte-identical (after normalization), emit the
    # final frontier-touched-objects.v1.json
    python3 gen_frontier_manifest.py reconcile --run-a /path/run1.json \\
        --run-b /path/run2.json --out frontier-touched-objects.v1.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, NoReturn

REPO_ROOT = Path(__file__).resolve().parents[2]
GENERATOR_PATH = Path(__file__).resolve()
PINNED_COMMIT = "0985dcc70764d888d70004641e210f3730ef9d2a"
PRE_FRONTIER_THROUGH = "0453_siep06a_evidence_graph.sql"
FRONTIER_FILES = [
    "0454_siep11_mutation_registry.sql",
    "0455_siep12_policy_epoch.sql",
    "0456_siep13_artifact_registry.sql",
    "0457_siep13_forward_mutation_registry.sql",
    "0458_siep14_root_trust.sql",
    "0459_siep14_forward_mutation_registry.sql",
    "0460_siep15_device_enrollment.sql",
    "0461_siep15_forward_mutation_registry.sql",
    "0462_siep16_forward_mutation_registry.sql",
    "0463_retired_rule_delivery_cleanup.sql",
    "0464_siep16_integrated_mutation_registry.sql",
    "0465_siep17_token_challenge_authority.sql",
    "0466_siep17_forward_mutation_registry.sql",
    "0467_siep18_atomic_db_monitor_grants.sql",
    "0468_siep18_forward_mutation_registry.sql",
    "0469_siep18_exact_effects_trusted_principal.sql",
    "0470_source_merge_authority_projection.sql",
    "0471_source_merge_catalog_registry_successor.sql",
]
FORBIDDEN_PORTS = {55432, 55471, 55472, 55473, 55474, 55475, 55710}

SNAPSHOT_SQL_PATH = Path(__file__).resolve().parent / "breakglass-snapshot.sql"
CENSUS_SQL_PATH = Path(__file__).resolve().parent / "census-queries.sql"

VOLATILE_PLACEHOLDER = "<volatile:normalized>"
VOLATILE_DEFAULT_RE = re.compile(
    r"(?i)\b(now|clock_timestamp|current_timestamp|statement_timestamp|"
    r"transaction_timestamp|localtimestamp|gen_random_uuid|uuid_generate_v4|random)\s*\("
)
# A second, content-shape-based rule (not schema-based): a value that IS an
# ISO-8601 timestamptz literal is normalized regardless of whether the
# defining column carries a DDL DEFAULT. Discovered empirically (--reconcile):
# ops.enforcement_control_catalog.verified_at is set via an inline
# `coalesce(verified_at, now())` inside a DML/upsert function body (migrations
# 0194/0470), not a column default, so the DDL-default scan above cannot see
# it. Applying this by VALUE SHAPE rather than chasing every DML call site is
# deliberately broader; the tradeoff (documented per Section T) is that a
# genuinely meaningful, intentionally-literal timestamptz value stored by a
# migration would also be normalized away here. No frontier file seeds a
# timestamp value whose exact instant is part of its identity (verified
# by inspection of migrations/0454-0471 at the pinned commit); all observed
# timestamptz content is bookkeeping (created_at/updated_at/verified_at/
# sealed_at-shaped columns).
ISO_TIMESTAMPTZ_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(\.\d+)?([+-]\d{2}:?\d{2}|Z)$"
)

# Named, reviewed volatile-normalization rule set (Section T: reviewed, extendable,
# never claimed complete). Rule (1) is schema-driven (see VOLATILE_DEFAULT_RE);
# rule (2) is the fixed set of raw-oid-typed catalog column names this generator
# never stores verbatim in "detail" content.
VOLATILE_NORMALIZATION_RULES = [
    {
        "id": "now_defaulted_columns",
        "rule": "any column whose pg_attrdef default expression matches "
                "now()/clock_timestamp()/current_timestamp/statement_timestamp()/"
                "transaction_timestamp()/localtimestamp/gen_random_uuid()/"
                "uuid_generate_v4()/random() (case-insensitive) is replaced with "
                f"the literal {VOLATILE_PLACEHOLDER!r} in stored row content and "
                "excluded from any content digest",
        "justification": "these functions are nondeterministic by design; the "
                          "column's presence and type are schema (reproducible), "
                          "the column's VALUE at a given moment is not",
        "discovered_by": "static inspection of pg_attrdef.def_expr at snapshot "
                          "time, cross-checked empirically by --reconcile "
                          "(a two-run field diff) which fails loudly on any "
                          "unexplained difference",
    },
    {
        "id": "iso_timestamptz_shaped_values",
        "rule": "any row-content STRING VALUE matching the ISO-8601 timestamptz "
                f"shape is replaced with {VOLATILE_PLACEHOLDER!r} and excluded "
                "from any content digest, REGARDLESS of whether the column has "
                "a DDL default (a value-shape rule, not a schema rule)",
        "justification": "empirically discovered via --reconcile: "
                          "ops.enforcement_control_catalog.verified_at is set "
                          "via an inline `coalesce(verified_at, now())` inside "
                          "a DML/upsert function body (migrations 0194/0470), "
                          "not a column DEFAULT, so the schema-driven rule "
                          "above cannot see it; broadening to value-shape "
                          "catches this and any similar case without chasing "
                          "every DML call site. Verified by inspection of "
                          "migrations/0454-0471 at the pinned commit that no "
                          "frontier file seeds a timestamp literal whose exact "
                          "instant is part of its identity",
        "discovered_by": "the --reconcile double-run empirical diff (one "
                          "unexplained field difference, ops.enforcement_"
                          "control_catalog row verified_at, before this rule "
                          "was added)",
    },
    {
        "id": "raw_pg_catalog_oids",
        "rule": "raw oid-typed pg_catalog columns (relnamespace, relowner, "
                "reltype, relam, pronamespace, proowner, prorettype, "
                "proargtypes, typnamespace, typowner, typrelid, typelem, "
                "typarray, seqrelid, seqtypid, atttypid, attcollation, adrelid, "
                "connamespace, conrelid, contypid, confrelid, conindid, "
                "tgrelid, tgfoid, polrelid, ev_class, defaclnamespace, "
                "defaclrole, enumtypid, partrelid, oid) are never stored in "
                "detail content or used for identity/digest computation; "
                "objects are identified purely by qualified NAME, and "
                "definitions are digested from pg_get_*def() canonical TEXT, "
                "both of which are name-based and reproducible across "
                "separately-initdb'd clusters",
        "justification": "PostgreSQL oid allocation is a cluster-instance "
                          "counter; two independently initialized clusters "
                          "are not guaranteed to assign the same oid to the "
                          "'same' (by name) object even given byte-identical "
                          "DDL replay",
        "discovered_by": "known PostgreSQL catalog design, applied "
                          "proactively rather than discovered by diffing",
    },
]

PG_IDENTIFY_TYPE_TO_PREFIX = {
    "table": "table",
    "view": "view",
    "materialized view": "matview",
    "sequence": "sequence",
    "index": "index",
    "schema": "schema",
    "function": "function",
    "procedure": "function",
}


def strip_spaces_after_commas(s: str) -> str:
    return re.sub(r",\s+", ",", s)


def fail(msg: str) -> NoReturn:
    print(f"gen_frontier_manifest: ERROR: {msg}", file=sys.stderr)
    raise SystemExit(1)


# ───────────────────────── disposable postgres ─────────────────────────

def find_postgres_binaries() -> dict[str, Path]:
    candidates = [
        Path("/opt/homebrew/opt/postgresql@17/bin"),
        Path("/usr/local/opt/postgresql@17/bin"),
        Path("/opt/homebrew/bin"),
        Path("/usr/local/bin"),
    ]
    located = shutil.which("initdb")
    if located:
        candidates.append(Path(located).resolve().parent)
    for directory in candidates:
        paths = {name: directory / name for name in ("initdb", "pg_ctl", "createdb", "psql")}
        if all(p.is_file() and os.access(p, os.X_OK) for p in paths.values()):
            return paths
    fail("postgresql@17 client/server binaries not found")


def port_is_available(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def scrub_env() -> dict[str, str]:
    allowed = {"HOME", "LANG", "LC_CTYPE", "LOGNAME", "PATH", "SHELL", "TERM", "TMPDIR", "USER"}
    env = {k: v for k, v in os.environ.items() if k in allowed}
    env["LC_ALL"] = "C"
    return env


class Disposable:
    """A disposable, loopback-only PostgreSQL 17 cluster. Never a caller DSN."""

    def __init__(self, workdir: Path, port: int):
        if port in FORBIDDEN_PORTS:
            fail(f"port {port} is a forbidden port (55432 / 55471-55475 / 55710)")
        if not port_is_available(port):
            fail(f"127.0.0.1:{port} is already in use — pick a different --port")
        self.bin = find_postgres_binaries()
        self.workdir = workdir
        self.port = port
        self.data = workdir / "pgdata"
        self.log = workdir / "postgres.log"
        self.env = scrub_env()
        self.dbname = "carr_frontier_manifest"
        self.user = "carr_manifest"
        self._started = False

    def dsn(self) -> str:
        return f"postgres://{self.user}@127.0.0.1:{self.port}/{self.dbname}"

    def _run(self, cmd: list, check_msg: str) -> subprocess.CompletedProcess:
        result = subprocess.run(cmd, env=self.env, capture_output=True, text=True)
        if result.returncode != 0:
            fail(f"{check_msg}: rc={result.returncode}\nstdout={result.stdout}\nstderr={result.stderr}")
        return result

    def start(self) -> None:
        self._run(
            [str(self.bin["initdb"]), "-D", str(self.data), "-U", self.user,
             "--auth=trust", "--encoding=UTF8", "--no-locale"],
            "initdb failed",
        )
        self._run(
            [str(self.bin["pg_ctl"]), "-D", str(self.data), "-l", str(self.log),
             "-o", f"-h 127.0.0.1 -p {self.port}", "-w", "start"],
            "pg_ctl start failed",
        )
        self._started = True
        self._run(
            [str(self.bin["createdb"]), "-h", "127.0.0.1", "-p", str(self.port),
             "-U", self.user, self.dbname],
            "createdb failed",
        )
        self.psql("create role neondb_owner;")

    def psql(self, sql: str) -> str:
        result = subprocess.run(
            [str(self.bin["psql"]), "-h", "127.0.0.1", "-p", str(self.port),
             "-U", self.user, "-d", self.dbname, "-v", "ON_ERROR_STOP=1", "-q", "-c", sql],
            env=self.env, capture_output=True, text=True,
        )
        if result.returncode != 0:
            fail(f"psql -c failed: {result.stdout}\n{result.stderr}")
        return result.stdout

    def psql_file(self, path: Path) -> str:
        result = subprocess.run(
            [str(self.bin["psql"]), "-h", "127.0.0.1", "-p", str(self.port),
             "-U", self.user, "-d", self.dbname, "-v", "ON_ERROR_STOP=1", "-q", "-f", str(path)],
            env=self.env, capture_output=True, text=True,
        )
        if result.returncode != 0:
            fail(f"psql -f {path} failed: {result.stdout[-4000:]}\n{result.stderr[-4000:]}")
        return result.stdout

    def stop(self) -> None:
        if not self._started:
            return
        subprocess.run(
            [str(self.bin["pg_ctl"]), "-D", str(self.data), "-m", "fast", "-w", "stop"],
            env=self.env, capture_output=True, text=True,
        )
        self._started = False


# ───────────────────────── pinned-commit materialization ─────────────────────────

def materialize_pinned_tree(dest: Path) -> Path:
    """git archive the pinned commit's migrations/, db/schema.sql, and the real
    runner (tools/migrate.py + its one dependency) into `dest`, decoupled from
    this worktree's live (dirty, divergent) state. Verified necessary: the live
    tree's migrations/0454-0471 differ from the pinned commit's content."""
    dest.mkdir(parents=True, exist_ok=True)
    tar_path = dest / "pinned.tar"
    with tar_path.open("wb") as fh:
        result = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "archive", PINNED_COMMIT,
             "migrations", "db/schema.sql", "tools/migrate.py", "tools/migration_number_contract.py"],
            stdout=fh, stderr=subprocess.PIPE, text=False,
        )
    if result.returncode != 0:
        fail(f"git archive of pinned commit failed: {result.stderr.decode('utf-8', errors='replace')}")
    subprocess.run(["tar", "-xf", str(tar_path)], cwd=dest, check=True)
    tar_path.unlink()
    return dest


def find_python() -> str:
    canonical_venv = Path("/Users/booko/carr-system/.venv/bin/python")
    if canonical_venv.is_file():
        return str(canonical_venv)
    return sys.executable


def run_migrate(pinned_tree: Path, dsn: str, through: str, python: str, log_lines: list) -> None:
    env = scrub_env()
    env["DATABASE_URL"] = dsn
    cmd = [python, str(pinned_tree / "tools/migrate.py"), "--apply", "--yes", "--through", through]
    result = subprocess.run(cmd, cwd=pinned_tree, env=env, capture_output=True, text=True)
    log_lines.append(f"$ DATABASE_URL=<disposable> python3 tools/migrate.py --apply --yes --through {through}")
    log_lines.append(result.stdout)
    if result.stderr:
        log_lines.append("[stderr] " + result.stderr)
    if result.returncode != 0:
        fail(f"tools/migrate.py --through {through} failed (rc={result.returncode}):\n{result.stdout}\n{result.stderr}")


# ───────────────────────── snapshot capture ─────────────────────────

# Anchored to the WHOLE comment line (^--\s*@snapshot ... $ / ^--\s*@end$),
# not just "contains the substring @snapshot". Both source files' own header
# comments document the marker FORMAT using literal example text nested
# inside an outer comment, e.g. "--   -- @snapshot <name>" -- an unanchored
# scan matches that documentation text as if it were a REAL block (captured
# family name "<name>", body swallowing everything up to the real pg_proc
# block's own "-- @end", which meant pg_proc was silently never captured
# under its own name at all). Caught by manual inspection of
# unmapped_family_changes after a full run, not by any automated check --
# every marked block's family name is asserted to be a plain identifier
# below as a second, permanent guard against the same class of defect.
BLOCK_RE = re.compile(r"^--\s*@snapshot\s+(\S+)\s*$\n(.*?)\n^--\s*@end\s*$", re.M | re.S)
FAMILY_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")


def parse_marked_blocks(sql_text: str) -> dict[str, str]:
    out = {}
    for name, body in BLOCK_RE.findall(sql_text):
        if not FAMILY_NAME_RE.match(name):
            fail(f"marked-block parser matched a non-identifier family name {name!r} "
                 "-- almost certainly matched documentation text, not a real @snapshot block")
        out[name] = body
    return out


PK_COLUMNS_SQL = """
select n.nspname, c.relname,
       array_agg(a.attname order by k.ord) as pk_cols
from pg_index i
join pg_class c on c.oid = i.indrelid
join pg_namespace n on n.oid = c.relnamespace
cross join lateral unnest(string_to_array(i.indkey::text, ' ')::smallint[]) with ordinality as k(attnum, ord)
join pg_attribute a on a.attrelid = c.oid and a.attnum = k.attnum
where i.indisprimary
  and n.nspname not in ('pg_catalog', 'information_schema')
  and n.nspname not like 'pg\\_temp%' escape '\\'
  and n.nspname not like 'pg\\_toast%' escape '\\'
group by n.nspname, c.relname;
"""

TABLE_LIST_SQL = """
select n.nspname, c.relname
from pg_class c join pg_namespace n on n.oid = c.relnamespace
where c.relkind = 'r'
  and n.nspname not in ('pg_catalog', 'information_schema')
  and n.nspname not like 'pg\\_temp%' escape '\\'
  and n.nspname not like 'pg\\_toast%' escape '\\'
order by n.nspname, c.relname;
"""

# Extension beyond the Artifact C surface: raw ACL exploded per object, for
# grant:/revoke: target derivation. NOT re-hashed with acldefault() -- an
# object with a NULL acl column explodes to zero rows, exactly matching "no
# explicit GRANT observed yet", which is what an OBSERVED effect manifest
# should report (not an invented implicit baseline).
ACL_RELATION_SQL = """
select n.nspname || '.' || c.relname as object_key, c.relkind as relkind,
       coalesce(r.rolname, 'PUBLIC') as grantee,
       acl.privilege_type as privilege, acl.is_grantable as is_grantable
from pg_class c
join pg_namespace n on n.oid = c.relnamespace
cross join lateral aclexplode(c.relacl) acl
left join pg_roles r on r.oid = acl.grantee
where n.nspname not in ('pg_catalog', 'information_schema')
  and n.nspname not like 'pg\\_temp%' escape '\\'
  and n.nspname not like 'pg\\_toast%' escape '\\';
"""

ACL_FUNCTION_SQL = """
select n.nspname || '.' || p.proname || '(' || pg_get_function_identity_arguments(p.oid) || ')' as object_key,
       coalesce(r.rolname, 'PUBLIC') as grantee,
       acl.privilege_type as privilege, acl.is_grantable as is_grantable
from pg_proc p
join pg_namespace n on n.oid = p.pronamespace
cross join lateral aclexplode(p.proacl) acl
left join pg_roles r on r.oid = acl.grantee
where n.nspname not in ('pg_catalog', 'information_schema');
"""

ACL_TYPE_SQL = """
select n.nspname || '.' || t.typname as object_key, t.typtype as typtype,
       coalesce(r.rolname, 'PUBLIC') as grantee,
       acl.privilege_type as privilege, acl.is_grantable as is_grantable
from pg_type t
join pg_namespace n on n.oid = t.typnamespace
cross join lateral aclexplode(t.typacl) acl
left join pg_roles r on r.oid = acl.grantee
where n.nspname not in ('pg_catalog', 'information_schema');
"""

ACL_DEFAULT_SQL = """
select coalesce(n.nspname, '(database-wide)') as schema_part,
       da.defaclrole::regrole::text as for_role,
       da.defaclobjtype::text as objtype,
       coalesce(r.rolname, 'PUBLIC') as grantee,
       acl.privilege_type as privilege, acl.is_grantable as is_grantable
from pg_default_acl da
left join pg_namespace n on n.oid = da.defaclnamespace
cross join lateral aclexplode(da.defaclacl) acl
left join pg_roles r on r.oid = acl.grantee;
"""

PG_DEPEND_SQL = """
select dep.type as dep_type, dep.schema as dep_schema, dep.name as dep_name, dep.identity as dep_identity,
       ref.type as ref_type, ref.schema as ref_schema, ref.name as ref_name, ref.identity as ref_identity,
       d.deptype as deptype
from pg_depend d
cross join lateral pg_identify_object(d.classid, d.objid, d.objsubid) as dep
cross join lateral pg_identify_object(d.refclassid, d.refobjid, d.refobjsubid) as ref
where d.deptype in ('n', 'a', 'e')
  and (coalesce(dep.schema, '') not in ('pg_catalog', 'information_schema')
       or coalesce(ref.schema, '') not in ('pg_catalog', 'information_schema'))
  and (dep.schema is not null or ref.schema is not null);
"""

PG_DESCRIPTION_SQL = """
select obj.type as objtype, obj.schema as schema, obj.name as name, obj.identity as identity,
       d.description as description
from pg_description d
cross join lateral pg_identify_object(d.classoid, d.objoid, d.objsubid) as obj
where obj.schema is not null
  and obj.schema not in ('pg_catalog', 'information_schema');
"""


def capture_snapshot(cur) -> dict:
    snap: dict[str, Any] = {"families": {}, "census": {}}

    families_sql = SNAPSHOT_SQL_PATH.read_text()
    for name, body in parse_marked_blocks(families_sql).items():
        cur.execute(body)
        snap["families"][name] = {row[0]: row[1] for row in cur.fetchall()}

    if "pg_sequence" in snap["families"]:
        for key in list(snap["families"]["pg_sequence"].keys()):
            schema, seqname = key.split(".", 1)
            cur.execute(f'select last_value, is_called from "{schema}"."{seqname}"')
            last_value, is_called = cur.fetchone()
            row = dict(snap["families"]["pg_sequence"][key])
            row["last_value"] = last_value
            row["is_called"] = is_called
            snap["families"]["pg_sequence"][key] = row

    census_sql = CENSUS_SQL_PATH.read_text()
    for name, body in parse_marked_blocks(census_sql).items():
        cur.execute(body)
        snap["census"][name] = {row[0]: row[1] for row in cur.fetchall()}

    cur.execute(PK_COLUMNS_SQL)
    snap["pk_columns"] = {f"{r[0]}.{r[1]}": r[2] for r in cur.fetchall()}

    cur.execute(TABLE_LIST_SQL)
    tables = cur.fetchall()
    table_rows: dict[str, list] = {}
    for schema, table in tables:
        cur.execute(f'select to_jsonb(t) from "{schema}"."{table}" t')
        table_rows[f"{schema}.{table}"] = [r[0] for r in cur.fetchall()]
    snap["table_rows"] = table_rows

    acl: dict[str, list] = {}
    cur.execute(ACL_RELATION_SQL)
    acl["relation"] = [
        {"object_key": r[0], "relkind": r[1], "grantee": r[2], "privilege": r[3], "is_grantable": r[4]}
        for r in cur.fetchall()
    ]
    cur.execute(ACL_FUNCTION_SQL)
    acl["function"] = [
        {"object_key": r[0], "grantee": r[1], "privilege": r[2], "is_grantable": r[3]}
        for r in cur.fetchall()
    ]
    cur.execute(ACL_TYPE_SQL)
    acl["type"] = [
        {"object_key": r[0], "typtype": r[1], "grantee": r[2], "privilege": r[3], "is_grantable": r[4]}
        for r in cur.fetchall()
    ]
    cur.execute(ACL_DEFAULT_SQL)
    acl["default"] = [
        {"schema_part": r[0], "for_role": r[1], "objtype": r[2], "grantee": r[3],
         "privilege": r[4], "is_grantable": r[5]}
        for r in cur.fetchall()
    ]
    snap["acl"] = acl

    cur.execute(PG_DEPEND_SQL)
    snap["pg_depend"] = [
        {"dep_type": r[0], "dep_schema": r[1], "dep_name": r[2], "dep_identity": r[3],
         "ref_type": r[4], "ref_schema": r[5], "ref_name": r[6], "ref_identity": r[7], "deptype": r[8]}
        for r in cur.fetchall()
    ]

    cur.execute(PG_DESCRIPTION_SQL)
    snap["comments"] = [
        {"objtype": r[0], "schema": r[1], "name": r[2], "identity": r[3], "description": r[4]}
        for r in cur.fetchall()
    ]

    return snap


# ───────────────────────── volatility ─────────────────────────

def volatile_columns_from_attrdef(families: dict) -> set[str]:
    """{'schema.table.column', ...} whose default expression is nondeterministic."""
    out = set()
    for key, row in families.get("pg_attrdef", {}).items():
        expr = row.get("def_expr") or ""
        if VOLATILE_DEFAULT_RE.search(expr):
            out.add(key)
    return out


def normalize_row_content(row: dict, volatile_local_names: set[str]) -> dict:
    if not isinstance(row, dict):
        return row
    out = {}
    for k, v in row.items():
        if k in volatile_local_names:
            out[k] = VOLATILE_PLACEHOLDER
        elif isinstance(v, str) and ISO_TIMESTAMPTZ_RE.match(v):
            out[k] = VOLATILE_PLACEHOLDER
        else:
            out[k] = v
    return out


def canonical_json(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ───────────────────────── target identity rendering ─────────────────────────

RELKIND_PREFIX = {"r": "table", "p": "table", "v": "view", "m": "matview", "S": "sequence"}


def relation_identity(schema: str, name: str, relkind: str | None) -> str | None:
    prefix = RELKIND_PREFIX.get(relkind or "")
    if prefix is None:
        return None
    return f"{prefix}:{schema}.{name}"


def function_identity_from_key(key: str) -> str:
    m = re.match(r"^(?P<schema>[^.]+)\.(?P<name>[^(]+)\((?P<args>.*)\)$", key)
    if not m:
        return f"function:{key}"
    return f"function:{m.group('schema')}.{m.group('name')}({strip_spaces_after_commas(m.group('args'))})"


def build_relkind_map(pre_families: dict, post_families: dict) -> dict[str, str]:
    out: dict[str, str] = {}
    for families in (pre_families, post_families):
        for key, row in families.get("pg_class", {}).items():
            out[key] = row.get("relkind")
    return out


def family_target_identity(family: str, key: str, row: dict, relkind_map: dict[str, str]) -> str | None:
    if family == "pg_namespace":
        return f"schema:{key}"
    if family == "pg_class":
        schema, name = key.rsplit(".", 1)
        return relation_identity(schema, name, row.get("relkind"))
    if family in ("pg_attribute", "pg_attrdef"):
        schema, table, _col = key.split(".", 2)
        return relation_identity(schema, table, relkind_map.get(f"{schema}.{table}"))
    if family == "pg_constraint":
        schema, table, cname = key.split(".", 2)
        return f"constraint:{schema}.{table}.{cname}"
    if family == "pg_trigger":
        schema, table, tname = key.split(".", 2)
        return f"trigger:{schema}.{table}.{tname}"
    if family == "pg_policy":
        schema, table, pname = key.split(".", 2)
        return f"policy:{schema}.{table}.{pname}"
    if family == "pg_rewrite":
        schema, table, _rname = key.split(".", 2)
        relkind = relkind_map.get(f"{schema}.{table}")
        return relation_identity(schema, table, relkind if relkind in ("v", "m") else "v")
    if family == "pg_default_acl":
        return None  # handled via the acl-exploded diff, not the raw family
    if family == "pg_index":
        schema, idxname = key.split(".", 1)
        return f"index:{schema}.{idxname}"
    if family == "pg_sequence":
        return f"sequence:{key}"
    if family == "pg_type":
        typtype = row.get("typtype")
        return f"{'enum' if typtype == 'e' else 'type'}:{key}"
    if family == "pg_enum":
        schema, typename, _label = key.split(".", 2)
        return f"enum:{schema}.{typename}"
    if family == "pg_partitioned_table":
        schema, table = key.split(".", 1)
        return relation_identity(schema, table, relkind_map.get(key, "p"))
    if family == "pg_proc":
        return function_identity_from_key(key)
    # pg_roles, pg_auth_members, pg_extension: no contract identity form.
    # Named limitation (Section T) -- see RESULT.md.
    return None


# ───────────────────────── per-file diff ─────────────────────────

def diff_families(pre: dict, post: dict, relkind_map: dict[str, str], filename: str,
                   detail: dict, targets: set[str]) -> None:
    for family in sorted(set(pre["families"]) | set(post["families"])):
        pre_fam = pre["families"].get(family, {})
        post_fam = post["families"].get(family, {})
        all_keys = sorted(set(pre_fam) | set(post_fam), key=lambda s: s.encode("utf-8"))
        for key in all_keys:
            pre_row = pre_fam.get(key)
            post_row = post_fam.get(key)
            if pre_row == post_row:
                continue
            status = "created" if pre_row is None else ("dropped" if post_row is None else "altered")
            ident = family_target_identity(family, key, post_row or pre_row, relkind_map)
            if ident is None:
                detail.setdefault("unmapped_family_changes", []).append(
                    {"family": family, "key": key, "status": status, "file": filename}
                )
                continue
            targets.add(ident)
            entry = detail["targets"].setdefault(ident, {
                "status": status, "first_seen_in_file": filename, "families": [], "files": [],
            })
            if filename not in entry["files"]:
                entry["files"].append(filename)
            if family not in entry["families"]:
                entry["families"].append(family)
            # definition text/digest from the richer *def / def_expr text fields,
            # which are name-based and reproducible across separate clusters.
            def_text = None
            row_for_text = post_row or pre_row
            if isinstance(row_for_text, dict):
                def_text = row_for_text.get("def") or row_for_text.get("def_expr") or row_for_text.get("prosrc")
            if def_text and not entry.get("definition_text"):
                entry["definition_text"] = def_text
                entry["definition_digest"] = "sha256:" + sha256_text(def_text)


def diff_acl(pre: dict, post: dict, filename: str, detail: dict, targets: set[str]) -> None:
    def relation_ident(item):
        return relation_identity(*item["object_key"].split(".", 1), item["relkind"])

    def function_ident(item):
        return function_identity_from_key(item["object_key"])

    def type_ident(item):
        schema, name = item["object_key"].split(".", 1)
        return f"{'enum' if item['typtype'] == 'e' else 'type'}:{schema}.{name}"

    families = [
        ("relation", relation_ident),
        ("function", function_ident),
        ("type", type_ident),
    ]
    for fam_name, ident_fn in families:
        pre_set = {
            (ident_fn(item), item["grantee"], item["privilege"], item["is_grantable"])
            for item in pre["acl"][fam_name] if ident_fn(item)
        }
        post_set = {
            (ident_fn(item), item["grantee"], item["privilege"], item["is_grantable"])
            for item in post["acl"][fam_name] if ident_fn(item)
        }
        for ident, grantee, priv, grantable in sorted(post_set - pre_set):
            target = f"grant:{ident}:{grantee}:{priv}"
            targets.add(target)
            detail["grants"].append({
                "target": target, "object": ident, "grantee": grantee, "privilege": priv,
                "is_grantable": grantable, "direction": "grant", "file": filename,
            })
        for ident, grantee, priv, grantable in sorted(pre_set - post_set):
            target = f"revoke:{ident}:{grantee}:{priv}"
            targets.add(target)
            detail["grants"].append({
                "target": target, "object": ident, "grantee": grantee, "privilege": priv,
                "is_grantable": grantable, "direction": "revoke", "file": filename,
            })

    pre_default = {
        (i["schema_part"], i["for_role"], i["objtype"], i["grantee"], i["privilege"])
        for i in pre["acl"]["default"]
    }
    post_default = {
        (i["schema_part"], i["for_role"], i["objtype"], i["grantee"], i["privilege"])
        for i in post["acl"]["default"]
    }
    for schema_part, for_role, objtype, grantee, priv in sorted((pre_default ^ post_default)):
        target = f"defaultacl:{schema_part}.{for_role}:{objtype}:{grantee}:{priv}"
        direction = "added" if (schema_part, for_role, objtype, grantee, priv) in post_default else "removed"
        targets.add(target)
        detail["default_acl_changes"].append({"target": target, "direction": direction, "file": filename})


def diff_comments(pre: dict, post: dict, filename: str, detail: dict, targets: set[str]) -> None:
    # PATCH (WR-000046 comparison seat, slice-compare, applied to this copy
    # only -- never re-run against a database by the comparison seat): the
    # function branch below used to build its identity from
    # pg_identify_object()'s `c["identity"]` column (the `if False` was dead
    # code showing a correct path had been drafted and never wired in).
    # pg_identify_object() renders a function's identity with fully
    # schema-qualified built-in type names (e.g. "pg_catalog.text") and
    # WITHOUT parameter names -- this does not match the `function:` form
    # used everywhere else in this manifest (pg_get_function_identity_
    # arguments via the pg_proc family / function_identity_from_key(), which
    # DOES retain parameter names and uses unqualified canonical type names).
    # Cross-checked empirically: for every one of the 109 functions in
    # 0454-0471, the correct "name type" text matches that function's own
    # CREATE FUNCTION parameter list token-for-token (see comparison-report.md,
    # rule observed_comment_function_identity_regenerated). Fixed by
    # resolving each function comment against the already-known-correct
    # `function:` target for that schema-qualified bare name -- diff_families()
    # for the pg_proc family always runs before diff_comments() for the same
    # file (see the call order in the per-file driver loop), so by this point
    # `targets` already contains every function created/altered so far. No
    # function name in this corpus is overloaded (verified: zero (schema,
    # name) collisions across the 109 real functions), so bare-name
    # resolution is unambiguous.
    func_by_bare_name = {}
    for t in targets:
        if t.startswith("function:"):
            paren = t.index("(")
            func_by_bare_name[t[len("function:"):paren]] = t[len("function:"):]

    def to_map(comments):
        out = {}
        for c in comments:
            prefix = PG_IDENTIFY_TYPE_TO_PREFIX.get(c["objtype"])
            if prefix is None:
                continue
            if prefix == "schema":
                ident = f"schema:{c['name']}"
            elif prefix == "function":
                bare = f"{c['schema']}.{c['name']}"
                resolved = func_by_bare_name.get(bare)
                ident = f"function:{resolved}" if resolved is not None \
                    else f"function:{strip_spaces_after_commas(c['identity'])}"
            else:
                ident = f"{prefix}:{c['schema']}.{c['name']}"
            out[ident] = c["description"]
        return out

    pre_map, post_map = to_map(pre["comments"]), to_map(post["comments"])
    for ident in sorted(set(pre_map) | set(post_map), key=lambda s: s.encode("utf-8")):
        if pre_map.get(ident) == post_map.get(ident):
            continue
        target = f"comment:{ident}"
        targets.add(target)
        detail.setdefault("comments", []).append(
            {"target": target, "text": post_map.get(ident), "file": filename}
        )


def diff_owners(pre: dict, post: dict, filename: str, detail: dict, targets: set[str]) -> None:
    owner_fields = {"pg_class": "relowner", "pg_proc": "proowner", "pg_type": "typowner"}
    role_names_pre = {row.get("oid"): key for key, row in pre["families"].get("pg_roles", {}).items()}
    role_names_post = {row.get("oid"): key for key, row in post["families"].get("pg_roles", {}).items()}
    for family, field in owner_fields.items():
        pre_fam, post_fam = pre["families"].get(family, {}), post["families"].get(family, {})
        for key in sorted(set(pre_fam) & set(post_fam), key=lambda s: s.encode("utf-8")):
            pre_owner = pre_fam[key].get(field)
            post_owner = post_fam[key].get(field)
            if pre_owner == post_owner:
                continue
            ident: str | None
            if family == "pg_proc":
                ident = function_identity_from_key(key)
            elif family == "pg_type":
                typtype = post_fam[key].get("typtype")
                ident = f"{'enum' if typtype == 'e' else 'type'}:{key}"
            else:
                schema, name = key.rsplit(".", 1)
                ident = relation_identity(schema, name, post_fam[key].get("relkind"))
            if ident is None:
                continue
            target = f"owner:{ident}"
            targets.add(target)
            detail.setdefault("owner_changes", []).append({
                "target": target,
                "new_owner_role_oid_resolved": role_names_post.get(post_owner, post_owner),
                "file": filename,
            })


def diff_rows(pre: dict, post: dict, filename: str, detail: dict, targets: set[str],
              volatile_cols: set[str]) -> None:
    pk_columns = post.get("pk_columns") or pre.get("pk_columns") or {}
    all_tables = set(pre["table_rows"]) | set(post["table_rows"])
    for table_key in sorted(all_tables):
        pk_cols = pk_columns.get(table_key)
        pre_rows = pre["table_rows"].get(table_key, [])
        post_rows = post["table_rows"].get(table_key, [])
        vol_local = {c.rsplit(".", 1)[-1] for c in volatile_cols if c.startswith(table_key + ".")}
        if not pk_cols:
            pre_digest = sha256_text(canonical_json(sorted(
                canonical_json(normalize_row_content(r, vol_local)) for r in pre_rows)))
            post_digest = sha256_text(canonical_json(sorted(
                canonical_json(normalize_row_content(r, vol_local)) for r in post_rows)))
            if pre_digest != post_digest:
                detail.setdefault("tables_without_pk_changed", []).append(
                    {"table": table_key, "file": filename, "note": "no primary key; row: targets not derivable"}
                )
            continue

        def pk_key(row: dict) -> str:
            return "|".join(str(row.get(c)) for c in pk_cols)

        pre_by_pk = {pk_key(r): r for r in pre_rows}
        post_by_pk = {pk_key(r): r for r in post_rows}
        for pk in sorted(set(pre_by_pk) | set(post_by_pk), key=lambda s: s.encode("utf-8")):
            pre_r, post_r = pre_by_pk.get(pk), post_by_pk.get(pk)
            pre_norm = normalize_row_content(pre_r, vol_local) if pre_r else None
            post_norm = normalize_row_content(post_r, vol_local) if post_r else None
            if pre_norm == post_norm:
                continue
            op = "insert" if pre_r is None else ("delete" if post_r is None else "update")
            target = f"row:{table_key}:{pk}"
            targets.add(target)
            detail["row_changes"].append({
                "target": target, "table": table_key, "pk": pk, "operation": op,
                "file": filename, "row": post_norm if post_norm is not None else pre_norm,
            })


# ───────────────────────── pg_depend closure (best-effort, evidentiary) ─────────────────────────

def pg_identify_to_contract(objtype: str, schema: str | None, name: str | None, identity: str | None) -> str | None:
    prefix = PG_IDENTIFY_TYPE_TO_PREFIX.get(objtype)
    if prefix is None or schema is None or name is None:
        return None
    if prefix == "schema":
        return f"schema:{name}"
    if prefix == "function":
        return f"function:{strip_spaces_after_commas(identity)}" if identity else None
    return f"{prefix}:{schema}.{name}"


def compute_pg_depend_closure(final_snapshot: dict, created_targets: set[str]) -> dict[str, list[str]]:
    edges: dict[str, set[str]] = {}
    for row in final_snapshot["pg_depend"]:
        dep_ident = pg_identify_to_contract(row["dep_type"], row["dep_schema"], row["dep_name"], row["dep_identity"])
        ref_ident = pg_identify_to_contract(row["ref_type"], row["ref_schema"], row["ref_name"], row["ref_identity"])
        if dep_ident is None or ref_ident is None or dep_ident == ref_ident:
            continue
        edges.setdefault(dep_ident, set()).add(ref_ident)

    closure: dict[str, list[str]] = {}
    for root in sorted(created_targets):
        seen: set[str] = set()
        frontier = [root]
        while frontier:
            node = frontier.pop()
            for nxt in edges.get(node, ()):
                if nxt not in seen:
                    seen.add(nxt)
                    frontier.append(nxt)
        if seen:
            closure[root] = sorted(seen)
    return closure


# ───────────────────────── the full pipeline for one run ─────────────────────────

def run_pipeline(port: int, out_path: Path, apply_log_path: Path, run_tag: str) -> None:
    import psycopg  # imported here so --help works without it installed

    workdir = Path(tempfile.mkdtemp(prefix=f"frontier-manifest-{run_tag}-", dir=str(REPO_ROOT)))
    log_lines: list[str] = [f"gen_frontier_manifest run={run_tag} port={port} workdir={workdir}"]
    disposable: Disposable | None = None
    try:
        pinned_tree = materialize_pinned_tree(workdir / "pinned-tree")
        log_lines.append(f"materialized pinned tree from commit {PINNED_COMMIT}")

        disposable = Disposable(workdir, port)
        disposable.start()
        log_lines.append(f"disposable cluster up at {disposable.dsn()}")

        disposable.psql_file(pinned_tree / "db/schema.sql")
        log_lines.append("loaded db/schema.sql (pinned)")

        python = find_python()
        run_migrate(pinned_tree, disposable.dsn(), PRE_FRONTIER_THROUGH, python, log_lines)

        conn = psycopg.connect(disposable.dsn())
        conn.autocommit = True
        cur = conn.cursor()

        baseline = capture_snapshot(cur)
        log_lines.append(f"baseline snapshot captured after {PRE_FRONTIER_THROUGH}")

        volatile_cols = volatile_columns_from_attrdef(baseline["families"])

        detail: dict[str, Any] = {
            "targets": {}, "grants": [], "default_acl_changes": [], "row_changes": [],
            "comments": [], "owner_changes": [], "unmapped_family_changes": [], "tables_without_pk_changed": [],
        }
        targets: set[str] = set()
        by_file: dict[str, list[str]] = {}

        prev_snapshot = baseline
        for filename in FRONTIER_FILES:
            run_migrate(pinned_tree, disposable.dsn(), filename, python, log_lines)
            post_snapshot = capture_snapshot(cur)
            volatile_cols |= volatile_columns_from_attrdef(post_snapshot["families"])

            before_targets = set(targets)
            relkind_map = build_relkind_map(prev_snapshot["families"], post_snapshot["families"])
            diff_families(prev_snapshot, post_snapshot, relkind_map, filename, detail, targets)
            diff_acl(prev_snapshot, post_snapshot, filename, detail, targets)
            diff_comments(prev_snapshot, post_snapshot, filename, detail, targets)
            diff_owners(prev_snapshot, post_snapshot, filename, detail, targets)
            diff_rows(prev_snapshot, post_snapshot, filename, detail, targets, volatile_cols)

            by_file[filename] = sorted(targets - before_targets, key=lambda s: s.encode("utf-8"))
            log_lines.append(f"applied+snapshotted {filename}: {len(by_file[filename])} new/changed targets")
            prev_snapshot = post_snapshot

        created_targets = {
            ident for ident, entry in detail["targets"].items() if entry["status"] in ("created", "altered")
        }
        detail["pg_depend_closure"] = compute_pg_depend_closure(prev_snapshot, created_targets)

        result = {
            "targets": sorted(targets, key=lambda s: s.encode("utf-8")),
            "by_file": by_file,
            "detail": detail,
        }
        out_path.write_text(json.dumps(result, indent=2, sort_keys=True, default=str))
        apply_log_path.write_text("\n".join(log_lines) + "\n")
        print(f"run {run_tag}: {len(targets)} targets -> {out_path}")
    finally:
        if disposable is not None:
            disposable.stop()
        shutil.rmtree(workdir, ignore_errors=True)


# ───────────────────────── reconcile two runs ─────────────────────────

def deep_diff_paths(a, b, path=""):
    if a == b:
        return
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b)):
            yield from deep_diff_paths(a.get(k, "<missing>"), b.get(k, "<missing>"), f"{path}.{k}")
        return
    if isinstance(a, list) and isinstance(b, list) and len(a) == len(b):
        for i, (x, y) in enumerate(zip(a, b)):
            yield from deep_diff_paths(x, y, f"{path}[{i}]")
        return
    yield (path, a, b)


def reconcile(run_a_path: Path, run_b_path: Path, out_path: Path, result_note_path: Path | None) -> None:
    run_a = json.loads(run_a_path.read_text())
    run_b = json.loads(run_b_path.read_text())

    diffs = list(deep_diff_paths(run_a, run_b))
    if diffs:
        lines = [f"reconcile: {len(diffs)} field-level difference(s) between the two runs (after this "
                 f"generator's built-in normalization) -- these are NOT auto-normalized away:"]
        for path, a_val, b_val in diffs[:200]:
            lines.append(f"  {path}: run_a={a_val!r} run_b={b_val!r}")
        report = "\n".join(lines)
        print(report, file=sys.stderr)
        if result_note_path:
            result_note_path.write_text(report + "\n")
        fail(f"double-run byte-identity FAILED: {len(diffs)} unexplained difference(s); "
             "see stderr / the reconcile note for exact paths")

    manifest = {
        "targets": run_a["targets"],
        "by_file": run_a["by_file"],
        "detail": run_a["detail"],
        "provenance": {
            "pinned_commit": PINNED_COMMIT,
            "pre_frontier_through": PRE_FRONTIER_THROUGH,
            "frontier_files": FRONTIER_FILES,
            "generator_path": "docs/frontier-finding/gen_frontier_manifest.py",
            "generator_sha256": "sha256:" + hashlib.sha256(GENERATOR_PATH.read_bytes()).hexdigest(),
            "generation_method": (
                "Disposable local PostgreSQL 17 (LC_ALL=C on initdb and pg_ctl start), db/schema.sql "
                "and migrations loaded from `git archive` of the pinned commit (decoupled from this "
                "worktree's live, divergent tree), pre-frontier prefix applied through "
                f"{PRE_FRONTIER_THROUGH} with the real tools/migrate.py --through runner, then each of "
                "the eighteen frontier files applied one at a time via --through, full catalog snapshot "
                "(breakglass-snapshot.sql families + census-queries.sql + pg_depend + pg_description + "
                "per-table row content + ACL-exploded grants) taken after each apply, and diffed against "
                "the immediately preceding snapshot. Run twice on two independent fresh disposables and "
                "reconciled byte-identical by this script's `reconcile` mode before being accepted."
            ),
            "volatile_normalization_rules": VOLATILE_NORMALIZATION_RULES,
            "double_run_reconciliation": "byte-identical after normalization; see RESULT.md",
        },
    }
    out_path.write_text(json.dumps(manifest, indent=2, sort_keys=True, default=str) + "\n")
    print(f"reconcile: OK, byte-identical -> {out_path}")


# ───────────────────────── CLI ─────────────────────────

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    run_p = sub.add_parser("run", help="build a disposable, apply, snapshot, diff; emit one run's manifest")
    run_p.add_argument("--port", type=int, required=True)
    run_p.add_argument("--out", type=Path, required=True)
    run_p.add_argument("--apply-log", type=Path, required=True)
    run_p.add_argument("--run-tag", default="run")

    rec_p = sub.add_parser("reconcile", help="assert two runs byte-identical (post-normalization); emit final artifact")
    rec_p.add_argument("--run-a", type=Path, required=True)
    rec_p.add_argument("--run-b", type=Path, required=True)
    rec_p.add_argument("--out", type=Path, required=True)
    rec_p.add_argument("--note", type=Path, default=None)

    args = ap.parse_args(argv)
    if args.cmd == "run":
        run_pipeline(args.port, args.out, args.apply_log, args.run_tag)
    elif args.cmd == "reconcile":
        reconcile(args.run_a, args.run_b, args.out, args.note)
    return 0
