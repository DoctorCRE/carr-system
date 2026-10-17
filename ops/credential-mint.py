#!/usr/bin/env python3
"""ops/credential-mint.py — the nightly lane that mints and rotates CARR's own credentials.

Joe's ruling 2026-10-12 (decision cbe91287-10a2-48d0-bda7-3b1784b4bb93): minting
a credential must not require him. He places one root grant per provider once
(ops/config/credential-root-grants.v1.json names each, with its scope and the
one command); this lane does the rest every night through lib/credential_mint.py:
anything within rotate_within_days of expiry whose root grant is present is
minted, verified with a read-only call made by the new child, stored, installed
where a tool keeps its own copy, and its predecessor revoked. A missing root
grant is never a live call; it files or updates one deduplicated loop per
provider naming the one-time grant.

Exit 0 when the lane ran, whatever it found: every finding already has its
channel (a receipt in out/credential-mint-receipts.jsonl and, for a grant, a
loop). Exit 1 only when the lane could not run at all, because the root-grant
file and the credential inventory disagree or cannot be read. The same contract
as ops/credential-health.py --nightly, which runs right after it.

    .venv/bin/python ops/credential-mint.py

Nothing here prints a credential value; the log carries names and outcomes.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib import credential_mint  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    if argv:
        print("usage: ops/credential-mint.py   (no arguments)", file=sys.stderr)
        return 64
    try:
        plan = credential_mint.load_plan()
    except credential_mint.PlanError as exc:
        print(f"⚠︎ credential-mint — plan refused, nothing minted: {exc}")
        return 1
    receipts = credential_mint.Minter(plan, credential_mint.this_machine()).run_once()
    for line in credential_mint.report_lines(receipts):
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
