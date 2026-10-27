# Team memory and dreaming: design (WR-000562, slice S1)

**Outcome.** Every agent (Claude sessions, Codex jobs, cloud routines and the
Dot) gets one team memory in the record layer. It follows the three layers of
Anthropic's talk "Memory and dreaming for self-learning agents":

- **Storage.** The existing doctrine store becomes the memory store. It has two
  scopes. Org conventions (the rule corpus) are read-only to agents. Team
  memory is one read-write doctrine document whose sections are the memory files.
- **Structure.** Agents attach through one new verb, `attach-memory`, and write
  lessons through the existing `write-doctrine-section`.
- **Process.** A nightly dreaming pass on the Studio does three things. It
  clones the store's sections into a git directory. It runs one subagent per
  attached session transcript. It applies a verified diff back to the store as
  one atomic, attributed change set.

The design adds three pieces: one table, one verb and one dream command with
its launchd job. It removes eleven, listed under Deletions, and decides a
twelfth by measurement.

Joe's words, 2026-10-27: "I want the system to be architecturally engineered
correctly. Not a patch that simulates what it does."

The build happens in slices S2–S7. This slice writes nothing to production.

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
| Scopes | Each store is attached per session with `access: read_only` or `read_write`, up to 8 stores per session. Org knowledge is read-only ("agents read but never write"); team memory is read-write | "Built for multi-agent systems" slide: `org-conventions` read-only, `team-memory` read-write |
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
| Documents, scope | `new-doctrine-doc`, `doctrine-index`, `read-doctrine` | `doctrine_document` (0075): `content_class`, `visibility shared/personal`, `owner_actor_id`, `review_policy_id` | `personal` means the scope a rule applies in, not who may read it. Both partners read everything |
| Memory "files" | `write-doctrine-section`, `change-doctrine-sections` (atomic, multiple sections), `doctrine-sections` (batch read of up to 50), `retire-doctrine-section` | `doctrine_section` (`section_key`, `current_version`, `body_hash`, `review_after`, `status active/retired`) | `section_key` is the analogue of a memory path |
| Versions | (history inside the read verbs) | `doctrine_revision`: `version`, `parent_revision_id`, `actor_id`, `session_key`, `content_hash` (sha256), `commit_message`; `doctrine_change_set` with `op write/move/retire` | **`session_key` is filled with `actor.client_id`**, the OAuth client, not the session (`mcp-server/src/doctrine.js` L461–471) |
| Precondition | `base_version` on every write; a stale value returns `version_conflict` | `expected_version` in `doctrine_change_set_item` | Same semantics as `content_sha256`. The hash is stored as well |
| Retire with history | `retire-doctrine-section` | `status='retired'` | The analogue of delete-with-versions-kept. `change-doctrine-sections` does **not** yet expose `op: retire`, although the table supports it |
| Staleness clock | (write path) | `review_after = now() + policy.max_age_days` on every write | The natural hook for verification notes |
| Search | `search-doctrine` (lexical + concepts + Workers AI semantic, blended by reciprocal rank) | `doctrine_revision.search_vector`; embeddings (0958) | The analogue of grep across the store |
| Snapshot and generation | `read-doctrine` with `if_generation_match` | `doctrine_snapshot(generation, content_hash)`; `doctrine_meta.generation` | A consistent point-in-time clone source for dreaming |
| Gates | `dry-run-doctrine-gates` | `doctrine_check` (`impl_key`, `severity block/warn`) | A per-document rule such as "attach required" fits here |
| Claims | `claim-doctrine-sections`, `release-doctrine-claims` | `doctrine_claim` | A cooperative lease for long edits |
| Rules (org conventions) | `standing-context` (`boot`/`gist`/`full`/`rule_ids`/`packs`), `applicable-rules`, `find-rule`, `resolve-doctrine-rules` | `rule` (0001): **189 rules, 160 shared and 29 personal** (boot digest, 2026-10-27) | The agent write verbs (`teach`, `activate-rule`, `amend-rule`, `retire-rule`) are **retired** (`ops/config/retired-verbs.v1.json` L155–165). Rules are therefore already read-only to agents |
| Rule cards | (none; files) | `ops/config/rule-cards.v1.json` (60 cards: 12 core, 24 prompt, 14 tool, 10 planning) → rendered into CLAUDE.md/AGENTS.md by `ops/sync-core-rule-cards.py`, served per tool call by `hooks/rule-pack-preuse-reselection.py` | The second org-convention surface |
| Decision log | `log-decision`, `read-decision`, `update-decision`, `find-precedent` | decision table | Joe's rulings with their rationale |
| A second memory store | `recall-memory` (read only) | `memory_item` + `memory_evidence` (0299, 0573, 0574, 0579): kinds, `shared/personal` scope, `candidate → promoted` | **No write verb exists.** Recall returns only promoted rows and returned none for a test query on 2026-10-27. It is a parallel, unfed store |
| Sessions | `read-session-identity`, `list-my-codex-sessions` | `claude_continuity_leaf` (0485), `codex_continuity_checkpoint` (0480), `session_work` (0193) | **No live verb writes the continuity tables** (their harness was retired). Nothing links a session to the memory versions it read or wrote |

### 2.2 Private Claude memory

The folder is `~/.claude/projects/-Users-booko-carr-system/memory/`. It holds
`MEMORY.md` plus 104 topic files, about 195 KB in all, and one more file in
`_to_delete/`. Claude Code loads `MEMORY.md` into every Claude session on this
repo and into nothing else. Codex, routines and the Dot never see it.

Each file was read and classified. The classes are:
- **ORG**: a convention Joe stated, which belongs in read-only org conventions.
- **TEAM**: working knowledge any agent benefits from.
- **STALE**: superseded, or pointing at deleted things.
- **DUP**: already in AGENTS.md, CLAUDE.md or the core cards.
- **PERSONAL**: about one person rather than the work.

| Class | Count | Files | Destination |
|---|---|---|---|
| ORG | 41 | automation-invisible-background, batch-joe-blockers, briefs-ban-env-dumps, bug-inventory, build-it-myself-when-asked, carr-system-stays-public, check-platform-before-building, claude-models-for-jobs, client-portal-standard-site, commit-email-private, compliance-out-of-scope, decide-dont-ask-design, flash-off-until-maintenance, independent-taste-score-before-merge, jev-used-as-shipped, keep-orchestrating-without-joe, living-readiness-checklist, main-moved-is-not-rereview, maximize-work-not-tokens, never-ignore-bugs, no-machinery-creep, orchestrator-reviews-audit-deletions, outlook-intake-stays-live, owners-contacted-are-not-leads, paired-prs-server-first, post-call-e2e-review, product-backlog-is-work-requests, quote-joe-not-my-reading, read-tool-docs-first, removals-are-immediate, report-main-clashes-with-cause, requeue-dropped-prs, review-before-automerge-large-prs, review-round-cap, simplest-mechanism-first, skill-use-verified-not-claimed, team-territory, tiny-changes-skip-review, use-tools-as-intended, v1-to-v2-sequence, work-calendar-only-in-outlook | Check each against the rule corpus, the decision log and AGENTS.md. The uncovered ones go through the guidance-PR route (AGENTS.md § Guidance changes), never into team memory |
| TEAM | 34 | agent-concurrency-cap, assurance-fixture-repin, background-loops-need-a-deadline, briefs-state-rules-not-values, checkout-current-all-machines, cleanup-before-features, cloud-work-runs-headless, codex-briefs-avoid-attack-wording, codex-cloud-envs, codex-latest-model-families, connector-needs-new-session, dell-mac-ssh-alias, detect-stalled-jobs, dot-on-studio, dot-runs-one-job-at-a-time, email-calendar-top-priority, immediate-email-lane, keep-slots-full, keychain-hang-freezes-codex, live-checkout-stuck-check, macbook-ssh-alias, open-wrs-are-stale, outlook-applescript-send-works, read-area-memories-before-dispatch, read-work-mail-via-parser, security-items-deferred, social-batch-stays, stop-codex-job-whole-tree, studio-is-main-hub, studio-outlook-accounts, trellis-app-reference, wednesday-swarm, where-keys-live, wr557-work-queue-plan | Become `team-memory` sections verbatim, with secrets checked by `ops/ci-secret-scan.py`. The first dream then organizes them |
| STALE | 17 | apple-mail-blocked-outlook-workaround, cloud-session-credits, codemap-after-purge, direct-main-push-runs-gates, dispatch-checkout-new-prefix, free-seats-first-for-reviews, front-door-obsolete, heavy-build-obsolete, keep-dot-and-cloud-lanes-fed, model-subscriptions-2026-10, nous-and-grok-first, orchestrator-standard-process, review-pr-replaces-waiters, scac-successor-seal-procedure, start-work-not-suggestion-cards, workers-report-to-orchestrator, `_to_delete/codex-reset-2026-10-02` | Not migrated. The folder archive keeps them as evidence |
| DUP | 12 | briefs-name-pstack-pocock, builders-never-touch-live-checkout, client-sites-on-share-doctorcre, close-done-wrs-with-completion-verb, do-dashboard-steps-in-joes-chrome, dont-carry-dead-questions, grill-runs-autonomous, orchestrator-owns-merges, outlook-lane-owned-by-orchestrator, proposed-rules-are-not-live, search-both-macs-sessions, sizing-step-before-work | Not migrated. Any TEAM fragment inside one (for example a how-to recipe) goes into the matching TEAM section |
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
or environment IDs. They may live in the private record layer, but never in
this public repo, so the seed slice quotes none of them here.

### 2.3 How each agent gets context today

| Agent | Context today | Evidence |
|---|---|---|
| Claude Code session | CLAUDE.md (12 core cards); auto-memory `MEMORY.md`; per-call cards from `hooks/rule-pack-preuse-reselection.py` (PreToolUse, UserPromptSubmit and Stop in `~/.claude/settings.json`); `job-watchdog-digest.py` lines; the carr MCP instructions ("OPENING ACT, every session: call standing-context FIRST", in `mcp-server/src/mcp.js`) | CLAUDE.md says the cards are "loaded natively… No boot fetch is required", which contradicts the MCP instruction |
| Codex job (`bin/codex-job`, 90 lines) | AGENTS.md (the same 12 cards, generated); a `Skills:` block plus the full SKILL.md text, picked by `bin/skill_picker.py` (`prepare()` L106–156); the same card hook through `~/.codex/hooks.json` (run under `--dangerously-bypass-hook-trust`); the carr MCP from `~/.codex/config.toml` | **Adds no shared rule text.** The briefs carry the rules by hand |
| Hand-written briefs | 35 `~/carr-system/scratchpad/jobs/brief-*.md` (untracked, machine-local), sent by `launch.sh` and `runq.sh` | The same blocks are copied in by hand: never open .env/credentials (33 of 35), don't edit the live checkout (31), Skills line (29), noreply email (28), no production writes (28), Step 0 (27), sizing (25), auto-merge (22), env/printenv ban (20), never force-push (20), Co-Authored-By (16), migration-number rule (14). `standing-context` appears in 0 briefs |
| `tools/room-bridge/brief-footer.md` | The rules line, the skill map and the VERDICT format | **Nothing on main appends it.** Its appender `dispatch.py` was removed in #2140, yet CLAUDE.md and AGENTS.md still point to it as "the brief rules" |
| Cloud routines (RemoteTrigger) | The prompt text inside each trigger, on the claude.ai side; the repo templates `docs/agents/review-brief.md` and `scratchpad/jobs/brief-cloud-builder.md` (which carries its own copy of the rules); CLAUDE.md through the repo checkout | No `MEMORY.md`. Whether the record-layer connector is attached depends on each trigger (unverified per trigger) |
| The Dot | Only the prompt `bin/review-pr` builds (L29–31: PR URL, SHA, a "correctness-only" instruction, a `MACHINERY` rule string and the diff), sent to Slack through headless `claude -p` | No cards, no AGENTS.md, no memory |

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

- **Store.** A doctrine document that holds memory.
  - `org-conventions` is the virtual name for the rule corpus served by
    `standing-context`.
  - `team-memory` is a real doctrine document.
- **Access.** Each attach grants one of two modes per store. `read_only` means
  the agent may read the store but not write it. `read_write` means it may do both.
- **Memory.** One section of `team-memory`. Its `section_key` is the path, for
  example `jobs/codex-keychain-hang`, and its body is markdown.
- **Attach.** One call to `attach-memory` at session start. It returns an
  **attach receipt** containing an `attach_id`, the stores, each store's access
  mode and the team-memory index. It also records one `memory_session` row.
- **Lesson.** A real-time write to `team-memory` made with the `attach_id`.
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
decision. The S2 build logs each decision with `log-decision`; this slice makes
no production writes.

**D1. Should org conventions be the doctrine store, read-only to agents?**
*Answer:* Org conventions are the **rule corpus** (`rule` table, 189 rules) and
the core cards generated from it, read through `standing-context`. Agents
already cannot write them, because the teach and amend verbs are retired.
Doctrine documents stay a shared reference library that agents search.
*Decision:* Org conventions = the rule corpus, read-only to agents. New
conventions arrive only through the guidance-PR route and Joe's rulings
(`log-decision`). *Reopen if* an agent write path to `rule` reappears, or Joe
wants runbook-style doctrine documents attached as a read-only store.

**D2. Should team memory be a new store kind, or doctrine with a scope?**
*Answer:* Doctrine sections already have everything the talk's store needs
except session attribution: path-like keys, immutable versions with parents,
actor, content hash, compare-and-swap, retire-with-history, semantic search,
snapshots, a staleness clock and gates. `memory_item` has scope and lineage but
no write verb, no path, no compare-and-swap on content, and a human
promote-gate that would block the real-time reads the demo depends on.
*Decision:* Team memory is **one doctrine document**, slug `team-memory`,
content class `reference`, review policy `team-memory` with a 30-day
`max_age_days`. `memory_item`, `memory_evidence` and `recall-memory` are
deleted (S6). *Reopen if* the store passes about 2,000 sections, or a second
team (for example Dell's agents) needs its own read-write store. Either case
means a second document, not a new kind.

**D3. Should the precondition be `content_sha256` or `base_version`?**
*Answer:* They have the same semantics. `base_version` is already the
record-layer law, and `content_hash` is stored and returned. *Decision:* keep
`base_version`. The attach index and the dream clone carry both, so a sync
based on file hashes can be added without a schema change. *Reopen if* agents
edit materialized files instead of calling verbs.

**D4. How is a write attributed to its session?**
*Answer:* `session_key` holds the OAuth client id, so every Claude session looks
the same. *Decision:* The write verbs accept an optional `attach_id`. When it is
present, `session_key = attach_id`. A gate (`doctrine_check` row
`memory_attach_required`, applied to `team-memory`) refuses writes to team
memory that carry no `attach_id`, or whose attach was read-only. This uses the
existing gate framework. *Reopen if* a surface cannot pass an argument.

**D5. Should attach be a new verb, or `standing-context` extended?**
*Answer:* Attach must record the session, which is a write, and
`standing-context` is a read verb on a front door that hooks gate on. The
platform also separates the two: a session is created with its resources, and
the files are then read. *Decision:* add a new write verb, `attach-memory`. It
embeds the `standing-context` boot digest (org conventions), the team-memory
index and access modes, and the `attach_id`. `standing-context` stays the
lookup for rule text, in the same way that reading files is separate from
mounting them. *Reopen if* the two drift into duplicate payloads; then
`standing-context` becomes a thin alias of it.

**D6. Files or verbs: should the store be mounted as files?**
*Answer:* Self-hosted Managed Agents download the store and sync it every 15 s.
For us that would be a sync worker per session on three surfaces: a new moving
part with its own conflict rules. Our verbs already mirror the platform's
standalone memory API (create, update with precondition, list, versions), which
the talk calls the portable path. *Decision:* live sessions use **verbs**. The
**dream** uses files, because it runs locally and the slide's clone step is
literally a directory. *Reopen if* agents demonstrably fail to read or write
lessons through verbs (measured in S4's two-agent test).

**D7. How are sessions recorded as data?**
*Answer:* No live writer covers sessions, and agents do not reliably know their
own native session id. *Decision:* one new table, `memory_session`, with the
columns `attach_id`, `surface`, `native_session_id`, `transcript_ref`, `host`,
`actor_id`, `access`, `attached_generation`, `attached_index_digest` and
`attached_at`.
- **Read:** the attach row records which versions the session saw. The index
  digest pins the section versions.
- **Write:** `doctrine_revision.session_key = attach_id` records what it wrote.
- **Transcript:** where a surface knows its transcript (the Codex job launcher
  does), it passes `transcript_ref`. Otherwise the dream links the transcript
  by finding the `attach_id` in the receipt inside it and fills
  `transcript_ref`.

*Reopen if* a native session-id channel appears on every surface.

**D8. Where does the dream run, and how often?**
*Decision:* It runs nightly at 03:40 CT on the Studio. That is after the nightly
record-layer job at 02:05 and the job cleaner at 03:17, and outside working
hours. launchd starts it (`ops/launchd/com.carr.dream.plist` → `bin/run-scheduled.sh dream …` → `bin/dream`), and `bin/dream` runs headless
`claude -p` on the subscription. There is no paid API, no Claude scheduled task
and no sidebar session. *Reopen if* the subscription's weekly limit starts
starving daytime work (then run weekly), or the store churns faster than once a
day.

**D9. What is the dream's token budget?**
*Answer:* The talk's demo dream cost 3.7M tokens in and 60.9k out, and cost
scales with the number and length of transcripts. Our transcripts average
several MB each, mostly tool output. *Decision:*
- **Cap:** 40 sessions per night, chosen from sessions that attached in the
  last 24 h, newest first. The platform limit is 100.
- **Condensing:** each transcript is condensed to at most 40k tokens by a
  reader over `lib/transcript_read.py`. It keeps user turns, assistant text,
  tool names and errors, and drops tool-result bodies over 2 KB.
- **Models:** Sonnet subagents with an Opus orchestrator.
- **Hard stop:** at 2.5M input tokens per run. The run then records what it did
  not read.

*Reopen if* verification quality drops (the dreams log shows rejected diffs on
two consecutive nights), or usage reports show headroom to raise the cap.

**D10. Is the diff applied automatically, or reviewed first?**
*Answer:* Joe's acceptance criterion says it "applies only after it verifies".
The platform allows either. *Decision:* the diff applies **automatically when
the deterministic verifier passes**, as one atomic change set. A failing
verifier applies nothing. Rollback writes an old revision back. Joe gets the
dreams log section, not an approval ask. *Reopen if* a dream applies a wrong
change that Joe has to correct; then a failing verifier sends the diff to Joe's
review.

**D11. What happens to the per-call rule-card hook?**
*Answer:* The talk's model is a mount description plus agent-driven reads.
Per-call card injection is a different mechanism with its own coverage program.
Deleting it blind could drop rule compliance. *Decision:* it is decided in S7
by measurement. Run `tools/rule-delivery-eval-intake.py` cases with the hook
off and attach plus core cards on. If rule-citation compliance is within 5
points of the hook-on run, delete the hook from `~/.claude/settings.json` and
`~/.codex/hooks.json`. If not, keep it and log the decision. *Reopen if* the
rule corpus moves into team memory, or the hook's own program retires it.

**D12. How does the Dot attach, given it cannot call verbs?**
*Decision:* `bin/review-pr` attaches on the Dot's behalf with
`surface: dot, access: read_only` and puts the receipt's team-memory index into
the prompt it already builds. The Dot writes nothing; its verdicts remain PR
comments. *Reopen if* the Dot gains MCP access.

## 5. Storage layer

**Mapping from the platform to the record layer:**

| Platform | Record layer | Exists? | Gap and fix |
|---|---|---|---|
| `memory_store` (name, description) | `doctrine_document` `team-memory` (title, plus a `README` section as its description) | Yes | Created with `new-doctrine-doc` (S2, data only) |
| Read-only org store | Rule corpus via `standing-context` | Yes, and already read-only to agents | None |
| `access` per attach | `memory_session.access` + gate `memory_attach_required` | No | **New table** (D7) **+ one gate row** (D4) |
| Memory at `path` | `doctrine_section.section_key` | Yes | None |
| `content_sha256` precondition | `base_version` (+ stored `content_hash`) | Yes | None (D3) |
| Version per change | `doctrine_revision` (`version`, `parent_revision_id`) | Yes | None |
| Attribution: agent, session, time | `actor_id`, `session_key`, `created_at` | Partly | `session_key ← attach_id` (D4) |
| Delete, with versions kept | `retire-doctrine-section` | Yes | `change-doctrine-sections` items gain `op: retire`, so a dream applies atomically. The column already allows it |
| Redact | none | No | Not built. *Reopen if* a secret lands in a memory; then a revision-redact verb mirroring the platform's `redact` |
| List with prefix | the `read-doctrine` snapshot | Yes | The attach index serves it |
| Store size limit (10,000) | none | — | The dream keeps the index under 300 sections (D9 instructions) |

**New schema (S2):** one migration, at the next free number above main.
- `memory_session` with the columns in D7. Its primary key is `attach_id`, a
  uuid. `access` is `read_only` or `read_write`. `surface` is one of `claude`,
  `codex`, `routine`, `dot` or `dream`.
- One `doctrine_check` row for the gate `memory_attach_required`. Its `applies_to`
  is `{document: "team-memory"}` and its severity is `block`.
- One `doctrine_review_policy` row, `team-memory`, with `max_age_days` 30.

## 6. Structure layer

### 6.1 The two verbs

**`attach-memory`** (new, write)

```
in:  { idempotency_key, surface, native_session_id?, transcript_ref?, access? }
out: { attach_id,
       stores: [
         { name: "org-conventions", access: "read_only",
           boot: <standing-context boot digest + counts>,
           read_with: "standing-context rule_ids / search-doctrine" },
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
- Content is read on demand with `doctrine-sections` (up to 50 per call) or
  `search-doctrine`.
- `access` defaults to `read_write`, except for surfaces `dot` and `dream`
  (`dream` attaches `read_write`, but only to the apply step).

**`write-doctrine-section` / `change-doctrine-sections`** (existing) gain
`attach_id`. `change-doctrine-sections` items also gain `op: retire`.

There is no third verb. Rollback, history and search use the existing
read verbs.

### 6.2 How each surface calls them

Each surface uses the platform's own mechanism. No new hook injects text.

| Surface | Platform mechanism reused | What changes |
|---|---|---|
| Claude Code session | The MCP server `instructions` (already "OPENING ACT") plus a CLAUDE.md pointer | `mcp.js` instructions: "OPENING ACT: call `attach-memory` first". CLAUDE.md gets a three-line "Team memory" section with the same instruction and the write rule. `autoMemoryEnabled: false` in the repo's `.claude/settings.json`, so the private folder stops refilling (a documented Claude Code setting) |
| Codex job | AGENTS.md (read natively) plus the carr MCP already in `~/.codex/config.toml` | An AGENTS.md "Team memory" section, identical to CLAUDE.md's and generated from the same source by `ops/sync-core-rule-cards.py`. `bin/codex-job` does not attach for the job, because the agent attaches itself. The launcher only puts the job log path into the brief's first line so the agent can pass `transcript_ref` |
| Cloud routine | The repo checkout's CLAUDE.md plus the CARR Record Layer connector on the trigger | The routine template (`docs/agents/review-brief.md`) states the task only. Creating a trigger requires the connector (verified per trigger in S4) |
| The Dot | The prompt `bin/review-pr` already builds | `bin/review-pr` calls `./run.sh call attach-memory '{"surface":"dot","access":"read_only",…}'` and adds the index (D12) |
| Dream | `./run.sh call` | `bin/dream` attaches as `surface: dream` and applies with that `attach_id` |

**Briefs carry the task only.** Every rule now copied into briefs is either in
AGENTS.md/CLAUDE.md (org conventions the agents already load) or a team-memory
lesson.

The one rule missing from AGENTS.md today is the env/printenv/`ps eww` ban. It
moves into AGENTS.md § Authorized code homes in S4, before any brief is
stripped. The skill map in `brief-footer.md` moves into AGENTS.md § Before
every PR. The footer is then deleted.

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
   and `scratchpad/jobs/logs/` for the `attach_id`. Rows are capped per D9.
   Sessions without a transcript on the Studio, such as routines and the Dot,
   are listed in the dreams log as unread.
1. **Clone `$MEM` → `$MEM_OUT`.**
   - `read-doctrine team-memory` at generation G (the snapshot).
   - Write `~/carr-local/dreams/<date>/mem/<section_key>.md`, one file per
     section. Each file starts with `section_id`, `version` and
     `content_hash` in front matter.
   - `git init` and commit. The store is not touched.
2. **Orchestrator, one subagent per session.**
   - One headless `claude -p` (Opus) is given `$MEM_OUT`, the list of condensed
     transcripts, and the dream instructions: verify, organize, enrich, cite
     sessions, keep the index under 300.
   - It starts one Sonnet subagent per transcript with the Agent tool.
3. **Subagents read and write `$MEM_OUT` to reorganize it.** Each subagent reads
   its transcript and edits files with ordinary file tools:
   - **merge** duplicates into one file and delete the others;
   - **delete** stale or contradicted ones;
   - **add or extend** cross-session patterns;
   - **append a verification note** to each memory the transcript confirmed:
     `Verified <date> from session <attach_id>`.

   The orchestrator does a last pass for structure and writes
   `$MEM_OUT/_dream/notes.md`. That file records each change with the sessions
   that justify it.
4. **Diff.** `git diff` of the commit from step 1 against the working tree. This
   is the output. It is printed into the dreams log whether or not it applies.
5. **Verify.** All checks are deterministic; failing any of them applies nothing.
   1. **Precondition:** every changed or deleted file's front-matter `version`
      equals the section's live `current_version`. If any moved since the
      clone, the run stops and records `stale_clone`. Tomorrow's dream starts
      from the new generation; nothing retries.
   2. **Provenance:** every added, changed or deleted file has a
      `_dream/notes.md` entry citing at least one input `attach_id` (or, for a
      merge, the merged files).
   3. **Secrets:** `ops/ci-secret-scan.py` finds nothing in the changed files.
   4. **Shape:** front matter is intact, section keys are valid, and the index
      is at most 300 sections. No section exceeds the size gate.
   5. **Gates:** `dry-run-doctrine-gates` passes for every proposed write.
6. **Apply.** One `change-doctrine-sections` call with `attach_id` = the dream's
   attach. It has `op: write` items for changed and new files and
   `op: retire` items for deleted ones, each carrying the clone's
   `base_version`. The call either commits everything or nothing. The
   `commit_message` holds the dream id, generation G and the input count.
   The dream also writes the `dreams/log` section in the same change set: the
   notes, the counts, the token usage and the unread sessions. Revisions keep
   every past log.
7. **Clean up.** Keep `~/carr-local/dreams/<date>/` for 14 days; the job
   cleaner already sweeps by age.

**Why not the platform's dreams API?** It bills at API token rates and only
reads Managed Agents sessions. Our fleet runs on the subscription and its
transcripts are local files. The steps above are the platform's job, run where
our transcripts live.

## 8. Deletions and their order

Each item is deleted only after its replacement is proven. Nothing is deleted
in S1.

| # | Deleted | Replaced by | Precondition (the check) | Slice |
|---|---|---|---|---|
| 1 | Hand-copied rule blocks in the 35 `scratchpad/jobs/brief-*.md` (machine-local) | AGENTS.md/CLAUDE.md + `attach-memory` | The env/`ps eww` ban and the skill map are in AGENTS.md; S4's two-agent test passes | S4 |
| 2 | `tools/room-bridge/brief-footer.md` and its pointers (CLAUDE.md "brief rules" link, AGENTS.md § Before every PR link) | AGENTS.md sections | Same as #1 | S4 |
| 3 | `MEMORY.md` and the 104-file private folder | `team-memory` (TEAM) + rule corpus and guidance PR (ORG) | A seed ledger maps every file to a section, rule, decision or AGENTS.md line, or to "stale/dup, archived". `autoMemoryEnabled: false` is merged. The folder is tarred to `~/carr-local/archive/` before removal; a human runs the removal | S6 |
| 4 | `memory_item`, `memory_evidence`, the `recall-memory` verb, and the RLS from 0573/0574/0579 | `team-memory` | `select count(*) from memory_item` = 0 at deletion time (recall returned none on 2026-10-27) | S6 |
| 5 | `bin/learning-weekly.sh`, `bin/learning-monthly.sh` and their selftests (`ops/learning-*-selftest.py`) | Dream | Neither is scheduled by any plist; check that registry rows referencing them retire in the same migration | S6 |
| 6 | `ops/corrections-sweep.py` + `ops/corrections-sweep-selftest.py`, and the `corrections` miner in `pipelines/learning_jobs.py` (the weekly feature-cell report stays unless it has no reader) | Dream, which reads the same transcripts on a schedule | Unscheduled today; grep callers to zero | S6 |
| 7 | `hooks/session-brief.py` + `ops/session-brief-selftest.py` | `attach-memory` | Wired into no settings file today | S6 |
| 8 | Manual consolidate and retro passes: the `anthropic-skills:consolidate-memory` use on this folder, and `scratchpad/jobs/brief-retro.md` | Dream | The folder no longer exists, and one dream has applied | S6 |
| 9 | `ops/hermes-memory-check.py` + selftest | Nothing (it guards Hermes memory files) | Confirm Hermes memory is retired; otherwise it stays and is out of scope | S6 |
| 10 | The `memory`/`recall` mention in MCP instructions, if any | `attach-memory` | Grep | S6 |
| 11 | `claude_continuity_*` / `codex_continuity_*` as session records | `memory_session` | Only `read-session-identity` reads them; decide in S6 whether that verb switches to `memory_session` or keeps them | S6 |
| 12 | `hooks/rule-pack-preuse-reselection.py` (Claude and Codex hook entries) | `attach-memory` + core cards | The D11 measurement | S7 |

**Order, so nothing is lost mid-migration:**

1. Build storage (S2).
2. Seed and get a ledger with zero unmapped files (S3).
3. Point every surface at attach, prove the two-agent test, then strip the
   briefs and the footer (S4).
4. Ship dreaming and prove the fixture dream (S5).
5. Delete the folder and the dead machinery, archiving first (S6).
6. Measure, then decide on the hook (S7).

The private folder stays readable until S6. Sessions running between S3 and S6
use both, and the seed ledger is re-diffed against the folder just before
deletion so that any late file is caught.

**Count of moving parts.** Today the memory and context surfaces are items 1–12
above. Three are added: `memory_session`, `attach-memory`, and `bin/dream` with
its plist. Eleven are removed unconditionally (items 1–11), and item 12 is
decided by data.

## 9. Proof

### P1. Two-agent test: real-time sharing (`REALTIME`)

The check comes first. It runs at two levels.

1. **CI (deterministic, S2), against the CI Postgres.** The steps:
   - Attach A, write `incidents/probe-x` as "Ruled out: X (evidence …). Next
     agent: skip X, go straight to Y."
   - Attach B. B's index shows `incidents/probe-x` at version 1 with A's
     surface, and the revision's `session_key` equals A's `attach_id`.
   - B writes with `base_version` 0 and gets `version_conflict`.
   - A write without `attach_id` is refused by `memory_attach_required`.
   - A write with a read-only attach is refused.

   It fails before S2 because `attach-memory` does not exist.
2. **Live (S4) on the Studio, against a scratch document `team-memory-test`.**
   Two headless `claude -p` Sonnet sessions get the same task: "find why fixture
   F fails". Checking hypothesis X means running `tools/memory-probe-x`, a
   fixture script that sleeps 60 s and records each call to a counter file.
   - A runs the probe, rules X out and writes the lesson.
   - B attaches afterwards.

   **Pass:** the counter shows exactly one probe run (A's). B's transcript
   cites A's memory and goes straight to Y. B's tool calls number fewer than
   A's.
   **Fail:** B runs the probe.

### P2. Dreaming fixture (`DREAMING`)

The fixture lives under `ops/fixtures/dream/`, all synthetic with no real data:

- a store with **five** near-duplicate sections about the same Codex keychain
  freeze, each written by a different `attach_id`;
- one **stale** section telling agents to run `bin/fleet-sync.sh`;
- one section about the job log location that a transcript confirms;
- three condensed transcripts. One shows `bin/fleet-sync.sh: No such file`, one
  hits the keychain freeze again, and one uses the log location successfully.

**Checks:**

1. **CI (deterministic, S5a).** Feed a hand-written `$MEM_OUT` (the expected
   dream output) to the verifier and the apply step.
   - **Pass:** one change set; the five merged into one section citing all five
     `attach_id`s, with four retired; the stale section retired, its note citing
     the transcript; the confirmed section carrying
     `Verified <date> from session <id>` with `review_after` reset; the input
     generation unchanged until apply; a second apply a no-op.
   - **Negative cases:** a concurrent write between clone and apply produces
     `stale_clone` and zero writes. A planted secret fails verification with
     zero writes. A change without a note fails provenance with zero writes.
2. **Live (S5b).** `bin/dream --fixture ops/fixtures/dream` runs the real
   orchestrator against a scratch document. **Pass:** the same three outcomes
   (5→1, stale removed, verification note added), checked by a script over the
   applied revisions rather than by reading the model's own report.

### P3. Deletion safety (`DELETIONS`)

- **Seed ledger** (S3): 105 rows in, 105 dispositions out, and zero unmapped.
  It is re-run just before the S6 removal.
- **After S6**, all three must hold:
  - `rg -l "memory_item|recall-memory|brief-footer|corrections-sweep|learning-weekly|session-brief"`
    over tracked code returns only migrations;
  - `~/.claude/projects/-Users-booko-carr-system/memory/` is absent;
  - a new session's attach receipt lists the seeded memories.

## 10. Slices

Each slice fits in one session or less and is listed smallest first. Every
slice runs the design-and-debt pass (AGENTS.md) and reports its net lines.

| Card | Slice | Size | Acceptance check |
|---|---|---|---|
| WR562.S1.J1 | This design (docs only) | tiny | Merged; no production write |
| WR562.S2.J1 | Storage + attach. Migration `memory_session`, gate row and review-policy row; `attach-memory`; `attach_id` on both doctrine write verbs; `op: retire` on `change-doctrine-sections`; create the `team-memory` document; log D1–D12 with `log-decision` | medium | P1 CI test red, then green; `list-verbs` shows `attach-memory`; the existing doctrine write tests still pass |
| WR562.S3.J1 | Seed. Import the 34 TEAM files (and the PERSONAL one) as sections through one `change-doctrine-sections`; check the 41 ORG files against rules, decisions and AGENTS.md; one guidance PR for the uncovered ones; list the contradictions | small | P3 ledger has 105/105 rows mapped; the secret scan is clean; the guidance PR is open |
| WR562.S4.J1 | Attach everywhere. MCP instructions; CLAUDE.md/AGENTS.md "Team memory" section from the card source; env ban and skill map into AGENTS.md; `bin/review-pr` attaches for the Dot; routine template; strip the scratchpad briefs; delete `brief-footer.md` | medium | P1 live test passes; one Codex job, one routine and one Dot review each show an attach row in `memory_session` |
| WR562.S5a.J1 | Dream: clone, verify, apply (deterministic). `bin/dream` subcommands `clone`, `verify` and `apply`; the transcript condenser; the fixture | medium | P2 CI checks, including the three negative cases |
| WR562.S5b.J1 | Dream: orchestrator and schedule. The `claude -p` orchestrator prompt; `ops/launchd/com.carr.dream.plist` through `bin/run-scheduled.sh`; the token cap | small | P2 live fixture run passes; one real nightly run writes `dreams/log` with token counts |
| WR562.S6.J1 | Deletions 3–11. `autoMemoryEnabled: false`; archive, then a human removes the private folder; drop `memory_item` (count check); remove the learning, corrections and session-brief files | medium | P3 grep and absence checks; CI green |
| WR562.S7.J1 | Hook decision. Run the D11 measurement; delete or keep the rule-card hook and log the decision | small | The comparison numbers are in the PR, and the hook entries are removed or the decision is logged |

## 11. Defects found while mapping

These were found while mapping and are outside this design. Each needs a Work
Request through `report-problem`, which this slice does not file because it
makes no production writes.

- The S1 brief calls `work-request-card` with `human_ref`. The live schema
  takes `work_request` (`{"work_request":"WR-000562"}`).
- `brief-footer.md` is described as "the brief rules" in CLAUDE.md and
  AGENTS.md, but nothing appends it (deletion #2 resolves this).
- The repo's `.codex/hooks.json` references `hooks/builder-agent-gate.py`,
  which does not exist. The hook warns and skips on every Agent call.
- `com.carr.fleet-sync` is still installed in `~/Library/LaunchAgents` and runs
  the deleted `bin/fleet-sync.sh` every 5 minutes.
- CLAUDE.md says the core cards need "no boot fetch", while the MCP
  instructions say "call standing-context FIRST". S4's single "attach first"
  line resolves the conflict.
