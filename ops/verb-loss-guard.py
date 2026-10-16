#!/usr/bin/env python3
"""Validate registry names from stdin against the committed retirement ledger.

Called by verb-count.sh --check-retirements for both CI and deploy. The deployed
ledger records counts, not names, so a shrink must equal all listed retirements.
Every listed name must be absent, even after deployment establishes a new count.
A count-only baseline cannot detect a loss masked by a simultaneous addition.
No shrink override is accepted here. Typed staging recovery remains a separate
database-bound authorization in deploy-worker.sh.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys

LEDGER = Path(__file__).resolve().parent / "config/retired-verbs.v1.json"


def check_retirements(names: list[str], deployed: int, ledger: dict) -> None:
    if deployed < 0:
        raise ValueError("deployed count must be nonnegative")
    if not isinstance(names, list) or any(not isinstance(name, str) for name in names):
        raise ValueError("registry must provide a list of verb names")
    if len(names) != len(set(names)):
        raise ValueError("registry contains duplicate verb names")
    if ledger.get("schema") != "carr-retired-verbs/v1":
        raise ValueError("unsupported retirement ledger schema")
    retired = set()
    for entry in ledger["retirements"]:
        if not isinstance(entry.get("reference"), str) or not entry["reference"].strip():
            raise ValueError("each retirement needs a PR or decision reference")
        if not isinstance(entry.get("verbs"), list) or not entry["verbs"]:
            raise ValueError("each retirement needs verb names")
        for name in entry["verbs"]:
            if not isinstance(name, str) or not name.strip() or name in retired:
                raise ValueError(f"invalid or duplicate retired verb: {name!r}")
            retired.add(name)
    present = retired.intersection(names)
    if present:
        raise ValueError("retired verbs still present in shipping registry: "
                         + ", ".join(sorted(present)))
    lost = deployed - len(names)
    if lost > 0 and lost != len(retired):
        raise ValueError(f"unlisted verb loss or incomplete retirement: deployed {deployed}, "
                         f"shipping {len(names)}, loss {lost}, listed retirements {len(retired)}")


def main() -> int:
    try:
        if len(sys.argv) != 2:
            raise ValueError("usage: verb-loss-guard.py <deployed-count> (registry names on stdin)")
        names = json.load(sys.stdin)
        check_retirements(names, int(sys.argv[1]), json.loads(LEDGER.read_text()))
    except (ValueError, KeyError, TypeError, AttributeError, OSError) as exc:
        print(f"verb-loss-guard: {exc}", file=sys.stderr)
        return 1
    print(len(names))
    return 0


if __name__ == "__main__":
    sys.exit(main())
