# Replace rule ceremony with native instructions

Plan only. Observed 2026-10-22, America/Chicago. Source checkout `4698ca8e141f6baeb8374d08d9d82f20ce8cc935`. No runtime code, installed configuration, rule authority, database, or repository settings change in this PR. Implementation is four later PRs.

Joe authorized removing obstructive machinery on 2026-10-22: "God yes, please get rid of the crap like this that prevents progress and slows down everything". That authorizes this simplification plan. It does not silently settle the conflicting Jev cadence rulings below or retire any standing rule in this planning job.

## Destination and scope

Agents receive concise standing guidance from root `CLAUDE.md` and `AGENTS.md`, narrower guidance from native scoped files, and procedures from skills. A local tool action does not fetch a rule catalog, prove a boot fetch, print a rule receipt, or ask Jev which instruction applies. Deterministic controls continue to deny concrete harmful actions.

Joe's approval of exact wording makes guidance active in one authority transaction. Approval does not require a machine control. The result distinguishes approved guidance, published files, and verified technical enforcement.

This plan owns rule delivery, rule approval, their callers and derived metadata, and the Jev calls coupled to those hooks. It does not own Model Room retirement, the work queue, continuity, Dot, merge tooling, broad `guard-unattended.py` redesign, or business-runtime rule reasoning. Those audit recommendations require their own product-linked scope. No new Work Request or prerequisite program is proposed.

## Evidence and its limits

The supplied agent machinery audit at `~/carr-system/out/machinery-audit/agent-harness.md` was read in full from the shared coordination checkout. It reports 355 ms median per-call lookup latency, 19,428 empty receipts among 19,873 on October 20, and 76 usable Jev answers among 1,531 hook attempts since October 15. These are reused audit observations, not new timings or a claim about Jev model quality. Failed transport, question polarity, and instrumentation must be separated before judging the model.

The audit counted 1,461 lines for the per-call path and 1,537 for boot gating. Both totals reproduce at this checkout. The current boot endpoint describes itself as a digest-only page with no rule text. A digest fetch therefore proves neither delivery nor compliance.

The deployed `list-verbs` read returned 380 verbs. Its schema confirms that `admit-rule` requires 11 top-level fields. `approve-rule` accepts only `machine_enforceable` or `human_only`; migration `0194_atomic_rule_approval.sql:261` explicitly refuses `judgment_advisory`. Migration `0904_one_step_gate_rule_approval.sql` already derives an admission for a taught gate rule, so "all approvals require a separate admit" would be false. The remaining defect is conflating guidance approval with installed enforcement.

The brief names judgment rule `1c7e4bac` as an example blocked after Joe approved it. A live `standing-context` lookup returned it in `rule_ids_not_found`; this endpoint reads active guidance and cannot establish that proposed rule's status or full UUID. Treat this as a brief-supplied reproduction case, not independently verified live state. The implementation must locate the exact proposed record and approval evidence before replaying it. Do not invent a replacement rule or approve a guessed ID.

[Claude's native instruction documentation](https://code.claude.com/docs/en/memory) supports root instructions, path-scoped `.claude/rules` files, and on-demand skills. It states that instructions are context, while hooks enforce denials. Root instructions reload after compaction; nested files reload when relevant. [Codex's instruction documentation](https://developers.openai.com/codex/guides/agents-md) describes discovery from the project root to the working directory and a default 32 KiB aggregate limit. These are different loading contracts. Nested `AGENTS.md` must not be assumed to load merely because a root-started session edits a descendant file. Supported runtime versions still need the real-session checks in PR 1.

## Decision map and vocabulary

This written map is the requested Wayfinder artifact. It uses one document rather than creating a second issue hierarchy. Grill-with-docs ran autonomously under the brief's design authority. These decisions concern the proposed implementation; they do not alter live rules.

| Frontier question | Decision and trade-off |
| --- | --- |
| Where does approved wording live? | The existing canonical rule record retains wording, audience, approval, version, and provenance. Native files are its delivery projection. File edits alone cannot approve guidance. |
| What does "live" mean? | Approved guidance is authoritative. Publication says which revision each target has. Mechanical enforcement names tested controls. None implies the other two. |
| Where does the seam belong? | At approval and publication, where content changes. No publication interface receives a tool invocation or classifies its command. |
| How much projection machinery survives? | One deterministic renderer with Claude and Codex adapters, reusing existing export/config distribution where safe. No new daemon, receipt framework, rule registry, or feature flags. |
| Can a rule have no machine control? | Yes. Judgment guidance becomes active with `enforcement=none`. Safety controls retain exact verification. This removes fake enforcement claims without withholding approved instructions. |
| What happens offline? | Use the last complete local projection. Failed canonical approval stays pending. Failed distribution reports the target and retains its previous complete revision. Neither failure disables safety controls. |
| What happens in an existing session? | The approval response includes the approved wording for that session. Other active sessions receive an explicit update at the next task handoff or resume. No claim of instant fleet-wide context replacement. |
| What replaces semantic action triggers? | Always-on principles in root files; path guidance in native scoped files; action procedures in named skills with a short root discovery index. Consequential action restrictions remain technical controls. |
| What happens to automatic Jev? | Recommend a bounded PR review below. Changing its cadence is Joe's call. Existing cadence remains until that ruling is settled. |
| What can be deleted from storage? | Derived delivery tables after all readers migrate. Rules, decisions, quotes, approval and retirement receipts remain permanent history. |

A **guidance rule** is an approved instruction requiring human or agent judgment. A **safety control** deterministically prevents a named harmful action. A **projection** is a local native instruction file generated from approved wording. **Approval** establishes authority. **Publication** distributes a revision. **Verification** observes actual behavior. These terms prevent "active" from continuing to mean "a machine makes this impossible to violate."

Three read-only design alternatives were compared under codebase-design. A repository-only design makes Git the authority but adds a source-delivery delay to every preference. A flexible export/sync framework supports many clients but risks rebuilding the current machinery. The chosen design retains the canonical record and only the two actual client adapters. It gives the approval caller one small interface and keeps placement and distribution inside one module. Zero-tech-debt removes the old callers with each replacement instead of maintaining parallel delivery modes.

## Native instruction layout

Root `CLAUDE.md` and `AGENTS.md` carry the same approved shared principles, repository entry points, and a concise index of specialized instructions. Generate their common wording from the same approved revision. Keep client loading instructions separate. Target fewer than 200 lines per root file and keep Codex's aggregate budget below its configured cap; measure actual bytes rather than raising the cap by default.

Directory guidance belongs beside the work, for example `hooks/AGENTS.md`, `migrations/AGENTS.md`, and `mcp-server/AGENTS.md`. Claude may use matching `CLAUDE.md` files or `.claude/rules/*.md` with native `paths` frontmatter. Use one native Claude mechanism per rule, not both. Root-started Codex work explicitly reads the relevant descendant instructions before entering that area; scoped desks start in their assigned directory. The root index makes this discoverable without catalog retrieval. Real safety cannot depend on that voluntary read.

Procedures such as source study, review, source delivery, and rule capture belong in existing skills. Keep the trigger and any always-binding obligation in root guidance. Do not hide the obligation solely inside an optional skill. Business doctrine remains in its record layer, with the relevant skill naming its read verb; do not copy thousands of doctrine sections into root files.

Personal guidance stays private to its authenticated partner and outside public Git. Shared projections must never incorporate personal rules, client context, credentials, or raw transcripts. This plan installs nothing into `~/.claude` or `~/.codex`; any later distribution must preserve that scope boundary and use the already authorized configuration path.

Before removing delivery, account for every active shared and personal rule exactly once in a destination inventory. Preserve original IDs, wording, audience, supersession, and approval provenance. Unclear placement is an implementation finding, not permission to drop a rule. Moving content does not authorize rewriting it.

## One approval makes guidance live

Keep `teach`, `approve-rule`, `amend-rule`, and `retire-rule` as the public lifecycle. Remove `admit-rule` and the already-retired `activate-rule` compatibility door after migrating callers. Keep read access to history. A taught rule defaults to guidance; agents derive placement without forcing Joe to fill a contract questionnaire.

For an existing proposal, one `approve-rule` call binds Joe's recorded approval to the exact proposal version and statement hash. The server marks it active and records approval in the same transaction. Idempotent replay returns the original result; a stale version or reused key with different input refuses. The current version must be bound by the approval request or its prepared approval reference, not silently re-read and accepted after wording changed.

The interface returns the approved wording, scope, revision, and a truthful enforcement field. The normal session continuation publishes native files without another permission request. A pre-existing clear "yes" is authority for this clerical call; it is not a request to ask Joe again. Authentication and the current authority boundary remain intact. No caller can turn its own generated preference into Joe's approval.

For a judgment rule, empty control keys are valid. The approval path does not manufacture `judgment_ambient` controls, fixtures, delivery-pack admissions, or fake test references. New safety rules also become approved guidance in one step, while `enforcement=pending` remains visible until the real control is tested and delivered. Approving guidance cannot relax or disable any installed deny control.

Publication renders a complete revision to temporary files, verifies audience and content, and replaces each target atomically. It does not pretend that database and two files share one transaction. If one target fails, authority remains approved and the result names the pending target. Retry the same revision without creating another approval. Existing sessions use the response text; new sessions use the complete published files. Ordinary CI checks projection drift once per source change, never around tools.

## Honour automatic Jev at a cheaper point

The September 18 instruction is real. `hooks/lint-gate.py:236` preserves Joe's words: "make sure you are using jev to assist you during all these activities. if its not automatically doing that at this point the first thing you need to do is fix it so that jev is automatically involved without you having to remember or ask."

The requested `./run.sh retrieve` was tried with Jev, the date, and the quoted wording. In this checkout `tools/retrieve.py` queries doctrine only, not decision history. Its Jev results led to current `engineering-workflow-sop#14-jev-system-one-architecture`, section `6f2ad48d-4ca4-4a4d-8333-cb02e414ae2f@6`. The deployed decision reader, `find-precedent`, found September 18 decision `f2c3d819-b74e-46bd-ba96-46e118d23b3c`, "Host the command pre-check in an existing hook rather than admit a new sealed ingress", quoting "okay merge it so that it is part of every session automatically" and naming PR 1086. That is corroborating automatic-precheck authority, not a claim that it contains the full longer quote. The exact longer quote's own decision entry was not resolved in this bounded search.

Later authority strengthens the obligation. Decision `7b0d1353-4056-45f1-8d2f-011b76a51508`, September 20, makes Jev the default for qualified judgment work. Decision `0b11c89b-bd1c-45c2-a20f-b723dc7625a2`, September 24, explicitly says Jev is mandatory in building and diagnosis and rejects "advisory_only" as Joe's ruling. The current doctrine also requires acting judgments, visible abstention, budgets, and deterministic authority. These conflict with silently replacing all Jev use by an optional review.

**Recommendation for Joe's decision:** move the rule-machinery Jev calls from every write, spawn, and lookup to one automatically scheduled review per ready PR head. Bind the job to base SHA, head SHA, rule revision, question version, and model version. A new head invalidates the result; body edits and duplicate events reuse it. Batch the bounded diff into one review job using the existing client admission, provider limits, metering, and receipt path. Cap time, questions, and paid requests before execution; one job is not necessarily one provider request. No retry loop on each edit.

The review asks whether the change loses relevant approved guidance, introduces an unsupported enforcement claim, or weakens a retained safety control. Useful findings produce a reviewer decision and source-linked action. `unavailable` remains visible and triggers the existing independent review route; it never becomes a clean verdict. Do not call the new workflow advisory-only without Joe expressly changing the September 24 ruling. Preserve product Jev uses and their existing authority limits.

Joe must decide whether that PR cadence satisfies his automatic-Jev direction or whether a separate bounded build/diagnosis check is also needed. This planning job proposes the choice and does not enact or retire either policy. PRs 1 through 3 can ship without settling it; Jev-specific deletions in PR 4 wait. The evidence justifies reducing useless invocation placement, not abandoning the model or weakening deterministic controls.

## Deletion inventory

Counts below are physical lines measured at the source revision above, including comments and blanks. They describe candidate source, not guaranteed net savings. Shared callers must migrate before deleting a module. Historical migrations remain append-only. SQL definition sizes below are not lines to remove from historical files.

| File or group | Lines | Proposed action |
| --- | ---: | --- |
| `hooks/rule-pack-preuse-reselection.py` | 258 | Delete hook and every registration. |
| `lib/rule_delivery_preuse.py` | 341 | Delete per-call catalog and receipt path. |
| `lib/rule_scope_lookup.py` | 194 | Delete tool-time selector and Jev fallback. |
| `lib/claude_rule_delivery_dedupe.py` | 179 | Delete seen/receipt bookkeeping. |
| `lib/rule_cards.py` | 146 | Move approved wording to native files, delete card runtime. |
| `lib/rule_routes.py` | 343 | Delete routing, caps, and delivery state. |
| Per-call path subtotal | 1,461 | Same measured population as the supplied audit. |
| `hooks/rule-boot-gate.py` | 143 | Delete after native loading proof. |
| `lib/rule_boot_gate.py` | 1,394 | Delete boot epochs, fetch grammar, confirmations, and recovery state. |
| Boot subtotal | 1,537 | Remove boot arming from gate-integrity; keep integrity itself. |
| `hooks/delegation-gate.py` | 828 | Delete observer and its registrations. |
| `hooks/rule-claim-gate.py` | 242 | Delete Stop ceremony and duplicate registration. |
| `hooks/executor-tier-gate.py` | 351 | Delete after Jev cadence ruling; keep deterministic model-floor control. |
| Runtime candidate subtotal above | 4,419 | No shared safety library or business-runtime module included. |
| `ops/config/rule-cards.v1.json` | 3,007 | Delete after extracting approved wording and scope; no standing rule retirement implied. |
| `ops/rule-pack-preuse-reselection-selftest.py` | 436 | Delete old receipt/selector contract tests with their runtime. |
| `ops/rule-boot-gate-selftest.py` | 1,718 | Delete boot-fetch contract tests with their runtime. |
| `ops/rule-claim-gate-selftest.py` | 342 | Delete retired Stop protocol tests. |
| Listed obsolete test subtotal | 2,496 | New tests exercise native delivery and safety outcomes instead. |
| `lib/rule_admission.py` | 82 | Delete map-to-admission compiler after approval callers migrate. |
| `lib/rule_approval_worklist.py` | 461 | Replace technical-readiness activation workflow with direct authorized approval; remove obsolete readiness machinery. Whole-file deletion depends on caller migration. |
| `hooks/lint-gate.py` | 465 | Delete its `code_review` invocation and Jev-specific plumbing; retain deterministic lint. This is file size, not a deletion estimate. |
| `ops/jev_code_review.py` | 529 | Reuse qualified bounded review implementation if suitable; do not delete just because hook placement changes. |
| `lib/rule_operations.py` | 96 | Delete only if remaining safety/product callers do not need it. No savings booked. |
| `mcp-server/src/rule-boot.js` | 22 | Delete digest-page helper with final boot endpoint cleanup. |

The 4,419 runtime lines, 3,007 configuration lines, and 2,496 listed test lines total 9,922 candidate lines. This is a gross candidate inventory, not this PR's deletion count, and excludes uncertain partial edits and replacement code.

Delete all `rule-boot-gate.py` registrations in `ops/config/hooks.json`: SessionStart, SubagentStart, PreToolUse, PostToolUse, and PostToolUseFailure. Delete its three Codex registrations. Delete all per-call hook registrations in both files, including UserPromptSubmit and Stop. Remove the executor-tier registration only in PR 4. Remove delegation registrations from `.claude/settings.json` and the Codex template, and both rule-claim Stop registrations. Remove old installer requirements, missing-script wrappers, and integrity entries at the same time. Never leave a configured command pointing at a deleted script.

| Verb or database object | Measured source definition | Proposed action |
| --- | --- | --- |
| `admit-rule` | `mcp-server/src/tools.js:6495`, 113 lines through next registration | Delete verb and callers in PR 3. Its description still names a separate activation step. |
| `activate-rule` | `tools.js:6636`, 13 lines | Delete already-retired compatibility verb and discovery/registry entries. |
| `approve-rule` | `tools.js:6608`, 28 lines | Keep name; replace enforced-only contract and SQL coupling. |
| `teach` | `tools.js:6386`, 109 lines | Keep capture and audience safeguards; remove mandatory enforcement-placement questionnaire. |
| `applicable-rules` | `tools.js:6649`, 24 lines | Remove harness dependency. Retain only if real product callers require it; otherwise delete after caller inventory. |
| `standing-context` boot/scopes/pack branches | In shared `mcp-server/src/doctrine.js` | Delete obsolete delivery branches after callers migrate. Keep business context and focused rule/history reads. No whole-module deletion. |
| `ops.rule_pack` | `migrations/0291_rule_delivery_layers.sql:55`, 14 DDL lines | Drop derived runtime table in a new migration after export cutover. |
| `ops.rule_load_layer` | Same migration, line 74, 25 DDL lines | Replace placement with the small publication mapping, then drop derived layer/pack machinery. |
| `ops.rule_delivery_policy` | Same migration, line 132, 9 DDL lines | Drop shadow/enforced selector switch. No new feature flag. |
| `ops.rule_delivery_plan`, `ops.rule_pack_index`, `ops.validate_rule_load_layer_packs`, `ops.bind_rule_delivery` | Migrations 0291 and 0482 | Remove runtime functions and dependent triggers/grants through explicit new DDL, never `CASCADE`. |
| `ops.rule_admission` | Read by current approval and coverage machinery | Remove from new guidance authority path. Preserve historical rows read-only; do not erase receipts or pretend a table drop has been authorized. |
| `ops.rule_enforcement_point`, `ops.rule_control_binding`, `ops.enforcement_control_catalog` | Current safety/coverage readers | Retain real controls and exact bindings. Remove only delivery-theatre entries and caller coupling after proving no safety consumer depends on them. |
| `ops.rule_approval_receipt`, `ops.authority_receipt`, retirement receipts, `public.rule`, event/decision history | Immutable authority history | Keep. New guidance approvals need honest enforcement-free semantics, without rewriting old receipt meaning. |

The three derived table definitions occupy 48 historical DDL lines. Dropping runtime objects does not delete those 48 lines from migration history. No row counts, disk savings, or production dependency census are claimed.

Search-and-remove callers include `run.sh rule-approvals`, admission sync/audit/drift scripts, delivery eval wrappers, template fixtures, generated core IDs, config validation, hook deletion checks, SCAC mutation/operation registries, and native boot documentation. Retain any shared assurance, authentication, or product reader. The specific implementation PR owns a fresh exact-symbol caller inventory; a filename match alone is not evidence that an entire file can go. Replace legacy receipt-specific evals with the existing relevant guidance scenarios, not a new receipt compliance suite.

## Four independently shippable PRs

All four slices require their applicable red-green behavior tests before implementation, full `ops/ci.sh`, normal hosted checks, and source review. Each must leave a usable system if the next slice never lands. This plan PR does not authorize merging or deploying them.

1. **Publish native guidance while existing delivery still works.** Add the small deterministic projection and approved-rule destination inventory. Populate root and scoped files without changing rule wording or authority. Replace conflicting store-only boot instructions only when native output is ready. Verify Claude root load, scoped load, compaction, and subagent inheritance, plus Codex root and directory-start discovery and explicit descendant reads. Check private audience exclusion and file budgets. Use existing distribution tooling; do not install anything from a feature checkout. Shippable benefit is readable, local guidance and measured loading proof. Rollback reverts projections; the existing delivery path still works.
2. **Remove rule lookup, boot gates, and inert observers.** Remove the listed per-tool and boot runtime, registrations, dedupe state, claim/delegation observers, and their obsolete tests. Keep focused store reads and all Jev build/diagnosis hooks pending the cadence decision. Test a new session, resume, compaction, subagent, and an ordinary tool action with store unavailable: native guidance is available and no boot/rule lookup request occurs. Test representative root, scoped, and skill-trigger obligations against the destination inventory. Shippable benefit is no per-call rule network/receipt tax. Rollback restores this slice without undoing PR 1.
3. **Make approval independent of technical enforcement.** Change `teach` and `approve-rule` at the public seam. First fail the test "Joe approves a judgment proposal with zero controls and it becomes active in one call". Preserve authority, audience, optimistic version, replay, and exact quote tests. Verify publication success and partial failure honestly through the same interface. Remove `admit-rule`, `activate-rule`, readiness worklist coupling, and derived delivery tables/functions only after their last callers migrate. Preserve immutable history and safety coverage readers. Reproduce the exact brief-supplied judgment record in disposable fixtures after resolving it. Shippable benefit is immediate guidance approval. Rollback needs a forward correction for any already-approved guidance; never reclassify it as unapproved or erase history.
4. **Move automatic Jev to the approved cadence.** After Joe settles the recommendation, wire one ready-head review job through the existing review/client path. Test duplicate events, changed heads, unavailable service, request caps, source binding, and meaningful finding handling. Remove per-write review, executor-tier semantic judgment, and remaining rule-trigger/compile Jev calls only within this scope. Preserve deterministic model-floor and named model restrictions, metering, and qualified product Jev uses. Shippable benefit is bounded automatic review instead of failing hook fan-out. If Joe retains a build/diagnosis check, implement that bounded check here without restoring per-tool rule selection. Rollback restores cadence without resurrecting retired delivery tables.

For any slice editing `hooks/`, run `python3 hooks/gate-integrity.py --bless <changed gate file names>`, commit the baseline, and remove deleted gates from it. Re-run strict integrity and configured-script existence checks. Use bare filenames, not `hooks/` paths. This planning PR edits no hook and needs no blessing.

## Safety and acceptance

Keep force-push denies; secret and private-key catches; destructive SQL catches; canonical-file guards including `record-home-gate`, `canonical-edit-gate`, `one-repo-gate`, `bash-write-gate`, and `write-effect-check`; weekend quiet; CoStar lane; model floor; and GitHub pacing. Keep gate-integrity for surviving controls. Keep deterministic lint, CI, source authentication, tenant/audience isolation, and immutable provenance.

Do not replace semantic SQL or private-key checks with loose permission globs. Keep existing controls until an equivalent replacement demonstrably denies the same harmful operations across both clients and shell/tool variants. Each slice runs retained safety selftests and explicit positive/negative cases: force push denied while ordinary branch push works; private-key and destructive-SQL fixtures denied while safe text and qualified reads work; canonical writes denied while authorized isolated-tree edits work; weekend/CoStar/model-floor restrictions unchanged. Synthetic inputs only, no production effects.

Success is observed behavior: no catalog fetch or empty rule receipt around an ordinary tool call; no digest-fetch prerequisite on startup or compaction; correct scoped guidance actually loaded; one authorized approval makes judgment guidance active; unavailable publication is reported accurately; retained safety denials still fire. Measure new latency and context output on the same operation population before claiming a speedup. Do not turn the audit's 355 ms median into a guaranteed saving.

## Planning verification and remaining uncertainty

The plan was checked against the supplied full audit, current source and migration definitions, deployed verb schemas, focused live doctrine and ruling reads, and official client instruction documentation. Exact longer-quote decision lookup, proposed judgment record details, fleet-specific native loading, and production dependency removal remain explicitly unverified. They are implementation evidence tasks, not reasons to implement more machinery in this plan.

No behavior tests are added for a documentation-only PR. Pocock TDD defines the later behavior test seams above. Full CI and the final changed-path check are recorded in the PR body. The versioned copy of this plan is `out/machinery-audit/rules-plan.md`; the requested shared coordination copy has identical bytes.

The runtime and obsolete-test totals can be reproduced without calling a live service:

```sh
wc -l hooks/rule-pack-preuse-reselection.py lib/rule_delivery_preuse.py lib/rule_scope_lookup.py lib/claude_rule_delivery_dedupe.py lib/rule_cards.py lib/rule_routes.py hooks/rule-boot-gate.py lib/rule_boot_gate.py hooks/delegation-gate.py hooks/rule-claim-gate.py hooks/executor-tier-gate.py
wc -l ops/config/rule-cards.v1.json ops/rule-pack-preuse-reselection-selftest.py ops/rule-boot-gate-selftest.py ops/rule-claim-gate-selftest.py
```

Verb spans count from their quoted registration to the next quoted registration in `mcp-server/src/tools.js`, including intervening comments. Table sizes count the named `create table` statement through its closing `);` in migration 0291. Recount on each implementation head; neither measurement is a production dependency audit.
