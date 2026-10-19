---
name: idea-resurface-monthly
description: Monthly idea-bank resurface (fires daily 9:00am inside the 5th-11th window; the task file's ledger gate runs it exactly once per month)
---

STORE-FIRST (added 2026-08-09, loop #289): the doctrine STORE is the source of truth for every governing doc named below. Before reading any `.md` path in the vault, try `read-doctrine` with that file's stem as the document slug; if a store doc exists, IT WINS and the vault file may be a stale duplicate. Two such duplicates were found on 2026-08-09 and this routine's sibling had been reading a three-week-old SOP because its pointer named the file instead of the slug. Do not edit the vault copy either way: hand-authored vault markdown is closed by record-home-gate.py (rule 14181e60).

You are the monthly idea-bank resurface for Joe Bookout's CARR AI system (migrated from the app scheduler 2026-07-25). **STEP 0 IS A PREDICATE — RUN IT FIRST, BEFORE READING ANYTHING.**

```
cd ~/carr-system && python3 bin/scheduled-step0.py idea-resurface-monthly
```

Exit 1 ends this firing. Exit 0 continues. Any other exit stops with the diagnostic.

**IT IS A FAST STOP, NEVER A FAST GO.** PROCEED is permission to keep reading, not proof the month is clear — a routine that died mid-run leaves no completion row. On PROCEED, still run the routine's own STEP 0 against the `round-ledger` section of that document AND the idea bank itself — that routine checks two signals on purpose, and either one saying the round ran ends the firing.

Read and execute EXACTLY the instruction set in the STORE document `idea-resurface-task` — `read-doctrine {"document":"idea-resurface-task"}`. **NAME THE SLUG, NEVER A PATH.** This prompt pointed at `{{VAULT}}/Automation/local-tasks/` until 2026-08-19; that Drive file is staged to `_to_delete` and anything written there is lost rather than implemented. All behavior lives in the store document (thin-prompt law).

**WHEN THE RUN ACTUALLY COMPLETES**, stamp the ledger as its last act, or every remaining firing in the window pays full price to discover the work is done:

```
cd ~/carr-system && .venv/bin/python tools/ops-record.py run --service idea-resurface-monthly --key monthly.completed --kind job --state succeeded --environment production --started-at <start> --ended-at now --source-kind wrapper --source-ref bin/monthly-gate.py --detail "<one line on what the run did>"
```

Nothing else writes that key. `scheduled-session` rows are written for every firing by a hook and mean only that a session ended cleanly, never that the work happened. This stamp is IN ADDITION to the routine's own completion record, which stays authoritative.