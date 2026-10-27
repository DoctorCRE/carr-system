#!/usr/bin/env python3
# Exercise CI classes, fixture isolation, collection, and failure behavior.

import contextlib
import hashlib
import inspect
import json
import os
import pathlib
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import time

REPO = pathlib.Path(__file__).resolve().parent.parent

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from git_env import fixture_env, scrubbed_env  # noqa: E402

CI = REPO / "ops" / "ci.sh"

SEED_DSN = "DATABASE_URL=" + "postgres://prod:" + "hunter2hunter2" + "@ep-x.neon.tech/carr"

RESULTS: list[tuple] = []

def check(label, ok, detail=""):
    RESULTS.append((label, bool(ok), detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"  ({detail})" if detail and not ok else ""))

ANSI = re.compile(r"\x1b\[[0-9;]*m")

@contextlib.contextmanager
def ci_fixture():
    with tempfile.TemporaryDirectory(prefix="carr-ci-fixture-") as directory:
        root = pathlib.Path(directory)
        for rel in (
            "ops/ci.sh", "ops/ci-pushfloor.py", "ops/ci-migration.py", "ops/node_test_summary.py", "tools/codemap.py",
            "ops/ci-secret-scan.py", "ops/pii_guard.py", "ops/ci-dep-check.py",
            "ops/git_env.py", "ops/no-client-deliverables-gate.py",
            "ops/hook-deletion-check.py", "ops/githooks/path-hygiene-check.py",
            "ops/migration-open-pr-gate.py", "ops/migration-order-gate.py",
            "tools/migration_claims.py", "tools/migration_number_contract.py",
            "tools/migration_source.py", "tools/migration_baseline.py",
            "ops/config/public-source-identities.v1.json",
            "bin/type-check.sh", "bin/with-timeout.py", "mypy.ini",
        ):
            target = root / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(REPO / rel, target)
        for name in ("pipelines", "exporters", "lib", "generators", "shared",
                     "fill-engine", "hooks", "migrations", "mcp-server"):
            (root / name).mkdir()
            if name not in {"migrations", "mcp-server"}:
                (root / name / (name.replace("-", "_") + "_fixture.py")).write_text("value: int = 1\n")
        (root / "ops/config/ci-check-scope.json").write_text('{}')
        requirements = "synthetic-package>=1\n"
        (root / "requirements.txt").write_text(requirements)
        source_hash = hashlib.sha256(b"synthetic-package>=1").hexdigest()
        (root / "requirements.lock").write_text(
            f"# requirements.txt sha256: {source_hash}\nsynthetic-package==1.0\n")
        tsc = REPO / "mcp-server/node_modules/typescript/bin/tsc"
        (root / "mcp-server/package.json").write_text(json.dumps({
            "private": True,
            "scripts": {"typecheck": f"node {shlex.quote(str(tsc))} --noEmit --allowJs --checkJs fixture.js"},
        }))
        (root / "mcp-server/fixture.js").write_text("const value = 1;\n")
        mypy = REPO / ".venv/bin/mypy"
        if mypy.is_file():
            (root / ".venv/bin").mkdir(parents=True)
            (root / ".venv/bin/mypy").symlink_to(mypy)
        shutil.copy2(REPO / "ops/integration-successor-selftest.py",
                     root / "tools/test_migration_fixture.py")
        env = fixture_env()
        for args in (("init", "-q", "-b", "main"),
                     ("config", "user.email", "fixture@example.invalid"),
                     ("config", "user.name", "Fixture"),
                     ("add", *[str(p.relative_to(root)) for p in root.rglob("*")
                               if p.is_file() and ".git" not in p.parts and ".venv" not in p.parts]),
                     ("commit", "-qm", "CI fixture"),
                     ("update-ref", "refs/remotes/origin/main", "HEAD")):
            subprocess.run(["git", *args], cwd=root, env=env, check=True,
                           capture_output=True, text=True)
        yield root


def run(args, env=None, shell_cmd=None, timeout=60, root=None):
    if root is None:
        with ci_fixture() as tree:
            return run(args, env, shell_cmd, timeout, root=tree)
    e = fixture_env()
    for key in ("CARR_CI_DATABASE_URL", "CARR_CI_RANGE", "GITHUB_EVENT_NAME",
                "GITHUB_EVENT_PATH", "GITHUB_BASE_REF"):
        e.pop(key, None)
    if env:
        e.update(env)
    ci = root / "ops/ci.sh"
    cmd = ([*shell_cmd, str(ci), *args] if shell_cmd else [str(ci), *args])
    with tempfile.TemporaryDirectory(prefix="carr-ci-test-", dir="/tmp") as directory:
        e.update(TMPDIR=directory, TMP=directory, TEMP=directory, RUNNER_TEMP=directory)
        p = subprocess.run(cmd, capture_output=True, text=True, cwd=root, env=e, timeout=timeout)
    return p.returncode, ANSI.sub("", (p.stdout or "") + (p.stderr or ""))

def test_freshness_checks_the_tree_without_requiring_main_ancestry():
    with tempfile.TemporaryDirectory() as directory:
        root = pathlib.Path(directory)
        (root / "ops/config").mkdir(parents=True)
        (root / "ops/ci.sh").write_bytes(CI.read_bytes())
        shutil.copy2(REPO / "ops/node_test_summary.py", root / "ops/node_test_summary.py")
        (root / "ops/config/ci-check-scope.json").write_text('{}')
        env = fixture_env()
        def git(*args):
            return subprocess.run(["git", *args], cwd=root, env=env, check=True,
                                  capture_output=True, text=True).stdout.strip()
        git("init", "-q")
        git("config", "user.email", "fixture@example.invalid")
        git("config", "user.name", "Fixture")
        (root / "source.txt").write_text("first\n")
        git("add", "source.txt")
        git("commit", "-qm", "first")
        first = git("rev-parse", "HEAD")
        git("update-ref", "refs/remotes/origin/main", first)
        def freshness():
            return subprocess.run(["/bin/bash", str(root / "ops/ci.sh"), "--strict", "--only", "freshness"],
                                  cwd=root, env=env, capture_output=True, text=True, timeout=15)
        result = freshness()
        check("freshness runs without a duplicate config checker", result.returncode == 0, result.stdout + result.stderr)
        (root / "source.txt").write_text("second\n")
        git("commit", "-qam", "second")
        git("update-ref", "refs/remotes/origin/main", git("rev-parse", "HEAD"))
        git("checkout", "-q", "--detach", first)
        result = freshness()
        check("a valid tree passes without containing the latest main", result.returncode == 0,
              result.stdout + result.stderr)
        blob = git("rev-parse", "HEAD:source.txt")
        subprocess.run(["git", "update-index", "--index-info"], cwd=root, env=env,
                       input=f"0 {'0' * 40}\tsource.txt\n"
                             f"100644 {blob} 1\tsource.txt\n"
                             f"100644 {blob} 2\tsource.txt\n"
                             f"100644 {blob} 3\tsource.txt\n",
                       text=True, check=True, capture_output=True)
        result = freshness()
        check("an unresolved integration tree fails", result.returncode == 1
              and "unresolved" in result.stdout, result.stdout + result.stderr)


def test_no_green_without_running():
    for sh in ("zsh", "sh", "bash"):
        exe = shutil.which(sh)
        if not exe:
            continue
        rc, out = run(["--strict", "--only", "freshness"], shell_cmd=[exe])
        claims_green = "CI passed" in out
        actually_ran = re.search(r"(OK|FAIL|SKIP)\s+freshness", out) is not None
        check(f"{sh}: never reports green without running a class",
              rc == 0 and claims_green and actually_ran,
              f"claimed green={claims_green} ran={actually_ran}")

def test_strict_turns_skip_into_failure():
    rc_loose, out_loose = run(["--only", "migration"])
    rc_strict, out_strict = run(["--strict", "--only", "migration"])
    check("a SKIP alone passes without --strict", rc_loose == 0 and "SKIP" in out_loose,
          f"rc={rc_loose}")
    check("the same SKIP fails under --strict", rc_strict == 1 and "SKIP" in out_strict,
          f"rc={rc_strict}")
    check("--strict failure explains why a skip counts",
          "stopped running" in out_strict.lower() or "skipped" in out_strict.lower())

def test_binding_reports_missing_wrangler_as_skipped():
    with ci_fixture() as root:
        (root / "mcp-server/wrangler.toml").write_text(
            'name = "fixture"\nmain = "worker.js"\ncompatibility_date = "2026-01-01"\n')
        for strict in (False, True):
            args = ["--only", "binding"] + (["--strict"] if strict else [])
            rc, output = run(args, root=root)
            check(f"binding without Wrangler {'fails strict CI' if strict else 'skips locally'}",
                  rc == (1 if strict else 0)
                  and re.search(r"SKIP\s+binding", output) is not None
                  and re.search(r"OK\s+binding", output) is None,
                  output)


def test_binding_preserves_config_and_boot_failures():
    with ci_fixture() as root:
        config = root / "mcp-server/wrangler.toml"
        valid = 'name = "fixture"\nmain = "worker.js"\ncompatibility_date = "2026-01-01"\n'
        config.write_text('name = "fixture"\n')
        rc, output = run(["--only", "binding"], root=root)
        check("binding config failure is not masked by missing Wrangler",
              rc == 1 and re.search(r"FAIL\s+binding", output) is not None, output)
        wrangler = root / "mcp-server/node_modules/.bin/wrangler"
        wrangler.parent.mkdir(parents=True)
        wrangler.write_text("#!/bin/sh\nexit 0\n")
        wrangler.chmod(0o755)
        boot = root / "bin/worker-boot-check.sh"
        for status in (0, 1):
            config.write_text(valid)
            boot.write_text(f"#!/bin/sh\necho fixture-boot-executed > boot-result\nexit {status}\n")
            boot.chmod(0o755)
            rc, output = run(["--strict", "--only", "binding"], root=root)
            check(f"binding {'passes after successful boot' if status == 0 else 'fails after unsuccessful boot'}",
                  rc == status and (root / "boot-result").read_text() == "fixture-boot-executed\n"
                  and re.search(rf"{'OK' if status == 0 else 'FAIL'}\s+binding", output) is not None,
                  output)
            (root / "boot-result").unlink()
        config.write_text('name = "fixture"\n')
        boot.write_text("#!/bin/sh\nexit 0\n")
        rc, output = run(["--strict", "--only", "binding"], root=root)
        check("successful boot does not mask invalid config",
              rc == 1 and re.search(r"FAIL\s+binding", output) is not None, output)


def test_gates_build_hxprobe_before_parallel_selftests():
    with tempfile.TemporaryDirectory(prefix="carr-hxprobe-gates-") as directory:
        root = pathlib.Path(directory).resolve()
        (root / "ops/config").mkdir(parents=True)
        (root / "vendor/hxprobe").mkdir(parents=True)
        (root / "bin").mkdir()
        (root / "tools").mkdir()
        shutil.copy2(REPO / "tools/codemap.py", root / "tools/codemap.py")
        for name in ("ci.sh", "node_test_summary.py", "git_env.py",
                     "ci-secret-scan.py", "pii_guard.py"):
            shutil.copy2(REPO / "ops" / name, root / "ops" / name)
        shutil.copy2(REPO / "bin/with-timeout.py", root / "bin/with-timeout.py")
        (root / "ops/config/ci-check-scope.json").write_text('{}')
        (root / "vendor/hxprobe/Cargo.toml").touch()
        (root / "ops/probe-selftest.py").write_text(
            "from pathlib import Path\n"
            "assert Path('worker-built').exists(), 'worker not built before pool'\n"
            "Path('gate-ran').touch()\n")
        (root / "hooks").mkdir()
        (root / "hooks/gate-integrity.py").write_text("raise SystemExit(0)\n")
        cargo = root / "bin/cargo"
        cargo.write_text("#!/bin/sh\n"
                         'test "$*" = "build --locked" || exit 9\n'
                         'test "$PWD" = "$CARR_TEST_ROOT/vendor/hxprobe" || exit 10\n'
                         'test "$CARR_BUILD_FAIL" = 0 || exit 1\n'
                         'touch "$CARR_TEST_ROOT/worker-built"\n')
        cargo.chmod(0o755)
        env = fixture_env()
        env.update(PATH=str(root / "bin") + os.pathsep + os.environ["PATH"],
                   CARR_TEST_ROOT=str(root), CARR_CI_GATE_JOBS="2")
        subprocess.run(["git", "init", "-q", str(root)], env=env, check=True)
        subprocess.run(["git", "-C", str(root), "add", "ops/ci.sh"], env=env, check=True)
        subprocess.run(["git", "-C", str(root), "-c", "user.name=Fixture", "-c",
                        "user.email=fixture@example.invalid", "commit", "-qm", "fixture"], env=env, check=True)
        subprocess.run(["git", "-C", str(root), "update-ref", "refs/remotes/origin/main", "HEAD"],
                       env=env, check=True)
        for failure in ("0", "1"):
            for name in ("worker-built", "gate-ran"):
                (root / name).unlink(missing_ok=True)
            env["CARR_BUILD_FAIL"] = failure
            result = subprocess.run(["bash", str(root / "ops/ci.sh"), "--strict", "--only", "gates"],
                                    cwd=root, env=env, capture_output=True, text=True, timeout=30)
            check(f"gates prepare HxStore worker before pool (build failure={failure})",
                  (root / "gate-ran").exists() == (failure == "0"), result.stdout + result.stderr)
            check(f"gates propagate HxStore preparation result (build failure={failure})",
                  result.returncode == int(failure), result.stdout + result.stderr)


def test_hxprobe_runs_locked_checks_and_propagates_failures():
    with tempfile.TemporaryDirectory(prefix="carr-hxprobe-ci-") as directory:
        root = pathlib.Path(directory).resolve()
        (root / "ops/config").mkdir(parents=True)
        (root / "vendor/hxprobe").mkdir(parents=True)
        (root / "bin").mkdir()
        for rel in ("ops/ci.sh", "ops/node_test_summary.py",
                    "ops/ci-secret-scan.py", "ops/pii_guard.py"):
            shutil.copy2(REPO / rel, root / rel)
        (root / "ops/config/ci-check-scope.json").write_text('{}')
        cargo = root / "bin/cargo"
        cargo.write_text("#!/usr/bin/env python3\n"
                         "import json, os, sys\n"
                         "with open(os.environ['CARR_HXPROBE_CALLS'], 'a') as log:\n"
                         "    log.write(json.dumps([os.getcwd(), sys.argv[1:]]) + '\\n')\n"
                         "sys.exit(1 if sys.argv[1] == os.environ.get('CARR_HXPROBE_FAIL') else 0)\n")
        cargo.chmod(0o755)
        calls = root / "calls.jsonl"
        env = fixture_env()
        env.update(PATH=str(root / "bin") + os.pathsep + os.environ["PATH"],
                   CARR_HXPROBE_CALLS=str(calls))
        for failure, expected in (("", ["test", "build"]), ("test", ["test"]),
                                  ("build", ["test", "build"])):
            calls.unlink(missing_ok=True)
            env["CARR_HXPROBE_FAIL"] = failure
            result = subprocess.run(["bash", str(root / "ops/ci.sh"), "--strict", "--only", "hxprobe"],
                                    cwd=root, env=env, capture_output=True, text=True, timeout=30)
            observed = [json.loads(line) for line in calls.read_text().splitlines()] if calls.exists() else []
            check(f"hxprobe propagates {failure or 'successful checks'}",
                  result.returncode == (1 if failure else 0), result.stdout + result.stderr)
            check(f"hxprobe runs locked commands in crate directory with {failure or 'no'} failure",
                  observed == [[str(root / "vendor/hxprobe"),
                                [cmd, "--locked"] if cmd == "test" else [cmd, "--release", "--locked"]]
                               for cmd in expected], str(observed))


def test_unknown_class_refuses():
    rc, out = run(["--only", "definitely-not-a-class"])
    check("an unknown class name exits 64, not 0", rc == 64, f"rc={rc}")

def test_migration_refuses_non_loopback():
    for dsn, label in [
        ("postgres://u:p@ep-steep-field.us-east-2.aws.neon.tech/carr", "a Neon host"),
        ("postgres://u:p@10.0.0.5:5432/carr", "a private IP"),
        ("postgres://u:p@db.internal:5432/carr", "an internal hostname"),
    ]:
        rc, out = run(["--only", "migration"], env={"CARR_CI_DATABASE_URL": dsn})
        check(f"migration refuses {label}", rc != 0 and "REFUSED" in out, f"rc={rc}")

    rc, out = run(["--only", "migration"],
                  env={"CARR_CI_DATABASE_URL": "postgres://u:p@localhost:5432/x"})
    check("migration ACCEPTS loopback (fails on no server, not on the guard)",
          "REFUSED" not in out)

def test_migration_fixtures_ignore_hosted_pr_event():
    from unittest.mock import patch

    with tempfile.TemporaryDirectory() as directory:
        event = pathlib.Path(directory) / "event.json"
        event.write_text('{}')
        hosted = {"GITHUB_EVENT_NAME": "pull_request", "GITHUB_EVENT_PATH": str(event)}
        for portable in ("0", "1"):
            with patch.dict(os.environ, {**hosted, "CARR_CI_PORTABLE_ONLY": portable}):
                rc, out = run(["--only", "migration"])
                check(f"host portable={portable}: migration fixture reaches loose SKIP",
                      rc == 0 and "SKIP" in out and "no CARR_CI_DATABASE_URL" in out, out)
                rc, out = run(["--only", "migration"], env=hosted)
                check(f"host portable={portable}: explicit PR event still fails closed",
                      rc == 1 and "cannot verify claims" in out, out)

def test_database_scope_receipts():
    import importlib.util
    spec = importlib.util.spec_from_file_location("db_scope", REPO / "ops/db-acceptance-scope.py")
    scope = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(scope)
    for path in ("migrations/9999_probe.sql", "mcp-server/test/new-proof.sql",
                 "ops/new-db-gate.py", ".github/workflows/ci.yml"):
        check(f"database scope runs {path}", scope.needs_full([path]))
    check("database scope skips app-only change", not scope.needs_full(["web/app.js"]))
    with tempfile.TemporaryDirectory() as directory:
        event = pathlib.Path(directory) / "event.json"
        event.write_text('{}')
        output, receipt = pathlib.Path(directory) / "output", pathlib.Path(directory) / "receipt.json"
        result = subprocess.run([sys.executable, str(REPO / "ops/db-acceptance-scope.py"),
                                 "--receipt", str(receipt)], cwd=REPO,
                                env={**scrubbed_env(), "GITHUB_EVENT_NAME": "pull_request",
                                     "GITHUB_EVENT_PATH": str(event), "GITHUB_OUTPUT": str(output)},
                                capture_output=True, text=True)
        check("uncertain scope emits a full-run receipt", result.returncode == 0 and receipt.exists()
              and json.loads(receipt.read_text())["full"] is True, result.stdout + result.stderr)


def test_hosted_migration_ownership():
    wf = _hosted_workflow()
    runner = next(st for st in wf["jobs"]["classes"]["steps"] if st.get("name") == "Run this group's check classes")
    check("PR slot claims run before entering the credential-scrubbed database lane",
          runner["run"].index("python3 ops/migration-open-pr-gate.py") < runner["run"].index("python ops/local-pg-ci.py")
          and "GH_TOKEN" in runner["env"])
    check("hosted migration partitions cover all independent proof groups",
          set(wf["jobs"]["migration-fixtures"].get("strategy", {}).get("matrix", {}).get("part", []))
          == {"reconstruction", "equivalence", "shadow", "gates", "acceptance"}
          and "--migration-part proofs" in runner["run"])
    check("required aggregate waits for independent fixtures", "migration-fixtures" in wf["jobs"]["checks"]["needs"])
    aggregate = next(st for st in wf["jobs"]["checks"]["steps"] if st.get("name") == "Fail unless every class group succeeded")
    with tempfile.TemporaryDirectory() as directory:
        for full, fixtures, classes, draft, expected in (
            ("true", "success", "success", "false", 0), ("false", "skipped", "success", "false", 0),
            ("", "skipped", "success", "false", 1), ("true", "failure", "success", "false", 1),
            ("true", "cancelled", "success", "false", 1), ("false", "failure", "success", "false", 1),
            ("false", "skipped", "failure", "false", 1),
            ("true", "skipped", "success", "true", 1),
            ("true", "skipped", "success", "false", 1),
        ):
            result = subprocess.run(["bash", "-e", "-c", aggregate["run"]], capture_output=True, text=True,
                                    env={**scrubbed_env(), "PATH": str(pathlib.Path(sys.executable).parent) + os.pathsep + os.environ["PATH"],
                                         "CI_DRAFT": draft, "GITHUB_STEP_SUMMARY": str(pathlib.Path(directory) / "summary"),
                                         "CI_NEEDS": json.dumps({"scope": {"result": "success", "outputs": {"full": full}},
                                                                 "classes": {"result": classes},
                                                                 "fallow": {"result": "success"},
                                                                 "migration-fixtures": {"result": fixtures}})})
            check(f"aggregate scope={full!r} fixtures={fixtures} classes={classes}", result.returncode == expected)
    record = next(st for st in wf["jobs"]["checks"]["steps"] if st.get("name") == "Record the tested tree and class contract")
    check("not-applicable proof cannot be reused as full main evidence", "full != 'false'" in record.get("if", ""))


def test_postgres_path_boundary_rejects_platform_defaults():
    with ci_fixture() as root:
        path = root / 'lib/default.py'
        path.write_text("DEFAULT = '" + '/opt/homebrew/opt/' + "postgresql@17/bin'\n")
        subprocess.run(['git', 'add', 'lib/default.py'], cwd=root,
                       env=fixture_env(), check=True)
        result = subprocess.run(
            ['git', 'grep', '-n', '-F', '/opt/homebrew/opt/postgresql@17',
             '--', '*.py', ':!lib/disposable_pg_fixture.py'],
            cwd=root, capture_output=True, text=True)
        check('PostgreSQL path boundary rejects a platform default',
              result.returncode == 0 and 'lib/default.py' in result.stdout)
        path.write_text("from lib.disposable_pg_fixture import postgres_binary\n")
        subprocess.run(['git', 'add', 'lib/default.py'], cwd=root,
                       env=fixture_env(), check=True)
        result = subprocess.run(
            ['git', 'grep', '-n', '-F', '/opt/homebrew/opt/postgresql@17',
             '--', '*.py', ':!lib/disposable_pg_fixture.py'],
            cwd=root, capture_output=True, text=True)
        check('PostgreSQL path boundary accepts fixture-resolver ownership',
              result.returncode == 1 and not result.stdout)


def test_gate_shards_execute_a_disjoint_complete_inventory():
    with tempfile.TemporaryDirectory() as directory:
        root = pathlib.Path(directory)
        for name in ("ops", "tools", "hooks", "logs"):
            (root / name).mkdir()
        inventory = {"ops/a-selftest.py", "ops/b-selftest.py", "ops/c-selftest.py",
                     "tools/test-d.py", "tools/test-e.sh", "tools/test-f.sh"}
        shutil.copy2(REPO / "tools/codemap.py", root / "tools/codemap.py")
        for args in (("init", "-q"),
                     ("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                      "commit", "--allow-empty", "-qm", "Synthetic shard baseline"),
                     ("update-ref", "refs/remotes/origin/main", "HEAD")):
            subprocess.run(["git", *args], cwd=root, env=fixture_env(), check=True, capture_output=True)
        for path in inventory:
            (root / path).write_text("")
        (root / "hooks/gate-integrity.py").write_text("")
        (root / "timeout.py").write_text(
            "import os,sys\n"
            "path=sys.argv[-1]\n"
            "with open(os.environ['CALLS'],'a') as out: out.write(path+'\\n')\n"
            "sys.exit(7 if path==os.environ.get('FAIL_TEST') else 0)\n")
        prefix = "\n".join(("set -uo pipefail", f"PY={shlex.quote(sys.executable)}",
                              f"LOGDIR={shlex.quote(str(root / 'logs'))}", "FAILED=0",
                              "CI_SELFTEST_TIMEOUT_SECONDS=1", f"CI_TIMEOUT_HELPER={shlex.quote(str(root / 'timeout.py'))}",
                              "tree_fingerprint() { echo static; }", "ok() { :; }",
                              "bad() { FAILED=1; }",
                              'run_quiet() { local log="$1"; shift; "$@" >"$log" 2>&1; }',
                              "fail_tail() { :; }"))
        body = _ci_function_body("check_gates", "check_secret")
        seen = []
        for shard in (1, 2):
            calls = root / f"calls-{shard}"
            result = subprocess.run(["bash", "-c", prefix + f"\nGATE_SHARD={shard}\nGATE_SHARDS=2\n" + body +
                                     '\ncheck_gates; exit "$FAILED"'], cwd=root,
                                    env={**scrubbed_env(), "CALLS": str(calls)}, capture_output=True, text=True)
            check(f"gate shard {shard} executes successfully", result.returncode == 0, result.stderr)
            seen.append(set(calls.read_text().splitlines()))
        check("gate shard union executes every Python and shell suite", seen[0] | seen[1] == inventory, seen)
        check("gate shards never execute the same suite twice", not seen[0] & seen[1], seen)
        failed = next(iter(seen[1]))
        result = subprocess.run(["bash", "-c", prefix + "\nGATE_SHARD=2\nGATE_SHARDS=2\n" + body +
                                 '\ncheck_gates; exit "$FAILED"'], cwd=root,
                                env={**scrubbed_env(), "CALLS": str(root / "failed"), "FAIL_TEST": failed},
                                capture_output=True, text=True)
        check("a failing second-shard suite fails its job", result.returncode == 1, result.stderr)


def test_secret_scanner_catches_and_respects_allow():
    with tempfile.TemporaryDirectory(prefix="carr-ci-scanner.") as tmp:
        root = pathlib.Path(tmp)
        (root / "ops/config").mkdir(parents=True)
        paths = ["ops/ci-secret-scan.py", "ops/pii_guard.py",
                 "ops/config/public-source-identities.v1.json"]
        for rel in paths:
            shutil.copyfile(REPO / rel, root / rel)
        env = fixture_env()
        env.pop("GITHUB_EVENT_PATH", None)

        def git(*args):
            return subprocess.run(["git", *args], cwd=root, env=env,
                                  capture_output=True, text=True, check=True)

        git("init", "-b", "main")
        git("config", "user.name", "Synthetic Scanner Tester")
        git("config", "user.email", "scanner@example.invalid")
        seeded_rel = "fixture.txt"
        seeded = root / seeded_rel
        seeded.write_text("Synthetic scanner fixture\n")
        git("add", *paths, seeded_rel)
        message = root / "commit-message.txt"
        message.write_text("Synthetic scanner baseline\n")
        git("commit", "--no-verify", "-F", str(message))
        git("update-ref", "refs/remotes/origin/main", "HEAD")
        tracked = git("ls-files", "--error-unmatch", "--", seeded_rel).returncode == 0
        check("the credential fixture path is tracked for the scan", tracked)
        scan = [sys.executable, str(root / "ops/ci-secret-scan.py")]
        p = subprocess.run(scan, cwd=root, env=env, capture_output=True, text=True)
        check("the scanner accepts the clean fixture", p.returncode == 0,
              f"rc={p.returncode}")
        seeded.write_text(SEED_DSN + "\n")
        p = subprocess.run(scan, cwd=root, env=env, capture_output=True, text=True)
        check("a seeded credential is caught", tracked and p.returncode == 1,
              "credential fixture was not tracked" if not tracked else f"rc={p.returncode}")
        check("the finding never prints the credential value",
              tracked and "hunter2" + "hunter2" not in (p.stdout + p.stderr))

        seeded.write_text(SEED_DSN + "  # ci-secret-scan" + ": allow — selftest fixture\n")
        p = subprocess.run(scan, cwd=root, env=env, capture_output=True, text=True)
        check("an inline allow marker on the same line suppresses it",
              tracked and p.returncode == 0,
              "credential fixture was not tracked" if not tracked else f"rc={p.returncode}")

def test_dep_check_detects_a_stale_lock():
    with ci_fixture() as root:
        req = root / "requirements.txt"
        original = req.read_text()
        rc, out = run(["--only", "dependency"], root=root)
        check("dependency check passes on the clean fixture", rc == 0, out)
        req.write_text(original + "\nsome-package-that-is-not-locked>=1.0\n")
        rc, out = run(["--only", "dependency"], root=root)
        check("a requirements.txt edit makes the lock STALE", rc == 1 and "STALE" in out, out)
        req.write_text(original + "\n# a comment-only edit\n")
        rc, out = run(["--only", "dependency"], root=root)
        check("a comment-only edit does NOT invalidate the lock", rc == 0, out)

def test_types_class_catches_a_seeded_type_error():
    fixture = "mcp-server/fixture.js"
    with ci_fixture() as root:
        rc, out = run(["--only", "types"], root=root)
        check("types passes on the clean fixture", rc == 0,
              f"rc={rc} out={out[-400:]}")

        original = (root / fixture).read_text()
        (root / fixture).write_text(
            "/** @type {number} */\n"
            "const value = 'not a number';\n")
        rc, out = run(["--only", "types"], root=root)
        check("a seeded type error makes the types class FAIL", rc == 1,
              f"rc={rc} out={out[-400:]}")
        check("the failing run names the seeded type error and types class",
              re.search(r"FAIL\s+types\b", out) is not None
              and "fixture.js" in out and "TS2322" in out,
              f"out={out[-800:]}")

        (root / fixture).write_text(original)
        rc, out = run(["--only", "types"], root=root)
    check("removing the seed turns the types class green again", rc == 0,
          f"rc={rc} out={out[-400:]}")

UNCOLLECTED_BY_DECISION = {
    "docs/frontier-finding/breakglass_selftest.py":
        "Needs a disposable local PostgreSQL cluster, which it stands up "
        "itself (WR-000046 Artifact C harness). The gates class is repository "
        "content only — no machine state, no database — and local initdb is "
        "unavailable on Joe's Mac ('shmget: Operation not permitted'). Its "
        "database coverage belongs to the migration class, not this one.",
    "pipelines/doctrine_load_test.py":
        "Not a unit suite: it is the P6 preflight LOAD BAR. It requires "
        "DATABASE_URL for the runtime reader role against the production "
        "store, drives 20 concurrent sessions, and runs for 15 minutes by "
        "default. Network, database and wall-clock all disqualify it from a "
        "merge gate; it is run deliberately before a release.",
    "tools/doc-convo/bin/test-brain-stream.sh":
        "Not evaluated by this unit. It lives in a bin/ directory beside the "
        "convo server rather than in a tests/ directory, and the three "
        "doc-convo shell tests appear to drive a running server. Named here as "
        "a known gap; collecting them owes a run first.",
    "tools/doc-convo/bin/test-convo-server.sh":
        "Not evaluated by this unit — see the test-brain-stream.sh entry.",
    "tools/doc-convo/bin/test-streaming.sh":
        "Not evaluated by this unit — see the test-brain-stream.sh entry.",
}

TEST_FILE_NAME = re.compile(r"""
    ^(?:
        test[-_].*\.(?:py|sh)        # test-foo.py, test_foo.py, test-foo.sh
      | .*[-_]selftest\.(?:py|sh)    # foo-selftest.py, breakglass_selftest.py
      | .*_test\.py                  # foo_test.py
    )$""", re.VERBOSE)

UNWALKED_DIRS = frozenset({
    "node_modules", ".venv", "venv", "__pycache__", "out", "_inputs",
    ".mypy_cache", ".pytest_cache", "dist", "build", ".git", ".claude",
})

def _test_shaped_files(tree=None):
    tree = pathlib.Path(tree) if tree is not None else REPO
    found = set()
    for root, dirs, files in os.walk(tree):
        dirs[:] = sorted(d for d in dirs if d not in UNWALKED_DIRS)
        for name in files:
            if TEST_FILE_NAME.match(name):
                found.add(pathlib.Path(root, name)
                          .relative_to(tree).as_posix())
    return found

def test_each_class_runs_in_exactly_one_hosted_group():
    from collections import Counter
    listed = subprocess.run(["bash", str(CI), "--list"], cwd=REPO,
                            capture_output=True, text=True, check=True).stdout
    classes = set(re.findall(r"^  ([a-z]+)\s", listed, re.MULTILINE))
    for filename in ("ci.yml", "main-canary.yml"):
        workflow = (REPO / ".github/workflows" / filename).read_text()
        block = workflow.split("        classes:\n", 1)[1].split("    #", 1)[0]
        groups = re.findall(r'^          - "([a-z ]+)"$', block, re.MULTILINE)
        membership = Counter(name for group in groups for name in group.split())
        check(f"every hosted class runs in exactly one {filename} group",
              bool(classes) and set(membership) == classes
              and all(count == 1 for count in membership.values()),
              f"classes={sorted(classes)}, membership={dict(membership)}")


def test_every_test_file_in_the_tree_is_collected():
    ci = (REPO / "ops" / "ci.sh").read_text()
    guarded = re.findall(r'\[ -f (mcp-server/test/[\w./-]+) \]', ci)
    missing = sorted(path for path in guarded if not (REPO / path).is_file())
    check("every conditional migration proof names an existing file", not missing,
          f"absent files provide no coverage: {', '.join(missing)}")
    patterns: list[str] = []
    for m in re.finditer(r"for t in ([^;]+); do", ci):
        patterns += [tok for tok in m.group(1).split()
                     if re.fullmatch(r"[A-Za-z0-9_./*?\[\]-]+", tok)]
    check("ci.sh's selftest collection globs are readable from source",
          len(patterns) >= 2, f"found: {patterns}")
    if not patterns:
        return

    collected = set()
    for pat in patterns:
        collected |= {p.relative_to(REPO).as_posix() for p in REPO.glob(pat)}

    on_disk = _test_shaped_files()
    check("the tree still contains test files to collect", on_disk,
          "an empty set would make the assertion below vacuously true")

    depths = {name.count("/") + 1 for name in on_disk}
    check("the walk reaches test files nested below the top two levels",
          max(depths, default=0) >= 3,
          f"deepest test file found is {max(depths, default=0)} levels — "
          f"tools/room-bridge/ (3) and tools/*/tests/ (4) exist, so a maximum "
          f"of 2 means this enumeration flattened and is measuring nothing")

    stale = sorted(p for p in UNCOLLECTED_BY_DECISION if p not in on_disk)
    check("every skip-list entry names a file that exists", not stale,
          f"gone or renamed, so the entry protects nothing: {', '.join(stale)}")

    contradicted = sorted(p for p in UNCOLLECTED_BY_DECISION if p in collected)
    check("no skip-list entry excuses a file ci.sh already collects",
          not contradicted,
          f"collected AND excused, so the reason is fiction: "
          f"{', '.join(contradicted)}")

    thin = sorted(p for p, why in UNCOLLECTED_BY_DECISION.items()
                  if len(why.strip()) < 60)
    check("every skip-list entry states a real reason", not thin,
          f"a reason too short to be one: {', '.join(thin)}")

    missed = sorted(on_disk - collected - set(UNCOLLECTED_BY_DECISION))
    check("every test file in the tree is collected by a loop in ci.sh "
          "or excused by name in UNCOLLECTED_BY_DECISION",
          not missed,
          f"never executed and never excused: {', '.join(missed)}")
    print(f"        reach: {len(on_disk)} test files in the tree, "
          f"{len(on_disk & collected)} collected by ci.sh, "
          f"{len(UNCOLLECTED_BY_DECISION)} excused by name")

def test_the_walk_prunes_named_roots_only():
    with tempfile.TemporaryDirectory(prefix="ci-selftest-walk-") as td:
        tree = pathlib.Path(td)
        subprocess.run(["git", "init", "-q"], cwd=tree, check=True,
                       capture_output=True, env=scrubbed_env())

        reachable = ["tools/test_control.py", ".github/test_planted_dot_dir.py",
                     ".github/workflows/test_nested_dot_dir.py"]
        pruned = [f"{d}/test_planted.py" for d in sorted(UNWALKED_DIRS)]
        for rel in reachable + pruned:
            path = tree / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("# fixture\n", encoding="utf-8")
        subprocess.run(["git", "add", "-f", *reachable], cwd=tree, check=True,
                       capture_output=True, env=scrubbed_env())
        tracked = subprocess.run(["git", "ls-files"], cwd=tree, check=True,
                                 capture_output=True, text=True,
                                 env=scrubbed_env()).stdout.split()
        check("the fixture's dot-directory test really is tracked",
              ".github/test_planted_dot_dir.py" in tracked, f"{tracked}")

        found = _test_shaped_files(tree)

    for rel in reachable:
        check(f"the walk reaches {rel}", rel in found, f"found: {sorted(found)}")
    still_pruned = sorted(rel for rel in pruned if rel in found)
    check("every name in UNWALKED_DIRS is still pruned",
          not still_pruned, f"walked into: {', '.join(still_pruned)}")
    check("the prune set is named roots, with no shape rule behind it",
          "startswith" not in inspect.getsource(_test_shaped_files),
          "a category prune is back; a dot-directory test would go silent again")

FIXTURE_TOKEN = "ghp_0123456789abcdefghijklmnopqrstuvwxyz"  # ci-secret-scan: allow — inert redaction fixture

def _fail_tail(log_path, py=None):
    src = CI.read_text(encoding="utf-8")
    start = src.index("fail_tail() {")
    fn = src[start:src.index("\ncheck_unit()", start)]
    p = subprocess.run(
        ["bash", "-c", f'PY={shlex.quote(py or sys.executable)}\n{fn}\nfail_tail '
                       f'{shlex.quote(str(log_path))}'],
        cwd=str(REPO), capture_output=True, text=True, timeout=120)
    return ANSI.sub("", (p.stdout or "") + (p.stderr or ""))

def test_a_failing_gates_output_is_printed_whole_when_it_is_short():
    with tempfile.TemporaryDirectory(prefix="ci-selftest-failtail-") as td:
        log = pathlib.Path(td) / "gate-short.log"
        lines = [f"ok check {i}" for i in range(1, 31)]
        lines[4] = f"FAIL check 5: token={FIXTURE_TOKEN} leaked into the log"
        log.write_text("\n".join(lines) + "\n", encoding="utf-8")
        out = _fail_tail(log)

    printed = [ln for ln in out.splitlines() if ln.strip()]
    check("a short log is printed whole, not tailed",
          len(printed) == 30, f"{len(printed)} lines: {printed[:3]}")
    check("the first line of a short log reaches the reader",
          "ok check 1" in out, out[:400])
    check("and so does the last", "ok check 30" in out, out[-400:])
    check("the failing line is inside the window",
          "FAIL check 5" in out, out[:400])
    check("a token-shaped string in a short log is MASKED",
          FIXTURE_TOKEN not in out, "the raw credential reached the CI log")
    check("and the mask says what was removed, so the line stays diagnosable",
          "<redacted:github-token:40 chars>" in out, out[:400])

def test_a_long_failing_gate_log_is_tailed_and_still_redacted():
    with tempfile.TemporaryDirectory(prefix="ci-selftest-failtail-") as td:
        log = pathlib.Path(td) / "gate-long.log"
        lines = [f"ok check {i}" for i in range(1, 251)]
        lines[2] = f"early line 3: token={FIXTURE_TOKEN} above the window"
        lines[244] = f"FAIL check 245: token={FIXTURE_TOKEN} inside the window"
        log.write_text("\n".join(lines) + "\n", encoding="utf-8")
        out = _fail_tail(log)

    printed = [ln for ln in out.splitlines() if ln.strip()]
    check("a long log is tailed to 80 lines", len(printed) == 80,
          f"{len(printed)} lines")
    check("the tail starts where 80 lines from the end starts",
          "ok check 171" in out, out[:300])
    check("and does not reach back past it",
          "ok check 170" not in out, out[:300])
    check("the failing line inside the window is printed",
          "FAIL check 245" in out, out[-400:])
    check("a token-shaped string inside the tail is MASKED",
          FIXTURE_TOKEN not in out, "the raw credential reached the CI log")
    check("the mask names the shape and the length",
          "<redacted:github-token:40 chars>" in out, out[-400:])
    check("the line above the window is not printed at all",
          "early line 3" not in out, out[:300])

def test_fail_tail_withholds_the_window_when_it_cannot_redact():
    with tempfile.TemporaryDirectory(prefix="ci-selftest-failtail-") as td:
        log = pathlib.Path(td) / "gate-noredactor.log"
        log.write_text(f"FAIL: token={FIXTURE_TOKEN}\nsecond line\n", encoding="utf-8")
        out = _fail_tail(log, py=str(pathlib.Path(td) / "no-such-interpreter"))

    check("no part of the log is printed when redaction is unavailable",
          "second line" not in out, out)
    check("and least of all the credential", FIXTURE_TOKEN not in out, out)
    check("the reader is told the window was withheld",
          "WITHHELD" in out, out)
    check("and where the captured log actually is",
          str(log) in out, out)

def test_gates_never_run_the_production_diagnostic():
    with ci_fixture() as root:
        (root / "hooks/gate-integrity.py").write_text("raise SystemExit(0)\n")
        diagnostic = root / "ops/migration-production-proof.py"
        diagnostic.write_text(
            "from pathlib import Path\n"
            "Path('production-diagnostic-ran').touch()\n"
            "raise SystemExit(9)\n")
        marker = root / "production-diagnostic-ran"
        ordinary = root / "ops/ordinary-selftest.py"
        for hosted, portable in (("false", "0"), ("false", "1"), ("true", "0")):
            for status in (0, 1):
                marker.unlink(missing_ok=True)
                ordinary.write_text(f"print('ordinary suite ran')\nraise SystemExit({status})\n")
                rc, out = run(["--strict", "--only", "gates"], root=root,
                              env={"GITHUB_ACTIONS": hosted, "CARR_CI_PORTABLE_ONLY": portable})
                case = f"hosted={hosted}, portable={portable}, ordinary exit={status}"
                check(f"gates never launch the production diagnostic ({case})",
                      not marker.exists(), out)
                check(f"gates still run ordinary selftests ({case})",
                      "ordinary suite ran" in out, out)
                check(f"gates preserve the ordinary selftest verdict ({case})", rc == status, out)


def test_gates_report_every_completed_result_after_a_timeout():
    with ci_fixture() as root:
        ci = root / "ops/ci.sh"
        ci.write_text(ci.read_text().replace("CI_SELFTEST_TIMEOUT_SECONDS=120",
                                            "CI_SELFTEST_TIMEOUT_SECONDS=1"))
        (root / "hooks/gate-integrity.py").write_text("raise SystemExit(0)\n")
        (root / "tools/test_migration_fixture.py").write_text("print('later tool passed')\n")
        cases = {
            "a-timeout": f"import time; print('first timeout {FIXTURE_TOKEN}', flush=True); time.sleep(10)",
            "b-failure": "print('ordinary assertion failed'); raise SystemExit(7)",
            "c-success": "print('later success passed')",
            "d-skip": "print('fixture unavailable'); raise SystemExit(78)",
            "e-timeout": "import time; print('second timeout', flush=True); time.sleep(10)",
        }
        for name, source in cases.items():
            (root / f"ops/{name}-selftest.py").write_text(source + "\n")
        marker = root / "shell-started"
        shell = root / "tools/test_timeout_fixture.sh"
        shell.write_text(f"#!/bin/sh\ntouch {shlex.quote(str(marker))}\n")
        shell.chmod(0o755)
        rc, out = run(["--strict", "--only", "gates"],
                      env={"CARR_CI_GATE_JOBS": "1"}, root=root)
        check("timeout keeps the gates verdict red", rc == 1, out)
        check("every completed timeout is reported distinctly",
              "TIMEOUT:a-timeout-selftest.py" in out
              and "TIMEOUT:e-timeout-selftest.py" in out, out)
        check("a later assertion failure reaches the verdict",
              "ordinary assertion failed" in out and " b-failure-selftest.py" in out
              and "TIMEOUT:b-failure" not in out, out)
        check("later successful suites remain visible",
              "later success passed" in out and "later tool passed" in out, out)
        check("a later unconfigured suite is reported",
              "d-skip-selftest.py" in out and "NOT CONFIGURED (exit 78)" in out, out)
        check("completed Python work is not described as aborted",
              "aborting remaining gate selftests" not in out, out)
        check("timeout diagnostics pass through redaction",
              FIXTURE_TOKEN not in out and "<redacted:github-token:40 chars>" in out)
        check("unstarted shell suites remain withheld after timeout", not marker.exists(), out)


def test_failed_gate_logs_survive_exit():
    with ci_fixture() as root, tempfile.TemporaryDirectory() as logs:
        (root / "hooks/gate-integrity.py").write_text("raise SystemExit(0)\n")
        (root / "ops/a-failure-selftest.py").write_text(
            "print('first evidence\\n' + 'middle\\n' * 250 + 'last evidence'); raise SystemExit(7)\n")
        command = ["bash", str(root / "ops/ci.sh"), "--strict", "--only", "gates"]
        processes = [subprocess.Popen(command, cwd=root,
                     env={**fixture_env(), "RUNNER_TEMP": logs},
                     stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for _ in range(2)]
        for process in processes:
            output, error = process.communicate(timeout=30)
            check("deliberate gate failure remains red", process.returncode == 1, output + error)
        retained = list(pathlib.Path(logs).glob("carr-ci.*"))
        check("concurrent runs retain separate evidence directories", len(retained) == 2)
        for directory in retained:
            log = directory / "gate-a-failure-selftest.py.log"
            check("full failed log survives ci.sh exit", log.is_file()
                  and "first evidence" in log.read_text() and "last evidence" in log.read_text()
                  and len(log.read_text().splitlines()) == 252)
            check("passing gate logs are removed", not (directory / "safety-hooks.log").exists()
                  and not (directory / "gate-test_migration_fixture.py.log").exists())
        ci = root / "ops/ci.sh"
        ci.write_text(ci.read_text().replace("CI_SELFTEST_TIMEOUT_SECONDS=120",
                                            "CI_SELFTEST_TIMEOUT_SECONDS=1"))
        (root / "ops/a-failure-selftest.py").write_text(
            f"import time; print('timeout evidence {FIXTURE_TOKEN}', flush=True); time.sleep(10)\n")
        result = subprocess.run(command, cwd=root,
                                env={**fixture_env(), "RUNNER_TEMP": logs,
                                     "GITHUB_ACTIONS": "true", "CARR_CI_GATE_JOBS": "1"},
                                capture_output=True, text=True, timeout=30)
        timeout_dirs = set(pathlib.Path(logs).glob("carr-ci.*")) - set(retained)
        check("timeout preserves its exit verdict and evidence", result.returncode == 1
              and "TIMEOUT:a-failure" in result.stdout and len(timeout_dirs) == 1)
        for directory in timeout_dirs:
            raw = directory / "gate-a-failure-selftest.py.log"
            redacted = directory / "gate-a-failure-selftest.py.log.redacted"
            check("hosted timeout retains complete redacted artifact", raw.is_file()
                  and redacted.is_file() and "timeout evidence" in redacted.read_text()
                  and FIXTURE_TOKEN not in redacted.read_text()
                  and "<redacted:github-token:40 chars>" in redacted.read_text())
        retained += list(timeout_dirs)
        result = subprocess.run(["bash", str(root / "ops/ci.sh"), "--strict", "--only", "freshness"],
                                cwd=root, env={**fixture_env(), "RUNNER_TEMP": logs},
                                capture_output=True, text=True, timeout=15)
        check("passing runs remove their log directory", result.returncode == 0
              and set(pathlib.Path(logs).glob("carr-ci.*")) == set(retained))


def test_gates_selftests_have_a_process_group_watchdog():
    helper = REPO / "bin" / "with-timeout.py"
    with tempfile.TemporaryDirectory() as td:
        log = pathlib.Path(td) / "gate.log"
        p = subprocess.run(
            [sys.executable, str(helper), "0.2", sys.executable, "-c",
             "import time; print('fixture-output', flush=True); time.sleep(2)"],
            capture_output=True, text=True, timeout=10)
        log.write_text(p.stdout + p.stderr)
        text = log.read_text()
        check("a hanging gate fixture exits 124", p.returncode == 124,
              f"rc={p.returncode}")
        check("the timeout is named in the captured gate log",
              "with-timeout: TIMEOUT" in text and "fixture-output" in text,
              text[-300:])

    for code in (0, 7):
        p = subprocess.run(
            [sys.executable, str(helper), "10", sys.executable, "-c",
             f"raise SystemExit({code})"],
            capture_output=True, text=True, timeout=10)
        check(f"ordinary exit {code} remains unchanged", p.returncode == code,
              f"rc={p.returncode}")

FIXTURE_GATE = "hooks/zz-ci-fixture-gate.py"
FIXTURE_RANGE = "CI-SELFTEST-FLOOR-FIXTURE-RANGE"
FLOOR_BUDGET_SECONDS = 60

def _ci_function_body(name, until):
    src = CI.read_text(encoding="utf-8")
    start = src.index(f"\n{name}() {{")
    return src[start:src.index(f"\n{until}() {{", start)]

def _push_floor_body():
    return _ci_function_body("check_pushfloor", "check_dependency")

@contextlib.contextmanager
def _stub_git_answering_the_floor(changed_paths, *, main_paths=None, added_paths=(),
                                  main_tree_paths=(), main_readable=True):
    real = shutil.which("git")
    if main_paths is None:
        main_paths = changed_paths
    quoted_main = " ".join(shlex.quote(path) for path in main_paths)
    quoted_added = " ".join(shlex.quote(path) for path in added_paths)
    quoted_tree = " ".join(shlex.quote(path) for path in main_tree_paths)
    quoted_main_added = " ".join(shlex.quote(path) for path in added_paths
                                  if path in main_paths and path not in main_tree_paths)
    main_exit = "" if main_readable else "exit 7; "
    with tempfile.TemporaryDirectory(prefix="ci-selftest-stub-git-") as td:
        stub = pathlib.Path(td) / "git"
        stub.write_text(
            "#!/bin/sh\n"
            'case " $* " in *" diff --name-only origin/main HEAD "*)\n'
            f'  {main_exit}printf "%s\\n" {quoted_main}; exit 0 ;;\n'
            '  *" diff --diff-filter=A --name-only origin/main -- "*)\n'
            f'  {main_exit}printf "%s\\n" {quoted_main_added}; exit 0 ;;\n'
            '  *" ls-tree -r --name-only origin/main "*)\n'
            f'  {main_exit}printf "%s\\n" {quoted_tree}; exit 0 ;;\n'
            f'*" {FIXTURE_RANGE} "*)\n'
            '  case " $* " in *--diff-filter=ACMR*)\n'
            f'    printf "%s\\n" {" ".join(changed_paths)}; exit 0 ;;\n'
            f'  *--diff-filter=ACR*) printf "%s\\n" {quoted_added}; exit 0 ;;\n'
            '  esac ;;\n'
            'esac\n'
            f'exec {shlex.quote(real or "git")} "$@"\n'
        )
        stub.chmod(0o755)
        yield {"PATH": f"{td}{os.pathsep}{os.environ.get('PATH', '')}"}

def test_push_floor_defers_the_gates_class_instead_of_running_it():
    body = _push_floor_body()
    if "check_gates" in body:
        check("the floor no longer calls the gates class as a fallback", False,
              "check_pushfloor calls check_gates — refusing to run the class to confirm it")
        return
    check("the floor no longer calls the gates class as a fallback", True)

    t0 = time.monotonic()
    with _stub_git_answering_the_floor([FIXTURE_GATE]) as stub_env:
        try:
            rc, out = run(["--only", "pushfloor"],
                          env={"CARR_CI_RANGE": FIXTURE_RANGE, **stub_env},
                          timeout=FLOOR_BUDGET_SECONDS)
        except subprocess.TimeoutExpired:
            check("the floor returns promptly on the unpaired-gate shape", False,
                  f"still running after {FLOOR_BUDGET_SECONDS}s")
            return
    elapsed = time.monotonic() - t0

    gate_name = pathlib.Path(FIXTURE_GATE).stem
    check("the unpaired gate is still detected and named",
          gate_name in out and "deferred" in out, out[-600:])
    check("the deferral says where the class actually runs",
          "hosted" in out.lower(), out[-600:])
    check("the gates class produced no verdict on the push path",
          not re.search(r"(OK|FAIL|SKIP)\s+gates\b", out), out[-600:])
    check("the floor returns promptly on the unpaired-gate shape",
          elapsed < FLOOR_BUDGET_SECONDS, f"{elapsed:.0f}s")
    check("naming a deferred gate is not itself a failure", rc == 0, f"rc={rc}")

def test_push_floor_distinguishes_imported_main_paths_from_branch_changes():
    inherited_name = "mcp-server/test/doctorcre-v5-review.test.mjs"
    inherited_gate = "hooks/zz-ci-fixture-inherited-gate.py"
    with _stub_git_answering_the_floor(
            [inherited_name, inherited_gate, FIXTURE_GATE],
            main_paths=[FIXTURE_GATE], added_paths=[inherited_name],
            main_tree_paths=[inherited_name, inherited_gate]) as stub_env:
        rc, out = run(["--only", "pushfloor"],
                      env={"CARR_CI_RANGE": FIXTURE_RANGE, **stub_env},
                      timeout=FLOOR_BUDGET_SECONDS)
    check("importing admitted main paths does not fail the push floor", rc == 0, out[-900:])
    check("main-identical gates are excluded but the branch's gate remains checked",
          pathlib.Path(inherited_gate).stem not in out
          and pathlib.Path(FIXTURE_GATE).stem in out, out[-900:])
    owned_name = "ops/report-v2.json"
    with _stub_git_answering_the_floor(
            [owned_name], main_paths=[owned_name], added_paths=[owned_name]) as stub_env:
        rc, out = run(["--only", "pushfloor"],
                      env={"CARR_CI_RANGE": FIXTURE_RANGE, **stub_env},
                      timeout=FLOOR_BUDGET_SECONDS)
    check("a branch-owned forbidden filename is still refused",
          rc != 0 and owned_name in out and "path-hygiene" in out, out[-900:])
    historical_gate = "hooks/zz-ci-historical-v2.py"
    with _stub_git_answering_the_floor(
            [historical_gate], main_paths=[historical_gate],
            added_paths=[historical_gate], main_tree_paths=[historical_gate]) as stub_env:
        rc, out = run(["--only", "pushfloor"],
                      env={"CARR_CI_RANGE": FIXTURE_RANGE, **stub_env},
                      timeout=FLOOR_BUDGET_SECONDS)
    check("editing an imported historical path is checked without rejudging its name",
          rc == 0 and pathlib.Path(historical_gate).stem in out, out[-900:])
    with _stub_git_answering_the_floor(
            [owned_name], added_paths=[owned_name], main_readable=False) as stub_env:
        rc, out = run(["--only", "pushfloor"],
                      env={"CARR_CI_RANGE": FIXTURE_RANGE, **stub_env},
                      timeout=FLOOR_BUDGET_SECONDS)
    check("an unreadable main preserves the full push-floor scope",
          rc != 0 and owned_name in out and "path-hygiene" in out, out[-900:])


def test_push_floor_reports_missing_atlas_packages_without_blaming_registry():
    with ci_fixture() as root:
        tests = root / "mcp-server/test"
        tests.mkdir()
        paths = [tests / f"atlas-inventory-graph-{kind}.test.mjs"
                 for kind in ("read", "web")]
        source = "import 'atlas-fixture-dependency';\nthrow new Error('Atlas contract fixture failure');\n"
        for path in paths:
            path.write_text(source)
        changed = ["mcp-server/src/scac-mutation-registry.current.generated.js"]
        with _stub_git_answering_the_floor(changed) as stub_env:
            rc, out = run(["--only", "pushfloor"],
                          env={"CARR_CI_RANGE": FIXTURE_RANGE, **stub_env}, root=root)
            check("missing Atlas packages fail with the actual dependency and npm ci",
                  rc != 0 and "Cannot find package 'atlas-fixture-dependency'" in out
                  and "npm ci" in out and "mcp-server" in out, out[-1200:])
            check("missing Atlas packages do not prescribe registry repair",
                  "active registry changed" not in out, out[-1200:])
            package = root / "mcp-server/node_modules/atlas-fixture-dependency"
            package.mkdir(parents=True)
            (package / "package.json").write_text(
                '{"type":"module","exports":"./index.js"}')
            (package / "index.js").write_text("export const ready = true;\n")
            rc, out = run(["--only", "pushfloor"],
                          env={"CARR_CI_RANGE": FIXTURE_RANGE, **stub_env}, root=root)
            check("installed packages preserve the Atlas contract failure and remedy",
                  rc != 0 and "active registry changed" in out and "npm ci" not in out, out[-1200:])
            for path in paths:
                path.write_text("import 'atlas-fixture-dependency';\n")
            rc, out = run(["--only", "pushfloor"],
                          env={"CARR_CI_RANGE": FIXTURE_RANGE, **stub_env}, root=root)
            check("passing Atlas tests still pass the push floor", rc == 0, out[-1200:])


def _hosted_workflow(filename="ci.yml"):
    """Workflow as a dict, parsed by the js-yaml the server already carries."""
    import json
    out = subprocess.run(
        ["node", "-e",
         "const y=require('js-yaml');const fs=require('fs');"
         "process.stdout.write(JSON.stringify(y.load(fs.readFileSync(process.argv[1],'utf8'))))",
         str(REPO / ".github" / "workflows" / filename)],
        cwd=REPO / "mcp-server", capture_output=True, text=True, timeout=60)
    check(f"{filename} parses", out.returncode == 0, out.stderr[-300:])
    return json.loads(out.stdout) if out.returncode == 0 else {}


def test_hosted_draft_lifecycle():
    """Execute YAML conditions and verdict commands against same-head event fixtures.

    This models scheduler inputs, not GitHub's remote check selection.
    Ready transitions must schedule a full run even when the SHA is unchanged.
    """
    def evaluate(expression, context):
        expression = str(expression)
        if expression.count("${{") > 1:
            return re.sub(r"\$\{\{(.*?)\}\}",
                          lambda match: str(evaluate(match.group(1), context)), expression)
        expression = expression.removeprefix("${{").removesuffix("}}").strip()
        script = """const [expr, ctx] = JSON.parse(process.argv[1]);
        const {github, needs, cancelled: stopped = false} = ctx;
        const always=()=>true, cancelled=()=>stopped, success=()=>!stopped;
        const fromJSON=JSON.parse;
        process.stdout.write(JSON.stringify(eval(expr)));"""
        return json.loads(subprocess.check_output(
            ["node", "-e", script, json.dumps([expression, context])], text=True))

    for filename, lanes, verdict_name, command in (
            ("ci.yml", ("scope", "classes", "migration-fixtures", "fallow"),
             "checks", "verdict"),
            ("doc-drift.yml", ("check",), "verdict", "drift-verdict")):
        wf = _hosted_workflow(filename)
        events = wf["on"]
        expected_actions = {"opened", "synchronize", "reopened", "converted_to_draft", "ready_for_review"}
        check(f"{filename}: only intended PR lifecycle events schedule runs",
              set(events["pull_request"]["types"]) == expected_actions)
        check(f"{filename}: all non-PR full-suite triggers survive",
              {"merge_group", "schedule", "workflow_dispatch"} <= set(events))
        jobs = wf["jobs"]
        verdict = jobs.get(verdict_name, {})
        check(f"{filename}: verdict always evaluates and waits for every lane",
              verdict.get("if") == "${{ always() }}" and set(verdict.get("needs", [])) == set(lanes))
        if not verdict:
            continue
        step = next(st for st in verdict["steps"] if st.get("run") == f"python3 ops/ci-evidence.py {command}")
        needs = {name: {"result": "success"} for name in lanes}
        if "scope" in needs:
            needs["scope"]["outputs"] = {"full": "true"}
        def context(event="pull_request", draft=False, action="synchronize", sha="a" * 40):
            return {"github": {"workflow": wf["name"], "event_name": event,
                               "run_id": 41, "sha": sha,
                               "event": {"action": action, "pull_request": {
                                   "number": 12, "draft": draft, "head": {"sha": sha}}}},
                    "needs": needs}
        groups = []
        with tempfile.TemporaryDirectory() as directory:
            # No source change between these events. A ready success cannot
            # turn the following draft or cancelled ready run into success.
            for draft, action, sha in (
                    (True, "opened", "a" * 40), (False, "ready_for_review", "a" * 40),
                    (True, "converted_to_draft", "a" * 40), (False, "ready_for_review", "a" * 40),
                    (False, "reopened", "a" * 40), (False, "synchronize", "a" * 40),
                    (False, "synchronize", "b" * 40)):
                if action not in events["pull_request"]["types"]:
                    continue
                ctx = context(draft=draft, action=action, sha=sha)
                groups.append(evaluate(wf["concurrency"]["group"], ctx))
                check(f"{filename}: {action} draft={draft} SHA={sha[:7]} schedules correct lanes",
                      all(bool(evaluate(jobs[name].get("if", "true"), ctx)) == (not draft)
                          for name in lanes))
                result = subprocess.run(["bash", "-e", "-c", step["run"]], cwd=REPO,
                    env={**scrubbed_env(), "CI_NEEDS": json.dumps(needs),
                         "CI_DRAFT": str(evaluate(step["env"]["CI_DRAFT"], ctx)).lower(),
                         "GITHUB_STEP_SUMMARY": str(pathlib.Path(directory) / "summary")},
                    text=True, capture_output=True, timeout=10)
                check(f"{filename}: {action} draft={draft} verdict is explicit",
                      result.returncode == (1 if draft else 0)
                      and (not draft or "deferred; no full-suite evidence" in result.stdout))
                if filename == "ci.yml" and draft:
                    check("draft cannot record or upload a full-suite receipt",
                          all(not evaluate(st.get("if", "true"), ctx) for st in verdict["steps"]
                              if "record --output" in st.get("run", "") or st.get("with", {}).get("name") == "ci-tree-evidence"))
            check(f"{filename}: same PR uses one concurrency group across readiness and synchronize",
                  len(set(groups + [evaluate(wf["concurrency"]["group"], context(sha="b" * 40))])) == 1
                  and evaluate(wf["concurrency"]["cancel-in-progress"], context()))
            check(f"{filename}: different PRs have independent groups",
                  groups[0] != evaluate(wf["concurrency"]["group"], {
                      **context(), "github": {**context()["github"], "event": {"pull_request": {"number": 13}}}}))
            for event in ("merge_group", "schedule", "workflow_dispatch"):
                ctx = context(event=event, draft=True)
                check(f"{filename}: {event} runs every lane outside PR cancellation group",
                      all(evaluate(jobs[name].get("if", "true"), ctx) for name in lanes)
                      and not evaluate(wf["concurrency"]["cancel-in-progress"], ctx)
                      and evaluate(wf["concurrency"]["group"], ctx) != groups[0])
            for lane in lanes:
                for state in ("failure", "cancelled", "skipped", "timed_out", "missing"):
                    broken = {name: dict(value) for name, value in needs.items()}
                    if state == "missing":
                        broken.pop(lane)
                    else:
                        broken[lane]["result"] = state
                    result = subprocess.run(["bash", "-e", "-c", step["run"]], cwd=REPO,
                        env={**scrubbed_env(), "CI_NEEDS": json.dumps(broken), "CI_DRAFT": "false",
                             "GITHUB_STEP_SUMMARY": str(pathlib.Path(directory) / "summary")},
                        text=True, capture_output=True, timeout=10)
                    check(f"{filename}: ready {lane}={state} blocks success at same SHA",
                          result.returncode != 0)
            ctx = {**context(), "cancelled": True}
            check(f"{filename}: cancelled run still evaluates its required verdict",
                  evaluate(verdict["if"], ctx))
            # Status functions are valid in step conditions, not step env.
            guard = next((st for st in verdict['steps'] if st.get('if') == '${{ cancelled() }}'), {})
            result = subprocess.run(["bash", "-e", "-c", guard.get("run", "true")], cwd=REPO,
                                    text=True, capture_output=True, timeout=10)
            check(f"{filename}: cancellation after successful lanes cannot green the verdict",
                  bool(guard) and evaluate(guard["if"], ctx) and result.returncode != 0
                  and verdict['steps'].index(guard) == len(verdict['steps']) - 1)
        if filename == "ci.yml":
            matrix = jobs["classes"]["strategy"]["matrix"]
            check("ready matrix exclusions are static and cannot trim classes on draft state",
                  isinstance(matrix["exclude"], list))


def test_hosted_zsh_setup_does_not_refresh_working_indexes():
    """PR 1465 spent its entire job budget in apt update before any class ran.

    Execute the workflow's setup with a synthetic apt, not a second installer.
    A working install must never refresh; stale indexes must still be repaired;
    runs 37826917141, 37820191123, 37803579466, 37793286211, 37781682483,
    37762481248 and 37683204623 had Install-zsh timeouts. Run 37762481248
    confirms a mid-dpkg timeout (Reading database at 85%); the other logs do
    not establish a network cause. Synthetic download failures exercise the
    bounded fallback; an unavailable package must fail setup.
    """
    wf = _hosted_workflow()
    setup = next(st for st in wf["jobs"]["classes"]["steps"]
                 if st.get("name") == "Install zsh")
    # Three download attempts, one refresh, one unpack; each timeout also
    # permits five seconds to kill its process group. Backoff occurs after
    # attempts one and two only.
    check("hosted zsh setup uses the shared installer", setup["run"] == "ops/ci-zsh.sh")
    installer_source = (REPO / "ops/ci-zsh.sh").read_text()
    backoff = int(setup["env"]["ZSH_RETRY_BACKOFF"])
    attempts = len(re.search(r"for attempt in ([0-9 ]+); do", installer_source).group(1).split())
    download, refresh, unpack = [int(grace) + int(limit) for grace, limit in
                                re.findall(r"timeout --kill-after=(\d+)s (\d+)s", installer_source)]
    verification = int(re.search(r"signal\.alarm\((\d+)\)", installer_source).group(1))
    # One verification before each download and one immediately before install.
    budget_seconds = attempts * download + refresh + unpack + sum(range(1, attempts)) * backoff + (attempts + 1) * verification + 30
    deadline_minutes = (budget_seconds + 59) // 60
    check("zsh setup deadline equals its retry budget plus bounded overhead",
          setup.get("timeout-minutes") == deadline_minutes,
          {"expected": deadline_minutes, "actual": setup.get("timeout-minutes")})
    cache = next(st for st in wf["jobs"]["classes"]["steps"]
                 if st.get("name") == "Restore zsh package archives")
    check("zsh archives use a pinned cache action and the install directory",
          cache["uses"] == "actions/cache@5a3ec84eff668545956fd18022155c47e93e2684"
          and cache["with"]["path"] == setup["env"]["ZSH_ARCHIVE_DIR"] + "/*.deb"
          and wf["jobs"]["classes"]["runs-on"] == "ubuntu-24.04")
    canary = _hosted_workflow("main-canary.yml")["jobs"]["classes"]["steps"]
    canary_setup = next(st for st in canary if st.get("name") == "Install zsh")
    check("PR and canary execute the same bounded zsh installer",
          all(canary_setup[k] == setup[k] for k in ("run", "env", "timeout-minutes")))
    fixture_steps = wf["jobs"]["migration-fixtures"]["steps"]
    fixture_setup = next((st for st in fixture_steps if st.get("name") == "Install zsh"), {})
    check("equivalence installs the restore rehearsal interpreter before its proof",
          all(fixture_setup.get(k) == setup[k] for k in ("run", "env", "timeout-minutes"))
          and fixture_setup.get("if") == "matrix.part == 'equivalence'"
          and fixture_steps.index(fixture_setup) < next(
              i for i, st in enumerate(fixture_steps)
              if st.get("name") == "Prove independent migration fixture"))
    with tempfile.TemporaryDirectory(prefix="ci-zsh-setup-") as tmp:
        fixture = pathlib.Path(tmp)
        sudo = fixture / "sudo"
        sudo.write_text("#!" + sys.executable + "\n" + '''
import json, os, pathlib, sys, time
if sys.argv[1] == "rm":
    os.execvp("rm", sys.argv[1:])
log = pathlib.Path(os.environ["CI_SETUP_CALLS"])
calls = json.loads(log.read_text()) if log.exists() else []
calls.append(sys.argv[1:])
log.write_text(json.dumps(calls))
args = sys.argv[1:]
mode = os.environ["CI_SETUP_FIXTURE"]
kind = ("download" if "--download-only" in args else
        "install" if "--no-download" in args else
        "update" if "update" in args else "unknown")
if mode == "working" and kind == "update":
    sys.exit(91)
if mode == "stale" and len(calls) == 1:
    sys.exit(100)
if mode == "mirror-flaky" and len(calls) in (1, 3):
    sys.exit(100)
if mode == "unavailable" and kind in ("download", "update"):
    sys.exit(100)
if mode == "hung-download" and kind == "download":
    time.sleep(30)
if mode == "install-failed" and kind == "install":
    sys.exit(100)
''')
        sudo.chmod(0o755)
        zsh = fixture / "zsh"
        zsh.write_text("#!/bin/sh\n[ \"$CI_SETUP_FIXTURE\" = preinstalled ]\n")
        zsh.chmod(0o755)
        timeout = fixture / "timeout"
        timeout.write_text("#!" + sys.executable + "\n" + '''
import json, os, pathlib, subprocess, sys
log = pathlib.Path(os.environ["CI_SETUP_DEADLINES"])
calls = json.loads(log.read_text()) if log.exists() else []
calls.append(sys.argv[1:3])
log.write_text(json.dumps(calls))
assert sys.argv[1] == "--kill-after=5s"
assert 0 < int(sys.argv[2].removesuffix("s")) <= 120
try:
    result = subprocess.run(sys.argv[3:], timeout=1)
    sys.exit(result.returncode)
except subprocess.TimeoutExpired:
    sys.exit(124)
''')
        timeout.chmod(0o755)
        failing = ("unavailable", "hung-download", "install-failed")
        for mode, expected in (("preinstalled", []),
                               ("working", ["download", "install"]),
                               ("stale", ["download", "update", "download", "install"]),
                               ("mirror-flaky", ["download", "update", "download", "download", "install"]),
                               ("unavailable", ["download", "update", "download", "download"]),
                               ("hung-download", ["download", "update", "download", "download"]),
                               ("install-failed", ["download", "install"])):
            log = fixture / (mode + ".json")
            deadlines_log = fixture / (mode + "-deadlines.json")
            env = scrubbed_env()
            env.update(PATH=str(fixture) + os.pathsep + os.environ["PATH"],
                       CI_SETUP_CALLS=str(log), CI_SETUP_FIXTURE=mode,
                       CI_SETUP_DEADLINES=str(deadlines_log), ZSH_RETRY_BACKOFF="0",
                       ZSH_ARCHIVE_DIR=str(fixture / "archives"))
            try:
                process = subprocess.Popen(["bash", "-e", "-o", "pipefail", "-c", installer_source],
                                           cwd=fixture, env=env, stdout=subprocess.PIPE,
                                           stderr=subprocess.PIPE, text=True, start_new_session=True)
                stdout, stderr = process.communicate(timeout=8)
                ran = subprocess.CompletedProcess(process.args, process.returncode, stdout, stderr)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.communicate()
                check(f"zsh setup {mode} finishes within its process budget", False)
                continue
            calls = json.loads(log.read_text()) if log.exists() else []
            deadlines = json.loads(deadlines_log.read_text()) if deadlines_log.exists() else []
            actions = [("download" if "--download-only" in args else
                        "install" if "--no-download" in args else
                        "update" if "update" in args else "unknown")
                       for args in calls]
            check(f"zsh setup {mode} uses the required install/refresh path",
                  actions == expected, actions)
            check(f"zsh setup {mode} propagates its outcome",
                  (ran.returncode != 0) == (mode in failing),
                  ran.returncode)
            check(f"zsh setup {mode} bounds every apt process",
                  len(deadlines) == len(calls) and
                  sum(int(args[1].removesuffix("s")) + 5 for args in deadlines) < 480,
                  deadlines)
            check(f"zsh setup {mode} bounds every apt network request",
                  all(args[0] == "apt-get" and
                      all(option in args for option in ("Acquire::Retries=2",
                          "Acquire::http::Timeout=15", "Acquire::https::Timeout=15",
                          "DPkg::Lock::Timeout=30"))
                      for args in calls), calls)
            check(f"zsh setup {mode} uses cached archives without recommends",
                  all(any(option.startswith("Dir::Cache::archives=")
                          and option != "Dir::Cache::archives=" + str(fixture / "archives")
                          for option in args) and "--no-install-recommends" in args
                      for args in calls if "update" not in args), calls)
            check(f"zsh setup {mode} requires the zsh package when installing",
                  all("zsh" in args for args in calls if "update" not in args), calls)
            check(f"zsh setup {mode} never unpacks without a completed download",
                  all(calls.index(args) > 0 and
                      any("--download-only" in prior for prior in calls[:calls.index(args)])
                      for args in calls if "--no-download" in args), calls)


def test_hosted_zsh_cache_requires_authenticated_bytes():
    installer = (REPO / "ops/ci-zsh.sh").read_text()
    with tempfile.TemporaryDirectory(prefix="ci-zsh-cache-") as tmp:
        fixture = pathlib.Path(tmp)
        for name, source in {
            "zsh": "#!/bin/sh\nexit 1\n",
            "timeout": '#!/bin/sh\nshift 2\nexec "$@"\n',
            "sudo": '#!/bin/sh\nexec "$@"\n',
            "apt-cache": "#!" + sys.executable + "\n" + '''
import hashlib, os, pathlib, sys, time
mode = os.environ["CI_CACHE_MODE"]
downloaded = pathlib.Path(os.environ["CI_CACHE_LOG"]).exists()
if ((mode == "metadata-timeout" and not downloaded)
        or (mode == "install-metadata-timeout" and downloaded)):
    time.sleep(31)
if (os.environ["CI_CACHE_MODE"] == "missing-metadata"
        and not pathlib.Path(os.environ["CI_CACHE_LOG"]).exists()):
    sys.exit(0)
if os.environ["CI_CACHE_MODE"] == "metadata-error":
    sys.exit(2)
print("Package: zsh\\nVersion: 1\\nArchitecture: amd64\\nSHA256: " +
      hashlib.sha256(b"trusted-zsh-package").hexdigest())
''',
            "apt-get": "#!" + sys.executable + "\n" + '''
import json, os, pathlib, stat, sys
root = pathlib.Path(next(arg.split("=", 1)[1] for arg in sys.argv
                         if arg.startswith("Dir::Cache::archives=")))
archive = root / "zsh_1_amd64.deb"
log = pathlib.Path(os.environ["CI_CACHE_LOG"])
state = json.loads(log.read_text()) if log.exists() else {}
if "--download-only" in sys.argv:
    state["restored"] = {p.name: p.read_text() for p in root.glob("*.deb")}
    state.setdefault("download_archives", []).append(state["restored"])
    # Model apt's filename+size shortcut instead of repairing a bad cache.
    if not archive.exists():
        archive.write_bytes(b"trusted-zsh-package")
    mode = os.environ["CI_CACHE_MODE"]
    if mode in ("replace-after-verification", "replace-during-retries"):
        original = pathlib.Path(os.environ["ZSH_ARCHIVE_DIR"]) / archive.name
        original.write_bytes(b"altered-zsh-package")
    if mode == "replace-during-retries" and len(state["download_archives"]) < 3:
        log.write_text(json.dumps(state))
        sys.exit(100)
    if mode == "replace-downloaded-archive":
        archive.chmod(0o600)
        archive.write_bytes(b"altered-zsh-package")
elif "--no-download" in sys.argv:
    state["installed"] = archive.read_text()
    state["install_directory"] = str(root)
    state["directory_mode"] = stat.S_IMODE(root.stat().st_mode)
    state["archive_mode"] = stat.S_IMODE(archive.stat().st_mode)
log.write_text(json.dumps(state))
''',
        }.items():
            path = fixture / name
            path.write_text(source)
            path.chmod(0o755)
        for mode in ("valid", "same-size-tampered", "other-version", "old-version-filename",
                     "missing-metadata", "metadata-error", "metadata-timeout",
                     "install-metadata-timeout", "replace-after-verification",
                     "replace-during-retries", "replace-downloaded-archive"):
            archives = fixture / mode
            archives.mkdir()
            archive = archives / ("zsh_2_amd64.deb" if mode == "old-version-filename" else "zsh_1_amd64.deb")
            restored = (b"trusted-zsh-package" if mode in ("valid", "old-version-filename",
                        "replace-after-verification", "replace-during-retries",
                        "replace-downloaded-archive", "metadata-timeout",
                        "install-metadata-timeout") else
                        b"older-zsh-package!!" if mode == "other-version" else b"altered-zsh-package")
            archive.write_bytes(restored)
            if mode == "metadata-timeout":
                (archives / "zsh-common_1_all.deb").write_bytes(b"cached-zsh-common")
            log = fixture / (mode + ".json")
            env = scrubbed_env()
            env.update(PATH=str(fixture) + os.pathsep + os.environ["PATH"],
                       ZSH_ARCHIVE_DIR=str(archives), ZSH_RETRY_BACKOFF="0",
                       CI_CACHE_MODE=mode, CI_CACHE_LOG=str(log))
            ran = subprocess.run(["bash", "-c", installer], cwd=fixture, env=env,
                                 capture_output=True, text=True, timeout=40)
            state = json.loads(log.read_text()) if log.exists() else {}
            if mode == "metadata-error":
                check("zsh cache verification error stops before apt can consume archives",
                      ran.returncode != 0 and not state, {"rc": ran.returncode, "state": state})
                continue
            if mode == "replace-downloaded-archive":
                check("zsh refuses a same-size replacement of downloaded bytes before install",
                      ran.returncode != 0 and "installed" not in state, state)
                continue
            if mode == "install-metadata-timeout":
                check("zsh refuses downloaded bytes when their metadata verification times out",
                      ran.returncode != 0 and "installed" not in state, state)
                continue
            check(f"zsh cache {mode} installs authenticated package bytes",
                  ran.returncode == 0 and state.get("installed") == "trusted-zsh-package",
                  {"rc": ran.returncode, "state": state, "stderr": ran.stderr})
            expected = {"zsh_1_amd64.deb": "trusted-zsh-package"} if mode in (
                "valid", "replace-after-verification", "replace-during-retries") else {}
            check(f"zsh cache {mode} exposes only verified restored archives to apt",
                  state.get("restored") == expected, state)
            if mode in ("replace-after-verification", "replace-during-retries"):
                check(f"zsh cache {mode} isolates every retry from restored archive replacement",
                      all(archives == expected for archives in state.get("download_archives", [])), state)
                check(f"zsh cache {mode} installs from a private read-only archive set",
                      state.get("install_directory") != str(archives)
                      and state.get("directory_mode") == 0o700
                      and state.get("archive_mode") == 0o444, state)
                check(f"zsh cache {mode} removes the private installation directory",
                      not pathlib.Path(state.get("install_directory", str(archives))).exists(), state)
            elif mode != "valid":
                quarantined = archives / "quarantine" / archive.name
                check(f"zsh cache {mode} preserves rejected bytes in quarantine",
                      quarantined.is_file() and quarantined.read_bytes() == restored)
                if mode == "metadata-timeout":
                    remaining = archives / "quarantine/zsh-common_1_all.deb"
                    check("zsh quarantines remaining cached archives within the verification deadline",
                          remaining.is_file() and remaining.read_bytes() == b"cached-zsh-common")


def test_worktree_python_selection():
    with tempfile.TemporaryDirectory(prefix="carr-python-") as directory:
        root = pathlib.Path(directory)
        main, work = root / "main checkout", root / "worktree"
        main.mkdir()
        def git(*args):
            subprocess.run(["git", *args], cwd=main, env=fixture_env(),
                           check=True, capture_output=True)
        git("init")
        git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
            "commit", "--allow-empty", "-m", "fixture")
        git("worktree", "add", "-b", "fixture", str(work))
        fallback = root / "bin"
        fallback.mkdir()
        marker = root / "selected"
        for relative in ("ops/ci.sh", "ops/githooks/pre-push"):
            target = work / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(REPO / relative, target)
        for available in (False, True):
            interpreters = [(fallback / "python3", "fallback"),
                            (work / ".venv/bin/python", "worktree")]
            if available:
                interpreters.append((main / ".venv/bin/python", "main"))
            for path, name in interpreters:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(f"#!/bin/sh\nprintf '{name}' > '{marker}'\nexit 73\n")
                path.chmod(0o755)
            for relative, args, code in (("ops/ci.sh", ["--doc-drift"], 73),
                                         ("ops/githooks/pre-push", [], 1)):
                result = subprocess.run(["bash", str(work / relative), *args],
                    cwd=work, env={**fixture_env(), "PATH": str(fallback) + os.pathsep + os.defpath},
                    input="", text=True, capture_output=True, timeout=15)
                check(f"{relative} selects {'main venv' if available else 'python3 fallback'}",
                      result.returncode == code and marker.exists()
                      and marker.read_text() == ("main" if available else "fallback"), result.stderr)
                marker.unlink(missing_ok=True)


def main(argv=None):
    if (sys.argv[1:] if argv is None else argv) == ["--collection-only"]:
        test_every_test_file_in_the_tree_is_collected()
        test_each_class_runs_in_exactly_one_hosted_group()
        return 1 if any(not ok for _, ok, _ in RESULTS) else 0
    for fn in (test_failed_gate_logs_survive_exit, test_worktree_python_selection,
               test_freshness_checks_the_tree_without_requiring_main_ancestry,
               test_no_green_without_running,
               test_strict_turns_skip_into_failure,
               test_binding_reports_missing_wrangler_as_skipped,
               test_binding_preserves_config_and_boot_failures,
               test_unknown_class_refuses,
               test_hxprobe_runs_locked_checks_and_propagates_failures,
               test_gates_build_hxprobe_before_parallel_selftests,
               test_gate_shards_execute_a_disjoint_complete_inventory,
               test_postgres_path_boundary_rejects_platform_defaults,
               test_hosted_migration_ownership,
               test_hosted_draft_lifecycle,
               test_database_scope_receipts,
               test_migration_refuses_non_loopback,
               test_migration_fixtures_ignore_hosted_pr_event,
               test_secret_scanner_catches_and_respects_allow,
               test_dep_check_detects_a_stale_lock,
               test_types_class_catches_a_seeded_type_error,
               test_every_test_file_in_the_tree_is_collected,
               test_each_class_runs_in_exactly_one_hosted_group,
               test_the_walk_prunes_named_roots_only,
               test_a_failing_gates_output_is_printed_whole_when_it_is_short,
               test_a_long_failing_gate_log_is_tailed_and_still_redacted,
               test_fail_tail_withholds_the_window_when_it_cannot_redact,
               test_gates_never_run_the_production_diagnostic,
               test_gates_report_every_completed_result_after_a_timeout,
               test_gates_selftests_have_a_process_group_watchdog,
               test_push_floor_defers_the_gates_class_instead_of_running_it,
               test_push_floor_distinguishes_imported_main_paths_from_branch_changes,
               test_push_floor_reports_missing_atlas_packages_without_blaming_registry,
               test_hosted_zsh_setup_does_not_refresh_working_indexes,
               test_hosted_zsh_cache_requires_authenticated_bytes):
        try:
            fn()
        except Exception as exc:  # a crashing case is a failing case, never a silent skip
            check(f"{fn.__name__} raised", False, repr(exc))

    failed = sum(1 for _, ok, _ in RESULTS if not ok)
    print(f"\nci-selftest: {len(RESULTS) - failed}/{len(RESULTS)} passed")
    if failed:
        print("FAILED:")
        for label, ok, detail in RESULTS:
            if not ok:
                print(f"  - {label}  {detail}")
    return 1 if failed else 0

if __name__ == "__main__":
    sys.exit(main())
