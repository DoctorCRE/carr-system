---
name: til
description: >
  Captures a hard-won lesson or gotcha into a project lessons file the moment it's discovered, so no
  future session rediscovers the same landmine. TIL = "today I learned." Use when the user says
  /til, "log this lesson," "note this gotcha," "save this so we don't hit it again," "remember this
  for next time," or right after hitting a non-obvious snag, a surprising behavior, or a fix that
  took real effort to find. Appends a dated, one-entry note — what was expected, what actually
  happened, the cause, and the takeaway — to a lessons/gotchas file (creates one if none exists).
  The portable version of a handoff packet's "watch-outs" section, captured continuously instead of
  at the end.
---

# til — capture a lesson before it's forgotten

> **RECORD-BACKED PROJECTS (fixed 2026-08-06, loop #142's sweep):** in a project
> with a record layer (CARR), a lesson is NOT a LESSONS.md row — a standing
> lesson in the partner's words goes through the `teach` verb (proposed, then
> activated on his yes), and a one-off gotcha goes through `log-decision` so it
> renders into decision-history. The file mechanics below apply ONLY to projects
> with no record layer.


> **Doctrine ownership: single writer.** One seat edits this file, the Fable design seat. Every other session, either brain, proposes changes through a guidance-file pull request (AGENTS.md "Guidance changes") or a team-board row, and the seat lands them. Set 2026-08-01 per ORDER 38 (two-writer endgame D3).

## What this is for

The costliest gotchas are the ones a project hits twice. `/til` writes down a hard-won lesson the moment it happens, so future sessions (and handoff/catchup) inherit it instead of relearning it the hard way.

## Procedure

1. **Locate or create the lessons file.** Look for `LESSONS.md`, `gotchas.md`, or wherever the project's context file says such notes live. If none exists, create `LESSONS.md` at the project root and say so.
2. **Write one tight entry:**
   - **Date.**
   - **What I expected** — the assumption going in.
   - **What actually happened** — the surprise.
   - **Why** — the underlying cause, as far as it's known.
   - **Takeaway** — the rule or check to apply next time, phrased as actionable guidance ("always X before Y", "don't assume Z").
3. **Append, don't overwrite** — it's a growing ledger.
4. **Confirm** the one-line takeaway so the user can sharpen it.

## Notes

- The takeaway is the payload. A lesson with a vivid story but no "so next time, do X" is half-captured.
- Keep entries to genuine, non-obvious lessons — things a competent person would plausibly get wrong again. Don't log the trivially obvious; it dilutes the file.
- When the same lesson keeps recurring, that's a signal it belongs in the project's context file (CLAUDE.md/AGENTS.md) as a standing rule, not just the lessons log — flag that when you see it.
