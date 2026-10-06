"""breakglass_run.py — Artifact C's receipt driver (WR-000046, F01 condition (d)).

WHAT THIS IS. A best-effort RECEIPT INSTRUMENT, not a load-bearing gate
(gated-register-plan.md Section T). It widens detection over a reviewed,
extendable catalog surface (breakglass-snapshot.sql + census-queries.sql); it
makes no claim of mechanical completeness over every PostgreSQL catalog
family, and every guarantee below is scoped to that surface. F01's
LOAD-BEARING controls are procedural and human: per-use approval against a
recorded WR-000046 note, clone rehearsal, production readback, and the
forward-fix commitment. This driver only ADDS a three-sided assertion on top.

INVOCATION (exactly as F01 condition (d) and the execution path describe it):

    CARR_BREAK_GLASS=1 .venv/bin/python tools/db-tap.py --reason "<WR note ref>" \\
        run tools/run-breakglass.py -- \\
        --approved <run>.sql --receipt <receipt>.json

(tools/run-breakglass.py is the tracked, reviewed launcher registered as a
break-glass-class entrypoint by the WR-000048 registry re-pin; this module
stays import-only and is reached through the launcher's main().)

db-tap's `run` mode execs this file with DATABASE_URL already set in the
child environment and no DSN ever on a command line. This script also runs
STANDALONE with DATABASE_URL set directly in the environment — that is how
the test harness (breakglass_selftest.py) drives it against a local disposable
Postgres, and how a rehearsal or restore run against a clone is executed.

THE APPROVED FILE (`--approved <run>.sql`) is a SELF-CONTAINED RUN BUNDLE
produced by gen_breakglass_run.py: one manifest line (canonical JSON) plus the
verbatim candidate SQL, each fenced by its own BEGIN/END marker (see
parse_bundle() below). Three digests matter and are all printed before
anything executes: the run-script sha256 (the whole bundle file — this is
what Joe approves by digest, condition (a)), the candidate sha256 (just the
SQL body), and the manifest sha256 (just the canonical JSON line). A run
whose digests do not match the recorded WR-000046 approval note is a protocol
violation on its face; this driver does not check the note itself (the
record layer is the oracle, not this file) but prints exactly what an
operator or auditor needs to compare against it.

THE THREE-SIDED ASSERTION, exactly per the plan:
  (1) EXPECTED-PRE PRECHECK, before the candidate runs: every declared
      target's observed pre-state must equal the manifest's declared
      expected_pre. A mismatch refuses BEFORE the candidate ever executes.
  (2) NO UNDECLARED CHANGE on the snapshot surface: every row on the
      reviewed surface that is not a declared target must be identical
      before and after. For a table holding a declared row target, "no
      undeclared change" is checked EXCLUDING that row (a whole-table digest
      would not tell a lone declared edit apart from a declared edit plus a
      collateral one).
  (3) Each declared target's observed post-state must equal the manifest's
      declared expected_post.
COMMIT only if all three hold; otherwise ROLLBACK. A precheck failure never
runs the candidate at all (verdict "refused_precheck").

SEQUENCES ARE NEVER PART OF THAT GATE. nextval/setval effects are
non-transactional by PostgreSQL design and survive a ROLLBACK; gating commit
on every sequence delta would make ordinary declared INSERTs impossible to
approve in advance. Instead every sequence delta — forward advancement,
backward setval, an is_called flip, a configuration edit — is OBSERVED and
reported in the receipt's sequence_residuals section, with NO automatic
benign classification. The human reviewing the receipt classifies it in the
WR-000046 note.

THE RECEIPT is written in TWO DURABILITY PHASES to the file named by
`--receipt`. Phase 1 (candidate, digests, identity tuple, pre-snapshot with
full pre-images for declared targets) is written and fsynced — the file AND
its parent directory — BEFORE the candidate executes, and is by itself a
complete, valid, readable JSON document (see breakglass_selftest.py's durability
test: kill the process right after this phase and the file that remains is
exactly this). Phase 2 (post-snapshot, verdict, sequence_residuals, the
per-assertion report) is folded into the same document and the file is
rewritten and fsynced again after commit/rollback. "Appended" in the plan
text means the final document carries both phases; it is not a literal
byte-append, because a byte-append would leave an invalid partial JSON
document sitting on disk in between, which is worse for exactly the
durability property the two-phase ordering exists to buy.

IDENTITY TUPLE: (endpoint_host, database_name, pg_system_identifier), all
three read from the LIVE CONNECTION by this driver — never from a --branch
or any other argument, because nothing here claims to know what a wrapper
did or did not pass through.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO / "tools"))
import migrate  # noqa: E402  (verbatim contains_transaction_control lives here)

try:
    import psycopg
    from psycopg import sql as psql
except ImportError:  # pragma: no cover - exercised only off a bare interpreter
    sys.exit(
        "breakglass_run: psycopg is not installed in this interpreter — "
        "run through .venv/bin/python, or pip install -r requirements.txt"
    )

SNAPSHOT_SQL_FILES = ["breakglass-snapshot.sql", "census-queries.sql"]

# Families whose sequence-shaped state is reported, never gated (see module
# docstring). Everything else in the loaded snapshot blocks IS gated.
SEQUENCE_FAMILY = "pg_sequence"

RECEIPT_SCHEMA_VERSION = "breakglass-receipt.v1"
MANIFEST_SCHEMA_VERSION = "breakglass-manifest.v1"

BUNDLE_HEADER = "-- BREAKGLASS-RUN-BUNDLE v1"
MANIFEST_BEGIN = "-- MANIFEST-BEGIN"
MANIFEST_END = "-- MANIFEST-END"
CANDIDATE_BEGIN = "-- CANDIDATE-BEGIN"
CANDIDATE_END = "-- CANDIDATE-END"


# ── bundle format (shared with gen_breakglass_run.py) ───────────────────────


class BundleError(RuntimeError):
    pass


def sha256_text(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def canonical_json(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


def build_bundle(manifest: dict, candidate_sql: str) -> str:
    manifest_line = canonical_json(manifest)
    if "\n" in manifest_line:
        raise BundleError("canonical manifest JSON must not contain a newline")
    for marker in (MANIFEST_BEGIN, MANIFEST_END, CANDIDATE_BEGIN, CANDIDATE_END):
        if marker in candidate_sql:
            raise BundleError(f"candidate SQL must not contain the bundle marker line {marker!r}")
    return "\n".join([
        BUNDLE_HEADER,
        MANIFEST_BEGIN,
        "-- " + manifest_line,
        MANIFEST_END,
        CANDIDATE_BEGIN,
        candidate_sql.rstrip("\n"),
        CANDIDATE_END,
        "",
    ])


def parse_bundle(text: str) -> tuple[dict, str]:
    lines = text.split("\n")
    try:
        mb = lines.index(MANIFEST_BEGIN)
        me = lines.index(MANIFEST_END)
        cb = lines.index(CANDIDATE_BEGIN)
        ce = lines.index(CANDIDATE_END)
    except ValueError as exc:
        raise BundleError(f"not a well-formed breakglass run bundle: {exc}") from None
    if not (mb < me < cb < ce):
        raise BundleError("bundle markers out of order")
    manifest_lines = lines[mb + 1:me]
    if len(manifest_lines) != 1 or not manifest_lines[0].startswith("-- "):
        raise BundleError("manifest block must be exactly one '-- '-prefixed JSON line")
    manifest_text = manifest_lines[0][3:]
    try:
        manifest = json.loads(manifest_text)
    except json.JSONDecodeError as exc:
        raise BundleError(f"manifest block is not valid JSON: {exc}") from None
    candidate_sql = "\n".join(lines[cb + 1:ce])
    return manifest, candidate_sql


# ── snapshot query loading ───────────────────────────────────────────────


def load_snapshot_blocks() -> dict[str, str]:
    """Parse the `-- @snapshot name` / SELECT / `-- @end` blocks out of the
    named files in SNAPSHOT_SQL_FILES, both read relative to this script's
    own directory so the driver works from any cwd."""
    blocks: dict[str, str] = {}
    for filename in SNAPSHOT_SQL_FILES:
        path = HERE / filename
        if not path.is_file():
            raise BundleError(f"required snapshot query file is missing: {path}")
        text = path.read_text(encoding="utf-8")
        name = None
        body: list[str] = []
        for line in text.split("\n"):
            stripped = line.strip()
            if stripped.startswith("-- @snapshot "):
                if name is not None:
                    raise BundleError(f"{path}: nested @snapshot block for {name!r}")
                name = stripped[len("-- @snapshot "):].strip()
                body = []
                continue
            if stripped == "-- @end":
                if name is None:
                    raise BundleError(f"{path}: @end with no open @snapshot block")
                if name in blocks:
                    raise BundleError(f"duplicate snapshot block name {name!r} (in {path})")
                blocks[name] = "\n".join(body)
                name = None
                continue
            if name is not None:
                body.append(line)
        if name is not None:
            raise BundleError(f"{path}: @snapshot {name!r} never closed with @end")
    return blocks


# ── live snapshot capture ────────────────────────────────────────────────


def _rows_to_map(cur) -> dict[str, dict]:
    rows = cur.fetchall()
    grouped: dict[str, list[dict]] = {}
    for identity_key, row in rows:
        grouped.setdefault(identity_key, []).append(row)

    # Some PostgreSQL catalogs legitimately expose more than one physical row
    # for the logical identity a reviewed census reports. pg_auth_members is the
    # production example: the same role/member edge may be granted by distinct
    # grantors, while the role-authority census deliberately omits grantor from
    # its logical row. Silently collapsing those rows would lose multiplicity;
    # refusing makes a faithful pre-activation snapshot impossible. Preserve all
    # rows instead, with stable receipt-only keys derived from canonical row
    # content plus an occurrence number. A singleton keeps its reviewed key.
    out: dict[str, dict] = {}
    raw_keys = set(grouped)
    for identity_key, identity_rows in grouped.items():
        if len(identity_rows) == 1:
            out[identity_key] = identity_rows[0]
            continue
        ordered = sorted(identity_rows, key=canonical_json)
        for occurrence, row in enumerate(ordered, start=1):
            row_digest = hashlib.sha256(canonical_json(row).encode("utf-8")).hexdigest()
            receipt_key = (
                f"{identity_key}#duplicate:{occurrence:04d}:sha256:{row_digest}"
            )
            if receipt_key in raw_keys or receipt_key in out:
                raise BundleError(
                    f"snapshot duplicate disambiguation collided with identity_key: {receipt_key!r}"
                )
            out[receipt_key] = row
    return out


def capture_family_snapshot(cur, blocks: dict[str, str]) -> dict[str, dict[str, dict]]:
    snapshot: dict[str, dict[str, dict]] = {}
    for name, query in blocks.items():
        cur.execute(query)
        snapshot[name] = _rows_to_map(cur)
    return snapshot


def supplement_sequence_state(cur, snapshot: dict[str, dict[str, dict]]) -> None:
    """pg_sequence's own catalog row has no last_value/is_called (those live
    on the sequence object itself, one query per sequence — see
    breakglass-snapshot.sql's header comment for why this can't be one
    catalog-wide SELECT)."""
    family = snapshot.get(SEQUENCE_FAMILY, {})
    for identity_key, row in family.items():
        schema, _, seq_name = identity_key.partition(".")
        ident = psql.Identifier(schema, seq_name)
        cur.execute(psql.SQL("select last_value, is_called from {}").format(ident))
        last_value, is_called = cur.fetchone()
        row["last_value"] = last_value
        row["is_called"] = is_called


def list_user_tables(cur) -> list[tuple[str, str]]:
    cur.execute(
        """
        select n.nspname, c.relname
        from pg_class c
        join pg_namespace n on n.oid = c.relnamespace
        where c.relkind in ('r', 'p')
          and n.nspname not in ('pg_catalog', 'information_schema')
          and n.nspname not like 'pg\\_temp%'
          and n.nspname not like 'pg\\_toast%'
        order by 1, 2
        """
    )
    return [(schema, table) for schema, table in cur.fetchall()]


def table_digest(cur, schema: str, table: str, exclude_keys: list[dict] | None = None) -> str:
    ident = psql.Identifier(schema, table)
    where: psql.SQL | psql.Composed = psql.SQL("")
    params: list = []
    if exclude_keys:
        clauses = []
        for key in exclude_keys:
            cols = list(key.keys())
            clause = psql.SQL(" and ").join(
                psql.SQL("{} is not distinct from %s").format(psql.Identifier(col)) for col in cols
            )
            clauses.append(psql.SQL("(") + clause + psql.SQL(")"))
            params.extend(key[c] for c in cols)
        where = psql.SQL(" where not (") + psql.SQL(" or ").join(clauses) + psql.SQL(")")
    query = (
        psql.SQL("select coalesce(string_agg(h, '' order by h), '') from (")
        + psql.SQL("select md5(t::text) as h from {} t").format(ident)
        + where
        + psql.SQL(") s")
    )
    cur.execute(query, params)
    (digest,) = cur.fetchone()
    return digest


def capture_table_digests(cur, declared_row_targets: dict[str, list[dict]]) -> dict[str, str]:
    """One digest per user-schema table. A table holding declared row
    targets is digested EXCLUDING those rows' keys, so a collateral change to
    an undeclared row in that same table still shows up (assertion 2)."""
    out: dict[str, str] = {}
    for schema, table in list_user_tables(cur):
        qualified = f"{schema}.{table}"
        out[qualified] = table_digest(cur, schema, table, declared_row_targets.get(qualified))
    return out


def fetch_row_target_image(cur, table: str, key: dict) -> dict | None:
    schema, _, name = table.partition(".")
    ident = psql.Identifier(schema, name)
    cols = list(key.keys())
    where = psql.SQL(" and ").join(
        psql.SQL("{} is not distinct from %s").format(psql.Identifier(c)) for c in cols
    )
    query = psql.SQL("select to_jsonb(t) from {} t where ").format(ident) + where
    cur.execute(query, [key[c] for c in cols])
    rows = cur.fetchall()
    if not rows:
        return None
    if len(rows) > 1:
        raise BundleError(f"row target key is not unique in {table}: {key!r}")
    return rows[0][0]


# ── manifest / target handling ───────────────────────────────────────────


def target_identity(target: dict) -> str:
    if target["kind"] == "row":
        return "table_row:" + target["table"] + ":" + canonical_json(target["key"])
    return "definition:" + target["family"] + ":" + target["identity_key"]


def group_row_targets_by_table(targets: list[dict]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for t in targets:
        if t["kind"] == "row":
            out.setdefault(t["table"], []).append(t["key"])
    return out


def observe_target(cur, target: dict, family_snapshot: dict[str, dict[str, dict]]) -> dict | None:
    if target["kind"] == "row":
        return fetch_row_target_image(cur, target["table"], target["key"])
    family = family_snapshot.get(target["family"])
    if family is None:
        raise BundleError(f"manifest declares an unknown snapshot family: {target['family']!r}")
    return family.get(target["identity_key"])


# ── identity tuple ────────────────────────────────────────────────────────


def live_identity_tuple(conn) -> dict:
    with conn.cursor() as cur:
        cur.execute("select system_identifier from pg_control_system()")
        (system_identifier,) = cur.fetchone()
    return {
        "endpoint_host": conn.info.host or "",
        "database_name": conn.info.dbname or "",
        "pg_system_identifier": str(system_identifier),
    }


# ── receipt I/O ───────────────────────────────────────────────────────────


def _fsync_file_and_dir(path: Path) -> None:
    fd = os.open(str(path), os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    dir_fd = os.open(str(path.parent), os.O_RDONLY)
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)


def write_receipt(path: Path, document: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(document, fh, indent=2, sort_keys=True, default=str)
        fh.write("\n")
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)
    _fsync_file_and_dir(path)


# ── diffing (assertion 2) ────────────────────────────────────────────────


def diff_undeclared(
    pre: dict[str, dict[str, dict]],
    post: dict[str, dict[str, dict]],
    declared_definition_ids: set[tuple[str, str]],
) -> list[dict]:
    """Every (family, identity_key) not declared must be identical pre/post.
    pg_sequence is excluded here on purpose — see SEQUENCE_FAMILY."""
    violations = []
    families = set(pre) | set(post)
    for family in families:
        if family == SEQUENCE_FAMILY:
            continue
        pre_family = pre.get(family, {})
        post_family = post.get(family, {})
        keys = set(pre_family) | set(post_family)
        for key in keys:
            if (family, key) in declared_definition_ids:
                continue
            if pre_family.get(key) != post_family.get(key):
                violations.append({
                    "family": family,
                    "identity_key": key,
                    "pre": pre_family.get(key),
                    "post": post_family.get(key),
                })
    return violations


def diff_table_digests(pre: dict[str, str], post: dict[str, str]) -> list[dict]:
    violations = []
    for table in set(pre) | set(post):
        if pre.get(table) != post.get(table):
            violations.append({"table": table, "pre_digest": pre.get(table), "post_digest": post.get(table)})
    return violations


def diff_sequence_residuals(
    pre: dict[str, dict], post: dict[str, dict]
) -> list[dict]:
    residuals = []
    for identity_key in sorted(set(pre) | set(post)):
        before = pre.get(identity_key)
        after = post.get(identity_key)
        if before == after:
            continue
        kinds = []
        if before is not None and after is not None:
            if after.get("last_value") != before.get("last_value"):
                kinds.append("backward_setval" if after["last_value"] < before["last_value"] else "advanced")
            if after.get("is_called") != before.get("is_called"):
                kinds.append("is_called_flip")
            config_keys = [k for k in after if k not in ("last_value", "is_called")]
            if any(after.get(k) != before.get(k) for k in config_keys):
                kinds.append("configuration_change")
        elif before is None:
            kinds.append("sequence_created")
        else:
            kinds.append("sequence_dropped")
        residuals.append({
            "identity_key": identity_key,
            "pre": before,
            "post": after,
            "observed_kinds": kinds or ["changed"],
        })
    return residuals


# ── main run ──────────────────────────────────────────────────────────────


def run(approved_path: Path, receipt_path: Path) -> int:
    bundle_text = approved_path.read_text(encoding="utf-8")
    manifest, candidate_sql = parse_bundle(bundle_text)
    if manifest.get("manifest_version") != MANIFEST_SCHEMA_VERSION:
        sys.exit(f"breakglass_run: unrecognized manifest_version {manifest.get('manifest_version')!r}")

    run_script_sha256 = sha256_text(bundle_text)
    candidate_sha256 = sha256_text(candidate_sql)
    manifest_sha256 = sha256_text(canonical_json(manifest))
    print("breakglass_run: digests to compare against the WR-000046 approval note:")
    print(f"  run-script: {run_script_sha256}")
    print(f"  candidate:  {candidate_sha256}")
    print(f"  manifest:   {manifest_sha256}")

    # Defensive re-check. gen_breakglass_run.py already refuses to generate a
    # bundle whose candidate trips the lexer; this driver checks again rather
    # than trusting that whatever produced --approved was in fact the
    # generator, since nothing here can verify that.
    if migrate.contains_transaction_control(candidate_sql):
        sys.exit("breakglass_run: REFUSED — candidate SQL contains transaction control")

    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        sys.exit("breakglass_run: DATABASE_URL is not set")

    targets = manifest.get("targets", [])
    declared_definition_ids = {
        (t["family"], t["identity_key"]) for t in targets if t["kind"] == "definition"
    }
    declared_row_targets = group_row_targets_by_table(targets)

    outcome = {
        "receipt_schema_version": RECEIPT_SCHEMA_VERSION,
        "run_script_sha256": run_script_sha256,
        "candidate_sha256": candidate_sha256,
        "manifest_sha256": manifest_sha256,
        "manifest": manifest,
        "candidate_sql": candidate_sql,
        "started_at": time.time(),
    }

    with psycopg.connect(database_url, autocommit=False) as conn:
        # ONE MVCC snapshot for the whole run. This must be set before the
        # connection's first statement — psycopg opens the transaction
        # implicitly on first use in non-autocommit mode, and Postgres only
        # accepts SET TRANSACTION ISOLATION LEVEL as the transaction's first
        # statement. Setting it here, before live_identity_tuple's own
        # cursor use, is what makes it apply.
        conn.isolation_level = psycopg.IsolationLevel.REPEATABLE_READ
        identity = live_identity_tuple(conn)
        outcome["identity_tuple"] = identity

        identity_requirement = manifest.get("identity_requirement")
        if identity_requirement is not None:
            mismatches = [
                field for field in ("endpoint_host", "database_name", "pg_system_identifier")
                if identity_requirement.get(field) != identity.get(field)
            ]
            if mismatches:
                outcome["verdict"] = "refused_identity_mismatch"
                outcome["identity_mismatch_fields"] = mismatches
                write_receipt(receipt_path, outcome)
                conn.rollback()
                print(f"breakglass_run: REFUSED — identity mismatch on {mismatches}", file=sys.stderr)
                return 1

        blocks = load_snapshot_blocks()
        with conn.cursor() as cur:
            pre_family = capture_family_snapshot(cur, blocks)
            supplement_sequence_state(cur, pre_family)
            pre_table_digests = capture_table_digests(cur, declared_row_targets)
            pre_targets = {
                target_identity(t): observe_target(cur, t, pre_family) for t in targets
            }

        outcome["pre_snapshot"] = {
            "family": pre_family,
            "table_row_digests": pre_table_digests,
        }
        outcome["pre_images"] = pre_targets

        # ── ASSERTION 1: expected-pre precheck, BEFORE the candidate runs ──
        precheck_failures = []
        for t in targets:
            tid = target_identity(t)
            observed = pre_targets[tid]
            expected = t.get("expected_pre")
            if observed != expected:
                precheck_failures.append({"target": tid, "expected_pre": expected, "observed_pre": observed})

        write_receipt(receipt_path, outcome)  # PRE-PHASE, fsynced before the candidate executes

        if os.environ.get("CARR_BREAKGLASS_TEST_KILL_AFTER_PREPHASE") == "1":
            # Test-only seam for acceptance (3b): simulate the driver being
            # killed the instant after the pre-phase receipt is durable, so
            # the harness can assert that receipt is present and readable.
            # os._exit skips cleanup exactly as a real SIGKILL would; the
            # open transaction dies with the process and Postgres rolls it
            # back on its own — nothing here pretends otherwise.
            sys.stdout.flush()
            os._exit(137)

        if precheck_failures:
            outcome["verdict"] = "refused_precheck"
            outcome["precheck_failures"] = precheck_failures
            outcome["finished_at"] = time.time()
            write_receipt(receipt_path, outcome)
            conn.rollback()
            print("breakglass_run: REFUSED — expected-pre precheck failed before execution", file=sys.stderr)
            for f in precheck_failures:
                print(f"  target {f['target']}: expected {f['expected_pre']!r}, observed {f['observed_pre']!r}",
                      file=sys.stderr)
            return 1

        # ── execute the approved candidate ──
        with conn.cursor() as cur:
            cur.execute(candidate_sql)
            # THE POST-SNAPSHOT, taken inside the same still-open transaction. ──
            post_family = capture_family_snapshot(cur, blocks)
            supplement_sequence_state(cur, post_family)
            post_table_digests = capture_table_digests(cur, declared_row_targets)
            post_targets = {
                target_identity(t): observe_target(cur, t, post_family) for t in targets
            }

        outcome["post_snapshot"] = {
            "family": post_family,
            "table_row_digests": post_table_digests,
        }
        outcome["post_images"] = post_targets

        # ── ASSERTION 2: no undeclared change on the surface ──
        undeclared = diff_undeclared(pre_family, post_family, declared_definition_ids)
        undeclared_table_digests = [
            v for v in diff_table_digests(pre_table_digests, post_table_digests)
        ]

        # ── ASSERTION 3: declared targets equal their expected post-state ──
        postcheck_failures = []
        for t in targets:
            tid = target_identity(t)
            observed = post_targets[tid]
            expected = t.get("expected_post")
            if observed != expected:
                postcheck_failures.append({"target": tid, "expected_post": expected, "observed_post": observed})

        sequence_residuals = diff_sequence_residuals(
            pre_family.get(SEQUENCE_FAMILY, {}), post_family.get(SEQUENCE_FAMILY, {})
        )

        all_clear = not undeclared and not undeclared_table_digests and not postcheck_failures
        outcome["verdict"] = "committed" if all_clear else "rolled_back"
        outcome["undeclared_surface_changes"] = undeclared
        outcome["undeclared_table_digest_changes"] = undeclared_table_digests
        outcome["postcheck_failures"] = postcheck_failures
        outcome["sequence_residuals"] = sequence_residuals
        outcome["finished_at"] = time.time()

        if all_clear:
            conn.commit()
        else:
            conn.rollback()

        write_receipt(receipt_path, outcome)  # OUTCOME PHASE folded into the same document

    if outcome["verdict"] == "committed":
        print("breakglass_run: COMMITTED")
        if sequence_residuals:
            print(f"breakglass_run: {len(sequence_residuals)} sequence residual(s) — see receipt, not auto-classified")
        return 0
    print("breakglass_run: ROLLED BACK", file=sys.stderr)
    for v in undeclared:
        print(f"  undeclared change: {v['family']} {v['identity_key']}", file=sys.stderr)
    for v in undeclared_table_digests:
        print(f"  undeclared collateral row change in table: {v['table']}", file=sys.stderr)
    for f in postcheck_failures:
        print(f"  target {f['target']} did not reach its declared expected_post", file=sys.stderr)
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--approved", required=True, type=Path, help="the run bundle (see gen_breakglass_run.py)")
    parser.add_argument("--receipt", required=True, type=Path, help="where to write the two-phase receipt JSON")
    args = parser.parse_args(argv)
    try:
        return run(args.approved, args.receipt)
    except BundleError as exc:
        sys.exit(f"breakglass_run: {exc}")
