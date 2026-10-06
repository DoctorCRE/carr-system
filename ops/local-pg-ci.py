#!/usr/bin/env python3
"""Run canonical CI against a disposable, loopback-only local PostgreSQL 17.

This is the default database-development lane.  It creates no persistent
cluster, never accepts a caller DSN, strips cloud/provider credentials from the
child environment, and always stops and removes the temporary cluster.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Protocol, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib.disposable_pg_fixture import DisposablePostgres


class LocalPGRefusal(RuntimeError):
    pass


@dataclass(frozen=True)
class PostgresBinaries:
    initdb: Path
    pg_ctl: Path
    createdb: Path
    psql: Path


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str


class CommandRunner(Protocol):
    def run(
        self,
        command: Sequence[str | Path],
        *,
        env: Mapping[str, str] | None = None,
        cwd: Path | None = None,
        capture: bool = False,
    ) -> CommandResult: ...


class SubprocessRunner:
    def run(
        self,
        command: Sequence[str | Path],
        *,
        env: Mapping[str, str] | None = None,
        cwd: Path | None = None,
        capture: bool = False,
    ) -> CommandResult:
        completed = subprocess.run(
            [str(part) for part in command],
            env=None if env is None else dict(env),
            cwd=cwd,
            text=True,
            capture_output=capture,
            check=False,
        )
        return CommandResult(
            completed.returncode, completed.stdout or "", completed.stderr or ""
        )


def validate_port(value: int) -> int:
    if value < 1024 or value > 65535:
        raise LocalPGRefusal("local PostgreSQL port must be between 1024 and 65535")
    return value


def port_is_available(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def hosted_execution_is_declared(source: Mapping[str, str] | None = None) -> bool:
    """True only for the one workflow that deliberately drives this lane.

    The blanket hosted refusal below kept this lane off runners from the day it
    was written, and the cost of that was not visible until it was measured: the
    acceptance programs this module runs after ci.sh had NO hosted coverage on
    any commit, ever, and the rule-delivery cutover acceptance sat red on main
    across four map re-pins while the required strict context stayed green.
    Defect fa03017b.

    The refusal is kept as the DEFAULT rather than deleted, because what it
    prevents is real: a runner that quietly initdbs clusters is a surprise, and
    nothing should reach this lane by merely happening to set GITHUB_ACTIONS.
    Opting in takes an explicit variable AND this repository AND a real run id --
    the same shape the acceptance gates already use for `hosted_disposable`
    (see ops/zz-engineering-controller-concurrency-gate.py). A fork, a local
    shell exporting GITHUB_ACTIONS by accident, and any workflow that has not
    said so in as many words all still refuse.
    """
    env = os.environ if source is None else source
    return (
        env.get("CARR_LOCAL_PG_ALLOW_HOSTED") == "1"
        and env.get("GITHUB_ACTIONS", "").lower() == "true"
        and env.get("GITHUB_REPOSITORY") == "jbookout/carr-system"
        and bool(env.get("GITHUB_RUN_ID"))
    )


def refuse_hosted_execution() -> None:
    if os.environ.get("GITHUB_ACTIONS", "").lower() == "true":
        if hosted_execution_is_declared():
            return
        raise LocalPGRefusal("local-db-ci cannot run on a hosted GitHub runner")


def scrub_cloud_environment(source: Mapping[str, str]) -> dict[str, str]:
    """Build the minimal nonsecret environment needed by local tools."""
    allowed = {
        "HOME",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        # CI may explicitly forbid live Jev spend; this is a nonsecret mode.
        "CARR_JEV_OFFLINE_REPLAY",
        "LOGNAME",
        "PATH",
        "SHELL",
        "TERM",
        "TMPDIR",
        "USER",
    }
    return {key: value for key, value in source.items() if key in allowed}


def find_postgres_binaries() -> PostgresBinaries:
    override = os.environ.get("CARR_LOCAL_PG_BIN_DIR", "").strip()
    candidates: list[Path] = []
    if override:
        path = Path(override)
        if not path.is_absolute():
            raise LocalPGRefusal("CARR_LOCAL_PG_BIN_DIR must be an absolute path")
        candidates.append(path)
    candidates.extend(
        [
            Path("/opt/homebrew/opt/postgresql@17/bin"),
            Path("/usr/local/opt/postgresql@17/bin"),
            Path("/opt/homebrew/opt/postgresql@16/bin"),
            Path("/usr/local/opt/postgresql@16/bin"),
        ]
    )
    located = shutil.which("initdb")
    if located:
        candidates.append(Path(located).resolve().parent)
    for directory in candidates:
        paths = {name: directory / name for name in ("initdb", "pg_ctl", "createdb", "psql")}
        if all(path.is_file() and os.access(path, os.X_OK) for path in paths.values()):
            return PostgresBinaries(**paths)  # type: ignore[arg-type]
    raise LocalPGRefusal(
        "PostgreSQL client/server binaries were not found; install Homebrew postgresql@17"
    )


def _failure_detail(result: CommandResult, lines: int = 12) -> str:
    """The tail of a failed command's output, not just its last line.

    This used to return `detail[-1]` alone, and that one line is routinely the
    least informative one in the whole output. initdb's final line is the
    literal string "Examine the log output." -- so a hosted failure printed
    `local-db-ci setup failed: Examine the log output.` and named no cause,
    while the cause sat two lines above in output this function had already
    been handed. Keep the tail instead: the last line is still there, with
    whatever actually failed above it.
    """
    stream = result.stderr.strip() or result.stdout.strip()
    detail = [line for line in stream.splitlines() if line.strip()]
    if not detail:
        return "command failed without output"
    tail = detail[-lines:]
    prefix = "" if len(detail) <= lines else f"(last {lines} of {len(detail)} lines) "
    return prefix + " | ".join(line.strip()[:240] for line in tail)


def run_required_local_gate(
    runner: CommandRunner,
    python: Path,
    script: Path,
    *,
    env: Mapping[str, str],
    cwd: Path,
) -> CommandResult:
    """Run one required disposable-PG gate, refusing a missing artifact."""
    if not script.is_file():
        return CommandResult(78, "", f"required local PostgreSQL gate is unavailable: {script.name}")
    return runner.run([python, script], env=env, cwd=cwd, capture=True)


def run_local_ci(
    *,
    repo: Path,
    ci_class: str,
    port: int,
    runner: CommandRunner | None = None,
    integration_base: str | None = None,
) -> int:
    validate_port(port)
    refuse_hosted_execution()
    if ci_class not in {"migration", "strict"}:
        raise LocalPGRefusal("ci_class must be migration or strict")
    if not port_is_available(port):
        # NAME THE ESCAPE. This machine runs several sessions at once, so the
        # default port being taken is the ORDINARY case, not an error state —
        # it is almost always another session's disposable cluster. Until
        # 2026-08-22 the refusal stopped at "already in use", and a session that
        # did not already know about --port read it as "this lane is unavailable"
        # and hand-built its own cluster instead. One session did that seven
        # times in a night, initdb and all, while this flag sat one word away.
        raise LocalPGRefusal(
            f"127.0.0.1:{port} is already in use — almost always another session's "
            f"disposable cluster on this machine, not a problem with yours. "
            f"Re-run on a free port: ./run.sh local-db-ci --class {ci_class} --port {port + 8}")
    integration_source = None
    integration_port = port + 1
    if integration_base is not None:
        validate_port(integration_port)
        if not port_is_available(integration_port):
            raise LocalPGRefusal("integration proof needs an available adjacent port; select another --port")
        sys.path.insert(0, str(repo / "tools"))
        from integration_candidate import validate_candidate
        try:
            integration_source = validate_candidate(repo, integration_base)
        except ValueError as exc:
            raise LocalPGRefusal(str(exc)) from exc
    binaries = find_postgres_binaries()
    command_runner = runner or SubprocessRunner()
    clean_env = scrub_cloud_environment(os.environ)
    clean_env["LC_ALL"] = "C"
    clean_env["PATH"] = f"{binaries.initdb.parent}{os.pathsep}{clean_env.get('PATH', '')}"
    fixture = DisposablePostgres("carr-local-pg-ci.", binaries.pg_ctl, clean_env, runner=command_runner.run)
    root = fixture.root
    data = root / "data"
    integration_data = root / "integration-data"
    dsn = f"postgres://carr_ci@127.0.0.1:{port}/carr_ci"
    exit_code = 0

    def setup(command: Sequence[str | Path]) -> bool:
        nonlocal exit_code
        result = fixture.run(command, env=clean_env, cwd=repo, capture=True)
        if result.returncode:
            print(f"local-db-ci setup failed: {_failure_detail(result)}", file=sys.stderr)
            exit_code = result.returncode
            return False
        return True

    try:
        print(f"local-db-ci: creating disposable PostgreSQL on 127.0.0.1:{port}")
        if not setup(
            [
                binaries.initdb,
                "-D",
                data,
                "-U",
                "carr_ci",
                "--auth=trust",
                "--encoding=UTF8",
                "--no-locale",
            ]
        ):
            return exit_code
        if not setup(
            [
                binaries.pg_ctl,
                "-D",
                data,
                "-l",
                root / "postgres.log",
                "-o",
                f"-h 127.0.0.1 -p {port}",
                "-w",
                "start",
            ]
        ):
            return exit_code
        if not setup(
            [binaries.createdb, "-h", "127.0.0.1", "-p", str(port), "-U", "carr_ci", "carr_ci"]
        ):
            return exit_code
        if not setup(
            [
                binaries.psql,
                "-h",
                "127.0.0.1",
                "-p",
                str(port),
                "-U",
                "carr_ci",
                "-d",
                "carr_ci",
                "-v",
                "ON_ERROR_STOP=1",
                "-c",
                "create role neondb_owner;",
            ]
        ):
            return exit_code
        repo_python = repo / ".venv/bin/python"
        acceptance_python = (
            repo_python
            if repo_python.is_file() and os.access(repo_python, os.X_OK)
            else Path(sys.executable)
        )
        ownership_script = repo / "ops/canonical-ownership-lease-local-pg-gate.py"
        pre_database = "carr_ci_a2_pre"
        pre_dsn = f"postgres://carr_ci@127.0.0.1:{port}/{pre_database}"
        if not setup(
            [binaries.createdb, "-h", "127.0.0.1", "-p", str(port),
             "-U", "carr_ci", pre_database]
        ):
            return exit_code
        if not setup(
            [binaries.psql, "-h", "127.0.0.1", "-p", str(port),
             "-U", "carr_ci", "-d", pre_database, "-v", "ON_ERROR_STOP=1",
             "-q", "-f", repo / "db/schema.sql"]
        ):
            return exit_code
        pre_env = dict(clean_env)
        pre_env["DATABASE_URL"] = pre_dsn
        pre_apply = fixture.run(
            [acceptance_python, repo / "tools/migrate.py", "--apply", "--yes",
             "--through", "0431_completion_register_schema.sql"],
            env=pre_env,
            cwd=repo,
            capture=True,
        )
        if pre_apply.returncode:
            print(
                "local-db-ci: pre-0450 baseline migration failed: "
                f"{_failure_detail(pre_apply)}",
                file=sys.stderr,
            )
            return pre_apply.returncode
        # The canonical ownership gate compares the current frontier against a
        # pre-0450 catalog fingerprint. Migration 0507a intentionally replaces
        # engineering_record_slice_receipt, so prepare that one reviewed seam
        # in the isolated baseline before capturing the fingerprint. The
        # companion candidate remains frontier-only: its ownership functions
        # are exactly what the unchanged comparator must continue to inspect.
        if not setup(
            [binaries.psql, "-h", "127.0.0.1", "-p", str(port),
             "-U", "carr_ci", "-d", pre_database, "-v", "ON_ERROR_STOP=1",
             "-q", "-f", repo / "ops/f03-receipt-validator.candidate.sql"]
        ):
            return exit_code
        fingerprint_env = dict(clean_env)
        fingerprint_env["CARR_LOCAL_PG_DSN"] = pre_dsn
        pre_fingerprint = fixture.run(
            [acceptance_python, ownership_script, "--fingerprint-only"],
            env=fingerprint_env,
            cwd=repo,
            capture=True,
        )
        if pre_fingerprint.returncode:
            print(
                "local-db-ci: pre-0450 catalog fingerprint failed: "
                f"{_failure_detail(pre_fingerprint)}",
                file=sys.stderr,
            )
            return pre_fingerprint.returncode
        try:
            ownership_baseline = json.dumps(
                json.loads(pre_fingerprint.stdout), sort_keys=True, separators=(",", ":")
            )
        except (TypeError, ValueError) as exc:
            print(
                "local-db-ci: pre-0450 catalog fingerprint was not exact JSON",
                file=sys.stderr,
            )
            return 78
        if integration_base is not None:
            # Cluster-global roles require an independent cluster, not just a
            # database name. Preserve the canonical lane's fresh-role baseline.
            # Restore current main, forward the candidate and prove consumers.
            from integration_candidate import git, validate_candidate
            # Keep one active cluster: macOS has a small shared-memory ID budget.
            paused = fixture.run(
                [binaries.pg_ctl, "-D", data, "-m", "fast", "-w", "stop"],
                env=clean_env, cwd=repo, capture=True,
            )
            if paused.returncode:
                print("local-db-ci: canonical PostgreSQL pause failed", file=sys.stderr)
                return paused.returncode
            schema = root / "integration-main-schema.sql"
            schema.write_bytes(git(repo, "show", f"{integration_base}:db/schema.sql"))
            integration_dsn = f"postgres://carr_ci@127.0.0.1:{integration_port}/carr_ci_integration"
            integration_commands: tuple[tuple[str, list[str | Path]], ...] = (
                ("init", [binaries.initdb, "-D", integration_data, "-U", "carr_ci", "--auth=trust", "--encoding=UTF8", "--no-locale"]),
                ("start", [binaries.pg_ctl, "-D", integration_data, "-l", root / "integration-postgres.log", "-o", f"-h 127.0.0.1 -p {integration_port}", "-w", "start"]),
                ("create", [binaries.createdb, "-h", "127.0.0.1", "-p", str(integration_port), "-U", "carr_ci", "carr_ci_integration"]),
                ("role", [binaries.psql, integration_dsn, "-v", "ON_ERROR_STOP=1", "-q", "-c", "create role neondb_owner;"]),
                ("restore", [binaries.psql, integration_dsn, "-v", "ON_ERROR_STOP=1", "-q", "-f", schema]),
            )
            for stage, command in integration_commands:
                result = fixture.run(command, env=clean_env, cwd=repo, capture=True)
                if result.returncode:
                    print(f"local-db-ci: integrated {stage} failed (exit {result.returncode})", file=sys.stderr)
                    return result.returncode
            forward_env = dict(clean_env)
            forward_env["DATABASE_URL"] = integration_dsn
            forward = fixture.run([acceptance_python, repo / "tools/migrate.py", "--apply", "--yes"],
                                         env=forward_env, cwd=repo, capture=True)
            if forward.returncode:
                print("local-db-ci: current-main restore to candidate forward migration failed", file=sys.stderr)
                return forward.returncode
            for test_file, dsn_key in (
                ("find-rule-supersedes.test.mjs", "CARR_RULE_TEST_DATABASE_URL"),
                ("catch-me-up-writer-route.test.mjs", "CARR_WRITER_READ_TEST_DATABASE_URL"),
            ):
                consumer_env = dict(clean_env)
                consumer_env[dsn_key] = integration_dsn
                proof = fixture.run(["node", "--test", f"mcp-server/test/{test_file}"],
                                           env=consumer_env, cwd=repo, capture=True)
                if proof.returncode:
                    print(f"local-db-ci: integrated consumer proof failed: {test_file}", file=sys.stderr)
                    return proof.returncode
            if validate_candidate(repo, integration_base) != integration_source:
                raise LocalPGRefusal("integration source changed during restore/forward/consumer proof")
            disposed = fixture.run(
                [binaries.pg_ctl, "-D", integration_data, "-m", "fast", "-w", "stop"],
                env=clean_env, cwd=repo, capture=True,
            )
            if disposed.returncode:
                print("local-db-ci: integration PostgreSQL stop failed", file=sys.stderr)
                return disposed.returncode
            resumed = fixture.run(
                [binaries.pg_ctl, "-D", data, "-l", root / "postgres.log",
                 "-o", f"-h 127.0.0.1 -p {port}", "-w", "start"],
                env=clean_env, cwd=repo, capture=True,
            )
            if resumed.returncode:
                print("local-db-ci: canonical PostgreSQL resume failed", file=sys.stderr)
                return resumed.returncode
            print("local-db-ci: " + json.dumps(integration_source, sort_keys=True))
        ci_env = dict(clean_env)
        ci_env["CARR_CI_DATABASE_URL"] = dsn
        ci_command: list[str | Path] = [repo / "ops/ci.sh"]
        if ci_class == "strict":
            ci_command.append("--strict")
        else:
            ci_command.extend(["--only", "migration"])
        result = fixture.run(ci_command, env=ci_env, cwd=repo)
        exit_code = result.returncode
        if exit_code:
            print("local-db-ci: canonical CI failed", file=sys.stderr)
        else:
            f03_env = dict(ci_env)
            f03_env["CARR_F03_PSQL"] = str(binaries.psql)
            f03_acceptance = fixture.run(
                [acceptance_python, repo / "tools/test-f03-production-migration.py"],
                env=f03_env,
                cwd=repo,
                capture=True,
            )
            if f03_acceptance.returncode:
                print(
                    "local-db-ci: F03 production validator acceptance failed: "
                    f"{_failure_detail(f03_acceptance)}",
                    file=sys.stderr,
                )
                exit_code = f03_acceptance.returncode
            else:
                print(f03_acceptance.stdout, end="")
        if exit_code == 0:
            # The continuity handler suite has a real PostgreSQL branch.  Run it
            # on this same disposable cluster so its bounded integration proof
            # cannot become a permanent skip in hosted migration CI.  Neon’s
            # Pool is intentionally retained as the test default for external
            # ephemeral databases; raw loopback PostgreSQL needs the pinned pg
            # driver selected explicitly here.
            continuity_env = dict(ci_env)
            continuity_env["CARR_CONTINUITY_EPHEMERAL_DATABASE_URL"] = dsn
            continuity_env["CARR_CONTINUITY_DATABASE_DRIVER_MODULE"] = "pg"
            continuity = fixture.run(
                ["node", "--test", "mcp-server/test/codex-continuity.test.mjs"],
                env=continuity_env,
                cwd=repo,
                capture=True,
            )
            if continuity.returncode:
                print(
                    "local-db-ci: Codex continuity real-PostgreSQL integration failed: "
                    f"{_failure_detail(continuity)}",
                    file=sys.stderr,
                )
                exit_code = continuity.returncode
            else:
                print("local-db-ci: Codex continuity real-PostgreSQL integration passed")
        if exit_code == 0:
            leads_env = dict(ci_env)
            leads_env["LEAD_WORKSPACE_TEST_DATABASE_URL"] = dsn
            leads = fixture.run(
                ["node", "--test", "mcp-server/test/lead-workspace-pg.test.mjs"],
                env=leads_env, cwd=repo, capture=True,
            )
            if leads.returncode:
                print(f"local-db-ci: Leads registered PostgreSQL acceptance failed: {_failure_detail(leads)}", file=sys.stderr)
                exit_code = leads.returncode
            else:
                print("local-db-ci: Leads registered PostgreSQL acceptance passed")
        if exit_code == 0:
            acceptance_script = repo / "ops/atomic-rule-approval-local-pg-acceptance.py"
            if not acceptance_python.is_file() or not os.access(acceptance_python, os.X_OK):
                print("local-db-ci: repository Python environment is unavailable", file=sys.stderr)
                exit_code = 78
            elif not acceptance_script.is_file():
                print("local-db-ci: atomic rule authority acceptance is unavailable", file=sys.stderr)
                exit_code = 78
            else:
                acceptance_env = dict(clean_env)
                # Keep every disposable-database client on the same PostgreSQL toolchain
                # as the server. Hosted runners expose several client majors on PATH.
                acceptance_env["PATH"] = (
                    f"{binaries.psql.parent}{os.pathsep}{acceptance_env.get('PATH', '')}"
                )
                acceptance_env["CARR_LOCAL_PG_DSN"] = dsn
                acceptance_env["CARR_OWNERSHIP_PRE_0450_FINGERPRINT"] = ownership_baseline
                acceptance = fixture.run(
                    [acceptance_python, acceptance_script],
                    env=acceptance_env,
                    cwd=repo,
                    capture=True,
                )
                exit_code = acceptance.returncode
                if exit_code:
                    print(
                        f"local-db-ci: atomic rule authority acceptance failed: "
                        f"{_failure_detail(acceptance)}",
                        file=sys.stderr,
                    )
                else:
                    delivery_script = repo / "ops/rule-delivery-local-pg-acceptance.py"
                    delivery = fixture.run(
                        [acceptance_python, delivery_script],
                        env=acceptance_env,
                        cwd=repo,
                        capture=True,
                    )
                    if delivery.returncode:
                        print(
                            f"local-db-ci: rule-delivery acceptance failed: "
                            f"{_failure_detail(delivery)}",
                            file=sys.stderr,
                        )
                        exit_code = delivery.returncode
                    else:
                        print("local-db-ci: atomic rule-delivery cutover acceptance passed")
                if exit_code == 0:
                    engineering_claim_script = repo / "ops/engineering-claim-local-pg-gate.py"
                    engineering_claim = fixture.run(
                        [acceptance_python, engineering_claim_script],
                        env=acceptance_env,
                        cwd=repo,
                        capture=True,
                    )
                    if engineering_claim.returncode:
                        print(
                            f"local-db-ci: engineering claim acceptance failed: "
                            f"{_failure_detail(engineering_claim)}",
                            file=sys.stderr,
                        )
                        exit_code = engineering_claim.returncode
                    else:
                        print("local-db-ci: scoped engineering claim acceptance passed")
                if exit_code == 0:
                    engineering_race_script = repo / "ops/engineering-envelope-race-local-pg-gate.py"
                    engineering_race = fixture.run(
                        [acceptance_python, engineering_race_script],
                        env=acceptance_env,
                        cwd=repo,
                        capture=True,
                    )
                    if engineering_race.returncode:
                        print(
                            f"local-db-ci: engineering envelope race acceptance failed: "
                            f"{_failure_detail(engineering_race)}",
                            file=sys.stderr,
                        )
                        exit_code = engineering_race.returncode
                    else:
                        print("local-db-ci: Engineering envelope race acceptance passed")
                if exit_code == 0:
                    ownership = fixture.run(
                        [acceptance_python, ownership_script],
                        env=acceptance_env,
                        cwd=repo,
                        capture=True,
                    )
                    if ownership.returncode:
                        print(
                            f"local-db-ci: canonical ownership lease acceptance failed: "
                            f"{_failure_detail(ownership)}",
                            file=sys.stderr,
                        )
                        exit_code = ownership.returncode
                    else:
                        print("local-db-ci: canonical ownership lease acceptance passed")
                if exit_code == 0:
                    assurance_script = repo / "ops/assurance-evidence-acceptance-local-pg-gate.py"
                    assurance = run_required_local_gate(
                        command_runner,
                        acceptance_python,
                        assurance_script,
                        env=acceptance_env,
                        cwd=repo,
                    )
                    if assurance.returncode:
                        print(
                            f"local-db-ci: assurance evidence/acceptance persistence failed: "
                            f"{_failure_detail(assurance)}",
                            file=sys.stderr,
                        )
                        exit_code = assurance.returncode
                    else:
                        print("local-db-ci: assurance evidence/acceptance persistence passed")
                if exit_code == 0:
                    source_merge_script = repo / "ops/source-merge-authority-local-pg-gate.py"
                    source_merge = run_required_local_gate(
                        command_runner,
                        acceptance_python,
                        source_merge_script,
                        env=acceptance_env,
                        cwd=repo,
                    )
                    if source_merge.returncode:
                        print(
                            f"local-db-ci: source-merge authority projection failed: "
                            f"{_failure_detail(source_merge)}",
                            file=sys.stderr,
                        )
                        exit_code = source_merge.returncode
                    else:
                        print("local-db-ci: source-merge authority projection passed")
                if exit_code == 0:
                    canary_script = repo / "ops/calendar-canary-local-pg-acceptance.py"
                    canary = fixture.run(
                        [acceptance_python, canary_script],
                        env=acceptance_env,
                        cwd=repo,
                        capture=True,
                    )
                    if canary.returncode:
                        print(
                            f"local-db-ci: calendar canary acceptance failed: "
                            f"{_failure_detail(canary)}",
                            file=sys.stderr,
                        )
                        exit_code = canary.returncode
                    else:
                        print("local-db-ci: calendar canary isolation acceptance passed")
                if exit_code == 0:
                    nightly_canary_script = repo / "ops/nightly-canary-local-pg-acceptance.py"
                    nightly_canary = fixture.run(
                        [acceptance_python, nightly_canary_script],
                        env=acceptance_env,
                        cwd=repo,
                        capture=True,
                    )
                    if nightly_canary.returncode:
                        print(
                            f"local-db-ci: Nightly canary acceptance failed: "
                            f"{_failure_detail(nightly_canary)}",
                            file=sys.stderr,
                        )
                        exit_code = nightly_canary.returncode
                    else:
                        print("local-db-ci: Nightly canary isolation acceptance passed")
                if exit_code == 0:
                    renewal_ingress_script = repo / "ops/renewal-signed-ingress-local-pg-acceptance.py"
                    renewal_ingress = fixture.run(
                        [acceptance_python, renewal_ingress_script],
                        env=acceptance_env,
                        cwd=repo,
                        capture=True,
                    )
                    if renewal_ingress.returncode:
                        print(
                            f"local-db-ci: renewal signed ingress acceptance failed: "
                            f"{_failure_detail(renewal_ingress)}",
                            file=sys.stderr,
                        )
                        exit_code = renewal_ingress.returncode
                    else:
                        print("local-db-ci: renewal signed ingress least-privilege acceptance passed")
                if exit_code == 0:
                    renewal_lease_script = repo / "ops/renewal-lease-ledger-local-pg-gate.py"
                    renewal_lease = fixture.run(
                        [acceptance_python, renewal_lease_script],
                        env=acceptance_env,
                        cwd=repo,
                        capture=True,
                    )
                    if renewal_lease.returncode:
                        print(
                            f"local-db-ci: renewal lease ledger acceptance failed: "
                            f"{_failure_detail(renewal_lease)}",
                            file=sys.stderr,
                        )
                        exit_code = renewal_lease.returncode
                    else:
                        print("local-db-ci: authenticated renewal lease ledger acceptance passed")
                if exit_code == 0:
                    incident_recovery_script = repo / "ops/incident-recovery-local-pg-acceptance.py"
                    incident_recovery = fixture.run(
                        [acceptance_python, incident_recovery_script],
                        env=acceptance_env,
                        cwd=repo,
                        capture=True,
                    )
                    if incident_recovery.returncode:
                        print(
                            f"local-db-ci: incident fingerprint and recovery acceptance failed: "
                            f"{_failure_detail(incident_recovery)}",
                            file=sys.stderr,
                        )
                        exit_code = incident_recovery.returncode
                    else:
                        print("local-db-ci: incident fingerprint and success-clears acceptance passed")
                        completion_schema_script = (
                            repo / "ops/completion-register-schema-local-pg-gate.py"
                        )
                        completion_schema = fixture.run(
                            [acceptance_python, completion_schema_script],
                            env=acceptance_env,
                            cwd=repo,
                            capture=True,
                        )
                        if completion_schema.returncode:
                            print(
                                "local-db-ci: completion register schema fixture failed: "
                                f"{_failure_detail(completion_schema)}",
                                file=sys.stderr,
                            )
                            exit_code = completion_schema.returncode
                        else:
                            print(completion_schema.stdout, end="")
                if exit_code == 0:
                    snapshot = fixture.run(
                        [
                            repo / "bin/schema-snapshot.sh",
                            "--from-disposable-local",
                            dsn,
                            "--verify-only",
                        ],
                        env=acceptance_env,
                        cwd=repo,
                        capture=True,
                    )
                    if snapshot.returncode:
                        print(
                            f"local-db-ci: schema snapshot failed: {_failure_detail(snapshot)}",
                            file=sys.stderr,
                        )
                        exit_code = snapshot.returncode
                    else:
                        print(snapshot.stdout, end="")
                if exit_code == 0 and integration_base is not None:
                    if validate_candidate(repo, integration_base) != integration_source:
                        raise LocalPGRefusal("integration source changed during canonical candidate proof")
                if exit_code == 0:
                    print(
                        f"local-db-ci: {ci_class} proof and atomic Joe authority lifecycle "
                        "passed on disposable PostgreSQL"
                    )
    finally:
        try:
            fixture.close()
        except RuntimeError as exc:
            print(str(exc), file=sys.stderr)
            if exit_code == 0:
                exit_code = 70
    return exit_code


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--class",
        dest="ci_class",
        choices=("migration", "strict"),
        default="migration",
        help="migration is the fast DB lane; strict runs every canonical class locally",
    )
    parser.add_argument("--port", type=int, default=55432)
    parser.add_argument("--integration-base", help="exact current-main SHA for restore/forward/consumer union proof")
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[1]
    try:
        return run_local_ci(
            repo=repo, ci_class=args.ci_class, port=args.port, runner=SubprocessRunner(), integration_base=args.integration_base
        )
    except LocalPGRefusal as exc:
        print(f"local-db-ci refused: {exc}", file=sys.stderr)
        return 78


if __name__ == "__main__":
    raise SystemExit(main())
