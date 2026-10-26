---
name: catchup
description: >
  Orients a fresh session on a project that already has history — the receiving end of a handoff.
  Use when the user opens a new or cold session and says /catchup, "get me up to speed," "where did
  we leave off," "what's the status here," "brief me on this project," or "catch me up." Reads the
  most recent handoff packet, continuation notes, decision log, open-items/TODO file, recent commits
  or changelog, and the project's context files, then briefs BOTH the session (so it can act) and
  the user (in a few tight lines): where things stand, what was decided, and the single most useful
  next action. Read-and-orient only — it does not start doing the work until the user says go. Pairs
  with the handoff skill: handoff writes the baton, catchup receives it.
---

# catchup — orient a fresh session on existing work

> **Doctrine ownership: single writer.** One seat edits this file, the Fable design seat. Every other session, either brain, proposes changes through a guidance-file pull request (AGENTS.md "Guidance changes") or a team-board row, and the seat lands them. Set 2026-08-01 per ORDER 38 (two-writer endgame D3).

## What this is for

The inverse of a handoff. When a session starts cold on a project that already has history, `/catchup` reconstructs the state fast and briefs both the session and the user, so no one re-reads the whole history by hand.

## Procedure

1. **Find the trail, newest first.** Look for, in rough priority order:
   - a handoff packet (a `handoffs/` folder, a `HANDOFF*.md`, or a pasted packet in the conversation),
   - an open-items / TODO / open-loops file,
   - a decision log (`decisions*.md`, `decision-history*.md`, an ADR folder),
   - (in CARR these are GENERATED renders — `open-loops.md`, `open-loops-backlog.md`, `action-required.md`, `team-loops.md`, `decision-history.md`, `idea-bank.md`. Read them freely; they are current as of the last export. But treat the record layer as truth and never write back into them.)
   - a changelog or recent version-control history,
   - the project's context files (`CLAUDE.md`, `AGENTS.md`, `README`).
   Read what exists; skip what does not. Do not invent a trail that is not there.
2. **Reconstruct four things:** where the work stands (done / in progress / untouched), the decisions already settled and why, what is open, and the most valuable next action.
3. **Brief the user in ~5 lines** — plain, scannable: *Status · Last decision · Open questions · Recommended next step.* No wall of text.
4. **Brief the session (internally):** load the files the work will need so you can act immediately once the user says go.
5. **Then stop and ask** whether to pick up the recommended next step or something else. Catchup orients; it does not silently start executing.

## Notes

- If there is genuinely no history to catch up on (new project, empty folder), say so and offer to run the onboard skill instead.
- If a handoff packet exists, treat it as the primary source and reconcile it against current file state — flag anything in the packet that no longer matches reality (a path that moved, a decision since reversed).
- Keep the user-facing brief honest about gaps: "no decision log found, reconstructed from commits" beats false confidence.
