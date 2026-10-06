#!/usr/bin/env python3
"""ops/rule-classification-parity-check.py — the file map and the database
must agree about which rules are actually enforced.

WHY THIS EXISTS (WR-000019 slice S10, part 3). Two classifications of the same
219 rules have lived side by side with nothing ever comparing them:

  * ops/config/rule-enforcement-map.json — a hand-authored, CI-validated file
    naming five categories (hard_pre_action, transactional_schema,
    post_action_verification, session_task_rail, judgment_advisory) and six
    finer enforcement_class values, synced INTO the database only by an
    explicit human running bin/sync-rule-admission-prod.sh --apply.
  * ops.rule_admission.enforcement_class — the database's own three-value
    classification (machine_enforceable, judgment_advisory, human_only),
    written by admit-rule/approve-rule, the only thing ops.applicable_rules()
    and every runtime enforcement check actually reads.

They have disagreed structurally roughly 4x over: the file inventories 218-219
rules, production has admitted only 4 (measured 2026-08-23). Nothing failed
when they disagreed, because nothing ever read both and compared them.

WHAT THIS CHECKS. It reads ops/config/rule-enforcement-map.json (the file
truth) and ops/config/rule-admission-export.v1.json (a committed export of the
database truth — see bin/sync-rule-admission-prod.sh --export and
tools/export-rule-admission.py; that script is the ONLY thing that talks to a
database for this purpose). For every rule id present in BOTH, it normalizes
each side to the same two-bucket structure --

    machine_enforceable  <- file categories hard_pre_action, transactional_schema,
                             post_action_verification, session_task_rail
                          <- DB enforcement_class machine_enforceable, human_only
                             (both are actually gated; human_only just requires
                             a human authority act rather than a mechanical one)
    judgment_advisory     <- file category judgment_advisory
                          <- DB enforcement_class judgment_advisory

-- and fails on any rule whose bucket disagrees between the two sources.

WHAT THIS DELIBERATELY DOES NOT DO. It never reclassifies a rule, never edits
either file, and never requires full coverage — a rule id in the file map with
no counterpart in the export is not a finding here (ops/rule-admission-audit.py
already owns "how many active rules lack an admission row at all"; that is a
DIFFERENT question from "do the two sides disagree about a rule they BOTH
classify"). This check compares only the overlap, honestly reporting how large
that overlap currently is so a shrinking export can never masquerade as full
coverage.

REPOSITORY CONTENT ONLY, no machine state, no database — same standard as
rule-enforcement-map-check.py and the rest of ops/ci.sh's inventory loop. The
export refreshes only when a human runs the sync door against production;
this check simply compares whatever the two committed files currently say.

RUN IT:
    python3 ops/rule-classification-parity-check.py
    python3 ops/rule-classification-parity-check.py --json
"""
from __future__ import annotations

import argparse
import json
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAP = os.path.join(REPO, "ops", "config", "rule-enforcement-map.json")
EXPORT = os.path.join(REPO, "ops", "config", "rule-admission-export.v1.json")

# The five file categories; judgment_advisory alone forms the advisory bucket,
# the other four are the machine_enforceable bucket. Deliberately the SAME
# split rule-enforcement-map-check.py's BUILT_CLASS_BY_CATEGORY already uses
# (those four categories are exactly the keys of that dict) -- this file does
# not invent a second opinion about what the categories mean.
FILE_MACHINE_CATEGORIES = {
    "hard_pre_action", "transactional_schema", "post_action_verification",
    "session_task_rail",
}
FILE_ADVISORY_CATEGORY = "judgment_advisory"

# The database's three enforcement_class values (ops.rule_admission_enforcement_class_check).
# machine_enforceable and human_only both mean SOMETHING gates the rule --
# human_only requires a human authority act rather than a mechanical control,
# but ops.approve_rule refuses to approve a judgment_advisory rule at all, so
# an ACTIVE admitted rule is never structurally advisory in the database.
DB_MACHINE_CLASSES = {"machine_enforceable", "human_only"}
DB_ADVISORY_CLASS = "judgment_advisory"

# These active approved rows carry a broader admission classification than the
# specific residual duty the reviewed map records. The approved admission is
# preserved; the map must not claim a gate that does not enforce the duty.
# Keep the exception exact and fail when either side changes its classification.
PARTIAL_ADMISSION_RULES = {
    "1fcaa63a",  # heavy-build phases beyond admission validators
    "204391be",  # decide a missing product verb is required
    "a2e78f48",  # recognize a known repair
    "b7ec8f3b",  # review DoctorCRE surface grammar
}


def file_bucket(category: str | None) -> str | None:
    if category == FILE_ADVISORY_CATEGORY:
        return "judgment_advisory"
    if category in FILE_MACHINE_CATEGORIES:
        return "machine_enforceable"
    return None


def db_bucket(enforcement_class: str | None) -> str | None:
    if enforcement_class == DB_ADVISORY_CLASS:
        return "judgment_advisory"
    if enforcement_class in DB_MACHINE_CLASSES:
        return "machine_enforceable"
    return None


def load_json(path: str, label: str) -> tuple[dict | None, str | None]:
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh), None
    except FileNotFoundError:
        return None, f"{label} is missing: {path}"
    except json.JSONDecodeError as exc:
        return None, f"{label} is not valid JSON: {exc}"


def compare(map_data: dict, export_data: dict) -> dict:
    """Returns a report dict; never raises on a normal disagreement."""
    rule_controls = map_data.get("rule_controls", {})
    exported_rules = export_data.get("rules", {})
    if not isinstance(rule_controls, dict) or not isinstance(exported_rules, dict):
        return {"errors": ["rule_controls or export rules is not an object"],
                "compared": 0, "mismatches": []}

    errors: list[str] = []
    mismatches: list[dict] = []
    compared = 0
    for rule_id, exported in exported_rules.items():
        if not isinstance(exported, dict):
            errors.append(f"export entry for {rule_id} is not an object")
            continue
        detail = rule_controls.get(rule_id)
        if detail is None:
            # Not a parity finding: the export names a rule the file map does
            # not classify at all. That is a coverage gap in the FILE, which
            # rule-enforcement-map-check.py's own full-coverage requirement
            # would already have caught for an ACTIVE rule -- surfaced here
            # too, distinctly, so a diverging export cannot hide behind
            # "the check only compares overlap".
            errors.append(f"{rule_id} is admitted in the export but has no "
                           "rule-enforcement-map.json entry at all")
            continue
        if not isinstance(detail, dict):
            errors.append(f"{rule_id} rule-enforcement-map entry is not an object")
            continue
        f_bucket = file_bucket(detail.get("category"))
        d_bucket = db_bucket(exported.get("enforcement_class"))
        if f_bucket is None:
            errors.append(f"{rule_id} file category {detail.get('category')!r} "
                          "does not normalize to a known bucket")
            continue
        if d_bucket is None:
            errors.append(f"{rule_id} export enforcement_class "
                          f"{exported.get('enforcement_class')!r} does not "
                          "normalize to a known bucket")
            continue
        compared += 1
        if rule_id in PARTIAL_ADMISSION_RULES:
            if f_bucket != "judgment_advisory" or d_bucket != "machine_enforceable":
                errors.append(f"{rule_id} partial-admission exception is stale")
            continue
        if f_bucket != d_bucket:
            mismatches.append({
                "rule_id": rule_id,
                "file_category": detail.get("category"),
                "file_bucket": f_bucket,
                "db_enforcement_class": exported.get("enforcement_class"),
                "db_bucket": d_bucket,
            })
    return {"errors": errors, "compared": compared, "mismatches": mismatches}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args()

    map_data, map_err = load_json(MAP, "rule-enforcement-map.json")
    if map_err or map_data is None:
        print(f"rule-classification-parity: ERROR — {map_err}")
        return 2
    export_data, export_err = load_json(EXPORT, "rule-admission-export.v1.json")
    if export_err or export_data is None:
        print(f"rule-classification-parity: ERROR — {export_err}")
        return 2

    if export_data.get("schema_version") != 1:
        print("rule-classification-parity: ERROR — export schema_version must be 1")
        return 2
    if map_data.get("schema_version") != 1:
        print("rule-classification-parity: ERROR — enforcement-map schema_version must be 1")
        return 2

    report = compare(map_data, export_data)
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))

    if report["errors"] or report["mismatches"]:
        if not args.json:
            for err in report["errors"]:
                print(f"rule-classification-parity: FAIL — {err}")
            for m in report["mismatches"]:
                print("rule-classification-parity: FAIL — "
                      f"{m['rule_id']} disagrees: file={m['file_category']!r} "
                      f"(bucket {m['file_bucket']}) vs db="
                      f"{m['db_enforcement_class']!r} (bucket {m['db_bucket']})")
        return 1

    if not args.json:
        total_export = len(export_data.get("rules", {}))
        print(f"rule-classification-parity: OK — {report['compared']}/{total_export} "
              "exported rule(s) compared, zero bucket disagreements")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
