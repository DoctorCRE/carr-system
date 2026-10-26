---
name: redteam
description: >
  Pressure-tests a plan, idea, draft, or decision by switching into genuine-critic mode on demand.
  Use when the user says /redteam, "poke holes in this," "red team this," "what's wrong with this
  plan," "argue against this," "stress-test this," "where does this break," or "be my critic." Puts
  the strongest honest objections on the table: failure modes, hidden assumptions, weakest links,
  what a smart skeptic would say, and what would have to be true for this to fail. Deliberately
  adversarial and specific — no reflexive agreement, no vague caveats. Critique only; it does not
  rewrite or fix unless asked afterward.
---

# redteam — adversarial pressure-test

> **Doctrine ownership: single writer.** One seat edits this file, the Fable design seat. Every other session, either brain, proposes changes through a guidance-file pull request (AGENTS.md "Guidance changes") or a team-board row, and the seat lands them. Set 2026-08-01 per ORDER 38 (two-writer endgame D3).

## What this is for

Agents default to agreeable. `/redteam` deliberately flips that: for one pass, the job is to find what is wrong, not to be supportive. Use it before committing to a plan, shipping a draft, or locking a decision.

## Procedure

Attack the target — the plan, idea, draft, or decision the user names or just shared — along these lines, keeping every point concrete and tied to the actual content:

1. **Hidden assumptions.** What is being taken for granted that might not hold? Name each and why it matters.
2. **Failure modes.** Concretely, how does this go wrong? Walk the most likely bad path, not a generic "risks exist."
3. **Weakest link.** The single point most likely to break the whole thing.
4. **The smart skeptic.** What would a sharp, informed critic say on first read? Steelman their objection.
5. **What would have to be true** for this to fail — and how likely each of those conditions is.
6. **What's missing.** Gaps, unhandled cases, unasked questions.

Then close with a one-line honest verdict: is this fundamentally sound with fixable gaps, or is there a load-bearing problem?

## Rules of engagement

- **Specific, not generic.** "This could face scaling issues" is useless. "Step 3 assumes the list is deduped, but step 1 doesn't dedupe, so you'll double-count" is the job.
- **No false balance.** If it is mostly solid, say so and give the two real weaknesses — do not manufacture ten weak objections. If it is genuinely broken, say that plainly.
- **Critique only.** Do not rewrite or fix during the red-team pass. Offer to help fix afterward if the user wants it.
- Pair with the premortem skill for a forward-looking "assume it already failed" angle, and with steelman-style thinking when the user wants the case FOR something instead.
