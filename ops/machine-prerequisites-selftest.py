#!/usr/bin/env python3
"""Hermetic checks for the machine prerequisite detector."""
from __future__ import annotations

import os
import sys
import tempfile
import contextlib
import importlib.util
import io
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from lib.machine_prerequisites import (
    Requirement,
    find_executable,
    machine_prerequisites,
    openssl_executable,
    prerequisite_failure_report,
    probe_openssl_ed25519,
    probe_output_root,
)


failed: list[str] = []


def check(label: str, passed: bool) -> None:
    print(("  ok  " if passed else "  FAIL ") + label)
    if not passed:
        failed.append(label)


def fake_runner(returncode: int, version: str = "OpenSSL fixture"):
    class Result:
        def __init__(self, args):
            self.returncode = returncode if "genpkey" in args else 0
            self.stdout = version + "\n" if "version" in args else ""
            self.stderr = "fixture failure" if self.returncode else ""

    return lambda args, **_kwargs: Result(args)


with tempfile.TemporaryDirectory() as raw:
    root = Path(raw)
    binary = root / "psql"
    binary.write_text("fixture", encoding="utf-8")
    binary.chmod(0o755)
    check("an explicit executable candidate wins", find_executable([str(binary)]) == str(binary))
    check("a missing executable is absent", find_executable([str(root / "missing")], which=lambda _name: None) is None)
    # macOS PATH resolves bare "openssl" to Apple's LibreSSL; the Homebrew
    # OpenSSL 3 path must win over whatever PATH says (2026-09-23 Studio).
    homebrew_ssl = root / "openssl"
    homebrew_ssl.write_text("fixture", encoding="utf-8")
    homebrew_ssl.chmod(0o755)
    check("OpenSSL 3 by absolute path outranks PATH's openssl",
          openssl_executable(candidates=(str(homebrew_ssl), "openssl"),
                             which=lambda _name: "/usr/bin/openssl") == str(homebrew_ssl))
    check("PATH's openssl is only the fallback",
          openssl_executable(candidates=(str(root / "missing"), "openssl"),
                             which=lambda _name: "/usr/bin/openssl") == "/usr/bin/openssl")

    good_ssl = probe_openssl_ed25519(
        candidates=["openssl-fixture"], which=lambda name: f"/fixture/{name}",
        runner=fake_runner(0), temp_root=root,
    )
    bad_ssl = probe_openssl_ed25519(
        candidates=["openssl-fixture"], which=lambda name: f"/fixture/{name}",
        runner=fake_runner(1, "LibreSSL fixture"), temp_root=root,
    )
    check("Ed25519 capability is proven by minting a key", good_ssl.ok)
    check("a present but incapable OpenSSL is refused by behavior", not bad_ssl.ok and "LibreSSL fixture" in bad_ssl.detail)

    output = root / "repo" / "out"
    output.mkdir(parents=True)
    check("a real output directory passes", probe_output_root(root / "repo").ok)
    output.rmdir()
    target = root / "shared-out"
    target.mkdir()
    output.symlink_to(target, target_is_directory=True)
    check("a symlinked output root is named as unavailable", not probe_output_root(root / "repo").ok)

    results = machine_prerequisites(
        root / "repo",
        psql_candidates=[str(root / "missing-psql")],
        openssl_candidates=["openssl-fixture"],
        which=lambda _name: None,
    )
    report = prerequisite_failure_report(results)
    check("the aggregate names every missing prerequisite", all(
        name in report for name in ("PostgreSQL client", "OpenSSL Ed25519", "output directory")
    ))

ready = [
    Requirement("postgres-client", "PostgreSQL client", True, "/fixture/psql", ""),
    Requirement("openssl-ed25519", "OpenSSL Ed25519", True, "/fixture/openssl", ""),
    Requirement("output-root", "output directory", True, "/fixture/out", ""),
]
check("a ready machine emits no failure report", prerequisite_failure_report(ready) == "")

config_path = ROOT / "ops" / "config-as-code.py"
spec = importlib.util.spec_from_file_location("machine_prereq_config_integration", config_path)
assert spec and spec.loader
config = importlib.util.module_from_spec(spec)
spec.loader.exec_module(config)
setattr(config, "codex_configuration_state", lambda: "absent")
setattr(config, "pairs", lambda holds=None: [])
setattr(config, "hook_scripts_untracked", lambda: [])
setattr(config, "secondary_scheduled_task_violations", lambda: [])
setattr(config, "PREREQUISITE_CHECK", lambda _repo: [
    Requirement("postgres-client", "PostgreSQL client", False, "missing fixture", "install fixture"),
])
with contextlib.redirect_stdout(io.StringIO()) as captured:
    config_rc = config.cmd_check()
config_output = captured.getvalue()
check("config check refuses a clean render on an unprovisioned machine",
      config_rc == 1 and config_output.startswith("config-as-code: PREREQUISITES MISSING"))

print(f"machine-prerequisites-selftest: {len(failed)} failed")
raise SystemExit(bool(failed))
