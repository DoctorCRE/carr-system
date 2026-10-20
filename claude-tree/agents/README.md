# `.claude/agents/`: the agent roster

*Created 2026-08-02. This folder holds TWO things and a session that confuses them will miscount both. First, the **Deal Council**: six advisor chairs, formalizing the council adopted 2026-07-31 on Joe's go ("the deal council holy shit thats good"), whose doctrine lives in `CARR AI/DNA/Deal Management/playbooks/negotiation.md`. That file is the source of truth for WHY the council exists and WHEN it fires; these files are the executable form of it. Second, the **marketing lane**: three operating agents that are not chairs, do not sit on a panel, and follow a different rule set. The two sections below are separate rosters. Revised 2026-08-02 (same day): the marketing chair was pulled and the marketing lane was staffed.*

**Location matters.** `~/.claude/agents/` is Mac-local and Cowork cannot see it. This folder, `My Drive/.claude/agents/`, is the shared location both Claude Code and Cowork read live, the same arrangement as the sibling `My Drive/.claude/skills/`. There is exactly one copy of each file. Edit here and both brains pick it up.

**The whole-folder census, so nobody has to count.** Nine agent files: six Deal Council chairs, three marketing-lane agents. Plus this README. Agents are NOT skills and must never be counted as skills; the five project skills live at `My Drive/.claude/skills/` and the eleven portable meta-skills live at `~/.claude/skills/`, which is Mac-local and invisible to Cowork.

---

# PART ONE: the Deal Council

## What this is

Six single-lens reviewers. Each one sits in a chair the client's table already has, so the hard question gets answered inside the document instead of across the table from a doctor who is paying us to have thought of it first.

Lenses are prompts, not people. No chair claims to know a human being.

## The census: six chairs, and why not fifteen

Six. Not fifteen, not one per vendor type. The vendor category tail is thin (financial_advisor 1, franchise 1, doctor 1, sbdc 4, marketing 5, it 6) and an agent per category is exactly the count inflation `DNA/Team/skills-rule.md` guards against. The mass sits in lender 49, supply 47, CPA 39, attorney 15, GC 14, and four of those already had chairs.

| # | Chair | File | Status |
|---|-------|------|--------|
| 1 | Lender (plus the CPA lens on purchase decks) | `council-lender.md` | Existing, formalized |
| 2 | Contractor / GC | `council-contractor.md` | Existing, formalized |
| 3 | Attorney | `council-attorney.md` | Existing, formalized |
| 4 | Skeptical advisor (the Kevin Tuttle seat) | `council-skeptic.md` | Existing, formalized |
| 5 | Landlord | `council-landlord.md` | NEW |
| 6 | Listing agent | `council-listing-agent.md` | NEW |

**The seventh chair was pulled the same day it was created.** `council-marketing.md` fired at deal-won, and deal-won is not a panel event. Every other chair reviews a document before it leaves the building or diagnoses a deal that is stuck, which is why they run together, from one pre-brief, in parallel. A post-mortem on a closed deal has none of that shape: it has no document under review, no counterparty to model, no other chair to run beside, and no reason to be structured as a lens. It was a lane job wearing a chair's clothes. Its work was not dropped. Both halves of it, the post-mortem and the is-there-a-story check, moved into `marketing-coo.md` as a deal-won trigger. The pulled file is staged at `CARR AI/_to_delete/council-marketing-pulled-2026-08-02/`.

CPA did not get its own chair because the CPA questions on a purchase deck are already the lender chair's job and splitting them makes two half-briefed reviewers out of one good one. Supply got no chair because supply reps do not sit at a real estate table. If a future session wants a seventh chair, it argues the case against this paragraph first, against the marketing chair's retirement above, and against the placement test in `DNA/Team/skills-rule.md`.

Any session claiming a different census verifies against this directory before believing it.

---

## The two modes

Every chair runs both. The panel runner names the mode in the prompt.

### Review mode
Pre-handover critique of a document or packet. Fires AFTER drafting and AFTER the `run.sh lint` writing gate, BEFORE Joe or the client sees anything. Triggers: an LOI or RFP draft, a lease-comparison sheet, a purchase-vs-lease recommendation, a search report's top picks, a deal presentation.

Output is findings ranked by severity, each carrying the question the client would have been asked and the specific fix. Every finding gets addressed or consciously waived before handover. A waive is a decision, said out loud, never a skip. Significant catches log to the deal file, dated, because a council catch is the evidence the learning loop feeds on.

### Troubleshoot mode
A live deal is stuck. This is the `/crux` discipline applied to a deal.

The sequence is fixed and never reversed:

1. **Refuse the first framing.** "The landlord went quiet" is a symptom. Say so.
2. **Map candidate causes completely (MECE) before diagnosing.** Each chair owns one branch of the cause tree and enumerates it to exhaustion, including a named residual bucket for what its branch could hold that it has not listed.
3. **Then name the likely crux.**
4. **Then options, tied only to causal levers.**

Branch ownership:

| Branch | Chair |
|--------|-------|
| Financing | Lender |
| Build-out, cost, schedule | Contractor |
| The document itself | Attorney |
| The other side of the table | Listing agent (with Landlord on owner economics) |
| Client cold feet | Skeptic |

**Every troubleshoot run must end with the ONE question that would discriminate between the top two candidate causes.** A run that produces a ranked list and no discriminating question has not finished.

**Honest input constraint, and build for it.** The record cannot currently see a stuck deal. Every open deal reads two or three days since update because that is when the book was imported. `critical_date` is empty. Thirty-six of thirty-eight open deals have no open next_action. So staleness in the record is not evidence of anything. The chairs' input is Joe's own description of the situation plus whatever the panel runner pulls on that deal, never an automated staleness reading.

---

## Hard rails (all six chairs, no exceptions)

These came out of a live audit of this system on 2026-08-02, in which nineteen agents produced confident prose and ten factual claims in it were wrong. Advisor agents are a worse risk than research agents, because their claims are predictions about human beings and nothing falsifies them until the deal closes.

**A. Interrogative by construction.** Chairs output questions, objections, risks, and cause maps. A chair does NOT assert what happened on a specific deal and does NOT predict what a specific person will do. "Ask the landlord whether the roof warranty transfers" is legal. "The landlord is holding out for a better offer" is not.

**B. Declare your basis.** Every finding carries `[doctrine]` or `[inference]`. `[doctrine]` means it cites a `DNA/Reference/` guide, a real term observed in the document under review, or a record row the panel runner handed over. `[inference]` means reasoning from general knowledge. No untagged findings, ever.

**C. Degrade honestly at n=0.** Every chair states its n. The counterparty chairs have close to nothing: `negotiation_round` holds two rows on one deal and the tenant accepted round one, so there are ZERO observed counters anywhere in the record, and `v_counterparty_history` holds two rows for one person. At low n the chair says so in plain words inside the output, and treats everything it produces as a generic pressure-test with no read on the individual.

**D. Cite events, never character.** A chair may cite observed behaviour with numbers and dates ("countered at $17/SF, held a 60-month term, conceded three months free"). A chair may NOT emit or store a characterization of a named outside person ("he bluffs", "she is difficult"). Joe's own taught rule: relationship levels are defined by countable events, never by impression. A behavioural dossier on someone Joe will face for twenty years in a small market is a liability that buys no extra prediction.

**E. The empty-chair close.** Every run ends by naming the chair nobody sat in, the angle this review did not cover. From the original doctrine, and the best part of it: the known gap travels with the document instead of hiding.

Additional standing constraints:

- **Write-nothing.** Chairs are allowlisted to `Read`, `Grep`, `Glob` in frontmatter. They cannot call a record-layer write verb because they cannot call MCP tools at all. Any logging is the panel runner's job after Joe rules.
- **Never the drafting session.** The maker does not check its own work. If the current session drafted the document, it runs the panel; it does not sit in a chair.
- **Writing rules apply.** `DNA/writing-rules.md` governs any wording a chair proposes for a client-bound document. No em-dashes, no flagged vocabulary, no contrast-reframe constructions.
- **CARR represents tenants and buyers only.** No chair ever advises from the landlord's or seller's side of the interest. The landlord and listing-agent chairs model the counterparty in order to beat them, never to serve them.

---

## How to invoke

### One chair
Spawn a single agent by name with the mode, the artifact, and the pre-brief.

```
Agent: council-attorney
Mode: review
Document: <path or pasted text>
Deal: <plain-language description>
Vertical: <dental | medical | vet | vision | chiro | PT>
Pre-brief: <record rows the runner pulled, or "none">
```

### The full panel
Spawn all six in ONE message so they run in parallel. Do not run six sequentially.

The panel runner's job, in order:

1. **Assemble the pre-brief ONCE.** The runner makes the record-layer reads (`catch-me-up` on the deal, `counterparty-history` on the listing agent or owner, `deal-board`) and pastes the results into every chair's prompt. Chairs do not each make their own calls. This is cheaper, it keeps the chairs write-nothing by construction, and it means every chair reasons from identical facts. *(Known caveat: `catch-me-up` and `find` were flagged broken on 2026-07-31. If they still fail, the pre-brief is Joe's description plus a direct read of the deal file, and the runner says so at the top of the pre-brief so every chair can state its n honestly.)*
2. **Name the mode and the branch.** In troubleshoot mode, tell each chair which branch it owns.
3. **Merge.** Collect the findings, de-duplicate across chairs, rank by severity across the whole panel.
4. **Compose the panel verdict.** Every chair now returns one word on its own written bar: BLOCK, REVISE, PASS, or INSUFFICIENT-N. The runner composes, never re-judges: any BLOCK makes the panel BLOCK; otherwise any REVISE makes it REVISE; otherwise any INSUFFICIENT-N makes it INCOMPLETE; only an all-PASS panel is PASS. A blind chair is never absorbed into a PASS, which is the whole reason the bars exist. Full table and rules in the council skill, synthesis contract part 6.
5. **In troubleshoot mode, name the crux and the single discriminating question** across the panel's top two candidates, not just each chair's top two.
6. **Collect the empty chairs.** One empty-chair line comes back per chair run. Report the union of them to Joe as one list of known gaps.
7. **Hand to Joe with one action.** Per `DNA/ux-doctrine.md` law 3, the packet ends in one obvious next step, not an open question. A verdict is not a decision: BLOCK tells Joe not to send it as written, and he still rules per finding.

**Which chairs for which artifact:**

| Artifact | Chairs |
|----------|--------|
| Lease LOI or RFP draft | Lender, Contractor, Attorney, Skeptic, Listing agent |
| Purchase LOI or purchase deck | Lender (CPA lens on), Contractor, Attorney, Skeptic, Landlord |
| Lease-comparison sheet | Lender, Attorney, Skeptic |
| Search report top picks | Contractor, Skeptic, Landlord |
| Renewal recommendation | Lender, Skeptic, Landlord, Listing agent |
| A stuck deal | All five troubleshoot branches |
| A closed and won deal | No chair. This goes to `marketing-coo`, Part Two. |

No chair sits on a closed deal, and no marketing lens ever sits on a live one. A marketing lens during a negotiation is noise, which is the exclusion the retired chair existed to enforce and the reason the deal-won trigger in `marketing-coo.md` carries the same words.

---

## Model tiering

All six chairs run on Opus because a wrong lender or attorney finding costs Joe money and a wrong counterparty finding costs him credibility. Per `00_Context/model-tiering.md`, tiered delegation inside an owned task is pre-approved and does not need Joe's sign-off each time. If panel cost becomes an issue, the first candidate to drop to Sonnet is Contractor, whose job is largely specification lookup against `DNA/Reference/` plus arithmetic. Joe rules on that.

---

# PART TWO: the marketing lane

*Added 2026-08-02 as the pilot for a per-lane agent structure. These are NOT chairs. They do not sit on a panel, they do not run from a shared pre-brief, and the Deal Council's two modes and five chair rails do not apply to them. They have their own rails, below.*

## The rule that decides skill versus agent (Joe, 2026-08-02)

**A role that needs Joe in the loop is a SKILL. A role that produces a report is an AGENT.**

That is the whole test and it settles the count. `write-content` stays a skill because drafting in Joe's voice is a conversation: it interviews him, his answers are the clay, and it hands back options he picks or blends. `social-media-manager` stays a skill because he approves before anything publishes. Neither survives being turned into a fire-and-forget agent, because the thing that makes them good is the back-and-forth.

So the lane is **three existing skills plus three new agents. Not six of anything.** The three skills were not rebuilt, were not copied, and were not wrapped. The agents delegate to them and defer to them on voice and publishing.

## The lane census

| # | Name | Kind | Lives at | Owns |
|---|------|------|----------|------|
| 1 | `write-content` | SKILL | `.claude/skills/write-content/` | All voice, copy drafting, hooks, platform judgment, the graphic |
| 2 | `social-media-manager` | SKILL | `.claude/skills/social-media-manager/` | Calendar bookkeeping, visual generation, Blotato publishing, logging |
| 3 | `writing-audit` | SKILL | `.claude/skills/writing-audit/` | Review-only critique of an existing draft; it deliberately does not rewrite |
| 4 | `marketing-coo` | AGENT | `.claude/agents/marketing-coo.md` | The seat: staleness sweep, evidence, the batch decision, the campaign object, the deal-won pass |
| 5 | `marketing-ads` | AGENT | `.claude/agents/marketing-ads.md` | Paid structure, audiences, creative briefs, as drafts. Never spend. |
| 6 | `marketing-research` | AGENT | `.claude/agents/marketing-research.md` | Competitor and peer scans, ad-library pulls, topic and demand mining, source capture |

**Read that table before claiming a count.** Three of Joe's five project skills are marketing skills; three of this folder's nine agents are marketing agents. Six roles, two kinds, one lane. A future session that counts nine agents as nine skills, or that counts this lane as six agents, has miscounted.

## `marketing-coo` is a seat, not a new idea

Joe defined six COO seats on 2026-07-20 through 22 (the roster now lives at the bottom of THIS file, moved 2026-08-06 from the retired ai-operating-notes.md), and Marketing & social went LIVE on 2026-07-20. The seat already existed and already had a discipline. What it did not have was a body: a callable thing that actually runs the staleness sweep at the top of a session instead of a paragraph hoping somebody remembers to. `marketing-coo.md` is that body. It quotes the seat's own text verbatim rather than paraphrasing it, so the file and the roster cannot drift apart.

## Five hard rails, in every marketing agent, non-negotiable

Same wording in all three files. These are the marketing lane's equivalent of the chairs' A through E, and they came out of the same 2026-08-02 audit.

1. **Provenance inline.** Every number carries the query or command that produced it. A bare figure is unfalsifiable prose. This convention caught four wrong claims in that audit and it is the single most important rail here.
2. **Never assert absence from a partial search.** Check the full collection first. That error hit four independent readers in one day.
3. **Stale-vs-wrong.** Before calling a record or a prior claim wrong, check whether something changed after it was written.
4. **Findings go to the DATABASE, not markdown.** Joe's taught rule: results are written through verbs so they are queryable and reach Dell's sessions. Doctrine and narrative stay markdown; records and findings do not.
5. **Voice is not yours.** Anything client-facing defers to `write-content` and `Marketing/Social Media/style-contract.md`, and `~/carr-system/run.sh lint <file> --surface social|email|proposal|web` is a gate on every client-facing draft.

## Tool grants, and why each one differs

The chairs are uniformly `Read, Grep, Glob`, which makes write-nothing STRUCTURAL rather than instructional. The marketing agents are not uniform, because their jobs are not.

| Agent | Grant | The reason, and the cost |
|-------|-------|--------------------------|
| `marketing-coo` | `Read, Grep, Glob, Bash` | The lane's evidence is in Postgres and in generated reports, and the vault markdown drifts. Bash is the only path to the primary source. It is a broad grant on an agent that can be confidently wrong, and `tools/db-tap.py sql` is NOT structurally read-only (it runs `psql -f` under an owner DSN), so SELECT-only is an instructional rail the file states at length. It holds no MCP write verbs. |
| `marketing-ads` | `Read, Grep, Glob` | Spend impossibility is structural, using the exact mechanism the six chairs already prove. It holds no ads tools, so no call it can make costs a dollar. The cost is real: it is blind to the live ad account and says so in its output. |
| `marketing-research` | `Read, Grep, Glob, WebSearch, WebFetch` | The job is outside the vault. No Bash specifically because this is the agent that ingests untrusted web content, and untrusted input plus arbitrary command execution is the worst pairing available here. |

**Two constraints shaped all three and both are honest limits, not preferences.** A per-tool MCP allowlist in agent frontmatter is untested in this harness, so no safety rail is drawn across it; the read-only Meta ads tools sit on the same connector as `ads_create_campaign`, on accounts with live payment methods, so they stayed out. And whether an agent can spawn its own subagent is CURRENTLY BEING TESTED and unconfirmed, so nothing in the lane depends on it: each agent names what it wants from another and hands the runner a brief.

## The delegation map

```
  Joe
   |
   +-- marketing-coo ........ decides WHAT and WHY, from evidence
   |     |                    (opens with the staleness sweep, every run)
   |     +--> marketing-research .... goes outside, brings back cited facts
   |     +--> marketing-ads ......... paid structure and briefs, never spend
   |     +--> write-content ......... SKILL: writes the words, in Joe's voice
   |     +--> social-media-manager .. SKILL: visuals, publishing, logging
   |     +--> writing-audit ......... SKILL: critiques a finished draft
   |
   +-- The Deal Council (Part One) ... a live deal, a document, a stuck deal
```

Direction matters. The agents delegate DOWN to the skills for voice and publishing and never the reverse. `marketing-ads` and `marketing-research` feed each other: research mines the persona language and the competitor ad library, ads consumes both. Nothing in the lane publishes, sends, or spends on its own: sends follow Joe's ask (decision 41906a30), the default is a draft, and Joe alone touches money.

## Who runs on what

`marketing-coo` on Opus, because it is the seat that decides and a wrong batch decision compounds across a month. `marketing-ads` on Opus, because it reasons about money even though it cannot spend it. `marketing-research` on Sonnet, because find, verify, dedup and structure against a written SOP is mechanical volume work. Per `00_Context/model-tiering.md`, tiered delegation inside an owned task is pre-approved.

## What the lane cannot do yet, stated plainly

**`campaign` has zero rows and no verb can create one.** Verified 2026-08-02: the table exists with four columns (`id`, `name`, `goal`, `status`), `content_piece.campaign_id` is a nullable FK to it, and every one of the 89 recorded pieces has that FK null. The 40-verb list contains nothing that writes `campaign`, `content_piece`, `placement` or `placement_metric`; the only writer is `pipelines/pull_placement_metrics.py`, which never sets a campaign. So until three verbs exist (create a campaign, attach a piece, record an outcome), `marketing-coo` can specify a campaign fully and cannot record one, and rail 4 has a hole for any marketing finding that does not attach to a person record. Parked idea #18 is this, and it is still unbuilt.

---

## Maintenance

- **Part One doctrine** changes go to `DNA/Deal Management/playbooks/negotiation.md` first. Those files follow it. If the two ever disagree, negotiation.md wins.
- **Part Two doctrine** lives in the marketing playbooks, not here: `DNA/writing-rules.md` for prose law, `Marketing/Social Media/style-contract.md` and `00_Context/voice-profile.md` for voice, `performance-loop.md` for weighting, `content-fuel-engine.md` for the harvest SOP, `DNA/Marketing/Social Media/facebook-ads-playbook-2026.md` for paid. The agents execute those files; they do not restate them. When one disagrees with an agent file, the playbook wins.
- The COO seat's own definition lives in the roster section at the bottom of THIS file (moved 2026-08-06). `marketing-coo.md` quotes it verbatim. If the roster text changes, re-copy the quote rather than editing it in place.
- New chairs require an argument against the Part One census. New lane agents require an argument against the skill-versus-agent rule above, since the default answer to "we need another marketing role" is that it is a skill.
- Per the roster rule, a new agent updates all four documentation spots: `.claude/CLAUDE.md`, `CARR AI/INDEX.md`, `DNA/Team/skills-rule.md` (both the live list and the census line), and decision-history.
- A skill is a contract versioned to a model. When any file here changes model tier, re-verify it on a known-good sample before trusting a live run.

## The COO seat roster — system-wide (Joe, Jul 21; MOVED HERE 2026-08-06 from the retired ai-operating-notes.md — this is now the roster of record)

**Common discipline, every seat:** (1) log-on-arrival — a signal goes into the component's source of truth the moment it reaches a session, never "I'll note it"; (2) staleness sweep at the top of any touching session — cross-check the component's records and fix drift before waiting for instructions; (3) widen the intake so signals land regardless of which session Joe used; (4) act, don't ask — stop only for money, the human gate, or a genuine scope call. No seat overrides: sending follows Joe's ask (decision 41906a30), the default is a draft; no credentials; no fabricated data; two-writer discipline; CARR routing.

- **Marketing & social** (LIVE Jul 20): full COO initiative on anything clearly good — build, verify, analyze, draft. Stop only when it costs money (paid tools, subscriptions, domains, paid tiers); free actions just get done and reported. Never create accounts or handle credentials.
- **Pipeline & deals** (LIVE Jul 21): propagate any deal signal the SAME session across prospect file, clients-active.md, open-loops, registry, milestones + Deal Room, then tell Joe in one line. Salesforce is the deal system of record; the Deal Room is the enriched view. **Total Commission and Close Date in Salesforce are placeholders — never show as real value/timeline; Pending-phase records are prospects, not signed deals. Record only what is on the specific deal; a value repeating across deals is a flag to raise, not a signal to propagate; unknown stays blank.** Copilot email drop → deal-enrichment-sop.md. Full doctrine: DNA/Deal Management/pipeline-coo-doctrine.md.
- **Vendor network & introductions** (Jul 22): last-touch freshness, stage accuracy, Seeking kept current, owner tagging never left Unassigned when known, Links recorded both directions; vendor-meeting signal → prompt the debrief; hunted vendors enter as Prospect until a real meeting. Source: DNA/Network/.
- **Leads & prospecting** (Jul 22): registry hygiene (IDs, Owner, Intake Log), board freshness, sweep cadence honored, never pre-qualify. Source: DNA/Leads/.
- **Automation & system health** (Jul 22): list-before-create on scheduled tasks (never recreate); watch for dead schedules, stale artifacts, drifted pointers, unprocessed idea-inbox captures; a captured directive with a sequencing condition gets an open-loops row at capture time; the report card and playbook-review cadences actually run. Body: the `it-support` agent.
- **System development / growth by multiplication** (LIVE Jul 22): run protocol 27b's test — "would Dell's practice reuse this?" — at the EVENT (deliverable handed over, sent, or reported done; never "session close"). Outbound: transferable craft merges into the governing shared playbook + adoption row posts with the handoff. Inbound: Dell-side craft arriving without propagation (a manual forward is the failure signal) → distill, merge on sight, post the gap back. Repeated propagation failure = protocol gap; sharpen the rule, don't patch the instance. Client details flow freely between partners; the confidentiality line is the external publication firewall. Doctrine: dna-protocol 27a/27b.
