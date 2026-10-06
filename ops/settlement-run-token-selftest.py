#!/usr/bin/env python3
"""Independent denial, mutation, and trap fixtures for settlement-run-token.py.

Every case builds a disposable git repository. No destructive command is ever
run against this checkout or canonical. The four packet-mandated denials use
fresh tokens and assert the returned reason id, not a fixture name or generic
nonzero result. Four in-memory source mutants prove each assertion has a tooth;
no import cache or .pyc participates in that proof.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import types


OPS = Path(__file__).resolve().parent
SCRIPT = OPS / "settlement-run-token.py"
REGISTRATION = OPS / "config" / "settlement-run-token.v1.json"
sys.path.insert(0, str(OPS))
from git_env import fixture_env

# Every git call below builds a THROWAWAY repository under tempfile. git exports
# GIT_DIR and GIT_INDEX_FILE into hooks and they OVERRIDE cwd, so an unscrubbed
# fixture lands in whatever repository GIT_DIR names — live main, on 2026-08-14.
# fixture_env() strips them and refuses to inherit a real git identity.
FIXTURE_ENV = fixture_env()
SOURCE = SCRIPT.read_text(encoding="utf-8")
BASE_NOW = 1_788_200_000_000_000_000


def load_module(name: str, source: str = SOURCE):
    module = types.ModuleType(name)
    module.__file__ = str(SCRIPT)
    module.__package__ = None
    exec(compile(source, f"<{name}>", "exec"), module.__dict__)
    return module


TOKEN = load_module("settlement_run_token_under_test")


def run_checked(argv: list[str], *, cwd: Path) -> str:
    result = subprocess.run(argv, cwd=cwd, env=FIXTURE_ENV, capture_output=True, text=True, timeout=20)
    if result.returncode != 0:
        raise AssertionError(f"command failed {argv!r}: {result.stderr or result.stdout}")
    return result.stdout.strip()


class Fixture:
    def __init__(self, module, root: Path, *, sleep_seconds: float = 0.0, ready_file: Path | None = None):
        self.module = module
        self.root = root
        self.repo = root / "repo"
        self.store = root / "store"
        self.token_file = root / "token.txt"
        self.manifest = root / "manifest.json"
        self.allowlist = root / "allowlist.json"
        self.runner_log = root / "runner.jsonl"
        self.operator_a = module.operator_binding_from_env({"CODEX_THREAD_ID": "operator-session-a"})
        self.operator_b = module.operator_binding_from_env({"CODEX_THREAD_ID": "operator-session-b"})
        self.root.mkdir(parents=True)
        self.repo.mkdir()
        run_checked(["git", "init", "-q"], cwd=self.repo)
        run_checked(["git", "config", "user.name", "R03C Fixture"], cwd=self.repo)
        run_checked(["git", "config", "user.email", "r03c-fixture@example.invalid"], cwd=self.repo)
        runner = self.repo / "fixture-runner.py"
        runner.write_text(
            """#!/usr/bin/env python3
import argparse, json, os, sys, time
p=argparse.ArgumentParser()
p.add_argument('--manifest-fd',type=int,required=True)
p.add_argument('--allowlist-fd',type=int,required=True)
p.add_argument('--capability-receipt-fd',type=int,required=True)
p.add_argument('--log',required=True)
p.add_argument('--ready-file')
p.add_argument('--sleep-seconds',type=float,default=0)
a=p.parse_args()
manifest=os.read(a.manifest_fd,8*1024*1024)
allowlist=os.read(a.allowlist_fd,8*1024*1024)
receipt=os.read(a.capability_receipt_fd,1024*1024)
assert json.loads(manifest)['schema_version']=='carr.r03-run-manifest.fixture.v1'
assert json.loads(allowlist)['schema_version']=='carr.settlement-command-pathspec-allowlist.v1'
assert json.loads(receipt)['capability_key']=='R03C.settlement-capability.v1'
def _writable(fd):
    try:
        os.write(fd, b'\\0'); return True
    except OSError:
        return False
writable={'manifest':_writable(a.manifest_fd),'allowlist':_writable(a.allowlist_fd),'receipt':_writable(a.capability_receipt_fd)}
with open(a.log,'a',encoding='utf-8') as fh:
    fh.write(json.dumps({'manifest':len(manifest),'allowlist':len(allowlist),'exe':os.path.realpath(sys.argv[0]),'writable':writable})+'\\n')
if a.ready_file:
    with open(a.ready_file,'w',encoding='utf-8') as fh: fh.write('ready\\n')
time.sleep(a.sleep_seconds)
""",
            encoding="utf-8",
        )
        runner.chmod(0o755)
        run_checked(["git", "add", "fixture-runner.py"], cwd=self.repo)
        run_checked(["git", "commit", "-q", "-m", "fixture runner"], cwd=self.repo)
        self.oid = run_checked(["git", "rev-parse", "HEAD"], cwd=self.repo)
        self.manifest_bytes = json.dumps({
            "schema_version": "carr.r03-run-manifest.fixture.v1",
            "approved": True,
        }, sort_keys=True).encode() + b"\n"
        self.manifest.write_bytes(self.manifest_bytes)
        runner_argv = [
            str(runner.resolve()),
            "--manifest-fd={manifest_fd}",
            "--allowlist-fd={allowlist_fd}",
            "--capability-receipt-fd={capability_receipt_fd}",
            f"--log={self.runner_log}",
            f"--sleep-seconds={sleep_seconds}",
        ]
        if ready_file is not None:
            runner_argv.append(f"--ready-file={ready_file}")
        self.allowlist_bytes = json.dumps({
            "schema_version": "carr.settlement-command-pathspec-allowlist.v1",
            "runner_argv": runner_argv,
            "commands": [{
                "id": "fixture-pathscoped-clean",
                "argv": ["git", "clean", "-fd", "--", "approved-fixture-path"],
                "pathspecs": ["approved-fixture-path"],
            }],
        }, sort_keys=True).encode() + b"\n"
        self.allowlist.write_bytes(self.allowlist_bytes)

    def issue(self, *, operator: str | None = None, ttl: int = 60, now: int = BASE_NOW):
        return self.module.issue_capability(
            store_arg=self.store,
            token_file_arg=self.token_file,
            manifest_arg=self.manifest,
            approved_manifest_digest=self.module.sha256_bytes(self.manifest_bytes),
            allowlist_arg=self.allowlist,
            repository_arg=self.repo,
            starting_object_id=self.oid,
            ttl_seconds=ttl,
            operator_binding_digest=operator or self.operator_a,
            now_ns=now,
            registration_path=REGISTRATION,
        )

    def run(self, *, operator: str | None = None, now: int = BASE_NOW + 1):
        return self.module.run_capability(
            store_arg=self.store,
            token_file_arg=self.token_file,
            manifest_arg=self.manifest,
            allowlist_arg=self.allowlist,
            repository_arg=self.repo,
            operator_binding_digest=operator or self.operator_a,
            now_ns=now,
            registration_path=REGISTRATION,
        )


def exact_refusal(module, action, reason_id: str) -> None:
    try:
        action()
    except module.Refusal as exc:
        if exc.reason_id != reason_id:
            raise AssertionError(f"expected {reason_id}, got {exc.reason_id}: {exc.detail}") from exc
        return
    raise AssertionError(f"expected exact refusal {reason_id}, but the action succeeded")


def denial_second_use(module, root: Path) -> None:
    fixture = Fixture(module, root)
    fixture.issue()
    assert fixture.run() == 0
    exact_refusal(module, fixture.run, module.REASON_ALREADY_CONSUMED)


def denial_different_operator(module, root: Path) -> None:
    fixture = Fixture(module, root)
    fixture.issue()
    exact_refusal(module, lambda: fixture.run(operator=fixture.operator_b), module.REASON_OPERATOR_MISMATCH)
    assert fixture.run(operator=fixture.operator_a) == 0, "operator denial consumed the token"


def denial_drifted_manifest(module, root: Path) -> None:
    fixture = Fixture(module, root)
    fixture.issue()
    fixture.manifest.write_bytes(json.dumps({
        "schema_version": "carr.r03-run-manifest.fixture.v1",
        "approved": False,
    }, sort_keys=True).encode() + b"\n")
    exact_refusal(module, fixture.run, module.REASON_MANIFEST_MISMATCH)
    fixture.manifest.write_bytes(fixture.manifest_bytes)
    assert fixture.run() == 0, "manifest denial consumed the token"


def denial_expired(module, root: Path) -> None:
    fixture = Fixture(module, root)
    fixture.issue(ttl=1)
    exact_refusal(module, lambda: fixture.run(now=BASE_NOW + 2_000_000_000), module.REASON_EXPIRED)
    status = module.status_capability(
        store_arg=fixture.store, token_file_arg=fixture.token_file, registration_path=REGISTRATION,
    )
    assert status["state"] == "revoked" and status["revocation_reason"] == "expired"


DENIALS = (
    ("second_use", "settlement_capability.refusal.already_consumed", denial_second_use),
    ("different_operator", "settlement_capability.refusal.operator_mismatch", denial_different_operator),
    ("drifted_manifest", "settlement_capability.refusal.manifest_digest_mismatch", denial_drifted_manifest),
    ("expired_token", "settlement_capability.refusal.expired", denial_expired),
)


MUTANTS = {
    "second_use": (
        'if record.get("consumed_at_ns") is not None:',
        'if False and record.get("consumed_at_ns") is not None:',
    ),
    "different_operator": (
        'if operator_binding_digest != record.get("operator_binding_digest"):',
        'if False and operator_binding_digest != record.get("operator_binding_digest"):',
    ),
    "drifted_manifest": (
        'if sha256_bytes(manifest_body) != record.get("approved_manifest_digest"):',
        'if False and sha256_bytes(manifest_body) != record.get("approved_manifest_digest"):',
    ),
    "expired_token": (
        'if now_ns >= expires_at:',
        'if False and now_ns >= expires_at:',
    ),
}


def prove_denials_and_mutants() -> None:
    with tempfile.TemporaryDirectory(prefix="r03c-denials-") as temp:
        root = Path(temp)
        for index, (name, reason_id, scenario) in enumerate(DENIALS):
            scenario(TOKEN, root / f"base-{index}")
            print(f"PASS denial.{name} reason={reason_id}")

    killed = 0
    for index, (name, _reason_id, scenario) in enumerate(DENIALS):
        old, new = MUTANTS[name]
        if SOURCE.count(old) != 1:
            raise AssertionError(f"mutation anchor for {name} occurs {SOURCE.count(old)} times")
        mutant = load_module(f"settlement_run_token_mutant_{index}", SOURCE.replace(old, new, 1))
        try:
            with tempfile.TemporaryDirectory(prefix=f"r03c-mutant-{name}-") as temp:
                scenario(mutant, Path(temp) / "fixture")
        except AssertionError:
            killed += 1
            print(f"PASS mutation.{name} killed=in_memory_no_pyc")
        else:
            raise AssertionError(f"mutation {name} survived its exact denial assertion")
    assert killed == len(MUTANTS)


def prove_reviewer_abort() -> None:
    with tempfile.TemporaryDirectory(prefix="r03c-reviewer-abort-") as temp:
        fixture = Fixture(TOKEN, Path(temp) / "fixture")
        fixture.issue()
        result = TOKEN.revoke_capability(
            store_arg=fixture.store,
            token_file_arg=fixture.token_file,
            reason="ao_reviewer_abort",
            now_ns=BASE_NOW + 1,
            registration_path=REGISTRATION,
        )
        assert result["revocation_reason"] == "ao_reviewer_abort"
        exact_refusal(TOKEN, fixture.run, TOKEN.REASON_REVOKED)
        print("PASS revocation.ao_reviewer_abort reason=settlement_capability.refusal.revoked")


def prove_trap_revocation() -> None:
    with tempfile.TemporaryDirectory(prefix="r03c-trap-") as temp:
        root = Path(temp)
        ready = root / "ready"
        fixture = Fixture(TOKEN, root / "fixture", sleep_seconds=30, ready_file=ready)
        cli_env = os.environ.copy()
        for key in TOKEN.NATIVE_SESSION_KEYS:
            cli_env.pop(key, None)
        cli_env["CODEX_THREAD_ID"] = "trap-operator-session"
        operator = TOKEN.operator_binding_from_env(cli_env)
        fixture.issue(operator=operator, ttl=60, now=time.time_ns())
        command = [
            sys.executable, str(SCRIPT), "run",
            "--store", str(fixture.store), "--token-file", str(fixture.token_file),
            "--manifest", str(fixture.manifest), "--allowlist", str(fixture.allowlist),
            "--repository", str(fixture.repo),
        ]
        process = subprocess.Popen(command, env=cli_env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        deadline = time.monotonic() + 8
        while not ready.exists() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.05)
        if not ready.exists():
            stdout, stderr = process.communicate(timeout=2)
            raise AssertionError(f"runner never reached mid-flight state: rc={process.returncode} {stdout} {stderr}")
        process.send_signal(signal.SIGTERM)
        stdout, stderr = process.communicate(timeout=8)
        assert process.returncode == 128 + signal.SIGTERM, (process.returncode, stdout, stderr)
        status = TOKEN.status_capability(
            store_arg=fixture.store, token_file_arg=fixture.token_file, registration_path=REGISTRATION,
        )
        assert status["state"] == "revoked"
        assert status["consumed_at_ns"] is not None
        assert status["revocation_reason"] == "run_finally"
        print("PASS revocation.trap_midflight signal=SIGTERM state=consumed+revoked reason=run_finally")


def prove_fail_closed_uncertainty() -> None:
    with tempfile.TemporaryDirectory(prefix="r03c-fail-closed-") as temp:
        fixture = Fixture(TOKEN, Path(temp) / "fixture")
        exact_refusal(
            TOKEN,
            lambda: TOKEN.operator_binding_from_env({}),
            TOKEN.REASON_BINDING_AMBIGUOUS,
        )
        fixture.issue()
        exact_refusal(
            TOKEN,
            lambda: fixture.run(now=BASE_NOW - 1),
            TOKEN.REASON_CLOCK_INVALID,
        )
        fixture.store.chmod(0o755)
        exact_refusal(TOKEN, fixture.run, TOKEN.REASON_STORE_UNAVAILABLE)
        print("PASS fail_closed ambiguous_operator+clock_rollback+unsafe_store")


def prove_remaining_bindings() -> None:
    with tempfile.TemporaryDirectory(prefix="r03c-bindings-") as temp:
        root = Path(temp)

        allowlist_fixture = Fixture(TOKEN, root / "allowlist")
        allowlist_fixture.issue()
        changed = json.loads(allowlist_fixture.allowlist_bytes)
        changed["commands"][0]["pathspecs"] = ["different-approved-fixture-path"]
        allowlist_fixture.allowlist.write_text(json.dumps(changed, sort_keys=True) + "\n", encoding="utf-8")
        exact_refusal(TOKEN, allowlist_fixture.run, TOKEN.REASON_ALLOWLIST_MISMATCH)

        repository_fixture = Fixture(TOKEN, root / "repository-a")
        other_repository = Fixture(TOKEN, root / "repository-b")
        repository_fixture.issue()
        exact_refusal(
            TOKEN,
            lambda: TOKEN.run_capability(
                store_arg=repository_fixture.store,
                token_file_arg=repository_fixture.token_file,
                manifest_arg=repository_fixture.manifest,
                allowlist_arg=repository_fixture.allowlist,
                repository_arg=other_repository.repo,
                operator_binding_digest=repository_fixture.operator_a,
                now_ns=BASE_NOW + 1,
                registration_path=REGISTRATION,
            ),
            TOKEN.REASON_REPOSITORY_MISMATCH,
        )

        oid_fixture = Fixture(TOKEN, root / "starting-oid")
        oid_fixture.issue()
        (oid_fixture.repo / "post-issuance-drift.txt").write_text("drift\n", encoding="utf-8")
        run_checked(["git", "add", "post-issuance-drift.txt"], cwd=oid_fixture.repo)
        run_checked(["git", "commit", "-q", "-m", "drift head"], cwd=oid_fixture.repo)
        exact_refusal(TOKEN, oid_fixture.run, TOKEN.REASON_STARTING_OID_MISMATCH)

        print("PASS bindings allowlist_digest+repository_identity+starting_object_id")


def prove_runner_code_binding() -> None:
    # B1: the runner's own bytes are bound, not just its path. Rewrite the
    # runner in place with HEAD untouched and the token must refuse. The
    # restore-and-succeed assertion is load-bearing: it proves the redemption
    # check fired inside the lock BEFORE consume — a refusal that had already
    # consumed the token would make the rerun hit already_consumed instead.
    with tempfile.TemporaryDirectory(prefix="r03c-runner-code-") as temp:
        fixture = Fixture(TOKEN, Path(temp) / "fixture")
        fixture.issue()
        runner = fixture.repo / "fixture-runner.py"
        original = runner.read_bytes()
        runner.write_bytes(original + b"\nimport os as _os; _os.write(1, b'substituted code ran')\n")
        assert TOKEN.current_head(fixture.repo) == fixture.oid, "test must leave HEAD unchanged"
        exact_refusal(TOKEN, fixture.run, TOKEN.REASON_RUNNER_DIGEST_MISMATCH)
        runner.write_bytes(original)
        assert fixture.run() == 0, "runner-digest denial must refuse before consuming the token"
        print("PASS bindings runner_executable_digest reason=settlement_capability.refusal.runner_digest_mismatch")


def _last_runner_record(fixture) -> dict:
    lines = [l for l in fixture.runner_log.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert lines, "runner produced no log line"
    return json.loads(lines[-1])


def prove_runner_snapshot_execution() -> None:
    # B1 residual: what executes must be a private mode-0500 snapshot of the
    # verified bytes, NOT the live path — so a same-inode rewrite between the
    # digest check and exec cannot substitute code. The runner reports the path
    # it is running from; it must be the snapshot, not the repository runner,
    # and the snapshot must be gone after the run.
    with tempfile.TemporaryDirectory(prefix="r03c-snapshot-") as temp:
        fixture = Fixture(TOKEN, Path(temp) / "fixture")
        fixture.issue()
        assert fixture.run() == 0
        exe = _last_runner_record(fixture)["exe"]
        repo_runner = os.path.realpath(str(fixture.repo / "fixture-runner.py"))
        assert "settlement-capability-runner-" in exe, f"runner did not execute from a snapshot: {exe}"
        assert exe != repo_runner, f"runner executed the live repository path: {exe}"
        assert not Path(exe).exists(), f"snapshot was not cleaned up after the run: {exe}"
        print("PASS runner_snapshot exec_from=private_0500_snapshot removed_after_run")


def prove_runner_fds_read_only() -> None:
    # B2 residual: the manifest/allowlist/receipt descriptors the runner
    # inherits must be read-only, so the runner cannot rewrite the bytes it was
    # handed. The runner attempts a one-byte write to each and reports whether
    # it was rejected.
    with tempfile.TemporaryDirectory(prefix="r03c-ro-fds-") as temp:
        fixture = Fixture(TOKEN, Path(temp) / "fixture")
        fixture.issue()
        assert fixture.run() == 0
        writable = _last_runner_record(fixture)["writable"]
        assert writable == {"manifest": False, "allowlist": False, "receipt": False}, \
            f"runner descriptors are not all read-only: {writable}"
        print("PASS runner_fds read_only manifest+allowlist+receipt")


def prove_finally_preserves_abort() -> None:
    # B3: an AO reviewer abort landing mid-run must survive the run's finally,
    # which previously overwrote it with "run_finally" — destroying the only
    # durable record of the abort.
    with tempfile.TemporaryDirectory(prefix="r03c-finally-abort-") as temp:
        root = Path(temp)
        ready = root / "ready"
        fixture = Fixture(TOKEN, root / "fixture", sleep_seconds=6, ready_file=ready)
        cli_env = os.environ.copy()
        for key in TOKEN.NATIVE_SESSION_KEYS:
            cli_env.pop(key, None)
        cli_env["CODEX_THREAD_ID"] = "finally-abort-session"
        operator = TOKEN.operator_binding_from_env(cli_env)
        fixture.issue(operator=operator, ttl=60, now=time.time_ns())
        command = [
            sys.executable, str(SCRIPT), "run",
            "--store", str(fixture.store), "--token-file", str(fixture.token_file),
            "--manifest", str(fixture.manifest), "--allowlist", str(fixture.allowlist),
            "--repository", str(fixture.repo),
        ]
        process = subprocess.Popen(command, env=cli_env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        deadline = time.monotonic() + 8
        while not ready.exists() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.05)
        if not ready.exists():
            stdout, stderr = process.communicate(timeout=2)
            raise AssertionError(f"runner never reached mid-flight state: rc={process.returncode} {stdout} {stderr}")
        result = TOKEN.revoke_capability(
            store_arg=fixture.store, token_file_arg=fixture.token_file,
            reason="ao_reviewer_abort", now_ns=time.time_ns(), registration_path=REGISTRATION,
        )
        assert result["revocation_reason"] == "ao_reviewer_abort"
        stdout, stderr = process.communicate(timeout=12)
        assert process.returncode == 0, (process.returncode, stdout, stderr)
        status = TOKEN.status_capability(
            store_arg=fixture.store, token_file_arg=fixture.token_file, registration_path=REGISTRATION,
        )
        assert status["state"] == "revoked"
        assert status["revocation_reason"] == "ao_reviewer_abort", status
        assert status["run_finalized_at_ns"] is not None, status
        print("PASS revocation.finally_preserves_abort reason=ao_reviewer_abort run_finalized_recorded")


def prove_atomic_single_use() -> None:
    with tempfile.TemporaryDirectory(prefix="r03c-atomic-") as temp:
        root = Path(temp)
        fixture = Fixture(TOKEN, root / "fixture", sleep_seconds=0.4)
        cli_env = os.environ.copy()
        for key in TOKEN.NATIVE_SESSION_KEYS:
            cli_env.pop(key, None)
        cli_env["CODEX_THREAD_ID"] = "atomic-operator-session"
        fixture.issue(operator=TOKEN.operator_binding_from_env(cli_env), ttl=60, now=time.time_ns())
        command = [
            sys.executable, str(SCRIPT), "run",
            "--store", str(fixture.store), "--token-file", str(fixture.token_file),
            "--manifest", str(fixture.manifest), "--allowlist", str(fixture.allowlist),
            "--repository", str(fixture.repo),
        ]
        contenders = [
            subprocess.Popen(command, env=cli_env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            for _ in range(2)
        ]
        results = [process.communicate(timeout=8) + (process.returncode,) for process in contenders]
        return_codes = sorted(result[2] for result in results)
        assert return_codes == [0, 78], results
        refusal_output = "\n".join(result[1] for result in results if result[2] == 78)
        assert TOKEN.REASON_ALREADY_CONSUMED in refusal_output, results
        print("PASS atomic_single_use contenders=2 admitted=1 refused=already_consumed")


def main() -> int:
    prove_denials_and_mutants()
    prove_remaining_bindings()
    prove_runner_code_binding()
    prove_runner_snapshot_execution()
    prove_runner_fds_read_only()
    prove_atomic_single_use()
    prove_reviewer_abort()
    prove_trap_revocation()
    prove_finally_preserves_abort()
    prove_fail_closed_uncertainty()
    print("settlement-run-token-selftest: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
