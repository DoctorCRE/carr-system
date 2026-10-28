"""Store-backed selection for any hook event. Results are not delivery receipts.

Missing scope is a data defect. Selection uses stored structural conditions;
free-text moment descriptions do not invoke a classifier.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess
from typing import Callable

from lib.rule_operations import structural_matches

REPO = Path(__file__).resolve().parent.parent
MATCH_KEYS = ("tools", "verbs", "bash_patterns", "path_globs")


def _scope_condition(scope: object) -> dict:
    if not isinstance(scope, dict):
        raise ValueError("missing_scope")
    condition: dict = {}
    for key in MATCH_KEYS:
        values = scope.get(key, [])
        if not isinstance(values, list) or not all(isinstance(v, str) and v.strip() for v in values):
            raise ValueError("invalid_" + key)
        condition[key] = values
    for pattern in condition["bash_patterns"]:
        try:
            re.compile(pattern)
        except re.error:
            raise ValueError("invalid_bash_patterns") from None
    moment = scope.get("moment", scope.get("applies_to", scope.get("context", "")))
    if not isinstance(moment, str):
        raise ValueError("invalid_moment")
    condition["moment"] = moment.strip()
    if not any(condition.values()):
        raise ValueError("missing_scope")
    if "read_only" in scope and not isinstance(scope["read_only"], bool):
        raise ValueError("invalid_read_only")
    condition["read_only"] = scope.get("read_only", False)
    return condition


def select_rules(catalog: dict, event: dict) -> dict:
    """Select structurally matched rules without model calls or local snapshots."""
    if catalog.get("ok") is not True or catalog.get("schema") != "rule-scope-catalog/v1":
        raise ValueError("invalid_scope_catalog")
    if (not isinstance(catalog.get("corpus_digest"), str)
            or not re.fullmatch(r"[0-9a-f]{64}", catalog["corpus_digest"])):
        raise ValueError("invalid_scope_digest")
    if not isinstance(event, dict) or not isinstance(event.get("tool_name", ""), str):
        raise ValueError("invalid_scope_event")
    identity = catalog.get("identity")
    if (not isinstance(identity, dict) or not identity.get("agent_principal_id")
            or not identity.get("runtime_principal") or "sponsoring_human_id" not in identity):
        raise ValueError("invalid_scope_identity")
    rows = catalog.get("rules")
    if not isinstance(rows, list):
        raise ValueError("invalid_scope_catalog")
    selected, defects = [], []
    ids = set()
    for row in rows:
        if (not isinstance(row, dict) or not isinstance(row.get("id"), str)
                or not re.fullmatch(r"[0-9a-f]{8}(?:-[0-9a-f-]{27})?", row["id"])
                or not isinstance(row.get("statement"), str) or not row["statement"].strip()
                or "personal_to" not in row or row["id"][:8] in ids):
            raise ValueError("invalid_scope_rule")
        ids.add(row["id"][:8])
        if row["personal_to"] is not None and row["personal_to"] != identity["sponsoring_human_id"]:
            raise ValueError("scope_sponsor_mismatch")
        try:
            condition = _scope_condition(row.get("scope"))
        except ValueError as exc:
            defects.append({"rule_id": row["id"], "reason": str(exc)})
            continue
        if structural_matches(condition, event.get("tool_name", ""), event.get("tool_input", {})):
            selected.append(row)
    return {"schema": "rule-scope-selection/v1", "identity": identity,
            "corpus_digest": catalog["corpus_digest"],
            "rules": sorted(selected, key=lambda r: r["id"]),
            "data_defects": defects, "unresolved": [],
            "selection_mode": "deterministic",
            "models": [], "complete": not defects}


def fetch_catalog(runner: Callable = subprocess.run) -> dict:
    """Read the sponsor-scoped rule catalog from the store. Not yet validated: see select_rules."""
    env = {k: v for k, v in os.environ.items()
           if k not in {"DATABASE_URL", "CARR_BREAK_GLASS", "CARR_BREAK_GLASS_REASON", "CARR_MCP_CLIENT_PROFILE"}}
    try:
        response = runner([str(REPO / "run.sh"), "call", "standing-context", '{"detail":"scopes"}'],
                          cwd=str(REPO), env=env, capture_output=True, text=True, timeout=15, check=False)
        if response.returncode != 0:
            raise RuntimeError("scope_catalog_unavailable")
        return json.loads(response.stdout)
    except Exception:
        raise RuntimeError("scope_catalog_unavailable") from None


def lookup(event: dict, *, runner: Callable = subprocess.run) -> dict:
    """Read current sponsor-scoped records once and select for any hook event.

    No credentials, runtime settings, delivery receipts, or local snapshots are written.
    """
    return select_rules(fetch_catalog(runner), event)
