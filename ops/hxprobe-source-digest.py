#!/usr/bin/env python3
"""hxprobe-source-digest.py — BUILD-SHA256 must describe the vendor/hxprobe tree it sits in.

Line 1 of vendor/hxprobe/BUILD-SHA256 is the macOS release binary's SHA-256, and
bin/install-outlook-intake.sh installs a decoder only when its local build
matches it. That hash embeds Joe's Homebrew Cargo paths, so no CI runner can
rebuild it. A decoder PR that forgot to refresh the receipt therefore merged
green and the Studio installer refused the upgrade (WR-000515).

The receipt also records a digest of the crate's source. This check recomputes
it on Linux, so a source change without a refreshed receipt fails CI. Refresh
both together after the macOS release build:

  python3 ops/hxprobe-source-digest.py --write

ops/hxprobe-source-digest-selftest.py runs the check in the CI gates class.

Every file in the crate counts except the receipt itself, documentation, and
Cargo's target directory, so a new build input is covered without a list edit.
"""
from __future__ import annotations

import argparse
import hashlib
import re
import sys
from pathlib import Path

CRATE = Path(__file__).resolve().parents[1] / 'vendor/hxprobe'
RECEIPT = 'BUILD-SHA256'
NOT_SOURCE = {RECEIPT, 'UPSTREAM.md', 'LICENSE', '.gitignore', '.DS_Store'}
LINE = re.compile(r'^Source digest: ([0-9a-f]{64})$', re.MULTILINE)


def digest(crate: Path) -> str:
    entries = []
    for path in sorted(crate.rglob('*')):
        relative = path.relative_to(crate)
        if not path.is_file() or relative.parts[0] == 'target' or path.name in NOT_SOURCE:
            continue
        entries.append(f'{relative.as_posix()}\0{hashlib.sha256(path.read_bytes()).hexdigest()}\n')
    return hashlib.sha256(''.join(entries).encode()).hexdigest()


def recorded(crate: Path) -> str | None:
    match = LINE.search((crate / RECEIPT).read_text())
    return match.group(1) if match else None


def write(crate: Path) -> None:
    receipt = crate / RECEIPT
    text, line = receipt.read_text(), f'Source digest: {digest(crate)}'
    first, _, rest = text.partition('\n')
    rest = LINE.sub(line, rest) if LINE.search(rest) else f'{line}\n{rest}'
    receipt.write_text(f'{first}\n{rest}')


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--crate', type=Path, default=CRATE)
    parser.add_argument('--write', action='store_true', help='record the current digest in the receipt')
    args = parser.parse_args(argv)
    if args.write:
        write(args.crate)
        print(f'hxprobe-source-digest: recorded {digest(args.crate)}')
        return 0
    have, want = recorded(args.crate), digest(args.crate)
    if have == want:
        print(f'hxprobe-source-digest: {RECEIPT} describes this source tree ({want[:12]})')
        return 0
    print(f'hxprobe-source-digest: {RECEIPT} records source {have or "(none)"} but the tree is {want}.\n'
          '  Rebuild on the Studio with `cargo build --release --locked`, put the binary SHA-256 on line 1,\n'
          '  then run `python3 ops/hxprobe-source-digest.py --write` and commit both.', file=sys.stderr)
    return 1


if __name__ == '__main__':
    sys.exit(main())
