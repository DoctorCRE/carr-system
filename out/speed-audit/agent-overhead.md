# Agent and hook overhead audit

Observed on 2026-10-21 at 22:34 America/Chicago. Source revision `8e8e353bda188e11bc6466d478a44b1713d55bc1` in both the audit checkout and the canonical checkout. Their hook Python files matched byte for byte when checked. No hook, configuration, rule, service, or product behavior was changed.

The largest measured recurring cost is fetching the complete rule scope catalog on every applicable event. A live selection that returned no rules or cards still took **355.227 ms**. The full scope fetch alone took **333.952 ms** and transferred **339,771 bytes**. Those are medians of five successful runs, not estimates. See evidence E1 and E2.

## Ranked findings

Weekly figures below are **capacity scenarios**, not measured savings. They multiply measured per-invocation costs by retained October 20 activity and five identical working days. They represent summed desk wait across parallel desks, not elapsed calendar time. No speedup has been implemented or verified. Actual removal savings require an alternating before-and-after run. Rankings exclude unmeasured model inference time.

| Rank | Finding and measured current cost | Root cause and smallest fix | Expected saving and calculation | Risk | Did Joe ask for it? |
| --- | --- | --- | --- | --- | --- |
| 1 | Full catalog retrieval for each tool event. Fetch median 333.952 ms; complete empty selection median 355.227 ms. October 20 has 19,873 distinct Codex lookup receipts, including 19,605 PreToolUse receipts. E1, E2, E4. | `lib/rule_scope_lookup.py:163` starts `run.sh call standing-context` with `detail=scopes` each time. Reuse the sponsor-bound catalog within a context epoch and perform deterministic event selection locally. Refresh the digest at the chosen freshness boundary. Remove the per-call full-catalog transfer outright. | Up to **0.334 seconds per affected action**. A once-per-turn scenario removes 19,447 fetches from 19,873 receipts over 426 distinct turns. `19447 * .333952 * 5 / 3600 = 9.02 desk-hours/week`. This excludes Claude and silent Stop selections, and is not a guaranteed lower bound because cache implementation and refresh costs are unmeasured. | Medium. Catalog freshness, sponsor isolation, digest changes, and tool-specific rules must remain correct. Receipts still need their event binding. A local cache is a recommendation, not a verified implementation. | Rule compliance is required. No direct Joe request for a full network catalog transfer on every call was established from the inspected source and history. Remove that implementation cost, preserving rule selection. |
| 2 | Delegation observer starts even where it immediately exits. Direct synthetic read median 25.818 ms. Its full transcript parser separately took 15.462 ms for a 12,391,561-byte retained transcript. E2, E3. | The advisory hook imports its dispatcher before checking repository scope and later rereads the entire transcript for mechanical calls. Delete routine observer counters and their per-call registration. Keep any useful command-specific precheck at the command that needs it. | **0.026 seconds per matching no-op call**. Scaling 12,309 Codex exec calls plus 1,921 Claude Bash calls and one Claude Read gives `14231 * .025818 * 5 / 3600 = 0.51 hours/week`. This uses a no-transcript benchmark; real transcript parsing and locking are additional, unquantified costs. | Medium. The hook also hosts command precheck advice and sticky-task telemetry. Deleting it without checking those consumers would remove advice. The present hook does not enforce delegation. | The source documents the goal of routing cheap mechanical work. A direct request for per-call observational counters was not established. Recommend removing the observer outright, not the authorized routing goal. |
| 3 | Codex sends all `functions.exec` calls through the post-write lint process. October 20 had 12,309 exec calls; the current target extractor returned no patch paths for 11,920 of them. No-op lint invocation median 24.518 ms under the read-only wrapper. E2, E4. | The matcher is broader than `_changed_code_paths` in `hooks/lint-gate.py:157`. Native edits and patches need review; ordinary orchestration does not. Skip process launch when the event contains no patch operation, while preserving patch detection inside exec. | **0.025 seconds per no-patch exec**. `11920 * .024518 * 5 / 3600 = 0.406 hours/week` is the scenario ceiling before accounting for wrapper overhead and routing implementation cost. | Medium. Narrowing the matcher to native apply_patch alone would miss edits inside exec. Keep the existing explicit patch-target semantics. | Yes to automatic Jev involvement while code is written, quoted in `hooks/lint-gate.py:236`. No evidence of a request to launch its process on reads. Remove no-patch invocations outright. |
| 4 | Claude launches Bash write, draft-export, and model-floor hooks on ordinary shell reads. Their successful direct read medians are 19.090, 18.436, and 17.925 ms. E2, E5. | Three independent Python processes parse a command they often cannot affect. Filter unrelated commands before process launch, or dispatch the same policies in one process. Preserve the actual guarded operations. | Combined **0.055 seconds per qualifying read**. `1921 * (.019090 + .018436 + .017925) * 5 / 3600 = 0.148 hours/week` is an upper scenario assuming every October 20 Bash call qualified. The actual read fraction was not measured. | Medium to high for careless filtering. Arbitrary shell writes and indirect model invocations can escape a simplistic text filter. This is process routing work, not permission to remove safety policy. | The guarded outcomes have documented purposes. No direct request for three process launches on every shell read was established. Remove their provably irrelevant invocations. |
| 5 | Claude registers `rule-claim-gate.py` for Stop globally and in the repository. A missing-transcript synthetic invocation took 20.718 ms and produced a 116-byte stderr diagnostic. E2, E5. | Two differently wrapped commands target the same policy and event. Remove the repository duplicate when the canonical registration is present. | At most **0.021 seconds per Stop** before real transcript costs. Scaling October 20's 345 observed Stop summaries gives `345 * .020718 * 5 / 3600 = 0.0099 hours/week`. This is configured duplication, not proof the harness executed both commands at every Stop. | Low for conditional registration deduplication. Preserve the cloud fallback when the canonical installation is absent. | Accurate rule claims are required. No request for duplicate execution was established. Remove the duplicate outright. |

Do not add these figures to the context-byte or denial findings below. Those effects overlap, and their latency is not measured.

## Empty receipts are a large context cost, with unmeasured token cost

`hooks/rule-pack-preuse-reselection.py:154` suppresses empty output except on PreToolUse. Empty PreToolUse receipts remain deliberately model-visible. Their identity, hashes, event binding, and receipt digest travel with empty `rules`, `cards`, and `overflow` lists.

The October 20 Codex record contains **19,428 distinct empty receipts out of 19,873 distinct receipts**, or **97.76%**. They contain **17,620,630 UTF-8 bytes**. Dividing by **426 distinct task_started turn IDs** gives **41,363 bytes per recorded turn**. Repeated receipt occurrences are excluded from these counts. See E4. This is transport text, not a measured tokenizer result or a claim that all bytes were billed repeatedly.

The smallest safe fix is to remove the empty envelope from model-visible prose while retaining any integrity evidence its consumers actually require. `lib/rule_delivery_preuse.py:210` validates receipts and `:326` consumes them. Blindly returning no receipt could break that contract. Joe asked for useful rules and truthful verification; no direct request for tens of kilobytes of empty receipt prose per turn was established. Recommend removing the prose outright, with the integrity contract preserved outside repeated model text.

**Exact hook-attributable tokens are unmeasured.** Neither the active Python nor the repository virtual environment has tiktoken, and a search of the inspected Homebrew and uv package locations found no installed tiktoken directory. More importantly, a proxy tokenizer would not prove the native providers' billed token count. No package was installed and no bytes-per-token estimate is used. Native session usage totals include other context and cannot isolate hook tokens. The report measures actual injected bytes instead of inventing token counts.

## Daily volume across retained local desks

All dates in this table use America/Chicago. October 19 and 20 are completed days. October 21 is partial and includes this audit's activity. These are retained local transcripts, including child desks, not a claim about remote or deleted sessions. Native tool calls are deduplicated by runtime and call ID. Receipts are deduplicated by receipt ID. Nested shell calls can generate several receipts within one native exec call, so these denominators differ.

| Measure | October 19 | October 20 | October 21 partial |
| --- | ---: | ---: | ---: |
| Codex native tool calls | 16,599 | 13,531 | 8,492 |
| Claude native tool calls | 3,627 | 2,062 | 840 |
| Codex sessions with tool calls | 356 | 371 | 135 |
| Claude sessions with tool calls | 53 | 1 | 7 |
| Distinct Codex lookup receipts | 23,451 | 19,873 | 12,326 |
| Distinct empty Codex receipts | 22,832 | 19,428 | 12,009 |
| Distinct empty receipt bytes | 20,709,767 | 17,620,630 | 10,899,733 |
| Codex boot-banner occurrences | 485 | 384 | 193 |
| Codex boot-banner bytes, including core cards | 2,361,116 | 1,876,656 | 905,728 |
| Codex core-card delivery occurrences | 478 | 380 | 183 |
| Codex core-card bytes, already included in boot bytes | 1,777,682 | 1,413,220 | 680,577 |
| Claude Stop summaries | 545 | 345 | 138 |
| Hook executions reported in those Stop summaries | 2,407 | 2,323 | 890 |
| Stop summaries preventing continuation | 0 | 0 | 0 |

E4 contains the collection query and source roots. The native-call and banner scan preceded the receipt-deduplication scan, so partial-day counters have different capture moments. Use the completed October 20 column for comparisons.

Scaling the current empty selection median to October 20's distinct receipt count gives `19873 * .355227 / 3600 = 1.961 summed desk-hours/day`. It is a calibrated cost model, not a measured daily wall-clock total. Silent selections, Claude lookups, process startup, model inference, concurrent execution, and cache state prevent calling it a whole-system latency measurement.

## How many hooks fire

The installed global Claude configuration has **33 command registrations**, the installed Codex configuration has **15**, and the checkout's Claude configuration has **6**. These are event registrations, not unique programs. E5.

| Tool and runtime | Configured PreToolUse processes | Configured successful PostToolUse processes | Total |
| --- | ---: | ---: | ---: |
| Claude Bash in this repository | 9 | 3 | 12 |
| Claude Read in this repository | 2 | 0 | 2 |
| Claude Edit in this repository | 5 | 1 | 6 |
| Claude Agent in this repository | 4 | 0 | 4 |
| Codex functions.exec | 5 | 1 | 6 |
| Codex Bash | 5 | 1 | 6 |
| Codex functions.apply_patch | 4 | 1 | 5 |

Counts apply the configured regexes to the named native event. They do not assume whether the host serializes or overlaps independent hook processes. Claude Bash has boot, selection, guard, Bash write, write effect, draft export, GitHub burst, model floor, and repository delegation before execution. Its post hooks are write effect, GitHub burst, and boot observation. Codex's exec post hook is lint; Bash's is boot observation.

The two appearances of write-effect-check and github-burst-guard across PreToolUse and PostToolUse are **not duplicate policy execution**. They collect before-and-after state. Record-home and Bash-write guard the same policy through different write entry points, while write-effect reports actual effects after execution. Removing those protections as duplicates would be incorrect.

## Denials, retries, and stale telemetry

The timing ledger `/Users/booko/carr-system/out/hook-telemetry.jsonl` has **23,984 parseable rows**, all marked `source=live`, with **0 malformed rows**. Its timestamp range is **2026-10-15 18:11:31 through 2026-10-16 05:38:27 America/Chicago**. It does not cover the current audit window. E6.

Within that historical window, rule boot denied **27 of 11,233 invocations**, guard-unattended denied **8 of 745**, and rule selection allowed **888 of 888**. The staging observation tracker produced **1,441 errors** across the two ledger dates. That tracker is absent from the inspected current hook registrations, so it is historical waste, not a new removal proposal.

The recent plain guard log contains the following observations. It lacks a complete allow denominator, tool-use IDs, and fixture provenance. These are **log lines, not proven production denial rates or retries**.

| Logged event | October 19 | October 20 | October 21 partial |
| --- | ---: | ---: | ---: |
| Rule boot DENY | 99 | 50 | 40 |
| Guard-unattended DENY | 61 | 43 | 23 |
| GitHub burst DENY | 2 | 3 | 3 |
| Executor-tier DENY | 5 | 0 | 3 |
| Lint UNAVAILABLE | 127 | 53 | 14 |

Of October 20's **53** lint-unavailable lines, **49** say outside_repo and **4** report the Jev site budget. The current post-write path detects outside-repository work only after attempting repository discovery. Filter such targets before building review context. No paid-call or wall-time saving is claimed from these log counts.

**Production retry frequency and retry-induced agent time remain unmeasured.** A denied operation does not prove the next operation was its retry, and some log examples resemble fixtures. No fixed retry penalty is assigned. The existing historical ledger and recent plain log cannot honestly establish this number. A future implementation should reuse native event IDs and distinguish live versus fixture traffic rather than add another blocking gate or model judgment.

## Compaction and boot cost

The live boot reply is **699 bytes**, has **one page**, and declares **211 total characters**. The filtered digest-only boot used by this session returned the current digest successfully. Five full boot reads took **299.118, 290.987, 609.150, 285.535, and 354.838 ms**, with median **299.118 ms**. E1.

`lib/rule_boot_gate.py:811` fetches page one at SessionStart to arm the gate. `:832` creates a new epoch. Main-context compaction normally asks the model to fetch the page again. The same-digest carry path at `:1110` applies when transcript evidence identifies a child's compaction; it does not waive the main context's own boot. The redundant network component for a normal main boot is therefore approximately two measured boot reads, **0.598 seconds**, before local writes and the agent's scheduling gap. This is a component model, not a timed compaction. The exact same low-level call path was not timed end to end.

The retained October 20 transcripts contain **2 Codex compaction records** and **9 Claude compact_boundary records**. Their count alone does not prove each triggered precisely the same boot sequence. Multiplying by a guessed model pause would be invalid. Boot-banner bytes are much larger than the current digest reply because they also carry fetch instructions and the core cards. E4.

The smallest improvement is to retain a machine-verified same-digest receipt across a main compaction when the host can prove its identity and epoch, while still delivering the current core rules. Risk is medium because losing actual rule delivery is worse than removing a fetch. Joe's rule compliance requirement is established; a direct request for repeated same-digest network reads after every main compaction was not established. Recommend removing the redundant read after proving that receipt contract. No rule is retired by this audit.

## Jev attempts and paid-call evidence

The inspected usage sources are `/Users/booko/carr-system/out/jev-calls.jsonl` and `jev-judge.jsonl`. The first has **144,373 rows** and the second **43,064** at collection. Separate Jev usage directories were not present in the three inspected locations `.local/share/jev`, `.cache/jev`, and `Library/Application Support/jev`. This is a bounded local search, not proof that no vendor-side ledger exists. E7.

On October 20 America/Chicago, the call log records **7 attempts**. Six come from `jev_code_review`, with four admission_secret_missing and two site_daily_budget results. One comes from `jev_deal_read` and reports unattributed_call. None has usage, a successful result, or a cache hit. Against **15,593 observed native agent tool steps**, this is **7/15,593 attempted calls per step** and **0/15,593 usage-backed successful calls per step**. The latter is a lower bound on evidenced billing, **not proof of zero actual spend**. Recent judge rows are not additional billed calls.

For comparison, October 17 has **64 usage-bearing call rows**, including **52** attributed to rule_scope_lookup, and records **1,229,726 input tokens** and **117,191 output tokens**. Traffic provenance is unknown, so those are logged token observations, not a production workload or spend claim. The present selection hook supplies no moment judge and returned `selection_mode=deterministic` in all five live probes. Do not project October 17's model calls onto the current per-step path.

The existing read-only `tools/jev-value-report.py --since 2026-10-19 --until 2026-10-22 --root /Users/booko/carr-system --no-git --json` reports **133 attempts**, **0 usage-backed paid calls**, **133 billing-unknown rows**, and **28 unlinked judge observations** in its UTC window. That window corresponds to October 18 at 19:00 through October 21 at 19:00 America/Chicago, not the local-day table. Its `cost_complete=true` field must not be interpreted as complete billing while its own billing_unknown total is nonzero.

Automatic Jev code review was explicitly requested in the source's recorded Joe quote. Repeated attempts after a known unavailable admission or budget condition provide no successful review in the measured local-day sample. Suppress redundant attempts for that already-known unavailable interval and report the unreviewed state once. Do not claim review happened, weaken required human or CI checks, or remove Jev from actual code review. Savings are unmeasured because these rows have no elapsed time. No additional Jev call was made by this audit.

## Evidence and reproduction

### E1. Live read-only catalog and boot requests

The timed region was `subprocess.run([root + '/run.sh', 'call', 'standing-context', json.dumps(arg)], capture_output=True, text=True, timeout=20)`, using `time.perf_counter`. All replies had exit zero and `ok=true`. The source root was `/Users/booko/carr-system`. Byte counts used `len(stdout.encode())`. Each path ran five times.

| Path | Five wall-clock samples in milliseconds | Median ms | Reply bytes |
| --- | --- | ---: | ---: |
| detail=scopes | 294.265, 313.767, 371.083, 386.202, 333.952 | 333.952 | 339,771 each |
| detail=boot, page=1 | 299.118, 290.987, 609.150, 285.535, 354.838 | 299.118 | 699 each |
| Full hook process(payload, dedupe=False), already-imported module | 353.527, 355.227, 321.250, 374.021, 355.585 | 355.227 | 831 each |

The last row loads `hooks/rule-pack-preuse-reselection.py` with importlib, then calls `process` with a synthetic Bash `pwd`, an audit session ID, a tool-use ID, and a literal audit prompt. `dedupe=False` avoids delivery-state writes. All five outputs were complete, deterministic, contained no rules or cards, and did not deny. It excludes module import and cold process startup. The scope response held 203 catalog entries; that is catalog availability, not rules delivered into the model context.

The direct run.sh fetch includes its Python client and network round trip. The full lookup also selects rules and builds a receipt. These were sequential groups, not alternating cache implementations, so their difference is not a measured cache speedup.

### E2. Synthetic hook process timings

Five invocations per row. Input was a JSON event with tool_name Bash, command `pwd`, a successful stdout response, the standalone checkout cwd, an audit tool-use ID, and an audit prompt. All code came from `/Users/booko/carr-system/hooks/`. Each process used the canonical `.venv/bin/python -B`. The wrapper used runpy and an audit callback that refused filesystem mutations, socket connections, and subprocess launches. This protects the host but changes any hook path that needs those effects. Numbers are isolated synthetic invocation costs, not host end-to-end tool latency.

| Hook and event | Median ms | Observed range ms | Outcome and limitation |
| --- | ---: | --- | --- |
| rule-boot-gate PreToolUse | 23.255 | 21.850 to 24.816 | Existing fetched session; allow, no output, no prohibited effect. |
| rule-pack-preuse-reselection PreToolUse | 26.942 | 26.340 to 29.480 | Catalog subprocess deliberately refused; advisory fallback. Excluded from live-selection claims. |
| guard-unattended PreToolUse | 30.166 | 29.658 to 31.981 | Wrapper refused a write and process failed. Excluded from successful guard timings. Use direct row below. |
| github-burst-guard PreToolUse | 21.219 | 20.490 to 21.450 | Allow, no output or prohibited effect. |
| delegation-gate PreToolUse | 23.937 | 22.980 to 24.374 | Standalone cwd, no transcript; allow, no output. |
| bash-write-gate PreToolUse | 18.861 | 17.785 to 19.096 | Allow, no output. |
| write-effect-check PreToolUse | 18.506 | 17.427 to 19.752 | Marker write deliberately refused. Incomplete path; excluded from savings. |
| draft-export-gate PreToolUse | 17.670 | 16.960 to 18.277 | Allow, no output. |
| model-floor-gate PreToolUse | 17.677 | 16.719 to 17.948 | Allow, no output. |
| record-home-gate PreToolUse | 19.714 | 19.038 to 20.578 | Bash is irrelevant; measures dispatch cost, not actual edit checking. |
| one-repo-gate PreToolUse | 17.992 | 17.160 to 23.042 | Bash is irrelevant; no-op only. |
| canonical-edit-gate PreToolUse | 20.092 | 18.632 to 25.719 | Bash is irrelevant; no-op only. |
| executor-tier-gate PreToolUse | 18.719 | 18.026 to 19.497 | Bash is irrelevant; no-op only. |
| costar-lane-gate PreToolUse | 17.542 | 16.737 to 20.208 | Bash is irrelevant; no-op only. |
| weekend-quiet-gate PreToolUse | 19.211 | 18.417 to 20.900 | Bash is irrelevant; no-op only. |
| builder-agent-gate PreToolUse | 16.613 | 16.223 to 16.976 | Bash is irrelevant; no-op only. |
| rule-boot-gate PostToolUse | 22.160 | 21.617 to 23.035 | Non-boot pwd result; allow, no output. |
| github-burst-guard PostToolUse | 21.059 | 19.825 to 23.997 | Non-GitHub result; allow, no output. |
| write-effect-check PostToolUse | 18.776 | 17.467 to 21.376 | No marker; no-op only, not a filesystem sweep measurement. |
| lint-gate PostToolUse | 24.518 | 23.207 to 28.588 | No patch paths; allow, no output. |

Direct successful read probes then removed the wrapper and directed guard telemetry to `/dev/null` using the existing `CARR_HOOK_GUARD_LOG` fixture seam. They used cwd `/tmp`, no transcript, and a literal `pwd`. All 30 returned exit zero with no stdout or stderr.

| Hook | Five direct samples ms | Median ms |
| --- | --- | ---: |
| guard-unattended | 57.488, 44.041, 48.430, 46.110, 44.135 | 46.110 |
| github-burst-guard | 22.136, 21.611, 22.650, 21.836, 22.154 | 22.136 |
| bash-write-gate | 20.423, 18.996, 19.090, 19.660, 18.755 | 19.090 |
| draft-export-gate | 18.938, 17.710, 18.436, 18.497, 18.348 | 18.436 |
| model-floor-gate | 18.122, 17.746, 17.910, 17.925, 18.857 | 17.925 |
| delegation-gate | 26.837, 25.997, 25.818, 25.403, 25.619 | 25.818 |

A rule-claim missing-transcript probe took 23.412, 22.346, 20.526, 20.718, and 20.316 ms. It returned exit zero and 116 stderr bytes each time. It is not a measurement of the policy with a real transcript.

**Coverage limits.** Real write-effect sweeps, real edit guards, paid Jev review, identity/presence writes, machine convergence, worktree plumbing, continuity writes, and a complete hosted tool turn were not executed. The corresponding no-op rows do not price their active paths. Uptime reported load averages 6.99, 7.46, and 6.79 on 18 logical CPUs before measurement. Other desks remained active. Interpreter startup/import and the live request round trip limit the measured paths. No profiler overhead is included. There is no before-and-after implementation comparison.

**Probe correction.** A watchdog probe unexpectedly created its normal audit-session banner signature and lock. Only the two audit-specific files named by SHA-256 of `audit-read-only-no-transcript` were removed, and their absence was verified. Only those audit-specific paths were removed; other session signatures were untouched. The resulting watchdog timing is excluded from rankings. The implementation at `lib/job_watchdog.py:1331` already suppresses unchanged banners per session; this audit does not propose implementing that existing fix again. The read-only boundary was temporarily breached by this probe and restored.

### E3. Delegation transcript scan

`hooks/delegation-gate.py:148` opens and JSON-decodes every transcript line. The actual source file was `/Users/booko/.codex/sessions/2026/10/20/rollout-2026-10-20T08-52-37-01a15995-e31b-7aa2-bfdc-804103f7e7e1.jsonl`, with **12,391,561 bytes** and **428 records**. Five direct calls to `records(path)` took **17.521, 14.889, 15.462, 14.263, and 18.003 ms**. This measures parsing only, not task reconciliation or state locking. Transcript content is not reproduced.

### E4. Transcript queries

Sources were `/Users/booko/.codex/sessions/**/*.jsonl` and `/Users/booko/.claude/projects/**/*.jsonl`. Only files modified since October 19 America/Chicago were scanned. Within them, records were selected by their own timestamp converted to America/Chicago. An older file cannot hold a later append without its mtime changing. This is a retained-local-data query, not a completeness claim about other machines.

Native Codex tool records were response_item records whose payload type was function_call or custom_tool_call. Native Claude tool records were assistant message content blocks with type tool_use. The deduplication key was runtime plus call_id, id, or the Claude tool-use id. Session counts use distinct source transcript files containing those calls. Turn counts use distinct Codex task_started turn_id values.

Only Codex developer-message text starting with a JSON object and parsing as schema `rule-lookup-delivery/v1` was counted as a receipt. Contents printed inside tool results or audit scripts were excluded. Receipt IDs were deduplicated across the collection. `len(text.encode())` measured bytes. Empty means no rules and no cards. Boot counts use developer text beginning `RULE BOOT:`. Core-card bytes use the text after `RULE CARDS (always-on core)`, which remains part of the boot-banner total. Claude Stop counts use unique system/stop_hook_summary UUIDs and their hookCount, hookErrors, and preventedContinuation fields.

For example, actual receipt records were read from `/Users/booko/.codex/sessions/2026/10/20/rollout-2026-10-20T17-39-05-01a15b77-e2df-7fd0-b640-ae3bcc447da8.jsonl`. No transcript prose or client data is copied into this report.

To reproduce the no-patch count, load the current `hooks/lint-gate.py` module and pass each deduplicated October 20 exec input to `_changed_code_paths({'tool_name':'functions.exec','tool_input':input,'cwd':'/tmp'})`. Decode function_call arguments as JSON; retain custom_tool_call input as its original script string. Count empty path lists. This returned **11,920 empty lists** and **389 potentially matching patch calls**. Potential matches are not a claim that all edits ran successfully.

### E5. Installed dispatch configuration

Read-only sources were `/Users/booko/.claude/settings.json`, `/Users/booko/.codex/hooks.json`, and the standalone checkout's `.claude/settings.json`. Only hook configuration was used. Registration totals are the sum of lengths of each event group's hooks array. Tool counts apply their matcher regexes to the native tool names. The global and repository rule-claim Stop commands use different wrappers but name the same canonical script. Project Codex hooks are absent in this checkout; no project Codex registrations were added to the count.

### E6. Runtime logs

Sources were `/Users/booko/carr-system/out/hook-telemetry.jsonl`, `hook-guard.log`, and `hook-guard-unclassified.log`. JSON telemetry was counted by hook and outcome. Recent text logs were grouped by local date and the hook/action words before any command text. Commands, private material, and client content are not reproduced. Text lines were not joined to tool-use IDs because they do not provide them.

### E7. Jev evidence

Call usage was summed only from numeric fields in usage objects. Missing usage was not converted into measured tokens or dollars. Judge observations were not added to paid-call counts. Dates in the local tables were converted from each row's ts or at timestamp. The existing value-report command above was run without --fetch-ci and without git history collection. Its own billing_unknown rows limit what can be claimed despite cost_complete. The repository price configuration was inspected but no current price or monetary saving is asserted.

## Decisions, interactions, and verification

The audit uses the completed October 20 local day for capacity scenarios. It uses synthetic read inputs, existing read-only fixture seams, no new paid model requests, and no proposed production changes. Weekly figures are explicitly scenario arithmetic because deployment and post-change measurements are outside this assignment.

Merge-queue batching is already owned by job `7710a773`. CI path gating, deduplication, and snapshot migrations are already owned by `eaa72983`. They are not re-proposed here. The interaction is that GitHub burst control must remain around those owners' mutations, and this report should not trigger a new full-repository test sweep or an additional merge mechanism.

The same report is delivered to the requested canonical output path and committed from the standalone checkout. The orchestrator inbox receives one dated line. This is report delivery, not a rule retirement or a hook fix. No merge is authorized.

Skills used: poteto-mode; Prove It Works; benchmark-checklist; technical-writing; unslop; codebase-design and zero-tech-debt for the report-only design and debt pass. Pocock TDD is not applicable because no behavior changes. The requested grill-with-docs skill was not found in the checkout skill tree or the inspected user skill locations. The task's autonomous decision grant was applied, and the decisions are recorded here instead of asking for an interview. Throughput checkpoint: n/a, read-only investigation.

Design and debt pass: codebase-design found no new code interface in this diff. The recommendations preserve existing policy seams. Zero-tech-debt kept the deliverable to one report and recommends deleting irrelevant launches and duplicate output instead of adding flags or gates.

Verification performed includes live successful catalog and boot readbacks, five successful complete deterministic selection probes, direct hook outcome checks, transcript deduplication, source-byte comparison, formula evaluation, and report readback. **Automated behavior tests added or run: 0.** The audit is documentation only. Synthetic measurements are observations, not a substitute test suite. Exact hook tokens, complete current denial rates, proven retries, total actual paid Jev cost, active write paths, whole-turn latency, and post-fix savings remain unverified and are labeled above.
