---
name: understand-check
description: >
  Before doing expensive or ambiguous work, echoes back the task and assumptions in a few lines so
  the user can confirm or correct BEFORE the effort is spent. Use when the user says
  /understand-check, "play it back to me," "confirm you get this," "restate the task," "what's your
  understanding," or at the start of any large, ambiguous, or hard-to-reverse task where building
  the wrong thing well would be costly. Produces: a one-paragraph restatement of the goal, the
  explicit assumptions being made, what's deliberately out of scope, and the open questions that
  would change the approach — then waits for a green light. Cheap insurance against wasted sessions.
---

# understand-check — echo the ask before building

> **Doctrine ownership: single writer.** One seat edits this file, the Fable design seat. Every other session, either brain, proposes changes through a guidance-file pull request (AGENTS.md "Guidance changes") or a team-board row, and the seat lands them. Set 2026-08-01 per ORDER 38 (two-writer endgame D3).

## What this is for

The most expensive failure is building the wrong thing competently. `/understand-check` spends thirty seconds up front to prevent it: a short playback the user can correct before any real work starts.

## Procedure

Produce a compact confirmation, not an essay:

1. **The goal, restated** in your own words — one short paragraph. If your restatement drifts from what the user said, that gap is exactly what this catches.
2. **Assumptions I'm making** — the things you've inferred rather than been told, as an explicit list. These are the likeliest source of a wrong turn.
3. **Out of scope** — what you're deliberately NOT doing, so silent scope mismatches surface now.
4. **Questions that would change the approach** — only the ones whose answers actually alter what you'd build (not trivia). If there are none, say so.

Then **stop and wait** for confirmation or correction. Do not begin the work in the same breath.

## Notes

- Right-size it. For a small, clear task this is one or two lines, or skip it entirely — do not bureaucratize simple requests.
- The assumptions list is the heart of it. Surface the inferences you're least sure about first.
- If the user's answer reveals a materially different task, re-check briefly rather than charging ahead on the correction.
- Pairs well with options (when the "question that would change the approach" is really a fork) and with premortem/redteam (once the understanding is confirmed but the plan is big).
