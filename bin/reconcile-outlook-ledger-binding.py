#!/usr/bin/env python3
"""Bind a pre-binding Outlook ledger using retained identity and a verified decoder snapshot."""
import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib.outlook_intake import main

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--decoder-vendor', type=Path,
                        help='separate current-source decoder build directory; preserves the installed decoder')
    parser.add_argument('--attest-mailbox', metavar='ACCOUNT',
                        help='operator attests the retained ledger belongs to this configured mailbox; decoder must verify it')
    args = parser.parse_args()
    raise SystemExit(main(reconcile_binding=True, reconcile_vendor=args.decoder_vendor,
                          attest_mailbox=args.attest_mailbox))
