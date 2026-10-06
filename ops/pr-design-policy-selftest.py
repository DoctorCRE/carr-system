#!/usr/bin/env python3
"""Check the real client entry points for PR policy drift and boot overage."""
import importlib.util
from pathlib import Path
import re
import unittest


REPO = Path(__file__).resolve().parent.parent
POLICY_HEADING = "## Before every PR: design and debt pass"


class PRDesignPolicyTests(unittest.TestCase):
    def test_client_pointer_resolves_the_single_policy(self):
        claude = (REPO / "CLAUDE.md").read_text()
        agents = (REPO / "AGENTS.md").read_text()
        links = re.findall(r"\[[^\]]+\]\((AGENTS\.md#[^)]+)\)", claude)
        self.assertEqual(links, ["AGENTS.md#rule-lifecycle-evidence",
                                 "AGENTS.md#before-every-pr-design-and-debt-pass"])
        self.assertEqual(agents.count(POLICY_HEADING), 1)
        self.assertNotIn("`codebase-design`", claude)
        self.assertNotIn("`zero-tech-debt`", claude)

    def test_canonical_policy_keeps_timing_and_both_required_skills(self):
        policy = (REPO / "AGENTS.md").read_text().split(POLICY_HEADING, 1)[1]
        self.assertIn("Before opening or updating any pull request", policy)
        for name in ("codebase-design", "zero-tech-debt"):
            self.assertIn(f"~/.agents/skills/{name}/SKILL.md", policy)

    def test_real_boot_stays_within_the_existing_budget(self):
        spec = importlib.util.spec_from_file_location(
            "boot_budget", REPO / "ops/boot-budget-check.py")
        checker = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(checker)
        budget, tokens, total, _ = checker.measure()
        self.assertEqual(checker.evaluate(budget, tokens, total), [])


if __name__ == "__main__":
    unittest.main()
