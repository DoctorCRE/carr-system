# Model tiering — which brain does which work

> **Doctrine ownership: single writer.** One seat edits this file, the Fable design seat. Every other session, either brain, proposes changes through the `teach` verb or a team-board row, and the seat lands them. Set 2026-08-01 per ORDER 38 (two-writer endgame D3).

*Joe-personal operating doctrine, created 2026-07-24 (orchestrator migration, the model-tiering lane; Joe's go same day). Task-loaded: read when running a recurring pipeline task, spawning subagents, or designing an automation. The plan behind it: DNA/Deal Management/system-evolution-plan.md. Joe's framing (7/24 handoff, verbatim): "fable is not doing anything except reasoning ... it's a little overkill and expensive for a lot of the task."*

## The tiers (pricing verified 2026-09-29 against [Anthropic's Sonnet 5 update](https://www.anthropic.com/news/claude-sonnet-5) and [Sonnet 5.5 announcement](https://www.anthropic.com/claude-sonnet-5-5); re-verify before quoting elsewhere)

| Tier | What runs here | Cost per M tokens in/out |
|---|---|---|
| **T0 — Code** | Anything deterministic. The carr-system repo scripts: board renders, feed builds, drift checks, the façade check. | $0, no model |
| **T1 — Haiku** (`claude-haiku-4-5`) | Mechanical work that needs language but no judgment: extraction from a known format, dedup/matching sweeps, formatting, file-content summaries used internally, classification with clear rules. | $1 / $5 |
| **T2 — Sonnet** (`claude-sonnet-5-5`, Sonnet 5.5, released 2026-09-28, 1M context; older records may still say `claude-sonnet-5`) | Routine reasoning: assembling a digest from structured data, scoring leads against written criteria, internal research summarization, first-pass triage. | $2 / $10 ($0.20 cache read) for Sonnet 5.5 and Sonnet 5. Sonnet 5's introductory $2 / $10 price became permanent on 2026-08-10. |
| **T3 — the top seat** (Opus 4.8 `claude-opus-4-8` $5/$25; Fable 5 $10/$50 when it's driving) | Planning, judgment, verification of lower-tier output, negotiation/deal thinking, anything ambiguous, and ALL client-facing voice. The project manager, per Joe's design. | $5/$25 – $10/$50 |

Rule of thumb: T3 decides and verifies; T2 reasons through the routine; T1 does the mechanical; T0 does the repeatable. Moving a step down one tier is a 40–80% cut on that step; T3→T1 is up to 10x.

## Hard floors — never downtier these

- **Anything a prospect or client could ever see** (writing-rules.md surfaces: outreach, social copy, proposals, cards, artifacts' visible prose). Voice work stays on the top seat, full stop.
- **X reply runs and LinkedIn engagement runs: Opus 4.8+ ONLY** — Joe's standing rules (memory: x-reply-run-opus-only, linkedin-engagement-run-opus-only). Check the model at run start; halt if below Opus. Tiering never overrides these halt-gates.
- **Deal judgment** — negotiation strategy, counter analysis, what-to-say-to-the-client.
- **Verification** — a lower tier never grades its own work; T3 (or code diffing) checks it. This is the checkability guardrail from the evolution plan applied to models. **Amendment (Jul 25, 2026 — Joe's go, Dell approved verbally to Joe same day):** this floor covers JUDGMENT verification (grading lower-tier reasoning, deal logic, audit scores, anything ambiguous). MECHANICAL existence checks — "does the cited source actually say what the finder claims" — may run T2 Sonnet: independence comes from fresh context and a different instance than the maker, not from model tier. The maker's own model/conversation still never checks itself, and when a check turns ambiguous mid-run it escalates to T3 rather than guessing.
- **Two-writer/shared-tier writes** — any write governed by dna-protocol stays with the session's top model (the write-verify sandwich is judgment work).

## Mechanics

- **Claude Code sessions (local):** the Agent tool takes a `model` parameter (`haiku`, `sonnet`, `opus`). Delegating a T1/T2 step = spawning a subagent with that model set. Shape rule unchanged (fan-out hygiene, rule `2b66211d`, DNA/compiled-rules-shared.md): simple fan-out only, narrow jobs, each worker sees only what its job needs.
- **Standing amendment to the subagent rule (Joe's tiering go, 7/24):** routine tiered delegation INSIDE a task Claude already owns (a heartbeat step, a sweep, a feed distill) is pre-approved — do it, report it in one line. Genuinely heavy or novel fan-outs are still suggest-first, Joe decides.
- **Cowork scheduled runs:** the app controls the parent model and no config pins it (memory: scheduled-run-model-not-config-pinnable). Tiering there means: the run delegates T1/T2 steps to cheaper subagents where the environment offers model control, and the Opus-only halt-gates protect voice runs regardless. Never promise a config fix.
- **When unsure which tier:** take the higher one. A wrong answer from Haiku costs more than the tokens saved; the savings target is volume routine work, not edge cases.
- **Harness-blocked delegation gets a ONE-TAP BUTTON, never silently absorbed (Joe's directive, 7/30, refined same day: "prompt me to approve it with a button click"):** some session types inject a harness instruction that bars unprompted subagent use, and it overrides this doctrine for that session (the 7/30 hunt confirmed the line lives in the app's harness, not in any file Joe controls). When a session is (a) harness-blocked AND (b) facing work with real delegable volume (a build, a sweep, a multi-file distill — not a quick Q&A), it opens its FIRST response with the clickable question widget, worded exactly (Joe's wording, 7/30): **"Do you want to delegate to lower models in this session?"** [Yes (Recommended) / No]. Yes = the harness's own "unless the user requested it" condition is met and this doctrine takes over; No or dismissed = everything top-seat, said in one line. Trivial sessions skip the prompt (a button asked when nothing is delegable is noise). Dismissed/unanswered = stay top-seat and say so in one line. Never promise a config fix; the switch is app-side.

## Where it applies first (the recurring spend)

The daily heartbeat, the Monday brief, the weekly radar digest, and the NPI sweep are the standing token spend. Each task file carries a one-line pointer here; within those runs the T1/T2 candidates are: Gmail sweep parsing (T1), registry/board cross-checks not yet in code (T1, and T0 candidates for the next code-conversion pass), digest assembly from structured feeds (T2), lead scoring against the written criteria (T2). The judgment layer of each run (what to surface to Joe, TOP 3 selection, anything client-adjacent) stays T3.

*Log tier changes that prove out (or fail) in decision-history so the monthly playbook review can tune this file. Savings become measurable once Joe supplies real C/H figures (parked 7/24, tracked in the evolution plan).*
