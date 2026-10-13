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


def write_catalog(path, placements):
    """A services.json with one launchd service per (plist, runs_on)."""
    services = [{"key": name.removesuffix(".plist"), "runtime": "launchd",
                 "environments": [{"environment": "production",
                                   "deploy_mechanism": f"ops/launchd/{name}",
                                   "runs_on": where}]}
                for name, where in placements.items()]
    path.write_text(json.dumps({"services": services}), encoding="utf-8")


def launchd_rows(mod, primary, catalog, agents, holds=None):
    mod.ROWS.clear()
    mod.audit_launchd(primary, str(catalog), str(agents), holds={} if holds is None else holds)
    return {label: (state, detail) for section, label, state, detail in mod.ROWS
            if section == "launchd"}


def check_launchd_placement(mod):
    """The catalog's runs_on joined with what launchd actually has installed.

    Every case runs on fixtures: the cloud container has no launchd, and a test
    that read the real ~/Library/LaunchAgents would assert one machine's state.
    """
    sys.path.insert(0, REPO)
    from lib import launchd_scope

    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        catalog, agents = root / "services.json", root / "LaunchAgents"
        agents.mkdir()

        # ---- launchd_scope derives the two sets from the catalog
        write_catalog(catalog, {"com.carr.a.plist": "primary", "com.carr.b.plist": "secondary",
                                "com.carr.c.plist": "every"})
        derived = launchd_scope.placements(str(catalog))
        check("placements reads runs_on per plist",
              derived == {"com.carr.a.plist": "primary", "com.carr.b.plist": "secondary",
                          "com.carr.c.plist": "every"}, f"got {derived}")
        catalog.write_text(json.dumps({"services": [
            {"key": "x", "environments": [{"environment": "production",
                                           "deploy_mechanism": "ops/launchd/com.carr.a.plist"}]}]}))
        try:
            launchd_scope.placements(str(catalog))
            refused = ""
        except ValueError as exc:
            refused = str(exc)
        check("a launchd environment without runs_on is refused",
              "x:production" in refused and "runs_on" in refused, f"got {refused!r}")
        catalog.write_text(json.dumps({"services": [
            {"key": k, "environments": [{"environment": "production", "runs_on": w,
                                         "deploy_mechanism": "ops/launchd/com.carr.a.plist"}]}
            for k, w in (("x", "primary"), ("y", "every"))]}))
        try:
            launchd_scope.placements(str(catalog))
            refused = ""
        except ValueError as exc:
            refused = str(exc)
        check("two services placing one plist differently are refused",
              "com.carr.a.plist" in refused, f"got {refused!r}")

        # ---- the four cells of the join, on a primary machine
        write_catalog(catalog, {"com.carr.here.plist": "primary",
                                "com.carr.there.plist": "secondary",
                                "com.carr.missing.plist": "every",
                                "com.carr.held.plist": "primary",
                                "com.carr.misplaced.plist": "secondary"})
        for name in ("com.carr.here.plist", "com.carr.stray.plist", "com.carr.misplaced.plist",
                     "com.apple.unrelated.plist"):
            (agents / name).write_text("<plist/>", encoding="utf-8")
        rows = launchd_rows(mod, True, catalog, agents,
                            holds={"com.carr.held": launchd_hold_stub("paused by Joe")})
        install = "action: python3 ops/config-as-code.py install --apply"
        check("declared here and installed is OK",
              rows.get("com.carr.here") == (mod.OK, "runs on primary · installed"),
              f"got {rows.get('com.carr.here')}")
        check("declared for the other machine and absent is BY DESIGN",
              rows.get("com.carr.there") == (mod.DESIGN, "runs on secondary · not installed here"),
              f"got {rows.get('com.carr.there')}")
        check("declared here and absent is a GAP naming the install door",
              rows.get("com.carr.missing") == (
                  mod.GAP, "runs on every · not installed — " + install),
              f"got {rows.get('com.carr.missing')}")
        check("installed and undeclared is a GAP naming the register action",
              rows.get("com.carr.stray") == (
                  mod.GAP, "installed but declared nowhere — action: uninstall it or add its "
                           "plist to ops/launchd and register it in ops/config/services.json"),
              f"got {rows.get('com.carr.stray')}")
        check("a held job absent here is BY DESIGN with the hold's words",
              rows.get("com.carr.held") == (mod.DESIGN, "runs on primary · HELD: paused by Joe"),
              f"got {rows.get('com.carr.held')}")
        check("a job for the other machine that is installed here is a GAP",
              rows.get("com.carr.misplaced", (None, ""))[0] == mod.GAP
              and "action: launchctl bootout" in rows["com.carr.misplaced"][1],
              f"got {rows.get('com.carr.misplaced')}")
        check("only com.carr agents are audited", "com.apple.unrelated" not in rows,
              f"rows {sorted(rows)}")
        check("every GAP row carries its action",
              all("action: " in detail for state, detail in rows.values() if state == mod.GAP))

        # The same catalog on a secondary flips which side is by design.
        rows = launchd_rows(mod, False, catalog, agents)
        check("on a secondary, a primary job that is installed is a GAP",
              rows.get("com.carr.here", (None,))[0] == mod.GAP, f"got {rows.get('com.carr.here')}")
        check("on a secondary, an absent secondary job is a GAP",
              rows.get("com.carr.there", (None,))[0] == mod.GAP, f"got {rows.get('com.carr.there')}")

        # An ops/launchd plist nobody registered is the resource-collector shape:
        # the installer places it, so uninstalling alone does not stick.
        repo_launchd = root / "ops-launchd"
        repo_launchd.mkdir()
        (repo_launchd / "com.carr.stray.plist").write_text("<plist/>", encoding="utf-8")
        mod.ROWS.clear()
        mod.audit_launchd(True, str(catalog), str(agents), holds={},
                          repo_launchd=str(repo_launchd))
        stray = {label: detail for _, label, _, detail in mod.ROWS}.get("com.carr.stray", "")
        check("an undeclared ops/launchd plist names DEFINITION_ONLY as the alternative",
              "lib/launchd_hold.DEFINITION_ONLY" in stray, f"got {stray!r}")

        (agents / "com.carr.repo-hygiene-janitor.plist").write_text("<plist/>", encoding="utf-8")
        janitor = launchd_rows(mod, True, catalog, agents).get("com.carr.repo-hygiene-janitor", ("", ""))
        check("an installed DEFINITION_ONLY agent is a GAP whose action removes it",
              janitor[0] == mod.GAP and "launchctl bootout gui/$(id -u)/com.carr.repo-hygiene-janitor"
              in janitor[1], f"got {janitor}")
        (agents / "com.carr.repo-hygiene-janitor.plist").unlink()

        rows = launchd_rows(mod, None, catalog, agents)
        check("an unknown machine role is one GAP, not a guess",
              list(rows) == ["placement"] and rows["placement"][0] == mod.GAP,
              f"got {rows}")
        rows = launchd_rows(mod, True, catalog, root / "absent")
        check("a host without LaunchAgents is one GAP",
              list(rows) == ["LaunchAgents"] and rows["LaunchAgents"][0] == mod.GAP,
              f"got {rows}")

        # ---- --strict exits 1 on a GAP and 0 otherwise; plain always exits 0
        def run_main(argv):
            mod.ROWS.clear()
            saved = {name: getattr(mod, name) for name in
                     ("load_config_as_code", "audit_identity", "audit_repo",
                      "audit_config_and_gates", "audit_toolchain", "audit_optional")}
            mod.load_config_as_code = lambda: None
            mod.audit_identity = lambda cac: True
            for name in ("audit_repo", "audit_config_and_gates", "audit_toolchain",
                         "audit_optional"):
                setattr(mod, name, lambda: None)
            try:
                with open(os.devnull, "w") as sink:
                    stdout, sys.stdout = sys.stdout, sink
                    try:
                        return mod.main(argv)
                    finally:
                        sys.stdout = stdout
            finally:
                for name, value in saved.items():
                    setattr(mod, name, value)

        paths = ["--services", str(catalog), "--launch-agents", str(agents)]
        check("--strict exits 1 when a launchd row is a GAP",
              run_main(["--strict", "--json"] + paths) == 1)
        check("without --strict a GAP still exits 0", run_main(["--json"] + paths) == 0)
        write_catalog(catalog, {"com.carr.here.plist": "primary",
                                "com.carr.there.plist": "secondary"})
        for name in ("com.carr.stray.plist", "com.carr.misplaced.plist"):
            (agents / name).unlink()
        check("--strict exits 0 when every launchd row is OK or BY DESIGN",
              run_main(["--strict"] + paths) == 0)


def launchd_hold_stub(reason):
    from lib.launchd_hold import Hold
    return Hold("com.carr.held", reason, 0.0, "fixture")


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

    check_launchd_placement(mod)

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
