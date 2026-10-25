"""Offline request-mode regression tests for the Grok adapter."""

import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from grok_wire import run_request


class RequestModeTests(unittest.TestCase):
    def test_ordinary_prompt_passes_localhost_url_through(self):
        task = "Review this test URL: http://localhost:8787/x"

        def invoke(prompt, **kwargs):
            self.assertEqual(prompt, "\n\n" + task)
            events = [
                {"type": "text", "data": "Reviewed."},
                {"type": "end", "stopReason": "end_turn",
                 "requestId": "test-request", "sessionId": "test-session",
                 "modelUsage": {"grok-4.7-build": {"modelCalls": 1}}},
            ]
            return subprocess.CompletedProcess([], 0, "\n".join(map(json.dumps, events)), "")

        result = run_request(task, invoke=invoke)
        self.assertEqual(result["code"], 0)
        self.assertEqual(result["result"], "Reviewed.")

    def test_retrieval_rejects_localhost_url_before_invocation(self):
        def invoke(*args, **kwargs):
            self.fail("Invalid retrieval must not invoke Grok")

        with tempfile.TemporaryDirectory() as directory:
            result = run_request("Read http://localhost:8787/x", retrieval=True,
                                 invoke=invoke, receipt_path=Path(directory) / "receipt.json")
        self.assertEqual(result["code"], 6)
        self.assertEqual(result["detail"], "invalid_retrieval_url")


if __name__ == "__main__":
    unittest.main()
