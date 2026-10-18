ROLE: Platform Engineer. Model: Claude Opus 5.5, high effort. Repo jbookout/carr-system, branch claude/continuity-spool.

GOAL (Joe 2026-10-05, gap #9): the Claude continuity save points must reach the record layer. Every session start reports "Local spool holds 100 unsent continuity receipt(s); nothing replays them. A spool that keeps growing means writes are being refused." So save points are being refused and silently pile up.

WHAT EXISTS: ops/claude-continuity-hook.py (writes/spools), ops/claude-continuity-selftest.py, ops/fixtures/real-replay/hook-advisories.jsonl, the claude-checkpoint / claude-record-event verbs (mcp-server/src/tools.js), the carr-continuity MCP server.

BUILD (all required):
1. Diagnose first: read the spooled receipts and find the refusal reason(s) by re-sending ONE receipt in a dry-run or reading the server's refusal. Name the root cause in the PR body with evidence (version conflict, schema drift, auth, size, cap at 100, etc.).
2. Fix the root cause so new receipts are accepted.
3. A safe drain: receipts are semantic records only. Drain those still valid in order with their original idempotency keys; receipts that can no longer apply (stale expected_version) are moved to an archive file with the reason, never retried forever and never deleted. Never replay a pending external effect (the hook's own rule).
4. The spool size and oldest age become a health row with a bound action per rule 590b11e1; growth over a threshold files one deduplicated loop.
5. Selftests that fail first: the refusal case, the drain, and the archive path.
RULES FOR THIS JOB (all required): you never merge, approve, enable auto-merge or force push. Reports go to the orchestrator, never to Joe; never ask Joe to review or approve. No credentials printed. No rm (move to _to_delete/). No production deploy, DB apply or settings.json edits. Commits use 64207374+jbookout@users.noreply.github.com. Work in a NEW git worktree off origin/main. Run tests in the foreground and open the PR before you finish. Use the repo's skills where they fit: Matt Pocock `tdd` for test-first, pstack principles (prove-it-works, fix-root-causes, subtract-before-you-add). PR body follows the Pocock `pr` shape: smallest visual summary, before/after evidence, merge danger (one-way or two-way door, blast radius). Last line of your output: the PR URL.
