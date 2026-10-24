---
name: council
description: >
  Runs the Deal Council: the panel runner that assembles one pre-brief, fans a deal document or a
  stuck deal out to the advisor chairs in parallel, and merges what comes back into one
  severity-ranked packet for Joe. Two modes. REVIEW mode is the pre-handover critique of anything
  client-bound (an LOI or RFP draft, a lease-comparison sheet, a purchase-vs-lease recommendation,
  a search report's top picks, a deal presentation) after drafting and after the writing-lint gate,
  before Joe or the client sees it. TROUBLESHOOT mode is the crux discipline applied to a live deal
  that is stuck: refuse the first framing, map candidate causes completely, then name the crux,
  then options. Invoke it as /council review <artifact>, /council troubleshoot <deal>, or
  /council <chair> <mode> <target> for a single seat. Also trigger on "run the council," "run the
  panel," "pressure-test this LOI before it goes out," "review this packet," "would this survive
  the client's advisor," "this deal is stuck," "the landlord went quiet," "why has this stalled,"
  "what will they counter," "convene the chairs." It owns the panel-level close that no individual
  chair can produce: naming which chair NOBODY sat in. It writes nothing during a run and it never
  fires a record verb mid-panel; logging is proposed to Joe with the call pre-written and waits for
  his yes. Do NOT use it to draft or rewrite the document (that is the drafting session's job, and
  the maker never checks its own work). Do NOT use it for the writing-quality pass (that is
  run.sh lint plus the writing-audit skill, both of which run BEFORE this). Do NOT use it on
  marketing copy, on a lead, or on a closed deal.
---

# The Deal Council runner

> **Doctrine ownership.** `CARR AI/DNA/Deal Management/playbooks/negotiation.md`, the Deal Council
> section, is the source of truth for why the council exists and when it fires. The chair files in
> `.claude/agents/` are the executable form of the lenses. This skill is the runner that sits above
> both. If this file and negotiation.md ever disagree, negotiation.md wins.

## Why this is a skill and not a seventh chair

Two reasons, both structural.

**The close cannot live in a chair.** The council's last line names the chair nobody sat in. No
individual chair can produce that line, because no chair knows which others ran. Only the seat that
dispatched the panel knows the roster it chose from and the roster it used. Leaving that job in a
README makes it depend on a human remembering to read the README. A skill loads when it is invoked,
so the close becomes part of the machinery.

**The runner needs Joe in the loop.** He describes the situation, he rules on every finding, he
decides what gets logged. The house rule: a role that needs Joe in the loop is a skill, a role that
produces a report is an agent. The chairs produce reports. This runs the room.

## Invocation

| Form | What it does |
|---|---|
| `/council review <artifact>` | Full review panel on a document or packet. Artifact can be a path or pasted text. |
| `/council troubleshoot <deal>` | Full troubleshoot panel on a stuck deal. Deal can be a deal ID, a client name, or a plain description. |
| `/council <chair> <mode> <target>` | One seat only. Chair is `lender`, `contractor`, `attorney`, `skeptic`, `landlord`, or `listing-agent`. Mode is `review` or `troubleshoot`. |
| `/council verify <finding #>` | Second-round adversarial check on one already-produced finding. See the second round below. |
| `/council` | Bare. Ask Joe which mode and what the target is, then proceed. Two questions maximum, per ux-doctrine law 6. |

Optional flags, both modes:

- `--chairs lender,attorney` overrides the routing table. Say in the report that routing was
  overridden and which chairs the table would have called, because the ones dropped go straight
  into the panel empty-chair close.
- `--no-prebrief` skips the record reads when Joe already has the facts in front of him. The
  pre-brief still gets assembled from what he pasted, and it still carries the record-limits block.

Natural language reaches the same place. "Run the council on this LOI" is `/council review`.
"This one is stuck, what is going on" is `/council troubleshoot`.

## The roster: six chairs

| Chair | Agent file | Review lens | Troubleshoot branch |
|---|---|---|---|
| Lender (CPA lens on purchase decks) | `council-lender` | Would this finance | Financing (F1 to F8, F-other) |
| Contractor | `council-contractor` | Is the build-out math real | Build-out, cost, schedule (B1 to B8, B-other) |
| Attorney | `council-attorney` | Where is the redline bait | The document itself (D1 to D8, D-other) |
| Skeptic (the Kevin Tuttle seat) | `council-skeptic` | Would this survive the client's own advisor | Client cold feet (P1 to P7, P-other) |
| Landlord | `council-landlord` | Why would the owner refuse this | Owner economics (O1 to O8, O-other) |
| Listing agent | `council-listing-agent` | What will the other side counter and exploit | The broker and the channel (A1 to A8, A-other) |

**Six, not seven.** The marketing chair was pulled from the roster on 2026-08-02 and staged at
`CARR AI/_to_delete/council-marketing-pulled-2026-08-02/`. It fired at deal-won, which is not a
panel event: no document under review, no counterparty to model, no other chair to run beside. Both
halves of its job, the post-mortem and the is-there-a-story check, moved into `marketing-coo.md` as
a deal-won trigger. Never route to it from here. If a session or a stale document claims seven
chairs, verify against the actual `.claude/agents/` directory before believing it.

All six run on **Opus**, per Joe's ruling. A wrong lender or attorney finding costs money and a
wrong counterparty finding costs credibility, so the tiering doctrine's delegate-down default does
not apply here. Do not drop a chair to Sonnet to save cost without saying so; that is Joe's call.

## Before the panel runs: four gates

1. **The maker never checks its own work.** If this session drafted the artifact, it runs the
   panel and does not sit in a chair. Never inline a chair's reasoning to save a spawn.
2. **Review mode fires after the writing gate.** `run.sh lint <draft> --surface <surface>` runs
   first, and the writing-audit skill if the piece needs a voice pass. The council checks the deal,
   not the prose. If lint has not run, run it or tell Joe it has not.
3. **Write-nothing for the whole run.** No record verb fires between the moment the panel is
   dispatched and the moment Joe rules. The chairs cannot write because they hold `Read, Grep,
   Glob` only. The runner can write and must choose not to.
4. **Tenant and buyer side only.** The landlord and listing-agent chairs model the counterparty in
   order to beat them. Nothing the panel produces is ever usable by an owner or a seller.

## Step 1: assemble the pre-brief ONCE

This is the runner's most important mechanical job. **The chairs cannot fetch anything.** They hold
`Read, Grep, Glob` and cannot call an MCP tool at all, which is what makes write-nothing structural
rather than a promise. So the runner gathers everything once and pastes the identical block into
every chair prompt. Cheaper than six sets of reads, and all six reason from the same facts, so a
disagreement between two chairs is a real disagreement about the same evidence.

**What to gather:**

| Source | Call | Notes |
|---|---|---|
| The deal's own history | `catch-me-up` on the deal | Flagged broken 2026-07-31. If it fails, say so in the pre-brief and fall back to a direct read of the deal file plus Joe's description. |
| The other side | `counterparty-history` on the listing agent, and separately on the owner | Almost always returns nothing. That result is itself part of the pre-brief. |
| Stage and terms | `deal-board` | Pull the deal's row: stage, value, owner, next action. |
| The artifact | Read the document in full, or take Joe's pasted text | Review mode. Paste the full text into every chair prompt, not a summary. A chair asked to quote a clause needs the clause. |
| Joe's own description | Ask him | Troubleshoot mode. This is the primary input, not a supplement. |
| Anything else in the vault | `~/carr-system/run.sh retrieve "<question>"` | Standing rule for any file beyond the always-read core. Do not hunt by hand. |

**The pre-brief block, pasted verbatim into every chair prompt:**

```
=== PRE-BRIEF (assembled by the panel runner, identical for every chair) ===
Deal: <id, client, vertical, stage>
Artifact under review: <type, and the full text below> | Situation: <Joe's own words>
Vertical: <dental | medical | vet | vision | chiro | PT | not stated>
Counterparty: <listing agent name, owner name/type, or "unknown">

RECORD PULLS
catch-me-up: <output, or "call failed / returned nothing">
counterparty-history (agent): <rows, or "zero rows">
counterparty-history (owner): <rows, or "zero rows">
deal-board row: <stage, value, next_action, last update>

RECORD LIMITS, verified 2026-08-02. Read these before you state your n.
- Staleness in the record is NOT evidence. Every open deal reads 2-3 days since update because
  that is when the book was imported. Do not treat a date as evidence of neglect.
- critical_date is empty across the board. An empty date field means nothing was entered.
- 36 of 38 open deals carry no open next_action. Absence there is a capture gap.
- negotiation_round holds 2 rows, on one deal, where the tenant ACCEPTED round one. There are
  ZERO observed counters anywhere in the record.
- v_counterparty_history holds 2 rows for one person. Counterparty data is effectively nil.
- Email silence counts as evidence only on an active deal from LOI onward. Joe and Dell run real
  contact over text and phone.
- Weekends are not workdays for Joe or Dell. Never read weekend silence as drift.
- Absence in a partial collection is not absence. If a fact is missing here, say it is missing
  from THIS pull, never that it does not exist.

MODE: <review | troubleshoot>
YOUR BRANCH (troubleshoot only): <the branch this chair owns>
=== END PRE-BRIEF ===
```

The record-limits block is not optional and does not get trimmed for length. It is the single thing
standing between the panel and six chairs confidently diagnosing an import artifact.

## Step 2: route

**Review mode routing table.** Route by what the artifact touches. The name on the file is not the guide. A lease-comparison sheet carrying a TI number gets the contractor chair.

| Artifact | Chairs |
|---|---|
| Lease LOI or RFP draft | Lender, Contractor, Attorney, Skeptic, Listing agent |
| Purchase LOI or purchase deck | Lender (CPA lens on), Contractor, Attorney, Skeptic, Landlord |
| Lease-comparison sheet | Lender, Attorney, Skeptic |
| Purchase-vs-lease recommendation | Lender (CPA lens on), Attorney, Skeptic |
| Search report top picks | Contractor, Skeptic, Landlord |
| Renewal recommendation | Lender, Skeptic, Landlord, Listing agent |
| Counter or redline response | Attorney, Listing agent, Landlord, Lender |
| Deal presentation to the client | Skeptic, Lender, Attorney |
| Work letter or delivery-condition exhibit | Contractor, Attorney |
| A closed and won deal | No chair. This goes to `marketing-coo` on its deal-won trigger, Part Two of the agents README. |

Additive triggers that pull a chair in regardless of row:

- Any TI allowance, shell condition, delivery date, or commencement date pulls **Contractor**.
- Any personal guarantee, rate, term, or debt-service figure pulls **Lender**.
- Any clause language at all pulls **Attorney**.
- Anything that goes to the other side pulls **Listing agent**.
- Anything a client will read pulls **Skeptic**. The skeptic seat works at n equals zero by
  design, so there is never a data reason to skip it.

**Troubleshoot mode routing.** All six chairs, every time. The cause map has to be complete before
anything gets diagnosed. A branch nobody enumerated is a hole in the map, and dropping a chair to
save a spawn puts one there.

*(Counting note, so the two documents agree. The README's branch table lists FIVE branches because
the landlord and listing-agent chairs share the other side of the table between them, one on owner
economics and one on the broker and the channel. Six chairs, five branches, the same panel.)*

Every chair the table named and you did not run goes into the panel empty-chair close by name.

## Step 3: dispatch

Spawn every chair in **one message** so they run in parallel. Never sequentially.

Per-chair prompt:

```
Mode: <review | troubleshoot>
Chair: <name>
[troubleshoot] Your branch: <financing | build-out | document | other-side | owner economics | client>

<the full PRE-BRIEF block, verbatim>

<the full artifact text, review mode>

Return your standard output shape. Every finding tagged [doctrine] or [inference]. State your n
first. End with your EMPTY CHAIR line.
```

Do not paraphrase the artifact and do not hand a chair a summary. A finding about a clause the
chair did not read is an inference about a document it did not open.

## Step 4: the synthesis contract

This is the job the runner owns, and it has seven parts. All seven run on every panel.

**1. Deduplicate.** Several chairs will independently flag the same clause. The HVAC-into-TI move
gets caught by the contractor, the attorney, and the listing agent on the same LOI. Merge them into
one finding, keep the sharpest wording of the risk, and list every chair that raised it. Multiple
chairs converging raises severity, and it gets recorded as one finding: `raised by: attorney, contractor,
listing-agent`.

**2. Rank across the whole panel.** Severity is panel-wide, not per chair. A material finding from
the contractor outranks a minor one from the attorney. Sort critical, then material, then minor,
and inside each tier put the convergent findings first.

**3. Surface conflicts explicitly.** When two chairs disagree, **that disagreement is the finding**.
Never average it, never pick the one that reads better, never drop the weaker one without saying so. The
attorney wanting an unambiguous landlord HVAC obligation against the landlord chair saying an owner
reads that and stops reading is not noise. It is the actual tradeoff Joe has to decide, and it
belongs in the packet as its own numbered item with both positions stated in full and the decision
named. Format:

```
CONFLICT 1: <the axis in one line>
  <Chair A> says: <position, with its basis tag>
  <Chair B> says: <position, with its basis tag>
  What Joe is deciding: <the tradeoff, plainly>
```

**4. Audit the basis tags.** Every finding arrives tagged `[doctrine]` or `[inference]`. An
untagged finding does not enter the packet. A finding tagged `[doctrine]` that cites nothing
checkable gets demoted to `[inference]` by the runner and the demotion is noted. A contractor run
with no `[doctrine]` tags at all means the vertical guide was never opened, so re-run that chair.

**5. Provenance inline on every number.** Any figure that reaches Joe carries where it came from in
the same line: the document, a named Reference guide, a record row, a comp, or the chair's own
estimate. A number with no provenance gets pulled out of the packet and listed as a question
instead.

**6. Compose the panel verdict.** Every chair now returns one of four words on its own bar: BLOCK,
REVISE, PASS, or INSUFFICIENT-N. The runner does not re-judge those, and does not average them. It
applies this table, in this order, and stops at the first row that matches:

| Condition across the seated chairs | Panel verdict |
|---|---|
| Any chair returned BLOCK | **BLOCK** |
| No BLOCK, and any chair returned REVISE | **REVISE** |
| No BLOCK, no REVISE, and any chair returned INSUFFICIENT-N | **INCOMPLETE** |
| Every seated chair returned PASS | **PASS** |

Three rules that make the table mean something:

- **INSUFFICIENT-N never disappears into a PASS.** If a chair could not look, the panel has not
  cleared the artifact, and INCOMPLETE says so honestly. Naming which chairs were blind, and on
  what, is the point. A panel that reports PASS while a seated chair saw nothing is precisely the
  rubber stamp the bars were added to prevent.
- **The runner cannot overturn a chair's verdict.** It may demote a basis tag under part 4 and it
  may re-run a chair that clearly never opened its source, but it does not convert a BLOCK to a
  REVISE because the finding looks soft to the runner. Disagreement with a chair's verdict is a
  CONFLICT item under part 3, surfaced for Joe.
- **A verdict is not a decision.** BLOCK means the panel is telling Joe not to send it as written.
  Joe still rules, per finding, exactly as he does today. The verdict changes what he is ruling
  against, not who rules.

Carry the four chair verdicts into the packet header verbatim, so Joe can see the composition
rather than trusting the arithmetic.

**7. The panel-level empty-chair close.** See below. This is the last thing written and the reason
the skill exists.

## The empty-chair close, at panel level

Every chair ends its own output with an `EMPTY CHAIR:` line naming what its single pass did not
cover. Those are chair-level gaps. The runner owes a different and larger thing.

The close has three parts, in this order:

```
EMPTY CHAIRS

Not seated: <every chair the routing table named or could have named that did NOT run, each with
  one line on what its absence leaves uncovered. If routing was overridden, say so.>

Nobody's chair: <the angle no seat on the roster covers at all. The original doctrine's own
  examples: the patient-volume angle, the spouse's risk tolerance, the landlord's bank. This is the
  hardest line in the packet and the most valuable, because it is the gap the roster itself has.>

Chair-level gaps (the union of what each seated chair reported):
  - <chair>: <its EMPTY CHAIR line>
  - ...
```

Never collapse the three into one list. "Not seated" is a routing decision Joe can reverse in
thirty seconds. "Nobody's chair" is a roster limit he cannot. They are different kinds of gap and
they get different treatment.

The close travels with the document. When Joe hands the packet to the client, the known gap goes
with it instead of hiding.

## Troubleshoot mode: the ordering is non-negotiable

This is `/crux` applied to a deal. The sequence never reverses, and the runner enforces it in the
report even when a chair volunteers a diagnosis in paragraph one.

**1. Refuse the first framing.** State plainly that the presenting complaint is a symptom. "The
landlord went quiet" is not a problem, it is an observation with at least a dozen causes across
three branches. Write the refusal into the report as its own line so Joe sees the move being made.

**2. Map candidate causes completely, before diagnosing anything.** Assemble all six branches into
one map. Every branch keeps its residual bucket (`F-other`, `B-other`, and so on) because a branch
enumerated without a residual is a branch claiming to be exhaustive when it is not. Print the whole
map. Do not prune it to the interesting ones. The completeness of the map is what makes the
diagnosis worth anything.

**3. Then name the likely crux.** One cause, or a named pair, in plain language. Tagged. If the
evidence does not support naming one, say that instead of manufacturing a favorite.

**4. Then the ONE discriminating question.** Panel-level, across the panel's top two candidates,
not each chair's top two. This is the finish line: a troubleshoot run that produces a ranked list
and no discriminating question has not finished. The shape to aim for is a single question one
person can answer in one sentence that moves probability hard between the top two. "Has this been
in front of the owner yet" separates three listing-agent branches in one answer.

Give Joe the question in the words he would say it in, and name who he asks.

**5. Then options, tied only to causal levers.** No option that does not act on a named cause. An
option list that would look the same whatever the diagnosis is a to-do list wearing a costume.
Each option carries the cause it acts on and what it costs.

**Input honesty.** The record cannot see a stuck deal. Troubleshoot input is Joe's own description
plus the pre-brief, never an automated staleness reading. If Joe's description is thin, ask two
questions before dispatching. Two, not six.

## The optional second round

After the panel returns, the runner may spawn **one** focused verifier against **one** finding.
This is adversarial verification, not a rerun.

**Worth it when all three hold:**

- The finding is critical or high material, meaning it would change the document or the advice.
- It is contested: two chairs disagree, or one chair asserts something the pre-brief does not
  support, or a `[doctrine]` tag looks thin.
- A second pass can resolve it, because the resolving evidence exists in a Reference
  guide, the document itself, or a record row.

**Waste when any of these hold:**

- The finding is inference at n equals zero on a counterparty. A second inference does not verify
  the first, it launders it. Both counterparty chairs run at n equals zero by default, so most of
  their output is out of scope for verification.
- The disagreement is a genuine tradeoff rather than a factual dispute. That belongs in the
  CONFLICT block for Joe to decide, and a verifier asked to settle it picks a side.
- Joe can answer it faster than an agent can. If the resolving fact is in his head or one phone
  call away, ask him.
- More than two findings look like candidates. That means the pre-brief was thin, so fix the
  pre-brief and rerun the panel rather than patching findings one at a time.

Dispatch the verifier as the chair whose subject matter owns the finding, on Opus, with the same
pre-brief plus the contested finding and the instruction to argue against it. Report the result as
`VERIFIED`, `WEAKENED`, or `UNRESOLVED, and here is what would settle it`.

## Handing it to Joe

**Every finding is addressed or consciously waived before handover, and a waive is a decision said
out loud, never a skip.** That is the standing doctrine and the runner carries it. The packet ends
with a ledger Joe fills in, one line per finding, and nothing goes to the client with a blank line
in it.

The packet ends in **one action**, per ux-doctrine law 3, with the literal words. Not an open
question.

### Output shape

```
DEAL COUNCIL | mode: <review | troubleshoot> | <deal / artifact>
PANEL VERDICT: <BLOCK | REVISE | INCOMPLETE | PASS>
Chair verdicts: <lender: PASS | contractor: REVISE | attorney: BLOCK | ...  every seated chair, verbatim>
Blind chairs: <any chair that returned INSUFFICIENT-N, and what it needed. "None" if none.>
Chairs seated: <list>   Model: Opus   Run: <date>
Pre-brief basis: <what the record returned, in one line, including what failed>

[troubleshoot only]
REFUSED FRAMING: <the presenting complaint, named as a symptom>
CAUSE MAP: <all six branches, complete, each candidate tagged>
LIKELY CRUX: <one cause or a named pair, tagged>
THE ONE QUESTION: "<verbatim, in Joe's words>"  Ask: <who>
OPTIONS: <each tied to a named cause, with its cost>

FINDINGS, ranked across the panel
1. [critical] [doctrine] <the risk>
   Raised by: <chairs>
   Where: <quote or section>
   Client question it answers: "<in the client's words>"
   Fix: <the specific change, in document language>
   Joe's ruling: [ ] fix  [ ] waive, because ______
2. ...

CONFLICTS
CONFLICT 1: <axis>
  <Chair A> says / <Chair B> says / What Joe is deciding

EMPTY CHAIRS
Not seated: ...
Nobody's chair: ...
Chair-level gaps: ...

PROPOSED FOLLOW-UPS (nothing has fired; these wait for your yes)
<pre-written verb calls, see below>

NEXT: <one action, with the literal words>
```

## Proposed follow-ups, never fired

Doctrine: significant catches log to the deal file, dated, because a council catch is the evidence
the learning loop feeds on. The runner **proposes** that log with the call pre-written and fires
nothing mid-panel. Joe rules first.

| What happened | Proposed call |
|---|---|
| **EVERY panel, without exception** | `log-decision` recording the PANEL VERDICT and each chair's verdict verbatim, titled with the artifact. See below — this one is not conditional on anything interesting happening. |
| A significant catch | `log-activity` on the deal, dated, one line naming the catch and the chair that raised it |
| Joe waives a finding | `log-decision`, the finding, the waive, and his reason in his words |
| A newly observed counterparty move | `log-activity`, event only, with a date. Never a characterization. The listing-agent chair returns these in its CAPTURE block already formatted. |
| An actual counter arrived | `record-counter` |
| The panel opened an unowned commitment | `add-loop` |
| A next step got named | `set-next-action` |

Write each call out fully so Joe can say yes and the session executes it. Note in the proposal that
each write needs a fresh idempotency_key and a base_version from a fresh read, and that a
`version_conflict` or `needs_confirm` goes back to Joe rather than getting retried.

### Logging the verdict is proposed on EVERY panel, including the boring ones

The other rows in that table fire when something notable happens. This one fires always, and the
reason is a gap found on 2026-08-09: **nothing in the system records what our review path passed.**
The `document` table held five rows, all draft, `lint_passed` and `leak_check_passed` both null. So
the question "is the council actually catching things, or is it a rubber stamp with six chairs"
could not be asked, because there was no corpus of passed artifacts to grade against. Loop #295
exists to answer that question and cannot run until this row accumulates.

That makes an all-PASS panel the MOST important one to log, not the least. A panel that found
nothing is precisely the row the measurement needs: it is the claim being tested. Logging only the
interesting panels would build a corpus of catches and no corpus of clean passes, which measures
nothing at all.

Propose it in this shape, and price it when a later grading pass gives you the numbers:

```
log-decision
  title:    "Council verdict: <artifact> — <PANEL VERDICT>"
  rationale: "<every seated chair and its verdict verbatim>. Findings: <count by severity>.
              Blind chairs: <any INSUFFICIENT-N and what it needed>. Joe's rulings: <fix/waive per finding>."
  about:     "<the deal ref, when the artifact belongs to one>"
```

Joe still rules first and nothing fires mid-panel. What changes is that "nothing notable happened"
stops being a reason to write nothing down.

If a council catch produces a standing lesson ("always spell HVAC responsibility in the first
draft"), propose an edit to the relevant CLAUDE.md, AGENTS.md or skill in a PR with the trade-off; proposals bind nobody until merged.

## How this degrades when the record is thin

The record is thin right now. The panel is built to run anyway, and honesty about the thinness is
what makes the output usable.

| Condition | What the runner does |
|---|---|
| `catch-me-up` fails | Say so at the top of the pre-brief. Fall back to a direct read of the deal file plus Joe's description. Every chair states its n against that. |
| `counterparty-history` returns zero rows | Expected. Both counterparty chairs open with "no observed data, n equals zero, generic pressure-test with no read on this individual." The runner repeats that line in the packet header so Joe does not have to find it inside two chair outputs. |
| No vertical stated | Ask Joe. If he does not know yet, the contractor chair runs universal items only and says so, and the missing vertical becomes a finding. |
| Deal not in the record at all | Run on Joe's description alone. Say in the header that the pre-brief is description-only. Nothing in the packet gets tagged `[doctrine]` on a record basis. |
| Only a term sheet when a lease was implied | The attorney chair names what a full document would still need checked. That list travels forward as an open item. |
| A chair returns confident prose with no tags | Reject it and re-run that chair once. Nineteen agents produced confident prose in this system on 2026-08-02 and ten factual claims in it were wrong. Confidence is not the product. |

The failure mode to guard hardest: a thin record plus six articulate chairs produces a packet that
reads authoritative and is mostly invention. Every degradation above gets stated in the packet
header, in plain words, where Joe reads it first.

## Hard rails the runner enforces

The chairs carry five rails each. The runner checks all five on the way out, and carries three of
its own.

Chair rails, verified per finding: **A** interrogative by construction, no assertion about what
happened on a specific deal and no prediction about a named person. **B** basis declared on every
finding. **C** n stated honestly. **D** events cited with numbers and dates, never a
characterization of a named outside person. **E** the chair's own empty-chair line present.

Runner rails: **write nothing during the run**. **Never sit in a chair it drafted for**. **End in
one action.**

Any wording the packet proposes for a client-bound document obeys `DNA/writing-rules.md`. No
em-dashes, no flagged vocabulary, no contrast-reframe constructions, no stacked parallel sentence
skeletons. The council checks the deal; the prose was already checked by lint, and a fix written in
banned language sends the document back through the gate.

## Cross-references

`CARR AI/DNA/Deal Management/playbooks/negotiation.md` (the doctrine, and the counterparty-tailored
openings section) · `.claude/agents/README.md` (the chair roster and the shared rails) ·
`CARR AI/DNA/ux-doctrine.md` (law 3, one action; law 5, plain language; law 6, smart defaults) ·
`CARR AI/DNA/writing-rules.md` (any wording that reaches a client) ·
`CARR AI/DNA/Team/skills-rule.md` (the placement test and the skill census) ·
`CARR AI/00_Context/model-tiering.md` (why the chairs stay on Opus) ·
`CARR AI/DNA/Team/preflight-pass.md` (multi-agent craft, including maker-never-checks) ·
the `/crux` global skill (the ordering troubleshoot mode implements).

## Maintenance

- Doctrine changes go to negotiation.md first. This file follows it.
- A new chair argues against the census paragraph in `.claude/agents/README.md` before it exists,
  and its routing row lands here in the same change.
- A skill is a contract versioned to a model. If a chair's tier changes, re-verify the panel on a
  known-good sample before trusting a live run.
- Per the roster rule, a new capability updates four documentation spots. What this skill owes is
  listed in the build report and is outside this folder.
