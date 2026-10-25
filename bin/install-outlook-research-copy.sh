#!/bin/zsh
# Build and pin the research-only copier, then render its on-demand LaunchAgent.
set -eu

if (( $# != 0 )); then
  print -u2 -- "install-outlook-research-copy: no arguments accepted"
  exit 1
fi

REPO="$(cd "$(dirname "$0")/.." && pwd)"

/usr/bin/python3 - "$REPO" "$HOME" <<'PY'
import hashlib
import json
import os
from pathlib import Path
import plistlib
import shlex
import subprocess
import sys
import tempfile

if len(sys.argv) != 3:
    raise SystemExit('install-outlook-research-copy: unexpected arguments')
repo, home = (Path(value).resolve() for value in sys.argv[1:])
source = repo / 'tools/outlook-research-copy.c'
helper = home / 'carr-local/bin/outlook-research-copy'
profile = home / 'Library/Group Containers/UBF8T346G9.Office/Outlook/Outlook 15 Profiles/Main Identity'
root = home / 'carr-local/outlook-research'
flags = ['-std=c11', '-O2', '-Wall', '-Wextra', '-Werror',
         f'-DOUTLOOK_PROFILE={json.dumps(str(profile))}',
         f'-DOUTLOOK_RESEARCH_ROOT={json.dumps(str(root))}']
wanted = hashlib.sha256(source.read_bytes() + json.dumps(flags).encode()).hexdigest()
pin = helper.with_suffix('.pin')
helper.parent.mkdir(parents=True, exist_ok=True)
current = None
if pin.is_file() and not pin.is_symlink():
    try:
        current = json.loads(pin.read_text())
    except (ValueError, UnicodeError):
        pass
if not (helper.is_file() and not helper.is_symlink() and os.access(helper, os.X_OK)
        and current == {'build_sha256': wanted, 'binary_sha256': hashlib.sha256(helper.read_bytes()).hexdigest()}):
    with tempfile.TemporaryDirectory(dir=helper.parent) as temp:
        built = Path(temp) / helper.name
        subprocess.run(['/usr/bin/cc', *flags, str(source), '-o', str(built)], check=True)
        built.chmod(0o700)
        digest = hashlib.sha256(built.read_bytes()).hexdigest()
        os.replace(built, helper)
        temporary_pin = Path(temp) / 'pin'
        temporary_pin.write_text(json.dumps({'build_sha256': wanted, 'binary_sha256': digest}) + '\n')
        temporary_pin.chmod(0o600)
        os.replace(temporary_pin, pin)

label = 'com.carr.outlook-research-copy'
plist = plistlib.loads((repo / f'ops/launchd/{label}.plist').read_bytes())
plist['ProgramArguments'] = [str(helper)]
for key in ('StandardOutPath', 'StandardErrorPath'):
    plist[key] = plist[key].replace('{{HOME}}', str(home))
rendered = repo / f'out/{label}.plist'
rendered.parent.mkdir(parents=True, exist_ok=True)
rendered.write_bytes(plistlib.dumps(plist))
rendered.chmod(0o644)
log = home / 'carr-local/outlook-research-copy.log'
fd = os.open(log, os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
os.fchmod(fd, 0o600)
os.close(fd)
destination = home / f'Library/LaunchAgents/{label}.plist'
print(f'Built and pinned {helper}.')
print('Grant Full Disk Access in System Settings > Privacy & Security > Full Disk Access > +.')
print(f'Add {helper}. Rebuilding the binary requires re-granting access.')
print('The job is not loaded. Run both load commands:')
print(f'  mkdir -p {shlex.quote(str(destination.parent))} && install -m 644 {shlex.quote(str(rendered))} {shlex.quote(str(destination))}')
print(f'  launchctl bootstrap gui/{os.getuid()} {shlex.quote(str(destination))}')
print(f'Take a snapshot with launchctl kickstart gui/{os.getuid()}/{label}.')
print(f'Snapshots land in {root}.')
PY
