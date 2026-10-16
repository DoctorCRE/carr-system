# docs/frontier-finding

Break-glass tooling and retained WR-000046 Frontier Finding research for
migrations 0454-0471.

## Files

- `breakglass_run.py` / `gen_breakglass_run.py` — the receipt driver and its
  run/restore bundle generator. Libraries only; see below.
- `breakglass_selftest.py` — the acceptance suite (3)/(3b)/(3c)/(4) against
  a disposable local Postgres. This one DOES run directly.
- `breakglass-snapshot.sql` / `census-queries.sql` define the driver's snapshot queries.
- The retained reports, review transcript, and target dispositions need record-layer import verification before removal.

## Import-only libraries

The driver and generator expose `main(argv=None) -> int`. They remain
import-only so the SCAC mutation registry sees the reviewed launcher as
the entrypoint.

## Running break-glass

The libraries still cannot execute directly — that is permanent and on
purpose. The WR-000048 registry re-pin added the honest front door:
`tools/run-breakglass.py`, a TRACKED, REVIEWED launcher inventoried as a
break-glass-class entrypoint (beside `tools/db-tap.py` and
`tools/call-verb.py`), wrapping the same sys.path shim the selftest proves.
The earlier out-of-index interim shim (`out/frontier-finding/bin/
run_breakglass.py`) is retired by that registration and should be deleted
where found.

```
CARR_BREAK_GLASS=1 .venv/bin/python tools/db-tap.py --reason "<WR-000046 note ref>" \
    run tools/run-breakglass.py -- \
    --approved <run>.sql --receipt <receipt>.json
```
