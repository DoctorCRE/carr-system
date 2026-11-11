#!/usr/bin/env python3
"""Bind a pre-binding Outlook ledger using retained identity and a verified decoder snapshot."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib.outlook_intake import main

if __name__ == '__main__':
    raise SystemExit(main(reconcile_binding=True))
