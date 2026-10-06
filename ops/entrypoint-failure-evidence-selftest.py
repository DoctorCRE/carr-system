#!/usr/bin/env python3
"""Hermetic checks that a failed deterministic entrypoint leaves evidence.

Five workflows dead-lettered overnight and every dead-letter receipt carried
only ``{"detail": "entrypoint exited 1", "failure_class": "RuntimeError"}`` --
the child's stdout and stderr were captured by subprocess.run and then
discarded. Rule 1f3a7372: an unattended run must record what it FOUND; a
failure receipt that cannot say why is the defect.

This suite is the PAIRED selftest for that fix across three files:
  - lib/secret_redaction.py       the redaction helper, tested standalone
  - lib/control_plane.py          EntrypointFailure, the structured exception
  - tools/control-plane.py        _execute_deterministic raising it with
                                   bounded/redacted tails, and _failure_detail
                                   /run_once landing those tails in the
                                   evidence jsonb ops.fail_job stores
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from lib.secret_redaction import redact_text, redacted_tail, sensitive_env_values  # noqa: E402

SPEC = importlib.util.spec_from_file_location("control_plane_cli", REPO / "tools" / "control-plane.py")
assert SPEC and SPEC.loader
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


def main() -> int:
    failures: list[str] = []
    total = 0

    def check(name: str, condition: bool) -> None:
        nonlocal total
        total += 1
        print(f"  {'ok  ' if condition else 'FAIL'} {name}")
        if not condition:
            failures.append(name)

    # ---- lib/secret_redaction.py, standalone -----------------------------

    postgres_uri = "connecting to postgres://svc_user:S3cretPW1@db.internal:5432/carr now"  # ci-secret-scan: allow — synthetic fixture proving redaction; no real credential
    check("redact_text masks a password-bearing postgres URI",
          "S3cretPW1" not in redact_text(postgres_uri)
          and "db.internal" not in redact_text(postgres_uri)
          and "connecting to [REDACTED] now" == redact_text(postgres_uri))

    token_text = "trace:\nghp_1234567890ABCDEFghijklmnopqrstuvwxyz\nplain stderr line"
    check("redact_text masks a GitHub-shaped token and leaves surrounding text",
          "ghp_1234567890ABCDEFghijklmnopqrstuvwxyz" not in redact_text(token_text)  # ci-secret-scan: allow — synthetic fixture proving redaction; no real credential
          and "plain stderr line" in redact_text(token_text))

    fine_grained_token = "github" + "_pat_" + ("Ab9_" * 20) + "Z9"
    check("redact_text masks a fine-grained GitHub token without known secrets",
          redact_text(f"Authorization: token {fine_grained_token}\nHTTP 401")
          == "Authorization: token [REDACTED]\nHTTP 401")
    check("redacted_tail masks a fine-grained token crossing the cutoff",
          redacted_tail(f"Authorization: token {fine_grained_token}\nHTTP 401", limit=50)
          == "Authorization: token [REDACTED]\nHTTP 401")

    openai_text = "provider said: sk-abcdefghijklmnopqrstuvwxyz012345 was rejected"  # ci-secret-scan: allow — synthetic fixture proving redaction; no real credential
    check("redact_text masks an sk- prefixed key",
          "sk-abcdefghijklmnopqrstuvwxyz012345" not in redact_text(openai_text))  # ci-secret-scan: allow — synthetic fixture proving redaction; no real credential

    assigned_secret = "PASSWORD=deadbeefdeadbeefdeadbeefdeadbeef1234 more diagnostic text"
    check("redact_text masks a long value assigned after '=' but keeps trailing prose",
          "deadbeefdeadbeefdeadbeefdeadbeef1234" not in redact_text(assigned_secret)
          and "more diagnostic text" in redact_text(assigned_secret))

    colon_secret = "token: 0123456789abcdef0123456789abcdef01234567 accepted"
    check("redact_text masks a long value assigned after ':'",
          "0123456789abcdef0123456789abcdef01234567" not in redact_text(colon_secret))

    ordinary = "notes-sweep: scanned=12 unposted=3 failed=0 duration_ms=418"
    check("redact_text leaves ordinary diagnostic text with no secret shape untouched",
          redact_text(ordinary) == ordinary)

    known_env = {"CARR_DB_JOBS_URL": "postgres://runner-known/isolated-value",
                 "PATH": "/usr/bin", "SHORT_TOKEN": "abc",
                 "CARR_AI_ROUTE_PRIMARY_TOKEN": "runner-known-secret-token-value"}
    check("sensitive_env_values selects only credential-named, non-trivial values",
          set(sensitive_env_values(known_env)) ==
          {"postgres://runner-known/isolated-value", "runner-known-secret-token-value"})
    check("redact_text masks a literal known-secret value with no generic pattern shape",
          redact_text("child echoed runner-known-secret-token-value verbatim",
                      known_secrets=sensitive_env_values(known_env))
          == "child echoed [REDACTED] verbatim")

    check("redacted_tail caps at the requested length",
          len(redacted_tail("x" * 5000, limit=2000)) == 2000
          and redacted_tail("x" * 5000, limit=2000) == "x" * 2000)
    check("redacted_tail returns empty text unchanged",
          redacted_tail(None) == "" and redacted_tail("") == "")

    # A secret that straddles the truncation boundary must never leak a
    # partial credential: redact BEFORE bounding, never after.
    straddling = ("x" * 1990) + "postgres://svc:S3cretPW1@db.internal:5432/carr" + ("y" * 50)  # ci-secret-scan: allow — synthetic fixture proving redaction; no real credential
    boundary_tail = redacted_tail(straddling, limit=2000)
    check("redacted_tail never leaks half a secret that straddles the cut point",
          "S3cretPW1" not in boundary_tail and "svc" not in boundary_tail
          and boundary_tail.endswith("[REDACTED]"))

    # ---- tools/control-plane.py: _execute_deterministic on nonzero exit --

    original_run = module.subprocess.run
    secret_stdout = ("normal line one\n"
                      "postgres://svc_user:S3cretPW1@db.internal:5432/carr\n"  # ci-secret-scan: allow — synthetic fixture proving redaction; no real credential
                      "normal line two")
    secret_stderr = ("Traceback (most recent call last):\n"
                      "ghp_1234567890ABCDEFghijklmnopqrstuvwxyz\n"  # ci-secret-scan: allow — synthetic fixture proving redaction; no real credential
                      "  raise RuntimeError('boom')")

    def failing_run(argv, **_kwargs):
        return subprocess.CompletedProcess(argv, 7, secret_stdout, secret_stderr)

    module.subprocess.run = failing_run
    try:
        workflow = {"execution": {"entrypoint": "bin/nightly.sh", "args": [],
                                  "shadow_args": [], "canary_args": []}}
        try:
            module._execute_deterministic(workflow, {}, 30, "live")
            entrypoint_failure = None
        except module.EntrypointFailure as exc:
            entrypoint_failure = exc
    finally:
        module.subprocess.run = original_run
    check("a nonzero exit raises EntrypointFailure carrying the exit code",
          entrypoint_failure is not None and entrypoint_failure.returncode == 7
          and str(entrypoint_failure) == "entrypoint exited 7")
    check("EntrypointFailure records both stdout and stderr tails",
          entrypoint_failure is not None
          and "normal line one" in entrypoint_failure.stdout_tail
          and "normal line two" in entrypoint_failure.stdout_tail
          and "raise RuntimeError" in entrypoint_failure.stderr_tail)
    check("EntrypointFailure's tails are redacted before storage",
          entrypoint_failure is not None
          and "S3cretPW1" not in entrypoint_failure.stdout_tail
          and "ghp_1234567890ABCDEFghijklmnopqrstuvwxyz" not in entrypoint_failure.stderr_tail  # ci-secret-scan: allow — synthetic fixture proving redaction; no real credential
          and "[REDACTED]" in entrypoint_failure.stdout_tail
          and "[REDACTED]" in entrypoint_failure.stderr_tail)

    def huge_failing_run(argv, **_kwargs):
        return subprocess.CompletedProcess(argv, 1, "O" * 5000, "E" * 5000)

    module.subprocess.run = huge_failing_run
    try:
        try:
            module._execute_deterministic(workflow, {}, 30, "live")
            huge_failure = None
        except module.EntrypointFailure as exc:
            huge_failure = exc
    finally:
        module.subprocess.run = original_run
    check("EntrypointFailure caps each tail at ~2000 characters",
          huge_failure is not None
          and len(huge_failure.stdout_tail) == 2000 and len(huge_failure.stderr_tail) == 2000)

    # ---- tools/control-plane.py: success path stays byte-identical -------

    def success_run(argv, **_kwargs):
        return subprocess.CompletedProcess(argv, 0, "O" * 5000, "unused stderr")

    module.subprocess.run = success_run
    try:
        success_evidence = module._execute_deterministic(workflow, {}, 30, "live")
    finally:
        module.subprocess.run = original_run
    check("success evidence is exactly the four registered fields, unredacted stdout_tail",
          success_evidence == {"entrypoint": "bin/nightly.sh", "mode": "live", "args": [],
                               "exit_code": 0, "stdout_tail": "O" * 2000})

    # ---- tools/control-plane.py: _failure_detail ---------------------------

    plain_exc = RuntimeError("x" * 1500)
    check("_failure_detail keeps the existing 1000-char cap for ordinary exceptions",
          module._failure_detail(plain_exc) == "x" * 1000)

    entry_exc = module.EntrypointFailure(
        3, stdout_tail="stdout-tail-marker", stderr_tail="stderr-tail-marker")
    detail_json = module._failure_detail(entry_exc)
    decoded = json.loads(detail_json)
    check("_failure_detail encodes EntrypointFailure as JSON with both tails intact",
          decoded == {"message": "entrypoint exited 3", "stdout_tail": "stdout-tail-marker",
                     "stderr_tail": "stderr-tail-marker"})
    long_entry_exc = module.EntrypointFailure(
        1, stdout_tail="O" * 2000, stderr_tail="E" * 2000)
    check("_failure_detail does not truncate EntrypointFailure's full-length tails to 1000 chars",
          len(module._failure_detail(long_entry_exc)) > 1000
          and json.loads(module._failure_detail(long_entry_exc))["stdout_tail"] == "O" * 2000)

    # ---- run_once end to end: the tails land in the stored evidence jsonb --

    claim_row: tuple = (
        "00000000-0000-0000-0000-0000000000ab",  # job_id
        "00000000-0000-0000-0000-0000000000cd",  # lease_token
        "entrypoint-failure-fixture",             # definition_key
        1,                                        # definition_version
        {"scheduled_for": "2026-08-26T00:00:00+00:00"},  # payload
        "deterministic",                          # execution_kind
        {},                                        # execution_contract
        1,                                         # attempt
        30,                                        # timeout_seconds
        "live",                                    # mode
    )
    claim_columns = ("job_id", "lease_token", "definition_key", "definition_version",
                     "payload", "execution_kind", "execution_contract", "attempt",
                     "timeout_seconds", "mode")

    class _Col:
        def __init__(self, name):
            self.name = name

    class RunOnceCursor:
        description = [_Col(name) for name in claim_columns]

        def __init__(self, conn):
            self.conn = conn
            self._last = ()

        def __enter__(self): return self
        def __exit__(self, *_args): return False

        def execute(self, sql, args=()):
            self.conn.calls.append((sql, args))
            if sql.startswith("select * from ops.claim_job("):
                self._last = claim_row
            elif sql.startswith("select ops.fail_job("):
                self.conn.fail_job_calls.append(args)
                self._last = ("dead_lettered",)
            else:
                raise AssertionError(f"unexpected SQL in fixture: {sql}")

        def fetchone(self):
            return self._last

    class RunOnceConnection:
        def __init__(self):
            self.calls: list = []
            self.fail_job_calls: list = []
            self.commits = 0
            self.rollbacks = 0

        def __enter__(self): return self
        def __exit__(self, *_args): return False

        def cursor(self):
            return RunOnceCursor(self)

        def commit(self):
            self.commits += 1

        def rollback(self):
            self.rollbacks += 1

    run_once_conn = RunOnceConnection()
    manifest = {"workflows": [{
        "key": "entrypoint-failure-fixture", "version": 1,
        # A REAL, registered file: _execute_deterministic resolves and
        # is_file()-checks the entrypoint before it ever reaches the mocked
        # subprocess.run below, so this must exist inside REPO.
        "execution": {"kind": "deterministic", "entrypoint": "bin/nightly.sh",
                      "args": []},
    }]}

    original_connect = getattr(module, "connect")
    original_evaluate_stage = getattr(module, "evaluate_stage")
    setattr(module, "connect", lambda: run_once_conn)
    setattr(module, "evaluate_stage", lambda *_a, **_k: True)  # routing/filtering/validation trivially pass
    module.subprocess.run = failing_run
    try:
        result = module.run_once(manifest, "worker")
    finally:
        setattr(module, "connect", original_connect)
        setattr(module, "evaluate_stage", original_evaluate_stage)
        module.subprocess.run = original_run

    check("run_once reports the dead-lettered state and failure_class from ops.fail_job",
          result.get("state") == "dead_lettered"
          and result.get("failure_class") == "EntrypointFailure"
          and run_once_conn.rollbacks == 1)
    check("run_once calls ops.fail_job with the job's own id, lease and failure class",
          len(run_once_conn.fail_job_calls) == 1
          and run_once_conn.fail_job_calls[0][0] == claim_row[0]
          and run_once_conn.fail_job_calls[0][1] == claim_row[1]
          and run_once_conn.fail_job_calls[0][2] == "EntrypointFailure")

    stored_detail = (run_once_conn.fail_job_calls[0][3]
                     if run_once_conn.fail_job_calls else "")
    stored = json.loads(stored_detail) if stored_detail else {}
    check("the p_detail landed in ops.fail_job is valid JSON carrying both tails",
          stored.get("message") == "entrypoint exited 7"
          and "normal line one" in stored.get("stdout_tail", "")
          and "raise RuntimeError" in stored.get("stderr_tail", ""))
    check("the tails stored via ops.fail_job are redacted, not raw child output",
          "S3cretPW1" not in stored_detail
          and "ghp_1234567890ABCDEFghijklmnopqrstuvwxyz" not in stored_detail)  # ci-secret-scan: allow — synthetic fixture proving redaction; no real credential

    # ---- exit 78 (EX_CONFIG): NOT A FAILURE ------------------------------
    #
    # Measured 2026-08-27: notes-sweep-hourly's canary exits 78 every window
    # because ~/.config/carr/notes-canary.env has never existed on this Mac.
    # bin/nightly.sh and every other launchd chain already read 78 as SKIP,
    # never FAIL (this repo's standing EX_CONFIG convention); run_once did
    # not, and burned the job's retry budget on a gap no retry could close.
    # Rule 88e9b5eb: "not authorized" and "not possible" are different
    # findings and must never be reported as the same one.

    def not_configured_run(argv, **_kwargs):
        return subprocess.CompletedProcess(
            argv, 78, "notes-sweep: probing config\n",
            "notes canary config must be 0600")

    module.subprocess.run = not_configured_run
    try:
        try:
            module._execute_deterministic(workflow, {}, 30, "live")
            not_configured = None
        except module.EntrypointNotConfigured as exc:
            not_configured = exc
    finally:
        module.subprocess.run = original_run
    check("exit 78 raises EntrypointNotConfigured, not the generic EntrypointFailure",
          not_configured is not None and type(not_configured) is module.EntrypointNotConfigured)
    check("EntrypointNotConfigured is still an EntrypointFailure (shared tail-capture code, not a duplicate)",
          not_configured is not None and isinstance(not_configured, module.EntrypointFailure))
    check("EntrypointNotConfigured records the fixed EX_CONFIG return code",
          not_configured is not None and not_configured.returncode == 78)
    check("EntrypointNotConfigured carries the entrypoint's own message on its stderr tail",
          not_configured is not None
          and "notes canary config must be 0600" in not_configured.stderr_tail)

    def secret_not_configured_run(argv, **_kwargs):
        return subprocess.CompletedProcess(
            argv, 78, "normal probe line",
            "postgres://svc_user:S3cretPW1@db.internal:5432/carr\nconfig missing")  # ci-secret-scan: allow — synthetic fixture proving redaction; no real credential

    module.subprocess.run = secret_not_configured_run
    try:
        try:
            module._execute_deterministic(workflow, {}, 30, "live")
            not_configured_secret = None
        except module.EntrypointNotConfigured as exc:
            not_configured_secret = exc
    finally:
        module.subprocess.run = original_run
    check("EntrypointNotConfigured's tails are redacted before storage, exactly like any other exit code",
          not_configured_secret is not None
          and "S3cretPW1" not in not_configured_secret.stderr_tail  # ci-secret-scan: allow — synthetic fixture proving redaction; no real credential
          and "[REDACTED]" in not_configured_secret.stderr_tail)

    # Regression guard for the special-case added above: an ordinary failure
    # (exit 1) must still raise the plain EntrypointFailure and go through
    # the unchanged ops.fail_job path, never ops.skip_job.
    def exit1_run(argv, **_kwargs):
        return subprocess.CompletedProcess(argv, 1, "", "boom")

    module.subprocess.run = exit1_run
    try:
        try:
            module._execute_deterministic(workflow, {}, 30, "live")
            plain_failure = None
        except module.EntrypointFailure as exc:
            plain_failure = exc
    finally:
        module.subprocess.run = original_run
    check("exit 1 still raises the plain EntrypointFailure, never EntrypointNotConfigured",
          plain_failure is not None and type(plain_failure) is module.EntrypointFailure
          and plain_failure.returncode == 1)

    not_configured_detail = module._failure_detail(not_configured) if not_configured else ""
    not_configured_decoded = json.loads(not_configured_detail) if not_configured_detail else {}
    check("_failure_detail encodes EntrypointNotConfigured as JSON with both tails, same shape as EntrypointFailure",
          not_configured_decoded.get("message") == "entrypoint exited 78 (not configured)"
          and "notes canary config must be 0600" in not_configured_decoded.get("stderr_tail", ""))

    # ---- run_once end to end: exit 78 lands as skipped, not dead-lettered --

    class SkipRunOnceCursor(RunOnceCursor):
        def execute(self, sql, args=()):
            self.conn.calls.append((sql, args))
            if sql.startswith("select * from ops.claim_job("):
                self._last = claim_row
            elif sql.startswith("select ops.skip_job("):
                self.conn.skip_job_calls.append(args)
                self._last = ("skipped",)
            elif sql.startswith("select ops.fail_job("):
                self.conn.fail_job_calls.append(args)
                self._last = ("dead_lettered",)
            else:
                raise AssertionError(f"unexpected SQL in fixture: {sql}")

    class SkipRunOnceConnection(RunOnceConnection):
        def __init__(self):
            super().__init__()
            self.skip_job_calls: list = []

        def cursor(self):
            return SkipRunOnceCursor(self)

    skip_conn = SkipRunOnceConnection()
    original_connect = getattr(module, "connect")
    original_evaluate_stage = getattr(module, "evaluate_stage")
    setattr(module, "connect", lambda: skip_conn)
    setattr(module, "evaluate_stage", lambda *_a, **_k: True)
    module.subprocess.run = not_configured_run
    try:
        skip_result = module.run_once(manifest, "worker")
    finally:
        setattr(module, "connect", original_connect)
        setattr(module, "evaluate_stage", original_evaluate_stage)
        module.subprocess.run = original_run

    check("run_once reports the skipped state and EntrypointNotConfigured class for exit 78, never dead_lettered",
          skip_result.get("state") == "skipped"
          and skip_result.get("failure_class") == "EntrypointNotConfigured"
          and skip_conn.rollbacks == 1)
    check("run_once routes exit 78 through ops.skip_job, never ops.fail_job -- no retry budget spent, no dead-letter",
          len(skip_conn.skip_job_calls) == 1 and not skip_conn.fail_job_calls
          and skip_conn.skip_job_calls[0][0] == claim_row[0]
          and skip_conn.skip_job_calls[0][1] == claim_row[1])

    skipped_stored_detail = (skip_conn.skip_job_calls[0][2]
                             if skip_conn.skip_job_calls else "")
    skipped_stored = json.loads(skipped_stored_detail) if skipped_stored_detail else {}
    check("the not-configured message lands in ops.skip_job's p_detail as valid, redacted JSON",
          skipped_stored.get("message") == "entrypoint exited 78 (not configured)"
          and "notes canary config must be 0600" in skipped_stored.get("stderr_tail", ""))

    print(f"\nentrypoint-failure-evidence-selftest: {total-len(failures)}/{total} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
