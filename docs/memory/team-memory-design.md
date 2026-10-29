# Team memory and dreaming: design (WR-000562, slice S1)

**Outcome.** Every agent (Claude sessions, Codex jobs and cloud routines) gets
one memory that lives entirely in the record layer, in two scopes. It follows
the three layers of Anthropic's talk "Memory and dreaming for self-learning
agents".

- **Storage.** Two record-layer stores.
  - **Org conventions** are the `rule` corpus. It is read-only to agents.
    Rules are adopted only by Joe or Dell in person, through the app's
    existing `governance-queue` approval, which calls one door: `adopt-rule`,
    flagged `humanCallerOnly`.
  - **Team memory** is one read-write doctrine document whose sections are the
    memory files.
- **Structure.** Agents attach through one new verb, `attach-memory`, and write
  lessons (and convention proposals) through the existing
  `write-doctrine-section`.
- **Process.** A nightly dreaming pass on the Studio does three things. It
  clones the team-memory sections into a git directory. It runs one subagent
  per attached session transcript. It applies a verified diff back as one
  atomic, attributed change set.

When the build is done, CLAUDE.md and AGENTS.md hold only a generated
core-card block and navigation pointers.

The number of places a convention or a memory can live drops from six to two:
- the `rule` table;
- `ops/config/rule-cards.v1.json` wording;
- hand-written CLAUDE.md and AGENTS.md prose;
- the private memory folder;
- `memory_item`;
- rules copied into briefs.

By count of parts the design adds 9 and removes 10, with 3 more removals
conditional. By weight the removals are much larger. §8 gives the honest
ledger.

Joe's words, 2026-10-27: "I want the system to be architecturally engineered
correctly. Not a patch that simulates what it does."

The build happens in slices S2–S11. This slice writes nothing to production.
It adds one operator step at merge: freezing the private memory folder (D13).

## Contents

1. [The talk's architecture, as specified](#1-the-talks-architecture-as-specified)
2. [Today's system, mapped](#2-todays-system-mapped)
3. [Glossary](#3-glossary)
4. [Decisions (grilled)](#4-decisions-grilled)
5. [Storage layer](#5-storage-layer)
6. [Structure layer](#6-structure-layer)
7. [Process layer: sessions and dreaming](#7-process-layer-sessions-and-dreaming)
8. [Deletions, their order, and the moving-part ledger](#8-deletions-their-order-and-the-moving-part-ledger)
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
| Documents, scope | `new-doctrine-doc`, `doctrine-index`, `read-doctrine` | `doctrine_document` (0075): `content_class` (`playbook, sop, index, reference, dossier_narrative, distillation, rule`), `visibility` values `shared` and `personal`, `owner_actor_id`, `review_policy_id` | `rule` is reserved "until the P7 rule-store ruling". `personal` means the scope a rule applies in, not who may read it |
| Memory "files" | `write-doctrine-section`, `change-doctrine-sections` (atomic, multiple sections), `doctrine-sections` (batch read of up to 50), `retire-doctrine-section` | `doctrine_section` (`section_key`, `current_version`, `body_hash`, `review_after`, `status active/retired`) | `section_key` is the analogue of a memory path |
| Versions | (history inside the read verbs) | `doctrine_revision`: `version`, `parent_revision_id`, `actor_id`, `session_key`, `content_hash` (sha256), `commit_message`; `doctrine_change_set` with `op write/move/retire` | **`session_key` is filled with `actor.client_id`**, the OAuth client, not the session (`mcp-server/src/doctrine.js` L461–471) |
| Precondition | `base_version` on every write; a stale value returns `version_conflict` | `expected_version` in `doctrine_change_set_item` | Same semantics as `content_sha256`. The hash is stored as well |
| Retire with history | `retire-doctrine-section` | `status='retired'` | `change-doctrine-sections` does **not** yet expose `op: retire`, although the table allows it |
| Staleness clock | (write path) | `review_after = now() + policy.max_age_days` on every write | The hook for verification notes |
| Search | `search-doctrine` (lexical + concepts + Workers AI semantic) | `doctrine_revision.search_vector`; embeddings (0958) | The analogue of grep across the store |
| Snapshot and generation | `read-doctrine` with `if_generation_match` | `doctrine_snapshot(generation, content_hash)` | A consistent clone source for dreaming |
| Gates | `dry-run-doctrine-gates` | **`doctrine_gate_check`** (0075). "A new gate is a function and a row": a `GATE_IMPLS` entry in `mcp-server/src/doctrine.js` plus an INSERT | **`banned_phrases`** (block) applies only to `playbook`, `sop` and `reference`. Its fallback list starts with the em-dash, and it supports `exempt_slugs`. The other gates are not class-scoped |
| Rules (org conventions) | `standing-context`, `applicable-rules`, `find-rule`, `resolve-doctrine-rules` | `rule` (0001): `statement`, `human_quote`, `taught_by`, `scope`, `personal_to`, `status proposed/active/retired/superseded`, `activated_by`/`activated_at` (check: "no silent activation"), `supersedes`. **189 rules: 160 shared and 29 personal** | **No live verb writes it.** `teach`, `activate-rule`, `amend-rule` and `retire-rule` are retired (`ops/config/retired-verbs.v1.json` L155–165) |
| Rule cards | (none; files) | `ops/config/rule-cards.v1.json` (60 cards). Each cites `source_rule_ids` (K01 cites three). `ops/sync-core-rule-cards.py` renders the core cards into CLAUDE.md and AGENTS.md, and `--check` (run by `ops/rule-cards-selftest.py`) fails a stale render | The wording is hand-authored in git, a second home for conventions. `rule` has **no column** for it |
| Human-only acts | `humanOnly: true` admits any actor with `canExercisePartnerAuthority`, which includes the sponsored `claude`, `codex`, `joe-local` and `dell-local` agents (decision dc57f62d retired the humanOnly refusal). **`humanCallerOnly: true`** requires `authorizationClassForActor(actor) === "verified_partner"` however the caller is sponsored (decision cbe91287; `mcp-server/src/tools.js` L8866–8880) | — | Used today by `mcp-server/src/board-answers.js`, `mcp-server/src/email-next-step.ts` and `mcp-server/src/email-deal-progress.ts` |
| Pending decisions | `governance-queue` (read only): "the ONE list of every open item waiting on Joe … rule approvals" | — | The app's existing approval surface |
| Decision log | `log-decision`, `read-decision`, `update-decision`, `find-precedent` | decision table | Joe's rulings with their rationale |
| A second memory store | `recall-memory` (read only) | `memory_item` + `memory_evidence` (0299; sponsor RLS 0573/0574/0579); also read by two 0303 functions (deletion #4) | **No write verb exists.** Recall returned none for a test query on 2026-10-27 |
| Sessions | `read-session-identity`, `list-my-codex-sessions` | `claude_continuity_leaf` (0485), `codex_continuity_checkpoint` (0480), `session_work` (0193) | **No live verb writes the continuity tables.** Nothing links a session to the memory versions it read or wrote |

### 2.2 Private Claude memory

The folder is `~/.claude/projects/-Users-booko-carr-system/memory/`. It holds
`~/.claude/projects/-Users-booko-carr-system/memory/MEMORY.md` plus 104 topic files, about 195 KB in all, and one more file in
`_to_delete/`. Claude Code loads the first 200 lines of `~/.claude/projects/-Users-booko-carr-system/memory/MEMORY.md` into every
Claude session on this repo and into nothing else. Codex, routines and the Dot
never see it.

Each file was read and classified. **34 of the 104 contain an em-dash.**

| Class | Count | Files | Destination |
|---|---|---|---|
| ORG | 41 | automation-invisible-background, batch-joe-blockers, briefs-ban-env-dumps, bug-inventory, build-it-myself-when-asked, carr-system-stays-public, check-platform-before-building, claude-models-for-jobs, client-portal-standard-site, commit-email-private, compliance-out-of-scope, decide-dont-ask-design, flash-off-until-maintenance, independent-taste-score-before-merge, jev-used-as-shipped, keep-orchestrating-without-joe, living-readiness-checklist, main-moved-is-not-rereview, maximize-work-not-tokens, never-ignore-bugs, no-machinery-creep, orchestrator-reviews-audit-deletions, outlook-intake-stays-live, owners-contacted-are-not-leads, paired-prs-server-first, post-call-e2e-review, product-backlog-is-work-requests, quote-joe-not-my-reading, read-tool-docs-first, removals-are-immediate, report-main-clashes-with-cause, requeue-dropped-prs, review-before-automerge-large-prs, review-round-cap, simplest-mechanism-first, skill-use-verified-not-claimed, team-territory, tiny-changes-skip-review, use-tools-as-intended, v1-to-v2-sequence, work-calendar-only-in-outlook | **The `rule` store.** Each is checked against the 189 rules. Uncovered ones become proposals that Joe adopts in person (D1). TEAM fragments go to team memory |
| TEAM | 34 | agent-concurrency-cap, assurance-fixture-repin, background-loops-need-a-deadline, briefs-state-rules-not-values, checkout-current-all-machines, cleanup-before-features, cloud-work-runs-headless, codex-briefs-avoid-attack-wording, codex-cloud-envs, codex-latest-model-families, connector-needs-new-session, dell-mac-ssh-alias, detect-stalled-jobs, dot-on-studio, dot-runs-one-job-at-a-time, email-calendar-top-priority, immediate-email-lane, keep-slots-full, keychain-hang-freezes-codex, live-checkout-stuck-check, macbook-ssh-alias, open-wrs-are-stale, outlook-applescript-send-works, read-area-memories-before-dispatch, read-work-mail-via-parser, security-items-deferred, social-batch-stays, stop-codex-job-whole-tree, studio-is-main-hub, studio-outlook-accounts, trellis-app-reference, wednesday-swarm, where-keys-live, wr557-work-queue-plan | `team-memory` sections verbatim, with secrets checked by `ops/ci-secret-scan.py`. The first dream organizes them |
| STALE | 17 | apple-mail-blocked-outlook-workaround, cloud-session-credits, codemap-after-purge, direct-main-push-runs-gates, dispatch-checkout-new-prefix, free-seats-first-for-reviews, front-door-obsolete, heavy-build-obsolete, keep-dot-and-cloud-lanes-fed, model-subscriptions-2026-10, nous-and-grok-first, orchestrator-standard-process, review-pr-replaces-waiters, scac-successor-seal-procedure, start-work-not-suggestion-cards, workers-report-to-orchestrator, `_to_delete/codex-reset-2026-10-02` | Not migrated; archived as evidence |
| DUP | 12 | briefs-name-pstack-pocock, builders-never-touch-live-checkout, client-sites-on-share-doctorcre, close-done-wrs-with-completion-verb, do-dashboard-steps-in-joes-chrome, dont-carry-dead-questions, grill-runs-autonomous, orchestrator-owns-merges, outlook-lane-owned-by-orchestrator, proposed-rules-are-not-live, search-both-macs-sessions, sizing-step-before-work | Not migrated. The ledger names the rule or card that covers each, and TEAM fragments go to team memory |
| PERSONAL | 1 | iphone-home-screen-style | A team-memory section under `joe/` |

**Contradictions inside the folder.** The first dream must resolve these, and
the seed slice lists them:
- **Merge command:** `--auto --squash` in the memory, refused per AGENTS.md.
- **Routines:** banned in one file, used in another.
- **AppleScript send:** called hollow in one file, said to work in another.
- **Concurrency cap:** 10 in the description, 6 in the body.
- **Top priority:** cleanup-first versus email-first.

**Dangling references.** Three `[[…]]` links point at files that do not exist:
`dot-chat-never-unanswered`, `merge-queue-auto-merges-green` and
`merge-queue-rearm-exhausted`.

**Sensitive values.** Nine TEAM files hold tailnet addresses, chat channel IDs
or environment IDs. None are quoted in this public repo.

### 2.3 How each agent gets context today

| Agent | Context today | Evidence |
|---|---|---|
| Claude Code session | CLAUDE.md (126 lines: the 12 generated core cards plus hand-written sections); auto-memory `~/.claude/projects/-Users-booko-carr-system/memory/MEMORY.md`; per-call cards from `hooks/rule-pack-preuse-reselection.py`; the carr MCP instructions ("OPENING ACT, every session: call standing-context FIRST") | CLAUDE.md says "No boot fetch is required", which contradicts the MCP instruction. `hooks/job-watchdog-digest.py` injects ops status; it is out of scope |
| Codex job (`bin/codex-job`) | AGENTS.md (327 lines: the same generated cards plus 16 hand-written sections); a header `Type:` declaration selecting the workflow stages and full SKILL.md text in the `## Required skills` block from `bin/skill_picker.py`; the card hook through the local, untracked `~/.codex/hooks.json`; the carr MCP | **Adds no shared rule text.** The briefs carry the rules by hand |
| Hand-written briefs | 35 untracked `~/carr-system/scratchpad/jobs/brief-*.md`, sent by `~/carr-system/scratchpad/jobs/launch.sh`, `~/carr-system/scratchpad/jobs/runq.sh` and `~/carr-system/scratchpad/jobs/review-after.sh` through `bin/codex-job` | The same blocks are copied in by hand: never open .env/credentials (33 of 35), don't edit the live checkout (31), historical Skills line (29), now replaced by the required header Type declaration, noreply email (28), no production writes (28), Step 0 (27), sizing (25), auto-merge (22), env/printenv ban (20), never force-push (20), Co-Authored-By (16), migration-number rule (14) |
| `tools/room-bridge/brief-footer.md` | The rules line, the skill map and the VERDICT format | **Nothing on main appends it**, yet CLAUDE.md and AGENTS.md point to it as "the brief rules" |
| Cloud routines (RemoteTrigger) | The prompt inside each trigger, on the claude.ai side; the templates `docs/agents/review-brief.md` and `~/carr-system/scratchpad/jobs/brief-cloud-builder.md` (its own copy of the rules); CLAUDE.md through the checkout | These prompts **never pass through `bin/codex-job`** |
| The Dot | Only the prompt `bin/review-pr` builds (L29–31) | Not named in the WR's ATTACH criterion |

### 2.4 Session transcripts

| Source | Location | Volume (2026-10-27) | Consumers |
|---|---|---|---|
| Claude Code | `~/.claude/projects/-Users-booko-carr-system/*.jsonl` | 416 top-level files (1,358 with subagents), 3.2 GB | Per-session hooks only. Bulk readers `ops/corrections-sweep.py` and `tools/rule-delivery-eval-intake.py` are **not scheduled** |
| Codex | `~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl` | 2,830 files, 7.0 GB (+353 archived) | `ops/codex-history.py` reads only the current rollout |
| Codex job stdout | `~/carr-system/scratchpad/jobs/logs/*.log` | 805 files, 677 MB | **None** |
| Routines | claude.ai run log | n/a | None |

**Nothing consumes transcripts on any schedule.**

## 3. Glossary

- **Store.** A record-layer home for memory.
  - **`org-conventions`** is the `rule` table, served by `standing-context`.
    It is read-only to every agent.
  - **`team-memory`** is one doctrine document, read-write to agents.
- **Access.** Each attach grants `read_only` or `read_write` per store.
- **Memory.** One section of `team-memory`. Its `section_key` is the path and
  its body is markdown.
- **Convention.** One active `rule` row. It changes only by superseding: a new
  row with `supersedes` set to the old row.
- **Proposal.** A team-memory section under `conventions/proposed/<slug>`,
  written by an agent. It binds nobody.
- **Adoption.** Joe or Dell approves a proposal in person in the app's
  `governance-queue` surface, which calls `adopt-rule`.
- **Card.** A rule whose `card` column is set. Card rules render into the
  generated block.
- **Attach.** One call to `attach-memory` at session start. It returns an
  **attach receipt** and records one `memory_session` row.
- **Lesson.** A real-time write to `team-memory` made with the `attach_id`.
- **Dream.** One run of planned `bin/dream`, which goes through these steps:
  - **Clone:** `$MEM` is the store at one generation; `$MEM_OUT` is a git
    directory made from it.
  - **One subagent per session.**
  - **Diff:** the changes to `$MEM_OUT`.
  - **Verify**, then **apply**.
- **Verification note.** A line inside a memory saying when and from which
  session it was last confirmed. Writing one resets `review_after`.

## 4. Decisions (grilled)

Each question was answered from the sources rather than by asking Joe.
"Reopen if" names the evidence that would change a decision. S2 logs these
decisions with `log-decision`; S1 makes no production writes.

**D1. How is the read-only scope one store, and who writes it?**
*Answer:* The WR's ONE-STORE criterion says "All agent memory lives in the
record layer with two scopes". The `rule` table is already the store the talk
describes: versioned through `supersedes`, attributed through `taught_by` and
`activated_by`, and protected by a no-silent-activation check. It lacks a human
write path and a home for card wording. Conventions therefore leak into git
prose, the private folder and briefs.

*Decision:* **Org conventions = the `rule` table, the only home.**
- **Adoption is in person only.** Joe or Dell adopts a rule through the app's
  existing `governance-queue` approval. That approval calls a new verb,
  **`adopt-rule`**, declared `humanOnly: true, humanCallerOnly: true`.
  `humanCallerOnly` is the flag that refuses machine identities however they
  are sponsored (decision cbe91287); `humanOnly` alone admits sponsored agents
  (decision dc57f62d).
  - A Claude session on the connector never adopts rules.
  - Whether the connector session is classed `verified_partner` is
    **unverified**. If it is, S4 also restricts `adopt-rule` to the app's
    approval surface, and S4's CI test proves both a `claude`-sponsored actor
    and a connector session get `human_only_verb_requires_verified_partner`.
- **Agents only propose.** A proposal is a `team-memory` section under
  `conventions/proposed/`, written with `write-doctrine-section`. That verb
  cannot touch `rule`, so the propose path cannot activate anything.
  `governance-queue` lists these sections beside its existing rule approvals.
  On adoption, `adopt-rule` writes the active row and retires the proposal
  section in one transaction.
- **Card wording lives on the rule** (D16). `ops/sync-core-rule-cards.py` renders
  `ops/config/rule-cards.v1.json` and the CLAUDE.md and AGENTS.md block **from the store**.
- **CLAUDE.md and AGENTS.md** hold only the generated core-card block and
  navigation pointers. Their hand-written sections (12 in CLAUDE.md
  counting sub-sections, 16 in AGENTS.md) are migrated line by line in S5
  (§10). The generated block is the one named exception to
  "pointers only": it is the platform's native always-loaded mount note, it is
  generated from the store, and `--check` fails any hand edit. It is retired if
  attach alone is measured to hold rule compliance (S11).

*Authority and the K12 conflict:* core card **K12** sends durable corrections to
"an edit to the relevant guidance file (CLAUDE.md, AGENTS.md or a skill) in a
PR". **WR-000562**, Joe's store record, requires one store, so under K06 the
store record governs. The K12 rewrite ("propose a convention as a team-memory
proposal; Joe adopts it in the app") goes in a guidance PR. **That PR must merge
before S4 turns on the store-rendered block and before S5 turns on the
pointers-only check**, so no agent is ever told to make an edit CI refuses.

*Reopen if:* Joe rules that git guidance files are part of the read-only scope.

**D2. Should team memory be a new store kind, or doctrine with a scope? And
which content class?**
*Answer:* Doctrine sections have everything the talk's store needs except
session attribution. `memory_item` has no write verb, no path, no
compare-and-swap, and a promote-gate that blocks real-time reads.
`banned_phrases` blocks the em-dash in `playbook`, `sop` and `reference`, and
34 of the 104 files contain one.

*Decision:* team memory is **one doctrine document**, slug `team-memory`, with
**content class `distillation`** and review policy `team-memory`
(`max_age_days` 30).
- That lint is scoped to prospect-visible classes, and team memory is internal
  knowledge distilled from sessions, never prospect-visible.
- An `exempt_slugs` entry would misuse a list the gate config documents as
  being for documents that define the bans.

S3's acceptance is a passing `dry-run-doctrine-gates` over every seed section.

*Reopen if:* the store passes about 2,000 sections, or a second team needs its
own read-write store. Either case means a second document, not a new kind.

**D3. Should the precondition be `content_sha256` or `base_version`?**
*Decision:* keep `base_version`; the semantics are the same, and
`content_hash` is stored and returned. *Reopen if* agents edit materialized
files.

**D4. How is a write attributed to its session, and how is access enforced?**
*Decision:*
- **Attribution.** The two doctrine write verbs accept `attach_id`, which
  becomes `session_key`.
- **Enforcement.** A new gate ships as a `doctrine_gate_check` row
  `memory_attach_required` (block;
  `applies_to {"ops":["write","retire"],"content_classes":["distillation"]}`;
  `config {"slugs":["team-memory"]}`) **plus a `GATE_IMPLS` function** in
  `mcp-server/src/doctrine.js`. The function:
  - returns no finding for other documents;
  - otherwise looks up `memory_session` by `attach_id`;
  - refuses with `memory_attach_required` when the id is missing or unknown;
  - refuses with `memory_read_only` when the attach is read-only.
- **Tests.** A selftest covers the three outcomes.

*Reopen if* a surface cannot pass an argument.

**D5. Should attach be a new verb, or `standing-context` extended?**
*Decision:* a new write verb, `attach-memory`, because attach records the
session and `standing-context` is a read front door. *Reopen if* the payloads
drift into duplicates.

**D6. Files or verbs: should the store be mounted as files?**
*Decision:* live sessions use verbs, which mirror the platform's standalone
memory API. The dream uses files. *Reopen if* agents fail to read or write
lessons through verbs (measured in the S6 two-agent test).

**D7. How are sessions recorded as data?**
*Decision:* one new table, `memory_session`, with the columns `attach_id`
(primary key), `surface`, `native_session_id`, `transcript_ref`, `host`,
`actor_id`, `access`, `attached_generation`, `attached_index_digest` and
`attached_at`. It has no personal scope. Reads are recorded on the attach row,
writes through `session_key = attach_id`, and transcripts are linked by
`transcript_ref` or by finding the receipt. *Reopen if* every surface gets a
native session-id channel.

**D8. Where does the dream run, and how often?**
*Decision:* nightly at 03:40 CT on the Studio: launchd
Planned `ops/launchd/com.carr.dream.plist` → `bin/run-scheduled.sh dream …` →
planned `bin/dream` → headless `claude -p` on the subscription. There is no paid API,
scheduled task or sidebar session. *Reopen if* weekly limits starve daytime
work.

**D9. What is the dream's token budget?**
*Decision:*
- 40 sessions per night.
- Each transcript condensed to at most 40k tokens (over `lib/transcript_read.py`).
- Sonnet subagents with an Opus orchestrator.
- A hard stop at 2.5M input tokens.

*Reopen if* verification rejects diffs on two consecutive nights.

**D10. Is the diff applied automatically, or reviewed first?**
*Decision:* automatic when the deterministic verifier passes, as one atomic
change set. *Reopen if* a dream applies a wrong change.

**D11. What happens to the per-call rule-card hook?**
*Decision:* measure in S11.
- If compliance is within 5 points with the hook off, delete it.
- If not, keep it as a **named exception to WR-000562**, logged with
  `log-decision`. It is removed when a measurement shows parity after the card
  wording lives in the store and the dream has run for 30 days.

**D12. Does the Dot attach?**
*Decision:* no injection. The Dot attaches through the verb itself (read-only)
if its connector allows it. Otherwise "no connector" is recorded. Nothing is
pasted.

**D13. When do writes to the private folder stop?**
*Decision:* at the S1 merge, by freezing the folder read-only (`chmod -R a-w`,
an operator step). Reads continue. The first dream backfills transcripts since
the merge (S9). `autoMemoryEnabled: false` and the removal follow in S10,
because whether the setting also stops loading `~/.claude/projects/-Users-booko-carr-system/memory/MEMORY.md` is unproven. *Reopen
if* the freeze breaks session start.

**D14. What does the dream do when a section moved since its clone?**
*Decision:* rebase. Only the change units that touch moved sections are
dropped and recorded; the rest applies. *Reopen if* the same section drops on
three nights in a row (then split it).

**D15. What happens to `memory_item`: reuse it or drop it?**
*Decision:* drop it (S10), after every dependent in §8 is migrated. The drop
migration **fails closed** on a database-side check (§8, deletion #4). *Reopen
if* Joe wants personal-scope memories; then they return as a
`visibility: personal` doctrine document.

**D16. Where does card wording live on a rule?**
*Answer:* `rule` has no column for card metadata, and a card can cite several
rules (K01 cites three). *Decision:* one migration in S4 adds a nullable
`rule.card jsonb` column (`id`, `title`, `placement`, `core_rank`,
`acknowledgement`, `triggers`). The card's wording is the rule's `statement`.
- A card citing one rule sets `card` on that rule. When the wording differs,
  the card supersedes the rule with a new row whose statement is the card text.
- A card citing several rules becomes **its own rule row**: statement = card
  wording, `card.source_rule_ids` = the cited rules, which stay active.
- All 60 cards enter as proposals that Joe adopts in one in-person batch.

*Reopen if* card metadata outgrows one column; then it gets a child table.

## 5. Storage layer

| Platform | Record layer | Exists? | Gap and fix |
|---|---|---|---|
| Read-only org store | `rule` | Store yes; human write path no; card wording no | **`adopt-rule`** (`humanOnly` + `humanCallerOnly`, called by `governance-queue` approval) (D1); **`rule.card`** column (D16) |
| Read-write team store | `team-memory`, class `distillation` | Yes | Created with `new-doctrine-doc` (S2) |
| `access` per attach | `memory_session.access` + gate `memory_attach_required` | No | **New table** + **gate row and function** |
| Memory at `path` | `doctrine_section.section_key` | Yes | None |
| `content_sha256` precondition | `base_version` (+ `content_hash`) | Yes | None |
| Version per change | `doctrine_revision`; `rule.supersedes` | Yes | None |
| Attribution | `actor_id`, `session_key`, `created_at` | Partly | `session_key ← attach_id` |
| Delete, with versions kept | `retire-doctrine-section` | Yes | `op: retire` in `change-doctrine-sections` |
| Redact | none | No | Not built. *Reopen if* a secret lands in a memory |

**New schema:**
- **S2:** `memory_session`; the gate row (its function ships in the same PR);
  the `team-memory` review policy; the documents `team-memory` and `dreams-log`
  (both `distillation`).
- **S4:** `rule.card`.

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
           propose_with: "write-doctrine-section document=team-memory section_key=conventions/proposed/<slug>" },
         { name: "team-memory", access: "read_write" | "read_only",
           document: "team-memory", generation,
           index: [ { section_id, section_key, title, version, content_hash,
                      review_after, last_writer_surface } ],
           instructions: "Check the index before starting. Read with doctrine-sections.
             Write lessons as you learn them with write-doctrine-section
             document=team-memory attach_id=<id> base_version=<version>.
             On version_conflict re-read, then write once." } ] }
```

**`write-doctrine-section` / `change-doctrine-sections`** (existing) gain
`attach_id` and `op: retire`.

**`adopt-rule`** (new; `humanOnly: true, humanCallerOnly: true`) is called only
by the app's `governance-queue` approval. It takes a proposal section (or a
card import) and writes the active `rule` row with `taught_by` and
`activated_by` set to the human, then retires the proposal.

### 6.2 How each surface calls them

| Surface | Platform mechanism reused | What changes |
|---|---|---|
| Claude Code session | MCP server `instructions` + the generated CLAUDE.md block | "OPENING ACT: call `attach-memory` first", in both places |
| Codex job | AGENTS.md (native) + the carr MCP | The same generated line. `bin/codex-job` puts the job log path in the brief's first line and refuses copied blocks (below) |
| Cloud routine | The checkout's CLAUDE.md + the CARR connector on the trigger | Routine prompts **do not pass through `bin/codex-job`**. S7 strips the copied rules directly from `docs/agents/review-brief.md`, `~/carr-system/scratchpad/jobs/brief-cloud-builder.md` and every live trigger prompt. After that, the only control is that routines attach; nothing refuses a future routine prompt that copies rules, and that is stated as a known gap |
| The Dot | Its own CARR connector, if present | `bin/review-pr` asks it to attach read-only; nothing is pasted |
| Dream | `./run.sh call` | planned `bin/dream` attaches as `surface: dream` |

**The launcher's refusal is narrow.** S7 takes the copied boilerplate blocks
from the 35 briefs. Each block is the whole multi-line paragraph, normalized
for whitespace, and the launcher keeps its sha256. `bin/codex-job` refuses a
brief only when it contains one of those exact blocks. A phrase that a real
task needs, such as "no production writes", "never force-push" or a
`Co-Authored-By` line, is never refused on its own. Its selftest proves both: an
exact block is refused, and the same phrase inside task text passes.

## 7. Process layer: sessions and dreaming

### 7.1 Sessions as data

```
attach-memory ──► memory_session(attach_id, surface, generation, index digest)   ← versions READ
write-doctrine-section(attach_id) ──► doctrine_revision.session_key = attach_id   ← versions WRITTEN
transcript (jsonl on the Studio) ── contains the attach receipt ──► dream links transcript_ref
```

### 7.2 One dream

This follows `joe-dreaming-pass.png` step for step.

0. **Select.** The `memory_session` rows since the last dream, capped per D9.
   - Missing `transcript_ref` values are found by searching the Claude, Codex
     and job-log locations for the `attach_id`.
   - The first run backfills sessions since the S1 merge.
   - Sessions without a transcript on the Studio are recorded as unread.
1. **Clone `$MEM` → `$MEM_OUT`.** `read-doctrine team-memory` at generation G
   becomes `~/carr-local/dreams/<date>/mem/<section_key>.md`, with
   `section_id`, `version` and `content_hash` in front matter. Then
   `git init` and commit.
2. **Orchestrator, one subagent per session.** A headless `claude -p` (Opus)
   gets `$MEM_OUT`, the condensed transcripts and the dream instructions, and
   starts one Sonnet subagent per transcript.
3. **Subagents read and write `$MEM_OUT`.** They merge, delete stale entries,
   add patterns, and append `Verified <date> from session <attach_id>`. The
   orchestrator finishes the structure and writes `<dream-output>/_dream/notes.md`, one entry
   per change unit with its sessions.
4. **Diff.** `git diff` against the clone commit.
5. **Verify.** All checks are deterministic.
   1. **Rebase (D14):** drop and record change units on moved sections.
   2. **Provenance:** every remaining change cites an input session.
   3. **Secrets:** `ops/ci-secret-scan.py` is clean.
   4. **Shape:** front matter is intact and the index is at most 300 sections.
   5. **Gates:** `dry-run-doctrine-gates` passes.

   Failing checks 2–5 applies nothing.
6. **Apply.** One `change-doctrine-sections` call (`op: write` and
   `op: retire`, each with its clone `base_version`) with the dream's
   `attach_id`, then one `dreams-log` write: the notes, the counts, the dropped
   units, the tokens and the unread sessions.
7. **Clean up.** Keep the directory for 14 days.

**Why not the platform's dreams API?** It bills at API rates and only reads
Managed Agents sessions. Our transcripts are local files on the subscription
fleet.

## 8. Deletions, their order, and the moving-part ledger

Each item is deleted only after its replacement is proven. Nothing is deleted
in S1.

| # | Deleted | Replaced by | Precondition (the check) | Slice |
|---|---|---|---|---|
| 1 | Copied rule blocks in the 35 briefs and in the routine prompts | Generated block + `attach-memory` | The env ban and the skill map are adopted rules. The S6 two-agent test passes. The exact-block grep over briefs and routine templates returns zero | S7 |
| 2 | `tools/room-bridge/brief-footer.md` and its two pointers | `rule` rows | As #1 | S7 |
| 3 | Hand-written convention prose in CLAUDE.md (126 lines) and AGENTS.md (327 lines) | `rule` rows + pointers | The S5 heading ledger has every line classified; the K12 rewrite is merged first | S5 |
| 4 | `memory_item`, `memory_evidence`, `recall-memory`, their RLS (0573/0574/0579), and the memory branches of two 0303 functions | `team-memory` | **The database-side check, failing closed** (below) | S10 |
| 5 | `~/.claude/projects/-Users-booko-carr-system/memory/MEMORY.md` and the 104-file private folder | `team-memory` + `rule` | Frozen at S1; the ledger maps 105 of 105; `autoMemoryEnabled: false`; tarred to `~/carr-local/archive/`, then removed by a human | S10 |
| 6 | `bin/learning-weekly.sh` + selftests | Dream | Unscheduled; registry rows retire with it | S10 |
| 7 | `bin/learning-monthly.sh` + selftests | Dream | As #6 | S10 |
| 8 | `ops/corrections-sweep.py` + selftest, and the `corrections` miner in `pipelines/learning_jobs.py` | Dream | Callers swept to zero | S10 |
| 9 | `hooks/session-brief.py` + selftest | `attach-memory` | Wired into no settings file | S10 |
| 10 | Manual consolidate and retro passes (`consolidate-memory` on this folder, `~/carr-system/scratchpad/jobs/brief-retro.md`) | Dream | The folder is gone and one dream has applied | S10 |
| C1 | `ops/hermes-memory-check.py` + selftest (conditional) | Nothing | Only if Hermes memory is retired | S10 |
| C2 | `claude_continuity_*` / `codex_continuity_*` as session records (conditional) | `memory_session` | Only if `read-session-identity` switches to `memory_session` | S10 |
| C3 | `hooks/rule-pack-preuse-reselection.py` (conditional) | Attach + generated block | The D11 measurement; otherwise a logged exception | S11 |

**Deletion #4, every dependent, migrated in S10 before the drop:**

| Dependent | Migration |
|---|---|
| `ops.context_activation_compiler_items` (0303 L81; reads `public.memory_item` at L141) and `ops.render_context_activation_for_brief` (0303 L837; reads it at L864) | A migration in S10, **before** the drop, uses `create or replace` to remove the memory branch from both functions. Attach now delivers memory. These were PL/pgSQL bodies, so Postgres records no dependency and `drop table` alone would leave them failing at runtime |
| `mcp-server/src/evidence-activation.js` (calls the second function, L87) and `ops/evidence-activation-db-gate.py` (L212, L240) | Re-run their tests against the replaced functions; drop any memory-row expectations |
| `mcp-server/src/memory.js` (`memoryTools`) and its import in `mcp-server/src/tools.js` | Delete |
| `mcp-server/src/doc-profile.ts` verb list; `mcp-server/src/mcp.js` mentions | Remove `recall-memory` |
| `mcp-server/test/memory-kernel.test.mjs`, `mcp-server/test/memory-rls-connection.test.mjs` | Delete |
| `mcp-server/test/situation-retrieval-integration.test.mjs`, `mcp-server/test/doc-profile.test.mjs`, `mcp-server/test/tool-advertisement.test.mjs`, `mcp-server/test/bigint-version-responses.test.mjs`, `mcp-server/test/dot-security-definers-postgres.sql` | Remove their memory cases |
| `control-room/contracts/memory-evaluation-s0.v1.schema.json`, `control-room/contracts/fixtures/memory-evaluation-s0.synthetic.v1.json`, and `control-room/test/contracts.test.mjs` (L240–258) | Retire the contract, its fixture and its assertions together |
| `ops/private-row-sponsor-rls-gate.py` (fails "enumeration is vacuous" without `memory_item`) | Run the behaviour proof over every fenced personal table the enumeration finds, and pass explicitly with "no fenced personal tables" when there are none. The enumeration still fails a new unfenced personal-scope table |
| `ops/definer-hardening-selftest.py`, `ops/migration-production-proof-selftest.py`, `tools/test-schema-snapshot-roles.py`, `ops/fixtures/mcp-catalog/before-2026-10-05.json` | Remove the expectations; mark the historical catalog fixture as frozen |
| `db/schema.sql`, `db/migration-baseline.json`, `bin/schema-snapshot.sh` output, `mcp-server/src/scac-mutation-registry.current.generated.js` | Regenerate after the drop |

**The S10 drop fails closed.** The drop migration starts with a preflight
`do` block. It raises an exception, so nothing is dropped, when this query
returns any row:

```sql
select 'function ' || p.oid::regprocedure::text as dependent
  from pg_proc p
 where (p.prosrc ilike '%memory_item%' or p.prosrc ilike '%memory_evidence%')
   and p.oid not in (select t.tgfoid from pg_trigger t
                      where t.tgrelid in ('public.memory_item'::regclass,
                                          'public.memory_evidence'::regclass))
union all
select 'view ' || schemaname || '.' || viewname
  from pg_views
 where definition ilike '%memory_item%' or definition ilike '%memory_evidence%';
```

The allowed exception is the trigger functions attached to the two tables,
which the drop removes. The repo side checks too: `rg -l "memory_item|memory_evidence|recall-memory"`
over tracked files outside `migrations/`, `db/schema.sql` and
`db/migration-baseline.json` must return nothing. The slice's CI runs both
checks before the drop, and the migration's own preflight runs the database
check again at apply time.

**Order:**

1. Freeze the folder (S1 merge).
2. Storage and attach (S2).
3. Seed team memory (S3).
4. The K12 guidance PR merges; then `adopt-rule`, the card column and the ORG
   files (S4).
5. CLAUDE.md and AGENTS.md prose into rules (S5).
6. Attach for Claude and Codex (S6).
7. Attach for routines and the Dot; strip briefs (S7).
8. Dream (S8, S9).
9. Deletions (S10).
10. Measure the hook (S11).

**Moving-part ledger (honest count).**

Added, 9 new parts:
1. the `memory_session` table;
2. `attach-memory`;
3. `adopt-rule`;
4. the `memory_attach_required` gate function and row (with selftest);
5. the `rule.card` column and its migration;
6. planned `bin/dream` (clone, verify, apply, orchestrate);
7. the transcript condenser inside it;
8. the `com.carr.dream` launchd plist;
9. the `bin/codex-job` exact-block refusal (with selftest).

Modified, not new:
- `write-doctrine-section` and `change-doctrine-sections` (`attach_id`,
  `op: retire`);
- `governance-queue` (lists proposal sections);
- `ops/sync-core-rule-cards.py` (renders from the store; `--check` also fails a
  heading outside the generated block and the pointer allowlist);
- the MCP instructions.

Removed unconditionally, 10:
1. copied rule blocks in 35 briefs and the routine prompts;
2. `tools/room-bridge/brief-footer.md`;
3. hand-written convention prose in CLAUDE.md and AGENTS.md;
4. the memory kernel (2 tables, 3+ triggers, RLS policies, a verb, a module,
   a contract with its fixture, 2 function branches);
5. the private folder with `~/.claude/projects/-Users-booko-carr-system/memory/MEMORY.md` (105 files);
6. `bin/learning-weekly.sh`;
7. `bin/learning-monthly.sh`;
8. `ops/corrections-sweep.py` with its miner;
9. `hooks/session-brief.py`;
10. the manual consolidate and retro passes.

Conditional, 3: C1–C3.

**Verdict:** by count it is 9 added against 10 removed, so fewer parts holds,
but narrowly. By weight the removals are much larger. The design's real gain is
that the homes of truth for conventions and memory drop from six to two.

## 9. Proof

### P1. Two-agent test (`REALTIME`), plus scope refusals

1. **CI (S2, plus the S4 cases):**
   - **S2:**
     - Attach A, write `incidents/probe-x` (its body contains an em-dash) with
       "Ruled out: X … Next agent: skip X, go straight to Y."
     - Attach B. The index shows version 1, and `session_key` equals A's
       `attach_id`.
     - A stale `base_version` returns `version_conflict`.
     - A write without `attach_id` is refused, and so is a read-only attach.
   - **S4:**
     - `adopt-rule` called by a `claude`-sponsored actor, and by a connector
       session, returns `human_only_verb_requires_verified_partner`.
     - A proposal section cannot change `rule`.
2. **Live (S6):** two headless `claude -p` sessions run against
   `team-memory-test`. The planned fixture probe `tools/memory-probe-x` sleeps 60 s
   and counts its calls.
   - **Pass:** one probe run (A's), B cites A's memory, and B makes fewer tool
     calls than A.

### P2. Dreaming fixture (`DREAMING`)

The planned fixture lives under `ops/fixtures/dream/`, all synthetic: five
near-duplicate sections with five `attach_id`s, one stale section
(old script `bin/fleet-sync.sh`), one confirmed section, and three condensed transcripts.

1. **CI (S8):** a hand-written `$MEM_OUT` goes through verify and apply.
   - **Pass:** one change set; 5→1 with four retired; the stale section retired
     with a note; a verification note added and `review_after` reset; the input
     unchanged until apply; a second apply a no-op.
   - **Negative cases:** a moved section drops only its unit; a planted secret
     applies nothing; a change without a note applies nothing.
2. **Live (S9):** planned `bin/dream --fixture` against a scratch document gives the
   same outcomes, checked by a script.

### P3. Deletion, attach and one-store safety

- **Memory ledger (S3, S4):** 105 of 105 files mapped.
- **Guidance ledger (S5):** every line of CLAUDE.md and AGENTS.md is
  classified. Afterwards, `sync-core-rule-cards.py --check` fails on any
  heading outside the generated block and the pointer allowlist.
- **Briefs (S7):** the exact-block grep over briefs and routine templates
  returns zero, and the launcher selftest passes.
- **Attach rows (S6, S7):** one `memory_session` row per surface (or "no
  connector" recorded for the Dot).
- **Drop (S10):** the database preflight and the repo grep both return
  nothing; the private folder is absent; a new attach lists the seeded
  memories.

## 10. Slices

Each slice fits in one session or less and is listed in build order. Card IDs
match `WR\d+\.S\d+\.J\d+`. Every slice runs the design-and-debt pass and
reports its net lines.

| Card | Slice | Size | Acceptance check |
|---|---|---|---|
| WR562.S1.J1 | This design. The orchestrator freezes the private folder at merge | tiny | Merged; the folder is unwritable and still readable |
| WR562.S2.J1 | Storage + attach. `memory_session`; the gate row, function and selftest; the review policy; `attach-memory`; `attach_id` and `op: retire`; the `team-memory` and `dreams-log` documents; log D1–D16 | medium | P1 S2 cases red, then green; the existing doctrine gate tests pass |
| WR562.S3.J1 | Seed team memory. The TEAM and PERSONAL files plus TEAM fragments, in one change set; list the contradictions | small | `dry-run-doctrine-gates` passes over every seed section; the secret scan is clean; ledger TEAM, STALE and DUP rows mapped |
| WR562.S4.J1 | Org-conventions store. **Precondition: the K12 guidance PR has merged.** `adopt-rule` (`humanCallerOnly`), wired into the `governance-queue` approval; `governance-queue` lists proposal sections; the `rule.card` migration; `ops/sync-core-rule-cards.py` renders from the store; the 60 cards and the uncovered ORG files staged as proposals; Joe adopts the batch in person | medium | P1 S4 refusals; the block renders from the store and `--check` passes; the ledger has all 105 rows mapped |
| WR562.S5.J1 | Guidance prose into the store. A line ledger of CLAUDE.md and AGENTS.md: **convention** (→ proposal, then adoption), **navigation pointer** (keep), or **obsolete** (drop). Code documentation moves to the `docs/` file it describes, leaving a pointer. Add the heading allowlist to `--check` | medium | Every line classified; after Joe's in-person adoption both files hold only the generated block and pointers; `--check` fails a test file with a hand-written convention heading |
| WR562.S6.J1 | Attach for Claude and Codex. MCP instructions; the generated attach line; the job-log path in `bin/codex-job` | small | P1 live test passes; one Claude and one Codex `memory_session` row |
| WR562.S7.J1 | Attach for routines and the Dot; strip copies. Routine templates and live trigger prompts stripped; the Dot connector attach; the 35 briefs stripped; the exact-block refusal and selftest; delete `tools/room-bridge/brief-footer.md` | small | P3 brief checks; routine and Dot rows exist (or "no connector" is recorded) |
| WR562.S8.J1 | Dream: clone, verify (rebase), apply; the condenser; the fixture | medium | P2 CI checks, including the negative cases |
| WR562.S9.J1 | Dream: orchestrator, plist and token cap; the one-time backfill since the S1 merge | small | P2 live fixture; one nightly `dreams-log` entry with token counts |
| WR562.S10.J1 | Deletions 4–10 (C1, C2 if their conditions hold). Replace the 0303 functions first; migrate every dependent; the drop with its fail-closed preflight; `autoMemoryEnabled: false`; archive, then a human removes the folder | medium | The database preflight and the repo grep return nothing; P3 drop checks; CI green |
| WR562.S11.J1 | Hook decision (D11) | small | The comparison numbers are in the PR; the hook is removed or the exception logged |

**Joe's in-person time.** S4 and S5 each end in one batched adoption in the
app's approval surface. That is the only human work besides removing the
folder in S10.

## 11. Defects found while mapping

These were found while mapping and are outside this design. Each needs a Work
Request through `report-problem`, which S1 does not file because it makes no
production writes.

- The S1 brief calls `work-request-card` with `human_ref`. The live schema
  takes `work_request`.
- `tools/room-bridge/brief-footer.md` is described as "the brief rules" in CLAUDE.md and
  AGENTS.md, but nothing appends it (deletion #2 resolves this).
- The local, untracked `~/carr-system/.codex/hooks.json` references
  `hooks/builder-agent-gate.py`, which does not have an implementation on main.
- `com.carr.fleet-sync` is still installed in `~/Library/LaunchAgents` and runs
  the deleted old script `bin/fleet-sync.sh` every 5 minutes.
- CLAUDE.md says the core cards need "no boot fetch", while the MCP
  instructions say "call standing-context FIRST". S6's single "attach first"
  line resolves the conflict.
