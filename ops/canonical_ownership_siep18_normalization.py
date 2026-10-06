"""Narrow SIEP-18 normalization for the ownership catalog fingerprint."""

from __future__ import annotations

from copy import deepcopy
import json
import os
from typing import Any, Iterable


ROW_TRIGGER_NAME = "scac_reference_monitor_guard_row"
TRUNCATE_TRIGGER_NAME = "scac_reference_monitor_guard_truncate"
GUARD_FUNCTION = "ops.scac_reference_monitor_guard()"


class SIEP18NormalizationError(RuntimeError):
    """The current catalog does not carry the one reviewed guard shape."""


def expected_guards(target: str) -> list[dict[str, str]]:
    # pg_get_triggerdef(..., true), which is also the immutable ownership
    # fingerprint representation, omits the default public schema but keeps
    # non-default schemas qualified.
    rendered_target = target.removeprefix("public.")
    return [
        {
            "name": ROW_TRIGGER_NAME,
            "enabled": "O",
            "definition": (
                f"CREATE TRIGGER {ROW_TRIGGER_NAME} BEFORE INSERT OR DELETE OR UPDATE "
                f"ON {rendered_target} FOR EACH ROW EXECUTE FUNCTION {GUARD_FUNCTION}"
            ),
        },
        {
            "name": TRUNCATE_TRIGGER_NAME,
            "enabled": "O",
            "definition": (
                f"CREATE TRIGGER {TRUNCATE_TRIGGER_NAME} BEFORE TRUNCATE ON {rendered_target} "
                f"FOR EACH STATEMENT EXECUTE FUNCTION {GUARD_FUNCTION}"
            ),
        },
    ]


def _is_guard_candidate(trigger: Any) -> bool:
    if not isinstance(trigger, dict):
        return False
    return trigger.get("name") in {ROW_TRIGGER_NAME, TRUNCATE_TRIGGER_NAME} or GUARD_FUNCTION in str(
        trigger.get("definition", "")
    )


def validate_siep18_guard_rows(rows: Iterable[dict[str, Any]]) -> dict[str, list[dict[str, str]]]:
    """Validate the exact live pg_trigger shape and return fingerprint records."""

    grouped: dict[str, dict[str, Any]] = {}
    for row in rows:
        target = str(row.get("target", ""))
        if not target:
            raise SIEP18NormalizationError("guard-surface target is malformed")
        group = grouped.setdefault(target, {"eligible": row.get("eligible"), "rows": []})
        if group["eligible"] is not row.get("eligible"):
            raise SIEP18NormalizationError(f"guard-surface eligibility drifted: {target}")
        if row.get("name") is not None:
            group["rows"].append(row)

    validated: dict[str, list[dict[str, str]]] = {}
    for target, group in grouped.items():
        candidates = group["rows"]
        if group["eligible"] is not True:
            if candidates:
                raise SIEP18NormalizationError(
                    f"ineligible fingerprint target carries a SIEP-18 guard: {target}"
                )
            continue
        expected = expected_guards(target)
        if [row.get("name") for row in candidates] != [ROW_TRIGGER_NAME, TRUNCATE_TRIGGER_NAME]:
            raise SIEP18NormalizationError(
                f"runtime-DML fingerprint target lacks exactly two named SIEP-18 guards: {target}"
            )
        for row, expected_record, expected_type in zip(candidates, expected, (31, 34), strict=True):
            if (
                row.get("record") != expected_record
                or row.get("function_oid_exact") is not True
                or row.get("tgtype") != expected_type
                or row.get("tgnargs") != 0
                or row.get("args_bytes") != 0
                or row.get("qual_absent") is not True
                or row.get("old_table_absent") is not True
                or row.get("new_table_absent") is not True
                or row.get("constraint_absent") is not True
                or row.get("deferrable") is not False
                or row.get("initially_deferred") is not False
            ):
                raise SIEP18NormalizationError(
                    f"runtime-DML fingerprint target has a nonexact SIEP-18 guard: {target}"
                )
        validated[target] = [row["record"] for row in candidates]
    return validated


def normalize_siep18_reference_monitor_guards(
    fingerprint: dict[str, Any], validated_guards: dict[str, list[dict[str, str]]]
) -> dict[str, Any]:
    """Match the exact enabled SIEP-18 guard generation in the baseline.

    Eligibility comes from the live runtime-DML ACL projection. The caller must
    still compare the returned value byte-for-byte with its pre-0450 baseline;
    normalization never excuses any other catalog, grant, or trigger drift.

    Historical baselines predate SIEP-18, so their validated pairs are removed
    from the current fingerprint. A refreshed schema snapshot already contains
    those pairs. When that snapshot is the disposable baseline, preserve the
    same exact pairs instead of manufacturing drift by deleting them from only
    the current side of the comparison.
    """

    normalized = deepcopy(fingerprint)
    tables = normalized.get("tables")
    if not isinstance(tables, dict):
        raise SIEP18NormalizationError("catalog fingerprint tables are malformed")
    missing_targets = set(validated_guards).difference(tables)
    if missing_targets:
        raise SIEP18NormalizationError(
            f"runtime-DML fingerprint target is absent: {sorted(missing_targets)!r}"
        )

    baseline_tables: dict[str, Any] = {}
    baseline_text = os.environ.get("CARR_OWNERSHIP_PRE_0450_FINGERPRINT", "")
    if baseline_text:
        try:
            baseline = json.loads(baseline_text)
            candidate_tables = baseline.get("tables")
        except (AttributeError, TypeError, ValueError) as exc:
            raise SIEP18NormalizationError("catalog fingerprint baseline is malformed") from exc
        if not isinstance(candidate_tables, dict):
            raise SIEP18NormalizationError("catalog fingerprint baseline tables are malformed")
        baseline_tables = candidate_tables

    for target, table in tables.items():
        if not isinstance(table, dict) or not isinstance(table.get("triggers"), list):
            raise SIEP18NormalizationError(f"catalog trigger projection is malformed: {target}")
        triggers = table["triggers"]
        expected = validated_guards.get(target, [])
        candidates = [trigger for trigger in triggers if _is_guard_candidate(trigger)]
        if candidates != expected:
            raise SIEP18NormalizationError(
                f"fingerprint guard records differ from the validated pg_trigger surface: {target}"
            )
        baseline_table = baseline_tables.get(target, {})
        if not isinstance(baseline_table, dict):
            raise SIEP18NormalizationError(f"baseline catalog table is malformed: {target}")
        baseline_triggers = baseline_table.get("triggers", [])
        if not isinstance(baseline_triggers, list):
            raise SIEP18NormalizationError(f"baseline catalog triggers are malformed: {target}")
        baseline_candidates = [trigger for trigger in baseline_triggers if _is_guard_candidate(trigger)]
        if baseline_candidates and baseline_candidates != expected:
            raise SIEP18NormalizationError(
                f"baseline fingerprint carries a nonexact SIEP-18 guard: {target}"
            )
        if expected and not baseline_candidates:
            table["triggers"] = [trigger for trigger in triggers if not _is_guard_candidate(trigger)]
    return normalized
