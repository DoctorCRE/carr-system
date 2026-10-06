#!/usr/bin/env python3
# doctrine: rule-delivery-load-layers
"""rule-boot-gate.py — no tool runs until this context has read the rules.

Joe asked for 100% recall on relevant rules; Jev chose this design (p=1.00).
Every session and every subagent must fetch every page of the gated rule boot
(standing-context `detail: "boot"`: the full text of the always-on rules plus
a one-line index of every active rule, served live from the store) before any
other tool call. hooks/gate-integrity.py arms the gate at SessionStart
(startup, resume, clear, compact) and tells the model which calls to make.

Registered on PreToolUse with matcher ".*" so it sees every tool, which is why
it is deliberately tiny: no network, no model, one small state read. Also
registered on PostToolUse and PostToolUseFailure for the fetch (Bash and the
MCP standing-context), where it reads what the fetch returned: a real page
(recorded; a new digest or page count re-arms the session), a rejection of
detail=boot (the Worker is not deployed yet) or an error (store unreachable).
See lib/rule_boot_gate.py for the state layout, the fetch-call grammar and
the recovery paths that remain available while effects are held.

  · a boot page fetch (CARR MCP standing-context, or a Bash command that runs
    this repo's run.sh `call standing-context '<json>'` by any path, after an
    absolute `cd ... &&`, and through harmless output filters — the grammar is
    in lib/rule_boot_gate.py)                 -> allow, and record the attempt;
    a page counts as READ only when PostToolUse finds that page's rule_boot
    (matching page, digest and text) in the result, and the page lengths add
    up to the boot's total_chars
  · other standing-context calls, the read-only rule verbs, ToolSearch -> allow
  · every page of the armed digest confirmed in this context          -> allow
  · incomplete boot, outage, absent deployment, unwritable state or repeated
    holds                                     -> DENY with the recovery calls
  · otherwise                                   -> DENY, naming the exact calls

An internal error cannot establish delivery. PreToolUse therefore denies the
effect; PostToolUse reports the error without confirming a page. Errors are
logged to out/hook-guard.log.

Fixtures: ops/rule-boot-gate-selftest.py
"""
import json
import os
import sys
from datetime import datetime, timezone

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO)
LOG = os.environ.get("CARR_HOOK_GUARD_LOG") or os.path.join(REPO, "out", "hook-guard.log")


def log(msg):
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        ts = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
        with open(LOG, "a", encoding="utf-8") as fh:
            fh.write(f"{ts} rule-boot-gate {msg.rstrip()}\n")
    except OSError:
        pass


def emit(decision, text, event="PreToolUse"):
    out = {"hookEventName": event}
    if decision == "deny":
        out.update(permissionDecision="deny", permissionDecisionReason=text)
    elif text:
        out["additionalContext"] = text
    else:
        return
    print(json.dumps({"hookSpecificOutput": out}))


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception as exc:
        log(f"DENY(parse-error) {type(exc).__name__}")
        emit("deny", "RULE BOOT UNVERIFIED: invalid hook input; repair the adapter before an ordinary effect.")
        return 0
    if not isinstance(payload, dict):
        emit("deny", "RULE BOOT UNVERIFIED: hook input must be an object; repair the adapter.")
        return 0
    event = payload.get("hook_event_name") or "PreToolUse"
    try:
        if event in ("PostToolUse", "PostToolUseFailure"):
            from lib.rule_boot_gate import observe
            emit("allow", observe(payload), event)
            return 0
        from lib.rule_boot_gate import verdict
        decision, text = verdict(payload)
        if decision == "deny":
            log(f"DENY tool={payload.get('tool_name')} agent={payload.get('agent_id') or 'main'}")
        emit(decision, text)
    except Exception as exc:
        log(f"UNVERIFIED(internal-error) {type(exc).__name__}")
        emit("allow" if event in ("PostToolUse", "PostToolUseFailure") else "deny",
             "RULE BOOT UNVERIFIED: the delivery check failed; repair it and fetch the missing pages.", event)
    return 0


if __name__ == "__main__":
    sys.exit(main())
