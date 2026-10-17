# Let direct approval questions through the escalation gate (carr-system)
Work in /Users/booko/carr-system-gatefix (branch claude/escalation-gate-approvals). Open ONE PR. Never merge. No credentials.

## Problem (reproduced twice on 2026-10-04)
hooks/escalation-gate.py refuses every AskUserQuestion it classes as an "internal decision". But Joe's ACTIVE rule a3da3d39
(approved 2026-10-04) orders: "ASK JOE DIRECTLY FOR EVERY APPROVAL, IN THE SAME TURN, WITH AN INTERVIEW BOX ... a taught rule left
proposed, a decision only he can make, a retirement he must confirm." Refused asks: (1) "Do you approve restoring the source-study
rule?" (2) "Do you approve building all 16 retro fixes?" Both are approval asks the rule requires; the gate forced plain-text asks.

## Required (all)
1. Read hooks/escalation-gate.py, its selftest, and the gate baseline mechanism (rule c0b38d80: re-bless the gate baseline hash in
   the same commit that changes a gate).
2. Write failing selftest cases FIRST: the two refused questions above must PASS; an internal "which approach should I take for X"
   ask (no approval of a Joe-owned artifact) must still be REFUSED.
3. Implement the narrowest deterministic predicate (no model call): an AskUserQuestion whose question asks Joe to approve / not approve
   a named artifact that only Joe can approve — a proposed rule, a rule restoration/retirement, a build/plan approval he must give,
   or options labelled approve/don't approve — is allowed. Everything else keeps today's behaviour.
4. Re-bless the gate baseline in the same commit. Run the gate selftests and ops/ci.sh classes that cover hooks.
5. PR body: the two real refused examples, the predicate, the still-refused example. Print the PR URL on its own line at the end.
