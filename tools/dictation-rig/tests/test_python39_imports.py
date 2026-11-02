"""Exercise launchd entries and import their local closure on Python 3.9."""
import ast
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPTS = Path(__file__).resolve().parents[1] / "bin"
REPO = SCRIPTS.parents[2]
ENTRY_POINTS = ("call_budget.py", "capture-bridge.py", "call-mode.py", "phone_intake.py")
SEARCH_PATH = (SCRIPTS, REPO, REPO / "tools")
NEWER_PYTHON_MODULES = {
    "post_call_jev",  # post_call runs this worker with .venv/bin/python3.
    "lib.headless_tasks",  # Only the .venv post_call_jev worker imports this module.
}


class LaunchdImports(unittest.TestCase):
    def test_local_import_closure_imports(self):
        pending = [SCRIPTS / filename for filename in ENTRY_POINTS]
        visited = set()
        modules = set()
        while pending:
            path = pending.pop()
            if path in visited:
                continue
            visited.add(path)
            source = path.read_text()
            for root in SEARCH_PATH:
                if path.is_relative_to(root):
                    parts = path.relative_to(root).with_suffix("").parts
                    modules.add(".".join(parts[:-1] if parts[-1] == "__init__" else parts))
                    break
            tree = ast.parse(source, filename=str(path))
            for node in ast.walk(tree):
                names = []
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    base = node.module or ""
                    if node.level:
                        package = path.parent.relative_to(REPO).parts
                        base = ".".join((*package[:len(package) - node.level + 1], base)).strip(".")
                    names = [base] + [base + "." + alias.name for alias in node.names]
                for name in names:
                    parts = name.split(".")
                    for root in SEARCH_PATH:
                        for length in range(1, len(parts) + 1):
                            target = root.joinpath(*parts[:length])
                            for candidate in (target.with_suffix(".py"), target / "__init__.py"):
                                if candidate.is_file():
                                    pending.append(candidate)
        self.assertTrue(NEWER_PYTHON_MODULES <= modules)
        result = self.run_isolated('''
import importlib, sys
for name in sys.argv[1:]:
    importlib.import_module(name)
    print("IMPORTED " + name)
''', sorted(modules - NEWER_PYTHON_MODULES))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_newer_python_exclusions(self):
        self.assertEqual(NEWER_PYTHON_MODULES, {"post_call_jev", "lib.headless_tasks"})

    def test_real_entry_points(self):
        for filename, arguments, expected in (
            ("call_budget.py", [], ""),
            ("capture-bridge.py", ["check"], "provisioned: no"),
            ("call-mode.py", ["--help"], "usage:"),
            ("phone_intake.py", ["--help"], "usage:"),
        ):
            with self.subTest(script=filename):
                result = self.run_entry(filename, arguments)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn(expected, result.stdout)

    def test_release_requires_venv(self):
        result = self.run_entry("call-mode.py", ["release", "synthetic-session"])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("release requires the repository virtual environment", result.stderr)
        self.assertIn(".venv/bin/python3", result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        relative = self.run_entry("call-mode.py", ["release", "synthetic-session"], relative=True)
        self.assertIn(str((SCRIPTS / "call-mode.py").resolve()), relative.stderr)

    def run_entry(self, filename, arguments, relative=False):
        script = SCRIPTS / filename
        if relative:
            script = script.relative_to(REPO / "tools")
        return self.run_isolated('''
import runpy, sys
sys.argv = sys.argv[1:]
runpy.run_path(sys.argv[0], run_name="__main__")
''', [str(script), *arguments], cwd=REPO / "tools" if relative else REPO)

    def run_isolated(self, code, arguments, cwd=REPO):
        bootstrap = '''
import sys
from unittest.mock import patch
sys.path[:0] = sys.argv[1:4]
code = sys.argv[4]
sys.argv = sys.argv[4:]
with patch("socket.socket.connect", side_effect=AssertionError("network during entry")), patch(
    "subprocess.Popen", side_effect=AssertionError("process during entry")
):
    exec(code)
'''
        with tempfile.TemporaryDirectory() as home:
            return subprocess.run(
                [sys.executable, "-I", "-S", "-c", bootstrap, *map(str, SEARCH_PATH), code, *arguments],
                cwd=cwd, env={"HOME": home, "PATH": "/usr/bin:/bin"},
                capture_output=True, text=True, timeout=10,
            )


if __name__ == "__main__":
    unittest.main()
