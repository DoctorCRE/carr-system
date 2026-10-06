#!/usr/bin/env python3
"""rule-pack-drift-gate.py — the turn's OBSERVED WORK is diffed against the rule
packs the session actually loaded, and the gap is either recorded or reopened.

# doctrine: rule-delivery-load-layers

WHY THIS EXISTS. Scoping rule delivery is safe only while one thing stays true:
a session that wanders into work it never declared still gets that work's rules.
Rule 347a9ca6 is the law — "a session's name does not predict the work it will
do", taught after Joe pointed out that he does full system builds inside a
session called nightly-record-layer. Both 2026-08-23 council chairs made the
same requirement structural rather than hopeful:

    "A Stop gate diffs the turn's verbs and nouns against loaded packs. If
     git/CI ran and the engineering pack was not loaded, reopen and load."

Without this gate, scoping does not merely risk the old failure — it INSTALLS
it, because the boot payload would shrink on the guess that a session's declared
work is its whole work.

WHAT IT LOOKS AT. Only the current turn: every tool call the session made, the
verbs it named, and the text on both sides, from the last genuine user message
onward. Triggers come from ops/config/rule-enforcement-map.json, the same
reviewed file the database tags are compiled from, so the gate and the compiler
cannot drift into disagreeing about what a pack is for.

SHADOW FIRST, AND THE MODE IS NOT THIS FILE'S TO DECIDE. Both chairs required a
week of running the selector beside full recitation before anything is cut. That
switch lives in one place — ops.rule_delivery_policy in the database — and this
gate reads it the only way a hook can: out of the standing-context result already
sitting in the transcript. No second copy of the policy, no local flag to fall
out of sync with the verb (rule 0f38532e). If the mode cannot be established,
the gate RECORDS and does not block, because a gate that blocks on an
unestablished policy is a gate that will be removed within a week.

WHAT IT WRITES, and this is the shadow week's whole evidence base:
out/rule-delivery-shadow.jsonl, one row per turn — which packs the work implied,
which were loaded, which rules a scoped boot would have omitted, and the subset
of those the work actually needed. ops/rule-delivery-shadow-watch.py reads it
nightly, so the comparison cannot quietly stop running (the lesson of the
admission contract, which sat unmeasured in Production for months because the
only thing that could measure it was a door a human had to open).

A MISS is the row that matters: a rule this turn's work needed, that a scoped
boot would not have delivered. Enforcement flips on at zero unexplained misses.
"""
from __future__ import annotations

import ast
import hashlib
import importlib
import json
import os
import re
import sys
from pathlib import Path

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
from lib.transcript_read import load_transcript  # noqa:E402
from lib.rule_delivery_preuse import (  # noqa:E402
    contains_receipt_marker, has_background_tool_call, preuse_delivery,
)
from lib.rule_delivery_shadow import (  # noqa:E402
    append_locked, file_sha256, make_error_observation, make_observation,
    source_sha256, stamp,
)
MAP = os.path.join(REPO, "ops", "config", "rule-enforcement-map.json")
LOG = os.path.join(REPO, "out", "rule-delivery-shadow.jsonl")
RECEIPT_SCHEMA = os.path.join(
    REPO, "control-room", "contracts", "engineering-slice-receipt.v1.schema.json")
CARR_PATH_MARKERS = ("/carr-system/", "/carr-system", "my drive/carr ai")
SYNTHETIC_PREFIXES = ("The following is the Codex agent history", "<environment_context>",
                      "<app-context>", "<skills_instructions>", "<permissions instructions>",
                      "<task-notification>", "<scheduled-task")
SCHEDULED_TASK_PREAMBLE = (
    "This is an automated run of a scheduled task. The user is not present "
    "to answer questions. For implementation details, execute autonomously "
    "without asking clarifying questions — make reasonable choices and note "
    "them in your output. \"write\" actions (e.g. MCP tools that send, post, "
    "create, update, or delete), only take them if the task file asks for that "
    "specific action. When in doubt, producing a report of what you found is "
    "the correct output."
)
ENGINEERING_WORKFLOW_PACKS = (
    "engineering-git", "delegation-council", "scheduled-automation", "source-study")
ENGINEERING_WORKFLOW_HEADER = (
    "You are the fresh, dedicated Codex executor for one bounded CARR Engineering Passport slice.\n\n"
    "RULE-DELIVERY WORKFLOW: engineering-slice\n"
    "RULE-DELIVERY PACKS: engineering-git,delegation-council,scheduled-automation,source-study\n")
# Immutable custom-tool payloads from the source transcript for event 10529812.
# Receipt normalization is deliberately a positive provenance check, not a
# Python semantics decision. A one-byte change makes the payload observable.
# Both captured programs emit the same schema-exact receipt.
CAPTURED_RECEIPT_TOOL_INPUTS = {
    "34c6578998ba91f1cf7d4a2192f907a9c1f05643236e450e60a34d71754c9627":
        "d06a1a058c74e1859fc88e2ede25faf670193aee7acb9313559e05dfaf8e570f",
    "bc258550a9b3a9812d322a0b702e546baa260dc89e4990a5e59c84eb44aeed08":
        "d06a1a058c74e1859fc88e2ede25faf670193aee7acb9313559e05dfaf8e570f",
}

# Immutable tool payloads from five later pre-PR684 Engineering Passport
# observations.  These payloads are normalized only at byte-exact digests; a
# one-byte change restores full lexical scanning.  Values name the inert token
# class proven by the source transcript, while all surrounding commands remain.
CAPTURED_INERT_TOOL_METADATA = {
    "b06a9a6da3269ad1576eb891b1e7a67d20da392801bb5aa395ddcbbef121b486":
        ("checkpoint",),
    "59933d4362afbde0179275b300958da284e401591712b5addd386ac26e5d0cf1":
        ("negated-vendor-glob",),
    "7c6cf289ce5d8665372bdf971cd3d8b24f066035fa5199477341cddfebcfaeee":
        ("checkpoint",),
    "e28f270f13290125b8d04f5f6063e8580d6a68236b58b524021dbff2b8973ac9":
        ("checkpoint",),
    "8b74a92d076a9457b76a1504c2655a4dadb327a4c3231909fbc70fcb12abe754":
        ("artifact-receipt",),
    "1a2ccfb72fa6b13a1240ecd625e6026924c711d6a5651adcb78d8516a20d2663":
        ("negated-vendor-glob",),
    "b5b5575c18bf1568bf9f348870aa089ed8356d09d765c9f96761e473a28baf02":
        ("negated-vendor-glob",),
    # Hermetic fixture for hosted strict where the owning transcripts are absent.
    "c41d1cb2a236bda78011d7595a21dad91f93e5316aa9a7a15ee9cbec9c695ae0":
        ("checkpoint", "negated-vendor-glob", "artifact-receipt"),
}


def load_packs():
    """Pack name -> compiled trigger regex, and pack name -> its rules.

    NOT REPOINTED TO THE NEW TRIGGER TABLE (WR-000019 slice S9). Slice S9 adds
    a declarative, per-RULE trigger table (ops/config/rule-jit-triggers.v1.json,
    compiled by ops/rule-jit-compile.py) that the pre-tool-call rail
    (hooks/rule-pack-preuse-reselection.py) reads for its generalized delivery
    path. This gate's own keyword matching stays exactly as it is, reading
    `rule_packs` straight out of ops/config/rule-enforcement-map.json, for two
    reasons that made "repoint it" not the straightforward move the slice's
    own instructions allowed for:

      1. GRANULARITY MISMATCH. This gate's `triggers` is PACK-shaped (11 packs,
         one regex each) and its `members` is used only to name the rules a
         MISSING pack would have carried (see `evaluate()`'s `missed` line).
         The new table is TRIGGER-shaped and only ~49 of 126 JIT rules carry a
         seeded, more precise detector; the rest still resolve through a
         pack-level fallback that is (by construction) the identical keyword
         list this file already compiles. Repointing `members` to the new
         table's rule_ids would either reproduce the exact same pack-level
         answer for those un-seeded rules or silently narrow this gate's
         "which rules would this miss have cost you" reporting to only the
         49 seeded ones — a real behavior change to a byte-pinned, fixture-
         proven observation contract (CAPTURED_RECEIPT_TOOL_INPUTS and
         CAPTURED_INERT_TOOL_METADATA above exist for exactly this reason:
         this file's behavior on real historical transcripts is verified, not
         merely unit-tested).
      2. THIS GATE IS TELEMETRY-ONLY, STAYING THAT WAY. It already never
         blocks outside `mode == "enforced"`, which shadow mode never reaches
         (see the module docstring). Rewiring its comparison during the same
         slice that also changes the delivery rail it is meant to audit would
         make the audit and the thing it audits move together — exactly the
         coupling an independent drift check exists to avoid.

    So this compiles the SAME `rule_packs` keyword lists it always has, and
    the new trigger table's own compiler (ops/rule-jit-compile.py) reuses this
    exact word-boundary construction for its pack_fallback rows on purpose
    (see `pack_keyword_pattern()` there), so the two are recognizably the same
    keyword source even though neither reads the other's file.
    """
    raw = Path(MAP).read_bytes()
    data = json.loads(raw)
    triggers, members = {}, {}
    for name, pack in data.get("rule_packs", {}).items():
        words = [re.escape(t) for t in pack.get("triggers", []) if str(t).strip()]
        if not words:
            continue
        # \b around a term that ends in punctuation (x.com) never matches, so the
        # boundary is only asserted where the term's own edge is a word character.
        parts = []
        for word in words:
            left = r"\b" if re.match(r"\w", word[0]) else ""
            right = r"\b" if re.match(r"\w", word[-1]) else ""
            parts.append(f"{left}{word}{right}")
        triggers[name] = re.compile("|".join(parts), re.I)
    for short, entry in data.get("rule_load_layers", {}).items():
        for name in entry.get("packs", []):
            members.setdefault(name, []).append(short)
    return triggers, members, hashlib.sha256(raw).hexdigest()


def _content_text(content, kinds=("text", "input_text", "output_text")):
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    return "\n".join(str(b.get("text", "")) for b in content
                     if isinstance(b, dict) and b.get("type") in kinds)


def _payload_message(record):
    payload = record.get("payload")
    if isinstance(payload, dict) and payload.get("type") == "message":
        return payload
    return None


def role_and_text(record):
    payload = record.get("payload")
    if isinstance(payload, dict) and payload.get("type") == "message":
        return payload.get("role"), _content_text(payload.get("content"))
    message = record.get("message") if isinstance(record.get("message"), dict) else record
    return message.get("role") or record.get("type"), _content_text(message.get("content"))


def _schema_valid(value, schema, root):
    """The dependency-free subset of JSON Schema used by the receipt contract."""
    if "$ref" in schema:
        prefix = "#/$defs/"
        ref = schema["$ref"]
        return (isinstance(ref, str) and ref.startswith(prefix)
                and _schema_valid(value, root["$defs"].get(ref[len(prefix):], {}), root))
    if "oneOf" in schema:
        return sum(_schema_valid(value, item, root) for item in schema["oneOf"]) == 1
    if "const" in schema and value != schema["const"]:
        return False
    if "enum" in schema and value not in schema["enum"]:
        return False
    kind = schema.get("type")
    if kind == "object":
        if not isinstance(value, dict):
            return False
        properties = schema.get("properties", {})
        required = schema.get("required", [])
        if any(key not in value for key in required):
            return False
        if schema.get("additionalProperties") is False and set(value) - set(properties):
            return False
        return all(key not in properties or _schema_valid(item, properties[key], root)
                   for key, item in value.items())
    if kind == "array":
        if not isinstance(value, list) or len(value) < schema.get("minItems", 0):
            return False
        if schema.get("uniqueItems"):
            rows = [json.dumps(item, sort_keys=True, separators=(",", ":")) for item in value]
            if len(rows) != len(set(rows)):
                return False
        return all(_schema_valid(item, schema.get("items", {}), root) for item in value)
    if kind == "string":
        return (isinstance(value, str) and len(value) >= schema.get("minLength", 0)
                and ("pattern" not in schema or re.fullmatch(schema["pattern"], value) is not None))
    if kind == "boolean":
        return isinstance(value, bool)
    if kind == "null":
        return value is None
    return True


def exact_engineering_receipt(value):
    try:
        schema = json.loads(Path(RECEIPT_SCHEMA).read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError):
        return False
    return _schema_valid(value, schema, schema)


def assistant_machine_receipt(record):
    """Return only a whole, strict, assistant-authored typed receipt."""
    message = _payload_message(record)
    if message is None:
        message = record.get("message") if isinstance(record.get("message"), dict) else record
    if message.get("role") != "assistant":
        return None
    content = message.get("content")
    if not isinstance(content, list) or len(content) != 1:
        return None
    block = content[0]
    if not isinstance(block, dict) or block.get("type") not in {
            "text", "output_text"} or not isinstance(block.get("text"), str):
        return None
    try:
        value = json.loads(block["text"].strip())
    except (TypeError, ValueError):
        return None
    return value if exact_engineering_receipt(value) else None


def serialized(record):
    values = []

    def walk(value):
        if isinstance(value, dict):
            for key, item in value.items():
                values.append(str(key))
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)
        elif value is not None:
            values.append(str(value))

    walk(record)
    return "\n".join(values)


def genuine_user_task(record):
    role, value = role_and_text(record)
    if role not in {"user", "human"} or not value.strip():
        return ""
    if synthetic_turn_boundary(record):
        return ""
    message = record.get("message") if isinstance(record.get("message"), dict) else record
    content = message.get("content")
    if isinstance(content, list) and content and all(
            isinstance(b, dict) and b.get("type") == "tool_result" for b in content):
        return ""
    return value


def _skill_injection_record_ids(records):
    """Rows that Claude injected after a provenance-bound Skill tool result.

    Claude serializes a selected Skill body as a user-shaped text row.  The
    trusted boundary is the transcript chain, never the visible heading: an
    assistant Skill tool_use, its linked tool_result, then the child text row
    under the same prompt.  An ordinary human copy lacks that chain and remains
    fully observable.
    """
    uuid_counts = {}
    by_uuid = {}
    for record in records:
        if not isinstance(record, dict) or not isinstance(record.get("uuid"), str):
            continue
        uuid = record["uuid"]
        uuid_counts[uuid] = uuid_counts.get(uuid, 0) + 1
        by_uuid[uuid] = record
    injected = set()
    for row in records:
        if not isinstance(row, dict) or row.get("type") != "user" or row.get("origin"):
            continue
        message = row.get("message")
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        if message.get("role") != "user" or not isinstance(content, list) \
                or len(content) != 1 or not isinstance(content[0], dict) \
                or content[0].get("type") != "text" \
                or not isinstance(content[0].get("text"), str):
            continue
        child_uuid = row.get("uuid")
        result_uuid = row.get("parentUuid")
        prompt_id = row.get("promptId")
        if (not isinstance(child_uuid, str) or uuid_counts.get(child_uuid) != 1
                or not isinstance(result_uuid, str) or uuid_counts.get(result_uuid) != 1
                or not isinstance(prompt_id, str) or not prompt_id.strip()):
            continue
        result = by_uuid.get(result_uuid)
        result_message = result.get("message") if isinstance(result, dict) else None
        if (not isinstance(result, dict) or result.get("type") != "user"
                or not isinstance(result_message, dict)
                or result_message.get("role") != "user"):
            continue
        result_content = (result_message or {}).get("content")
        if (not isinstance(result_content, list) or len(result_content) != 1
                or not isinstance(result_content[0], dict)
                or result_content[0].get("type") != "tool_result"):
            continue
        assistant_uuid = result.get("parentUuid")
        if (not isinstance(assistant_uuid, str)
                or uuid_counts.get(assistant_uuid) != 1
                or result.get("sourceToolAssistantUUID") != assistant_uuid
                or result.get("promptId") != prompt_id):
            continue
        assistant = by_uuid.get(assistant_uuid)
        assistant_message = (assistant or {}).get("message")
        if (not isinstance(assistant, dict) or assistant.get("type") != "assistant"
                or not isinstance(assistant_message, dict)
                or assistant_message.get("role") != "assistant"):
            continue
        assistant_content = (assistant_message or {}).get("content")
        if (not isinstance(assistant_content, list) or len(assistant_content) != 1
                or not isinstance(assistant_content[0], dict)
                or assistant_content[0].get("type") != "tool_use"
                or assistant_content[0].get("name") != "Skill"):
            continue
        tool_use_id = assistant_content[0].get("id")
        if (not isinstance(tool_use_id, str) or not tool_use_id.strip()
                or result_content[0].get("tool_use_id") != tool_use_id):
            continue
        injected.add(id(row))
    return injected


def synthetic_turn_boundary(record):
    role, value = role_and_text(record)
    if role not in {"user", "human"} or not value.strip():
        return False
    head = value.lstrip()
    if head.startswith("<task-notification>"):
        origin = record.get("origin")
        return isinstance(origin, dict) and origin.get("kind") == "task-notification"
    if head.startswith("<scheduled-task"):
        return bool(scheduled_workflow_packs(record))
    return head.startswith(SYNTHETIC_PREFIXES)


def suppress_static_text(record):
    """Only provenance-authenticated or exact source-owned wrappers are static."""
    role, value = role_and_text(record)
    if role not in {"user", "human"} or not value.strip():
        return False
    head = value.lstrip()
    if head.startswith("<task-notification>"):
        origin = record.get("origin")
        return isinstance(origin, dict) and origin.get("kind") == "task-notification"
    return head.startswith("<scheduled-task") and bool(scheduled_workflow_packs(record))


def current_turn(records):
    injected = _skill_injection_record_ids(records)
    for index in range(len(records) - 1, -1, -1):
        if id(records[index]) in injected:
            continue
        if genuine_user_task(records[index]) or synthetic_turn_boundary(records[index]):
            return records[index:]
    return records


def scheduled_workflow_packs(record):
    """Packs from an exact source-owned scheduled-task wrapper, else none.

    The scheduler omits YAML frontmatter and prepends one fixed paragraph.  We
    compare the complete rendered body before honoring its declaration: a user
    cannot paste the marker into arbitrary instructions to suppress lexical
    drift detection, and a changed installed task fails closed to ordinary text
    scanning until config-as-code restores source parity.
    """
    role, value = role_and_text(record)
    if role not in {"user", "human"} or not value.lstrip().startswith("<scheduled-task"):
        return []
    matched = re.fullmatch(
        r'<scheduled-task name="([a-z0-9-]+)" file="([^"]+)">\n(.*)</scheduled-task>',
        value.strip(), re.S)
    if not matched:
        return []
    name, installed_path, inner = matched.groups()
    if not installed_path.endswith(f"/.claude/scheduled-tasks/{name}/SKILL.md"):
        return []
    source = Path(REPO) / "ops" / "scheduled-tasks" / f"{name}.SKILL.md"
    if not source.is_file():
        return []
    portable = source.read_text(encoding="utf-8")
    pieces = portable.split("---\n", 2)
    if len(pieces) != 3:
        return []
    body = pieces[2].lstrip().replace("{{REPO}}", REPO)
    if inner != f"{SCHEDULED_TASK_PREAMBLE}\n\n{body}":
        return []
    workflow = re.search(r"^RULE-DELIVERY WORKFLOW: ([a-z0-9-]+)$", body, re.M)
    packs = re.search(r"^RULE-DELIVERY PACKS: ([a-z0-9,-]+)$", body, re.M)
    if not workflow or workflow.group(1) != name or not packs:
        return []
    declared = packs.group(1).split(",")
    catalog = json.loads(Path(MAP).read_text(encoding="utf-8")).get("rule_packs", {})
    if (not declared or any(not pack for pack in declared)
            or len(set(declared)) != len(declared)
            or any(pack not in catalog for pack in declared)):
        return []
    return declared


def engineering_workflow_packs(record):
    """Packs from a fully bound controller Engineering Passport wrapper."""
    role, value = role_and_text(record)
    if role not in {"user", "human"} or not value.startswith(ENGINEERING_WORKFLOW_HEADER):
        return []
    if (value.count("SERVER-ISSUED SLICE PACKET (immutable):") != 1
            or value.count("CONTROLLER TASK BINDING (immutable):") != 1
            or ('FIRST: call `standing-context` with exactly this input and read the returned rules: '
                '{"packs":["engineering-git","delegation-council",'
                '"scheduled-automation","source-study"]}') not in value
            or "Do not pass `workflow`" not in value
            or "REFUSE before inspecting the envelope, source, or job" not in value):
        return []
    packet_marker = "SERVER-ISSUED SLICE PACKET (immutable):\n"
    task_marker = "\n\nCONTROLLER TASK BINDING (immutable):\n"
    try:
        packet_text, task_text = value.split(packet_marker, 1)[1].split(task_marker, 1)
        packet = json.loads(packet_text)
        task = json.loads(task_text)
        bridge = os.path.join(REPO, "tools", "room-bridge")
        if bridge not in sys.path:
            sys.path.insert(0, bridge)
        passport = importlib.import_module("engineering_passport")
        expected_task_keys = {
            "attempt_id", "engineering_plan", "engineering_slice", "generation",
            "job_ref", "plan_digest", "slice_ref", "work_request",
        }
        if not isinstance(task, dict) or set(task) != expected_task_keys:
            return []
        plan = passport.validate_engineering_slice_plan(task["engineering_plan"])
        expected_packet_keys = {
            "schema_version", "slice_ref", "plan_digest", "envelope_digest",
            "fresh_native_session_required", "objective", "definition_of_done",
            "planned_checks", "scope_boundary", "forbidden_change_refs", "envelope",
            "packet_digest",
        }
        if not isinstance(packet, dict) or set(packet) != expected_packet_keys:
            return []
        envelope = passport.base.validate_execution_envelope(packet["envelope"])
        selected = next(item for item in plan["slices"]
                        if item["slice_ref"] == task["slice_ref"])
        packet_without_digest = {key: item for key, item in packet.items()
                                 if key != "packet_digest"}
        expected_scope = {
            "plan_step_refs": sorted(set(selected["declared_plan_step_refs"])),
            "component_refs": sorted(set(selected["declared_component_refs"])),
            "component_dependencies": sorted(
                envelope["request"]["declared_expectations"]["component_dependencies"],
                key=lambda edge: (edge["component_ref"], edge["depends_on_component_ref"])),
            "resource_refs": sorted(set(selected["declared_resource_refs"])),
        }
        if (task["engineering_slice"] != selected
                or task["plan_digest"] != plan["plan_digest"]
                or task["work_request"] != plan["work_request"]["id"]
                or task["work_request"] != envelope["work_request_id"]
                or packet["plan_digest"] != plan["plan_digest"]
                or packet["slice_ref"] != task["slice_ref"]
                or packet["schema_version"] != "engineering-slice-packet.v1"
                or packet["fresh_native_session_required"] is not True
                or not isinstance(packet["envelope_digest"], str)
                or re.fullmatch(r"sha256:[a-f0-9]{64}", packet["envelope_digest"]) is None
                or packet["packet_digest"] != passport.base.canonical_digest(
                    packet_without_digest)
                or envelope["request"]["declared_expectations"] != expected_scope
                or envelope["request"]["job_ref"] != task["job_ref"]
                or any(packet[field] != selected[field] for field in (
                    "objective", "definition_of_done", "planned_checks",
                    "scope_boundary", "forbidden_change_refs"))
                or not isinstance(task["generation"], int) or isinstance(task["generation"], bool)
                or task["generation"] < 1
                or not all(isinstance(task[field], str) and task[field].strip()
                           for field in ("attempt_id", "job_ref"))):
            return []
    except (ImportError, KeyError, StopIteration, TypeError, ValueError):
        return []
    return list(ENGINEERING_WORKFLOW_PACKS)


def delivery_state(records):
    """What this session has loaded, across every standing-context call it made.

    Returns (mode, declared_packs, would_omit). A session that never called the
    verb yields (None, [], []) — nothing to compare against and nothing to block.

    PACKS ACCUMULATE; THEY DO NOT REPLACE. The council's word for it is monotonic:
    entering another domain ADDS its pack and never subtracts an earlier one. That
    is also what stops a false miss here — a session that loads the engineering
    pack, then calls standing-context again bare to look a rule up by id, has not
    unloaded anything, and reading only the latest call would say it had. The mode
    and the omission list come from the LATEST call, because those describe the
    policy and the payload as they stand now.
    """
    mode, declared, omit = None, set(), []
    for index, record in enumerate(records):
        found = preuse_delivery(record, records[:index], repo=Path(REPO))
        if found is None:
            if "rule_delivery" not in serialized(record):
                continue
            found = standing_result_delivery(record, records[:index])
        if found:
            mode, packs, omit = found
            declared.update(packs)
    return mode, sorted(declared), omit


def standing_result_delivery(record, prior):
    """Credit service results only when tied to a standing-context call."""
    calls = set()
    for previous in prior:
        native = previous.get("payload") or {}
        if (previous.get("type") == "response_item" and native.get("type") in {'function_call', 'custom_tool_call'}
                and ("standing_context" in str(native.get("name", ""))
                     or "standing-context" in str(native.get("name", ""))
                     or _native_standing_invocation(native))):
            calls.add(native.get("call_id"))
        message = previous.get("message") or {}
        content = message.get("content")
        for block in content if isinstance(content, list) else []:
            if not isinstance(block, dict) or block.get("type") != "tool_use":
                continue
            name = str(block.get("name", ""))
            arguments = block.get('input') or {}
            if "standing-context" in name or "standing_context" in name or (
                    name == "Bash" and isinstance(arguments, dict)
                    and _shell_standing_invocation(arguments.get('command', ''))):
                calls.add(block.get("id"))
    message = record.get("message") or {}
    content = message.get("content")
    for block in content if isinstance(content, list) else []:
        if (isinstance(block, dict) and block.get("type") == "tool_result"
                and block.get("tool_use_id") in calls and not block.get("is_error")):
            return _service_delivery(block.get("content"))
    payload = record.get("payload") or {}
    if (record.get("type") == "response_item" and payload.get("type") in {'function_call_output', 'custom_tool_call_output'}
            and payload.get("call_id") in calls and not payload.get("is_error")):
        return _service_delivery(payload.get("output"))
    invocation = payload.get("invocation") or {}
    native_name = payload.get("tool_name") or invocation.get("tool", "")
    if (record.get("type") == "event_msg" and payload.get("type") == "mcp_tool_call_end"
            and ("standing_context" in str(native_name) or "standing-context" in str(native_name))
            and not payload.get("is_error") and "Err" not in (payload.get("result") or {})):
        return _service_delivery(payload.get("result"))
    return None


def _literal_transport(source):
    """Parse a single native call with literal JSON-like transport arguments."""
    import ast
    source = source.strip().rstrip(';').strip()
    if source.startswith('text(') and source.endswith(')'):
        source = source[5:-1].strip()
    match = re.fullmatch(r'(?:await\s+)?tools\.(mcp__carr__standing[_-]context|exec_command)\((.*)\)', source, re.S)
    if not match:
        return None
    try:
        raw = match.group(2)
        # Replace strings first so key normalization cannot change their data.
        strings = []
        def protect(token):
            strings.append(ast.literal_eval(token.group()))
            return f'"__transport_string_{len(strings)-1}__"'
        string_token = r"\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'"
        raw = re.sub(string_token, protect, raw)
        raw = re.sub(r'([{,]\s*)([A-Za-z_$][\w$]*)(\s*:)', r'\1"\2"\3', raw)
        args = json.loads(raw)
        def restore(value):
            if isinstance(value, str) and re.fullmatch(r'__transport_string_\d+__', value):
                return strings[int(value[len('__transport_string_'):-2])]
            if isinstance(value, list):
                return [restore(v) for v in value]
            if isinstance(value, dict):
                return {restore(k):restore(v) for k,v in value.items()}
            return value
        return match.group(1), restore(args)
    except (ValueError, SyntaxError, IndexError):
        return None


def _native_standing_invocation(payload):
    if payload.get('type') == 'custom_tool_call' and payload.get('name') in {'exec', 'functions.exec'}:
        parsed = _literal_transport(str(payload.get('input', '')))
        if parsed:
            name, args = parsed
            return name.startswith('mcp__carr__standing') or (
                isinstance(args, dict) and _shell_standing_invocation(str(args.get('cmd', ''))))
    if payload.get('type') == 'function_call' and str(payload.get('name', '')).split('.')[-1] == 'exec_command':
        try:
            args = json.loads(payload.get('arguments', '{}'))
            return _shell_standing_invocation(args.get('cmd', ''))
        except (ValueError, TypeError):
            return False
    return False


def _shell_standing_invocation(command):
    import shlex
    try:
        words = shlex.split(command)
    except ValueError:
        return False
    if len(words) >= 3 and words[0] == 'cd' and os.path.isabs(words[1]) and words[2] == '&&':
        words = words[3:]
    if words and words[0] in {'bash', 'sh'}:
        words = words[1:]
    return (len(words) in {3, 4} and os.path.basename(words[0]) == 'run.sh'
            and words[1:3] == ['call', 'standing-context'])


def _service_delivery(value):
    """Accept service envelopes and known transport wrappers, not arbitrary text."""
    if isinstance(value, str):
        value = value.strip()
        if value.startswith('local-verb identity ->'):
            value = value.partition('\n')[2].strip()
        elif value.startswith('Script completed') and '\nOutput:\n' in value:
            value = value.split('\nOutput:\n', 1)[1].strip()
        try:
            return _service_delivery(json.loads(value))
        except ValueError:
            return None
    if isinstance(value, list):
        for item in value:
            found = _service_delivery(item)
            if found:
                return found
    if isinstance(value, dict):
        if (value.get('is_error') or value.get('isError') or value.get('ok') is False
                or value.get('error') or value.get('session_id') or value.get('cell_id')
                or ('exit_code' in value and value['exit_code'] != 0)):
            return None
        if 'rule_delivery' in value:
            return _find_delivery(value)
        for key in ('content', 'text', 'output', 'Ok'):
            if key in value:
                found = _service_delivery(value[key])
                if found:
                    return found
    return None


def _find_delivery(value):
    """Depth-first hunt for a rule_delivery object, whatever wrapper it arrived in."""
    if contains_receipt_marker(value):
        return None
    if isinstance(value, dict):
        block = value.get("rule_delivery")
        if isinstance(block, dict) and "mode" in block:
            not_found = {str(p).strip().lower()
                         for p in block.get("packs_not_found", []) or []}
            return (block.get("mode"),
                    [str(p) for p in block.get("declared_packs", []) or []
                     if str(p).strip().lower() not in not_found],
                    [str(r) for r in block.get("would_omit", []) or []])
        for item in value.values():
            found = _find_delivery(item)
            if found:
                return found
        return None
    if isinstance(value, list):
        for item in value:
            found = _find_delivery(item)
            if found:
                return found
        return None
    if isinstance(value, str) and "rule_delivery" in value:
        try:
            return _find_delivery(json.loads(value))
        except (TypeError, ValueError):
            return None
    return None


def _safe_literal(node, values):
    """Evaluate data-only Python literals and local literal-constructor lambdas."""
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        if node.id not in values:
            raise ValueError("unknown literal name")
        return values[node.id]
    if isinstance(node, ast.List):
        return [_safe_literal(item, values) for item in node.elts]
    if isinstance(node, ast.Tuple):
        return tuple(_safe_literal(item, values) for item in node.elts)
    if isinstance(node, ast.Dict):
        return {_safe_literal(key, values): _safe_literal(item, values)
                for key, item in zip(node.keys, node.values)}
    if isinstance(node, ast.Lambda):
        return node
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        function = values.get(node.func.id)
        if not isinstance(function, ast.Lambda) or node.keywords:
            raise ValueError("not a data-only literal constructor")
        names = [arg.arg for arg in function.args.args]
        if len(names) != len(node.args):
            raise ValueError("literal constructor arity mismatch")
        local = dict(values)
        local.update({name: _safe_literal(arg, values)
                      for name, arg in zip(names, node.args)})
        return _safe_literal(function.body, local)
    raise ValueError("not a data-only literal")


def _line_offsets(text):
    offsets = [0]
    for matched in re.finditer("\n", text):
        offsets.append(matched.end())
    return offsets


def _captured_receipt_span(text):
    """Locate one strict receipt literal after its enclosing bytes were pinned."""
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return None
    offsets = _line_offsets(text)
    values = {}
    spans = []
    for statement in tree.body:
        if not isinstance(statement, ast.Assign) or len(statement.targets) != 1 \
                or not isinstance(statement.targets[0], ast.Name):
            continue
        name = statement.targets[0].id
        try:
            value = _safe_literal(statement.value, values)
        except (TypeError, ValueError):
            continue
        values[name] = value
        if name != "receipt" or not exact_engineering_receipt(value):
            continue
        start = offsets[statement.value.lineno - 1] + statement.value.col_offset
        end = offsets[statement.value.end_lineno - 1] + statement.value.end_col_offset
        canonical = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
        spans.append((start, end, hashlib.sha256(canonical).hexdigest()))
    return spans[0] if len(spans) == 1 else None


def _decoded_command(input_text):
    """Decode a Codex tools.exec_command cmd string, preserving outer metadata."""
    matched = re.search(r"\bcmd\s*:\s*", input_text)
    if not matched:
        return None
    decoder = json.JSONDecoder()
    try:
        value, used = decoder.raw_decode(input_text[matched.end():])
    except (TypeError, ValueError):
        return None
    if not isinstance(value, str):
        return None
    start = matched.end()
    return value, input_text[:start] + '"[decoded-command]"' + input_text[start + used:]


def _normalize_captured_tool_input(raw):
    """Normalize only an immutable source-transcript payload and receipt."""
    raw_digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    expected_receipt_digest = CAPTURED_RECEIPT_TOOL_INPUTS.get(raw_digest)
    if expected_receipt_digest is None:
        return None
    decoded = _decoded_command(raw)
    if decoded is None:
        return None
    command, outer = decoded
    matched = re.search(r"<<['\"]?([A-Za-z0-9_]+)['\"]?\n(.*?)\n\1(?:\n|$)",
                        command, re.S)
    if matched is None:
        return None
    body = matched.group(2)
    receipt = _captured_receipt_span(body)
    if receipt is None:
        return None
    start, end, receipt_digest = receipt
    if receipt_digest != expected_receipt_digest:
        return None
    normalized = body[:start] + "[engineering-slice-receipt.v1]" + body[end:]
    command = command[:matched.start(2)] + normalized + command[matched.end(2):]
    return "\n".join((outer, command))


def _normalize_captured_inert_metadata(raw):
    """Normalize only byte-pinned inert tokens from reviewed transcripts."""
    classes = CAPTURED_INERT_TOOL_METADATA.get(
        hashlib.sha256(raw.encode("utf-8")).hexdigest())
    if classes is None:
        return None
    normalized = raw
    if "checkpoint" in classes:
        normalized = normalized.replace("checkpoint", "state-marker")
    if "negated-vendor-glob" in classes:
        normalized = normalized.replace("!vendor", "![excluded-path]")
    if "artifact-receipt" in classes:
        normalized = normalized.replace("artifact_refs", "output_refs")
        normalized = normalized.replace("artifact:", "output:")
    return normalized


def custom_tool_text(payload):
    """Observe tools fully except immutable, source-bound receipt payloads."""
    name = str(payload.get("name", ""))
    raw = payload.get("input")
    if not isinstance(raw, str):
        return "\n".join((name, serialized(raw)))
    parsed = _literal_transport(raw)
    if parsed and parsed[0].startswith('mcp__carr__standing') and isinstance(parsed[1], dict):
        # This call establishes delivery state; its surface/tier/detail routing
        # metadata is not observed domain work. Keep the call and declared pack
        # names visible while excluding those fixed transport arguments.
        packs = parsed[1].get('packs', [])
        return "\n".join((name, "standing_context", serialized(packs)))
    normalized = _normalize_captured_tool_input(raw) if name == "exec" else None
    if normalized is None and name == "exec":
        normalized = _normalize_captured_inert_metadata(raw)
    return "\n".join((name, normalized if normalized is not None else raw))


def work_text(record, injected_skill_body=False):
    """What the SESSION did and said this turn — never what a tool said back.

    Tool RESULTS are excluded on purpose, and the reason is not tidiness. The
    standing-context payload itself carries the pack index, which is a list of
    every pack's triggers; scanning results would make one boot call fire every
    pack in the catalog and the gate would demand all of them on every turn. A
    directory listing or a search result has the same shape of problem: the
    nouns in it are the world's, not the session's. What this gate is
    adjudicating is the work the session CHOSE to do — its tool calls and its
    prose — which is exactly what rule 347a9ca6 says to judge by.
    """
    if injected_skill_body:
        return ""
    parts = []
    payload = record.get("payload")
    if isinstance(payload, dict) and payload.get("type") == "custom_tool_call":
        parts.append(custom_tool_text(payload))
    message = record.get("message") if isinstance(record.get("message"), dict) else record
    role = message.get("role") or record.get("type")
    if role in {"user", "human"} and suppress_static_text(record):
        return ""
    content = message.get("content")
    payload_message = _payload_message(record)
    if payload_message is not None and payload_message.get("role") in {
            "user", "human", "assistant"}:
        payload_text = _content_text(payload_message.get("content"))
        # Codex response_item messages were not historically part of this
        # adapter's prose path. Admit receipt-shaped rows narrowly: exact
        # machine receipts disappear; malformed/extra/trailing near-shapes fail
        # closed to ordinary lexical scanning.
        if (payload_text.lstrip().startswith("{")
                and "engineering-slice-receipt.v1" in payload_text
                and assistant_machine_receipt(record) is None):
            parts.append(payload_text)
        elif (payload_text.startswith(ENGINEERING_WORKFLOW_HEADER)
              and not engineering_workflow_packs(record)):
            # An ordinary user can copy the visible header. Without a packet
            # rebuilt from the strict plan/envelope and exact task binding it
            # has no source provenance and fails closed to lexical scanning.
            parts.append(payload_text)
    if isinstance(content, str):
        if role in {"user", "human", "assistant"}:
            parts.append(content)
    elif isinstance(content, list):
        receipt = assistant_machine_receipt(record)
        for block in content:
            if not isinstance(block, dict):
                continue
            kind = block.get("type")
            if kind == "tool_use":
                parts.append(str(block.get("name", "")))
                parts.append(serialized(block.get("input")))
            elif kind in {"text", "input_text", "output_text"}:
                if receipt is None:
                    parts.append(str(block.get("text", "")))
    return "\n".join(p for p in parts if p)


def observed_packs(turn, triggers):
    """Which packs THIS TURN's work implies, with the words that fired each."""
    injected = _skill_injection_record_ids(turn)
    text = "\n".join(work_text(record, id(record) in injected) for record in turn)
    hits = {}
    for name, pattern in triggers.items():
        found = sorted({m.group(0).lower() for m in pattern.finditer(text)})
        if found:
            hits[name] = found[:6]
    for record in turn:
        if has_background_tool_call(record):
            if "scheduled-automation" in triggers:
                hits.setdefault("scheduled-automation", ["background-tool"])
        for name in scheduled_workflow_packs(record):
            if name in triggers:
                hits.setdefault(name, ["workflow-contract"])
        for name in engineering_workflow_packs(record):
            if name in triggers:
                hits.setdefault(name, ["workflow-contract"])
    return hits


def evaluate(records, triggers, members):
    turn = current_turn(records)
    mode, declared, omit = delivery_state(records)
    hits = observed_packs(turn, triggers)
    needed = sorted(hits)
    loaded = sorted({str(p).strip().lower() for p in declared})
    missing = [p for p in needed if p not in loaded]
    omitted = {str(r).lower() for r in omit}
    # THE MISS: a rule this turn's work needed that a scoped boot would not have
    # handed over. Everything else in this row is context for reading it.
    missed = sorted({short for pack in missing
                     for short in members.get(pack, [])
                     if short.lower() in omitted})
    return {"mode": mode, "needed": needed, "loaded": loaded, "missing": missing,
            "triggers": hits, "would_omit_count": len(omitted), "missed_rules": missed}


def audit(row):
    if row.get("session") == "selftest":
        return
    try:
        append_locked(Path(LOG), lambda _rows: row)
    except Exception:
        pass


def payload_is_carr(payload):
    cwd = (payload.get("cwd") or payload.get("working_directory")
           or payload.get("workingDirectory"))
    if not isinstance(cwd, str) or not cwd.strip():
        return True
    normalized = cwd.replace("\\", "/").lower()
    repo = REPO.replace("\\", "/").lower().rstrip("/")
    return (normalized == repo or normalized.startswith(repo + "/")
            or any(marker in normalized for marker in CARR_PATH_MARKERS))


def block_reason(result):
    packs = ", ".join(result["missing"])
    words = "; ".join(f"{p}: {', '.join(result['triggers'][p])}" for p in result["missing"])
    return ("RULE PACK DRIFT — this turn did work you did not load the rules for. "
            f"Missing pack(s): {packs}. What named them: {words}. "
            f"Call standing-context with packs:[{', '.join(repr(p) for p in result['missing'])}] "
            "and read what comes back before you finish. A session's name does not "
            "predict its work (rule 347a9ca6), which is exactly why this fires on "
            "what you DID rather than on what you said you would do.")


def main():
    payload = {}
    try:
        payload = json.load(sys.stdin)
        if payload.get("stop_hook_active") or not payload_is_carr(payload):
            return 0
        path = payload.get("transcript_path") or payload.get("transcriptPath")
        if not path or not os.path.exists(path):
            return 0
        # One bad line in the session's own transcript must not switch the
        # gate off (bypass hunt, PR #1224): lib/transcript_read.py skips it
        # and records a transcript_tamper event instead of raising.
        # Logged beside the other gates' tamper events rather than into this
        # gate's own schema-bound rule-delivery-shadow.jsonl.
        records = load_transcript(
            path, hook="rule-pack-drift-gate",
            session=payload.get("session_id") or payload.get("sessionId"),
            log_path=os.path.join(REPO, "out", "jev-required-actions-gate.jsonl"))
        triggers, members, local_map_digest = load_packs()
        result = evaluate(records, triggers, members)
        raw_session = payload.get("session_id") or payload.get("sessionId")
        session = raw_session if isinstance(raw_session, str) and raw_session.strip() \
            else "unavailable"
        row = make_observation(
            session=session, map_digest=local_map_digest,
            source_digest=source_sha256(Path(REPO)), result=result)
        # A turn that implied no pack and loaded none is the ordinary case and
        # writing a row per turn for it would bury the rows that matter.
        if result["needed"] or result["loaded"]:
            audit(row)
        if result["mode"] != "enforced" or not result["missing"]:
            return 0
        print(json.dumps({"decision": "block", "reason": block_reason(result)}))
        return 0
    except Exception as exc:
        # FAIL OPEN, DELIBERATELY, AND SAY SO IN THE LOG. Its siblings fail
        # closed because they guard a claim that would otherwise go out wrong.
        # This one guards DELIVERY: a bug here that blocked every turn would
        # stop all work over a check that has not cut a single rule yet, and the
        # first fix anyone reached for would be to uninstall it.
        raw_session = payload.get("session_id") or payload.get("sessionId")
        session = raw_session if isinstance(raw_session, str) and raw_session.strip() \
            else "unavailable"
        category = {"JSONDecodeError": "invalid-transcript-json",
                    "FileNotFoundError": "transcript-or-source-absent",
                    "PermissionError": "transcript-or-source-unreadable",
                    "ValueError": "invalid-shadow-observation"}.get(
                        type(exc).__name__, "unexpected-gate-error")
        try:
            map_digest = file_sha256(Path(MAP))
            source_digest = source_sha256(Path(REPO))
        except Exception:
            map_digest = "0" * 64
            source_digest = "0" * 64
        row = make_error_observation(
            session=session, error=category, detail="rule-pack-drift-gate-failed-open",
            map_digest=map_digest, source_digest=source_digest)
        audit(row)
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
