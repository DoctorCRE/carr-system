# Spec: party identity, and contact type as two axes

**Status:** proposed 2026-08-02. Design settled with Joe in conversation; nothing built.
**Supersedes:** the party-dedup half of loop #125's gate, which this absorbs and extends.
**Home:** `carr-system/specs/` — it specifies code, and the two-layer rule keeps a
plain-language spec beside its code rather than in the vault.

---

## The problem, in numbers

Identity currently lives on the **role**, not the **person**. So every new role mints a
new person.

- **42** name-identical lead/client pairs sit on **different** parties.
- **0** parties hold more than one role. Not a coincidence — it is the mechanism.
- Dr. Marcus Whitfield is party `909881e7…` as lead L-201 and party `60346cc2…` as client
  C-126. Dr. Daniel Osei likewise. Same humans, two identity rows each.
- Owen Feld is `V-MSC-005` **and** `L-102`, on two parties. One person in 665
  wears two roles, and the model cannot express it.

Three symptoms already logged separately are this one bug:

| Symptom | Actually |
|---|---|
| Duplicate parties (Castillo, Sorbara, Whitfield, Halvorsen, Ferris, Okafor) | What conversion *does* |
| `find Halvorsen` returns `deals: []` while a deal exists | Deal hangs off the client party; search found the lead party |
| Capture coverage ~5% | Touches split across parties that do not know about each other |

Joe's framing was that records get "boxed in by L-163". The ref is the symptom; identity
on the role is the disease.

---

## The model

**One identity per person.** A party. Roles hang off it, each with its own ref, its own
stage, its own dates. Vendors keep `V-` refs and their own lifecycle; clients keep `C-`;
leads keep `L-`. Nothing anyone has written stops resolving — existing refs survive as
historical aliases.

**Contact type is TWO INDEPENDENT AXES, never a combined value.** Joe's design:

| Column | Source | Values |
|---|---|---|
| Client journey stage | stored | lead · prospect · client · past_client |
| Vendor | **derived** | Y if a vendor record exists for this party |
| `contact_type` (display) | **derived** | renders "Vendor + Client", "Past Client", … |

An earlier draft enumerated combinations (`vendor+lead`, `vendor+client`, `past client +
active client`, …). Two axes beat that on three counts:

1. **Open-ended.** Covers pairings nobody listed, without adding enum values or touching
   queries. The enumerated list was already incomplete at ten values.
2. **Holds both lifecycles.** `vendor+lead` as one string cannot say Derrick's vendor stage
   is "aligned" while his lead stage is "outreach active". Two axes can.
3. **Cannot drift.** The vendor Y is derived from the vendor record existing, so marking
   someone a vendor *is* creating their vendor record. A typed flag would be one more field
   able to disagree with reality — exactly the Whitley fault (0045), where `drip_campaign`
   said one thing while three other fields said another.

**Past client implies prospect — by DEFAULT, not automatically.** Joe: *"if a deal ended
badly we may not handle them like a prospect due to the context."* The override already
exists in loop #125's design: `contact_state` on party — `active` · `nurture(cadence)` ·
`paused_until(date)` · `do_not_contact(reason, who_initiated)`. A past client who ended
badly carries the state and the REASON, so the next session knows why without asking.

**Consent is stored; eligibility is derived.** Nothing can infer "this doctor asked not to
receive email" — it is a fact about a person and must survive every stage change. But
"eligible for the prospecting newsletter" is just *not a client, not in a deal, not opted
out*, all of which the database already knows. Storing it means maintaining it, and that is
what 0045 had to repair.

**Candidates stay out of identity.** The 9,860 `prospect_pool` rows carry no `party_id`
today and should not gain one. A party is minted at promotion — `promote-pool` already does
this. Minting 9,860 identities means deduping 9,860 records that may never be contacted,
and every metric drowns: capture coverage would read 2 touches in 10,000.

**The lines are drawn by JUDGMENT, not contact.** Contacting someone is what you do *to* a
lead; it does not promote them. candidate → lead is the promotion decision (party minted).
lead → prospect is deciding they are worth working.

---

## Open question — the only one

**What concretely triggers lead → prospect?** "A judgment made" is honest but not
operational: two people could disagree about which stage someone is in, and it decides
where 207 leads land.

The usual answer is a checkable trigger — a lead who has responded, or who has a known
real-estate event (`est_lease_event` is already collected). Joe's call, not a database
question. Nothing breaks while it is open.

---

## Migration sequence

Order matters, and each step is verifiable before the next.

1. **Introduce party identity + roles.** Additive. Existing refs become aliases; nothing
   is merged yet, nothing stops resolving.
2. **Add `contact_state` on party**, and the derived `contact_type` / vendor-Y views.
3. **Rename `prospect_pool` → `candidate_pool`** (table, the `pool` status value,
   `promote-pool`, `v_pool`) — freeing "prospect" for the stage after lead. Loop #125
   requires this in the SAME migration as the dedup, because both touch the party layer
   and doing them apart means a second window where they disagree.
4. **Merge the 42 pairs — LAST, and one at a time.** `confirm-merge` is human-gated by
   design. This system merged the wrong Castillo once (an import welded Jenna Castillo to
   Jeff Castillo, DMD; corrected within the hour, Jeff now L-158). Never match on name:
   0044's guard had to learn that even a name comparison needs parentheticals stripped
   before it means anything.

**Gate:** agent write attribution (loop #124) covers bulk mutation. Steps 1–3 are schema
and views; step 4 is bulk and sits behind that gate.

---

---

# Part 2: retire the role refs

**Added 2026-08-02 after the first ten merges landed. Joe's conclusion, reached by pulling
on `V-CPA-006` until it came apart.**

## Every role ref encodes a fact that can change

Unpack `V-CPA-006`:

| Part | Asserts | Already stored in |
|---|---|---|
| `V` | this is a vendor | the vendor record existing |
| `CPA` | they are a CPA | `vendor.category_slug` |
| `006` | a unique number | `party.ref` (P-####) |

Three facts, all held properly elsewhere, frozen into a string that cannot update. Joe:
*"theres no need to tag it as V-CPA-006. it would only need to be: 'CPA' or 'Lender' etc
because they are already tagged as a vendor in the database and they already have a unique
id 'P-#'."*

**Proven the same afternoon.** Chris Kellan was recorded as `V-CPA-006` and is a financial
advisor. Adding the category took one row — the ref-table design working as intended — and
his ref still reads CPA. **227 vendor refs encode a category that can now change beneath
them.**

`L-` and `C-` fail the same way, and Joe found that first: *"whats the point of having an L
tag once you've progressed to a client?"* `L-201` asserts "Whitfield is a lead"; `C-126`
asserts "Whitfield is a client". Both are the SAME relationship at different stages, which is
why the ten merges each produced one person holding two refs. `V-` survives a merge with a
lead because vendor genuinely IS a separate relationship — Owen Feld is a vendor and
a lead simultaneously. Lead and client can never coexist.

That asymmetry is the tell: `lead` and `client` are two tables for one journey, and the
duplicate parties were the symptom.

## End state

`P-####` is the only identifier. Everything else is an attribute that can change without
breaking an ID:

```
P-0603 · Justin Danforth        · Vendor  · Financial Advisor · Established
P-0425 · Dr. Marcus Whitfield    · Client
P-0355 · Owen Feld   · Vendor + Lead · Supply
```

- **journey stage** (lead / prospect / client / past_client) — an attribute of the person
- **vendor** — derived from a vendor record existing (Part 1)
- **category, relationship level, disposition** — attributes of the vendor role

## The constraint that makes this safe

**Old refs must keep resolving, forever.** Every email, Salesforce record, dossier line and
document citing `V-CPA-006`, `L-163` or `C-126` has to look up. They become **aliases** —
resolvable, never assigned. Without that this is a break rather than a migration, and it
recreates at scale the exact problem it is fixing.

## Blast radius

Every surface that displays a role ref: the deal board, the 23 dossiers, `registry-audit`
(whose entire pointer-rot check reads `Registry: L-###` out of dossier prose), the
exporters, `lead-registry.xlsx` / `client-roster.xlsx` / `vendors.xlsx`, and the verbs that
mint refs (`new-lead`, `new-client`, `new-vendor`, `promote-pool`).

Bigger than any single migration done on 2026-08-02, and it should not be improvised.

## Sequencing — and why NOT yet

1. **Enrichment first.** Thursday's routine gathers email and cell, which is the second
   signal the 32 remaining name-only pairs need. Merging on a name alone is how the wrong
   Castillo got merged.
2. **Then the remaining merges**, one at a time through `confirm-merge`.
3. **Then collapse `lead` + `client`** into one relationship record carrying a stage.
4. **Then retire the refs**, with the alias table landing in the same migration.

Doing identity work while 32 known duplicates are outstanding is the wrong order: each
unmerged pair would need its aliases reconciled twice.

## Provenance

Every element here came from Joe pushing back on a weaker proposal in the 2026-08-02
session: six loop domains instead of three, the directional vendor-introduction rule, two
axes instead of enumerated combinations, and past-client-as-default rather than automatic.
Recorded because the reasoning is the part worth keeping — the schema is downstream of it.
