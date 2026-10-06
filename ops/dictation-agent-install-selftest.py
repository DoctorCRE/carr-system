#!/usr/bin/env python3
"""Exercise the rig's public installer in an isolated machine fixture."""
import json
import os
from pathlib import Path
import plistlib
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

from git_env import fixture_env

REPO = Path(__file__).resolve().parent.parent
HELPER = Path("tools/dictation-rig/bin/install-agent-via-config.sh")
CAPTURE = "com.carr.capture-poll"
DICTATE = "com.carr.quill-dictate"

LAUNCHCTL = r'''
import json, os, pathlib, plistlib, sys
root = pathlib.Path(os.environ["FIXTURE_ROOT"])
state_file = root / "loaded.json"
state = json.loads(state_file.read_text())
args = sys.argv[1:]
with (root / "calls.jsonl").open("a") as log:
    log.write(json.dumps(args) + "\n")
command = args[0]
if command == "print":
    label = args[1].split("/")[-1]
    if label not in state:
        print('Could not find service "' + label + '"', file=sys.stderr)
        sys.exit(113)
    job = state[label]
    print("path = " + job["path"])
    print("arguments = {\n" + "\n".join(job["arguments"]) + "\n}")
elif command in ("load", "unload"):
    path = args[-1]
    job = plistlib.loads(pathlib.Path(path).read_bytes())
    label = job["Label"]
    if os.environ.get("FAIL_" + command.upper()) == label:
        print("fixture activation failure", file=sys.stderr)
        sys.exit(5)
    if command == "unload":
        state.pop(label, None)
    else:
        state[label] = {"path": path, "arguments": job["ProgramArguments"]}
        if os.environ.get("WRONG_ARGUMENTS") == label:
            state[label]["arguments"] = ["/old/executable"]
        if os.environ.get("WRONG_DISK") == label:
            job["ProgramArguments"] = ["/old/executable"]
            pathlib.Path(path).write_bytes(plistlib.dumps(job))
    state_file.write_text(json.dumps(state))
else:
    sys.exit("unexpected launchctl operation: " + repr(args))
'''


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="carr-rig-install-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.repo = self.root / "repo"
        self.home = self.root / "home"
        self.repo.mkdir()
        self.home.mkdir()
        (self.home / "recordings").mkdir()
        shutil.copytree(REPO / "lib", self.repo / "lib", ignore=shutil.ignore_patterns("__pycache__"))
        for name in (HELPER, Path("ops/config-as-code.py"), Path("ops/git_env.py"),
                     Path("ops/config/claude-continuity-hooks.json"),
                     Path("ops/claude-continuity-hook.py"), Path("mcp-server/continuity-stdio-proxy.mjs")):
            dest = self.repo / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(REPO / name, dest)
        (self.repo / "ops/config/hooks.json").write_text("{}")
        (self.repo / "ops/launchd").mkdir()
        (self.repo / ".venv/bin").mkdir(parents=True)
        (self.repo / ".venv/bin/python").symlink_to(sys.executable)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        launchctl = self.bin / "launchctl"
        launchctl.write_text("#!" + sys.executable + "\n" + LAUNCHCTL)
        launchctl.chmod(0o755)
        self.env = {k: v for k, v in fixture_env().items()
                    if not k.startswith(("CARR_", "FAIL_", "WRONG_"))}
        self.env.update(HOME=str(self.home), FIXTURE_ROOT=str(self.root),
                        PATH=str(self.bin) + os.pathsep + os.environ["PATH"])
        marker = self.home / ".config/carr/machine-role.json"
        marker.parent.mkdir(parents=True)
        marker.write_text('{"role":"secondary"}')
        subprocess.run(["git", "init", "-q", str(self.repo)], env=self.env, check=True)
        (self.root / "loaded.json").write_text("{}")
        self.agent(CAPTURE)
        self.agent(DICTATE)

    def agent(self, label, program="/usr/bin/true"):
        body = {"Label": label, "ProgramArguments": [program, "{{HOME}}/recordings"],
                "RunAtLoad": False}
        (self.repo / "ops/launchd" / (label + ".plist")).write_bytes(plistlib.dumps(body))

    def stale(self, label, matching_disk=False):
        dest = self.home / "Library/LaunchAgents" / (label + ".plist")
        dest.parent.mkdir(parents=True, exist_ok=True)
        body = (self.repo / "ops/launchd" / (label + ".plist")).read_text()
        dest.write_text(body.replace("{{HOME}}", str(self.home)) if matching_disk else
                        plistlib.dumps({"Label": label, "ProgramArguments": ["/old/executable"]}).decode())
        state = json.loads((self.root / "loaded.json").read_text())
        state[label] = {"path": str(dest), "arguments": ["/old/executable"]}
        (self.root / "loaded.json").write_text(json.dumps(state))
        return dest

    def run_helper(self, *labels):
        return subprocess.run(["/bin/sh", str(self.repo / HELPER), *labels],
                              cwd=self.repo, env=self.env, capture_output=True, text=True, timeout=30)

    def calls(self):
        path = self.root / "calls.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def test_scope_ignores_unrelated_config_agents_and_holds(self):
        (self.repo / "ops/config/hooks.json").write_text("invalid unrelated config")
        (self.home / ".config/carr/launchd-hold").write_text(DICTATE + " maintenance\n")
        self.agent(DICTATE, "/nonexistent/unrelated")
        (self.repo / "ops/launchd/broken.plist").write_text("unrelated invalid plist")
        old = self.stale(DICTATE)
        before = old.read_bytes()
        result = self.run_helper(CAPTURE)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(old.read_bytes(), before)
        self.assertTrue(all(DICTATE not in " ".join(call) for call in self.calls()))
        self.assertFalse((self.home / ".claude").exists())
        self.assertFalse((self.home / ".codex").exists())
        installed = self.home / "Library/LaunchAgents" / (CAPTURE + ".plist")
        self.assertEqual(plistlib.loads(installed.read_bytes())["ProgramArguments"],
                         ["/usr/bin/true", str(self.home / "recordings")])

    def test_success_installs_only_requested_label(self):
        result = self.run_helper(CAPTURE)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(set(json.loads((self.root / "loaded.json").read_text())), {CAPTURE})
        self.assertFalse((self.home / ".claude/settings.json").exists())

    def test_stale_loaded_missing_target_fails(self):
        old = self.stale(CAPTURE)
        before = old.read_bytes()
        self.agent(CAPTURE, "/nonexistent/target")
        result = self.run_helper(CAPTURE)
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertEqual(old.read_bytes(), before)
        self.assertFalse(any(call[0] in {"load", "unload"} for call in self.calls()))

    def test_stale_loaded_role_skip_fails_without_retiring_job(self):
        for label, role in (("com.carr.rules-refresh", "secondary"),
                            ("com.carr.fetch-allowlist", "primary")):
            with self.subTest(label=label):
                (self.home / ".config/carr/machine-role.json").write_text(json.dumps({"role": role}))
                self.agent(label)
                old = self.stale(label)
                before = old.read_bytes()
                result = self.run_helper(label)
                self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assertTrue(old.exists())
                self.assertEqual(old.read_bytes(), before)

    def test_activation_and_verification_failures_refuse_success(self):
        for variable in ("FAIL_LOAD", "FAIL_UNLOAD", "WRONG_ARGUMENTS", "WRONG_DISK"):
            with self.subTest(variable=variable):
                self.stale(CAPTURE)
                self.env[variable] = CAPTURE
                result = self.run_helper(CAPTURE)
                self.env.pop(variable)
                self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assertTrue((self.home / "Library/LaunchAgents" / (CAPTURE + ".plist.pending-reload")).exists())

    def test_matching_disk_with_stale_loaded_arguments_is_repaired(self):
        self.stale(CAPTURE, matching_disk=True)
        result = self.run_helper(CAPTURE)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        loaded = json.loads((self.root / "loaded.json").read_text())[CAPTURE]
        self.assertEqual(loaded["arguments"], ["/usr/bin/true", str(self.home / "recordings")])

    def test_dead_rig_copies_removed_and_call_mode_retained(self):
        folder = REPO / "tools/dictation-rig/launchd"
        for label in (CAPTURE, DICTATE, "com.carr.capture-watch", "com.carr.dictation-consent"):
            self.assertFalse((folder / (label + ".plist")).exists(), label)
        self.assertTrue((folder / "com.carr.call-mode.plist").is_file())

    def test_helper_commentary_uses_generic_paths_and_machine_roles(self):
        comments = "\n".join(line for line in (REPO / HELPER).read_text().splitlines() if line.startswith("#"))
        self.assertIsNone(re.search(r"/Users/[^<{\s]+|\b(?:Joe|Dell)\b", comments))


if __name__ == "__main__":
    unittest.main()
