---
name: options
description: >
  Turns a fork in the road into a clear options matrix with a recommendation, instead of a
  wishy-washy "it depends." Use when the user says /options, "lay out the options," "what are my
  choices here," "compare the approaches," "give me a decision matrix," "pros and cons of each," or
  is visibly stuck between paths. Produces 2–4 real, distinct options; the few criteria that
  actually matter for THIS decision; how each option scores against them; the key tradeoff in one
  line each; and a specific recommendation with its reasoning — not a shrug. Feeds naturally into
  the decide skill once a choice is made.
---

# options — structured decision support

> **Doctrine ownership: single writer.** One seat edits this file, the Fable design seat. Every other session, either brain, proposes changes through a guidance-file pull request (AGENTS.md "Guidance changes") or a team-board row, and the seat lands them. Set 2026-08-01 per ORDER 38 (two-writer endgame D3).

## What this is for

When facing a choice, a good options matrix beats both an unstructured ramble and a false-confidence single answer. `/options` lays the real alternatives side by side and then commits to a recommendation.

## Procedure

1. **Frame the actual decision** in one line, so the options are answering the same question.
2. **Name 2–4 genuinely distinct options.** Real alternatives, not one plan and two strawmen. If there are really only two, give two. Include the "do nothing / defer" option when it is live.
3. **Pick the criteria that matter for THIS decision** — usually 3 to 5 (e.g. cost, speed, reversibility, effort, risk, fit). Do not use a generic template; choose what actually drives this call.
4. **Build the matrix:** options as rows, criteria as columns, a short honest cell in each (a rating plus a few words, not just High/Low). Render it as a markdown table.
5. **One-line tradeoff per option** — the essential "you get X but pay Y."
6. **Recommend one, and say why.** Name the option you'd pick, the reasoning, and the condition under which you'd switch to the runner-up. A recommendation the user can disagree with is the deliverable; "any of these could work" is a failure.

## Notes

- Weight criteria if they are not equal — say which one is doing most of the work in your recommendation.
- Surface the reversibility of each option explicitly; a cheap-to-undo choice deserves less agonizing than a one-way door.
- When the user commits, offer to log it with the decide skill so the rationale is not lost.
