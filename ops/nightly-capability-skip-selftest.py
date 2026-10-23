#!/usr/bin/env python3
"""nightly-capability-skip-selftest.py — an unprovisioned capability must SKIP.

WHAT THIS DEFENDS. bin/nightly.sh runs under `set -u`. Its credential loader
exports only the names it FINDS in ~/.config/carr/db.env, so a capability that
was never provisioned on a given Mac leaves its variable UNSET rather than
empty. Dereferencing it then does not fail the step — it kills the entire
shell, mid-chain, with no FAIL line, no `chain FINISHED` line, and nothing in
out/nightly.log to find it by. Every step after that point silently never runs.

That happened on 2026-08-17: the hardening that replaced backup-dump.sh's
neonctl owner-credential path with a dedicated carr_backup DSN landed in the
code while this Mac had no such DSN. The chain died between `vendor level
drift` and `encrypted backup`, taking the encrypted backup, the portability
mirror, the calendar archive and the config mirror with it. The scheduled 02:05
run escaped only because its checkout was 38 commits behind; the failure was
invisible until a run happened to be watched.

WHY IT SHELLS OUT. The artifact that matters is the SCRIPT AS INVOKED under
`set -u` with the variable genuinely unset. Reading the source cannot prove the
shell's behaviour, and an `export VAR=` in a test harness makes the variable
EMPTY, which is a different state from UNSET and does not reproduce the bug.

Each case runs a tiny zsh script that reproduces one shape in isolation, so no
case touches the real chain, the real vault, or any credential.

Exit 0 if every case passes, 1 otherwise.

    ops/nightly-capability-skip-selftest.py
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
NIGHTLY = REPO / "bin" / "nightly.sh"

failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'ok  ' if ok else 'FAIL'}  {name}" + (f" — {detail}" if detail and not ok else ""))
    if not ok:
        failures.append(name)


def run_zsh(body: str) -> subprocess.CompletedProcess:
    """Run a fragment under the same shell and flags the chain uses."""
    return subprocess.run(
        ["/bin/zsh", "-c", "set -u\n" + body],
        capture_output=True, text=True,
        env={"PATH": os.environ.get("PATH", "/usr/bin:/bin")},
    )


print("nightly-capability-skip: an unprovisioned capability skips, never kills the chain")

# ---- 1. the hazard is real: bare dereference of an UNSET var under set -u ----
bare = run_zsh('reached=no\nenv X="$CARR_DB_BACKUP_URL" true\nreached=yes\necho "reached=$reached"')
check(
    "bare dereference of an unset variable aborts the shell",
    bare.returncode != 0 and "reached=yes" not in bare.stdout,
    f"rc={bare.returncode} stdout={bare.stdout!r}",
)

# ---- 2. the fix: defaulting first lets execution continue -------------------
defaulted = run_zsh(
    'CARR_DB_BACKUP_URL="${CARR_DB_BACKUP_URL:-}"\n'
    'env X="$CARR_DB_BACKUP_URL" true\n'
    'echo "reached=yes"'
)
check(
    "after defaulting, the same dereference continues",
    defaulted.returncode == 0 and "reached=yes" in defaulted.stdout,
    f"rc={defaulted.returncode} stdout={defaulted.stdout!r}",
)

# ---- 3. UNSET and EMPTY are different states, and only UNSET is the bug -----
empty = run_zsh('CARR_DB_BACKUP_URL=\nenv X="$CARR_DB_BACKUP_URL" true\necho "reached=yes"')
check(
    "an EMPTY variable was never the hazard (so a test must use UNSET)",
    empty.returncode == 0 and "reached=yes" in empty.stdout,
    f"rc={empty.returncode}",
)

# ---- 4. the chain defaults the variable before any use ----------------------
text = NIGHTLY.read_text()

# Comment lines are excluded deliberately: this file's own explanation quotes
# the dereference, and a prose mention cannot abort a shell. Each comment line
# is replaced by a blank line of ITS OWN LENGTH, so every surviving offset is
# still an offset into the real file — the first draft of this test dropped
# comment lines entirely and then compared an offset taken from the raw text
# against offsets taken from the stripped text, which is not a comparison at
# all. It passed anyway, which is the only reason it was worth catching.
_uncommented = "\n".join(
    " " * len(line) if line.lstrip().startswith("#") else line
    for line in text.split("\n")
)
assert len(_uncommented) == len(text), "offset-preserving strip must not change length"

default_at = _uncommented.find('CARR_DB_BACKUP_URL="${CARR_DB_BACKUP_URL:-}"')
check("bin/nightly.sh defaults CARR_DB_BACKUP_URL", default_at != -1)

uses = [m.start() for m in re.finditer(r'"\$CARR_DB_BACKUP_URL"', _uncommented)]
check("bin/nightly.sh actually uses the variable", bool(uses))
check(
    "every use comes AFTER the default",
    default_at != -1 and all(u > default_at for u in uses),
    f"default at {default_at}, earliest use at {min(uses) if uses else None}",
)

# ---- 5. set -u is still on, or none of the above matters -------------------
check("the chain still runs under set -u", re.search(r"^set -u$", text, re.M) is not None)

# ---- 6. the mirror SKIPS on an absent credential ---------------------------
# This used to assert the shape of a shell guard in nightly.sh: that the step
# sat inside `if [ -n "$CARR_DB_BACKUP_URL" ]` and refused through the helper.
# That guard existed because doctrine_mirror.py exited 2 on an empty
# DATABASE_URL, and 2 reads as FAIL. #387 moved the fix into the callee, where
# it belongs — one exit code protects every caller instead of one branch
# protecting one caller — and the guard came out again.
#
# So this now checks the PROPERTY the guard was standing in for, by running the
# mirror with no credential and reading what it returns. A test that pins the
# shape of a fix cannot survive the fix moving; a test that pins the behaviour
# does not care where it lives. That is the same mistake #380's gate made in
# the other direction, and this file should not repeat it.
mirror = subprocess.run(
    [sys.executable, str(REPO / "pipelines" / "doctrine_mirror.py"),
     "--out", "/tmp/nightly-capability-skip-probe",
     "--also", "/tmp/nightly-capability-skip-probe2"],
    capture_output=True, text=True,
    env={k: v for k, v in os.environ.items() if k != "DATABASE_URL"},
)
check("the portability mirror skips rather than fails without a credential",
      mirror.returncode == 78, f"rc={mirror.returncode}")
check("and it says which setting is missing",
      "DATABASE_URL" in (mirror.stderr + mirror.stdout))

print()
if failures:
    print(f"nightly-capability-skip: FAIL — {len(failures)} case(s): {', '.join(failures)}")
    sys.exit(1)
print("nightly-capability-skip: OK — an unprovisioned backup capability skips and the chain goes on")
