#!/usr/bin/env python3
"""reserve-migration.py — CLAIM a migration number at mint time, atomically.

WHY THIS EXISTS (2026-08-24, ci-failures council cluster B, ~5 red runs).
next-migration.py tells you a number is free; it cannot STOP the session that
asks next from taking the same one while you type your filename. Allocation and
claiming have to be one atomic act (grok, council transcript: "reserve the next
number AT MINT ... not from session memory, not at merge").

WHAT A RESERVATION IS. One JSON line appended to
    <canonical repo>/out/migration-reservations.jsonl
    {"number": 288, "name": "0288_the_thing.sql", "owner": "...@...",
     "branch": "...", "host": "...", "pid": 123, "ts": "..."}
The canonical path matters: worktrees isolate trees, and isolation is exactly
what hides peer claims (see next-migration.py's docstring). The canonical
checkout is the one directory every session on this machine shares, so the
ledger lives there regardless of which tree calls this script.

ATOMICITY. fcntl.flock on a sidecar lock file serialises read-scan +
append between concurrent callers on this machine. Cross-machine races do not
exist here yet — every writer is a session on Joe's Mac — and the ledger says
so plainly rather than pretending otherwise.

NEVER SILENTLY REUSED. An expired reservation (> RESERVATION_TTL_DAYS) is
REPORTED as stale and still counted as claimed. Only a human decision to
tombstone frees a number, matching codex's ruling: "Abandoned reservations may
tombstone, but numbers must not be silently reused."

FAILURE TEXT NAMES THE MOVE. When your requested number is taken the error
says who holds it and what the next free number is — the retry loop that
produced the fix-0248-collision branch died guessing.

Risk colour YELLOW: appends to out/ inside the canonical checkout — its own
bookkeeping file, nothing tracked, nothing sent anywhere.

Usage:
  ./run.sh reserve-migration                       # claim the next free number
  ./run.sh reserve-migration --name my_change      # record intended filename
  ./run.sh reserve-migration --number 288          # claim a SPECIFIC number
  ./run.sh reserve-migration --list                # show the ledger
"""

from __future__ import annotations

import argparse
import datetime
import fcntl
import importlib.util
import json
import os
import socket
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

LEDGER = os.path.join(REPO, "out", "migration-reservations.jsonl")
LOCK = LEDGER + ".lock"
RESERVATION_TTL_DAYS = 14


def _load_next_migration():
    spec = importlib.util.spec_from_file_location(
        "next_migration", os.path.join(REPO, "tools", "next-migration.py"))
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def read_reservations():
    """Every reservation ever recorded, oldest first. Missing file = none."""
    rows = []
    try:
        with open(LEDGER, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue  # a torn last line from a killed process; skip it
                if isinstance(row, dict) and isinstance(row.get("number"), int):
                    rows.append(row)
    except OSError:
        pass
    return rows


def _age_days(row):
    try:
        then = datetime.datetime.fromisoformat(row["ts"])
    except (KeyError, ValueError):
        return None
    return (datetime.datetime.now(datetime.timezone.utc) - then).days


def _scan_claims(nm):
    """Rebuild the full claim set — remote, every worktree, reservations —
    exactly the sources next-migration reads, plus this ledger."""
    claims = {}
    warnings = []

    remote = nm.run(["git", "ls-tree", "--name-only", "origin/main", "migrations/"])
    remote_names = [os.path.basename(n.strip()) for n in remote.splitlines() if n.strip()]
    nm.merge(claims, nm.numbers_from_names(remote_names), "origin/main")

    here = os.path.realpath(REPO)
    for wt in nm.worktree_paths():
        mdir = os.path.join(wt, "migrations")
        if not os.path.isdir(mdir):
            continue
        label = "this tree" if os.path.realpath(wt) == here else f"worktree {os.path.basename(wt)}"
        nm.merge(claims, nm.numbers_from_names(os.listdir(mdir)), label)

    for row in read_reservations():
        num = row["number"]
        claims.setdefault(num, {}).setdefault(
            row.get("name") or f"reserved-by-{row.get('owner', '?')}", set()
        ).add("reservation")
    nm.merge(claims, {
        number: {f"burned:{reason}"}
        for number, reason in nm.PERMANENTLY_BURNED_MIGRATION_SLOTS.items()
    }, "permanent-burn-register")
    return claims, warnings


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", help="intended filename (recorded, not created)")
    ap.add_argument("--number", type=int, help="claim this specific number")
    ap.add_argument("--list", action="store_true", help="print the ledger and exit")
    args = ap.parse_args()

    nm = _load_next_migration()

    if args.list:
        for row in read_reservations():
            age = _age_days(row)
            stale = "" if age is None or age <= RESERVATION_TTL_DAYS else \
                f"  STALE {age}d — tombstone deliberately, never silently reuse"
            print(f"{row['number']:04d}  {row.get('name', '?')}  "
                  f"{row.get('owner', '?')}@{row.get('host', '?')} "
                  f"{row.get('branch', '?')}{stale}")
        return 0

    os.makedirs(os.path.dirname(LEDGER), exist_ok=True)
    with open(LOCK, "w") as lock_fh:
        fcntl.flock(lock_fh, fcntl.LOCK_EX)
        try:
            claims, _ = _scan_claims(nm)

            nxt = (max(claims) + 1) if claims else 1
            wanted = args.number if args.number is not None else nxt

            holders = claims.get(wanted)
            if holders:
                sources = sorted({s for names in holders.values() for s in names})
                if args.number is not None:
                    print(f"ERROR: {wanted:04d} is already claimed ({', '.join(sources)}). "
                          f"The next free number is {nxt:04d}. Re-run without --number.",
                          file=sys.stderr)
                    return 1
                # Someone raced the scan even under our lock ordering (a peer
                # worktree gained the file between their write and our scan).
                # Take the next free number instead of the colliding one.
                wanted = nxt

            name = args.name or ""
            if name and not name.endswith(".sql"):
                name += ".sql"
            if name:
                m = nm.NUM.match(name)
                if m and int(m.group(1)) != wanted:
                    print(f"ERROR: --name {name} does not start with the claimed "
                          f"number {wanted:04d}. Fix the name or claim "
                          f"{m.group(1)} explicitly.", file=sys.stderr)
                    return 1

            row = {
                "number": wanted,
                "name": name,
                "owner": subprocess_git(["config", "user.email"]) or "unknown",
                "branch": subprocess_git(["rev-parse", "--abbrev-ref", "HEAD"]) or "detached",
                "host": socket.gethostname(),
                "pid": os.getpid(),
                "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            }
            with open(LEDGER, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(row, sort_keys=True) + "\n")
        finally:
            fcntl.flock(lock_fh, fcntl.LOCK_UN)

    print(f"reserved migration number {wanted:04d}"
          + (f" for {name}" if name else "")
          + f"\n  ledger: {os.path.relpath(LEDGER, REPO)}"
          + "\n  Create migrations/%04d_<slug>.sql next — the number is now yours "
            "and every other session's next-migration sees it claimed." % wanted)
    return 0


def subprocess_git(args):
    import subprocess
    r = subprocess.run(["git", "-C", REPO] + args, capture_output=True,
                       text=True, timeout=30)
    return r.stdout.strip() if r.returncode == 0 else ""


if __name__ == "__main__":
    sys.exit(main())
