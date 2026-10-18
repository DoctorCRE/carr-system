ROLE: Platform Engineer. Model: gpt-6.1-sol, high effort.

Problem: the live Progress board has not published for days. The launchd job local.carr-progress-board (every 15 min, log ~/Library/Logs/carr-progress-board.log) fails every run with: SnapshotTooLarge: board carr-v5 snapshot is 1975198 characters after trimming every Live, Merged and History entry; the server limit is 262144 (tools/progress_board.py fit_snapshot, publish_board). About 300 consecutive failures are logged. Second defect: the plist's WorkingDirectory is /Users/booko/carr-system-ruleapprove, a feature-branch worktree for PR 1536, so the job runs unmerged code. Find the plist's source in the repo (ops/launchd or similar plus fleet-sync) and its documented intended working directory.

All required:
1. Find what makes the carr-v5 snapshot ~2 MB after trimming (measure per field and per item; read the state file the job reads). Fix the cause so a board snapshot fits the server limit with headroom, keeping every open item visible. Do not raise the server limit to hide it.
2. Point the job at the canonical main-tracking path the repo's fleet config intends, never a feature worktree, and add a check that fails when a launchd WorkingDirectory or program path points into a non-main worktree.
3. Tests first: a test that fails on origin/main for each defect and passes after. Run ops/ci.sh --strict.
4. Worktree: /Users/booko/carr-system-board-size on branch claude/board-snapshot-size. Commit by named paths, push, open a PR titled "Progress board publishes again: snapshot fits the limit; job runs from main", with the measurements before and after, ending with: 🤖 Generated with [Claude Code](https://claude.com/claude-code). Print the PR URL on its own line, then DONE.
BOUNDARIES: do not edit the installed plist under ~/Library/LaunchAgents (the orchestrator reloads it after merge); no production deploy; no force push; never rm outside git; no credentials printed; no paid Claude API; do not merge.
