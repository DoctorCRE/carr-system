#!/usr/bin/env python3
# doctrine: snapshot-rebuild-fidelity
"""The corpus push must judge each root on its own, and must not revive the vault.

WHY THIS EXISTS (2026-08-23). `tools/corpus-sync.py --push` gated the entire run
on one probe: unless the vault resolved as a directory, it returned 78 and
pushed nothing. That was correct while every row in the corpus set lived in the
vault. It stopped being correct when the set gained `home:` rows, which address
~/.claude/skills on this Mac and need no Drive at all — twelve of the fifty-four.
After the 2026-08-19 cutoff retired the vault, one dead root was switching off a
dozen live rows. A step with a live half and a dead half fails whole, and the
live half goes quiet without anyone deciding it should.

AND THE SUBTLER HALF, which is the one worth a permanent test. The obvious repair
is to probe each root for reachability. That is wrong here and would have been
worse than the bug: the Drive mount still resolves on this Mac, so a reachability
probe says yes and the push would rewrite retired doctrine renders back into the
vault the cutoff turned off. Being able to write somewhere is not permission to.
Drive-rooted rows therefore move only inside an explicitly opened recovery
envelope, and the retirement — not the mount — is what decides them.

Both properties are asserted here because each one alone is satisfied by a
version that breaks the other: gate on reachability and the vault comes back;
gate on the vault for everything and the home rows go quiet.

Needs no Drive, no network and no database: it calls the classifier directly.

Run: python3 ops/corpus-push-root-split-selftest.py
"""
from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("corpus_sync", REPO / "tools" / "corpus-sync.py")
assert spec and spec.loader
corpus = importlib.util.module_from_spec(spec)
spec.loader.exec_module(corpus)

failures: list[str] = []
checked = 0


import pathlib, sys  # noqa: E401
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))  # repo root, for lib/
from lib.gate_check import Check  # noqa: E402

check = Check(detail="  {detail}", failures=failures, counters={"checked": "all"}, ns=globals())


def main() -> int:
    rows = [rel for rel, _klass, _why in corpus.load_set()]
    vault_rows = [r for r in rows if ":" not in r]
    home_rows = [r for r in rows if r.startswith("home:")]

    # Never let this pass over an empty set: with no rows of a class, every
    # assertion about that class is vacuously true.
    check("the corpus set still holds vault-rooted rows to protect", bool(vault_rows))
    check("the corpus set still holds home-rooted rows to keep alive", bool(home_rows))
    if not (vault_rows and home_rows):
        print(f"\ncorpus push root split — {checked - len(failures)}/{checked} passed")
        return 1

    prior = os.environ.pop("CARR_DRIVE_RECOVERY", None)
    try:
        ok_vault, why_vault = corpus.row_root_writable(vault_rows[0])
        check("a vault row is refused on the normal path",
              not ok_vault, f"{vault_rows[0]} was writable")
        check("and the refusal names retirement rather than a missing mount",
              "retired" in why_vault, why_vault)

        ok_home, why_home = corpus.row_root_writable(home_rows[0])
        check("a home row is NOT refused for the vault's retirement",
              ok_home or "retired" not in why_home,
              f"{home_rows[0]}: {why_home}")

        os.environ["CARR_DRIVE_RECOVERY"] = "1"
        _ok, why_recovery = corpus.row_root_writable(vault_rows[0])
        check("an opened recovery envelope lifts the retirement refusal",
              "retired" not in why_recovery, why_recovery)
    finally:
        os.environ.pop("CARR_DRIVE_RECOVERY", None)
        if prior is not None:
            os.environ["CARR_DRIVE_RECOVERY"] = prior

    print(f"\ncorpus push root split — {checked - len(failures)}/{checked} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
