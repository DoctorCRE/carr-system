#!/usr/bin/env python3
"""The main-run hint performs one read and never replays a check."""
import importlib.util
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("main_hint", Path(__file__).with_name("inherited-from-main.py"))
assert spec and spec.loader
hint = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hint)


class MainHintTests(unittest.TestCase):
    def test_links_the_failed_main_run(self):
        url = "https://github.com/jbookout/carr-system/actions/runs/123"
        with patch.object(hint.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, url + "\n")) as run, patch("builtins.print") as output:
            hint.main()
        output.assert_called_once_with("Latest failed main CI run: " + url)
        self.assertEqual(run.call_count, 1)
        self.assertEqual(run.call_args.args[0][:3], ["gh", "run", "list"])

    def test_unavailable_history_is_only_a_missing_hint(self):
        for error in (OSError(), subprocess.TimeoutExpired("gh", 15)):
            with patch.object(hint.subprocess, "run", side_effect=error), patch("builtins.print") as output:
                hint.main()
                output.assert_not_called()


if __name__ == "__main__":
    unittest.main()
