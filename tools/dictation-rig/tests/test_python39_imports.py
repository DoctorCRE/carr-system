"""Exercise launchd entries and compile their local import closure on Python 3.9."""
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


class LaunchdImports(unittest.TestCase):
    def test_local_import_closure_compiles(self):
        pending = [SCRIPTS / filename for filename in ENTRY_POINTS]
        visited = set()
        while pending:
            path = pending.pop()
            if path in visited:
                continue
            visited.add(path)
            source = path.read_text()
            compile(source, str(path), "exec")
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
            print("COMPILED " + str(path.relative_to(REPO)))

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

    def run_entry(self, filename, arguments):
        code = '''
import runpy, sys
from unittest.mock import patch
sys.path[:0] = sys.argv[1:4]
sys.argv = sys.argv[4:]
with patch("socket.socket.connect", side_effect=AssertionError("network during entry")), patch(
    "subprocess.Popen", side_effect=AssertionError("process during entry")
):
    runpy.run_path(sys.argv[0], run_name="__main__")
'''
        with tempfile.TemporaryDirectory() as home:
            return subprocess.run(
                [sys.executable, "-I", "-S", "-c", code, *map(str, SEARCH_PATH),
                 str(SCRIPTS / filename), *arguments],
                cwd=REPO, env={"HOME": home, "PATH": "/usr/bin:/bin"},
                capture_output=True, text=True, timeout=10,
            )


if __name__ == "__main__":
    unittest.main()
