#!/usr/bin/env python3
"""Selftest for the Tailscale health row and the start-at-login agent (WR-000178).

After the 2026-09-30 macOS reboot Tailscale on the Mac Studio sat "stopped" for
about six hours and SSH to the MacBook was cut off with nothing reporting it.
Three pieces close that, and this suite proves each one hermetically, with a
stub Tailscale binary so no test touches the real daemon:

  1. ops/tailscale_health.py  — classifies `Tailscale status` and renders the
     health row, which must FAIL on "stopped" and print its bound action.
  2. bin/tailscale-up.sh      — runs `Tailscale up` only when status says
     stopped; never when already running, never when logged out (that path
     would wait on a browser nobody is at).
  3. ops/launchd/com.carr.tailscale-up.plist — RunAtLoad agent, primary-only,
     pointing at the script above.
"""
from __future__ import annotations

import importlib.util
import ast
import json
import os
import plistlib
import stat
import subprocess
import sys
import tempfile
import time
import signal
from pathlib import Path
from unittest.mock import Mock
from typing import Any
from contextlib import redirect_stdout
from io import StringIO

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "bin" / "tailscale-up.sh"
PLIST = REPO / "ops" / "launchd" / "com.carr.tailscale-up.plist"

spec = importlib.util.spec_from_file_location("tailscale_health", REPO / "ops" / "tailscale_health.py")
assert spec and spec.loader
th = importlib.util.module_from_spec(spec)
spec.loader.exec_module(th)

cac_spec = importlib.util.spec_from_file_location("config_as_code_ts", REPO / "ops" / "config-as-code.py")
assert cac_spec and cac_spec.loader
cac = importlib.util.module_from_spec(cac_spec)
cac_spec.loader.exec_module(cac)

FAILS: list[str] = []


def check(label: str, cond: bool, detail: object = "") -> None:
    print(f"{'PASS' if cond else 'FAIL'}  {label}" + ("" if cond or not detail else f": {detail}"))
    if not cond:
        FAILS.append(label)


def stub(root: Path, status_out: str, status_rc: int) -> tuple[Path, Path]:
    """A fake Tailscale CLI: `status` prints the given text, every call is logged."""
    log = root / "calls.log"
    fake = root / "Tailscale"
    fake.write_text(
        "#!/bin/sh\n"
        f"echo \"$*\" >> '{log}'\n"
        "if [ \"$1\" = status ]; then\n"
        f"  printf '%s\\n' '{status_out}'\n"
        f"  exit {status_rc}\n"
        "fi\nexit 0\n", encoding="utf-8")
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    return fake, log


# ---- 1. classification and the health row ---------------------------------
health_tree = ast.parse((REPO / "tools/health-check.py").read_text())
# Exercise the import boundary without running the health script's live reads.
loader_function = next((n for n in health_tree.body if isinstance(n, ast.FunctionDef)
                        and n.name == "_tailscale_row"), None)
loader_block = loader_function or next(n for n in health_tree.body
    if isinstance(n, ast.Try) and "tailscale_health" in ast.unparse(n))
for invalid_spec in (None, Mock(loader=None)):
    util = Mock()
    util.spec_from_file_location.return_value = invalid_spec
    ns: dict[str, Any] = {"importlib": Mock(util=util), "os": os, "REPO_ROOT": str(REPO), "rc": 0}
    exec(compile(ast.Module(body=[loader_block], type_ignores=[]), "health-import", "exec"), ns)
    if loader_function:
        try:
            ns["_tailscale_row"]()
        except ImportError:
            pass
    check("unavailable spec/loader is checked before module creation",
          not util.module_from_spec.called)

RUNNING = json.dumps({"BackendState": "Running", "Self": {"TailscaleIPs": ["100.64.0.1"]}, "Peer": {}})

for bad in ("", "arbitrary", "[]", "{}", '{"BackendState":"Starting"}',
            '{"BackendState":"Running"}', '{"BackendState":"Running","Self":{}}'):
    state, summary = th.classify(bad, 0)
    check("empty/malformed/Starting data cannot clear health", state != "running", bad)
    check("unready state renders failed", th.health_row(status=state, summary=summary)[1])

for output, code, expected in ((json.dumps({"BackendState": "Stopped"}), 1, True),
                                (json.dumps({"BackendState": "NeedsLogin"}), 1, True), ("unreadable", 1, True),
                                (RUNNING, 0, False)):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        fake, _ = stub(root, output, code)
        fixture = root / "snapshot.json"
        fixture.write_text('{"errors": []}')
        findings = root / "findings.json"
        result = subprocess.run([sys.executable, str(REPO / "tools/health-check.py"),
            "--section", "tailscale", "--fixture", str(fixture),
            "--findings-json", str(findings)], capture_output=True, text=True,
            env={**os.environ, "TAILSCALE_BIN": str(fake)}, timeout=30)
        rows = json.loads(findings.read_text())["findings"] if findings.exists() else []
        check("ordinary health visits Tailscale", "tailscale" in result.stdout, result.stderr)
        check("ordinary health exit reflects node state", result.returncode == int(expected))
        check("ordinary health preserves finding schema", len(rows) == int(expected) and
            all(r["key"] == "tailscale" and r["subject"] == "local-node" and r["hard_error"]
                and r["count"] == 1 and not r["time_rolling"] for r in rows), rows)
        # Also execute the exact canonical branch with the default 'all'
        # selection, isolating unrelated sections that have live DB readers.
        canonical = next(n for n in health_tree.body if isinstance(n, ast.FunctionDef)
                         and n.name == "_canonical_health")
        branches: list[ast.stmt] = [n for n in canonical.body if isinstance(n, ast.If)
                    and "_tailscale_row" in ast.unparse(n)]
        default_ns: dict[str, Any] = {"CANONICAL_SECTION": "all", "rc": 0,
                                     "_FINDINGS": [], "_tailscale_row": lambda: th.row(str(fake))}
        recorders: list[ast.stmt] = [n for n in health_tree.body if isinstance(n, ast.FunctionDef)
                     and n.name in ("_canonical_finding", "_red")]
        with redirect_stdout(StringIO()):
            exec(compile(ast.Module(body=recorders + branches, type_ignores=[]),
                         "health-default-selection", "exec"), default_ns)
        check("default all-sections health visits the same detector", len(branches) == 1 and
              default_ns["rc"] == int(expected) and len(default_ns["_FINDINGS"]) == int(expected))

s, _ = th.classify(json.dumps({"BackendState": "Stopped"}), 1)
check("stopped status classifies as stopped", s == "stopped", s)
s, _ = th.classify(RUNNING, 0)
check("local Running backend with no peers classifies as running", s == "running", s)
s, _ = th.classify(json.dumps({"BackendState": "NeedsLogin"}), 1)
check("logged-out status classifies as logged_out", s == "logged_out", s)
s, _ = th.classify("failed to connect to local Tailscale service", 1)
check("any other nonzero status classifies as error", s == "error", s)

line, failed = th.health_row(status="stopped", summary=json.dumps({"BackendState": "Stopped"}))
check("stopped row FAILS the health run", failed is True)
check("stopped row is marked red", line.lstrip().startswith("✗✗"), line)
check("stopped row prints its bound action inline", "on breach:" in line, line)
check("bound action names the start-at-login agent", "com.carr.tailscale-up" in line, line)
check("bound action names owner, verify and auto-clear",
      all(k in line for k in ("owner", "verify", "auto-clear")), line)

line, failed = th.health_row(status="running", summary="2 peers")
check("running row passes", failed is False and line.lstrip().startswith("OK"), line)
check("running row still prints its bound action", "on breach:" in line, line)

line, failed = th.health_row(status="logged_out", summary=json.dumps({"BackendState": "NeedsLogin"}))
check("logged-out row FAILS (SSH is just as cut off)", failed is True, line)

line, failed = th.health_row(status="absent", summary="")
check("absent app is a visible skip, not a failure", failed is False and "not installed" in line, line)

with tempfile.TemporaryDirectory() as d:
    fake, _ = stub(Path(d), json.dumps({"BackendState": "Stopped"}), 1)
    line, failed = th.row(binary=str(fake))
    check("row() runs the binary and fails on stopped", failed is True and "stopped" in line, line)
    fake, _ = stub(Path(d), RUNNING, 0)
    line, failed = th.row(binary=str(fake))
    check("row() passes on a running node", failed is False, line)
line, failed = th.row(binary="/nonexistent/Tailscale")
check("row() with no app installed skips", failed is False, line)


# ---- 2. the idempotent start script ---------------------------------------
def run_script(status_out: str, status_rc: int) -> tuple[int, list[str], str]:
    with tempfile.TemporaryDirectory() as d:
        fake, log = stub(Path(d), status_out, status_rc)
        p = subprocess.run(["/bin/zsh", str(SCRIPT)], capture_output=True, text=True,
                           env={**os.environ, "TAILSCALE_BIN": str(fake)}, timeout=30)
        calls = log.read_text().split("\n") if log.exists() else []
        return p.returncode, [c for c in calls if c], p.stdout + p.stderr


rc, calls, out = run_script(json.dumps({"BackendState": "Stopped"}), 1)
check("stopped: script runs `up`", any(c.startswith("up") for c in calls), calls)
check("stopped: bare `up` preserves existing preferences",
      [c for c in calls if c.startswith("up")] == ["up"], calls)
check("stopped: script exits 0 after up", rc == 0, out)

rc, calls, out = run_script(RUNNING, 0)
check("running: script does NOT run `up` (idempotent)", not any(c.startswith("up") for c in calls), calls)
check("running: script exits 0", rc == 0, out)

for bad in ("", "arbitrary", '{"BackendState":"Starting"}'):
    rc, calls, out = run_script(bad, 0)
    check("invalid/Starting status cannot report already running", rc != 0 and
          "already running" not in out and not any(c.startswith("up") for c in calls), out)

rc, calls, out = run_script(json.dumps({"BackendState": "NeedsLogin"}), 1)
check("logged out: script does NOT run `up` (would wait on a browser)",
      not any(c.startswith("up") for c in calls), calls)
check("logged out: script exits nonzero so the log shows it", rc != 0, out)

with tempfile.TemporaryDirectory() as d:
    p = subprocess.run(["/bin/zsh", str(SCRIPT)], capture_output=True, text=True,
                       env={**os.environ, "TAILSCALE_BIN": f"{d}/missing"}, timeout=30)
    check("absent app: script exits 0 without error", p.returncode == 0, p.stdout + p.stderr)

# ---- 3. the LaunchAgent definition ----------------------------------------
def recovery_probe(mode: str, *, startup_delay: float = 0) -> tuple[int, list[list[str]], str, float]:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        log = root / "calls.jsonl"
        fake = root / "Tailscale"
        fake.write_text(f'''#!{sys.executable}
import sys,json,time
time.sleep({startup_delay!r})
with open({str(log)!r}, 'a') as f: f.write(json.dumps(sys.argv[1:])+'\\n')
if sys.argv[1] == 'status':
    if {mode!r} == 'status-stall': time.sleep(60)
    if {mode!r} == 'retry': sys.exit(7)
    if {mode!r} == 'diagnostic':
        print('password=synthetic-sensitive-payload https://private.example.test/value')
        sys.exit(7)
    print(json.dumps({{"BackendState":"Stopped"}}))
    sys.exit(1)
if {mode!r} == 'up-stall': time.sleep(60)
if {mode!r} == 'preferences' and sys.argv[1:] != ['up']:
    print('non-default DNS/login-server/exit-node settings require bare up', file=sys.stderr)
    sys.exit(1)
if {mode!r} == 'auth':
    print('Log in at: https://login.tailscale.com/a/synthetic-auth-value')
    print('password=synthetic-sensitive-payload', file=sys.stderr)
    sys.exit(7)
sys.exit(0)
''')
        fake.chmod(0o700)
        # Only the deliberately stalled command gets the tight watchdog budget.
        # Ordinary fake CLI launches need room for interpreter startup under CI load.
        status_timeout = .15 if mode == "status-stall" else 5
        up_timeout = .15 if mode == "up-stall" else 5
        command = ("import sys; sys.path.insert(0,sys.argv[1]); from tailscale_health import recover; "
                   f"sys.exit(recover(sys.argv[2],status_timeout={status_timeout},"
                   f"up_timeout={up_timeout},retry_delay=.01))")
        before = time.monotonic()
        proc = subprocess.Popen([sys.executable, "-c", command, str(REPO / "ops"), str(fake)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True)
        try:
            out, err = proc.communicate(timeout=3 if mode in ("status-stall", "up-stall") else 15)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            out, err = proc.communicate()
        calls = [json.loads(l) for l in log.read_text().splitlines()] if log.exists() else []
        return proc.returncode, calls, out + err, time.monotonic() - before

for mode in ("status-stall", "up-stall"):
    rc, calls_json, out, elapsed = recovery_probe(mode)
    check(f"{mode}: CLI watchdog preserves timeout failure", rc == 124 and "timed out" in out, out)
    check(f"{mode}: recovery has a wall-clock bound", elapsed < 2, elapsed)
rc, calls_json, out, elapsed = recovery_probe("retry")
check("retry exhaustion stops after six status attempts", len(calls_json) == 6, calls_json)
check("retry exhaustion retains failure status", rc == 7, out)
for mode in ("auth", "diagnostic"):
    rc, calls_json, out, elapsed = recovery_probe(mode, startup_delay=.25)
    check(f"{mode}: failure code survives output redaction", rc == 7, out)
    check(f"{mode}: no CLI URLs or sensitive diagnostics reach log streams",
          all(s not in out for s in ("https://", "password=", "synthetic-sensitive-payload")), out)
    if mode == "auth":
        check("status-to-up sign-in race reports authentication required", "authentication required" in out, out)
rc, calls_json, out, elapsed = recovery_probe("preferences")
check("non-default DNS/login-server/exit-node recovery succeeds", rc == 0, out)
check("recovery preserves settings by invoking only bare up", [c for c in calls_json if c[0] == "up"] == [["up"]], calls_json)

check("plist exists in ops/launchd", PLIST.exists())
if PLIST.exists():
    d = plistlib.loads(PLIST.read_bytes())
    check("plist label matches filename", d.get("Label") == "com.carr.tailscale-up", d.get("Label"))
    check("plist runs at login", d.get("RunAtLoad") is True)
    check("plist runs bin/tailscale-up.sh",
          any("bin/tailscale-up.sh" in a for a in d.get("ProgramArguments", [])),
          d.get("ProgramArguments"))
    check("plist uses the repo portability token",
          any(a.startswith("{{REPO}}") for a in d.get("ProgramArguments", [])))
    check("plist is not KeepAlive (one-shot at login)", not d.get("KeepAlive"))
check("agent is primary-only (the Studio is the hub)", "com.carr.tailscale-up.plist" in cac.PRIMARY_ONLY)
check("agent is not definition-only (the normal install path loads it)",
      "com.carr.tailscale-up.plist" not in cac.launchd_hold.DEFINITION_ONLY)
services = json.loads((REPO / "ops/config/services.json").read_text())["services"]
owners = [s for s in services if any(e.get("deploy_mechanism") ==
    "ops/launchd/com.carr.tailscale-up.plist" for e in s.get("environments", []))]
check("live agent has exactly one service catalog owner", len(owners) == 1, owners)

# Launchd rows remain auditable historical evidence, but no longer require a
# registry successor on each source edit. The DB gate must use its sealed
# service catalog while separately verifying today's source closure.
sys.path.insert(0, str(REPO / "ops"))
db_spec = importlib.util.spec_from_file_location("siep11_gate_ts",
    REPO / "ops/siep11-mutation-registry-local-pg-gate.py")
assert db_spec and db_spec.loader
db_gate = importlib.util.module_from_spec(db_spec)
db_spec.loader.exec_module(db_gate)
sealed_catalog = getattr(db_gate, "sealed_service_launchd", None)
check("DB seal has an independent historical service catalog", callable(sealed_catalog))
if callable(sealed_catalog):
    historical_services = sealed_catalog("scac-mutation-registry.v99")
    check("new live agent does not rewrite historical DB service authority",
          all(row[0] != "tailscale-up" for row in historical_services), historical_services)
    check("historical catalog retains deployed service authority",
          ("rules-refresh", "production", "ops/launchd/com.carr.rules-refresh.plist")
          in historical_services)
    try:
        sealed_catalog("scac-mutation-registry.unreviewed")
    except (ValueError, subprocess.CalledProcessError):
        check("unreviewed historical catalog version refuses", True)
    else:
        check("unreviewed historical catalog version refuses", False)
    historical = [("old", "production", "ops/launchd/old.plist")]
    live = historical + [("new", "production", "ops/launchd/new.plist")]
    for fault in (None, "historical_ref", "live_path"):
        authority = [("launchd-workflow:old", "ops/launchd/old.plist",
                      "ops.service_environment:old:production")]
        actual_services = [(*row, False) for row in live]
        if fault == "historical_ref":
            authority = [(authority[0][0], authority[0][1],
                          "ops.service_environment:forged:production")]
        if fault == "live_path":
            actual_services[-1] = ("new", "production", "ops/launchd/wrong.plist", False)
        cursor = Mock()
        cursor.execute.side_effect = [
            Mock(fetchall=Mock(return_value=authority)),
            Mock(fetchall=Mock(return_value=actual_services)),
            Mock(fetchall=Mock(return_value=[])),
        ]
        try:
            db_gate.validate_launchd_authority_refs(cursor, "scac-mutation-registry.v99",
                                                  [], live, historical)
        except RuntimeError:
            check(f"DB parity refuses {fault}", fault is not None)
        else:
            check("new live service coexists with unchanged historical seal", fault is None)

print(f"tailscale-health-selftest: {'FAIL ' + str(len(FAILS)) if FAILS else 'all passed'}")
sys.exit(1 if FAILS else 0)
