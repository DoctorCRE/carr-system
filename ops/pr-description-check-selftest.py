#!/usr/bin/env python3
"""The PR description gate must refuse empty and untouched template bodies."""

import importlib.util
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPT = Path(__file__).with_name("pr-description-check.py")


class PRDescriptionCheckTest(unittest.TestCase):
    def test_description_edits_trigger_validation_without_a_push(self):
        repo = SCRIPT.parent.parent
        result = subprocess.run(
            ["node", "-e", "const fs=require('fs'); const yaml=require('js-yaml');"
             "console.log(JSON.stringify(yaml.load(fs.readFileSync(process.argv[1], 'utf8'))));",
             str(repo / ".github/workflows/ci.yml")],
            cwd=repo / "mcp-server", capture_output=True, text=True, check=True)
        workflow = json.loads(result.stdout)
        trigger = workflow["on"]["pull_request"] or {}
        self.assertEqual(set(trigger.get("types", [])),
                         {"opened", "synchronize", "reopened", "edited"})
        steps = workflow["jobs"]["classes"]["steps"]
        step = next(step for step in steps if step.get("run") == "python3 ops/pr-description-check.py")
        self.assertIn("github.event_name == 'pull_request'", step["if"])
        self.assertIn("matrix.classes == 'gates'", step["if"])

    def test_empty_and_template_only_fail(self):
        spec = importlib.util.spec_from_file_location("pr_description_check", SCRIPT)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.assertFalse(module.has_description(""))
        self.assertFalse(module.has_description("  \n"))
        self.assertFalse(module.has_description(
            "## What changed\n<!-- Describe the change. -->\n\n"
            "## How verified\n<!-- Name the checks you ran. -->"))
        self.assertTrue(module.has_description(
            "## What changed\nThe progress board now shows model and effort.\n"
            "## How verified\nRan the board tests."))

    def test_event_body_is_checked(self):
        spec = importlib.util.spec_from_file_location("pr_description_check", SCRIPT)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as directory:
            event = Path(directory) / "event.json"
            event.write_text(json.dumps({"pull_request": {"body": ""}}))
            with patch.dict("os.environ", {"GITHUB_EVENT_PATH": str(event)}):
                self.assertEqual(module.main(), 1)
            event.write_text(json.dumps({"pull_request": {"body": "Explain the board change."}}))
            with patch.dict("os.environ", {"GITHUB_EVENT_PATH": str(event)}):
                self.assertEqual(module.main(), 0)


if __name__ == "__main__":
    unittest.main()
