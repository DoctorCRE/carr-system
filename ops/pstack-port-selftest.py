#!/usr/bin/env python3
"""Exercise the pstack installation and CARR routing contracts."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from git_env import fixture_env

REPO = Path(__file__).resolve().parents[1]
PLUGIN = REPO / "plugins/pstack"


class PortContractTests(unittest.TestCase):
    def test_versioned_marketplace_survives_source_worktree_removal(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "canonical"
            root.mkdir()
            env = fixture_env()
            def git(*args):
                return subprocess.run(["git", *args], cwd=root, env=env, check=True, capture_output=True)
            git("init", "-q", "-b", "main")
            git("config", "user.email", "selftest@example.invalid")
            git("config", "user.name", "Selftest")
            git("config", "core.hooksPath", "/dev/null")
            shutil.copytree(PLUGIN, root / "plugins/pstack", ignore=shutil.ignore_patterns("node_modules", "__pycache__"))
            shutil.copytree(REPO / ".claude-plugin", root / ".claude-plugin")
            git("add", "plugins/pstack", ".claude-plugin")
            git("commit", "-qm", "fixture")
            revision = git("rev-parse", "HEAD").stdout.decode().strip()
            feature = Path(tmp) / "feature"
            git("worktree", "add", "-qb", "topic", str(feature))
            store = Path(tmp) / "installed-marketplaces"
            def bundle(source, destination=store):
                return subprocess.run([sys.executable, str(source / "plugins/pstack/scripts/bundle.py"),
                                       str(destination)], cwd=source, env=env, capture_output=True, text=True)
            rejected = bundle(feature, feature / "out/bundles")
            self.assertNotEqual(rejected.returncode, 0)
            self.assertIn("outside Git checkouts", rejected.stderr)
            result = bundle(feature)
            self.assertEqual(result.returncode, 0, result.stderr)
            installed = Path(result.stdout.strip())
            self.assertEqual(installed, store.resolve() / revision)
            expected = (feature / "plugins/pstack/skills/architect/SKILL.md").read_bytes()
            git("worktree", "remove", str(feature))
            self.assertEqual((installed / "plugins/pstack/skills/architect/SKILL.md").read_bytes(), expected)
            self.assertFalse(any(path.is_symlink() for path in installed.rglob("*")))
            self.assertFalse((installed / ".git").exists())
            self.assertEqual(json.loads((installed / ".claude-plugin/marketplace.json").read_text())["name"], "carr-local")
            runtime_cache = installed / "plugins/pstack/skills/poteto-mode/scripts/node_modules"
            runtime_cache.mkdir()
            (runtime_cache / "generated-dependency").write_text("runtime cache")
            again = bundle(root)
            self.assertEqual(again.returncode, 0, again.stderr)
            self.assertEqual(again.stdout, result.stdout)
            (installed / "plugins/pstack/skills/architect/SKILL.md").write_text("corrupted")
            corrupt = bundle(root)
            self.assertNotEqual(corrupt.returncode, 0)
            self.assertIn("bundle differs from committed source", corrupt.stderr)

    def test_manifest_cannot_grant_a_depth_exception(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            subprocess.run(["git", "init", "-q"], cwd=root, env=fixture_env(), check=True)
            manifest = json.loads((PLUGIN / "UPSTREAM.json").read_text())
            path = "plugins/pstack/a/b/c/d/anything.md"
            manifest["files"].append({"path": path.removeprefix("plugins/pstack/"), "sha256": "0" * 64})
            target = root / path
            target.parent.mkdir(parents=True)
            target.write_text("fabricated\n")
            (root / "plugins/pstack/UPSTREAM.json").write_text(json.dumps(manifest))
            subprocess.run(["git", "add", path, "plugins/pstack/UPSTREAM.json"],
                           cwd=root, env=fixture_env(), check=True)
            for args in ([], ["--paths", path]):
                with self.subTest(args=args):
                    result = subprocess.run([sys.executable, str(REPO / "ops/githooks/path-hygiene-check.py"), *args],
                                            cwd=root, env=fixture_env(), capture_output=True, text=True)
                    self.assertEqual(result.returncode, 1, result.stderr)
                    self.assertIn(path, result.stderr)

    def test_boot_route_has_one_home_and_readable_migration_steps(self):
        claude = (REPO / "CLAUDE.md").read_text()
        agents = (REPO / "AGENTS.md").read_text()
        self.assertNotIn("Rigorous engineering work uses", claude)
        self.assertEqual(agents.count("Rigorous engineering work uses"), 1)
        steps = json.loads((REPO / "ops/config/task-boot/dell-migration.json").read_text())["instructions"]
        self.assertLessEqual(max(map(len, steps.splitlines())), 90)

    def test_merge_override_routes_both_playbooks_to_the_queue(self):
        port = (PLUGIN / "PORT.md").read_text().split("<!-- reference-inventory -->", 1)[0]
        row = next((line for line in port.splitlines() if line.startswith("| M13 |")), "")
        for required in ("Shipping", "Autopilot", "orchestrator", "release-pipeline.v1.json",
                         "gh pr merge", "auto-merge", "overrides"):
            self.assertIn(required, row)
        self.assertNotIn("Preserve create/edit/view/check/thread/merge semantics", port)

    def test_delegate_routes_use_model_room_with_verified_model_and_effort(self):
        port = (PLUGIN / "PORT.md").read_text().split("<!-- reference-inventory -->", 1)[0]
        routes = port.split("## Delegate routes", 1)[1].split("## Remaining platform limits", 1)[0]
        for model in ("gpt-6.1-sol", "grok-4.7", "claude-opus-5-5"):
            self.assertIn(model, routes)
        for required in ("Model Room", "desk", "Jev", "readback", "result", "effort"):
            self.assertIn(required, routes)
        for binary, verb in (("codex", "exec"), ("claude", "-p"), ("grok", "-p")):
            self.assertNotIn(f"{binary} {verb}", port)
        self.assertNotIn("--permission-mode auto", port)

    def test_installer_refuses_a_feature_worktree_before_client_effects(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "canonical"
            root.mkdir()
            env = fixture_env()
            def git(*args):
                return subprocess.run(["git", *args], cwd=root, env=env, check=True, capture_output=True)
            git("init", "-q", "-b", "main")
            git("config", "user.email", "selftest@example.invalid")
            git("config", "user.name", "Selftest")
            git("config", "core.hooksPath", "/dev/null")
            shutil.copytree(PLUGIN, root / "plugins/pstack", ignore=shutil.ignore_patterns("node_modules", "__pycache__"))
            shutil.copytree(REPO / ".claude-plugin", root / ".claude-plugin")
            git("add", "plugins/pstack", ".claude-plugin")
            git("commit", "-qm", "fixture")
            worktree = Path(tmp) / "feature"
            git("worktree", "add", "-qb", "topic", str(worktree))
            stubs = Path(tmp) / "bin"
            stubs.mkdir()
            log = Path(tmp) / "client-effects"
            for name in ("claude", "bun"):
                stub = stubs / name
                stub.write_text('#!/bin/sh\nprintf "called\\n" >> "$PSTACK_EFFECT_LOG"\nprintf "[]\\n"\n')
                stub.chmod(0o755)
            env.update(PATH=str(stubs) + os.pathsep + env["PATH"],
                       CODEX_HOME=str(Path(tmp) / "codex"), PSTACK_EFFECT_LOG=str(log))
            for source in (worktree, root):
                if source == root:
                    git("checkout", "-qb", "other-topic")
                with self.subTest(source=source):
                    result = subprocess.run(["bash", str(source / "plugins/pstack/scripts/install.sh")],
                                            cwd=source, env=env, capture_output=True, text=True)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn("canonical main checkout", result.stderr)
                    self.assertFalse(log.exists(), result.stderr)
                    self.assertFalse((Path(tmp) / "codex").exists())

    def test_canonical_reinstall_moves_only_this_repositories_old_bindings(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "canonical"
            root.mkdir()
            env = fixture_env()
            def git(*args):
                return subprocess.run(["git", *args], cwd=root, env=env, check=True, capture_output=True)
            git("init", "-q", "-b", "main")
            git("config", "user.email", "selftest@example.invalid")
            git("config", "user.name", "Selftest")
            git("config", "core.hooksPath", "/dev/null")
            shutil.copytree(PLUGIN, root / "plugins/pstack", ignore=shutil.ignore_patterns("node_modules", "__pycache__"))
            shutil.copytree(REPO / ".claude-plugin", root / ".claude-plugin")
            git("add", "plugins/pstack", ".claude-plugin")
            git("commit", "-qm", "fixture")
            old = Path(tmp) / "feature"
            git("worktree", "add", "-qb", "topic", str(old))
            stubs = Path(tmp) / "bin"
            stubs.mkdir()
            claude = stubs / "claude"
            claude.write_text('''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
root = Path(os.environ["PSTACK_CANONICAL"])
old = Path(os.environ["PSTACK_OLD"])
with open(os.environ["PSTACK_EFFECT_LOG"], "a") as log:
    log.write(" ".join(sys.argv[1:]) + "\\n")
if sys.argv[1:] == ["plugin", "marketplace", "list", "--json"]:
    added = "plugin marketplace add " + str(root.resolve()) in Path(os.environ["PSTACK_EFFECT_LOG"]).read_text()
    source = root if added and not os.environ.get("PSTACK_STALE_MARKETPLACE") else old
    print(json.dumps([{"name":"carr-local", "source":{"path":str(source)}}]))
elif sys.argv[1:] == ["plugin", "list", "--json"]:
    print(json.dumps([{"id":"pstack@carr-local", "scope":"user", "enabled":True,
                      "installPath":str(root / "plugins/pstack")}]))
''')
            claude.chmod(0o755)
            bun = stubs / "bun"
            bun.write_text("#!/bin/sh\nexit 0\n")
            bun.chmod(0o755)
            codex = Path(tmp) / "codex"
            skills = codex / "skills"
            skills.mkdir(parents=True)
            (skills / "teach").symlink_to(old / "plugins/pstack/skills/teach")
            unrelated = Path(tmp) / "unrelated"
            unrelated.mkdir()
            (skills / "correct").symlink_to(unrelated)
            log = Path(tmp) / "client-effects"
            env.update(PATH=str(stubs) + os.pathsep + env["PATH"], CODEX_HOME=str(codex),
                       PSTACK_CANONICAL=str(root), PSTACK_OLD=str(old), PSTACK_EFFECT_LOG=str(log))
            stale = subprocess.run(["bash", str(root / "plugins/pstack/scripts/install.sh")],
                                   cwd=root, env={**env, "PSTACK_STALE_MARKETPLACE": "1"},
                                   capture_output=True, text=True)
            self.assertNotEqual(stale.returncode, 0, "installer accepted a feature-worktree marketplace after rebind")
            self.assertIn("marketplace readback", stale.stderr)
            self.assertNotIn("Claude marketplace and Codex skills use canonical main", stale.stdout)
            self.assertEqual((skills / "teach").resolve(), (old / "plugins/pstack/skills/teach").resolve())
            self.assertEqual((skills / "correct").resolve(), unrelated.resolve())
            self.assertFalse((skills / "pstack-correct").exists())
            result = subprocess.run(["bash", str(root / "plugins/pstack/scripts/install.sh")],
                                    cwd=root, env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("plugin marketplace remove carr-local", log.read_text())
            self.assertIn("plugin marketplace add " + str(root.resolve()), log.read_text())
            self.assertEqual((skills / "teach").resolve(), (root / "plugins/pstack/skills/teach").resolve())
            self.assertEqual((skills / "correct").resolve(), unrelated.resolve())
            self.assertEqual((skills / "pstack-correct").resolve(), (root / "plugins/pstack/skills/correct").resolve())


if __name__ == "__main__":
    unittest.main()
