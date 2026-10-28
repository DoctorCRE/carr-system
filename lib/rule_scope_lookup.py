"""Store-backed selection for any hook event. Results are not delivery receipts.

Missing scope is a data defect. Selection uses stored structural conditions;
free-text moment descriptions do not invoke a classifier.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import time
from typing import Callable

from lib.rule_operations import structural_matches

REPO = Path(__file__).resolve().parent.parent
MATCH_KEYS = ("tools", "verbs", "bash_patterns", "path_globs")
# How long a session reuses one fetched catalog before it asks the store again.
# The re-read is the only way to see a changed corpus_digest (another session's
# or partner's rule edit), so this bounds how stale a long session's rules get.
CATALOG_SECONDS = 600


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


def _fetch_catalog(runner: Callable) -> dict:
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


def _catalog_path(session_id: str) -> Path:
    root = Path(os.environ.get("CARR_RULE_SCOPE_CACHE_DIR",
                               Path.home() / ".config/carr/claude-rule-delivery/scope-catalog"))
    return root / (hashlib.sha256(session_id.encode("utf-8")).hexdigest() + ".json")


def _session_catalog(session_id: str, runner: Callable, now: float) -> dict:
    """One catalog per session, re-read from the store every CATALOG_SECONDS.

    The hook fires on every tool call; the corpus changes only when a rule does.
    A re-read replaces the cached copy, so a changed corpus_digest reaches the
    session at the next re-read and an unchanged one changes nothing. A failed
    read is never cached and a corrupt file is simply re-read. The lock makes
    parallel tool calls in one session wait for one fetch instead of each
    starting its own.
    """
    path = _catalog_path(session_id)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with open(path.with_suffix(".lock"), "a", encoding="utf-8") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            cached = json.loads(path.read_text(encoding="utf-8"))
            if 0 <= now - cached["fetched_at"] < CATALOG_SECONDS and isinstance(cached["catalog"], dict):
                return cached["catalog"]
        except (OSError, ValueError, KeyError, TypeError):
            pass
        catalog = _fetch_catalog(runner)
        temp = path.with_suffix(".tmp")
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump({"fetched_at": now, "catalog": catalog}, handle)
        os.replace(temp, path)
        return catalog


def lookup(event: dict, *, runner: Callable = subprocess.run, session_id: str | None = None,
           now: float | None = None) -> dict:
    """Read current sponsor-scoped records and select for any hook event.

    With a session_id the catalog is reused across that session's events (see
    _session_catalog); without one every call reads the store. No credentials,
    runtime settings or delivery receipts are written.
    """
    if not session_id:
        return select_rules(_fetch_catalog(runner), event)
    try:
        catalog = _session_catalog(session_id, runner, time.time() if now is None else now)
    except OSError:
        catalog = _fetch_catalog(runner)  # an unwritable cache must not cost the rules
    return select_rules(catalog, event)
