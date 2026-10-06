# docs/frontier-finding

WR-000046 Frontier Finding evidence: Artifacts A/B/C for migrations
0454-0471, plus the Artifact C break-glass receipt instrument.

## Files

- `breakglass_run.py` / `gen_breakglass_run.py` — the receipt driver and its
  run/restore bundle generator. Libraries only; see below.
- `breakglass_selftest.py` — the acceptance suite (3)/(3b)/(3c)/(4) against
  a disposable local Postgres. This one DOES run directly.
- `gen_census_matrix.py` — Artifact B generator (`census-reconciliation-matrix.v1.json`).
- `gen_frontier_manifest.py` — Artifact A generator, the OBSERVED effect
  manifest (`frontier-touched-objects.v1.json`).
- `parse_extract_targets.py` — the independent PARSED extractor for
  Artifact A's cross-check.
- `compare_targets.py` / `rerender_observed_comments.py` — the comparison
  seat's cross-check and rendering-bug patch (`comparison-report.md`).
- `*.sql`, `*.v1.json`, `*.md` — the evidence: queries, snapshots,
  manifests, dispositions, and written reports.

## Why the first six are libraries, not scripts

`ops/scac-mutation-inventory.mjs` seals the SCAC mutation registry at v10,
parked behind the integrity frontier. Any net-new `.py` with a shebang or an
`if __name__ == "__main__":` block becomes an unreviewed entrypoint against
that sealed registry (`isScriptEntrypoint()`). So these six are deliberately
import-only — no shebang, no `__main__`, each exposing
`main(argv=None) -> int`. Registering them as reviewed entrypoints is
deferred to the integrity activation program's registry re-pin (WR-000048),
where the break-glass tooling must be inventoried as reviewed rows, not
smuggled in by omission.

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
