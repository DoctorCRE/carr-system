#!/usr/bin/env python3
"""Run the seed-coverage check against the COMMITTED db/schema.sql.

WHY THIS EXISTS, and it is the gap the sixth review named. ops/snapshot-seed-
coverage.py has exactly one call site: bin/schema-snapshot.sh, against the
freshly composed candidate, just before the write. That is the right place to
refuse a bad snapshot, and it is the wrong place to notice a bad one that is
already committed -- because reaching it needs production read access, so nobody
running CI ever reaches it at all.

The consequence was not hypothetical. Twice in eight days a commit reclassified a
table to carried and added the generator's emit block WITHOUT regenerating the
snapshot, because regenerating needs credentials the author did not have in that
moment. #756 did it for doctrine_gate_check, the doctrine validation registry, so
a rebuilt database ran no doctrine gates at all. #762 did it for agent_profile, so
the bot brief would fail for every named profile. Both landed green. Both were
found later by hand. Nothing in CI could have said a word, because nothing in CI
ever read the committed file.

This closes that. It reads the artifact on disk and the classification beside it,
which is all the check needs -- no database, no credentials, no network. The same
function bin/schema-snapshot.sh calls, pointed at the file that is actually in the
repository.

WHAT A FAILURE HERE MEANS. Not that the check is broken. Either the snapshot needs
regenerating against production (bin/migrate-prod.sh does this, and bin/schema-
snapshot.sh --check reports it), or a classification entry was changed without the
matching emit block. The message names the table either way.
"""
from pathlib import Path
import importlib.util
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT = ROOT / "db" / "schema.sql"
CHECKER = ROOT / "ops" / "snapshot-seed-coverage.py"


def load_checker():
    spec = importlib.util.spec_from_file_location("snapshot_seed_coverage", CHECKER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> int:
    if not SNAPSHOT.exists():
        print(f"  FAIL {SNAPSHOT} does not exist")
        return 1
    module = load_checker()
    artifact = SNAPSHOT.read_text(encoding="utf-8", errors="replace")

    # NO INPUT FLOORS HERE, and the reason is worth recording because an earlier
    # version had them and they were decoration. They asserted the ledger held at
    # least 100 migrations and detection found at least 50 tables, meaning to catch
    # a collapsed read reporting green over nothing. They could never fire: with the
    # ledger emptied, check() itself returns 90 findings, one per classified table
    # that nothing seeds any more, and it returns them before any floor could speak.
    # Measured, not assumed. A guard that cannot reach its own failure case is not a
    # guard, and leaving it in place claims a protection that check() was already
    # providing more loudly. Found by the seventh independent review.
    applied = module.applied_migrations(artifact)
    seeds, _missing = module.seeded_tables(str(ROOT), applied)

    # A BUDGET, because the cost of this check is not a fixed price. scan_sql was
    # quadratic in artifact size -- 1.5s at 0.5MB, 7.4s at 1MB, 28.9s at 1.5MB --
    # and db/schema.sql is 2.3MB and grows with every migration. ops/ci.sh gives
    # each gate selftest CI_SELFTEST_TIMEOUT_SECONDS and, on exit 124, sets
    # gates_timed_out and BREAKS: a check that eventually runs long does not fail
    # alone, it abandons every remaining gate selftest in the class. Correctness
    # cases cannot see this - the quadratic form returns identical answers - so the
    # property is asserted here or nowhere. Measured at 5s; the budget is six times
    # that, loose enough for a loaded machine and tight enough to catch a return to
    # the shape that cost 37s.
    started = time.monotonic()
    failures = module.check(str(ROOT), artifact)
    elapsed = time.monotonic() - started
    if elapsed > 30.0:
        print(f"  FAIL the seed-coverage check took {elapsed:.1f}s against a 30s budget. "
              f"It is not merely slow: on timeout ops/ci.sh abandons the remaining gate "
              f"selftests in this class. Profile scan_sql before raising this number.")
        return 1
    if failures:
        print(f"  FAIL the committed db/schema.sql does not pass its own seed-coverage check "
              f"({len(failures)} finding(s)). A database rebuilt from this file would come up "
              f"short. Regenerate it with bin/schema-snapshot.sh, or fix the classification.\n")
        for failure in failures:
            print("  " + failure.replace("\n", "\n  ") + "\n")
        return 1

    print(f"  ok   the committed db/schema.sql passes seed coverage "
          f"({len(applied)} applied migrations, {len(seeds)} seeded tables classified, "
          f"checked in {elapsed:.1f}s)")
    print("snapshot seed-coverage committed-artifact selftest: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
