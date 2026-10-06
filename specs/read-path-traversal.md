# Spec: read-path traversal of the lead ↔ client link

**Status:** proposed, 2026-08-02
**Type:** read-path only. No writes, no migration, no data change.
**Evidence:** the 2026-08-02 system audit (file deleted; findings are loops #124–128), "Root cause" and "The link is already there"

---

## Problem

`convert-lead` writes a `client_id` onto a lead. Joe ran it on ten records on 2026-07-31
(`detail: "Link all 10"`). **The read verbs don't follow it.**

Observed today:

```
catch-me-up L-005  (Jonathan Sorbara, lead)   → 3 bare rows: import, convert-lead, last-touch stamp
catch-me-up C-113  (Jonathan Sorbara, client) → the actual narrative
```

Same person. The pointer from L-005 to C-113 exists and is unused. The work queues
(`today-triage`, `lead-hot`) surface the *lead*, so they surface the empty record — which
is why Castillo's standing "do not contact" instruction is invisible to the queue that
keeps scheduling her.

This is the cheapest high-value fix available: **no data changes, no backfill, and it
respects the no-backfill policy because it computes rather than fabricates.**

---

## Scope

| Verb | Change |
|---|---|
| `catch-me-up` | Return the merged timeline across the linked pair |
| `today-triage` | Resolve suppression and next-action state across the pair |
| `lead-hot` | Same, and stop ranking a record whose counterpart is suppressed |
| `find` | Show that two refs are the same person, rather than as separate results |
| deal reads | A deal resolves through `client_ref` to its client's timeline |

---

## Behavior

### 1. Resolve to a cluster, then union

Given any ref, resolve to the set of records linked to it:

- lead → its `client_id` target, if set
- client → any lead whose `client_id` points at it
- deal → its `client_ref` target

Union the timelines, sort by `occurred_at` descending.

**Traversal is one hop. Never chain.** A deal resolves to its client and stops — it does
not then pick up the client's *other* deals. `C-131` (Deshmukh) carries twelve deals;
rendering all of them on each one is noise, not context.

### 2. Attribute every row to its source

Merging must not erase which record a row came from. Add `source_ref` to each timeline
entry:

```json
{"entry_kind":"activity","occurred_at":"...","actor":"joe","verb":"analysis",
 "summary":"What actually happened — July 11, 2026","source_ref":"C-117"}
```

Without this you cannot tell whether an instruction lives on the lead or the client, and
the next person to debug this loses the thread the same way.

### 3. Symmetry

`catch-me-up L-005` and `catch-me-up C-113` return the same merged set. Direction of the
stored pointer is an implementation detail, not a user-facing distinction.

### 4. Suppression resolves across the pair

`contact_state` (once it exists — see `DECISIONS.md`) is read from **any** record in the
cluster. A suppression on the client suppresses the lead. Until that field ships, this is
the interim rule:

> If any record in the cluster carries a next-action whose text begins `HOLD`, `NONE`, or
> `DO NOT`, `today-triage` excludes the whole cluster and reports it under a separate
> `suppressed` count rather than dropping it silently.

That is a stopgap on prose parsing and should be deleted the moment `contact_state`
lands. It is written here so it does not become permanent by accident.

---

## Explicit non-goal: do not match by name

**Traversal follows explicit links only.** Never infer that two records are the same
person from name similarity, even with high confidence.

This system has already been burned by exactly that. From the C-117 record:

> *Identity note (Jul 6, 2026): a same-day import error briefly merged this prospect with
> Jeff Castillo, DMD (a dentist from Dell's contact export — different person, now registry
> L-158). Corrected within the hour by Joe.*

`find Castillo` returns Dr. Jenna Castillo (L-001), Jeff Castillo, DMD (L-158) and Dr. Jenna
Castillo (C-117). A name matcher merges the wrong pair. Linking stays explicit and human-
confirmed via `convert-lead` / `confirm-merge`.

### What to do about unlinked records instead

Castillo has no `convert-lead` event — her L-001 and C-117 are unlinked, so traversal will
not help her. Surface them rather than guessing:

> When a record's timeline contains only import-era rows, and another record shares its
> `org_name` or exact normalized name, emit a **`possible_unlinked_counterpart`** flag on
> the read. Do not merge. Do not traverse. Just make it visible so a human can run
> `convert-lead`.

That converts a silent gap into a reviewable queue, which is the whole point.

---

## Follow-on this unlocks: derive `last_touch` instead of storing it

Finding 1 in the audit — every deal stamped `2026-07-31T00:00:00.000Z`, staleness
detection blind until Aug 14 — exists because `last_touch` is a **stored** column set by
the import.

Once the cluster union exists, `last_touch` can be **derived**: the newest real
(non-import, non-`"[]"`) entry across the cluster.

That fixes staleness detection with no backfill and no invented data — it is computation
over records that already exist, which is precisely what the no-backfill policy permits.
Records with genuinely nothing resolve to null and flag as never-touched, which is the
truthful answer.

Worth doing as a second change, after traversal is verified, not bundled into it.

---

## Acceptance tests

Use real records — these are the ones that failed today:

| # | Given | Expect |
|---|---|---|
| 1 | `catch-me-up L-005` | Sorbara's C-113 narrative rows, each tagged `source_ref: C-113` |
| 2 | `catch-me-up C-113` | Same set as #1, symmetric |
| 3 | `catch-me-up` on the Ferris deal `5e4ee8f4` | C-127's analysis rows included |
| 4 | `catch-me-up L-001` (Castillo, unlinked) | Unchanged content, **plus** `possible_unlinked_counterpart: C-117` |
| 5 | `find Castillo` | L-001 and C-117 shown as one person; **L-158 Jeff Castillo stays separate** |
| 6 | `today-triage` | Castillo's row excluded once C-117's "PAUSED — inbound only" resolves across the pair |
| 7 | `catch-me-up` on any C-131 deal | That deal + C-131's own rows. **No rows from C-131's other eleven deals** |
| 8 | `catch-me-up C-129` (Colby Marsh, genuinely empty) | Still empty. Traversal must not invent content |

Test 8 matters as much as the rest: the fix must make hidden content visible without
making empty records look populated.

---

## Out of scope

- Deduplicating or merging parties (see the gate in `DECISIONS.md`)
- Writing `notes_path`, `stage`, or `contact_state`
- Any change to `last_touch` (separate follow-on above)
- The `prospect_pool` → `candidate_pool` rename
