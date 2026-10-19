"""Authenticated event receipts for the single store-backed rule lookup."""
from __future__ import annotations
import hashlib
import json
import re
from pathlib import Path
from typing import Any
from lib.rule_routes import within_cap

LOOKUP_RECEIPT_SCHEMA = "rule-lookup-delivery/v1"
LOOKUP_INSTRUCTION = "Read every rule in rules before acting. Fetch the full text of every overflow id with standing-context rule_ids before acting. Selection gaps are advisory and do not deny this action; incomplete selection is not evidence of full rule coverage."
IDENTITY_KEYS = frozenset({"agent_principal_id", "runtime_principal", "sponsoring_human_id"})
SANCTIONED_LOCAL_SPONSORS = {"joe-local": "joe", "dell-local": "dell"}
DELIVERY_SOURCE_PATHS = (
    "hooks/rule-pack-preuse-reselection.py", "lib/rule_delivery_preuse.py",
    "lib/rule_scope_lookup.py", "lib/rule_operations.py", "lib/rule_routes.py",
    "lib/rule_boot_gate.py", "lib/hook_runtime.py", "ops/jev_verdict_cache.py",
    "ops/jev_semantic.py", "ops/typesafe_client.py", "ops/config/jev-call-sites.v1.json",
    "ops/config/hooks.json", "ops/config/codex-hooks.json",
)
LOOKUP_RECEIPT_KEYS = frozenset({"schema", "client", "session_id", "turn_id", "tool_use_id",
    "tool_name", "tool_input_sha256", "task_sha256", "event_name", "corpus_digest",
    "source_digest", "identity", "rule_ids", "rules", "overflow", "data_defects",
    "unresolved", "models", "complete", "instruction", "receipt_id"})
# Optional receipt field: at most three rule cards, each with its acknowledgement.
CARD_KEYS = frozenset({"id", "title", "wording", "acknowledgement"})
POSTWRITE_RECEIPT_SCHEMA = "jev-post-write-review/v2"
POSTWRITE_RECEIPT_KEYS = frozenset({
    "schema", "receipt_id", "client", "session_id", "turn_id", "tool_use_id",
    "tool_name", "tool_input_sha256", "configuration_digest",
    "reviewer_digest", "status", "paths", "findings", "models", "reason",
    "instruction",
})

def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_sha256(repo: Path) -> str:
    hasher = hashlib.sha256()
    for relative in DELIVERY_SOURCE_PATHS:
        path = repo / relative
        if not path.is_file():
            raise FileNotFoundError(f"delivery source identity path is absent: {relative}")
        payload = path.read_bytes()
        hasher.update(relative.encode("utf-8") + b"\0")
        hasher.update(str(len(payload)).encode("ascii") + b"\0")
        hasher.update(payload)
    return hasher.hexdigest()


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


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


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


def _json_text(value: object) -> dict | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None


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


def validate_lookup_receipt(row: object, *, repo: Path) -> bool:
    if not isinstance(row, dict):
        return False
    keys = set(row) - {'cards', 'unresolved_reasons'}
    if keys not in (LOOKUP_RECEIPT_KEYS, LOOKUP_RECEIPT_KEYS - {'unresolved'} | {'unresolved_count'}):
        return False
    if 'unresolved' in row:
        if not isinstance(row['unresolved'], list):
            return False
        unresolved_count = len(row['unresolved'])
    else:
        unresolved_count = row['unresolved_count']
        if type(unresolved_count) is not int or unresolved_count < 0:
            return False
    if 'unresolved_reasons' in row:
        counts = row['unresolved_reasons']
        if ('unresolved_count' not in row or not isinstance(counts, dict)
                or any(not _nonempty(reason) or type(count) is not int or count <= 0
                       for reason, count in counts.items())
                or sum(counts.values()) != unresolved_count):
            return False
    if 'cards' in row and (not isinstance(row['cards'], list) or not row['cards'] or len(row['cards']) > 3
                           or any(not isinstance(c, dict) or set(c) != CARD_KEYS
                                  or not all(_nonempty(c[k]) for k in CARD_KEYS) for c in row['cards'])):
        return False
    if (row['schema'] != LOOKUP_RECEIPT_SCHEMA or row['client'] not in {'claude', 'codex'}
            or row['event_name'] not in {'PreToolUse', 'UserPromptSubmit', 'Stop'}
            or not _nonempty(row['session_id']) or not valid_local_identity(row['identity'])
            or type(row['complete']) is not bool or not isinstance(row['models'], list)
            or any(model != 'jev-1.13.0' for model in row['models'])
            or row['instruction'] != LOOKUP_INSTRUCTION):
        return False
    if ((row['client'] == 'codex' and not _nonempty(row['turn_id']))
            or (row['client'] == 'claude' and row['turn_id'] is not None)):
        return False
    if row['event_name'] == 'PreToolUse' and not all(_nonempty(row[k]) for k in ('tool_use_id', 'tool_name')):
        return False
    if not all(isinstance(row[k], str) and re.fullmatch('[0-9a-f]{64}', row[k])
               for k in ('tool_input_sha256', 'task_sha256', 'corpus_digest', 'source_digest')):
        return False
    ids, rules, overflow = row['rule_ids'], row['rules'], row['overflow']
    if (not isinstance(ids, list) or not all(isinstance(i, str) for i in ids)
            or ids != sorted(set(ids)) or not isinstance(rules, list) or not isinstance(overflow, list)
            or not isinstance(row['data_defects'], list)):
        return False
    if any(not isinstance(r, dict) or not _nonempty(r.get('id')) or not _nonempty(r.get('statement'))
           or r.get('personal_to') not in (None, row['identity']['sponsoring_human_id']) for r in rules):
        return False
    if any(not isinstance(r, dict) or set(r) != {'id', 'summary'} or not isinstance(r['summary'], str)
           or not isinstance(r['id'], str) for r in overflow):
        return False
    parts = [r['id'] for r in rules] + [r['id'] for r in overflow]
    if sorted(parts) != ids or len(parts) != len(set(parts)):
        return False
    return (row['complete'] == (not row['data_defects'] and not unresolved_count)
            and row['source_digest'] == source_sha256(repo)
            and within_cap(canonical(row).decode()) and row['receipt_id'] == receipt_id(row))


def receipt_from_envelope(record: object) -> dict | None:
    if not isinstance(record, dict):
        return None
    attachment = record.get('attachment')
    if record.get('type') == 'attachment' and isinstance(attachment, dict):
        content = attachment.get('content')
        row = _json_text(content[0]) if isinstance(content, list) and len(content) == 1 else None
        if not row or row.get('client') != 'claude' or attachment.get('type') != 'hook_additional_context':
            return None
        event = row.get('event_name') if row.get('schema') == LOOKUP_RECEIPT_SCHEMA else 'PostToolUse'
        tool_event = event in {'PreToolUse', 'PostToolUse'}
        if (row.get('schema') in {LOOKUP_RECEIPT_SCHEMA, POSTWRITE_RECEIPT_SCHEMA}
                and record.get('sessionId') == row.get('session_id')
                and attachment.get('hookEvent') == event
                and attachment.get('hookName') == (f"{event}:{row.get('tool_name')}" if tool_event else event)
                and (attachment.get('toolUseID') == row.get('tool_use_id') if tool_event else 'toolUseID' not in attachment)):
            return row
        return None
    payload = record.get('payload')
    if (record.get('type') == 'response_item' and isinstance(payload, dict)
            and payload.get('type') == 'message' and payload.get('role') == 'developer'):
        content = payload.get('content')
        if (not isinstance(content, list) or len(content) != 1 or not isinstance(content[0], dict)
                or set(content[0]) != {'type', 'text'} or content[0]['type'] != 'input_text'):
            return None
        row = _json_text(content[0]['text'])
        metadata = payload.get('internal_chat_message_metadata_passthrough')
        if (row and row.get('client') == 'codex' and isinstance(metadata, dict)
                and _nonempty(row.get('turn_id')) and metadata.get('turn_id') == row['turn_id']):
            return row
    return None


def matched_tool_call(row: dict, prior_records: list[dict]) -> bool:
    calls = [(name, value, context) for record in prior_records
             for tool_id, name, value, context in tool_calls(record) if tool_id == row['tool_use_id']]
    return (len(calls) == 1 and calls[0][0] == row['tool_name']
            and digest(calls[0][1]) == row['tool_input_sha256']
            and calls[0][2] == row['session_id' if row['client'] == 'claude' else 'turn_id'])


def matched_prompt(row: dict, prior_records: list[dict]) -> bool:
    for record in reversed(prior_records):
        text = _claude_user_text(record)
        if text is not None:
            return record.get('sessionId') == row['session_id'] and digest(text) == row['task_sha256']
        payload = record.get('payload')
        if row['client'] == 'codex' and isinstance(payload, dict) and payload.get('type') == 'user_message':
            return digest(payload.get('message')) == row['task_sha256']
    return False


def preuse_delivery(record: dict, prior_records: list[dict], *, repo: Path):
    row = receipt_from_envelope(record)
    if row is None or not validate_lookup_receipt(row, repo=repo) or not row['complete'] or row['overflow']:
        return None
    if not (matched_tool_call(row, prior_records) if row['event_name'] == 'PreToolUse'
            else matched_prompt(row, prior_records)):
        return None
    return [r['id'] for r in row['rules']]


def contains_receipt_marker(value: object) -> bool:
    schemas = {LOOKUP_RECEIPT_SCHEMA, POSTWRITE_RECEIPT_SCHEMA}
    if isinstance(value, dict):
        return any(contains_receipt_marker(v) for v in value.values())
    if isinstance(value, list):
        return any(contains_receipt_marker(v) for v in value)
    return isinstance(value, str) and any(schema in value for schema in schemas)
