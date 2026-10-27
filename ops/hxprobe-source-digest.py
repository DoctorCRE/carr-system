#!/usr/bin/env python3
"""Verify the decoder source receipt and install a tested, locked local build.

The source digest covers the crate, atlas, and atlas generator on every host.
BUILD-SHA256's first line records one reference build, whose binary hash can
vary by toolchain and Cargo home. Installation verifies the source digest,
runs locked release tests and build, then records the local artifact hash.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import shutil
import subprocess
import tempfile
from pathlib import Path

CRATE = Path(__file__).resolve().parents[1] / 'vendor/hxprobe'
RECEIPT = 'BUILD-SHA256'
NOT_SOURCE = {RECEIPT, 'UPSTREAM.md', 'LICENSE', '.gitignore', '.DS_Store'}
EXTERNAL_INPUTS = ('spec/outlook/hxstore-atlas.json', 'ops/hxstore-atlas.py')
LINE = re.compile(r'^Source digest: ([0-9a-f]{64})$', re.MULTILINE)


def digest(crate: Path) -> str:
    entries = []
    for path in sorted(crate.rglob('*')):
        relative = path.relative_to(crate)
        if not path.is_file() or relative.parts[0] == 'target' or path.name in NOT_SOURCE:
            continue
        entries.append(f'{relative.as_posix()}\0{hashlib.sha256(path.read_bytes()).hexdigest()}\n')
    root = crate.parent.parent
    for external_input in EXTERNAL_INPUTS:
        path = root / external_input
        entries.append(f'{external_input}\0{hashlib.sha256(path.read_bytes()).hexdigest()}\n')
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


def build(crate: Path, vendor: Path, source_digest: str) -> None:
    target = vendor / 'build'
    for command in ('test', 'build'):
        subprocess.run(['cargo', command, '--release', '--locked', '--quiet', '--target-dir', str(target)],
                       cwd=crate, check=True)
    built = target / 'release/hxprobe'
    receipt = {
        'source_digest': source_digest,
        'binary_sha256': hashlib.sha256(built.read_bytes()).hexdigest(),
        'cargo': subprocess.run(['cargo', '--version'], check=True, capture_output=True, text=True).stdout.strip(),
        'rustc': subprocess.run(['rustc', '-vV'], check=True, capture_output=True, text=True).stdout.strip(),
    }
    decoder = vendor / 'hxprobe/target/release/hxprobe'
    decoder.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=vendor) as temp:
        staged = Path(temp) / 'hxprobe'
        shutil.copyfile(built, staged)
        staged.chmod(0o700)
        local_receipt = Path(temp) / 'receipt.json'
        local_receipt.write_text(json.dumps(receipt, indent=2) + '\n')
        os.replace(staged, decoder)
        os.replace(local_receipt, vendor / '.carr-build.json')


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--crate', type=Path, default=CRATE)
    action = parser.add_mutually_exclusive_group()
    action.add_argument('--write', action='store_true', help='record the current digest in the receipt')
    action.add_argument('--build-into', type=Path, help='install a tested local build under this vendor directory')
    args = parser.parse_args(argv)
    if args.write:
        write(args.crate)
        print(f'hxprobe-source-digest: recorded {digest(args.crate)}')
        return 0
    have, want = recorded(args.crate), digest(args.crate)
    if have == want:
        if args.build_into:
            try:
                build(args.crate, args.build_into, want)
            except (OSError, subprocess.SubprocessError) as error:
                print(f'hxprobe-source-digest: decoder build or installation failed: {error}', file=sys.stderr)
                return 1
        print(f'hxprobe-source-digest: {RECEIPT} describes this source tree ({want[:12]})')
        return 0
    print(f'hxprobe-source-digest: {RECEIPT} records source {have or "(none)"} but the tree is {want}.\n'
          '  Refresh the reference build with `cargo build --release --locked` and its SHA-256 on line 1,\n'
          '  then run `python3 ops/hxprobe-source-digest.py --write` and commit both.', file=sys.stderr)
    return 1


if __name__ == '__main__':
    sys.exit(main())
