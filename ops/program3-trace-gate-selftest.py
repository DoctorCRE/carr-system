#!/usr/bin/env python3
"""Verify trace chronology across the repeated daylight-saving fallback hour."""
import sys
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib.loadpy import load_module_from_path

gate = load_module_from_path("trace_gate", str(Path(__file__).with_name("program3-trace-gate.py")))


class TraceOrderTest(unittest.TestCase):
    def test_chicago_fallback_uses_instant_order(self):
        chicago = ZoneInfo("America/Chicago")
        earlier = datetime(2026, 11, 1, 1, 52, tzinfo=chicago, fold=0)
        later = datetime(2026, 11, 1, 1, 11, tzinfo=chicago, fold=1)
        chain = [(None, None, None, earlier), (None, None, None, later)]
        self.assertTrue(gate.chain_in_time_order(chain))
        self.assertFalse(gate.chain_in_time_order(list(reversed(chain))))


if __name__ == "__main__":
    unittest.main()
