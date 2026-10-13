"""door-reachability-check-selftest.py — the paired suite for
ops/door-reachability-check.py.

Each case builds a throwaway repository outside the checkout, writes the one
door, test, doc or declaration the case is about, runs the real check against
it with --repo, and asserts the exit code and the line it prints. The base
fixture passes; every failing case is that base plus one change, so a failure
here names exactly the edge the check stopped seeing.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from git_env import fixture_env  # noqa: E402

CHECK = os.path.join(os.path.dirname(os.path.abspath(__file__)), "door-reachability-check.py")
ENV = fixture_env()
REASON = "Imported the 2026-08 ledger rows into the record layer once and never again."


def manifest(*declared: dict) -> str:
    return json.dumps({"schema": "door-inventory/v1", "work_request": "WR-000284",
                       "declared": list(declared)}, indent=2)


def declare(path: str, cls: str = "one_shot", reason: str = REASON,
            evidence: str = "989f4122") -> dict:
    return {"path": path, "class": cls, "reason": reason, "evidence": evidence}


BASE = {
    "ops/ci.sh": "python3 tools/wired-tool.py --check\n",
    "tools/wired-tool.py": "import json\n",
    "ops/x-selftest.py": "subprocess.run(['python3', 'tools/ci_only_tool.py'])\n",
    "tools/ci_only_tool.py": "print('exercised by a test')\n",
    "docs/guide.md": "Run `python3 tools/prose_tool.py` by hand.\n",
    "tools/prose_tool.py": "print('named in prose')\n",
    "tools/oneshot.py": "print('ran once')\n",
    "ops/config/door-inventory.v1.json": manifest(declare("tools/oneshot.py")),
}


class DoorReachability(unittest.TestCase):
    def setUp(self) -> None:
        self.repo = tempfile.mkdtemp(prefix="door-reach-")
        self.addCleanup(shutil.rmtree, self.repo, True)

    def build(self, files: dict) -> None:
        for rel, body in files.items():
            path = os.path.join(self.repo, rel)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(body)
        subprocess.run(["git", "init", "-q"], cwd=self.repo, env=ENV, check=True)
        subprocess.run(["git", "add", "-A"], cwd=self.repo, env=ENV, check=True)

    def run_check(self, changes: dict[str, str | None] | None = None) -> tuple[int, str]:
        files = dict(BASE)
        for rel, body in (changes or {}).items():
            if body is None:
                files.pop(rel, None)
            else:
                files[rel] = body
        self.build(files)
        p = subprocess.run([sys.executable, CHECK, "--repo", self.repo],
                           capture_output=True, text=True, env=ENV, timeout=60)
        return p.returncode, p.stdout + p.stderr

    def test_base_fixture_passes_with_every_tier_counted(self) -> None:
        code, out = self.run_check()
        self.assertEqual(code, 0, out)
        self.assertIn("door-reachability: OK — 2 wired, 1 ci-only, 1 prose-only, 1 declared", out)

    def test_unreachable_and_undeclared_fails_naming_the_path(self) -> None:
        code, out = self.run_check({"tools/orphan.py": "print('nobody calls me')\n"})
        self.assertEqual(code, 1, out)
        self.assertIn("unreachable  tools/orphan.py", out)

    def test_declared_one_shot_passes(self) -> None:
        code, out = self.run_check()
        self.assertEqual(code, 0, out)
        self.assertIn("1 declared", out)
        self.assertNotIn("tools/oneshot.py", out)

    def test_script_named_from_ci_sh_is_wired(self) -> None:
        code, out = self.run_check()
        self.assertEqual(code, 0, out)
        self.assertIn("2 wired", out)
        self.assertNotIn("tools/wired-tool.py", out)

    def test_script_named_only_from_a_selftest_is_ci_only_and_does_not_fail(self) -> None:
        code, out = self.run_check()
        self.assertEqual(code, 0, out)
        ci_block = out.split("ci-only:", 1)[1].split("prose-only:", 1)[0]
        self.assertIn("tools/ci_only_tool.py", ci_block)
        self.assertIn("do not fail yet", out)

    def test_script_named_only_from_docs_is_prose_only(self) -> None:
        code, out = self.run_check()
        self.assertEqual(code, 0, out)
        prose_block = out.split("prose-only:", 1)[1]
        self.assertIn("tools/prose_tool.py", prose_block)

    def test_declaration_whose_path_is_gone_fails(self) -> None:
        code, out = self.run_check({"tools/oneshot.py": None})
        self.assertEqual(code, 1, out)
        self.assertIn("stale declaration  tools/oneshot.py — the path no longer exists", out)

    def test_declaration_whose_artifact_became_reachable_fails_as_stale(self) -> None:
        code, out = self.run_check({"ops/ci.sh": "python3 tools/wired-tool.py tools/oneshot.py\n"})
        self.assertEqual(code, 1, out)
        self.assertIn("stale declaration  tools/oneshot.py — now wired", out)

    def test_placeholder_reason_fails(self) -> None:
        bad = manifest(declare("tools/oneshot.py", reason="n/a"))
        code, out = self.run_check({"ops/config/door-inventory.v1.json": bad})
        self.assertEqual(code, 1, out)
        self.assertIn("invalid declaration  tools/oneshot.py — placeholder reason", out)

    def test_invalid_class_fails(self) -> None:
        bad = manifest(declare("tools/oneshot.py", cls="someday"))
        code, out = self.run_check({"ops/config/door-inventory.v1.json": bad})
        self.assertEqual(code, 1, out)
        self.assertIn("invalid declaration  tools/oneshot.py — class 'someday'", out)

    def test_unreferenced_skill_fails(self) -> None:
        code, out = self.run_check({"plugins/kit/skills/lonely-skill/SKILL.md": "# lonely\n"})
        self.assertEqual(code, 1, out)
        self.assertIn("unreachable  plugins/kit/skills/lonely-skill/SKILL.md", out)

    def test_skill_of_a_plugin_named_from_agents_md_is_wired_through_its_readme(self) -> None:
        code, out = self.run_check({
            "plugins/kit/skills/lonely-skill/SKILL.md": "# lonely\n",
            "plugins/kit/README.md": "Use the `lonely-skill` skill.\n",
            "AGENTS.md": "Engineering work uses plugins/kit.\n",
        })
        self.assertEqual(code, 0, out)
        self.assertIn("3 wired", out)

    def test_skill_of_an_unnamed_plugin_is_prose_only(self) -> None:
        code, out = self.run_check({
            "plugins/kit/skills/lonely-skill/SKILL.md": "# lonely\n",
            "plugins/kit/README.md": "Use the `lonely-skill` skill.\n",
        })
        self.assertEqual(code, 0, out)
        prose_block = out.split("prose-only:", 1)[1]
        self.assertIn("plugins/kit/skills/lonely-skill/SKILL.md", prose_block)

    def test_test_tier_does_not_promote_to_wired(self) -> None:
        code, out = self.run_check({"tools/ci_only_tool.py": "import wired_helper_module\n",
                                      "tools/wired_helper_module.py": "X = 1\n"})
        self.assertEqual(code, 0, out)
        ci_block = out.split("ci-only:", 1)[1].split("prose-only:", 1)[0]
        self.assertIn("tools/wired_helper_module.py", ci_block)

    def test_short_python_import_reaches_the_module(self) -> None:
        code, out = self.run_check({"tools/wired-tool.py": "from lib import tiny\n",
                                    "lib/tiny.py": "X = 1\n"})
        self.assertEqual(code, 0, out)
        self.assertIn("3 wired", out)

    def test_short_stem_in_prose_does_not_reach(self) -> None:
        code, out = self.run_check({"ops/ci.sh": "python3 tools/wired-tool.py; echo tiny\n",
                                    "lib/tiny.py": "X = 1\n"})
        self.assertEqual(code, 1, out)
        self.assertIn("unreachable  lib/tiny.py", out)

    def test_declared_ci_only_artifact_is_not_stale(self) -> None:
        both = manifest(declare("tools/oneshot.py"),
                        declare("tools/ci_only_tool.py", cls="inert", evidence="WR-000284"))
        code, out = self.run_check({"ops/config/door-inventory.v1.json": both})
        self.assertEqual(code, 0, out)
        self.assertIn("0 ci-only, 1 prose-only, 2 declared", out)

    def test_db_gate_marker_outside_the_ci_glob_is_not_a_door(self) -> None:
        code, out = self.run_check({"tools/marked-elsewhere.py": "# ci: db-gate\nprint(1)\n"})
        self.assertEqual(code, 1, out)
        self.assertIn("unreachable  tools/marked-elsewhere.py", out)

    def test_db_gate_marker_on_an_ops_gate_is_a_door(self) -> None:
        code, out = self.run_check({"ops/marked-gate.py": "# ci: db-gate\nprint(1)\n"})
        self.assertEqual(code, 0, out)
        self.assertIn("3 wired", out)

    def test_unwired_extensionless_executable_fails(self) -> None:
        code, out = self.run_check({"bin/orphan-tool": "#!/bin/sh\necho hi\n"})
        self.assertEqual(code, 1, out)
        self.assertIn("unreachable  bin/orphan-tool", out)

    def test_unwired_zsh_script_fails(self) -> None:
        code, out = self.run_check({"tools/orphan.zsh": "echo hi\n"})
        self.assertEqual(code, 1, out)
        self.assertIn("unreachable  tools/orphan.zsh", out)

    def test_unwired_hook_fails(self) -> None:
        code, out = self.run_check({"hooks/orphan-hook.py": "print(1)\n"})
        self.assertEqual(code, 1, out)
        self.assertIn("unreachable  hooks/orphan-hook.py", out)

    def test_json_under_docs_is_a_record_not_prose(self) -> None:
        code, out = self.run_check({"docs/inventory.json": '["tools/listed_only.py"]\n',
                                    "tools/listed_only.py": "print(1)\n"})
        self.assertEqual(code, 1, out)
        self.assertIn("unreachable  tools/listed_only.py", out)

    def test_records_are_not_callers(self) -> None:
        code, out = self.run_check({"out/test-report.py": "run('tools/recorded_only.py')\n",
                                    "corpus/README.md": "tools/recorded_only.py\n",
                                    "tools/recorded_only.py": "print(1)\n"})
        self.assertEqual(code, 1, out)
        self.assertIn("unreachable  tools/recorded_only.py", out)

    def test_to_delete_is_a_record_not_an_artifact(self) -> None:
        code, out = self.run_check({"_to_delete/benny/SKILL.md": "# benny\n",
                                    "_to_delete/pipelines/old_import.py": "print(1)\n"})
        self.assertEqual(code, 0, out)
        self.assertNotIn("_to_delete/", out)

    def test_short_skill_name_as_a_bare_word_does_not_reach(self) -> None:
        code, out = self.run_check({"ops/ci.sh": "python3 tools/wired-tool.py # zzq\n",
                                    "plugins/kit/skills/zzq/SKILL.md": "# zzq\n"})
        self.assertEqual(code, 1, out)
        self.assertIn("unreachable  plugins/kit/skills/zzq/SKILL.md", out)

    def test_short_skill_name_in_skill_form_reaches(self) -> None:
        code, out = self.run_check({"ops/ci.sh": "python3 tools/wired-tool.py # run /zzq\n",
                                    "plugins/kit/skills/zzq/SKILL.md": "# zzq\n"})
        self.assertEqual(code, 0, out)
        self.assertIn("3 wired", out)

    def test_inert_without_a_loop_or_work_request_fails(self) -> None:
        bad = manifest(declare("tools/oneshot.py", cls="inert", evidence="989f4122"))
        code, out = self.run_check({"ops/config/door-inventory.v1.json": bad})
        self.assertEqual(code, 1, out)
        self.assertIn("invalid declaration  tools/oneshot.py — inert needs the loop", out)


if __name__ == "__main__":
    unittest.main()
