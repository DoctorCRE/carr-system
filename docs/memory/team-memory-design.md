# Team memory and dreaming: design (WR-000562, slice S1)

**Outcome.** Every agent (Claude sessions, Codex jobs and cloud routines) gets
one memory that lives entirely in the record layer, in two scopes. It follows
the three layers of Anthropic's talk "Memory and dreaming for self-learning
agents".

- **Storage.** Two record-layer stores.
  - **Org conventions** are the `rule` corpus. It is read-only to agents and
    written only through one human-only door, `adopt-rule`.
  - **Team memory** is one read-write doctrine document whose sections are the
    memory files.
- **Structure.** Agents attach through one new verb, `attach-memory`, and write
  lessons through the existing `write-doctrine-section`.
- **Process.** A nightly dreaming pass on the Studio does three things. It
  clones the team-memory sections into a git directory. It runs one subagent
  per attached session transcript. It applies a verified diff back as one
  atomic, attributed change set.

The design adds four pieces: one table, two verbs, and one dream command with
its launchd job. It removes eleven, listed under Deletions. A twelfth, the
per-call rule-card hook, is decided by measurement; if kept, it is logged as a
named exception.

Joe's words, 2026-10-27: "I want the system to be architecturally engineered
correctly. Not a patch that simulates what it does."

The build happens in slices S2–S10. This slice writes nothing to production.
It adds one operator step at merge: freezing the private memory folder (D13).

## Contents

1. [The talk's architecture, as specified](#1-the-talks-architecture-as-specified)
2. [Today's system, mapped](#2-todays-system-mapped)
3. [Glossary](#3-glossary)
4. [Decisions (grilled)](#4-decisions-grilled)
5. [Storage layer](#5-storage-layer)
6. [Structure layer](#6-structure-layer)
7. [Process layer: sessions and dreaming](#7-process-layer-sessions-and-dreaming)
8. [Deletions and their order](#8-deletions-and-their-order)
9. [Proof](#9-proof)
10. [Slices](#10-slices)
11. [Defects found while mapping](#11-defects-found-while-mapping)

---

## 1. The talk's architecture, as specified

**Sources:**
- The talk itself: YouTube `RtywqDFBYnQ`, with a transcript and 43 scene frames.
- Joe's two slides: "A unified memory system" and "Inside a dreaming pass".
- The platform docs:
  - <https://platform.claude.com/docs/en/managed-agents/memory>
  - <https://platform.claude.com/docs/en/managed-agents/dreams>
  - <https://platform.claude.com/docs/en/managed-agents/self-hosted-sandboxes-memory>

| Talk concept | What it is in Managed Agents | Where it shows |
|---|---|---|
| Memory store | A collection of text documents addressed by path, with a `name` and a `description` that are given to the agent | Docs; demo frames of `org-knowledge`, `team-sre` and `team-code` |
| Scopes | Each store is attached per session with `access: read_only` or `read_write`, up to 8 stores per session. Org knowledge is read-only ("agents read but never write"; "maintained by platform team"); team memory is read-write | "Built for multi-agent systems" slide: `org-conventions` read-only, `team-memory` read-write |
| Optimistic concurrency | Updates can carry `precondition: {type: content_sha256}`. On a mismatch the agent re-reads and retries | "Built for auditability" slide; demo `precondition content_sha256: e58b…` |
| Versions | Every change creates an immutable version, and versions belong to the store. Roll back by writing an old version back. History can be redacted | Demo version strip `v1 … v6 · head` |
| Attribution | "Every memory links to the session that produced it" | Demo `written by sesn_…`, plus per-entry agent and time |
| Attach | Stores are attached at session creation only, and a note describing each mount goes into the system prompt. Self-hosted workers download the store to a directory and sync it back | Docs; slide caption: "what A learns, B and C read on next attach" |
| Real-time memory | Agents read and write while they work, for example "Ruled out: … Next agent: skip dep checks, go straight to config diff." | Demo: agent a07 writes, then a16 reads it and skips the work |
| Dreaming | An asynchronous job. It takes one memory store plus 1–100 session transcripts and writes a new output store; the input is never modified. Its output covers verify, organize and enrich: merge duplicates, replace stale or contradicted entries, add cross-session insights, and add verification notes. You review the output, then use or discard it | "Inside a dreaming pass" slide (clone `$MEM`→`$MEM_OUT`; orchestrator; one subagent per session; subagents read and write `$MEM_OUT`); Dreams console showing token counts in and out (3.7M / 60.9k on `team-sre`) |
| Out of band | Dreaming runs outside the hot path, has a separate objective (memory quality), and can see patterns across agents | "Out-of-band memory updates" slide |
| Three layers | **Storage** (where data lives and what metadata is kept), **Structure** (format, and what Claude is steered to remember), **Process** (how often memory updates, and where it is derived from) | "Components of the memory architecture" slide |

## 2. Today's system, mapped

### 2.1 Record layer and doctrine store

The live registry, checked with `list-verbs` on 2026-10-27, has **337 verbs**.
The memory-relevant ones:

| Capability | Verb(s) | Storage (migration) | Notes |
|---|---|---|---|
| Documents, scope | `new-doctrine-doc`, `doctrine-index`, `read-doctrine` | `doctrine_document` (0075): `content_class` (`playbook, sop, index, reference, dossier_narrative, distillation, rule`), `visibility shared/personal`, `owner_actor_id`, `review_policy_id` | `rule` is reserved "until the P7 rule-store ruling" (`new-doctrine-doc` description). `personal` means the scope a rule applies in, not who may read it |
| Memory "files" | `write-doctrine-section`, `change-doctrine-sections` (atomic, multiple sections), `doctrine-sections` (batch read of up to 50), `retire-doctrine-section` | `doctrine_section` (`section_key`, `current_version`, `body_hash`, `review_after`, `status active/retired`) | `section_key` is the analogue of a memory path |
| Versions | (history inside the read verbs) | `doctrine_revision`: `version`, `parent_revision_id`, `actor_id`, `session_key`, `content_hash` (sha256), `commit_message`; `doctrine_change_set` with `op write/move/retire` | **`session_key` is filled with `actor.client_id`**, the OAuth client, not the session (`mcp-server/src/doctrine.js` L461–471) |
| Precondition | `base_version` on every write; a stale value returns `version_conflict` | `expected_version` in `doctrine_change_set_item` | Same semantics as `content_sha256`. The hash is stored as well |
| Retire with history | `retire-doctrine-section` | `status='retired'` | The analogue of delete-with-versions-kept. `change-doctrine-sections` does **not** yet expose `op: retire`, although the table allows it |
| Staleness clock | (write path) | `review_after = now() + policy.max_age_days` on every write | The natural hook for verification notes |
| Search | `search-doctrine` (lexical + concepts + Workers AI semantic, blended by reciprocal rank) | `doctrine_revision.search_vector`; embeddings (0958) | The analogue of grep across the store |
| Snapshot and generation | `read-doctrine` with `if_generation_match` | `doctrine_snapshot(generation, content_hash)`; `doctrine_meta.generation` | A consistent point-in-time clone source for dreaming |
| Gates | `dry-run-doctrine-gates` | **`doctrine_gate_check`** (0075). "A new gate is a function and a row": a `GATE_IMPLS` entry in `doctrine.js` plus an INSERT | Seeded gates: `base_version_match`, `body_schema` (non-empty `body.text`), `target_exists`, `edge_class_allowed`, `edge_acyclic`, `unresolved_conflict`, **`banned_phrases`** (block; `content_classes: playbook, sop, reference`; the fallback list starts with the em-dash; supports `exempt_slugs`), `slug_unique`, `personal_owner_required`, `claim_holder_or_free`, `no_md_escape` |
| Claims | `claim-doctrine-sections`, `release-doctrine-claims` | `doctrine_claim` | A cooperative lease for long edits |
| Rules (org conventions) | `standing-context` (`boot`/`gist`/`full`/`rule_ids`/`packs`), `applicable-rules`, `find-rule`, `resolve-doctrine-rules` | `rule` (0001): `statement`, `human_quote`, `taught_by`, `scope`, `personal_to`, `status proposed/active/retired/superseded`, `activated_by`/`activated_at` (with the check "no silent activation"), and `supersedes` (the version chain). **189 rules: 160 shared, 29 personal** (boot digest, 2026-10-27) | A versioned, attributed, human-gated store. **No live verb writes it.** `teach`, `activate-rule`, `amend-rule` and `retire-rule` are retired (`ops/config/retired-verbs.v1.json` L155–165) |
| Rule cards | (none; files) | `ops/config/rule-cards.v1.json` (60 cards: 12 core, 24 prompt, 14 tool, 10 planning). Each card cites `source_rule_ids` in `rule`. The file renders into CLAUDE.md/AGENTS.md through `ops/sync-core-rule-cards.py` and is served per tool call by `hooks/rule-pack-preuse-reselection.py` | A condensed delivery projection of the corpus, but its wording is hand-authored in git |
| Human-only acts | e.g. `set-prospect-engagement-status`, `accept-ready-plan` | The registry flag `humanOnly: true` (`mcp-server/src/tools.js`; enforced in `mcp.js`) | The existing mechanism for "Joe or Dell in person" |
| Decision log | `log-decision`, `read-decision`, `update-decision`, `find-precedent` | decision table | Joe's rulings with their rationale |
| A second memory store | `recall-memory` (read only) | `memory_item` + `memory_evidence` (0299; sponsor RLS 0573/0574/0579): kinds, `shared/personal` scope, `candidate → promoted` | **No write verb exists.** Recall returns only promoted rows and returned none for a test query on 2026-10-27. Dependents are listed under deletion #4 |
| Sessions | `read-session-identity`, `list-my-codex-sessions` | `claude_continuity_leaf` (0485), `codex_continuity_checkpoint` (0480), `session_work` (0193) | **No live verb writes the continuity tables.** Nothing links a session to the memory versions it read or wrote |

### 2.2 Private Claude memory

The folder is `~/.claude/projects/-Users-booko-carr-system/memory/`. It holds
`MEMORY.md` plus 104 topic files, about 195 KB in all, and one more file in
`_to_delete/`. Claude Code loads the first 200 lines of `MEMORY.md` into every
Claude session on this repo and into nothing else. Codex, routines and the Dot
never see it.

Each file was read and classified. The classes are:
- **ORG**: a convention Joe stated, which belongs in the read-only scope.
- **TEAM**: working knowledge any agent benefits from.
- **STALE**: superseded, or pointing at deleted things.
- **DUP**: already in a rule, a core card or AGENTS.md.
- **PERSONAL**: about one person rather than the work.

**34 of the 104 files contain an em-dash.**

| Class | Count | Files | Destination |
|---|---|---|---|
| ORG | 41 | automation-invisible-background, batch-joe-blockers, briefs-ban-env-dumps, bug-inventory, build-it-myself-when-asked, carr-system-stays-public, check-platform-before-building, claude-models-for-jobs, client-portal-standard-site, commit-email-private, compliance-out-of-scope, decide-dont-ask-design, flash-off-until-maintenance, independent-taste-score-before-merge, jev-used-as-shipped, keep-orchestrating-without-joe, living-readiness-checklist, main-moved-is-not-rereview, maximize-work-not-tokens, never-ignore-bugs, no-machinery-creep, orchestrator-reviews-audit-deletions, outlook-intake-stays-live, owners-contacted-are-not-leads, paired-prs-server-first, post-call-e2e-review, product-backlog-is-work-requests, quote-joe-not-my-reading, read-tool-docs-first, removals-are-immediate, report-main-clashes-with-cause, requeue-dropped-prs, review-before-automerge-large-prs, review-round-cap, simplest-mechanism-first, skill-use-verified-not-claimed, team-territory, tiny-changes-skip-review, use-tools-as-intended, v1-to-v2-sequence, work-calendar-only-in-outlook | **The `rule` store.** Each is checked against the 189 rules. Covered ones get a ledger line; uncovered ones become one `adopt-rule` batch that Joe approves in one desk session (D1). Any TEAM fragment (a recipe or a path) goes to team memory |
| TEAM | 34 | agent-concurrency-cap, assurance-fixture-repin, background-loops-need-a-deadline, briefs-state-rules-not-values, checkout-current-all-machines, cleanup-before-features, cloud-work-runs-headless, codex-briefs-avoid-attack-wording, codex-cloud-envs, codex-latest-model-families, connector-needs-new-session, dell-mac-ssh-alias, detect-stalled-jobs, dot-on-studio, dot-runs-one-job-at-a-time, email-calendar-top-priority, immediate-email-lane, keep-slots-full, keychain-hang-freezes-codex, live-checkout-stuck-check, macbook-ssh-alias, open-wrs-are-stale, outlook-applescript-send-works, read-area-memories-before-dispatch, read-work-mail-via-parser, security-items-deferred, social-batch-stays, stop-codex-job-whole-tree, studio-is-main-hub, studio-outlook-accounts, trellis-app-reference, wednesday-swarm, where-keys-live, wr557-work-queue-plan | Become `team-memory` sections verbatim, with secrets checked by `ops/ci-secret-scan.py`. The first dream organizes them |
| STALE | 17 | apple-mail-blocked-outlook-workaround, cloud-session-credits, codemap-after-purge, direct-main-push-runs-gates, dispatch-checkout-new-prefix, free-seats-first-for-reviews, front-door-obsolete, heavy-build-obsolete, keep-dot-and-cloud-lanes-fed, model-subscriptions-2026-10, nous-and-grok-first, orchestrator-standard-process, review-pr-replaces-waiters, scac-successor-seal-procedure, start-work-not-suggestion-cards, workers-report-to-orchestrator, `_to_delete/codex-reset-2026-10-02` | Not migrated. The folder archive keeps them as evidence |
| DUP | 12 | briefs-name-pstack-pocock, builders-never-touch-live-checkout, client-sites-on-share-doctorcre, close-done-wrs-with-completion-verb, do-dashboard-steps-in-joes-chrome, dont-carry-dead-questions, grill-runs-autonomous, orchestrator-owns-merges, outlook-lane-owned-by-orchestrator, proposed-rules-are-not-live, search-both-macs-sessions, sizing-step-before-work | Not migrated. The ledger names the rule or card that covers each, and any TEAM fragment goes to team memory |
| PERSONAL | 1 | iphone-home-screen-style | A team-memory section under `joe/`, because it is an operating preference agents act on |

**Contradictions inside the folder.** The first dream must resolve these, and
the seed slice lists them:
- **Merge command:** `orchestrator-owns-merges` says to use `--auto --squash`;
  AGENTS.md says `--squash` is refused.
- **Routines:** `automation-invisible-background` bans them, while
  `cloud-work-runs-headless` uses them.
- **AppleScript send:** `outlook-lane-owned-by-orchestrator` calls it hollow,
  while `outlook-applescript-send-works` says it works.
- **Concurrency cap:** `agent-concurrency-cap` says 10 in its description and 6
  in its body.
- **Top priority:** the cleanup-first files and the email-first files disagree.

**Dangling references.** Three `[[…]]` links point at files that do not exist:
`dot-chat-never-unanswered`, `merge-queue-auto-merges-green` and
`merge-queue-rearm-exhausted`.

**Sensitive values.** Nine TEAM files hold tailnet addresses, chat channel IDs
or environment IDs. They may live in the private record layer, but none are
quoted in this public repo.

### 2.3 How each agent gets context today

| Agent | Context today | Evidence |
|---|---|---|
| Claude Code session | CLAUDE.md (12 core cards); auto-memory `MEMORY.md`; per-call cards from `hooks/rule-pack-preuse-reselection.py` (PreToolUse, UserPromptSubmit and Stop in `~/.claude/settings.json`); the carr MCP instructions ("OPENING ACT, every session: call standing-context FIRST", in `mcp-server/src/mcp.js`) | CLAUDE.md says the cards are "loaded natively… No boot fetch is required", which contradicts the MCP instruction. `hooks/job-watchdog-digest.py` (UserPromptSubmit) also injects up to 5 lines; that is ops status rather than memory, and is out of scope |
| Codex job (`bin/codex-job`, 90 lines) | AGENTS.md (the same 12 cards, generated); a `Skills:` block plus the full SKILL.md text, picked by `bin/skill_picker.py` (`prepare()` L106–156); the same card hook through the local, untracked `~/.codex/hooks.json` (run under `--dangerously-bypass-hook-trust`); the carr MCP from `~/.codex/config.toml` | **Adds no shared rule text.** The briefs carry the rules by hand |
| Hand-written briefs | 35 `~/carr-system/scratchpad/jobs/brief-*.md` (untracked, machine-local), sent by `launch.sh` and `runq.sh` through `bin/codex-job` | The same blocks are copied in by hand: never open .env/credentials (33 of 35), don't edit the live checkout (31), Skills line (29), noreply email (28), no production writes (28), Step 0 (27), sizing (25), auto-merge (22), env/printenv ban (20), never force-push (20), Co-Authored-By (16), migration-number rule (14). `standing-context` appears in 0 briefs |
| `tools/room-bridge/brief-footer.md` | The rules line, the skill map and the VERDICT format | **Nothing on main appends it.** Its appender `dispatch.py` was removed in #2140, yet CLAUDE.md and AGENTS.md still point to it as "the brief rules" |
| Cloud routines (RemoteTrigger) | The prompt text inside each trigger, on the claude.ai side; the repo templates `docs/agents/review-brief.md` and `scratchpad/jobs/brief-cloud-builder.md` (which carries its own copy of the rules); CLAUDE.md through the repo checkout | No `MEMORY.md`. Whether the record-layer connector is attached depends on each trigger (unverified per trigger) |
| The Dot | Only the prompt `bin/review-pr` builds (L29–31: PR URL, SHA, a "correctness-only" instruction, a `MACHINERY` rule string and the diff), sent to Slack through headless `claude -p` | No cards, no AGENTS.md, no memory. Not named in the WR's ATTACH criterion |

### 2.4 Session transcripts

| Source | Location | Volume (2026-10-27) | Consumers |
|---|---|---|---|
| Claude Code | `~/.claude/projects/-Users-booko-carr-system/*.jsonl` (subagents in `<session>/subagents/`) | 416 top-level files (1,358 with subagents), 3.2 GB, 2026-09-26 → today | Read only per session by hooks (`lib/transcript_read.py`, `hooks/rule-claim-gate.py`). Bulk readers `ops/corrections-sweep.py` and `tools/rule-delivery-eval-intake.py` are **not scheduled** |
| Codex | `~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl` | 2,830 files, 7.0 GB (+353 archived) | `ops/codex-history.py` reads only the current rollout |
| Codex job stdout | `~/carr-system/scratchpad/jobs/logs/*.log` | 805 files, 677 MB | **None** |
| Routines | claude.ai run log (`get_run_log`) | n/a | None |

**Nothing consumes transcripts on any schedule.** The talk's input to dreaming
exists on disk and goes unused.

## 3. Glossary

- **Store.** A record-layer home for memory.
  - **`org-conventions`** is the `rule` table, served by `standing-context`.
    It is read-only to agents.
  - **`team-memory`** is one doctrine document, read-write to agents.
- **Access.** Each attach grants one of two modes per store. `read_only` means
  the agent may read the store but not write it. `read_write` means it may do both.
- **Memory.** One section of `team-memory`. Its `section_key` is the path, for
  example `jobs/codex-keychain-hang`, and its body is markdown.
- **Convention.** One active `rule` row. It changes only by superseding: a new
  row with `supersedes` set to the old row.
- **Attach.** One call to `attach-memory` at session start. It returns an
  **attach receipt** containing an `attach_id`, the stores, each store's access
  mode and the team-memory index. It also records one `memory_session` row.
- **Lesson.** A real-time write to `team-memory` made with the `attach_id`.
- **Adoption.** One human-only `adopt-rule` call, which turns Joe's stated
  convention into an active `rule` row.
- **Dream.** One run of `bin/dream`, which goes through these steps:
  - **Clone:** `$MEM` is the store at one generation; `$MEM_OUT` is a git
    directory made from it.
  - **One subagent per session.**
  - **Diff:** the changes to `$MEM_OUT`.
  - **Verify**, then **apply**.
- **Verification note.** A line inside a memory saying when and from which
  session it was last confirmed. Writing one resets `review_after`.

## 4. Decisions (grilled)

Each question was answered from the sources rather than by asking Joe, per the
autonomous grilling rule. "Reopen if" names the evidence that would change the
decision. S2 logs these decisions with `log-decision`; S1 makes no production
writes.

**D1. Where does the read-only scope live: one store, or two?**
*Answer:* The WR's ONE-STORE criterion says "All agent memory lives in the
record layer". The `rule` table is already a store the talk would recognize:
versioned through `supersedes`, attributed through `taught_by` and
`activated_by`, and human-gated (a check forbids silent activation). It is
read-only to agents because its write verbs are retired. What it lacks is any
human write path. Joe's rulings therefore leak into git prose (AGENTS.md,
CLAUDE.md) and into the private folder.

*Decision:* **Org conventions = the `rule` table, one store.**
- **One human door.** The new verb `adopt-rule` is marked `humanOnly: true`,
  the existing registry flag. It takes `{statement, human_quote, scope,
  supersedes?}` and writes an active row with `taught_by` and `activated_by` set
  to the human partner.
- **Agents propose only.** An agent may stage a proposal (a `rule` row with
  `status: proposed`) through the same verb's propose mode, and nothing more.
- **Cards come from the store.** The rule cards become a generated projection:
  each card's wording is stored on its source rule (S4), and
  `ops/sync-core-rule-cards.py` renders `rule-cards.v1.json` and the
  CLAUDE.md/AGENTS.md core-card block *from the store*.
- **What stays in git.** CLAUDE.md and AGENTS.md keep only that generated block
  and a pointer. That block is the **one named exception** to "a pointer at
  most": it is the platform's native always-loaded mount note (Claude Code and
  Codex load these files by design), it is generated, and CI fails on hand
  edits. It is retired when attach alone is measured to hold rule compliance
  (S10).

*Conflict, stated openly:* core card **K12** routes durable corrections to "an
edit to the relevant guidance file (CLAUDE.md, AGENTS.md or a skill) in a PR".
The WR's ONE-STORE criterion and the coordinator's direction override it here.
S4 proposes the K12 rewrite ("propose a rule with `adopt-rule`; Joe adopts it")
in a guidance PR, which binds nobody until merged.

*Reopen if:* Joe rules that git guidance files are part of the read-only scope.
In that case ONE-STORE is amended with him rather than worked around.

**D2. Should team memory be a new store kind, or doctrine with a scope? And
which content class?**
*Answer:* Doctrine sections already have everything the talk's store needs
except session attribution: path-like keys, immutable versions with parents,
actor, content hash, compare-and-swap, retire-with-history, semantic search,
snapshots, a staleness clock and gates. `memory_item` has scope and lineage but
no write verb, no path, no compare-and-swap on content, and a human
promote-gate that would block the real-time reads the demo depends on.

The class matters because `banned_phrases` blocks the em-dash in `playbook`,
`sop` and `reference`. 34 of the 104 files contain one, so a `reference` seed
would fail atomically, and so would many live lessons.

*Decision:* team memory is **one doctrine document**, slug `team-memory`, with
**content class `distillation`** and review policy `team-memory`
(`max_age_days` 30).
- `distillation` is outside the lint by the gate's own scope ("writing-rules
  lint on prospect-visible content classes").
- Team memory is internal agent knowledge distilled from sessions and is never
  prospect-visible, so that is the class it belongs in.
- An `exempt_slugs` entry would instead tell a lie the gate config documents as
  being for "documents that define the bans".
- The other write gates still apply: `body_schema`, `no_md_escape` and
  `claim_holder_or_free`.

S3's acceptance is a passing `dry-run-doctrine-gates` over every seed section.

*Reopen if:* the store passes about 2,000 sections, or a second team (for
example Dell's agents) needs its own read-write store. Either case means a
second document, not a new kind.

**D3. Should the precondition be `content_sha256` or `base_version`?**
*Answer:* They have the same semantics. `base_version` is already the
record-layer law, and `content_hash` is stored and returned. *Decision:* keep
`base_version`. The attach index and the dream clone carry both. *Reopen if*
agents edit materialized files instead of calling verbs.

**D4. How is a write attributed to its session, and how is access enforced?**
*Answer:* `session_key` holds the OAuth client id. *Decision:*
- **Attribution.** The two doctrine write verbs accept an optional `attach_id`.
  When it is present, `session_key = attach_id`.
- **Enforcement.** A new gate, `memory_attach_required`, ships as **a
  `doctrine_gate_check` row** (`severity block`,
  `applies_to {"ops":["write","retire"],"content_classes":["distillation"]}`,
  `config {"slugs":["team-memory"]}`) **plus a `GATE_IMPLS` function** in
  `mcp-server/src/doctrine.js`. The function:
  - returns no finding when the document slug is not in `cfg.slugs`;
  - otherwise looks up `memory_session` by the write's `attach_id`;
  - refuses with `memory_attach_required` when the id is missing or unknown;
  - refuses with `memory_read_only` when `access = 'read_only'`.
- **Tests.** A selftest beside the existing doctrine gate tests covers the
  three outcomes.

*Reopen if:* a surface cannot pass an argument.

**D5. Should attach be a new verb, or `standing-context` extended?**
*Answer:* Attach must record the session, which is a write, and
`standing-context` is a read verb on a front door that hooks gate on. The
platform also separates the two: a session is created with its resources, and
the files are then read. *Decision:* add a new write verb, `attach-memory`.
`standing-context` stays the lookup for rule text, in the same way that reading
files is separate from mounting them. *Reopen if* the two drift into duplicate
payloads; then `standing-context` becomes a thin alias of it.

**D6. Files or verbs: should the store be mounted as files?**
*Answer:* Self-hosted Managed Agents download the store and sync it every 15 s.
For us that would be a sync worker per session on three surfaces. Our verbs
already mirror the platform's standalone memory API, which the talk calls the
portable path. *Decision:* live sessions use **verbs**. The **dream** uses
files, because the slide's clone step is a directory. *Reopen if* agents
demonstrably fail to read or write lessons through verbs (measured in the S5
two-agent test).

**D7. How are sessions recorded as data?**
*Decision:* one new table, `memory_session`, with the columns `attach_id`
(primary key), `surface`, `native_session_id`, `transcript_ref`, `host`,
`actor_id`, `access`, `attached_generation`, `attached_index_digest` and
`attached_at`.
- **Read:** the attach row records which versions the session saw.
- **Write:** `doctrine_revision.session_key = attach_id` records what it wrote.
- **Transcript:** where a surface knows its transcript (the Codex job launcher
  does), it passes `transcript_ref`. Otherwise the dream links the transcript
  by finding the `attach_id` in the receipt inside it.

The table carries no `personal` scope; sessions are shared team data, readable
by both partners. *Reopen if* a native session-id channel appears on every
surface.

**D8. Where does the dream run, and how often?**
*Decision:* It runs nightly at 03:40 CT on the Studio. That is after the
nightly record-layer job at 02:05 and the job cleaner at 03:17. launchd starts
it (`ops/launchd/com.carr.dream.plist` → `bin/run-scheduled.sh dream …` →
`bin/dream`), and `bin/dream` runs headless `claude -p` on the subscription.
There is no paid API, no Claude scheduled task and no sidebar session.
`bin/dream` is the one wrapper: the manual run and the launchd run share it.
*Reopen if* the subscription's weekly limit starts starving daytime work (then
run weekly), or the store churns faster than once a day.

**D9. What is the dream's token budget?**
*Answer:* The demo dream cost 3.7M tokens in and 60.9k out, and cost scales with
transcripts. *Decision:*
- **Cap:** 40 sessions per night, newest first.
- **Condensing:** each transcript is condensed to at most 40k tokens by a reader
  over `lib/transcript_read.py`. It keeps user turns, assistant text, tool names
  and errors, and drops tool-result bodies over 2 KB.
- **Models:** Sonnet subagents with an Opus orchestrator.
- **Hard stop:** at 2.5M input tokens. Sessions it did not read are recorded.

*Reopen if* verification rejects diffs on two consecutive nights, or usage
reports show headroom.

**D10. Is the diff applied automatically, or reviewed first?**
*Answer:* The WR says it "applies only after it verifies". *Decision:* the diff
applies **automatically when the deterministic verifier passes**, as one atomic
change set. Joe gets the dreams log, not an approval ask. *Reopen if* a dream
applies a wrong change that Joe has to correct; then a failing verifier sends
the diff to Joe's review.

**D11. What happens to the per-call rule-card hook?**
*Answer:* Per-call text injection is the mechanism Joe's bar calls simulation,
but deleting it blind could drop rule compliance. *Decision:* measure in S10.
Run `tools/rule-delivery-eval-intake.py` cases with the hook off and attach plus
the core-card block on.
- If rule-citation compliance is within 5 points of the hook-on run, **delete**
  the hook from `~/.claude/settings.json` and `~/.codex/hooks.json`.
- If not, **keep** it as a **named exception to WR-000562**, logged with
  `log-decision`, with its removal condition: the next measurement after the
  card wording lives in the store (S4) and the dream has curated the corpus for
  30 days shows parity.

*Reopen if* the hook's own coverage program retires it first.

**D12. Does the Dot attach?**
*Answer:* The WR's ATTACH criterion names Claude sessions, Codex jobs and
routines. The Dot receives only a prompt, and pasting the index into it would be
text injection without usable memory. *Decision:* **no injection.** The Dot
attaches **through the verb itself** where it can: `bin/review-pr` asks it to
call `attach-memory` (`surface: dot`, `access: read_only`) through its CARR
connector before reviewing. The S6 check is a `memory_session` row with
`surface = dot` after one review. If the Dot has no connector, it stays outside
memory and that is recorded. Nothing is pasted. *Reopen if* the Dot gains or
loses connector access.

**D13. When do writes to the private folder stop?**
*Answer:* `autoMemoryEnabled: false` is a documented Claude Code setting. The
docs do not say whether it also stops loading `MEMORY.md` (unproven).
Switching it off before S3 could strip sessions of the TEAM notes for days.
*Decision:* **writes stop at S1 merge** by freezing the folder read-only
(`chmod -R a-w` on the memory folder). This is one reversible operator step on
the Studio, run by the orchestrator after merge, because this slice is
read-only outside its worktree.
- Reads continue, so no session loses context.
- A Claude attempt to write there fails visibly.
- Lessons from S1 to S2 stay in transcripts, which the first dream reads
  through a one-time backfill (S8: sessions since the S1 merge, not only
  attached ones).
- S3 seeds from the frozen folder, so no late files exist to re-diff.
- `autoMemoryEnabled: false` and the removal follow in S9.

*Reopen if* the freeze breaks session start (then flip the setting at S3 right
after the seed).

**D14. What does the dream do when a section moved since its clone?**
*Decision:* rebase rather than fail. In verify step 5.1, any change unit (one
`_dream/notes.md` entry, so a merge counts as one unit) that touches a section
whose live `current_version` moved since the clone is **dropped from tonight's
diff and recorded**. The rest applies. A dropped unit is re-derived the next
night from the new generation. Nothing auto-retries the same write. *Reopen if*
the same section drops on three consecutive nights. That signals a hot section,
which is then split.

**D15. What happens to `memory_item`: reuse it or drop it?**
*Answer:* Reusing it would mean a second write path, a promote gate and no
compare-and-swap. *Decision:* **drop it** (S9), with every dependent migrated
first. The list is under deletion #4. The precondition is a caller sweep to zero
plus `count(*) = 0`. *Reopen if* Joe wants personal-scope memories; then the
personal scope returns as a `visibility: personal` doctrine document, not as
`memory_item`.

## 5. Storage layer

**Mapping from the platform to the record layer:**

| Platform | Record layer | Exists? | Gap and fix |
|---|---|---|---|
| Read-only org store | `rule` (supersession versions, `taught_by`/`activated_by`) | Store yes; human write path no | **`adopt-rule`** (`humanOnly`) (D1). Card wording moves onto rules, and the card file is generated |
| Read-write team store | `doctrine_document` `team-memory`, class `distillation` | Yes | Created with `new-doctrine-doc` (S2, data only) |
| `access` per attach | `memory_session.access` + gate `memory_attach_required` | No | **New table** (D7) **+ gate row and `GATE_IMPLS` function** (D4) |
| Memory at `path` | `doctrine_section.section_key` | Yes | None |
| `content_sha256` precondition | `base_version` (+ stored `content_hash`) | Yes | None (D3) |
| Version per change | `doctrine_revision` (`version`, `parent_revision_id`); `rule.supersedes` | Yes | None |
| Attribution: agent, session, time | `actor_id`, `session_key`, `created_at` | Partly | `session_key ← attach_id` (D4) |
| Delete, with versions kept | `retire-doctrine-section` | Yes | `change-doctrine-sections` items gain `op: retire` |
| Redact | none | No | Not built. *Reopen if* a secret lands in a memory |
| Store size limit | none | — | The dream keeps the index under 300 sections |

**New schema (S2):** one migration, at the next free number above main.
- `memory_session` (D7).
- The `doctrine_gate_check` row `memory_attach_required` (D4). Its function
  ships in the same PR.
- The `doctrine_review_policy` row `team-memory`, with `max_age_days` 30.

The dreams log is a separate `distillation` document, `dreams-log`, so the
attach index lists only memories.

## 6. Structure layer

### 6.1 The verbs

**`attach-memory`** (new, write)

```
in:  { idempotency_key, surface, native_session_id?, transcript_ref?, access? }
out: { attach_id,
       stores: [
         { name: "org-conventions", access: "read_only",
           boot: <standing-context boot digest + counts>,
           read_with: "standing-context rule_ids / search-doctrine",
           propose_with: "adopt-rule mode=propose" },
         { name: "team-memory", access: "read_write" | "read_only",
           document: "team-memory", generation,
           index: [ { section_key, title, version, content_hash,
                      review_after, last_writer_surface } ],
           instructions: "Check the index before starting. Read a memory with
             doctrine-sections. Write lessons as you learn them with
             write-doctrine-section document=team-memory attach_id=<id>
             base_version=<version>. On version_conflict re-read, then write once." } ] }
```

- The index is the platform's mount note: one line per memory.
- Content is read on demand with `doctrine-sections` or `search-doctrine`.

**`write-doctrine-section` / `change-doctrine-sections`** (existing) gain
`attach_id`; `change-doctrine-sections` items also gain `op: retire`.

**`adopt-rule`** (new, `humanOnly`) is the one write path into org conventions.
Its propose mode is open to agents and adopts nothing.

### 6.2 How each surface calls them

Each surface uses the platform's own mechanism. No new hook injects text.

| Surface | Platform mechanism reused | What changes |
|---|---|---|
| Claude Code session | The MCP server `instructions` (already "OPENING ACT") plus the generated CLAUDE.md block | `mcp.js` instructions: "OPENING ACT: call `attach-memory` first". The generated block gains one "Team memory" line with the same instruction |
| Codex job | AGENTS.md (read natively) plus the carr MCP in `~/.codex/config.toml` | The same generated line in AGENTS.md. `bin/codex-job` puts the job log path into the brief's first line, so the agent can pass `transcript_ref`, and **refuses a brief that contains any of the copied-rule markers** in §2.3. Briefs are untracked, and the launcher is the one place every brief passes, so it holds the line on every job |
| Cloud routine | The repo checkout's CLAUDE.md plus the CARR Record Layer connector on the trigger | The routine template (`docs/agents/review-brief.md`) states the task only. Creating a trigger requires the connector, checked per trigger in S6 |
| The Dot | Its own CARR connector, if present (D12) | `bin/review-pr` asks it to call `attach-memory` read-only. Nothing is pasted |
| Dream | `./run.sh call` | `bin/dream` attaches as `surface: dream` |

**Briefs carry the task only.** Every rule now copied into briefs is a `rule`
row (rendered into the generated block) or a team-memory lesson. Two rules have
no rule row today and are adopted in S4 before any brief is stripped: the
env/printenv/`ps eww` ban and the skill map from `brief-footer.md`.

## 7. Process layer: sessions and dreaming

### 7.1 Sessions as data

```
attach-memory ──► memory_session(attach_id, surface, generation, index digest)   ← versions READ
write-doctrine-section(attach_id) ──► doctrine_revision.session_key = attach_id   ← versions WRITTEN
transcript (jsonl on the Studio) ── contains the attach receipt ──► dream links transcript_ref
```

### 7.2 One dream

This follows `joe-dreaming-pass.png` step for step.

0. **Select.** The `memory_session` rows attached since the last dream, with
   `transcript_ref` resolved. Where it is missing, `bin/dream` searches
   `~/.claude/projects/-Users-booko-carr-system/*.jsonl`, `~/.codex/sessions/`
   and `scratchpad/jobs/logs/` for the `attach_id`.
   - Rows are capped per D9.
   - The first run also backfills sessions since the S1 merge (D13).
   - Sessions without a transcript on the Studio are recorded as unread.
1. **Clone `$MEM` → `$MEM_OUT`.**
   - `read-doctrine team-memory` at generation G.
   - Write `~/carr-local/dreams/<date>/mem/<section_key>.md`, one file per
     section. Each file starts with `section_id`, `version` and
     `content_hash` in front matter.
   - `git init` and commit. The store is not touched.
2. **Orchestrator, one subagent per session.**
   - One headless `claude -p` (Opus) is given `$MEM_OUT`, the condensed
     transcripts, and the dream instructions: verify, organize, enrich, cite
     sessions, keep the index under 300.
   - It starts one Sonnet subagent per transcript with the Agent tool.
3. **Subagents read and write `$MEM_OUT` to reorganize it.** Each subagent:
   - **merges** duplicates;
   - **deletes** stale or contradicted ones;
   - **adds or extends** cross-session patterns;
   - **appends a verification note**, `Verified <date> from session <attach_id>`.

   The orchestrator does a last pass for structure and writes
   `$MEM_OUT/_dream/notes.md`: one entry per change unit, citing its sessions.
4. **Diff.** `git diff` of the clone commit against the working tree.
5. **Verify.** All checks are deterministic. Failing checks 2–5 applies nothing.
   1. **Precondition (rebase, D14):** drop each change unit that touches a
      section whose live version moved since the clone, and record it. The rest
      continues.
   2. **Provenance:** every remaining added, changed or deleted file has a notes
      entry citing at least one input `attach_id` (or the merged files).
   3. **Secrets:** `ops/ci-secret-scan.py` is clean over the changed files.
   4. **Shape:** front matter is intact, section keys are valid, and the index
      is at most 300 sections.
   5. **Gates:** `dry-run-doctrine-gates` passes for every proposed write.
6. **Apply.**
   - One `change-doctrine-sections` call with the dream's `attach_id`, carrying
     `op: write` and `op: retire` items, each with its clone `base_version`.
     It commits everything or nothing.
   - Then one write to the `dreams-log` document: the notes, the counts, the
     dropped units, the token usage and the unread sessions.
7. **Clean up.** Keep `~/carr-local/dreams/<date>/` for 14 days; the job cleaner
   sweeps by age.

**Why not the platform's dreams API?** It bills at API rates and only reads
Managed Agents sessions. Our transcripts are local files on the subscription
fleet. The steps above are the platform's job, run where our transcripts live.

## 8. Deletions and their order

Each item is deleted only after its replacement is proven. Nothing is deleted
in S1.

| # | Deleted | Replaced by | Precondition (the check) | Slice |
|---|---|---|---|---|
| 1 | Hand-copied rule blocks in the 35 `scratchpad/jobs/brief-*.md` | Generated block + `attach-memory` | The env ban and the skill map are adopted rules. The S5 two-agent test passes. A grep over `scratchpad/jobs/brief-*.md` for the 12 copied-block markers returns zero, and `bin/codex-job` refuses any brief that reintroduces one | S6 |
| 2 | `tools/room-bridge/brief-footer.md` and its two pointers (CLAUDE.md, AGENTS.md) | `rule` rows | As #1 | S6 |
| 3 | `MEMORY.md` and the 104-file private folder | `team-memory` (TEAM) + `rule` (ORG) | Frozen at S1 merge (D13). The S3/S4 ledger maps every file. `autoMemoryEnabled: false` is merged. The folder is tarred to `~/carr-local/archive/`, then removed by a human | S9 |
| 4 | `memory_item`, `memory_evidence`, `recall-memory` and their RLS (0573, 0574, 0579) | `team-memory` | The caller sweep reaches zero over every dependent (list below), and `count(*) = 0` | S9 |
| 5 | `bin/learning-weekly.sh`, `bin/learning-monthly.sh` and `ops/learning-*-selftest.py` | Dream | Neither is scheduled by any plist; their registry rows retire in the same migration | S9 |
| 6 | `ops/corrections-sweep.py` + selftest, and the `corrections` miner in `pipelines/learning_jobs.py` | Dream | Unscheduled; callers swept to zero | S9 |
| 7 | `hooks/session-brief.py` + `ops/session-brief-selftest.py` | `attach-memory` | Wired into no settings file | S9 |
| 8 | Manual consolidate and retro passes: `consolidate-memory` use on this folder, and `scratchpad/jobs/brief-retro.md` | Dream | The folder is gone and one dream has applied | S9 |
| 9 | `ops/hermes-memory-check.py` + selftest | Nothing | Confirm Hermes memory is retired; otherwise it stays, out of scope | S9 |
| 10 | Any `recall-memory` mention in MCP instructions or `doc-profile.js` | `attach-memory` | Grep | S9 |
| 11 | `claude_continuity_*` / `codex_continuity_*` as session records | `memory_session` | `read-session-identity` either switches to `memory_session` or keeps them; decided in S9 | S9 |
| 12 | `hooks/rule-pack-preuse-reselection.py` (Claude and Codex hook entries) | `attach-memory` + generated block | The D11 measurement; a keep outcome is a logged exception | S10 |

**Deletion #4, every dependent, migrated in S9 before the drop:**

| Dependent | Migration |
|---|---|
| `mcp-server/src/memory.js` (`memoryTools`) and its import in `mcp-server/src/tools.js` | Delete the module and its import |
| `mcp-server/src/doc-profile.js` verb list; `mcp-server/src/mcp.js` mentions | Remove `recall-memory`; add `attach-memory` where a memory verb is profiled |
| `mcp-server/test/memory-kernel.test.mjs`, `memory-rls-connection.test.mjs` | Delete |
| `mcp-server/test/situation-retrieval-integration.test.mjs`, `doc-profile.test.mjs`, `tool-advertisement.test.mjs`, `bigint-version-responses.test.mjs`, `dot-security-definers-postgres.sql` | Remove their `memory_item`/`recall-memory` cases |
| `ops/private-row-sponsor-rls-gate.py` (uses `memory_item` as its live RLS exemplar; fails "enumeration is vacuous" without it) | Reshape: run the behaviour proof over **every** fenced personal table the enumeration finds, and pass explicitly with "no fenced personal tables" when there are none. The enumeration still fails any new personal-scope table that is unfenced. This keeps the gate's purpose without inventing a table to test |
| `ops/definer-hardening-selftest.py`, `ops/migration-production-proof-selftest.py`, `tools/test-schema-snapshot-roles.py` | Remove `memory_item` expectations |
| `control-room/contracts/memory-evaluation-s0.v1.schema.json` and `control-room/contracts/fixtures/memory-evaluation-s0.synthetic.v1.json`, `ops/fixtures/mcp-catalog/before-2026-10-05.json` | Retire the memory-evaluation contract and its fixture with their callers, swept to zero; mark the historical catalog fixture as frozen |
| `db/schema.sql`, `db/migration-baseline.json`, `bin/schema-snapshot.sh` output | Regenerate after the drop migration |
| `scac-mutation-registry.current.generated.js` | Regenerate through `ops/scac-mutation-inventory.mjs` |

**Order, so nothing is lost mid-migration:**

1. Freeze the folder (S1 merge).
2. Build storage and attach (S2).
3. Seed team memory (S3).
4. Make org conventions one store (S4).
5. Point Claude and Codex at attach and prove the two-agent test (S5).
6. Point routines and the Dot at attach, strip the briefs and delete the footer
   (S6).
7. Ship dreaming (S7, S8).
8. Delete (S9).
9. Measure the hook (S10).

**Count of moving parts.** Four are added: `memory_session`, `attach-memory`,
`adopt-rule`, and `bin/dream` with its plist. Eleven are removed
unconditionally, and item 12 is decided by data.

## 9. Proof

### P1. Two-agent test: real-time sharing (`REALTIME`)

1. **CI (deterministic, S2), against the CI Postgres.** The steps:
   - Attach A, write `incidents/probe-x` as "Ruled out: X (evidence …). Next
     agent: skip X, go straight to Y." The body contains an em-dash, proving the
     class choice.
   - Attach B. B's index shows `incidents/probe-x` at version 1, and the
     revision's `session_key` equals A's `attach_id`.
   - B writes with `base_version` 0 and gets `version_conflict`.
   - A write without `attach_id` is refused by `memory_attach_required`.
   - A write with a read-only attach is refused with `memory_read_only`.

   It fails before S2 because `attach-memory` does not exist.
2. **Live (S5) on the Studio, against a scratch document `team-memory-test`.**
   Two headless `claude -p` Sonnet sessions get the same task. Checking
   hypothesis X means running `tools/memory-probe-x`, a fixture that sleeps 60 s
   and counts its calls.
   - **Pass:** the counter shows one run (A's), B's transcript cites A's memory,
     and B makes fewer tool calls than A.
   - **Fail:** B runs the probe.

### P2. Dreaming fixture (`DREAMING`)

The fixture lives under `ops/fixtures/dream/`, all synthetic:

- **five** near-duplicate sections about one Codex keychain freeze, each written
  by a different `attach_id`;
- one **stale** section telling agents to run `bin/fleet-sync.sh`;
- one section that a transcript confirms;
- three condensed transcripts (`bin/fleet-sync.sh: No such file`, the freeze
  recurring, and the confirmed path used).

**Checks:**

1. **CI (deterministic, S7).** Feed a hand-written `$MEM_OUT` to the verifier and
   the apply step.
   - **Pass:** one change set; 5→1 with four retired; the stale section retired
     with a cited note; `Verified <date> from session <id>` added and
     `review_after` reset; the input generation unchanged until apply; a second
     apply a no-op.
   - **Negative cases:**
     - A concurrent write to one merged section drops only that unit, which is
       recorded, and applies the rest.
     - A planted secret applies nothing.
     - A change without a note applies nothing.
2. **Live (S8).** `bin/dream --fixture ops/fixtures/dream` against a scratch
   document. **Pass:** the same outcomes, checked by a script over the applied
   revisions.

### P3. Deletion and attach safety (`DELETIONS`, `ATTACH`)

- **Ledger** (S3/S4): 105 rows in, 105 dispositions out, and zero unmapped.
- **Briefs** (S6): the marker grep over `scratchpad/jobs/brief-*.md` returns
  zero, and a `bin/codex-job` selftest shows it refuses a brief containing a
  marker.
- **Attach rows** (S5, S6): one row in `memory_session` per surface (`claude`,
  `codex`, `routine`, and `dot` or a recorded "no connector").
- **After S9:**
  - `rg -l "memory_item|recall-memory|brief-footer|corrections-sweep|learning-weekly|session-brief"`
    over tracked code returns only migrations;
  - the private folder is absent;
  - a new attach receipt lists the seeded memories.

## 10. Slices

Each slice fits in one session or less and is listed in build order. The card
IDs match `WR\d+\.S\d+\.J\d+` (`lib/progress_hierarchy.py`). Every slice runs
the design-and-debt pass and reports its net lines.

| Card | Slice | Size | Acceptance check |
|---|---|---|---|
| WR562.S1.J1 | This design (docs only). The orchestrator freezes the private folder at merge (D13) | tiny | Merged; the folder is unwritable and still readable |
| WR562.S2.J1 | Storage + attach. Migration `memory_session`, gate row and review-policy row; the `memory_attach_required` `GATE_IMPLS` function and selftest; `attach-memory`; `attach_id` on both doctrine write verbs; `op: retire`; create `team-memory` and `dreams-log` (class `distillation`); log D1–D15 | medium | P1 CI test red, then green; the existing doctrine gate tests pass |
| WR562.S3.J1 | Seed team memory. The 34 TEAM files plus one PERSONAL file into sections through one `change-doctrine-sections`; TEAM fragments of DUP and ORG files; list the contradictions | small | `dry-run-doctrine-gates` passes over every seed section; the secret scan is clean; ledger TEAM, STALE and DUP rows mapped |
| WR562.S4.J1 | Org conventions as one store. The `adopt-rule` verb (`humanOnly`, with an agent propose mode); card wording onto source rules; `sync-core-rule-cards.py` renders from the store; the 41 ORG files checked and uncovered ones (plus the env ban and the skill map) proposed; Joe adopts the batch in one desk session; K12 rewrite proposed | medium | The ledger has all 105 rows mapped; CI fails a hand edit to the generated block; proposals are listed in `governance-queue` |
| WR562.S5.J1 | Attach for Claude and Codex. MCP instructions; the "Team memory" line in the generated block; the job-log path in `bin/codex-job` | small | P1 live two-agent test passes; one Claude and one Codex `memory_session` row |
| WR562.S6.J1 | Attach for routines and the Dot; strip briefs. Routine template; the Dot connector attach; strip the 35 briefs; `bin/codex-job` refuses copied-rule markers; delete `brief-footer.md` and its pointers | small | P3 brief grep is zero; the launcher selftest passes; routine and Dot rows exist (or "no connector" is recorded) |
| WR562.S7.J1 | Dream: clone, verify (rebase), apply. `bin/dream` subcommands; the condenser; the fixture | medium | P2 CI checks, including the three negative cases |
| WR562.S8.J1 | Dream: orchestrator and schedule. The `claude -p` prompt; the plist through `bin/run-scheduled.sh`; the token cap; the one-time backfill since the S1 merge | small | P2 live fixture passes; one nightly run writes `dreams-log` with token counts |
| WR562.S9.J1 | Deletions 3–11. `autoMemoryEnabled: false`; archive, then a human removes the folder; the `memory_item` dependents (table above), then the drop; remove the learning, corrections and session-brief files | medium | P3 grep and absence checks; CI green, including the reshaped RLS gate |
| WR562.S10.J1 | Hook decision. The D11 measurement; delete the hook, or log the named exception | small | The comparison numbers are in the PR; hook entries removed or the exception logged |

## 11. Defects found while mapping

These were found while mapping and are outside this design. Each needs a Work
Request through `report-problem`, which this slice does not file because it
makes no production writes.

- The S1 brief calls `work-request-card` with `human_ref`. The live schema
  takes `work_request` (`{"work_request":"WR-000562"}`).
- `brief-footer.md` is described as "the brief rules" in CLAUDE.md and
  AGENTS.md, but nothing appends it (deletion #2 resolves this).
- The local, untracked `~/carr-system/.codex/hooks.json` references
  `hooks/builder-agent-gate.py`, which does not exist on main. The hook warns
  and skips on every Agent call.
- `com.carr.fleet-sync` is still installed in `~/Library/LaunchAgents` and runs
  the deleted `bin/fleet-sync.sh` every 5 minutes.
- CLAUDE.md says the core cards need "no boot fetch", while the MCP
  instructions say "call standing-context FIRST". S5's single "attach first"
  line resolves the conflict.
