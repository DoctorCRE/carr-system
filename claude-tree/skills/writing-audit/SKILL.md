---
name: writing-audit
description: >
  Audits a finished or draft piece of Joe Bookout's CARR writing against a merged ruleset
  (David Ogilvy's 10 writing rules + CARR's own hard voice rules) and reports what's broken,
  ranked by severity, with the exact offending quote, why it's a problem, and a specific fix —
  WITHOUT rewriting it for him. This is a REVIEW/CRITIQUE tool, not a drafting tool. Use it
  whenever Joe has already written something (or pasted something in) and wants it checked,
  tightened, pressure-tested, or told "why does this feel off": outreach emails, follow-ups,
  proposals, LinkedIn/Facebook/Instagram/X posts, LinkedIn articles, landing/web copy, flyers,
  one-pagers, internal updates, or any client- or public-facing writing. Trigger phrases:
  "audit this," "review this draft," "critique this," "what's wrong with this," "is this good,"
  "tighten this up," "does this sound like AI," "make this sharper," "pressure-test this email,"
  "would you send this." Do NOT use this to WRITE a new post from scratch — that's the
  write-content skill. Do NOT use for the monthly content calendar — that's the
  social-media-manager skill. This skill only critiques writing that already exists.
---

# Writing Audit — CARR Editing Coach

> **Guidance changes:** Propose an edit to the relevant CLAUDE.md, AGENTS.md or skill in a PR, with the trade-off. Proposals bind nobody until merged; Git history preserves attribution.

A review-only editing pass for Joe Bookout's CARR writing. It applies David Ogilvy's 10 writing
rules (from his 1982 staff memo) filtered through CARR's own established voice rules, and returns
a severity-ranked critique. It does not rewrite the piece — the point is that Joe keeps
internalizing the rules and does his own rewriting, so his voice stays his.

## When to use / when not to use

Use this when a piece of writing **already exists** and needs to be checked before it goes out,
or when Joe can feel something is off but can't name it.

Do NOT use this to generate new writing:
- Writing a fresh social post → **write-content** skill.
- Building/updating the monthly content calendar → **social-media-manager** skill.
- Drafting a new outreach email from a lead → follow **DNA/templates.md** (this skill can then
  audit the resulting draft, but it doesn't write it).

This skill is the missing *review* layer that sits after any of those. It's surface-agnostic:
email, social, proposal, article, web copy, flyer, internal memo — all fair game.

## Canonical rule sources — do not duplicate, defer to these

The authoritative CARR rules live in these files. When a rule below references one, the file
wins if they ever disagree, and any *new* approved rule goes into the file, not into this skill:
- **DNA/templates.md** — email/outreach voice + CTA rules, strongest pitch hooks, the
  Pre-Delivery Self-Check.
- **DNA/brand-voice.md** — marketing/brand voice, messaging pillars, visual identity.
- **The compiled rules (DNA/compiled-rules-shared.md)** — behavioral rules (healthcare-not-CRE audience,
  no-conflict framing, Microsoft-format default, HIPAA spelling, no-sycophancy), plus its
  **Zero-Tolerance AI-Tell List** (added July 6, 2026) — the hard-ban list this skill's "sounds
  like AI" pass now runs first, before the judgment-based checks.

This skill is the *lens*, not a second copy of the rules. If auditing reveals a genuinely new,
approved rule, add it to the right file above and note it — don't let this skill quietly become a
competing rulebook.

**Before auditing, actually open the source-of-record file(s) for the surface you named in Step 1
and audit against their current contents, not the rule summaries baked into this skill — those
files change over time, and they win.** Map: outreach email → DNA/templates.md; social post →
write-content/SKILL.md (the voice source of record) plus DNA/brand-voice.md; brand/marketing piece →
DNA/brand-voice.md; any surface → the compiled rules (DNA/compiled-rules-shared.md) for the behavioral rules. The lists below are a
checklist for applying the live rules, not a substitute for reading them.

## Step 1 — Identify the surface and its rules

Before auditing, name what you're looking at, because the rules differ:

- **1:1 outreach (cold/warm/re-engagement email):** the strictest CARR rules apply — see the
  "CARR hard rules" list. Local/personal voice, NOT corporate/national-scale voice.
- **Social post (LinkedIn/FB/IG/X):** write-content's voice + DNA/brand-voice.md. Hooks matter more;
  the no-em-dash and no-"just" rules still apply.
- **Proposal / client doc:** consultative, educational, healthcare-not-CRE audience; Microsoft
  format expected for the final deliverable.
- **Marketing/brand piece (flyer, web copy, one-pager):** DNA/brand-voice.md and the messaging
  pillars; here CARR's national authority CAN back up local specifics (unlike first-touch email).
- **Internal note/memo:** clarity and next-step rules apply; voice rules relax.

If the surface is ambiguous, ask Joe which it is before auditing — the CTA and voice standards
hinge on it.

## Step 2 — Run the merged ruleset

### Ogilvy's 10 rules (adapted for CARR)
1. **Read the Roman-Raphaelson book / learn to write.** (Meta-rule — not a line-item to flag.)
2. **Write the way you talk.** Does it sound like Joe, a knowledgeable local colleague, talking to
   another person — or like a document/AI? Flag stiff, essay-like phrasing.
3. **Short words, short sentences, short paragraphs.** Email body: max 3 short paragraphs, 2–3
   sentences each (DNA/templates.md). Flag walls of text and long sentences.
4. **Never use jargon.** Write to an 8th grader. For CARR this is doubled: the reader is an expert
   in medicine, NOT in commercial real estate. Flag CRE jargon (NNN, LOI, TI, cap rate, "demising
   wall," etc.) that isn't plainly explained. Also flag corporate clichés banned by DNA/templates.md:
   synergy, leverage, utilize, touch base, circle back, value-add.
5. **Never more than 2 pages on any subject.** If it's longer than it needs to be, flag it. "99% of
   blog posts should be LinkedIn posts."
6. **Check your quotations / facts.** Flag any stat, claim, name, or number that should be verified
   (savings figures, market data, a prospect's city/specialty). Never let an unverifiable claim ship.
7. **Let it breathe — read it aloud the next morning, then edit.** Flag anything that reads clunky
   out loud. For genuinely important pieces, recommend Joe sleep on it before sending.
8. **Get a colleague to improve important pieces.** For high-stakes writing (a big proposal), note
   that a second set of eyes — Dell — is worth it.
9. **Make it crystal clear what you want the recipient to do.** Outreach CTA must be ONE
   low-friction, yes/no ask (DNA/templates.md): "Would you be open to a quick 15-minute call?" — not
   open-ended, not "click this link," not multiple asks. Flag a missing, buried, vague, or doubled CTA.
10. **If you want ACTION, don't write — go tell them.** Strategic flag: if the message is really
    asking for a decision or is emotionally loaded, note that a call may beat an email. Writing is
    not a substitute for a targeted conversation.

### CARR hard rules (from DNA/templates.md / the compiled rules (DNA/compiled-rules-shared.md) — these are non-negotiable)
- **No em dashes** in client-facing writing — use commas/periods or restructure. (Email: Critical.)
- **Never the word "just"** ("I just wanted to reach out") — sounds apologetic.
- **No corporate clichés** (list above).
- **No "I hope this finds you well"** or any filler opener.
- **Don't open with "My name is Joe"** or lead with credentials — lead with the prospect's situation.
- **Don't start the body with "I"** — center the prospect, not Joe.
- **No CARR national-size/scale language in first-touch email** — local and personal only.
- **No signature/sign-off** on outreach — it ends after the CTA.
- **Reference the prospect's specific city/market by name** at least once in outreach.
- **Mention services are at no cost** to the prospect where relevant.
- **Conflict-of-interest / fiduciary hook** is the strongest pitch — check it's present and prominent
  when the piece is meant to persuade.
- **HIPAA** is always spelled HIPAA (not HIPPA). Flag any patient/practice-level data use that needs
  a HIPAA-compliance caveat.

### Step 0: run the linter (added July 25, 2026 — LOCAL Claude Code sessions)
Before reading the piece yourself, get the mechanical findings for free. Save the draft to a scratch file and run:

```bash
~/carr-system/run.sh lint /path/to/draft.txt --surface email
```

Surfaces: `email`, `social`, `proposal`, `web`. Every HARD finding it returns is a **Critical** in your report, quoted exactly as the linter quotes it. REVIEW findings are hard-ban rules whose pattern has legitimate CRE uses (literal land, leverage as a noun, landscaping, a market shift) — judge each one and either promote it to Critical or state plainly why it is clean. RATION findings (two or more rationed filler adverbs) are a normal finding, not Critical.

The linter is a floor, not a ceiling. It cannot see genericness, false balance, missing specifics, uniform rhythm, synonym cycling, or a piece that breaks no rule and still reads machine-made. **A clean linter run is never the audit.** Say in your report that the mechanical pass ran and what it returned, then do the real work below. In a Cowork/cloud session `~/carr-system` is unreachable — do the pass by hand and say so.

### The zero-tolerance AI-tell pass (added July 6, 2026 — run this FIRST, before the judgment-based pass below)
Check the draft against `DNA/writing-rules.md`'s Zero-Tolerance AI-Tell List: em dash (any surface now, not just email), the flagged-vocabulary list (delve, unlock/unleash, seamless, cutting-edge, harness, elevate, etc. — full list in that file), the contrast-reframe construction ("it's not X, it's Y"), rule-of-three overuse, generic AI openers ("in today's fast-paced world," "picture this"), corporate clichés. **A single instance of any item on that list is automatically Critical** — flag it as such regardless of how well-written the surrounding piece is. Rationale (from a direct conversation with Joe, July 6, 2026): a reader primed to spot these patterns dismisses the whole piece the moment one appears, so the risk is per-instance, not per-frequency — "it read fine here" is not a defense for this category.

### The judgment-based "sounds like AI" pass (from DNA/templates.md Pre-Delivery Self-Check)
- **Credibility test:** cut anything too safe, too hedged, too "trying to satisfy everyone."
- **Voice shaper:** cut generic professional filler that could be swapped into anyone's email.
- **Pattern breaker:** flag predictable/formulaic cold-email structures.
- **Artificial sentence check:** name the single most "written by AI"-sounding sentence and say why.

These catch tells that don't carry a recognizable one-word signature — genericness, false balance, missing specifics — real problems, but graded on judgment rather than banned outright. Don't let a clean pass here overshadow a zero-tolerance hit above; the two passes are ranked separately.

## Step 3 — Assign severity

- **Critical** — breaks a CARR hard rule, hits anything on the Zero-Tolerance AI-Tell List
  (the compiled rules (DNA/compiled-rules-shared.md) — em dash on any surface, flagged vocabulary, contrast-reframe, rule-of-three
  overuse, generic AI openers, corporate clichés), would hurt credibility or response rate, or would
  embarrass Joe if sent (CRE jargon to a dentist, no clear CTA, an unverified dollar claim, "I hope
  this finds you well," HIPAA misspelled or an uncaveated patient-data reference). Fix before sending.
  A zero-tolerance hit is Critical regardless of how well-written the rest of the piece is — it's
  callout risk driving the severity here, not overall writing quality.
- **Moderate** — weakens the piece but wouldn't sink it (too long, a cliché, a flat generic
  sentence, buried CTA, corporate-scale language creeping into a first email). Should fix.
- **Minor** — polish (a clunky read-aloud spot, a slightly long sentence, a weak-but-not-wrong word
  choice). Fix if time allows.

Not every violation is fatal — calibrate. Don't inflate Minor issues to Critical.

## Step 4 — Output format

Return exactly this structure, nothing rewritten:

1. **Overall read (one line):** is it working, and what's the single biggest issue?
2. **Findings, most severe first.** For each: `[Severity]` — "the exact offending quote" — why it's
   a problem (name the rule) — a specific, concrete fix (describe the fix; do NOT write the
   replacement sentence for him unless he explicitly asks).
3. **What's working** — 1–3 things done well, so Joe doesn't edit away the good parts.
4. **Verdict:** send as-is / quick fixes then send / needs a real rework / this should be a call not
   an email.

## Hard constraints

- **Do not rewrite the piece.** Critique and point; Joe does the rewriting. Only produce replacement
  text if he explicitly asks ("just fix it for me").
- **Do not fabricate texture.** Never suggest adding fake hesitation, fake anecdotes, fake typos, or
  manufactured "imperfections" to seem human — that produces fake voice, not real voice (same
  principle as the standing rule against inventing hypothetical prospects). Real voice comes from
  cutting filler and being specific, not from adding noise.
- **Be direct, no sycophancy** (the compiled rules, DNA/compiled-rules-shared.md). If the piece is weak, say so plainly and
  say why. Joe values accuracy over flattery.
- **Match the surface.** Don't apply first-touch-email CTA rules to a brand flyer, or relax
  no-em-dash for a client proposal.

## Cross-references
- Drafting new social posts → **write-content** skill.
- Content calendar / scheduling / publishing → **social-media-manager** skill.
- Email/outreach rules of record → **DNA/templates.md**.
- Brand/marketing voice of record → **DNA/brand-voice.md**.
- Behavioral rules → **the compiled rules (DNA/compiled-rules-shared.md + 00_Context/compiled-rules-joe.md)**.

*Built July 3, 2026 from Nicolas Cole's "David Ogilvy Writing Coach" article (X, July 2, 2026),
adapted to CARR's own rules rather than adopted generically. The Ogilvy rules that overlapped
existing CARR rules were merged, not duplicated; the net-new contribution is the standalone
severity-ranked review format itself.*
