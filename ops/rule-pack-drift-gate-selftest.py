#!/usr/bin/env python3
"""Selftest for hooks/rule-pack-drift-gate.py.

The gate exists to keep rule 347a9ca6 true under scoping, so the cases here are
built around that rule's own evidence: a session whose declared work and actual
work differ. Each case is a transcript, not a mock — the gate's real job is
reading one.

Written before the gate was wired into any Stop set (rule e65efc68: the bar is
highest for anything that can refuse other work).
"""
from __future__ import annotations

import importlib.util
import hashlib
import io
import json
import shutil
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
from lib.rule_delivery_shadow import WINDOW_SOURCE_PATHS, source_sha256  # noqa:E402
SPEC = importlib.util.spec_from_file_location(
    "rule_pack_drift_gate", REPO / "hooks" / "rule-pack-drift-gate.py")
assert SPEC and SPEC.loader
gate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gate)

TRIGGERS, MEMBERS, MAP_DIGEST = gate.load_packs()
FAILURES: list[str] = []

# HOST-EVIDENCE AUDIT IS OPT-IN. The replays near the bottom of this file bind
# native Claude/Codex session transcripts by digest, and those files exist only
# on the evidence-owning machine. That made this suite report a different
# verdict per host: green wherever the files were absent, red wherever they were
# present and no longer matched. Neither verdict was the suite's own to give.
# The pins, the transcripts and the audit's semantics are unchanged — only WHEN
# the audit runs. Ordinary CI is hermetic and reads no local evidence;
# `--verify-host-evidence` runs the audit, and an audit whose evidence has
# changed still FAILS, which is the point of pinning it.
# OBSERVED, CAUSE NOT ESTABLISHED: the bytes of several pinned transcripts on
# the evidence-owning machine differ from the recorded digests, and the change
# is not confined to appended rows. Nothing here diagnoses why, and no
# explanation should be inferred from this suite passing hermetically.
_ARGUMENTS = sys.argv[1:]
_UNKNOWN = [argument for argument in _ARGUMENTS if argument != "--verify-host-evidence"]
if _UNKNOWN:
    # A mistyped flag must never read as a passing audit.
    raise SystemExit(f"unknown argument(s): {' '.join(_UNKNOWN)}")
VERIFY_HOST_EVIDENCE = "--verify-host-evidence" in _ARGUMENTS


def check(label: str, ok: bool, detail: str = "") -> None:
    if not ok:
        FAILURES.append(f"{label}{': ' + detail if detail else ''}")


def host_evidence_required(path: Path) -> bool:
    """Gate one host-evidence replay, and refuse to pass over absent evidence.

    Ordinary CI never reads these files. Under the explicit audit, an absent
    transcript is a failure rather than a skip: an audit that quietly verifies
    nothing is indistinguishable from one that verified everything.
    """
    if not VERIFY_HOST_EVIDENCE:
        return False
    present = path.is_file()
    check(f"host evidence is present to audit: {path.name}", present)
    return present


def user(text: str) -> dict:
    return {"type": "user", "message": {"role": "user",
                                        "content": [{"type": "text", "text": text}]}}


def synthetic_user(text: str, kind: str | None = None) -> dict:
    record = user(text)
    if kind:
        record["origin"] = {"kind": kind}
    return record


def scheduled_user(repo: Path = REPO) -> dict:
    source = (repo / "ops/scheduled-tasks/nightly-record-layer.SKILL.md").read_text()
    body = source.split("---\n", 2)[2].lstrip().replace("{{REPO}}", str(repo))
    preamble = (
        "This is an automated run of a scheduled task. The user is not present "
        "to answer questions. For implementation details, execute autonomously "
        "without asking clarifying questions — make reasonable choices and note "
        "them in your output. \"write\" actions (e.g. MCP tools that send, post, "
        "create, update, or delete), only take them if the task file asks for that "
        "specific action. When in doubt, producing a report of what you found is "
        "the correct output."
    )
    return synthetic_user(
        '<scheduled-task name="nightly-record-layer" '
        'file="/Users/booko/.claude/scheduled-tasks/nightly-record-layer/SKILL.md">\n'
        f"{preamble}\n\n{body}</scheduled-task>"
    )


def assistant_tool(name: str, payload: dict) -> dict:
    return {"type": "assistant", "message": {"role": "assistant", "content": [
        {"type": "tool_use", "id": "t1", "name": name, "input": payload}]}}


def standing_context_result(mode: str, declared: list[str], omit: list[str],
                            packs_not_found: list[str] | None = None,
                            map_digest: str = MAP_DIGEST) -> dict:
    # The REAL payload carries the pack index, and the pack index carries every
    # pack's triggers. A gate that scanned tool results would therefore fire
    # every pack the moment a session booted. It is in this fixture on purpose.
    index = [{"pack": name, "title": name, "triggers": pack["triggers"], "rules": 1}
             for name, pack in json.loads(
                 (REPO / "ops" / "config" / "rule-enforcement-map.json").read_text()
             )["rule_packs"].items()]
    delivery = {"mode": mode, "enforcing": mode == "enforced",
                "declared_packs": declared, "would_omit": omit,
                "map_digest": map_digest,
                "pack_index": index}
    if packs_not_found:
        delivery["packs_not_found"] = packs_not_found
    body = {"ok": True, "rule_delivery": delivery}
    return {"type": "user", "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "t1",
         "content": [{"type": "text", "text": json.dumps(body)}]}]}}


def codex_standing_context_result(mode: str, declared: list[str], omit: list[str]) -> dict:
    """The real Codex JSONL wrapper captured from an mcp_tool_call_end event."""
    body = {"ok": True, "rule_delivery": {"mode": mode,
                                          "enforcing": mode == "enforced",
                                          "declared_packs": declared,
                                          "map_digest": MAP_DIGEST,
                                          "would_omit": omit}}
    return {"type": "event_msg", "payload": {"type": "mcp_tool_call_end", "tool_name": "mcp__carr__standing_context",
            "result": {"Ok": {"content": [
                {"type": "text", "text": json.dumps(body)}]}}}}


def codex_tool(name: str, input_text: str) -> dict:
    return {"type": "response_item", "payload": {"type": "custom_tool_call",
            "name": name, "input": input_text}}


def codex_assistant(text: str) -> dict:
    return {"type": "response_item", "payload": {"type": "message",
            "role": "assistant", "content": [{"type": "output_text", "text": text}]}}


def codex_user(text: str) -> dict:
    return {"type": "response_item", "payload": {"type": "message",
            "role": "user", "content": [{"type": "input_text", "text": text}]}}


def claude_skill_injection(skill_text: str) -> list[dict]:
    """Minimal provenance chain emitted by Claude when a Skill body is injected."""
    prompt = "prompt:human"
    assistant_uuid = "assistant:skill-launch"
    result_uuid = "user:skill-result"
    return [
        {"type": "assistant", "uuid": assistant_uuid,
         "message": {"role": "assistant", "content": [{
             "type": "tool_use", "id": "skill-1", "name": "Skill",
             "input": {"skill": "update-config", "args": "configure the project"}}]}},
        {"type": "user", "uuid": result_uuid, "promptId": prompt,
         "parentUuid": assistant_uuid, "sourceToolAssistantUUID": assistant_uuid,
         "message": {"role": "user", "content": [{
             "type": "tool_result", "tool_use_id": "skill-1",
             "content": "Launching skill: update-config"}]}},
        {"type": "user", "uuid": "user:skill-body", "promptId": prompt,
         "parentUuid": result_uuid,
         "message": {"role": "user", "content": [{
             "type": "text", "text": skill_text}]}},
    ]


def engineering_receipt() -> dict:
    digest = "sha256:" + "a" * 64
    evidence = {"ref": "evidence:blocked", "redaction_class": "metadata_only",
                "content_digest": digest}
    return {
        "schema_version": "engineering-slice-receipt.v1",
        "envelope_digest": digest, "attempt_id": "attempt:2",
        "slice_ref": "slice:tour-foundation-contracts", "plan_digest": digest,
        "attribution": {"actor_ref": "agent:codex", "session_ref": "session:receipt",
                        "adapter_ref": "adapter:codex-desktop"},
        "planned_resource_refs": ["repository:carr-system"],
        "actual_resource_refs": ["repository:carr-system"],
        "planned_component_refs": ["component:evidence-ledger"],
        "actual_component_refs": [],
        "checks": [{"check_ref": "check:foundation", "state": "blocked",
                    "evidence_refs": [evidence]}],
        "outcome": "blocked", "artifact_refs": [], "evidence_refs": [evidence],
        "deviations": [],
        "source_evidence": {"worktree_ref": "worktree:not-created",
                            "branch_ref": "branch:not-created",
                            "source_sha": "not-created", "evidence_refs": [evidence]},
        "reset_reconstruction": {"fresh_session": True,
                                 "inherited_transcript_used": False,
                                 "reconstruction_free": True,
                                 "remediation_action": None},
        "executor_claim": {"claim_state": "executor_claim", "claimed_by": "codex",
                           "claimed_at": "2026-08-26T09:41:27Z"},
        "independent_verification_required": True,
    }


def run(records):
    return gate.evaluate(records, TRIGGERS, MEMBERS)


# ── the pack catalog itself has to be usable ────────────────────────────────
check("every pack in the reviewed map compiles a trigger", len(TRIGGERS) >= 8,
      f"only {len(TRIGGERS)} packs have triggers")
check("every pack has at least one rule in it",
      all(MEMBERS.get(name) for name in TRIGGERS),
      str(sorted(set(TRIGGERS) - set(MEMBERS))))
check("the classifier digest covers the exact parsed map bytes",
      MAP_DIGEST == hashlib.sha256(
          (REPO / "ops/config/rule-enforcement-map.json").read_bytes()).hexdigest())

# The recorder must be installed on every interactive client that consumes the
# standing-context payload. A parity check alone cannot catch a source contract
# that forgot one client: source and machine would agree on the same omission.
claude_hooks = json.loads((REPO / "ops" / "config" / "hooks.json").read_text())
codex_hooks = json.loads((REPO / "ops" / "config" / "codex-hooks.json").read_text())


def stop_commands(document):
    hooks = document.get("hooks", document)
    return [hook.get("command", "")
            for group in hooks.get("Stop", [])
            for hook in group.get("hooks", [])]


check("Claude Stop set carries the rule-pack drift recorder",
      any("hooks/rule-pack-drift-gate.py" in command
          for command in stop_commands(claude_hooks)))
check("Codex Stop set carries the rule-pack drift recorder",
      any("hooks/rule-pack-drift-gate.py" in command
          for command in stop_commands(codex_hooks)))

# ── the case the gate was built for: declared one thing, did another ────────
drifted = run([
    user("clean up the loop board please"),
    assistant_tool("mcp__carr__standing-context", {"packs": ["governance-rules"]}),
    standing_context_result("enforced", ["governance-rules"], ["4a53ff82", "424ba0cc"]),
    assistant_tool("Bash", {"command": "git commit -m 'loop cleanup' && git push"}),
])
check("git work in a loop-cleanup session names the engineering pack",
      "engineering-git" in drifted["missing"], str(drifted))
check("the boot payload's own pack index does not fire every pack",
      set(drifted["needed"]) <= {"engineering-git", "governance-rules"},
      "fired: " + str(drifted["needed"]))
check("and the rule the scoped boot omitted is named as a MISS",
      "4a53ff82" in drifted["missed_rules"], str(drifted["missed_rules"]))
check("the pack that WAS loaded is not reported missing",
      "governance-rules" not in drifted["missing"])
check("the block text names the pack and the words that fired it",
      "engineering-git" in gate.block_reason(drifted)
      and "347a9ca6" in gate.block_reason(drifted))

# ── shadow mode records and never blocks ────────────────────────────────────
shadow = run([
    user("clean up the loop board please"),
    assistant_tool("mcp__carr__standing-context", {}),
    standing_context_result("shadow", [], ["4a53ff82"]),
    assistant_tool("Bash", {"command": "git commit -m x"}),
])
check("shadow mode still computes the miss", shadow["missed_rules"] == ["4a53ff82"],
      str(shadow))
check("shadow mode is reported as shadow", shadow["mode"] == "shadow")

# ── a session that never called the verb is not blocked on a guess ──────────
unknown = run([user("do a git thing"), assistant_tool("Bash", {"command": "git status"})])
check("no standing-context call means no established mode", unknown["mode"] is None)
check("and the observed pack is still recorded for the shadow week",
      "engineering-git" in unknown["needed"])

# ── the loaded pack satisfies the work ──────────────────────────────────────
clean = run([
    user("take a worktree and land the migration"),
    assistant_tool("mcp__carr__standing-context", {"packs": ["engineering-git"]}),
    standing_context_result("enforced", ["engineering-git"], ["424ba0cc"]),
    assistant_tool("Bash", {"command": "git worktree add ../x && ./run.sh migrate"}),
])
check("declaring the pack the work needs leaves nothing missing",
      clean["missing"] == [], str(clean))
check("and a rule omitted from an UNRELATED pack is not counted as a miss",
      clean["missed_rules"] == [], str(clean["missed_rules"]))

# ── only THIS turn is judged ────────────────────────────────────────────────
older = run([
    user("draft an LOI for the Pensacola deal"),
    assistant_tool("Bash", {"command": "echo loi"}),
    user("now just tell me the time"),
    assistant_tool("Bash", {"command": "date -u"}),
])
check("a previous turn's deal work does not follow the session forever",
      "client-deal" not in older["needed"], str(older["needed"]))

# Claude injects a selected Skill body as a user-shaped transcript row after a
# source-linked Skill tool call and its tool result.  That body is instruction
# data, not a second human task or observed work.  Its provenance chain must not
# replace the real human boundary, while later tools remain fully observable.
skill_body = ("# Update Config Skill\nclient contact email override artifact "
              "subagent workflow checkpoint git commit")
skill_turn = [
    assistant_tool("mcp__carr__standing-context", {
        "packs": ["engineering-git", "governance-rules", "source-study"]}),
    standing_context_result(
        "shadow", ["engineering-git", "governance-rules", "source-study"],
        ["1e62c007"]),
    {"type": "user", "uuid": "user:human", "promptId": "prompt:human",
     "origin": {"kind": "human"},
     "message": {"role": "user", "content":
                 "add an allow rule for the staging database door"}},
    *claude_skill_injection(skill_body),
    assistant_tool("Bash", {
        "command": "until test -f /tmp/allow-rule; do sleep 5; done",
        "run_in_background": True, "timeout": 3600000}),
]
skill_result = run(skill_turn)
check("provenance-bound Skill body is not a genuine user boundary",
      gate.current_turn(skill_turn)[0] is skill_turn[2])
check("injected Skill prose does not create domain work",
      set(skill_result["needed"]) == {
          "engineering-git", "governance-rules", "scheduled-automation"},
      str(skill_result))
check("real background work after a Skill injection remains observable",
      skill_result["missing"] == ["scheduled-automation"], str(skill_result))
copied_skill = run([
    user(skill_body),
    assistant_tool("mcp__carr__standing-context", {"packs": []}),
    standing_context_result("shadow", [], ["006a7eaa"]),
])
check("ordinary user Skill-shaped prose is never trusted as injected metadata",
      {"client-deal", "delegation-council", "governance-rules", "joe-comms",
       "joe-development", "records-intake", "surface-doctrine"}
      <= set(copied_skill["needed"]), str(copied_skill))

skill_chain_negatives = []
for label in (
    "mismatched-tool-result", "wrong-result-parent", "wrong-result-source",
    "missing-child-prompt", "missing-result-prompt", "mismatched-prompt",
    "mixed-tool-call", "multiple-skill-calls", "duplicate-tool-results",
    "duplicate-tool-use-id", "missing-tool-use-id", "missing-tool-result-id",
    "assistant-type-swap", "assistant-role-swap", "result-type-swap",
    "result-role-swap",
    "duplicate-record-uuid",
):
    chain = json.loads(json.dumps(claude_skill_injection(skill_body)))
    if label == "mismatched-tool-result":
        chain[1]["message"]["content"][0]["tool_use_id"] = "skill-other"
    elif label == "wrong-result-parent":
        chain[1]["parentUuid"] = "assistant:other"
    elif label == "wrong-result-source":
        chain[1]["sourceToolAssistantUUID"] = "assistant:other"
    elif label == "missing-child-prompt":
        del chain[2]["promptId"]
    elif label == "missing-result-prompt":
        del chain[1]["promptId"]
    elif label == "mismatched-prompt":
        chain[2]["promptId"] = "prompt:other"
    elif label == "mixed-tool-call":
        chain[0]["message"]["content"].append({
            "type": "tool_use", "id": "bash-1", "name": "Bash",
            "input": {"command": "true"}})
    elif label == "multiple-skill-calls":
        chain[0]["message"]["content"].append({
            "type": "tool_use", "id": "skill-2", "name": "Skill",
            "input": {"skill": "other"}})
    elif label == "duplicate-tool-results":
        chain[1]["message"]["content"].append({
            "type": "tool_result", "tool_use_id": "skill-1",
            "content": "duplicate"})
    elif label == "duplicate-tool-use-id":
        chain[0]["message"]["content"].append({
            "type": "tool_use", "id": "skill-1", "name": "Skill",
            "input": {"skill": "other"}})
    elif label == "missing-tool-use-id":
        del chain[0]["message"]["content"][0]["id"]
    elif label == "missing-tool-result-id":
        del chain[1]["message"]["content"][0]["tool_use_id"]
    elif label == "assistant-type-swap":
        chain[0]["type"] = "user"
    elif label == "assistant-role-swap":
        chain[0]["message"]["role"] = "user"
    elif label == "result-type-swap":
        chain[1]["type"] = "assistant"
    elif label == "result-role-swap":
        chain[1]["message"]["role"] = "assistant"
    elif label == "duplicate-record-uuid":
        chain.append(json.loads(json.dumps(chain[1])))
    negative = run([
        user("configure the project"),
        *chain,
        assistant_tool("Bash", {"command": "date -u"}),
    ])
    skill_chain_negatives.append((label, negative))
for label, negative in skill_chain_negatives:
    check(f"invalid Skill provenance {label} stays observable",
          {"client-deal", "delegation-council", "engineering-git",
           "governance-rules", "joe-comms", "joe-development",
           "records-intake", "surface-doctrine"}
          <= set(negative["needed"]), str(negative))

# Only inert machine syntax is normalized.  A negated search glob is not vendor
# work; typed receipt evidence keys are not governance or surface work.  The
# surrounding commands remain visible and still trigger engineering work.
metadata_payload = ("const receipt={schema_version:"
                    "'engineering-slice-receipt.v1',artifact_refs:"
                    "['artifact:red-test'],replacement_checkpoint_ref:"
                    "'checkpoint:reissue'}; tools.exec_command({cmd:\"rg --files "
                    "--glob '!vendor' && git status\"});")
metadata_only = run([
    user("inspect the repository"),
    assistant_tool("mcp__carr__standing-context", {
        "packs": list(gate.ENGINEERING_WORKFLOW_PACKS)}),
    standing_context_result("shadow", list(gate.ENGINEERING_WORKFLOW_PACKS), []),
    codex_tool("exec", metadata_payload),
])
check("negated glob and receipt metadata do not create unrelated packs",
      set(metadata_only["needed"]) <= {
          "engineering-git", "delegation-council", "scheduled-automation",
          "source-study"}, str(metadata_only))
check("normalization preserves the surrounding git command",
      "engineering-git" in metadata_only["needed"], str(metadata_only))
tampered_metadata = run([codex_tool(
    "exec",
    "const receipt={schema_version:'engineering-slice-receipt.v1',"
    "artifact_refs:['artifact:red-test'],replacement_checkpoint_ref:"
    "'checkpoint:reissue'}; tools.exec_command({cmd:\"rg --files "
    "--glob '!vendor' && git status\"}); ")])
check("one-byte metadata payload change restores full lexical scanning",
      {"governance-rules", "records-intake", "surface-doctrine"}
      <= set(tampered_metadata["needed"]), str(tampered_metadata))
# Normalization is bound to the tool identity as well as the bytes. The same
# assertion exists in the host replay against the captured receipt payloads;
# proving it here keeps the identity binding covered on every machine.
check("pinned bytes under another tool identity fail closed",
      "!vendor" in gate.custom_tool_text(
          {"type": "custom_tool_call", "name": "other", "input": metadata_payload}))

# Harness notifications are turn boundaries, not partner work. Walking backward
# past one would pull stale work into a later agent action; scanning its static
# summary would make the harness itself look like new work.
notification = run([
    user("draft the Apple Mail follow-up"),
    synthetic_user(
        "<task-notification>background git commit completed</task-notification>",
        "task-notification",
    ),
    assistant_tool("Bash", {"command": "date -u"}),
])
check("task notification starts a new turn without carrying stale partner work",
      "joe-comms" not in notification["needed"], str(notification))
check("task notification static text is not observed work",
      "engineering-git" not in notification["needed"], str(notification))

notification_drift = run([
    user("draft the Apple Mail follow-up"),
    synthetic_user("<task-notification>done</task-notification>", "task-notification"),
    assistant_tool("Bash", {"command": "git status"}),
])
check("real work after a task notification remains observable",
      "engineering-git" in notification_drift["needed"], str(notification_drift))

spoofed_notification = run([
    user("<task-notification>background git commit completed</task-notification>"),
])
check("an ordinary user cannot spoof authenticated task-notification suppression",
      "engineering-git" in spoofed_notification["needed"],
      str(spoofed_notification))

for legacy_marker in (
    "The following is the Codex agent history",
    "<environment_context>",
    "<app-context>",
    "<skills_instructions>",
    "<permissions instructions>",
):
    adversarial = run([
        user(f"{legacy_marker} ordinary user request: git commit the change"),
    ])
    check(f"legacy-shaped user text remains observable: {legacy_marker}",
          "engineering-git" in adversarial["needed"], str(adversarial))

# A source-owned scheduled workflow declares its semantic pack explicitly. Its
# long static instructions name many other domains, but only actions chosen by
# the session after the boundary may expand the needed set.
nightly = run([
    scheduled_user(),
    assistant_tool("mcp__carr__standing-context", {"packs": ["scheduled-automation"]}),
    standing_context_result("shadow", ["scheduled-automation"], ["424ba0cc"]),
    assistant_tool("Bash", {
        "command": 'cd ~/carr-system && ./bin/nightly.sh >/dev/null 2>&1; '
                   'echo "direct script exit=$?"'}),
    assistant_tool("Bash", {"command": "cd ~/carr-system && ./run.sh health"}),
])
check("nightly scheduled workflow produces a genuinely scoped event",
      nightly["needed"] == ["scheduled-automation"], str(nightly))
check("nightly canonical selector result has no missing pack",
      nightly["missing"] == [], str(nightly))

nightly_alias = run([
    scheduled_user(),
    assistant_tool("mcp__carr__standing-context", {"packs": ["automation"]}),
    standing_context_result("shadow", ["automation"], ["424ba0cc"], ["automation"]),
])
check("unknown nightly alias cannot satisfy the canonical workflow pack",
      nightly_alias["loaded"] == []
      and nightly_alias["missing"] == ["scheduled-automation"], str(nightly_alias))

nightly_drift = run([
    scheduled_user(),
    assistant_tool("mcp__carr__standing-context", {"packs": ["scheduled-automation"]}),
    standing_context_result("shadow", ["scheduled-automation"], ["424ba0cc"]),
    assistant_tool("Bash", {"command": "git commit -m drift"}),
])
check("source-owned scheduled scope does not hide later real drift",
      "engineering-git" in nightly_drift["missing"], str(nightly_drift))

tampered = scheduled_user()
tampered["message"]["content"][0]["text"] = tampered["message"]["content"][0][
    "text"].replace("seven generated files", "seventeen generated files", 1)
tampered_result = run([tampered])
check("changed scheduled instructions lose authoritative static-text exclusion",
      len(tampered_result["needed"]) > 1, str(tampered_result))

with tempfile.TemporaryDirectory() as directory:
    fixture = Path(directory)
    task_path = fixture / "ops/scheduled-tasks/nightly-record-layer.SKILL.md"
    task_path.parent.mkdir(parents=True)
    map_path = fixture / "ops/config/rule-enforcement-map.json"
    map_path.parent.mkdir(parents=True)
    shutil.copyfile(REPO / "ops/config/rule-enforcement-map.json", map_path)
    original_source = (REPO / task_path.relative_to(fixture)).read_text()
    old_repo, old_map = getattr(gate, "REPO"), getattr(gate, "MAP")
    try:
        setattr(gate, "REPO", str(fixture))
        setattr(gate, "MAP", str(map_path))
        for label, declaration in (
            ("unknown", "scheduled-automatio"),
            ("empty", ""),
            ("duplicate", "scheduled-automation,scheduled-automation"),
        ):
            task_path.write_text(
                original_source.replace(
                    "RULE-DELIVERY PACKS: scheduled-automation",
                    f"RULE-DELIVERY PACKS: {declaration}", 1),
                encoding="utf-8")
            record = scheduled_user(fixture)
            check(f"{label} scheduled pack declaration fails closed",
                  gate.scheduled_workflow_packs(record) == []
                  and bool(gate.work_text(record)))
    finally:
        setattr(gate, "REPO", old_repo)
        setattr(gate, "MAP", old_map)

# ── a turn with no pack signal at all writes nothing ────────────────────────
quiet = run([user("what time is it"), assistant_tool("Bash", {"command": "date -u"})])
check("a turn implying no pack and loading none is silent",
      not quiet["needed"] and not quiet["loaded"], str(quiet))

# ── the gate never blocks outside enforced mode, whatever it found ──────────
for mode, should_block in (("shadow", False), ("enforced", True), (None, False)):
    records = [
        user("clean the loop board"),
        assistant_tool("mcp__carr__standing-context", {"packs": ["governance-rules"]}),
    ]
    if mode:
        records.append(standing_context_result(mode, ["governance-rules"], ["4a53ff82"]))
    records.append(assistant_tool("Bash", {"command": "git push"}))
    result = run(records)
    blocks = result["mode"] == "enforced" and bool(result["missing"])
    check(f"mode {mode!r} blocks = {should_block}", blocks == should_block, str(result))

# ── a later bare call does not unload what an earlier one loaded ────────────
reloaded = run([
    user("take a worktree and land the migration"),
    assistant_tool("mcp__carr__standing-context", {"packs": ["engineering-git"]}),
    standing_context_result("enforced", ["engineering-git"], ["424ba0cc"]),
    assistant_tool("Bash", {"command": "git worktree add ../x"}),
    # the ordinary second call: reading one rule's binding text by id
    assistant_tool("mcp__carr__standing-context", {"rule_ids": ["4a53ff82"]}),
    standing_context_result("enforced", [], ["424ba0cc", "4a53ff82"]),
])
check("a bare lookup call does not unload the pack already loaded",
      reloaded["loaded"] == ["engineering-git"], str(reloaded["loaded"]))
check("and so it reports nothing missing", reloaded["missing"] == [], str(reloaded))

# ── Codex's real MCP result wrapper is part of the delivery contract ─────────
codex = run([
    user("take a worktree and land the migration"),
    {"type": "event_msg", "payload": {"type": "mcp_tool_call_begin",
      "server": "carr", "tool": "standing_context",
      "arguments": {"packs": ["engineering-git"]}}},
    codex_standing_context_result("shadow", ["engineering-git"], ["424ba0cc"]),
    {"type": "event_msg", "payload": {"type": "custom_tool_call",
      "name": "exec_command", "arguments": {"cmd": "git worktree add ../x"}}},
])
check("Codex mcp_tool_call_end yields the declared pack",
      codex["loaded"] == ["engineering-git"], str(codex))
check("Codex mcp_tool_call_end yields the scoped omission count",
      codex["would_omit_count"] == 1, str(codex))
check("Codex transcript fixture has no false drift",
      codex["missing"] == [], str(codex))

# A typed Engineering Passport receipt is machine output, not prose. The exact
# event 10529812 was polluted by lexical terms inside a blocked receipt (tour,
# ledger, artifact) even though the receipt said no source or component work
# occurred. Only a schema-exact assistant receipt may be normalized; every
# near-shape and every surrounding tool action remains observable.
receipt = engineering_receipt()
receipt_json = json.dumps(receipt, separators=(",", ":"))
machine_only = run([codex_assistant(receipt_json)])
check("exact assistant engineering receipt is not scanned as prose",
      machine_only["needed"] == [], str(machine_only))

extra = dict(receipt)
extra["unreviewed_extra"] = "tour ledger artifact"
check("extra-key receipt-shaped assistant JSON remains observable",
      "client-deal" in run([codex_assistant(json.dumps(extra))])["needed"])
check("trailing prose after an exact receipt remains observable",
      "client-deal" in run([codex_assistant(receipt_json + "\nplease tour")])["needed"])
check("user-supplied exact receipt remains observable",
      "client-deal" in run([user(receipt_json)])["needed"])
check("Codex user-supplied exact receipt remains observable",
      "client-deal" in run([codex_user(receipt_json)])["needed"])

python_literal = repr(receipt)
embedded = run([
    codex_tool("exec", "const r = await tools.exec_command({cmd: "
               + json.dumps("python3 - <<'PY'\nreceipt = " + python_literal
                            + "\nprint(receipt)\nPY")
               + ", workdir: '/Users/booko/carr-system'}); text(r.output);"),
    codex_tool("exec", "const r = await tools.exec_command({cmd: "
               + json.dumps("git status --short") + "}); text(r.output);"),
])
check("unproven Python receipt payload remains fully observable",
      {"client-deal", "delegation-council", "engineering-git", "joe-development"}
      <= set(embedded["needed"]), str(embedded))
json_emission = run([codex_tool(
    "exec", "const r = await tools.exec_command({cmd: "
    + json.dumps("printf '%s' '" + receipt_json + "'") + "});")])
check("unproven JSON receipt emission remains fully observable",
      "client-deal" in json_emission["needed"], str(json_emission))
json_then_action = run([codex_tool(
    "exec", "const r = await tools.exec_command({cmd: "
    + json.dumps("printf '%s' '" + receipt_json + "'; git status") + "});")])
check("JSON receipt followed by executable work is not normalized",
      "engineering-git" in json_then_action["needed"], str(json_then_action))

malformed_literal = dict(receipt)
malformed_literal["extra"] = "tour ledger artifact"
malformed_embedded = run([codex_tool(
    "exec", "const r = await tools.exec_command({cmd: "
    + json.dumps("receipt = " + repr(malformed_literal)) + "});")])
check("malformed embedded receipt literal fails closed to lexical scanning",
      "client-deal" in malformed_embedded["needed"], str(malformed_embedded))
nested_malformed = json.loads(receipt_json)
nested_malformed["source_evidence"]["extra"] = "tour"
check("nested extra-key receipt literal fails closed",
      "client-deal" in run([codex_tool(
          "exec", "const r = await tools.exec_command({cmd: "
          + json.dumps("receipt = " + repr(nested_malformed)) + "});")])["needed"])
trailing_tool_prose = run([codex_tool(
    "exec", "const r = await tools.exec_command({cmd: "
    + json.dumps("receipt = " + python_literal + "\nprint('tour remains visible')") + "});")])
check("prose surrounding an exact tool receipt remains observable",
      "client-deal" in trailing_tool_prose["needed"], str(trailing_tool_prose))
executable_receipt = dict(receipt)
executable_receipt["source_evidence"] = {
    **receipt["source_evidence"], "source_sha": "git"}
executable_tool = run([codex_tool(
    "exec", "const r = await tools.exec_command({cmd: "
    + json.dumps("import subprocess\nreceipt = " + repr(executable_receipt)
                 + "\nsubprocess.run([receipt['source_evidence']['source_sha'], 'status'])")
    + "});")])
check("receipt-derived executable work is never normalized away",
      "engineering-git" in executable_tool["needed"], str(executable_tool))
evil_validator = run([codex_tool(
    "exec", "const r = await tools.exec_command({cmd: "
    + json.dumps("import subprocess\nclass Evil:\n"
                 "  def validate(self, value):\n"
                 "    subprocess.run([value['source_evidence']['source_sha'], 'status'])\n"
                 "evil = Evil()\nreceipt = " + repr(executable_receipt)
                 + "\nevil.validate(receipt)") + "});")])
check("user-defined validate suffix cannot launder executable receipt data",
      "engineering-git" in evil_validator["needed"], str(evil_validator))
shadowed_print = run([codex_tool(
    "exec", "const r = await tools.exec_command({cmd: "
    + json.dumps("import subprocess\nprint = subprocess.run\nreceipt = "
                 + repr(executable_receipt)
                 + "\nprint([receipt['source_evidence']['source_sha'], 'status'])") + "});")])
check("shadowed print cannot launder executable receipt data",
      "engineering-git" in shadowed_print["needed"], str(shadowed_print))
container_taint = run([codex_tool(
    "exec", "const r = await tools.exec_command({cmd: "
    + json.dumps("import subprocess\nreceipt = " + repr(executable_receipt)
                 + "\ncommand = receipt['source_evidence']['source_sha']\n"
                   "subprocess.run([command, 'status'])") + "});")])
check("receipt-derived container alias remains observable",
      "engineering-git" in container_taint["needed"], str(container_taint))
subscript_taint = run([codex_tool(
    "exec", "const r = await tools.exec_command({cmd: "
    + json.dumps("import subprocess\nholder = {}\nreceipt = " + repr(executable_receipt)
                 + "\nholder['x'] = receipt\n"
                   "subprocess.run([holder['x']['source_evidence']['source_sha'], 'status'])")
    + "});")])
check("receipt-derived subscript container remains observable",
      "engineering-git" in subscript_taint["needed"], str(subscript_taint))
attribute_taint = run([codex_tool(
    "exec", "const r = await tools.exec_command({cmd: "
    + json.dumps("import subprocess\nclass Holder: pass\nholder = Holder()\nreceipt = "
                 + repr(executable_receipt) + "\nholder.x = receipt\n"
                   "subprocess.run([holder.x['source_evidence']['source_sha'], 'status'])")
    + "});")])
check("receipt-derived attribute container remains observable",
      "engineering-git" in attribute_taint["needed"], str(attribute_taint))
for alias_name, call in (
    ("ep", "ep._validate_receipt(receipt)"),
    ("json", "json.dumps(receipt)"),
    ("jsonschema", "jsonschema.Draft202012Validator.validate(receipt)"),
):
    alias_spoof = run([codex_tool(
        "exec", "const r = await tools.exec_command({cmd: "
        + json.dumps(f"import evil as {alias_name}\nreceipt = "
                     + repr(executable_receipt) + "\n" + call) + "});")])
    check(f"arbitrary import origin cannot spoof inert {alias_name} validation",
          "engineering-git" in alias_spoof["needed"], str(alias_spoof))
module_mutation = run([codex_tool(
    "exec", "const r = await tools.exec_command({cmd: "
    + json.dumps("import json, subprocess\n"
                 "setattr(json, 'dumps', subprocess.run)\nreceipt = "
                 + repr(executable_receipt)
                 + "\njson.dumps([receipt['source_evidence']['source_sha'], 'status'])")
    + "});")])
check("dynamic module mutation cannot spoof inert serialization",
      "engineering-git" in module_mutation["needed"], str(module_mutation))
for label, mutation, call in (
    ("module __dict__", "json.__dict__['dumps'] = subprocess.run",
     "json.dumps([receipt['source_evidence']['source_sha'], 'status'])"),
    ("validator __dict__",
     "jsonschema.Draft202012Validator.__dict__['validate'] = subprocess.run",
     "jsonschema.Draft202012Validator.validate("
     "[receipt['source_evidence']['source_sha'], 'status'])"),
    ("callable attribute", "json.dumps.__code__ = subprocess.run.__code__",
     "json.dumps([receipt['source_evidence']['source_sha'], 'status'])"),
):
    import_line = "import jsonschema, subprocess" if "validator" in label \
        else "import json, subprocess"
    target_mutation = run([codex_tool(
        "exec", "const r = await tools.exec_command({cmd: "
        + json.dumps(import_line + "\n" + mutation + "\nreceipt = "
                     + repr(executable_receipt) + "\n" + call) + "});")])
    check(f"pre-receipt {label} mutation cannot spoof inert calls",
          "engineering-git" in target_mutation["needed"], str(target_mutation))

for label, prefix, suffix in (
    ("context-manager execution", "from evil import cm\n", "\nwith cm:\n  pass"),
    ("operator execution", "from evil import runner\n", "\nresult = runner @ "
     "[receipt['source_evidence']['source_sha'], 'status']"),
    ("descriptor execution", "from evil import obj\n", "\nresult = obj.run"),
):
    implicit_execution = run([codex_tool(
        "exec", "const r = await tools.exec_command({cmd: "
        + json.dumps(prefix + "receipt = " + repr(executable_receipt) + suffix)
        + "});")])
    check(f"unproven receipt with {label} remains fully observable",
          "engineering-git" in implicit_execution["needed"],
          str(implicit_execution))

# ── the two pin semantics, proven on bytes this repository owns ─────────────
# The replays below bind native session files two different ways: the Codex
# rollouts by WHOLE-FILE digest, and the Claude session by a line-count PREFIX
# digest. Those files are host evidence, so the semantics themselves are proven
# here on synthetic JSONL instead — mutation and truncation must break both
# pins, and a lawful later append must break the whole-file pin while leaving
# the prefix pin intact. This runs on every machine, including one where no
# session transcript exists at all.
with tempfile.TemporaryDirectory() as directory:
    pinned_path = Path(directory) / "rollout-synthetic.jsonl"
    original = b"".join(
        json.dumps({"type": "response_item",
                    "payload": {"type": "message", "role": "assistant",
                                "content": [{"type": "output_text",
                                             "text": f"row {n}"}]}},
                   sort_keys=True).encode() + b"\n"
        for n in range(1, 5))
    PINNED_ROWS = 3

    def pins(payload: bytes) -> tuple[str, str]:
        """Whole-file and PINNED_ROWS-prefix digests of one candidate file."""
        pinned_path.write_bytes(payload)
        prefix = b"".join(pinned_path.read_bytes().splitlines(keepends=True)[:PINNED_ROWS])
        return gate.file_sha256(pinned_path), hashlib.sha256(prefix).hexdigest()

    whole_pin, prefix_pin = pins(original)
    check("the whole-file pin is the digest of the pinned bytes",
          whole_pin == hashlib.sha256(original).hexdigest())
    check("the prefix pin covers only the pinned rows",
          prefix_pin == hashlib.sha256(
              b"".join(original.splitlines(keepends=True)[:PINNED_ROWS])).hexdigest())
    check("a pinned file with no pinned rows would not be provable",
          len(original.splitlines()) > PINNED_ROWS)

    mutated_whole, mutated_prefix = pins(original.replace(b"row 2", b"row 9", 1))
    check("one changed byte inside the pinned rows breaks the whole-file pin",
          mutated_whole != whole_pin)
    check("one changed byte inside the pinned rows breaks the prefix pin",
          mutated_prefix != prefix_pin)

    truncated_whole, truncated_prefix = pins(
        b"".join(original.splitlines(keepends=True)[:2]))
    check("truncation breaks the whole-file pin", truncated_whole != whole_pin)
    check("truncation below the pinned rows breaks the prefix pin",
          truncated_prefix != prefix_pin)

    appended_whole, appended_prefix = pins(
        original + json.dumps({"type": "event_msg"}).encode() + b"\n")
    check("a lawful later append still breaks a WHOLE-FILE pin",
          appended_whole != whole_pin)
    check("a lawful later append leaves a PREFIX pin intact",
          appended_prefix == prefix_pin)

# On the evidence-owning machine, replay the immutable 69-record prefix that
# produced event 10529812. The only hypothetical inputs are the landed
# source-owned wrapper rail and its required four-pack readback; every original
# tool action and the exact final receipt remain byte-for-byte source evidence.
exact_replay_path = Path(
    "/Users/booko/.codex/sessions/2026/08/26/"
    "rollout-2026-08-26T04-40-10-01a03d70-fd22-7e63-bbcf-78a0cee529fa.jsonl")
if host_evidence_required(exact_replay_path):
    exact_raw = exact_replay_path.read_bytes()
    check("event 10529812 transcript digest is immutable",
          gate.file_sha256(exact_replay_path)
          == "1e1dc9a09fc10cbb1906fc38c22b178207a64e40ac84f25d347abc157000eb73")
    exact_records = [json.loads(line) for line in exact_raw.splitlines()[:69]]
    captured_inputs = [row["payload"]["input"] for row in exact_records
                       if row.get("payload", {}).get("type") == "custom_tool_call"
                       and row["payload"].get("name") == "exec"
                       and hashlib.sha256(
                           row["payload"].get("input", "").encode()).hexdigest()
                       in gate.CAPTURED_RECEIPT_TOOL_INPUTS]
    check("event 10529812 binds both captured receipt tool payloads",
          len(captured_inputs) == 2, str(len(captured_inputs)))
    for captured in captured_inputs:
        normalized = gate.custom_tool_text(
            {"type": "custom_tool_call", "name": "exec", "input": captured})
        check("captured payload removes only the strict receipt literal",
              "engineering-slice-receipt.v1]" in normalized
              and "tour-foundation-contracts" not in normalized, normalized[:500])
        tampered = captured.replace("python3", "python4", 1)
        check("one-byte captured-payload change fails closed",
              "tour-foundation-contracts" in gate.custom_tool_text(
                  {"type": "custom_tool_call", "name": "exec", "input": tampered}))
        check("captured bytes under another tool identity fail closed",
              "tour-foundation-contracts" in gate.custom_tool_text(
                  {"type": "custom_tool_call", "name": "other", "input": captured}))
    exact_records.append(codex_standing_context_result(
        "shadow", list(gate.ENGINEERING_WORKFLOW_PACKS), ["424ba0cc"]))
    exact_result = run(exact_records)
    check("immutable event 10529812 action replay removes receipt-only packs",
          exact_result["needed"] == ["engineering-git", "scheduled-automation",
                                     "source-study"],
          str(exact_result))
    check("immutable event 10529812 action replay closes drift",
          exact_result["missing"] == [], str(exact_result))

# Replay the five later pre-repair Engineering Passport observations.  The
# source-owned workflow rail supplies exactly its four reviewed packs.  The
# replay proves that normalization removes only negated-search and typed
# evidence metadata; domain-bearing tour/ledger/surface terms and an actual
# source URL ending in .html stay observable.
engineering_replays = [
    ("22523b89", "rollout-2026-08-26T04-45-27-01a03d75-d35e-7222-8ece-e17ddd5bb614.jsonl",
     70, "ad095eead12f3a5befceb9c3722cffd6df24f8955f9ae5981dd0d4f0f9f44d21",
     ["client-deal", "joe-development", "surface-doctrine"]),
    ("f223b855", "rollout-2026-08-26T04-50-13-01a03d7a-2ff8-7402-a197-9330017515cf.jsonl",
     56, "ed2e31f5e33d76bab8e3ab9ff91d33bcc9f7576e0846b9b86d67da14b0031406", []),
    ("f8f72943", "rollout-2026-08-26T04-54-39-01a03d7e-4159-7972-b125-f7ba75dcff0b.jsonl",
     63, "3c79eb43ddfb53c6b307cd6c8036ac241a56d15b42135b040176a3e308edd272", []),
    ("9f560848", "rollout-2026-08-26T05-14-40-01a03d90-93ad-7661-945f-7f5d817b6b4b.jsonl",
     195, "e7fcfd8a89eccf708141f62c725295797f19f0dda9f0df9f3b63022bc9621f2c",
     ["surface-doctrine"]),
    ("5cfde2b7", "rollout-2026-08-26T05-24-43-01a03d99-c647-7802-93b3-1dc40ec2a8e7.jsonl",
     49, "49858916bfddf419f251d62f2d1cf6c732be7875b0b772d4b1dc5d85b5fb2b73", []),
]
codex_session_dir = Path("/Users/booko/.codex/sessions/2026/08/26")
for event, filename, line_count, digest, expected_missing in engineering_replays:
    replay_path = codex_session_dir / filename
    if not host_evidence_required(replay_path):
        continue
    check(f"event {event} transcript digest is immutable",
          gate.file_sha256(replay_path) == digest)
    replay_records = [json.loads(line) for line in
                      replay_path.read_bytes().splitlines()[:line_count]]
    replay_records.append(codex_standing_context_result(
        "shadow", list(gate.ENGINEERING_WORKFLOW_PACKS), []))
    replay_result = run(replay_records)
    check(f"event {event} preserves only evidence-backed remaining domains",
          replay_result["missing"] == expected_missing, str(replay_result))
    if event == "9f560848":
        check("source-study .html URL remains observable surface vocabulary",
              replay_result["triggers"].get("surface-doctrine") == ["html"],
              str(replay_result["triggers"].get("surface-doctrine")))

# The sixth observation is a Claude config turn, not an Engineering Passport
# slice.  Its append-only prefix proves the injected Skill body had displaced
# the genuine human boundary.  After provenance repair, only the real
# background watcher remains missing; no source-owned update-config workflow
# rail exists in this repository to declare that pack in advance.
claude_replay_path = Path(
    "/Users/booko/.claude/projects/-Users-booko-carr-system/"
    "48574aa2-9289-4e90-906c-b4f958806026.jsonl")
if host_evidence_required(claude_replay_path):
    claude_prefix = b"\n".join(
        claude_replay_path.read_bytes().splitlines()[:1081]) + b"\n"
    check("event 2b17ed65 immutable prefix is preserved",
          hashlib.sha256(claude_prefix).hexdigest()
          == "e464b1ee8d65f5e810881094b96f4f2bd192cb9641ea70de72a28f910933d412")
    claude_records = [json.loads(line) for line in claude_prefix.splitlines()]
    claude_result = run(claude_records)
    check("event 2b17ed65 retains the real scheduled automation miss",
          claude_result["missing"] == ["scheduled-automation"], str(claude_result))
    check("event 2b17ed65 excludes injected Skill prose only",
          set(claude_result["needed"]) == {
              "engineering-git", "governance-rules", "scheduled-automation"},
          str(claude_result))

# ── every new observation can be bound to the exact epoch source/map ───────
source_digest = gate.source_sha256(REPO)
map_digest = gate.file_sha256(REPO / "ops/config/rule-enforcement-map.json")
check("shadow source identity is a sha256", len(source_digest) == 64, source_digest)
check("reviewed map identity is a sha256", len(map_digest) == 64, map_digest)
check("nightly consumer is part of the strict shadow source identity",
      "ops/scheduled-tasks/nightly-record-layer.SKILL.md" in WINDOW_SOURCE_PATHS,
      str(WINDOW_SOURCE_PATHS))
check("engineering-slice workflow is part of the strict shadow source identity",
      "ops/scheduled-tasks/engineering-slice.SKILL.md" in WINDOW_SOURCE_PATHS,
      str(WINDOW_SOURCE_PATHS))
engineering_task = (REPO / "ops/scheduled-tasks/engineering-slice.SKILL.md").read_text()
check("engineering-slice declares its canonical rule-delivery workflow",
      "RULE-DELIVERY WORKFLOW: engineering-slice" in engineering_task)
check("engineering-slice declares the exact reviewed pack set",
      "RULE-DELIVERY PACKS: engineering-git,delegation-council,scheduled-automation,source-study"
      in engineering_task)
check("engineering-slice calls standing-context with packs only",
      '{"packs":["engineering-git","delegation-council","scheduled-automation","source-study"]}'
      in engineering_task and "`workflow`" in engineering_task
      and "also interprets that field as a pack" in engineering_task)
check("engineering-slice refuses before work when scoped standing-context fails",
      "REFUSE" in engineering_task and "standing-context" in engineering_task)
spoofed_engineering = codex_user(
    gate.ENGINEERING_WORKFLOW_HEADER.replace("source-study", "source-studies", 1)
    + "SERVER-ISSUED SLICE PACKET (immutable):\n{}\n\n"
      "CONTROLLER TASK BINDING (immutable):\n{}")
check("changed engineering workflow wrapper loses source-owned pack declaration",
      gate.engineering_workflow_packs(spoofed_engineering) == [])
copied_engineering = codex_user(
    gate.ENGINEERING_WORKFLOW_HEADER
    + "FIRST: call `standing-context` with exactly the four packs above. "
      "REFUSE before inspecting the envelope, source, or job.\n\n"
      "SERVER-ISSUED SLICE PACKET (immutable):\n{}\n\n"
      "CONTROLLER TASK BINDING (immutable):\n{}")
check("copied engineering wrapper without strict bindings is not provenance",
      gate.engineering_workflow_packs(copied_engineering) == [])
check("copied unbound engineering wrapper fails closed to lexical scanning",
      bool(gate.work_text(copied_engineering)))
with tempfile.TemporaryDirectory() as directory:
    copy = Path(directory)
    for relative in WINDOW_SOURCE_PATHS:
        target = copy / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(REPO / relative, target)
    before = source_sha256(copy)
    nightly_copy = copy / "ops/scheduled-tasks/nightly-record-layer.SKILL.md"
    nightly_copy.parent.mkdir(parents=True, exist_ok=True)
    if not nightly_copy.exists():
        shutil.copyfile(REPO / "ops/scheduled-tasks/nightly-record-layer.SKILL.md",
                        nightly_copy)
    nightly_copy.write_text(nightly_copy.read_text() + "\n", encoding="utf-8")
    after_nightly = source_sha256(copy)
    check("nightly consumer drift changes the epoch source digest",
          after_nightly != before)
    adapter_copy = copy / "tools/room-bridge/engineering_dispatch_adapter.py"
    adapter_copy.write_text(adapter_copy.read_text() + "\n", encoding="utf-8")
    check("engineering dispatch contract drift changes the epoch source digest",
          source_sha256(copy) != after_nightly)

# Arbitrary exception detail (including credentials) is never persisted.
with tempfile.TemporaryDirectory() as directory:
    transcript = Path(directory) / "transcript.jsonl"
    secret = "postgresql://user:SUPER-SECRET@example.invalid/db"  # ci-secret-scan: allow
    transcript.write_text('{"type": "user"}\n', encoding="utf-8")
    # A bad transcript LINE no longer reaches this path: lib/transcript_read.py
    # skips it (PR #1224's tamper fix), so the exception is forced here
    # instead, carrying the secret in its message, which must still never be
    # persisted.
    def _raise_with_secret(*_args, **_kwargs):
        raise RuntimeError("transcript read failed for " + secret)
    old_log, old_stdin = getattr(gate, "LOG"), sys.stdin
    old_reader = getattr(gate, "load_transcript")
    setattr(gate, "LOG", str(Path(directory) / "shadow.jsonl"))
    setattr(gate, "load_transcript", _raise_with_secret)
    sys.stdin = io.StringIO(json.dumps({"transcript_path": str(transcript),
                                        "session_id": "secret-test"}))
    try:
        gate.main()
    finally:
        setattr(gate, "LOG", old_log)
        setattr(gate, "load_transcript", old_reader)
        sys.stdin = old_stdin
    persisted = Path(directory, "shadow.jsonl").read_text()
    error_row = json.loads(persisted)
    check("hook exception detail is redacted", secret not in persisted, persisted)
    check("hook error row retains exact v2 shape",
          gate.make_error_observation(
              session=error_row["session"], error=error_row["error"],
              detail=error_row["detail"], map_digest=error_row["map_digest"],
              source_digest=error_row["source_digest"],
              at=gate.stamp(error_row)) == error_row, str(error_row))

# A non-JSON transcript line is SKIPPED, not a gate failure (PR #1224 bypass
# hunt): before, one appended bad line raised and switched this gate off.
with tempfile.TemporaryDirectory() as directory:
    transcript = Path(directory) / "transcript.jsonl"
    transcript.write_text('{"type": "user"}\n{not-json\n', encoding="utf-8")
    old_log, old_stdin = getattr(gate, "LOG"), sys.stdin
    setattr(gate, "LOG", str(Path(directory) / "shadow.jsonl"))
    sys.stdin = io.StringIO(json.dumps({"transcript_path": str(transcript),
                                        "session_id": "selftest"}))
    try:
        gate.main()
    finally:
        setattr(gate, "LOG", old_log)
        sys.stdin = old_stdin
    shadow = Path(directory, "shadow.jsonl")
    rows = [json.loads(line) for line in shadow.read_text().splitlines()] \
        if shadow.exists() else []
    check("a bad transcript line is skipped, not recorded as a gate failure",
          not any(row.get("error") for row in rows), str(rows))

# ── a trigger that ends in punctuation still matches ────────────────────────
xcom = run([user("pull the metrics from x.com for last week"),
            assistant_tool("Bash", {"command": "grok x search"})])
check("x.com fires the joe-comms pack despite the word boundary",
      "joe-comms" in xcom["needed"], str(xcom["needed"]))

if FAILURES:
    print("rule-pack-drift-gate-selftest: FAIL", file=sys.stderr)
    for line in FAILURES:
        print(f"  {line}", file=sys.stderr)
    raise SystemExit(1)
if VERIFY_HOST_EVIDENCE:
    print("rule-pack-drift-gate-selftest: all cases passed, "
          "INCLUDING the pinned host-evidence replays")
else:
    # Say the audit did not run. A hermetic pass proves the gate's behaviour;
    # it proves nothing about the historical transcripts, and a reader who is
    # not told that will assume it did.
    print("rule-pack-drift-gate-selftest: all hermetic cases passed; "
          "pinned host-evidence replays NOT audited "
          "(run with --verify-host-evidence on the evidence-owning machine)")

# Independently reproduced Dot cases share the offline behavioral fixtures.
import runpy as _dot_runpy
_dot_runpy.run_path(str(__import__("pathlib").Path(__file__).with_name("dot-review-selftest.py")))["run_regressions"](['test_b26', 'test_b27', 'test_control_native'])
