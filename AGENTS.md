# AGENTS.md — boot instructions for a session rooted at the CODE repo

Codex and kin look for this filename by convention. Until 2026-08-14 it existed
only in the Drive vault, so a session rooted here — which is where every piece of
code work happens — booted with no instructions at all.

## First, load the standing rules from the STORE

Call `mcp__carr__standing_context` directly FIRST. Codex may keep MCP tools out
of the shortened active-tool description until they are needed, so if the tool
is not displayed, search the deferred tool catalog for the exact name before
concluding it is unavailable. In the first response, report the number of
shared and personal rules actually delivered, alongside the available corpus
counts. Do not describe corpus counts as rules loaded into the session. Fetch
full text only for rules binding the current task or explicit rule IDs.

Only when the direct MCP tool is genuinely unavailable or returns a service
error, use the checkout's fallback:

```
./run.sh call standing-context '{}'
```

That shell command runs inside Codex's network sandbox. A first `fetch failed`
from the sandbox is a permission-path failure, not a store outage: retry it with
the sanctioned network escalation before saying the Worker or database is
down. Declare the store unreachable only after the direct MCP call AND the
network-approved fallback both fail, and name both errors.

The rendered files (`DNA/compiled-rules-shared.md` in the vault and the
partner's personal file) are a FALLBACK, not the boot path — they are Dell's
boot path until his 2026-08-21 cutoff plus an emergency fallback for everyone
else, and they are only as fresh as the last hourly export. A silent fallback
is worse than a loud failure.

## Active product-first delivery policy

<!-- carr-product-first-policy:start -->
Decision `1facbf00-60d9-4cde-bfab-9798f1b6e307` is the current record for
this policy and supersedes the narrower approval rule. It carries forward
the product priority from decision `019146bd-15fb-4f5e-8849-ed63911469e0`.
The full current text is STORE doctrine
`engineering-workflow-sop#00-scope-and-provenance`, section
`52880de2-ab90-4673-b046-b74f900aa2de@6`, content hash
`0d4fde90e98b0fab9f769e07f1b4732f8ec90cdf458834a8df2e9e1611e13626`.
That version and hash record this observed policy's provenance. At runtime,
fetch the current section by its stable section ID; do not use this observed
snapshot as a current-version gate.

- Continue the next unfinished DoctorCRE product task attended; preserve
  completed audits and reviews. The unattended engineering controller is not
  its prerequisite. Joe's ruling 2026-09-24, decision
  `b729859d-be5d-4521-ba50-d4517bc57208`: the claim that "unattended
  dispatch remains disabled" was never his rule — a model wrote it and
  framed it as his direction. His actual goal is maximum automation,
  including scripted jobs starting agent sessions automatically. This does
  not touch the real production, credential, external-send,
  destructive-action, and merge-approve safeguards below, which remain in
  force as technical checks, nor does it waive CI as the merge gate.
- Do not put a new Work Request ahead of product work unless it names the
  product task it blocks. Existing substrate work may finish but
  may not spawn child Work Requests. Backlog a substrate follow-up with the
  product task it blocks; do not build it in-session under the older general
  follow-up rule. Active follow-up rule
  `179be4b8-2fe0-418d-9503-52d1e33921d3@3`, amendment
  `80e6d24c-6b49-4765-80c3-e05c1025ba38`, carries this scoped exception.
- Execute authorized work without CARR approval gates. Once Joe directs a task
  or authorizes a workflow, carry it through applicable source and production
  effects. Do not require a separate CARR plan acceptance, Work Shape approval,
  plan hash approval, Gate A, Gate R, merge permission, release signoff, or
  fresh human confirmation for an effect. A changed plan version does not
  create a new approval request.
- Deliver source through an ordinary pull request: isolated branch, relevant
  local verification, hosted CI as the merge gate, merge, and delivery
  verification. Automated tests, source and contract checks, authentication,
  audit records, and truthful refusal on failed checks remain in force.

Reuse verified evidence while its relevant source or contract remains
unchanged. A blocker must name the concrete missing fact, authority, or external
dependency. Run independent authorized work in parallel isolated worktrees.
Existing production and destructive-action safeguards remain in force as
technical checks, without repeat approval requirements. A technical gate that
still enforces old approval semantics must be changed through ordinary source
delivery; it remains a real constraint until then. Permissions imposed by the
host, provider, sandbox, or law are outside CARR's control and must be reported
accurately.
<!-- carr-product-first-policy:end -->

## Jev in reviews

Decision `d57501f6-00e7-4ff5-a886-c28a5d5501d6` records Joe's direction:
Jev is authorized to participate in every kind of review. Use it for bounded
review judgments wherever it helps, including code, CI, pull requests, product
behavior, and CARR records. Do not ask for a separate Jev-specific approval or
exclude a review merely because its category is not prelisted. Preserve source
evidence, required checks, and any distinct reviewer-of-record requirement.

## Model Room before another model

<!-- carr-model-room-route:start -->
Decision `284028a5-8295-498a-af1e-6cae5c6034e7` records Joe's route:
call Claude, Grok, and other external models through the Model Room. Before
attempting a direct model CLI or API call, pull this rule into the preflight
judgment with Jev and route the work to the named Model Room desk. Verify the
desk's actual model and result. Choose every subagent by the cheapest tier
still qualified to do the task correctly, name it on the call, and never
silently delegate a portion to another model (Joe, 2026-09-23: the earlier
Opus-always line was a temporary usage-window instruction, now retired). A model
CLI used solely for authentication or health readback is not a model-work call.
<!-- carr-model-room-route:end -->

## Authorized code homes and repository boundaries

Decision `1ceee300-7627-426f-b729-ab339d6984fc` supersedes the former
single-code-home rule after Gate Zero. The current placement contract is STORE
doctrine `doctorcre-v5-astra-integration-review`, section
`3bb51d3e-2661-4ea2-a585-053540545b5d@1`, content hash
`d36252e69e32aa8af7c5d79f8dc3cc5365848af83839e1a11c5db5548b3d5c5e`.
Fetch the current section by stable ID at runtime; the observed version and hash
above record this projection's provenance, not a current-version gate.

The only authorized code homes are:

- `jbookout/carr-system`: the CARR-specific runtime and authority — canonical
  business records, domain rules and doctrine, versioned APIs and MCP,
  migrations, runtime control plane, assurance fabric, authentication,
  environments, and CARR releases.
- `jbookout/doctorcre-app`: DoctorCRE's independently built and deployed human
  application, including its UI, interaction logic, project knowledge, and
  app-only state. It uses authenticated versioned CARR contracts and never
  accesses the CARR database directly.
- `jbookout/software-factory`: development-time orchestration and tooling —
  bounded agent workflows, skills, prompts, templates, scaffolds, CI and review
  conventions, evals, release helpers, and factory job/evidence records. It is
  not a product runtime and owns no product data, domain rules, standing
  credentials, or deployment authority.

Do not improvise another repository or silently move code between these homes.
Cross-repository work binds exact source revisions and versioned contracts.
Products must not depend on the software factory at runtime. CARR runtime code
remains here until a second real production consumer proves a neutral,
independently pinnable, testable, and replaceable boundary with measurable
duplication. If an authorized repository is unreachable, STOP and name the
missing repository rather than substituting an unrelated scaffold.

## The shared root is NOT an agent work surface

`~/carr-system` is the stable bootstrap and coordination checkout. It exists so
sessions can load these instructions, inspect shared state read-only, and create
their own isolated worktree or clone. No coding session may implement a change
in that shared checkout.

Before the first repository edit, create and enter a session-owned tree:

```
./run.sh worktree <name>
cd .claude/worktrees/<name>
```

If that helper is unavailable, use a separate clone rather than falling back to
the shared checkout. One session owns one tree and one branch. It owns the full
delivery path too: checks, explicit-path staging, commit, push, pull request,
green CI, merge, verification on `main`, and cleanup. A patch, commit, pushed
branch, or open PR is intermediate state, not completion. Stop short only for a
concrete human-only gate, and name that gate and the exact remaining action.

The only permitted writes in the shared root are deliberate bootstrap or
coordination-state changes to the session machinery itself. Those changes still
must be copied into an isolated tree and delivered through the normal PR path.

## Map work has one mandatory front door

For any request to recommend, design, build, revise, review, or publish a map,
GIS analysis, route, day trip, or Tour surface, call the live `map-architecture`
verb before advising or editing. It returns the current doctrine and the
machine-contract pointer. The configured Stop gate enforces this route.

## main is not directly pushable

Ruleset "main: CI must be green" requires the `ops/ci.sh --strict` status check,
and blocks force-pushes and branch deletion. A direct push to main is refused
with GH013. The path is branch, PR, green CI, merge — and it needs no checkout
and no worktree, which matters because several sessions share this one working
tree:

```
git push origin HEAD:refs/heads/<name>
gh pr create --base main --head <name> --title "..." --body "..."
gh pr merge <n> --squash --delete-branch
```

**And do not COMMIT on main either.** `ops/githooks/pre-commit` refuses it. The
push half was always blocked; the commit half was not, so a commit made on main
in `~/carr-system` simply stranded there — that checkout reached NINE unpushed
commits on 2026-08-14, existing on no other machine, and took an hour to
reconcile. Work in your own tree instead:

```
./run.sh worktree <name>
cd .claude/worktrees/<name>
```

Reconciling that checkout is the one real exception, and it is a commit on main
by definition: `CARR_ALLOW_MAIN_COMMIT=1 git commit ...`, for one command only.

## Checks

`ops/ci.sh` is the ONE check script. The GitHub workflow and the pre-push hook
both call it; neither contains check logic, so a check added there appears in
both. Run one class while iterating:

```
./ops/ci.sh --list
./ops/ci.sh --only <class>
```

`ops/ci-selftest.py` tests the checker itself. Do not remove the bash re-exec at
the top of `ops/ci.sh`: under zsh its class loop does not word-split, and the
script will report every class green having executed none.

## Changes to LLM-steering surfaces ship with an eval

Any change to a surface registered in `evals/surfaces.json` runs
`/claude-api build-eval`, then `/claude-api hillclimb`, and ships with
`evals/<surface>/receipt.json`, or a reasoned `no-eval: <surface>: <reason>`
line in the PR body. Procedure: `evals/README.md`.

Reviewer checklist on the exact head: `ops/check-eval-receipt.py` passed; each
receipt was changed in this PR; its verdict matches its numbers and authorizes
shipping (`ship` or `ship_cost_at_parity`, with no critical regression); each
no-eval line names a real reason a measurement is impossible. An unreadable PR
event fails the check.

## Progress board

For work >5 steps or >30 min, update the board via `tools/progress_board.py`
after each step; record Joe questions with defaults, and give a blocked task
`--reason` and `--next-action`. Every `init`, `task`, `ask`, `answer`,
`deliver` and `note` writes `out/boards/<project>.json` under a per-board lock
and publishes it; the only board UI is https://app.doctorcre.com/progress-board
(`?board=<project>`, or `?board=all-repos` for every jbookout PR). A failed
publish exits nonzero with the local state kept and names the retry
(`render <project> --publish`); `PROGRESS_BOARD_LOCAL_ONLY=1` skips publishing
and says so. There is no static HTML copy. The launchd job runs
`ops/progress-board-render.sh` from a repository checkout, which binds
`CARR_REPO_ROOT` and the repo's `.venv` Python; never run an extracted copy.
The installer uses fleet sync's canonical main checkout.
`install-progress-board --apply` and `verify-progress-board` preserve canonical
`out/boards`; `--repo` accepts only that checkout. Feature worktrees are refused,
including pre-merge verification. Fleet sync rebinds the agent after main
advances. `check-launchd-main-paths` audits installed WorkingDirectory and
program paths for non-main worktrees without changing plists.
A `done` card with no PR is Live (complete). A project card with a merged PR
stays Merged until production shows it: only `--delivery-target worker`
(carr-system) or `app` (doctorcre-app) completes from the release readback;
any other target, or none (a local tool, a LaunchAgent), needs `--stage live
--evidence` naming its operational receipt. An all-repos card goes Live once
a verified release of a lane that deploys every path it changed contains its
merge commit. `failed` and `superseded` need
`--reason` and leave the pipeline for the History list.

## Git discipline on a shared tree

Several sessions run against this one checkout at the same time.

- `git add <explicit paths>` only. Never `-A`, never `-a`, never `.` — a gate
  refuses those, because a broad add once swept another session's work onto the
  wrong branch.
- Commit messages through a file: `git commit -F <file>`. Backticks in an inline
  message get shell-evaluated and silently eat text.
- `core.fileMode` is FALSE here, so `chmod +x` never reaches the index. Use
  `git update-index --chmod=+x <path>` and check the index, not the filesystem.
- Leave any modified file you did not write, and say so.

## Rule lifecycle evidence

Before teaching, admitting, approving, amending or retiring a rule, run
`./.venv/bin/python ops/rule-admission-audit.py --preflight` with the existing
read credential; add `--rule-id <full-rule-UUID>` for its prepared admission.
It derives the `projection.delivery` keys from the installed writer
([migration 0482](migrations/0482_rule_delivery_binding_writer.sql)) and reports
`ready` only when the prepared admission carries every one. Any other status,
or no connection, is a failed readback, never permission to infer readiness.
Approval still goes through the record verbs.

## Writing

Content goes through the record layer's verbs, never into a markdown file — a
hard gate enforces it. `./run.sh call <verb> '<json>'` reaches any verb.
This file and the vault's `CLAUDE.md`/`AGENTS.md` are among the few
exact-path exceptions.

## Active WR-000070 R09 executor recovery

Only for the accepted executor source-recovery role of WR-000070 slice R09: read
[the complete scoped assignment instructions](ops/config/task-boot/r09.json)
before assignment validation or execution. Resolve its current dispatcher intent
and server-issued runbook; all bindings and refusal conditions remain mandatory.

## Temporary supervised WR68 source execution

Only for WR-000068 slice wr68-source-repair-v1: read
[the complete scoped assignment instructions](ops/config/task-boot/wr68.json)
before assignment validation or execution. Resolve its current dispatcher intent
and server-issued runbook; all bindings and refusal conditions remain mandatory.

## Temporary supervised WR69 registered Codex validation

Only for the registered Codex validator of WR-000069 slice wr69-source-repair-v1: read
[the complete scoped assignment instructions](ops/config/task-boot/wr69.json)
before assignment validation or execution. Resolve its current dispatcher intent
and server-issued runbook; all bindings and refusal conditions remain mandatory.

## Temporary supervised R06 registered validation

Only for the registered Codex validator of WR-000070 slice R06: read
[the complete scoped assignment instructions](ops/config/task-boot/r06.json)
before assignment validation or execution. Resolve its current dispatcher intent
and server-issued runbook; all bindings and refusal conditions remain mandatory.

## Before every PR: design and debt pass

Before opening or updating any pull request, apply both skills to the diff:

1. `~/.agents/skills/codebase-design/SKILL.md`: deep modules, real seams, design the interface twice when it matters.
2. `~/.agents/skills/zero-tech-debt/SKILL.md`: rework the change from its intended end state; delete dead compatibility paths and duplicated rules.

Both passes are required. Read the skill files before applying them; if either
is unavailable, report the missing skill instead of claiming the pass.
This section is the canonical policy for both client entry points.

Rigorous engineering work uses `/poteto-mode` from [pstack](plugins/pstack/skills/poteto-mode/SKILL.md).
