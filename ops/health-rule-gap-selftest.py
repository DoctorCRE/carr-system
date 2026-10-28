#!/usr/bin/env python3
"""health-rule-gap-selftest.py — the active-rule-gap acceptance stays narrow.

THE PROBLEM, found 2026-08-21. `run.sh health` printed "⚠︎ active-rule-gaps 2
active admitted rules not hard-enforced" and nothing could ever clear it. The
query counted every active rule whose enforcement_status was not exactly
'hard_enforced', and the two it flagged were both working as designed:

  · the rule requiring a whole path be exercised before a capability is called
    live carries a real stop gate (hooks/completion-evidence-gate.py, wired in
    ~/.claude/settings.json), recorded in ops/config/rule-enforcement-map.json
    as enforcement_class stop_gate with control completion_evidence;
  · Joe's rule that a permanently chosen machine state must be read as accepted
    is recorded there as judgment_ambient with a written why_unenforceable —
    "No control can judge whether a human chose a state on purpose."

So the check was doing to a deliberate state exactly what that second rule
forbids: leaving it red forever. Two corrections went in — 'authority_enforced'
counts as enforced (migrations/0194_atomic_rule_approval.sql allows exactly
hard_enforced, authority_enforced and blocked), and the one judgment class is
accepted rather than counted.

WHAT THIS TEST IS FOR. Rule bd4a6d22 does not merely permit a named acceptance,
it constrains one: name the accepted thing exactly, keep it an explicit
constant, keep printing it on the passing line, and never widen a pattern or
delete an assertion to make a check quiet. Everything outside the named
acceptance must still fail, AND there must be a test proving it still fails.
This is that test. Its fixtures are written against the constants themselves, so
the way to break the gate — quietly widening RULE_UNENFORCEABLE_CLASS to cover
another class, or adding 'blocked' to the enforced states to zero the count — is
the way to fail this file.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path
from types import SimpleNamespace

REPO = Path(__file__).resolve().parent.parent
SOURCE_PATH = REPO / "tools" / "health-check.py"

# READ THE CONSTANTS, DO NOT RUN THE SCRIPT. tools/health-check.py performs the
# whole health check at module level, so importing it here would fire a live run
# against the canonical store — and its exit status, not this file's assertions,
# would decide whether the selftest passed. Parsing the assignments gives the
# same two values with no side effect and no network.
_tree = ast.parse(SOURCE_PATH.read_text(encoding="utf-8"))
_found: dict[str, object] = {}
for _node in _tree.body:
    if isinstance(_node, ast.Assign) and len(_node.targets) == 1:
        _target = _node.targets[0]
        if isinstance(_target, ast.Name) and _target.id in (
                "RULE_ENFORCED_STATES", "RULE_UNENFORCEABLE_CLASS"):
            _found[_target.id] = ast.literal_eval(_node.value)
for _name in ("RULE_ENFORCED_STATES", "RULE_UNENFORCEABLE_CLASS"):
    if _name not in _found:
        sys.exit(f"health-rule-gap-selftest: {_name} is not a module-level "
                 f"constant in tools/health-check.py — the acceptance must stay "
                 f"an explicit named constant (rule bd4a6d22)")
health = SimpleNamespace(**_found)

PASSED = 0
FAILED: list[str] = []


import pathlib, sys  # noqa: E401
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))  # repo root, for lib/
from lib.gate_check import Check  # noqa: E402

check = Check(ok="  ok  {label}", failures=FAILED, counters={"PASSED": "pass"}, ns=globals())


# ── the acceptance is exactly what was decided, and no wider ────────────────
check("the accepted-unenforceable class is exactly judgment_advisory",
      health.RULE_UNENFORCEABLE_CLASS == "judgment_advisory",
      f"got {health.RULE_UNENFORCEABLE_CLASS!r}")

check("exactly two statuses count as enforced",
      health.RULE_ENFORCED_STATES == ("hard_enforced", "authority_enforced"),
      f"got {health.RULE_ENFORCED_STATES!r}")

check("'blocked' is NOT an enforced status",
      "blocked" not in health.RULE_ENFORCED_STATES,
      "adding it would zero the count by fiat and the gate would stop binding")

# ── everything outside the acceptance must still be counted ────────────────
# migrations/0194_atomic_rule_approval.sql's check constraint fixes the status
# vocabulary; these are the classes seen in ops.v_rule_enforcement_status.
for other in ("machine_enforceable", "post_action_verification", "stop_gate",
              "judgment_ambient", ""):
    check(f"class {other!r} is OUTSIDE the acceptance, so an unenforced rule still counts",
          other != health.RULE_UNENFORCEABLE_CLASS,
          "a second accepted class would need its own decision and its own line here")

# ── the query is built FROM the constants, so the two cannot drift ──────────
source = (REPO / "tools" / "health-check.py").read_text(encoding="utf-8")
check("the gap query interpolates the enforced-states constant",
      "not in ('{_enforced}')" in source,
      "the states were spelled inline again; the constant is then decorative")
check("the gap query interpolates the accepted-class constant",
      "<> '{RULE_UNENFORCEABLE_CLASS}'" in source,
      "the class was spelled inline again; the constant is then decorative")
check("the accepted count is SELECTED, not assumed",
      "= '{RULE_UNENFORCEABLE_CLASS}'" in source,
      "without the second count the passing line cannot show what is carried")

# ── the chosen state stays visible on a green line ──────────────────────────
check("a passing line still prints what is carried as unenforceable",
      "carried as unenforceable by design" in source,
      "rule bd4a6d22: the accepted state must stay visible, never become silence")
check("the reader is parsing five columns, matching the five the query returns",
      "len(cols) == 5" in source,
      "a four-column reader silently drops the accepted count back to zero")

print(f"\nhealth-rule-gap-selftest: {PASSED}/{PASSED + len(FAILED)} passed")
if FAILED:
    print("FAILED: " + ", ".join(FAILED))
    sys.exit(1)
print("SELFTEST MET: the active-rule-gap acceptance is named, narrow, visible, "
      "and everything outside it still counts.")
