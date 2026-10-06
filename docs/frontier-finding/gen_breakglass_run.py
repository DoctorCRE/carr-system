"""gen_breakglass_run.py — Artifact C's per-use run-bundle generator (WR-000046).

GENERATE MODE. Takes a candidate SQL file and an expected-delta target
manifest (JSON: per declared target, its expected PRE-state and expected
POST-state — definition text for a catalog object, or row values by key for
a table row) and emits the self-contained run bundle that
docs/frontier-finding/breakglass_run.py executes:

    .venv/bin/python -c "import sys; sys.path.insert(0, sys.argv[1]); \\
        import gen_breakglass_run as m; sys.exit(m.main(sys.argv[2:]))" \\
        docs/frontier-finding \\
        --candidate <candidate>.sql --manifest <targets>.json \\
        --wr-note-ref "<ref>" --out <run>.sql

INVOKE IT THROUGH THAT SHIM AND NOTHING ELSE. This file is deliberately an
IMPORT-ONLY library — no shebang, no `if __name__ == "__main__"` block — so the
sealed SCAC mutation registry never counts it as a script entrypoint (see
README.md and breakglass_selftest.py:46). Running it directly as
`.venv/bin/python docs/frontier-finding/gen_breakglass_run.py ...` DEFINES THE
MODULE, WRITES NO BUNDLE, PRINTS NOTHING, AND EXITS 0 — a silent success. That
is the dangerous shape: a session following the old wording sees exit 0 and can
record "pre-activation baseline captured" when nothing was captured, leaving a
production-apply prerequisite looking satisfied while being empty. Measured
2026-09-03: REAL_EXIT=0, no output file. The docstring itself used to give the
direct form on this line and at RESTORE MODE below; both are corrected.

REFUSES a candidate that trips contains_transaction_control(), imported
VERBATIM from tools/migrate.py — not reimplemented, per the plan.

REFUSES (the categorical floor, F01 condition (d)) a manifest that declares a
target inside ops.scac_*, a named SIEP control table, or a name obviously
shaped like one — production changes to the reference-monitor's own catalog
are refused a run entirely, not merely gated by the assertion. This list is
reviewed and extendable, not complete (Section T); --restore is the plan's
one named exception and skips it.

Prints the run-script / candidate / manifest sha256 digests the driver will
independently recompute and that a WR-000046 approval note must carry —
computed with the exact same functions the driver uses, imported from
breakglass_run.py rather than re-derived, so the two can never drift apart.

RESTORE MODE:

    .venv/bin/python -c "import sys; sys.path.insert(0, sys.argv[1]); \\
        import gen_breakglass_run as m; sys.exit(m.main(sys.argv[2:]))" \\
        docs/frontier-finding \\
        --restore <receipt>.json --out <restore-run>.sql

Generates a restore run for a NAMED receipt (one that actually committed —
a refused or rolled-back run changed nothing, so there is nothing to restore
from it). The restore bundle's manifest carries an `identity_requirement`
(the receipt's own recorded identity tuple AND its candidate sha256) that
breakglass_run.py checks against the LIVE connection before doing anything
else — refusing on EITHER axis (a different database, or the same database
on a different endpoint host) is the driver's job, not this generator's,
because only the driver ever holds a live connection.

COMPARE-AND-SWAP is not new driver logic: each restore target's declared
expected_pre is set to the ORIGINAL receipt's recorded POST-state for that
target, so the driver's ordinary assertion (1) — the expected-pre precheck
that already runs before any candidate executes — refuses the restore
outright if the world has moved since the incident. Restoration SQL is
synthesized from the receipt's stored pre-images.

RESTORATION IS SCOPED, HONESTLY. Row targets are always restorable — a full
pre-image and post-image were captured for every declared row target, so the
three shapes (update-back, insert-undo, delete-undo) are all mechanical.
Definition targets are restorable only where the snapshot captured a literal,
replayable definition text (currently: a view body via pg_rewrite's
`def` column, i.e. pg_get_ruledef output). Every other definition-kind
target REFUSES restoration generation, NAMING the family — dropped objects
with dependent state, identity/generated columns, and trigger-mediated
cascades are exactly the "designed forward fix" territory the plan names,
and no attempt is made to be clever about them here.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

# Same reuse pattern as tools/db-tap.py's _load_credential_module: the run
# bundle format (build_bundle/parse_bundle), the digest helpers, and
# MANIFEST_SCHEMA_VERSION all live in breakglass_run.py so the generator and
# the driver can never quietly drift into two different bundle shapes.
_spec = importlib.util.spec_from_file_location("breakglass_run", HERE / "breakglass_run.py")
if _spec is None or _spec.loader is None:
    sys.exit("gen_breakglass_run: breakglass_run.py is unavailable")
breakglass_run = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(breakglass_run)

# CATEGORICAL FLOOR (reviewed, extendable — Section T). Anything shaped like
# the reference monitor's own catalog is refused a generated run entirely,
# not merely gated by the three-sided assertion.
SIEP_CONTROL_PREFIXES = ("ops.scac_",)
SIEP_CONTROL_EXACT = {
    "ops.enforcement_control_catalog",
    "ops.scac_reference_monitor_mode_event",
    "ops.scac_mutation_registry_version",
}


def _named(target: dict) -> str:
    if target["kind"] == "row":
        return target["table"]
    return target["identity_key"]


def categorical_floor_violation(target: dict) -> str | None:
    name = _named(target)
    if name in SIEP_CONTROL_EXACT:
        return name
    for prefix in SIEP_CONTROL_PREFIXES:
        if name.startswith(prefix):
            return name
    return None


def validate_target_shape(target: dict) -> None:
    if target.get("kind") not in ("row", "definition"):
        raise SystemExit(f"gen_breakglass_run: target kind must be 'row' or 'definition': {target!r}")
    if target["kind"] == "row":
        if not isinstance(target.get("table"), str) or not isinstance(target.get("key"), dict) or not target["key"]:
            raise SystemExit(f"gen_breakglass_run: row target needs 'table' and a nonempty 'key': {target!r}")
    else:
        if not isinstance(target.get("family"), str) or not isinstance(target.get("identity_key"), str):
            raise SystemExit(f"gen_breakglass_run: definition target needs 'family' and 'identity_key': {target!r}")
    if "expected_pre" not in target or "expected_post" not in target:
        raise SystemExit(f"gen_breakglass_run: target missing expected_pre/expected_post: {target!r}")


def cmd_generate(args: argparse.Namespace) -> int:
    candidate_sql = Path(args.candidate).read_text(encoding="utf-8")
    if breakglass_run.migrate.contains_transaction_control(candidate_sql):
        sys.exit(
            "gen_breakglass_run: REFUSED — candidate SQL contains transaction control "
            "(BEGIN/COMMIT/ROLLBACK/END/ABORT/START TRANSACTION/PREPARE TRANSACTION at "
            "top level). The driver owns the transaction; the candidate must not."
        )

    raw_manifest_input = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    targets = raw_manifest_input["targets"] if isinstance(raw_manifest_input, dict) else raw_manifest_input
    if not isinstance(targets, list):
        sys.exit("gen_breakglass_run: manifest 'targets' must be a list (an empty list is a legitimate "
                 "zero-declared-targets run, used to assert nothing changes)")
    for target in targets:
        validate_target_shape(target)
        violation = categorical_floor_violation(target)
        if violation is not None:
            sys.exit(
                f"gen_breakglass_run: REFUSED — target {violation!r} is inside the "
                "categorical floor (ops.scac_* / a named SIEP control table). No run "
                "is generated for it outside --restore."
            )

    manifest = {
        "manifest_version": breakglass_run.MANIFEST_SCHEMA_VERSION,
        "wr_note_ref": args.wr_note_ref,
        "targets": targets,
    }
    if isinstance(raw_manifest_input, dict) and "expected_transactional_delta" in raw_manifest_input:
        manifest["expected_transactional_delta"] = raw_manifest_input["expected_transactional_delta"]

    bundle_text = breakglass_run.build_bundle(manifest, candidate_sql)
    out_path = Path(args.out)
    out_path.write_text(bundle_text, encoding="utf-8")

    _print_digests(out_path, bundle_text, candidate_sql, manifest)
    return 0


def _print_digests(out_path: Path, bundle_text: str, candidate_sql: str, manifest: dict) -> None:
    print(f"gen_breakglass_run: wrote {out_path}")
    print(f"  run-script sha256: {breakglass_run.sha256_text(bundle_text)}")
    print(f"  candidate sha256:  {breakglass_run.sha256_text(candidate_sql)}")
    print(f"  manifest sha256:   {breakglass_run.sha256_text(breakglass_run.canonical_json(manifest))}")
    print("  Record these three digests on the WR-000046 approval note BEFORE this run script executes.")


# ── restore mode ──────────────────────────────────────────────────────────


def _sql_literal(value) -> str:
    """Conservative literal quoting with no live connection required. Scoped
    to the JSON-safe value shapes a stored pre-image can hold: None, bool,
    int/float, str, and dict/list (round-tripped through to_jsonb originally,
    written back out as a jsonb literal)."""
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, str):
        return "'" + value.replace("'", "''") + "'"
    if isinstance(value, (dict, list)):
        return "'" + json.dumps(value).replace("'", "''") + "'::jsonb"
    return "'" + str(value).replace("'", "''") + "'"


def _quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _qualified_ident(qualified: str) -> str:
    schema, _, rest = qualified.partition(".")
    return _quote_ident(schema) + "." + _quote_ident(rest)


def _row_key_where(key: dict) -> str:
    return " AND ".join(f"{_quote_ident(col)} = {_sql_literal(val)}" for col, val in key.items())


def _row_restore_statement(table: str, key: dict, pre_image: dict | None, post_image: dict | None) -> str | None:
    where = _row_key_where(key)
    ident = _qualified_ident(table)
    if pre_image is not None and post_image is not None:
        assignments = ", ".join(f"{_quote_ident(c)} = {_sql_literal(v)}" for c, v in pre_image.items())
        return f"UPDATE {ident} SET {assignments} WHERE {where};"
    if pre_image is not None and post_image is None:
        cols = list(pre_image.keys())
        col_list = ", ".join(_quote_ident(c) for c in cols)
        val_list = ", ".join(_sql_literal(pre_image[c]) for c in cols)
        return f"INSERT INTO {ident} ({col_list}) VALUES ({val_list});"
    if pre_image is None and post_image is not None:
        return f"DELETE FROM {ident} WHERE {where};"
    return None  # both None: nothing to restore


_REPLAYABLE_DEF_FAMILIES = {
    "pg_rewrite": ("def", lambda text: text.replace("CREATE RULE", "CREATE OR REPLACE RULE", 1)),
}


def _definition_restore_statement(family: str, identity_key: str, pre_image: dict | None) -> tuple[str | None, str | None]:
    """Returns (statement, refusal_reason). pre_image is None means the
    object did not exist before the incident, i.e. the incident CREATED it —
    restoring means dropping it, which this generator does not attempt for
    definition-kind targets (naming the family is the honest answer, per the
    plan's own scoping of restoration assistance)."""
    if pre_image is None:
        return None, f"{family} {identity_key}: pre-image is absent (object was created by the incident); dropping it is not attempted here"
    spec = _REPLAYABLE_DEF_FAMILIES.get(family)
    if spec is None:
        return None, f"{family} {identity_key}: no replayable definition text is captured for this family"
    field, transform = spec
    text = pre_image.get(field)
    if not text:
        return None, f"{family} {identity_key}: pre-image has no {field!r} text"
    return transform(text), None


def cmd_restore(args: argparse.Namespace) -> int:
    receipt = json.loads(Path(args.restore).read_text(encoding="utf-8"))
    reasons = []
    for required in ("verdict", "manifest", "pre_images", "post_images", "identity_tuple", "candidate_sha256"):
        if required not in receipt:
            reasons.append(f"receipt is missing required field {required!r}")
    if reasons:
        sys.exit("gen_breakglass_run: REFUSED — wrong-row receipt: " + "; ".join(reasons))
    if receipt["verdict"] != "committed":
        sys.exit(
            f"gen_breakglass_run: REFUSED — wrong-row receipt: verdict was {receipt['verdict']!r}, "
            "not 'committed'; a refused or rolled-back run changed nothing to restore"
        )

    targets = receipt["manifest"]["targets"]
    statements: list[str] = []
    restore_targets: list[dict] = []
    refusals: list[str] = []
    for target in targets:
        tid = breakglass_run.target_identity(target)
        pre_image = receipt["pre_images"].get(tid)
        post_image = receipt["post_images"].get(tid)
        if target["kind"] == "row":
            statement = _row_restore_statement(target["table"], target["key"], pre_image, post_image)
            if statement is not None:
                statements.append(statement)
            restore_targets.append({
                **target,
                "expected_pre": post_image,   # compare-and-swap: world must still match the incident's post-state
                "expected_post": pre_image,   # restoring means reaching the incident's pre-state
            })
        else:
            statement, refusal = _definition_restore_statement(target["family"], target["identity_key"], pre_image)
            if refusal is not None:
                refusals.append(refusal)
                continue
            assert statement is not None, (
                "_definition_restore_statement invariant: a None refusal always pairs with a non-None statement"
            )
            statements.append(statement)
            restore_targets.append({
                **target,
                "expected_pre": post_image,
                "expected_post": pre_image,
            })

    if refusals:
        sys.exit(
            "gen_breakglass_run: REFUSED — restoration cannot be mechanically generated for every "
            "declared target; this is manual-forward-fix territory for:\n  " + "\n  ".join(refusals)
        )
    if not statements:
        sys.exit("gen_breakglass_run: REFUSED — nothing to restore (no targets produced a statement)")

    manifest = {
        "manifest_version": breakglass_run.MANIFEST_SCHEMA_VERSION,
        "wr_note_ref": f"restore-of:{receipt.get('manifest', {}).get('wr_note_ref', '?')}",
        "targets": restore_targets,
        "identity_requirement": {
            "endpoint_host": receipt["identity_tuple"]["endpoint_host"],
            "database_name": receipt["identity_tuple"]["database_name"],
            "pg_system_identifier": receipt["identity_tuple"]["pg_system_identifier"],
            "candidate_sha256": receipt["candidate_sha256"],
        },
    }
    candidate_sql = "\n".join(statements)
    bundle_text = breakglass_run.build_bundle(manifest, candidate_sql)
    out_path = Path(args.out)
    out_path.write_text(bundle_text, encoding="utf-8")
    _print_digests(out_path, bundle_text, candidate_sql, manifest)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--candidate", help="candidate SQL file (generate mode)")
    parser.add_argument("--manifest", help="expected-delta target manifest JSON (generate mode)")
    parser.add_argument("--wr-note-ref", default="", help="the WR-000046 note reference this run will be approved under")
    parser.add_argument("--restore", help="receipt JSON to generate a restore run for (restore mode)")
    parser.add_argument("--out", required=True, help="path to write the generated run bundle")
    args = parser.parse_args(argv)

    if args.restore:
        if args.candidate or args.manifest:
            sys.exit("gen_breakglass_run: --restore is exclusive with --candidate/--manifest")
        return cmd_restore(args)
    if not args.candidate or not args.manifest:
        sys.exit("gen_breakglass_run: --candidate and --manifest are required in generate mode")
    return cmd_generate(args)
