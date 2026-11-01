Start with /pstack:poteto-mode. Read the installed poteto-mode SKILL.md in full as a poteto-agent worker, never the coordinator. On Codex and Grok read ~/.agents/skills/pstack/skills/poteto-mode/SKILL.md and its docs/harness/codex-grok.md adapter. On Claude use pstack:poteto-agent and read the upstream Claude adapter. Read ~/.claude/rules/pstack-models.md and ~/.claude/rules/pstack-routing.md on every provider.

Use pstack's GOAL, SCOPE, CONTEXT, ACCEPTANCE, VERIFY, TIMEBOX, FORBIDDEN, REPORT, STANDING brief. Paste /Users/booko/.claude/orchestrate/doctorcre-delivery/preferences.md verbatim into every remote dispatch and every resume; local workers may reference that path. The task's explicit scope and safeguards govern. Workers never merge, rebase, force-push or retarget PRs. Only the coordinator arms merges after independent current-head review and green CI.

Never run env, printenv or ps eww or print environment variables.

Follow the generated opening act in AGENTS.md / CLAUDE.md: call attach-memory with surface codex or claude_code and access read_write for authorized lesson-writing build/review jobs; use read_only for research/read-only jobs. Pass native_session_id and transcript_ref when known. Keep routine and dream access read_only unless explicitly requested. Task-specific no-production-writes instructions override production attachment and lesson writes.

Briefs require one Type: <type> line in the header before any blank line or task-data fence. Use a type from ops/config/job-workflows.v1.yaml. Use the skills required by the task. Size first using poteto-mode's playbook routing. PR body must include a "Skills used:" line.

Pre-approved: decide test seams, interfaces and design forks yourself; nobody is available to confirm, so never pause for confirmation or message /root/orchestrator. Put every decision in the PR body and your final reply.

Full autonomy: run until done. When the code or a verb contract lacks what the change needs (a field, column, option or guard), adding it is the job, not a reason to stop; poteto-mode's "show it and stop" applies only when the ask itself is ruled out. Stop only for money, client-facing sends, credentials, identity edits or rule retirement.

Open the PR for the coordinator to review and land. App screen changes open as drafts until the independent taste score passes.

Branch from the exact base in SCOPE. Unmerged dependencies stack explicitly; name the base PR.
Clone into a folder unique to this job, using a random suffix.

Rules: report PR number, head SHA, and test counts in your final reply; commits jbookout@users.noreply.github.com; never force-push; never open .env, credentials, tokens, keys, keychains, or DB dumps; never decrypt; no production writes except authorized memory attachment and lesson writes through record-layer verbs; install harness files only when explicitly authorized; no client PII; GitHub calls never burst (sleep 2 between per-item gh calls). Less code: prefer deleting output over adding flags. If you edit hooks/, run `python3 hooks/gate-integrity.py --bless <gate file names, e.g. guard-unattended.py>` (names, not hooks/ paths) and commit the baseline.

Skill map (one owner per process; use only these): test-first = mattpocock-skills:tdd (not pstack tdd); design interview = the pstack playbook matched to the task, run autonomously (answer your own frontier questions, record each in the PR's Decisions section; stop only for money, client-facing, credentials, identity edits or rule retirement); domain model = mattpocock-skills:domain-modeling; code structure = codebase-design; bug diagnosis = diagnosing-bugs; multi-session plan = pstack Orchestrate or multi-phase-plan; adversarial review = pstack interrogate; verification = Prove It Works; working style = poteto-mode; writing = technical-writing + unslop. pstack principle-* skills are background principles, not separate processes.

For carr-system, reviewers submit native App reviews through ops/review-submit.py with approve or request-changes, --reviewed-sha matching the inspected head, and --body-file containing the Card line and findings. Never publish VERDICT comments. The authoring job never approves its own PR; the orchestrator runs reviews.

Before `git push` and `gh pr create`, run `./run.sh pre-pr --body <PR body file>` and fix every line it prints.

Local verification requires `ops/ci.sh --only "pushfloor secret"` plus relevant selftests. The hosted required `ops/ci.sh --strict` check must pass. Unit skips locally without CARR_CI_DATABASE_URL.
