# 0059 — the organization layer: spec and the reasoning behind the shape

*Written 2026-08-02 alongside `0059_org_identity.sql`. Every number below was measured
read-only on the `rehearse-verbs-20260802` Neon branch (a copy of production) before any
migration was written. Where the brief I was given differs from the data, the data is
recorded here and the brief is corrected.*

---

## 1. The claim I was asked to test, checked line by line

The reframing handed to me was: the 157 duplicate party rows are not 157 duplicate records
but a missing organization layer, because identity was minted by the CONTACT rather than
held by the ENTITY — the same disease `0046_party_identity.sql` diagnosed one level up when
it moved identity from role to person.

| claim | verdict | measured |
|---|---|---|
| ~157 duplicate party rows | **wrong number, right order of magnitude** | 172 surplus rows across 98 name groups (case- and whitespace-normalised, live rows only) |
| 115 of them are `kind='org'` rows | **exactly right** | 48 org name groups, 163 rows, **115 surplus** |
| Example Organization 25 exists as 17 separate org rows | **confirmed** | 17 rows, all `kind='org'`, one distinct exact spelling |
| Patterson Dental 10, Example Organization 38 13 | **confirmed** | 10 and 13 |
| every org row has exactly ONE person pointing at it via `party.org_id` | **confirmed, with no exceptions at all** | the inbound-count distribution over all 415 org rows is a single bucket: `1 → 415`. Not one org has two people; not one has zero |
| 157 is a RUNNING TOTAL, not a fixed backlog | **confirmed at the code level, not merely inferred** | `add-party` (`mcp-server/src/tools.js:1156`) and `add-premises` (`:1281`) both do an unconditional `insert into party (kind,name,...) values ('org', $1, ...)` whenever an `org_name` is supplied. There is no lookup on either path. Every promotion that carries an employer mints a fresh org row by construction |
| one org party is literally named `example-6343f421.invalid` | **true but understated** | **22** org rows are bare domains (`example-b00c0fa1.invalid`, `example-50352747.invalid`, `example-e5f57802.invalid`, `example-73bf0a66.invalid`, …), not one |

**The person half of the number does not survive contact with the data.** 115 of the surplus
is org; the remaining **57** is person, not the 42 that `0046` recorded. 0046 counted
name-identical *lead/client pairs* — a narrower thing than name-identical parties — so the
two numbers were never measuring the same set, and 157 appears to be one of them added to
something else. It does not matter for this migration, which touches no person row, but the
figure should stop being quoted.

**So: the characterisation holds, and it holds more strongly than it was stated.** A 1:1
inbound relationship with *zero* exceptions across 415 rows is not a table with some
duplicates in it. It is a table where `party.org_id` is functionally a per-person employer
NAME field that happens to be stored as a foreign key. Seventeen Example Organization 25 rows are not
seventeen companies anyone believes in; they are seventeen reps, and the company was minted
seventeen times because the row that carried the identity was the contact.

### Pressure already queued behind it

`candidate_pool` holds 9,773 unpromoted rows. **57 distinct `org_name` values in that pool
already appear on more than one candidate row**, and one already matches an existing org
party. Promoting the pool as it stands mints at least 57 new duplicate groups on top of the
46 that exist today.

---

## 2. Two shapes, and why one of them wins on this data

### Shape A — a dedicated `organization` table

The textbook answer: `organization(id, name, domain, city, …)`, `party.organization_id`
pointing at it, org parties migrated across and dropped.

### Shape B — consolidate the duplicate org PARTIES and repoint `party.org_id`

Keep `kind='org'` parties as the org entity, collapse each name group to one row, repoint
every person at the survivor, and put a unique key on org identity so the collapse cannot
un-happen.

### Shape B, for five reasons that are specific to this system rather than general

1. **Blast radius, measured.** `party.org_id` is a self-FK and every consumer already reads
   an org *through party*: `left join party org on org.id = p.org_id` appears in
   `v_ref_index` (all five branches, 0056), in `tools.js:507`, and in the exporters. Shape A
   rewrites all of that. Three other seats are working in `tools/`, `exporters/` and
   `mcp-server/` in this same session; a change that requires simultaneous edits in files I
   do not own is a change that ships half-applied.

2. **A separate table buys zero columns today.** I counted what the 415 org rows actually
   carry: `city` on 57, `county` on 54, name on all. **Every other column is null on every
   org row** — no email, no phone, no npi, no notes_path, no specialty, no title, no parent
   org, and not one non-default `contact_state`. An `organization` table created now would
   be `party` with the person columns removed and nothing added.

3. **The reversibility mechanism already exists and is already tested.** `party.merged_into`
   is the A3 tombstone from `0001`, exercised by `confirm-merge` and repaired by `0055`.
   Consolidating *through* it means **no row is deleted**: the 109 losers keep every column
   and every `P-####` ref, and `find` keeps returning them with `merged=true`, which is the
   posture `0056` deliberately chose so that a search for a merged name learns where it went.
   Shape A has no equivalent path that anyone has run.

4. **The disease is not the table shape, it is the missing unique key.** An `organization`
   table without a unique name key reproduces the bug in a new table on day one. A unique key
   on the org rows fixes it without moving anything. Shape A is only a fix if it also does
   the thing Shape B does, which makes the table move optional rather than the fix.

5. **Nothing else references an org row, verified across all twelve FK paths into
   `party.id`.** `building_ownership`, `commission_allocation`, `deal_participant`,
   `registration`, `lead`, `client`, `vendor`, and all three `party_link` columns hold
   **zero** references to a `kind='org'` party. The only inbound edge in the entire schema is
   `party.org_id`. That is what makes the repoint a one-column update rather than a project.

### What Shape B costs, stated plainly

An org that lives in `party` can never grow org-only attributes (entity type, parent company,
NAICS, headcount) without hanging columns on `party` that are meaningless for a person, and
every person-scoped query has to remember `kind='person'`. That cost is real and it is
deferred, not avoided.

**Reopen condition, so this is not relitigated by feel:** promote to a real `organization`
table when either (a) org-only attributes exceed roughly three columns, or (b) an org needs
to hold a role of its own — a client that *is* a company rather than a person, which is a
live possibility for a DSO or a franchise parent. Note that 0059 makes that promotion
*easier*, not harder: after it, `party.org_id` points at exactly one row per real
organization, so the extraction is a mechanical `insert into organization select … from
party where kind='org'` plus a key swap. Doing it in the other order would mean migrating
115 duplicates into the new table and deduping there.

---

## 3. What the migration actually does

1. **`org_identity_key(text)`** — an immutable normaliser: trim, collapse internal
   whitespace, lowercase. Nothing else.
   - It deliberately does **not** strip legal suffixes or parentheticals. The data contains
     `Example Organization 116` and `Example Organization 116 (advisory)` as separate rows on purpose, and
     an aggressive normaliser would weld an advisory arm onto a CPA firm. Case and whitespace
     are the only differences that are never meaningful.
   - It returns **null for placeholders**, which is the trap in this dataset. Six rows are
     literally named `(TBD — enrich)`, plus `(TBD)`, `Example Organization 41c813fb)` ×2, and two spellings of `(new practice, relocating VA → FL)`. Eleven rows in
     total. Merging six `(TBD — enrich)` rows would assert that six different people work at
     the same company, which is a **fabricated fact**, and fabricating one is worse than
     leaving eleven duplicates. A null key is excluded from both the merge and the unique
     index, so placeholders stay separate and new ones stay allowed. I checked the rejection
     list against all 415 names: it catches those five spellings and nothing else.

2. **Consolidate, org rows only.** Within each identity key, the survivor is the oldest by
   `(created_at, id)` — the same deterministic rule `0046` used for `P-####` assignment, so a
   re-run on a restored dump picks the same survivor rather than reshuffling. Losers get
   `merged_into = survivor`; nothing is deleted.

3. **`org_merge_log`** records `(party_id, from_org, to_org, identity_key)` for every person
   repointed. This is what makes the change reversible *in effect*: `merged_into` alone tells
   you the org rows collapsed but not which person came from which row, and that mapping is
   destroyed by the repoint unless it is written down first. The exact reversal statement is
   in the migration header.

4. **A partial unique index** on `org_identity_key(name)` where the row is a live, non-merged
   org with a non-null key. This is the part that stops 46 from becoming 103 next month.

5. **`org_party_id(name, actor)`** — an atomic find-or-create, race-safe, with the
   placeholder path preserved. This exists so the companion code change is one line.

### Nothing here merges a person. Structurally, not by intention.

Every statement filters `kind = 'org'`. The guard block asserts, before and after, that the
person row count, the person `merged_into` count, and the person duplicate-name group count
are all *identical* — so if a future edit widened a `where` clause by accident, the migration
rolls back instead of applying. Person merges remain the exclusive business of the humanOnly
`confirm-merge` verb under the survivorship rule, and this system has already merged the
wrong Beasley once.

Org survivorship is trivial *and that was verified rather than assumed*: within every one of
the 46 groups there is exactly one distinct exact spelling, one distinct email, one phone,
one notes_path and one specialty. **Zero groups contain two different non-null cities.**
Eleven groups have a city on one row and null on another, and the migration salvages the
non-null value onto the survivor so the collapse loses nothing at all.

---

## 4. The one thing this breaks, and it is not optional

After the unique index exists, `add-party` and `add-premises` will **raise
`unique_violation`** whenever they are given an `org_name` that already exists as a live org,
because both do a blind insert. That is a live verb failing.

Loud failure is the right side of the tradeoff — silent duplication is exactly what produced
115 rows nobody noticed, and the house rule is that a verb that errors gets investigated
while a verb that invents an answer gets believed. But it must not be discovered in
production. **The companion change is one line in each of the two call sites**, replacing the
blind insert with:

```sql
select org_party_id($1, $2)
```

`mcp-server/src/tools.js` is owned by another seat this session and is deliberately not
touched here. **0059 and that change should be applied in the same deploy.** 0057 and 0058
have no such coupling and can go ahead on their own.

---

## 5. Deliberately out of scope

- **Domain matching.** 22 org rows are bare domains. Those rows already hold identity *by
  domain*; they simply are not labelled as such, and some of them almost certainly name the
  same organisation as a text-named row (`example-e5f57802.invalid` and a Example Organization 109 practice, to
  pick the obvious one). Reconciling them needs a verified name↔domain mapping this system
  does not have, so the migration does not guess. The enrichment plan that intends to use
  domain as an exact match key should extend `org_identity_key` in a later migration rather
  than inventing a second normalisation rule, and any name↔domain collapse should go through
  human review, never a `where` clause.
- **Genuine same-name collisions.** Two unrelated `Example Organization 76` practices in different
  towns would now collide on the unique key. The escape hatch is to disambiguate the name —
  which is what the data already does with `Example Organization 116 (advisory)` — rather than to
  weaken the key.
- **Backfilling `org_id` for the 254 people who have none.** Out of scope, and it is an
  enrichment question rather than an identity one.
