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


def check(label: str, condition: bool) -> None:
    global passed
    if condition:
        passed += 1
    else:
        failed.append(label)


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
    "CARR_JEV_OFFLINE_REPLAY": "1",
}
clean = mod.scrub_cloud_environment(source)
check("required local environment survives", clean["PATH"] == source["PATH"] and clean["HOME"] == source["HOME"])
check("explicit offline CI mode survives credential scrubbing",
      clean.get("CARR_JEV_OFFLINE_REPLAY") == "1")
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

# The declared hosted lane (.github/workflows/db-acceptance.yml) is the one
# caller allowed through, and every clause is load-bearing. Dropping any one of
# them would re-open the blanket hole the refusal exists to keep shut, so each
# is tested by removing exactly that clause and requiring a refusal.
DECLARED_HOSTED = {
    "CARR_LOCAL_PG_ALLOW_HOSTED": "1",
    "GITHUB_ACTIONS": "true",
    "GITHUB_REPOSITORY": "jbookout/carr-system",
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
        if str(command[-1]) == "--fingerprint-only":
            return mod.CommandResult(0, "{}", "")
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
    patch.object(mod.shutil, "rmtree") as remove,
):
    result = mod.run_local_ci(
        repo=REPO, ci_class="migration", port=55432, runner=FakeRunner()
    )
check("discovered binaries reach the scrubbed Linux child PATH",
      all(env.get("PATH", "").split(os.pathsep)[0] == "/fake" for env in child_envs))
check("successful lane returns zero", result == 0)
check("initdb is first PostgreSQL operation", events[0][0] == "/fake/initdb")
check("server binds loopback", "-h 127.0.0.1 -p 55432" in events[1])
check("server output is detached from runner pipes", "-l" in events[1] and "postgres.log" in events[1][events[1].index("-l") + 1])
check("database is created locally", events[2][0] == "/fake/createdb")
check("fixture owner role is created", events[3][0] == "/fake/psql" and "neondb_owner" in events[3][-1])
check("pre-0450 fingerprint database is isolated", events[4][0] == "/fake/createdb" and events[4][-1] == "carr_ci_a2_pre")
check("pre-0450 schema is loaded canonically", events[5][0] == "/fake/psql" and events[5][-1].endswith("db/schema.sql"))
check("pre-0450 migrations stop at the exact predecessor", events[6][-2:] == ("--through", "0431_completion_register_schema.sql"))
check(
    "reviewed receipt seam prepares the pre-0450 fingerprint",
    events[7][0] == "/fake/psql"
    and events[7][-2] == "-f"
    and events[7][-1].endswith("ops/f03-receipt-validator.candidate.sql"),
)
check("true pre-0450 fingerprint is captured", events[8][-1] == "--fingerprint-only")
check("migration class runs through canonical CI", events[9][-2:] == ("--only", "migration"))
check("migration gates discover the selected PostgreSQL binaries in the scrubbed child",
      child_envs[9]["PATH"].split(os.pathsep)[0] == "/fake")
check(
    "F03 PostgreSQL acceptance runs immediately after canonical CI",
    events[10][-1].endswith("tools/test-f03-production-migration.py"),
)
check(
    "F03 acceptance receives only the disposable DSN and exact psql",
    child_envs[10].get("CARR_CI_DATABASE_URL")
    == "postgres://carr_ci@127.0.0.1:55432/carr_ci"
    and child_envs[10].get("CARR_F03_PSQL") == "/fake/psql",
)
continuity_event = next(
    index for index, event in enumerate(events)
    if event[-1].endswith("mcp-server/test/codex-continuity.test.mjs")
)
check(
    "Codex continuity real-PostgreSQL proof runs after canonical CI",
    continuity_event == 11,
)
check(
    "Codex continuity proof receives the same disposable DSN and raw-PG driver",
    child_envs[continuity_event].get("CARR_CONTINUITY_EPHEMERAL_DATABASE_URL")
    == "postgres://carr_ci@127.0.0.1:55432/carr_ci"
    and child_envs[continuity_event].get("CARR_CONTINUITY_DATABASE_DRIVER_MODULE") == "pg",
)
check("Leads PostgreSQL regressions run after continuity", events[12][-1].endswith("mcp-server/test/lead-workspace-pg.test.mjs"))
check("Leads PostgreSQL regressions receive the disposable DSN", child_envs[12].get("LEAD_WORKSPACE_TEST_DATABASE_URL") == "postgres://carr_ci@127.0.0.1:55432/carr_ci")
check(
    "atomic Joe lifecycle runs after canonical CI",
    events[13][-1].endswith("ops/atomic-rule-approval-local-pg-acceptance.py"),
)
check(
    "atomic rule-delivery cutover runs after authority acceptance",
    events[14][-1].endswith("ops/rule-delivery-local-pg-acceptance.py"),
)
check(
    "scoped engineering claim runs after the authority acceptances",
    events[15][-1].endswith("ops/engineering-claim-local-pg-gate.py"),
)
check(
    "Engineering terminalization race runs after the scoped claim",
    events[16][-1].endswith("ops/engineering-envelope-race-local-pg-gate.py"),
)
check(
    "canonical ownership lease runs after the Engineering race proof",
    events[17][-1].endswith("ops/canonical-ownership-lease-local-pg-gate.py"),
)
check(
    "assurance persistence runs immediately after canonical ownership",
    events[18][-1].endswith("ops/assurance-evidence-acceptance-local-pg-gate.py"),
)
check(
    "source-merge reader projection runs immediately after assurance persistence",
    events[19][-1].endswith("ops/source-merge-authority-local-pg-gate.py"),
)
completion_event = next(
    index for index, event in enumerate(events)
    if event[-1].endswith("ops/completion-register-schema-local-pg-gate.py")
)
snapshot_event = next(
    index for index, event in enumerate(events)
    if event[0].endswith("bin/schema-snapshot.sh")
)
check(
    "completion register fixture runs before schema snapshot",
    completion_event < snapshot_event,
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
    "authority acceptance receives only the local disposable DSN",
    child_envs[13].get("CARR_LOCAL_PG_DSN")
    == "postgres://carr_ci@127.0.0.1:55432/carr_ci"
    and "CARR_CI_DATABASE_URL" not in child_envs[13],
)
check(
    "fingerprint reads only the isolated pre-0450 database",
    child_envs[8].get("CARR_LOCAL_PG_DSN")
    == "postgres://carr_ci@127.0.0.1:55432/carr_ci_a2_pre"
    and "DATABASE_URL" not in child_envs[8],
)
check(
    "post-CI gates receive the exact pre-0450 fingerprint",
    child_envs[13].get("CARR_OWNERSHIP_PRE_0450_FINGERPRINT") == "{}"
    and child_envs[17].get("CARR_OWNERSHIP_PRE_0450_FINGERPRINT") == "{}",
)
check("server always stops", events[-1][0] == "/fake/pg_ctl" and events[-1][-1] == "stop")
check("temporary cluster is always removed", remove.call_count == 1)
check("ordinary children receive no ambient secrets", all(
    not ({"NEON_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GH_TOKEN"} & set(env))
    and (
        "DATABASE_URL" not in env
        or (any(part.endswith("tools/migrate.py") for part in event)
            and env["DATABASE_URL"] == "postgres://carr_ci@127.0.0.1:55432/carr_ci_a2_pre")
    )
    for event, env in zip(events, child_envs)
))

events.clear()
with (
    patch.object(mod, "find_postgres_binaries", return_value=fake_bins),
    patch.object(mod, "port_is_available", return_value=True),
    patch.object(tempfile, "mkdtemp", return_value=str(fake_root)),
    patch.object(mod.shutil, "rmtree"),
):
    result = mod.run_local_ci(repo=REPO, ci_class="strict", port=55432, runner=FakeRunner())
check("strict lane uses canonical strict CI", result == 0 and events[9][-1] == "--strict")
continuity_event = next(
    index for index, event in enumerate(events)
    if event[-1].endswith("mcp-server/test/codex-continuity.test.mjs")
)
check("strict lane runs the F03 PostgreSQL acceptance", events[10][-1].endswith("tools/test-f03-production-migration.py"))
check("strict lane runs the Codex continuity real-PostgreSQL proof", continuity_event == 11)
check(
    "strict lane also proves atomic Joe lifecycle",
    events[13][-1].endswith("ops/atomic-rule-approval-local-pg-acceptance.py"),
)
check(
    "strict lane also proves atomic rule-delivery cutover",
    events[14][-1].endswith("ops/rule-delivery-local-pg-acceptance.py"),
)
check(
    "strict lane also proves the scoped engineering claim",
    events[15][-1].endswith("ops/engineering-claim-local-pg-gate.py"),
)
check(
    "strict lane also proves the Engineering terminalization race",
    events[16][-1].endswith("ops/engineering-envelope-race-local-pg-gate.py"),
)
check(
    "strict lane also proves canonical ownership leases",
    events[17][-1].endswith("ops/canonical-ownership-lease-local-pg-gate.py"),
)
check(
    "strict lane also proves assurance persistence",
    events[18][-1].endswith("ops/assurance-evidence-acceptance-local-pg-gate.py"),
)
check(
    "strict lane also proves source-merge reader projection",
    events[19][-1].endswith("ops/source-merge-authority-local-pg-gate.py"),
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


class FailingAssuranceRunner(FakeRunner):
    def run(self, command, *, env=None, cwd=None, capture=False):
        result = super().run(command, env=env, cwd=cwd, capture=capture)
        if str(command[-1]).endswith("ops/assurance-evidence-acceptance-local-pg-gate.py"):
            return mod.CommandResult(23, "", "ASSURANCE_STAGE_MISMATCH: exact causal fixture")
        return result


events.clear()
with (
    patch.object(mod, "find_postgres_binaries", return_value=fake_bins),
    patch.object(mod, "port_is_available", return_value=True),
    patch.object(tempfile, "mkdtemp", return_value=str(fake_root)),
    patch.object(mod.shutil, "rmtree"),
):
    assurance_stderr = io.StringIO()
    with redirect_stderr(assurance_stderr):
        assurance_failure = mod.run_local_ci(
            repo=REPO, ci_class="migration", port=55432, runner=FailingAssuranceRunner()
        )
check("assurance gate failure code is preserved", assurance_failure == 23)
check(
    "assurance gate exact child stderr is surfaced",
    "ASSURANCE_STAGE_MISMATCH: exact causal fixture" in assurance_stderr.getvalue(),
)
check(
    "later local gates do not run after assurance failure",
    not any(event[-1].endswith("ops/calendar-canary-local-pg-acceptance.py") for event in events),
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
    patch.object(mod.shutil, "rmtree") as remove_failure,
):
    result = mod.run_local_ci(repo=REPO, ci_class="migration", port=55432,
                              runner=FailingRunner())
check("CI failure is preserved", result == 7)
check(
    "authority acceptance never runs after CI failure",
    not any(event[-1].endswith("atomic-rule-approval-local-pg-acceptance.py") for event in events),
)
check("failure still stops server", events[-1][0] == "/fake/pg_ctl" and events[-1][-1] == "stop")
check("failure still removes cluster", remove_failure.call_count == 1)

events.clear()
with (
    patch.object(mod, "find_postgres_binaries", return_value=fake_bins),
    patch.object(mod, "port_is_available", return_value=True),
    patch.object(tempfile, "mkdtemp", return_value=str(fake_root)),
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
    "authority acceptance never runs after continuity integration failure",
    not any(event[-1].endswith("atomic-rule-approval-local-pg-acceptance.py") for event in events),
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
    patch.object(mod.shutil, "rmtree") as remove_start_failure,
):
    result = mod.run_local_ci(repo=REPO, ci_class="migration", port=55432,
                              runner=FailingStartRunner())
check("start failure is preserved", result == 1)
check("start failure still attempts stop", events[-1][0] == "/fake/pg_ctl" and events[-1][-1] == "stop")
check("start failure still removes cluster", remove_start_failure.call_count == 1)

run_sh = (REPO / "run.sh").read_text(encoding="utf-8")
check("one-command route is registered", "local-db-ci)" in run_sh and "ops/local-pg-ci.py" in run_sh)

print(f"local PG CI selftest — {passed}/{passed + len(failed)} passed")
if failed:
    print("FAILED: " + "; ".join(failed))
    raise SystemExit(1)

fake_owner.close()
