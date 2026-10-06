#!/usr/bin/env python3
"""Guards for ops/machine-sync-audit.py.

The audit exists to catch a machine that LOOKS in step and is not, so the two
ways it can betray that job are: reporting a gap as fine, and hard-coding a
floor that has quietly drifted from the files that actually enforce it.

The floor check is the load-bearing one. Three places name Python 3.10 — this
audit, requirements.txt's mypy marker, and bin/migrate-dell.sh's PY_MIN_MINOR.
If one moves and the others do not, the audit reports a machine as fine while
the migration script builds something the repo cannot run, which is the exact
failure of 2026-08-19 wearing a different hat.
"""
import importlib.util
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AUDIT = os.path.join(REPO, "ops", "machine-sync-audit.py")
# The repository's one fixture scrubber removes every Git location/config
# override that could redirect a throwaway commit into the invoking checkout.
from git_env import fixture_env  # noqa: E402

# (label, passed, detail)
RESULTS: list[tuple[str, bool, str]] = []


def check(label, ok, detail=""):
    RESULTS.append((label, bool(ok), detail))
    print(f"{'PASS' if ok else 'FAIL'}  {label}" + (f"  ({detail})" if detail and not ok else ""))


def load():
    spec = importlib.util.spec_from_file_location("msa", AUDIT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main():
    mod = load()

    # ---- the floor must agree everywhere it is enforced
    check("the audit names a 3.10 floor", mod.PY_MIN == (3, 10), f"got {mod.PY_MIN}")

    reqs = open(os.path.join(REPO, "requirements.txt"), encoding="utf-8").read()
    m = re.search(r'python_version\s*>=\s*"3\.(\d+)"', reqs)
    check("requirements.txt pins mypy to the same floor",
          m is not None and int(m.group(1)) == mod.PY_MIN[1],
          f"requirements says 3.{m.group(1) if m else '?'}")

    mig = open(os.path.join(REPO, "bin", "migrate-dell.sh"), encoding="utf-8").read()
    m2 = re.search(r"PY_MIN_MINOR=(\d+)", mig)
    check("migrate-dell.sh enforces the same floor",
          m2 is not None and int(m2.group(1)) == mod.PY_MIN[1],
          f"migrate says 3.{m2.group(1) if m2 else '?'}")

    # ---- a GAP must never be reported as fine
    check("the three states are distinct", len({mod.OK, mod.DESIGN, mod.GAP}) == 3)

    mod.ROWS.clear()
    mod.row("t", "a", mod.GAP, "d")
    mod.row("t", "b", mod.OK, "d")
    mod.row("t", "c", mod.DESIGN, "d")
    gaps = [r for r in mod.ROWS if r[2] == mod.GAP]
    check("only GAP rows count as gaps", len(gaps) == 1, f"counted {len(gaps)}")
    check("a by-design row is not a gap",
          not any(r[2] == mod.GAP for r in mod.ROWS if r[1] == "c"))

    # ---- it must actually run on this machine, read-only, and exit 0 plain
    py = os.path.join(REPO, ".venv", "bin", "python")
    py = py if os.path.exists(py) else sys.executable

    # Run the exact audit bytes from an isolated tracked fixture. The gates pool
    # deliberately runs selftests concurrently, so a status snapshot of REPO
    # would also observe another test's temporary mutation and misattribute it
    # to this audit.
    git_fixture_env = fixture_env()

    def tracked_status(repo):
        out = subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"],
                             capture_output=True, text=True, cwd=repo, check=True,
                             env=git_fixture_env).stdout
        return out.splitlines()

    def exercise_isolated_audit():
        with tempfile.TemporaryDirectory() as fixture_dir:
            fixture_repo = pathlib.Path(fixture_dir)
            fixture_ops = fixture_repo / "ops"
            fixture_ops.mkdir()
            fixture_audit = fixture_ops / "machine-sync-audit.py"
            shutil.copy2(AUDIT, fixture_audit)
            fixture_sentinel = fixture_repo / "requirements.txt"
            fixture_sentinel.write_text("fixture remains unchanged\n", encoding="utf-8")
            subprocess.run(["git", "init", "-q"], cwd=fixture_repo, check=True,
                           env=git_fixture_env)
            subprocess.run(["git", "add", "ops/machine-sync-audit.py", "requirements.txt"],
                           cwd=fixture_repo, check=True, env=git_fixture_env)
            subprocess.run(
                ["git", "-c", "user.name=fixture", "-c", "user.email=fixture@invalid",
                 "commit", "-q", "-m", "fixture"],
                cwd=fixture_repo, check=True, env=git_fixture_env,
            )
            before = tracked_status(fixture_repo)
            result = subprocess.run(
                [py, fixture_audit, "--json"], capture_output=True, text=True,
                cwd=fixture_repo, timeout=300, env=git_fixture_env,
            )
            after = tracked_status(fixture_repo)
            changed = sorted(set(after) - set(before)) + sorted(set(before) - set(after))

            # Keep the detector itself honest: the same isolated snapshot must
            # notice a subprocess that writes one of its tracked files.
            mutator = fixture_repo / "mutator.py"
            mutator.write_text(
                "from pathlib import Path\nPath('requirements.txt').write_text('mutated\\n')\n",
                encoding="utf-8",
            )
            before_mutation = tracked_status(fixture_repo)
            subprocess.run([py, mutator], cwd=fixture_repo, check=True,
                           env=git_fixture_env)
            after_mutation = tracked_status(fixture_repo)
            detected = sorted(set(after_mutation) - set(before_mutation)) + sorted(
                set(before_mutation) - set(after_mutation))
            return result, changed, detected

    p, changed, detected = exercise_isolated_audit()
    check("the audit exits 0 without --strict", p.returncode == 0, f"rc={p.returncode}")
    try:
        data = json.loads(p.stdout)
    except ValueError as exc:
        data = None
        check("--json emits valid JSON", False, repr(exc))
    if data:
        check("--json emits valid JSON", True)
        sections = {r["section"] for r in data["rows"]}
        for want in ("identity", "repo", "config", "toolchain", "optional"):
            check(f"the {want} section is reported", want in sections, f"{sorted(sections)}")
        check("every row carries a state",
              all(r["state"] in (mod.OK, mod.DESIGN, mod.GAP) for r in data["rows"]))
        check("the gap count matches the rows",
              data["gaps"] == sum(1 for r in data["rows"] if r["state"] == mod.GAP))

    # ---- read-only: running it must not dirty the tree
    #
    # BEFORE-AND-AFTER, not after-only. This used to read `git status` exactly
    # once, after the audit, and assert the tree was CLEAN — so it did not
    # measure "the audit changed nothing", it measured "nobody on this machine
    # has uncommitted work". ~/carr-system is a shared checkout that several
    # Claude sessions write at once by design, so the test failed on other
    # people's files: on 2026-08-19 it blocked a push with two _to_delete
    # deletions and a claude-tree settings edit, none of them belonging to the
    # session being gated and none of them touched by the audit. A test that
    # fails on work it does not own teaches its own bypass — the pre-push hook
    # prints CARR_SKIP_CI=1 right underneath it.
    #
    # The diff is the claim. Comparing the two snapshots proves the audit is
    # read-only whatever else is in flight, and it still catches the real
    # regression: an audit that writes a tracked file shows up as a new line.
    check("the audit changes nothing on the machine", not changed,
          f"the audit altered: {changed[:3]}")
    check("the read-only detector catches a tracked mutation",
          detected == [" M requirements.txt"], f"observed {detected}")

    failed = sum(1 for _, ok, _ in RESULTS if not ok)
    print(f"\nmachine-sync-audit-selftest: {len(RESULTS) - failed}/{len(RESULTS)} passed")
    if failed:
        print("FAILED:")
        for label, ok, detail in RESULTS:
            if not ok:
                print(f"  - {label}  {detail}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
