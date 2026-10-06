---
name: engineering-slice
description: On-demand Engineering Passport execution contract; never scheduled.
---

# doctrine: carr-production-maturity-baseline

# Engineering Passport Slice

RULE-DELIVERY WORKFLOW: engineering-slice
RULE-DELIVERY PACKS: engineering-git,delegation-council,scheduled-automation,source-study

FIRST: call `standing-context` with exactly
`{"packs":["engineering-git","delegation-council","scheduled-automation","source-study"]}`
and read the returned rules before inspecting an envelope, source, or job. Do
not pass `workflow`: `standing-context` also interprets that field as a pack
name, and `engineering-slice` is a workflow label rather than a canonical rule
pack. REFUSE to claim or execute the slice when that call fails, returns any
`packs_not_found`, or does not read back all four canonical pack names. Do not
substitute an alias or a full-set fallback.

This is an on-demand control-plane definition, not a recurring task. Never
create a provider schedule, cron entry, automation, or fallback launcher for
it. Admission occurs only through `admit-engineering-slice` after an exact
accepted Work Request plan has been projected into a typed slice plan.

The server selects the Codex Desktop adapter, binds a fresh native session,
issues the immutable execution envelope, and enqueues the fixed shadow-mode
`ops.job`. The room-bridge's lease-bound controller calls only
`ops.engineering_claim_slice`, derives the exact executor and plan from that
envelope, then starts the dedicated Codex desk fresh with no database
credential. The executor may return only a lease-bound typed receipt. Its
claim does not complete the slice until a different actor records an
evidence-backed review with `review-engineering-slice`.

Never accept caller-selected tenant, sponsor, identity, authority, model,
provider, adapter, or session continuity. Never use inherited transcripts,
silently widen a slice, bypass unresolved dependencies, or treat an executor
claim as independent verification.
