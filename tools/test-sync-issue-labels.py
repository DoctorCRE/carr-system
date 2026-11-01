"""Verify label synchronization without contacting GitHub."""
import importlib.util
import subprocess
import unittest
from pathlib import Path
from unittest.mock import call, patch

SPEC = importlib.util.spec_from_file_location("sync_labels", Path(__file__).with_name("sync-issue-labels.py"))


class LabelSyncTest(unittest.TestCase):
    def setUp(self):
        self.module = importlib.util.module_from_spec(SPEC)
        SPEC.loader.exec_module(self.module)
        self.labels = [{"name": "needs-triage", "color": "ededed", "description": "Evaluate"},
                       {"name": "ready-for-agent", "color": "0e8a16", "description": "Ready"}]

    def test_serial_gh_calls_have_two_second_spacing(self):
        with patch.object(self.module.subprocess, "run") as run, patch.object(self.module.time, "sleep") as sleep:
            events = []
            sleep.side_effect = lambda delay: events.append(("wait", delay))
            run.side_effect = lambda *args, **kwargs: events.append(("gh", args[0]))
            self.module.sync_labels("DoctorCRE/carr-system", self.labels)
        self.assertEqual([event[0] for event in events], ["wait", "gh", "wait", "gh"])
        self.assertEqual(sleep.call_args_list, [call(2), call(2)])
        self.assertEqual(run.call_args_list, [call([
            "gh", "label", "create", label["name"], "--repo", "DoctorCRE/carr-system",
            "--color", label["color"], "--description", label["description"], "--force"
        ], check=True) for label in self.labels])

    def test_failed_call_stops_sync(self):
        with patch.object(self.module.subprocess, "run", side_effect=subprocess.CalledProcessError(1, "gh")) as run, patch.object(self.module.time, "sleep"):
            with self.assertRaises(subprocess.CalledProcessError):
                self.module.sync_labels("DoctorCRE/carr-system", self.labels)
        self.assertEqual(run.call_count, 1)


if __name__ == "__main__":
    unittest.main()
