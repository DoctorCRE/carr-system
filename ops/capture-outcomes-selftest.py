#!/usr/bin/env python3
"""Run the capture outage and consent fixtures in the gates collection."""
from pathlib import Path
import runpy

runpy.run_path(str(Path(__file__).resolve().parents[1] / "tools/dictation-rig/tests/test_capture_outcomes.py"), run_name="__main__")
