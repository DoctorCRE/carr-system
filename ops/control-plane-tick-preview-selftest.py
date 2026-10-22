#!/usr/bin/env python3
"""Read-only revival preview with a synthetic ledger connection."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("tick_preview_cli", ROOT / "tools/control-plane.py")
assert SPEC and SPEC.loader
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


class PreviewTests(unittest.TestCase):
    def test_dry_run_lists_due_canaries_and_backlog_without_effects(self):
        workflow = {"key": "synthetic-collector", "version": 1, "enabled": True,
                    "recurrence": {"cron": "* * * * *", "timezone": "UTC"},
                    "execution": {"kind": "cognition", "input_builder": "synthetic.inputs",
                                  "canary": {"enabled": True}},
                    "inventory": {"canonical_reads": ["synthetic source"]}}
        with patch.object(module, "load_manifest", return_value={"workflows": [workflow]}), \
             patch.object(module, "connect") as connect, \
             patch.object(module, "run_once") as run, \
             patch.object(sys, "argv", ["control-plane.py", "tick", "--mode", "auto", "--dry-run", "--max-jobs", "1"]), \
             contextlib.redirect_stdout(io.StringIO()) as output:
            conn = connect.return_value.__enter__.return_value
            cur = conn.cursor.return_value.__enter__.return_value
            cur.fetchall.side_effect = [[("shadow", "accepted")],
                [("synthetic-collector", 1, "canary", "queued", True, 500, "2026-10-16T00:00:00Z")]]
            self.assertEqual(module.main(), 0)
        result = json.loads(output.getvalue())
        self.assertTrue(result["dry_run"])
        self.assertEqual(result["due"][0]["mode"], "canary")
        self.assertEqual(result["due"][0]["canonical_reads"], ["synthetic source"])
        self.assertEqual(result["backlog"][0]["count"], 500)
        self.assertEqual(result["backlog"][0]["execution"]["input_builder"], "synthetic.inputs")
        self.assertEqual(result["max_jobs"], 1)
        self.assertFalse(result["replays_missed_cron_minutes"])
        connect.assert_called_once_with(read_only=True)
        run.assert_not_called()
        self.assertTrue(all(call.args[0].strip().lower().startswith("select ")
                            and not any(verb in call.args[0] for verb in ("enqueue_job", "claim_job", "reap_expired"))
                            for call in cur.execute.call_args_list))
        conn.commit.assert_not_called()


if __name__ == "__main__":
    unittest.main()
