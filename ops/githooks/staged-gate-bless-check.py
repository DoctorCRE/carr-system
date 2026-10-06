#!/usr/bin/env python3
"""staged-gate-bless-check.py — a gate change carries its bless, or it does not
commit (rule c0b38d80). Called by ops/githooks/pre-commit; not a hook itself.

TWICE THIS WAS VIOLATED FOR REAL. 2026-08-12: a gate change landed without its
bless and main's baseline was later overwritten by a PR cut before it. 2026-08-14:
a live hotfix to settings-change-gate.py sat unblessed in the shared checkout and
every session that morning started under a GATE INTEGRITY FAILURE banner. Both
times the gate moved without the baseline and every later session had to treat
the enforcement layer as untrusted. The commit is the one moment the two can be
forced to move together, so that is where this stands.

WHAT IT CHECKS, against the INDEX, not the working tree — a partially staged
bless is exactly the ambiguity it exists to refuse:

  1. Any staged hooks/*.py (except *-selftest.py, which gate-integrity
     deliberately leaves unprotected) requires ops/config/gate-baseline.json
     staged in the same commit.
  2. The staged baseline's recorded hash must match the staged file bytes.
     Presence alone would bless the wrong bytes — the 2026-08-13 false
     re-bless — so the hash is verified, not assumed.
  3. The wiring contracts get the same treatment: a contract changes what the
     gates enforce without touching a hook, which is why gate-integrity hashes
     them too.

The contract list is duplicated from hooks/gate-integrity.py CONTRACTS on
purpose: this script must run inside selftest fixture repos that carry no
hooks/ directory, and a drifted entry here fails toward a false refusal, which
the escape hatch answers, never toward silence.

AUTO-APPLY, added 2026-08-23 by the gates-audit council. The INVARIANT above is
not the friction; the CEREMONY was. A session that changed a gate had to fail
this check once, read the remedy, run the bless, stage the result and commit
again — reliably enough that the miss has its own selftest. The baseline update
is fully determined by the staged bytes, so a human retyping it adds no
judgement, only a round trip.

So when the only problem is a baseline that does not yet record the staged
bytes, this now COMPUTES that baseline and stages it into the same commit,
rather than refusing and explaining. What it must not become is a rubber stamp,
so it is bounded in four ways:

  * It rewrites ONLY entries for gate files staged in THIS commit. Every other
    entry is carried forward byte-for-byte, so it cannot adopt another session's
    in-flight edit — the defect 320f5531 shape that scoped bless exists to stop.
  * It derives hashes from the STAGED blob, never the working tree. Blessing the
    working tree when the two differ is the 2026-08-13 false re-bless.
  * It PRINTS every entry it changes, old hash and new. A bless nobody can see
    is indistinguishable from drift.
  * It re-runs the verification afterwards and still refuses if anything fails,
    so the auto-apply cannot pass a commit the manual path would have stopped.

The co-change requirement is therefore unchanged: gate and baseline still move
in one commit, and hosted CI still verifies the committed bytes against the
committed baseline independently. What changed is who types it.

Opt out with CARR_NO_AUTO_BLESS=1 to get the old refuse-and-explain behaviour.

Exit 0 to allow, 1 to refuse (pre-commit relays the message), and 0 with a
warning if git itself cannot be read — an accident-stopper that crashes must
never become the thing that blocks every commit.
"""

# PEP 604 annotations (`bytes | None`) below are evaluated at def time before
# Python 3.10 and raise TypeError there, which crashes this hook rather than
# failing the check it performs — so a commit is refused for a reason that has
# nothing to do with gates. Hit on Dell's Mac 2026-08-18, whose python3 is the
# macOS system 3.9.6. This makes the annotations lazy so the hook runs on both.
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys

CONTRACTS = ("delegation-gate-hook.json", "hooks.json", "codex-hooks.json",
             "rule-enforcement-map.json", "model-floors.json",
             "session-context-lifecycle.v2.json")
BASELINE = "ops/config/gate-baseline.json"


def staged_files() -> list[str]:
    out = subprocess.run(["git", "diff", "--cached", "--name-only"],
                         capture_output=True, text=True, check=True)
    return [line for line in out.stdout.splitlines() if line.strip()]


def staged_blob(path: str) -> bytes | None:
    """The INDEX copy of a path, or None when the stage deletes it."""
    p = subprocess.run(["git", "show", f":{path}"], capture_output=True)
    return p.stdout if p.returncode == 0 else None


def gate_paths(staged: list[str]) -> list[str]:
    hooks = [p for p in staged
             if p.startswith("hooks/") and p.endswith(".py")
             and p.count("/") == 1 and not p.endswith("-selftest.py")]
    contracts = [p for p in staged
                 if p.startswith("ops/config/")
                 and p.split("/")[-1] in CONTRACTS]
    return hooks + contracts


def baseline_entry(baseline: dict, path: str) -> str | None:
    name = path.split("/")[-1]
    table = "contracts" if name in CONTRACTS else "hashes"
    return (baseline.get(table) or {}).get(name)


def verify(staged: list[str], touched: list[str]) -> list[str]:
    """Every way the staged baseline can fail to record the staged gates."""
    if BASELINE not in staged:
        return [f"{p} is staged, but {BASELINE} is not" for p in touched]

    raw = staged_blob(BASELINE)
    try:
        baseline = json.loads(raw or b"")
    except ValueError:
        baseline = None
    if not isinstance(baseline, dict):
        return [f"{BASELINE} is staged but is not readable JSON"]

    problems = []
    for path in touched:
        blob = staged_blob(path)
        want = baseline_entry(baseline, path)
        if blob is None:
            # A staged deletion: the baseline moved in this commit and that is
            # what co-change requires; gate-integrity owns whether the removal
            # itself is coherent.
            continue
        got = hashlib.sha256(blob).hexdigest()
        if want != got:
            problems.append(
                f"{path}: staged bytes hash {got[:12]}…, baseline "
                f"records {(want or 'NO ENTRY')[:12]}…")
    return problems


def head_baseline() -> dict:
    """The committed baseline, the base every carried-forward entry comes from."""
    p = subprocess.run(["git", "show", f"HEAD:{BASELINE}"], capture_output=True)
    if p.returncode != 0:
        return {}
    try:
        data = json.loads(p.stdout)
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def auto_apply(staged: list[str], touched: list[str]) -> list[str]:
    """Write and stage the baseline the staged gates imply. Returns changes."""
    base = None
    if BASELINE in staged:
        raw = staged_blob(BASELINE)
        try:
            base = json.loads(raw or b"")
        except ValueError:
            base = None
    if not isinstance(base, dict):
        base = head_baseline()
    if not isinstance(base, dict) or not base.get("hashes"):
        # No trustworthy base to carry forward. A scoped update on top of
        # nothing would silently DROP every gate it does not name, which is a
        # far worse outcome than the refusal this replaces.
        return []

    # A DELETION IS NEVER AUTO-BLESSED. Removing a gate is a retirement, not a
    # content update: the baseline entry is the only record that the gate was
    # ever there, and a mechanism that quietly drops it would turn "delete the
    # file" into "delete the protection, silently, with a green commit". Every
    # other case here is fully determined by bytes the author staged; this one
    # is a decision. It falls through to the manual remedy, which is correct.
    if any(staged_blob(path) is None for path in touched):
        return []

    hashes = dict(base.get("hashes") or {})
    contracts = dict(base.get("contracts") or {})
    changes = []
    for path in touched:
        blob = staged_blob(path)
        if blob is None:
            # Unreachable: the deletion guard above returns before this loop.
            # Stated rather than assumed, because "unreachable" is how a None
            # reaches sha256 after somebody edits the guard.
            return []
        name = path.split("/")[-1]
        table = contracts if name in CONTRACTS else hashes
        got = hashlib.sha256(blob).hexdigest()
        was = table.get(name)
        if was == got:
            continue
        table[name] = got
        changes.append(f"{name}: {(was or 'NO ENTRY')[:12]}… -> {got[:12]}…")

    if not changes:
        return []

    who = subprocess.run(["git", "config", "user.email"],
                         capture_output=True, text=True).stdout.strip()
    rev = subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                         capture_output=True, text=True).stdout.strip()
    data = dict(base)
    data["blessed_by"] = who or "unknown"
    data["blessed_at_rev"] = rev or "unknown"
    data["hashes"] = hashes
    data["contracts"] = contracts

    top = subprocess.run(["git", "rev-parse", "--show-toplevel"],
                         capture_output=True, text=True).stdout.strip()
    if not top:
        return []
    target = os.path.join(top, BASELINE)
    with open(target, "w") as fh:
        json.dump(data, fh, indent=2)
        fh.write("\n")
    add = subprocess.run(["git", "add", "--", BASELINE], capture_output=True)
    if add.returncode != 0:
        return []
    return changes


def main() -> int:
    try:
        staged = staged_files()
    except Exception as exc:                                  # noqa: BLE001
        print(f"staged-gate-bless-check: could not read the index ({exc}); "
              "letting the commit through UNCHECKED.", file=sys.stderr)
        return 0

    touched = gate_paths(staged)
    if not touched:
        return 0

    problems = verify(staged, touched)

    if problems and os.environ.get("CARR_NO_AUTO_BLESS") != "1":
        try:
            changes = auto_apply(staged, touched)
        except Exception as exc:                              # noqa: BLE001
            print(f"staged-gate-bless-check: auto-bless failed ({exc}); "
                  "falling back to the manual remedy below.", file=sys.stderr)
            changes = []
        if changes:
            # RE-VERIFY against the index as it now stands. The auto-apply must
            # never pass a commit the manual path would have refused, so the
            # same function decides both.
            staged = staged_files()
            problems = verify(staged, gate_paths(staged))
            if not problems:
                sys.stderr.write(
                    "\n  gate baseline re-blessed automatically and staged into "
                    "this commit\n  (rule c0b38d80 — the gate and its bless move "
                    "together):\n\n")
                for line in changes:
                    sys.stderr.write(f"    {line}\n")
                sys.stderr.write(
                    "\n  Only gates staged in THIS commit were rewritten; every "
                    "other entry\n  was carried forward unchanged. Hashes come "
                    "from the STAGED bytes.\n"
                    "  Review it like any staged change: git diff --cached "
                    f"{BASELINE}\n"
                    "  To do this by hand instead: CARR_NO_AUTO_BLESS=1\n\n")
                return 0

    if not problems:
        return 0

    names = " ".join(sorted({p.split("/")[-1] for p in touched
                             if not p.startswith("ops/config/")}
                            | {p.split("/")[-1] for p in touched
                               if p.startswith("ops/config/")}))
    sys.stderr.write(
        "\n"
        "  ────────────────────────────────────────────────────────────────\n"
        "  COMMIT REFUSED — a gate change must carry its bless (c0b38d80)\n"
        "  ────────────────────────────────────────────────────────────────\n\n"
        "  Your changes are untouched and still staged. Nothing was lost.\n\n")
    for p in problems:
        sys.stderr.write(f"    {p}\n")
    sys.stderr.write(
        "\n"
        "  A gate that moves without its baseline makes every later session\n"
        "  start under a GATE INTEGRITY FAILURE it can do nothing about —\n"
        "  that was 2026-08-12, and again the morning of 2026-08-14.\n\n"
        "  Bless what you changed and stage the result with it:\n\n"
        f"      python3 hooks/gate-integrity.py --bless {names}\n"
        f"      git add {BASELINE}\n\n"
        "  If this commit genuinely must not carry the bless, say so for one\n"
        "  command:\n\n"
        "      CARR_ALLOW_UNBLESSED_GATE=1 git commit ...\n\n")
    return 1


if __name__ == "__main__":
    sys.exit(main())
