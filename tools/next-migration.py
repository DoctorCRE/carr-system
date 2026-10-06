#!/usr/bin/env python3
"""next-migration.py — allocate the next migration number without colliding.

WHY THIS EXISTS (2026-08-13). Migration numbers are a single global ordered
namespace, and the method for picking the next one was `ls migrations/ | tail`.
That reads ONE directory: the tree you happen to be standing in. It cannot see a
number a concurrent session has already taken in a file it has not committed yet,
and this machine runs many sessions against one repository with NINETEEN linked
worktrees. Nine migrations landed on 2026-08-13 alone, from several sessions.

The collision is not hypothetical and it is not rare: on 2026-08-12 a session
wrote 0110, a peer took 0110 while it was writing, and it renumbered to 0111. The
same shape recurred the next day. Each one costs a rename plus every reference
that moved with it.

WORKTREES MAKE THIS WORSE, WHICH IS THE POINT WORTH UNDERSTANDING. Isolating each
session in its own tree is the right answer to sweeping another writer's FILES,
and it is already standard here. But it is the wrong shape for a SHARED namespace:
the more isolated the trees, the less any one session can see of what the others
have already claimed. Isolation and allocation pull in opposite directions, so the
allocation has to be done deliberately rather than by looking around.

WHAT THIS READS, and why each source is needed:
  1. origin/main            — every number already merged, including ones this
                              checkout has not fetched into its working tree.
  2. this tree's migrations/ — committed and uncommitted alike.
  3. EVERY OTHER WORKTREE's migrations/ — the source `ls` cannot see and the one
                              that actually causes the collisions. A peer's
                              uncommitted 0112 is a real claim on 0112.

It prints the next free number and, separately, names any number that is claimed
only by an uncommitted file somewhere — because that is a number that will look
free to anyone reading git alone, and is not.

Risk colour GREEN: reads only. Writes nothing, creates nothing, sends nothing.

Usage:
  ./run.sh next-migration            # the next free number, and what is in flight
  ./run.sh next-migration --quiet    # just the number, for scripting
"""

import json
import os
import re
import subprocess
import sys

from migration_number_contract import (
    APPROVED_INTERSTITIAL_COLLISIONS,
    FROZEN_COLLISIONS,
    MigrationNumberError,
    PERMANENTLY_BURNED_MIGRATION_SLOTS,
    collision_report,
    validate_migration_names,
)

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NUM = re.compile(r"^(\d{4})[_a-z0-9]*.*\.sql$", re.IGNORECASE)


def run(args, cwd=REPO):
    try:
        r = subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=30)
        return r.stdout if r.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def numbers_from_names(names):
    """Map every 4-digit-prefixed .sql name to its int. Ignores anything else —
    0013a_historical_client_status_vocabulary.sql is a real filename here, so the
    pattern deliberately tolerates a suffix after the digits."""
    out = {}
    for n in names:
        base = os.path.basename(n.strip())
        m = NUM.match(base)
        if m:
            out.setdefault(int(m.group(1)), set()).add(base)
    return out


def merge(into, more, source):
    for num, names in more.items():
        for name in names:
            into.setdefault(num, {}).setdefault(name, set()).add(source)


def worktree_paths():
    """Every checkout attached to this repository, the main one included."""
    paths = []
    for line in run(["git", "worktree", "list", "--porcelain"]).splitlines():
        if line.startswith("worktree "):
            paths.append(line.split(" ", 1)[1].strip())
    return paths or [REPO]


TRANSIENT_ORIGIN_COLLISION = {
    "0298": (
        "0298_memory_kernel.sql",
        "0298_partner_room_origin.sql",
    ),
}
TRANSIENT_MEMORY_OLD = "0298_memory_kernel.sql"
TRANSIENT_MEMORY_NEW = "0299_memory_kernel.sql"
TRANSIENT_PARTNER = "0298_partner_room_origin.sql"


def _head_contains_origin_main() -> bool:
    try:
        result = subprocess.run(
            ["git", "merge-base", "--is-ancestor", "origin/main", "HEAD"],
            cwd=REPO,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def _repair_contents_match() -> bool:
    """Require the 0299 memory file to be 0298 with only ordinal labels changed."""
    try:
        old_blob = subprocess.run(
            ["git", "show", f"origin/main:migrations/{TRANSIENT_MEMORY_OLD}"],
            cwd=REPO,
            capture_output=True,
            timeout=30,
            check=True,
        ).stdout
        with open(os.path.join(REPO, "migrations", TRANSIENT_MEMORY_NEW), "rb") as fh:
            new_blob = fh.read()
    except (OSError, subprocess.SubprocessError):
        return False
    if old_blob.count(b"0298") != 3 or new_blob.count(b"0299") != 3:
        return False
    expected = old_blob.replace(b"0298", b"0299")
    return expected == new_blob


def _repairs_exact_origin_collision(
    remote_names: list[str],
    current_names: list[str],
    contents_match: bool,
    head_contains_origin: bool,
) -> bool:
    """Admit only this self-expiring 0298 memory migration repair state."""
    remote = set(remote_names)
    current = set(current_names)
    registered_slots = FROZEN_COLLISIONS | APPROVED_INTERSTITIAL_COLLISIONS
    unregistered = {
        slot: names for slot, names in collision_report(remote_names).items()
        if slot not in registered_slots
    }
    frozen = {
        slot: names for slot, names in collision_report(remote_names).items()
        if slot in FROZEN_COLLISIONS
    }
    if frozen != FROZEN_COLLISIONS:
        return False
    approved = {
        slot: names for slot, names in collision_report(remote_names).items()
        if slot in APPROVED_INTERSTITIAL_COLLISIONS
    }
    if approved and approved != APPROVED_INTERSTITIAL_COLLISIONS:
        return False
    if unregistered != TRANSIENT_ORIGIN_COLLISION:
        return False
    if not head_contains_origin or not contents_match:
        return False
    if TRANSIENT_PARTNER not in remote or TRANSIENT_PARTNER not in current:
        return False
    if TRANSIENT_MEMORY_OLD not in remote or TRANSIENT_MEMORY_OLD in current:
        return False
    if TRANSIENT_MEMORY_NEW not in current:
        return False
    if [name for name in current if name.endswith("_memory_kernel.sql")] != [TRANSIENT_MEMORY_NEW]:
        return False
    # No unrelated migration from origin/main may disappear in the repair.
    return remote - {TRANSIENT_MEMORY_OLD} <= current


def main():
    quiet = "--quiet" in sys.argv[1:]
    claims = {}
    try:
        current_names = os.listdir(os.path.join(REPO, "migrations"))
    except OSError:
        current_names = []

    # 1. everything merged on the remote
    remote = run(["git", "ls-tree", "--name-only", "origin/main", "migrations/"])
    remote_names = [os.path.basename(name.strip()) for name in remote.splitlines() if name.strip()]
    merge(claims, numbers_from_names(remote_names), "origin/main")
    if not remote:
        print("WARNING: could not read origin/main — the number below is based only on "
              "local trees and may collide with something already merged.", file=sys.stderr)
    else:
        try:
            validate_migration_names(
                remote_names,
                require_frozen=True,
                # The merged tree may still be the approved pair's predecessor:
                # it has 0494 but not its reviewed 0494a companion. A checked
                # worktree never gets this allowance.
                allow_approved_interstitial_base=True,
            )
        except MigrationNumberError as exc:
            try:
                validate_migration_names(current_names, require_frozen=True)
                current_tree_valid = True
            except MigrationNumberError:
                current_tree_valid = False
            exact_repair = (
                current_tree_valid
                and _head_contains_origin_main()
                and _repairs_exact_origin_collision(
                    remote_names, current_names, _repair_contents_match(), True
                )
            )
            if not exact_repair:
                print(f"ERROR: origin/main violates the migration-number contract: {exc}",
                      file=sys.stderr)
                return 1
            print(
                "WARNING: origin/main has the exact 0298 memory/partner collision; "
                "this descendant preserves partner-room and carries memory as 0299.",
                file=sys.stderr,
            )

    # 2 & 3. every worktree's migrations/ directory, on disk, committed or not
    here = os.path.realpath(REPO)
    for wt in worktree_paths():
        mdir = os.path.join(wt, "migrations")
        if not os.path.isdir(mdir):
            continue
        label = "this tree" if os.path.realpath(wt) == here else f"worktree {os.path.basename(wt)}"
        try:
            tree_names = os.listdir(mdir)
        except OSError:
            continue
        try:
            validate_migration_names(tree_names, allow_frozen_subset=True)
        except MigrationNumberError as exc:
            # WHOSE TREE IS IT. A violation in origin/main or in the tree the
            # caller is standing in is theirs to fix and genuinely blocks: the
            # number they are about to be handed may be wrong. A violation in
            # ANOTHER session's worktree is neither.
            #
            # This distinction cost most of a night on 2026-08-22. Two branches
            # merged 0248 twice; the collision was resolved on main by renumber,
            # and every OTHER worktree on the machine still held the old
            # filename on disk — as stale checkouts do, harmlessly. Because this
            # returned 1 for any tree, the allocator refused for everyone,
            # migration-number-contract-selftest.py failed, and the pre-push
            # gate refused pushes from branches touching no migration at all.
            # Three unrelated pushes were blocked by a file nobody was editing,
            # and the only way through was skipping CI entirely — which is worse
            # than the thing being guarded against.
            #
            # The council ruled this class on 2026-08-22: a machine-global
            # condition may open a loop, never veto unrelated work. So a foreign
            # tree now WARNS and its numbers are still merged into the claim set
            # below, which is the part that actually protects the caller — those
            # numbers stay reserved either way.
            if os.path.realpath(wt) == here:
                print(f"ERROR: this tree violates the migration-number contract: {exc}",
                      file=sys.stderr)
                return 1
            print(f"WARNING: {label} violates the migration-number contract: {exc}\n"
                  f"  It is another session's checkout, not yours, and it does not make the "
                  f"number below unsafe — its numbers are still counted as claimed. Most often "
                  f"this is a stale worktree that has not pulled since a renumber on main.",
                  file=sys.stderr)
        merge(claims, numbers_from_names(tree_names), label)

    # 4. THE RESERVATION LEDGER (2026-08-24, council cluster B). A number
    # another session has RESERVED at mint time is claimed whether or not any
    # file exists yet — the whole point of reserving. The ledger lives in the
    # canonical checkout's out/, which every worktree on this machine shares.
    # A torn or missing ledger line is skipped by read_reservations(), and a
    # ledger this tool cannot read degrades to today's behaviour (warn, don't
    # refuse) rather than blocking allocation.
    ledger = os.path.join(REPO, "out", "migration-reservations.jsonl")
    try:
        with open(ledger, encoding="utf-8") as fh:
            for line in fh:
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if isinstance(row, dict) and isinstance(row.get("number"), int):
                    merge(claims, {row["number"]: {"reserved"}}, "reservation")
    except OSError:
        pass  # no ledger yet — nothing reserved on this machine

    merge(claims, {
        number: {f"burned:{reason}"}
        for number, reason in PERMANENTLY_BURNED_MIGRATION_SLOTS.items()
    }, "permanent-burn-register")

    if not claims:
        print("0001", flush=True)
        return 0

    nxt = max(claims) + 1
    if quiet:
        print(f"{nxt:04d}")
        return 0

    print(f"next free migration number: {nxt:04d}")
    print(f"  highest claimed: {max(claims):04d}")

    frozen = collision_report(remote_names)
    if frozen:
        print("\n  registered numeric collisions on origin/main — full filenames are distinct")
        print("  ledger identities; never rename, delete, edit, or add to these sets:")
        for slot, names in frozen.items():
            label = (
                "approved interstitial"
                if slot in APPROVED_INTERSTITIAL_COLLISIONS
                else "historical frozen"
            )
            print(f"    {slot} ({label}): {', '.join(names)}")

    # A number absent from origin/main is the dangerous case. It may be genuinely
    # uncommitted, or committed on a branch that has not merged — the distinction
    # does not matter to the collision and IS NOT ASSERTED HERE, because this
    # function cannot tell them apart from a directory listing and a tool that
    # guesses which it is would be stating something it did not check. What both
    # cases share is the only thing that matters: origin/main does not show the
    # number, so it looks free to anyone reading git, and it is not.
    in_flight = []
    for num in sorted(claims):
        for name, sources in claims[num].items():
            if "origin/main" not in sources:
                in_flight.append((num, name, sorted(sources)))
    if in_flight:
        print("\n  CLAIMED BUT NOT ON origin/main — uncommitted, or on an unmerged")
        print("  branch. Either way it looks free to anyone reading git, and is not:")
        for num, name, sources in in_flight:
            print(f"    {num:04d}  {name}  — in {', '.join(sources)}")
        print("\n  Do not reuse those numbers. Re-run this immediately before you write "
              "the file; another session may claim one while you are typing.")
    else:
        print("  every claimed number is on origin/main — nothing in flight elsewhere")
    return 0


if __name__ == "__main__":
    sys.exit(main())
