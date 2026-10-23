# docs/frontier-finding

Break-glass tooling and retained WR-000046 Frontier Finding research for
migrations 0454-0471.

## Files

- `breakglass_run.py` / `gen_breakglass_run.py` — the receipt driver and its
  run/restore bundle generator. Libraries only; see below.
- `breakglass_selftest.py` — the acceptance suite (3)/(3b)/(3c)/(4) against
  a disposable local Postgres. This one DOES run directly.
- `breakglass-snapshot.sql` / `census-queries.sql` define the driver's snapshot queries.
- The research notes, review trail, reports and dispositions were imported into the record layer (doctrine document `frontier-finding-evidence`); their repo copies are staged under `_to_delete/frontier-finding/` for removal.

## Import-only libraries

The driver and generator expose `main(argv=None) -> int`. They remain
import-only so the SCAC mutation registry sees the reviewed launcher as
the entrypoint.

## Historical launcher

The one-time source-repair launcher is retired. Use forward migrations and the
normal deployment path for current changes. Git history retains its receipt
procedure.
where found.
