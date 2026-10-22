#!/usr/bin/env python3
"""Print the launchd template by default. --apply installs it from a main checkout."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import plistlib
import subprocess
import sys
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from lib.salesforce_browser_refresh import private_write, validate_url


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', required=True, type=validate_url)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    template = (ROOT / 'ops/templates/com.carr.salesforce-refresh.plist.in').read_text()
    template = template.replace('__PYTHON__', escape(sys.executable)).replace('__REPO__', escape(str(ROOT)))
    plistlib.loads(template.encode())
    if not args.apply:
        print(template, end='')
        return 0
    branch = subprocess.run(['git', 'branch', '--show-current'], cwd=ROOT, capture_output=True,
                            text=True, check=True).stdout.strip()
    config = json.loads((ROOT / 'ops/config/local-jobs.json').read_text())
    canonical = Path(config['repository_roots']['jbookout/carr-system']).expanduser().resolve()
    if sys.platform != 'darwin' or branch != 'main' or ROOT != canonical:
        parser.error('--apply requires macOS and the canonical main checkout')
    directory = Path.home() / '.config/carr/salesforce-refresh'
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    directory.chmod(0o700)
    private_write(directory / 'config.json', json.dumps({'url': args.url}))
    agents = Path.home() / 'Library/LaunchAgents'
    agents.mkdir(parents=True, exist_ok=True)
    target = agents / 'com.carr.salesforce-refresh.plist'
    private_write(target, template)
    subprocess.run(['/bin/launchctl', 'bootstrap', f'gui/{os.getuid()}', str(target)], check=True)
    print('Installed Salesforce browser refresh. Weekdays at 6:30 America/Chicago with wake-up catch-up.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
