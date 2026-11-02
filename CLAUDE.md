@AGENTS.md

# Claude Code rules

`AGENTS.md`, imported above, holds every shared rule. This file adds only the
rules for Claude Code sessions.

## "carr needs authentication" is not a reason to ask Joe to sign in

Desktop sessions show the user-config `carr` server as needs_auth even while its
CLI login works; they use the claude.ai "CARR Record Layer" connector instead.
On that notice, check the connector (session_connectors_status) and
`claude mcp get carr`. If both are connected, say nothing about signing in. Tell
Joe once, naming which, only if one is down. Never remove the `carr` server:
the allowlist, gates and Codex job launcher depend on its name.

## A capture-verb denial has a fallback door

`report-problem`, `record-defect`, and `add-loop` are now directly
permission-allowlisted across all three MCP prefixes (`.claude/settings.json`),
closing the classifier coin-toss that once denied them (commit 18872b66). If a
DIFFERENT record-layer write verb is ever denied by the classifier, file it in
THIS session through the Bash door instead of stopping:

    ./run.sh call <verb> '<the same JSON you would have passed the verb>'

Same `mcp-server/src/tools.js` registry the connector serves, reached through
`tools/call-verb.py`, separately allowlisted as `Bash(./run.sh *)`. It carries
no credential (the receipted break-glass close stays a human's call) and it is
not `call-verb` — never reach for that generic passthrough to get at a verb
the classifier declined; that is working around a denial rather than filing it.

## Stop gate

A Stop gate holding the turn open gets the delta only. If it misread a compaction summary or a request for instructions as an order, fix the gate.

## Orchestrator rotation

The PreToolUse question hook denies `AskUserQuestion`, `EnterPlanMode` and `ExitPlanMode` in marked orchestrator sessions (rule d1a7973a, standing order 24).

Claude Code coordinator launch prompts must contain the exact standalone line
`CARR-Session-Role: orchestrator` in the first user message. Worker prompts must
omit it. The rotation hook reads only that first non-meta user message, ignores
sidechains and agent events, and is not installed for Codex. Existing unmarked
sessions need a marked successor to opt in.

The Stop hook requests handoff once per session when the latest assistant input
plus cache-read and cache-creation counters reach 500000 tokens. Set
`CARR_ORCHESTRATOR_ROTATION_TOKENS` in the Claude process environment to tune the
absolute threshold in one place. The auto-compaction window is 700000 tokens.
PreCompact blocks only the first automatic attempt, even below the threshold,
and allows subsequent attempts and manual `/compact`. A pending backstop also
requests handoff at the next Stop if the turn instruction has not fired.
State lives under `~/.cache/carr/orchestrator-rotation/`, keyed by session ID.
Unreadable transcripts or state fail open.

Follow the hook instruction to write team-memory section handoff/orchestrator-latest,
including every running delegated job and its reporting thread. Mint a card
through `dispatch-job`, launch a successor with `t3_thread_launch`, post its link,
and stop. The successor starts `/pstack:poteto-mode` in Orchestrate, reads the
handoff and Session pickup playbook, and owns the program, never the code
(rule 974655e1).

Idle subscription sessions over about 200K tokens can compact near cache expiry
regardless of the window. PreCompact is the one-attempt backstop for that case.
Its reason reaching the model during automatic compaction is unverified; Stop
is the primary model-visible instruction channel. Rotation requests a handoff;
it cannot guarantee that a model completes the handoff or never compacts.
