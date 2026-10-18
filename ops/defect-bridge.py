#!/usr/bin/env python3
"""ops/defect-bridge.py — one Work Request per unaddressed defect class (WR-000269).

    ops/defect-bridge.py               file (at most --max-filings, default 10)
    ops/defect-bridge.py --dry-run     read and print what it would file
    ops/defect-bridge.py --health-row  the `unaddressed defects` health row

Everything lives in lib/defect_bridge.py; this is its command line. Reads and
the one write go through `./run.sh call` (lib/record_call.py), never the
database directly. Exit 1 when a read or a filing failed, or (health row) when
any defect is unaddressed.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib.defect_bridge import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
