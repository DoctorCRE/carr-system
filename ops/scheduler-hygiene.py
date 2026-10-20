#!/usr/bin/env python3
"""Read-only scheduler registration reconciliation."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib.scheduler_hygiene import main

if __name__ == '__main__':
    raise SystemExit(main())
