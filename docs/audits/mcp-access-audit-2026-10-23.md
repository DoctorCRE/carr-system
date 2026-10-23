# CARR MCP access-control audit — 2026-10-23

Report only. No runtime, schema, registry, test, configuration, or deployment changes.

## Result and scope

**The requested invariants do not all hold.** Two high-severity ownership gaps allow selected registered verbs to modify another partner's personal records. Error responses disclose internals, and generated authority metadata does not fully describe the checks actually encountered by callers. No unauthenticated registered-verb entry or caller-controlled SQL injection was demonstrated.

Audited checkout: `DoctorCRE/carr-system` at `dca1863a78a43eeed47cdece6b1875be3b8741f8`. All `file:line` citations refer to that revision. The assembled `TOOLS` object contains **365 verbs in 50 source modules: 255 writes and 110 reads; 28 `humanOnly`, 51 `authorityOnly`, and one `humanCallerOnly`**. Every runtime verb was enumerated and checked against the generated registry; the complete census appears below. Counts describe the checkout, not a live deployed inventory.

Method: source tracing through HTTP authentication, dispatch, handlers, query construction, relevant migration definitions, existing tests, and repository probe files; selected offline tests and synthetic in-memory clients. Registry/schema checks cover every registered verb. Row-level findings are supported by source and focused fixtures; live database grants, RLS, deployed configuration, and actual stored contents were **not** tested. Passing mocks are not a proof of deployed database isolation.

`AGENTS.md` was read first. The user's explicit Markdown deliverable, draft-only PR, and no-network audit restrictions take precedence over repository defaults requiring record-layer prose, live doctrine fetches, full delivery/merge, and live probes. An isolated local clone was used because the worktree helper performs an automatic fetch. No audit request contacted the Worker or a database; no dependency installation, production write, or deploy was performed. The user explicitly authorized GitHub-only publication/readback; the offline restriction applies to live CARR and production services. No environment dump, secret file, keychain, or database dump was opened. Fixtures use synthetic values.

Interpretation: “another partner's records” includes records explicitly marked personal or protected by an owner check elsewhere in the same module. Shared CRM rows are intentionally co-managed. The broader literal prohibition on all cross-partner access is therefore **not** an existing product invariant; deliberate exceptions are recorded separately below. Effort sizes: **S** = several hours/up to one day; **M** = approximately 1–3 engineering days including negative tests; **L** = a larger schema/authorization project. Estimates are implementation guidance, not authorization to change behavior in this PR.

## Findings, ranked by severity

| ID | Severity | Finding | Effort |
|---|---|---|---|
| F1 | High | Personal doctrine ownership is skipped by three mutation verbs | M |
| F2 | High | Personal loop ownership is not enforced by update/close/amend paths | M |
| F3 | Medium | HTTP and tool error paths return stacks, SQL internals, and raw error values | M |
| F4 | Medium | Registry flags omit enforced human/partner restrictions | S |
| F5 | Medium | Sponsored-authority dispatch and handler human checks disagree | M |
| F7 | Medium | Dispatch acknowledgement does not authorize the target recipient | M |
| F6 | Low | Several authentication adapters accept a bare Authorization token | S |

### F1 — Personal doctrine can be changed through neighboring unguarded verbs

**Evidence.** `mcp-server/src/doctrine.js:84` locks a section and reads its document's `visibility` and `owner_actor_id`. `write-doctrine-section` explicitly rejects a personal document owned by someone else at `doctrine.js:421`; `change-doctrine-sections` does the same at `doctrine.js:787`. In contrast:

- `set-doctrine-refs` checks version, then deletes/replaces links and retires/replaces edges without checking that owner (`doctrine.js:480`, `doctrine.js:488`, `doctrine.js:494`).
- `retire-doctrine-section` checks version and then retires any resolved section (`doctrine.js:523`, `doctrine.js:531`).
- `claim-doctrine-sections` checks the current claim holder, not the document owner, and inserts/replaces a claim on any supplied section ID (`doctrine.js:726`, `doctrine.js:730`, `doctrine.js:737`). Claims are advisory; this does not establish that they block all owner edits.

The generic `personal_owner_required` gate requires an owner to exist and applies to create/write; it does not authorize the caller for these operations (`migrations/0075_doctrine_store.sql:289`). Writer grants cover the affected tables, including link/claim deletion (`migrations/0076_doctrine_grants.sql:12`, `:27`). The SCAC monitor's session/effect checks are not a row-owner predicate (`migrations/0467_siep18_atomic_db_monitor_grants.sql:669`, `:686`, `:692`). No compensating doctrine owner policy was identified in the migration source review.

**Local reproduction.** Through the real `executeRegisteredTool`, an in-memory Dell-personal section at version 2 was targeted by a Joe-shaped principal with a fresh synthetic idempotency key. The two guarded content verbs refused `personal_doc_not_owner`; refs, retirement, and claiming succeeded and emitted their domain mutations. The fake client follows the existing doctrine version-test seam; it proves missing handler authorization, not a production database exploit. Existing tests such as `mcp-server/test/doctrine-bigint-versions.test.mjs` exercise version handling but do not close this owner gap.

**Why it matters.** An authenticated caller allowed these ordinary write verbs can alter or remove another partner's personal knowledge using a known section ID/version, despite the same document being protected against direct content edits. Idempotency and optimistic concurrency do not grant ownership.

**Concrete fix.** Centralize an owner assertion immediately after locked section/document resolution and apply it to every mutation, including refs, retirement, and claims. Resolve any permitted agent delegation from authenticated server identity, never an argument. Back it with owner-constrained SQL/RLS or narrow definer functions. Add Joe-to-Dell and Dell-to-Joe personal fixtures plus shared-document positive controls. **Effort: M.**

### F2 — Personal loop mutations lack owner checks

**Evidence.** `add-loop` marks `open_loop` and `idea` rows personal and stores `personal_to=actor.id` (`mcp-server/src/tools.js:7556`, `:7560`). `resolveLoop` accepts ID/number/kind/status but does not select or constrain `tier`/`personal_to` (`tools.js:1204`, `:1208`, `:1219`). Its consumers only version-check before writing: `update-loop` (`tools.js:7624`, `:7796`), `close-loop` (`tools.js:7910`, `:8015`), and `amend-closed-loop` (`tools.js:8070`, `:8124`, `:8138`). `read-loop` ignores its actor and selects personal content by ID or number (`tools.js:3769`, `:3777`, `:3784`), including amendment history.

The original schema explicitly calls out the personal/shared boundary and expects consumers to filter it (`migrations/0024_loop_item.sql:264`). It grants the writer direct loop updates (`:260`). No loop-owner RLS policy was identified in the migration review. Shared `action_required` handoffs are intentionally cross-partner: `mcp-server/test/loop-owner-repair.test.mjs:74` must not be mistaken for a private-record denial test.

**Local reproduction.** Real registered dispatch with a synthetic Dell-personal loop and Joe caller accepted update, close, and closed-loop amendment with the current version. The fake client observed one, one, and two domain mutations respectively. Existing amendment/deal-item suites also pass their normal behavior cases; they do not establish personal ownership isolation.

**Why it matters.** Knowing or discovering a loop reference and version permits another authenticated partner/runtime to replace, close, or amend a personal row. Cross-partner recall was explicitly intended (`migrations/0024_loop_item.sql:3`), so the readable version is not itself classified as a separate privacy bypass. `owner` vocabulary validation checks the requested business owner, not the caller's right to mutate `personal_to`. Error conflict history can further expose prior values before an ownership decision (`tools.js:1014`, `:1032`).

**Concrete fix.** Make mutation-time loop resolution accept authenticated principal/sponsor context and authorize the row before version/conflict processing or mutation. Apply the same predicate to locked updates and database policies; keep read visibility an explicit separate policy. Preserve shared handoff behavior; explicitly map legacy machine-owned personal rows before introducing sponsor-based ownership. Add private cross-partner denial tests for all three mutations and both identity directions. **Effort: M.**

### F3 — Error paths disclose implementation and database contents

**Evidence.** Public `/health` returns raw exception text (`mcp-server/src/index.js:128`). Ingest, pipeline, and capture adapters also expose raw messages (`index.js:192`, `:230`; `capture.js:78`). MCP returns `ToolError.payload` unchanged (`mcp.js:1000`), returns exception stacks with only URL-shaped redaction (`mcp.js:1021`, `:1034`), and returns unredacted outer RPC exception text (`mcp.js:1053`, `:1062`). PostgreSQL translation forwards table/column/constraint names and raw driver details/messages (`tools.js:219`, `:230`), while constraint enrichment exposes SQL definitions (`tools.js:190`, `:205`). Workflow handlers wrap raw errors into public `detail`, e.g. `workflow-cutover.v5.js:160`, `:191`.

**Local evidence.** A synthetic CHECK error containing `Failing row contains (synthetic-private-record-marker)` survives translation. A synthetic workflow database failure survives in `ToolError.detail`; an MCP failure with no configured test database returns stack frames without making a database call. Existing `error-unmasking.test.mjs:33`, `:50`, `:94` and `constraint-refusal-legibility.test.mjs:14` intentionally assert internal details and narrow connection-string redaction. Passing these tests documents the present leak; it does not refute it.

**Why it matters.** Anonymous health callers and authenticated tool callers can learn internal schema, code paths, and possibly values from failing rows. Redacting DSN-shaped URLs does not remove embedded tokens, arbitrary messages, or row data. This audit did not access or demonstrate exposure of a real secret.

**Concrete fix.** Use one public error projection with stable codes, safe input-field identifiers, and correlation IDs. Keep stack traces, raw driver messages/details, constraint definitions, and unfiltered wrapped exceptions in restricted diagnostics. Cover every adapter with synthetic secret, failing-row, and stack non-disclosure tests; preserve useful validation guidance through curated messages. The opaque top-level handler at `correlation.js:147` is a useful existing pattern. **Effort: M.**

### F4 — Registry metadata omits enforced human/partner restrictions

**Evidence.** `approve-retrieval-proposals` and `retire-retrieval-curation` advertise `human_only=false` and `authority_only=false` (`mcp-server/src/scac-mutation-registry.current.generated.js:423`, `:2736`). Their descriptions say HUMAN-ONLY (`situation-retrieval.js:294`, `:333`); the invoked SQL functions reject a nonhuman actor (`migrations/0135_situation_retrieval.sql:406`, `:438`). `mcp-server/test/situation-retrieval.test.mjs:95` explicitly expects the flags to remain absent using an outdated comment that the dispatcher does not read `humanOnly`; it now does at `tools.js:9023`.

The same under-declaration occurs for all four deal-item verbs (`record-deal-item`, `backfill-deal-items`, `instruct-deal-item`, `complete-deal-item`): both flags are false but their common handler requires `canExercisePartnerAuthority` (`deal-items.js:115`, `:118`). `resolve-post-call-candidate` likewise omits the flag while requiring verified partner authority (`tools.js:9865`, `:9873`). These need the correct authority classification, not an automatic switch to an authority database credential.

**Why it matters.** Discovery and profile consumers receive a weaker classification than the actual operation. A machine may be admitted far enough to reach the database before refusal. This is a contract mismatch and unnecessary privileged execution attempt, **not a demonstrated human-check bypass**. The false `authority_only` value alone is not proof that these functions need the separate authority credential.

**Concrete fix.** Decide and encode the operation's intended human-versus-sponsored authority semantics, align handler/database checks and generated metadata, and replace the stale flag assertions with end-to-end dispatch tests. Set `authorityOnly` only if the intended database role requires it; do not widen grants to make tests pass. **Effort: S.**

### F5 — Human-only dispatch admits actors that the handlers then reject

**Evidence.** Shared dispatch treats `humanOnly` as verified partner authority, including server-bound sponsored agents unless `humanCallerOnly` is true (`mcp-server/src/tools.js:9023`). The unattended allowlist includes the lead approval/undo trio (`tools.js:8966`). Yet `approve-lead-draft`, `approve-lead-move`, and `undo-lead-move` reject `!actor.human` (`lead-automation.js:249`, `:263`, `:278`). `undo-invoice-close` does likewise (`invoice-automation.js:142`); `answer-board-question` requires a human Joe/Dell (`board-answers.js:257`). `answer-work-request-for-joe` also has a narrower direct-human check (`work-request-intake.js:738`). Only `confirm-merge` currently declares `humanCallerOnly` in the assembled registry.

Further mismatches identified in the complete census:

- `append-tour-entrance-verification-receipt` has `authorityOnly` but no `humanOnly`, although its verifier requires a literal verified human (`tour-property-jurisdiction.js:92`, `:276`).
- `record-tour-map-promotion-receipt` and `record-tour-pdf-human-review` have `humanOnly` but no literal-human flag; their handlers require actual humans (`tour-map-promotion.js:76`, `:145`; `tour-artifacts.js:64`, `:196`). Existing negative tests affirm this restriction (`tour-map-promotion.test.mjs:60`; `tour-artifacts.test.mjs:103`).
- F01/J102 shared store checks require a human for `humanOnly` and `verified_partner` for `authorityOnly`, unlike common dispatch (`record-source-authority-store.v5.js:515`; `cre-lifecycle-store.v5.js:1434`). Affected verbs include `register-record-source-authority-policy`, `record-artifact-preservation-hold`, `record-lifecycle-correction`, `cancel-pending-deal`, `commit-winning-property`, `record-deal-closing`, and `record-evidence-subject-link`. `record-partner-lifecycle-fact` has an additional payload-dependent direct-human restriction for partner-authored fact kinds (`cre-lifecycle-store.v5.js:3392`, `:3440`), rather than that generic store gate. Their tests affirm the stricter policy (`record-source-authority-store.v5.test.mjs:607`; `cre-lifecycle-store.v5.test.mjs:513`).

**Local evidence.** Synthetic handler invocations with admitted sponsored nonhuman actors reach `human_approval_required` for the lead/invoice verbs and `board_answer_requires_human_partner` for the board verb. Registry-wide early-refusal tests and zero-argument admission probes do not exercise these later checks with valid arguments.

**Why it matters.** The published authority classification and unattended allowlist promise an operation that later refuses. This is a fail-closed availability/contract defect, not an authority escalation. The generated parity check compares only source/schema/write/human/authority fields; it does not attest `humanCallerOnly` semantics (`mutation-registry.js:51`).

**Concrete fix.** Choose one intended policy per affected operation. For actual human presence, declare and attest `humanCallerOnly` and keep those verbs out of unattended admission. For sponsor-authorized actions, use the shared verified-authority predicate instead of raw `actor.human`, preserving any separately documented actor-specific rules. Add valid-argument tests through `callTool`/registered dispatch for human, sponsored, unattended, reviewer, and unsponsored actors. **Effort: M.**

### F7 — Dispatch acknowledgement is not bound to its recipient/session

**Evidence.** `acknowledge-dispatch` accepts a caller-selected dispatch reference and stage (`mcp-server/src/dispatch-spine.js:127`, `:133`). Its definer derives the caller but checks only that the dispatch exists before inserting (`migrations/0531_room_dispatch_spine.sql:535`, `:549`, `:554`). It does not authorize the caller against the target session, recipient, or desk assignment. Uniqueness on `(dispatch_ref, stage)` makes later calls return the first acknowledgement, including its original actor (`:559`, `:565`). Dispatch history uses these rows as stage evidence (`:289`).

The source explicitly leaves received/acknowledged stage discipline to call sites (`:515`). The repository probe intentionally accepts a second fixture identity, `wr119-intruder`, for acknowledgement without establishing target-session membership (`mcp-server/test/dispatch-spine-postgres.sql:220`). That proves the expected current contract from source; the SQL probe was not executed in this audit.

**Why it matters.** An authenticated actor permitted the verb and knowing a dispatch reference can reserve another session's stage before its recipient acts, producing misleading delivery/acknowledgement evidence and preventing a later legitimate acknowledgement from replacing it. Authenticated authorship is preserved, but target authorization is absent. This is a documented trust assumption that fails the requested record-authorization invariant, not identity spoofing.

**Concrete fix.** Resolve permitted recipients/session identities or explicit desk assignments inside the definer before insertion, enforce which principal may write each stage, and deny unrelated actors before deduplication returns another actor's receipt. Add two-principal negative probes alongside the current attribution tests. **Effort: M.**

### F6 — Authorization parsing permits a missing Bearer scheme

**Evidence.** Multiple machine/device adapters remove an optional Bearer prefix rather than requiring one (`mcp-server/src/identity.js:348`, `:377`, `:438`, `:528`, `:562`, `:910`; `index.js:154`; `capture.js:28`). A synthetic `agentActorForToken` call accepts the same valid test secret both bare and with `Bearer`; a Basic-form header does not authenticate.

**Why it matters.** Adapters disagree with OAuth and the documented HTTP grammar, making gateway validation and audit interpretation inconsistent. **Possession of the correct secret is still required; this is not an authentication bypass.**

**Concrete fix.** Share a strict case-insensitive Bearer parser across adapters. Refuse absent schemes, empty credentials, duplicate/multiple credentials, and malformed values before lookup; test positive and negative cases at each entry. **Effort: S.**

## Invariants verified to hold, and their bounds

1. **All 365 registered verbs are behind an authenticated HTTP principal.** Exact alternate machine doors run only for `/mcp`; otherwise OAuth/browser authentication resolves the actor before dispatch (`index.js:697`, `mcp.js:1072`). Missing/unknown principals refuse (`mcp.js:1088`); Doc requires its human partner principal (`mcp.js:1092`). Internal exported handlers are not independently authenticated HTTP endpoints. This is source and offline identity evidence, not a live deployment attestation.
2. **All 365 assembled contracts match their generated entries.** Runtime enumeration and `assertRegisteredOperation` checked source locator, schema digest, write, human-only, and authority-only fields with zero mismatches (`mutation-registry.js:48`). Unknown verbs, altered metadata, and undeclared top-level fields refuse before handler execution (`tools.js:8980`, `mutation-registry.js:37`). This verifies structural parity; F4/F5 show why semantic parity is separate.
3. **Caller arguments cannot select the central authority identity.** Reserved fields are rejected before delegation/database acquisition (`tools.js:8876`, `mcp.js:641`). OAuth native authority is recomputed from exact registered client bindings (`identity.js:138`, `:201`). Server-held authentication branding prevents copied/self-asserted actors acquiring trusted principal receipts (`identity.js:620`).
4. **Authority connection selection fails closed.** A required authority operation cannot fall back to the routine writer (`mcp.js:710`, `:809`). Transaction-local actor, sponsor, and fixed tenant context are installed, then database principal/role readback is verified before the registered handler (`mcp.js:51`, `:829`, `:831`). Writer-path reads use `begin read only` (`mcp.js:816`). Ordinary reader-route calls do not acquire this context; their protection depends on their views and grants (`mcp.js:597`, `:771`).
5. **Human/authority gates and delegation are centralized.** Every flagged human verb rejects unverified, probe, and review identities; unattended authority is restricted to an explicit eight-verb set (`tools.js:8966`, `:9023`, `:9040`). `confirm-merge` has the real-human exception. `call-verb` recurses through the same profile and contract checks and prohibits recursive wrappers (`mcp.js:669`, `:699`, `:707`). F4/F5 qualify later handler semantics.
6. **Idempotency requirements are enforced where declared.** 252 of 255 write schemas require `idempotency_key`; required-field enforcement rejects missing/null/empty required values (`tools.js:843`, `:9054`). The envelope also rejects a falsy key (`tools.js:299`), hashes operation plus actor/client/sponsor/tenant/arguments, and refuses mismatched reuse before returning a stored response (`tools.js:310`, `:334`). This prevents replaying another principal's stored response under a reused key. It is not a universal concurrency-serialization claim: advisory key locks cover an explicit subset (`tools.js:331`).
7. **Contract-specific optimistic concurrency checks are present.** The census records numeric, nested, event, version-vector, and digest forms below. `versionGuard` locks and refuses missing/invalid versions (`tools.js:984`, `:996`); permitted disjoint-field rebasing is explicit (`tools.js:1024`). No missing mandatory-version bypass was found in the reviewed optional-schema cases. SQL-backed CAS enforcement remains source evidence when its database probes were not executed.
8. **Selected private scopes and response projections are explicit.** Personal-memory recall and mutations bind owner and tenant; transaction-routed memory reads install sponsor context (`memory.js:154`; `memory-kernel.test.mjs:195`; `memory-rls-connection.test.mjs:9`). Notification functions resolve the caller rather than accept recipient/actor arguments (`notifications.js:53`, `:74`). Tour sharing emits allowlisted projections excluding secret/digest-bearing fields (`tour-sharing.js:41`, `:95`; `tour-sharing.test.mjs:99`). `export-email-domains` returns aggregate domains, not addresses (`tools.js:9971`; `export-email-domains.test.mjs`). These are bounded controls, not a blanket proof for all stored JSON or SQL functions.

### Contract exceptions and limits

- **Single tenant, not demonstrated general multitenancy.** `organizationTenantForActor` always returns `carr-internal` (`identity.js:61`). It rejects caller-selected tenancy by construction but does not prove that every legacy CRM table/view would isolate a second tenant. Many shared-business reads use the stateless reader connection and unscoped views. Before onboarding another tenant, add server-derived tenant provisioning and comprehensive row policies/definers plus two-tenant positive/negative database probes. That is an **L** follow-up, not a claim of a currently reachable foreign-tenant principal.
- **Shared partner book is intentional.** Deal/lead ownership is business assignment, not universal row-access authorization (`mcp.js:116`). `set-next-action` deliberately permits assigning either partner and replacing that partner's open actions (`identity.js:270`; `tools.js:4096`). `review-memory` explicitly allows either partner to inspect the other's personal memories, while mutations remain owner scoped (`memory.js:164`; `memory-kernel.test.mjs:143`). These exceptions mean the literal “never another partner's record” invariant does not hold by design. They do not justify F1/F2's missing private-write checks.
- **Three idempotency-schema exceptions:** `call-verb` delegates to the target's contract (`mcp.js:699`); `whats-new` requires a key only for `mark_seen=true` and wraps that mutation (`whats-new.js:22`, `:100`); `dry-run-doctrine-gates` records gate-run evidence without a key/envelope (`doctrine.js:1443`). The latter is an explicit audit-side-effect contract, not a rollback-only/no-write operation; repeating it may append another gate-run record.
- **Version details:** 54 write schemas require top-level `base_version`; nine require another top-level concurrency field. Loop update/close/amend and header edit declare optional base versions but unconditionally call the rejecting guard (`tools.js:7625`, `:7862`, `:7911`, `:8071`). Campaign reattachment requires `piece_base_version` conditionally (`tools.js:8496`). Lease creation accepts no existing version; replacement requires one (`tools.js:4254`; `migrations/0856_qualify_security_definer_dependencies.sql:2334`). Multi-section doctrine changes validate each nested version (`doctrine.js:338`, `:813`). Lifecycle reconciliation requires a state digest; some other lifecycle transitions intentionally compare a server-loaded state digest rather than a client numeric version. No-base cells in the appendix mean the schema does not declare that concurrency field, not that every operation ought to require it.
- **No universal secret-free-output proof.** No registered handler explicitly returning server-held OAuth/refresh tokens or database credentials was found in source review. Handlers receive a query client/actor/arguments, while credential selection stays in transport (`mcp.js:771`, `:811`). Stored prose/JSON and arbitrary error messages can contain sensitive data; F3 defeats a universal non-disclosure claim. Data contents were not inspected. Separate capture/share HTTP bootstrap routes intentionally issue session credentials; they are not registered business verbs.

### Additional source/probe coverage

Store wrappers do not merely trust actor fields: F01 compares independently resolved actor identity and, for authority operations, human/class (`record-source-authority-store.v5.js:957`); J102 compares actor, sponsor, and authorization class (`cre-lifecycle-store.v5.js:1722`). Personal Doc activity binds tenant and sponsor (`doc-activity.js:82`, `:113`); conversation sharing/revocation checks creator (`migrations/0523_doc_conversation_write_doors.sql:134`), and listing requires creator or an unrevoked grant (`0525_doc_conversation_list.sql:135`). Notification acknowledgement checks recipient (`0521_r03_notifications.sql:282`). Session readers accept no actor selector, with two-actor SQL exclusion probes (`session-identity.js:113`; `session-identity-postgres.sql:184`). Schedule and system-work views bind the sponsor (`schedule-board.js:93`; `system-work-census.v5.js:291`). These database statements/probes were inspected, not executed.

Correspondence consent binds the mailbox to transaction-derived partner identity (`governed-correspondence-store.v5.js:231`, `:350`; `migrations/0700_governed_correspondence_store.sql:411`). The plain-reader `read-correspondence-thread` path does not install the sponsor context its SQL filter expects (`governed-correspondence-store.v5.js:273`; `0700_governed_correspondence_store.sql:599`); it is a fail-closed activation/read-availability limitation, not evidence of another partner's mail escaping. Its current contract says no runtime adapter writes these receipts yet.

## HTTP entry review

| Surface | Verified control / exception and evidence |
|---|---|
| `/mcp`, `/doc/mcp` | Origin gate runs before alternate doors (`index.js:683`); machine doors match exact `/mcp`, Doc uses OAuth (`index.js:697`; `mcp.js:1092`). |
| CORS | Canonical HTTPS exact allowlist; hostile/null/malformed Origin refuses; no Origin allows native clients (`oauth-policy.js:22`). Only a validated Origin is reflected with `Vary: Origin` (`:64`). No arbitrary wildcard reflection found. |
| OAuth audiences | `/mcp`, `/doc/mcp`, and `/pipeline/changes` are distinct audiences; legacy broad grants are confined to `/mcp` (`oauth-policy.js:85`). S256 PKCE configured (`index.js:610`). |
| OAuth bootstrap | Google signature/issuer/audience/expiry and verified-email allowlist checks (`google-oidc.js:222`, `:362`); callback is browser-bound (`:340`), consent same-origin and atomically consumed (`:457`). Public auth bootstrap is not an anonymous record verb. |
| Browser session MCP | Session gate and exact same-Origin requirement (`dealroom-web.js:1100`, `:1139`); opaque hashed, bounded sessions and Secure/HttpOnly/host-only cookies (`:149`, `:418`). Generic MCP does not additionally require the signout CSRF header. |
| `/healthz`, `/health`, `/release` | Deliberately public (`index.js:208`); healthz has no DB read, release exposes provenance, health error path has F3. |
| `/pipeline/changes` | OAuth actor or browser-session gate; handler requires actor and GET (`index.js:267`; `dealroom-web.js:1144`; `dealroom.js:63`). |
| `POST /ingest` | Server token-map source authentication before body processing (`index.js:152`); values use Neon tagged parameters (`:185`); F3/F6 apply. |
| Capture routes | Exact POST/GET route map (`capture.js:485`); device claim binds credential to requested device (`:88`); session hash and expiry on subsequent calls (`:129`). Call-context uses caller-selected shared deal IDs (`:378`); not proof of multitenant filtering. Session-token issuance is intentional. |
| App/system/room/tour APIs | Browser-session gate (`dealroom-web.js:1100`); unsupported legacy v1 routes refuse (`:1074`). Staging E2E session minting is unavailable outside staging (`:396`, `:424`). |
| Share/report host | Isolated before MCP (`index.js:689`); exact public assets and session-scoped allowlisted projection (`reports-web.js:16`, `:136`, `:183`). Mutating share endpoints require session, same Origin, fetch-site and UUID idempotency (`:193`); strips CORS (`:219`). Share credential exchange is intentional. |
| Legacy hostname / unknown routes | GET/HEAD bookmark redirect allowlist drops queries; other legacy-host paths, including MCP, return 404 (`dealroom-web.js:1048`). Unknown main paths return 404 (`index.js:215`). |

## Postgres string construction review

String-built SQL **exists**; it is not accurate to describe the layer as using only fixed query text. The reviewed dynamic families use fixed identifiers/fragments and bound values. No caller-controlled SQL-text injection was found in those constructions.

| Construction | Evidence and why the reviewed use is bounded |
|---|---|
| Deal/lead dynamic UPDATE columns | Field allowlists precede column joining; values are positional parameters (`tools.js:4301`, `:4324`, `:5319`, `:5373`). |
| Shared version helper table names | `versionGuard` interpolates `table`, but callers supply code literals, not request table names (`tools.js:992`; calls at `:4323`, `:7625`). |
| Event cause | SQL interpolation uses a closed server-side allowed-cause set (`tools.js:372`, `:409`, `:423`). |
| Deal-item identifiers | Table/column pairs are code constants (`deal-items.js:142`). |
| Doctrine target identifiers | Caller kind is resolved through a fixed target-kind map (`doctrine.js:212`). |
| System-work filters/sorting | Enum/static fragment choices; filter values bind separately (`system-work-census.v5.js:253`). |
| Workflow function identifiers | Interpolated function comes from fixed call sites (`workflow-cutover.v5.js:154`, `:427`, `:435`). |
| Ingest insert | Neon tagged-template interpolation binds values; it is not raw SQL concatenation (`index.js:185`). |

These observations do not prove every deployed definer function or future identifier addition safe. The audit did not execute SQL against PostgreSQL. The repository's rollback probes address narrower database invariants, as described below.

## Verification record

The main offline batch executed **250 assertions across 14 files: 249 passed, one failed due to the host's `spawnSync python3 EPERM` error**. Each selected file was inspected for effects and launched with a synthetic-only process configuration and a scratch preloader refusing fetch/WebSocket/HTTP(S)/TCP/TLS/UDP/DNS entry points. No guard or probe was added to the repository. The execution shape was `node --require <scratch-offline-guard.cjs> --test-reporter=tap mcp-server/test/<suite>.test.mjs`.

| Suite (`mcp-server/test/`) | Passed / total |
|---|---:|
| `identity.test.mjs` | 45/45 |
| `human-only-dispatch.test.mjs` | 91/91 |
| `unattended-authority.test.mjs` | 27/27 |
| `control-plane-authority-boundary.test.mjs` | 8/8 |
| `required-args-nullable.test.mjs` | 4/4 |
| `verb-gate-checks.test.mjs` | 24/24 |
| `current-mutation-registry-generation.test.mjs` | 3/3 |
| `memory-rls-connection.test.mjs` | 1/1 |
| `memory-kernel.test.mjs` | 17/18 |
| `call-verb-passthrough.test.mjs` | 7/7 |
| `local-client-auth.test.mjs` | 8/8 |
| `siep-11-mutation-registry.test.mjs` | 4/4 |
| `confirm-merge-scac.test.mjs` | 1/1 |
| `bigint-version-roundtrip.test.mjs` | 9/9 |

Additional offline evidence, not included again in that batch total: error-unmasking 6/6; constraint-refusal-legibility 5/5; tour-sharing 5/5; export-email-domains 7/7; system-work-census 19/19; doctrine-bigint-versions 13/13; amend-closed-loop plus deal-items 21/21. Correlation, confirm-merge-human-only, arg-type-coercion, work-request-intake, and the non-browser shared-OAuth tests also passed. The all-verb inventory/admission and required-field sweeps passed. The targeted F1/F2 probes used real registered dispatch, synthetic principals/rows, and fake clients adapted from repository test seams; they confirmed acceptance of the unauthorized target as described above.

Failures/limits were retained, not counted as passes:

- `memory-kernel.test.mjs:299`, board-answers, and work-request-ready-plan migration-source assertions hit host `spawnSync python3 EPERM` through `migration-source.testhelper.mjs:12`; the ready-plan suite passed its other 12 assertions. A standalone Python-version child reproduced the host error. `siep-18-exact-effects` failed at its top-level migration read before assertions. These are test-execution limitations, not newly established product failures.
- `find-rule-database-safety` failed both cases because its subprocess returned empty stdout and JSON parsing failed; isolation coverage from that suite is unproven here.
- `doc-oauth` failed before testing because the installed bundle dependencies lack `@typesafe-ai/sdk`, imported by `jev-deal-reading.js:1`. Worker-bundle shared-OAuth tests were not run for the same dependency limitation. No installation was attempted.
- Full `npm test` and strict CI were not run: they include database/browser/live-probe work outside the offline scope. Even `ops/ci.sh --only contract` invokes a probe that discovers a local credential path and can call the deployed Worker (`ops/ci.sh:181`; `ops/capture-verb-reachability.py:99`, `:155`). Documentation diff checks are separate from runtime assurance.

### Repository probes inspected, not executed

- `ops/nightly-verb-probe.py:20` / `:102`: requires unauthenticated MCP to return 401. Its own disclaimer at `:34` says it does not verify transport, dispatch, argument validation, or answer correctness; view checks at `:111` use a database connection. A green nightly result cannot establish this audit's full invariant set.
- `ops/atomic-rule-approval-authority-probe.py:22`, `:25`, `:62`: requires an explicitly disposable DB session and verifies Joe/Dell authority with rollback fixtures. No live execution was attempted.
- `mcp-server/test/completion-tenant-barriers-postgres.sql:1`: hidden-tenant error-disclosure test with an unsafe-view positive control and rollback. The safe view is tested at `:35`; both security-barrier options are checked at `:42`. It supports the intended invariant, not an executed result here.
- `mcp-server/test/authenticated-canonical-ownership-postgres.sql`, `notification-preferences-postgres.sql`, `doc-conversation-write-doors-postgres.sql`, `tour-client-share-allowlist-postgres.sql`, and `codex-continuity-postgres.sql` provide additional store-level negative fixtures. They were treated as contracts/evidence, not as successful runs or authorization to open a database.

## Complete registered-verb census

Every row inherits the verified HTTP authentication gate (**A**) and generated structural parity (**F**) above; these were checked for all 365 names. The columns describe each verb's actual declared write/concurrency/authority contract, not a claim that all row policies passed live tests. **I** = required idempotency key; **D** = inner delegated contract; **C** = conditional key; **audit** = explicitly keyless gate-run side effect. Flags: **H** = partner authority (`humanOnly`), **A** = separate authority connection, **P** = literal-human carve-out, **r** = writer-connection read; `—` = none. Concurrency lists declared version/digest fields; `?` means conditional/handler-enforced rather than schema-required. Row checks: **F1/F2/F4/F5/F7** refer to findings; **personal** = explicit private-scope implementation; **tenant/DB** = server tenant/context or definer contract, whose live grants/policies were not verified; **shared/DB** = shared-business or system record contract with no universal per-partner barrier. Every row is also subject to the unresolved public-error invariant F3.

### `mcp-server/src/agent-profiles.js`

| Verb | Write | Flags | Key | Concurrency field(s) | Row contract / exception | Definition/factory → handler line |
|---|---|---|---|---|---|---|
| `assign-profile` | yes | — | I | `base_version` | shared/DB | 69 → 81 |
| `read-profiles` | no | — | — | — | shared/DB | 57 → 60 |

### `mcp-server/src/assurance-health-store.v5.js`

| Verb | Write | Flags | Key | Concurrency field(s) | Row contract / exception | Definition/factory → handler line |
|---|---|---|---|---|---|---|
| `read-assurance-health` | no | r | — | — | tenant/DB | 122 → 144 |

### `mcp-server/src/board-answers.js`

| Verb | Write | Flags | Key | Concurrency field(s) | Row contract / exception | Definition/factory → handler line |
|---|---|---|---|---|---|---|
| `acknowledge-board-answer` | yes | — | I | `base_version` | personal | 317 → 324 |
| `answer-board-question` | yes | H | I | `base_version` | personal; F5 | 249 → 256 |
| `ask-board-question` | yes | — | I | `base_version` | personal | 142 → 149 |
| `list-progress-boards` | no | — | — | — | personal | 208 → 212 |
| `publish-board-snapshot` | yes | — | I | `base_version` | personal | 105 → 112 |
| `read-board-answers` | no | — | — | — | personal | 290 → 296 |
| `read-progress-board` | no | — | — | — | personal | 223 → 227 |
| `record-board-answer-applied` | yes | — | I | `base_version` | personal | 345 → 352 |
| `revise-board-question` | yes | — | I | `base_version` | personal | 172 → 179 |

### `mcp-server/src/capability-program.js`

| Verb | Write | Flags | Key | Concurrency field(s) | Row contract / exception | Definition/factory → handler line |
|---|---|---|---|---|---|---|
| `cancel-capability-session` | yes | — | I | `base_version` | shared/DB | 385 → 389 |
| `capability-program` | no | — | — | — | shared/DB | 157 → 161 |
| `complete-capability-project` | yes | — | I | `base_version` | shared/DB | 292 → 296 |

### `mcp-server/src/codex-continuity.js`

| Verb | Write | Flags | Key | Concurrency field(s) | Row contract / exception | Definition/factory → handler line |
|---|---|---|---|---|---|---|
| `codex-checkpoint` | yes | — | I | `expected_version` | personal | 361 → 369 |
| `codex-read-recovery` | no | — | — | — | personal | 423 → 431 |
| `list-my-codex-sessions` | no | — | — | — | personal | 336 → 339 |

### `mcp-server/src/cre-lifecycle-store.v5.js`

| Verb | Write | Flags | Key | Concurrency field(s) | Row contract / exception | Definition/factory → handler line |
|---|---|---|---|---|---|---|
| `cancel-pending-deal` | yes | A | I | — | tenant/DB; F5 | 1061 → 4306 |
| `commit-winning-property` | yes | A | I | — | tenant/DB; F5 | 1041 → 4306 |
| `initialize-assignment` | yes | — | I | — | tenant/DB | 1005 → 4306 |
| `initialize-property-negotiation` | yes | — | I | — | tenant/DB | 1011 → 4306 |
| `initialize-prospect-relationship` | yes | — | I | — | tenant/DB | 1000 → 4306 |
| `link-salesforce-reference` | yes | — | I | — | tenant/DB | 1071 → 4306 |
| `open-cre-assignment` | yes | — | I | — | tenant/DB | 1023 → 4306 |
| `read-cre-lifecycle` | no | r | — | — | tenant/DB | 965 → 4307 |
| `record-deal-axis` | yes | — | I | — | tenant/DB | 1066 → 4306 |
| `record-deal-closing` | yes | A | I | — | tenant/DB; F5 | 1056 → 4306 |
| `record-deal-execution` | yes | — | I | — | tenant/DB | 1046 → 4306 |
| `record-diligence-outcome` | yes | — | I | — | tenant/DB | 1051 → 4306 |
| `record-evidence-subject-link` | yes | A | I | — | tenant/DB; F5 | 988 → 4306 |
| `record-lifecycle-correction` | yes | HA | I | — | tenant/DB; F5 | 1081 → 4306 |
| `record-lifecycle-fact` | yes | — | I | — | tenant/DB | 974 → 4306 |
| `record-lifecycle-reconciliation` | yes | — | I | `subject_ref.expected_state_digest` | tenant/DB | 1091 → 4306 |
| `record-loi-acceptance` | yes | — | I | — | tenant/DB | 1033 → 4306 |
| `record-loi-submission` | yes | — | I | — | tenant/DB | 1028 → 4306 |
| `record-partner-lifecycle-fact` | yes | A | I | — | tenant/DB; F5 | 4306 → 4306 |
| `record-representation-agreement` | yes | — | I | — | tenant/DB | 1017 → 4306 |
| `run-migration-shadow` | yes | — | I | — | tenant/DB | 1098 → 4306 |

### `mcp-server/src/deal-items.js`

| Verb | Write | Flags | Key | Concurrency field(s) | Row contract / exception | Definition/factory → handler line |
|---|---|---|---|---|---|---|
| `backfill-deal-items` | yes | — | I | — | shared/DB; F4 | 117 → 117 |
| `complete-deal-item` | yes | — | I | — | shared/DB; F4 | 117 → 117 |
| `instruct-deal-item` | yes | — | I | — | shared/DB; F4 | 117 → 117 |
| `record-deal-item` | yes | — | I | — | shared/DB; F4 | 117 → 117 |

### `mcp-server/src/dispatch-spine.js`

| Verb | Write | Flags | Key | Concurrency field(s) | Row contract / exception | Definition/factory → handler line |
|---|---|---|---|---|---|---|
| `acknowledge-dispatch` | yes | — | I | — | shared/DB; F7 | 116 → 126 |
| `record-dispatch-link` | yes | — | I | — | shared/DB | 68 → 79 |

### `mcp-server/src/doc-activity.js`

| Verb | Write | Flags | Key | Concurrency field(s) | Row contract / exception | Definition/factory → handler line |
|---|---|---|---|---|---|---|
| `read-doc-activity` | no | r | — | — | personal | 69 → 69 |

### `mcp-server/src/doc-conversation.js`

| Verb | Write | Flags | Key | Concurrency field(s) | Row contract / exception | Definition/factory → handler line |
|---|---|---|---|---|---|---|
| `add-doc-conversation-turn` | yes | A | I | — | tenant/DB | 114 → 126 |
| `create-doc-conversation` | yes | — | I | — | tenant/DB | 165 → 176 |
| `list-doc-conversations` | no | r | — | — | tenant/DB | 320 → 340 |
| `read-doc-conversation` | no | r | — | — | tenant/DB | 298 → 308 |
| `rename-doc-conversation` | yes | — | I | `base_version` | tenant/DB | 252 → 264 |
| `share-doc-conversation` | yes | — | I | — | tenant/DB | 210 → 220 |

### `mcp-server/src/doc-suggestions.js`

| Verb | Write | Flags | Key | Concurrency field(s) | Row contract / exception | Definition/factory → handler line |
|---|---|---|---|---|---|---|
| `complete-doc-suggestion-scan` | yes | A | I | — | shared/DB | 12 → 19 |
| `decide-doc-suggestion` | yes | H | I | `base_version` | shared/DB | 69 → 77 |
| `list-doc-suggestions` | no | r | — | — | shared/DB | 55 → 61 |
| `propose-doc-correction` | yes | H | I | `base_version` | shared/DB | 89 → 97 |
| `suggest-doc-work` | yes | A | I | — | shared/DB | 32 → 41 |

### `mcp-server/src/doctrine.js`

| Verb | Write | Flags | Key | Concurrency field(s) | Row contract / exception | Definition/factory → handler line |
|---|---|---|---|---|---|---|
| `change-doctrine-sections` | yes | — | I | `items[].base_version` | shared read; personal writes | 767 → 780 |
| `claim-doctrine-sections` | yes | — | I | — | shared read; personal writes; F1 | 717 → 726 |
| `doctrine-index` | no | — | — | — | shared read; personal writes | 690 → 694 |
| `doctrine-sections` | no | — | — | — | shared read; personal writes | 652 → 657 |
| `dry-run-doctrine-gates` | yes | — | audit | — | shared read; personal writes | 1443 → 1453 |
| `map-architecture` | no | — | — | — | shared read; personal writes | 573 → 576 |
| `new-doctrine-doc` | yes | — | I | — | shared read; personal writes | 356 → 367 |
| `read-doctrine` | no | — | — | — | shared read; personal writes | 541 → 547 |
| `release-doctrine-claims` | yes | — | I | — | shared read; personal writes | 750 → 757 |
| `resolve-doctrine-rules` | no | — | — | — | shared read; personal writes | 871 → 877 |
| `retire-doctrine-section` | yes | — | I | `base_version` | shared read; personal writes; F1 | 514 → 523 |
| `search-doctrine` | no | — | — | — | shared read; personal writes | 678 → 685 |
| `set-doctrine-refs` | yes | — | I | `base_version` | shared read; personal writes; F1 | 466 → 480 |
| `standing-context` | no | — | — | — | shared read; personal writes | 921 → 932 |
| `write-doctrine-section` | yes | — | I | `base_version` | shared read; personal writes | 393 → 406 |

### `mcp-server/src/engineering-runtime.js`

| Verb | Write | Flags | Key | Concurrency field(s) | Row contract / exception | Definition/factory → handler line |
|---|---|---|---|---|---|---|
| `admit-engineering-slice` | yes | — | I | — | shared/DB | 2280 → 2284 |
| `engineering-passport` | no | — | — | — | shared/DB | 2292 → 2295 |
| `engineering-passport-source` | no | — | — | — | shared/DB | 2238 → 2241 |
| `engineering-progress` | no | — | — | — | shared/DB | 2297 → 2300 |
| `engineering-writer-runtime-preflight` | no | r | — | — | shared/DB | 2313 → 2318 |
| `register-engineering-slice-plan` | yes | — | I | — | shared/DB | 2255 → 2259 |
| `review-engineering-slice` | yes | — | I | — | shared/DB | 2286 → 2290 |
| `source-merge-authority` | no | — | — | — | shared/DB | 2308 → 2311 |

### `mcp-server/src/feature-switches.js`

| Verb | Write | Flags | Key | Concurrency field(s) | Row contract / exception | Definition/factory → handler line |
|---|---|---|---|---|---|---|
| `check-feature-switches` | yes | — | I | — | shared/DB | 82 → 86 |
| `flip-feature-switch` | yes | — | I | `base_version` | shared/DB | 127 → 47 |
| `list-feature-switches` | no | — | — | — | shared/DB | 134 → 137 |
| `set-feature-switch` | yes | — | I | `base_version` | shared/DB | 118 → 47 |

### `mcp-server/src/global-boundaries-door.v5.js`

| Verb | Write | Flags | Key | Concurrency field(s) | Row contract / exception | Definition/factory → handler line |
|---|---|---|---|---|---|---|
| `read-global-boundaries` | no | — | — | — | shared/DB | 762 → 770 |

### `mcp-server/src/governed-correspondence-store.v5.js`

| Verb | Write | Flags | Key | Concurrency field(s) | Row contract / exception | Definition/factory → handler line |
|---|---|---|---|---|---|---|
| `correspondence-readiness` | no | — | — | — | tenant/DB | 241 → 245 |
| `read-correspondence-thread` | no | — | — | — | tenant/DB | 273 → 285 |
| `record-correspondence-adapter-consent` | yes | H | I | — | tenant/DB | 318 → 333 |
| `revoke-correspondence-adapter-consent` | yes | H | I | — | tenant/DB | 376 → 388 |

### `mcp-server/src/incident.js`

| Verb | Write | Flags | Key | Concurrency field(s) | Row contract / exception | Definition/factory → handler line |
|---|---|---|---|---|---|---|
| `adjudicate-incident` | yes | H | I | — | shared/DB | 1298 → 1318 |
| `close-incident` | yes | H | I | — | shared/DB | 1216 → 1249 |
| `get-incident` | no | — | — | — | shared/DB | 793 → 806 |
| `incident-board` | no | — | — | — | shared/DB | 721 → 743 |
| `link-incident-work-request` | yes | — | I | — | shared/DB | 1151 → 1162 |
| `open-incident` | yes | — | I | — | shared/DB | 877 → 903 |
| `triage-incident` | yes | — | I | — | shared/DB | 1095 → 1109 |

### `mcp-server/src/investigation.js`

| Verb | Write | Flags | Key | Concurrency field(s) | Row contract / exception | Definition/factory → handler line |
|---|---|---|---|---|---|---|
| `adjudicate-investigation-branch` | yes | — | I | — | shared/DB | 407 → 416 |
| `close-investigation` | yes | — | I | — | shared/DB | 473 → 485 |
| `get-investigation` | no | — | — | — | shared/DB | 452 → 455 |
| `investigation-neighborhood` | no | — | — | — | shared/DB | 297 → 302 |
| `next-signals` | no | — | — | — | shared/DB | 146 → 153 |
| `open-investigation` | yes | — | I | — | shared/DB | 261 → 269 |
| `open-investigation-branch` | yes | — | I | — | shared/DB | 317 → 325 |
| `record-branch-evidence` | yes | — | I | — | shared/DB | 367 → 377 |
| `record-signal` | yes | — | I | — | shared/DB | 166 → 182 |

### `mcp-server/src/invoice-automation.js`

| Verb | Write | Flags | Key | Concurrency field(s) | Row contract / exception | Definition/factory → handler line |
|---|---|---|---|---|---|---|
| `invoice-close-queue` | no | — | — | — | shared/DB | 131 → 133 |
| `record-deal-invoice` | yes | — | I | — | shared/DB | 109 → 115 |
| `undo-invoice-close` | yes | H | I | `base_version` | shared/DB; F5 | 138 → 141 |

### `mcp-server/src/invoice-tracker.js`

| Verb | Write | Flags | Key | Concurrency field(s) | Row contract / exception | Definition/factory → handler line |
|---|---|---|---|---|---|---|
| `read-invoice-tracker` | no | — | — | — | shared/DB | 7 → 11 |
| `record-commission-receipt` | yes | H | I | `base_version` | shared/DB | 23 → 31 |

### `mcp-server/src/lead-automation.js`

| Verb | Write | Flags | Key | Concurrency field(s) | Row contract / exception | Definition/factory → handler line |
|---|---|---|---|---|---|---|
| `advance-leads` | yes | — | I | — | shared/DB | 223 → 226 |
| `approve-lead-draft` | yes | H | I | — | shared/DB; F5 | 245 → 248 |
| `approve-lead-move` | yes | H | I | `base_version` | shared/DB; F5 | 259 → 262 |
| `last-new-lead-search` | no | — | — | — | shared/DB | 307 → 309 |
| `lead-approval-queue` | no | — | — | — | shared/DB | 235 → 237 |
| `lead-stage-preview` | no | — | — | — | shared/DB | 216 → 218 |
| `record-lead-contact` | yes | — | I | — | shared/DB | 180 → 189 |
| `undo-lead-move` | yes | H | I | `base_version` | shared/DB; F5 | 274 → 277 |

### `mcp-server/src/lease-term-comparison.js`

| Verb | Write | Flags | Key | Concurrency field(s) | Row contract / exception | Definition/factory → handler line |
|---|---|---|---|---|---|---|
| `compare-lease-terms` | no | — | — | — | shared/DB | 229 → 255 |

### `mcp-server/src/meeting-mode.js`

| Verb | Write | Flags | Key | Concurrency field(s) | Row contract / exception | Definition/factory → handler line |
|---|---|---|---|---|---|---|
| `add-meeting-note` | yes | — | I | — | shared/DB | 333 → 239 |
| `claim-meeting-processing` | yes | — | I | — | shared/DB | 306 → 239 |
| `decide-meeting-action` | yes | — | I | — | shared/DB | 414 → 239 |
| `end-meeting` | yes | — | I | — | shared/DB | 483 → 239 |
| `propose-meeting-action` | yes | — | I | — | shared/DB | 365 → 239 |
| `read-meeting` | no | r | — | — | shared/DB | 507 → 518 |
| `record-meeting-action-outcome` | yes | — | I | — | shared/DB | 454 → 239 |
| `start-meeting` | yes | — | I | — | shared/DB | 251 → 239 |

### `mcp-server/src/memory.js`

| Verb | Write | Flags | Key | Concurrency field(s) | Row contract / exception | Definition/factory → handler line |
|---|---|---|---|---|---|---|
| `correct-memory` | yes | — | I | `base_version` | personal | 206 → 212 |
| `forget-memory` | yes | — | I | `base_version` | personal | 238 → 244 |
| `observe-memory` | yes | — | I | — | personal | 69 → 78 |
| `promote-memory` | yes | — | I | `base_version` | personal | 187 → 193 |
| `recall-memory` | no | r | — | — | personal | 134 → 144 |
| `review-memory` | no | r | — | — | shared review; tenant/DB | 164 → 171 |

### `mcp-server/src/notifications.js`

| Verb | Write | Flags | Key | Concurrency field(s) | Row contract / exception | Definition/factory → handler line |
|---|---|---|---|---|---|---|
| `acknowledge-notification` | yes | — | I | — | personal | 77 → 84 |
| `notification-feed` | no | r | — | — | personal | 111 → 118 |
| `read-notification-preferences` | no | r | — | — | personal | 132 → 136 |
| `set-notification-preference` | yes | — | I | `base_version` | personal | 145 → 158 |

### `mcp-server/src/partner-room.js`

| Verb | Write | Flags | Key | Concurrency field(s) | Row contract / exception | Definition/factory → handler line |
|---|---|---|---|---|---|---|
| `add-room-turn` | yes | — | I | — | shared/DB | 232 → 243 |
| `project-room-queue` | yes | — | I | — | shared/DB | 272 → 280 |
| `read-room` | no | — | — | — | shared/DB | 316 → 323 |
| `read-room-latest` | no | — | — | — | shared/DB | 329 → 337 |
| `read-room-queue` | no | — | — | — | shared/DB | 343 → 346 |

### `mcp-server/src/party-identity.js`

| Verb | Write | Flags | Key | Concurrency field(s) | Row contract / exception | Definition/factory → handler line |
|---|---|---|---|---|---|---|
| `correct-party-identity` | yes | — | I | `base_version` | shared/DB | 176 → 198 |

### `mcp-server/src/record-source-authority-store.v5.js`

| Verb | Write | Flags | Key | Concurrency field(s) | Row contract / exception | Definition/factory → handler line |
|---|---|---|---|---|---|---|
| `evaluate-artifact-deletion` | yes | — | I | — | tenant/DB | 418 → 2295 |
| `read-record-source-authority` | no | r | — | — | tenant/DB | 369 → 2296 |
| `record-artifact-preservation-hold` | yes | HA | I | `expected_prior_hold_digest`? | tenant/DB; F5 | 413 → 2295 |
| `record-corporate-artifact` | yes | — | I | — | tenant/DB | 385 → 2295 |
| `record-document-identity` | yes | — | I | `expected_prior_document_digest`? | tenant/DB | 407 → 2295 |
| `record-parsed-proposal` | yes | — | I | — | tenant/DB | 390 → 2295 |
| `record-source-observation` | yes | — | I | — | tenant/DB | 380 → 2295 |
| `register-derivative-source-link` | yes | — | I | — | tenant/DB | 395 → 2295 |
| `register-record-source-authority-policy` | yes | HA | I | `expected_prior_policy_digest`? | tenant/DB; F5 | 373 → 2295 |

### `mcp-server/src/research-sites.js`

| Verb | Write | Flags | Key | Concurrency field(s) | Row contract / exception | Definition/factory → handler line |
|---|---|---|---|---|---|---|
| `add-research-site` | yes | — | I | — | shared/DB | 145 → 155 |
| `list-research-sites` | no | — | — | — | shared/DB | 112 → 118 |
| `remove-research-site` | yes | — | I | — | shared/DB | 183 → 191 |

### `mcp-server/src/resource-observation.v5.js`

| Verb | Write | Flags | Key | Concurrency field(s) | Row contract / exception | Definition/factory → handler line |
|---|---|---|---|---|---|---|
| `read-resource-dashboard` | no | — | — | — | shared/DB | 37 → 41 |
| `record-resource-observation` | yes | — | I | — | shared/DB | 50 → 79 |

### `mcp-server/src/rule-context-runtime.v5.js`

| Verb | Write | Flags | Key | Concurrency field(s) | Row contract / exception | Definition/factory → handler line |
|---|---|---|---|---|---|---|
| `bind-rule-context-contract` | yes | A | I | — | shared/DB | 414 → 443 |
| `read-action-context` | no | r | — | — | shared/DB | 391 → 403 |

### `mcp-server/src/salesforce-read-run-store-rw02.v5.js`

| Verb | Write | Flags | Key | Concurrency field(s) | Row contract / exception | Definition/factory → handler line |
|---|---|---|---|---|---|---|
| `read-salesforce-autonomy-counter` | no | — | — | — | shared/DB | 232 → 238 |
| `record-salesforce-run-outcome` | yes | — | I | — | shared/DB | 219 → 229 |
| `revoke-salesforce-read-consent` | yes | H | I | — | shared/DB | 240 → 247 |

### `mcp-server/src/salesforce-reconciliation-store-rw02.v5.js`

| Verb | Write | Flags | Key | Concurrency field(s) | Row contract / exception | Definition/factory → handler line |
|---|---|---|---|---|---|---|
| `read-salesforce-action-evidence` | no | — | — | — | shared/DB | 282 → 282 |
| `record-salesforce-duplicate-check` | yes | — | I | — | shared/DB | 315 → 315 |
| `record-salesforce-page-stop` | yes | — | I | — | shared/DB | 315 → 315 |
| `record-salesforce-write-readback` | yes | — | I | — | shared/DB | 315 → 315 |

### `mcp-server/src/schedule-board.js`

| Verb | Write | Flags | Key | Concurrency field(s) | Row contract / exception | Definition/factory → handler line |
|---|---|---|---|---|---|---|
| `schedule-board` | no | — | — | — | personal | 88 → 92 |

### `mcp-server/src/session-identity.js`

| Verb | Write | Flags | Key | Concurrency field(s) | Row contract / exception | Definition/factory → handler line |
|---|---|---|---|---|---|---|
| `read-dispatch-history` | no | r | — | — | personal | 129 → 137 |
| `read-session-identity` | no | r | — | — | personal | 113 → 121 |

### `mcp-server/src/situation-retrieval.js`

| Verb | Write | Flags | Key | Concurrency field(s) | Row contract / exception | Definition/factory → handler line |
|---|---|---|---|---|---|---|
| `approve-retrieval-proposals` | yes | — | I | `base_versions` | shared/DB; F4 | 294 → 302 |
| `propose-retrieval-concept` | yes | — | I | — | shared/DB | 217 → 225 |
| `propose-retrieval-mapping` | yes | — | I | — | shared/DB | 258 → 267 |
| `propose-retrieval-phrase` | yes | — | I | — | shared/DB | 235 → 246 |
| `propose-retrieval-retirement` | yes | — | I | `base_version` | shared/DB | 278 → 286 |
| `retire-retrieval-curation` | yes | — | I | `base_version` | shared/DB; F4 | 331 → 339 |

### `mcp-server/src/system-work-census.v5.js`

| Verb | Write | Flags | Key | Concurrency field(s) | Row contract / exception | Definition/factory → handler line |
|---|---|---|---|---|---|---|
| `unfinished-work` | no | — | — | — | tenant/DB | 311 → 311 |

### `mcp-server/src/tools.js`

| Verb | Write | Flags | Key | Concurrency field(s) | Row contract / exception | Definition/factory → handler line |
|---|---|---|---|---|---|---|
| `accept-workflow` | yes | A | I | — | shared/DB | 6736 → 6743 |
| `activate-guidance-registry` | yes | A | I | — | shared/DB | 6802 → 6810 |
| `activate-rule` | yes | — | I | — | shared/DB | 6673 → 6680 |
| `add-critical-date` | yes | — | I | — | shared/DB | 4193 → 4200 |
| `add-deal-note` | yes | — | I | — | shared/DB | 9441 → 9448 |
| `add-industry-event` | yes | — | I | — | shared/DB | 8754 → 8766 |
| `add-loop` | yes | — | I | — | shared/DB | 7377 → 7401 |
| `add-party` | yes | — | I | — | shared/DB | 4501 → 4512 |
| `add-premises` | yes | — | I | — | shared/DB | 4592 → 4618 |
| `admit-rule` | yes | — | I | — | shared/DB | 6532 → 6555 |
| `amend-closed-loop` | yes | — | I | `base_version`? | shared/DB; F2 | 8033 → 8047 |
| `amend-rule` | yes | A | I | `base_version` | shared/DB | 6927 → 6939 |
| `applicable-rules` | no | — | — | — | shared/DB | 6686 → 6692 |
| `approve-rule` | yes | A | I | — | shared/DB | 6645 → 6655 |
| `attach-to-campaign` | yes | — | I | `piece_base_version`? | shared/DB | 8429 → 8443 |
| `call-verb` | yes | — | D | — | discovery/delegation | 10205 → 10219 |
| `capture-queue` | no | — | — | — | shared/DB | 3604 → 3608 |
| `catch-me-up` | no | r | — | — | shared/DB | 3192 → 3199 |
| `claim-card` | no | — | — | — | shared/DB | 3707 → 3714 |
| `claim-lead` | yes | H | I | `base_version` | shared/DB | 5251 → 5257 |
| `close-loop` | yes | — | I | `base_version`? | shared/DB; F2 | 7887 → 7900 |
| `complete-action` | yes | — | I | — | shared/DB | 4133 → 4140 |
| `confirm-merge` | yes | HP | I | — | shared/DB | 5941 → 5954 |
| `counterparty-history` | no | — | — | — | shared/DB | 3112 → 3119 |
| `create-national-account` | yes | — | I | — | shared/DB | 9631 → 9640 |
| `create-national-market-deal` | yes | — | I | — | shared/DB | 9670 → 9681 |
| `deactivate-guidance-registry` | yes | A | I | — | shared/DB | 6842 → 6850 |
| `deal-board` | no | — | — | — | shared/DB | 3485 → 3489 |
| `deal-room-board` | no | — | — | — | shared/DB | 3497 → 3504 |
| `decide-guidance-import-batch` | yes | A | I | — | shared/DB | 6822 → 6830 |
| `decline-candidate` | yes | — | I | `base_version` | shared/DB | 4877 → 4886 |
| `detach-decision` | yes | — | I | — | shared/DB | 7308 → 7317 |
| `disable-legacy-schedule` | yes | A | I | — | shared/DB | 6754 → 6764 |
| `edit-loop-header` | yes | — | I | `base_version`? | shared/DB | 7830 → 7841 |
| `end-deal-review` | yes | — | I | — | shared/DB | 9553 → 9561 |
| `export-email-domains` | no | — | — | — | shared/DB | 9967 → 9970 |
| `find` | no | — | — | — | shared/DB | 2664 → 2668 |
| `find-and-catch-up` | no | r | — | — | shared/DB | 3217 → 3226 |
| `find-precedent` | no | — | — | — | shared/DB | 10072 → 10080 |
| `find-rule` | no | — | — | — | shared/DB | 6389 → 6396 |
| `get-call-context` | no | — | — | — | shared/DB | 3640 → 3646 |
| `get-deal-room` | no | — | — | — | shared/DB | 9178 → 9181 |
| `governance-queue` | no | — | — | — | shared/DB | 6710 → 6714 |
| `integrity-digest` | no | — | — | — | shared/DB | 3754 → 3758 |
| `lead-board` | no | — | — | — | shared/DB | 3680 → 3684 |
| `lead-hot` | no | — | — | — | shared/DB | 3673 → 3677 |
| `link-lead-client` | yes | H | I | `base_version` | shared/DB | 5275 → 5282 |
| `link-parties` | yes | — | I | — | shared/DB | 5809 → 5819 |
| `list-industry-events` | no | — | — | — | shared/DB | 8789 → 8793 |
| `list-verbs` | no | — | — | — | discovery/delegation | 9942 → 9947 |
| `log-activity` | yes | — | I | — | shared/DB | 3942 → 3966 |
| `log-capture` | yes | — | I | — | shared/DB | 7051 → 7064 |
| `log-decision` | yes | — | I | — | shared/DB | 7097 → 7113 |
| `log-outreach` | yes | — | I | — | shared/DB | 4936 → 4953 |
| `loop-board` | no | — | — | — | shared/DB | 3799 → 3812 |
| `loop-headers` | no | — | — | — | shared/DB | 7810 → 7817 |
| `mail-contact-policy` | no | — | — | — | shared/DB | 3615 → 3621 |
| `measure-placement` | yes | — | I | — | shared/DB | 8585 → 8602 |
| `merge-vendor-rows` | yes | — | I | — | shared/DB | 6144 → 6152 |
| `morning-brief` | no | — | — | — | shared/DB | 3340 → 3344 |
| `new-client` | yes | — | I | — | shared/DB | 5119 → 5128 |
| `new-deal` | yes | — | I | — | shared/DB | 4339 → 4358 |
| `new-lead` | yes | — | I | — | shared/DB | 4755 → 4765 |
| `new-vendor` | yes | — | I | — | shared/DB | 5145 → 5154 |
| `open-campaign` | yes | — | I | — | shared/DB | 8195 → 8212 |
| `patch-deal-field` | yes | — | I | `base_event_id` | shared/DB | 9404 → 9414 |
| `prepare-conversation` | no | r | — | — | shared/DB | 3271 → 3284 |
| `prepare-document` | yes | — | I | — | shared/DB | 5615 → 5626 |
| `presence-lease` | yes | — | I | — | shared/DB | 9381 → 9387 |
| `promote-pool` | yes | — | I | `base_version` | shared/DB | 4804 → 4816 |
| `read-deal-reconciliation` | no | — | — | — | shared/DB | 9355 → 9359 |
| `read-doc-outcome-cards` | no | r | — | — | shared/DB | 10293 → 10297 |
| `read-home-agenda` | no | — | — | — | shared/DB | 9285 → 9288 |
| `read-loop` | no | — | — | — | shared/DB | 3761 → 3769 |
| `read-v5-a02-rule-enforcement-coverage` | no | — | — | — | shared/DB | 2657 → 2661 |
| `reassign-deal` | yes | — | I | `base_version` | shared/DB | 4422 → 4432 |
| `record-counter` | yes | — | I | — | shared/DB | 5447 → 5471 |
| `record-defect` | yes | — | I | — | shared/DB | 10009 → 10025 |
| `record-executed-lease` | yes | A | I | `base_version`? | shared/DB | 4213 → 4225 |
| `record-finding` | yes | — | I | — | shared/DB | 6236 → 6257 |
| `record-rule-enforcement-fallback` | yes | A | I | — | shared/DB | 3897 → 3911 |
| `register-template` | yes | — | I | — | shared/DB | 5561 → 5574 |
| `resolve-candidate` | yes | — | I | — | shared/DB | 9797 → 9804 |
| `resolve-conflict` | yes | — | I | — | shared/DB | 9763 → 9770 |
| `resolve-post-call-candidate` | yes | — | I | — | shared/DB; F4 | 9865 → 9872 |
| `retire-rule` | yes | A | I | — | shared/DB | 6862 → 6871 |
| `revert-deal-field` | yes | — | I | — | shared/DB | 9731 → 9737 |
| `review-deal` | yes | — | I | — | shared/DB | 9522 → 9530 |
| `score-campaign` | yes | — | I | `base_version` | shared/DB | 8325 → 8341 |
| `set-lead` | yes | — | I | `base_version` | shared/DB | 4471 → 4479 |
| `set-market-agent` | yes | — | I | — | shared/DB | 9580 → 9588 |
| `set-national-account-owner` | yes | — | I | — | shared/DB | 9610 → 9617 |
| `set-next-action` | yes | — | I | — | shared/DB | 4063 → 4071 |
| `set-next-step` | yes | — | I | — | shared/DB | 9471 → 9478 |
| `set-prospect-engagement-status` | yes | H | I | `base_version` | shared/DB | 5056 → 5064 |
| `source-attribution` | no | — | — | — | shared/DB | 3174 → 3180 |
| `stale-records` | no | — | — | — | shared/DB | 3747 → 3751 |
| `stamp-touch` | yes | — | I | — | shared/DB | 4013 → 4020 |
| `start-deal-review` | yes | — | I | — | shared/DB | 9488 → 9496 |
| `state-as-of` | no | — | — | — | shared/DB | 10159 → 10166 |
| `teach` | yes | — | I | — | shared/DB | 6423 → 6436 |
| `today-triage` | no | — | — | — | shared/DB | 3333 → 3337 |
| `triage-item` | yes | — | I | — | shared/DB | 4030 → 4040 |
| `update-deal` | yes | — | I | `base_version` | shared/DB | 4281 → 4289 |
| `update-decision` | yes | — | I | — | shared/DB | 7217 → 7232 |
| `update-document-status` | yes | — | I | — | shared/DB | 5710 → 5724 |
| `update-industry-event` | yes | — | I | `base_version` | shared/DB | 8808 → 8820 |
| `update-lead` | yes | — | I | `base_version` | shared/DB | 5299 → 5308 |
| `update-loop` | yes | — | I | `base_version`? | shared/DB; F2 | 7593 → 7623 |
| `update-party-contact` | yes | — | I | `base_version` | shared/DB | 5396 → 5410 |
| `update-vendor` | yes | — | I | `base_version` | shared/DB | 5173 → 5180 |
| `who-do-we-know` | no | — | — | — | shared/DB | 2874 → 2882 |

### `mcp-server/src/tour-artifacts.js`

| Verb | Write | Flags | Key | Concurrency field(s) | Row contract / exception | Definition/factory → handler line |
|---|---|---|---|---|---|---|
| `read-tour-pdf-render` | no | r | — | — | tenant/DB | 140 → 144 |
| `record-tour-pdf-human-review` | yes | HA | I | — | tenant/DB; F5 | 189 → 195 |
| `record-tour-pdf-render-result` | yes | A | I | — | tenant/DB | 155 → 160 |
| `request-tour-pdf-render` | yes | — | I | — | tenant/DB | 99 → 109 |

### `mcp-server/src/tour-domain.js`

| Verb | Write | Flags | Key | Concurrency field(s) | Row contract / exception | Definition/factory → handler line |
|---|---|---|---|---|---|---|
| `accept-tour-route-version` | yes | A | I | `expected_prior_route_version` | tenant/DB | 30 → 30 |
| `append-tour-cheat-sheet-revision` | yes | — | I | `expected_revision_number` | tenant/DB | 31 → 31 |
| `append-tour-route-stop` | yes | — | I | — | tenant/DB | 28 → 28 |
| `append-tour-route-stop-transition` | yes | — | I | — | tenant/DB | 29 → 29 |
| `append-tour-route-version` | yes | — | I | `expected_route_version` | tenant/DB | 26 → 26 |
| `create-tour-domain` | yes | — | I | — | tenant/DB | 25 → 25 |
| `prepare-tour-route-version` | yes | — | I | `expected_route_version` | tenant/DB | 27 → 27 |
| `restore-tour-cheat-sheet-revision` | yes | — | I | `expected_revision_number` | tenant/DB | 32 → 32 |

### `mcp-server/src/tour-map-promotion.js`

| Verb | Write | Flags | Key | Concurrency field(s) | Row contract / exception | Definition/factory → handler line |
|---|---|---|---|---|---|---|
| `record-tour-map-promotion-receipt` | yes | HA | I | — | tenant/DB; F5 | 85 → 108 |

### `mcp-server/src/tour-property-jurisdiction.js`

| Verb | Write | Flags | Key | Concurrency field(s) | Row contract / exception | Definition/factory → handler line |
|---|---|---|---|---|---|---|
| `append-tour-coordinate-candidate` | yes | — | I | — | tenant/DB | 217 → 225 |
| `append-tour-entrance-verification-receipt` | yes | A | I | — | tenant/DB; F5 | 261 → 269 |
| `append-tour-property-identifier-assertion` | yes | A | I | — | tenant/DB | 129 → 138 |
| `register-tour-property` | yes | A | I | — | tenant/DB | 172 → 181 |

### `mcp-server/src/tour-property-search.js`

| Verb | Write | Flags | Key | Concurrency field(s) | Row contract / exception | Definition/factory → handler line |
|---|---|---|---|---|---|---|
| `append-tour-selection-cart-version` | yes | — | I | `expected_selection_version` | tenant/DB | 191 → 199 |
| `read-tour-selection-cart` | no | r | — | — | tenant/DB | 224 → 228 |
| `search-tour-properties` | no | r | — | — | tenant/DB | 146 → 163 |

### `mcp-server/src/tour-rights-projection.js`

| Verb | Write | Flags | Key | Concurrency field(s) | Row contract / exception | Definition/factory → handler line |
|---|---|---|---|---|---|---|
| `append-tour-field-assertion` | yes | A | I | — | tenant/DB | 493 → 503 |
| `append-tour-rights-receipt` | yes | A | I | — | tenant/DB | 408 → 421 |
| `append-tour-source-evidence` | yes | — | I | — | tenant/DB | 467 → 475 |
| `create-tour-public-projection-draft` | yes | — | I | — | tenant/DB | 522 → 526 |
| `read-tour-public-projection` | no | r | — | — | tenant/DB | 576 → 580 |
| `revoke-tour-rights-receipt` | yes | A | I | — | tenant/DB | 440 → 445 |
| `seal-tour-public-projection` | yes | A | I | — | tenant/DB | 549 → 554 |

### `mcp-server/src/tour-sharing.js`

| Verb | Write | Flags | Key | Concurrency field(s) | Row contract / exception | Definition/factory → handler line |
|---|---|---|---|---|---|---|
| `issue-tour-share-grant` | yes | A | I | — | tenant/DB | 95 → 95 |
| `read-tour-feedback` | no | r | — | — | tenant/DB | 95 → 95 |
| `read-tour-sharing-library` | no | r | — | — | tenant/DB | 95 → 95 |
| `revoke-tour-share-grant` | yes | A | I | — | tenant/DB | 95 → 95 |
| `rotate-tour-share-grant` | yes | A | I | — | tenant/DB | 95 → 95 |

### `mcp-server/src/whats-new.js`

| Verb | Write | Flags | Key | Concurrency field(s) | Row contract / exception | Definition/factory → handler line |
|---|---|---|---|---|---|---|
| `whats-new` | yes | — | C | — | shared/DB | 10 → 16 |

### `mcp-server/src/work-portfolio.js`

| Verb | Write | Flags | Key | Concurrency field(s) | Row contract / exception | Definition/factory → handler line |
|---|---|---|---|---|---|---|
| `accept-portfolio-revision` | yes | HA | I | — | tenant/DB | 940 → 951 |
| `propose-portfolio-revision` | yes | — | I | — | tenant/DB | 852 → 871 |
| `read-portfolio` | no | — | — | — | tenant/DB | 837 → 844 |
| `review-portfolio-revision` | yes | — | I | — | tenant/DB | 912 → 924 |

### `mcp-server/src/work-request-intake.js`

| Verb | Write | Flags | Key | Concurrency field(s) | Row contract / exception | Definition/factory → handler line |
|---|---|---|---|---|---|---|
| `accept-outcome-feedback` | yes | HA | I | `base_version` | tenant/DB | 1125 → 1133 |
| `accept-ready-plan` | yes | HA | I | `base_version` | tenant/DB | 1083 → 1087 |
| `accept-ready-plan-amendment` | yes | HA | I | `base_version` | tenant/DB | 991 → 998 |
| `acknowledge-ready-plan-amendment` | yes | — | I | — | tenant/DB | 1053 → 1059 |
| `answer-work-request-for-joe` | yes | HA | I | `base_version` | tenant/DB; F5 | 713 → 726 |
| `bind-work-request-criterion-evidence` | yes | — | I | — | tenant/DB | 1153 → 1166 |
| `current-work-item` | no | — | — | — | tenant/DB | 441 → 451 |
| `current-work-requests` | no | — | — | — | tenant/DB | 424 → 428 |
| `decline-work-request` | yes | — | I | `base_version` | tenant/DB | 815 → 824 |
| `effective-ready-plan` | no | r | — | — | tenant/DB | 1018 → 1022 |
| `propose-outcome-feedback` | yes | — | I | `base_version` | tenant/DB | 1089 → 1104 |
| `propose-ready-plan` | yes | — | I | `base_version` | tenant/DB | 914 → 936 |
| `propose-ready-plan-amendment` | yes | — | I | `base_version` | tenant/DB | 958 → 968 |
| `ready-plan-amendment-discovery` | no | r | — | — | tenant/DB | 1033 → 1039 |
| `report-problem` | yes | — | I | — | tenant/DB | 555 → 566 |
| `review-and-triage` | yes | HA | I | `base_version` | tenant/DB | 682 → 691 |
| `supersede-work-request` | yes | — | I | `base_version` | tenant/DB | 866 → 876 |
| `work-request-card` | no | — | — | — | tenant/DB | 596 → 602 |

### `mcp-server/src/work-shape.js`

| Verb | Write | Flags | Key | Concurrency field(s) | Row contract / exception | Definition/factory → handler line |
|---|---|---|---|---|---|---|
| `read-work-shape` | no | — | — | — | shared/DB | 161 → 168 |
| `set-work-shape-disposition` | yes | — | I | `base_version` | shared/DB | 185 → 196 |
| `write-work-shape` | yes | — | I | `base_version` | shared/DB | 227 → 238 |

### `mcp-server/src/workflow-cutover.v5.js`

| Verb | Write | Flags | Key | Concurrency field(s) | Row contract / exception | Definition/factory → handler line |
|---|---|---|---|---|---|---|
| `advance-workflow-cutover-stage` | yes | A | I | — | shared/DB | 226 → 240 |
| `bind-slice-criterion-evidence` | yes | — | I | — | shared/DB | 422 → 426 |
| `cancel-workflow-cutover-plan` | yes | A | I | — | shared/DB | 199 → 211 |
| `confirm-slice-completions` | yes | A | I | — | shared/DB | 596 → 612 |
| `list-shipped-releases` | no | — | — | — | shared/DB | 479 → 486 |
| `mark-slice-completion` | yes | A | I | — | shared/DB | 537 → 560 |
| `mark-slice-progress` | yes | — | I | — | shared/DB | 496 → 520 |
| `open-workflow-cutover-plan` | yes | A | I | — | shared/DB | 170 → 183 |
| `pending-slice-completion-proposals` | no | — | — | — | shared/DB | 639 → 643 |
| `propose-slice-completion` | yes | — | I | — | shared/DB | 576 → 580 |
| `read-slice-completion` | no | — | — | — | shared/DB | 681 → 689 |
| `rebind-slice-criterion-evidence` | yes | A | I | — | shared/DB | 430 → 434 |
| `record-release-slice-members` | yes | — | I | — | shared/DB | 438 → 463 |
| `record-workflow-caller` | yes | — | I | — | shared/DB | 287 → 304 |
| `register-slice-checkable-done` | yes | A | I | — | shared/DB | 349 → 378 |
| `register-slice-criteria-from-catalog` | yes | — | I | — | shared/DB | 396 → 407 |
| `retire-workflow-cutover-plan` | yes | A | I | — | shared/DB | 257 → 270 |
| `set-slice-mark-hold` | yes | A | I | — | shared/DB | 651 → 665 |
| `workflow-cutover-board` | no | — | — | — | shared/DB | 321 → 332 |

## Report-only design and debt pass

- **codebase-design:** reviewed the report and proposed fixes against the authenticated dispatcher, private-record resolver, and public-error interfaces. Recommendations place shared policy in those existing seams; this PR adds no interfaces or implementation.
- **zero-tech-debt:** the intended end state is one evidence-backed audit file. Recommendations replace contradictory checks with one explicit policy rather than adding compatibility exceptions; no runtime cleanup or speculative refactor is included.

The deliverable remains a draft audit. All behavioral fixes above are recommendations; none is implemented or deployed by this change.
