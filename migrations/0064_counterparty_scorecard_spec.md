# 0064 — the counterparty scorecard: spec, measurements, and the arguments behind the shape

*Written 2026-08-02 alongside `0064_counterparty_scorecard.sql` and `0063_counterparty_observation.sql`.
Every number below was measured read-only against production before any SQL was written. Where
the brief I was given differs from the data, the data is recorded here and the brief is
corrected.*

---

## 1. What the record actually holds, measured

| thing | count | note |
|---|---|---|
| `deal` | 40 | 6 in phase `negotiation`, 2 closed |
| deals with any `negotiation_round` at all | **1** | Example Organization 163 |
| `negotiation_round` | **2** | landlord round 1, tenant round 1 |
| observed COUNTERS by our side, anywhere | **0** | see §2 |
| `deal_participant` rows | 42 | 41 are `role='lead'` carrying an `actor_id` |
| `deal_participant` rows carrying a `party_id` | **1** | Mehdi, `listing_side`, on that same deal |
| `v_counterparty_history` | 2 rows | both Mehdi, on that same deal |
| `negotiation_claim` | 0 | 0063 creates it empty |
| `availability` / `lease` / `comp` | 0 / 0 / 0 | the market-comparison layer is empty too |

**Correction to the brief I was handed.** It said the 123 disagreements in
`v_vendor_level_suggestion` were "all of them recorded > suggested=0". Measured, they are
**64** of that shape plus **59** where `recorded` is NULL. That distinction is the whole
argument of 0065 and is written up there. It is recorded here only because the same brief also
supplied 0064's premises, and one of its numbers did not survive checking.

**Second correction, smaller.** The brief describes `v_vendor_level_suggestion` as "the 0052
triggers". The live view is **0053's** rewrite of it — 0053 dropped and replaced 0052's
version to stop an unreturned voicemail counting as a relationship, and renamed
`outbound_only` to `attempts_only`. 0065 builds on 0053, not 0052.

---

## 2. The exclusion rule, which is the reason this spec exists

The single negotiation in the record looks like this:

| seq | side | proposed_on | rate | TI | free rent | term |
|---|---|---|---|---|---|---|
| 1 | landlord | 2026-07-30 | $17.00/SF/yr | (null) | 3.0 | 60 |
| 2 | tenant | 2026-07-30 | $17.00/SF/yr | (null) | 3.0 | 60 |

The tenant round is an **acceptance**. The note on it says so: "Tenant ACCEPTANCE of landlord
round 1 terms". Every economic number is identical.

Score that naively and the listing agent conceded **0%** of the movement, held **100%** of his
opening position, and never reversed a claim. He rates a perfect 10 — the hardest negotiator
in the book, on a deal where nobody ever pushed him. That is not a small measurement error. It
is the number being exactly backwards, produced confidently, from real data.

So the rule is absolute: **a deal where our side never countered contributes nothing.** Not a
discounted contribution, not a low-confidence one. It does not enter `n`, it does not enter any
average, it does not enter the field the curve is drawn over. And it lives in the view, not in
a doc, because a rule in a doc is a rule that the second consumer of this view will not apply.

### How "countered" is defined, and the trap in defining it

Countering is defined structurally: **our side filed a round, there was already a position on
the table from their side, and ours differs from it on at least one of rate / TI / free rent /
term.**

The trap is `escalator`. The two production rows differ in escalator *text* — the landlord's
says `3% annual (to ~$19.13/SF by year 5)` and the tenant's says `3% annual` — while agreeing
on every number. A movement test that read prose would have classed this acceptance as a
counter and the whole exclusion would have failed on the only deal it had to catch. The test
therefore reads **numbers only**, and `moves_off_standing` in `v_negotiation_position` says so
in a comment so nobody "improves" it later.

Verified against production: with the rule as written the deal is excluded; with escalator
included in the comparison it is not.

### The two other exclusions

- **No `listing_side` party recorded.** Movement cannot be attributed to anyone. Reported as
  `exclusion_reason`, not dropped silently.
- **More than one live `listing_side` party.** Two agents on one file; we cannot say which of
  them held the line. Also excluded, also reported. Migration 0060 (another seat) is
  backfilling `deal_participant.party_id`, so this case becomes reachable.

A `referring_agent` is deliberately **not** treated as a counterparty here, even though
`v_counterparty_history` (0026) surfaces both roles. A referring agent sits on our side of the
table. 0026 is right for its purpose and wrong for this one.

---

## 3. Store absolutes, render relatives

Joe's ruling, and the reason nothing in 0064 is a table.

A stored curved grade is wrong the moment the field changes. If the toughest agent in the
panhandle retires, a stored curve silently re-rates everyone who is left, and no one can
afterwards distinguish "this agent got softer" from "the field got softer around him". The
stored number would be a fact about a population that no longer exists.

So:

- **`hardness_absolute`** — 0-100, a fixed formula over that counterparty's own negotiations.
  It does not reference any other counterparty. It is the durable measurement.
- **`threat_rating`** — 1-10, `percent_rank()` over the *current* field, evaluated at read
  time. A new toughest agent becomes the new 10.0 and not one stored value anywhere changes.
- **`field_n`** — how many counterparties the curve was drawn over, exposed on every row,
  because a 10.0 out of a field of three is a different claim from a 10.0 out of forty.

`threat_rating` is NULL when `field_n < 3`. A percentile over one person is a number about
nobody, and `percent_rank` would cheerfully return 0 (rendering as 1.0, "easiest in the
market") for a single row.

### The composite formula, and what is deliberately outside it

| component | weight | source |
|---|---|---|
| `c_asym` — mean share of total movement that was OURS | 45 | Joe: the single best measure |
| `c_grip` — 1 − mean fraction of their own opening they gave up | 20 | |
| `c_bluff` — 1 − reversal rate over **falsifiable** claims | 15 | |
| `c_hold` — held ÷ tested across rate, TI, free rent | 20 | |

Weights **re-normalise over whatever components have data**, so a missing component is absent
rather than silently scored zero — a counterparty with no claims logged is not thereby a
perfect bluffer or a hopeless one. Fewer than two live components yields no composite at all.

**Excluded from the composite on purpose, though reported:**

- `avg_rounds_to_settle`. Directionally ambiguous. Many rounds can mean they were immovable, or
  that we ground them down over six weeks. Nothing in the record distinguishes those, and
  putting an ambiguous signal behind a decisive-looking number is how a scorecard starts lying.
  It is exposed because it genuinely informs a human reading the row.
- **term** and **escalator**, in the category profile. A longer term is not obviously worse for
  a tenant, so "conceded" has no meaning there. Their profile vocabulary is
  `fixed`/`flexible`/`mixed` rather than `holds`/`concedes`/`mixed` so the difference is
  visible in the output itself rather than buried in this document.

---

## 4. Store observations, compute patterns

No column anywhere in 0063 or 0064 holds a characterisation of a named human being. There is no
`bluffs`, no `aggressive`, no `reasonable`.

What is stored (0063) is: a claim of a given class was made, on a given round, on a given date,
optionally with the number that was named and optionally with their words. What is computed
(0064) is whether that same side's own later rounds contradicted it.

| class | falsifiable | reversed when |
|---|---|---|
| `finality` | yes | they later file a round improved for us on rate, TI or free rent. Re-sending the same numbers is not a reversal. |
| `authority` | yes | they later propose below the stated floor (or below the claim round's own rate, when no floor was named) |
| `walk_away` | yes | any later round exists. Continuing to negotiate *is* the contradiction. |
| `deadline` | yes | they file a round dated after `expires_on` |
| `competing_interest` | **no** | never — see below |

**"Another tenant is looking" is not falsifiable and never touches a score.** We can never
observe whether the other tenant existed. It is loggable, because seeing the tactic in a
history is useful to a human, and it is excluded from every number by
`negotiation_claim_type.falsifiable = false`, which `v_counterparty_bluff` **joins on**. The
rule lives in the data. 0052 hardcoded a kind list into a view and 0053 had to rewrite the view
to correct that list; marking a future claim type unfalsifiable here requires no view edit.

**Deadlines get no claim row.** `negotiation_round.expires_on` already records the deadline and
a later round already falsifies it. A `deadline` claim row would be a second home for one fact
— the 0045 fault, and the same reasoning by which 0019(g) refused to create `job_config` beside
`system_config`. 0063 marks the class `derived=true` and a composite foreign key makes the
insert **impossible** rather than merely discouraged; 0063's guard proves the refusal by
attempting it.

### Why a child table rather than the `claimed_firm` boolean the brief asked for

Three reasons, in order of weight.

1. **A boolean cannot carry the class, and the class is the test.** Finality is falsified by a
   later concession, authority by a later price below a named floor, walk-away by the mere
   existence of a later round. Three different queries. One boolean answers none of them.
2. **A side makes several claims at once.** "This is our best and final, the owner won't go
   below eighteen, and we have another tenant looking" is one sentence and three claims. A
   single enum column keeps one and discards two — the 0045 fault (one slot doing several jobs)
   arriving pre-built.
3. **The vocabulary needs columns of its own.** `falsifiable` and `derived` are properties of
   the claim class, and putting them in a ref table is what lets the score read the rule
   instead of carrying a copy of it. This is the standing 0017 pattern: `activity_kind`,
   `party_link_kind`, `participant_role` and `vendor_disposition` all live this way.

No convenience `claimed_firm` boolean is added alongside. Two homes for one fact is the thing
being avoided, not a thing to add for ergonomics.

---

## 5. n, bands, and refusing to answer

| n (qualifying deals) | band | what is emitted |
|---|---|---|
| 0-2 | `unrated` | no `threat_rating` at all; raw round counts, `deals_seen`, and `why_no_number` |
| 3-5 | `provisional` | rating plus `rating_low` / `rating_high`, and the word provisional |
| 6+ | `rated` | rating plus a narrower band |

`n` is on every row. Counts are rendered as **"reversed 4 of 5"**, never "80%" — at this n a
percentage asserts a precision that does not exist.

**The band is a spread heuristic and is labelled as one in the column comment.** It is
`6.0/sqrt(n)` clamped to `[0.5, 4.5]`: ±3.5 at n=3, ±2.4 at n=6, ±1.7 at n=12. It is **not** a
confidence interval. There is no distributional model here to build one from, and a
statistical-looking band on n=4 would be exactly the false precision the bands exist to
prevent. If someone later wants a real interval, the honest route is a bootstrap over the
per-deal `our_share_of_movement` values, which is a change to make when there are enough of
them to bootstrap.

---

## 6. Confounders: tagged, never corrected for

Joe named two.

**Submarket condition.** A landlord in a tight submarket concedes nothing because he does not
have to; scoring that as toughness credits the market to the man. 0063 adds a three-value tag
(`soft` / `balanced` / `tight`, signed −1/0/+1) on the **round** rather than the deal, because
a submarket demonstrably moves during a negotiation and a deal-level column freezes whoever
answered first. `v_negotiation_deal` reads the latest non-null value on the deal, so it need
only be recorded once. The scorecard reports `avg_submarket_tightness` and
`deals_condition_unrecorded` **beside** the score and subtracts nothing: with n in single
digits, any correction would be invented. The honest form is "8.4, and all four of those deals
were in a tight submarket", which is what those two columns say.

Three values, not five: a human applies soft/balanced/tight consistently from memory, and a
five-point scale invites false precision on a judgement nobody measures.

**The counterparty's client's motivation.** Deliberately **not** added as a column, and this is
the one place I declined something Joe listed. "The landlord is motivated" is a characterisation
of a third party we never speak to — precisely the class of fact ruling #2 exists to keep out.
The falsifiable version of the question is **time on market**, which the schema already answers
through `availability.available_on` and `availability.observed_at`. Those tables hold 0 rows,
and that emptiness is the honest blocker. A subjective "motivated" flag would be cheaper, would
fill in immediately, and would be wrong.

---

## 7. Comparability, because a wrong subtraction is worse than none

`rate_basis` spans six values across two incompatible families (0022): four rent bases and two
purchase bases. `rate_norm_sf_yr` is generated only for `usd_sf_yr` and `usd_sf_mo`; 0022 left
it NULL for purchase bases on purpose, because "a price is not a rent and must never land in a
rent comparison".

`v_negotiation_position` decides comparability **per deal, once**:

1. every rated round on the deal carries a `rate_norm_sf_yr` → compare on the norm;
2. otherwise every rated round shares one identical `rate_basis` → compare on the raw amount;
3. otherwise the rate axis is NULL for that deal and the deal contributes no rate metric.

TI is comparable only when the deal uses a single `ti_basis` — `usd_total` and `usd_sf` are
different questions.

---

## 8. Standing assumption, and its reopen condition

**Our side is always `tenant` or `buyer`; theirs is always `landlord` or `seller`.** CARR
represents tenants and buyers exclusively and never landlords or sellers. This is what makes
every metric's orientation unambiguous — their side always wants the rate higher, ours always
wants it lower, on every deal — and it is why `camp` is derived from `side` with no per-deal
configuration.

**Reopen condition:** if CARR ever takes a listing-side engagement, these four views are wrong
in a way no guard in 0064 can catch, because a landlord-rep deal is structurally
indistinguishable from a tenant-rep deal in `negotiation_round`. The fix at that point is a
per-deal `our_side` column, not a smarter view.

---

## 9. What these views return today, and what has to happen before they return anything else

Today, `v_counterparty_scorecard` returns **one row**: Mehdi, `deals_seen` 1, `n` 0,
`n_band` `unrated`, `threat_rating` NULL, `hardness_absolute` NULL, `category_profile` `{}`,
`why_no_number` = *"our side never countered on any recorded deal — this counterparty has not
been tested and no number is available"*. `v_counterparty_bluff` returns **zero rows**.

That is the system working. A view that emits a confident number from zero counters is the
exact failure this whole design exists to stop.

**Three things stand between this schema and a usable scorecard, and none of them is more SQL:**

1. **`record-counter` has to be used on both sides of a live negotiation.** Two rounds in a
   book of forty deals, six of which are in phase `negotiation`, is the binding constraint.
   Nothing here helps until rounds are logged as they happen — and unlike a rate, a claim
   cannot be reconstructed afterwards from an email.
2. **The verb that writes `negotiation_claim` and `negotiation_round.submarket_condition` —
   already written, and it must deploy alongside 0063.** The seat owning
   `mcp-server/src/tools.js` has extended `record-counter` with a `submarket_condition`
   argument and a `claims[]` array (plus `validateSubmarket`, `validateClaimType`,
   `require0063`). Checked line by line against 0063: the insert column list, both ref-table
   shapes, all five claim slugs and all three submarket slugs match exactly, and the verb
   refuses a `derived` class itself so the composite FK violation reaches the caller as a
   sentence rather than a constraint name. It also degrades safely if the migration has not
   landed — `require0063()` returns `migration_not_applied`, and `submarket_condition` only
   joins the INSERT column list when supplied. Either half can go first; both must go.
3. **`deal_participant.party_id` has to be populated for listing agents.** One row today.
   Migration 0060 (another seat) is backfilling it; until then almost every deal would be
   excluded for "no listing_side party recorded" even if its rounds were logged.

---

## 10. Open questions left for Joe

1. **Should a lost deal count differently?** A counterparty who never moved and we walked away
   is arguably the hardest of all, but our walking away also means the negotiation ended
   untested at the margin. Today `deal.outcome` is not read at all by these views. It is a real
   modelling question and I did not want to answer it by default.
2. **Listing-agent turnover mid-negotiation.** `deal_participant` carries `from_at`/`to_at`, but
   those record when *we noted* the role, not when the person held it. A deal that changes
   listing agents halfway through is currently excluded as ambiguous rather than split. Splitting
   it would need role windows that actually mean something.
3. **Is 45/20/15/20 the right weighting?** It encodes Joe's statement that gap asymmetry is the
   single best measure and nothing more. It cannot be validated against anything until n is real,
   and it should be revisited once roughly ten qualifying negotiations exist rather than tuned now.
4. **Does the scorecard belong on the Deal Room dashboard?** It is internal-only by nature — the
   same [D5] posture `v_counterparty_history` carries. It must never reach a client-facing
   surface, and no exporter reads it today.
