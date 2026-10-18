# 0061 — the national account: spec and the reasoning behind the shape

*Written 2026-08-02 alongside `0061_national_account.sql`. Every number below was measured
read-only against production before any migration was written, using
`tools/db-tap.py sql`. Where the brief I was given differs from the data, the data is
recorded here and the brief is corrected.*

---

## 1. Joe's ruling, and what each clause demands structurally

> "A franchise or multi-location brand is ONE national account, and each franchisee is their
> OWN SUB-CLIENT under that main client — not a line item on it. Each franchisee also carries
> their OWN SALESFORCE DEAL, so the deal grain is per franchisee, never per brand.
> Structurally: the parent is a single org party with one client record over it (client_type
> national_account); each franchisee is a person party whose party.org_id points at that ONE
> parent org; each deal attaches to the FRANCHISEE'S client, never to the parent and never to
> another franchisee. Deals move DOWN to the franchisee, never up to the parent. Also: the
> brand is NOT a segment. Segment holds the vertical; the national flag is a lane; the account
> is the parent-client link." — Joe Bookout, 2026-08-02

| clause | already true? | what 0061 does |
|---|---|---|
| parent is a single org party | **yes**, `0059` collapsed 13 Example Organization 38 org rows to 1 (P-900029, 12 tombstoned into it) | asserts it survived, changes nothing |
| one client record over it, `client_type = 'national_account'` | **no**, no client existed over P-900029 | creates it |
| franchisee is a person party whose `org_id` points at that one org | **yes**, all 13 already do (0059) | asserts it, changes nothing |
| each deal attaches to the franchisee's client | **no**, 12 of 13 were on the wrong client | re-points the 12 |
| deals never on the parent | n/a | creates the parent with 0 deals and asserts it stays 0 |
| the brand is not a segment | **no**, `deal.segment = 'Example Organization 38'` on 13 rows and `client.vertical = 'Example Organization 38'` on 13 rows | clears both |
| the national flag is a lane | **no column existed** | adds `deal.lane` + `deal_lane` vocab, backfilled from source |
| the account is the parent-client link | **already resolvable** through `party.org_id` | exposes it as `v_client_account`; adds no column |

---

## 2. The measurements

Every figure here came out of a query run against production on 2026-08-02.

**The mis-attachment.** 40 deals. 13 carry `segment = 'Example Organization 38'`. All 13 have
`client_id` = C-900004, whose party is P-900034 Example Organization 65. That is correct for exactly one
of them, "Example Organization 01bf4c3d". The other 12 belong to 12 different franchisees
who each already had their own person party AND their own client row, all holding **zero**
deals:

| franchisee | party | client | deal |
|---|---|---|---|
| Example Organization 65 | P-900034 | C-900004 | Example Organization 01bf4c3d *(already correct)* |
| Example Organization 10 | P-900037 | C-900005 | Example Organization 11 |
| Example Organization 89 | P-900032 | C-900012 | Example Organization 130 |
| Example Organization 160 | P-900047 | C-900006 | Example Organization 6 |
| Example Organization 114 | P-900049 | C-900008 | Example Organization 144 |
| Example Organization 105 | P-900035 | C-900009 | Example Organization 142 |
| Example Organization 103 | P-900036 | C-900010 | Example Organization 49 |
| Example Organization 55 | P-900041 | C-900016 | Example Organization 68 |
| Example Organization 129 | P-900045 | C-900007 | Example Organization 78 |
| Example Organization 83 | P-900031 | C-900014 | Example Organization 53 |
| Example Organization 98 | P-900030 | C-900017 | Example Organization 156 *(name mismatch, see §3)* |
| Example Organization 13 | P-900038 | C-900018 | Example Organization 145 |
| Example Organization 126 | P-900033 | C-900015 | Example Organization 126 – MASS |

So the sub-client structure Joe describes was already in the data. The deal grain was the
part that collapsed.

**What else is attached.** Each of the 13 deals carries exactly one activity row, and
activities hang off `activity.deal_id`, so they follow the deal automatically. Zero of the 13
clients carry a client-level activity. None of the 13 franchisee parties has a `lead` row.
There is nothing else to move.

**client_type has never been used.** All 168 client rows have `client_type = NULL`. The
vocabulary (`independent`, `group`, `dso`, `franchise`, `regional_system`, `national_account`)
was seeded in 0002 and has sat unused since. 0061 writes the first values: `national_account`
on the parent, `franchise` on the 13 franchisees.

**No client has ever sat over an org party.** All 168 sit over `kind='person'`. See §6.

---

## 3. The "Example Organization b18f0aa7" problem does not exist, and finding that out mattered

I was warned that `deal.name` says "Example Organization b18f0aa7" while the party is "Example Organization 98" (double
n), that this was the one fuzzy match in the set, and that Joe's standing rule forbids
asserting an identity link on inference — anything not exactly matchable must be left alone
and reported.

The warning is right about `deal.name`. `deal.name` is simply the wrong column.

Every deal carries its Salesforce import verbatim in `deal.source_row`, a jsonb blob with 26
keys including a dedicated `contact` field. For the deal named "Example Organization 156",
`source_row->>'contact'` is **`Example Organization 98`** — the correct spelling. Checked across all 13:

- `source_row->>'contact'` matches a live `kind='person'` party on **exact string equality**
- exactly **one** party each — never zero, never two
- each of those parties carries exactly **one** live client row
- 13 for 13

So the migration matches on `source_row->>'contact'`, and the plan is built from exact
equality only. There is no similarity function, no normalisation, no fallback branch and no
"closest match" anywhere in the file. The guard asserts all 13 resolved before anything
commits: if even one fails, the migration raises and applies nothing. There is deliberately
no partial mode — under Joe's rule the correct behaviour for an unresolvable row is to stop,
not to skip it quietly and not to guess.

The general lesson is worth keeping: when a display name and a source field disagree, the
source field is the evidence and the display name is a rendering.

---

## 4. `deal.segment` was holding three different facts, and the source data proves it

`deal.segment` is free text with no foreign key — the only deal column of its kind
(`deal_type`, `phase`, `client_type`, `party_link_kind` all have vocabulary tables). That is
a large part of how it ended up holding three unrelated things at once.

The Salesforce source already separated two of them. `deal.source_row` carries **`seg`** and
**`lane`** as distinct keys:

- `lane`: populated and non-empty on all 40 deals — `territory` 25, `national` 15
- `seg`: equals the stored `deal.segment` on **38 of 40** deals
- the 2 that differ are **Example Organization 143** and **Example Organization 77**.
  On both, source `seg` is the **empty string**, source `lane` is `national`, and the stored
  `segment` reads `national`.

The importer fell back to the lane when the segment was blank, and the lane has been sitting
in the segment column ever since. That is a provable transcription defect, not a modelling
opinion.

`0061` therefore adds `deal.lane` with a `deal_lane` vocabulary table and backfills it from
`source_row->>'lane'`. That is pure transcription of a field that already exists on every
row: no inference, and the guard asserts every `deal.lane` equals its own `source_row->>'lane'`.

---

## 5. The vertical for Example Organization 38: there is no correct value, and I did not invent one

I was asked to determine the correct vertical from the data or the vault rather than
inventing one, and to say so plainly and propose options if there genuinely is none.

**There genuinely is none.** Example Organization 38 is a music-lesson franchise.

- The vertical values in live use on deals are: Chiro, DPC, Dental, Fitness, Healthcare,
  Legal, Ortho, Other, Vet.
- The vault's vertical reference guides (`DNA/Reference/`) cover dental, medical, vision and
  veterinary.
- The Salesforce `seg` field for these 13 rows says `Example Organization 38` and nothing else.
- **There is no segment vocabulary table anywhere in the schema** to consult — I checked
  `information_schema.tables` for anything matching `segment` or `vertical` and got zero rows.

Music education is not any of the existing values. `0061` sets `segment` to **NULL** for the
13 deals and `client.vertical` to NULL for the 13 franchisee clients.

**Why NULL rather than a placeholder.** NULL means "the vertical for these deals is not
known", which is exactly true. It *removes* a value that is known to be wrong rather than
replacing it with one that is merely unverified. Writing `Other` would be inventing a
classification; writing `Fitness` or `Healthcare` would be worse, and both would then be
rendered by the exporters as though someone had decided them. Setting the right value later
is a one-line update; un-inventing a fabricated classification after a month of reports is
not.

**The options, for Joe to pick from.** Any of these is a one-line update and none of them
should be chosen by a migration:

1. **`Music Education`** (or `Music Lessons`) — a new vertical value. Honest and specific.
   Costs nothing, since the column is free text; the argument against is that it grows the
   uncontrolled vocabulary by one more ad-hoc string.
2. **`Other`** — already in use on 3 deals. Truthful in the sense that it is none of the named
   verticals, and it keeps the value set small. The argument against is that `Other` on 13 of
   40 deals stops meaning anything.
3. **Leave NULL** — the state 0061 ships. Correct until Joe decides, and visibly incomplete
   rather than quietly wrong.
4. **Seed a `deal_segment` vocabulary table** and decide the whole list at once, the way
   `deal_phase` and `deal_type_ref` were decided. This is the option that fixes the *class* of
   problem rather than this instance, and it is the one I would recommend Joe consider — a
   free-text column with no vocabulary is what let a brand and a lane both move in.

Note that Example Organization 38 is arguably not healthcare at all, which is a separate and more
interesting question than which vertical string to write. CARR's stated verticals are dental,
medical, veterinary, chiropractic, therapy, vision, fitness and wellness; the pipeline also
holds a Legal deal (Example Organization 99f6800b). Whether music education belongs in this book at all is
Joe's call and nothing here presumes an answer.

---

## 6. Two shapes for the parent link, and why no column wins

### Shape A — `client.parent_client_id`

A self-referencing FK on `client`. The textbook answer, and the one the phrase "parent-client
link" invites.

### Shape B — resolve the parent through the chain that already exists

`franchisee client → party → party.org_id → the org's national_account client`, exposed as a
view.

### Shape B, for three reasons specific to this data

1. **The chain is already populated and already the house mechanism.** Joe's own wording is
   the argument: "each franchisee is a person party whose party.org_id points at that ONE
   parent org." All 13 already do — `0059` did it. Every other consumer in the system already
   reaches an org through `party.org_id` (`v_ref_index`'s five branches all carry
   `left join party org on org.id = p.org_id`; `tools.js:507`; the exporters). A column would
   be a second path to a fact the first path already answers.

2. **Two representations of one fact must be kept in agreement forever.** `parent_client_id`
   and `party.org_id` would both encode "who is this client's parent". Nothing would keep them
   consistent, and on the day they disagree there is no way to tell which is right. That is
   the same failure mode `0059`'s header describes one level down, where identity lived on the
   contact instead of the entity.

3. **It buys nothing today.** There is exactly one national account and 13 children. A view
   answers every question a column would.

**The reopen condition is genuine**, and it is not a formality: extract the column the moment
an account needs to span two org parties (a brand that acquires another brand), or a client
needs a parent that is not its employer. Both are plausible. Neither is true now, and
`v_client_account` makes the extraction easier rather than harder, because by then the chain
will have been exercised and its gaps will be visible.

---

## 7. A client over an org party is a structural first, and it was checked rather than assumed

All 168 existing clients sit over `kind='person'` parties. Nothing forbids an org:
`client.party_id` is a plain FK to `party(id)` with no kind filter, and the consumers read
`party.name`, which an org row has.

The one thing worth checking was `v_ref_index`, because `0058` turned "these branches cannot
overlap" into a test that now runs against a shape `0056` never saw. The party branch is
guarded by `NOT EXISTS (select 1 from client c where c.party_id = p.id)`, so **P-900029 leaves
the party branch at the instant it gains a client row and arrives in the client branch**. The
total is unchanged. The guard in `0061` asserts the branch counts moved by exactly one in each
direction, and re-runs `0058`'s party-disjointness assertion, rather than trusting the
reasoning.

One behavioural consequence to know about: `find "Example Organization 38"` now resolves the brand to a
**client** ref instead of a party ref. It returned 13 rows before (the org party plus the 12
tombstones from `0059`) and returns 13 now (the client plus the same 12 tombstones), so
nothing became more ambiguous than it already was.

The parent's `status` is `engaged`, taken from the vocabulary's own definition — "Live
relationship, no open deal. On the active book." That is literally and *permanently* the
parent's position, because Joe's grain rule puts the deals on the franchisees and never on the
brand.

---

## 8. The one judgment call, and what was deliberately left for Joe

**Example Organization 43 (C-900011) and Example Organization 148 (C-900013) carry `segment = 'national'`.** I was told
their business-model question is open, Joe has not ruled on it, and they must not be silently
restructured.

**What 0061 does NOT do to them:** no parent org, no `national_account` client, no
`client_type`, no change to their party rows, no change to their self-named org parties
(P-900042 "Example Organization 43", P-900043 "Example Organization 148" — themselves artifacts of the org-minting
defect `0059` fixed), no change to their clients or deals beyond the one column below.

**What it does do:** sets `segment` to NULL on those two deals, because `source_row` proves
the value is the lane rather than a vertical — `seg` is the empty string and `lane` is
`national` on both. The lane is preserved in the new `deal.lane` column, so **no information
is lost**; it moves from a column where it is wrong to a column where it is right. I judged
this to be undoing a transcription error rather than restructuring a business model, and the
distinction is what the paragraph above is for. The migration says so out loud in a second
`raise notice` at the end of the run, and the reversal is one statement:

```sql
update deal set segment = 'national'
 where name in ('Example Organization 143', 'Example Organization 77');
```

**Still open, for Joe:**

1. Whether Example Organization 1370ea72 and Example Organization efecbbd6 are national accounts in the sense of this ruling — i.e.
   whether each has a brand above them that should become a parent org + national_account
   client the way Example Organization 38 just did. Their `lane` is `national` in the source, which is
   evidence they are national-lane deals, and that is a different claim from being national
   *accounts*.
2. The correct vertical for the 13 Example Organization 38 deals (§5).
3. Whether to seed a `deal_segment` vocabulary table so `segment` stops being the only
   uncontrolled classification column on `deal` (§5, option 4).
4. Whether music education belongs in a healthcare CRE book at all (§5).

---

## 9. Reversal

No row is deleted and no party row is touched. `deal_reattach_log` records deal → old client →
new client before the update, because `deal.client_id` is a single column and overwriting it
destroys the only copy of where the deal used to sit — the same reason `0059` needed
`org_merge_log`.

```sql
-- 1. the 12 deals go back to the client they hung off
update deal d set client_id = r.from_client
  from deal_reattach_log r where r.deal_id = d.id;

-- 2. every segment goes back to its own source row. This covers all 15 touched deals
--    (13 Example Organization 38 + Example Organization 1370ea72 + Example Organization efecbbd6) in one statement and needs no log at all,
--    because deal.source_row is never modified by anything and holds the import verbatim.
update deal set segment = nullif(source_row->>'seg', '')
 where segment is null and source_row->>'seg' is not null;
update deal set segment = 'national'
 where name in ('Example Organization 143', 'Example Organization 77');

-- 3. the franchisee clients
update client set vertical = 'Example Organization 38', client_type = null
 where id in (select c.id from client c join party p on p.id = c.party_id
               where p.org_id = (select id from party where ref = 'P-900029'));

-- 4. the parent account and the new structure
delete from client where party_id = (select id from party where ref = 'P-900029');
drop view v_client_account;
alter table deal drop column lane;
drop table deal_lane;
```

**Why segment reverts from `source_row` and not from `deal_reattach_log`.** The log holds
`from_segment`, but it only has rows for the 12 deals that MOVED — the Example Organization ee5ed70d deal was
already on the right client, so it has no log row while its segment was still cleared. Rather
than log a non-move to paper over that, the revert reads `source_row->>'seg'`, which is the
untouched Salesforce import and is present on all 40 deals. `from_segment` stays in the log
because it is the cheaper answer for the 12 and because a log row that records only half of
what changed is a trap for whoever reads it next. The `nullif(..., '')` matters: Example Organization 1370ea72 and
Example Organization efecbbd6 have an empty-string `seg`, which is why they need the second statement.
