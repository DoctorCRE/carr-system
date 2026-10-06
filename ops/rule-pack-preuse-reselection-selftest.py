#!/usr/bin/env python3
"""Behavioral contract for the shadow-compatible pre-use reselection rail."""
from __future__ import annotations

import contextlib
import copy
import hashlib
import importlib.util
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
from pathlib import Path
from types import SimpleNamespace
from typing import cast


REPO = Path(__file__).resolve().parent.parent


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


rail = load("rule_pack_preuse_reselection", REPO / "hooks/rule-pack-preuse-reselection.py")
contract = load("rule_delivery_preuse_test", REPO / "lib/rule_delivery_preuse.py")
drift = load("rule_pack_drift_preuse_test", REPO / "hooks/rule-pack-drift-gate.py")

# The route rail (ROUTE TRIGGERS) has its own section at the end of this file.
# Every earlier section pins behaviour that existed before the route file, so
# the route file is switched off for them and switched back on for its own.
_ROUTE_DOC_LOADER = rail.load_route_doc
rail.load_route_doc = lambda: {"schema": rail.rule_routes.ROUTES_SCHEMA, "rules": {}}


FAILURES: list[str] = []


def check(name: str, condition: bool, detail: object = "") -> None:
    if condition:
        print(f"PASS  {name}")
    else:
        FAILURES.append(f"{name}: {detail}")
        print(f"FAIL  {name}: {detail}")


MAP = json.loads((REPO / "ops/config/rule-enforcement-map.json").read_text())
EXPECTED_IDS = sorted(
    short for short, row in MAP["rule_load_layers"].items()
    if "scheduled-automation" in row.get("packs", [])
)
MAP_DIGEST = hashlib.sha256(
    (REPO / "ops/config/rule-enforcement-map.json").read_bytes()).hexdigest()
SOURCE_DIGEST = rail.source_sha256(REPO)


def selector_result(*, mode: str = "shadow", ids: list[str] | None = None,
                    declared: list[str] | None = None, unknown: list[str] | None = None,
                    agent: str = "joe-local", runtime: str | None = None,
                    sponsor: str = "joe") -> dict:
    wanted = EXPECTED_IDS if ids is None else ids
    block = {
        "mode": mode,
        "declared_packs": ["scheduled-automation"] if declared is None else declared,
        "would_omit": ["deadbeef"],
    }
    if unknown is not None:
        block["packs_not_found"] = unknown
    return {
        "ok": True,
        "identity": {
            "organization_tenant_id": "carr-internal",
            "sponsoring_human_id": sponsor,
            "agent_principal_id": agent,
            "runtime_principal": agent if runtime is None else runtime,
            "personal_brain_scope": "joe-personal",
            "personal_scope_source": "verified_grant_sponsor",
            "session_capability_profile": "sponsored_agent",
            "operational_profile": "full",
            "human_only_authority": False,
        },
        "shared_rules": [
            {"id": short, "statement": f"binding scheduled rule {short}",
             "human_quote": "reviewed"} for short in wanted
        ],
        "personal_rules": [],
        "rule_delivery": block,
    }


class Runner:
    def __init__(self, result: dict | None = None, *, returncode: int = 0,
                 stderr: str = "", error: Exception | None = None,
                 stdout: str | None = None):
        self.result = selector_result() if result is None else result
        self.returncode = returncode
        self.stderr = stderr
        self.stdout = stdout
        self.error = error
        self.calls: list[tuple[tuple, dict]] = []

    def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        if self.error:
            raise self.error
        return SimpleNamespace(
            returncode=self.returncode,
            stdout=json.dumps(self.result) if self.stdout is None else self.stdout,
            stderr=self.stderr,
        )


def payload(*, tool: str = "Bash", background: object = True,
            client: str = "claude") -> dict:
    row = {
        "hook_event_name": "PreToolUse",
        "cwd": str(REPO),
        "session_id": "session-exact",
        "tool_name": tool,
        "tool_use_id": "tool-exact",
        "tool_input": {
            "command": "sleep 5",
            "description": "wait",
            "run_in_background": background,
        },
    }
    if client == "codex":
        row["turn_id"] = "turn-exact"
        row["permission_mode"] = "default"
    else:
        row["transcript_path"] = "/tmp/claude/session-exact.jsonl"
    return row


def context(output: dict | None) -> str:
    if not output:
        return ""
    return output.get("hookSpecificOutput", {}).get("additionalContext", "")


def receipt(output: dict) -> dict:
    return json.loads(context(output))


# The source map, not a typed count or list, owns membership.
check("scheduled ids derive from the reviewed map",
      rail.scheduled_rule_ids() == EXPECTED_IDS and bool(EXPECTED_IDS),
      rail.scheduled_rule_ids())

# The exact observed event is selected before the unchanged action proceeds.
runner = Runner()
original = payload()
before = copy.deepcopy(original)
output = rail.process(original, runner=runner)
row = receipt(output)
check("exact top-level boolean triggers one selector call", len(runner.calls) == 1)
check("hook leaves the tool payload byte-for-byte equivalent", original == before, original)
expected_args = json.dumps({
    "packs": ["scheduled-automation"], "rule_ids": EXPECTED_IDS,
}, sort_keys=True, separators=(",", ":"))
check("selector uses the sanctioned existing door with exact dynamic ids",
      runner.calls[0][0][0] == [str(REPO / "run.sh"), "call", "standing-context", expected_args],
      runner.calls[0] if runner.calls else "no call")
specific = output.get("hookSpecificOutput", {})
check("cross-client output is context-only and never enforcing",
      specific.get("hookEventName") == "PreToolUse"
      and set(specific) == {"hookEventName", "additionalContext"}
      and not any(key in output for key in ("decision", "reason", "updatedInput")), output)
check("receipt binds exact map/source/tool provenance",
      row["schema"] == rail.RECEIPT_SCHEMA
      and row["map_digest"] == MAP_DIGEST
      and row["source_digest"] == SOURCE_DIGEST
      and row["tool_input_sha256"] == rail.digest(before["tool_input"])
      and row["session_id"] == "session-exact"
      and row["tool_use_id"] == "tool-exact", row)
check("receipt carries every dynamic member and full binding text",
      row["rule_ids"] == EXPECTED_IDS
      and [item["id"] for item in row["rules"]] == EXPECTED_IDS
      and all(item["statement"].startswith("binding scheduled rule") for item in row["rules"]),
      row.get("rules"))

# Claude persists additionalContext over 10,000 characters behind a preview.
# A full receipt beyond that limit is a false delivery claim: the model only
# sees the preview, while Stop telemetry may credit every scheduled rule.
oversize_response = selector_result()
oversize_response["shared_rules"][0]["statement"] = "binding scheduled rule " + "x" * 12_000
oversize_output = rail.process(payload(), runner=Runner(oversize_response))
oversize_text = context(oversize_output)
check("oversize scheduled rail stays within the visible context budget",
      rail.rule_routes.within_cap(oversize_text), len(oversize_text))
check("oversize scheduled rail names every undelivered rule without a receipt",
      oversize_text.startswith("RULE PACK PREUSE DELIVERY TOO LARGE")
      and "NOT delivered" in oversize_text
      and all(short in oversize_text for short in EXPECTED_IDS)
      and "rule-delivery-preuse-reselection/v1" not in oversize_text,
      oversize_text[:160])

dell_output = rail.process(
    payload(), runner=Runner(selector_result(agent="dell-local", sponsor="dell")))
check("sanctioned Dell local identity receives the same rail",
      receipt(dell_output)["identity"] == {
          "agent_principal_id": "dell-local",
          "runtime_principal": "dell-local",
          "sponsoring_human_id": "dell",
      }, receipt(dell_output).get("identity"))

check("Dell receipt passes the exact receipt validator",
      contract.validate_receipt(receipt(dell_output), repo=REPO))
identity_cases = [
    ("mismatched local sponsor", {
        "agent_principal_id": "joe-local", "runtime_principal": "joe-local",
        "sponsoring_human_id": "dell",
    }),
    ("mismatched runtime and agent", {
        "agent_principal_id": "joe-local", "runtime_principal": "codex",
        "sponsoring_human_id": "joe",
    }),
    ("unknown local identity", {
        "agent_principal_id": "some-local", "runtime_principal": "some-local",
        "sponsoring_human_id": "joe",
    }),
    ("non-string local identity", {
        "agent_principal_id": ["joe-local"], "runtime_principal": "joe-local",
        "sponsoring_human_id": "joe",
    }),
]
for label, identity in identity_cases:
    forged = copy.deepcopy(row)
    forged["identity"] = identity
    forged["receipt_id"] = contract.receipt_id(forged)
    check(f"{label} receipt is rejected even with a recomputed receipt id",
          not contract.validate_receipt(forged, repo=REPO), forged)

# Exact booleans and exact tool names only. Text and nested values never fire it.
for label, candidate in [
    ("false", False), ("string true", "true"), ("integer one", 1),
    ("missing", None),
]:
    probe = payload(background=candidate)
    if candidate is None:
        del probe["tool_input"]["run_in_background"]
    fake = Runner()
    check(f"{label} does not trigger", rail.process(probe, runner=fake) is None
          and fake.calls == [])
nested = payload(background=False)
nested["tool_input"]["metadata"] = {"run_in_background": True}
check("nested boolean does not trigger", rail.process(nested, runner=Runner()) is None)
prose = payload(background=False)
prose["tool_input"]["command"] += " # run_in_background=true"
check("command prose does not trigger", rail.process(prose, runner=Runner()) is None)
check("unrelated tool does not trigger",
      rail.process(payload(tool="Read"), runner=Runner()) is None)
check("Codex structured exec receives the same rail",
      receipt(rail.process(payload(tool="functions.exec", client="codex"), runner=Runner()))["client"]
      == "codex")

# Exercise the caller's process() interface, including parsing, validation and
# rendering. Each fixture has an independent expected cause: accepting merely
# any safe cause would allow the diagnostic to collapse to one generic reason.
def check_failure_cases(label: str, call: dict, response: dict, missing_cause: str,
                        base: str) -> None:
    def changed(**patch):
        result = copy.deepcopy(response)
        result.update(patch)
        return result

    delivery = dict(response["rule_delivery"], mode="mystery")
    duplicate = response["shared_rules"] + response["shared_rules"][:1]
    nonbinding = copy.deepcopy(response["shared_rules"])
    nonbinding[0]["statement"] = " "
    identity = response["identity"]
    plan = response["rule_delivery"]
    cases = [
        ("nonzero", Runner(response, returncode=1, stderr="token=SUPER-SECRET"),
         "selector returned nonzero"),
        ("malformed JSON", Runner(response, stdout="SUPER-SECRET{"),
         "selector returned malformed JSON"),
        ("not ok", Runner(changed(ok=False)), "selector response was not ok"),
        ("non-object response", Runner(stdout="[]"), "selector response was not ok"),
        ("identity", Runner(changed(identity={})), "selector identity is incomplete"),
        ("mismatched local sponsor", Runner(changed(identity=dict(identity,
            sponsoring_human_id="dell"))), "selector identity is incomplete"),
        ("mismatched runtime and agent", Runner(changed(identity=dict(identity,
            runtime_principal="codex"))), "selector identity is incomplete"),
        ("unknown local identity", Runner(changed(identity=dict(identity,
            agent_principal_id="some-local", runtime_principal="some-local"))),
         "selector identity is incomplete"),
        ("unknown pack", Runner(changed(rule_delivery=dict(plan,
            packs_not_found=["unknown-pack"]))), "selector delivery plan is not exact"),
        ("extra declared pack", Runner(changed(rule_delivery=dict(plan,
            declared_packs=plan["declared_packs"] + ["unknown-pack"]))),
         "selector delivery plan is not exact"),
        ("delivery plan", Runner(changed(rule_delivery=delivery)),
         "selector delivery plan is not exact"),
        ("rule pools", Runner(changed(personal_rules=None)),
         "selector rule pools are malformed"),
        ("shared rule pool", Runner(changed(shared_rules=None)),
         "selector rule pools are malformed"),
        ("malformed rule", Runner(changed(shared_rules=[None])),
         "selector returned a malformed rule"),
        ("duplicate rule", Runner(changed(shared_rules=duplicate)),
         "selector returned duplicate or nonbinding rule"),
        ("nonbinding rule", Runner(changed(shared_rules=nonbinding)),
         "selector returned duplicate or nonbinding rule"),
        ("missing rule", Runner(changed(shared_rules=response["shared_rules"][:-1])),
         missing_cause),
        ("timeout", Runner(error=subprocess.TimeoutExpired("SUPER-SECRET", 15)),
         "unexpected TimeoutExpired"),
        ("unknown exception", Runner(error=RuntimeError("SUPER-SECRET")),
         "unexpected RuntimeError"),
        ("unknown typed selector reason", Runner(error=rail.SelectorError("SUPER-SECRET")),
         "unexpected SelectorError"),
        ("typed selector subclass", Runner(error=type("SneakySelector", (rail.SelectorError,), {})(
            "nonzero")), "unexpected SneakySelector"),
        ("RuntimeError subclass", Runner(error=type("Sneaky", (RuntimeError,), {})(
            "selector returned nonzero")), "unexpected Sneaky"),
    ]
    for name, fake, cause in cases:
        original_call = copy.deepcopy(call)
        failed = rail.process(call, runner=fake)
        expected = {"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "additionalContext": f"{base} Cause: {cause}.",
        }}
        check(f"{label}: {name} renders its exact redacted nonblocking cause",
              failed == expected and call == original_call and len(fake.calls) == 1,
              failed)


check_failure_cases("scheduled rail", payload(), selector_result(),
                    "selector did not return every scheduled rule", rail.FAILURE_CONTEXT)

# Stop telemetry credits only a platform-proven receipt bound to the exact tool call.
def claude_tool_call() -> dict:
    return {"type": "assistant", "message": {"role": "assistant", "content": [{
        "type": "tool_use", "id": "tool-exact", "name": "Bash",
        "input": before["tool_input"],
    }]}, "sessionId": "session-exact"}


def claude_attachment(text: str) -> dict:
    return {
        "type": "attachment", "sessionId": "session-exact",
        "attachment": {
            "type": "hook_additional_context", "hookEvent": "PreToolUse",
            "hookName": "PreToolUse:Bash", "toolUseID": "tool-exact",
            "content": [text],
        },
    }


def codex_tool_call(tool: str = "functions.exec") -> dict:
    return {"type": "response_item", "payload": {
        "type": "function_call", "call_id": "tool-exact", "name": tool,
        "arguments": json.dumps(before["tool_input"], sort_keys=True,
                                separators=(",", ":")),
        "internal_chat_message_metadata_passthrough": {"turn_id": "turn-exact"},
    }}


def codex_custom_tool_call(*, name: str = "exec", wrapper: str = "response_item",
                           raw: object | None = None) -> dict:
    tool_input = before["tool_input"] if raw is None else raw
    if wrapper == "event_msg":
        return {"type": "event_msg", "payload": {
            "type": "custom_tool_call", "name": name,
            "arguments": tool_input,
        }}
    return {"type": "response_item", "payload": {
        "type": "custom_tool_call", "id": "ctc-exact", "status": "completed",
        "call_id": "tool-exact", "name": name,
        "input": (json.dumps(tool_input, sort_keys=True, separators=(",", ":"))
                  if not isinstance(tool_input, str) else tool_input),
        "internal_chat_message_metadata_passthrough": {"turn_id": "turn-exact"},
    }}


def codex_context(text: str) -> dict:
    return {"type": "response_item", "payload": {
        "type": "message", "role": "developer",
        "content": [{"type": "input_text", "text": text}],
        "internal_chat_message_metadata_passthrough": {"turn_id": "turn-exact"},
    }}


claude_records = [claude_tool_call(), claude_attachment(context(output))]
mode, loaded, _ = drift.delivery_state(claude_records)
check("Claude exact hook envelope credits scheduled automation",
      mode == "shadow" and loaded == ["scheduled-automation"], (mode, loaded))

codex_output = rail.process(payload(tool="functions.exec", client="codex"), runner=Runner())
codex_records = [codex_tool_call(), codex_context(context(codex_output))]
mode, loaded, _ = drift.delivery_state(codex_records)
check("Codex exact developer context credits scheduled automation",
      mode == "shadow" and loaded == ["scheduled-automation"], (mode, loaded))
codex_bash_output = rail.process(payload(tool="Bash", client="codex"), runner=Runner())
mode, loaded, _ = drift.delivery_state([
    codex_tool_call("Bash"), codex_context(context(codex_bash_output)),
])
check("Codex Bash alias receives and proves the same receipt",
      mode == "shadow" and loaded == ["scheduled-automation"], (mode, loaded))

TRIGGERS, MEMBERS, _ = drift.load_packs()
for label, records in [
    ("Claude success", claude_records),
    ("Codex function success", codex_records),
    ("Codex response-item custom exec success",
     [codex_custom_tool_call(), codex_context(context(codex_output))]),
]:
    evaluated = drift.evaluate(records, TRIGGERS, MEMBERS)
    check(f"{label} structurally requires and loads scheduled automation",
          evaluated["needed"] == ["scheduled-automation"]
          and evaluated["loaded"] == ["scheduled-automation"]
          and evaluated["missing"] == [], evaluated)

custom_needed = drift.evaluate([codex_custom_tool_call()], TRIGGERS, MEMBERS)
check("Codex custom-tool background call structurally requires scheduled automation",
      custom_needed["needed"] == ["scheduled-automation"]
      and custom_needed["loaded"] == []
      and custom_needed["missing"] == ["scheduled-automation"], custom_needed)

for label, records in [
    ("Claude no receipt", [claude_tool_call()]),
    ("Claude selector failure", [claude_tool_call(), {
        "type": "attachment", "sessionId": "session-exact",
        "attachment": {
            "type": "hook_additional_context", "hookEvent": "PreToolUse",
            "hookName": "PreToolUse:Bash", "toolUseID": "tool-exact",
            "content": [rail.FAILURE_CONTEXT],
        },
    }]),
    ("Codex function no receipt", [codex_tool_call()]),
    ("Codex response-item custom exec no receipt", [codex_custom_tool_call()]),
    ("Codex event custom exec_command cannot claim an uncorrelated receipt",
     [codex_custom_tool_call(name="exec_command", wrapper="event_msg"),
      codex_context(context(codex_output))]),
]:
    evaluated = drift.evaluate(records, TRIGGERS, MEMBERS)
    check(f"{label} stays needed and missing",
          evaluated["needed"] == ["scheduled-automation"]
          and evaluated["loaded"] == []
          and evaluated["missing"] == ["scheduled-automation"], evaluated)

for label, record in [
    ("unknown custom alias", codex_custom_tool_call(name="other")),
    ("unstructured custom wrapper prose", codex_custom_tool_call(
        raw="tools.exec_command({run_in_background: true})")),
]:
    evaluated = drift.evaluate([record], TRIGGERS, MEMBERS)
    check(f"{label} does not create a structured background requirement",
          evaluated["needed"] == [] and evaluated["missing"] == [], evaluated)

for label, records in [
    ("copied user context", [claude_tool_call(), {
        "type": "user", "message": {"role": "user", "content": context(output)}}]),
    ("wrong Claude hook name", [claude_tool_call(), {
        **claude_attachment(context(output)),
        "attachment": {**claude_attachment(context(output))["attachment"],
                       "hookName": "PreToolUse:Read"}}]),
    ("wrong tool id", [claude_tool_call(), {
        **claude_attachment(context(output)),
        "attachment": {**claude_attachment(context(output))["attachment"],
                       "toolUseID": "tool-other"}}]),
    ("tampered receipt", [claude_tool_call(), claude_attachment(
        context(output).replace("scheduled-automation", "engineering-git", 1))]),
    ("Codex user role", [codex_tool_call(), {
        **codex_context(context(codex_output)),
        "payload": {**codex_context(context(codex_output))["payload"], "role": "user"}}]),
]:
    found = drift.delivery_state(records)
    check(f"{label} does not count as loaded", found[1] == [], found)

malformed = receipt(output)
malformed["unexpected"] = True
found = drift.delivery_state([
    claude_tool_call(),
    claude_attachment(json.dumps(malformed, sort_keys=True, separators=(",", ":"))),
])
check("extra-key additionalContext does not count as loaded",
      found[1] == [], found)
for record_type, message_role in (("user", "user"), ("assistant", "user"),
                                  ("user", "assistant")):
    forged = claude_tool_call()
    forged["type"] = record_type
    forged["message"]["role"] = message_role
    found = drift.delivery_state([forged, claude_attachment(context(output))])
    check(f"Claude {record_type}/{message_role} tool-call provenance refuses",
          found[1] == [], found)

# Config parity and shadow-window source identity travel with the rail.
claude = json.loads((REPO / "ops/config/hooks.json").read_text())
codex = json.loads((REPO / "ops/config/codex-hooks.json").read_text())["hooks"]
command = "hooks/rule-pack-preuse-reselection.py"
claude_rows = [group for group in claude["PreToolUse"]
               if any(command in hook.get("command", "") for hook in group.get("hooks", []))]
codex_rows = [group for group in codex["PreToolUse"]
              if any(command in hook.get("command", "") for hook in group.get("hooks", []))]
CLAUDE_MATCHER = "Bash|Write|Edit|MultiEdit|NotebookEdit|Agent|WebFetch|WebSearch|Artifact|AskUserQuestion|mcp__.*"
CODEX_MATCHER = ".*"  # Codex local tools use canonical names, including apply_patch.
check("Claude wiring is exact and unique, widened for the generalized rail (S9)",
      len(claude_rows) == 1 and claude_rows[0]["matcher"] == CLAUDE_MATCHER)
check("Codex wiring is exact and unique, widened for the generalized rail (S9)",
      len(codex_rows) == 1 and codex_rows[0]["matcher"] == CODEX_MATCHER)
claude_prompt_rows = [group for group in claude["UserPromptSubmit"]
                      if any(command in hook.get("command", "")
                             for hook in group.get("hooks", []))]
codex_prompt_rows = [group for group in codex["UserPromptSubmit"]
                     if any(command in hook.get("command", "")
                            for hook in group.get("hooks", []))]
check("Claude wires the same rule-delivery module once at the partner-message seam",
      len(claude_prompt_rows) == 1)
check("Codex wires the same rule-delivery module once at the partner-message seam",
      len(codex_prompt_rows) == 1)
check("new rail participates in the epoch source digest",
      "hooks/rule-pack-preuse-reselection.py" in rail.WINDOW_SOURCE_PATHS)
check("the compiled trigger table participates in the epoch source digest too",
      "ops/config/rule-jit-triggers.v1.json" in rail.WINDOW_SOURCE_PATHS
      and "ops/rule-jit-compile.py" in rail.WINDOW_SOURCE_PATHS)

# Claude-only compaction-scoped dedupe. Codex and disabled Claude retain the
# exact historical output path; active dedupe requires the continuity config
# digest, whose canonical hooks guarantee reset callbacks are installed.
dedupe = load("claude_rule_delivery_dedupe_test",
              REPO / "lib/claude_rule_delivery_dedupe.py")
runtime_dedupe = __import__("lib.claude_rule_delivery_dedupe", fromlist=["*"])

# ---------------------------------------------------------------------------
# THE DEDUPE WRITER'S DESCRIPTOR OWNERSHIP, proven before the concurrency check
# that depends on it. os.fdopen takes ownership of the descriptor it is handed,
# so closing that integer again after the context exits does not re-close "our"
# file — the kernel hands the lowest free number straight back out, and in a
# process with more than one thread it hands it to somebody else. The close then
# destroys a stranger's file and the stranger fails with EBADF. That is how one
# thread's dedupe write broke the other's, made _locked raise, and sent
# should_deliver down its fail-open path so the same rule set delivered twice.
#
# Modeled deterministically and without threads: a stand-in for the real fdopen
# context opens os.devnull the instant the real context releases its descriptor,
# which reuses that exact number. If _atomic closes the raw descriptor again,
# the stand-in's file is gone and os.fstat on it raises EBADF.


class ReuseProbe:
    """The real fdopen context, plus an unrelated open the moment it lets go."""

    def __init__(self, handle):
        self._handle = handle
        self.owned_fd: int = handle.fileno()
        # Set only once the real context releases its descriptor, so the
        # declared type has to admit both states.
        self.reused_fd: int | None = None

    def __enter__(self):
        self._handle.__enter__()
        return self._handle

    def __exit__(self, *exc_info):
        result = self._handle.__exit__(*exc_info)
        self.reused_fd = os.open(os.devnull, os.O_RDONLY)
        return result

    def write(self, data):
        return self._handle.write(data)

    def flush(self):
        return self._handle.flush()

    def fileno(self):
        return self._handle.fileno()


def descriptor_open(fd: int | None) -> bool:
    if fd is None:
        return False
    try:
        os.fstat(fd)
    except OSError:
        return False
    return True


STATE_VALUE = {"schema_version": 1, "compaction_generation": 3, "digests": ["a" * 64]}
_real_fdopen = os.fdopen
_real_fsync = os.fsync

with tempfile.TemporaryDirectory(prefix="rule-dedupe-fd-") as fd_temp_name:
    fd_temp = Path(fd_temp_name)

    def probing_fdopen(fd, *args, **kwargs):
        fd_probe = ReuseProbe(_real_fdopen(fd, *args, **kwargs))
        PROBES.append(fd_probe)
        return fd_probe

    # 1. Success. The write must land and no descriptor but its own may close.
    PROBES: list[ReuseProbe] = []
    target = fd_temp / "state.json"
    os.fdopen = probing_fdopen
    try:
        dedupe._atomic(target, STATE_VALUE)
    finally:
        os.fdopen = _real_fdopen
    check("the atomic write hands its temp descriptor to exactly one fdopen",
          len(PROBES) == 1, len(PROBES))
    success_probe = PROBES[-1]
    reused_after_success = success_probe.reused_fd
    check("the released temp descriptor is genuinely reused by an unrelated open",
          reused_after_success == success_probe.owned_fd,
          (success_probe.owned_fd, reused_after_success))
    check("a successful atomic write never closes the descriptor fdopen already owned",
          descriptor_open(reused_after_success), reused_after_success)
    if reused_after_success is not None and descriptor_open(reused_after_success):
        os.close(reused_after_success)
    check("the atomic write still replaced the file with exactly the canonical bytes",
          target.read_bytes() == json.dumps(
              STATE_VALUE, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode() + b"\n",
          target.read_bytes())
    check("the replaced file keeps owner-only mode and leaves no temp behind",
          (target.stat().st_mode & 0o777) == 0o600
          and [entry.name for entry in fd_temp.iterdir()] == ["state.json"],
          sorted(entry.name for entry in fd_temp.iterdir()))

    # 2. Failure inside the context. The file object still closes exactly once,
    #    the temp is removed, the target is untouched, and no stranger is hit.
    PROBES = []
    doomed = fd_temp / "never-written.json"

    def failing_fsync(fd):
        raise OSError("simulated fsync failure")

    write_error: OSError | None = None
    os.fdopen = probing_fdopen
    os.fsync = failing_fsync
    try:
        dedupe._atomic(doomed, STATE_VALUE)
    except OSError as error:
        write_error = error
    finally:
        os.fdopen = _real_fdopen
        os.fsync = _real_fsync
    check("a failing atomic write still hands its descriptor to exactly one fdopen",
          len(PROBES) == 1, len(PROBES))
    failure_probe = PROBES[-1]
    reused_after_failure = failure_probe.reused_fd
    check("a write failure propagates rather than reporting a durable write",
          isinstance(write_error, OSError), write_error)
    check("a failed atomic write closes no descriptor twice either",
          reused_after_failure == failure_probe.owned_fd
          and descriptor_open(reused_after_failure),
          (failure_probe.owned_fd, reused_after_failure))
    if reused_after_failure is not None and descriptor_open(reused_after_failure):
        os.close(reused_after_failure)
    check("a failed atomic write leaves no temp file and no half-written target",
          not doomed.exists()
          and [entry.name for entry in fd_temp.iterdir()] == ["state.json"],
          sorted(entry.name for entry in fd_temp.iterdir()))

    # 3. Failure to construct the handle. Ownership never transferred, so the
    #    raw descriptor is _atomic's to close — exactly once — and the temp goes.
    CONSTRUCTION: dict[str, int] = {}

    def refusing_fdopen(fd, *args, **kwargs):
        CONSTRUCTION["fd"] = fd
        raise OSError("simulated fdopen failure")

    construction_error: OSError | None = None
    os.fdopen = refusing_fdopen
    try:
        dedupe._atomic(fd_temp / "unconstructed.json", STATE_VALUE)
    except OSError as error:
        construction_error = error
    finally:
        os.fdopen = _real_fdopen
    check("a handle that cannot be constructed propagates its failure",
          isinstance(construction_error, OSError), construction_error)
    check("the descriptor fdopen never took is closed by the writer that still owns it",
          "fd" in CONSTRUCTION and not descriptor_open(CONSTRUCTION["fd"]), CONSTRUCTION)
    check("a construction failure leaves no temp file and creates no target",
          not (fd_temp / "unconstructed.json").exists()
          and [entry.name for entry in fd_temp.iterdir()] == ["state.json"],
          sorted(entry.name for entry in fd_temp.iterdir()))

with tempfile.TemporaryDirectory(prefix="rule-dedupe-") as temp_name:
    temp = Path(temp_name)
    old_env = dict(os.environ)
    try:
        os.environ["CARR_CLAUDE_CONTINUITY_MODE_FILE"] = str(temp / "mode.json")
        os.environ["CARR_CLAUDE_RULE_DEDUPE_DIR"] = str(temp / "state")
        os.environ["CARR_CLAUDE_RULE_DEDUPE_AUDIT"] = str(temp / "audit.jsonl")
        (temp / "mode.json").write_text(json.dumps({
            "schema_version": 1, "mode": "checkpoint",
            # Prime the process contract before introducing a transient source
            # read failure. The verified receipt must keep every concurrent
            # caller on the same dedupe path after that successful read.
            "config_digest": runtime_dedupe.expected_config_digest(),
        }))
        parallel_outputs: list[dict | None] = []
        barrier = threading.Barrier(2)
        source_barrier = threading.Barrier(2)
        source_attempt = iter((True, False))
        source_lock = threading.Lock()
        real_contract_load = runtime_dedupe.continuity_config.load
        def unstable_contract_load(repo):
            with source_lock:
                fail = next(source_attempt)
            source_barrier.wait()
            if fail:
                raise OSError("transient canonical contract read failure")
            return real_contract_load(repo)
        def invoke(index):
            candidate = payload()
            candidate["tool_use_id"] = f"parallel-{index}"
            barrier.wait()
            parallel_outputs.append(rail.process(candidate, runner=Runner()))
        runtime_dedupe.continuity_config.load = unstable_contract_load
        try:
            threads = [threading.Thread(target=invoke, args=(index,)) for index in range(2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
        finally:
            runtime_dedupe.continuity_config.load = real_contract_load
        check("concurrent identical Claude rule sets inject exactly once",
              sum(output is not None for output in parallel_outputs) == 1,
              parallel_outputs)
        audit_rows = [json.loads(line) for line in (temp / "audit.jsonl").read_text().splitlines()]
        check("dedupe telemetry measures delivered and suppressed bytes",
              sum(row["delivered_bytes"] > 0 for row in audit_rows) == 1
              and sum(row["suppressed_bytes"] > 0 for row in audit_rows) == 1,
              audit_rows)

        check("identical Claude rule set stays suppressed in one generation",
              rail.process(payload(), runner=Runner()) is None)
        check("compaction reset allows the same Claude rule set once more",
              dedupe.reset("session-exact", dedupe.transcript_path_digest(
                  "/tmp/claude/session-exact.jsonl"))
              and rail.process(payload(), runner=Runner()) is not None)

        parent = payload()
        parent["session_id"] = "shared-native-session"
        parent["transcript_path"] = "/tmp/claude/shared-native-session.jsonl"
        subagent = copy.deepcopy(parent)
        subagent["tool_use_id"] = "subagent-tool"
        subagent["transcript_path"] = "/tmp/claude/subagents/agent-leaf.jsonl"
        check("parent leaf receives its first rule set",
              rail.process(parent, runner=Runner()) is not None)
        check("subagent leaf independently receives the same rule set",
              rail.process(subagent, runner=Runner()) is not None)
        check("subagent repeat is suppressed within only that leaf",
              rail.process(subagent, runner=Runner()) is None)
        check("subagent compaction reset does not reset the parent leaf",
              dedupe.reset("shared-native-session", dedupe.transcript_path_digest(
                  subagent["transcript_path"]))
              and rail.process(parent, runner=Runner()) is None
              and rail.process(subagent, runner=Runner()) is not None)

        baseline_receipt = receipt(output)
        # Claim the baseline receipt, then independently vary every provenance
        # component that is required to invalidate a prior dedupe claim.
        session = "provenance-change"
        base_payload = payload()
        base_payload["session_id"] = session
        check("baseline provenance set delivers",
              rail._deduped_context(base_payload, baseline_receipt) is not None)
        for field in ("source_digest", "map_digest"):
            changed = copy.deepcopy(baseline_receipt)
            changed[field] = "f" * 64
            check(f"changed {field} reinjects Claude rules",
                  rail._deduped_context(base_payload, changed) is not None)
        changed_trigger = copy.deepcopy(baseline_receipt)
        changed_trigger["schema"] = rail.GENERALIZED_RECEIPT_SCHEMA
        changed_trigger["trigger_ids"] = ["changed-trigger"]
        check("changed trigger digest reinjects Claude rules",
              rail._deduped_context(base_payload, changed_trigger) is not None)

        codex_candidate = payload(client="codex")
        first_codex = rail.process(codex_candidate, runner=Runner())
        codex_candidate["tool_use_id"] = "codex-repeat"
        second_codex = rail.process(codex_candidate, runner=Runner())
        check("Codex delivery remains byte-present on every matching call",
              first_codex is not None and second_codex is not None)
    finally:
        os.environ.clear()
        os.environ.update(old_env)


# ===========================================================================
# GENERALIZED RAIL (WR-000019 slice S9) — the declarative trigger-table path.
# The tests above are entirely unchanged and still exercise the ORIGINAL,
# single-pack scheduled-automation rail alone; everything below is new.

TRIGGER_TABLE = json.loads((REPO / "ops/config/rule-jit-triggers.v1.json").read_text())
TRIGGER_ROWS = {row["trigger_id"]: row for row in TRIGGER_TABLE["triggers"]}
MAX_PER_TRIGGER = TRIGGER_TABLE["max_rules_per_trigger"]


def gen_payload(*, tool: str, tool_input: dict, client: str = "claude",
               session: str = "g-session", tool_use_id: str = "g-tool") -> dict:
    row = {
        "hook_event_name": "PreToolUse",
        "cwd": str(REPO),
        "session_id": session,
        "tool_name": tool,
        "tool_use_id": tool_use_id,
        "tool_input": tool_input,
    }
    if client == "codex":
        row["turn_id"] = "g-turn"
        row["permission_mode"] = "default"
    else:
        row["transcript_path"] = f"/tmp/claude/{session}.jsonl"
    return row


def gen_selector_result(*, packs: list[str], ids: list[str], mode: str = "shadow",
                        agent: str = "joe-local", sponsor: str = "joe") -> dict:
    return {
        "ok": True,
        "identity": {
            "organization_tenant_id": "carr-internal", "sponsoring_human_id": sponsor,
            "agent_principal_id": agent, "runtime_principal": agent,
            "personal_brain_scope": "joe-personal",
            "personal_scope_source": "verified_grant_sponsor",
            "session_capability_profile": "sponsored_agent", "operational_profile": "full",
            "human_only_authority": False,
        },
        "shared_rules": [
            {"id": short, "statement": f"binding jit rule {short}", "human_quote": "reviewed"}
            for short in ids
        ],
        "personal_rules": [],
        "rule_delivery": {"mode": mode, "declared_packs": packs, "would_omit": ["deadbeef"],
                          "packs_not_found": []},
    }


def find_row(*, kind: str, contains: str, source: str | None = None):
    # The rows these checks were written against are the reviewed ones; the
    # Jev-compiled rows (source jev_compiled) have their own checks below.
    for row in TRIGGER_ROWS.values():
        if (row["kind"] == kind and contains in row["pattern"]
                and (row.get("source") == source if source
                     else row.get("source") != "jev_compiled")):
            return row
    raise AssertionError(f"no compiled {kind} trigger contains {contains!r}")


council_row = find_row(kind="verb", contains="^Agent$")
gitpush_row = find_row(kind="bash_family", contains="git")
path_row = find_row(kind="path_pattern", contains="hooks/")
governance_fallback_row = find_row(kind="content_regex", contains="doctrine")

# Non-match: an ordinary, keyword-free call matches nothing and injects nothing.
neutral = gen_payload(tool="Read", tool_input={"limit": 5})
check("matched_triggers is empty for a neutral, keyword-free call",
      rail.matched_triggers(neutral) == [])
neutral_runner = Runner()
check("process() returns None (no injection, no selector call) for a non-match",
      rail.process(neutral, runner=neutral_runner) is None and neutral_runner.calls == [])

# Verb match injects: the Agent tool exactly matches the council trigger.
agent_call = gen_payload(tool="Agent", tool_input={"description": "spawn helper", "prompt": "zzz"})
# Rule ede4b241 (cloud model choice) adds a second verb trigger on the same
# dispatch moment, so an Agent call hits the council trigger AND the model-choice
# trigger; the council trigger alone still carries its own five rules.
model_choice_row = next(row for row in TRIGGER_ROWS.values()
                        if row["kind"] == "verb" and "ede4b241" in row["rule_ids"])
check("model-choice trigger delivers only rule ede4b241 via delegation-council",
      model_choice_row["rule_ids"] == ["ede4b241"]
      and model_choice_row["packs"] == ["delegation-council"])
check("matched_triggers finds the council and model-choice verb triggers for an Agent call",
      sorted(r["trigger_id"] for r in rail.matched_triggers(agent_call))
      == sorted([council_row["trigger_id"], model_choice_row["trigger_id"]]))
agent_rows = [council_row, model_choice_row]
agent_packs = sorted({p for r in agent_rows for p in r["packs"]})
agent_ids = sorted({i for r in agent_rows for i in r["rule_ids"]})
agent_trigger_ids = sorted(r["trigger_id"] for r in agent_rows)
agent_runner = Runner(gen_selector_result(packs=agent_packs, ids=agent_ids))
agent_output = rail.process(agent_call, runner=agent_runner)
agent_row = json.loads(context(agent_output))
check("verb-match Agent call fires exactly one selector call",
      len(agent_runner.calls) == 1)
check("generalized selector call declares the matched triggers' merged packs and rule_ids",
      agent_runner.calls[0][0][0] == [
          str(REPO / "run.sh"), "call", "standing-context",
          json.dumps({"packs": agent_packs, "rule_ids": agent_ids},
                     sort_keys=True, separators=(",", ":"))])
check("generalized receipt uses the new schema and passes its own validator",
      agent_row["schema"] == rail.GENERALIZED_RECEIPT_SCHEMA
      and contract.validate_generalized_receipt(agent_row, repo=REPO))
check("generalized receipt binds exactly the matched triggers, packs, and rule_ids",
      agent_row["trigger_ids"] == agent_trigger_ids
      and agent_row["packs"] == agent_packs
      and agent_row["rule_ids"] == agent_ids)
# The cap lives per trigger in the compiler (lib/rule_delivery_preuse.py,
# merge_trigger_delivery): an Agent call hits two triggers, so the merged set
# may exceed one trigger's cap, and each matched trigger must stay inside it.
check("over-delivery stays inside the compiler's per-trigger cap",
      all(len(r["rule_ids"]) <= MAX_PER_TRIGGER for r in agent_rows)
      and set(agent_row["rule_ids"]) == set(agent_ids))
check("original scheduled-automation receipt fields are absent from the generalized shape",
      "pack" not in agent_row and "triggers_digest" in agent_row)

# path_pattern match: a hooks/ write hits the structural extra trigger.
write_call = gen_payload(tool="Write", tool_input={"file_path": "hooks/preuse.py",
                                                   "content": "print(1)\n"})
check("matched_triggers finds the hooks/ path_pattern trigger for a Write call",
      [r["trigger_id"] for r in rail.matched_triggers(write_call)] == [path_row["trigger_id"]])
write_output = rail.process(
    write_call, runner=Runner(gen_selector_result(packs=path_row["packs"], ids=path_row["rule_ids"])))
write_row = json.loads(context(write_output))
check("path_pattern match delivers exactly the structural extra rule",
      write_row["rule_ids"] == path_row["rule_ids"] and write_row["packs"] == path_row["packs"])
# Cloud model choice fires on a cloud-session dispatch and stays silent on
# routine reads, including the read-only calls of the same remote server.
for dispatch_tool in ("mcp__Claude_Code_Remote__create_session",
                      "mcp__Claude_Code_Remote__create_trigger"):
    dispatch_call = gen_payload(tool=dispatch_tool, tool_input={"prompt": "fix the bug", "model": "sonnet"})
    check(f"{dispatch_tool} hits the model-choice trigger",
          model_choice_row["trigger_id"]
          in [r["trigger_id"] for r in rail.matched_triggers(dispatch_call)])
model_runner = Runner(gen_selector_result(packs=model_choice_row["packs"], ids=model_choice_row["rule_ids"]))
model_output = rail.process(
    gen_payload(tool="mcp__Claude_Code_Remote__create_session",
                tool_input={"prompt": "fix the bug", "model": "sonnet"}), runner=model_runner)
model_receipt = json.loads(context(model_output))
check("a cloud-session dispatch delivers rule ede4b241 in one selector call",
      len(model_runner.calls) == 1 and "ede4b241" in model_receipt["rule_ids"]
      and model_receipt["trigger_ids"] == [model_choice_row["trigger_id"]], model_receipt)
for routine_tool, routine_input in (
        ("Read", {"file_path": "README.md"}), ("Grep", {"pattern": "model"}),
        ("Glob", {"pattern": "*.py"}), ("Bash", {"command": "git status"}),
        ("mcp__Claude_Code_Remote__list_sessions", {}),
        ("mcp__Claude_Code_Remote__get_session", {"session_id": "x"}),
        ("mcp__Claude_Code_Remote__list_repos", {})):
    routine_hits = [r["trigger_id"] for r in
                    rail.matched_triggers(gen_payload(tool=routine_tool, tool_input=routine_input))]
    check(f"{routine_tool} stays silent for the model-choice trigger",
          model_choice_row["trigger_id"] not in routine_hits, routine_hits)
silent_runner = Runner()
check("a routine Read delivers nothing and makes no selector call",
      rail.process(gen_payload(tool="Read", tool_input={"file_path": "README.md"}),
                   runner=silent_runner) is None and silent_runner.calls == [])
missing_session = gen_payload(tool="Agent", tool_input={"description": "spawn helper", "prompt": "zzz"})
missing_session["session_id"] = ""
missing_session_runner = Runner()
check("generalized rail refuses a call with no session_id even though it structurally matches",
      rail.process(missing_session, runner=missing_session_runner) is None
      and missing_session_runner.calls == [])
missing_tool_use = gen_payload(tool="Agent", tool_input={"description": "spawn helper", "prompt": "zzz"})
del missing_tool_use["tool_use_id"]
missing_tool_use_runner = Runner()
check("generalized rail refuses a call with no tool_use_id even though it structurally matches",
      rail.process(missing_tool_use, runner=missing_tool_use_runner) is None
      and missing_tool_use_runner.calls == [])
non_hooks_write = gen_payload(tool="Write", tool_input={"file_path": "lib/plain.py",
                                                        "content": "print(1)\n"})
check("a Write outside hooks/ does not match the path_pattern trigger",
      path_row["trigger_id"] not in
      [r["trigger_id"] for r in rail.matched_triggers(non_hooks_write)])

# Semantic keyword matching is replaced, not layered. Tool payload prose no
# longer fires content_regex rows; Jev sees the partner message at
# UserPromptSubmit instead. Structural verb, command-family and path rows stay
# deterministic because those are facts, not judgments.
gov_call = gen_payload(tool="Bash",
                      tool_input={"command": "echo checking the retrieval doctrine index"})
check("content_regex no longer fires on tool payload prose",
      governance_fallback_row["trigger_id"] not in
      [r["trigger_id"] for r in rail.matched_triggers(gov_call)])
gov_call_upper = gen_payload(tool="Bash",
                            tool_input={"command": "echo checking the retrieval DOCTRINE Index"})
check("content_regex replacement is independent of keyword case",
      governance_fallback_row["trigger_id"] not in
      [r["trigger_id"] for r in rail.matched_triggers(gov_call_upper)])

# The exact bash family remains deterministic without a semantic keyword row
# layering a second answer onto it.
gitpush_call = gen_payload(tool="Bash", tool_input={"command": "git push origin main"})
gitpush_matches = rail.matched_triggers(gitpush_call)
gitpush_ids = {r["trigger_id"] for r in gitpush_matches}
check("a git push Bash command matches its seeded bash_family trigger",
      gitpush_row["trigger_id"] in gitpush_ids)
merged_trigger_ids, merged_packs, merged_rule_ids = contract.merge_trigger_delivery(gitpush_matches)
check("structural merge derives packs/rule_ids from the remaining matched rows",
      merged_rule_ids == sorted({rid for r in gitpush_matches for rid in r["rule_ids"]})
      and merged_packs == sorted({p for r in gitpush_matches for p in r["packs"]}))
check("git push has one structural answer rather than a layered keyword answer",
      gitpush_ids == {gitpush_row["trigger_id"]}, gitpush_ids)
non_bash_gitpush = gen_payload(tool="SomeOtherTool", tool_input={"command": "git push origin main"})
check("bash_family is gated to Bash/functions.exec — the same command on another "
      "tool name does not fire the bash_family trigger",
      gitpush_row["trigger_id"] not in
      [r["trigger_id"] for r in rail.matched_triggers(non_bash_gitpush)])
check("every individual matched row still respects the per-trigger cap",
      all(len(r["rule_ids"]) <= MAX_PER_TRIGGER for r in gitpush_matches))
gitpush_output = rail.process(gitpush_call, runner=Runner(
    gen_selector_result(packs=merged_packs, ids=merged_rule_ids)))
gitpush_row_receipt = json.loads(context(gitpush_output))
check("git push call's receipt reflects the full multi-trigger union",
      gitpush_row_receipt["rule_ids"] == merged_rule_ids
      and gitpush_row_receipt["trigger_ids"] == merged_trigger_ids)

# Partner-message semantic selection. The fake adviser is the test adapter at
# the same seam the production Jev adapter occupies; the standing-context
# runner remains the existing authenticated rule-text adapter.
def prompt_payload(*, client="claude", prompt="we need to improve this source"):
    row = {
        "hook_event_name": "UserPromptSubmit",
        "cwd": str(REPO),
        "session_id": "session-prompt",
        "prompt": prompt,
    }
    if client == "codex":
        row["turn_id"] = "turn-prompt"
    else:
        row["transcript_path"] = "/tmp/claude/session-prompt.jsonl"
    return row


semantic_id = governance_fallback_row["rule_ids"][0]
semantic_packs = MAP["rule_load_layers"][semantic_id]["packs"]


def fake_adviser(_situation):
    return [{"id": semantic_id, "gist": "governance rule",
             "statement": "local candidate text", "probability": 0.91,
             "ranking_model": "jev-test-ranker",
             "binding_model": "jev-test-binder"}]


semantic_runner = Runner(gen_selector_result(packs=semantic_packs, ids=[semantic_id]))
semantic_output = rail.process(prompt_payload(), runner=semantic_runner,
                               adviser=fake_adviser)
semantic_row = json.loads(context(semantic_output))
check("UserPromptSubmit asks Jev once about the partner message",
      semantic_row["schema"] == contract.SEMANTIC_RECEIPT_SCHEMA
      and semantic_row["prompt_sha256"] == rail.digest("we need to improve this source")
      and semantic_row["selector_digest"] == contract.semantic_selector_digest(REPO))
check("semantic candidates are authenticated through standing-context",
      semantic_runner.calls[0][0][0] == [
          str(REPO / "run.sh"), "call", "standing-context",
          json.dumps({"packs": semantic_packs, "rule_ids": [semantic_id]},
                     sort_keys=True, separators=(",", ":"))])
check("semantic receipt uses authoritative text and keeps the Jev probability",
      semantic_row["rules"] == [{"id": semantic_id,
                                  "statement": f"binding jit rule {semantic_id}"}]
      and semantic_row["probabilities"] == {semantic_id: 0.91}
      and semantic_row["model_provenance"] == {
          semantic_id: {"ranking_model": "jev-test-ranker",
                        "binding_model": "jev-test-binder"}})
check("semantic receipt validates through the shared rule-delivery interface",
      contract.validate_semantic_receipt(semantic_row, repo=REPO))

semantic_claude_attachment = {
    "type": "attachment", "sessionId": "session-prompt",
    "attachment": {
        "type": "hook_additional_context", "hookEvent": "UserPromptSubmit",
        "hookName": "UserPromptSubmit", "content": [context(semantic_output)],
    },
}
semantic_claude_prompt = {
    "type": "user", "sessionId": "session-prompt",
    "message": {"role": "user", "content": "we need to improve this source"},
}
mode, loaded, _ = drift.delivery_state([
    semantic_claude_prompt, semantic_claude_attachment])
check("Claude Stop telemetry credits only the validated semantic hook envelope",
      mode == "shadow" and loaded == semantic_packs, (mode, loaded))

replayed_prompt = copy.deepcopy(semantic_claude_prompt)
cast(dict[str, object], replayed_prompt["message"])["content"] = (
    "a different later request")
mode, loaded, _ = drift.delivery_state([replayed_prompt, semantic_claude_attachment])
check("Claude cannot replay a valid semantic receipt onto a different prompt",
      mode is None and loaded == [], (mode, loaded))

codex_semantic = rail.process(
    prompt_payload(client="codex"),
    runner=Runner(gen_selector_result(packs=semantic_packs, ids=[semantic_id])),
    adviser=fake_adviser)
check("Codex semantic receipt binds the native turn",
      json.loads(context(codex_semantic))["turn_id"] == "turn-prompt")
codex_semantic_context = codex_context(context(codex_semantic))
codex_semantic_context["payload"]["internal_chat_message_metadata_passthrough"] = {
    "turn_id": "turn-prompt"}
mode, loaded, _ = drift.delivery_state([codex_semantic_context])
check("Codex Stop telemetry credits the validated semantic developer context",
      mode == "shadow" and loaded == semantic_packs, (mode, loaded))

forged_semantic = copy.deepcopy(semantic_row)
forged_semantic["probabilities"][semantic_id] = 0.01
forged_attachment = copy.deepcopy(semantic_claude_attachment)
cast(dict[str, object], forged_attachment["attachment"])["content"] = [
    json.dumps(forged_semantic)]
mode, loaded, _ = drift.delivery_state([
    semantic_claude_prompt, forged_attachment])
check("tampered semantic context cannot claim a loaded pack",
      mode is None and loaded == [], (mode, loaded))

no_bind_runner = Runner()
no_bind_output = rail.process(
    prompt_payload(prompt="hello"), runner=no_bind_runner,
    adviser=lambda _situation: [])
check("no binding produces no annotation or store call", no_bind_output is None and no_bind_runner.calls == [])

layer0_id = next(short for short, entry in MAP["rule_load_layers"].items()
                 if entry.get("load_layer") == "layer0")
layer0_runner = Runner()
layer0_output = rail.process(
    prompt_payload(), runner=layer0_runner,
    adviser=lambda _situation: [{"id": layer0_id,
                                 "probability": 0.99,
                                 "ranking_model": None,
                                 "binding_model": "jev-test"}])
check("already-loaded rules produce no annotation", layer0_output is None and layer0_runner.calls == [])
failed = rail.process(prompt_payload(client="codex"), runner=Runner(returncode=1, stderr="SUPER-SECRET"), adviser=fake_adviser)
check("rule failure stays visible and redacts provider output",
      "RULE DELIVERY FAILED: selector_call (nonzero)" in context(failed) and "SUPER-SECRET" not in context(failed))

# The verdict cache is keyed on the hook payload's OWN session id — never the
# environment, never a shared default — so the default adviser must carry it.
default_adviser_calls: list[tuple] = []
_real_semantic_adviser = rail._semantic_adviser


def recording_adviser(situation: str, session_id: str | None = None) -> list[dict]:
    default_adviser_calls.append((situation, session_id))
    return []


rail._semantic_adviser = recording_adviser
try:
    rail.process(prompt_payload(prompt="hello"), runner=Runner())
finally:
    rail._semantic_adviser = _real_semantic_adviser
with tempfile.TemporaryDirectory() as fake_repo:
    (Path(fake_repo) / "ops").mkdir()
    (Path(fake_repo) / "ops/rule_trigger_delivery.py").write_text(
        "SEEN = []\n"
        "DEADLINE_SECONDS = 12.0\n"
        "def advise(situation, **kwargs):\n"
        "    SEEN.append(kwargs)\n"
        "    return [kwargs]\n", encoding="utf-8")
    _real_repo = rail.REPO
    rail.REPO = Path(fake_repo)
    try:
        forwarded = _real_semantic_adviser("hello", "session-prompt")
    finally:
        rail.REPO = _real_repo
check("the default adviser receives the hook payload's own session id",
      default_adviser_calls == [("hello", "session-prompt")]
      and len(forwarded) == 1 and forwarded[0].get("session_id") == "session-prompt",
      (default_adviser_calls, forwarded))
check("the rule judgment's deadline leaves the selector its reserve of the hook budget",
      isinstance(forwarded[0].get("deadline"), float)
      and forwarded[0]["deadline"] <= rail._hook_deadline() - rail.SELECTOR_RESERVE_SECONDS,
      forwarded)

check("malformed prompt events fail open before either adapter runs",
      rail.process({"hook_event_name": "UserPromptSubmit", "session_id": "x"},
                   runner=Runner(), adviser=fake_adviser) is None)

oversize_adviser_calls: list[str] = []


def oversize_adviser(situation: str) -> list[dict]:
    oversize_adviser_calls.append(situation)
    return []


oversize = rail.process(
    prompt_payload(prompt="x" * (rail.MESSAGE_LIMIT_CHARS + 1)),
    runner=Runner(),
    adviser=oversize_adviser)
check("oversized prompts fail open visibly", "RULE DELIVERY NOT ATTEMPTED" in context(oversize) and oversize_adviser_calls == [])

forged_selector = copy.deepcopy(semantic_row)
forged_selector["selector_digest"] = "0" * 64
forged_selector["receipt_id"] = contract.receipt_id(forged_selector)
check("a receipt from different selector bytes is rejected even when resealed",
      not contract.validate_semantic_receipt(forged_selector, repo=REPO))

# Original rail still wins outright on its own exact shape, even though a
# background Bash git-push command would ALSO structurally match the new
# bash_family trigger above — mutual exclusion per call, by design.
background_gitpush = gen_payload(tool="Bash", tool_input={
    "command": "git push origin main", "run_in_background": True})
bg_runner = Runner()
bg_output = rail.process(background_gitpush, runner=bg_runner)
bg_row = json.loads(context(bg_output))
check("the original exact background shape still takes the original rail, not the generalized one",
      bg_row["schema"] == rail.RECEIPT_SCHEMA and bg_row.get("pack") == rail.PACK)

check_failure_cases(
    "generalized rail", agent_call,
    gen_selector_result(packs=council_row["packs"], ids=council_row["rule_ids"]),
    "selector did not return every triggered rule", rail.GENERALIZED_FAILURE_CONTEXT)

# Tampering with a generalized receipt's content fails validate_generalized_receipt.
tamper_cases = [
    ("wrong trigger_ids", {"trigger_ids": ["0" * 12]}),
    ("wrong packs", {"packs": ["some-other-pack"]}),
    ("extra rule id", {"rule_ids": agent_row["rule_ids"] + ["deadbeef"]}),
    ("unsorted rule_ids (same set, different order)",
     {"rule_ids": list(reversed(agent_row["rule_ids"]))}
     if len(agent_row["rule_ids"]) > 1 else {"rule_ids": agent_row["rule_ids"]}),
    ("duplicated rule_ids", {"rule_ids": agent_row["rule_ids"] + agent_row["rule_ids"][:1]}),
]
for label, patch in tamper_cases:
    forged = copy.deepcopy(agent_row)
    forged.update(patch)
    forged["receipt_id"] = contract.receipt_id(forged)
    check(f"generalized receipt tamper ({label}) fails validate_generalized_receipt",
          not contract.validate_generalized_receipt(forged, repo=REPO))

# A consistent-but-unsorted reordering (rule_ids AND rules moved together, same
# set, same content) isolates the sortedness invariant from the cross-check
# against the compiled table above, which a same-set reorder would not catch.
if len(agent_row["rule_ids"]) > 1:
    reordered = copy.deepcopy(agent_row)
    reordered["rule_ids"] = list(reversed(agent_row["rule_ids"]))
    reordered["rules"] = list(reversed(agent_row["rules"]))
    reordered["receipt_id"] = contract.receipt_id(reordered)
    check("a same-set, consistently-reordered (unsorted) rule_ids/rules pair still fails "
          "validate_generalized_receipt on the sortedness invariant alone",
          not contract.validate_generalized_receipt(reordered, repo=REPO))

# COMPILED-TRIGGER WIRING (2026-09-25). The default message adviser is now the
# compiled-trigger matcher with its budgeted judgment; that it receives the
# session (for its per-session dedupe) is checked above.
prompt_rows = [row for row in TRIGGER_ROWS.values() if row["kind"] == "prompt_regex"]
check("the compiled table carries prompt_regex rows", bool(prompt_rows))
leaky = [row["trigger_id"] for row in prompt_rows
         if rail._row_matches("Bash", {"command": "x " * 3 + row["pattern"]}, row)]
check("prompt_regex rows never fire on a PreToolUse payload", leaky == [], leaky)
check("the trigger table with prompt_regex rows still loads for the PreToolUse rail",
      len(contract.load_trigger_table(REPO)) == len(TRIGGER_TABLE["triggers"]))

# One clock for the whole prompt hook: a hook that has already spent 15 s
# gives the standing-context door only what is left of its 18 s, not 15 s.
import time as _time  # noqa: E402
clock_runner = Runner()
started = rail._HOOK_STARTED
rail._HOOK_STARTED = _time.monotonic() - 15.0
try:
    try:
        rail._run_generalized_selector(["engineering-git"], ["173119a8"], clock_runner)
    except Exception:
        pass
finally:
    rail._HOOK_STARTED = started
given = clock_runner.calls[0][1].get("timeout") if clock_runner.calls else None
check("the standing-context door gets only what is left of the hook's budget",
      isinstance(given, float) and given <= rail.HOOK_BUDGET_SECONDS - 15.0 + 0.5, given)

# ===========================================================================
# ROUTE RAIL — ops/config/rule-routes.v1.json is the source of truth for which
# rules a tool call triggers. Exact matches only; per-rule, per-tool, 30-minute
# session dedupe; every routed rule either delivered in full, listed in
# overflow with a one-line summary, or listed as not found.

rail.load_route_doc = _ROUTE_DOC_LOADER
routes_lib = rail.rule_routes
ROUTES = routes_lib.load_routes(REPO)


def route_result(ids: list[str], *, statement=lambda rid: f"binding routed rule {rid}",
                 missing: tuple[str, ...] = ()) -> dict:
    packs = rail._route_packs(ids)
    result = gen_selector_result(packs=packs, ids=[i for i in ids if i not in missing])
    for row in result["shared_rules"]:
        row["statement"] = statement(row["id"])
    return result


def routed_for(tool: str, tool_input: dict) -> list[str]:
    return rail.routed_rule_ids(gen_payload(tool=tool, tool_input=tool_input))


def rules_routed_by(predicate) -> set[str]:
    return {rid for rid, entry in ROUTES["rules"].items()
            if any(predicate(route) for route in entry["routes"])}


# The Bash route (production route rail) fires on every supported cloud launch
# form, wherever the flag sits among the options, and not on a local session.
def _cli_routed(command):
    return "ede4b241" in routed_for("Bash", {"command": command})


for cloud_command in (
        'claude --remote "fix the bug"',
        'claude --model sonnet --remote "fix the bug"',
        'claude --effort medium --remote',
        'claude --cloud "fix the bug"',
        'claude -p "fix the bug" --environment ccpool_synthetic',
        'claude --remote="fix the bug"',
        'cd repo && claude --model sonnet --cloud "fix; the bug"',
        # A flag may end at a shell terminator, not only at a space or end of line.
        'claude --remote; printf done',
        'claude --remote&& printf done',
        'claude --remote || printf failed',
        '(claude --remote)',
        'claude --cloud | tee launch.log',
        'claude --remote>launch.log',
        'claude --cloud<input.txt',
        'claude --remote\nprintf done'):
    check(f"cloud launch routes the model-choice rule: {cloud_command}",
          _cli_routed(cloud_command))
for local_command in (
        'claude --remote-control',
        'claude --remote-control "name"',
        'claude --model sonnet',
        'claude -p "explain this file"',
        'claude --version && git remote add origin x',
        'git push --remote origin',
        'claude --remote-control; printf done',
        '(claude --remote-control)',
        'claude --cloud-init; printf done',
        'claude --remote-control>launch.log',
        'claude --cloud-init<input.txt',
        'echo done; ls --cloud-init'):
    check(f"local or unrelated command stays silent for the model-choice rule: {local_command}",
          not _cli_routed(local_command))

with tempfile.TemporaryDirectory() as route_tmp:
    saved_env = dict(os.environ)
    os.environ["CARR_RULE_ROUTE_DEDUPE_DIR"] = str(Path(route_tmp) / "dedupe")
    os.environ["CARR_RULES_ALWAYS_ON_FILE"] = str(Path(route_tmp) / "always-on.md")
    try:
        # Exact tool name: an Agent call delivers every rule routed to Agent.
        agent_expected = rules_routed_by(
            lambda r: r["kind"] == "trigger" and "Agent" in r.get("tools", []))
        agent_ids = routed_for("Agent", {"description": "x", "prompt": "y"})
        check("an Agent call routes exactly the rules whose trigger names Agent",
              bool(set(agent_ids) == agent_expected and agent_expected), agent_ids)

        # Verb names reach through all three doors: MCP tool, call-verb, Bash.
        deal_expected = rules_routed_by(
            lambda r: r["kind"] == "trigger" and "new-deal" in r.get("verbs", []))
        via_mcp = set(routed_for("mcp__carr__new-deal", {"name": "x"}))
        via_other_prefix = set(routed_for("mcp__b36e17b6-7e3b__new-deal", {"name": "x"}))
        via_call_verb = set(routed_for("mcp__carr__call-verb", {"verb": "new-deal", "args": {}}))
        via_bash = set(routed_for("Bash", {"command": "./run.sh call new-deal '{\"a\":1}'"}))
        check("a verb routes identically through every MCP prefix, call-verb and run.sh call",
              bool(deal_expected and deal_expected <= via_mcp and via_mcp == via_other_prefix
                   and deal_expected <= via_call_verb and deal_expected <= via_bash),
              (sorted(deal_expected), sorted(via_mcp), sorted(via_call_verb), sorted(via_bash)))
        check("a verb name is matched exactly, never by prefix or similarity",
              not deal_expected & set(routed_for("mcp__carr__new-dealership", {})))

        # Bash command patterns.
        push_ids = set(routed_for("Bash", {"command": "git push -u origin feature"}))
        push_expected = rules_routed_by(
            lambda r: r["kind"] == "trigger"
            and any(re.search(p, "git push -u origin feature", re.I)
                    for p in r.get("bash_patterns", [])))
        check("git push routes exactly the rules whose bash patterns match it",
              bool(push_expected and push_ids == push_expected and "86647daf" in push_ids),
              sorted(push_ids))
        check("a neutral Bash command routes nothing",
              routed_for("Bash", {"command": "ls -la"}) == [])

        # Model-choice advice follows cloud CLI arguments through the production
        # route rail, including options before the dispatch flag.
        for command in (
                'claude --remote "fix the bug"',
                'claude --model sonnet --remote "fix the bug"',
                'claude --effort medium --remote',
                'claude --cloud "fix the bug"',
                'claude --model sonnet --cloud="fix the bug"',
                'claude -p "fix the bug" --environment ccpool_synthetic',
                'claude --environment=ccpool_synthetic -p "fix the bug"',
                'claude -p "fix; the bug" --environment "ccpool_synthetic"',
                '/opt/homebrew/bin/claude --effort medium --cloud "fix the bug"',
                'git status && claude --model sonnet --cloud "fix the bug"'):
            cloud_call = gen_payload(tool="Bash", tool_input={"command": command},
                                     session="cloud-cli-" + command, tool_use_id="cloud-cli")
            cloud_ids = rail.routed_rule_ids(cloud_call)
            check("cloud CLI routes model-choice advice: " + command,
                  "ede4b241" in cloud_ids, cloud_ids)
            cloud_union = sorted(set(cloud_ids) | set(
                contract.merge_trigger_delivery(rail.matched_triggers(cloud_call))[2]))
            cloud_runner = Runner(route_result(cloud_union))
            cloud_output = rail.process(cloud_call, runner=cloud_runner)
            cloud_receipt = json.loads(context(cloud_output)) if cloud_output else {}
            check("cloud CLI delivers model-choice advice in one validated receipt: " + command,
                  len(cloud_runner.calls) == 1
                  and "ede4b241" in cloud_receipt.get("rule_ids", [])
                  and routes_lib.validate_route_receipt(cloud_receipt, repo=REPO))
        for command in (
                'claude --remote-control',
                'claude --model sonnet --remote-control "local session"',
                'claude --remote-control-session-name-prefix local',
                'claude --remote-controlled',
                'claude --cloudy',
                'claude --environment',
                'claude --model sonnet -p "fix the bug"',
                'claude -p "mention --cloud in the answer"',
                'claude --model sonnet; echo --cloud',
                'claude --model sonnet && echo --remote',
                'my-claude --cloud "fix the bug"'):
            check("local CLI or flag prefix stays silent for model choice: " + command,
                  "ede4b241" not in routed_for("Bash", {"command": command}))

        # Path globs, and the path_rule kind carries them.
        hook_ids = set(routed_for("Write", {"file_path": str(REPO / "hooks/x-gate.py"),
                                            "content": ""}))
        check("a write under hooks/ routes the gate-building rule through path_rule",
              "e65efc68" in hook_ids, sorted(hook_ids))
        ci_ids = set(routed_for("Edit", {"file_path": str(REPO / "ops/ci.sh")}))
        check("an edit to ops/ci.sh routes the CI-check rules",
              {"e65efc68", "bd4a6d22"} <= ci_ids, sorted(ci_ids))
        # A path route is a WRITE-moment route: reading a file binds no
        # build-time rule (evals/rule-delivery: routine reads received rules).
        hooks_glob = {"kind": "path_rule", "path_globs": ["*hooks/*.py"]}
        review_route = {"kind": "path_rule", "path_globs": ["*.html"], "read_only": True}
        check("a CARR page read keeps review-time path rules",
              routes_lib.route_matches(review_route, "Read", {"file_path": str(REPO / "dealroom/public/index.html")}))
        review_ids = set(routed_for("Read", {"file_path": str(REPO / "dealroom/public/index.html")}))
        check("an actual CARR page read delivers the visual review rules",
              {"67580c28", "9293d609", "b7ec8f3b"} <= review_ids, sorted(review_ids))
        for reader, args in (("Read", {"file_path": str(REPO / "hooks/x-gate.py")}),
                             ("Grep", {"pattern": "x", "path": str(REPO / "hooks/x-gate.py")}),
                             ("Glob", {"pattern": "*.py", "path": str(REPO / "hooks/x-gate.py")})):
            check(f"a {reader} under hooks/ does not match a path_rule route",
                  not routes_lib.route_matches(hooks_glob, reader, args))
        for writer, args in (("Write", {"file_path": str(REPO / "hooks/x-gate.py"), "content": ""}),
                             ("Edit", {"file_path": str(REPO / "hooks/x-gate.py")}),
                             ("MultiEdit", {"file_path": str(REPO / "hooks/x-gate.py")}),
                             ("NotebookEdit", {"notebook_path": str(REPO / "hooks/x-gate.py")}),
                             ("Bash", {"path": str(REPO / "hooks/x-gate.py")})):
            check(f"a {writer} under hooks/ still matches a path_rule route",
                  routes_lib.route_matches(hooks_glob, writer, args))
        check("an apply_patch header path still matches a path_rule route",
              routes_lib.route_matches(hooks_glob, "apply_patch", {"command":
                  "*** Begin Patch\n*** Add File: hooks/x-gate.py\n+x\n*** End Patch"}))
        path_rules = rules_routed_by(lambda r: r["kind"] == "path_rule")
        check("every path_rule route carries globs and an optional read policy",
              bool(all(set(r) in ({"kind", "path_globs"}, {"kind", "path_globs", "read_only"})
                       and r.get("read_only", True) is True for e in ROUTES["rules"].values()
                       for r in e["routes"] if r["kind"] == "path_rule") and path_rules))

        # Connector glob: the mail draft tool on any server segment.
        draft_ids = set(routed_for("mcp__e16bdf9e-a665__create_draft", {"to": "x"}))
        check("a mail draft on any connector server routes the prospect-writing rules",
              {"725dff46", "ede4c735"} <= draft_ids, sorted(draft_ids))

        # Delivery: one door call with the union of routed ids and table rows.
        call = gen_payload(tool="Agent", tool_input={"description": "spawn", "prompt": "p"},
                           session="route-session", tool_use_id="route-1")
        table_ids = contract.merge_trigger_delivery(rail.matched_triggers(call))[2]
        union = sorted(set(agent_ids) | set(table_ids))
        runner = Runner(route_result(union))
        out = rail.process(call, runner=runner)
        row = json.loads(context(out))
        check("route delivery makes exactly one standing-context call",
              len(runner.calls) == 1, len(runner.calls))
        sent = json.loads(runner.calls[0][0][0][3]) if runner.calls else {}
        check("the door call carries the union of routed and table rule ids",
              sent.get("rule_ids") == union, sent)
        check("route receipt uses its own schema and validates",
              row["schema"] == routes_lib.ROUTE_RECEIPT_SCHEMA
              and routes_lib.validate_route_receipt(row, repo=REPO), row.get("schema"))
        check("every requested rule is delivered in full when it fits",
              [r["id"] for r in row["rules"]] == union and row["overflow"] == []
              and row["not_found"] == [])
        check("the injected context stays under the harness cap",
              routes_lib.context_chars(context(out)) <= routes_lib.CONTEXT_CAP_CHARS)

        # Dedupe: same session, same tool, inside 30 minutes -> nothing new.
        repeat = copy.deepcopy(call)
        repeat["tool_use_id"] = "route-2"
        repeat_runner = Runner(route_result(union))
        check("the same rules are not re-injected for the same tool within the window",
              rail.process(repeat, runner=repeat_runner) is None
              and repeat_runner.calls == [])
        # A different tool re-delivers the rules both tools route.
        other = gen_payload(tool="Bash",
                            tool_input={"command": "python3 ops/dispatch.py send claude-desk x"},
                            session="route-session", tool_use_id="route-3")
        other_ids = rail.routed_rule_ids(other)
        shared = sorted(set(other_ids) & set(agent_ids))
        other_table = contract.merge_trigger_delivery(rail.matched_triggers(other))[2]
        other_union = sorted(set(other_ids) | set(other_table))
        other_out = rail.process(other, runner=Runner(route_result(other_union)))
        other_row = json.loads(context(other_out))
        check("a different tool re-delivers rules already delivered to another tool",
              bool(shared and set(shared) <= {r["id"] for r in other_row["rules"]}),
              (shared, other_row.get("rule_ids")))
        # A different session starts clean.
        fresh = copy.deepcopy(call)
        fresh["session_id"] = "route-session-2"
        check("another session is not deduped by this one",
              rail.process(fresh, runner=Runner(route_result(union))) is not None)
        # The window expires.
        later = routes_lib.fresh_ids("route-session", "Agent", union,
                                     now=_time.time() + routes_lib.DEDUPE_SECONDS + 1)
        check("after 30 minutes the same tool delivers the rules again", later == union)

        # Overflow: more text than the cap. Rules not in the always-on file
        # come first; every rule that does not fit is listed with a summary.
        big = gen_payload(tool="Agent", tool_input={"description": "big", "prompt": "p"},
                          session="overflow-session", tool_use_id="big-1")
        long_text = lambda rid: (f"RULE {rid} FIRST SENTENCE SAYS WHAT BINDS. "  # noqa: E731
                                 + "detail " * 700)
        always_on = union[:2]
        Path(os.environ["CARR_RULES_ALWAYS_ON_FILE"]).write_text(
            "\n".join(f"- {rid}: always on" for rid in always_on))
        big_out = rail.process(big, runner=Runner(route_result(union, statement=long_text)))
        big_text = context(big_out)
        big_row = json.loads(big_text)
        delivered = [r["id"] for r in big_row["rules"]]
        overflowed = [o["id"] for o in big_row["overflow"]]
        check("overflow: the injected context still fits under the 10,000-character cap",
              routes_lib.context_chars(big_text) <= routes_lib.CONTEXT_CAP_CHARS,
              routes_lib.context_chars(big_text))
        check("overflow: no routed rule is dropped — each is full text or listed",
              sorted(delivered + overflowed) == union, (delivered, overflowed))
        check("overflow: some rules overflowed and each carries a one-line summary",
              bool(overflowed and all(o["summary"].startswith("RULE ")
                                      for o in big_row["overflow"])),
              big_row["overflow"][:2])
        check("overflow: rules not in the always-on file are delivered before those that are",
              bool(delivered and (not set(delivered) & set(always_on)
                                  or set(union) - set(always_on) <= set(delivered))), delivered)
        check("overflow: the receipt still validates",
              routes_lib.validate_route_receipt(big_row, repo=REPO))
        check("overflow: only fully delivered rules are recorded for dedupe",
              routes_lib.fresh_ids("overflow-session", "Agent", overflowed) == sorted(overflowed))
        Path(os.environ["CARR_RULES_ALWAYS_ON_FILE"]).unlink()

        # A routed rule the store does not return is listed, never fails the rest.
        nf_call = gen_payload(tool="Agent", tool_input={"description": "nf", "prompt": "p"},
                              session="nf-session", tool_use_id="nf-1")
        nf_row = json.loads(context(rail.process(
            nf_call, runner=Runner(route_result(union, missing=(union[0],))))))
        check("a routed rule the store did not return is listed as not_found",
              nf_row["not_found"] == [union[0]]
              and [r["id"] for r in nf_row["rules"]] == union[1:]
              and routes_lib.validate_route_receipt(nf_row, repo=REPO), nf_row["not_found"])

        # Door failure: the failure context names every rule that did not arrive.
        fail_call = gen_payload(tool="Agent", tool_input={"description": "f", "prompt": "p"},
                                session="fail-session", tool_use_id="fail-1")
        fail_text = context(rail.process(fail_call, runner=Runner(returncode=1)))
        check("a door failure is visible and names every routed rule it could not deliver",
              fail_text.startswith("RULE ROUTE DELIVERY FAILED")
              and "NOT delivered" in fail_text
              and all(rid in fail_text for rid in union), fail_text[:200])
        check("a door failure records nothing as delivered",
              routes_lib.fresh_ids("fail-session", "Agent", union) == union)

        # Route rail owns only calls it routes; the rest keep the old rails.
        check("a call no route hits keeps the generalized/none behaviour",
              rail.routed_rule_ids(gen_payload(tool="Read", tool_input={"limit": 1})) == [])
        check("the scheduled rail still wins for a background Bash call",
              json.loads(context(rail.process(payload(client="codex"),
                                              runner=Runner())))["schema"]
              == rail.RECEIPT_SCHEMA)

        # ------------------------------------------------------------------
        # FAIL OPEN, VISIBLY. Order is Jev's verification_selection ranking
        # (2026-09-26, the chance the failure happens AND silently costs
        # delivery): door failure 0.52 (above), route file missing 0.51,
        # top-level exception 0.46, wrong schema 0.46, 209-rule worst case
        # 0.45, invalid JSON 0.42, empty/null path glob 0.39, non-UTF-8
        # always-on file 0.37, non-string tool entry 0.26.
        fo_tmp = Path(route_tmp) / "fail-open"
        (fo_tmp / "ops/config").mkdir(parents=True)
        fo_file = fo_tmp / routes_lib.ROUTES_RELATIVE
        candidates = rail._unroutable_candidates()

        def unreadable_text(label: str) -> str:
            rail.load_route_doc = lambda: routes_lib.load_routes(fo_tmp)
            try:
                call = gen_payload(tool="Agent", tool_input={"description": label, "prompt": "p"},
                                   session=f"fo-{label}", tool_use_id=f"fo-{label}")
                table = contract.merge_trigger_delivery(rail.matched_triggers(call))
                out = rail.process(call, runner=Runner(gen_selector_result(
                    packs=table[1], ids=table[2])))
                return context(out)
            finally:
                rail.load_route_doc = _ROUTE_DOC_LOADER

        text = unreadable_text("missing")
        check("a missing route file announces loudly instead of falling through silently",
              text.startswith("RULE ROUTE FILE UNREADABLE (missing)")
              and "NOT delivered" in text, text[:160])
        check("the unreadable-file notice names every rule that may bind",
              bool(candidates) and all(rid in text for rid in candidates), len(candidates))
        check("the unreadable-file notice keeps the table rail's delivery after it",
              "\n\n{" in text and text.index("\n\n{") > 0, text[-120:])
        check("the unreadable-file output stays under the cap",
              routes_lib.within_cap(text), routes_lib.context_chars(text))

        # Top-level: any exception in process() is a fixed notice and exit 0.
        real_process = rail.process
        rail.process = lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("secret /path"))
        stdout, saved_stdin = io.StringIO(), sys.stdin
        sys.stdin = io.StringIO(json.dumps(gen_payload(tool="Agent", tool_input={})))
        try:
            with contextlib.redirect_stdout(stdout):
                rc = rail.main()
        finally:
            rail.process, sys.stdin = real_process, saved_stdin
        top = json.loads(stdout.getvalue() or "{}")
        check("a crash anywhere in process() exits 0 with a visible notice",
              rc == 0 and context(top) == rail.HOOK_ERROR_CONTEXT
              and top["hookSpecificOutput"]["hookEventName"] == "PreToolUse", stdout.getvalue())
        check("the crash notice never echoes the exception text",
              "secret" not in stdout.getvalue())

        real_routed = rail.routed_rule_ids
        rail.routed_rule_ids = lambda _p: (_ for _ in ()).throw(RuntimeError("boom"))
        try:
            err = context(rail.process(gen_payload(tool="Agent", tool_input={},
                                                   session="fo-err", tool_use_id="fo-err"),
                                       runner=Runner()))
        finally:
            rail.routed_rule_ids = real_routed
        check("an exception while matching routes is a fixed notice, not a crash",
              err == routes_lib.notice_error(), err[:120])

        real_fit = routes_lib.fit_rules
        routes_lib.fit_rules = lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("boom"))
        try:
            err = context(rail.process(
                gen_payload(tool="Agent", tool_input={"description": "spawn", "prompt": "p"},
                            session="fo-fit", tool_use_id="fo-fit"),
                runner=Runner(route_result(union))))
        finally:
            routes_lib.fit_rules = real_fit
        check("an exception after the door call names every undelivered id",
              err.startswith("RULE ROUTE DELIVERY ERROR") and all(r in err for r in agent_ids),
              err[:160])

        fo_file.write_text(json.dumps({"schema": "rule-routes/v0", "rules": {}}))
        text = unreadable_text("schema")
        check("a route file with the wrong schema announces loudly",
              text.startswith("RULE ROUTE FILE UNREADABLE (wrong_schema)"), text[:80])

        # Worst case: every one of the 209 rules routes to one call.
        every = routes_lib.corpus_ids(REPO)
        all_doc = {"schema": routes_lib.ROUTES_SCHEMA, "rules": {
            rid: {"moment": "x", "routes": [{"kind": "trigger", "tools": ["Agent"]}]}
            for rid in every}}
        rail.load_route_doc = lambda: all_doc
        try:
            for label, statement in (("long", long_text),
                                     ("short", lambda rid: f"binding routed rule {rid}")):
                worst = context(rail.process(
                    gen_payload(tool="Agent", tool_input={"description": label, "prompt": "p"},
                                session=f"worst-{label}", tool_use_id=f"worst-{label}"),
                    runner=Runner(route_result(every, statement=statement))))
                check(f"209-rule worst case ({label} statements) fits the cap by construction",
                      routes_lib.context_chars(worst) <= routes_lib.CONTEXT_CAP_CHARS,
                      routes_lib.context_chars(worst))
                check(f"209-rule worst case ({label} statements) still names every rule",
                      len(every) >= 200 and all(rid in worst for rid in every))
                check(f"209-rule worst case ({label} statements) is a valid receipt or the "
                      "compact not-delivered notice",
                      bool((worst.startswith("RULE ROUTE DELIVERY TOO LARGE")
                            and "NONE was delivered" in worst)
                           or routes_lib.validate_route_receipt(json.loads(worst), repo=REPO)),
                      worst[:120])
            check("the compact notice records nothing as delivered",
                  routes_lib.fresh_ids("worst-long", "Agent", every) == sorted(every))
        finally:
            rail.load_route_doc = _ROUTE_DOC_LOADER

        fo_file.write_text("{not json")
        text = unreadable_text("json")
        check("an invalid-JSON route file announces loudly",
              text.startswith("RULE ROUTE FILE UNREADABLE (invalid_json)")
              and all(rid in text for rid in candidates), text[:80])

        # A malformed single route over-delivers its rule instead of crashing.
        bad_doc = copy.deepcopy(ROUTES)
        bad_doc["rules"]["e65efc68"]["routes"] = [{"kind": "path_rule", "path_globs": [None]}]
        bad_doc["rules"]["bd4a6d22"]["routes"] = [{"kind": "path_rule", "path_globs": [""]}]
        bad_doc["rules"]["86647daf"]["routes"] = [{"kind": "trigger", "tools": [7]}]
        rail.load_route_doc = lambda: bad_doc
        try:
            bad_ids = routed_for("Write", {"file_path": str(REPO / "README.md"), "content": ""})
            bad_out = rail.process(gen_payload(tool="Write", tool_input={
                "file_path": str(REPO / "README.md"), "content": ""},
                session="fo-bad", tool_use_id="fo-bad"), runner=Runner(route_result(bad_ids)))
        finally:
            rail.load_route_doc = _ROUTE_DOC_LOADER
        check("a null or empty path glob does not crash and its rule is still delivered",
              {"e65efc68", "bd4a6d22"} <= set(bad_ids), bad_ids)
        check("a non-string tool entry does not crash and its rule is still delivered",
              "86647daf" in bad_ids, bad_ids)
        check("a malformed route's rule arrives in a valid receipt",
              {"e65efc68", "bd4a6d22", "86647daf"}
              <= {r["id"] for r in json.loads(context(bad_out))["rules"]})

        # A non-UTF-8 always-on file (outside the repo, unguarded by CI).
        Path(os.environ["CARR_RULES_ALWAYS_ON_FILE"]).write_bytes(b"\xff\xfe\x00\xc3(bad")
        try:
            utf = rail.process(gen_payload(tool="Agent", tool_input={"description": "spawn", "prompt": "p"},
                                           session="fo-utf", tool_use_id="fo-utf"),
                               runner=Runner(route_result(union)))
        finally:
            Path(os.environ["CARR_RULES_ALWAYS_ON_FILE"]).unlink()
        check("a non-UTF-8 always-on file still delivers every rule in a valid receipt",
              routes_lib.validate_route_receipt(json.loads(context(utf)), repo=REPO)
              and [r["id"] for r in json.loads(context(utf))["rules"]] == union)

        # A non-string tool NAME in the payload itself.
        odd = gen_payload(tool="Agent", tool_input={}, session="fo-odd", tool_use_id="fo-odd")
        odd["tool_name"] = 7
        try:
            odd_out = rail.process(odd, runner=Runner())
            odd_ok = odd_out is None or "RULE" in context(odd_out)
        except Exception as exc:  # noqa: BLE001 — the test is that nothing escapes
            odd_ok = False
            odd_out = repr(exc)
        check("a non-string tool name in the payload never raises", odd_ok, odd_out)

        # Tamper: a receipt whose partition does not add up fails validation.
        forged = copy.deepcopy(row)
        forged["rules"] = forged["rules"][1:]
        forged["receipt_id"] = contract.receipt_id(forged)
        check("a receipt that silently drops a routed rule fails validation",
              not routes_lib.validate_route_receipt(forged, repo=REPO))
    finally:
        os.environ.clear()
        os.environ.update(saved_env)

if FAILURES:
    print("rule-pack-preuse-reselection-selftest: FAIL")
    for failure in FAILURES:
        print("  " + failure)
    raise SystemExit(1)
print("rule-pack-preuse-reselection-selftest: all cases passed")
