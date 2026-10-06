#!/usr/bin/env python3
"""
gate-integrity-strict-selftest.py — the acceptance test for gate-integrity's
--strict mode, written before the mode itself (rule e65efc68).

THE HOLE IT CLOSES, found 2026-08-14 by watching it happen. ops/ci.sh's gates
class runs hooks/gate-integrity.py and its own comment says "a gate edited
without a re-bless in the same commit (rule c0b38d80) fails here". It cannot.
gate-integrity.py returns 0 on every path INCLUDING the failure path, on
purpose: it is a SessionStart announcer, and a boot check that can wedge a
session is worse than the drift it reports. So the CI call could never fail,
and the class has been reporting "baseline integrity" it never verified.

WHAT THAT COST, the same day: pull request #102 was cut before #99 merged, so
its whole-file copy of the baseline knew nothing of the two gates #99 added.
Merging it overwrote main's baseline and dropped both gates plus one hash,
with CI green the whole way. The local pre-commit check added that morning
cannot see this — the overwrite happens in GitHub's merge, where no local hook
runs — so CI is the only layer that can catch it.

THE SPLIT THIS MODE DRAWS, and it is the whole design. Two kinds of problem
land in the same report today:

  CONTENT   a gate file's hash does not match the baseline, a blessed gate has
            vanished, a gate exists that nothing blessed. Pure facts about the
            repository, true on every machine and on a bare CI runner.
  ENVIRONMENT   live settings.json wiring, the Codex adapter, the vault-backed
            rule-enforcement map. All depend on a configured machine. A CI
            runner has no vault and no ~/.claude, and a worktree renders its
            own root into the wiring paths, so these are noisy exactly where
            --strict must be trustworthy.

--strict fails on CONTENT only. Anything that fails on a correct machine for
environmental reasons would get the flag removed within a week, and then it
protects nothing — the same argument the settings-change gate makes about
firing on reads.

WHAT MUST STAY TRUE, which most of this file is here to pin:
  1. Bare invocation still exits 0 with drift present. The SessionStart hook
     must never block a session, and that is not negotiable.
  2. --strict exits 1 when a gate's hash does not match the baseline.
  3. --strict exits 1 when a blessed gate file is missing.
  4. --strict exits 1 when a gate file has no baseline entry at all.
  5. --strict exits 0 on a clean tree.
  6. --strict still PRINTS the same report either way; it changes the exit
     code, never the words, so one reader serves both callers.

RUNNING IT. No database, no network, no production access:

    .venv/bin/python ops/gate-integrity-strict-selftest.py
"""

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
GATE = REPO / "hooks" / "gate-integrity.py"

passed = 0
failures: list[str] = []


def check(name, cond, detail=""):
    global passed
    if cond:
        passed += 1
        print(f"  ok    {name}")
    else:
        failures.append(name)
        print(f"  FAIL  {name}" + (f" — {detail}" if detail else ""))


def make_fixture(tmp):
    """A throwaway repo laid out the way gate-integrity expects: hooks/ beside
    ops/config/gate-baseline.json, with the real script copied in so the test
    drives production code rather than a re-implementation of it."""
    root = Path(tmp) / "fixture"
    (root / "hooks").mkdir(parents=True)
    (root / "ops" / "config").mkdir(parents=True)
    shutil.copy(GATE, root / "hooks" / "gate-integrity.py")

    # A couple of ordinary gate files plus the real gate-integrity.py, which
    # guards itself and therefore has to be in the baseline too.
    (root / "hooks" / "demo-gate.py").write_text("print('v1')\n")
    (root / "hooks" / "other-gate.py").write_text("print('other')\n")
    write_baseline(root)
    return root


def sha_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_baseline(root: Path, omit=None, extra=None):
    hashes = {}
    for f in sorted((root / "hooks").iterdir()):
        if f.suffix == ".py" and f.name != omit:
            hashes[f.name] = sha_of(f)
    if extra:
        hashes.update(extra)
    (root / "ops" / "config" / "gate-baseline.json").write_text(json.dumps({
        "_note": "fixture baseline",
        "blessed_by": "fixture@example.invalid",
        "blessed_at_rev": "0000000",
        "hashes": hashes,
        "contracts": {},
    }, indent=2) + "\n")


def run(root: Path, *args):
    """Drive the copied script inside the fixture. CLAUDE_PROJECT_DIR points at
    the fixture so the project-adapter probe cannot reach the real machine."""
    env = dict(os.environ)
    env["CLAUDE_PROJECT_DIR"] = str(root)
    p = subprocess.run([sys.executable, str(root / "hooks" / "gate-integrity.py"), *args],
                       capture_output=True, text=True, env=env, cwd=str(root))
    return p.returncode, (p.stdout or "") + (p.stderr or "")


print("\nhooks/gate-integrity.py --strict — CI can finally fail on baseline drift")

if not GATE.exists():
    print(f"  FAIL  the gate does not exist at {GATE}")
    sys.exit(1)

with tempfile.TemporaryDirectory() as tmp:
    root = make_fixture(tmp)

    # ── 1. clean tree ───────────────────────────────────────────────────────
    rc, out = run(root)
    check("a clean tree exits 0 without --strict", rc == 0, f"rc={rc}")
    rc, out = run(root, "--strict")
    check("a clean tree exits 0 WITH --strict", rc == 0, f"rc={rc} {out[:160]}")

    # ── 2. a changed gate ───────────────────────────────────────────────────
    (root / "hooks" / "demo-gate.py").write_text("print('v2 — edited, never blessed')\n")
    rc, out = run(root)
    check("a changed gate still exits 0 bare (the boot hook must never block)",
          rc == 0, f"rc={rc}")
    check("and it still says so in the report", "CHANGED" in out and "demo-gate.py" in out)
    rc, out = run(root, "--strict")
    check("a changed gate exits NONZERO with --strict", rc != 0, f"rc={rc}")
    check("--strict prints the same report, it does not go quiet",
          "CHANGED" in out and "demo-gate.py" in out)

    # ── 3. a gate that was blessed and is now gone ──────────────────────────
    write_baseline(root)                       # re-bless the edit, clean again
    rc, _ = run(root, "--strict")
    check("re-blessing the edit makes --strict pass again", rc == 0, f"rc={rc}")
    (root / "hooks" / "other-gate.py").unlink()
    rc, out = run(root, "--strict")
    check("a deleted blessed gate exits NONZERO with --strict", rc != 0, f"rc={rc}")
    check("and the report names it MISSING", "MISSING" in out and "other-gate.py" in out)

    # ── 4. a gate nothing ever blessed — THE 2026-08-14 REGRESSION ──────────
    # This is the exact shape a stale pull request produces when its whole-file
    # baseline lands on top of a newer one: the gate FILE is present from the
    # other branch, and the baseline that overwrote main has no entry for it.
    write_baseline(root)
    (root / "hooks" / "brand-new-gate.py").write_text("print('shipped unblessed')\n")
    rc, out = run(root)
    check("an unblessed gate still exits 0 bare", rc == 0, f"rc={rc}")
    rc, out = run(root, "--strict")
    check("an unblessed gate exits NONZERO with --strict", rc != 0, f"rc={rc}")
    check("and the report names it UNBLESSED",
          "UNBLESSED" in out and "brand-new-gate.py" in out)

    # ── 5. --strict must not invent failures from machine state ─────────────
    # A CI runner has no vault, no ~/.claude and no ~/.codex. Those checks are
    # ENVIRONMENT, not CONTENT, and a --strict that failed on them would be
    # removed the first week it ran and protect nothing after that.
    write_baseline(root)
    (root / "hooks" / "brand-new-gate.py").unlink()
    write_baseline(root)
    rc, out = run(root, "--strict")
    check("environment-only findings do not fail --strict", rc == 0,
          f"rc={rc} {out[:200]}")

# The owning tool must never put the git email into a committed audit field.
with tempfile.TemporaryDirectory() as tmp:
    root = make_fixture(tmp)
    env = dict(os.environ, GIT_CONFIG_COUNT="1", GIT_CONFIG_KEY_0="user.email",
               GIT_CONFIG_VALUE_0="private-fixture@example.invalid")
    proc = subprocess.run([sys.executable, str(root / "hooks/gate-integrity.py"), "--bless"],
                          cwd=root, env=env, text=True, capture_output=True)
    baseline = json.loads((root / "ops/config/gate-baseline.json").read_text())
    check("bless persists private attribution without a raw git email",
          proc.returncode == 0 and "@" not in baseline["blessed_by"] and
          "private-fixture" not in baseline["blessed_by"], proc.stdout)

print(f"\n{passed} passed, {len(failures)} failed")
if failures:
    print(f"FAILED: {', '.join(failures)}")
    sys.exit(1)
print("GATE INTEGRITY STRICT SELFTEST PASSED: content drift fails CI, the boot "
      "hook still never blocks, and machine state never fabricates a failure.")

import runpy as _dot_runpy
_dot_runpy.run_path(str(Path(__file__).with_name("dot-review-selftest.py")))["run_regressions"](["test_b30"])
