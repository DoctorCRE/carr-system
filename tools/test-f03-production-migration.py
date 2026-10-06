#!/usr/bin/env python3
"""Pin migration 0507a to the two reviewed F03 validator candidates."""
from __future__ import annotations

import hashlib
import importlib.util
import os
import re
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools"))

from migrate import contains_transaction_control  # noqa: E402


CANDIDATES = (
    (
        REPO / "ops/f03-receipt-validator.candidate.sql",
        "14a73684fc6c18c2283edf0b8686489e3e78dd7085e5b62b3ef561e67ada736e",
    ),
    (
        REPO / "ops/f03-plan-ownership-validator.candidate.sql",
        "6114d0ea7d4342aa0067b2b9ababfd9ca039540495975060389a5035302c412e",
    ),
)
MIGRATION = REPO / "migrations/0507a_engineering_slice_plan_validators.sql"
POSTGRES_FIXTURES = (
    REPO / "mcp-server/test/f03-receipt-validator-postgres.sql",
    REPO / "mcp-server/test/f03-plan-ownership-validator-postgres.sql",
)


def normalized_candidate(path: Path) -> str:
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    transaction_lines = [
        line.strip().lower()
        for line in lines
        if line.strip().lower() in {"begin;", "commit;"}
    ]
    assert transaction_lines == ["begin;", "commit;"], (path, transaction_lines)
    return "".join(
        line for line in lines
        if line.strip().lower() not in {"begin;", "commit;"}
    )


def load_lane_fixture_module():
    module_path = REPO / "ops/engineering-claim-local-pg-gate.py"
    sys.path.insert(0, str(REPO / "ops"))
    spec = importlib.util.spec_from_file_location("f03_lane_fixture", module_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def build_scratch_lane(dsn: str) -> tuple[str, uuid.UUID, uuid.UUID, uuid.UUID]:
    """Reuse the canonical claim fixture up to, but not including, plan/envelope rows."""
    import psycopg

    module = load_lane_fixture_module()
    original_one = module.one

    def omit_case_owned_rows(cur, query: str, params: tuple = ()):
        compact = " ".join(query.split()).lower()
        if "insert into ops.engineering_slice_plan" in compact:
            return (uuid.uuid4(),)
        if "insert into ops.engineering_execution_envelope" in compact:
            return (uuid.uuid4(),)
        return original_one(cur, query, params)

    module.one = omit_case_owned_rows
    lease_token = uuid.uuid4()
    try:
        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                job_id, _envelope_id, session_id, _actor_id, _digest, _slice_ref, _ = (
                    module.fixture(cur)
                )
                row = original_one(
                    cur,
                    "select payload->>'work_request' from ops.job where id=%s",
                    (job_id,),
                )
                work_request_ref = row[0]
                cur.execute(
                    """update ops.job
                          set state='running',attempt=1,lease_owner='f03-production-migration',
                              lease_token=%s,leased_until=date_trunc('second',now())+interval '25 minutes'
                        where id=%s""",
                    (lease_token, job_id),
                )
            conn.commit()
    finally:
        module.one = original_one
    return work_request_ref, job_id, lease_token, session_id


def rendered_fixture(path: Path, lane: tuple[str, uuid.UUID, uuid.UUID, uuid.UUID]) -> str:
    work_request_ref, job_id, lease_token, session_id = lane
    source = path.read_text(encoding="utf-8")
    source = source.replace(
        "\\set lane_work_request_ref 'REPLACE-WITH-SCRATCH-WORK-REQUEST-REF'",
        f"\\set lane_work_request_ref '{work_request_ref}'",
    )
    source = source.replace(
        "\\set lane_job_id '00000000-0000-0000-0000-000000000000'",
        f"\\set lane_job_id '{job_id}'",
    )
    source = source.replace(
        "\\set lane_lease_token '00000000-0000-0000-0000-000000000000'",
        f"\\set lane_lease_token '{lease_token}'",
    )
    source = source.replace(
        "\\set lane_agent_session_id '00000000-0000-0000-0000-000000000000'",
        f"\\set lane_agent_session_id '{session_id}'",
    )
    fixture_dir = path.parent
    source = re.sub(
        r"(?m)^\\ir ([a-z0-9_.-]+)$",
        lambda match: f"\\ir {fixture_dir / match.group(1)}",
        source,
    )
    assert "REPLACE-WITH-SCRATCH" not in source
    return source


def run_sql_fixture(
    dsn: str,
    psql: str,
    path: Path,
    lane: tuple[str, uuid.UUID, uuid.UUID, uuid.UUID],
) -> None:
    import psycopg

    rendered = rendered_fixture(path, lane)
    with tempfile.NamedTemporaryFile("w", suffix=".sql", encoding="utf-8") as handle:
        handle.write(rendered)
        handle.flush()

        def invoke() -> subprocess.CompletedProcess[str]:
            return subprocess.run(
                [psql, dsn, "-v", "ON_ERROR_STOP=1", "-f", handle.name],
                cwd=REPO,
                text=True,
                capture_output=True,
                check=False,
            )

        completed = invoke()
        output = completed.stdout + completed.stderr
        digest_matches = set(re.findall(
            r"set the lane job payload plan_digest to (sha256:[0-9a-f]{64})",
            output,
        ))
        # A first pass may both report the exact lane digest and expose an
        # independent corpus failure later in the same fixture. Rebind the one
        # reported digest and rerun once; the terminal run must then be fully
        # green and contain no skip.
        if "SKIPPED" in output and len(digest_matches) == 1:
            required_digest = digest_matches.pop()
            with psycopg.connect(dsn) as conn:
                conn.execute(
                    "update ops.job set payload=jsonb_set(payload,'{plan_digest}',to_jsonb(%s::text)) where id=%s",
                    (required_digest, lane[1]),
                )
                conn.commit()
            completed = invoke()
            output = completed.stdout + completed.stderr
        assert completed.returncode == 0, output[-4000:]
        assert "SKIPPED" not in output, output[-4000:]
        assert "FAIL" not in output, output[-4000:]
        print(f"F03 PostgreSQL fixture passed without skips: {path.name}")


def run_postgres_acceptance(dsn: str, psql: str) -> None:
    if not re.fullmatch(r"postgres(?:ql)?://carr_ci@127\.0\.0\.1:\d+/carr_ci", dsn):
        raise AssertionError("F03 PostgreSQL acceptance requires the disposable local carr_ci database")
    lane = build_scratch_lane(dsn)
    for fixture in POSTGRES_FIXTURES:
        run_sql_fixture(dsn, psql, fixture, lane)


def main() -> int:
    chunks: list[str] = []
    for path, expected_digest in CANDIDATES:
        source = path.read_bytes()
        assert hashlib.sha256(source).hexdigest() == expected_digest, path
        chunks.append(normalized_candidate(path))

    expected = "".join(chunks)
    actual = MIGRATION.read_text(encoding="utf-8")
    assert actual == expected, "0507a is not the exact ordered candidate composition"
    assert not contains_transaction_control(actual), (
        "0507a contains explicit transaction control; the migration runner owns atomicity"
    )
    assert hashlib.sha256(actual.encode()).hexdigest() == (
        "1ab06cc390472a1f9458d09970d9e35c5362ebb789fb3da856ab3f5d87773f7f"
    )
    print("F03 production migration: exact reviewed composition; runner owns atomicity")
    dsn = os.environ.get("CARR_CI_DATABASE_URL")
    psql = os.environ.get("CARR_F03_PSQL")
    # The hosted gates class carries an ambient CARR_CI_DATABASE_URL for other
    # checks.  CARR_F03_PSQL is the local-db runner's explicit opt-in to this
    # test's PostgreSQL fixture, which otherwise runs after canonical CI.
    if psql:
        assert dsn, "CARR_F03_PSQL requires CARR_CI_DATABASE_URL"
        run_postgres_acceptance(dsn, psql)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
