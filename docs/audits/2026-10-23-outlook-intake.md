# Outlook mail, calendar intake, and sending audit — 2026-10-23

Report only. No application, deployment, scheduler, or production changes were made. Publication as a **draft PR**, not merge or deployment, is the authorized delivery boundary.

## Assessment

The intake has useful protections, but a successful run does not establish complete mail/calendar capture. Matching completion, source identity, and preview repair can silently omit records or obligations. Retrying a frozen request is safe under the same server identity; the surrounding discovery and send workflows do not provide exactly-once processing. The send entry point defaults to a draft, but a caller can request dispatch without presenting Joe's per-message approval.

The supplied `unmatched=13` and `writes_failed=3` cannot identify thirteen particular missed emails or three particular missing records. Their counters have narrower—and sometimes misleading—meanings. This audit establishes reproducible mechanisms and the missing diagnostic evidence, not an invented explanation of those historical items.

### Scope, assumptions, and limits

- Read `AGENTS.md` before repository inspection. Joe's explicit Markdown/report-only/no-production/draft-PR instructions take precedence over the repository's record-layer writing, live-doctrine lookup, and merge workflow rules.
- Source audit began at `dca1863a78a43eeed47cdece6b1875be3b8741f8`; the report branch was advanced to observed GitHub main [`c53b791f15d07b4e2c64dfd4aac10f3b03277161`](https://github.com/DoctorCRE/carr-system/tree/c53b791f15d07b4e2c64dfd4aac10f3b03277161). The cited Outlook implementations, tests, decoder patch, record verbs, and scheduling files were unchanged between these revisions. **All file:line evidence below refers to this latter revision.** Line numbers identify the relevant start of a function or branch, with nearby lines supplying context.
- The date is Joe's requested report date. Schedules below use America/Chicago where the implementation does. Estimates are engineering days for one developer, including regression tests, excluding rollout or unresolved decoder-format research.
- Recent status values are user-supplied observations. No live launchd configuration, actual Outlook mailbox, state/report/spool contents, installed decoder binary, contact exports, or production receipts were read. Consequently, installed-version drift and the exact historical causes remain unverified.
- No live CARR or production services were called. No email/invitation was sent; no UI automation, installer, or scheduled job was launched against a real account. No `.env`, credential/token/key/keychain files, DB dumps, or environment values were opened or printed. No `env`, `printenv`, or `ps eww` inspection was run. Test databases were newly generated synthetic fixtures only.
- This repository is public. This report contains source findings and synthetic examples, not client names, addresses, message content, or operational payloads.

## Execution path traced

| Stage | Source and behavior |
|---|---|
| Install and schedule | `bin/install-outlook-intake.sh:34` enforces machine placement, pins the decoder and patch, builds the dedicated copier with fixed paths, and renders the plist. `ops/launchd/com.carr.outlook-intake.plist:16` invokes the helper with `--run-intake`; `:24` schedules 07:00–19:00 every 30 minutes. Runtime additionally permits 07:00–19:30 Chicago time (`lib/outlook_intake.py:69`, `:186`). |
| Process wrapper | `tools/outlook-intake-copy.c:159` starts fixed `bin/run-scheduled.sh outlook-intake outlook.intake … lib.outlook_intake.main`. `bin/run-scheduled.sh:76` executes it; `:115` submits operational status through `tools/ops-spool.py`. |
| Snapshot and decode | `lib/outlook_intake.py:236` creates a private working directory, calls the fixed-scope copier, runs the pinned hxprobe decoder, loads decoded SQLite tables, and cleans up. `:674` and `:676` reject low verification or calendar identities with zero decoded events. Decoder body/identity/calendar behavior is in `vendor-patches/hxprobe-calendar-and-bodies.patch`. |
| Contact matching | `lib/outlook_intake.py:736` calls `tools/calendar-touch-matcher.py:61`, which uses `exporters.common.connect` to read the canonical client, lead, and vendor export views. This read dependency is separate from the Worker write path. `Matcher` (`lib/outlook_intake.py:273`) requires a unique exact address to a supported ref. |
| Business writes | `lib/outlook_intake.py:350` journals requests, then `:717` calls `run.sh call` → `tools/call-verb.py:152` → `mcp-server/local-verb.mjs:160` → deployed Worker verbs. Reviewed `log-activity`, `set-next-action`, `catch-me-up`, replay envelope, and transaction handling by source only. |
| Next step | `lib/outlook_intake.py:470` uses one isolated Haiku call (`:302`, `:323`), then conditionally sets an action. No model call was executed during this audit. |
| Full-body repair | Intake writes `needs-refresh.json` (`lib/outlook_intake.py:579`); `lib/outlook_triage.py:407` reads it and opens eligible already-read messages to hydrate bodies. Intake later appends a full-text note. |
| Send and calendar creation | `run.sh:149` and `:150` route to `lib.outlook_send`; it uses `lib/outlook_ui`, a private ledger, contact suppression, form readback, dispatch, mailbox confirmation, and CARR logging. Also reviewed the older `bin/outlook-draft.py`/AppleScript path and Call Mode's draft-only caller (`tools/dictation-rig/bin/post_call.py:917`). |

## What the reported statuses actually mean

| Observation | Meaning and diagnosis |
|---|---|
| `unmatched=13` | Sum of email party count minus **unique matched refs** (`lib/outlook_intake.py:444`), not thirteen emails. Duplicate recipients or aliases to one ref inflate it; calendar misses are omitted from the number. Exact mapping can fail because the single exported Email is missing/different, an address maps to multiple refs, the ref is unsupported, or the row is excluded. Names alone intentionally do not match. See F2/F9. |
| `writes_failed=3` | Three write attempts failed to return `ok: true` within that run (`:360`). A timeout may occur after a server commit, so this does not prove three absent records. Transport, process, auth, schema, subject resolution, key reuse, and database errors lose their distinction. Oversized argv is a concrete local failure mechanism. See F9/F11. |
| `preview_only` | A matched message was logged using available preview text and listed for hydration (`:451`, `:468`). It is not itself a failed write. The model also consumes that preview, and the repair loop has multiple gaps (F4). |
| `steps_pending` | An activity acknowledgement is still missing, or a queued step has no saved reply and its source is absent from the current decode (`:477`, `:480`). Any remaining step also makes the run fail even when the numerical counter is zero (`:705`). See F10. |
| `carr_unreachable` | A contact read or read-side CARR operation failed (`:654`, `:698`). Empty/unusable contact exports and setup/schema failures can produce it; it is not proof of a network outage (`:736`). Write failures are usually caught separately as `writes_failed`. |

## Findings ranked by impact on Joe's daily use

Rank reflects external-send consequence first, then silent history/action errors, recovery blockers, and operational/privacy debt. **High** means material loss, unintended external action, or wrong business state; **medium** means a significant recovery, diagnosis, or exposure gap. Every finding is source-confirmed; historical occurrence is unproven unless explicitly identified as an offline reproduction.

| Rank | Finding | Impact | Delivery slice |
|---|---|---|---|
| 1 | F1 — The send flag is not Joe's per-message approval | High | E1 |
| 2 | F2 — Partial mail matches and unmatched calendar items are marked complete | High | B1 |
| 3 | F3 — Source identity can both merge distinct emails and duplicate one email | High | B2 |
| 4 | F4 — Preview repair can leave the full body and real obligation undiscovered | High | C |
| 5 | F5 — A truncated timeline can let automation replace Joe's open action | High | D |
| 6 | F6 — Late-synced mail can fall behind the discovery cutoff permanently | High | B3 |
| 7 | F7 — Delivered sends cannot reliably repair failed CRM logging; future event logging is refused | High | E2 |
| 8 | F8 — Send retry latches strand unsent work yet permit aged ambiguous resends | Medium/high | E2 |
| 9 | F9 — Counters and run receipts hide the repairable cause | Medium | A |
| 10 | F10 — One disappeared source can hold `steps_pending` and the watermark indefinitely | Medium/high | B3 |
| 11 | F11 — Client content reaches process arguments and legacy draft output | Medium privacy; also write failures | A, F |
| 12 | F12 — Private operational state becomes a second plaintext mail archive | Medium | F |
| 13 | F13 — Calendar lifecycle and attendance are not established by decoded rows | Medium/high | G |

## Ready-to-ship slices and detailed findings

Each slice is a proposed independently reviewable change, **not implemented by this report**. Ship A promptly for diagnosis and the small transport fix. E1 addresses the highest-consequence boundary. B2 supplies identity needed by B3 and reliable cross-path reconciliation. Other slices can proceed independently with the dependencies noted.

### A — Make failures diagnosable and remove intake payloads from argv

**F9 — The reported counters do not tell Joe what to repair.**

- **Evidence:** `lib/outlook_intake.py:444` subtracts unique refs from party count. `:547` records calendar misses only in a private set; `:574` reduces unmatched detail to name/domain. `_contacts` (`:736`) reads only one Email field per row; `Matcher` (`:273`) refuses ambiguous mappings. `_carr` (`:717`) discards structured failure details; `write` (`:360`) retains only a counter. `bin/run-scheduled.sh:82` maps failure to `exit_1`, and `:115` sends generic exit summaries, despite the count-carrying description in `ops/config/services.json:111`.
- **Consequence:** the same dashboard symptom can mean missing mapping, ambiguous identity, oversized payload, authentication/setup failure, permanent record refusal, or outage. Repeating it cannot distinguish a temporary problem from one that will never heal. A synthetic email listing the same known recipient twice reproduced `unmatched=1`.
- **Fix:** separate unresolved messages, distinct unresolved addresses, and unresolved calendar occurrences; record missing/ambiguous/unsupported mapping reasons. Persist allowlisted error codes, verb, opaque source/request identity, attempt count, and retry time. Propagate a structured reason/count receipt to monitoring. Keep client diagnostics private and exclude raw stderr and bodies. Use transient backoff and explicit disposition for persistent invalid requests.
- **Effort / acceptance:** **1–2 days**. Tests must distinguish timeout, auth refusal, key reuse, oversized transport, and no acknowledgement, while keeping synthetic client sentinels out of logs. Missing versus ambiguous mappings and calendar counts must be independently visible.

**F11a — Intake passes full client payloads through process arguments.**

- **Evidence:** email/full-body/calendar details are assembled at `lib/outlook_intake.py:458`, `:513`, and `:554`; `_carr` serializes them into argv at `:720`; `tools/call-verb.py:166` forwards them to Node. `mcp-server/local-verb.mjs:150` already supports `-` to read JSON from stdin. An offline subprocess stub verified body text was in argv and no stdin was supplied.
- **Consequence:** local process inspection or command-capture diagnostics can observe client content. Large bodies can exceed OS argument limits and raise `OSError`/`E2BIG`, which intake labels unreachable. This is a mechanism, not proof it caused the reported three failures or that anyone captured the content.
- **Fix:** use the existing `run.sh call <verb> -` stdin route, as `lib/outlook_send.py:797` already does. Do not log raw request/response errors.
- **Effort / acceptance:** **0.5 day**, included in A's **1–2 day** total. Verify large synthetic bodies and assert payload sentinels never occur in subprocess argv or diagnostic output.

### B1 — Recover missing participants without weakening identity matching

**F2 — Completion is per source, even when some or all external parties remain unmatched.**

- **Evidence:** `lib/outlook_intake.py:438` collects matched refs, but `:445` queues only wholly unmatched mail; `:464` marks the whole message done after writes for any matched refs. Calendar `:543` builds refs, then `:554`/`:562` uses `all(...)`; an empty list succeeds and records the source as logged/balled at `:558`/`:565`. Contact-map reevaluation is triggered only by `mail_unresolved` (`:654`).
- **Consequence:** adding the missing address later repairs wholly unmatched mail, but not the other recipient of a partly matched outbound email or an already-completed unmatched calendar occurrence. Synthetic reproductions showed only the first mail recipient ever received activity, and an unknown past meeting still produced zero writes after a contact was added and the store changed.
- **Fix:** track matched completion and unresolved participants per canonical source and participant/ref. Empty external match sets must remain unresolved. Recheck both partial mail and calendar participants on mapping changes, including outside the rolling horizon. Preserve exact-address/unique-ref matching; do not auto-link by name.
- **Effort / acceptance:** **2–3 days**. Cover partial matches, zero calendar matches, alias ambiguity, changed mappings without changed store, and mixed successful/failed participant writes. Coordinate the persisted source key with B2.

### B2 — Give each logical source one durable identity across retries and send/intake

**F3 — Content fingerprints and mutable timestamps are not reliable event identities.**

- **Evidence:** `vendor-patches/hxprobe-calendar-and-bodies.patch:130` derives copy keys from sender, subject, and a short preview prefix; short previews can lack a key. `lib/outlook_intake.py:147` falls back to sender+subject and adds send time to the intake ID. `:257` collapses same-key copies inside 24 hours. `:418` pins historical identity only for wholly unmatched mail; recovery replays a request at `:684` before reprocessing source rows. Separately, `lib/outlook_send.py:458` keys activity by `outlook-send|caller-key`, while intake uses decoded mail/event identity (`lib/outlook_intake.py:458`, `:529`). Server `mcp-server/src/tools.js:4000` inserts activities; its replay lookup is by idempotency key (`:334`), not source content.
- **Consequence:** distinct short replies such as synthetic “yes” and “no” under the same subject collapsed to one activity. A failed write followed by a copy timestamp five seconds earlier produced three attempts with **two different keys for the same email**, allowing two committed activities. Direct-send logging and later intake also have no shared deduplication identity, so both can record the same business touch. This is separate from a safe same-key replay.
- **Fix:** obtain a stable provider/store message or event identifier; persist its canonical binding before any write and reuse it for every copy, match state, and retry. Treat content hashes as duplicate candidates, not authority. Reconcile direct-send confirmation to the same source identity used by intake, or nominate one owner of canonical activity logging. Successful retry acknowledgement must update source completion.
- **Effort / acceptance:** **3–5 days** including decoder identity work. Require short-reply/template/recipient-change tests, earlier/later copy timestamps, lost acknowledgements, and one activity after direct-send plus intake. Do not claim exactly-once semantics until these cases pass.

### B3 — Separate durable discovery from delayed writes and model work

**F6 — Send-time cutoffs lose late-arriving source items.**

- **Evidence:** first-run backfill is 48 hours and subsequent overlap six hours (`lib/outlook_intake.py:77`); `:679` derives the cutoff from the prior watermark; `:426` ignores older mail unless already unresolved. A newly appearing, known email sent seven hours before the watermark reproduced zero examined emails and zero writes.
- **Consequence:** delayed Outlook sync/hydration can make legitimate correspondence invisible even when the store changes. With no watermark after first-run failures or state reset, recovery only looks back 48 hours from recovery time. This differs from an ordinary outage with an intact watermark, which retains the older cutoff.
- **Fix:** discover unseen stable source IDs against a durable processed/discovered index. Use time only for an explicit bootstrap/backfill policy, and expose coverage gaps. Implement bounded backfill without depending on the source's send timestamp as proof it was previously seen.
- **Effort / acceptance:** **2–4 days**, sharing work with F10 and depending on B2. Test late sync beyond six hours, prolonged first-run failure, and state recovery.

**F10 — Missing source text can keep every run failed indefinitely.**

- **Evidence:** `lib/outlook_intake.py:480` requires a source in the current decode when a step has no saved model reply, despite the exact write payload having been persisted at `:352`. `:705` prevents watermark advancement while any step remains. The existing offline test explicitly covers the missing-source interruption (`ops/outlook-intake-selftest.py:658`).
- **Consequence:** an activity may be safely recorded, yet every future run remains `steps_pending` and rescans the old window. The queue has no retry age, bounded unresolved disposition, or independent completion cursor.
- **Fix:** retain a minimal durable content/provenance reference for pending model work; recover from the pending payload where appropriate. Track discovered source, activity delivery, and model/action work separately. Advance discovery after durable capture, with failures still visible and individually retryable. Add reasoned dead-letter/disposition handling without silently discarding obligations.
- **Effort / acceptance:** **2–3 days** alone, **3–5 days total for B3** with F6. A disappeared source must not block new intake; its unresolved action must remain visible and recoverable. Account for F12's retention limits.

### C — Complete preview hydration on the intake host, then infer obligations

**F4 — Preview repair is disconnected from both deployment and next-step completion.**

- **Evidence:** intake runs on primary and triage on secondary (`ops/config/services.json:88`, `:108`). Intake writes local `needs-refresh.json` (`lib/outlook_intake.py:579`); triage reads its own local path (`lib/outlook_triage.py:53`, `:407`), treating missing files as empty (`:287`). `out/` is Git-ignored (`.gitignore:5`). No transfer mechanism was found among relevant source references. Intake's unchanged-store test uses only HxStore mtime/size (`lib/outlook_intake.py:650`); pending bodies call `full_text([])` without decoding (`:663`). Preview text is sent to next-step inference (`:470`); a null/successful answer deletes the step (`:494`). Full-text arrival only adds a note (`:499`), and missing bodies expire seven days after original send time (`:509`).
- **Consequence:** the configured repair path can appear successful while refreshing nothing on the intake host. Even when fuller text arrives, it may never be decoded or used to correct an obligation inferred from the preview. Synthetic fixtures reproduced unchanged-store/no-decode after body hydration, and later full-text logging with no second model review or action.
- **Fix:** deploy a dedicated refresh worker on the same host/account/profile as intake, or implement a private explicit cross-host queue and a body-decoded acknowledgement. Revisit pending bodies on a bounded cadence or include sidecar freshness. Defer authoritative next-step inference until full text, or mark preview-derived proposals provisional and reevaluate without overwriting subsequent human decisions. Expired hydration must become visible unresolved work.
- **Effort / acceptance:** **3–4 days** for same-host hydration and inference repair; split-host transport needs separate sizing. Test actual handoff configuration, sidecar-only change, expired bodies, preview-null/full-body-action, and intervening human actions. Native Mac validation remains required before rollout.

### D — Protect Joe's current action using canonical state

**F5 — The newest 30 timeline rows are not the current-action record.**

- **Evidence:** `lib/outlook_intake.py:372` reconstructs action status from `catch-me-up(limit=30)` and Joe/joe-local summary text. `mcp-server/src/tools.js:3201` enforces the timeline limit. When no action is seen, intake writes (`lib/outlook_intake.py:397`); the server drops that owner's existing ordinary open action before inserting its replacement (`mcp-server/src/tools.js:4096`). Intake omits `owner` (`lib/outlook_intake.py:403`), so the server defaults to the runtime caller (`mcp-server/src/tools.js:4078`). Pending-action retry uses the same incomplete read (`lib/outlook_intake.py:687`).
- **Consequence:** thirty newer timeline entries can hide a still-open action. When the runtime caller owns Joe's action, an upcoming meeting's “only if none” check can replace that obligation. If another identity runs the job, its new action instead defaults to that caller, despite checking Joe's timeline. The deployed identity was not inspected. Read-then-write is also unprotected from an intervening change.
- **Fix:** read canonical current open actions, with explicit owner and version, and make replacement conditional on that version. Do not derive business state from a bounded display timeline. Ensure automation explicitly targets Joe's intended owner identity.
- **Effort / acceptance:** **1–2 days**. Seed more than 30 newer timeline rows plus an open action; verify it survives. Add an intervening-human-write conflict test. This conclusion is source-derived; no live action was inspected.

### E1 — Bind external dispatch to Joe's approval of that message

**F1 — A caller-controlled boolean can authorize a send or invitation.**

- **Evidence:** `lib/outlook_send.py:823` accepts `send_requested`; CLI `:858`/`:868` supplies `--send`. `_write` returns a draft when false (`:330`) and reaches `form.press()` when true (`:337`). Suppression checks (`:365`) do not validate Joe's approval; the server documents suppression as not permission to send (`mcp-server/src/tools.js:3615`). A synthetic Harness invocation with only the true boolean returned `done` with one mock press and no approval artifact.
- **Consequence:** **yes, a send can happen without evidence of Joe's per-message yes at this entry point**, if an authorized runtime caller supplies the flag and the other checks pass. This is an enforcement gap, not evidence that an unauthorized real send occurred. Agent instructions or context-specific hooks do not make the callable runtime verify approval.
- **Fix:** keep draft default. Require a trusted one-use approval receipt tied to the exact account, To/Cc/Bcc roles, subject/body, or complete invitation fields, with expiry. Verify the binding immediately before dispatch; the sending caller must not be able to mint its own approval. Any edited field must invalidate it.
- **Effort / acceptance:** **2–4 days**. Flag-only, missing, expired, replayed, and content-modified approval cases must never press Send/Save. Preserve suppression, idle, account, and exact-field checks.

### E2 — Recover delivery and activity logging as separate obligations

**F7 — Confirmed delivery can leave permanent missing history, and future calendar logging has a deterministic refusal.**

- **Evidence:** `lib/outlook_send.py:361` latches delivery before `_write_transport` logs activity (`:293`). Same-key retry stops at `:300`, never repairing logs. `_log_matches` can return lookup/matching failures (`:438`); only `log_failed` changes DONE to activity-log-failed (`:295`). Event logging uses `kind=meeting` and the event's future start (`:455`), while `log-activity` rejects occurrence more than five minutes ahead (`mcp-server/src/tools.js:3984`).
- **Consequence:** an outage after delivery yields missing CRM history which a retry cannot repair. A newly saved future client meeting can be successful in Outlook but refused as a completed future touch. Intake might later record it, but this is not a guaranteed repair queue. Synthetic delivery/log failure followed by restored CARR returned `duplicate_key` with zero repair writes.
- **Fix:** journal delivery, source identity, per-record matching, and log acknowledgement separately; allow log-only repair with frozen arguments/key and zero UI dispatch. Represent future scheduling as an obligation/scheduling event, and log a contact touch only when supported. Reconcile with B2 so send and intake do not independently create duplicate activities.
- **Effort / acceptance:** **1–2 days** for recovery mechanics, plus B2 integration. Test delivery success followed by logging/lookup failure, later repair without a second press, and creation of a future meeting without an invalid future touch.

**F8 — The retry latch is conservative in one direction and time-limited in another.**

- **Evidence:** `lib/outlook_send.py:337` persists `pressing` before dispatch. `:341` can then return `not_idle`; `Window.press` may know background dispatch was refused before discovering renewed user activity (`:576`). Any state for the same key blocks forever (`:301`). A new mail key is checked against only a 24-hour fingerprint window (`:36`, `:301`); the fingerprint omits body and To/Cc/Bcc roles (`:206`). Mailbox matching also uses subject and any overlapping recipient (`:246`).
- **Consequence:** a known-unsent operation can become permanently blocked and consume the daily cap. Legitimate same-subject follow-ups can be refused. An ambiguous mail can be pressed again with a new key after 24 hours. Offline probes reproduced `not_idle → duplicate_key` with zero presses, and ambiguous delivery followed by a second press after 86,401 seconds under another key.
- **Fix:** distinguish proven pre-dispatch abort, uncertain dispatch, confirmed delivery, and logging pending. Release only reservations proven not dispatched. Reconcile uncertain delivery instead of making it retryable by elapsed time/new key. Separate operation identity from coarse duplicate warnings; use precise approval/content binding from E1.
- **Effort / acceptance:** **2–3 days**, **3–4 days total for E2** with F7, excluding B2. Exercise aborted-before-send recovery, ambiguous dispatch after more than a day, valid same-thread follow-up, and log-only retry. Native mailbox confirmation remains heuristic until a stable message identifier is available.

### F — Minimize operational client content and finish safe UI transport

**F11b — Downstream UI transport and the older draft helper expose content too.**

- **Evidence:** `lib/outlook_send.py:491`/`:520` builds and passes a content-bearing `mailto:` URL to `open`. Field filling (`:627`, `:677`) reaches `lib/outlook_ui.py:149`, which places JSON values in `cua-driver` argv. `bin/outlook-draft.py:95`, `:104`, `:110` passes content downstream and prints the subject; `bin/outlook-draft.applescript:43` can echo a subject in errors.
- **Consequence:** safe input file/stdin handling at the first entry point does not keep client content out of downstream process arguments or caller logs. Actual capture by telemetry was not tested or asserted.
- **Fix:** use stdin/private IPC end-to-end for UI payloads. If the installed driver lacks a safe channel, add a narrow adapter. Replace content-bearing success/error messages with opaque operation IDs and safe statuses.
- **Effort / acceptance:** **1–2 days** if supported by the driver, otherwise **3–4 days**. Synthetic sentinels must be absent from every subprocess argv and stdout/stderr path, including refusal and exceptions.

**F12 — Private files retain more content for longer than retry needs.**

- **Evidence:** `lib/outlook_intake.py:352` persists full request JSON before writes. `:597` sets an approximately eleven-day age threshold for acknowledged requests and exempts unacknowledged requests from expiry. Pruning is reached only after successful processing (`:705`); even successful unchanged-store runs return earlier (`:666`). Reports contain names/domains/refs and sometimes sender addresses (`:448`, `:569`) and have no deletion path in this module. `state.json`, reports, and refresh lists are under `out/outlook-intake`.
- **Consequence:** even acknowledged bodies form a second plaintext mail archive; failures or an unchanged store can extend retention indefinitely. Eleven days is a pruning threshold, not a retention guarantee. Private permissions reduce exposure but do not restrict same-user software or backups. This is a demonstrated minimization/retention gap, not a finding that an uninspected storage policy was violated or that data was exfiltrated.
- **Fix:** compact acknowledged requests immediately to minimal replay receipts; retain exact pending payloads only where retry requires them, with explicit protected retention and unresolved disposition. Prune reports independently of CARR success. Prefer opaque IDs over names and addresses where they add no repair value. Coordinate model-work content references with B3.
- **Effort / acceptance:** **1 day**. Verify success removes body content, failed requests remain recoverable, expiry is visible, and repeated CARR failures do not prevent housekeeping. F totals **2–3 days** with supported safe UI transport.

### G — Establish current calendar lifecycle and distinguish schedule from attendance

**F13 — Decoded calendar rows do not prove current occurrence state or a completed meeting.**

- **Evidence:** decoded event fields lack cancellation/deletion/revision state (`vendor-patches/hxprobe-calendar-and-bodies.patch:377`); collection groups parsed GOID/start occurrences (`:399`), skips attendee status bytes (`:474`), and combines revisions using maximum end, longest body, and largest attendee list (`:560`). Intake identifies GOID/start (`lib/outlook_intake.py:529`) and logs elapsed events as meeting/tour/call (`:551`); server activity stores that assertion as `source='stated'` (`mcp-server/src/tools.js:4001`).
- **Consequence:** where historical revisions remain in HxStore, reschedules may appear as distinct occurrences, removed attendees may survive, and cancelled meetings may produce reminders or contact evidence. Independently, elapsed scheduled time is not evidence of attendance. No particular live meeting was shown to be wrong.
- **Fix:** decode authoritative lifecycle/revision/recurrence metadata and expose unsupported cases. Preserve scheduling evidence as inferred/provisional; promote it to a completed contact only with appropriate evidence. Reconcile reschedules and cancellations to the same occurrence identity rather than simply accumulating rows.
- **Effort / acceptance:** **3–5 days**, potentially more for format research. Fixtures must cover cancellation, moved start, attendee removal/decline, recurring exceptions, and elapsed-but-unconfirmed meetings. Native decoder validation is required before claiming calendar completeness.

## CARR outage and retry assessment

1. **Contact or read failure:** intake returns `carr_unreachable`; an early unresolved-contact failure leaves existing state untouched, while later read failures save current state (`lib/outlook_intake.py:654`, `:698`). Contacts come through the exporter DB reader; business writes normally use the Worker, so these are distinct availability/setup dependencies.
2. **Business write failure:** exact verb/payload/key is saved before dispatch, remains unacknowledged after failure, and is replayed before new work (`:350`, `:684`). Per-run attempted-key tracking prevents repeatedly hammering the same key in one run. Calls are bounded to 120 seconds, but there is no per-request backoff/circuit breaker, so many failures can still lengthen a run.
3. **Cursor:** failures and pending model steps do not advance the watermark (`:705`). This protects ordinary recovery with retained state, but F6/F10 explain first-run coverage and head-of-line blocking. Malformed/missing state is treated as empty (`:647`), not a separately visible recovery event.
4. **Server replay:** identical key/operation/principal/arguments returns the stored response; changed identity or arguments produce `key_reuse` (`mcp-server/src/tools.js:299`). Mutations and receipts share a transaction (`mcp-server/src/mcp.js:816`); the key is a primary key (`migrations/0001_init.sql:145`). That protects same-key retries, including lost acknowledgements, but not changed identities or different keys for the same logical item (F3).
5. **Operational receipt:** failures attempt immediate status delivery and fall back to a local spool; succeeded/skipped rows queue locally (`tools/ops-spool.py:181`, `:209`). This protects status delivery, not business-record delivery. If both delivery and spool fail, the error is visible (`:201`). Local spool contents and installed flushing were not inspected.
6. **Send behavior:** recipient-bearing sends fail closed if contact suppression cannot be read (`lib/outlook_send.py:369`). An outage after dispatch can strand only the logging obligation (F7). Personal events without recipients skip suppression by design. The normal Worker client has no automatic direct-DB fallback (`tools/call-verb.py:152`; `mcp-server/local-verb.mjs:213`).

## What was verified sound

- **Snapshot containment:** fixed copier source/destination scope, owned 0700 work directory, exclusive 0600 outputs, regular-file checks, and symlink refusal (`tools/outlook-intake-copy.c:39`, `:111`, `:172`). Installer scopes the privileged helper and tightens log permissions (`bin/install-outlook-intake.sh:68`).
- **Cleanup and concurrency:** raw copies and decoded databases are removed in `finally`; cleanup failure fails the run, and stale working directories are removed at the next eligible run (`lib/outlook_intake.py:236`, `:615`). A hard kill may leave private files until then. Intake's nonblocking lock prevents same-directory overlap (`:621`). These are access/cleanup protections, not an encryption claim.
- **Conservative identity:** exact unique addresses are required; names alone and ambiguous shared addresses do not auto-link. Wholly unmatched mail is retained and retried on contact-map changes even outside the lookback (`lib/outlook_intake.py:273`, `:418`, `:654`). Canonical contact views have no implicit workbook fallback (`tools/calendar-touch-matcher.py:40`).
- **Stable request replay:** request payloads are frozen before a write; acknowledgements and model replies are persisted; full-text enrichment gets a separate note key instead of changing the original request (`lib/outlook_intake.py:350`, `:491`, `:513`). These properties hold when source and server identity remain stable.
- **Send defaults and controls:** drafts are default; contact-policy failure and do-not-contact refuse dispatch; idle checks, exact form readback, expected account, existing-compose refusal, private locked ledger, and durable pre-dispatch latch exist (`lib/outlook_send.py:233`, `:278`, `:324`, `:365`, `:645`, `:723`). Ambiguous background results do not trigger a second foreground press; only explicit unavailable/refused background action permits fallback (`:566`).
- **Send confirmation:** a click alone is not success; the window must close and a mailbox match must appear (`lib/outlook_send.py:348`). Drafts are excluded from Sent Items inspection (`:781`). This is useful confirmation but still heuristic, as F8 describes. The legacy AppleScript intentionally saves drafts; Call Mode's reviewed caller validates its draft approval binding (`tools/dictation-rig/bin/post_call.py:917`).
- **Routine logs and model isolation:** intake status output contains only reason/counts, and normal wrapper receipts contain status/exit metadata (`lib/outlook_intake.py:134`; `bin/run-scheduled.sh:115`). The model command disables tools, MCP, settings sources, slash commands, dynamic sections, and session persistence and runs in an empty directory (`lib/outlook_intake.py:302`). Subject plus up to 12,000 body characters are intentionally sent to that model (`:323`); provider-side retention was not verified. Send transport exceptions are reduced to type/digest (`lib/outlook_send.py:160`).

## Offline verification and delivery review

| Check | Result |
|---|---|
| `python3 -B ops/outlook-intake-selftest.py` | 74 tests passed |
| `python3 -B ops/outlook-send-selftest.py` | 55 tests passed |
| `python3 ops/outlook-intake-copy-selftest.py` | 6 passed; 1 macOS installer test skipped |
| `python3 -B ops/outlook-triage-selftest.py` | 48 tests passed |
| `python3 -B ops/calendar-intake-gate-selftest.py` | 12 checks passed |

Existing suites were inspected before execution. Intake/triage/calendar-gate verification also used credential-free child processes with a Python network-denial audit hook; no guard tripped. Other suites use fake UI/CARR/mailbox ports and synthetic temporary files. Additional temporary fake-port probes reproduced F1, F2, F3, F4, F6, F7, F8, F9, and intake argv exposure; the expected/observed behavior is recorded in each finding. No new application tests or code were committed in this report-only change.

Report validation: staged-content secret/identity scan, path hygiene, and `git diff --check` passed. The repository's `ops/ci.sh --only pushfloor,freshness` passed for the report-only commit range. Explicit file:line references were checked for valid paths/line numbers; a separate source review checked the principal conclusions and corrected retention and action-ownership qualifications. Only the report path was staged and committed, with author `jbookout <jbookout@users.noreply.github.com>`.

These results do **not** establish deployed correctness: intake's fake writer accepts writes without exercising real server schemas (`ops/outlook-intake-selftest.py:105`), its decoder copies a synthetic SQLite fixture (`:124`), and UI tests do not validate the installed Outlook Accessibility labels. Calendar labels are explicitly unverified in source (`lib/outlook_send.py:656`). Rust decoder/native macOS tests, full hosted CI, real service health, and mailbox coverage were not established by these offline runs. Source inspection supplies the server-contract findings.

**Design and debt pass:** Applied `.agents/skills/codebase-design/SKILL.md` and `.agents/skills/zero-tech-debt/SKILL.md`. Recommendations centralize source identity, durable work state, canonical action reads, and approval enforcement at their actual interfaces; they do not add a second retry framework or a fuzzy identity fallback. No implementation refactor is part of this diff. The repository-referenced `poteto-mode`, `grill-with-docs`, `wayfinder`, and `tdd` skills were not present in the searched local skill catalogs; their use is not claimed.

The report is the only intended repository change. Findings describe proposed work and bounded evidence; they do not claim fixes, verified production behavior, or authorization to merge/deploy them.
