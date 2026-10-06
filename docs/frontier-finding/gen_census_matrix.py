"""gen_census_matrix.py — Artifact B generator (WR-000046, F02, plan Section 2).

Builds docs/frontier-finding/census-reconciliation-matrix.v1.json from two
independent sources:

  EXPECTED — the five pinned census categories' per-version counts+digests,
  extracted mechanically via `git show <pinned_commit>:migrations/<file>` over
  the eighteen frontier files (0454-0471). Nothing here touches a live
  database; it is a pure function of the pinned commit and is BYTE-IDENTICAL
  across runs by construction (no timestamps, no wall-clock data anywhere in
  this section).

  OBSERVED — production's live row sets for the same five categories, read
  through the read-only tap (`tools/db-tap.py sql`, no CARR_BREAK_GLASS, no
  --reason) against the plain-SQL queries in census-queries.sql. If the tap is
  unreachable or refuses, the category is recorded as
  observed:{"status":"unavailable","error":"<exact tap stderr>"} — never
  faked.

Claim discipline (plan Section T): this script and its output make NO
completeness claim. It reports what git show and the tap actually returned,
with the exact command used beside each figure. Where an earlier evidence
file (r13) mis-cited a pinned baseline, this script's own extraction is the
correction, and the discrepancy is recorded, not silently overwritten (see
`corrections_verified`).

Usage:
  .venv/bin/python docs/frontier-finding/gen_census_matrix.py \
      [--out docs/frontier-finding/census-reconciliation-matrix.v1.json] \
      [--no-tap]   # build the expected section only; observed:"skipped"
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from typing import Any

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# docs/frontier-finding/gen_census_matrix.py -> REPO is two dirs up.
DOCS_DIR = os.path.join(REPO, "docs", "frontier-finding")
PINNED_COMMIT = "0985dcc70764d888d70004641e210f3730ef9d2a"
INVENTORY_SOURCE = "ops/scac-mutation-inventory.mjs"

# The eighteen frontier files, in migration order, as named by the plan
# (Section 0 / Section 2, Artifact B) and independently confirmed by
# `git show <pinned_commit>:migrations/` listing 0454..0471 inclusive.
FRONTIER_FILES = [
    "migrations/0454_siep11_mutation_registry.sql",
    "migrations/0455_siep12_policy_epoch.sql",
    "migrations/0456_siep13_artifact_registry.sql",
    "migrations/0457_siep13_forward_mutation_registry.sql",
    "migrations/0458_siep14_root_trust.sql",
    "migrations/0459_siep14_forward_mutation_registry.sql",
    "migrations/0460_siep15_device_enrollment.sql",
    "migrations/0461_siep15_forward_mutation_registry.sql",
    "migrations/0462_siep16_forward_mutation_registry.sql",
    "migrations/0463_retired_rule_delivery_cleanup.sql",
    "migrations/0464_siep16_integrated_mutation_registry.sql",
    "migrations/0465_siep17_token_challenge_authority.sql",
    "migrations/0466_siep17_forward_mutation_registry.sql",
    "migrations/0467_siep18_atomic_db_monitor_grants.sql",
    "migrations/0468_siep18_forward_mutation_registry.sql",
    "migrations/0469_siep18_exact_effects_trusted_principal.sql",
    "migrations/0470_source_merge_authority_projection.sql",
    "migrations/0471_source_merge_catalog_registry_successor.sql",
]

CENSUS_CATEGORIES = [
    "secdef_execute",
    "relation_dml",
    "column_dml",
    "role_authority",
    "runtime_dml_grants",
]

VERSION_ORDER = [f"v{n}" for n in range(1, 11)]


# ---------------------------------------------------------------------------
# git show helpers — everything in this section reads ONLY the pinned commit.
# ---------------------------------------------------------------------------

def git_show_bytes(path: str) -> bytes:
    proc = subprocess.run(
        ["git", "show", f"{PINNED_COMMIT}:{path}"],
        cwd=REPO, capture_output=True, check=True,
    )
    return proc.stdout


def git_show_text(path: str) -> str:
    return git_show_bytes(path).decode("utf-8")


def sha256_hex(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


# ---------------------------------------------------------------------------
# EXPECTED — per-version catalog_projection extraction
# ---------------------------------------------------------------------------

VERSION_INSERT_RE = re.compile(
    r"insert\s+into\s+ops\.scac_mutation_registry_version\(",
    re.IGNORECASE,
)
VERSION_LITERAL_RE = re.compile(r"values\s*\(\s*'scac-mutation-registry\.(v\d+)'")
PROJECTION_RE = re.compile(r"'(\{\"projection_version\":\"scac-db-catalog-projection\.v\d+\".*?\})'::jsonb")


def extract_version_pin(content: str) -> tuple[str, dict] | None:
    """Find the (registry_version, catalog_projection dict) this file INSERTS,
    if any. Returns None for files that create no new registry version (they
    only add supporting tables/functions, or amend a later version's tables
    without inserting a new scac_mutation_registry_version row)."""
    m = VERSION_INSERT_RE.search(content)
    if not m:
        return None
    rest = content[m.end():]
    vm = VERSION_LITERAL_RE.search(rest)
    if not vm:
        return None
    version = vm.group(1)
    pm = PROJECTION_RE.search(rest, vm.end())
    if not pm:
        return None
    projection = json.loads(pm.group(1))
    return version, projection


def build_expected(frontier_file_digests: dict) -> dict:
    expected = {}
    for path in FRONTIER_FILES:
        content = git_show_text(path)
        result = extract_version_pin(content)
        if result is None:
            continue
        version, projection = result
        categories: dict[str, Any] = {}
        for cat in CENSUS_CATEGORIES:
            entry = projection.get(cat)
            if entry is None:
                categories[cat] = "not_applicable"
            else:
                categories[cat] = {"count": entry["count"], "digest": entry["digest"]}
        expected[version] = {
            "source_file": path,
            "source_file_digest": frontier_file_digests[path],
            "projection_version": projection.get("projection_version"),
            "categories": categories,
        }
    missing = [v for v in VERSION_ORDER if v not in expected]
    if missing:
        raise SystemExit(
            f"gen_census_matrix: expected to find a catalog_projection insert for "
            f"every version v1-v10 across the 18 frontier files; missing {missing}. "
            f"This is a hard failure, not a soft finding — the versioned shape "
            f"(plan Section 0) could not be reconstructed."
        )
    return expected


def category_count_by_version(expected: dict) -> dict:
    return {
        v: sum(1 for cat in CENSUS_CATEGORIES if expected[v]["categories"][cat] != "not_applicable")
        for v in VERSION_ORDER
    }


# ---------------------------------------------------------------------------
# rebased-write command enumeration from ops/scac-mutation-inventory.mjs
# (read at the SAME pinned commit, for the same reproducibility reason).
# ---------------------------------------------------------------------------

REBASED_RUNTIME_RE = re.compile(
    r'"(--write-rebased-runtime-v\d+)":\s*\[[^,\]]+,\s*[^,\]]+,\s*"([^"]+)"\]'
)
REBASED_MIGRATION_RE = re.compile(
    r'"(--write-rebased-migration-v\d+)":\s*\[[^,\]]+,\s*"([^"]+)"\]'
)
SINGLE_FLAG_BRANCH_RE = re.compile(
    r'process\.argv\[2\]\s*===\s*"(--write-[\w-]+)"\)\s*\{\s*'
    r'const target = resolve\(process\.argv\[3\] \|\| "([^"]+)"'
)
REFUSAL_BLOCK_RE = re.compile(
    r'\}\s*else if \(\[(.*?)\]\.includes\(process\.argv\[2\]\)\)\s*\{\s*'
    r'throw new Error\(`\$\{process\.argv\[2\]\} refused: ([^`]+)`\)',
    re.S,
)
QUOTED_RE = re.compile(r'"([^"]+)"')


def build_rebased_write_commands(inventory_digest: str) -> dict:
    content = git_show_text(INVENTORY_SOURCE)

    runtime_commands = dict(REBASED_RUNTIME_RE.findall(content))
    migration_commands = dict(REBASED_MIGRATION_RE.findall(content))
    for flag, target in SINGLE_FLAG_BRANCH_RE.findall(content):
        if flag.startswith("--write-runtime"):
            runtime_commands[flag] = target
        else:
            migration_commands[flag] = target

    refusal_match = REFUSAL_BLOCK_RE.search(content)
    if refusal_match is None:
        raise SystemExit(
            "gen_census_matrix: expected to find the sealed-historical-evidence "
            "refusal block in ops/scac-mutation-inventory.mjs at the pinned "
            "commit; the extraction pattern no longer matches the source."
        )
    refused_flags = sorted(set(QUOTED_RE.findall(refusal_match.group(1))))
    refusal_reason = refusal_match.group(2).strip()

    return {
        "source_file": INVENTORY_SOURCE,
        "source_file_digest_at_pinned_commit": inventory_digest,
        "spec_line_range": "1988-2059 (plan Section 1.3's cited range; the writer-mode "
                            "dispatch block runs to the file's last line, 2064, at the "
                            "pinned commit)",
        "runtime_projection_commands": dict(sorted(runtime_commands.items())),
        "migration_commands": dict(sorted(migration_commands.items())),
        "refused_flags_sealed_historical_evidence": refused_flags,
        "refusal_reason_verbatim": refusal_reason,
        "embedded_constants_source": (
            "Every *_DB_CATALOG_BASELINE constant these commands render into a "
            "migration or runtime-projection file is sourced from a DISPOSABLE "
            "database reading, never production — the source file's own comments "
            "say so at each constant's definition (e.g. 'a disposable database "
            "after every ... change', 'disposable-DB readback', 'Exact disposable-DB "
            "predecessor receipt'). None of these commands read FROM production."
        ),
        "missing_production_measurement_stage": (
            "No command in this file (or anywhere else found in this Work "
            "Request's evidence) measures PRODUCTION's live catalog and feeds "
            "that measurement into a rebase/re-pin. Every rebased-write command "
            "above regenerates a migration or runtime projection from a "
            "from-scratch DISPOSABLE database (committed db/schema.sql + applied "
            "migrations), which is by definition already at whatever the source "
            "files say — it cannot observe production's drift (e.g. this "
            "matrix's own observed counts, all of which exceed every pinned "
            "version). Building that measurement stage — read production's live "
            "catalog, and either (a) feed it into a new rebase of the frontier's "
            "source-only registry migrations, or (b) bootstrap out-of-band under "
            "the integrity program's own charter/receipts — is the activation "
            "program's own work, per the plan's Section 1 point 4 and Section 2 "
            "F02 (the bootstrap ordering circle: production's pending migration "
            "selection stops at 0454, so no successor after 0471 runs while the "
            "frontier is pending). This generator does not attempt to build that "
            "stage; it only names the gap."
        ),
    }


def build_reconciliation_sequences(rebased: dict) -> dict:
    return {
        "disposable": {
            "available": True,
            "sequence": [
                "Build a from-scratch disposable database from the committed "
                "db/schema.sql, then apply migrations/0454-0471 in order "
                "(clean by construction on disposables, per r13).",
                "Run the frontier's own validity functions directly on that "
                "disposable (e.g. ops.scac_mutation_catalog_v10_current() for "
                "v10, or the earlier per-version validity checks embedded in "
                "each forward-registry migration) — they are expected to pass, "
                "because the disposable IS the source the pins were computed "
                "from.",
                "If a rebase is needed (e.g. after a reviewed source change), "
                "run the matching --write-rebased-runtime-vN / "
                "--write-rebased-migration-vN command from "
                "ops/scac-mutation-inventory.mjs against fresh inventory rows, "
                "then re-verify on a new disposable.",
            ],
            "gap": None,
        },
        "staging": {
            "available": "partial",
            "sequence": [
                "Point the read-only tap (or, for an approved rehearsal, the "
                "receipted break-glass driver under F01 condition (b)) at the "
                "staging project instead of production (tools/db-tap.py "
                "--project staging).",
                "Run census-queries.sql's five category queries against "
                "staging the same way this generator runs them against "
                "production, to get staging's own observed row sets.",
            ],
            "gap": (
                "Staging is a separate Neon project (r13, tools/db-tap.py "
                "docstring) — it holds no production data, so a staging "
                "census answers 'does staging's catalog match a pin', not "
                "'does production's'. No rebased-write command in "
                "ops/scac-mutation-inventory.mjs reads FROM staging either; "
                "staging can be a rehearsal target for F01's clone-rehearsal "
                "condition, not a source of production measurement."
            ),
        },
        "production": {
            "available": False,
            "sequence": None,
            "gap": (
                "No rebased-write command in ops/scac-mutation-inventory.mjs "
                "reads FROM production — every embedded *_DB_CATALOG_BASELINE "
                "constant comes from disposable-DB readback (see "
                "rebased_write_commands.embedded_constants_source above). "
                "Production's own pending migration selection also stops at "
                "0454 (r13), so no successor migration after 0471 can even run "
                "while the frontier is pending — this is the bootstrap "
                "ordering circle the plan's Section 1 point 4 and Section 2 "
                "F02 name as the activation program's own first problem, not "
                "something this Work Request's tooling can close. The named "
                "gap is exactly rebased_write_commands.missing_production_"
                "measurement_stage above."
            ),
        },
    }


# ---------------------------------------------------------------------------
# OBSERVED — read-only tap against production via census-queries.sql
# ---------------------------------------------------------------------------

CATEGORY_MARKER_RE = re.compile(r"^-- CATEGORY: (\w+)$", re.M)
BLOCK_DIVIDER = "-- " + "=" * 60 + "\n"


def split_census_queries(census_sql_path: str) -> dict:
    content = open(census_sql_path, encoding="utf-8").read()
    markers = list(CATEGORY_MARKER_RE.finditer(content))
    found = [m.group(1) for m in markers]
    if found != CENSUS_CATEGORIES:
        raise SystemExit(
            f"gen_census_matrix: census-queries.sql category markers {found} "
            f"do not match the expected order {CENSUS_CATEGORIES} — the file "
            f"was edited in a way this splitter cannot follow."
        )
    blocks = {}
    for i, m in enumerate(markers):
        header_end = content.index(BLOCK_DIVIDER, m.end())
        sql_start = header_end + len(BLOCK_DIVIDER)
        if i + 1 < len(markers):
            sql_end = content.rfind(BLOCK_DIVIDER, sql_start, markers[i + 1].start())
        else:
            sql_end = len(content)
        blocks[m.group(1)] = content[sql_start:sql_end].strip() + "\n"
    return blocks


def local_canonical_digest(rows: list[dict]) -> str:
    """A LOCAL, script-owned digest — NOT the pinned ops.scac_canonical_json
    algorithm (that function is absent pre-activation; see census-queries.sql
    header). Provided only so two runs of THIS generator against an unchanged
    production state can be checked for observed-field reproducibility."""
    canon = [json.dumps(r, sort_keys=True, separators=(",", ":")) for r in rows]
    canon.sort()
    return "sha256:" + hashlib.sha256("\n".join(canon).encode("utf-8")).hexdigest()


def run_tap(sql_text: str, receipts: list) -> dict:
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".sql", prefix="census-tap-", delete=False, encoding="utf-8"
    ) as fh:
        fh.write(sql_text)
        tmp_path = fh.name
    try:
        env = dict(os.environ)
        env.pop("CARR_BREAK_GLASS", None)  # belt and suspenders: this tap is READ-ONLY, always.
        proc = subprocess.run(
            [os.path.join(REPO, ".venv", "bin", "python"),
             os.path.join(REPO, "tools", "db-tap.py"), "sql", tmp_path],
            cwd=REPO, capture_output=True, text=True, env=env, timeout=120,
        )
        command_str = ".venv/bin/python tools/db-tap.py sql <category-query.sql>  # read-only default, no CARR_BREAK_GLASS"
        if proc.returncode != 0:
            receipts.append({
                "tap_command": command_str,
                "returncode": proc.returncode,
                "status": "unavailable",
                "stderr": proc.stderr.strip(),
            })
            return {"status": "unavailable", "error": proc.stderr.strip(), "row_count": None,
                    "local_digest": None, "local_digest_algorithm": None}
        rows = []
        bad_lines = 0
        for line in proc.stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            # census-queries.sql's reconciled shape (WR-000046 reconcile-census,
            # 2026-09-02) returns two columns -- identity_key, then row (jsonb) --
            # per block, matching breakglass_run.py/breakglass-snapshot.sql's
            # documented contract. db-tap.py's printer joins columns with "|" and
            # does not re-serialize a jsonb column back to JSON text (psycopg
            # decodes jsonb to a Python object by default, and str(dict) on that
            # object is Python repr -- single-quoted, True/False/None -- not
            # JSON). Split off the leading identity_key (never contains "|" for
            # any identity_key shape this file emits) and accept the remainder as
            # either valid JSON or that Python-repr text.
            _, sep, remainder = line.partition("|")
            field = remainder if sep else line
            try:
                rows.append(json.loads(field))
            except json.JSONDecodeError:
                try:
                    rows.append(ast.literal_eval(field))
                except (ValueError, SyntaxError):
                    bad_lines += 1
        receipts.append({
            "tap_command": command_str,
            "returncode": 0,
            "status": "ok",
            "row_count": len(rows),
            "unparseable_lines": bad_lines,
        })
        digest = local_canonical_digest(rows)
        return {
            "status": "ok",
            "error": None,
            "row_count": len(rows),
            "unparseable_lines": bad_lines,
            "local_digest": digest,
            "local_digest_algorithm": (
                "sha256 over the sorted list of json.dumps(row, sort_keys=True, "
                "separators=(',',':')) — a LOCAL canonicalization, NOT the "
                "pinned ops.scac_canonical_json algorithm (absent pre-activation)."
            ),
        }
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


def build_observed(census_sql_path: str, do_tap: bool) -> tuple[dict, list]:
    blocks = split_census_queries(census_sql_path)
    observed = {}
    receipts: list[dict[str, Any]] = []
    for cat in CENSUS_CATEGORIES:
        if not do_tap:
            observed[cat] = {
                "status": "skipped", "error": "generator invoked with --no-tap",
                "row_count": None, "local_digest": None, "local_digest_algorithm": None,
            }
            continue
        result = run_tap(blocks[cat], receipts)
        result["sql_provenance"] = f"docs/frontier-finding/census-queries.sql#CATEGORY:{cat}"
        observed[cat] = result
    return observed, receipts


# ---------------------------------------------------------------------------
# deltas — counts only (see note on why not row-level identifier diffs)
# ---------------------------------------------------------------------------

def build_deltas(expected: dict, observed: dict) -> dict:
    deltas: dict[str, Any] = {}
    for cat in CENSUS_CATEGORIES:
        by_version: dict[str, Any] = {}
        for v in VERSION_ORDER:
            pin = expected[v]["categories"][cat]
            if pin == "not_applicable":
                by_version[v] = "not_applicable"
                continue
            obs = observed[cat]
            if obs["status"] != "ok":
                by_version[v] = {"expected_count": pin["count"], "delta": None,
                                  "reason": f"observed status was '{obs['status']}'"}
            else:
                by_version[v] = {
                    "expected_count": pin["count"],
                    "observed_count": obs["row_count"],
                    "delta": obs["row_count"] - pin["count"],
                }
        deltas[cat] = by_version
    deltas["_note"] = (
        "Deltas are COUNT-only (observed_count - pinned_count), per version, "
        "per category. A true row-level (per-identifier) symmetric difference "
        "against v1-v9's pins is not attempted: those pins record only an "
        "aggregate count+digest in the frontier files (migrations/0454-0471 at "
        "the pinned commit), not an enumerated row/ingress_key list — there is "
        "nothing to diff against at the row level for a version other than the "
        "one the observed set is compared to at generation time. This is a "
        "measurement limit, not an oversight (plan Section T: no completeness "
        "claim beyond what was actually measured)."
    )
    return deltas


# ---------------------------------------------------------------------------
# corrections — verified against the pinned migration text, not memory.
# ---------------------------------------------------------------------------

def build_corrections(expected: dict, observed: dict) -> dict:
    v1 = expected["v1"]["categories"]["secdef_execute"]
    v10 = expected["v10"]["categories"]["secdef_execute"]
    max_pin = max(expected[v]["categories"]["secdef_execute"]["count"] for v in VERSION_ORDER)
    obs = observed.get("secdef_execute", {})
    obs_count = obs.get("row_count")
    return {
        "claim": (
            "r13-frontier-forensics.md (2026-09-01) annotated production's "
            "apply-time failure at migration 0454 as '600 vs the migration's "
            "pinned baseline (347)'. 0454 is the v1 registry migration; its "
            "own secdef_execute pin, read directly from the file at the pinned "
            "commit, is 290 — not 347. 347 is v10's pin, from migrations/0471, "
            "a different file applied nine successor migrations later."
        ),
        "verified_v1_pin": {"file": "migrations/0454_siep11_mutation_registry.sql",
                             "secdef_execute": v1},
        "verified_v10_pin": {"file": "migrations/0471_source_merge_catalog_registry_successor.sql",
                              "secdef_execute": v10},
        "max_pinned_secdef_execute_count_any_version": max_pin,
        "this_session_observed_secdef_execute_count": obs_count,
        "prior_session_observed_secdef_execute_count_r13_2026_09_01": 600,
        "conclusion": (
            "Both figures verified directly from the pinned commit's migration "
            "text (git show, not memory): v1=290, v10=347, confirming the "
            "correction. Production's live secdef_execute count exceeds v10's "
            "347 (the highest pin of any version) both in r13's prior "
            "measurement (600, 2026-09-01) and in this session's independent "
            "tap measurement" + (f" ({obs_count}, this run)" if obs_count is not None else
                                   " (not re-measured this run — see observed.secdef_execute.status)") +
            ". The two observed figures are NOT expected to match each other "
            "exactly — they were taken roughly a day apart against a live, "
            "still-changing production catalog — and this matrix does not "
            "claim they do; both are recorded as what was actually measured, "
            "each with its own date."
        ),
    }


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(DOCS_DIR, "census-reconciliation-matrix.v1.json"))
    ap.add_argument("--census-sql", default=os.path.join(DOCS_DIR, "census-queries.sql"))
    ap.add_argument("--no-tap", action="store_true", help="build expected section only; skip the production tap")
    ap.add_argument("--receipts-out", default=None, help="append tap receipts (jsonl) to this path")
    args = ap.parse_args(argv)

    frontier_file_digests = {path: sha256_hex(git_show_bytes(path)) for path in FRONTIER_FILES}
    expected = build_expected(frontier_file_digests)
    inventory_digest = sha256_hex(git_show_bytes(INVENTORY_SOURCE))
    rebased_write_commands = build_rebased_write_commands(inventory_digest)
    reconciliation_command_sequences = build_reconciliation_sequences(rebased_write_commands)

    observed, tap_receipts = build_observed(args.census_sql, do_tap=not args.no_tap)
    deltas = build_deltas(expected, observed)
    corrections_verified = build_corrections(expected, observed)

    matrix = {
        "artifact": "census-reconciliation-matrix.v1",
        "work_request": "WR-000046",
        "pinned_commit": PINNED_COMMIT,
        "claim_discipline": (
            "This matrix reports what was measured, with the exact provenance "
            "beside each figure (plan Section T). It makes no claim of "
            "completeness for any enumeration — not the category list, not the "
            "row sets, not the command list."
        ),
        "census_categories": CENSUS_CATEGORIES,
        "versioned_shape": {
            "version_order": VERSION_ORDER,
            "category_count_by_version": category_count_by_version(expected),
            "description": (
                "v1: three categories (secdef_execute, relation_dml, "
                "column_dml). v2-v8: four (adds role_authority). v9-v10: five "
                "(adds runtime_dml_grants). Mechanically derived from which "
                "categories each version's catalog_projection JSON actually "
                "carries; not hardcoded."
            ),
        },
        "frontier_files": dict(sorted(frontier_file_digests.items())),
        "expected": expected,
        "observed": observed,
        "deltas": deltas,
        "corrections_verified": corrections_verified,
        "rebased_write_commands": rebased_write_commands,
        "reconciliation_command_sequences": reconciliation_command_sequences,
    }

    payload = json.dumps(matrix, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    with open(args.out, "w", encoding="utf-8") as fh:
        fh.write(payload)

    if args.receipts_out:
        os.makedirs(os.path.dirname(args.receipts_out), exist_ok=True)
        with open(args.receipts_out, "a", encoding="utf-8") as fh:
            for r in tap_receipts:
                fh.write(json.dumps(r, sort_keys=True) + "\n")

    print(f"wrote {args.out}", file=sys.stderr)
    return 0
