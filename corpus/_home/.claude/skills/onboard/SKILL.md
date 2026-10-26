---
name: onboard
description: >
  Spins up a brand-new project properly — the start-of-life counterpart to handoff. Use when the
  user says /onboard, "set up this project," "help me start a new project," "scaffold this,"
  "initialize this project," or is standing in a fresh/empty folder about to begin. Interviews the
  user about the project first (what it is, who it's for, the goal, constraints, conventions, how
  they like to work), THEN generates a context file (CLAUDE.md or AGENTS.md), a sensible folder
  scaffold, and a short set of standing rules — so the project begins organized instead of
  accreting mess. Interview-first: it asks before it writes. Better than a stock init because it
  captures intent, not just structure.
---

# onboard — start a new project the right way

> **Doctrine ownership: single writer.** One seat edits this file, the Fable design seat. Every other session, either brain, proposes changes through a guidance-file pull request (AGENTS.md "Guidance changes") or a team-board row, and the seat lands them. Set 2026-08-01 per ORDER 38 (two-writer endgame D3).

## What this is for

A project's first hour sets its habits. `/onboard` front-loads the structure and the standing context so future sessions (and future handoffs) have something to stand on.

## Procedure

1. **Interview first, one or two questions at a time.** Do not scaffold from a guess. Draw out:
   - What is this project, in one sentence, and who is it for?
   - What does "done" or "success" look like?
   - Hard constraints — deadline, stack, budget, compliance, house style, things to never do.
   - How does the user want to work with the agent here — anything that should always/never happen, an approval gate for outbound actions, formatting defaults?
   - Where should notes, decisions, and handoffs live?
   Stop interviewing once you genuinely understand the project, not before.
2. **Propose the scaffold before creating it.** Show the folder layout and the standing rules you intend to write; adjust on feedback.
3. **Generate, on approval:**
   - a **context file** (`CLAUDE.md` or `AGENTS.md`) capturing who/what/why, the standing rules, and where things live — lean, not bloated;
   - a **folder scaffold** matching the work (docs, source, notes/handoffs, whatever fits);
   - a short **standing-rules** section baked into the context file, in the user's own words from the interview.
4. **Point at the next step.** End by telling the user what the very first real task should be, and offer to start it.

## Notes

- Keep the generated context file short and true. A 200-line CLAUDE.md nobody reads is worse than a 30-line one that is accurate. Note that it should be edited in place as facts change.
- Match existing conventions if the folder is not actually empty — read what is there before imposing structure.
- Do not encode rules the user did not give you. An empty "standing rules" section is fine; invented rules are not.
