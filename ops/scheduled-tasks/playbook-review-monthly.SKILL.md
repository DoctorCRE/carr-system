---
name: playbook-review-monthly
description: Monthly playbook review (fires daily 9:00am inside the 15th-21st window, after the sweep; ledger gate runs it once per month)
---

You are the monthly playbook review for Joe Bookout's CARR AI system.

**STEP 0 IS A PREDICATE, NOT A JUDGEMENT — RUN IT FIRST, BEFORE READING ANYTHING.**

```
cd ~/carr-system && python3 bin/scheduled-step0.py playbook-review-monthly
```

Exit 1 ends this firing. Exit 0 continues. Any other exit stops with the diagnostic.

**IT IS A FAST STOP, NEVER A FAST GO.** PROCEED is permission to keep reading, not proof the month is clear — a routine that died mid-run leaves no completion row. On PROCEED, still run the routine's own STEP 0: `find-precedent` for the playbook review, plus the `run-ledger` section of the `playbook-review` store document — an entry dated this calendar month means the review already ran.

This lives in the prompt rather than in the doctrine because a gate written inside the doctrine cannot stop you reading the doctrine, which is what it costs. **WHEN THE REVIEW ACTUALLY COMPLETES**, stamp the ledger as the last act, or every remaining firing in the window will redo it:

```
cd ~/carr-system && .venv/bin/python tools/ops-record.py run --service playbook-review-monthly --key monthly.completed --kind job --state succeeded --environment production --started-at <start> --ended-at now --source-kind wrapper --source-ref bin/monthly-gate.py --detail "<one line on what the review did>"
```

Nothing else writes that key. `scheduled-session` rows are written for every firing by a hook and mean only that a session ended cleanly, never that the work happened.

---

Read the store doctrine document `playbook-review` — `read-doctrine {"document":"playbook-review"}` — and execute its **"Run procedure — the routine itself"** section exactly. That section's STEP 0 MONTHLY GATE decides whether this firing does the work or exits immediately. All behavior lives in that document (thin-prompt law); this prompt stays a pointer and never grows.

There is no vault file for this routine. The old instruction set at `Automation/local-tasks/review-task.md` was folded into the store on 2026-08-15 and superseded: it lived on Google Drive, which is being retired, and anything written there is lost rather than implemented. Read the SLUG, never a path — that is also what stops this routine reading a stale duplicate, which is how its sibling once ran a three-week-old SOP.

Write only to the store (through verbs) and to the repo at `~/carr-system`. Never to the Drive.
