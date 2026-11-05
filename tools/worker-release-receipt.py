#!/usr/bin/env python3
"""Seal a Worker lane receipt from the deploy job's verified release readback."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib.worker_release_receipt import seal


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--release', type=Path, required=True)
    parser.add_argument('--sha', required=True)
    parser.add_argument('--version', required=True)
    parser.add_argument('--verbs', type=int, required=True)
    parser.add_argument('--run-id', type=int, required=True)
    parser.add_argument('--attempt', type=int, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    receipt = seal(json.loads(args.release.read_text()), args.sha, args.version,
                   args.verbs, args.run_id, args.attempt)
    args.output.write_text(json.dumps(receipt, sort_keys=True) + '\n')


if __name__ == '__main__':
    main()
