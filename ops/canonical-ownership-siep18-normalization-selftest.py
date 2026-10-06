#!/usr/bin/env python3
"""Regression proof for the ownership fingerprint's narrow SIEP-18 allowance."""

from __future__ import annotations

from copy import deepcopy
import json
import os
from typing import Any

from canonical_ownership_siep18_normalization import (
    SIEP18NormalizationError,
    expected_guards,
    normalize_siep18_reference_monitor_guards,
    validate_siep18_guard_rows,
)


TARGET = "ops.work_request"
ROW = {
    "name": "scac_reference_monitor_guard_row",
    "enabled": "O",
    "definition": (
        "CREATE TRIGGER scac_reference_monitor_guard_row BEFORE INSERT OR DELETE OR UPDATE "
        "ON ops.work_request FOR EACH ROW EXECUTE FUNCTION ops.scac_reference_monitor_guard()"
    ),
}
TRUNCATE = {
    "name": "scac_reference_monitor_guard_truncate",
    "enabled": "O",
    "definition": (
        "CREATE TRIGGER scac_reference_monitor_guard_truncate BEFORE TRUNCATE ON ops.work_request "
        "FOR EACH STATEMENT EXECUTE FUNCTION ops.scac_reference_monitor_guard()"
    ),
}
BASELINE: dict[str, Any] = {
    "tables": {
        TARGET: {"acl": "baseline", "triggers": [{"name": "existing", "enabled": "O", "definition": "stable"}]},
        "public.actor": {"acl": "baseline", "triggers": []},
    },
    "functions": {"ops.example()": {"definition": "stable"}},
}


def candidate() -> dict[str, Any]:
    value = deepcopy(BASELINE)
    value["tables"][TARGET]["triggers"].extend([deepcopy(ROW), deepcopy(TRUNCATE)])
    return value


def metadata(record: dict, *, eligible: bool = True, tgtype: int | None = None) -> dict:
    return {
        "target": TARGET,
        "eligible": eligible,
        "name": record["name"],
        "record": deepcopy(record),
        "function_oid_exact": True,
        "tgtype": tgtype if tgtype is not None else (31 if record["name"].endswith("_row") else 34),
        "tgnargs": 0,
        "args_bytes": 0,
        "qual_absent": True,
        "old_table_absent": True,
        "new_table_absent": True,
        "constraint_absent": True,
        "deferrable": False,
        "initially_deferred": False,
    }


def live_rows() -> list[dict]:
    return [metadata(ROW), metadata(TRUNCATE)]


def must_refuse_rows(label: str, rows: list[dict]) -> None:
    try:
        validate_siep18_guard_rows(rows)
    except SIEP18NormalizationError:
        return
    raise AssertionError(f"{label} was validated")


validated = validate_siep18_guard_rows(live_rows())
assert normalize_siep18_reference_monitor_guards(candidate(), validated) == BASELINE
public_row, public_truncate = expected_guards("public.actor")
assert " ON actor " in public_row["definition"]
assert " ON actor " in public_truncate["definition"]
assert "public.actor" not in public_row["definition"]

must_refuse_rows("missing row guard", [metadata(TRUNCATE)])
must_refuse_rows("missing truncate guard", [metadata(ROW)])

renamed = live_rows()
renamed[0]["name"] = "renamed_guard"
renamed[0]["record"]["name"] = "renamed_guard"
must_refuse_rows("renamed guard", renamed)

must_refuse_rows("third guard", live_rows() + [metadata(TRUNCATE)])

disabled = live_rows()
disabled[0]["record"]["enabled"] = "D"
must_refuse_rows("disabled guard", disabled)

for label, field, value in [
    ("wrong function", "function_oid_exact", False),
    ("wrong events or level", "tgtype", 30),
    ("arguments", "tgnargs", 1),
    ("argument bytes", "args_bytes", 1),
    ("WHEN predicate", "qual_absent", False),
    ("transition table", "old_table_absent", False),
    ("new transition table", "new_table_absent", False),
    ("constraint trigger", "constraint_absent", False),
    ("deferrable trigger", "deferrable", True),
    ("initially deferred trigger", "initially_deferred", True),
]:
    rows = live_rows()
    rows[0][field] = value
    must_refuse_rows(label, rows)

nonexact_definition = live_rows()
nonexact_definition[1]["record"]["definition"] = nonexact_definition[1]["record"]["definition"].replace(
    "BEFORE TRUNCATE", "AFTER TRUNCATE"
)
must_refuse_rows("nonexact definition", nonexact_definition)

ineligible = live_rows()
for row in ineligible:
    row["eligible"] = False
must_refuse_rows("guard on ineligible target", ineligible)

other_drift = candidate()
other_drift["tables"][TARGET]["acl"] = "widened"
assert normalize_siep18_reference_monitor_guards(other_drift, validated) != BASELINE

other_trigger = candidate()
other_trigger["tables"][TARGET]["triggers"].insert(
    0, {"name": "unrelated_new_trigger", "enabled": "O", "definition": "visible drift"}
)
assert normalize_siep18_reference_monitor_guards(other_trigger, validated) != BASELINE

snapshot_baseline = candidate()
os.environ["CARR_OWNERSHIP_PRE_0450_FINGERPRINT"] = json.dumps(snapshot_baseline)
try:
    assert normalize_siep18_reference_monitor_guards(candidate(), validated) == snapshot_baseline
    malformed_baseline = candidate()
    malformed_baseline["tables"][TARGET]["triggers"][-1]["enabled"] = "D"
    os.environ["CARR_OWNERSHIP_PRE_0450_FINGERPRINT"] = json.dumps(malformed_baseline)
    try:
        normalize_siep18_reference_monitor_guards(candidate(), validated)
    except SIEP18NormalizationError:
        pass
    else:
        raise AssertionError("a nonexact snapshot-baseline guard was normalized")
finally:
    os.environ.pop("CARR_OWNERSHIP_PRE_0450_FINGERPRINT", None)

print("canonical ownership SIEP-18 normalization selftest — 23/23 passed")
