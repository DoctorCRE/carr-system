"""Exact receipt contract shared by pre-use selection and Stop telemetry."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from lib.rule_delivery_shadow import file_sha256, source_sha256
from lib.rule_routes import ROUTE_RECEIPT_SCHEMA, validate_route_receipt, within_cap


PACK = "scheduled-automation"
RECEIPT_SCHEMA = "rule-delivery-preuse-reselection/v1"
RECEIPT_KEYS = frozenset({
    "schema", "receipt_id", "client", "session_id", "turn_id", "tool_use_id",
    "tool_name", "tool_input_sha256", "pack", "map_digest", "source_digest",
    "identity", "rule_ids", "rules", "rule_delivery",
})
IDENTITY_KEYS = frozenset({
    "agent_principal_id", "runtime_principal", "sponsoring_human_id",
})
DELIVERY_KEYS = frozenset({"mode", "declared_packs", "packs_not_found"})
RULE_KEYS = frozenset({"id", "statement"})
SANCTIONED_LOCAL_SPONSORS = {
    "joe-local": "joe",
    "dell-local": "dell",
}

# WR-000019 slice S9 — the generalized, multi-shape sibling of the schema
# above. The scheduled-automation rail above stays byte-for-byte as it was:
# one pack, one call shape, proven in production. This second schema is for
# EVERY OTHER call shape the compiled trigger table
# (ops/config/rule-jit-triggers.v1.json) recognizes — an MCP verb, a Bash
# command family, a file-path write, or the general content fallback — where
# more than one trigger can match a single call and more than one pack can be
# implicated at once, so the receipt carries LISTS rather than the single
# `pack` string the original schema fixes.
GENERALIZED_RECEIPT_SCHEMA = "rule-jit-trigger-delivery/v1"
GENERALIZED_RECEIPT_KEYS = frozenset({
    "schema", "receipt_id", "client", "session_id", "turn_id", "tool_use_id",
    "tool_name", "tool_input_sha256", "trigger_ids", "packs", "triggers_digest",
    "map_digest", "source_digest", "identity", "rule_ids", "rules", "rule_delivery",
})
TRIGGER_TABLE_RELATIVE = "ops/config/rule-jit-triggers.v1.json"
TRIGGER_KINDS = frozenset({"verb", "bash_family", "path_pattern", "content_regex", "prompt_regex"})

# The partner-message sibling. Unlike the two PreToolUse receipts, this one is
# selected by semantic judgment rather than by a compiled trigger row. Its
# candidates must still resolve through the reviewed load-layer map and the
# authenticated standing-context door before any rule text is injected.
SEMANTIC_RECEIPT_SCHEMA = "rule-jev-message-delivery/v3"
SEMANTIC_RECEIPT_KEYS = frozenset({
    "schema", "receipt_id", "client", "session_id", "turn_id",
    "prompt_sha256", "packs", "corpus_digest", "selector_digest",
    "map_digest", "source_digest", "identity", "rule_ids", "rules",
    "probabilities", "model_provenance", "rule_delivery",
})
POSTWRITE_RECEIPT_SCHEMA = "jev-post-write-review/v2"
POSTWRITE_RECEIPT_KEYS = frozenset({
    "schema", "receipt_id", "client", "session_id", "turn_id", "tool_use_id",
    "tool_name", "tool_input_sha256", "configuration_digest",
    "reviewer_digest", "status", "paths", "findings", "models", "reason",
    "instruction",
})
CORPUS_RELATIVE = "ops/config/rule-selection-corpus.v1.json"
SELECTOR_SOURCE_PATHS = (
    "ops/jev_judge.py",
    "ops/jev_semantic.py",
    "ops/typesafe_client.py",
    # The verdict cache and envelope test decide what is reused and skipped,
    # so a change to either must invalidate old receipts too.
    "ops/jev_verdict_cache.py",
    "ops/machine_envelope.py",
    # Message-time selection is now a match against Jev's compile-time
    # judgments, so the matcher, the compiler and both compiled files are
    # part of what selected a delivered rule.
    "ops/rule_trigger_delivery.py",
    "ops/rule_trigger_compile.py",
    "ops/config/rule-jev-triggers.v1.json",
    "ops/config/rule-jit-triggers.v1.json",
)


def postwrite_reviewer_digest(repo: Path) -> str:
    return digest({
        relative: file_sha256(repo / relative)
        for relative in (
            "hooks/lint-gate.py", "ops/jev_code_review.py", "ops/jev_semantic.py",
            "ops/typesafe_client.py",
        )
    })


def validate_postwrite_receipt(row: object, *, repo: Path) -> bool:
    if not isinstance(row, dict) or set(row) != POSTWRITE_RECEIPT_KEYS:
        return False
    if (row.get("schema") != POSTWRITE_RECEIPT_SCHEMA
            or row.get("client") not in {"claude", "codex"}
            or row.get("status") not in {"reviewed", "skipped", "unavailable"}):
        return False
    if not all(_nonempty(row.get(key)) for key in (
            "receipt_id", "session_id", "tool_use_id", "tool_name",
            "tool_input_sha256", "configuration_digest", "reviewer_digest")):
        return False
    turn_id = row.get("turn_id")
    if ((row["client"] == "codex" and not _nonempty(turn_id))
            or (row["client"] == "claude" and turn_id is not None)):
        return False
    expected_config = digest({
        relative: file_sha256(repo / relative)
        for relative in ("ops/config/hooks.json", "ops/config/codex-hooks.json")
    })
    if (row["configuration_digest"] != expected_config
            or row["reviewer_digest"] != postwrite_reviewer_digest(repo)):
        return False
    if (not isinstance(row.get("paths"), list) or
            any(not isinstance(path, dict) for path in row["paths"]) or
            not isinstance(row.get("findings"), list)):
        return False
    if (not isinstance(row.get("models"), list)
            or any(not _nonempty(model) for model in row["models"])):
        return False
    if row["status"] == "reviewed":
        if (not row["models"] or row.get("reason") is not None
                or row.get("instruction") is not None
                or not any(isinstance(path, dict) and path.get("status") == "jev_reviewed"
                           for path in row["paths"])):
            return False
    elif row["status"] == "skipped":
        if (row["models"] or row["findings"] or row.get("reason") not in
                {"no_jev_candidate", "no_supported_code_paths", "outside_repo"}
                or row.get("instruction") is not None
                or any(isinstance(path, dict) and path.get("status") == "jev_reviewed"
                       for path in row["paths"])):
            return False
        if (row["reason"] == "no_supported_code_paths" and
                any(path.get("status") != "not_reviewed" or
                    path.get("reason") != "unsupported_extension"
                    for path in row["paths"])):
            return False
        if (row["reason"] == "outside_repo" and
                any(path.get("status") != "not_reviewed" or
                    path.get("reason") != "outside_repo"
                    for path in row["paths"])):
            return False
    elif not _nonempty(row.get("reason")) or not _nonempty(row.get("instruction")):
        return False
    return row["receipt_id"] == receipt_id(row)


def semantic_selector_digest(repo: Path) -> str:
    """Bind a semantic receipt to every implementation file that judged it.

    An absent file digests as "absent" rather than raising: a missing compiled
    trigger file is the fail-open case (delivery falls back to judging), and
    its receipt must still be issuable and verifiable."""
    return digest({relative: (file_sha256(repo / relative)
                              if (repo / relative).is_file() else "absent")
                   for relative in SELECTOR_SOURCE_PATHS})


def load_trigger_table(repo: Path) -> list[dict]:
    """The compiled trigger table, never hand-derived a second way here.

    Raises on anything structurally wrong rather than silently degrading —
    the caller (the hook's generalized rail) treats any exception as a
    redacted, nonblocking failure the same way the scheduled rail already
    does for a selector failure.
    """
    path = repo / TRIGGER_TABLE_RELATIVE
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("schema") != "rule-jit-triggers/v1":
        raise ValueError("trigger table has the wrong schema")
    rows = data.get("triggers")
    if not isinstance(rows, list) or not rows:
        raise ValueError("trigger table has no triggers")
    for row in rows:
        if (not isinstance(row, dict)
                or row.get("kind") not in TRIGGER_KINDS
                or not isinstance(row.get("pattern"), str) or not row["pattern"]
                or not isinstance(row.get("rule_ids"), list) or not row["rule_ids"]
                or not isinstance(row.get("trigger_id"), str) or not row["trigger_id"]
                or not isinstance(row.get("packs"), list)):
            raise ValueError("trigger table row is malformed")
    return rows


def merge_trigger_delivery(rows: list[dict]) -> tuple[list[str], list[str], list[str]]:
    """(trigger_ids, packs, rule_ids) — the union across every matched row.

    Over-delivery is the stated bias when more than one trigger matches a
    single call: nothing here re-applies the per-trigger cap, because the cap
    already lives in the compiler (rule 015183f5's own home: lean per
    trigger, not lean in aggregate across a rare multi-match).
    """
    trigger_ids = sorted({row["trigger_id"] for row in rows})
    packs = sorted({p for row in rows for p in row["packs"]})
    rule_ids = sorted({rid for row in rows for rid in row["rule_ids"]})
    return trigger_ids, packs, rule_ids


def semantic_delivery(repo: Path, rule_ids: list[str]) -> tuple[list[str], list[str]]:
    """Return reviewed pack members and packs for Jev-selected rule ids.

    Layer-zero rules are already present at boot and are intentionally omitted.
    Unknown, malformed, or non-pack rows are omitted rather than promoted by a
    model answer; the reviewed map remains the deterministic authority.
    """
    data = json.loads((repo / "ops/config/rule-enforcement-map.json").read_text(
        encoding="utf-8"))
    layers = data.get("rule_load_layers")
    if not isinstance(layers, dict):
        raise ValueError("reviewed map has no rule_load_layers object")
    kept = []
    packs = set()
    for short in sorted(set(rule_ids)):
        row = layers.get(short)
        if (not isinstance(short, str) or len(short) != 8
                or not isinstance(row, dict) or row.get("load_layer") != "pack"
                or not isinstance(row.get("packs"), list) or not row["packs"]
                or any(not _nonempty(pack) for pack in row["packs"])):
            continue
        kept.append(short)
        packs.update(row["packs"])
    return kept, sorted(packs)


def validate_semantic_receipt(row: object, *, repo: Path) -> bool:
    """Validate one authenticated Jev-at-message-boundary delivery receipt."""
    if not isinstance(row, dict) or set(row) != SEMANTIC_RECEIPT_KEYS:
        return False
    if row.get("schema") != SEMANTIC_RECEIPT_SCHEMA:
        return False
    if row.get("client") not in {"claude", "codex"}:
        return False
    if not all(_nonempty(row.get(key)) for key in (
            "receipt_id", "session_id", "prompt_sha256", "corpus_digest",
            "selector_digest", "map_digest", "source_digest")):
        return False
    turn_id = row.get("turn_id")
    if ((row["client"] == "codex" and not _nonempty(turn_id))
            or (row["client"] == "claude" and turn_id is not None)):
        return False
    identity = row.get("identity")
    if (not isinstance(identity, dict) or set(identity) != IDENTITY_KEYS
            or not valid_local_identity(identity)):
        return False
    rule_ids = row.get("rule_ids")
    packs = row.get("packs")
    if (not isinstance(rule_ids, list) or not rule_ids
            or rule_ids != sorted(set(rule_ids))
            or not isinstance(packs, list) or not packs
            or packs != sorted(set(packs))):
        return False
    try:
        expected_ids, expected_packs = semantic_delivery(repo, rule_ids)
    except (OSError, ValueError, json.JSONDecodeError):
        return False
    if rule_ids != expected_ids or packs != expected_packs:
        return False
    rules = row.get("rules")
    if (not isinstance(rules, list) or len(rules) != len(rule_ids)
            or any(not isinstance(item, dict) or set(item) != RULE_KEYS
                   or not _nonempty(item.get("id"))
                   or not _nonempty(item.get("statement")) for item in rules)
            or [item["id"] for item in rules] != rule_ids):
        return False
    probabilities = row.get("probabilities")
    if (not isinstance(probabilities, dict)
            or sorted(probabilities) != rule_ids):
        return False
    try:
        if any(not 0.0 <= float(value) <= 1.0
               for value in probabilities.values()):
            return False
    except (TypeError, ValueError):
        return False
    model_provenance = row.get("model_provenance")
    if (not isinstance(model_provenance, dict)
            or sorted(model_provenance) != rule_ids):
        return False
    for route in model_provenance.values():
        if (not isinstance(route, dict)
                or set(route) != {"ranking_model", "binding_model"}
                or not _nonempty(route.get("binding_model"))
                or (route.get("ranking_model") is not None
                    and not _nonempty(route.get("ranking_model")))):
            return False
    delivery = row.get("rule_delivery")
    if (not isinstance(delivery, dict) or set(delivery) != DELIVERY_KEYS
            or delivery.get("mode") not in {"shadow", "enforced"}
            or sorted(delivery.get("declared_packs") or []) != packs
            or delivery.get("packs_not_found") != []):
        return False
    if row["corpus_digest"] != file_sha256(repo / CORPUS_RELATIVE):
        return False
    if row["selector_digest"] != semantic_selector_digest(repo):
        return False
    if row["map_digest"] != file_sha256(repo / "ops/config/rule-enforcement-map.json"):
        return False
    if row["source_digest"] != source_sha256(repo):
        return False
    return row["receipt_id"] == receipt_id(row)


def validate_generalized_receipt(row: object, *, repo: Path) -> bool:
    """The multi-shape sibling of validate_receipt, for GENERALIZED_RECEIPT_SCHEMA."""
    if not isinstance(row, dict) or set(row) != GENERALIZED_RECEIPT_KEYS:
        return False
    if row.get("schema") != GENERALIZED_RECEIPT_SCHEMA:
        return False
    if row.get("client") not in {"claude", "codex"}:
        return False
    if not all(_nonempty(row.get(key)) for key in (
            "receipt_id", "session_id", "tool_use_id", "tool_name",
            "tool_input_sha256", "triggers_digest", "map_digest", "source_digest")):
        return False
    turn_id = row.get("turn_id")
    if row["client"] == "codex":
        if not _nonempty(turn_id):
            return False
    elif turn_id is not None:
        return False
    identity = row.get("identity")
    if (not isinstance(identity, dict) or set(identity) != IDENTITY_KEYS
            or not valid_local_identity(identity)):
        return False
    trigger_ids = row.get("trigger_ids")
    packs = row.get("packs")
    rule_ids = row.get("rule_ids")
    if (not isinstance(trigger_ids, list) or not trigger_ids
            or trigger_ids != sorted(set(trigger_ids))
            or not isinstance(packs, list) or packs != sorted(set(packs))
            or not isinstance(rule_ids, list) or not rule_ids
            or rule_ids != sorted(set(rule_ids))):
        return False
    try:
        table = load_trigger_table(repo)
    except (OSError, ValueError, json.JSONDecodeError):
        return False
    by_id = {r["trigger_id"]: r for r in table}
    if any(tid not in by_id for tid in trigger_ids):
        return False
    expected_trigger_ids, expected_packs, expected_rule_ids = merge_trigger_delivery(
        [by_id[tid] for tid in trigger_ids])
    if (trigger_ids != expected_trigger_ids or packs != expected_packs
            or rule_ids != expected_rule_ids):
        return False
    rules = row.get("rules")
    if (not isinstance(rules, list) or len(rules) != len(rule_ids)
            or any(not isinstance(item, dict) or set(item) != RULE_KEYS
                   or not _nonempty(item.get("id"))
                   or not _nonempty(item.get("statement")) for item in rules)
            or [item["id"] for item in rules] != rule_ids):
        return False
    delivery = row.get("rule_delivery")
    if (not isinstance(delivery, dict) or set(delivery) != DELIVERY_KEYS
            or delivery.get("mode") not in {"shadow", "enforced"}
            or sorted(delivery.get("declared_packs") or []) != packs
            or delivery.get("packs_not_found") != []):
        return False
    triggers_path = repo / TRIGGER_TABLE_RELATIVE
    if row["triggers_digest"] != file_sha256(triggers_path):
        return False
    map_path = repo / "ops/config/rule-enforcement-map.json"
    if row["map_digest"] != file_sha256(map_path):
        return False
    if row["source_digest"] != source_sha256(repo):
        return False
    return row["receipt_id"] == receipt_id(row)


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def scheduled_rule_ids(repo: Path) -> list[str]:
    """Derive membership from the current reviewed map, never a typed list."""
    path = repo / "ops/config/rule-enforcement-map.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    layers = data.get("rule_load_layers")
    if not isinstance(layers, dict):
        raise ValueError("reviewed map has no rule_load_layers object")
    found = sorted(
        short for short, row in layers.items()
        if isinstance(short, str) and isinstance(row, dict)
        and isinstance(row.get("packs"), list) and PACK in row["packs"]
    )
    if not found or any(len(short) != 8 for short in found):
        raise ValueError("reviewed map has no valid scheduled-automation members")
    return found


def receipt_id(row: dict) -> str:
    return digest({key: value for key, value in row.items() if key != "receipt_id"})


def _nonempty(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def valid_local_identity(identity: object) -> bool:
    """Bind the local verb door to its exact server-owned sponsor mapping."""
    if not isinstance(identity, dict):
        return False
    agent = identity.get("agent_principal_id")
    return (isinstance(agent, str)
            and agent in SANCTIONED_LOCAL_SPONSORS
            and identity.get("runtime_principal") == agent
            and identity.get("sponsoring_human_id") == SANCTIONED_LOCAL_SPONSORS[agent])


def validate_receipt(row: object, *, repo: Path) -> bool:
    if not isinstance(row, dict) or set(row) != RECEIPT_KEYS:
        return False
    if row.get("schema") != RECEIPT_SCHEMA or row.get("pack") != PACK:
        return False
    if row.get("client") not in {"claude", "codex"}:
        return False
    if not all(_nonempty(row.get(key)) for key in (
            "receipt_id", "session_id", "tool_use_id", "tool_name",
            "tool_input_sha256", "map_digest", "source_digest")):
        return False
    turn_id = row.get("turn_id")
    if row["client"] == "codex":
        if not _nonempty(turn_id):
            return False
    elif turn_id is not None:
        return False
    if ((row["client"] == "claude" and row["tool_name"] != "Bash")
            or (row["client"] == "codex"
                and row["tool_name"] not in {"Bash", "functions.exec"})):
        return False
    identity = row.get("identity")
    if (not isinstance(identity, dict) or set(identity) != IDENTITY_KEYS
            or not valid_local_identity(identity)):
        return False
    expected_ids = scheduled_rule_ids(repo)
    if row.get("rule_ids") != expected_ids:
        return False
    rules = row.get("rules")
    if (not isinstance(rules, list) or len(rules) != len(expected_ids)
            or any(not isinstance(item, dict) or set(item) != RULE_KEYS
                   or not _nonempty(item.get("id"))
                   or not _nonempty(item.get("statement")) for item in rules)
            or [item["id"] for item in rules] != expected_ids):
        return False
    delivery = row.get("rule_delivery")
    if (not isinstance(delivery, dict) or set(delivery) != DELIVERY_KEYS
            or delivery.get("mode") not in {"shadow", "enforced"}
            or delivery.get("declared_packs") != [PACK]
            or delivery.get("packs_not_found") != []):
        return False
    map_path = repo / "ops/config/rule-enforcement-map.json"
    if row["map_digest"] != file_sha256(map_path):
        return False
    if row["source_digest"] != source_sha256(repo):
        return False
    return row["receipt_id"] == receipt_id(row)


def _json_text(value: object) -> dict | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None


def receipt_from_envelope(record: object) -> dict | None:
    """Accept only platform-owned Claude/Codex context envelopes."""
    if not isinstance(record, dict):
        return None
    attachment = record.get("attachment")
    if (record.get("type") == "attachment" and isinstance(attachment, dict)
            and attachment.get("type") == "hook_additional_context"
            and isinstance(attachment.get("content"), list)
            and len(attachment["content"]) == 1):
        row = _json_text(attachment["content"][0])
        if (row and row.get("client") == "claude"
                and isinstance(row.get("schema"), str)
                and row.get("schema") in {RECEIPT_SCHEMA, ROUTE_RECEIPT_SCHEMA}
                and attachment.get("hookEvent") == "PreToolUse"
                and attachment.get("hookName") == f"PreToolUse:{row.get('tool_name')}"
                and attachment.get("toolUseID") == row.get("tool_use_id")
                and record.get("sessionId") == row.get("session_id")):
            return row
        if (row and row.get("client") == "claude"
                and row.get("schema") == SEMANTIC_RECEIPT_SCHEMA
                and attachment.get("hookEvent") == "UserPromptSubmit"
                and attachment.get("hookName") == "UserPromptSubmit"
                and "toolUseID" not in attachment
                and record.get("sessionId") == row.get("session_id")):
            return row
        if (row and row.get("client") == "claude"
                and row.get("schema") == POSTWRITE_RECEIPT_SCHEMA
                and attachment.get("hookEvent") == "PostToolUse"
                and attachment.get("hookName") == f"PostToolUse:{row.get('tool_name')}"
                and attachment.get("toolUseID") == row.get("tool_use_id")
                and record.get("sessionId") == row.get("session_id")):
            return row
        return None

    payload = record.get("payload")
    if (record.get("type") == "response_item" and isinstance(payload, dict)
            and payload.get("type") == "message" and payload.get("role") == "developer"):
        content = payload.get("content")
        if (not isinstance(content, list) or len(content) != 1
                or not isinstance(content[0], dict)
                or set(content[0]) != {"type", "text"}
                or content[0].get("type") != "input_text"):
            return None
        row = _json_text(content[0].get("text"))
        metadata = payload.get("internal_chat_message_metadata_passthrough")
        if (row and row.get("client") == "codex" and isinstance(metadata, dict)
                and metadata.get("turn_id") == row.get("turn_id")):
            return row
    return None


def tool_calls(record: object):
    if not isinstance(record, dict):
        return
    message = record.get("message")
    if (record.get("type") == "assistant" and isinstance(message, dict)
            and message.get("role") == "assistant"):
        for block in message.get("content", []) if isinstance(message.get("content"), list) else []:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                yield (block.get("id"), block.get("name"), block.get("input"),
                       record.get("sessionId"))
    payload = record.get("payload")
    record_type = record.get("type")
    payload_type = payload.get("type") if isinstance(payload, dict) else None
    structured_codex_call = (
        record_type == "response_item"
        and payload_type in {"function_call", "custom_tool_call"}
    ) or (record_type == "event_msg" and payload_type == "custom_tool_call")
    if isinstance(payload, dict) and structured_codex_call:
        raw = payload.get("arguments", payload.get("input"))
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except (TypeError, ValueError):
                raw = None
        name = payload.get("name")
        if payload_type == "custom_tool_call" and name in {"exec", "exec_command"}:
            name = "functions.exec"
        metadata = payload.get("internal_chat_message_metadata_passthrough")
        turn_id = metadata.get("turn_id") if isinstance(metadata, dict) else None
        yield (payload.get("call_id"), name, raw, turn_id)


def has_background_tool_call(record: object) -> bool:
    """Recognize exact Claude and Codex structured background invocations."""
    return any(
        name in {"Bash", "functions.exec"}
        and isinstance(tool_input, dict)
        and tool_input.get("run_in_background") is True
        for _tool_id, name, tool_input, _session_id in tool_calls(record))


def matched_tool_call(row: dict, prior_records: list[dict]) -> bool:
    matches = []
    for record in prior_records:
        for tool_id, name, tool_input, context_id in tool_calls(record):
            if tool_id == row["tool_use_id"]:
                matches.append((name, tool_input, context_id))
    if len(matches) != 1:
        return False
    name, tool_input, context_id = matches[0]
    exact_context = (
        context_id == row["session_id"] if row["client"] == "claude"
        else context_id == row["turn_id"]
    )
    return (name == row["tool_name"]
            and isinstance(tool_input, dict)
            and tool_input.get("run_in_background") is True
            and digest(tool_input) == row["tool_input_sha256"]
            and exact_context)


def _claude_user_text(record: object) -> str | None:
    if not isinstance(record, dict) or record.get("type") not in {"user", "human"}:
        return None
    message = record.get("message")
    if not isinstance(message, dict) or message.get("role") not in {"user", "human"}:
        return None
    content = message.get("content")
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return None
    pieces = []
    for item in content:
        if (not isinstance(item, dict) or item.get("type") != "text"
                or not isinstance(item.get("text"), str)):
            return None
        pieces.append(item["text"])
    return "".join(pieces)


def matched_prompt(row: dict, prior_records: list[dict]) -> bool:
    """Bind a Claude message receipt to its immediately preceding user turn.

    Codex context is already turn-bound by platform metadata in
    receipt_from_envelope(). Claude supplies a session id instead, so its prompt
    digest must match the latest genuine user record in that same session.
    """
    if row.get("client") == "codex":
        return True
    for record in reversed(prior_records):
        text = _claude_user_text(record)
        if text is None:
            continue
        return (record.get("sessionId") == row.get("session_id")
                and digest(text) == row.get("prompt_sha256"))
    return False


def preuse_delivery(record: dict, prior_records: list[dict], *, repo: Path):
    row = receipt_from_envelope(record)
    if row is None:
        return None
    if row.get("schema") == SEMANTIC_RECEIPT_SCHEMA:
        if (not validate_semantic_receipt(row, repo=repo)
                or not matched_prompt(row, prior_records)):
            return None
        delivery = row["rule_delivery"]
        return delivery["mode"], list(row["packs"]), []
    if row.get("schema") == ROUTE_RECEIPT_SCHEMA:
        if (not validate_route_receipt(row, repo=repo)
                or not within_cap(canonical(row).decode("utf-8"))
                or not matched_tool_call(row, prior_records)
                or PACK not in row["packs"]
                or not set(scheduled_rule_ids(repo)) <= {r["id"] for r in row["rules"]}):
            return None
        return row["rule_delivery"]["mode"], [PACK], []
    if (not validate_receipt(row, repo=repo)
            or not matched_tool_call(row, prior_records)):
        return None
    delivery = row["rule_delivery"]
    return delivery["mode"], [PACK], []


def contains_receipt_marker(value: object) -> bool:
    if isinstance(value, dict):
        return (isinstance(value.get("schema"), str)
                and value.get("schema") in {RECEIPT_SCHEMA, ROUTE_RECEIPT_SCHEMA}
                or value.get("schema") == SEMANTIC_RECEIPT_SCHEMA
                or value.get("schema") == POSTWRITE_RECEIPT_SCHEMA
                or any(contains_receipt_marker(item) for item in value.values()))
    if isinstance(value, list):
        return any(contains_receipt_marker(item) for item in value)
    return (isinstance(value, str)
            and (RECEIPT_SCHEMA in value or ROUTE_RECEIPT_SCHEMA in value or SEMANTIC_RECEIPT_SCHEMA in value or POSTWRITE_RECEIPT_SCHEMA in value))
