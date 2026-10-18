#!/usr/bin/env python3
import json
import os
import plistlib
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


class RouteGuardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.table = self.root / "interface"
        self.table.write_text("en0")
        self.calls = self.root / "calls"
        self.cli = self.root / "Tailscale"
        self.route = self.root / "route"
        self.cli.write_text(f'''#!{sys.executable}
import json,os,sys,time
from pathlib import Path
with open({str(self.calls)!r}, "a") as f: f.write(" ".join(sys.argv[1:])+"\\n")
if sys.argv[1] == "status":
    print(json.dumps({{"BackendState":"Running","Self":{{"TailscaleIPs":["100.70.0.2"]}}}}))
elif sys.argv[1] == "down":
    if os.environ.get("FAKE_DOWN") == "stall": time.sleep(60)
    if os.environ.get("FAKE_DOWN") == "fail": sys.exit(7)
elif sys.argv[1] == "up":
    if os.environ.get("FAKE_REPAIR") == "fail":
        print("synthetic-sensitive-diagnostic", file=sys.stderr)
        sys.exit(7)
    if os.environ.get("FAKE_REPAIR") != "ineffective":
        Path({str(self.table)!r}).write_text("utun8")
''')
        self.route.write_text(f'''#!{sys.executable}
import os,sys,time
from pathlib import Path
with open({str(self.calls)!r}, "a") as f: f.write("route "+" ".join(sys.argv[1:])+"\\n")
if os.environ.get("FAKE_ROUTE") == "stall": time.sleep(60)
print("   route to: 100.64.0.1\\n  interface: "+Path({str(self.table)!r}).read_text())
sys.exit(1 if os.environ.get("FAKE_ROUTE") == "error" else 0)
''')
        self.cli.chmod(0o700)
        self.route.chmod(0o700)
        self.env = {**os.environ, "TAILSCALE_BIN": str(self.cli),
                    "TAILSCALE_ROUTE_BIN": str(self.route)}

    def run_command(self, *, recover=False, **env):
        argv = ["/bin/zsh", str(REPO / "bin/tailscale-up.sh")] if recover else [
            sys.executable, str(REPO / "ops/tailscale_health.py")]
        return subprocess.run(argv, env={**self.env, **env}, text=True,
                              capture_output=True, timeout=15)

    def operations(self):
        return self.calls.read_text().splitlines()

    def test_present_route_is_healthy_and_recovery_is_noop(self):
        self.table.write_text("utun8")
        self.assertEqual(self.run_command().returncode, 0)
        self.assertEqual(self.run_command(recover=True).returncode, 0)
        self.assertIn("route -n get 100.64.0.1", self.operations())
        self.assertNotIn("down", self.operations())
        self.assertNotIn("up", self.operations())

    def test_running_without_utun_route_fails_health(self):
        for interface in ("en0", "", "utunfake"):
            with self.subTest(interface=interface):
                self.table.write_text(interface)
                result = self.run_command()
                self.assertEqual(result.returncode, 1)
                self.assertIn("ROUTE MISSING", result.stdout)
                self.assertIn("on breach:", result.stdout)
                self.assertIn("route -n get 100.64.0.1", result.stdout)

    def test_one_reconnect_repairs_and_rechecks_route(self):
        result = self.run_command(recover=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        operations = self.operations()
        self.assertEqual([c for c in operations if c in ("down", "up")], ["down", "up"])
        self.assertEqual(operations.count("route -n get 100.64.0.1"), 2)
        self.assertNotIn("ping", " ".join(operations))
        self.assertRegex(result.stdout, r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ tailscale-up:.*route")

    def test_failed_or_ineffective_repair_exposes_bound_health_row(self):
        for mode in ("fail", "ineffective"):
            with self.subTest(mode=mode):
                self.calls.write_text("")
                result = self.run_command(recover=True, FAKE_REPAIR=mode)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("ROUTE MISSING", result.stdout)
                self.assertIn("owner orchestrator", result.stdout)
                self.assertIn("auto-clear:", result.stdout)
                self.assertNotIn("synthetic-sensitive-diagnostic", result.stdout + result.stderr)
                operations = self.operations()
                self.assertEqual(operations.count("down"), 1)
                self.assertEqual(operations.count("up"), 1)
                self.assertEqual(operations.count("route -n get 100.64.0.1"), 2)

    def test_route_command_failure_cannot_clear_health(self):
        self.table.write_text("utun8")
        self.assertEqual(self.run_command(FAKE_ROUTE="error").returncode, 1)

    def test_canonical_health_records_the_missing_route(self):
        fixture = self.root / "snapshot.json"
        fixture.write_text('{"errors": []}')
        findings = self.root / "findings.json"
        result = subprocess.run([sys.executable, str(REPO / "tools/health-check.py"),
            "--section", "tailscale", "--fixture", str(fixture),
            "--findings-json", str(findings)], env=self.env, text=True,
            capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 1)
        self.assertIn("ROUTE MISSING", result.stdout)
        rows = json.loads(findings.read_text())["findings"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["key"], "tailscale")
        self.assertEqual(rows[0]["subject"], "local-node")
        self.assertTrue(rows[0]["hard_error"])

    def test_down_failure_still_attempts_up(self):
        result = self.run_command(recover=True, FAKE_DOWN="fail")
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual([c for c in self.operations() if c in ("down", "up")], ["down", "up"])

    def test_route_watchdog_cannot_report_healthy(self):
        command = "from tailscale_health import recover; import sys; sys.exit(recover(sys.argv[1], route_timeout=.15))"
        result = subprocess.run([sys.executable, "-c", command, str(self.cli)],
            cwd=REPO / "ops", env={**self.env, "FAKE_ROUTE": "stall"},
            capture_output=True, text=True, timeout=5)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("ROUTE MISSING", result.stdout)
        self.assertIn("exit 124", result.stdout)

    def test_existing_agent_checks_periodically(self):
        definition = plistlib.loads((REPO / "ops/launchd/com.carr.tailscale-up.plist").read_bytes())
        self.assertEqual(definition["Label"], "com.carr.tailscale-up")
        self.assertTrue(definition["RunAtLoad"])
        self.assertNotIn("StartInterval", definition)
        minutes = sorted(entry["Minute"] for entry in definition["StartCalendarInterval"])
        self.assertEqual(minutes, list(range(0, 60, 2)))
        self.assertFalse(definition.get("KeepAlive", False))


if __name__ == "__main__":
    unittest.main()
