---
name: premortem
description: >
  Runs a premortem on a plan before committing to it — imagines the plan has already failed
  spectacularly and works backward to the causes. Use when the user says /premortem, "run a
  premortem," "assume this failed, why," "what could kill this," "imagine this went wrong," or
  before committing to anything expensive or hard to reverse. Distinct from redteam: redteam argues
  against the plan now; premortem time-travels to the failure and traces the path there, which
  surfaces different, often more concrete risks. Produces the top failure paths ranked by
  likelihood-times-damage, the earliest warning sign for each, and the cheapest hedge.
---

# premortem — assume it failed, find out why

> **Doctrine ownership: single writer.** One seat edits this file, the Fable design seat. Every other session, either brain, proposes changes through a guidance-file pull request (AGENTS.md "Guidance changes") or a team-board row, and the seat lands them. Set 2026-08-01 per ORDER 38 (two-writer endgame D3).

## What this is for

A premortem beats a risk list because it forces a specific story. Standing at "this already failed," the mind produces concrete causes instead of vague caveats. Run it before a big or hard-to-undo commitment.

## Procedure

1. **Set the scene.** State the plan and a failure horizon: "It's [some time] from now and this plan failed badly." Be specific about what "failed" means for this particular plan.
2. **Generate failure paths.** List the distinct ways it got there — each as a short causal story ("X happened, which meant Y, so it collapsed"), not a one-word risk. Aim for the 4–6 most plausible, not an exhaustive dump.
3. **Score each** on likelihood and on damage-if-it-happens (a quick High/Med/Low each is enough), and order by the combination. The top one or two are where attention goes.
4. **For each top path, give:**
   - the **earliest warning sign** — what you'd see first, while it's still cheap to change course;
   - the **cheapest hedge** — the smallest thing done now that most reduces this path.
5. **Close with a call:** proceed as-is, proceed with the top hedges added, or rework the plan.

## Notes

- Concreteness is everything. "Scope creep" is not a failure path; "we kept saying yes to small additions and missed the deadline by three weeks" is.
- Do not pad. Two vivid, likely failure paths with real hedges beat eight generic ones.
- This is analysis, not a veto. The output is meant to strengthen the plan or kill it honestly, whichever the evidence supports.
