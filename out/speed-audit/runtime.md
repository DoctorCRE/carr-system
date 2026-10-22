# Product runtime speed audit

Observed 2026-10-21, America/Chicago. Read-only production audit for Joe and Dell. No runtime code, configuration, database records, or services were changed.

Home is the first speed target. Its complete dashboard read takes **5.239 seconds at the median**, with full detail reads for 54 active deals. Leads takes **1.204 seconds** and has both a redundant identity read and repeated database scans. The published all-repositories board itself loads in **0.537 seconds**. Its separate directory verb fails because it retrieves more than Neon's response limit.

This report ranks the current waits that a fix can address. It does **not** rank invented future savings. Only the directory serialization comparison has a measured replacement. All other expected savings below identify removable work and explicitly leave the end-user delta unmeasured. Weekly hours saved cannot be calculated from these logs: automated polling and audit calls are mixed with user reads, and no human action counts were established.

## Findings ranked by current seconds per user action

### 1. Home requests every active deal's complete record

- **Measured cost now:** 5.239 s median, range 5.205–5.316 s across five browser reloads. All completed with `dealAttention[aria-busy=false]` and no partial-data notice. The screen reports 54 active deals. The source loads their details through four workers, then repeats the dashboard on its refresh cadence. Evidence E1 and [Home reader](https://github.com/jbookout/doctorcre-app/blob/d23276125b82170fc6d5568a386ab32ce0e48e42/js/home-dashboard-model.js#L105), [refresh](https://github.com/jbookout/doctorcre-app/blob/d23276125b82170fc6d5568a386ab32ce0e48e42/js/workspace-command-center.js#L18).
- **Root cause:** the calendar needs dates and actions, but `readHomeDashboard` calls `getDeal` for every scoped deal. That returns notes, events, premises, documents, negotiations, and other detail too. Each update also republishes and redraws the dashboard. This is an application N+1 pattern, not evidence that individual database indexes are generally slow.
- **Smallest fix:** replace the per-deal full-record loop with one bounded agenda read containing deal IDs, dates, and open actions. Keep full detail on explicit record opening. Delete the loop rather than increasing concurrency or adding flags. Preserve parked, closed, owner, and task-hold semantics.
- **Expected saving:** eliminate the source's 54 full-record reads per complete dashboard refresh for this observed inventory. The resulting seconds saved are unmeasured. The entire observed 5.239 s load is the maximum available budget, not a promised saving. First deal flags arrive progressively before full completion; this is not a 5.239 s blank screen.
- **Risk:** a narrower agenda could omit undated or held tasks, lose national-account scope, or misrepresent incomplete data. Verify agenda membership against the existing read before switching.
- **Did Joe ask for it?** The product agenda is requested functionality. No requirement for eagerly loading every complete record was established. Remove that implementation overhead outright, while retaining the agenda.

### 2. Leads pays for an identity board and repeated scans before rendering

- **Measured cost now:** 1.204 s median browser completion, range 1.126–1.672 s, with 361 visible leads. The workspace read returns 375 rows and takes 0.633 s median HTTPS completion. Its underlying view aggregation takes 474.824 ms median across five database runs. These populations differ because the UI applies eligibility filters. Evidence E1, E2, E4.
- **Root cause:** `refresh()` awaits `getActor()` before `getWorkspace()`. `getActor()` reads a complete `deal-room-board` solely for `actor`. Separately, the workspace view uses correlated lifecycle, name-match, and latest-transition reads. The measured plan includes `Seq Scan on party cp_4`, 1,480 rows per loop, repeated 375 times, and client scans repeated 375 times. Sorting uses an in-memory quicksort of 136 kB in the initial diagnostic run, so disk sort is not the observed problem.
- **Smallest fix:** reuse the shell's existing authenticated session read at `/api/system-work/session` instead of fetching deals for identity. Then aggregate lifecycle matches once per workspace statement and join by ID or normalized name. Preserve suppression and contact eligibility. Source: [refresh](https://github.com/jbookout/doctorcre-app/blob/d23276125b82170fc6d5568a386ab32ce0e48e42/js/leads-workspace-app.js#L169), [identity client](https://github.com/jbookout/doctorcre-app/blob/d23276125b82170fc6d5568a386ab32ce0e48e42/js/leads-client.js), [workspace SQL](../../mcp-server/src/lead-workspace.js), [view](../../migrations/0847_lead_score_owner_reader_repair.sql).
- **Expected saving:** remove one serial full-board request per refresh. The comparable `workspace=all` board probe costs 243.472 ms median and returns a 234,861-byte decompressed MCP envelope; the identity call uses `workspace=team`, so those figures are a comparator, not the exact removable call's timing. No seconds saved are claimed for the database rewrite until an equivalent result is measured.
- **Risk:** reusing identity must fail closed on actor changes and retain the existing refusal behavior. Rewriting match joins must preserve tombstone exclusions and possible-client ambiguity. Do not remove contact or lifecycle checks to get a faster number.
- **Did Joe ask for it?** Leads and safe eligibility are product behavior. No requirement for reading deals to identify the partner, or scanning all parties for every lead, was established. Remove both implementation costs; retain their results.

### 3. Deal detail makes serial reads for independent sections

- **Measured cost now:** 1.053 s median browser reload through a populated deal panel, range 0.929–1.069 s. The selected record, ID `ca3835fc-d07f-4f30-8bed-29185312c9e0`, takes 414.534 ms median HTTPS completion and returns a 67,941-byte decompressed envelope. Evidence E1 and E2.
- **Root cause:** after subject resolution and the deal read, the handler awaits notes, critical dates, history, actions, activities, participants, premises, negotiations, and documents separately. Most HTTPS time occurs before response headers. Serial database round trips are a source-supported explanation, but no distributed trace attributed every millisecond. Two targeted database checks were fast: notes 0.094 ms and events 0.104 ms, each one diagnostic execution. This does not justify a blanket index campaign.
- **Smallest fix:** compose independent sections into one read statement with the same response contract and ordering. Retain complete history at the explicit record opening; paginate history only if its measured size becomes a problem. Source: [handler](../../mcp-server/src/tools.js#L9150).
- **Expected saving:** remove serial database round trips. The end-user saving is unmeasured and cannot exceed this action's current 1.053 s median total. Do not count this independently of Home if Home stops calling the same full-detail handler.
- **Risk:** preserve nulls, ordering, archived-deal support, permissions, and snapshot consistency. Arbitrarily parallelizing independent HTTP database requests can weaken the consistency of the displayed record.
- **Did Joe ask for it?** The complete record is intentional product behavior. Serial retrieval is an implementation choice. Speed up retrieval; do not remove requested record sections.

### 4. Morning brief computes sections the current modal does not use

- **Measured cost now:** 0.676 s median fresh browser refresh, range 0.580–0.725 s. Cached modal opening took 0.293 s in one run. HTTPS completion is 0.600 s median, range 0.562–0.668 s, for a 392,664-byte decompressed envelope. The observed modal rendered two cards. Evidence E1 and E2.
- **Root cause:** the backend awaits section reads in sequence. The current modal model reads `today`, `deals`, and `loops`, then the browser separately reads `/pipeline/changes`. The backend also computes `needs_joe`, `claim_card`, `renewals`, `assurance_cadence`, and `costs`. Those five sections occupy 145,139 bytes when reserialized as compact ASCII-escaped JSON in the measured response. That is an exact local serialization measurement, not compressed wire bytes. The brief starts reading on mount and refreshes even while its dialog is closed, provided the page remains visible and active.
- **Smallest fix:** give the modal a read containing the fields it consumes and delete unused work from that path. Load on opening and invalidate on changes rather than repeatedly rebuilding the closed brief. Where full brief consumers exist, retain their contract. Source: [backend](../../mcp-server/src/tools.js#L3333), [modal](https://github.com/jbookout/doctorcre-app/blob/d23276125b82170fc6d5568a386ab32ce0e48e42/js/morning-brief.js#L96), [model](https://github.com/jbookout/doctorcre-app/blob/d23276125b82170fc6d5568a386ab32ce0e48e42/js/morning-brief-model.js#L17).
- **Expected saving:** eliminate five unused section computations and their serialized output from the modal path. Seconds saved are unmeasured. Removing background prefetch trades less work for a potentially slower first opening; compare fresh opening before choosing that part of the change.
- **Risk:** the backend explicitly says Joe's brief leads with the needs-Joe list, but the inspected modal does not render that section. This is a product/source conflict, not authority to retire needs-Joe. Preserve required outputs in full consumers. Do not silently remove a business requirement because one UI ignores it.
- **Did Joe ask for it?** The brief is requested. The backend documents needs-Joe as requested. No requirement for recomputing unused sections in a closed modal was established. Remove unused work from this UI path, not the requested business feature.

### 5. Progress transfers full snapshots repeatedly; directory fails on their aggregate size

- **Measured cost now:** direct all-repositories board load 0.537 s median, range 0.401–0.558 s. Its read takes 207.702 ms median HTTPS completion and returns a 316,513-byte decompressed MCP envelope. The formatted CLI output is 455,966 bytes; those two sizes must not be confused. Evidence E1 and E2.
- **Root cause:** the direct board fetches a complete published snapshot on each poll. More urgently, `list-progress-boards` selects every `snapshot_json` and only then maps each to title/project/counts. Its live call failed with Neon HTTP 507, `response is too large (max is 67108864 bytes)`. The scoped database query found 1,028 visible board rows with 81,188,624 bytes of snapshot text. A later ID-only count found 701 `carr-v5-external-*` rows and 327 other rows. These are snapshot rows, not distinct human projects. Evidence E3.
- **Smallest fix:** project directory summary fields inside SQL and keep internal page snapshots out of human navigation where the page contract identifies them. Delete full-snapshot selection from the directory. The direct canonical board route suppresses directory loading, so the directory failure does not mean the direct board fails. Add version-aware reads only after measuring actual user benefit; do not begin with a cache framework.
- **Expected saving:** the read-only SQL comparison produced 188,605 bytes of summary JSON and reduced database serialization from 255.955 ms to 158.796 ms median. That is 97.159 ms less in this microbenchmark, with five paired runs. This is not the deployed endpoint and does not include network transfer or mapping. The immediate expected result is avoiding the response limit; its end-user time saving remains unmeasured.
- **Risk:** preserve tenant/sponsor filters, current question state, freshness, and published counts. Do not count internal pages as projects. Do not replace missing freshness with empty data.
- **Did Joe ask for it?** Progress visibility is product behavior. Fetching all full snapshots for summaries is unnecessary implementation work. Remove it outright. Polling freshness itself should be preserved while removing unchanged payload work.
- **Interaction with work already underway:** coordinate any reader changes with CI path-gating/dedupe/snapshot work, job `eaa72983`; do not start a competing snapshot migration. Merge-queue batching, job `7710a773`, changes producer cadence and board contents, not the full-snapshot directory SELECT. Neither job's fix is proposed again here.

## Evidence E1: real-browser observations

Chrome, Joe's existing signed-in account, production `app.doctorcre.com`. Read-only view navigation and refresh controls. Times include automation transport, page navigation, module boot, network, and rendering. They are upper-bound observations of the completion marker, **not** Navigation Timing, FCP, LCP, or isolated network timings. Browser caching and server warmth were left as found. No cache purge or restart occurred.

| Screen and completion marker | Runs, ms in order | Median, ms | Sample p95, ms |
| --- | --- | ---: | ---: |
| Home, `#dealAttention` busy false, no partial notice | 5316, 5239, 5216, 5205, 5254 | 5239 | 5316 |
| Leads, `#leadBoard` busy false, count populated | 1672, 1126, 1251, 1193, 1204 | 1204 | 1672 |
| Deal panel open, `data-updated` present, no Updating placeholder | 1069, 1053, 942, 929, 1058 | 1053 | 1069 |
| Morning refresh, `#morningUpdated[datetime]` changes | 682, 725, 582, 580, 676 | 676 | 725 |
| All-repositories board, Published metadata, populated stage text, no busy marker | 537, 558, 544, 401, 431 | 537 | 558 |

The sample p95 uses nearest rank and is the maximum of five samples. It is not a production population p95. Initial navigation to Home showed the shell with Updating placeholders; it was not a complete first-load measurement. Later reloads above measure complete reads. True empty-cache first load and Worker/Neon cold starts remain unmeasured.

The timing harness used the clock immediately before `goto`, `reload`, or the refresh click, then polled the stated DOM marker with the documented browser tool. Failed harness attempts are excluded: an attached-selector deadline; three Home observations stopped while still busy; a board loop waited for busy false although the implementation removes the attribute; a second board loop used an unreliable descendant-button count. The latter loops timed out as measurements while the board was already populated. They are **not page failures**. A browser-tool kernel reset remapped browser IDs; the same live tab was found and verified before further measurements.

Browser network timing could not be collected through the supported surface. Both `window.performance` and `document.defaultView.performance` returned undefined in its read-only DOM scope. Native Chrome inspection returned `cgWindowNotFoundBrowser`; the tab shortcut did not expose DevTools. Capability discovery offered page assets and viewport/management, not a network log. Accordingly, the following network numbers come from separate HTTPS probes, not the browser.

## Evidence E2: repeated read-only production MCP probes

Five interleaved rounds, completed at 2026-10-21 22:19:43 CDT. The deployed registry identified every invoked verb as a read. The ordinary `mcp-server/local-verb.mjs` HTTPS path handled authentication internally. An in-memory Node preload wrapped `fetch` and response `text()` to measure headers and complete body consumption. No credentials were opened by the audit, copied, printed, or put in arguments. No disk instrumentation file was created.

| Verb and arguments | Full HTTPS completion, ms in order | Median TTFB, ms | Median total, ms | Sample p95 total, ms | Decompressed envelope bytes |
| --- | --- | ---: | ---: | ---: | ---: |
| `morning-brief {}` | 615.969, 561.956, 600.097, 581.209, 667.566 | 595.633 | 600.097 | 667.566 | 392664 |
| `deal-room-board {workspace:all}` | 289.586, 243.472, 269.756, 227.193, 241.954 | 240.267 | 243.472 | 289.586 | 234861 |
| `get-deal-room {deal:ca3835fc-d07f-4f30-8bed-29185312c9e0}` | 437.368, 391.318, 437.037, 407.718, 414.534 | 412.399 | 414.534 | 437.368 | 67941 |
| `lead-board {workspace:leads}` | 666.531, 610.683, 588.452, 632.530, 670.944 | 628.519 | 632.530 | 670.944 | 424856 |
| `read-progress-board {board_id:all-repos}` | 274.520, 239.255, 197.811, 206.669, 207.702 | 203.581 | 207.702 | 274.520 | 316513 |

All 25 probes returned HTTP 200, valid JSON, expected response shapes, and no tool error. The brief's costs section was partial, which is a data-coverage state, not a failed request. All responses advertised zstd; `text()` measures decoded bytes, so **compressed wire bytes are unknown**. No Server-Timing header was returned. TTFB combines network, Worker, and database work. It cannot identify their individual shares.

An earlier seven-round CLI baseline made 49 successful reads, including today-triage and both carr-v5/all-repos boards. It timed subprocess startup, wrapper, HTTPS, JSON parsing, formatting, and output. These are not substituted for the HTTPS numbers. Formatted stdout sizes were stable: morning brief 553210, default deal board 285245, selected detail 72929, default lead board 352080, triage 25633, carr-v5 84856, all-repos 455966 bytes. These figures explain why a '460K snapshot' observation differs from a wire envelope.

The machine observation before probes was load averages 7.31/6.07/5.88 on 18 CPUs. It was a working shared machine, not an isolated benchmark host. No other process was stopped. Browser DOM polling also adds observer work. No before/after user-facing speedup is claimed.

## Evidence E3: directory size, failure, and read-only SQL comparison

SQL ran through `/Users/booko/carr-system/tools/db-tap.py sql /dev/stdin` using its existing environment. `transaction_read_only` returned `on`. Checkout attempts first failed because its virtualenv and neonctl dependencies were absent; the existing shared wrapper then ran successfully. No dependency installation or config edit occurred. Each diagnostic used a statement timeout.

At 2026-10-21 22:18:17 CDT, this query returned `1028 | 81188624 | 246488`:

```sql
select count(*), sum(octet_length(snapshot_json::text)),
       max(octet_length(snapshot_json::text))
from board_snapshot
where organization_tenant_id='carr-internal'
  and sponsoring_human_slug='joe'
  and board_id <> 'needs-joe-local';
```

At 22:28:39 CDT, the same tenant/sponsor/exclusion predicates with `count(*) filter(where board_id like 'carr-v5-external-%')` and its inverse returned `701 | 327`. These are later observations; no claim is made that all rows represent separate projects or that they are all currently referenced.

The full serialization comparison was:

```sql
explain(analyze, buffers, timing off)
select sum(octet_length(row_to_json(t)::text)) from (
  select board_id, snapshot_json, updated_at from board_snapshot
  where organization_tenant_id='carr-internal'
    and sponsoring_human_slug='joe' and board_id <> 'needs-joe-local'
) t;
```

The projected comparison replaced only the inner SELECT list:

```sql
select board_id, snapshot_json->>'title' as title,
       snapshot_json->>'project' as project,
       snapshot_json->'task_counts' as task_counts, updated_at
```

| Paired run | Full serialization, ms | Projected serialization, ms |
| --- | ---: | ---: |
| 1 | 273.007 | 158.796 |
| 2 | 256.909 | 160.472 |
| 3 | 254.890 | 161.981 |
| 4 | 255.955 | 157.033 |
| 5 | 255.818 | 157.309 |

Both forms scanned the same 1,028 rows. An initial unreported diagnostic pair was 311.887/178.618 ms. The projected `sum(octet_length(row_to_json(t)::text))` returned 188605. This comparison forces server serialization and returns only sizes and plans; no board bodies were retrieved into the report. It demonstrates a smaller database result and reduced serialization work, not the exact runtime of a patched MCP handler. Repeated JSON field extraction still decompresses stored values; a new materialized cache is not necessary to avoid the response limit.

Observed seven-day read ledger, ending 22:18:17 CDT, uses:

```sql
select verb, count(*) as calls, count(*) filter(where not ok) as failures
from tool_read_call
where created_at >= now()-interval '7 days'
  and organization_tenant_id='carr-internal'
  and sponsoring_human_slug='joe'
  and verb in ('morning-brief','deal-room-board','get-deal-room','lead-board',
               'today-triage','read-progress-board','list-progress-boards')
group by verb order by calls desc;
```

| Verb | Calls | Failures |
| --- | ---: | ---: |
| read-progress-board | 116178 | 3 |
| get-deal-room | 4946 | 13 |
| deal-room-board | 1681 | 13 |
| morning-brief | 802 | 0 |
| today-triage | 569 | 0 |
| lead-board | 233 | 0 |
| list-progress-boards | 165 | 145 |

These are read-call ledger entries, including automated calls and this audit, not unique people, loads, projects, or business rows. Tombstone exclusion does not apply to an immutable call ledger. Error kinds were not aggregated, so the 145 directory failures are not all asserted to be the size error.

## Evidence E4: slow query diagnosis and telemetry limits

The live extension lookup returned false for `pg_stat_statements`. A `pg_views` search for names matching `%slow%` or `%stat_statement%` returned no rows. `information_schema.columns` showed no latency/duration column in `tool_read_call`. The full deployed registry contained 380 verbs; no generic SQL latency reader was used or assumed. This bounds the telemetry finding to these tested surfaces. It does not establish that Neon has no provider-side telemetry.

The Leads diagnostic used this exact read, which is a subcomponent of the handler's wider statement:

```sql
explain(analyze, buffers, timing off)
select coalesce(jsonb_agg(to_jsonb(w)
  order by w.stage_sort,w.score desc nulls last,w.name,w.id),'[]'::jsonb)
from v_lead_workspace w;
```

The initial diagnostic took 486.372 ms. Five subsequent runs took `468.562, 475.179, 474.337, 478.852, 474.824` ms. The median is 474.824 ms; the plan produced 375 rows. Its repeated nodes include party and client sequential scans and correlated lifecycle subplans, each with `loops=375`. Those SQL node counts demonstrate database repetition, independent of the browser and wrapper timings. They are not a measured speedup for a replacement query.

The separate one-run targeted probes used:

```sql
explain(analyze,buffers,timing off)
select id,kind,to_jsonb(created_at)#>>'{}' from v_deal_room_note
where deal_id='ca3835fc-d07f-4f30-8bed-29185312c9e0'
order by created_at desc,id desc;
explain(analyze,buffers,timing off)
select id,field from v_deal_room_event
where subject_id='ca3835fc-d07f-4f30-8bed-29185312c9e0'
order by recorded_at desc,id desc;
```

They took 0.094 and 0.104 ms respectively. They do not cover the full handler, every record, or concurrency. No production p95 database query ranking or cold-start attribution is claimed. Enabling an extension, logs, tracing, or a keepalive was outside this read-only assignment.

## Provenance, decisions, and remaining limits

- Source checkout: CARR `8e8e353bda188e11bc6466d478a44b1713d55bc1`, standalone branch `runtime`. App source inspected read-only: `/Users/booko/doctorcre-app` at `d23276125b82170fc6d5568a386ab32ce0e48e42`.
- Latest recorded shipped CARR release returned `d0a03715fbe263f9c8dfe0f4e9fa47211a607f15`, release `r-2026-10-21-04`, completed 2026-10-21 21:04:18 CDT. `git diff` found no changes from that revision to this checkout in `tools.js`, `board-answers.js`, or `lead-workspace.js`. The tools.js SHA-256 matched `f14d555126e8ab508b9756b19599cc06c78f534e4c039cf94beaaca0ee07a798` at both revisions. This is a recorded-release check, not a direct readback of the active Worker build stamp.
- App asset fetch outside the authenticated browser returned HTTP 403. Exact equality between inspected app files and deployed assets was not verified. Browser-observed completion and content remain direct production evidence; source explanations are identified separately.
- Native evidence log: `/Users/booko/.codex/sessions/2026/10/21/rollout-2026-10-21T22-14-27-01a1619a-595f-7261-b734-d97ca1439fb7.jsonl`. It contains tool commands and results behind these sanitized extracts. Do not attach the raw transcript to a PR: it includes rendered private business context. The report contains counts and IDs only.
- Autonomous decisions: keep the audit read-only; compare existing reads rather than implement fixes; rank current seconds/action instead of guessing weekly human usage; distinguish full completion from first paint; use existing wrappers after dependency failures; report cold starts and browser network timing as unmeasured; retain business features while questioning implementation work; preserve overlapping jobs instead of creating competing fixes.
- Validation: 25 completed browser measurements, 25 instrumented successful HTTPS probes, 49 successful CLI baseline reads, five paired directory SQL comparisons, five repeated Leads SQL diagnoses, and targeted diagnostic reads. Automated behavior tests: 0, because this change is only a report. No implementation performance improvement has shipped from this audit.
- Skills used: poteto-mode; Prove It Works; benchmark-checklist; grill-with-docs with its grilling/domain-modeling questions answered autonomously; technical-writing; unslop; codebase-design and zero-tech-debt for the required report/PR pass. Pocock TDD is not applicable because no behavior changed. Prove It Works changed the report by excluding failed measurement markers and separating formatter bytes from network envelopes.
- Design and debt pass: codebase-design kept proposed reads narrow and owned by the runtime, without a new generic client layer. Zero-tech-debt favors deleting eager detail fetches, redundant identity reads, and unused output instead of adding flags or a cache framework. No source architecture changed in this PR.

The report is the sole new tracked artifact. Its identical delivery copy is `/Users/booko/carr-system/out/speed-audit/runtime.md`; the requested coordination line goes to `/Users/booko/carr-system/out/orchestrator-inbox.md`. No merge, production write, configuration edit, restart, credential inspection, or client-data export is part of this work.
