---
name: loop-drain-weekdays
description: Weekday loop drain: works the claude-owned queue and closes loops by DOING them, at a rate that exceeds the add rate. Yellow risk — internal record and repo writes only, nothing external, nothing sent.
---

Call `standing-context` FIRST and recite the rule counts.

YOUR ONE JOB: close loops by DOING the work. Nothing else.

Joe's standing order, 2026-08-10, verbatim: "You need to actually perform the work of closing the loops doing the work. You get the loops down to a low number by fixing this problem and then working through the existing loops at a rate that exceeds loops being added in." Target: SINGLE-DIGIT open loops.

WHY THIS TASK EXISTS. On 2026-08-10 a session filed a handoff and a click-to-start chip instead of doing the work, and five hours passed with nothing happening — the open count did not move. A drain that waits for a human is not a drain. This runs on its own.

RISK COLOR: YELLOW. It writes to the record layer and commits code to ~/carr-system. It sends nothing, publishes nothing, and touches no client-facing surface. If a loop's work would reach a client, the public, money, or anything irreversible, STOP on that loop, leave it open, and say so in your report — that is Joe's call, not yours.

YOU COMMIT YOUR OWN WORK, AND THIS RUN IS NOT EXEMPT. Rule `bc9188b4` (Joe, 2026-08-11: "you need to start committing your work. thats a stupid rule") abolished the Aug 9 line that told unattended sessions never to commit. That line cost this task directly: on 2026-08-11 this run finished a real fix to hooks/delegation-gate.py and ops/delegation-gate-selftest.py, made four git calls — every one of them read-only — and stopped, leaving the fix loose in the shared tree and every session that booted in that window warned its gate baseline no longer matched. Do not repeat it. A loop is NOT closed while the change that closes it is still uncommitted.

HOW TO RUN IT:

1. `loop-board` with owner:'claude', status:'open'. These are yours: not blocked, just not done.
2. Pick loops you can genuinely FINISH this run. Prefer ones whose body already names the fix — many do, in a "THE FIX, so nobody re-derives it" paragraph. Those are the cheapest real wins.
3. Do the work. Actually make the change, run the thing, verify the artifact.
4. Close each with `close-loop` and a real outcome saying what you did and how you verified it. The verb refuses an empty outcome, and it should.
5. Minimum THREE closed per run. If you cannot finish three, say why in one line rather than padding the count with rows you did not really finish.

HARD RULES, each of which a previous session broke:
- DO NOT add loops. If you find new work, do it or leave it. A run that ends with more loops than it started has failed.
- DO NOT build another view, dashboard, report or script about the loop pile. `ops/blocker-review.py` already exists and reports what has genuinely unblocked; use it, do not rebuild it.
- DO NOT close a loop whose work you did not actually do. An `ok:true` from a verb means the call parsed, never that the work landed. Verify the artifact — read the file, run the command, query the record.
- DO NOT touch the Doc queue app / browser surface for the ask pile. Joe sequenced it after the loop count comes down.

MECHANICS:
- Code lives in ~/carr-system and nowhere else.
- Migrations: rehearse on a throwaway Neon branch first, ALWAYS, then `bin/migrate-prod.sh --apply`. Delete the branch after.
- Worker: `bin/deploy-worker.sh`. It refuses a dirty mcp-server/ or a non-main HEAD, correctly.
- NEON_API_KEY is in ~/.config/carr/db.env, so nothing waits on a browser login.
- TWO-WRITER DISCIPLINE: this repo regularly holds another live session's uncommitted work. `git add` only the specific paths you wrote — never -A, never -a, never `.`. Check `git status --porcelain <path>` before editing a file to confirm nobody else holds it. A modified file you did not write belongs to someone else: report it, leave it.
- TO CHANGE BRANCH, TAKE A WORKTREE — `run.sh worktree <name>` (add `--from origin/main` to start from the pushed tip). It hands you an isolated checkout with .venv and out/ already linked, so shared HEAD never moves under another writer, which is the whole of rule 308ef1de. Never `git checkout` in ~/carr-system while anyone else's files are loose; the git-writer-gate will refuse you anyway. Remove it with `run.sh worktree --remove <name>` when done — it refuses if the tree is dirty.
- PUSH WHAT YOU COMMIT. Committed-but-unpushed is the same drift as deployed-but-uncommitted. If `origin/main` has moved under you, do NOT rebase the shared tree — cherry-pick your commit onto a worktree cut from `origin/main`, verify there, and push that.
- BEFORE YOU END THE RUN: `git status --porcelain` must list nothing you wrote. That is the acceptance test, and `run.sh health`'s `uncommitted work` row is where a miss shows up publicly.

REPORT AT THE END, in the record not just in the transcript (rule 1f3a7372 — an unattended run that only prints is a run whose findings die with the session): log one `log-decision` naming which loops you closed, what you actually did for each, and the open count before and after. Keep it to a few lines per loop.

CONTEXT: decision 4090076e in decision-history carries the full state as of 2026-08-10 — what shipped, two corrections a session made to its own wrong claims, and what is deliberately not started. Read it on your first run.
