#!/usr/bin/env python3
"""Collect one local scheduler observation receipt without changing schedules."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib.scheduler_observation_receipts import main

if __name__ == '__main__':
    raise SystemExit(main())
