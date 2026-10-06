#!/usr/bin/env python3
"""Load dynamically introduced scheduled-work rules before the tool proceeds.

This is a shadow-compatible reselection rail, not an enforcement gate. An exact
top-level ``run_in_background: true`` is observed work in the scheduled domain.
The hook calls the existing authenticated standing-context door, injects the
source-owned rules as additional context, and leaves the original tool input
untouched.  Any failure remains visible to Stop telemetry and never blocks.

GENERALIZED (WR-000019 slice S9). The paragraph above describes the ORIGINAL
single-pack, single-shape rail. Its full receipt contract remains when it fits
the visible hook context cap; an oversized receipt yields a short not-delivered
notice so a file preview cannot masquerade as rule delivery. This
file now also drives a SECOND, more general rail off the declarative
compiled trigger table, ops/config/rule-jit-triggers.v1.json
(ops/rule-jit-compile.py is its only writer): an MCP verb call, a Bash
command family, a file-path write, or a general content fallback, each
mapped to up to ~5 rules. On any PreToolUse call the ORIGINAL rail's exact
shape does not match, this file checks the compiled table instead, and on a
match calls the same standing-context door with the union of every matched
trigger's packs and rule ids, validates the response, and injects a second,
differently-schemad receipt (GENERALIZED_RECEIPT_SCHEMA in
lib/rule_delivery_preuse.py) as additionalContext. The two rails are mutually
exclusive per call (the original shape, when it matches, is handled by the
original code path alone) so the proven rail's behavior cannot be disturbed
by the new one.

MESSAGE SEMANTICS (loop 620). The same module is also wired once at
UserPromptSubmit, the earliest seam that carries the partner's actual message.
Which pack-layer rules bind that message is decided in
ops/rule_trigger_delivery.py: the `prompt_regex` rows Jev compiled once per
rule are matched first; a machine envelope stops there with zero Jev
requests, and a human prompt then gets one budgeted judgment (a ranking plus
single-rule binding requests for its top 7, stale rules always included; a
deterministic word-overlap shortlist stands in when the ranking request is
unavailable) — at most 8 Jev requests per human prompt; code rejects unknown and
already-loaded layer-zero candidates; the existing authenticated
standing-context door supplies the authoritative rule text and identity before
one typed advisory receipt is injected. The content_regex rows no longer run
against tool payloads. Exact verbs, command families, and paths remain code-
owned because those are structured facts rather than semantic judgments.

ROUTE TRIGGERS (100%-recall design, 2026-09-26). ops/config/rule-routes.v1.json
is now the source of truth for which rules a tool call triggers: every active
rule carries at least one route (lib/rule_routes.py), and its `trigger` and
`path_rule` routes are exact matches on the tool name, the record verb (the
MCP tool, the call-verb passthrough, or `run.sh call`), a Bash command
pattern, or a path glob. On a PreToolUse call that hits any route, the rules
it hits are UNIONED with the compiled table's rows, fetched through the same
standing-context door in ONE call, and injected as a third receipt
(ROUTE_RECEIPT_SCHEMA). A call no route hits takes the generalized rail above
unchanged, and the scheduled rail retains its exact-match precedence.
Two properties the route rail adds. It dedupes PER RULE PER SESSION: a rule
delivered in full is not re-injected for the same tool within 30 minutes, and
a different tool delivers it again. And it FITS THE CAP: Claude Code persists
any additionalContext over 10,000 characters to a file and shows a 2,000-
character preview, which is not delivery, so rules not already in the
always-on file are fitted first, then the rest, and any rule that does not
fit is still listed by id with a one-line summary — never dropped silently.
The cap holds by construction, not by margin: the final rendered receipt is
measured, and a routed set too large even for an id-only receipt becomes one
compact notice naming every id (lib/rule_routes.notice_too_large).

THE ROUTE RAIL FAILS OPEN, VISIBLY. No failure blocks or rewrites the call,
and none is silent. A missing or malformed route file announces RULE ROUTE
FILE UNREADABLE on every admitted call, naming the rules that may bind, and
still carries the table rail's delivery after the notice. A door failure, an
exception anywhere in the rail, or a crash anywhere in process() ends in a
fixed notice (never the exception's own text) saying the rules were NOT
delivered and what to do before acting, instead of exit 1 with empty
stdout. A malformed single route counts as matched, so it over-delivers its
rule rather than dropping it; ops/rule-route-coverage.py refuses it in CI.

NOT DONE HERE, ON PURPOSE: hooks/rule-pack-drift-gate.py's Stop-side keyword
telemetry still only recognizes the original schema's receipt as "loaded"
evidence for scheduled-automation; it does not yet credit a generalized-rail
receipt for the packs it delivers. Wiring that up is a comparison this slice
chose to leave rather than force through the drift gate's byte-pinned
observation fixtures under scope pressure — see the note beside
`load_packs()` in that file.
"""

# doctrine: rule-delivery-load-layers

from __future__ import annotations

import fnmatch
import importlib.util
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable, TypeGuard


REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
from lib.rule_delivery_preuse import (  # noqa:E402
    CORPUS_RELATIVE, GENERALIZED_RECEIPT_SCHEMA, PACK, RECEIPT_SCHEMA,
    SEMANTIC_RECEIPT_SCHEMA, TRIGGER_TABLE_RELATIVE, canonical, digest,
    load_trigger_table, merge_trigger_delivery, receipt_id, semantic_delivery,
    semantic_selector_digest, scheduled_rule_ids as _scheduled_rule_ids, valid_local_identity,
    validate_generalized_receipt,
)
from lib.rule_delivery_shadow import (  # noqa:E402
    WINDOW_SOURCE_PATHS, file_sha256, source_sha256,
)
from lib.claude_rule_delivery_dedupe import should_deliver, transcript_path_digest  # noqa:E402
from lib import rule_routes  # noqa:E402


MAP = REPO / "ops/config/rule-enforcement-map.json"
TRIGGERS_PATH = REPO / TRIGGER_TABLE_RELATIVE
FAILURE_CONTEXT = (
    "RULE PACK PREUSE RESELECTION FAILED: selector_unavailable. "
    "The scheduled action was not blocked or rewritten; Stop telemetry must "
    "treat scheduled-automation as not loaded."
)
GENERALIZED_FAILURE_CONTEXT = (
    "RULE JIT TRIGGER DELIVERY FAILED: selector_unavailable. "
    "One or more matched triggers were not delivered; Stop telemetry must "
    "treat their packs as not loaded."
)
SEMANTIC_FAILURE_CONTEXT = (
    "JEV MESSAGE RULE DELIVERY FAILED: selector_unavailable. "
    "The partner message was not blocked or rewritten; no semantic rule was "
    "treated as loaded."
)
# typesafe_client's state guard is 96k characters. Leave headroom for JSON
# structure and reject above it rather than judging only a prompt's edges while
# issuing a receipt that appears to cover the whole message.
MESSAGE_LIMIT_CHARS = 90_000

# ONE CLOCK FOR THE WHOLE PROMPT HOOK. ops/config/hooks.json kills this hook at
# 20 s, and a killed hook delivers nothing. The two slow steps run in order:
# rule judgment (ops/rule_trigger_delivery.py, with its own 12 s clock) and
# the standing-context door. Both end by
# HOOK_BUDGET_SECONDS after the process started, leaving ~2 s for the
# interpreter and the receipt.
HOOK_BUDGET_SECONDS = 18.0
SELECTOR_RESERVE_SECONDS = 4.0
_HOOK_STARTED = time.monotonic()


def _hook_deadline() -> float:
    return _HOOK_STARTED + HOOK_BUDGET_SECONDS


SELECTOR_REASONS = frozenset({
    "selector delivery plan is not exact",
    "selector did not return every scheduled rule",
    "selector did not return every triggered rule",
    "selector identity is incomplete",
    "selector response was not ok",
    "selector returned a malformed rule",
    "selector returned duplicate or nonbinding rule",
    "selector returned malformed JSON",
    "selector returned nonzero",
    "selector rule pools are malformed",
})


def _failure_reason(exc: BaseException) -> str:
    """Name the cause without ever echoing untrusted exception text.

    An allowlist, not a denylist: redaction that tries to strip secrets out of
    arbitrary text is a guess, and a guess that is wrong once puts a bearer in
    the transcript permanently. `type(exc) is RuntimeError` rather than
    isinstance, so a subclass carrying a coincidentally-matching message
    cannot pass.
    """
    # Main's generalized selector uses a typed taxonomy shared with the
    # semantic rail. Translate only its three closed transport reasons.
    if type(exc) is SelectorError:
        reason = {
            "nonzero": "selector returned nonzero",
            "invalid_json": "selector returned malformed JSON",
            "not_ok": "selector response was not ok",
        }.get(exc.reason)
        if reason is not None:
            return reason
    if type(exc) is RuntimeError and str(exc) in SELECTOR_REASONS:
        return str(exc)
    return f"unexpected {type(exc).__name__}"


def _failed(base: str, exc: BaseException) -> dict:
    """The fixed non-blocking failure line, plus the one safe word for why."""
    return _context(f"{base} Cause: {_failure_reason(exc)}.")


def scheduled_rule_ids() -> list[str]:
    """Expose the current reviewed-map membership for the hook and its gate."""
    return _scheduled_rule_ids(REPO)


def _extract_command(tool_input: object) -> str | None:
    if isinstance(tool_input, dict):
        value = tool_input.get("command")
        if isinstance(value, str):
            return value
    return None


def _extract_paths(tool_input: object) -> list[str]:
    # Share the Codex apply_patch and ordinary file-path parser with the
    # declarative route rail. A patch's Move to destination binds too.
    return rule_routes.call_paths(tool_input)


def _serialized_payload(tool_name: str, tool_input: object) -> str:
    try:
        blob = json.dumps(tool_input, sort_keys=True, ensure_ascii=False)
    except (TypeError, ValueError):
        blob = str(tool_input)
    return f"{tool_name}\n{blob}"


def _row_matches(tool_name: str, tool_input: object, row: dict) -> bool:
    kind = row.get("kind")
    pattern = row.get("pattern")
    if not isinstance(pattern, str):
        return False
    try:
        if kind == "verb":
            if re.search(pattern, tool_name):
                return True
            if tool_name.startswith(("mcp__carr__", "mcp__carr_records__")):
                prefix, verb = tool_name.rsplit("__", 1)
                return re.search(pattern, prefix + "__" + verb.replace("_", "-")) is not None
            return False
        if kind == "bash_family":
            if tool_name not in {"Bash", "functions.exec"}:
                return False
            command = _extract_command(tool_input)
            return command is not None and re.search(pattern, command, re.I) is not None
        if kind == "path_pattern":
            return any(fnmatch.fnmatch(path, pattern) for path in _extract_paths(tool_input))
        if kind == "content_regex":
            # Replaced by Jev at UserPromptSubmit. Keeping this branch explicit
            # makes the replacement property visible while the compiled table
            # retains historical rows for audit and rollback.
            return False
    except re.error:
        return False
    return False


def matched_triggers(payload: dict) -> list[dict]:
    """Every compiled trigger row this exact PreToolUse call structurally hits."""
    if payload.get("hook_event_name") != "PreToolUse":
        return []
    tool_name = payload.get("tool_name")
    if not isinstance(tool_name, str) or not tool_name:
        return []
    try:
        rows = load_trigger_table(REPO)
    except Exception:
        return []
    tool_input = payload.get("tool_input")
    return [row for row in rows if _row_matches(tool_name, tool_input, row)]


def _nonempty(value: object) -> TypeGuard[str]:
    return isinstance(value, str) and bool(value.strip())


def _client(payload: dict) -> str:
    return "codex" if _nonempty(payload.get("turn_id")) else "claude"


def _matches(payload: dict) -> bool:
    tool_input = payload.get("tool_input")
    return (payload.get("hook_event_name") == "PreToolUse"
            and payload.get("tool_name") in {"Bash", "functions.exec"}
            and isinstance(tool_input, dict)
            and tool_input.get("run_in_background") is True
            and _nonempty(payload.get("session_id"))
            and _nonempty(payload.get("tool_use_id")))


def _selector_args(ids: list[str]) -> str:
    return canonical({"packs": [PACK], "rule_ids": ids}).decode("utf-8")


def _selector_environment() -> dict[str, str]:
    env = dict(os.environ)
    for name in ("DATABASE_URL", "CARR_BREAK_GLASS", "CARR_BREAK_GLASS_REASON",
                 "CARR_MCP_CLIENT_PROFILE"):
        env.pop(name, None)
    return env


def _run_selector(ids: list[str], runner: Callable) -> dict:
    command = [str(REPO / "run.sh"), "call", "standing-context", _selector_args(ids)]
    result = runner(command, cwd=str(REPO), capture_output=True, text=True,
                    timeout=15, check=False, env=_selector_environment())
    if result.returncode != 0:
        raise RuntimeError("selector returned nonzero")
    try:
        response = json.loads(result.stdout)
    except (TypeError, ValueError) as exc:
        raise RuntimeError("selector returned malformed JSON") from exc
    if not isinstance(response, dict) or response.get("ok") is not True:
        raise RuntimeError("selector response was not ok")
    return response


def _validate_selector(response: dict, ids: list[str]):
    identity = response.get("identity")
    if not valid_local_identity(identity):
        raise RuntimeError("selector identity is incomplete")
    delivery = response.get("rule_delivery")
    if (not isinstance(delivery, dict)
            or delivery.get("mode") not in {"shadow", "enforced"}
            or delivery.get("declared_packs") != [PACK]
            or delivery.get("packs_not_found", []) != []):
        raise RuntimeError("selector delivery plan is not exact")
    found: dict[str, str] = {}
    shared = response.get("shared_rules")
    personal = response.get("personal_rules")
    if not isinstance(shared, list) or not isinstance(personal, list):
        raise RuntimeError("selector rule pools are malformed")
    for item in shared + personal:
        if not isinstance(item, dict):
            raise RuntimeError("selector returned a malformed rule")
        short = item.get("id")
        if short in ids:
            if short in found or not _nonempty(item.get("statement")):
                raise RuntimeError("selector returned duplicate or nonbinding rule")
            found[short] = item["statement"]
    if sorted(found) != ids:
        raise RuntimeError("selector did not return every scheduled rule")
    return identity, delivery, [{"id": short, "statement": found[short]} for short in ids]


def _receipt(payload: dict, response: dict, ids: list[str]) -> dict:
    identity, delivery, rules = _validate_selector(response, ids)
    client = _client(payload)
    row = {
        "schema": RECEIPT_SCHEMA,
        "client": client,
        "session_id": payload["session_id"],
        "turn_id": payload.get("turn_id") if client == "codex" else None,
        "tool_use_id": payload["tool_use_id"],
        "tool_name": payload["tool_name"],
        "tool_input_sha256": digest(payload["tool_input"]),
        "pack": PACK,
        "map_digest": file_sha256(MAP),
        "source_digest": source_sha256(REPO),
        "identity": {key: identity[key] for key in (
            "agent_principal_id", "runtime_principal", "sponsoring_human_id")},
        "rule_ids": ids,
        "rules": rules,
        "rule_delivery": {
            "mode": delivery["mode"],
            "declared_packs": [PACK],
            "packs_not_found": [],
        },
    }
    row["receipt_id"] = receipt_id(row)
    return row


def _context(text: str, event: str = "PreToolUse") -> dict:
    return {"hookSpecificOutput": {
        "hookEventName": event,
        "additionalContext": text,
    }}


def _deduped_context(payload: dict, receipt: dict) -> dict | None:
    text = canonical(receipt).decode("utf-8")
    rule_set = {key: receipt.get(key) for key in (
        "schema", "pack", "packs", "trigger_ids", "rule_ids", "rules",
        "map_digest", "source_digest", "triggers_digest", "rule_delivery")}
    if (_client(payload) == "claude"
            and _nonempty(payload.get("transcript_path"))
            and not should_deliver(payload["session_id"],
                                   transcript_path_digest(payload["transcript_path"]),
                                   digest(rule_set), len(text.encode("utf-8")))):
        return None
    return _context(text)


def _scheduled_oversize_notice(ids: list[str]) -> str:
    # The original receipt contract credits the pack only when every rule is
    # present in full. Claude hides oversized additionalContext behind a file
    # preview, so a partial receipt would falsely certify delivery.
    return (
        "RULE PACK PREUSE DELIVERY TOO LARGE: scheduled-automation rules were "
        "NOT delivered. The full receipt exceeds the visible hook context cap. "
        "The tool call was not blocked or changed. Before acting, fetch these "
        "rules with standing-context rule_ids in batches and read their full "
        "text: " + ", ".join(ids) + "."
    )


# ---------------------------------------------------------------------------
# GENERALIZED RAIL (WR-000019 slice S9) — same shape as the four functions
# above, parameterized over the compiled trigger table's (trigger_ids, packs,
# rule_ids) instead of the fixed PACK/scheduled_rule_ids pair. Kept separate
# rather than folded into the originals so the proven rail above never has to
# change to accommodate a shape it was never asked to handle.


def _generalized_selector_args(packs: list[str], ids: list[str]) -> str:
    return canonical({"packs": packs, "rule_ids": ids}).decode("utf-8")


class SelectorError(RuntimeError):
    """A fixed, non-sensitive reason for a failed standing-context call."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def _run_generalized_selector(packs: list[str], ids: list[str], runner: Callable) -> dict:
    command = [str(REPO / "run.sh"), "call", "standing-context",
               _generalized_selector_args(packs, ids)]
    timeout = max(1.0, min(15.0, _hook_deadline() - time.monotonic()))
    result = runner(command, cwd=str(REPO), capture_output=True, text=True,
                    timeout=timeout, check=False, env=_selector_environment())
    if result.returncode != 0:
        raise SelectorError("nonzero")
    try:
        response = json.loads(result.stdout)
    except (TypeError, ValueError) as exc:
        raise SelectorError("invalid_json") from exc
    if not isinstance(response, dict) or response.get("ok") is not True:
        raise SelectorError("not_ok")
    return response


def _validate_generalized_selector(response: dict, packs: list[str], ids: list[str]):
    identity = response.get("identity")
    if not valid_local_identity(identity):
        raise RuntimeError("selector identity is incomplete")
    delivery = response.get("rule_delivery")
    if (not isinstance(delivery, dict)
            or delivery.get("mode") not in {"shadow", "enforced"}
            or sorted(delivery.get("declared_packs") or []) != packs
            or delivery.get("packs_not_found", []) != []):
        raise RuntimeError("selector delivery plan is not exact")
    found: dict[str, str] = {}
    shared = response.get("shared_rules")
    personal = response.get("personal_rules")
    if not isinstance(shared, list) or not isinstance(personal, list):
        raise RuntimeError("selector rule pools are malformed")
    for item in shared + personal:
        if not isinstance(item, dict):
            raise RuntimeError("selector returned a malformed rule")
        short = item.get("id")
        if short in ids:
            if short in found or not _nonempty(item.get("statement")):
                raise RuntimeError("selector returned duplicate or nonbinding rule")
            found[short] = item["statement"]
    if sorted(found) != ids:
        raise RuntimeError("selector did not return every triggered rule")
    return identity, delivery, [{"id": short, "statement": found[short]} for short in ids]


def _generalized_receipt(payload: dict, response: dict, trigger_ids: list[str],
                         packs: list[str], ids: list[str]) -> dict:
    identity, delivery, rules = _validate_generalized_selector(response, packs, ids)
    client = _client(payload)
    row = {
        "schema": GENERALIZED_RECEIPT_SCHEMA,
        "client": client,
        "session_id": payload["session_id"],
        "turn_id": payload.get("turn_id") if client == "codex" else None,
        "tool_use_id": payload["tool_use_id"],
        "tool_name": payload["tool_name"],
        "tool_input_sha256": digest(payload["tool_input"]),
        "trigger_ids": trigger_ids,
        "packs": packs,
        "triggers_digest": file_sha256(TRIGGERS_PATH),
        "map_digest": file_sha256(MAP),
        "source_digest": source_sha256(REPO),
        "identity": {key: identity[key] for key in (
            "agent_principal_id", "runtime_principal", "sponsoring_human_id")},
        "rule_ids": ids,
        "rules": rules,
        "rule_delivery": {
            "mode": delivery["mode"],
            "declared_packs": packs,
            "packs_not_found": [],
        },
    }
    row["receipt_id"] = receipt_id(row)
    return row


def _semantic_adviser(situation: str, session_id: str | None = None) -> list[dict]:
    # Compiled-trigger match, then (human prompts only) one budgeted Jev
    # judgment of at most 8 requests (ops/rule_trigger_delivery.py). The
    # session is the hook payload's own session_id; with none, nothing is
    # deduped or pooled.
    path = REPO / "ops/rule_trigger_delivery.py"
    spec = importlib.util.spec_from_file_location("rule_trigger_delivery_live", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("semantic selector unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    deadline = min(time.monotonic() + module.DEADLINE_SECONDS,
                   _hook_deadline() - SELECTOR_RESERVE_SECONDS)
    return module.advise(situation, session_id=session_id, deadline=deadline)


def _semantic_receipt(payload: dict, response: dict, selected: list[dict],
                      packs: list[str], ids: list[str]) -> dict:
    identity, delivery, rules = _validate_generalized_selector(response, packs, ids)
    client = _client(payload)
    probabilities = {row["id"]: float(row["probability"])
                     for row in selected if row.get("id") in ids}
    model_provenance = {
        row["id"]: {
            "ranking_model": row.get("ranking_model"),
            "binding_model": row.get("binding_model"),
        }
        for row in selected if row.get("id") in ids
    }
    if any(not _nonempty(route["binding_model"])
           or (route["ranking_model"] is not None
               and not _nonempty(route["ranking_model"]))
           for route in model_provenance.values()):
        raise RuntimeError("semantic selector omitted model provenance")
    row = {
        "schema": SEMANTIC_RECEIPT_SCHEMA,
        "client": client,
        "session_id": payload["session_id"],
        "turn_id": payload.get("turn_id") if client == "codex" else None,
        "prompt_sha256": digest(payload["prompt"]),
        "packs": packs,
        "corpus_digest": file_sha256(REPO / CORPUS_RELATIVE),
        "selector_digest": semantic_selector_digest(REPO),
        "map_digest": file_sha256(MAP),
        "source_digest": source_sha256(REPO),
        "identity": {key: identity[key] for key in (
            "agent_principal_id", "runtime_principal", "sponsoring_human_id")},
        "rule_ids": ids,
        "rules": rules,
        "probabilities": probabilities,
        "model_provenance": model_provenance,
        "rule_delivery": {
            "mode": delivery["mode"],
            "declared_packs": packs,
            "packs_not_found": [],
        },
    }
    row["receipt_id"] = receipt_id(row)
    return row


def _process_prompt(payload: dict, runner: Callable,
                    adviser: Callable[[str], list[dict]] | None) -> dict | None:
    prompt = payload.get("prompt")
    if (not _nonempty(payload.get("session_id")) or not _nonempty(prompt)
            or (_client(payload) == "codex" and not _nonempty(payload.get("turn_id")))):
        return None
    if len(prompt) > MESSAGE_LIMIT_CHARS:
        return _context("RULE DELIVERY NOT ATTEMPTED: prompt exceeds the context limit.", "UserPromptSubmit")
    failure_stage = "semantic_adviser"
    try:
        selected = (adviser(prompt) if adviser is not None
                    else _semantic_adviser(prompt, payload["session_id"]))
        if not isinstance(selected, list):
            raise RuntimeError("semantic selector returned malformed advice")
        failure_stage = "candidate_selection"
        candidate_ids: list[str] = []
        for row in selected:
            candidate_id = row.get("id")
            if isinstance(candidate_id, str):
                candidate_ids.append(candidate_id)
        ids, packs = semantic_delivery(REPO, candidate_ids)
        if not ids:
            return None
        by_id = {row.get("id"): row for row in selected if isinstance(row, dict)}
        selected = [by_id[short] for short in ids]
        if any(row.get("probability") is None for row in selected):
            raise RuntimeError("semantic selector omitted probability")
        failure_stage = "selector_call"
        response = _run_generalized_selector(packs, ids, runner)
        failure_stage = "selector_response"
        _validate_generalized_selector(response, packs, ids)
        failure_stage = "receipt_assembly"
        receipt = _semantic_receipt(
            payload, response, selected, packs, ids)
        return _context(canonical(receipt).decode("utf-8"), "UserPromptSubmit")
    except Exception as exc:
        if isinstance(exc, SelectorError):
            failure_reason = exc.reason
        elif isinstance(exc, subprocess.TimeoutExpired):
            failure_reason = "timeout"
        elif isinstance(exc, (RuntimeError, TypeError, ValueError, KeyError)):
            failure_reason = "invalid_data"
        else:
            failure_reason = "exception"
        return _context(f"RULE DELIVERY FAILED: {failure_stage} ({failure_reason}); read the applicable rules before acting.", "UserPromptSubmit")


# ---------------------------------------------------------------------------
# ROUTE RAIL (100%-recall design). ops/config/rule-routes.v1.json decides which
# rules a call triggers; the compiled table's rows ride along in the same call.


def load_route_doc() -> dict:
    """The committed route file. Raises rule_routes.RouteFileError when it is
    missing or malformed; process() turns that into a loud notice, never a
    silent fall-through to the table-only rail."""
    return rule_routes.load_routes(REPO)


def routed_rule_ids(payload: dict) -> list[str]:
    """Every rule whose trigger or path route this PreToolUse call hits. Pure:
    no dedupe state is read, so the eval harness can call it directly. Raises
    rule_routes.RouteFileError when the route file cannot be used."""
    if payload.get("hook_event_name") != "PreToolUse":
        return []
    tool_name = payload.get("tool_name")
    if not isinstance(tool_name, str) or not tool_name:
        return []
    doc = load_route_doc()
    return rule_routes.matched_rule_ids(doc, tool_name, payload.get("tool_input"))


def _unroutable_candidates() -> list[str]:
    """When the route file cannot be read: every corpus rule that is not
    delivered at boot (class a) or through a surviving duplicate (class e).
    These are the rules that MIGHT bind a call; the notice names them all."""
    try:
        classes = rule_routes.rule_classes(REPO)
        return sorted(rid for rid in rule_routes.corpus_ids(REPO)
                      if (classes.get(rid) or {}).get("class") not in {"a", "e"})
    except Exception:
        return []


def _route_packs(ids: list[str]) -> list[str]:
    """Packs of the pack-layer rules among ids. Declaring them is what lets the
    door return a pack-only rule (the intro-politics scope) by id."""
    layers = json.loads(MAP.read_text(encoding="utf-8")).get("rule_load_layers") or {}
    packs: set[str] = set()
    for rid in ids:
        row = layers.get(rid)
        if isinstance(row, dict) and row.get("load_layer") == "pack":
            packs.update(p for p in row.get("packs") or () if _nonempty(p))
    return sorted(packs)


def _route_statements(response: dict, packs: list[str], ids: list[str]):
    """Like _validate_generalized_selector, but a routed id the store does not
    return is reported (not_found) instead of failing every other rule."""
    identity = response.get("identity")
    if not valid_local_identity(identity):
        raise RuntimeError("selector identity is incomplete")
    delivery = response.get("rule_delivery")
    if (not isinstance(delivery, dict)
            or delivery.get("mode") not in {"shadow", "enforced"}
            or sorted(delivery.get("declared_packs") or []) != packs
            or delivery.get("packs_not_found", []) != []):
        raise RuntimeError("selector delivery plan is not exact")
    shared = response.get("shared_rules")
    personal = response.get("personal_rules")
    if not isinstance(shared, list) or not isinstance(personal, list):
        raise RuntimeError("selector rule pools are malformed")
    found: dict[str, str] = {}
    for item in shared + personal:
        if not isinstance(item, dict):
            raise RuntimeError("selector returned a malformed rule")
        short = item.get("id")
        if short in ids:
            if short in found or not _nonempty(item.get("statement")):
                raise RuntimeError("selector returned duplicate or nonbinding rule")
            found[short] = item["statement"]
    not_found = sorted(set(ids) - set(found))
    return identity, delivery, found, not_found


def _route_delivery(payload: dict, rows: list[dict], routed: list[str],
                    runner: Callable) -> dict | None:
    tool_name = payload["tool_name"]
    trigger_ids, _table_packs, table_ids = (merge_trigger_delivery(rows) if rows
                                            else ([], [], []))
    candidates = sorted(set(routed) | set(table_ids))
    ids = rule_routes.fresh_ids(payload["session_id"], tool_name, candidates)
    if not ids:
        return None
    try:
        packs = _route_packs(ids)
        response = _run_generalized_selector(packs, ids, runner)
        identity, delivery, found, not_found = _route_statements(response, packs, ids)
    except Exception:
        return _context(rule_routes.notice_door_failure(ids))
    client = _client(payload)
    base = {
        "schema": rule_routes.ROUTE_RECEIPT_SCHEMA,
        "client": client,
        "session_id": payload["session_id"],
        "turn_id": payload.get("turn_id") if client == "codex" else None,
        "tool_use_id": payload["tool_use_id"],
        "tool_name": tool_name,
        "tool_input_sha256": digest(payload["tool_input"]),
        "routes_digest": rule_routes.routes_digest(REPO),
        "triggers_digest": file_sha256(TRIGGERS_PATH),
        "map_digest": file_sha256(MAP),
        "source_digest": source_sha256(REPO),
        "trigger_ids": trigger_ids,
        "route_rule_ids": sorted(set(routed) & set(ids)),
        "packs": packs,
        "identity": {key: identity[key] for key in (
            "agent_principal_id", "runtime_principal", "sponsoring_human_id")},
        "rule_ids": ids,
        "not_found": not_found,
        "instruction": rule_routes.ROUTE_INSTRUCTION,
        "rule_delivery": {"mode": delivery["mode"], "declared_packs": packs,
                          "packs_not_found": []},
    }

    def render(full: list[dict], overflow: list[dict]) -> str:
        row = dict(base, rules=[{"id": r["id"], "statement": r["statement"]} for r in full],
                   overflow=overflow)
        row["receipt_id"] = receipt_id(row)
        return canonical(row).decode("utf-8")

    rules = [{"id": rid, "statement": found[rid]} for rid in sorted(found)]
    full, overflow = rule_routes.fit_rules(rules, render,
                                           always_on=rule_routes.always_on_ids())
    text = render(full, overflow)
    if not rule_routes.within_cap(text):
        # THE CAP HOLDS BY CONSTRUCTION. Even an id-only receipt grows ~50
        # characters per id, so a large enough routed set cannot fit as a
        # receipt at all. Then nothing is claimed as delivered: the compact
        # notice names every id and records nothing for dedupe.
        return _context(rule_routes.notice_too_large(ids))
    rule_routes.record_delivered(payload["session_id"], tool_name, [r["id"] for r in full])
    try:
        from lib.rule_recall import log_delivery
        log_delivery(Path(REPO) / "out/rule-route-delivery.jsonl", json.loads(text)["receipt_id"],
                     [r["id"] for r in full])
    except (OSError, ValueError):
        pass
    return _context(text)


def process(payload: dict, *, runner: Callable = subprocess.run,
            adviser: Callable[[str], list[dict]] | None = None) -> dict | None:
    if payload.get("hook_event_name") == "UserPromptSubmit":
        return _process_prompt(payload, runner, adviser)
    if _matches(payload):
        # Keep the original receipt when it is visible in full. An oversized
        # receipt is not delivery: Claude persists it and shows a preview.
        try:
            ids = scheduled_rule_ids()
            response = _run_selector(ids, runner)
            row = _receipt(payload, response, ids)
            if not rule_routes.within_cap(canonical(row).decode("utf-8")):
                notice = _scheduled_oversize_notice(ids)
                return _context(notice if rule_routes.within_cap(notice)
                                else rule_routes.notice_too_large(ids))
            return _deduped_context(payload, row)
        except Exception as exc:
            # Known selector failures retain their safe cause; arbitrary
            # provider/auth/network exception text never reaches the transcript.
            return _failed(FAILURE_CONTEXT, exc)

    # THE ROUTE AND GENERALIZED RAILS (WR-000019 slice S9). Only reached when
    # the original exact shape did not match, so a background
    # Bash/functions.exec call keeps getting exactly the original behavior
    # above and nothing from these rails layers onto it.
    if not (_nonempty(payload.get("session_id")) and _nonempty(payload.get("tool_use_id"))):
        return None
    rows = matched_triggers(payload)
    # THE ROUTE RAIL. When the route file routes any rule to this call, it
    # owns the delivery (the table's rows ride along in the same door call).
    # Every failure below ends in a fixed notice naming what was not
    # delivered: never an exception, never a silent table-only fall-through.
    try:
        routed = routed_rule_ids(payload)
    except rule_routes.RouteFileError as exc:
        return _route_file_unreadable(payload, rows, runner, exc.reason)
    except Exception:
        return _context(rule_routes.notice_error())
    if routed:
        try:
            return _route_delivery(payload, rows, routed, runner)
        except Exception:
            table = merge_trigger_delivery(rows)[2] if rows else []
            return _context(rule_routes.notice_error(sorted(set(routed) | set(table))))
    return _table_delivery(payload, rows, runner)


def _table_delivery(payload: dict, rows: list[dict], runner: Callable) -> dict | None:
    """The compiled-table (generalized) rail, unchanged."""
    if not rows:
        return None
    try:
        trigger_ids, packs, ids = merge_trigger_delivery(rows)
        response = _run_generalized_selector(packs, ids, runner)
        return _deduped_context(payload,
                                _generalized_receipt(payload, response, trigger_ids, packs, ids))
    except Exception as exc:
        return _failed(GENERALIZED_FAILURE_CONTEXT, exc)


def _route_file_unreadable(payload: dict, rows: list[dict], runner: Callable,
                           reason: str) -> dict:
    """The route file is missing or malformed: announce it loudly on every
    admitted call, naming the rules that may bind, and still deliver whatever
    the table rail delivers, after the notice so a truncated preview keeps it."""
    candidates = _unroutable_candidates()
    notice = rule_routes.notice_file_unreadable(reason, candidates)
    table = _table_delivery(payload, rows, runner)  # never raises: its own fixed notice
    extra = ((table or {}).get("hookSpecificOutput") or {}).get("additionalContext")
    if extra:
        combined = notice + "\n\n" + extra
        if rule_routes.within_cap(combined):
            return _context(combined)
        table_ids = merge_trigger_delivery(rows)[2] if rows else []
        notice = rule_routes.notice_file_unreadable(
            reason, sorted(set(candidates) | set(table_ids)))
    return _context(notice)


HOOK_ERROR_CONTEXT = (
    "RULE DELIVERY HOOK ERROR (internal_error): rule-pack-preuse-reselection failed, so "
    "NO rule was delivered for this step; you have not seen them. Nothing was blocked or "
    "changed. Before you act: call the standing-context verb with no arguments, read each "
    "rule whose subject covers what you are about to do, and follow it; if standing-context "
    "also fails, stop and tell the user the rules could not be read."
)


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0
    if not isinstance(payload, dict):
        return 0
    try:
        output = process(payload)
    except Exception:
        # FAIL OPEN, VISIBLY. A crash would exit 1 with empty stdout: the call
        # proceeds and the model is told nothing. Never echo the exception.
        event = payload.get("hook_event_name")
        output = _context(HOOK_ERROR_CONTEXT,
                          event if event in {"PreToolUse", "UserPromptSubmit"} else "PreToolUse")
    if output is not None:
        print(json.dumps(output, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
