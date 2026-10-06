"""Run the Wave 1 exporters. Usage:
  python -m exporters.run_exports [--only <target>] [--bootstrap]
Draft by default; CARR_EXPORT_LIVE=1 activates vault paths (cutover only).
"""
import argparse, os, sys
from .common import EXPORT_HOME, LIVE, run_export, wait_for_provider
from .targets import TARGETS

def select(only):
    """Exact target key, else every key with that prefix.

    The prefix form exists so `--only compiled-rules` refreshes both the shared
    and the personal rules file in one call — that is the command a session runs
    after a teach, and having to remember two target names would guarantee one of
    them goes stale.
    """
    if only is None:
        return TARGETS
    if only in TARGETS:
        return {only: TARGETS[only]}
    hits = {k: v for k, v in TARGETS.items() if k.startswith(only)}
    if not hits:
        sys.exit(f"no target matches '{only}'. known: {', '.join(sorted(TARGETS))}")
    return hits

def md_renders_retired():
    """THE CUTOFF FLAG (doctrine-store P5, decisions 20dfdfcc + 82a2fb62):
    when system_config doctrine.md_renders_retired = 'true', every .md target
    is skipped — the store serves doctrine and records; markdown projections
    are over. Non-md targets (xlsx working sheets, json feeds, html boards)
    are untouched. Fails OPEN (renders keep rendering) on any read error so a
    dead config lookup can never silently kill the render fleet pre-cutoff.

    TEST HOOK, never set by a job or a shell profile: CARR_MD_RENDERS_RETIRED
    pins the answer ('1' on, '0' off) so a selftest can exercise both sides of
    the cutoff without its result depending on which day production fired it.
    ops/vault-drift-watch-selftest.py builds a FIXTURE vault whose registry file
    is DNA/compiled-rules-shared.md; once the real flag went true its six
    registry-path cases started failing against a live config value they never
    meant to read. The hook lives HERE, in the one function every caller shares,
    rather than in each caller — a second place to decide this is the drift the
    single flag function exists to prevent."""
    pinned = os.environ.get("CARR_MD_RENDERS_RETIRED")
    if pinned is not None:
        return pinned.strip().lower() in ("1", "true", "yes")
    try:
        from .common import connect
        with connect() as conn, conn.cursor() as cur:
            cur.execute("select value #>> '{}' from system_config "
                        "where key = 'doctrine.md_renders_retired'")
            row = cur.fetchone()
            return bool(row) and str(row[0]).lower() == "true"
    except Exception:
        return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only")
    ap.add_argument("--bootstrap", action="store_true")
    a = ap.parse_args()
    targets = select(a.only)
    if md_renders_retired():
        dropped = [k for k, (rel, _fn) in targets.items() if rel.lower().endswith(".md")]
        for k in dropped:
            print(f"[{k}] RETIRED — md renders ended at the doctrine-store cutoff; "
                  f"the store serves this (standing-context / read-doctrine / catch-me-up)")
        targets = {k: v for k, v in targets.items() if k not in dropped}
        if not targets:
            sys.exit(0)
    # ONE WAIT FOR A CONDITION ALL SIX SHARE. keep_generation() reads each
    # target's PREVIOUS copy out of OneDrive, and on this Mac those copies are
    # cloud-only, so every target needs the File Provider to be up. Waiting per
    # target spends six sequential 23.5s budgets on one shared outage and
    # reports six tracebacks for a single cause; waiting here spends one budget
    # and reports its observations. An exhausted budget does NOT abort the sweep: each
    # target still runs and records its own receipt, so a provider that never
    # wakes produces the same honest per-target failures it does today rather
    # than a new way for the step to die before it starts.
    if LIVE:
        result = wait_for_provider(EXPORT_HOME / rel for rel, _fn in targets.values())
        if result.cold:
            names = ", ".join(sorted(path.name for path, _e in result.cold))
            print(f"[provider] giving up the wait; {len(result.cold)} file(s) still "
                  f"unreadable ({names}). {result.diagnostic()}. Each target will "
                  f"now try and report for itself.", file=sys.stderr)

    # NOT `all(...)`: it short-circuits, so ONE failing target silently cancels every
    # target after it in dict order. That is exactly what bit on 2026-08-02 — an
    # unbootstrapped decision-history target aborted the whole nightly export sweep and
    # five generated files went stale, while the chain reported only the first failure.
    # Run every target, then fail if any did.
    #
    # AND NOT A BARE COMPREHENSION EITHER, which is how the SAME outcome came back
    # through a different door on 2026-08-25 through 08-27 (loop #535). run_export
    # returns False on a failure it HANDLES, but keep_generation() re-raises OSError
    # once its EDEADLK retry budget is spent — by design, since silently skipping the
    # dated copy would discard the rollback guarantee. That exception escaped the
    # comprehension and killed every remaining target. `curriculum` is the first key
    # in TARGETS and its OneDrive file locks intermittently around the 02:05 window,
    # so one transiently unreadable file took down all six exports for three nights
    # and left five receipts stale.
    #
    # Catching per target is what makes the comment above TRUE rather than merely
    # intended: an unhandled error becomes this target's failure, and the sweep goes
    # on to the next one. Bare `except Exception` is deliberate — the contract here is
    # blast radius, not diagnosis, and narrowing it to OSError would let the next
    # unexpected exception type re-open the exact hole this closes.
    results = []
    for k, (rel, fn) in targets.items():
        try:
            results.append(run_export(k, rel, fn, bootstrap=a.bootstrap))
        except Exception as e:
            print(f"[{k}] FAILED (unhandled {type(e).__name__}): {e}", file=sys.stderr)
            results.append(False)
    failed = [k for k, r in zip(targets, results) if not r]
    if failed:
        print(f"\n{len(failed)} of {len(results)} target(s) FAILED: {', '.join(failed)}")
    sys.exit(0 if not failed else 1)

if __name__ == "__main__":
    main()
