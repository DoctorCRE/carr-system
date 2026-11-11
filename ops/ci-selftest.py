#!/usr/bin/env python3
# Exercise CI classes, fixture isolation, collection, and failure behavior.

import ast
import contextlib
import hashlib
import importlib.util
import inspect
import io
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

import pathlib, sys  # noqa: E401
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))  # repo root, for lib/
from lib.gate_check import Check  # noqa: E402

check = Check(
    ok="  PASS  {label}",
    detail="  ({detail})",
    record=lambda label, ok, detail: RESULTS.append((label, bool(ok), detail)),
)

ANSI = re.compile(r"\x1b\[[0-9;]*m")

@contextlib.contextmanager
def ci_fixture():
    with tempfile.TemporaryDirectory(prefix="carr-ci-fixture-") as directory:
        root = pathlib.Path(directory)
        for rel in (
            "ops/ci.sh", "ops/ci.py", "ops/proof-transport-check.py", "ops/ci_scope.py", "ops/ci-gates.py", "ops/ci-pushfloor.py", "ops/ci-migration.py", "ops/ci-secret.py",
            "ops/node_test_summary.py", "tools/codemap.py",
            "ops/ci-secret-scan.py", "ops/pii_guard.py", "ops/ci-dep-check.py", "ops/gitleaks.py", ".gitleaks.toml",
            "ops/git_env.py", "ops/no-client-deliverables-gate.py",
            "ops/hook-deletion-check.py", "ops/githooks/path-hygiene-check.py",
            "ops/migration-open-pr-gate.py", "ops/migration-order-gate.py",
            "tools/migration_claims.py", "tools/migration_number_contract.py",
            "tools/migration_source.py", "tools/migration_baseline.py",
            "ops/config/public-source-identities.v1.json", "ops/config/proof-process-allowlist.json",
            "bin/type-check.sh", "bin/with-timeout.py", "mypy.ini",
        ):
            target = root / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(REPO / rel, target)
        for name in ("pipelines", "exporters", "lib", "generators", "shared",
                     "fill-engine", "hooks", "migrations", "mcp-server"):
            (root / name).mkdir()
            if name not in {"migrations", "mcp-server", "lib"}:
                (root / name / (name.replace("-", "_") + "_fixture.py")).write_text("value: int = 1\n")
        (root / "ops/scac-mutation-inventory.ts").write_text("// Check the synthetic registry.\n")
        for name in ("release_reader.py", "release-reader-marker.json"):
            shutil.copy2(REPO / "lib" / name, root / "lib" / name)
        (root / "ops/migration-depth1.py").write_text("print('synthetic depth-1 acceptance passed')\n")
        (root / "ops/config/ci-check-scope.json").write_text('{}')
        requirements = "synthetic-package>=1\n"
        (root / "requirements.txt").write_text(requirements)
        source_hash = hashlib.sha256(b"synthetic-package>=1").hexdigest()
        (root / "requirements.lock").write_text(
            f"# requirements.txt sha256: {source_hash}\nsynthetic-package==1.0\n")
        tsc = REPO / "mcp-server/node_modules/typescript/bin/tsc"
        (root / "mcp-server/package.json").write_text(json.dumps({
            "private": True,
            "scripts": {"typecheck": f"node {shlex.quote(str(tsc))} --noEmit --allowJs --checkJs fixture.js",
                        "check:staged": "node -e \"process.stdout.write(\'synthetic staged gate\\n\')\""},
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

def test_changed_path_selection():
    import ci
    import ci_scope as scope
    all_classes = set(ci.CLASS_DESCRIPTIONS)
    for path in ('docs/guide.md', 'tools/pstack/routing.md', 'AGENTS.md', 'CLAUDE.md',
                 '.agents/skills/example/SKILL.md', 'README.md'):
        check(f'documentation selects lint only: {path}',
              scope.select_paths([path], all_classes) == {'lint'})
    for path in ('ops/example.py', '.github/workflows/ci.yml', 'contracts/a.json',
                 'migrations/123.sql', 'mcp-server/src/index.js', 'new/subsystem.py', 'package-lock.json',
                 'practice-plugin/package-lock.json', 'practice-plugin/package.json', 'ops/README.md',
                 'practice-plugin/bun.lock', 'practice-plugin/bun.lockb', 'practice-plugin/packages.lock.json'):
        check(f'unknown and infrastructure select everything: {path}',
              scope.select_paths([path], all_classes) == all_classes)
    check('product mapping selects affected classes',
          scope.select_paths(['practice-plugin/src/calculator.js'], all_classes)
          == scope.PRODUCT_CLASSES)
    check('mixed documentation and product retain product classes',
          scope.select_paths(['README.md', 'practice-plugin/src/calculator.js'], all_classes)
          == all_classes - {'dependency', 'hxprobe', 'migration'})
    with ci_fixture() as root:
        base = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, text=True).strip()
        path = root / 'docs/new.md'
        path.parent.mkdir(exist_ok=True)
        path.write_text('New guidance\n')
        subprocess.run(['git', 'add', 'docs/new.md'], cwd=root, env=fixture_env(), check=True)
        subprocess.run(['git', 'commit', '-qm', 'Guidance'], cwd=root, env=fixture_env(), check=True)
        head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, text=True).strip()
        event = root / 'event.json'
        event.write_text(json.dumps({'pull_request': {'base': {'sha': base}, 'head': {'sha': head}}}))
        env = {'GITHUB_EVENT_NAME': 'pull_request', 'GITHUB_EVENT_PATH': str(event)}
        check('real merge-base diff selects lint', scope.selection(all_classes, root=root, environ=env)['classes'] == ['lint'])
        for event_name in ('merge_group', 'push', 'schedule', 'workflow_dispatch'):
            check(f'{event_name} always selects full suite',
                  set(scope.selection(all_classes, root=root, environ={**env, 'GITHUB_EVENT_NAME': event_name})['classes']) == all_classes)
        (root / 'ops/ci-secret.py').write_text("print('synthetic secret qualification passed')\n")
        rc, output = run(['--strict', '--changed-from', base], root=root)
        check('shell controller executes lint alone for a real docs diff',
              rc == 0 and re.search(r'ci-timing: lint=\d+s\n', output) is not None, output)
        (root / 'ops/ci-secret.py').write_text("print('seeded credential refusal')\nraise SystemExit(1)\n")
        rc, output = run(['--strict', '--changed-from', base], root=root)
        check('documentation lint propagates credential qualification failure',
              rc == 1 and 'seeded credential refusal' in output, output)
        path.write_text('Guidance with trailing whitespace \n')
        subprocess.run(['git', 'add', 'docs/new.md'], cwd=root, env=fixture_env(), check=True)
        subprocess.run(['git', 'commit', '-qm', 'Whitespace defect'], cwd=root, env=fixture_env(), check=True)
        rc, output = run(['--strict', '--changed-from', base], root=root)
        check('documentation lint catches a seeded whitespace defect',
              rc == 1 and 'diff whitespace check failed' in output, output)
        subprocess.run(['git', 'mv', 'mcp-server/fixture.js', 'docs/renamed.md'],
                       cwd=root, env=fixture_env(), check=True)
        subprocess.run(['git', 'commit', '-qm', 'Move source into docs'], cwd=root, env=fixture_env(), check=True)
        check('renaming product into documentation retains the old product path',
              set(scope.selection(all_classes, root=root, base=base, environ={})['classes']) == all_classes)
        event.write_text('{}')
        check('malformed PR event falls back to full suite',
              set(scope.selection(all_classes, root=root, environ=env)['classes']) == all_classes)
        check('unavailable merge base falls back to full suite',
              set(scope.selection(all_classes, root=root, base='missing-ref', environ={})['classes']) == all_classes)


def test_python_controller_entrypoint():
    result = subprocess.run([sys.executable, str(REPO / "ops/ci.py"), "--list"],
                            capture_output=True, text=True, timeout=15)
    check("Python controller exposes the canonical class inventory", result.returncode == 0
          and all(name in result.stdout for name in
                  ("pushfloor", "unit", "types", "gates", "secret", "dependency",
                   "migration", "binding", "artifact", "hxprobe")), result.stdout + result.stderr)


def test_controller_selection_and_invalid_arguments():
    with ci_fixture() as root:
        for args, message in ((["--unknown"], "unknown argument"),
                              (["--unit-part", "unknown"], "unknown unit part"),
                              (["--migration-part", "unknown"], "unknown migration part"),
                              (["--gate-shard", "3/2"], "gate shard must be 1/2 or 2/2")):
            rc, output = run(args, root=root)
            check(f"invalid arguments refuse with exit 64: {args}",
                  rc == 64 and message in output and "CI passed" not in output, output)
        for shell in ("bash", "zsh", "sh"):
            if not shutil.which(shell):
                continue
            rc, output = run(["--only", "dependency,unit", "--only", "dependency"],
                             root=root, shell_cmd=[shell])
            verdicts = re.findall(r"(?:OK|FAIL|SKIP)\s+(unit|dependency)\s", output)
            check(f"{shell} selects repeated and comma-separated classes once in canonical order",
                  rc == 0 and verdicts == ["unit", "dependency"], output)
            check(f"{shell} reports timings for exactly the selected classes",
                  re.search(r"ci-timing: unit=\d+s dependency=\d+s", output) is not None, output)


def test_doc_drift_keeps_its_argument_and_interpreter_handoff():
    with ci_fixture() as root:
        path = root / 'scripts/doc-drift/ci.py'
        path.parent.mkdir(parents=True)
        path.write_text("import json,sys\nprint(json.dumps(sys.argv[1:])); raise SystemExit(73)\n")
        rc, output = run(['--doc-drift', '--fixture', 'two words'], root=root,
                         env={'CARR_DOC_DRIFT_PYTHON': sys.executable})
        check('doc drift preserves arguments and its own exit code',
              rc == 73 and json.loads(output) == ['--fixture', 'two words'], output)
        rc, output = run(['--strict', '--doc-drift'], root=root)
        check('doc drift is only a first-argument handoff',
              rc == 64 and 'unknown argument: --doc-drift' in output, output)


def test_migration_requires_newline_terminated_verdict():
    with ci_fixture() as root:
        for content, expected in (("ok\tfixture migration", 1),
                                  ("ok\tfixture migration\r", 1),
                                  ("ok\tfixture migration\n", 0),
                                  ("ok\tfixture migration\r\n", 0),
                                  ("ok\tA\nbad\tB", 1),
                                  ("ok\tA\nskip\tS", 1),
                                  ("ok\tA\rbad\tB\n", 1)):
            (root / "ops/ci-migration.py").write_text(
                "import sys\nfrom pathlib import Path\n"
                f"Path(sys.argv[sys.argv.index('--result')+1]).write_text({content!r})\n")
            rc, output = run(["--strict", "--only", "migration"], root=root)
            check(f"migration verdict newline={content.endswith(chr(10))} exits {expected}",
                  rc == expected, output)
            check("malformed migration records cannot pass CI",
                  ("CI passed" in output) == (expected == 0), output)


def test_controller_sigterm_cleans_children_and_temp_state():
    for name, runner, signals, delay in (
            ("dependency", "ci-dep-check.py", (signal.SIGTERM,), None),
            ("secret", "ci-secret.py", (signal.SIGTERM,), None),
            ("migration", "ci-migration.py", (signal.SIGTERM,), None),
            ("dependency", "ci-dep-check.py", (signal.SIGINT,), None),
            ("dependency", "ci-dep-check.py", (signal.SIGINT, signal.SIGTERM), None),
            *(("dependency", "ci-dep-check.py", pair, delay)
              for pair in ((signal.SIGINT, signal.SIGINT),
                           (signal.SIGTERM, signal.SIGTERM),
                           (signal.SIGINT, signal.SIGTERM))
              for delay in (0, .02))):
        escalating = len(signals) == 2
        retained = signals[0] == signal.SIGINT
        scenario = f"{name} {' then '.join(signal.Signals(sig).name for sig in signals)}"
        if delay is not None:
            scenario += f" gap={delay}s"
        with ci_fixture() as root, tempfile.TemporaryDirectory() as directory:
            state = pathlib.Path(directory)
            (root / "child.py").write_text(
                "import signal,time\nfrom pathlib import Path\n"
                f"signal.signal(signal.SIGTERM, {'signal.SIG_IGN' if escalating else 'lambda *_: exit(0)'})\n"
                "Path('child-ready').touch()\n"
                "while True: time.sleep(1)\n")
            (root / "ops" / runner).write_text(
                "import os,signal,subprocess,sys,time\nfrom pathlib import Path\n"
                "child=subprocess.Popen([sys.executable,'child.py'],start_new_session=True)\n"
                "def stop(*_):\n" +
                ("    Path('terminating').touch()\n" if escalating else
                 "    child.wait(timeout=5)\n    sys.exit(143)\n") +
                "signal.signal(signal.SIGTERM,stop)\n"
                "while not Path('child-ready').exists(): time.sleep(.01)\n"
                "print('cancellation fixture log', flush=True)\n"
                "Path('pids.pending').write_text(f'{os.getpid()} {child.pid}')\n"
                "Path('pids.pending').replace('pids')\n"
                "while True: time.sleep(1)\n")
            for finalizer in ("ci-evidence.py", "ci-secret-scan.py"):
                (root / "ops" / finalizer).write_text(
                    "import signal,sys\nfrom pathlib import Path\n"
                    "assert signal.getsignal(signal.SIGINT) == signal.default_int_handler\n"
                    "assert signal.getsignal(signal.SIGTERM) == signal.SIG_DFL\n"
                    "Path(__file__).with_suffix('.ready').touch()\n"
                    "if '--redact' in sys.argv: sys.stdout.write(sys.stdin.read())\n")
            process = subprocess.Popen(["bash", "ops/ci.sh", "--only", name], cwd=root,
                env={**fixture_env(), "RUNNER_TEMP": directory, "GITHUB_ACTIONS": "true",
                     "CARR_CI_PROGRAM_RECORDS": str(state / "records.json")}, start_new_session=True,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            pids = []
            try:
                deadline = time.monotonic() + 15
                while not (root / "pids").exists() and process.poll() is None and time.monotonic() < deadline:
                    time.sleep(.01)
                if (root / "pids").exists():
                    pids = [int(pid) for pid in (root / "pids").read_text().split()]
                check(f"{name} cancellation fixture starts runner and descendant", len(pids) == 2)
                started = time.monotonic()
                process.send_signal(signals[0])
                if escalating and delay is None:
                    deadline = time.monotonic() + 5
                    while not (root / "terminating").exists() and time.monotonic() < deadline:
                        time.sleep(.01)
                    check(f"{scenario} reaches child cleanup", (root / "terminating").exists())
                if escalating:
                    if delay:
                        time.sleep(delay)
                    process.send_signal(signals[1])
                try:
                    stdout, stderr = process.communicate(timeout=5 if escalating and delay is None else 15)
                except subprocess.TimeoutExpired:
                    check(f"{scenario} exits within cancellation bound", False)
                    continue
                check(f"{scenario} exits nonzero without claiming success",
                      (process.returncode != 0 if retained else process.returncode == 143) and "CI passed" not in stdout, stdout + stderr)
                for finalizer in (("ci-evidence", "ci-secret-scan") if retained else ("ci-evidence",)):
                    check(f"{scenario} {finalizer} restores cancellation signals",
                          (root / "ops" / f"{finalizer}.ready").exists(), stdout + stderr)
                for pid in pids:
                    try:
                        os.kill(pid, 0)
                        alive = True
                    except ProcessLookupError:
                        alive = False
                    check(f"{name} child {pid} exits before controller returns", not alive)
                if retained:
                    logs = list(state.glob("carr-ci.*/dep.log.redacted"))
                    check(f"{scenario} retains redacted logs for upload",
                          "ci-retained-logs:" in stderr and len(logs) == 1
                          and "cancellation fixture log" in logs[0].read_text(), stderr)
                else:
                    check(f"{scenario} removes controller temporary state",
                          not list(state.glob("carr-ci.*")), stderr)
                if escalating and delay is None:
                    check(f"{scenario} escalation exits promptly", time.monotonic() - started < 5)
            finally:
                for pid in [process.pid, *pids]:
                    try:
                        os.killpg(pid, signal.SIGKILL)
                    except OSError:
                        pass
                    try:
                        os.kill(pid, signal.SIGKILL)
                    except OSError:
                        pass
                process.wait(timeout=10)


def test_controller_requires_runner_verdicts():
    with ci_fixture() as root:
        for name in ("gates", "secret", "pushfloor", "migration"):
            runner = root / f"ops/ci-{name}.py"
            original = runner.read_text()
            runner.write_text("raise SystemExit(0)\n")
            rc, output = run(["--strict", "--only", f"{name},dependency"], root=root)
            runner.write_text(original)
            check(f"{name} cannot pass with an empty successful child verdict",
                  rc == 1 and "without a verdict" in output
                  and re.search(rf"FAIL\s+{name}\s", output) is not None, output)
            check(f"dependency still runs after {name} fails",
                  re.search(r"OK\s+dependency\s", output) is not None
                  and f"ci-failed-classes: {name}" in output, output)


def test_controller_consumes_evidence_destination_before_nested_runs():
    with ci_fixture() as root:
        unit = root / "ops/ci-unit.py"
        unit.write_text("import os,subprocess\n"
                        "assert 'CARR_CI_PROGRAM_RECORDS' not in os.environ\n"
                        "raise SystemExit(subprocess.call(['bash','ops/ci.sh','--only','dependency']))\n")
        evidence = root / "ops/ci-evidence.py"
        evidence.write_text("import json,sys\nfrom pathlib import Path\n"
                            "args=sys.argv\n"
                            "target=Path(args[args.index('--output')+1])\n"
                            "assert not target.exists(), 'nested CI wrote the parent receipt'\n"
                            "rows=Path(args[args.index('--rows')+1]).read_text()\n"
                            "target.write_text(json.dumps(rows))\n")
        output = root / "receipt.json"
        rc, text = run(["--strict", "--only", "unit"], root=root,
                       env={"CARR_CI_PROGRAM_RECORDS": str(output)})
        rows = json.loads(output.read_text()).splitlines() if output.exists() else []
        check("nested CI cannot overwrite the parent's program evidence",
              rc == 0 and len(rows) == 1 and rows[0].split('\t')[:3] == ["unit", "unit", "0"], text)


def test_artifact_requires_native_rule_projection_and_tests():
    for failing in ('native_rules.py', 'test_native_rules.py', None):
        with ci_fixture() as root:
            for script in ('native_rules.py', 'test_native_rules.py'):
                (root / 'tools' / script).write_text(
                    "import sys\n"
                    "from pathlib import Path\n"
                    f"Path('{script}.ran').write_text(' '.join(sys.argv[1:]))\n"
                    f"print('native-rule-probe-{script}')\n"
                    f"raise SystemExit({1 if script == failing else 0})\n")
            (root / 'tools/release-manifest.py').write_text(
                "from pathlib import Path\nPath('release.ran').touch()\nraise SystemExit(1)\n")
            rc, output = run(['--only', 'artifact'], root=root)
            projection = root / 'native_rules.py.ran'
            coverage = root / 'test_native_rules.py.ran'
            check(f'artifact checks projection before release, failure={failing}',
                  rc == 1 and projection.is_file() and projection.read_text() == '--check', output)
            check(f'artifact stops at the first native-rule failure, failure={failing}',
                  coverage.is_file() == (failing != 'native_rules.py')
                  and (root / 'release.ran').exists() == (failing is None), output)
            if failing:
                check(f'artifact reports and retains native-rule failure from {failing}',
                      'FAIL' in output and 'artifact' in output
                      and 'native rule projection is stale or coverage tests failed' in output
                      and f'native-rule-probe-{failing}' in output, output)


def test_retired_freshness_class_is_not_available():
    rc, out = run(["--list"])
    check("freshness is absent from the class inventory", rc == 0 and "freshness" not in out, out)
    rc, out = run(["--strict", "--only", "freshness"])
    check("retired freshness class refuses instead of silently passing", rc == 64 and "no such class" in out, out)


def test_no_green_without_running():
    for sh in ("zsh", "sh", "bash"):
        exe = shutil.which(sh)
        if not exe:
            continue
        rc, out = run(["--strict", "--only", "dependency"], shell_cmd=[exe])
        claims_green = "CI passed" in out
        actually_ran = re.search(r"(OK|FAIL|SKIP)\s+dependency", out) is not None
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
        for name in ("ci.sh", "ci.py", "ci-gates.py", "node_test_summary.py", "git_env.py",
                     "ci-secret-scan.py", "pii_guard.py"):
            shutil.copy2(REPO / "ops" / name, root / "ops" / name)
        shutil.copy2(REPO / "bin/with-timeout.py", root / "bin/with-timeout.py")
        (root / "ops/scac-mutation-inventory.ts").write_text("// Check the synthetic registry.\n")
        (root / "ops/config/ci-check-scope.json").write_text('{}')
        (root / "vendor/hxprobe/Cargo.toml").touch()
        (root / "ops/probe-selftest.py").write_text(
            "import os\n"
            "from pathlib import Path\n"
            "binary = Path(os.environ['CARR_HXPROBE_TEST_BINARY'])\n"
            "assert binary.is_file() and binary.parent.parent != Path('vendor/hxprobe/target').resolve()\n"
            "assert Path('worker-built').exists(), 'worker not built before pool'\n"
            "Path('gate-ran').touch()\n")
        (root / "hooks").mkdir()
        (root / "hooks/gate-integrity.py").write_text("raise SystemExit(0)\n")
        cargo = root / "bin/cargo"
        cargo.write_text("#!/bin/sh\n"
                         'test "$1 $2 $3" = "build --locked --target-dir" || exit 9\n'
                         'test -d "$4" || exit 11\n'
                         'test "$PWD" = "$CARR_TEST_ROOT/vendor/hxprobe" || exit 10\n'
                         'test "$CARR_BUILD_FAIL" = 0 || exit 1\n'
                         'mkdir -p "$4/debug" && touch "$4/debug/hxprobe"\n'
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
        for rel in ("ops/ci.sh", "ops/ci.py", "ops/ci_scope.py", "ops/node_test_summary.py",
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
                 "ops/new-db-gate.py", ".github/workflows/ci.yml",
                 "mcp-server/src/proof.js", "lib/proof.py",
                 "ops/db_acceptance_programs.py", "requirements.lock"):
        check(f"database scope runs {path}", scope.needs_full([path]))
    check("database scope skips app-only change", not scope.needs_full(["web/app.js"]))
    check("database scope skips docs-only changes", not scope.needs_full(["README.md", "docs/guide.md"]))

    from unittest.mock import patch
    with tempfile.TemporaryDirectory() as directory:
        receipt = pathlib.Path(directory) / 'receipt.json'
        for force in (False, True):
            argv = ['scope', '--receipt', str(receipt)] + (['--full'] if force else [])
            with patch.object(scope, 'classify', return_value=(False, ['docs/guide.md'], 'docs only')), \
                 patch.object(sys, 'argv', argv), patch.dict(os.environ, {'GITHUB_OUTPUT': os.devnull}), \
                 contextlib.redirect_stdout(io.StringIO()):
                rc = scope.main()
            check(f'full CI selection overrides narrower DB scope force={force}',
                  rc == 0 and json.loads(receipt.read_text())['full'] is force)

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


def test_scope_checkout_needs_only_current_tree():
    workflow = _hosted_workflow()
    checkout = next(step for step in workflow["jobs"]["scope"]["steps"]
                    if step.get("uses", "").startswith("actions/checkout@"))
    check("scope checkout does not fetch repository history",
          checkout.get("with", {}).get("fetch-depth", 1) == 1)
    spec = importlib.util.spec_from_file_location("db_scope_shallow", REPO / "ops/db-acceptance-scope.py")
    scope = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(scope)
    from unittest.mock import patch
    with patch.dict(os.environ, {"GITHUB_EVENT_NAME": "merge_group", "GITHUB_EVENT_PATH": "/dev/null"}), \
         patch.object(scope.json, "loads", return_value={"merge_group": {}}), \
         patch.object(scope, "changed_paths", side_effect=AssertionError("history must not be queried")):
        full, paths, reason = scope.classify()
    check("merge group requires full proof without history", full and paths is None)


def test_hosted_migration_ownership():
    wf = _hosted_workflow()
    runner = next(st for st in wf["jobs"]["classes"]["steps"] if st.get("name") == "Run this group's check classes")
    check("PR slot claims run before entering the credential-scrubbed database lane",
          "ops/migration-open-pr-gate.py" in (REPO / "ops/ci-pushfloor.py").read_text()
          and "GH_TOKEN" in wf["jobs"]["fast"]["steps"][-1]["env"])
    check("hosted migration partitions cover all independent proof groups",
          set(wf["jobs"]["migration-fixtures"].get("strategy", {}).get("matrix", {}).get("part", []))
          == {"fixtures", "gates", "acceptance"}
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
                                                                 "unit": {"result": "success"}, "classes": {"result": classes},
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
        shutil.copy2(REPO / "ops/ci-gates.py", root / "ops/ci-gates.py")
        seen = []
        def invoke(shard, calls, failed=""):
            return subprocess.run([sys.executable, "ops/ci-gates.py", "--logdir", str(root / "logs"),
                                   "--result", str(root / "result"), "--python", sys.executable,
                                   "--timeout-helper", str(root / "timeout.py"),
                                   "--shard", str(shard), "--shards", "2"], cwd=root,
                                  env={**fixture_env(), "CALLS": str(calls), "FAIL_TEST": failed},
                                  capture_output=True, text=True)
        for shard in (1, 2):
            calls = root / f"calls-{shard}"
            result = invoke(shard, calls)
            check(f"gate shard {shard} executes successfully", result.returncode == 0, result.stderr)
            seen.append(set(calls.read_text().splitlines()))
        check("gate shard union executes every Python and shell suite", seen[0] | seen[1] == inventory, seen)
        check("gate shards never execute the same suite twice", not seen[0] & seen[1], seen)
        result = invoke(2, root / "failed", next(iter(seen[1])))
        check("a failing second-shard suite fails its job", result.returncode == 1, result.stderr)


def test_secret_scanner_catches_and_respects_allow():
    with tempfile.TemporaryDirectory(prefix="carr-ci-scanner.") as tmp:
        root = pathlib.Path(tmp)
        (root / "ops/config").mkdir(parents=True)
        paths = ["ops/ci-secret-scan.py", "ops/pii_guard.py", "ops/gitleaks.py", ".gitleaks.toml",
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
        check("an unreviewed inline allow marker cannot suppress a finding",
              tracked and p.returncode == 1,
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

def test_dep_check_rejects_mixed_hash_coverage():
    with ci_fixture() as root:
        lock = root / "requirements.lock"
        lock.write_text(
            "# requirements.txt sha256: " + hashlib.sha256(b"synthetic-package>=1").hexdigest() + "\n"
            "synthetic-package==1.0 --hash=sha256:" + "a" * 64 + "\n"
            "transitive-package==2.0\n")
        rc, out = run(["--only", "dependency"], root=root)
        check("mixed hashed and unhashed pins fail the dependency class",
              rc == 1 and "mixes hashed and unhashed pins" in out, out)

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
        package_path = root / "mcp-server/package.json"
        package = json.loads(package_path.read_text())
        package["scripts"]["check:staged"] = "node -e 'process.exit(7)'"
        package_path.write_text(json.dumps(package))
        rc, out = run(["--only", "types"], root=root)
        check("types refuses a failing staged strict gate", rc == 1
              and "staged strict TypeScript gate failed" in out,
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
    listed = subprocess.run(["bash", str(CI), "--gate-programs"], cwd=REPO,
                            env=fixture_env(), text=True, capture_output=True)
    check("canonical gate inventory is readable", listed.returncode == 0, listed.stderr)
    collected = set(listed.stdout.splitlines())
    spec = importlib.util.spec_from_file_location('ci_secret', REPO / 'ops/ci-secret.py')
    assert spec is not None and spec.loader is not None
    secret = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(secret)
    collected.update(secret.qualification_programs(REPO))

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
    script = (
        "import runpy,sys,shutil\n"
        "from pathlib import Path\n"
        "m=runpy.run_path(sys.argv[1])\n"
        "c=m['Controller'](m['Options'](),sys.argv[3])\n"
        "shutil.rmtree(c.logdir)\n"
        "c.logdir=Path(sys.argv[2]).parent\n"
        "c.fail_tail(Path(sys.argv[2]).stem)\n"
    )
    p = subprocess.run([sys.executable, "-c", script, str(REPO / "ops/ci.py"),
                        str(log_path), py or sys.executable],
                       cwd=REPO, capture_output=True, text=True, timeout=120)
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

def test_log_tail_counts_newlines_in_progress_output():
    with tempfile.TemporaryDirectory(prefix="ci-selftest-progress-") as directory:
        log = pathlib.Path(directory) / "progress.log"
        log.write_bytes(b"".join(f"line {i}\rprogress {i}\n".encode() for i in range(1, 251))
                        + f"failure {FIXTURE_TOKEN}\n".encode())
        output = _fail_tail(log)
    check("progress updates do not shorten the 80-newline diagnostic window",
          output.splitlines()[0] == "line 172", output[:100])
    check("progress diagnostics retain the final failure line",
          "progress 250" in output and "failure <redacted:github-token:40 chars>" in output, output[-150:])
    check("progress diagnostics still redact shaped credentials", FIXTURE_TOKEN not in output)


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

def test_gates_exclude_prose_counts_but_enforce_rule_consistency():
    with ci_fixture() as root:
        (root / "hooks/gate-integrity.py").write_text("raise SystemExit(0)\n")
        (root / "ops/stale-count-check.py").write_text(
            "from pathlib import Path\nPath('prose-check-ran').touch()\nraise SystemExit(9)\n")
        (root / "ops/stale-count-selftest.py").write_text("print('prose checker selftest ran')\n")
        rule_marker = root / "rule-check-ran"
        for status in (0, 7):
            rule_marker.unlink(missing_ok=True)
            (root / "ops/rule-load-layer-check.py").write_text(
                "from pathlib import Path\nPath('rule-check-ran').touch()\n"
                f"raise SystemExit({status})\n")
            rc, out = run(["--strict", "--only", "gates"], root=root)
            check(f"strict gates never invoke the prose count checker (rule exit={status})",
                  not (root / "prose-check-ran").exists(), out)
            check(f"strict gates retain the prose checker selftest (rule exit={status})",
                  "prose checker selftest ran" in out, out)
            check(f"strict gates invoke the rule consistency checker (rule exit={status})",
                  rule_marker.exists(), out)
            check(f"strict gates propagate rule consistency failures (rule exit={status})",
                  rc == (1 if status else 0), out)


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
        ci = root / "ops/ci.py"
        ci.write_text(ci.read_text().replace("CI_SELFTEST_TIMEOUT_SECONDS = 120",
                                            "CI_SELFTEST_TIMEOUT_SECONDS = 1"))
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
        ci = root / "ops/ci.py"
        ci.write_text(ci.read_text().replace("CI_SELFTEST_TIMEOUT_SECONDS = 120",
                                            "CI_SELFTEST_TIMEOUT_SECONDS = 1"))
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
        result = subprocess.run(["bash", str(root / "ops/ci.sh"), "--strict", "--only", "dependency"],
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

def _ci_function_body(name):
    source = (REPO / "ops/ci.py").read_text()
    method = next(node for node in ast.walk(ast.parse(source))
                  if isinstance(node, ast.FunctionDef) and node.name == name)
    return ast.get_source_segment(source, method) or ""


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
    body = _ci_function_body("check_pushfloor")
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
    inherited_name = "mcp-server/test/doctorcre-frozen-review.test.ts"
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
        changed = ["mcp-server/src/scac-mutation-registry.current.generated.ts"]
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
    """Keep required event wiring and execute the actual aggregate on bad results."""
    for filename, lanes, verdict_name, command in (
            ("ci.yml", ("fast", "secret", "unit", "scope", "classes", "migration-fixtures"),
             "checks", "verdict"),
            ("doc-drift.yml", ("check",), "verdict", "drift-verdict")):
        wf = _hosted_workflow(filename)
        events = wf["on"]
        expected_actions = {"opened", "synchronize", "reopened", "converted_to_draft", "ready_for_review"}
        if filename == "ci.yml":
            expected_actions.add("edited")
        check(f"{filename}: only intended PR lifecycle events schedule runs",
              set(events["pull_request"]["types"]) == expected_actions)
        check(f"{filename}: all non-PR full-suite triggers survive",
              {"merge_group", "schedule", "workflow_dispatch"} <= set(events))
        jobs = wf["jobs"]
        if filename == "doc-drift.yml":
            baseline = next(st for st in jobs['check']['steps']
                            if st.get('name', '').startswith('Summarize drift'))
            check("drift baseline only applies to scheduled and manual checks",
                  baseline['if'] == "github.event_name == 'schedule' || github.event_name == 'workflow_dispatch'")
        if filename == "ci.yml":
            weekly = _hosted_workflow('fallow.yml')
            check("Fallow runs weekly outside the PR aggregate",
                  set(weekly['on']) == {'schedule', 'workflow_dispatch'}
                  and weekly['on']['schedule'] == [{'cron': '20 6 * * 1'}]
                  and 'fallow' not in jobs)
        verdict = jobs.get(verdict_name, {})
        expected_needs = set(lanes) | ({"select", "lint", "unit", "launchd-python39"} if filename == "ci.yml" else set())
        check(f"{filename}: verdict always evaluates and waits for every lane",
              verdict.get("if") == "${{ always() }}"
              and set(verdict.get("needs", [])) == expected_needs)
        if not verdict:
            continue
        step = next(st for st in verdict["steps"] if st.get("run") == f"python3 ops/ci-evidence.py {command}")
        needs = {name: {"result": "success"} for name in lanes}
        if "scope" in needs:
            needs["scope"]["outputs"] = {"full": "true"}
        if filename == 'ci.yml':
            from ci_scope import PR_FAST_CLASSES, PR_JOBS
            needs.update({name: {'result': 'success'} for name in ('lint', 'unit', 'launchd-python39')})
            needs['select'] = {'result': 'success', 'outputs': {
                'classes': json.dumps(sorted(PR_FAST_CLASSES)), 'full': 'false',
                **{job: 'true' for job in PR_JOBS}}}
            for name in ('scope', 'classes', 'migration-fixtures', 'unit', 'launchd-python39'):
                needs[name]['result'] = 'skipped'
        with tempfile.TemporaryDirectory() as directory:
            for draft in (False, True):
                result = subprocess.run(["bash", "-e", "-c", step["run"]], cwd=REPO,
                    env={**scrubbed_env(), "CI_NEEDS": json.dumps(needs),
                         "CI_DRAFT": str(draft).lower(), "CI_EVENT": "pull_request",
                         "GITHUB_STEP_SUMMARY": str(pathlib.Path(directory) / "summary")},
                    text=True, capture_output=True, timeout=10)
                check(f"{filename}: draft={draft} verdict is explicit",
                      result.returncode == (1 if draft else 0))
            for lane in (tuple(n for n in lanes if n not in ("fast", "secret")) if filename == "ci.yml" else lanes):
                for state in ("failure", "cancelled", "skipped", "timed_out", "missing"):
                    broken = {name: dict(value) for name, value in needs.items()}
                    if filename == 'ci.yml':
                        for full_lane in ('scope', 'classes', 'migration-fixtures'):
                            broken[full_lane]['result'] = 'success'
                    if state == "missing":
                        broken.pop(lane)
                    else:
                        broken[lane]["result"] = state
                    result = subprocess.run(["bash", "-e", "-c", step["run"]], cwd=REPO,
                        env={**scrubbed_env(), "CI_NEEDS": json.dumps(broken), "CI_DRAFT": "false",
                             "CI_EVENT": "merge_group",
                             "GITHUB_STEP_SUMMARY": str(pathlib.Path(directory) / "summary")},
                        text=True, capture_output=True, timeout=10)
                    check(f"{filename}: ready {lane}={state} blocks success at same SHA",
                          result.returncode != 0)
            # Status functions are valid in step conditions, not step env.
            guard = next((st for st in verdict['steps'] if st.get('if') == '${{ cancelled() }}'), {})
            result = subprocess.run(["bash", "-e", "-c", guard.get("run", "true")], cwd=REPO,
                                    text=True, capture_output=True, timeout=10)
            check(f"{filename}: cancellation after successful lanes cannot green the verdict",
                  bool(guard) and result.returncode != 0
                  and verdict['steps'].index(guard) == len(verdict['steps']) - 1)
        if filename == "ci.yml":
            matrix = jobs["classes"]["strategy"]["matrix"]
            check("ready matrix exclusions are static and cannot trim classes on draft state",
                  isinstance(matrix["exclude"], list))


def test_hosted_zsh_setup_does_not_refresh_working_indexes():
    for filename in ("ci.yml", "main-canary.yml", "unit-shards.yml"):
        for job in _hosted_workflow(filename)["jobs"].values():
            if job.get("container"):
                check(f"{filename} reaps orphaned PostgreSQL processes",
                      "--init" in job["container"].get("options", ""))
                check(f"{filename} uses baked zsh instead of package acquisition",
                      not any("ci-zsh.sh" in step.get("run", "") and step.get("if") !=
                              "matrix.classes != 'unit' && matrix.classes != 'gates' && matrix.classes != 'migration' && matrix.classes != 'dependency'"
                              for step in job.get("steps", [])))
    _exercise_zsh_installer()


def _exercise_zsh_installer():
    with tempfile.TemporaryDirectory(prefix="ci-zsh-setup-") as tmp:
        fixture = pathlib.Path(tmp)
        scripts = {
            "zsh": '#!/bin/sh\n[ "$CI_SETUP_FIXTURE" = preinstalled ]\n',
            "sudo": '#!/bin/sh\nexec "$@"\n',
            "timeout": "#!" + sys.executable + "\n" + '''
import os, sys
assert sys.argv[1] == "--kill-after=5s"
assert 0 < int(sys.argv[2].removesuffix("s")) <= 180
os.execvp(sys.argv[3], sys.argv[3:])
''',
            "apt-cache": "#!" + sys.executable + "\n" + '''
import hashlib, os, signal, sys
if os.environ["CI_SETUP_FIXTURE"] == "metadata-deadline":
    os.kill(os.getppid(), signal.SIGALRM)
if os.environ["CI_SETUP_FIXTURE"] in ("metadata-error", "publish-error"):
    sys.exit(2)
print("Package: zsh\\nVersion: 1\\nArchitecture: amd64\\nSHA256: " +
      hashlib.sha256(b"trusted-zsh-package").hexdigest())
''',
            "apt-get": "#!" + sys.executable + "\n" + '''
import json, os, pathlib, sys
log = pathlib.Path(os.environ["CI_SETUP_CALLS"])
calls = json.loads(log.read_text()) if log.exists() else []
args = sys.argv[1:]
calls.append(args)
log.write_text(json.dumps(calls))
mode = os.environ["CI_SETUP_FIXTURE"]
root = pathlib.Path(next(a.split("=", 1)[1] for a in args if a.startswith("Dir::Cache::archives=")))
archive = root / "zsh_1_amd64.deb"
if "--download-only" in args:
    sys.exit(100)
if "update" in args:
    sys.exit(100 if mode == "refresh-failed" else 0)
if "--no-download" in args:
    original = pathlib.Path(os.environ["ZSH_ARCHIVE_DIR"]) / archive.name
    if mode == "cache-replaced":
        original.write_bytes(b"altered-zsh-package")
    assert archive.read_bytes() == b"trusted-zsh-package"
    assert root != original.parent
    assert archive.stat().st_mode & 0o777 == 0o444
    sys.exit(124 if mode == "cached-timed-out" else 100 if mode == "partial-cache" else 0)
if mode == "timed-out":
    sys.exit(124)
if mode == "native-error":
    sys.exit(42)
if mode in ("cached", "cache-replaced"):
    sys.exit("network must not be used on a verified cache hit")
if mode in ("unavailable", "refresh-failed") or (mode == "stale" and not any("update" in c for c in calls)):
    sys.exit(100)
if not archive.exists():
    archive.write_bytes(b"trusted-zsh-package")
''',
        }
        for name, source in scripts.items():
            path = fixture / name
            path.write_text(source)
            path.chmod(0o755)
        for mode, expected, success in (
            ("preinstalled", [], True),
            ("cached", ["offline"], True),
            ("cached-timed-out", ["offline"], False),
            ("cache-replaced", ["offline"], True),
            ("cold", ["install"], True),
            ("tampered", ["install"], True),
            ("partial-cache", ["offline", "install"], True),
            ("stale", ["install", "update", "install"], True),
            ("unavailable", ["install", "update", "install"], False),
            ("refresh-failed", ["install", "update"], False),
            ("metadata-error", [], False),
            ("metadata-deadline", [], False),
            ("timed-out", ["install"], False),
            ("native-error", ["install"], False),
            ("publish-error", ["install"], False),
            ("old-version", ["install"], True),
            ("symlink", ["install"], True),
        ):
            archives = fixture / mode
            archives.mkdir()
            archive = archives / "zsh_1_amd64.deb"
            if mode in ("cached", "cache-replaced", "partial-cache", "tampered", "metadata-error", "old-version", "cached-timed-out", "metadata-deadline"):
                archive.write_bytes(b"altered-zsh-package" if mode == "tampered" else b"trusted-zsh-package")
            if mode == "old-version":
                archive.rename(archives / "zsh_2_amd64.deb")
            if mode == "symlink":
                target = fixture / "external.deb"
                target.write_bytes(b"trusted-zsh-package")
                archive.symlink_to(target)
            log = fixture / (mode + ".json")
            env = scrubbed_env()
            env.update(PATH=str(fixture) + os.pathsep + os.environ["PATH"],
                       CI_SETUP_CALLS=str(log), CI_SETUP_FIXTURE=mode, TMPDIR=str(fixture),
                       ZSH_ARCHIVE_DIR=str(archives), ZSH_RETRY_BACKOFF="0")
            ran = subprocess.run(["bash", str(REPO / "ops/ci-zsh.sh")],
                                 cwd=fixture, env=env, capture_output=True, text=True, timeout=15)
            check(f"zsh {mode} cleans up after setup", not any(fixture.glob("carr-zsh.*")))
            calls = json.loads(log.read_text()) if log.exists() else []
            actions = [("download" if "--download-only" in c else "offline" if "--no-download" in c
                        else "update" if "update" in c else "install") for c in calls]
            check(f"zsh {mode} follows the package-manager path", actions == expected, actions)
            check(f"zsh {mode} propagates setup success or failure", (ran.returncode == 0) == success,
                  {"rc": ran.returncode, "stderr": ran.stderr})
            for call in calls:
                private = pathlib.Path(next(a.split("=", 1)[1] for a in call if a.startswith("Dir::Cache::archives=")))
                check(f"zsh {mode} removes private archives", not private.exists())
            if mode == "tampered":
                check("zsh quarantines unauthenticated cache bytes",
                      (archives / "quarantine" / archive.name).read_bytes() == b"altered-zsh-package")
            if mode in ("cold", "stale", "partial-cache"):
                check(f"zsh {mode} publishes authenticated cache bytes", archive.is_file() and archive.read_bytes() == b"trusted-zsh-package")


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


def test_hosted_queue_demand_contract():
    import copy
    from collections import Counter
    from ci import CLASS_DESCRIPTIONS
    from ci_scope import PR_FAST_CLASSES
    wf = _hosted_workflow()

    def full_contract(workflow):
        jobs = workflow['jobs']
        matrix = jobs['classes']['strategy']['matrix']
        counts = Counter(name for group in matrix['classes'] for shard in matrix['gate_shard']
                         if {'classes': group, 'gate_shard': shard} not in matrix['exclude']
                         for name in group.split())
        return (set(counts) == set(CLASS_DESCRIPTIONS)
                and all(count == (2 if name == 'gates' else 1) for name, count in counts.items())
                and workflow['concurrency']['cancel-in-progress'] ==
                "${{ github.event_name == 'pull_request' || (github.event_name == 'push' && github.ref != 'refs/heads/main') }}"
                and {'merge_group', 'push'} <= set(workflow['on'])
                and workflow['on']['push']['branches'] == ['main']
                and jobs['checks']['name'] == 'ops/ci.sh --strict'
                and {'select', 'unit', 'scope', 'classes', 'migration-fixtures', 'launchd-python39'} <= set(jobs['checks']['needs'])
                and any('ci_unit_shards.py verify' in step.get('run', '')
                        and step.get('if') == "github.event_name != 'pull_request'"
                        for step in jobs['checks']['steps'])
                and jobs['classes']['if'] == "${{ !cancelled() && (github.event_name != 'pull_request' || needs.select.outputs.full == 'true') }}"
                and jobs['migration-fixtures']['strategy']['matrix']['part'] == ['fixtures', 'gates', 'acceptance'])

    check('merge groups retain all classes, shard coverage and uncancelled required result', full_contract(wf))
    for missing in CLASS_DESCRIPTIONS:
        broken = copy.deepcopy(wf)
        matrix = broken['jobs']['classes']['strategy']['matrix']
        matrix['classes'] = [' '.join(name for name in group.split() if name != missing)
                             for group in matrix['classes']]
        check(f'queue contract detects missing {missing}', not full_contract(broken))
    broken = copy.deepcopy(wf)
    broken['concurrency']['cancel-in-progress'] = True
    check('queue contract detects cancellable merge groups', not full_contract(broken))
    check('superseded runs group by workflow and ref', wf['concurrency']['group'] ==
          '${{ github.workflow }}-${{ github.head_ref || github.ref }}')
    with tempfile.TemporaryDirectory() as directory:
        output = pathlib.Path(directory) / 'outputs'
        result = subprocess.run([sys.executable, str(REPO / 'ops/ci_scope.py'), '--hosted'],
                                env={**os.environ, 'GITHUB_EVENT_NAME': 'pull_request',
                                     'GITHUB_EVENT_PATH': '/does/not/exist', 'GITHUB_OUTPUT': str(output)},
                                capture_output=True, text=True, timeout=15)
        selection = json.loads(result.stdout)
        check('every PR uses the named fast subset even for unknown diffs', result.returncode == 0
              and set(selection['classes']) == PR_FAST_CLASSES and not selection['full']
              and set(selection['jobs']) == {'lint', 'fast', 'secret'})
        check('selector workflow uses the hosted subset policy', any(
            step.get('run') == 'python3 ops/ci_scope.py --hosted' for step in wf['jobs']['select']['steps']))


def main(argv=None):
    if (sys.argv[1:] if argv is None else argv) == ["--collection-only"]:
        test_every_test_file_in_the_tree_is_collected()
        test_each_class_runs_in_exactly_one_hosted_group()
        return 1 if any(not ok for _, ok, _ in RESULTS) else 0
    for fn in (test_hosted_queue_demand_contract, test_changed_path_selection, test_artifact_requires_native_rule_projection_and_tests,
               test_controller_selection_and_invalid_arguments,
               test_doc_drift_keeps_its_argument_and_interpreter_handoff,
               test_migration_requires_newline_terminated_verdict,
               test_controller_sigterm_cleans_children_and_temp_state,
               test_controller_requires_runner_verdicts,
               test_controller_consumes_evidence_destination_before_nested_runs,
               test_python_controller_entrypoint, test_failed_gate_logs_survive_exit, test_worktree_python_selection,
               test_retired_freshness_class_is_not_available,
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
               test_database_scope_receipts, test_scope_checkout_needs_only_current_tree,
               test_migration_refuses_non_loopback,
               test_migration_fixtures_ignore_hosted_pr_event,
               test_secret_scanner_catches_and_respects_allow,
               test_dep_check_detects_a_stale_lock,
               test_dep_check_rejects_mixed_hash_coverage,
               test_types_class_catches_a_seeded_type_error,
               test_every_test_file_in_the_tree_is_collected,
               test_each_class_runs_in_exactly_one_hosted_group,
               test_the_walk_prunes_named_roots_only,
               test_a_failing_gates_output_is_printed_whole_when_it_is_short,
               test_a_long_failing_gate_log_is_tailed_and_still_redacted,
               test_log_tail_counts_newlines_in_progress_output,
               test_fail_tail_withholds_the_window_when_it_cannot_redact,
               test_gates_exclude_prose_counts_but_enforce_rule_consistency,
               test_gates_never_run_the_production_diagnostic,
               test_gates_report_every_completed_result_after_a_timeout,
               test_gates_selftests_have_a_process_group_watchdog,
               test_push_floor_defers_the_gates_class_instead_of_running_it,
               test_push_floor_distinguishes_imported_main_paths_from_branch_changes,
               test_push_floor_reports_missing_atlas_packages_without_blaming_registry,
               test_hosted_zsh_setup_does_not_refresh_working_indexes,
               ):
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
