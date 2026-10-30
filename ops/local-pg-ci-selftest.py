#!/usr/bin/env python3
"""Hermetic contract tests for the one-command disposable local PG lane."""
from __future__ import annotations

import importlib.util
import io
import os
import sys
import tempfile
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[1]
MODULE_PATH = REPO / "ops/local-pg-ci.py"


def load_module():
    spec = importlib.util.spec_from_file_location("local_pg_ci", MODULE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load local PG CI module")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


mod = load_module()
passed = 0
failed: list[str] = []


import pathlib, sys  # noqa: E401
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))  # repo root, for lib/
from lib.gate_check import Check  # noqa: E402

check = Check(
    ok=None,
    fail=None,
    detail=None,
    failures=failed,
    counters={"passed": "pass"},
    ns=globals(),
)


check("valid loopback port accepted", mod.validate_port(55432) == 55432)
for invalid in (0, 80, 65536):
    try:
        mod.validate_port(invalid)
    except mod.LocalPGRefusal:
        pass
    else:
        failed.append(f"invalid port {invalid} refused")

source = {
    "PATH": "/usr/bin:/bin",
    "HOME": "/tmp/home",
    "TMPDIR": "/tmp",
    "DATABASE_URL": "postgres://owner:secret@remote/db",  # ci-secret-scan: allow - synthetic non-routable fixture
    "CARR_DB_JOBS_URL": "postgres://jobs:secret@remote/db",  # ci-secret-scan: allow - synthetic non-routable fixture
    "CARR_AI_ROUTE_PRIMARY_TOKEN": "secret",
    "NEON_API_KEY": "secret",
    "PGPASSWORD": "secret",
    "GH_TOKEN": "secret",
    "OPENAI_API_KEY": "secret",
    "ANTHROPIC_API_KEY": "secret",
    "SAFE_LOCAL_FLAG": "must-not-cross",
}
clean = mod.scrub_cloud_environment(source)
check("required local environment survives", clean["PATH"] == source["PATH"] and clean["HOME"] == source["HOME"])
check("unregistered ambient values are scrubbed", "SAFE_LOCAL_FLAG" not in clean)
check("owner DSN is scrubbed", "DATABASE_URL" not in clean)
check("routine DB DSNs are scrubbed", "CARR_DB_JOBS_URL" not in clean)
check("provider token is scrubbed", "CARR_AI_ROUTE_PRIMARY_TOKEN" not in clean)
check("Neon credential is scrubbed", "NEON_API_KEY" not in clean)
check("libpq credential is scrubbed", "PGPASSWORD" not in clean)
check("GitHub credential is scrubbed", "GH_TOKEN" not in clean)
check("model credentials are scrubbed", "OPENAI_API_KEY" not in clean and "ANTHROPIC_API_KEY" not in clean)

with patch.dict(os.environ, {"GITHUB_ACTIONS": "true"}, clear=True):
    try:
        mod.refuse_hosted_execution()
    except mod.LocalPGRefusal:
        check("hosted runner refuses", True)
    else:
        check("hosted runner refuses", False)

# The declared hosted lane (.github/workflows/ci.yml) is the one
# caller allowed through, and every clause is load-bearing. Dropping any one of
# them would re-open the blanket hole the refusal exists to keep shut, so each
# is tested by removing exactly that clause and requiring a refusal.
DECLARED_HOSTED = {
    "CARR_LOCAL_PG_ALLOW_HOSTED": "1",
    "GITHUB_ACTIONS": "true",
    "GITHUB_REPOSITORY": "DoctorCRE/carr-system",
    "GITHUB_RUN_ID": "1234567890",
}
with patch.dict(os.environ, DECLARED_HOSTED, clear=True):
    try:
        mod.refuse_hosted_execution()
    except mod.LocalPGRefusal:
        check("the declared hosted lane is allowed", False)
    else:
        check("the declared hosted lane is allowed", True)

for dropped in sorted(DECLARED_HOSTED):
    if dropped == "GITHUB_ACTIONS":
        # Without the hosted marker this is simply not a hosted runner, and the
        # refusal is not meant to fire at all — a developer shell is the norm.
        continue
    partial = {k: v for k, v in DECLARED_HOSTED.items() if k != dropped}
    with patch.dict(os.environ, partial, clear=True):
        try:
            mod.refuse_hosted_execution()
        except mod.LocalPGRefusal:
            check(f"hosted refuses without {dropped}", True)
        else:
            check(f"hosted refuses without {dropped}", False)

# A fork running the same workflow must not inherit the opt-in.
with patch.dict(os.environ, {**DECLARED_HOSTED, "GITHUB_REPOSITORY": "someone/fork"}, clear=True):
    try:
        mod.refuse_hosted_execution()
    except mod.LocalPGRefusal:
        check("a fork is refused", True)
    else:
        check("a fork is refused", False)

# The remaining cases use a fully mocked local runner. Clear the ambient hosted
# marker after testing the refusal so CI and a developer shell exercise the
# exact same hermetic fixtures below.
os.environ.pop("GITHUB_ACTIONS", None)

events: list[tuple[str, ...]] = []
child_envs: list[dict[str, str]] = []


class FakeRunner:
    def run(self, command, *, env=None, cwd=None, capture=False):
        del cwd, capture
        events.append(tuple(str(part) for part in command))
        child_envs.append(dict(env or {}))
        return mod.CommandResult(0, "", "")


fake_bins = mod.PostgresBinaries(
    initdb=Path("/fake/initdb"), pg_ctl=Path("/fake/pg_ctl"),
    createdb=Path("/fake/createdb"), psql=Path("/fake/psql"),
)
fake_owner = mod.DisposablePostgres("carr-local-pg-ci.selftest-", fake_bins.pg_ctl)
fake_root = fake_owner.root
with (
    patch.object(mod, "find_postgres_binaries", return_value=fake_bins),
    patch.object(mod, "port_is_available", return_value=True),
    patch.object(tempfile, "mkdtemp", return_value=str(fake_root)),
    patch.object(Path, "mkdir"),
    patch.object(mod.shutil, "rmtree") as remove,
):
    result = mod.run_local_ci(
        repo=REPO, ci_class="migration", port=55432, runner=FakeRunner()
    )
check("discovered binaries reach the scrubbed Linux child PATH",
      all(env.get("PATH", "").split(os.pathsep)[0] == "/fake" for env in child_envs))
check("successful lane returns zero", result == 0)
check("initdb is first PostgreSQL operation", events[0][0] == "/fake/initdb")
check("server binds loopback and private socket",
      f"-h 127.0.0.1 -p 55432 -k {fake_root / 'socket'}" in events[1])
check("server output is detached from runner pipes", "-l" in events[1] and "postgres.log" in events[1][events[1].index("-l") + 1])
check("database is created locally", events[2][0] == "/fake/createdb")
check("fixture owner role is created", events[3][0] == "/fake/psql" and "neondb_owner" in events[3][-1])
check("migration class runs through canonical CI", events[4][-2:] == ("--only", "migration"))
check("migration gates discover the selected PostgreSQL binaries in the scrubbed child",
      child_envs[4]["PATH"].split(os.pathsep)[0] == "/fake")
continuity_event = next(
    index for index, event in enumerate(events)
    if event[-1].endswith("mcp-server/test/codex-continuity.test.mjs")
)
check(
    "Codex continuity real-PostgreSQL proof runs after canonical CI",
    continuity_event == 5,
)
check(
    "Codex continuity proof receives the same disposable DSN and raw-PG driver",
    child_envs[continuity_event].get("CARR_CONTINUITY_EPHEMERAL_DATABASE_URL")
    == "postgres://carr_ci@127.0.0.1:55432/carr_ci"
    and child_envs[continuity_event].get("CARR_CONTINUITY_DATABASE_DRIVER_MODULE") == "pg",
)
check("Leads PostgreSQL regressions run after continuity", events[6][-1].endswith("mcp-server/test/lead-workspace-pg.test.mjs"))
check("Leads PostgreSQL regressions receive the disposable DSN", child_envs[6].get("LEAD_WORKSPACE_TEST_DATABASE_URL") == "postgres://carr_ci@127.0.0.1:55432/carr_ci")
check(
    "retired rule-authority acceptance is not dispatched",
    not any(event[-1].endswith("atomic-rule-approval-local-pg-acceptance.py") for event in events),
)
check(
    "atomic rule-delivery cutover runs after leads acceptance",
    events[7][-1].endswith("ops/rule-delivery-local-pg-acceptance.py"),
)
snapshot_event = next(
    index for index, event in enumerate(events)
    if event[0].endswith("bin/schema-snapshot.sh")
)
check(
    "disposable schema snapshot proof cannot rewrite the tracked artifact",
    "--verify-only" in events[snapshot_event],
)
check(
    "disposable snapshot uses clients from the server toolchain",
    child_envs[snapshot_event].get("PATH", "").split(os.pathsep)[0] == "/fake",
)
check(
    "rule-delivery acceptance receives only the local disposable DSN",
    child_envs[7].get("CARR_LOCAL_PG_DSN")
    == "postgres://carr_ci@127.0.0.1:55432/carr_ci"
    and "CARR_CI_DATABASE_URL" not in child_envs[7],
)
check("server always stops", events[-1][0] == "/fake/pg_ctl" and events[-1][-1] == "stop")
check("temporary cluster is always removed", remove.call_count == 1)
check("ordinary children receive no ambient secrets", all(
    not ({"NEON_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GH_TOKEN"} & set(env))
    and "DATABASE_URL" not in env
    for event, env in zip(events, child_envs)
))

core_events_start = len(events)
with (
    patch.object(mod, "find_postgres_binaries", return_value=fake_bins),
    patch.object(mod, "port_is_available", return_value=True),
):
    result = mod.run_local_ci(repo=REPO, ci_class="migration", port=55432,
                              runner=FakeRunner(), migration_part="core")
core_events = events[core_events_start:]
check("core partition remains strict and runs the full acceptance manifest",
      result == 0 and any(event[-3:] == ("--strict", "--migration-part", "core") for event in core_events)
      and any(event[-1].endswith("mcp-server/test/codex-continuity.test.mjs") for event in core_events)
      and any("--verify-only" in event for event in core_events))
for part in ("proofs", "gates", "acceptance"):
    start = len(events)
    try:
        with patch.object(mod, "find_postgres_binaries", return_value=fake_bins), \
                patch.object(mod, "port_is_available", return_value=True):
            result = mod.run_local_ci(repo=REPO, ci_class="migration", port=55432,
                                      runner=FakeRunner(), migration_part=part)
    except mod.LocalPGRefusal:
        result = 78
    calls = events[start:]
    check(f"{part} partition runs strictly", result == 0 and any(
        event[-3:] == ("--strict", "--migration-part", part) for event in calls))
    acceptance = any(event[-1].endswith("mcp-server/test/codex-continuity.test.mjs") for event in calls)
    check(f"{part} partition owns the intended acceptance programs", acceptance == (part == "acceptance"))

for invalid_part in ("fixtures", "unknown"):
    try:
        mod.run_local_ci(repo=REPO, ci_class="migration", port=55432, runner=FakeRunner(), migration_part=invalid_part)
    except mod.LocalPGRefusal:
        check(f"local lane refuses migration part {invalid_part}", True)
    else:
        check(f"local lane refuses migration part {invalid_part}", False)

events.clear()
with (
    patch.object(mod, "find_postgres_binaries", return_value=fake_bins),
    patch.object(mod, "port_is_available", return_value=True),
    patch.object(tempfile, "mkdtemp", return_value=str(fake_root)),
    patch.object(Path, "mkdir"),
    patch.object(mod.shutil, "rmtree"),
):
    result = mod.run_local_ci(repo=REPO, ci_class="strict", port=55432, runner=FakeRunner())
check("strict lane uses canonical strict CI", result == 0 and events[4][-1] == "--strict")
continuity_event = next(
    index for index, event in enumerate(events)
    if event[-1].endswith("mcp-server/test/codex-continuity.test.mjs")
)
check("strict lane runs the Codex continuity real-PostgreSQL proof", continuity_event == 5)
check(
    "retired rule-authority acceptance is not dispatched",
    not any(event[-1].endswith("atomic-rule-approval-local-pg-acceptance.py") for event in events),
)
check(
    "strict lane also proves atomic rule-delivery cutover",
    events[7][-1].endswith("ops/rule-delivery-local-pg-acceptance.py"),
)

missing_gate = mod.run_required_local_gate(
    FakeRunner(),
    Path("/fake/python"),
    Path("/definitely/missing/assurance-gate.py"),
    env={},
    cwd=REPO,
)
check(
    "a missing assurance gate is a causal refusal",
    missing_gate.returncode == 78
    and "assurance-gate.py" in missing_gate.stderr
    and "unavailable" in missing_gate.stderr,
)


class FailingRenewalRunner(FakeRunner):
    def run(self, command, *, env=None, cwd=None, capture=False):
        result = super().run(command, env=env, cwd=cwd, capture=capture)
        if str(command[-1]).endswith("ops/renewal-signed-ingress-local-pg-acceptance.py"):
            return mod.CommandResult(23, "", "RENEWAL_STAGE_MISMATCH: exact causal fixture")
        return result


events.clear()
with (
    patch.object(mod, "find_postgres_binaries", return_value=fake_bins),
    patch.object(mod, "port_is_available", return_value=True),
    patch.object(tempfile, "mkdtemp", return_value=str(fake_root)),
    patch.object(Path, "mkdir"),
    patch.object(mod.shutil, "rmtree"),
):
    renewal_stderr = io.StringIO()
    with redirect_stderr(renewal_stderr):
        renewal_failure = mod.run_local_ci(
            repo=REPO, ci_class="migration", port=55432, runner=FailingRenewalRunner()
        )
check("renewal gate failure code is preserved", renewal_failure == 23)
check(
    "renewal gate exact child stderr is surfaced",
    "RENEWAL_STAGE_MISMATCH: exact causal fixture" in renewal_stderr.getvalue(),
)
check(
    "later local gates do not run after renewal failure",
    not any(event[-1].endswith("ops/incident-recovery-local-pg-acceptance.py") for event in events),
)


class FailingRunner(FakeRunner):
    def run(self, command, *, env=None, cwd=None, capture=False):
        result = super().run(command, env=env, cwd=cwd, capture=capture)
        if str(command[0]).endswith("ops/ci.sh"):
            return mod.CommandResult(7, "", "local CI failed")
        return result


class FailingStartRunner(FakeRunner):
    def run(self, command, *, env=None, cwd=None, capture=False):
        result = super().run(command, env=env, cwd=cwd, capture=capture)
        if str(command[0]).endswith("pg_ctl") and command[-1] == "start":
            return mod.CommandResult(1, "", "start failed after spawn")
        return result


class FailingContinuityRunner(FakeRunner):
    def run(self, command, *, env=None, cwd=None, capture=False):
        result = super().run(command, env=env, cwd=cwd, capture=capture)
        if str(command[-1]).endswith("mcp-server/test/codex-continuity.test.mjs"):
            return mod.CommandResult(9, "", "continuity integration failed")
        return result


events.clear()
with (
    patch.object(mod, "find_postgres_binaries", return_value=fake_bins),
    patch.object(mod, "port_is_available", return_value=True),
    patch.object(tempfile, "mkdtemp", return_value=str(fake_root)),
    patch.object(Path, "mkdir"),
    patch.object(mod.shutil, "rmtree") as remove_failure,
):
    result = mod.run_local_ci(repo=REPO, ci_class="migration", port=55432,
                              runner=FailingRunner())
check("CI failure is preserved", result == 7)
check(
    "rule-delivery acceptance never runs after CI failure",
    not any(event[-1].endswith("rule-delivery-local-pg-acceptance.py") for event in events),
)
check("failure still stops server", events[-1][0] == "/fake/pg_ctl" and events[-1][-1] == "stop")
check("failure still removes cluster", remove_failure.call_count == 1)

events.clear()
with (
    patch.object(mod, "find_postgres_binaries", return_value=fake_bins),
    patch.object(mod, "port_is_available", return_value=True),
    patch.object(tempfile, "mkdtemp", return_value=str(fake_root)),
    patch.object(Path, "mkdir"),
    patch.object(mod.shutil, "rmtree") as remove_continuity_failure,
):
    continuity_stderr = io.StringIO()
    with redirect_stderr(continuity_stderr):
        result = mod.run_local_ci(
            repo=REPO, ci_class="migration", port=55432,
            runner=FailingContinuityRunner()
        )
check("continuity integration failure is preserved", result == 9)
check(
    "rule-delivery acceptance never runs after continuity integration failure",
    not any(event[-1].endswith("rule-delivery-local-pg-acceptance.py") for event in events),
)
check(
    "continuity integration failure is surfaced",
    "continuity integration failed" in continuity_stderr.getvalue(),
)
check("continuity failure still stops server", events[-1][0] == "/fake/pg_ctl" and events[-1][-1] == "stop")
check("continuity failure still removes cluster", remove_continuity_failure.call_count == 1)

events.clear()
with (
    patch.object(mod, "find_postgres_binaries", return_value=fake_bins),
    patch.object(mod, "port_is_available", return_value=True),
    patch.object(tempfile, "mkdtemp", return_value=str(fake_root)),
    patch.object(Path, "mkdir"),
    patch.object(mod.shutil, "rmtree") as remove_start_failure,
):
    result = mod.run_local_ci(repo=REPO, ci_class="migration", port=55432,
                              runner=FailingStartRunner())
check("start failure is preserved", result == 1)
check("start failure still attempts stop", events[-1][0] == "/fake/pg_ctl" and events[-1][-1] == "stop")
check("start failure still removes cluster", remove_start_failure.call_count == 1)

run_sh = (REPO / "run.sh").read_text(encoding="utf-8")
check("one-command route is registered", "local-db-ci)" in run_sh and "ops/local-pg-ci.py" in run_sh)

with tempfile.TemporaryDirectory() as directory:
    selected, fallback = Path(directory) / "selected", Path(directory) / "fallback"
    for bins in (selected, fallback):
        bins.mkdir()
        for name in ("initdb", "pg_ctl", "createdb", "psql", "postgres"):
            binary = bins / name
            binary.write_text("#!/bin/sh\nexit 0\n")
            binary.chmod(0o755)
    with patch.dict(os.environ, {"CARR_TEST_POSTGRES_BIN": str(selected),
                                 "CARR_LOCAL_PG_BIN_DIR": str(fallback)}):
        check("test PostgreSQL override precedes the local fallback",
              mod.find_postgres_binaries().initdb.parent == selected)
    with patch.dict(os.environ, {"CARR_TEST_POSTGRES_BIN": str(selected),
                                 "CARR_LOCAL_PG_BIN_DIR": "unused-relative-fallback"}):
        check("valid test override does not validate an unused fallback",
              mod.find_postgres_binaries().initdb.parent == selected)

# Exercise the real local CI process and its real disposable PostgreSQL server.
import shutil
import socket
import subprocess
import time

with tempfile.TemporaryDirectory(prefix="local-pg-kill-test-") as directory:
    repo = Path(directory)
    (repo / "ops").mkdir()
    ci = repo / "ops/ci.sh"
    ci.write_text(f"#!/bin/sh\necho $$ > {repo / 'ready'}\nexec sleep 60\n")
    ci.chmod(0o755)
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    script = """
import importlib.util, sys
from pathlib import Path
spec = importlib.util.spec_from_file_location('local_pg_ci_child', sys.argv[1])
m = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = m
spec.loader.exec_module(m)
original = m.DisposablePostgres
class Fixture(original):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        print(self.root, flush=True)
m.DisposablePostgres = Fixture
m.run_local_ci(repo=Path(sys.argv[2]), ci_class='migration', port=int(sys.argv[3]))
"""
    child = subprocess.Popen([sys.executable, '-u', '-c', script, str(MODULE_PATH), str(repo), str(port)],
                             stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    assert child.stdout is not None
    stdout = child.stdout
    root = None
    pid = None
    try:
        root = Path(stdout.readline().strip())
        deadline = time.monotonic() + 20
        pidfile = root / 'data/postmaster.pid'
        while not (repo / 'ready').exists() and child.poll() is None and time.monotonic() < deadline:
            time.sleep(0.05)
        if (repo / 'ready').exists() and pidfile.exists():
            pid = int(pidfile.read_text().splitlines()[0])
        child.kill()
        child.wait(timeout=5)
        deadline = time.monotonic() + 15
        alive = pid is not None
        while pid is not None and alive and time.monotonic() < deadline:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                alive = False
            if alive:
                time.sleep(0.1)
        check("SIGKILL of local CI stops its PostgreSQL process", pid is not None and not alive)
    finally:
        if child.poll() is None:
            child.kill()
            child.wait()
        if (repo / 'ready').exists():
            try:
                os.kill(int((repo / 'ready').read_text()), 9)
            except ProcessLookupError:
                pass
        if root and (root / 'data/postmaster.pid').exists():
            bins = mod.find_postgres_binaries()
            subprocess.run([str(bins.pg_ctl), '-D', str(root / 'data'), '-m', 'immediate', '-w', 'stop'], capture_output=True)
        if root and root.exists():
            shutil.rmtree(root, ignore_errors=True)
        stdout.close()

print(f"local PG CI selftest — {passed}/{passed + len(failed)} passed")
if failed:
    print("FAILED: " + "; ".join(failed))
    raise SystemExit(1)

fake_owner.close()
