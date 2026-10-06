#!/usr/bin/env python3
"""Acceptance checks for the local control-plane ledger tick adapter.

This checks the launchd *definition* and wrapper behavior without installing a
LaunchAgent or touching a scheduler.  The ledger owns work state; launchd only
gives the local, versioned adapter opportunities to call ``tick``.
"""
from __future__ import annotations

import json
import os
import plistlib
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
from lib import launchd_calendar  # noqa: E402
WRAPPER = REPO / "bin" / "control-plane-tick.sh"
PLIST = REPO / "ops" / "launchd" / "com.carr.control-plane-tick.plist"
SERVICES = REPO / "ops" / "config" / "services.json"
FAILED: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  ok    {label}")
    else:
        print(f"  FAIL  {label}" + (f"\n        {detail}" if detail else ""))
        FAILED.append(label)


def main() -> int:
    print("control-plane-tick-selftest — local adapter remains narrow and jobs-only\n")
    check("wrapper exists", WRAPPER.is_file(), str(WRAPPER))
    check("LaunchAgent definition exists", PLIST.is_file(), str(PLIST))
    if not WRAPPER.is_file() or not PLIST.is_file():
        return 1

    source = WRAPPER.read_text(encoding="utf-8")
    check("wrapper is noninteractive zsh", source.startswith("#!/bin/zsh"))
    check("wrapper invokes the ledger tick", "tools/control-plane.py\" tick" in source)
    check("wrapper pins the acceptance-ladder auto tier, not a fixed mode",
          "tick --mode auto" in source and "tick --mode shadow" not in source)
    check("wrapper forwards only the explicit jobs credential",
          'CARR_DB_JOBS_URL="$jobs_url"' in source and 'DATABASE_URL="$jobs_url"' not in source)
    check("wrapper starts its child with a scrubbed environment", "env -i" in source)
    check("wrapper never executes credential configuration", "source " not in source and "eval " not in source)
    check("wrapper admits only registered model-neutral provider routes",
          "CARR_CONTROL_PLANE_PROVIDER_ENV" in source and
          all(name in source for name in (
              "CARR_AI_ROUTE_PRIMARY_URL", "CARR_AI_ROUTE_PRIMARY_TOKEN",
              "CARR_AI_ROUTE_SECONDARY_URL", "CARR_AI_ROUTE_SECONDARY_TOKEN")))
    check("wrapper never forwards an ambient provider wildcard",
          "CARR_AI_ROUTE_*" not in source)
    check("wrapper serializes overlap with an atomic directory lock",
          "mkdir \"$LOCK_DIR\"" in source and "trap release_lock EXIT" in source)
    check("wrapper never installs or manipulates a scheduler",
          not any(word in source.lower() for word in ("launchctl", "bootstrap", "bootout", "unload", "load ")))
    check("wrapper never accepts an owner credential",
          "CARR_DB_OWNER_URL" not in source and "CARR_DB_EXPORTER_URL" not in source)

    syntax = subprocess.run(["/bin/zsh", "-n", str(WRAPPER)], text=True,
                            capture_output=True, check=False)
    check("wrapper passes zsh syntax validation", syntax.returncode == 0,
          syntax.stderr.strip())

    # An overlap is not a failure and must return without trying to fetch a
    # credential or touch the database.
    with tempfile.TemporaryDirectory() as td:
        lock = Path(td) / "already-running.lock"
        lock.mkdir()
        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": td,
               "CARR_CONTROL_PLANE_LOCK_DIR": str(lock),
               "CARR_CONTROL_PLANE_DB_ENV": str(Path(td) / "missing-db.env")}
        overlap = subprocess.run([str(WRAPPER)], env=env, text=True,
                                 capture_output=True, check=False)
        check("overlapping tick is skipped cleanly", overlap.returncode == 0,
              f"rc={overlap.returncode} stderr={overlap.stderr.strip()!r}")
        check("overlap reports a skip", "already active" in overlap.stderr,
              overlap.stderr.strip())

    # With no jobs credential, the wrapper refuses before invoking Python.  A
    # terminal's ambient DATABASE_URL must never become an implicit fallback.
    with tempfile.TemporaryDirectory() as td:
        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": td,
               "DATABASE_URL": "postgresql://owner-must-not-be-used",
               "CARR_CONTROL_PLANE_LOCK_DIR": str(Path(td) / "tick.lock"),
               "CARR_CONTROL_PLANE_DB_ENV": str(Path(td) / "missing-db.env")}
        missing = subprocess.run([str(WRAPPER)], env=env, text=True,
                                 capture_output=True, check=False)
        check("missing jobs credential refuses instead of falling back",
              missing.returncode == 78, f"rc={missing.returncode}: {missing.stderr.strip()}")

    # Exercise the real wrapper body against a fake interpreter.  Copying the
    # wrapper into an isolated repo-shaped directory lets its normal interpreter
    # lookup select the fake without adding a production-only test override.
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        wrapper = root / "bin" / "control-plane-tick.sh"
        wrapper.parent.mkdir()
        shutil.copy2(WRAPPER, wrapper)
        (root / "tools").mkdir()
        (root / "tools" / "control-plane.py").touch()
        fake_python = root / ".venv" / "bin" / "python"
        fake_python.parent.mkdir(parents=True)
        capture = root / "child-env.txt"
        args = root / "child-args.txt"
        fake_python.write_text(
            "#!/usr/bin/python3\n"
            "import json, os, sys\n"
            f"open({str(capture)!r}, 'w', encoding='utf-8').write(json.dumps(dict(os.environ)))\n"
            f"open({str(args)!r}, 'w', encoding='utf-8').write(json.dumps(sys.argv[1:]))\n",
            encoding="utf-8",
        )
        fake_python.chmod(0o700)
        db_env = root / "db.env"
        db_env.write_text(
            "CARR_DB_JOBS_URL='postgresql://jobs-file-only'\n"
            "CARR_DB_OWNER_URL=postgresql://owner-file-must-not-pass\n",
            encoding="utf-8",
        )
        db_env.chmod(0o600)
        provider_env = root / "providers.env"
        provider_env.write_text(
            "CARR_AI_ROUTE_PRIMARY_URL=https://primary.file.example\n"
            "CARR_AI_ROUTE_PRIMARY_TOKEN=primary-file-token\n"
            "CARR_AI_ROUTE_SECONDARY_URL=https://secondary.file.example\n"
            "CARR_AI_ROUTE_SECONDARY_TOKEN=secondary-file-token\n"
            "UNRELATED_SECRET=provider-file-secret\n",
            encoding="utf-8",
        )
        provider_env.chmod(0o600)
        env = {
            "PATH": "/usr/bin:/bin",
            "HOME": td,
            "DATABASE_URL": "postgresql://ambient-owner-must-not-pass",
            "CARR_DB_OWNER_URL": "postgresql://ambient-owner-must-not-pass",
            "CARR_AMBIENT_SECRET": "ambient-secret-must-not-pass",
            "CARR_CONTROL_PLANE_LOCK_DIR": str(root / "tick.lock"),
            "CARR_CONTROL_PLANE_DB_ENV": str(db_env),
            "CARR_CONTROL_PLANE_PROVIDER_ENV": str(provider_env),
        }
        executed = subprocess.run([str(wrapper)], env=env, text=True,
                                 capture_output=True, check=False)
        check("fake worker is invoked through the normal wrapper path", executed.returncode == 0,
              f"rc={executed.returncode}: {executed.stderr.strip()}")
        child_env = json.loads(capture.read_text(encoding="utf-8")) if capture.exists() else {}
        expected_child = {
            "CARR_DB_JOBS_URL": "postgresql://jobs-file-only",
            "CARR_AI_ROUTE_PRIMARY_URL": "https://primary.file.example",
            "CARR_AI_ROUTE_PRIMARY_TOKEN": "primary-file-token",
            "CARR_AI_ROUTE_SECONDARY_URL": "https://secondary.file.example",
            "CARR_AI_ROUTE_SECONDARY_TOKEN": "secondary-file-token",
        }
        check("worker receives the jobs credential from the provider-independent file",
              child_env.get("CARR_DB_JOBS_URL") == expected_child["CARR_DB_JOBS_URL"], repr(child_env))
        check("worker receives the exact four provider values from the provider file",
              all(child_env.get(key) == value for key, value in expected_child.items() if key != "CARR_DB_JOBS_URL"),
              repr(child_env))
        child_credentials = {key: value for key, value in child_env.items()
                             if key == "CARR_DB_JOBS_URL" or key.startswith("CARR_AI_ROUTE_")}
        check("worker credential environment contains only jobs DB and four registered routes",
              child_credentials == expected_child, repr(child_credentials))
        check("worker environment excludes owner and ambient secrets",
              not any(key in child_env for key in ("CARR_DB_OWNER_URL", "CARR_AMBIENT_SECRET", "UNRELATED_SECRET")) and
              "DATABASE_URL" not in child_env and
              "ambient-owner-must-not-pass" not in child_env.values() and
              "ambient-secret-must-not-pass" not in child_env.values(), repr(child_env))
        check("worker invocation remains ledger tick in auto ladder mode",
              json.loads(args.read_text(encoding="utf-8")) ==
              [str(root / "tools" / "control-plane.py"), "tick", "--mode", "auto"] if args.exists() else False)

    # Config content is data, never shell.  A command substitution must be
    # rejected without creating its marker, and duplicate/insecure files are
    # configuration failures before an interpreter can run.
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        marker = root / "must-not-exist"
        bad_db = root / "bad-db.env"
        bad_db.write_text(f"CARR_DB_JOBS_URL=$(touch {marker!s})\n", encoding="utf-8")
        bad_db.chmod(0o600)
        env = {"PATH": "/usr/bin:/bin", "HOME": td,
               "CARR_CONTROL_PLANE_LOCK_DIR": str(root / "tick.lock"),
               "CARR_CONTROL_PLANE_DB_ENV": str(bad_db)}
        malformed = subprocess.run([str(WRAPPER)], env=env, text=True,
                                   capture_output=True, check=False)
        check("executable-looking configuration is rejected without execution",
              malformed.returncode == 78 and not marker.exists(),
              f"rc={malformed.returncode}: {malformed.stderr.strip()}")
        duplicate_db = root / "duplicate-db.env"
        duplicate_db.write_text("CARR_DB_JOBS_URL=one\nCARR_DB_JOBS_URL=two\n", encoding="utf-8")
        duplicate_db.chmod(0o600)
        duplicate = subprocess.run([str(WRAPPER)], env={**env,
                                   "CARR_CONTROL_PLANE_LOCK_DIR": str(root / "duplicate.lock"),
                                   "CARR_CONTROL_PLANE_DB_ENV": str(duplicate_db)}, text=True,
                                   capture_output=True, check=False)
        check("duplicate configuration keys are rejected", duplicate.returncode == 78,
              f"rc={duplicate.returncode}: {duplicate.stderr.strip()}")
        insecure_db = root / "insecure-db.env"
        insecure_db.write_text("CARR_DB_JOBS_URL=postgresql://jobs\n", encoding="utf-8")
        insecure_db.chmod(0o644)
        insecure = subprocess.run([str(WRAPPER)], env={**env,
                                  "CARR_CONTROL_PLANE_LOCK_DIR": str(root / "insecure.lock"),
                                  "CARR_CONTROL_PLANE_DB_ENV": str(insecure_db)}, text=True,
                                  capture_output=True, check=False)
        check("group-readable credential files are rejected", insecure.returncode == 78,
              f"rc={insecure.returncode}: {insecure.stderr.strip()}")

    with PLIST.open("rb") as fh:
        plist = plistlib.load(fh)
    args = plist.get("ProgramArguments", [])
    check("plist label is stable", plist.get("Label") == "com.carr.control-plane-tick")
    check("plist invokes only the narrow wrapper",
          args == ["/bin/zsh", "{{REPO}}/bin/control-plane-tick.sh"], repr(args))
    check("plist wakes once per minute; the ledger owns actual recurrence",
          "StartInterval" not in plist and launchd_calendar.cadence_seconds(plist) == 60)
    check("plist has no RunAtLoad side effect", "RunAtLoad" not in plist)
    check("plist does not carry a database credential", "CARR_DB_JOBS_URL" not in str(plist))

    spec = json.loads(SERVICES.read_text(encoding="utf-8"))
    services = {item["key"]: item for item in spec["services"]}
    tick = services.get("control-plane-tick")
    check("tick service is registered", tick is not None)
    if tick:
        check("registered service identifies the adapter",
              tick.get("repo_path") == "bin/control-plane-tick.sh" and
              tick.get("runtime") == "launchd")
        envs = tick.get("environments", [])
        check("registered deploy mechanism is the uninstalled plist",
              len(envs) == 1 and envs[0].get("deploy_mechanism") ==
              "ops/launchd/com.carr.control-plane-tick.plist")
    dependencies = {(item["service"], item["depends_on"]) for item in spec.get("dependencies", [])}
    check("tick depends on the record layer", ("control-plane-tick", "neon-record-layer") in dependencies)

    scheduler_truth = (REPO / "tools" / "scheduler-truth.py").read_text(encoding="utf-8")
    check("scheduler reconciliation recognizes the ledger-native receipt",
          '"control-plane-tick"' in scheduler_truth and "jobs-role ledger adapter" in scheduler_truth)
    check("scheduler reconciliation preserves the uninstalled definition",
          "DEFINITION ONLY" in scheduler_truth and "shadow/canary evidence" in scheduler_truth)

    print()
    if FAILED:
        print(f"FAILED {len(FAILED)} check(s):")
        for label in FAILED:
            print(f"  - {label}")
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
