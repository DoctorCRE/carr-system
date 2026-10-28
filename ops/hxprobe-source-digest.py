#!/usr/bin/env python3
"""Install a tested, locked local hxprobe build and keep it bound to its source.

The source digest covers the crate, atlas, and atlas generator. Nothing in the
tree records it: two decoder PRs never both edit one pin. The installer builds
only committed source, so the digest is computed here at install time and kept
with the binary hash and toolchain in the vendor directory's .carr-build.json.
A matching decoder is left in place. A decoder whose hash differs from the
receipt is removed before rebuilding; one built from other source is kept
until a fresh locked test and build of this source replaces it.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import shutil
import subprocess
import tempfile
from pathlib import Path

CRATE = Path(__file__).resolve().parents[1] / 'vendor/hxprobe'
RECEIPT = '.carr-build.json'
DECODER = 'hxprobe/target/release/hxprobe'
NOT_SOURCE = {'UPSTREAM.md', 'LICENSE', '.gitignore', '.DS_Store'}
EXTERNAL_INPUTS = ('spec/outlook/hxstore-atlas.json', 'ops/hxstore-atlas.py')


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def digest(crate: Path) -> str:
    entries = []
    for path in sorted(crate.rglob('*')):
        relative = path.relative_to(crate)
        if not path.is_file() or relative.parts[0] == 'target' or path.name in NOT_SOURCE:
            continue
        entries.append(f'{relative.as_posix()}\0{sha256(path)}\n')
    root = crate.parent.parent
    for external_input in EXTERNAL_INPUTS:
        entries.append(f'{external_input}\0{sha256(root / external_input)}\n')
    return hashlib.sha256(''.join(entries).encode()).hexdigest()


def receipt(vendor: Path) -> dict:
    try:
        recorded = json.loads((vendor / RECEIPT).read_text())
    except (OSError, ValueError):
        return {}
    return recorded if isinstance(recorded, dict) else {}


def build(crate: Path, vendor: Path, source_digest: str) -> None:
    target = vendor / 'build'
    for command in ('test', 'build'):
        subprocess.run(['cargo', command, '--release', '--locked', '--quiet', '--target-dir', str(target)],
                       cwd=crate, check=True)
    built = target / 'release/hxprobe'
    receipt = {
        'source_digest': source_digest,
        'binary_sha256': sha256(built),
        'cargo': subprocess.run(['cargo', '--version'], check=True, capture_output=True, text=True).stdout.strip(),
        'rustc': subprocess.run(['rustc', '-vV'], check=True, capture_output=True, text=True).stdout.strip(),
    }
    decoder = vendor / DECODER
    decoder.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=vendor) as temp:
        staged = Path(temp) / 'hxprobe'
        shutil.copyfile(built, staged)
        staged.chmod(0o700)
        local_receipt = Path(temp) / 'receipt.json'
        local_receipt.write_text(json.dumps(receipt, indent=2) + '\n')
        os.replace(staged, decoder)
        os.replace(local_receipt, vendor / RECEIPT)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--crate', type=Path, default=CRATE)
    parser.add_argument('--build-into', type=Path, required=True, help='vendor directory holding the installed decoder')
    args = parser.parse_args(argv)
    want, recorded = digest(args.crate), receipt(args.build_into)
    decoder = args.build_into / DECODER
    ours = decoder.is_file() and recorded.get('binary_sha256') == sha256(decoder)
    if ours and recorded.get('source_digest') == want:
        print(f'hxprobe-source-digest: installed decoder is the receipted build of this source ({want[:12]})')
        return 0
    if not ours and decoder.exists():
        # Never leave a decoder this installer did not build in place, even if the rebuild fails.
        print(f'hxprobe-source-digest: {decoder} does not match {RECEIPT}; removing it', file=sys.stderr)
        decoder.unlink()
    try:
        build(args.crate, args.build_into, want)
    except (OSError, subprocess.SubprocessError) as error:
        print(f'hxprobe-source-digest: decoder build or installation failed: {error}', file=sys.stderr)
        return 1
    print(f'hxprobe-source-digest: built and installed the decoder for this source ({want[:12]})')
    return 0


if __name__ == '__main__':
    sys.exit(main())
