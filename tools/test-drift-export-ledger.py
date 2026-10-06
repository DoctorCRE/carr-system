#!/usr/bin/env python3
"""test-drift-export-ledger.py — the vault-drift tamper verdict, decided against
the export ledger rather than against a once-a-night snapshot.

WHY THIS EXISTS. The tamper pillar of ops/vault-drift-watch.py compared every
registered file to the hash captured at the previous night's --rebaseline, and
called ANY difference "rewritten outside the nightly export". But the nightly
export is not the only legitimate writer of those files, and never was:
  · bin/refresh-rules.sh re-exports the five rule renders hourly, 7am-8pm;
  · any session running `CARR_EXPORT_LIVE=1 ./run.sh export` writes them;
  · every render carries an `Exported: <timestamp>` line, so its bytes change
    on every pass even when not one row of content changed.
refresh-rules.sh had already hit this and patched around it by re-baselining its
own five paths after each hourly run — a fix that works only for the paths that
one job knows to name, and 2026-08-14 produced the general case: 30 registered
files reported as TAMPER, every diff a single timestamp line.

THE ORACLE. exporters/common.py records sha256(written file) in
export_run.file_sha on every LIVE write, and only on live writes (draft runs
store none, since a draft hash describes out/exports/ and poisoned this exact
class of check in 2026-08-08). So the question "did the exporter write these
bytes, or did something else?" has an exact answer already in the database, and
ops/renders-verify.py already trusts it. The baseline remains the fallback for
any target the ledger does not cover.

THE VERDICT UNDER TEST — tamper if and only if the current bytes match NEITHER
the baseline snapshot NOR the last hash the exporter recorded for that target.
A hand-edit matches neither and is still caught, which is the property that
must not be traded away for quiet.
"""
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
from lib.loadpy import load_module_from_path  # noqa: E402

vdw = load_module_from_path("vault_drift_watch",
                            os.path.join(REPO, "ops", "vault-drift-watch.py"))

failures: list[str] = []
checked = 0


def check(name, cond, detail=""):
    global checked
    checked += 1
    if cond:
        print(f"  ok    {name}")
    else:
        print(f"  FAIL  {name}  {detail}")
        failures.append(name)


BASE = "a" * 64      # what last night's rebaseline captured
EXPORTED = "b" * 64  # what the exporter has since written and recorded
FOREIGN = "c" * 64   # what a hand-edit or a foreign writer would leave


def main():
    verdict = vdw.tamper_verdict

    # 1. Unchanged since the baseline. No ledger consulted, no finding.
    check("a file matching its baseline is clean",
          verdict("f.md", BASE, BASE, {}) is None)

    # 2. THE 2026-08-14 CASE. Changed since the baseline, but the bytes are
    #    exactly what the exporter recorded writing — the hourly rule refresh,
    #    a session's export, or a concurrent chain. Legitimate, not tamper.
    v = verdict("f.md", BASE, EXPORTED, {"f.md": {EXPORTED}})
    check("a file re-exported since the baseline is NOT tamper", v is None,
          f"verdict={v}")

    # 3. THE PROPERTY THAT MUST SURVIVE. Changed since the baseline AND the
    #    bytes are not what the exporter wrote. Still tamper, ledger or no.
    v = verdict("f.md", BASE, FOREIGN, {"f.md": {EXPORTED}})
    check("a hand-edit is still tamper when the ledger disagrees",
          v is not None and "does not match" in v, f"verdict={v}")

    # 4. A target the ledger does not cover falls back to the baseline, which is
    #    the old behaviour — conservative in the noisy direction, never blessing
    #    an unverified path.
    v = verdict("f.md", BASE, FOREIGN, {})
    check("a target absent from the ledger falls back to the baseline",
          v is not None and "no exporter-recorded hash" in v, f"verdict={v}")

    # 5. Deleted file: no bytes to match anything.
    v = verdict("f.md", BASE, None, {"f.md": {EXPORTED}})
    check("a deleted registered file is always a finding",
          v is not None and "deleted" in v, f"verdict={v}")

    # 6. The ledger must never make a MISSING baseline entry look clean — an
    #    unknown baseline is not a licence to accept whatever is on disk.
    v = verdict("f.md", None, FOREIGN, {"f.md": {EXPORTED}})
    check("no baseline entry is not treated as a pass", v is None or "match" in v,
          f"verdict={v}")

    # 7. The mapping the verdict depends on: target key -> vault relpath, taken
    #    from the live registry rather than a hand-kept list. If this drifts, the
    #    ledger lookup silently misses and every file reads as unledgered.
    paths = vdw.export_target_paths()
    check("target->path map is non-empty and points into the vault registry",
          len(paths) > 20 and any(p.endswith(".md") for p in paths.values()),
          f"{len(paths)} targets")
    check("the map covers the rule renders that move hourly",
          any("compiled-rules-shared" in p for p in paths.values()),
          f"sample={sorted(paths.values())[:3]}")

    # 8. DERIVED_DIRS coverage. Both wholesale-regenerated graph trees classify
    #    as derived, each naming its OWN generator — "Graph/" is a string prefix
    #    of "Graph-System/", and the trailing slash is the only thing keeping
    #    the two from shadowing each other's writer label. Pin all three edges:
    #    Graph/ derived, Graph-System/ derived under its own writer, and a
    #    sibling dir that merely starts with "Graph" still UNEXPECTED.
    tag = vdw.classify("Graph/leads/Brooke Tallis (lead).md", {}, {})
    check("Graph/ files classify as derived (run.sh graph)",
          "derived" in tag and "run.sh graph" in tag and "graph-system" not in tag,
          f"tag={tag}")
    tag = vdw.classify("Graph-System/nodes/foo.md", {}, {})
    check("Graph-System/ keeps its own writer label",
          "derived" in tag and "graph-system" in tag, f"tag={tag}")
    tag = vdw.classify("Graphics/logo.md", {}, {})
    check("a sibling dir starting with 'Graph' is still UNEXPECTED",
          tag == "UNEXPECTED", f"tag={tag}")

    print(f"\npassed {checked - len(failures)} · failed {len(failures)}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
