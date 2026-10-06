#!/usr/bin/env python3
"""Fixtures for ops/migration-shadow.py.

The shadow run builds a throwaway PostgreSQL 18 cluster from the BASE branch's
db/schema.sql (production's structure and ledger), applies every pending
migration on top, dumps the result with bin/schema-snapshot.sh and compares it
with the committed db/schema.sql. These cases pin the comparison: it ignores
only the time each migration happened to apply, and it never passes when the
structure, the ledger membership or a checksum differs. The database path
itself runs in the CI migration class, where PostgreSQL 18 is installed.

Run: .venv/bin/python ops/migration-shadow-selftest.py
"""
import importlib.util
import pathlib
import subprocess
import sys
import tempfile
from typing import Any
from git_env import fixture_env

REPO = pathlib.Path(__file__).resolve().parent.parent
TOOL = REPO / "ops" / "migration-shadow.py"

spec = importlib.util.spec_from_file_location("shadow", TOOL)
assert spec is not None and spec.loader is not None, f"cannot load {TOOL}"
shadow: Any = importlib.util.module_from_spec(spec)
sys.modules["shadow"] = shadow
spec.loader.exec_module(shadow)

passed: int = 0
failures: list[str] = []


def check(name: str, cond: object, detail: str = "") -> None:
    global passed
    if cond:
        passed += 1
        print(f"  ok    {name}")
    else:
        failures.append(name)
        print(f"  FAIL  {name}" + (f" — {detail}" if detail else ""))


def snapshot(tables: str, rows: list[tuple[str, str, str]], seq: int = 7) -> str:
    body = "".join(f"{f}\t{s}\t{t}\n" for f, s, t in rows)
    return (tables
            + "select pg_catalog.setval('ops.work_request_ref_seq', " + str(seq) + ", true);\n"
            + "COPY public.schema_migrations (filename, sha256, applied_at) FROM stdin;\n"
            + body + "\\.\n\n-- CARR GRANTS\n")


T = "CREATE TABLE public.deal (id integer NOT NULL);\n"
R1 = [("0001_a.sql", "aa", "2026-09-01 10:00:00+00"), ("0002_b.sql", "bb", "2026-09-02 10:00:00+00")]

print("comparison")
check("identical snapshots match", shadow.differences(snapshot(T, R1), snapshot(T, R1)) == [])
later = [(f, s, "2026-10-05 12:34:56.789+00") for f, s, _ in R1]
check("only applied_at differs: match (apply time is not structure)",
      shadow.differences(snapshot(T, R1), snapshot(T, later)) == [])
check("ledger rows in a different order still match",
      shadow.differences(snapshot(T, R1), snapshot(T, list(reversed(R1)))) == [])
check("the operational work-request sequence value is ignored",
      shadow.differences(snapshot(T, R1, seq=7), snapshot(T, R1, seq=99)) == [])

d = shadow.differences(snapshot(T, R1), snapshot(T, R1[:1]))
check("a migration missing from the committed ledger is a difference",
      d and any("0002_b.sql" in line for line in d), repr(d))
d = shadow.differences(snapshot(T, R1), snapshot(T, [R1[0], ("0002_b.sql", "zz", R1[1][2])]))
check("a checksum difference is a difference", d and any("0002_b.sql" in line for line in d), repr(d))
d = shadow.differences(snapshot(T, R1),
                       snapshot("CREATE TABLE public.deal (id integer NOT NULL, region text);\n", R1))
check("a structural difference is a difference and is shown as a diff",
      d and any("region" in line for line in d), repr(d))
check("a snapshot with no ledger is never a match",
      shadow.differences(snapshot(T, R1), T) != [])

seed = "insert into ops.scac_mutation_registry_version select * from jsonb_populate_recordset(null::ops.scac_mutation_registry_version, '[{\"registry_version\": \"v1\", \"sealed_at\": \"2026-01-01T00:00:00Z\", \"digest\": \"abc\"}]'::jsonb);\n"
entry = "insert into ops.scac_mutation_registry_entry select r.* from jsonb_populate_record(null::ops.scac_mutation_registry_entry, p.entry || '{\"registered_at\": \"2026-01-01T00:00:00Z\", \"registry_version\": \"v1\"}'::jsonb) r;\n"
control = "insert into ops.enforcement_control_catalog (control_key,installed,verified_at,updated_at) values ('synthetic','t','2026-01-01T00:00:00Z'::timestamptz,'2026-01-01T00:00:00Z'::timestamptz);\n"
for name, row in (("seal", seed), ("registration", entry), ("control verification", control)):
    check(name + " timestamps are deterministic", shadow.differences(snapshot(row, R1), snapshot(row.replace('2026-01-01', '2026-02-02'), R1)) == [])
check("seed content remains bound", bool(shadow.differences(snapshot(seed, R1), snapshot(seed.replace('abc', 'xyz'), R1))))
check("unadmitted timestamps remain bound", bool(shadow.differences(snapshot("select '2026-01-01';\n", R1), snapshot("select '2026-02-02';\n", R1))))

from unittest.mock import patch
with tempfile.TemporaryDirectory() as tmp:
    work = pathlib.Path(tmp)
    repo = work / 'repo'
    repo.mkdir()
    env = fixture_env()
    env['GITHUB_BASE_REF'] = ''
    message = work / 'commit-message'
    message.write_text('Synthetic migration base fixture\n')
    def git(*args):
        return subprocess.run(['git', *args], cwd=repo, env=env,
                              capture_output=True, text=True, check=True).stdout.strip()
    def commit(path):
        (repo / path).write_text(path)
        git('add', path)
        git('commit', '-q', '-F', str(message))
    git('init', '-q', '-b', 'topic')
    git('config', 'user.name', 'Fixture')
    git('config', 'user.email', 'fixture@example.invalid')
    git('config', 'core.hooksPath', '/dev/null')
    commit('base.txt')
    base = git('rev-parse', 'HEAD')
    git('branch', 'main')
    commit('topic.txt')
    git('checkout', '-q', 'main')
    commit('main.txt')
    main = git('rev-parse', 'HEAD')
    git('update-ref', 'refs/remotes/origin/main', main)
    git('checkout', '-q', 'topic')
    with patch.object(shadow, 'REPO', repo), patch.dict(shadow.os.environ, env, clear=True):
        check('an unmerged branch shadows from its common base', shadow.default_base() == base)
        git('merge', '--no-commit', 'main')
        check('a pending main merge shadows from the main it incorporated', shadow.default_base() == main)

with tempfile.TemporaryDirectory() as tmp:
    work = pathlib.Path(tmp)
    def fake_run(command, env, label, log):
        if label == "bin/schema-snapshot.sh":
            (work / 'candidate.sql').write_text(snapshot(T, R1))
        return ''
    def fake_process(command, **kwargs):
        if command[0] == 'git':
            return subprocess.CompletedProcess(command, 0, snapshot(T, R1), '')
        if pathlib.Path(command[0]).name == 'initdb':
            data = pathlib.Path(command[command.index('-D') + 1])
            data.mkdir(parents=True, exist_ok=True)
            (data / 'PG_VERSION').write_text('18')
        rc = 1 if command[-1] in ('stop', 'status') else 0
        return subprocess.CompletedProcess(command, rc, '', '')
    with patch.object(shadow, 'run', side_effect=fake_run), patch.object(shadow.subprocess, 'run', side_effect=fake_process), patch.object(shadow, 'free_port', return_value=55701):
        try:
            shadow.shadow('HEAD', pathlib.Path('/synthetic/bin'), work)
        except shadow.StepFailed:
            check("failed shutdown fails the shadow result", True)
        else:
            check("failed shutdown fails the shadow result", False)

with tempfile.TemporaryDirectory() as tmp:
    bins = pathlib.Path(tmp)
    for name in ('initdb', 'pg_ctl', 'createdb', 'psql'):
        path = bins / name
        path.write_text('#!/bin/sh\necho "initdb (PostgreSQL) 18.6"\n')
        path.chmod(0o755)
    with patch.dict(shadow.os.environ, {'PATH': str(bins)}, clear=True), patch.object(shadow, 'PG_BIN_CANDIDATES', ()):
        check("client-only PG18 installation is unavailable", shadow.find_pg18(None) is None)
    (bins / 'postgres').write_text('synthetic server')
    with patch.dict(shadow.os.environ, {'PATH': str(bins)}, clear=True), patch.object(shadow, 'PG_BIN_CANDIDATES', ()):
        check("PG18 discovery follows the scrubbed child PATH", shadow.find_pg18(None) == bins.resolve())
    older = bins / 'older'
    older.mkdir()
    initdb = older / 'initdb'
    initdb.write_text('#!/bin/sh\necho "initdb (PostgreSQL) 17.9"\n')
    initdb.chmod(0o755)
    with patch.dict(shadow.os.environ, {'PATH': str(older) + shadow.os.pathsep + str(bins)}, clear=True), patch.object(shadow, 'PG_BIN_CANDIDATES', ()):
        check("PG17 earlier on PATH cannot hide PG18", shadow.find_pg18(None) == bins.resolve())

print("applied set")
check("newly applied migrations are the after-ledger minus the before-ledger, in order",
      shadow.newly_applied(snapshot(T, R1[:1]), snapshot(T, R1)) == ["0002_b.sql"])

print("refusal")
with tempfile.TemporaryDirectory() as empty:
    r = subprocess.run([sys.executable, str(TOOL), "--pg-bin-dir", empty, "--base", "HEAD"],
                       cwd=REPO, capture_output=True, text=True)
    check("without PostgreSQL 18 binaries the run is unavailable (exit 3), not a pass",
          r.returncode == 3, r.stdout + r.stderr)
    check("the refusal says which major it needs", "18" in r.stdout + r.stderr, r.stdout + r.stderr)

print(f"\n{passed} passed, {len(failures)} failed")
sys.exit(1 if failures else 0)
