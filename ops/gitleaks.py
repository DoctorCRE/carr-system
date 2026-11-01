#!/usr/bin/env python3
"""Install checksum-pinned official Gitleaks or run detection with location-only output."""
import argparse
import hashlib
import io
import json
from pathlib import Path
import platform
import subprocess
import sys
import tarfile
import tempfile
import urllib.request

VERSION = '8.30.0'
SOURCE_SHA = '2ca41cc1372d1e939a6a879f18cdc19fc1cac1ce'
ARCHIVES = {
    'darwin_arm64': 'b251ab2bcd4cd8ba9e56ff37698c033ebf38582b477d21ebd86586d927cf87e7',
    'darwin_x64': 'ca221d012d247080c2f6f61f4b7a83bffa2453806b0c195c795bbe9a8c775ed5',
    'linux_x64': '79a3ab579b53f71efd634f3aaf7e04a0fa0cf206b7ed434638d1547a2470a66e',
    'linux_arm64': 'b4cbbb6ddf7d1b2a603088cd03a4e3f7ce48ee7fd449b51f7de6ee2906f5fa2f',
}


def binary(install=False):
    arch = {'aarch64': 'arm64', 'arm64': 'arm64', 'x86_64': 'x64', 'AMD64': 'x64'}.get(platform.machine())
    target = platform.system().lower() + '_' + str(arch)
    if target not in ARCHIVES:
        raise ValueError('Gitleaks platform unsupported')
    cache = Path.home() / '.cache/leak-guard' / VERSION / target
    archive = cache / 'release.tar.gz'
    executable = cache / 'gitleaks'
    if install and not archive.exists():
        data = urllib.request.urlopen(
            f'https://github.com/gitleaks/gitleaks/releases/download/v{VERSION}/gitleaks_{VERSION}_{target}.tar.gz',
            timeout=60).read()
        if hashlib.sha256(data).hexdigest() != ARCHIVES[target]:
            raise ValueError('Gitleaks release checksum mismatch')
        cache.mkdir(parents=True, exist_ok=True, mode=0o700)
        archive.write_bytes(data)
    if not archive.exists():
        raise ValueError('Gitleaks missing. Install: python3 ops/gitleaks.py --install')
    data = archive.read_bytes()
    if hashlib.sha256(data).hexdigest() != ARCHIVES[target]:
        raise ValueError('Gitleaks cached release checksum mismatch')
    with tarfile.open(fileobj=io.BytesIO(data), mode='r:gz') as bundle:
        expected = bundle.extractfile('gitleaks').read()
    if install:
        executable.write_bytes(expected)
        executable.chmod(0o700)
    if not executable.exists() or executable.read_bytes() != expected:
        raise ValueError('Gitleaks binary missing or modified. Install: python3 ops/gitleaks.py --install')
    return executable


def detect(root, *, rev_range=None, history=False, files=None, redactor=str):
    executable = binary()
    with tempfile.TemporaryDirectory(prefix='carr-gitleaks-') as directory:
        work = Path(directory)
        report = work / 'report.json'
        command = [str(executable)]
        if rev_range or history:
            command += ['git', str(root), '--log-opts=' + (rev_range or '--all')]
        else:
            content = work / 'files'
            content.mkdir()
            for name, text in files:
                path = content / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(text)
            command += ['dir', str(content)]
        command += ['--config', str(root / '.gitleaks.toml'), '--redact=100', '--no-banner',
                    '--no-color', '--ignore-gitleaks-allow', '--gitleaks-ignore-path', str(work),
                    '--report-format=json', '--report-path', str(report)]
        result = subprocess.run(command, cwd=root, capture_output=True)
        if result.returncode not in (0, 1) or not report.exists():
            raise ValueError('Gitleaks could not scan; no clean verdict')
        findings = json.loads(report.read_text()) or []
        if bool(findings) != (result.returncode == 1):
            raise ValueError('Gitleaks report disagrees with exit status')
        for finding in findings:
            name = finding['File']
            if not (rev_range or history):
                name = str(Path(name).relative_to(content)) if Path(name).is_absolute() else name
            # Never forward provider diagnostics, source lines, author identity or matches.
            print(redactor(f"{name}:{finding['StartLine']} {finding['RuleID']}"), file=sys.stderr)
        if not findings:
            print('secret scan: clean (Gitleaks ' + VERSION + ')')
        return result.returncode


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--install', action='store_true', required=True)
    parser.parse_args()
    try:
        binary(install=True)
        print('Installed verified Gitleaks ' + VERSION)
    except (OSError, ValueError) as error:
        print(str(error), file=sys.stderr)
        sys.exit(2)
