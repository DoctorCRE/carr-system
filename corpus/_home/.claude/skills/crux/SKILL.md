---
name: crux
description: Finds the strategic crux BEFORE anyone generates options — the step upstream of /options, /redteam, and /premortem. Use when the user says /crux, "what's the real problem here," "find the crux," "why does this gap exist," "are we solving the right problem," or whenever a request arrives as a deliverable ("we need a strategy/plan/system for X") or a symptom ("Y is dropping," "Z keeps failing") with no named cause. Runs a five-step sequence with completion tests — refuse the first framing, state the gap in one sentence, map candidate causes completely (MECE) before diagnosing, trace causes until the crux is named in plain language, then options tied only to causal levers, then three stress tests before any recommendation. Deliberately adversarial to premature narrowing: it pushes back when the candidate list is already shaped around the answer someone wanted. NOT for problems whose cause is already named and agreed (go straight to /options), and NOT a substitute for /redteam (which attacks a finished plan; this runs before a plan exists).
---

# /crux — always solve the right problem

> **Doctrine ownership: single writer.** One seat edits this file, the Fable design seat. Every other session, either brain, proposes changes through a guidance-file pull request (AGENTS.md "Guidance changes") or a team-board row, and the seat lands them. Set 2026-08-01 per ORDER 38 (two-writer endgame D3).

Adapted 2026-07-31 from George Nurijanian's "/find-the-strategic-crux" (prodmgmt.world, x.com/nurijanian/status/2072998065972420806), generalized for any domain. The core claim adopted whole: AI's best strategy role is ENFORCING THE SEQUENCE, not generating options. A longer option list on top of an empty diagnosis is worse than no help at all.

## The five steps, each with a completion test

**1. What is the problem?** Refuse the first framing. A deliverable ("we need a newsletter strategy") is not a problem. A symptom ("leads aren't converting") is not an explanation. Make the user spell out what is true today (specifics: numbers, behaviors, decisions) and what they want true instead, with a timeframe. The distance between the two states is the problem. COMPLETION TEST: one sentence the rest of the analysis must answer. Cannot state it cleanly = step 1 is not done.

**2. Where could it live?** Map candidate LOCATIONS, not causes yet. The list must cover the whole space without overlap (MECE). Push back hard when the list arrives pre-narrowed to someone's preferred answer — that narrowing is the most common way these analyses reach the wrong conclusion. Include the socially expensive candidates nobody wants to say out loud (undefined ownership, a missing mandate, a partner's lane) — putting them on the list is the assistant's job precisely because it costs a human more to say them first. COMPLETION TEST: the map is honestly complete, including the uncomfortable rows.

**3. Why does it exist?** Work the candidates: is each contributing, and how? Trace causal chains; ask what would have to be true for a candidate to be the root. Rank by evidence, not equal depth everywhere. Write the reasoning down so it can be pushed back on. COMPLETION TEST: the CRUX — one or two plain sentences naming the core reason the gap exists, such that fixing it makes the rest easier. Product-shaped, org-shaped, or incentive-shaped all count; the only requirement is that it is named before options exist.

**4. What could we do?** Options flow from the cause map. Every option must pull a lever on it; an option that does not connect to the crux gets cut, however attractive. For survivors: feasibility under real constraints, root-cause vs symptom, upside vs downside. The user's domain knowledge is essential here — ask rather than assume.

**5. What should we do?** Choose. Picture the world with the change in place: what else must change, which numbers should move. Then three stress tests: (a) what if a key assumption in the analysis is wrong? (b) what if execution goes well and the gap still does not close? (c) who pushes back, and how does that play out? Any blocking risk = reconsider. Otherwise state the recommendation: the action, why it beats the alternatives, and the first concrete move.

## Conduct

- Sequential and strict: no options before a named cause, no recommendation before the stress tests. Pillars feel like progress and diagnosis feels like delay — hold the line anyway.
- The output artifacts, always: the one-sentence gap, the candidate map, the crux sentence, the lever-tied option list, the stress-tested recommendation with its first move.
- Hand off downstream: the crux feeds /options for a full matrix, /premortem before committing, /decide to log the ruling.
- In the CARR system: log the crux and ruling with the **`log-decision`** verb (the crux sentence is exactly what its `title` wants, the diagnosis its `rationale`). `00_Context/decision-history.md` is a GENERATED render of those events — never hand-edit it.

## When NOT to use

The cause is already named and agreed (start at /options). The question is small enough that diagnosis is overhead. The user asked for critique of an existing plan (/redteam) or failure imagination (/premortem).
