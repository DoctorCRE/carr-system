---
name: decide
description: >
  Records a decision AND its rationale to a durable decision log, so future sessions never
  relitigate a settled choice. Use when the user says /decide, "log this decision," "record why we
  chose this," "note that we decided," "write this down so we don't rehash it," or right after any
  real fork is settled in conversation. Captures: the decision, the date, the reasoning, the
  alternatives considered and why they lost, and any conditions that would reopen it. Writes through the project's decision
  verb when one exists (in CARR that is `log-decision`, whose render is the generated
  `decision-history.md`); appends to a file only where no such verb exists, creating one if needed. The highest-leverage habit in long AI
  projects — the number-one cross-session pain is re-explaining why a choice was made.
---

# decide — durable decision + rationale log

> **Doctrine ownership: single writer.** One seat edits this file, the Fable design seat. Every other session, either brain, proposes changes through a guidance-file pull request (AGENTS.md "Guidance changes") or a team-board row, and the seat lands them. Set 2026-08-01 per ORDER 38 (two-writer endgame D3).

## What this is for

The worst recurring friction in long-running projects is a later session reopening a question that was already settled, because the *why* was never written down. `/decide` fixes that with a one-entry, append-only log that future sessions (and the catchup/handoff skills) read.

## Procedure

1. **Locate the log, and find out whether it is a FILE or a RECORD.** Look for an existing decision log (`decisions.md`, `decision-history.md`, `docs/adr/`, or wherever the project's context file says such things live).

   **If the log is a generated projection of a database, do NOT write to it and do NOT create a parallel file beside it.** Use the verb that records a decision. In CARR that verb is **`log-decision`** (title, rationale, `human_quote` = the partner's literal words, optional `session_key`); `00_Context/decision-history.md` is its render and hand-editing it loses the entry on the next export. Do not use `add-loop` marker:`decision` for a settled ruling — that marker means an OPEN question awaiting one. Do not use `teach` either, unless the decision is a standing rule meant to bind future sessions.

   Only if no log and no verb exist, create `decisions.md` at the project root and say so.

   > This step exists because on 2026-08-02 a session with a settled decision to record found `decision-history.md` generated and unwritable, and responded by hand-authoring a `DECISIONS.md` full of prose that nothing enforced — solving 'decisions get lost in prose' with more prose. The verb it needed did not exist yet; it does now.
2. **Capture the entry** with these fields, tight:
   - **Decision** — what was chosen, in one line.
   - **Date.**
   - **Why** — the reasoning that actually drove it.
   - **Alternatives considered** — the real options that lost, and the one-line reason each lost. This is what stops the relitigation.
   - **Reopen-if** — the condition(s) under which this should be revisited (omit if none).
3. **Append, never overwrite.** The log is a ledger. New decisions go at the bottom (or top, matching the file's existing order); correcting a past decision means a NEW entry that references the old one, not editing history.
4. **Confirm** what was logged in one line so the user can catch an error.

## Notes

- Keep it to genuine decisions — forks with a real alternative that was rejected. Do not log every trivial choice; that buries the signal.
- Rationale is the point. "We chose Postgres" is nearly useless later; "chose Postgres over SQLite because we expect concurrent writers by v2" is what a future session needs.
- If the user states a decision that contradicts a logged one, flag the conflict and log it as a reversal with the new reasoning, rather than silently overwriting.
