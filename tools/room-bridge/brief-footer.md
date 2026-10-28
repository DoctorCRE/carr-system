Never run env, printenv or ps eww or print environment variables.

Follow the Skills line and Required skills block supplied by bin/codex-job. Size first: tiny -> build now; one session -> grill-with-docs; bigger -> wayfinder. PR body must include a "Skills used:" line.

Pre-approved: decide test seams, interfaces and design forks yourself; nobody is available to confirm, so never pause for confirmation or message /root/orchestrator. Put every decision in the PR body and your final reply.

Open the PR with GitHub auto-merge turned on (squash, pinned to your head commit); it merges itself when CI is green. App screen changes open as drafts until the independent taste score passes.

Branch from a fresh `origin/main`. If work depends on an unmerged PR, wait for it or build on main and say so in the PR body.
Clone into a folder unique to this job, using a random suffix.

Rules: report PR number, head SHA, and test counts in your final reply; commits jbookout@users.noreply.github.com; never force-push; never open .env, credentials, tokens, keys, keychains, or DB dumps; never decrypt; no production writes; never install config into ~/.claude or ~/.codex; no client PII; GitHub calls never burst (sleep 2 between per-item gh calls). Less code: prefer deleting output over adding flags. If you edit hooks/, run `python3 hooks/gate-integrity.py --bless <gate file names, e.g. guard-unattended.py>` (names, not hooks/ paths) and commit the baseline.

Skill map (one owner per process; use only these): test-first = mattpocock-skills:tdd (not pstack tdd); design interview = grill-with-docs, run autonomously (answer your own frontier questions, record each in the PR's Decisions section; stop only for money, client-facing, credentials, identity edits or rule retirement); domain model = mattpocock-skills:domain-modeling; code structure = codebase-design; bug diagnosis = diagnosing-bugs; multi-session plan = wayfinder; adversarial review = pstack interrogate; verification = Prove It Works; working style = poteto-mode; writing = technical-writing + unslop. pstack principle-* skills are background principles, not separate processes.

Reviewers post 'VERDICT: APPROVE|CHANGES' first and 'Reviewed-SHA: <head>'. The job that authored a PR never posts a VERDICT comment on it; the orchestrator runs reviews.

Before `git push` and `gh pr create`, run `./run.sh pre-pr --body <PR body file>` and fix every line it prints.

Local verification requires `ops/ci.sh --only "pushfloor secret"` plus relevant selftests. The hosted required `ops/ci.sh --strict` check must pass. Unit skips locally without CARR_CI_DATABASE_URL.
