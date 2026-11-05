#!/usr/bin/env python3
"""Read a canonical delivery plan for one full SHA in local origin/main history."""
import argparse
from pathlib import Path
import subprocess
import sys

from delivery_plan import ROOT, canonical, resolve


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT)
    parser.add_argument('--main-sha', required=True)
    args = parser.parse_args()
    try:
        sys.stdout.buffer.write(canonical(resolve(args.root, args.main_sha)) + b'\n')
        return 0
    except (ValueError, KeyError, TypeError, OSError, subprocess.SubprocessError):
        print('Delivery plan refused: source, classification, or requirement invalid', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
