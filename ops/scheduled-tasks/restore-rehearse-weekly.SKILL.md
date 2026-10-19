---
name: restore-rehearse-weekly
description: Weekly backup restore rehearsal with a deterministic PASS/FAIL report.
---

Run this command once in one Bash call. The parser reads the completed stdout
and stderr, including teardown, and prints the report.

```bash
cd ~/carr-system && ./run.sh restore-rehearse 2>&1 | python3 tools/restore-rehearse-report.py
```

Report the parser output verbatim. On FAIL, stop and raise the report as the
highest-priority item. Do not retry, repair a dump, or edit the restore script.
