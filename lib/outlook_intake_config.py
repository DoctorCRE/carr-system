"""Explicit per-machine Outlook intake identity, independent of credentials."""
from dataclasses import dataclass
import json
from pathlib import Path
import re

CONFIG = Path('.config/carr/outlook-intake.json')


@dataclass(frozen=True)
class IntakeIdentity:
    principal: str
    mailbox: str

    @classmethod
    def parse(cls, value: dict) -> 'IntakeIdentity':
        principal, mailbox = value.get('principal'), value.get('mailbox')
        if not isinstance(principal, str) or not re.fullmatch(r'[a-z][a-z0-9-]{0,63}', principal):
            raise ValueError('principal must be a CARR partner slug')
        if not isinstance(mailbox, str) or not re.fullmatch(r"[a-zA-Z0-9.!#$%&'*+/=?^_`{|}~-]+@carr\.us", mailbox):
            raise ValueError('mailbox must be the partner carr.us address')
        return cls(principal, mailbox.lower())


def read_identity(home: Path) -> IntakeIdentity | None:
    try:
        value = json.loads((home / CONFIG).read_text())
    except FileNotFoundError:
        return None
    if not isinstance(value, dict) or set(value) != {'principal', 'mailbox'}:
        raise ValueError('invalid Outlook intake configuration')
    return IntakeIdentity.parse(value)


def prepare_install(repo: Path, home: Path, argv: list[str], expected_pin: str | None = None) -> int:
    import argparse
    import os
    import plistlib
    import shutil
    import subprocess
    import sys
    from lib import machine_role
    from lib.launchd_scope import allowed_on_machine

    parser = argparse.ArgumentParser(description='Prepare the machine-local Outlook intake.')
    parser.add_argument('--principal')
    parser.add_argument('--mailbox')
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args(argv)
    checks = []
    identity = None
    try:
        if bool(args.principal) != bool(args.mailbox):
            raise ValueError('--principal and --mailbox are required together')
        identity = (IntakeIdentity.parse({'principal': args.principal, 'mailbox': args.mailbox})
                    if args.principal else read_identity(home))
        if machine_role.read_marker(str(home)) == 'partner' and identity is None:
            raise ValueError('partner installs require --principal and --mailbox')
        checks.append(('configuration', True, 'valid'))
    except (OSError, ValueError, TypeError) as error:
        checks.append(('configuration', False, str(error)))
    allowed = allowed_on_machine('com.carr.outlook-intake.plist',
                                 machine_role.is_primary(str(repo), home=str(home)), home=str(home))
    checks.append(('machine role', allowed, 'allowed' if allowed else 'machine role refuses Outlook intake'))
    if identity:
        checks.append(('mailbox account isolation', False,
                       'account_scope_unavailable: decoder has no verified mailbox ownership; nothing installed'))
    if args.check:
        info = Path('/Applications/Microsoft Outlook.app/Contents/Info.plist')
        try:
            version = plistlib.loads(info.read_bytes())['CFBundleShortVersionString']
        except (OSError, KeyError, ValueError):
            version = None
        checks.append(('Outlook version', bool(version), str(version or 'not installed')))
        try:
            tools = subprocess.run(['/usr/bin/xcode-select', '-p'], capture_output=True).returncode == 0
        except OSError:
            tools = False
        checks.append(('Command Line Tools', tools, 'available' if tools else 'missing'))
        decoder = home / 'carr-local/hxstore-reverse-engineering/hxprobe/target/release/hxprobe'
        try:
            pin = (home / 'carr-local/hxstore-reverse-engineering/.carr-pin').read_text().strip()
        except OSError:
            pin = None
        built = decoder.is_file() and os.access(decoder, os.X_OK) and pin == expected_pin and pin is not None
        rust = shutil.which('cargo') is not None
        checks.append(('Rust or prebuilt decoder', rust or built, 'available' if rust or built else 'missing'))
        checks.append(('repo checkout', (repo / '.git').exists() and (repo / 'run.sh').is_file(), str(repo)))
        for name, passed, detail in checks:
            print(f'{"PASS" if passed else "FAIL"} {name}: {detail}')
    else:
        for _, passed, detail in checks:
            if not passed:
                print(f'install-outlook-intake: {detail}', file=sys.stderr)
    return 0 if all(passed for _, passed, _ in checks) else 1
