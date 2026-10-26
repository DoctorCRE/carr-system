---
name: loose-ends
description: >
  Sweeps a project for unfinished business and hands back a prioritized list. Use when the user says
  /loose-ends, "what's left," "any loose ends," "find the open threads," "what did we leave hanging,"
  "sweep for TODOs," or before wrapping up a work session. Scans for: TODO/FIXME/HACK markers,
  half-finished threads in recent work, dangling "flagged for later / come back to this" notes, open
  items in TODO or open-loops files, decisions left unmade, and stale docs that no longer match
  reality. Returns them grouped and ranked by urgency — it reports and proposes, it does not fix
  them without a go-ahead.
---

# loose-ends — find what's still open

> **Doctrine ownership: single writer.** One seat edits this file, the Fable design seat. Every other session, either brain, proposes changes through a guidance-file pull request (AGENTS.md "Guidance changes") or a team-board row, and the seat lands them. Set 2026-08-01 per ORDER 38 (two-writer endgame D3).

## What this is for

Work leaves a trail of half-done things: a TODO dropped mid-file, a "we'll circle back" that never circled, a doc that describes how the code used to work. `/loose-ends` gathers them so nothing quietly rots.

## Procedure

1. **Scan the likely sources:**
   - inline markers — `TODO`, `FIXME`, `HACK`, `XXX`, "come back to", "flag for later" — across the working files (or the whole project if the user wants a full sweep);
   - the project's TODO / open-items / open-loops file, if one exists (in CARR, `open-loops.md` and `open-loops-backlog.md` are GENERATED renders — read them, but any item you open or close goes through `add-loop` / `update-loop` / `close-loop`, never an edit to the file);
   - recent work in this session for threads that were started and not closed;
   - decisions raised but never settled;
   - docs or context files that reference things that have since changed (stale-doc catch).
2. **Group and rank.** Cluster by kind (code TODOs / open decisions / dangling threads / stale docs), and within each, order by urgency — what blocks something else, or is about to cause a wrong turn, comes first.
3. **Report, with locations.** Each item gets a one-line description and its exact file path or reference, so it is actionable without further hunting.
4. **Offer to act — do not auto-fix.** Ask which the user wants handled now; some loose ends are intentional.

## Notes

- Scope the sweep to what the user means: "this session's loose ends" vs. "the whole project." Default to the current working area unless they ask for everything.
- Distinguish real loose ends from deliberate parking. A `TODO` that says "TODO: not doing this until v2" is a decision, not a gap — note it as such.
- Flag stale docs specifically; they are the most dangerous loose end because they actively mislead future sessions.
