#!/usr/bin/env python3
"""reachability-selftest.py — the acceptance suite for ops/reachability-check.py,
written before the check itself (rule e65efc68).

WHAT THE CHECK IS FOR. The 2026-08-23 completion-integrity council's fix D: a
declared control that no live execution path references is not "built", it is a
file. hooks/ledger-boundary-sweep.py sat in the tree since 2026-08-06 with its
own documentation saying "WRITTEN BUT REGISTERED NOWHERE"; ops/launchd carried
a fleet-sync definition no service declared until the recovery registration.
Both passed every gate at discovery because every one asked whether a thing
EXISTS and none asked whether anything CALLS it.

WHAT THIS SUITE IS FOR, and why it is not the same question. Every case below
runs against a synthetic fixture tree, so it measures THE CHECK and says nothing
whatever about the real inventory — the distinction ops/ci.sh's gates class
already draws for its three inventory checks. The real tree is measured by
running ops/reachability-check.py itself, which ci.sh does in the same class.

THE ONE CASE THAT MATTERS MOST is `unwired_hook_is_a_finding` plus
`tombstone_makes_it_pass`. Together they are the council's confirm-or-kill: if a
declared-but-uncalled control does not fail, the check is not checking
reachability and should be deleted rather than expanded. The three tombstone
INVALIDITY cases exist because a tombstone that can be written carelessly is a
mute button, not a mark: it must carry a reason and a reopen condition, it must
name something that still exists, and it must stop applying the moment the thing
it excuses becomes reachable.

Exit 0 all cases pass, 1 any case fails.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "ops"))

FAILED: list = []
PASSED = 0


def check(name, condition, detail=""):
    global PASSED
    if condition:
        PASSED += 1
        print(f"  ok    {name}")
    else:
        FAILED.append(name)
        print(f"  FAIL  {name}  {detail}")


def write(root, rel, body):
    path = os.path.join(root, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        fh.write(body)
    return path


def fixture(tombstones=None):
    """A miniature repository carrying one of every shape the check inspects.

    Deliberately built by hand rather than copied from the real tree: a fixture
    that mirrors the repository would pass or fail for reasons that move when
    the repository moves, which is how a suite starts measuring the weather.
    """
    root = tempfile.mkdtemp(prefix="reachability-fixture-")

    # --- hook lane -------------------------------------------------------
    # alpha is registered globally, beta is registered by the project file,
    # orphan is registered nowhere. helper_mod is imported by alpha; stray_mod
    # is imported by nothing.
    write(root, "hooks/alpha-gate.py", "import helper_mod\nfrom hooks.qualified_mod import VALUE\nprint('alpha')\n")
    write(root, "hooks/beta-gate.py", "print('beta')\n")
    write(root, "hooks/orphan-gate.py", "print('orphan')\n")
    # THE DISPATCHER SHAPE, and it is here because the check's first draft
    # failed it. hooks/run-record-gate.py is registered with the gate it should
    # run as an ARGUMENT, so the real gate's name never appears as a hooks/
    # path. Matching only paths reported two live gates as registered nowhere.
    write(root, "hooks/dispatched-gate.py", "print('dispatched')\n")
    write(root, "hooks/runner-gate.py", "print('runner')\n")
    write(root, "hooks/helper_mod.py", "VALUE = 1\n")
    write(root, "hooks/qualified_mod.py", "VALUE = 1\n")
    write(root, "hooks/stray_mod.py", "VALUE = 2\n")
    write(root, "ops/config/hooks.json", json.dumps({"hooks": {"PreToolUse": [
        {"matcher": "Bash", "hooks": [
            {"type": "command",
             "command": "/usr/bin/env python3 {{REPO}}/hooks/alpha-gate.py"},
            {"type": "command",
             "command": "/usr/bin/python3 {{REPO}}/hooks/runner-gate.py dispatched-gate.py"},
        ]}]}}))
    write(root, ".claude/settings.json", json.dumps({"hooks": {"Stop": [
        {"hooks": [{"type": "command",
         "command": "/usr/bin/env python3 {{REPO}}/hooks/beta-gate.py"}]}]}}))

    # --- launchd lane ----------------------------------------------------
    for label in ("declared", "undeclared"):
        write(root, f"ops/launchd/com.carr.{label}.plist",
              f"<plist><dict><key>Label</key><string>com.carr.{label}</string>"
              f"</dict></plist>\n")
    write(root, "ops/config/services.json", json.dumps({"services": [
        {"key": "declared", "runtime": "launchd", "environments": [
            {"environment": "production",
             "deploy_mechanism": "ops/launchd/com.carr.declared.plist"}]}]}))

    # --- installer lane --------------------------------------------------
    # called-tool has a bin/ door; ci-only-tool is named by a selftest and by
    # the CI script and by nothing else, which is precisely the PR #529 shape.
    write(root, "tools/sync-called-tool.py", "print('called')\n")
    write(root, "tools/sync-ci-only-tool.py", "print('ci only')\n")
    write(root, "bin/sync-called-prod.sh", "#!/bin/zsh\npython3 tools/sync-called-tool.py\n")
    write(root, "bin/sync-orphan-prod.sh", "#!/bin/zsh\nprint orphan\n")
    write(root, "ops/ci.sh", "#!/bin/bash\npython3 tools/sync-ci-only-tool.py\n")
    write(root, "ops/some-selftest.py", "# exercises tools/sync-ci-only-tool.py\n")
    # A RUNBOOK IS A DOOR. bin/sync-called-prod.sh is the human-facing end of
    # its own chain, so nothing in the tree calls it programmatically and
    # nothing ever should; what makes it reachable is that a procedure someone
    # reads names it. Requiring a programmatic caller here would have made
    # every deliberate manual door in the repository a finding.
    write(root, "docs/runbook.md", "Reconcile with `./bin/sync-called-prod.sh --apply`.\n")

    # --- registry lane ---------------------------------------------------
    write(root, "ops/config/rule-enforcement-map.json", json.dumps({
        "control_catalog": {
            "alpha": {"implementation": ["hooks/alpha-gate.py"]},
            "orphaned": {"implementation": ["hooks/orphan-gate.py"]},
            "vanished": {"implementation": ["ops/deleted-check.py"]},
        }}))

    write(root, "ops/config/reachability-tombstones.json",
          json.dumps({"tombstones": tombstones or []}, indent=2))
    return root


def run(root, extra=()):
    """Invoke the check exactly as ci.sh will, so the suite exercises the
    command line rather than an internal function ci.sh never calls."""
    proc = subprocess.run(
        [sys.executable, os.path.join(REPO, "ops", "reachability-check.py"),
         "--repo", root, "--json", *extra],
        capture_output=True, text=True)
    try:
        payload = json.loads(proc.stdout)
    except Exception:
        payload = {"findings": [], "_unparsed": proc.stdout, "_stderr": proc.stderr}
    return proc.returncode, payload


def entries(payload, lane=None):
    return {f["entry"] for f in payload.get("findings", [])
            if lane is None or f["lane"] == lane}


def main():
    print("reachability-selftest — a declared control that nothing calls must fail\n")

    if not os.path.exists(os.path.join(REPO, "ops", "reachability-check.py")):
        print("ops/reachability-check.py does not exist yet — this suite was "
              "written first on purpose (rule e65efc68).", file=sys.stderr)
        return 1

    # ---------------------------------------------------------------- clean
    root = fixture(tombstones=[
        {"lane": "hook", "entry": "hooks/orphan-gate.py",
         "reason": "fixture: deliberately unregistered",
         "reopen_when": "fixture asks for it"},
        {"lane": "hook", "entry": "hooks/stray_mod.py",
         "reason": "fixture: imported by nothing on purpose",
         "reopen_when": "fixture asks for it"},
        {"lane": "launchd", "entry": "ops/launchd/com.carr.undeclared.plist",
         "reason": "fixture: deliberately undeclared",
         "reopen_when": "fixture asks for it"},
        {"lane": "installer", "entry": "tools/sync-ci-only-tool.py",
         "reason": "fixture: CI is deliberately its only caller",
         "reopen_when": "fixture asks for it"},
        {"lane": "installer", "entry": "bin/sync-orphan-prod.sh",
         "reason": "fixture: named by nothing at all on purpose",
         "reopen_when": "fixture asks for it"},
        {"lane": "registry", "entry": "orphaned",
         "reason": "fixture: names an unregistered hook on purpose",
         "reopen_when": "fixture asks for it"},
        {"lane": "registry", "entry": "vanished",
         "reason": "fixture: names a deleted file on purpose",
         "reopen_when": "fixture asks for it"},
    ])
    try:
        rc, payload = run(root)
        check("a fully tombstoned tree exits 0", rc == 0,
              f"rc={rc} findings={payload.get('findings')}")
        check("tombstone makes an entry pass", not payload.get("findings"),
              repr(payload.get("findings")))
    finally:
        shutil.rmtree(root, ignore_errors=True)

    # ------------------------------------------------------------- no marks
    root = fixture(tombstones=[])
    try:
        rc, payload = run(root)
        found = entries(payload)
        check("an untombstoned tree exits 1", rc == 1, f"rc={rc}")
        check("unwired hook is a finding",
              "hooks/orphan-gate.py" in entries(payload, "hook"), repr(found))
        check("registered hooks are not findings",
              "hooks/alpha-gate.py" not in found and "hooks/beta-gate.py" not in found,
              repr(found))
        check("a gate dispatched by name as an argument counts as registered",
              "hooks/dispatched-gate.py" not in found,
              "the run-record-gate dispatcher shape was read as unregistered")
        check("BOTH registration surfaces count",
              "hooks/beta-gate.py" not in found,
              "the project .claude/settings.json registration was ignored")
        check("a helper module nothing imports is a finding",
              "hooks/stray_mod.py" in entries(payload, "hook"), repr(found))
        check("an imported helper module is not a finding",
              "hooks/helper_mod.py" not in found, repr(found))
        check("a qualified hook import reaches its helper module",
              "hooks/qualified_mod.py" not in found, repr(found))
        check("undeclared plist is a finding",
              "ops/launchd/com.carr.undeclared.plist" in entries(payload, "launchd"),
              repr(found))
        check("plist named by a service is not a finding",
              "ops/launchd/com.carr.declared.plist" not in found, repr(found))
        check("a tool whose only callers are CI and a selftest is a finding",
              "tools/sync-ci-only-tool.py" in entries(payload, "installer"), repr(found))
        check("a tool with a non-CI door is not a finding",
              "tools/sync-called-tool.py" not in found, repr(found))
        check("a runbook counts as a door for a human-facing installer",
              "bin/sync-called-prod.sh" not in found, repr(found))
        check("an installer named by nothing at all is a finding",
              "bin/sync-orphan-prod.sh" in entries(payload, "installer"), repr(found))
        check("registry entry naming a deleted file is a finding",
              "vanished" in entries(payload, "registry"), repr(found))
        check("registry entry whose only hook is unregistered is a finding",
              "orphaned" in entries(payload, "registry"), repr(found))
        check("registry entry whose hook is registered is not a finding",
              "alpha" not in entries(payload, "registry"), repr(found))
        check("every finding carries a remedy line",
              all(f.get("remedy") for f in payload.get("findings", [])),
              repr(payload.get("findings")))
    finally:
        shutil.rmtree(root, ignore_errors=True)

    # ------------------------------------------------- tombstone discipline
    root = fixture(tombstones=[
        {"lane": "hook", "entry": "hooks/orphan-gate.py", "reason": "because"},
    ])
    try:
        rc, payload = run(root)
        check("a tombstone missing reopen_when is itself a finding",
              rc == 1 and any(f["lane"] == "tombstone" and
                              "hooks/orphan-gate.py" in f["entry"]
                              for f in payload.get("findings", [])),
              repr(payload.get("findings")))
    finally:
        shutil.rmtree(root, ignore_errors=True)

    root = fixture(tombstones=[
        {"lane": "hook", "entry": "hooks/never-existed.py",
         "reason": "r", "reopen_when": "w"},
    ])
    try:
        rc, payload = run(root)
        check("a tombstone naming something that does not exist is a finding",
              rc == 1 and any(f["lane"] == "tombstone" and
                              "hooks/never-existed.py" in f["entry"]
                              for f in payload.get("findings", [])),
              repr(payload.get("findings")))
    finally:
        shutil.rmtree(root, ignore_errors=True)

    root = fixture(tombstones=[
        {"lane": "hook", "entry": "hooks/alpha-gate.py",
         "reason": "r", "reopen_when": "w"},
    ])
    try:
        rc, payload = run(root)
        check("a tombstone on a REACHABLE entry is a finding",
              rc == 1 and any(f["lane"] == "tombstone" and
                              "hooks/alpha-gate.py" in f["entry"]
                              for f in payload.get("findings", [])),
              "a tombstone must expire when the thing it excuses gets wired")
    finally:
        shutil.rmtree(root, ignore_errors=True)

    # ----------------------------------------------- the whole-tree over-match
    # grok's named risk for this check: a reference graph over the entire
    # repository that counts a comment, a history file or a dead test helper as
    # a caller and therefore never fails. The registries are the concrete case
    # — ops/config/gate-baseline.json names EVERY hook by construction, so if
    # inert records counted, nothing in the hook lane could ever be a finding.
    root = fixture(tombstones=[])
    write(root, "ops/config/gate-baseline.json",
          json.dumps({"hashes": {"orphan-gate.py": "0" * 64}}))
    write(root, "audits/some-audit-2026-01-01.tsv",
          "id\tnote\nx\ttools/sync-ci-only-tool.py is the tool\n")
    write(root, "db/schema.sql", "-- hooks/orphan-gate.py; tools/sync-ci-only-tool.py\n")
    try:
        rc, payload = run(root)
        found = entries(payload)
        check("a hash baseline does not count as a registration",
              "hooks/orphan-gate.py" in found, repr(found))
        check("an audit file does not count as a caller",
              "tools/sync-ci-only-tool.py" in found, repr(found))
    finally:
        shutil.rmtree(root, ignore_errors=True)

    # ------------------------------------------------------- unreadable tree
    rc, _ = run(os.path.join(tempfile.gettempdir(), "reachability-does-not-exist"))
    check("an unreadable tree exits 2, not 0", rc == 2, f"rc={rc}")

    print(f"\n{PASSED} passed, {len(FAILED)} failed")
    if FAILED:
        print("failed: " + ", ".join(FAILED), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
