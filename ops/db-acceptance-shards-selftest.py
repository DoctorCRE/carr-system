#!/usr/bin/env python3
"""Acceptance shard replays through the runner and its fail-closed report reader."""
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import socket
import subprocess
import io
import signal
import time
from contextlib import redirect_stderr, redirect_stdout
import unittest
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "ops"))
import db_acceptance_shards as shards

spec = importlib.util.spec_from_file_location("local_pg_shard_test", REPO / "ops/local-pg-ci.py")
if spec is None or spec.loader is None:
    raise RuntimeError("cannot load the local PostgreSQL runner")
pg = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = pg
spec.loader.exec_module(pg)

# Captured from the pre-change serial command stream, including the terminal
# snapshot proof. This is deliberately independent of the proposed partition.
SERIAL = (
    "tools/test-f03-production-migration.py",
    "mcp-server/test/codex-continuity.test.mjs",
    "mcp-server/test/lead-workspace-pg.test.mjs",
    "ops/atomic-rule-approval-local-pg-acceptance.py",
    "ops/rule-delivery-local-pg-acceptance.py",
    "ops/engineering-claim-local-pg-gate.py",
    "ops/engineering-envelope-race-local-pg-gate.py",
    "ops/canonical-ownership-lease-local-pg-gate.py",
    "ops/assurance-evidence-acceptance-local-pg-gate.py",
    "ops/source-merge-authority-local-pg-gate.py",
    "ops/calendar-canary-local-pg-acceptance.py",
    "ops/nightly-canary-local-pg-acceptance.py",
    "ops/renewal-signed-ingress-local-pg-acceptance.py",
    "ops/renewal-lease-ledger-local-pg-gate.py",
    "ops/incident-recovery-local-pg-acceptance.py",
    "ops/completion-register-schema-local-pg-gate.py",
    "bin/schema-snapshot.sh",
)
RULE_AUTHORITY = "ops/atomic-rule-approval-local-pg-acceptance.py"  # a mid-shard-1 program


class Runner:
    def __init__(self, bad_path=None, fault="nonzero"):
        self.events = []
        self.bad_path, self.fault = bad_path, fault

    def run(self, command, *, env=None, cwd=None, capture=False):
        command = tuple(str(x) for x in command)
        self.events.append((command, dict(env or {})))
        if command[0] == "/fake/initdb":
            data = Path(command[command.index("-D") + 1])
            data.mkdir(parents=True)
            (data / "PG_VERSION").write_text("17\n")
        if command[0] == "/fake/pg_ctl" and command[-1] == "status":
            return pg.CommandResult(3, "", "")  # pg_ctl: no server running
        if command[-1] == "--fingerprint-only":
            return pg.CommandResult(0, "{}", "")
        if self.bad_path and any(x.endswith(self.bad_path) for x in command):
            if self.fault == "exception":
                raise OSError("CANARY_CLIENT_IDENTIFIER postgres://u:CANARY_SECRET@host/db?q=CANARY_SECRET")  # ci-secret-scan: allow - synthetic non-routable sink canary
            return pg.CommandResult(9, "", "CANARY_CLIENT_IDENTIFIER CANARY_SECRET")
        return pg.CommandResult(0, "", "")


class ShardTests(unittest.TestCase):
    def run_lane(self, shard=0, runner=None, report=False):
        runner = runner or Runner()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "cluster"
            root.mkdir()
            bins = pg.PostgresBinaries(*(Path("/fake") / x for x in ("initdb", "pg_ctl", "createdb", "psql")))
            with (patch.dict(os.environ, {}, clear=True),
                  patch.object(pg, "port_is_available", return_value=True),
                  patch.object(pg, "find_postgres_binaries", return_value=bins),
                  patch.object(tempfile, "mkdtemp", return_value=str(root)),
                  patch.object(pg, "shadow_source_binding", return_value=(
                      {"head": "a" * 40, "tree": "b" * 40},
                      {"postgres": "17.6", "python": "3.14.0", "node": "v26.0.0"}))):
                rc = pg.run_local_ci(repo=REPO, ci_class="migration", port=55432 + shard,
                                     runner=runner, shard=shard,
                                     report_path=Path(tmp) / "report.json" if report else None)
            if report:
                runner.report_bytes = (Path(tmp) / "report.json").read_bytes()
            self.assertFalse(root.exists())
        return rc, runner

    def test_manifest_conserved_and_actual_runner_parity(self):
        self.assertEqual(tuple(p.path for p in shards.PROGRAMS), SERIAL)
        union = shards.select_programs(1) + shards.select_programs(2)
        self.assertCountEqual([p.path for p in union], SERIAL)
        self.assertEqual(len({p.id for p in union}), len(SERIAL))
        for shard in (0, 2, 1):
            rc, runner = self.run_lane(shard)
            self.assertEqual(rc, 0)
            paths = [p.path for p in shards.select_programs(shard)]
            observed = [path for command, _ in runner.events for path in SERIAL
                        if command[-1] != "--fingerprint-only" and any(x.endswith(path) for x in command)]
            self.assertEqual(observed, paths)
            ci_index = next(i for i, (c, _) in enumerate(runner.events) if c[0].endswith("ops/ci.sh"))
            self.assertTrue(all(i > ci_index for i, (c, _) in enumerate(runner.events)
                                if c[-1] != "--fingerprint-only" and any(x.endswith(path) for x in c for path in paths)))

    def test_failure_and_exception_dispose_and_do_not_run_successors(self):
        for fault in ("nonzero", "exception"):
            runner = Runner("ops/atomic-rule-approval-local-pg-acceptance.py", fault)
            rc, runner = self.run_lane(1, runner)
            self.assertNotEqual(rc, 0)
            self.assertFalse(any(x.endswith("ops/rule-delivery-local-pg-acceptance.py")
                                 for c, _ in runner.events for x in c))
            self.assertTrue(any(c[0] == "/fake/pg_ctl" and c[-1] == "stop" for c, _ in runner.events))
        for n in (-1, 3, True):
            with self.assertRaises(ValueError):
                shards.select_programs(n)

    def test_signalled_setup_and_acceptance_retain_failure_reports(self):
        for path in ("initdb", "tools/migrate.py", "ops/ci.sh", SERIAL[0], RULE_AUTHORITY):
            for sig in (signal.SIGTERM, signal.SIGKILL):
                with self.subTest(path=path, signal=sig):
                    class Signalled(Runner):
                        def run(self, command, **kwargs):
                            result = super().run(command, **kwargs)
                            if any(str(x).endswith(path) for x in command):
                                return pg.CommandResult(-sig, "CANARY_SECRET", "CANARY_CLIENT_IDENTIFIER")
                            return result
                    rc, runner = self.run_lane(1, Signalled(), report=True)
                    self.assertNotEqual(rc, 0)
                    report = json.loads(runner.report_bytes)
                    self.assertEqual(report["returncode"], 128 + sig)
                    self.assertTrue(report["cleanup"])
                    if path in SERIAL:
                        self.assertEqual(report["tests"][-1]["returncode"], 128 + sig)
                    else:
                        self.assertEqual(report["tests"], [])
                    for sentinel in (b"CANARY_SECRET", b"CANARY_CLIENT_IDENTIFIER"):
                        self.assertNotIn(sentinel, runner.report_bytes)
                    reports = self.reports()
                    reports[1] = report
                    with self.assertRaises(ValueError):
                        self.aggregate(reports)

    def test_sigterm_lane_stops_descendants_before_cleanup_report(self):
        grandchild = """import os, signal, sys, time
from pathlib import Path
signal.signal(signal.SIGTERM, signal.SIG_IGN)
Path(sys.argv[1]).write_text(str(os.getpid()))
time.sleep(20)
Path(sys.argv[2]).write_text('finished')
"""
        child = """import subprocess, sys, time
command = [sys.executable, '-c', sys.argv[1], sys.argv[2], sys.argv[3]]
if sys.argv[4] == 'reparented':
    intermediate = "import subprocess,sys; subprocess.Popen(sys.argv[1:], start_new_session=True)"
    subprocess.run([sys.executable, '-c', intermediate, *command], check=True)
    from pathlib import Path
    Path(sys.argv[2] + '.reparented').write_text('intermediate exited')
else:
    subprocess.Popen(command, start_new_session=sys.argv[4] == 'escaped')
time.sleep(30)
"""
        for mode in ("group", "escaped", "reparented"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as tmp:
                tmp = Path(tmp)
                pidfile, marker = tmp / "pid", tmp / "completed"
                report, root = tmp / "report.json", tmp / "cluster"
                root.mkdir()
                fixture = f"""import importlib.util, os, signal, sys, tempfile
from pathlib import Path
from unittest.mock import patch
spec = importlib.util.spec_from_file_location('fixture', {str(Path(__file__).resolve())!r})
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)
pg = fixture.pg
class RealAcceptance(fixture.Runner):
    def run(self, command, **kwargs):
        if str(command[-1]).endswith(fixture.SERIAL[0]):
            real = pg.SubprocessRunner()
            try:
                return real.run([sys.executable, '-c', {child!r},
                    {grandchild!r}, {str(pidfile)!r}, {str(marker)!r}, {mode!r}], **kwargs)
            finally:
                self.cleanup_confirmed = real.cleanup_confirmed
        return super().run(command, **kwargs)
signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
bins = pg.PostgresBinaries(*(Path('/fake') / x for x in ('initdb', 'pg_ctl', 'createdb', 'psql')))
with (patch.object(pg, 'port_is_available', return_value=True),
      patch.object(pg, 'find_postgres_binaries', return_value=bins),
      patch.object(tempfile, 'mkdtemp', return_value={str(root)!r}),
      patch.object(pg, 'shadow_source_binding', return_value=(
          {{'head': 'a'*40, 'tree': 'b'*40}},
          {{'postgres': '17.6', 'python': '3.14.0', 'node': 'v26.0.0'}}))):
    rc = pg.run_local_ci(repo=fixture.REPO, ci_class='migration', port=55433,
                         runner=RealAcceptance(), shard=1, report_path=Path({str(report)!r}))
sys.exit(rc)
"""
                fixture_env = {key: value for key, value in os.environ.items()
                               if not key.startswith(("GITHUB_", "CARR_"))}
                lane = subprocess.Popen([sys.executable, "-c", fixture], env=fixture_env,
                                        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                        text=True, start_new_session=True)
                descendant = None
                try:
                    deadline = time.monotonic() + 10
                    while not pidfile.exists() and lane.poll() is None and time.monotonic() < deadline:
                        time.sleep(0.02)
                    self.assertTrue(pidfile.exists(), "acceptance grandchild did not start")
                    descendant = int(pidfile.read_text())
                    if mode == "reparented":
                        reparented = Path(str(pidfile) + '.reparented')
                        while not reparented.exists() and lane.poll() is None and time.monotonic() < deadline:
                            time.sleep(0.02)
                        self.assertTrue(reparented.exists(), "intermediate did not exit before cancellation")
                    lane.send_signal(signal.SIGTERM)
                    stdout, stderr = lane.communicate(timeout=15)
                    self.assertEqual(lane.returncode, 130, stdout + stderr)
                    observed = json.loads(report.read_text())
                    self.assertEqual(observed["returncode"], 130)
                    if mode == "reparented":
                        self.assertFalse(observed["cleanup"], "reparented ownership cannot be confirmed")
                        self.assertTrue(root.exists(), "unconfirmed cleanup must retain resources")
                        reports = self.reports()
                        reports[1] = observed
                        with self.assertRaises(ValueError):
                            self.aggregate(reports)
                    status = subprocess.run(["ps", "-o", "stat=", "-p", str(descendant)],
                                            capture_output=True, text=True, timeout=5).stdout.strip()
                    if mode != "reparented":
                        self.assertTrue(not status or status.startswith("Z"),
                                        "discoverable acceptance descendant remains active")
                    self.assertFalse(marker.exists())
                    self.assertEqual(root.exists(), not observed["cleanup"])
                finally:
                    if lane.poll() is None:
                        os.killpg(lane.pid, signal.SIGKILL)
                    lane.communicate(timeout=5)
                    if descendant is not None:
                        try:
                            os.kill(descendant, signal.SIGKILL)
                        except ProcessLookupError:
                            pass

    def test_sigterm_fixture_ignores_hosted_parent_environment(self):
        with patch.dict(os.environ, {"GITHUB_ACTIONS": "true"}):
            self.test_sigterm_lane_stops_descendants_before_cleanup_report()

    def test_unconfirmed_process_cleanup_retains_cluster_and_refuses_aggregate(self):
        class Unconfirmed(Runner):
            cleanup_confirmed = False
            def run(self, command, **kwargs):
                if str(command[-1]).endswith(SERIAL[0]):
                    raise KeyboardInterrupt
                return super().run(command, **kwargs)
        with tempfile.TemporaryDirectory() as tmp:
            root, report = Path(tmp) / "cluster", Path(tmp) / "report.json"
            root.mkdir()
            bins = pg.PostgresBinaries(*(Path("/fake") / x for x in ("initdb", "pg_ctl", "createdb", "psql")))
            with (patch.dict(os.environ, {}, clear=True),
                  patch.object(pg, "port_is_available", return_value=True),
                  patch.object(pg, "find_postgres_binaries", return_value=bins),
                  patch.object(tempfile, "mkdtemp", return_value=str(root)),
                  patch.object(pg, "shadow_source_binding", return_value=(
                      {"head": "a" * 40, "tree": "b" * 40},
                      {"postgres": "17.6", "python": "3.14.0", "node": "v26.0.0"}))):
                self.assertEqual(pg.run_local_ci(repo=REPO, ci_class="migration", port=55433,
                                                runner=Unconfirmed(), shard=1, report_path=report), 130)
            observed = json.loads(report.read_text())
            self.assertFalse(observed["cleanup"])
            self.assertTrue(root.exists())
            reports = self.reports()
            reports[1] = observed
            with self.assertRaises(ValueError):
                self.aggregate(reports)

    def test_real_subprocess_capture_exit_status_environment_and_cwd(self):
        runner = pg.SubprocessRunner()
        result = runner.run([sys.executable, "-c",
                             "import os,sys; print(os.environ['LANE_SENTINEL']); "
                             "print(os.getcwd(), file=sys.stderr); sys.exit(9)"],
                            env={"LANE_SENTINEL": "synthetic"}, cwd=REPO, capture=True)
        self.assertEqual(result, pg.CommandResult(9, "synthetic\n", f"{REPO}\n"))
        result = runner.run([sys.executable, "-c",
                             "import os,signal; os.kill(os.getpid(),signal.SIGTERM)"], capture=True)
        self.assertEqual(result.returncode, -signal.SIGTERM)
        self.assertTrue(runner.cleanup_confirmed)

    def reports(self):
        reports = []
        for n in (0, 1, 2):
            programs = shards.select_programs(n)
            reports.append(shards.make_report(
                shard=n, source={"head": "a" * 40, "tree": "b" * 40},
                toolchain={"postgres": "17.6", "python": "3.12.1", "node": "22.1"},
                started=100, finished=120 if n == 0 else 109, queued=90,
                setup_seconds=1, tests=[{"id": p.id, "returncode": 0, "seconds": 0.5} for p in programs],
                cleanup=True, returncode=0, port=55432 + n, root=f"/tmp/test-shard-{n}",
            ))
        return reports

    def aggregate(self, reports):
        return shards.aggregate(reports, expected_head="a" * 40, max_work_ratio=1.0)

    def test_clean_control_measures_and_advises_only(self):
        out = self.aggregate(self.reports())
        self.assertTrue(out["accepted"])
        self.assertFalse(out["authorizes_gate_change"])
        self.assertEqual(out["longest_shard_seconds"], 9)
        self.assertEqual(out["serial_queue_to_verdict_seconds"], 30)
        self.assertEqual(out["sharded_queue_to_verdict_seconds"], 19)
        self.assertEqual(out["sharded_work_seconds"], 18)

    def test_every_fault_is_rejected_independently(self):
        mutations = {
            "failure": lambda r: r[1]["tests"][0].update(returncode=1),
            "refusal": lambda r: r[2].update(returncode=78),
            "empty exit zero": lambda r: r[1].update(tests=[]),
            "partial": lambda r: r[2]["tests"].pop(),
            "duplicate": lambda r: r[2]["tests"].append(r[2]["tests"][0]),
            "missing cleanup acknowledgement": lambda r: r[1].pop("cleanup"),
            "unconfirmed cleanup": lambda r: r[1].update(cleanup=False),
            "wrong source": lambda r: r[2]["source"].update(head="c" * 40),
            "wrong toolchain": lambda r: r[2]["toolchain"].update(postgres="18"),
            "port collision": lambda r: r[2].update(port=r[1]["port"]),
            "temp collision": lambda r: r[2].update(root=r[1]["root"]),
            "serial port collision": lambda r: r[1].update(port=r[0]["port"]),
            "serial temp collision": lambda r: r[1].update(root=r[0]["root"]),
            "nonfinite": lambda r: r[1].update(setup_seconds=float("nan")),
            "unknown field": lambda r: r[1].update(unknown="CANARY_SECRET"),
            "manifest drift": lambda r: r[1].update(manifest_sha256="0" * 64),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name):
                reports = self.reports()
                mutate(reports)
                with self.assertRaises(ValueError):
                    self.aggregate(reports)
        for reports in ([], self.reports()[1:], self.reports()[:-1]):
            with self.assertRaises(ValueError):
                self.aggregate(reports)

    def test_economics_and_failure_rate_do_not_promote(self):
        reports = self.reports()
        reports[1]["finished"] = 130
        self.assertFalse(self.aggregate(reports)["accepted"])
        reports = self.reports()
        reports[0]["tests"][0]["returncode"] = 1
        reports[0]["returncode"] = 1
        with self.assertRaises(ValueError):
            self.aggregate(reports)

    def test_raw_sink_never_serializes_subprocess_output(self):
        for fault in ("nonzero", "exception"):
            errors = io.StringIO()
            with redirect_stderr(errors), redirect_stdout(io.StringIO()):
                rc, runner = self.run_lane(1, Runner(RULE_AUTHORITY, fault), report=True)
            self.assertNotEqual(rc, 0)
            for sentinel in ("CANARY_SECRET", "CANARY_CLIENT_IDENTIFIER"):
                self.assertNotIn(sentinel.encode(), runner.report_bytes)
                self.assertNotIn(sentinel, errors.getvalue())
            self.assertNotEqual(json.loads(runner.report_bytes)["returncode"], 0)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "report.json"
            report = self.reports()[1]
            shards.write_report(path, report)
            raw = path.read_bytes()
            self.assertNotIn(b"CANARY_SECRET", raw)
            self.assertEqual(json.loads(raw), report)
            with self.assertRaises(FileExistsError):
                shards.write_report(path, report)

    def test_actual_cli_empty_partial_exception_and_clean_reports(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            def invoke():
                return subprocess.run([sys.executable, REPO / "ops/db_acceptance_shards.py", root,
                                       "--expected-head", "a" * 40, "--max-work-ratio", "1.0"],
                                      text=True, capture_output=True, timeout=10)
            self.assertNotEqual(invoke().returncode, 0)
            for report in self.reports():
                shards.write_report(root / f"shard-{report['shard']}.json", report)
            self.assertEqual(invoke().returncode, 0)
            one = root / "shard-1.json"
            clean = one.read_text()
            for corrupt in ("", "{", clean.replace('"cleanup": true', '"cleanup": false'),
                            clean.replace('"shard": 1', '"shard": 2, "shard": 1'),
                            '{"raw_error":"CANARY_CLIENT_IDENTIFIER CANARY_SECRET"}'):
                one.write_text(corrupt)
                result = invoke()
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn("CANARY_SECRET", result.stdout + result.stderr)
            one.write_text(clean)
            self.assertEqual(invoke().returncode, 0)

    def test_cancel_and_unknown_teardown_never_acknowledge_success(self):
        class Cancelled(Runner):
            def run(self, command, **kwargs):
                if str(command[-1]).endswith(SERIAL[0]):
                    raise KeyboardInterrupt
                return super().run(command, **kwargs)
        rc, runner = self.run_lane(1, Cancelled(), report=True)
        self.assertEqual(rc, 130)
        self.assertTrue(json.loads(runner.report_bytes)["cleanup"])
        class UnknownStop(Runner):
            def run(self, command, **kwargs):
                if str(command[0]) == "/fake/pg_ctl" and command[-1] in ("stop", "status"):
                    return pg.CommandResult(1, "", "unknown")
                return super().run(command, **kwargs)
        # Retain a real disposable root on unknown outcome. Disposal must not
        # destroy a possibly running postmaster's data.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "cluster"
            root.mkdir()
            bins = pg.PostgresBinaries(*(Path("/fake") / x for x in ("initdb", "pg_ctl", "createdb", "psql")))
            with (patch.dict(os.environ, {}, clear=True),
                  patch.object(pg, "port_is_available", return_value=True),
                  patch.object(pg, "find_postgres_binaries", return_value=bins),
                  patch.object(tempfile, "mkdtemp", return_value=str(root))):
                self.assertEqual(pg.run_local_ci(repo=REPO, ci_class="migration", port=55433,
                                                runner=UnknownStop(), shard=1), 70)
            self.assertTrue(root.exists())

    def test_collision_refusal_precedes_setup_and_recovery_runs_clean(self):
        runner = Runner()
        with patch.object(pg, "port_is_available", return_value=False):
            with self.assertRaises(pg.LocalPGRefusal):
                pg.run_local_ci(repo=REPO, ci_class="migration", port=55433, shard=1, runner=runner)
        self.assertEqual(runner.events, [])
        self.assertEqual(self.run_lane(1)[0], 0)
        for ci_class, base in (("strict", None), ("migration", "a" * 40)):
            with self.assertRaises(pg.LocalPGRefusal):
                pg.run_local_ci(repo=REPO, ci_class=ci_class, port=55433,
                                report_path=Path("/tmp/never-created-shard-report.json"), integration_base=base)
        for value in (float("nan"), float("inf"), -1):
            with self.assertRaises(pg.LocalPGRefusal):
                pg.run_local_ci(repo=REPO, ci_class="migration", port=55433, shard=1, queued_at=value)

    def test_hosted_postgres_version_suffix_and_wrong_major(self):
        bins = pg.PostgresBinaries(*(Path("/fake") / x for x in ("initdb", "pg_ctl", "createdb", "psql")))
        class Readback:
            version = "initdb (PostgreSQL) 17.7 (Ubuntu 17.7-1.pgdg24.04+1)"
            def run(self, command, **kwargs):
                args = tuple(map(str, command))
                output = ({("git", "rev-parse", "HEAD"): "a" * 40,
                           ("git", "rev-parse", "HEAD^{tree}"): "b" * 40,
                           ("git", "status", "--porcelain"): "",
                           ("/fake/initdb", "--version"): self.version,
                           (str(pg.repository_python(REPO)), "--version"): "Python 3.14.0",
                           ("node", "--version"): "v22.1.0"})[args]
                return pg.CommandResult(0, output, "")
        reader = Readback()
        with patch.object(pg, "SubprocessRunner", return_value=reader):
            for version in ("initdb (PostgreSQL) 17.7", reader.version):
                reader.version = version
                source, tools = pg.shadow_source_binding(REPO, bins)
                self.assertEqual(source["head"], "a" * 40)
                self.assertEqual(tools["postgres"], "17.7")
            reader.version = "initdb (PostgreSQL) 18.1 (Ubuntu 18.1-1.pgdg24.04+1)"
            with self.assertRaises(pg.LocalPGRefusal):
                pg.shadow_source_binding(REPO, bins)

    def test_workflow_default_off_and_full_serial_backstop(self):
        # Exercise the same YAML parser used by ops/ci-selftest.py.
        parsed = subprocess.run(["node", "-e", "console.log(JSON.stringify(require('js-yaml').load(require('fs').readFileSync(process.argv[1],'utf8'))))",
                                 str(REPO / ".github/workflows/db-acceptance.yml")],
                                cwd=REPO / "mcp-server", capture_output=True, text=True, check=True)
        workflow = json.loads(parsed.stdout)
        self.assertFalse(workflow["on"]["workflow_dispatch"]["inputs"]["shard_trial"]["default"])
        job = workflow["jobs"]["acceptance"]
        self.assertFalse(job["strategy"]["fail-fast"])
        self.assertIn("|| '[0]'", job["strategy"]["matrix"]["shard"])
        steps = job["steps"]
        self.assertTrue(any(s.get("run") == "python ops/local-pg-ci.py --class migration" for s in steps))
        upload = next(s for s in steps if s.get("name") == "Retain each shadow report even on failure")
        self.assertIn("always()", upload["if"])
        self.assertEqual(upload["with"]["if-no-files-found"], "error")
        aggregate = workflow["jobs"]["shadow-aggregate"]
        self.assertIn("always()", aggregate["if"])
        self.assertEqual(aggregate["needs"], "acceptance")
        self.assertIn("--expected-head", aggregate["steps"][-1]["run"])
        self.assertIn("github.run_attempt", aggregate["steps"][1]["with"]["pattern"])


@unittest.skipUnless("--postgres" in sys.argv, "pass --postgres for the real isolated PG17 sentinel replay")
class RealIsolation(unittest.TestCase):
    def test_concurrent_roles_tables_ports_sockets_and_cleanup(self):
        binaries = pg.find_postgres_binaries()
        real = pg.SubprocessRunner()
        env = pg.scrub_cloud_environment(os.environ)
        env["PATH"] = f"{binaries.psql.parent}:{env.get('PATH', '')}"
        self.assertIn("17.", real.run([binaries.initdb, "--version"], capture=True).stdout)
        barrier = threading.Barrier(2)
        roots, codes, ports, errors = [], [], [], []
        reservations = []
        for _ in range(2):
            sock = socket.socket()
            sock.bind(("127.0.0.1", 0))
            ports.append(sock.getsockname()[1])
            reservations.append(sock)
        for sock in reservations:
            sock.close()

        class SentinelRunner(Runner):
            def __init__(self, marker):
                super().__init__()
                self.marker, self.probed = marker, False

            def run(self, command, **kwargs):
                strings = [str(c) for c in command]
                if strings[0] == str(binaries.initdb):
                    roots.append(Path(strings[strings.index("-D") + 1]).parent)
                if strings[0] == str(binaries.pg_ctl) and strings[-1] == "start":
                    data = Path(strings[strings.index("-D") + 1])
                    if f"-k {data.parent / 'socket'}" not in strings[strings.index("-o") + 1]:
                        raise RuntimeError("socket namespace is not private")
                # Test the runner's owned-cluster/teardown mechanism using a
                # toy baseline. Full migrations and programs are separate
                # exact-source hosted acceptance, not fabricated by this probe.
                if strings[0] in tuple(str(getattr(binaries, x)) for x in ("initdb", "pg_ctl", "createdb", "psql")) and "-f" not in strings:
                    return real.run(command, **kwargs)
                if strings[-1] == "--fingerprint-only":
                    return pg.CommandResult(0, "{}", "")
                if any(c.endswith(p.path) for c in strings for p in shards.select_programs(self.marker)) and not self.probed:
                    self.probed = True
                    if not Path(kwargs["env"]["TMPDIR"]).parent in roots:
                        raise RuntimeError("program temp namespace is not owned")
                    # Replay a canonical consumer that starts another PG
                    # cluster under the runner's private TMPDIR. macOS AF_UNIX
                    # rejects the longer inherited /var/folders namespace.
                    with tempfile.TemporaryDirectory(prefix="carr-catchup-writer-", dir=kwargs["env"]["TMPDIR"]) as nested:
                        nested = Path(nested)
                        (nested / "socket").mkdir()
                        child_env = kwargs["env"]
                        initialized = real.run([binaries.initdb, "-D", nested / "data", "-A", "trust", "--no-locale"], env=child_env, capture=True)
                        if initialized.returncode:
                            raise RuntimeError("nested cluster init failed")
                        try:
                            started = real.run([binaries.pg_ctl, "-D", nested / "data", "-l", nested / "pg.log", "-o", f"-k {nested / 'socket'} -c listen_addresses=''", "-w", "start"], env=child_env, capture=True)
                            if started.returncode:
                                raise RuntimeError("nested private socket startup failed")
                        finally:
                            stopped = real.run([binaries.pg_ctl, "-D", nested / "data", "-m", "immediate", "-w", "stop"], env=child_env, capture=True)
                        if stopped.returncode:
                            raise RuntimeError("nested cleanup failed")
                    dsn = kwargs["env"].get("CARR_CI_DATABASE_URL") or kwargs["env"]["CARR_LOCAL_PG_DSN"]
                    def sql(query):
                        out = real.run([binaries.psql, dsn, "-X", "-At", "-v", "ON_ERROR_STOP=1", "-c", query], env=env, capture=True)
                        if out.returncode:
                            raise RuntimeError("sentinel SQL failed")
                        return out.stdout.strip()
                    sql(f"create role shard_sentinel; create table shard_sentinel(value int); insert into shard_sentinel values ({self.marker});")
                    barrier.wait(15)
                    if sql("select value from shard_sentinel") != str(self.marker):
                        raise RuntimeError("table leaked across shards")
                    if self.marker == 1:
                        sql("drop role shard_sentinel; drop table shard_sentinel;")
                    barrier.wait(15)
                    if self.marker == 2 and sql("select exists(select 1 from pg_roles where rolname='shard_sentinel')") != "t":
                        raise RuntimeError("role leaked across shards")
                return pg.CommandResult(0, "", "")

        def worker(index):
            try:
                codes.append(pg.run_local_ci(repo=REPO, ci_class="migration", port=ports[index-1],
                                             shard=index, runner=SentinelRunner(index)))
            except Exception as exc:
                errors.append(type(exc).__name__)
        threads = [threading.Thread(target=worker, args=(i,)) for i in (1, 2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(45)
        self.assertFalse(any(t.is_alive() for t in threads))
        self.assertEqual(errors, [])
        self.assertEqual(codes, [0, 0])
        self.assertEqual(len(set(roots)), 2)
        self.assertTrue(all(not root.exists() for root in roots))
        self.assertTrue(all(pg.port_is_available(port) for port in ports))


if __name__ == "__main__":
    if "--postgres" in sys.argv:
        sys.argv.remove("--postgres")
    unittest.main()
