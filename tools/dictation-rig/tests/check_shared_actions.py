"""Opt-in synthetic Sonnet regression. Run this file directly with Python."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bin"))
import post_call


class SharedActions(unittest.TestCase):
    def extract(self, lines):
        context = {"deals": [{
            "id": "synthetic-sheet", "name": "Synthetic Sheet Clinic", "owner": "Dell",
            "operating_state": "active", "participants": [],
        }]}
        transcript = {"segments": [{"speaker": speaker, "text": text} for speaker, text in lines]}
        raw = post_call.default_distiller({
            "context": context, "transcript": transcript, "call_date": "2026-10-26",
        })
        report = post_call.normalize_distillation(
            raw, context, "synthetic-shared-action", call_date="2026-10-26", transcript=transcript,
        )
        return [(owner, task) for owner in ("joe", "dell") for task in report[f"{owner}_tasks"]]

    def test_collective_action_belongs_once_to_deal_owner(self):
        tasks = self.extract([
            ("Joe", "For Synthetic Sheet Clinic, we will run the sheet and send the numbers together."),
            ("Dell", "Yes, we will run the sheet and send the numbers together."),
        ])
        self.assertEqual(len(tasks), 1, tasks)
        owner, task = tasks[0]
        self.assertEqual(owner, "dell")
        self.assertIn("joe", task["title"].casefold())

    def test_volunteer_overrides_deal_owner(self):
        tasks = self.extract([
            ("Dell", "For Synthetic Sheet Clinic, we need to run the sheet and send the numbers together."),
            ("Joe", "I will run the sheet and send the numbers with you, Dell."),
            ("Dell", "Agreed, Joe will do it with me."),
        ])
        self.assertEqual(len(tasks), 1, tasks)
        owner, task = tasks[0]
        self.assertEqual(owner, "joe")
        self.assertIn("dell", task["title"].casefold())

    def test_distinct_individual_actions_remain_separate(self):
        tasks = self.extract([
            ("Joe", "For Synthetic Sheet Clinic, I will run the sheet."),
            ("Dell", "For that same clinic, I will call the lender. Those are two separate tasks."),
        ])
        self.assertEqual(len(tasks), 2, tasks)
        self.assertEqual(sorted(owner for owner, _ in tasks), ["dell", "joe"])
        by_owner = dict(tasks)
        self.assertIn("sheet", by_owner["joe"]["title"].casefold())
        self.assertIn("lender", by_owner["dell"]["title"].casefold())


if __name__ == "__main__":
    unittest.main()
