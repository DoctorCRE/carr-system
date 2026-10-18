# Approved Email & Outreach Rules

> **Doctrine ownership: single writer.** One seat edits this file, the Fable design seat. Every other session, either brain, proposes changes through the `teach` verb or a team-board row, and the seat lands them. Set 2026-08-01 per ORDER 38 (two-writer endgame D3).

*Consolidated from the CARR Outreach Agent Master Prompt (Make.com Scenario 1) and session learnings. Apply these rules to all written outreach — cold, warm, and re-engagement.*

Last updated: July 12, 2026 (added Follow-Up & Objection Rules section — follow-up cadence, objection acknowledgment, one-option rule, proposal shape; Joe-directed X extraction). Prior: July 7, 2026 (added pitch hooks #11–13 patient-returns-ranking / most-options-wins / held-against-you, from CARR's flagship "Maximize Profitability" talk — Joe-approved). Prior: July 7, 2026 (added pitch hooks #8–10 advisor-incentive / dual-agency / negotiate-before-you-own, from CARR's "Startup vs. Acquisition" training talk — Joe-approved). July 7, 2026 (added pitch hooks #6 cost-of-going-unrepresented and #7 patient-experience, from the Colin Carr founder podcast — Joe-approved). July 3, 2026 (added the Make.com propagation note below)

---

## Core Voice Rules

- Format email bodies as clean, simple text/HTML — short paragraphs, no walls of text
- Never use em dashes in client-facing emails — use commas, periods, or restructure the sentence
- Total email body: no more than 3 short paragraphs, 2-3 sentences each. Brevity increases response rates.
- Never use the word "just" (e.g. "I just wanted to reach out") — it sounds apologetic
- Never use corporate clichés: "synergy," "leverage," "utilize," "touch base," "circle back," "value-add"
- Never mention CARR's national size/scale in a first email — keep it personal and local, not corporate
- Never open with "I hope this finds you well" or any variation — universally recognized as filler
- Never open with "My name is Joe" or lead with credentials — lead with the prospect's situation first
- Avoid starting the email body with "I" — centers Joe instead of the prospect
- The email should read like it came from a knowledgeable local colleague, not a salesperson working from a database

## CTA Rules

- CTA must be a yes/no question, not open-ended. "Would you be open to a quick call?" outperforms "Let me know if you'd like to connect."
- One simple, low-friction ask — a 15-minute call or a reply. Never "schedule a meeting" or "click this link."
- Reference the prospect's specific city or market by name at least once — signals local knowledge and credibility

## Subject Line Rules

- Specific, curiosity-driving, non-salesy
- Reference their specialty or location
- Never use generic phrases like "Introduction" or "Following Up"

## Structural Notes

- No signature or sign-off — the email ends after the CTA (CARR's mail system auto-appends Joe's corporate signature; adding one is redundant — origin of the rule, June 24, 2026)
- Mention services are at no cost to the prospect
- For warm re-engagement (e.g., a prospect who's pushed back before): shorten further, soften tone, single CTA, lead with the strongest value hook (usually conflict-of-interest framing)

### Animated GIFs in outreach — never a cold first touch (Joe-approved, July 25, 2026)

We can now produce an "animated static": a CARR card rebuilt as a ~6-second stop-motion build, exported as a GIF (`~/carr-system/video/make-animated-static.sh <layers> <name> --email`, local sessions only). The `--email` cut exists because **legacy Outlook on Windows renders only a GIF's first frame as a static image** (modern Outlook 365, Outlook Mac, web and mobile all animate); it leads with the FINISHED card so that frame is the complete, readable message, and it plays once rather than looping in an inbox. Never send a non-`--email` GIF to a prospect: its first frame is an empty canvas, which is what a legacy-Outlook recipient would receive.

**Do not put one in a cold first touch.** Two reasons, both structural rather than aesthetic. First, `DNA/writing-rules.md` bans bullets and mid-sentence bold on this surface precisely because outreach has to read as one person writing to another; an animated brand card is marketing collateral and it undercuts that at the exact moment we are trying to read as a person, not a campaign. Second, an image-heavy first-touch send is the most exposed to deliverability penalties, and a first touch that lands in spam has cost us the prospect for nothing.

**Where it does earn its place:** nurture and follow-up touches where the relationship already exists, a landing page, or any send whose job is to DELIVER information rather than to make an introduction. The test is the same one that governs the format on social — if the card would communicate identically as a still image, send the still.

The animated card is not a substitute for the plain, specific, personal email that this file exists to protect. It is an occasional supplement to a warm thread, and the human gate is unchanged: Claude drafts, Joe sends. [stamp: Joe's brain, Claude Code, 2026-07-25]

---

## Strongest Pitch Hooks (in priority order)

1. **Conflict-of-interest / fiduciary framing** — if a buyer transacts without representation or lets the listing broker "handle their side," that broker collects a double fee and still represents the seller. CARR is the buyer's only advocate. This is consistently the strongest hook.
2. **No cost to the client** — CARR's fee comes from the listing agreement already set aside by the seller/landlord, not from the client.
3. **Posture and leverage in negotiation** — using multiple competing properties as leverage rather than chasing one.
4. **Due diligence expertise** — healthcare-specific pitfalls (zoning, parking, utility build-outs, permitting) that general commercial brokers miss.
5. **Our vendor network** — the team's shared healthcare-specific relationships (lenders, CPAs, contractors, architects) as a differentiator. Refer to it as "our network" / "my network" (first person, solo-Joe by default), NEVER "Dell's network," NEVER framed as a Joe-and-Dell partnership unless Joe directs it, and NEVER credited to Dell's years of experience (HARD BAN, see DNA/writing-rules.md; Joe's corrections Jul 1, Jul 7, and Jul 16, 2026, after a post shipped violating it).
6. **The cost of going unrepresented (data-backed)** *(added July 7, 2026 from founder material)* — most owners negotiate their own deal and assume they did fine; unrepresented practices routinely leave six figures on the table. Use a concrete, local-voice figure as the hook, not a vague "we save you money": overpaying $3–4/sqft on a ~3,000 sqft space is ~$1,000–1,500/month; not capturing free rent on a renewal alone can mean giving up $30,000–80,000. The specific number is what makes this land. **First-touch caution:** use the *illustration/number*, NOT CARR's national scale — the "9 of 10 doctors" / "6,000+ clients" founder framing is credibility material for warm or later touches, never a first cold email (see the no-national-scale rule above). Source: founder Colin Carr, June 2026 podcast (`DNA/Marketing/Source Material/colin-carr-podcast-2026-06.md`).
7. **Real estate shapes the patient experience, not just the rent** *(added July 7, 2026)* — facility quality affects referrals, brand, staff morale, and patient trust "regardless of how good the care is" (Colin Carr's framing). A consultative, non-price angle for owners who think their rent is fine: would a new patient's eyes see a worn, dated space? Best for value-led touches where a pure savings pitch would feel transactional; pairs naturally with the conflict-of-interest hook (#1) rather than competing with it.
8. **"Consider how your advisor gets paid"** *(added July 7, 2026 from the Startup vs. Acquisition talk)* — every player around a practice decision carries a bias baked into their compensation: the practice broker earns a % of the sale (wants you to buy, and buy high), the equipment rep favors a start-up (more to sell), the listing broker is paid by the seller/landlord. Coach the prospect to filter every recommendation through who's paying the person giving it — then note CARR is paid only by the landlord/seller, never the buyer/tenant. Lands hardest when Joe names his OWN incentive first (disarming honesty earns the right to flag everyone else's). Reinforces #1. Source: `DNA/Marketing/Social Media/content-concept-library.md` §1D.
9. **"You can't serve two masters" (dual agency, in one line)** *(added July 7, 2026)* — the memorable mechanism behind #1: a broker paid by the seller/landlord — and paid more when you pay more — cannot also be your unbiased advocate. Watch for the tell of a practice broker charging the BUYER a fee (seen up to $7,500 on the call) — that's someone trying to work both sides of the aisle. Use to make the abstract conflict-of-interest point concrete and quotable. Source: same.
10. **"Your leverage is gone the moment you own the practice"** *(added July 7, 2026)* — in an acquisition, negotiate the real estate (the rate, and especially a pre-set purchase-option price) BEFORE closing on the practice; "if the real estate isn't right, I might not buy the practice" is leverage that vanishes once you own it. A concrete, non-obvious reason to bring representation in EARLY — for prospects mid-acquisition who assume the practice price is the only negotiation. Pairs with the "two transactions per acquisition" education. Source: same.
11. **"You're the 4th reason a patient returns — the building is the 1st"** *(added July 7, 2026 from the flagship "Maximize Profitability" talk)* — a PMC study's top four reasons a patient comes back: (1) facility, (2) front-desk staff, (3) support staff, (4) the doctor. A non-price, ego-aware hook for an owner who thinks their space is "fine" — the facility is judged before the patient ever meets them. Consultative, not transactional; pairs with #7 (patient experience) rather than a savings pitch. Source: `DNA/Marketing/Social Media/content-concept-library.md` §1E.
12. **"Whoever has the most viable options wins"** *(added July 7, 2026)* — the sharpest one-line version of the posture/leverage point (#3): all your negotiating power comes from having real alternatives (lease OR purchase) and being willing to walk. If the landlord believes you can't or won't move, they hold every card. Use as the memorable distillation when #3's longer framing is too abstract. Source: same.
13. **"Everything you say to a listing agent can be held against you"** *(added July 7, 2026)* — the person who answers the listing on a space represents the landlord, not you; tell them "I love this space" and your rate just went up. A concrete reason to route ALL landlord/seller contact through a rep — reinforces the conflict-of-interest hook (#1) with a specific, sticky warning. Source: same.

---

## Follow-Up & Objection Rules (added July 12, 2026 — Joe-directed extraction from an X sales-operator article, adapted to CARR's consultative tone; wording is Claude's, Joe reviewing)

These govern what happens AFTER a first touch gets a reply, a call, or real interest. Same voice rules as above apply throughout.

- **Follow-up sequence (after genuine contact — an inquiry, a call, a "let me think about it." NOT a cold-chase cadence for silent cold leads):**
  - Touch 1 (same/next business day): confirm what was discussed and name the specific next step. Nothing salesy, just clarity.
  - Touch 2 (~3 business days): add ONE piece of value relevant to their situation — a market data point for their specialty/city, a relevant CARR article from the Blog & Case Study list, an answer to something they raised. No ask.
  - Touch 3 (~7 business days): one direct yes/no question (consistent with the CTA rule above). Then stop and route by their answer; no drip past this without a reply.
  - All counts are business days (weekends are off in this system, and doctors don't want Saturday sales email anyway).
- **Objection handling in writing:** acknowledge what is TRUE in the objection before responding — never argue with it. Then redirect to the outcome for them (what representation gets them), not a defense of the process. Deal-stage/renewal objections have their own playbook: `Deal Management/playbooks/renewals.md`.
- **One option, not three.** When proposing a next step, offer ONE. Multiple options create hesitation and hand the prospect homework. (Same logic as the single low-friction CTA rule.)
- **Proposal/engagement shape (when putting the value of engaging CARR in writing):** open with the prospect's problem in THEIR words from the actual conversation, describe the outcome they get (not the process Joe will run), keep it to one page, end with a specific next step — never "let me know if you have questions."

---

## Blog & Case Study Reference

The full Make.com prompt includes a curated list of blog articles and case studies matched to lead situations (startup, expansion, relocation, etc.) — see **Automation/scenario-1-prompt.md** (the historical Make prompt, retired Jul 6, 2026) for the curated blog/case-study list matched to lead situations. Reference the most relevant one when it strengthens an email.

---

## Pre-Delivery Self-Check (added July 2, 2026)

**Zero-tolerance pass first (added July 6, 2026):** before the judgment-based checks below, run the draft against `DNA/writing-rules.md`'s Zero-Tolerance AI-Tell List (em dash, flagged vocabulary like "delve"/"seamless"/"unlock," the contrast-reframe construction, rule-of-three overuse, generic AI openers, corporate clichés). Those are hard bans — a single instance is disqualifying no matter how well the rest of the draft reads, because the risk is a reader pattern-matching and dismissing the whole email, not a writing-quality judgment call. The checks below are judgment calls on top of that, not a substitute for it.

Before delivering any outreach draft, run it through these checks — adapted from writing-craft prompts Joe found and approved (source: X post, July 2, 2026). These catch the specific tells that make writing sound AI-generated or generic; they're not a grammar pass.

- **Credibility Test:** read it as a skeptical prospect would. Cut anything too safe, too vague, or too perfectly hedged — the "trying to satisfy everyone" voice. Every sentence should sound like Joe has an actual point of view, not like a neutral assistant covering every base.
- **Voice Shaper:** check for generic professional filler and cut it. The email should sound specific and grounded in this prospect's actual situation, not swappable into a template for anyone.
- **Pattern Breaker:** read it like someone who's seen a thousand cold outreach emails. If a sentence or structure feels predictable or formulaic, rewrite it.
- **Read-Aloud Test:** picture saying it out loud. Fix anything that sounds like a document instead of something a person would actually say to another person.
- **Artificial Sentence Check:** find the single most "written by AI"-sounding sentence in the draft and rewrite it specifically — don't assume a pass on the whole thing means every sentence is fine.

**Do not** invent fake hesitation, fake personal anecdotes, fake typos, or other manufactured "imperfections" to seem more human — that produces fake texture, not real voice. Same principle as the existing rule against inventing a hypothetical prospect or scenario: don't fabricate false color of any kind, in the substance or in the writing style.

---

## Notes

*(The retired Make.com section was trimmed 2026-07-22 per team-loops T27, Dell-approved 7/20: Scenario 1 went fully Claude-native July 6; these rules apply live from THIS file. Automation/scenario-1-prompt.md remains historical reference only, incl. its curated blog/case-study list.)*

- When in doubt on tone, default to: concise, local, consultative, single clear ask.
- **Timely hooks live in the substance bank (added July 12, 2026):** `DNA/Marketing/Social Media/content-inspiration-bank.md` §2 entries carry an `Email angle:` line — when drafting outreach, check the recent entries for a live local fact that fits this prospect's situation. A real, dated market observation beats a generic hook.
- The Pre-Delivery Self-Check above also applies, in adapted form, to social content — see the write-content skill's own Step 4 (Tone) and Output steps for its version of this pass.

---

## Evaluator gate: independent audit loop on every outreach draft (added 2026-07-20)

Upgrade to the Pre-Delivery Self-Check above. That check is real, but it runs as a single maker-self-grade pass in the same context that wrote the draft, where a draft tends to get rubber-stamped. The fill-engine already proved the better rule for client-facing text: the maker never grades its own work (fill-it-in-workflow.md step 5 runs an independent verifier pass). Outreach is client-facing too, so it gets the same treatment.

The loop (runs before any draft reaches the human's send queue):
1. Draft the outreach per the rules above.
2. Run it through the **writing-audit skill** as an INDEPENDENT pass — a separate context or verifier subagent, blind to how the draft was written, grading against the zero-tolerance AI-tell list, the CARR hard rules, and the judgment passes.
3. Any zero-tolerance hit or failed judgment check → revise and re-audit. Loop until it passes clean.
4. Only a passing draft gets composed into the AI Gmail queue for the human to send.

This makes the self-check a real generate → audit → revise loop instead of one pass, and moves the grading out of the maker's own context. Applies to every outreach type: T1 openers, the follow-up touches, vendor check-ins. Same evaluator-optimizer pattern the reply engine and the fill-engine use. Stamp: Joe's brain, 2026-07-20.

## Signal-first outreach doctrine (merged 2026-07-31 from Vacca's 4-prompt outbound article, x.com/itsalexvacca/status/2082236149859488209 — read in full on the gold rule; his auto-send stage is DECLINED, the human gate stands, but two rules of his copy craft are adopted)

1. **The signal answers "why are you writing to me this week" — and the copy is downstream of it.** Every outreach draft names its signal before a word gets written (lease event window, new entity filed, PECOS enrollment, relocation marker, a hire that means growth). A draft without a this-week reason is a drip, not outreach, and gets held for one. Our radar exists precisely to supply these; the draft's job is to carry the signal, not to decorate it.
2. **One personalized line, carrying the signal — the rest stays standard.** Over-personalization is wasted work and reads as research theater: if the right doctor at the right practice in the right week gets ONE accurate observation about their situation ("your lease at [property] enters its option window this quarter"), that line proves the homework. If it is the wrong doctor, no anecdote about their podcast saves it. Templates stay templates; the signal line is the variable. (This also keeps drafts fast to review — Joe reads one line for truth, not five for cringe.)
3. *Kept as ours, validated by his:* scripts train on what actually got replies (correction→rule already does this); first campaigns by hand, keep the winners — the stage we are in, by design.

*[stamp: Joe's brain, Claude Code (Fable seat), 2026-07-31 night]*
