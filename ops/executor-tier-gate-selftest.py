#!/usr/bin/env python3
"""Selftest for hooks/executor-tier-gate.py, including Jev's tier pick (loop 615).

Runs the real hook as a subprocess with a stubbed Jev answer, so it is offline
and deterministic. Cases: a named model is never denied, only advised, and a
confident cheaper pick on it is advice; a low-confidence or equal pick on a
named model is silent; a fork stays exempt.

ACTING (Joe, 2026-09-24, decision 5ec806a4): with no model named, a confident
Jev pick (>= ACT_AT) is filled in as the call's model and the spawn is allowed,
said in the context line. The abstention path -- unavailable or under ACT_AT --
keeps the deterministic deny this gate always gave: an abstaining judge never
loosens it.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOOK = os.path.join(REPO, "hooks", "executor-tier-gate.py")
PASS = 0


def run(tool_input, stub, transcript_path=None, *, hook=HOOK):
    env = {**os.environ, "CARR_EXECUTOR_TIER_JEV_STUB": stub}
    payload = {"tool_name": "Agent", "tool_input": tool_input}
    if transcript_path:
        payload["transcript_path"] = transcript_path
    out = subprocess.run([sys.executable, hook], input=json.dumps(payload), capture_output=True,
                         text=True, env=env, timeout=30).stdout.strip()
    return json.loads(out)["hookSpecificOutput"] if out else None


def build_advisory_transcript(facets):
    advisory = {
        "schema": "jev-build-advisory/v1", "partner_request_sha256": "0" * 64,
        "model": "jev-1.13.0",
        "facets": {f: 0.9 for f in facets} or {"architecture_or_design": 0.9},
        "guidance": {},
        "required_actions": [{"facet": f, "instruction": f"do {f}"} for f in facets],
        "usage": {}, "authority": "required", "deterministic_exclusions": [],
    }
    receipt = {
        "schema": "jev-build-turn-receipt/v1", "client": "claude", "session_id": "s1",
        "turn_id": None, "prompt_sha256": "0" * 64, "adviser_digest": "sha256:" + "0" * 64,
        "configuration_digest": "sha256:" + "0" * 64, "source_digest": "sha256:" + "0" * 64,
        "semantic_rule_delivery": "delivered", "advisory": advisory,
    }
    # Real Claude Code shape (verified against a live ~/.claude/projects/*.jsonl
    # session): attachment.type == "hook_additional_context", content a list of
    # already-decoded JSON text — no "stdout"/hookSpecificOutput wrapper.
    record = {"attachment": {"type": "hook_additional_context",
                             "content": [json.dumps(receipt)]}}
    fh = tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False)
    fh.write(json.dumps({"type": "user", "message": {"role": "user", "content": "design it"}}) + "\n")
    fh.write(json.dumps(record) + "\n")
    fh.close()
    return fh.name


def check(label, condition, detail=""):
    global PASS
    if not condition:
        print(f"FAIL {label}: {detail}")
        sys.exit(1)
    PASS += 1


brief = {"description": "Find grants", "prompt": "grep db/schema.sql for grants", "subagent_type": "Explore"}

r = run({**brief}, "sonnet:0.89")
check("no model, confident Jev pick: allowed with the pick filled in",
      r and r.get("permissionDecision") == "allow" and r.get("updatedInput", {}).get("model") == "sonnet", r)
check("the rest of the call is passed through unchanged",
      all(r["updatedInput"].get(k) == v for k, v in brief.items()), r)
check("the context line says Jev named the executor",
      "EXECUTOR NAMED BY JEV" in r.get("additionalContext", "") and "`sonnet` at 0.89" in r["additionalContext"], r)

r = run({**brief}, "haiku:0.40")
check("no model, a low-confidence pick: still refused", r and r.get("permissionDecision") == "deny", r)
check("the refusal carries the pick as a hint", "below the acting threshold" in r["permissionDecisionReason"], r)

r = run({**brief}, "none")
check("no model, an unavailable judge: still refused, with no Jev line",
      r and r.get("permissionDecision") == "deny" and "JEV'S PICK" not in r["permissionDecisionReason"], r)
check("the refusal still names the fix", "EXECUTOR NOT NAMED" in r["permissionDecisionReason"], r)

r = run({**brief, "model": "opus"}, "haiku:0.95")
check("a confident cheaper pick on a named model is advice, not a refusal",
      r and "permissionDecision" not in r and "EXECUTOR ADVICE" in r.get("additionalContext", ""), r)

r = run({**brief, "model": "opus"}, "haiku:0.40")
check("a low-confidence cheaper pick is silent", r is None, r)

r = run({**brief, "model": "haiku"}, "haiku:0.99")
check("an equal pick is silent", r is None, r)

r = run({**brief, "model": "haiku"}, "opus:0.99")
check("a dearer pick never pushes a spawn upward", r is None, r)

r = run({**brief, "subagent_type": "fork"}, "haiku:0.99")
check("forks stay exempt", r is None, r)

# A routing pin exempts an in-process spawn only when the pin actually names
# that model. The merge_review pin preserves the Opus review policy.
pinned = {**brief, "model": "opus", "prompt": "executor: opus per routing pin merge_review\nReview PR 1 adversarially."}
r = run(pinned, "sonnet:0.84")
check("the Opus merge-review pin exempts its matching model", r is None, r)

routed = {**brief, "model": "opus", "prompt": "executor: opus per routing dispatch\nChange the parser."}
r = run(routed, "sonnet:0.84")
check("a spawn the routing dispatch chose gets no cheaper-tier advice", r is None, r)

made_up = {**brief, "model": "opus", "prompt": "executor: opus per routing pin because_i_said_so\nSweep grants."}
r = run(made_up, "sonnet:0.84")
check("an unknown pin name does not silence the advice",
      r and "EXECUTOR ADVICE" in r.get("additionalContext", ""), r)

mismatch = {**brief, "model": "opus", "prompt": "executor: haiku per routing dispatch\nSweep grants."}
r = run(mismatch, "sonnet:0.84")
check("a dispatch line naming a different model than the call does not silence the advice",
      r and "EXECUTOR ADVICE" in r.get("additionalContext", ""), r)

# A fixture run with no stub makes no live judgment, so its output is stable.
env = {k: v for k, v in os.environ.items() if k != "CARR_EXECUTOR_TIER_JEV_STUB"}
env["CARR_HOOK_FIXTURE"] = "1"
outs = [subprocess.run([sys.executable, HOOK], input=json.dumps({"tool_name": "Agent", "tool_input": brief}),
                       capture_output=True, text=True, env=env, timeout=30).stdout for _ in range(2)]
check("a fixture run makes no live judgment and is deterministic",
      outs[0] == outs[1] and "JEV'S PICK" not in outs[0], outs)

# A historical prompt-facet receipt cannot impose a new Agent-prompt gate.
path = build_advisory_transcript(["architecture_or_design"])
try:
    r = run({**brief, "model": "haiku"}, "haiku:0.99", transcript_path=path)
    check("historical prompt facets do not deny a named executor",
          not (r and r.get("permissionDecision") == "deny"), r)
finally:
    os.unlink(path)

print(f"executor-tier-gate-selftest: all {PASS} checks passed")

# Policy changes vary the pin contract, not the rest of the executor suite.
# Exercise the actual hook against the loaded policy and both supported target
# shapes. Test copies keep installed policy untouched.
policy = json.loads((Path(REPO) / "ops/config/model-routes.v1.json").read_text())
for target_model in ("loaded", "opus", None):
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        (root / "hooks").mkdir()
        (root / "ops/config").mkdir(parents=True)
        copied_hook = root / "hooks/executor-tier-gate.py"
        shutil.copyfile(HOOK, copied_hook)
        os.makedirs(os.path.join(root, "lib"), exist_ok=True)
        shutil.copyfile(os.path.join(REPO, "lib", "hook_runtime.py"), os.path.join(root, "lib", "hook_runtime.py"))
        candidate = json.loads(json.dumps(policy))
        if target_model != "loaded":
            candidate["dispatch_targets"]["merge_review_test"] = {"subagent_model": target_model}
            candidate["pins"]["merge_review"]["target"] = "merge_review_test"
        (root / "ops/config/model-routes.v1.json").write_text(json.dumps(candidate))
        pin_target = candidate["dispatch_targets"][candidate["pins"]["merge_review"]["target"]]
        r = run(pinned, "sonnet:0.84", hook=str(copied_hook))
        if pin_target.get("subagent_model") == pinned["model"]:
            check("a spawn matching the loaded merge-review pin gets no cheaper-tier advice", r is None, r)
        else:
            check("an Opus spawn cannot claim a different merge-review target as an exemption",
                  r and "EXECUTOR ADVICE" in r.get("additionalContext", ""), r)
print("executor-tier-gate-selftest: loaded and both merge-review policy shapes passed")
