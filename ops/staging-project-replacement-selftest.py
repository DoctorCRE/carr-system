#!/usr/bin/env python3
"""Hermetic lifecycle/parity contract for clean staging replacement."""
from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import re
import subprocess
import sys
import types
import uuid
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


ctl = load("staging_project_replacement", ROOT / "tools/staging-project-replacement.py")
fixture = load("staging_fixtures", ROOT / "tools/staging-fixtures.py")
manifest = load("replacement_release_manifest", ROOT / "tools/release-manifest.py")
sql_source = (ROOT / "migrations/0322_clean_staging_replacement_contract.sql").read_text()
controller_source = (ROOT / "tools/staging-project-replacement.py").read_text()


def refuses(call, contains: str) -> None:
    try:
        call()
    except (ctl.ReplacementRefusal, fixture.FixtureRefusal,
            ctl.credential_helper.CredentialRefusal, SystemExit) as exc:
        assert contains in str(exc), (contains, str(exc))
    else:
        raise AssertionError(f"expected refusal containing {contains!r}")


def sql_expected_keys(function_name: str) -> frozenset[str]:
    start = sql_source.index(f"function ops.{function_name}(")
    block = sql_source[start:sql_source.index("begin", start)]
    raw = block[block.index("expected_keys constant text[] := array["):]
    raw = raw[:raw.index("];")]
    return frozenset(re.findall(r"'([a-z0-9_]+)'", raw))


# Python <-> SQL parity is exact, not substring presence elsewhere in SQL.
assert ctl.PREPARE_KEYS == sql_expected_keys("prepare_staging_replacement_project")
assert ctl.OBSERVATION_KEYS == sql_expected_keys("record_staging_replacement_project")
assert ctl.CONTRACT_MIGRATION == "0322_clean_staging_replacement_contract.sql"
assert (ROOT / "migrations" / ctl.CONTRACT_MIGRATION).is_file()
assert "clean-staging-replacement-contract.v1" in sql_source
assert "clean-staging-replacement-observation.v1" in sql_source
db_gate_source = (ROOT / "ops/staging-replacement-project-local-pg-gate.py").read_text()
assert "prepare is not exact-replay idempotent" in db_gate_source
assert "changed preparation replay was accepted" in db_gate_source
prepare_block = controller_source[
    controller_source.index("def prepare_candidate("):
    controller_source.index("def parse_args(")]
assert prepare_block.index("read_production_overlap") < \
       prepare_block.index("call_json(cur, PREPARE_FUNCTION")


# Full operation UUID and source-controlled provider profile.
operation_id = uuid.UUID("11111111-2222-4333-8444-555555555555")
assert ctl.candidate_name(operation_id).endswith(operation_id.hex)
assert len(ctl.candidate_name(operation_id)) == len("carr-staging-replacement-") + 32
assert ctl.load_candidate_spec() == ctl.CandidateSpec(18, "aws-us-east-1",
                                                      ctl.Decimal("0.25"), ctl.Decimal("8"))


class ScopeRun:
    def __init__(self, project_bound: bool = True, endpoint_bound: bool = True,
                 branch_name: str = "main"):
        self.project_bound = project_bound
        self.endpoint_bound = endpoint_bound
        self.branch_name = branch_name

    def __call__(self, args, **unused):
        if "branches" in args:
            payload = {"branches": [{"id": "br-candidate", "name": self.branch_name, "default": True,
                         "project_id": "candidate" if self.project_bound else "wrong"}]}
        else:
            payload = {"endpoints": [{"id": "ep-candidate", "host": "ep-candidate.neon.tech",
                         "branch_id": "br-candidate" if self.endpoint_bound else "wrong",
                         "type": "read_write", "autoscaling_limit_min_cu": 0.25,
                         "autoscaling_limit_max_cu": 8}]}
        return subprocess.CompletedProcess(args, 0, json.dumps(payload), "")


project = {"id": "candidate", "name": ctl.candidate_name(operation_id),
           "pg_version": 18, "region_id": "aws-us-east-1"}
env = {"NEON_API_KEY": "fixture-key", "PATH": "/bin"}
scope = ctl.resolve_scope(project, expected_branch_name="main", run=ScopeRun(), environ=env)
assert scope.project_id == "candidate" and scope.branch_id == "br-candidate"
production_scope = ctl.resolve_scope(
    project, expected_branch_name="production", run=ScopeRun(branch_name="production"), environ=env)
assert production_scope.branch_id == "br-candidate"
refuses(lambda: ctl.resolve_scope(project, expected_branch_name="main",
                                  run=ScopeRun(branch_name="production"), environ=env),
        "default branch")
refuses(lambda: ctl.resolve_scope(project, expected_branch_name="main",
                                  run=ScopeRun(project_bound=False), environ=env),
        "default branch")
refuses(lambda: ctl.resolve_scope(project, expected_branch_name="main",
                                  run=ScopeRun(endpoint_bound=False), environ=env),
        "endpoint")


class StateCursor:
    def __init__(self, rows, table_count=10, has_ledger=True):
        self.rows, self.table_count, self.has_ledger = rows, table_count, has_ledger
        self.statement = ""

    def __enter__(self): return self
    def __exit__(self, *unused): return False
    def execute(self, statement, params=()): self.statement = statement
    def fetchone(self):
        if "server_version_num" in self.statement: return (ctl.OWNER_ROLE, "neondb", 180001)
        if "count(*) from pg_class" in self.statement: return (self.table_count,)
        if "to_regclass" in self.statement: return ("schema_migrations",) if self.has_ledger else (None,)
        raise AssertionError(self.statement)
    def fetchall(self):
        assert "schema_migrations" in self.statement
        return self.rows


class StateConnection:
    def __init__(self, cursor): self.value = cursor
    def __enter__(self): return self
    def __exit__(self, *unused): return False
    def cursor(self): return self.value


source_manifest = {"migration_ledger": {"0320_a.sql": "a"*64, "0322_b.sql": "b"*64}}
source = ctl.SourceContract("c"*40, source_manifest)
owner_scope = ctl.ProviderScope("candidate", ctl.candidate_name(operation_id), "br-candidate",
                                "ep-candidate", "ep-candidate.neon.tech")
owner = ctl.SecretDsn(owner_scope,
    "postgresql://neondb_owner:secret@ep-candidate.neon.tech/neondb")  # ci-secret-scan: allow
connect_for = lambda cursor: (lambda _dsn: StateConnection(cursor))

# Registry bootstrap is an owner operation; the routine jobs credential must
# never be mislabeled as its DATABASE_URL or leak into the child environment.
sync_calls: list[tuple[list[str], dict[str, str]]] = []
def capture_sync(args, **kwargs):
    sync_calls.append((args, kwargs["env"]))
    return subprocess.CompletedProcess(args, 0, "{}", "")
ctl.sync_control_plane(owner, run=capture_sync, environ={"PATH": "/bin"})
assert len(sync_calls) == 2
assert sync_calls[0][0][-1] == "sync"
assert sync_calls[0][1]["DATABASE_URL"] == owner.value
assert "CARR_DB_JOBS_URL" not in sync_calls[0][1]
assert sync_calls[1][1]["DATABASE_URL"] == owner.value

assert ctl.candidate_reconstruction_state(
    owner, source, connect=connect_for(StateCursor([], table_count=0))) == "empty"
assert ctl.candidate_reconstruction_state(
    owner, source, connect=connect_for(StateCursor([("0320_a.sql", "a"*64)]))) == "prefix"
assert ctl.candidate_reconstruction_state(
    owner, source, connect=connect_for(StateCursor(list(source_manifest["migration_ledger"].items())))) \
    == "reconstructed"
refuses(lambda: ctl.candidate_reconstruction_state(
    owner, source, connect=connect_for(StateCursor([("0321_wrong.sql", "f"*64)]))),
    "exact source prefix")
refuses(lambda: ctl.candidate_reconstruction_state(
    owner, source, connect=connect_for(StateCursor([], has_ledger=False))), "no migration ledger")


class ReadOnlyCursor:
    def __init__(self, read_only="on"):
        self.statement = ""; self.read_only = read_only
    def __enter__(self): return self
    def __exit__(self, *unused): return False
    def execute(self, statement, params=()): self.statement = statement
    def fetchone(self):
        return (self.read_only,) if "transaction_read_only" in self.statement else (0,)


class ReadOnlyConnection:
    def __init__(self, value="on"): self.read_only = False; self.value = ReadOnlyCursor(value)
    def __enter__(self): return self
    def __exit__(self, *unused): return False
    def cursor(self): return self.value


production = ReadOnlyConnection()
assert ctl.production_overlap_count({table: () for table in ctl.CLIENT_TABLES}, production) == 0
assert production.read_only is True
refuses(lambda: ctl.production_overlap_count({table: () for table in ctl.CLIENT_TABLES},
                                              ReadOnlyConnection("off")), "not read-only")

class OwnerReadRun:
    def __init__(self): self.calls = []
    def __call__(self, args, **unused):
        self.calls.append(args)
        value = "postgresql://neondb_owner:secret@ep-production.neon.tech/neondb"  # ci-secret-scan: allow
        return subprocess.CompletedProcess(args, 0, value, "")

production_scope = ctl.ProviderScope(
    ctl.PRODUCTION_PROJECT_ID, "production", "br-production", "ep-production",
    "ep-production.neon.tech")
owner_read_run = OwnerReadRun()
owner_read_connection = ReadOnlyConnection()
assert ctl.read_production_overlap(
    production_scope, {table: () for table in ctl.CLIENT_TABLES},
    run=owner_read_run, environ=env,
    connect=lambda _dsn: owner_read_connection) == 0
assert owner_read_connection.read_only is True
assert owner_read_run.calls[0][owner_read_run.calls[0].index("--role-name") + 1] == ctl.OWNER_ROLE


# Provider-bound fixture door: declarations must match all three live objects.
class FixtureProviderRun:
    def __init__(self, wrong_host=False): self.calls = []; self.wrong_host = wrong_host
    def __call__(self, args, **unused):
        self.calls.append(args)
        if "projects" in args:
            payload = {"projects": [{"id": "candidate", "name": "carr-staging-replacement-abc"}]}
        elif "branches" in args:
            payload = {"branches": [{"id": "br-candidate", "name": "main", "default": True,
                                      "project_id": "candidate"}]}
        else:
            payload = {"endpoints": [{"id": "ep-candidate", "branch_id": "br-candidate",
                "type": "read_write", "host": "wrong.neon.tech" if self.wrong_host
                else "ep-candidate.neon.tech"}]}
        return subprocess.CompletedProcess(args, 0, json.dumps(payload), "")


fixture_dsn = "postgresql://neondb_owner:secret@ep-candidate.neon.tech/neondb"  # ci-secret-scan: allow
fixture.validate_target(fixture_dsn, "candidate", "ep-candidate")
provider_run = FixtureProviderRun()
fixture.validate_provider_binding(fixture_dsn, "candidate", "ep-candidate",
                                  run=provider_run, environ=env)
assert len(provider_run.calls) == 3
refuses(lambda: fixture.validate_provider_binding(fixture_dsn, "candidate", "ep-candidate",
        run=FixtureProviderRun(wrong_host=True), environ=env), "does not own")
refuses(lambda: fixture.validate_target(fixture_dsn, ctl.PRODUCTION_PROJECT_ID, "ep-candidate"),
        "forbidden")


# Strict receipt projection: any omitted durable field fails.
expected_receipt = {"contract_id": str(uuid.uuid4()), "receipt_id": str(uuid.uuid4()),
                    "evidence_ref": "ops.staging-replacement-project:sha256:" + "d"*64,
                    "prior_staging_project_id": "old", "replacement_project_id": "new"}
good_receipt = {**expected_receipt, "receipt_sha256": "sha256:" + "e"*64,
                "observed_at": "2026-08-25T12:00:00Z"}
ctl.validate_receipt_readback(good_receipt, expected_receipt)
for missing in expected_receipt:
    changed = dict(good_receipt); changed.pop(missing)
    refuses(lambda changed=changed: ctl.validate_receipt_readback(changed, expected_receipt),
            "readback disagrees")


# Final provider preservation is behavioral, not an output-string assertion.
saved_resolver = ctl.resolve_existing_scopes
try:
    prod = ctl.ProviderScope("prod", "production", "br-p", "ep-p", "ep-p.neon.tech")
    old = ctl.ProviderScope("old", "carr-staging", "br-o", "ep-o", "ep-o.neon.tech")
    candidate = owner_scope
    ctl.resolve_existing_scopes = lambda *args, **kwargs: (prod, old, candidate)
    ctl.prove_provider_preservation(operation_id, prod, old, candidate, run=lambda *a, **k: None,
                                    environ={})
    ctl.resolve_existing_scopes = lambda *args, **kwargs: (prod, old,
        ctl.ProviderScope("other", candidate.project_name, candidate.branch_id,
                          candidate.endpoint_id, candidate.endpoint_host))
    refuses(lambda: ctl.prove_provider_preservation(operation_id, prod, old, candidate,
             run=lambda *a, **k: None, environ={}), "changed")
finally:
    ctl.resolve_existing_scopes = saved_resolver


# Secrets are captured/suppressed and unrelated ambient credentials never reach children.
secret = "do-not-leak"
refuses(lambda: ctl._success(subprocess.CompletedProcess([], 9, secret, secret), "provider"),
        "output suppressed")
assert secret not in repr(ctl.SecretDsn(owner_scope, secret))
sanitized = ctl.safe_environment({"PATH": "/bin", "HOME": "/tmp/home", "GH_TOKEN": secret,
                                  "DATABASE_URL": secret, "OPENAI_API_KEY": secret})
assert sanitized == {"PATH": "/bin", "HOME": "/tmp/home"}
assert "os.write(" not in controller_source and "O_EXCL" not in controller_source
assert "credential_helper.prepare_pending" in controller_source
assert "credential_helper.exclusive_lock" in controller_source
assert 'psql_bin, "--single-transaction"' in controller_source
assert '"projects", "update"' not in controller_source
assert '"projects", "delete"' not in controller_source


# Full-tree tuple encoding terminates every field with NUL, including newline-bearing paths.
entries = [("100644", "blob", "f"*40, "line\nbreak")]
material = b"100644\0blob\0" + (b"f"*40) + b"\0line\nbreak\0"
assert manifest.digest_full_tree(entries) == "sha256:" + hashlib.sha256(material).hexdigest()
contract = manifest.source_contract("HEAD")
assert contract["source_tree_sha256"] != contract["artifact_sha256"]
assert contract["migration_highest"] == next(reversed(contract["migration_ledger"]))
assert ctl.CONTRACT_MIGRATION in contract["migration_ledger"]

# The replacement reconstructs the complete requested source tree, including
# migrations merged after the receipt contract itself.
future_manifest = dict(contract)
future_manifest["migration_ledger"] = dict(contract["migration_ledger"])
future_filename = "9999_future_source.sql"
future_manifest["migration_ledger"][future_filename] = "f" * 64
future_manifest["migration_count"] = len(future_manifest["migration_ledger"])
future_manifest["migration_highest"] = future_filename
future_material = "".join(f"{name}\0{digest}\n"
                          for name, digest in future_manifest["migration_ledger"].items())
future_manifest["migration_ledger_sha256"] = (
    "sha256:" + hashlib.sha256(future_material.encode()).hexdigest())
future_source = ctl.SourceContract("a" * 40, future_manifest)
migration_calls: list[list[str]] = []
def capture_migration(args, **_kwargs):
    migration_calls.append(args)
    return subprocess.CompletedProcess(args, 0, "", "")
ctl.apply_candidate_migrations(
    owner, future_source,
    run=capture_migration,
    environ={"PATH": "/bin"})
assert migration_calls and migration_calls[0][-2:] == ["--through", future_filename]
prior_scope = ctl.ProviderScope("old", "carr-staging", "br-old", "ep-old", "ep-old.neon.tech")
future_payload = ctl.prepare_payload(future_source, prior_scope, owner_scope, 1)
assert future_payload["migration_highest"] == future_filename
assert future_payload["migration_count"] == len(future_manifest["migration_ledger"])

def copied_future_manifest():
    copied = dict(future_manifest)
    copied["migration_ledger"] = dict(future_manifest["migration_ledger"])
    return copied

stale_digest = copied_future_manifest()
stale_digest["migration_ledger_sha256"] = contract["migration_ledger_sha256"]
refuses(lambda: ctl.validate_migration_ledger(stale_digest), "boundary")
bad_filename = copied_future_manifest()
bad_filename["migration_ledger"]["0324 NOT-CANONICAL.sql"] = "a" * 64
refuses(lambda: ctl.validate_migration_ledger(bad_filename), "canonical")
bad_value = copied_future_manifest()
bad_value["migration_ledger"][ctl.CONTRACT_MIGRATION] = "not-a-sha"
refuses(lambda: ctl.validate_migration_ledger(bad_value), "canonical")
reordered = copied_future_manifest()
reordered["migration_ledger"] = dict(reversed(list(reordered["migration_ledger"].items())))
refuses(lambda: ctl.validate_migration_ledger(reordered), "canonical")
wrong_count = copied_future_manifest()
wrong_count["migration_count"] += 1
refuses(lambda: ctl.validate_migration_ledger(wrong_count), "boundary")
wrong_highest = copied_future_manifest()
wrong_highest["migration_highest"] = ctl.CONTRACT_MIGRATION
refuses(lambda: ctl.validate_migration_ledger(wrong_highest), "boundary")
missing_contract = copied_future_manifest()
del missing_contract["migration_ledger"][ctl.CONTRACT_MIGRATION]
refuses(lambda: ctl.validate_migration_ledger(missing_contract), "lacks")


# All fallible read-only evidence is complete before the append-only prepare
# write. The DB gate above separately proves exact replay and changed-source refusal.
phase_events = []
saved_phase_functions = {name: getattr(ctl, name) for name in (
    "exact_ledger", "client_row_ids", "prepare_payload", "read_production_overlap",
    "observation_payload", "call_json", "validate_receipt_readback")}
phase_operation_id = uuid.UUID("aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee")
phase_receipt_id = uuid.UUID("12345678-1234-4234-8234-123456789abc")
class PhaseCursor:
    def __enter__(self): return self
    def __exit__(self, *unused): return False
class PhaseConnection:
    def __enter__(self): return self
    def __exit__(self, *unused): return False
    def cursor(self): return PhaseCursor()
    def commit(self): phase_events.append("commit")
def phase_call(_cur, function, _operation, _payload=None):
    if function == ctl.PREPARE_FUNCTION:
        phase_events.append("prepare_write")
        return {"state": "prepared", "contract_id": "contract"}
    if function == ctl.RECORD_FUNCTION:
        phase_events.append("record_write")
        return {"state": "observed", "contract_id": "contract",
                "receipt_id": str(phase_receipt_id),
                "evidence_ref": "ops.staging-replacement-project:sha256:" + "d"*64}
    phase_events.append("receipt_read")
    return {}
def phase_overlap(*_args, **_kwargs):
    phase_events.append("overlap")
    return 0
def phase_observation(*_args, **_kwargs):
    phase_events.append("observation")
    return {"observed": True}
try:
    ctl.exact_ledger = lambda _cur: {key: future_manifest[key] for key in (
        "migration_ledger", "migration_count", "migration_highest", "migration_ledger_sha256")}
    ctl.client_row_ids = lambda _conn: {
        table: (("1",) if table == "party" else ()) for table in ctl.CLIENT_TABLES}
    ctl.prepare_payload = lambda *_args, **_kwargs: {"prepared": True}
    ctl.read_production_overlap = phase_overlap
    ctl.observation_payload = phase_observation
    ctl.call_json = phase_call
    ctl.validate_receipt_readback = lambda *_args, **_kwargs: phase_events.append("validated")
    phase_result = ctl.prepare_candidate(
        future_source, phase_operation_id, production_scope, prior_scope, owner_scope,
        owner, "jobs-dsn", "verifier-dsn", connect=lambda _dsn: PhaseConnection(),
        run=lambda *_args, **_kwargs: None, environ={})
    assert phase_events.index("overlap") < phase_events.index("prepare_write")
    assert phase_events.index("observation") < phase_events.index("prepare_write")
    assert phase_result["receipt_id"] == str(phase_receipt_id)
finally:
    for name, value in saved_phase_functions.items(): setattr(ctl, name, value)


# Same-operation credential retries serialize before touching pending files or roles.
saved_psycopg = ctl.psycopg
saved_psycopg_module = sys.modules.get("psycopg")
saved_lock = ctl.credential_helper.exclusive_lock
lock_secret = "lock-secret-must-not-escape"
lock_entered: list[bool] = []
@contextmanager
def busy_lock(_path):
    lock_entered.append(True)
    raise ctl.credential_helper.CredentialRefusal(lock_secret)
    yield
try:
    fake_psycopg = types.ModuleType("psycopg")
    setattr(fake_psycopg, "sql", types.SimpleNamespace())
    ctl.psycopg = fake_psycopg
    sys.modules["psycopg"] = fake_psycopg
    ctl.credential_helper.exclusive_lock = busy_lock
    refuses(lambda: ctl.provision_scoped_credentials(owner, operation_id, connect=lambda *_a, **_k: None),
            lock_secret)
    assert lock_entered == [True]
finally:
    ctl.psycopg = saved_psycopg
    if saved_psycopg_module is None:
        sys.modules.pop("psycopg", None)
    else:
        sys.modules["psycopg"] = saved_psycopg_module
    ctl.credential_helper.exclusive_lock = saved_lock


# A helper refusal at the real main boundary is concise rc2 and never reflects secret text.
saved = {name: getattr(ctl, name) for name in (
    "validate_source", "load_candidate_spec", "resolve_existing_scopes",
    "validate_candidate_spec", "derive_dsn", "candidate_reconstruction_state",
    "install_fixtures", "provision_scoped_credentials")}
top_secret = "credential-uri-secret-must-not-escape"
try:
    ctl.validate_source = lambda _sha: ctl.SourceContract("a"*40, {})
    ctl.load_candidate_spec = lambda: ctl.CandidateSpec(18, "aws-us-east-1",
        ctl.Decimal("0.25"), ctl.Decimal("8"))
    prod = ctl.ProviderScope(ctl.PRODUCTION_PROJECT_ID, "production", "br-p", "ep-p",
                             "ep-p.neon.tech")
    old = ctl.ProviderScope("old", "carr-staging", "br-o", "ep-o", "ep-o.neon.tech")
    ctl.resolve_existing_scopes = lambda *_a, **_k: (prod, old, owner_scope)
    ctl.validate_candidate_spec = lambda *_a, **_k: None
    ctl.derive_dsn = lambda *_a, **_k: owner
    ctl.candidate_reconstruction_state = lambda *_a, **_k: "reconstructed"
    ctl.install_fixtures = lambda *_a, **_k: None
    ctl.provision_scoped_credentials = lambda *_a, **_k: (
        (_ for _ in ()).throw(ctl.credential_helper.CredentialRefusal(top_secret)))
    ctl.psycopg = types.SimpleNamespace(connect=lambda *_a, **_k: None)
    stderr = io.StringIO()
    with redirect_stderr(stderr):
        rc = ctl.main(["prepare", "--sha", "a"*40, "--operation-id", str(operation_id),
                       "--apply", "--local-checks-green"])
    assert rc == 2 and "REFUSED" in stderr.getvalue()
    assert top_secret not in stderr.getvalue() and "Traceback" not in stderr.getvalue()
finally:
    for name, value in saved.items(): setattr(ctl, name, value)
    ctl.psycopg = saved_psycopg


# Any unexpected database/provider exception is a concise refusal boundary,
# never a traceback that can reflect credentials or provider output.
saved_validate_source = ctl.validate_source
unexpected_secret = "unexpected-database-secret-must-not-escape"
try:
    ctl.validate_source = lambda _sha: (_ for _ in ()).throw(RuntimeError(unexpected_secret))
    stderr = io.StringIO()
    with redirect_stderr(stderr):
        rc = ctl.main(["plan", "--sha", "a"*40, "--operation-id", str(operation_id)])
    assert rc == 2 and "REFUSED" in stderr.getvalue()
    assert unexpected_secret not in stderr.getvalue() and "Traceback" not in stderr.getvalue()
finally:
    ctl.validate_source = saved_validate_source


try:
    ctl.parse_args(["prepare", "--sha", "a"*40, "--operation-id", str(operation_id)])
except SystemExit:
    pass
else:
    raise AssertionError("prepare without explicit apply was accepted")


# A successor receipt reuses the exact existing candidate and can never create
# a second project under the new receipt idempotency key.
receipt_operation_id = uuid.UUID("aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee")
candidate_operation_id = uuid.UUID("11111111-2222-4333-8444-555555555555")
saved = {name: getattr(ctl, name) for name in (
    "validate_source", "load_candidate_spec", "resolve_existing_scopes", "create_candidate",
    "validate_candidate_spec", "derive_dsn", "candidate_reconstruction_state",
    "install_fixtures", "provision_scoped_credentials", "sync_control_plane",
    "prepare_candidate", "prove_provider_preservation")}
saved_psycopg = ctl.psycopg
resolved_operations: list[uuid.UUID] = []
create_calls: list[uuid.UUID] = []
try:
    ctl.validate_source = lambda _sha: ctl.SourceContract("a"*40, {})
    ctl.load_candidate_spec = lambda: ctl.CandidateSpec(
        18, "aws-us-east-1", ctl.Decimal("0.25"), ctl.Decimal("8"))
    prod = ctl.ProviderScope(ctl.PRODUCTION_PROJECT_ID, "production", "br-p", "ep-p",
                             "ep-p.neon.tech")
    old = ctl.ProviderScope("old", "carr-staging", "br-o", "ep-o", "ep-o.neon.tech")
    candidate = ctl.ProviderScope("candidate", ctl.candidate_name(candidate_operation_id),
                                  "br-c", "ep-c", "ep-c.neon.tech")
    def resolve_successor(operation, **_kwargs):
        resolved_operations.append(operation)
        return prod, old, candidate
    ctl.resolve_existing_scopes = resolve_successor
    stdout = io.StringIO()
    with redirect_stdout(stdout):
        rc = ctl.main(["plan", "--sha", "a"*40,
            "--operation-id", str(receipt_operation_id),
            "--candidate-operation-id", str(candidate_operation_id)])
    planned = json.loads(stdout.getvalue())
    assert rc == 0 and resolved_operations == [candidate_operation_id]
    assert planned["receipt_operation_id"] == str(receipt_operation_id)
    assert planned["candidate_operation_id"] == str(candidate_operation_id)
    assert planned["candidate_exists"] is True
    assert planned["standing_project_create_count"] == 0
    ctl.resolve_existing_scopes = lambda *_args, **_kwargs: (prod, old, None)
    ctl.create_candidate = lambda operation, *_args, **_kwargs: create_calls.append(operation)
    stderr = io.StringIO()
    with redirect_stderr(stderr):
        rc = ctl.main(["plan", "--sha", "a"*40,
            "--operation-id", str(receipt_operation_id),
            "--candidate-operation-id", str(candidate_operation_id)])
    assert rc == 2 and "candidate does not exist" in stderr.getvalue()
    assert create_calls == []
    prepare_routing: list[object] = []
    ctl.resolve_existing_scopes = resolve_successor
    ctl.validate_candidate_spec = lambda *_args, **_kwargs: None
    ctl.derive_dsn = lambda *_args, **_kwargs: owner
    ctl.candidate_reconstruction_state = lambda *_args, **_kwargs: "reconstructed"
    ctl.install_fixtures = lambda *_args, **_kwargs: prepare_routing.append("fixtures")
    def provision_for_candidate(_owner, operation, **_kwargs):
        prepare_routing.append(("credentials", operation))
        return "jobs-dsn", "verifier-dsn"
    ctl.provision_scoped_credentials = provision_for_candidate
    ctl.sync_control_plane = lambda *_args, **_kwargs: prepare_routing.append("sync")
    def prepare_for_receipt(_source, operation, *_args, **_kwargs):
        prepare_routing.append(("receipt", operation))
        return {"receipt_id": str(uuid.uuid4()), "evidence_ref": "evidence"}
    ctl.prepare_candidate = prepare_for_receipt
    ctl.prove_provider_preservation = lambda operation, *_args, **_kwargs: \
        prepare_routing.append(("preservation", operation))
    ctl.create_candidate = lambda operation, *_args, **_kwargs: \
        (_ for _ in ()).throw(AssertionError(f"unexpected create {operation}"))
    ctl.psycopg = types.SimpleNamespace(connect=lambda *_args, **_kwargs: None)
    stdout = io.StringIO()
    with redirect_stdout(stdout):
        rc = ctl.main(["prepare", "--apply", "--local-checks-green",
            "--sha", "a"*40, "--operation-id", str(receipt_operation_id),
            "--candidate-operation-id", str(candidate_operation_id)])
    completed = json.loads(stdout.getvalue())
    assert rc == 0 and completed["receipt_operation_id"] == str(receipt_operation_id)
    assert completed["candidate_operation_id"] == str(candidate_operation_id)
    assert ("credentials", candidate_operation_id) in prepare_routing
    assert ("preservation", candidate_operation_id) in prepare_routing
    assert ("receipt", receipt_operation_id) in prepare_routing
finally:
    for name, value in saved.items(): setattr(ctl, name, value)
    ctl.psycopg = saved_psycopg

print("staging-project-replacement-selftest: PASS")
