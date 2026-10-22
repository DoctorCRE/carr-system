"""Offline product tests for the generated Deal Room's deal reading."""
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ops import jev_deal_read
from typesafe_sdk import TypeSafeError


class DealReadingTests(unittest.TestCase):
    def bundle(self, **overrides):
        return dict({
            "id": "synthetic-deal", "name": "Synthetic Clinic", "client": "Synthetic", "phase": "negotiation",
            "deal_type": "renewal", "segment": "medical", "city": "Synthetic City", "owner": "Joe",
            "next_step": "The tenant must review the landlord counter and send a response.",
            "next_step_due": None, "status_narrative": "Landlord sent revised terms.",
            "history": ["The tenant asked CARR to prepare a counter."],
            "days_since_record_touched": 2, "has_evidence": True, "evidence_chars": 240,
        }, **overrides)

    def test_deal_uses_one_official_sdk_call_for_all_three_judgments(self):
        client = Mock()
        client.system_one.return_value = SimpleNamespace(answers={
            "movement": SimpleNamespace(score=3.0, confidence=0.8),
            "waiting_on": SimpleNamespace(choice="client", confidence=0.2),
            "silence_is_bad": SimpleNamespace(noul=0.1),
        })
        result = jev_deal_read.read_deal(self.bundle(), client=client)
        self.assertTrue(result["judged"])
        self.assertEqual(result["movement_rung"], 4)
        self.assertEqual(result["waiting_on"], "client")
        self.assertEqual(client.system_one.call_count, 1)
        self.assertEqual(set(client.system_one.call_args.kwargs["questions"]),
                         {"movement", "waiting_on", "silence_is_bad"})

    def test_thin_evidence_does_not_call_the_sdk(self):
        client = Mock()
        result = jev_deal_read.read_deal(self.bundle(has_evidence=False, evidence_chars=10), client=client)
        self.assertFalse(result["judged"])
        client.system_one.assert_not_called()

    def test_sdk_failure_preserves_the_deal_without_advice(self):
        client = Mock()
        client.system_one.side_effect = TypeSafeError("synthetic offline failure")
        result = jev_deal_read.read_deal(self.bundle(), client=client)
        self.assertFalse(result["judged"])
        self.assertEqual(result["id"], "synthetic-deal")

    def test_default_client_uses_the_vendor_model_and_no_key_file(self):
        client = Mock()
        client.system_one.side_effect = TypeSafeError("synthetic offline failure")
        with patch.object(jev_deal_read, "TypeSafeClient") as constructor:
            constructor.return_value.__enter__.return_value = client
            result = jev_deal_read.read_deal(self.bundle())
        self.assertFalse(result["judged"])
        constructor.assert_called_once_with(model="jev-latest")


if __name__ == "__main__":
    unittest.main()
