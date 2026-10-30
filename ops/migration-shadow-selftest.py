#!/usr/bin/env python3
"""Synthetic fixtures for shadow rebuilds, snapshot integrity, and cleanup."""
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


import pathlib, sys  # noqa: E401
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))  # repo root, for lib/
from lib.gate_check import Check  # noqa: E402

check = Check(failures=failures, counters={"passed": "pass"}, ns=globals())


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
        event = work / 'merge-group.json'
        event.write_text('{"merge_group":{"base_sha":"' + base + '"}}')
        with patch.dict(shadow.os.environ, {'GITHUB_EVENT_NAME': 'merge_group', 'GITHUB_EVENT_PATH': str(event)}):
            check('merge queue replays its group base even when origin/main differs', shadow.default_base() == base)
            event.write_text('{"merge_group":{}}')
            with patch.object(shadow, 'find_pg18', return_value=pathlib.Path('/synthetic/bin')):
                check('a merge queue event without its base fails closed', shadow.main([]) == 2)

print("base deployment order")
with tempfile.TemporaryDirectory() as tmp:
    work = pathlib.Path(tmp)
    repo = work / 'repo'
    (repo / 'migrations').mkdir(parents=True)
    (repo / 'db').mkdir()
    (repo / 'tools').mkdir()
    env = fixture_env()
    def base_git(*args):
        return subprocess.run(['git', *args], cwd=repo, env=env,
                              capture_output=True, text=True, check=True).stdout.strip()
    base_git('init', '-q', '-b', 'main')
    base_git('config', 'user.name', 'Fixture')
    base_git('config', 'user.email', 'fixture@example.invalid')
    base_git('config', 'core.hooksPath', '/dev/null')
    (repo / 'migrations/0002_higher.sql').write_bytes(b"select 'base\rvalue';\n")
    (repo / 'db/migration-baseline.json').write_text('{}\n')
    base_git('add', 'migrations/0002_higher.sql', 'db/migration-baseline.json')
    message = work / 'commit-message'
    message.write_text('Synthetic base with higher migration\n')
    base_git('commit', '-q', '-F', str(message))
    base_revision = base_git('rev-parse', 'HEAD')
    (repo / 'migrations/0001_lower.sql').write_text('select 1;\n')
    for name in ('migrate.py', 'migration_baseline.py', 'migration_number_contract.py'):
        (repo / 'tools' / name).write_text('current policy for ' + name)
    with patch.object(shadow, 'REPO', repo):
        runner = shadow.materialize_base(base_revision, work / 'base-source')
    check('base deployment excludes the lower migration introduced by HEAD',
          sorted(path.name for path in (runner.parent.parent / 'migrations').iterdir()) == ['0002_higher.sql'])
    check('base deployment preserves SQL bytes',
          (runner.parent.parent / 'migrations/0002_higher.sql').read_bytes() == b"select 'base\rvalue';\n")
    check('base deployment carries its own manifest',
          (runner.parent.parent / 'db/migration-baseline.json').read_text() == '{}\n')
    check('base deployment uses the current runner policy',
          runner.read_text() == 'current policy for migrate.py')

# A CHECK constraint can hold a literal carriage return (the SCAC ingress_key
# checks do). Universal-newline text IO rewrites it to a line feed, which then
# loads into the shadow cluster and lands in db/schema.sql as a different
# constraint. Every snapshot read and write must keep the bytes.
CR = "CREATE TABLE ops.k (k text CHECK (k !~ '[\n\r\t]'));\n"
print("byte fidelity")
with tempfile.TemporaryDirectory() as tmp:
    path = pathlib.Path(tmp) / 'schema.sql'
    shadow.write_snapshot(path, snapshot(CR, R1))
    check("a written snapshot keeps a carriage return", b"\r" in path.read_bytes())
    check("a read snapshot keeps a carriage return", "\r" in shadow.read_snapshot(path))

with tempfile.TemporaryDirectory() as tmp:
    work = pathlib.Path(tmp)
    labels: list[str] = []
    different_order = False
    fail_shutdown = True
    def fake_run(command, env, label, log):
        labels.append(label)
        if label == "bin/schema-snapshot.sh":
            pathlib.Path(command[-1]).write_text(snapshot(T, R1))
        if label == "canonical migration ledger":
            return '{"0001_a.sql":"aa","0002_b.sql":"bb"}'
        if label.startswith("dump "):
            tables = "-- Name: deal; Type: TABLE; Schema: public; Owner: -\n" + T
            if different_order and label == "dump canonical schema and grants":
                tables += "CREATE TABLE widget(id int);\n"
            pathlib.Path(command[-1]).write_text(tables)
        return ''
    def fake_process(command, **kwargs):
        if command[0] == 'git':
            out = snapshot(CR, R1)
            if kwargs.get('text') or kwargs.get('universal_newlines'):
                # What a text-mode pipe really returns: universal newlines.
                return subprocess.CompletedProcess(
                    command, 0, out.replace('\r\n', '\n').replace('\r', '\n'), '')
            return subprocess.CompletedProcess(command, 0, out.encode(), '')
        if pathlib.Path(command[0]).name == 'initdb':
            data = pathlib.Path(command[command.index('-D') + 1])
            data.mkdir(parents=True, exist_ok=True)
            (data / 'PG_VERSION').write_text('18')
        rc = 0
        if command[-1] == 'status':
            rc = 1 if fail_shutdown else 3
        elif command[-1] == 'stop' and fail_shutdown:
            rc = 1
        return subprocess.CompletedProcess(command, rc, '', '')
    with patch.object(shadow, 'materialize_base', return_value=work / 'base-source/tools/migrate.py'), patch.object(shadow.seeds, 'compare'), patch.object(shadow, 'run', side_effect=fake_run), patch.object(shadow.subprocess, 'run', side_effect=fake_process), patch.object(shadow, 'free_port', return_value=55701):
        try:
            shadow.shadow('HEAD', pathlib.Path('/synthetic/bin'), work)
        except shadow.StepFailed:
            check("failed shutdown fails the shadow result", True)
        else:
            check("failed shutdown fails the shadow result", False)
    check("the base snapshot loads with its carriage return intact",
          b"\r" in (work / 'base.sql').read_bytes())
    check("the shadow applies the base before migrations introduced by HEAD",
          labels.index('base migrations before HEAD') < labels.index('tools/migrate.py --apply'))
    different_order, fail_shutdown = True, False
    with patch.object(shadow, 'materialize_base', return_value=work / 'base-source/tools/migrate.py'), patch.object(shadow.seeds, 'compare'), patch.object(shadow, 'run', side_effect=fake_run), patch.object(shadow.subprocess, 'run', side_effect=fake_process), patch.object(shadow, 'free_port', return_value=55701):
        try:
            shadow.shadow('HEAD', pathlib.Path('/synthetic/bin'), work)
        except shadow.StepFailed as exc:
            check("a silent schema overwrite caused by migration order is refused",
                  'base-first migration order differs from canonical rebuild' in str(exc), str(exc))
        else:
            check("a silent schema overwrite caused by migration order is refused", False)

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

print("pending snapshot contract")
with tempfile.TemporaryDirectory() as tmp:
    repo = pathlib.Path(tmp)
    (repo / 'db').mkdir()
    base, candidate = snapshot(T, R1[:1]), snapshot(T + "CREATE TABLE widget(id int);\n", R1)
    with patch.object(shadow, 'REPO', repo), patch.object(shadow, 'find_pg18', return_value=pathlib.Path('/synthetic/bin')), patch.object(shadow, 'shadow', return_value=(base, candidate, ['0002_b.sql'])):
        for name, committed, expected in (
            ('unchanged snapshot with pending migrations passes', base, 0),
            ('refreshed snapshot passes', candidate, 0),
            ('hand-edited snapshot fails', base + 'SELECT 42;\n', 1),
            ('corrupt snapshot fails', 'invalid SQL', 1),
        ):
            shadow.write_snapshot(repo / 'db/schema.sql', committed)
            check(name, shadow.main(['--base', 'synthetic']) == expected)

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
