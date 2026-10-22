#!/usr/bin/env python3
"""Run the dispatch acceptance suite at its safety-catalog entrypoint."""
from pathlib import Path
import runpy
runpy.run_path(str(Path(__file__).resolve().parents[1] / 'tools/room-bridge/test_dispatch_unit.py'), run_name='__main__')
