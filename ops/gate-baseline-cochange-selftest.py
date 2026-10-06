#!/usr/bin/env python3
"""
gate-baseline-cochange-selftest.py — the acceptance test for the gate/baseline
co-change half of ops/githooks/pre-commit, written before the check itself
(rule e65efc68, enforcing rule c0b38d80).

WHAT THE CHECK IS FOR. The blessed baseline (ops/config/gate-baseline.json) is
the only thing that lets a session distinguish "a gate changed on purpose" from
"something disabled a gate". Rule c0b38d80 says the re-bless rides in the SAME
commit as the gate change, and it has been violated twice for real:

  2026-08-12  a gate change landed without its bless, and main's baseline was
              later overwritten by a PR cut before it (repaired in 6e2be66).
  2026-08-14  a live hotfix to hooks/settings-change-gate.py sat uncommitted
              and unblessed in the shared checkout; every session that morning
              started under a GATE INTEGRITY FAILURE banner it could do
              nothing about.

Both incidents are the same shape: the gate moved, the baseline did not, and
every later session had to treat the enforcement layer as untrusted. A commit
is the one moment where the two can be forced to move together.

WHAT THE CHECK MUST DO:

  1. REFUSE a staged change to any hooks/*.py gate file when
     ops/config/gate-baseline.json is not staged with it.
  2. REFUSE when the baseline IS staged but its recorded hash does not match
     the staged gate content — a bless of the wrong bytes is the 2026-08-13
     false re-bless, not a bless.
  3. ALLOW the same commit once the baseline is re-blessed and staged.
  3b. AUTO-APPLY that re-bless (2026-08-23 gates council): the baseline update
     is fully determined by the staged bytes, so the check computes it and
     stages it into the SAME commit rather than refusing and making the author
     retype it. The invariant is untouched — gate and bless still land together,
     and a wrong-bytes bless is still impossible, because the auto-applied
     result is re-verified by the same code that would have refused it. What the
     auto-apply must NOT do is bless a gate DELETION (that is a retirement, not
     a content update) or touch an entry for a gate this commit does not stage.
     CARR_NO_AUTO_BLESS=1 restores refuse-and-explain, and the refusal text is
     still asserted here so the manual path cannot rot.
  4. IGNORE commits that touch no gate file and no wiring contract; ignore
     hooks/*-selftest.py, which gate-integrity deliberately does not protect.
  5. COVER the wiring contracts (rule-enforcement-map.json and friends) the
     same way — a contract changes enforcement without touching a hook.
  6. HAVE A WORKING ESCAPE HATCH, same idiom as its neighbours:
     CARR_ALLOW_UNBLESSED_GATE=1 for one command.
  7. NOT TOUCH the repository that invoked it (the ops/git_env.py leak).

RUNNING IT. No database, no network, no production access:

    .venv/bin/python ops/gate-baseline-cochange-selftest.py

ops/ci.sh already globs ops/*-selftest.py, so this is enforced from the commit
that adds it, with no CI change.
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
HOOK = REPO / "ops" / "githooks" / "pre-commit"
HELPER = REPO / "ops" / "githooks" / "staged-gate-bless-check.py"

# ops/git_env.py is the one scrub list; every git hook exports GIT_DIR, which
# outranks both cwd and -C, so the hand-rolled loop this file used to carry
# (see item 9's "the ops/git_env.py leak" in the docstring above) is exactly
# the kind of second copy that can drift from the real list. __file__'s
# directory is ops/, so this reaches it directly.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from git_env import fixture_env  # noqa: E402

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


def git(repo, *args, env=None, check_rc=False):
    # THE LEAK GUARD, same as main-commit-gate-selftest.py: git hands every hook
    # a GIT_DIR pointing at the repository that invoked it. fixture_env() is
    # ops/git_env.py's one definition of the scrub list, used here instead of
    # a second hand-rolled copy of it.
    e = fixture_env()
    if env:
        e.update(env)
    p = subprocess.run(["git", "-C", str(repo)] + list(args),
                       capture_output=True, text=True, env=e)
    if check_rc and p.returncode != 0:
        raise SystemExit(f"fixture setup failed: git {' '.join(args)}\n{p.stderr}")
    return p


NO_AUTO = {"CARR_NO_AUTO_BLESS": "1"}


def committed_baseline(repo) -> dict:
    """The baseline as it exists in HEAD — what the commit actually carries."""
    raw = git(repo, "show", "HEAD:ops/config/gate-baseline.json").stdout
    try:
        return json.loads(raw)
    except ValueError:
        return {}


def blessed_hash(repo, name, table="hashes"):
    return (committed_baseline(repo).get(table) or {}).get(name)


def sha256_of(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def write_baseline(repo, hashes: dict, contracts: dict | None = None):
    path = repo / "ops" / "config" / "gate-baseline.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "_note": "fixture baseline",
        "blessed_by": "fixture@example.invalid",
        "blessed_at_rev": "0000000",
        "hashes": hashes,
        "contracts": contracts or {},
    }, indent=2) + "\n")
    return path


def make_repo(tmp):
    """A throwaway repo on a feature branch (the main guard is not under test
    here) with the hook and its helper installed through core.hooksPath."""
    repo = Path(tmp) / "fixture"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "feature/gates", check_rc=True)
    git(repo, "config", "user.email", "fixture@example.invalid", check_rc=True)
    git(repo, "config", "user.name", "Fixture", check_rc=True)
    hooks = repo / "githooks"
    hooks.mkdir()
    shutil.copy(HOOK, hooks / "pre-commit")
    os.chmod(hooks / "pre-commit", 0o755)
    shutil.copy(HELPER, hooks / "staged-gate-bless-check.py")
    git(repo, "config", "core.hooksPath", "githooks", check_rc=True)

    (repo / "hooks").mkdir()
    gate = repo / "hooks" / "demo-gate.py"
    gate.write_text("print('v1')\n")
    write_baseline(repo, {"demo-gate.py": sha256_of("print('v1')\n")})
    (repo / "seed.txt").write_text("seed\n")
    git(repo, "add", "-A", check_rc=True)
    git(repo, "commit", "-q", "-m", "seed", check_rc=True)
    return repo


print("\nops/githooks/pre-commit — a gate change carries its bless (c0b38d80)")

for required, what in ((HOOK, "hook"), (HELPER, "helper")):
    if not required.exists():
        print(f"  FAIL  the {what} does not exist at {required}")
        print("\n1 check(s) failed: missing file")
        sys.exit(1)

with tempfile.TemporaryDirectory() as tmp:
    repo = make_repo(tmp)
    head0 = git(repo, "rev-parse", "HEAD").stdout.strip()

    # ── 1. THE MANUAL PATH, under CARR_NO_AUTO_BLESS=1. It is asserted first
    #      and in full: auto-apply is the convenience, this is the invariant,
    #      and a remedy nobody exercises is a remedy that rots.
    (repo / "hooks" / "demo-gate.py").write_text("print('v2')\n")
    git(repo, "add", "hooks/demo-gate.py")
    r = git(repo, "commit", "-m", "edit gate without bless", env=NO_AUTO)
    check("a gate edit without the baseline is refused", r.returncode != 0,
          f"exit {r.returncode}")
    check("and nothing was committed",
          git(repo, "rev-parse", "HEAD").stdout.strip() == head0)
    msg = (r.stdout + r.stderr).lower()
    check("the refusal names the unblessed file", "demo-gate.py" in msg)
    check("the refusal says how to bless", "--bless" in msg)
    check("the refusal names its escape hatch", "carr_allow_unblessed_gate" in msg)
    check("the refused work is still staged",
          "hooks/demo-gate.py" in git(repo, "diff", "--cached", "--name-only").stdout)

    # ── 1b. THE SAME COMMIT, auto-apply on. It is allowed, and the baseline it
    #       carries records the bytes that were STAGED.
    r = git(repo, "commit", "-m", "edit gate, bless applied for me")
    check("auto-apply lets the unblessed gate edit commit", r.returncode == 0,
          r.stderr)
    check("auto-apply staged the baseline into that same commit",
          "ops/config/gate-baseline.json"
          in git(repo, "show", "--name-only", "--format=", "HEAD").stdout)
    check("the auto-applied hash is the STAGED bytes, not something else",
          blessed_hash(repo, "demo-gate.py") == sha256_of("print('v2')\n"),
          blessed_hash(repo, "demo-gate.py"))
    check("auto-apply announced what it changed",
          "demo-gate.py" in (r.stdout + r.stderr))
    head1 = git(repo, "rev-parse", "HEAD").stdout.strip()

    # ── 1c. AUTO-APPLY MUST NOT ADOPT A GATE THIS COMMIT DOES NOT STAGE.
    #       Defect 320f5531: one session's bless absorbing another's in-flight
    #       edit. peer-gate.py is blessed at v1 and dirty at v2 in the working
    #       tree, and only demo-gate.py is staged.
    (repo / "hooks" / "peer-gate.py").write_text("print('peer-v1')\n")
    write_baseline(repo, {"demo-gate.py": sha256_of("print('v2')\n"),
                          "peer-gate.py": sha256_of("print('peer-v1')\n")})
    git(repo, "add", "hooks/peer-gate.py", "ops/config/gate-baseline.json")
    git(repo, "commit", "-q", "-m", "seed a peer gate")
    (repo / "hooks" / "peer-gate.py").write_text("print('peer-v2-IN-FLIGHT')\n")
    (repo / "hooks" / "demo-gate.py").write_text("print('v3')\n")
    git(repo, "add", "hooks/demo-gate.py")
    r = git(repo, "commit", "-m", "edit only demo-gate")
    check("auto-apply allows the scoped edit", r.returncode == 0, r.stderr)
    check("auto-apply updated the gate it staged",
          blessed_hash(repo, "demo-gate.py") == sha256_of("print('v3')\n"))
    check("auto-apply did NOT adopt the peer session's unstaged gate",
          blessed_hash(repo, "peer-gate.py") == sha256_of("print('peer-v1')\n"),
          blessed_hash(repo, "peer-gate.py"))

    # ── 2. A STALE BLESS IS NOT A BLESS. With auto-apply off the mismatch is
    #      refused; with it on the mismatch is CORRECTED rather than accepted —
    #      what must never happen is the wrong hash landing (2026-08-13).
    (repo / "hooks" / "demo-gate.py").write_text("print('v4')\n")
    write_baseline(repo, {"demo-gate.py": sha256_of("print('WRONG')\n"),
                          "peer-gate.py": sha256_of("print('peer-v1')\n")})
    git(repo, "add", "hooks/demo-gate.py", "ops/config/gate-baseline.json")
    r = git(repo, "commit", "-m", "mismatched bless", env=NO_AUTO)
    check("a baseline whose hash does not match the staged gate is refused",
          r.returncode != 0, f"exit {r.returncode}")
    check("the mismatch refusal names the file",
          "demo-gate.py" in (r.stdout + r.stderr).lower())
    r = git(repo, "commit", "-m", "mismatched bless, corrected")
    check("auto-apply corrects a wrong-bytes bless rather than accepting it",
          r.returncode == 0 and
          blessed_hash(repo, "demo-gate.py") == sha256_of("print('v4')\n"),
          blessed_hash(repo, "demo-gate.py"))

    # ── 3. the matching bless lets the same commit through ──────────────────
    (repo / "hooks" / "demo-gate.py").write_text("print('v5')\n")
    write_baseline(repo, {"demo-gate.py": sha256_of("print('v5')\n"),
                          "peer-gate.py": sha256_of("print('peer-v1')\n")})
    git(repo, "add", "hooks/demo-gate.py", "ops/config/gate-baseline.json")
    r = git(repo, "commit", "-m", "edit gate with real bless")
    check("gate edit plus matching baseline is allowed", r.returncode == 0,
          r.stderr)
    check("and it landed", git(repo, "rev-parse", "HEAD").stdout.strip() != head1)

    # ── 4. a NEW gate file needs a bless too ────────────────────────────────
    (repo / "hooks" / "new-gate.py").write_text("print('new')\n")
    git(repo, "add", "hooks/new-gate.py")
    r = git(repo, "commit", "-m", "new gate without bless", env=NO_AUTO)
    check("a new gate file without a baseline entry is refused",
          r.returncode != 0, f"exit {r.returncode}")
    r = git(repo, "commit", "-m", "new gate, bless applied for me")
    check("a new gate file is auto-blessed into its own commit",
          r.returncode == 0, r.stderr)
    check("the new gate's auto-applied entry is correct",
          blessed_hash(repo, "new-gate.py") == sha256_of("print('new')\n"),
          blessed_hash(repo, "new-gate.py"))
    check("auto-blessing a new gate carried the existing entries forward",
          blessed_hash(repo, "demo-gate.py") == sha256_of("print('v5')\n"))

    # ── 5. contracts are gates by another name ──────────────────────────────
    contract = repo / "ops" / "config" / "rule-enforcement-map.json"
    contract.write_text('{"rules": 1}\n')
    git(repo, "add", "ops/config/rule-enforcement-map.json")
    r = git(repo, "commit", "-m", "contract without bless", env=NO_AUTO)
    check("a wiring-contract edit without the baseline is refused",
          r.returncode != 0, f"exit {r.returncode}")
    r = git(repo, "commit", "-m", "contract, bless applied for me")
    check("a wiring-contract edit is auto-blessed", r.returncode == 0, r.stderr)
    check("the contract's auto-applied entry lands in the contracts table",
          blessed_hash(repo, "rule-enforcement-map.json", "contracts")
          == sha256_of('{"rules": 1}\n'),
          blessed_hash(repo, "rule-enforcement-map.json", "contracts"))

    # The session lifecycle's executable wiring and data contract are both
    # enforcement. Protecting only the Python would allow a threshold, event,
    # or model window to move without the same reviewed bless.
    for contract_name, body in (
        ("hooks.json", '{"Stop": []}\n'),
        ("session-context-lifecycle.v2.json", '{"schema_version": 1}\n'),
    ):
        contract = repo / "ops" / "config" / contract_name
        contract.write_text(body)
        git(repo, "add", f"ops/config/{contract_name}")
        r = git(repo, "commit", "-m", f"{contract_name} without bless", env=NO_AUTO)
        check(f"{contract_name} edit without baseline is refused",
              r.returncode != 0, f"exit {r.returncode}")
        r = git(repo, "commit", "-m", f"{contract_name}, bless applied")
        check(f"{contract_name} is auto-blessed", r.returncode == 0, r.stderr)
        check(f"{contract_name} lands in contracts table",
              blessed_hash(repo, contract_name, "contracts") == sha256_of(body),
              blessed_hash(repo, contract_name, "contracts"))

    # ── 6. what the check must leave alone ──────────────────────────────────
    (repo / "unrelated.txt").write_text("hello\n")
    git(repo, "add", "unrelated.txt")
    r = git(repo, "commit", "-m", "unrelated change")
    check("a commit touching no gate is allowed", r.returncode == 0, r.stderr)

    (repo / "hooks" / "demo-selftest.py").write_text("print('test')\n")
    git(repo, "add", "hooks/demo-selftest.py")
    r = git(repo, "commit", "-m", "selftest change")
    check("hooks/*-selftest.py is not treated as a gate", r.returncode == 0,
          r.stderr)

    # ── 7. the escape hatch works ───────────────────────────────────────────
    (repo / "hooks" / "demo-gate.py").write_text("print('v3')\n")
    git(repo, "add", "hooks/demo-gate.py")
    r = git(repo, "commit", "-m", "hatch",
            env={"CARR_ALLOW_UNBLESSED_GATE": "1"})
    check("CARR_ALLOW_UNBLESSED_GATE=1 lets an unblessed gate commit through",
          r.returncode == 0, r.stderr)

    # ── 8. a gate DELETION still requires the baseline to move ──────────────
    git(repo, "rm", "-q", "hooks/new-gate.py")
    r = git(repo, "commit", "-m", "delete gate without bless")
    # AUTO-APPLY ON, and still refused. Removing a gate is a retirement: the
    # baseline entry is the only record the gate existed, so a mechanism that
    # dropped it automatically would turn `git rm` into a silent, green
    # de-protection. This is the one case that must stay a human decision.
    check("deleting a gate without touching the baseline is refused",
          r.returncode != 0, f"exit {r.returncode}")
    check("a deletion is never auto-blessed away",
          blessed_hash(repo, "new-gate.py") == sha256_of("print('new')\n"),
          blessed_hash(repo, "new-gate.py"))
    write_baseline(repo, {"demo-gate.py": sha256_of("print('v5')\n")},
                   {"rule-enforcement-map.json": sha256_of('{"rules": 1}\n')})
    git(repo, "add", "ops/config/gate-baseline.json")
    r = git(repo, "commit", "-m", "delete gate with bless")
    check("deleting a gate with the baseline updated is allowed",
          r.returncode == 0, r.stderr)

    # ── 9. the hook did not touch the repository that invoked it ────────────
    outer_status = subprocess.run(["git", "-C", str(REPO), "status", "--porcelain"],
                                  capture_output=True, text=True).stdout
    check("the outer repository gained no fixture files",
          "demo-gate.py" not in outer_status and "unrelated.txt" not in outer_status)

print(f"\n{passed} passed, {len(failures)} failed")
if failures:
    print(f"FAILED: {', '.join(failures)}")
    sys.exit(1)
print("GATE BASELINE CO-CHANGE SELFTEST PASSED: a gate or contract change moves "
      "only with a matching bless, everything else is untouched, and the escape "
      "hatch works.")
