"""The private per-machine Outlook configuration. Credential values stay in their own files."""
from __future__ import annotations

from dataclasses import dataclass, fields
import json
import os
import plistlib
from pathlib import Path
import re
import stat
import subprocess
import sys
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


class SettingsError(ValueError):
    pass


@dataclass(frozen=True)
class OutlookSettings:
    owner: str
    account: str
    internal_domain: str
    timezone: ZoneInfo
    repo: Path
    profile: Path
    vendor: Path
    copy_helper: Path
    driver: Path
    folder_guide: Path
    send_state: Path
    mcp_credentials: Path
    database_credentials: Path
    executable_path: str
    deal_prefix: str
    vendor_prefix: str
    services: tuple[str, ...]
    full_screen_height: int
    source: Path
    model: str = 'haiku'
    partner_cc: str = ''
    signing_identity: str = ''
    folder_account_id: int | None = None
    attachment_root: Path | None = None
    attachment_max_bytes: int = 0

    @property
    def decoder(self) -> Path:
        return self.vendor / 'hxprobe/target/release/hxprobe'

    def transport_environment(self) -> dict[str, str]:
        env = {key: value for key, value in os.environ.items()
               if key not in ('CARR_MCP_LOCAL_TOKEN', 'CARR_BREAK_GLASS', 'CARR_BREAK_GLASS_REASON')}
        return {**env, 'CARR_MCP_ENV': str(self.mcp_credentials), 'CARR_MCP_CLIENT_PROFILE': 'local'}

    @property
    def plist_replacements(self) -> dict[str, str]:
        return {'{{REPO}}': str(self.repo), '{{HOME}}': str(self.source.parents[2]),
                        '{{COPY_HELPER}}': str(self.copy_helper), '{{EXECUTABLE_PATH}}': self.executable_path}

    def render_plist(self, source: Path | bytes) -> bytes:
        def replace(value):
            if isinstance(value, dict):
                return {key: replace(item) for key, item in value.items()}
            if isinstance(value, list):
                return [replace(item) for item in value]
            if isinstance(value, str):
                for key, item in self.plist_replacements.items():
                    value = value.replace(key, item)
            return value

        plist = plistlib.loads(source if isinstance(source, bytes) else source.read_bytes())
        return plistlib.dumps(replace(plist), sort_keys=False)

    def require_repo(self, repo: Path) -> None:
        if repo.resolve() != self.repo.resolve():
            raise SettingsError(f'{self.source}: field repo: command checkout differs from configured checkout')

    def require_service(self, service: str) -> None:
        if service not in self.services:
            raise SettingsError(f'{self.source}: field services: {service} is not enabled')

    def verify_helper(self) -> None:
        try:
            result = subprocess.run([str(self.copy_helper), '--scope'], capture_output=True, text=True, timeout=10)
        except (OSError, subprocess.SubprocessError):
            raise SettingsError(f'{self.source}: field copy_helper: cannot read compiled scope; run the intake installer') from None
        if result.returncode or result.stdout.splitlines() != [str(self.profile), str((self.repo / 'out/outlook-intake').resolve()), str(self.repo)]:
            raise SettingsError(f'{self.source}: field copy_helper: compiled profile/repo scope differs; run the intake installer')


_PATHS = frozenset({'repo', 'profile', 'vendor', 'copy_helper', 'folder_guide', 'send_state',
                    'mcp_credentials', 'database_credentials', 'driver'})


def load_settings(home: Path | None = None) -> OutlookSettings:
    path = (home or Path.home()) / '.config/carr/outlook.json'

    def fail(field: str, reason: str):
        raise SettingsError(f'{path}: field {field}: {reason}')

    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        fail('file', 'an owned regular settings file is required')
    with os.fdopen(fd) as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
            fail('file', 'must be a regular file owned by the current user')
        if stat.S_IMODE(info.st_mode) != 0o600:
            fail('mode', 'must be 0600')
        try:
            def unique(pairs):
                obj = {}
                for key, value in pairs:
                    if key in obj:
                        fail(key, 'duplicate field')
                    obj[key] = value
                return obj
            values = json.load(handle, object_pairs_hook=unique)
        except (json.JSONDecodeError, UnicodeError):
            fail('json', 'invalid JSON')
    if not isinstance(values, dict):
        fail('json', 'must be an object')
    expected = {field.name for field in fields(OutlookSettings)} - {'source'}
    if 'model' in values and (not isinstance(values['model'], str)
                              or not re.fullmatch(r'[a-z0-9][a-z0-9.-]{0,63}', values['model'])):
        fail('model', 'must be a model name such as haiku')
    if 'signing_identity' in values:
        identity = values['signing_identity']
        if (not isinstance(identity, str) or not identity.strip() or identity == '-'
                or identity != identity.strip() or any(ord(c) < 32 for c in identity)):
            fail('signing_identity', 'must name a code-signing identity without control characters or surrounding spaces')
    if 'folder_account_id' in values and (type(values['folder_account_id']) is not int
                                           or not 0 < values['folder_account_id'] < 2**63):
        fail('folder_account_id', 'must be a positive signed 64-bit store account key')
    if 'attachment_root' in values:
        root = values['attachment_root']
        if (not isinstance(root, str) or not Path(root).is_absolute() or '..' in Path(root).parts
                or str(Path(root)) != root or any(ord(c) < 32 for c in root)):
            fail('attachment_root', 'must be a normalized absolute path')
        values['attachment_root'] = Path(root)
    if 'attachment_max_bytes' in values and (type(values['attachment_max_bytes']) is not int
                                             or values['attachment_max_bytes'] <= 0):
        fail('attachment_max_bytes', 'must be a positive integer')
    for name in sorted(expected - {'model', 'partner_cc', 'signing_identity', 'folder_account_id', 'attachment_root',
                                   'attachment_max_bytes'}):
        if name not in values:
            fail(name, 'required')
        value = values[name]
        if name == 'full_screen_height':
            if type(value) is not int or value <= 0:
                fail(name, 'must be a positive integer in screen points')
            continue
        if name == 'services':
            if not isinstance(value, list) or not value or any(v not in ('intake', 'refresh', 'triage', 'outbox') for v in value):
                fail(name, 'must list enabled intake, refresh, triage, or outbox services')
            values[name] = tuple(value)
            continue
        if not isinstance(value, str) or not value.strip() or value != value.strip() or any(ord(c) < 32 for c in value):
            fail(name, 'must be nonempty text without control characters or surrounding spaces')
        if name in _PATHS:
            p = Path(value)
            if not p.is_absolute() or '..' in p.parts or str(p) != value:
                fail(name, 'must be a normalized absolute path')
            values[name] = p.resolve() if name == 'repo' else p
    partner_cc = values.get('partner_cc', '')
    if (not isinstance(partner_cc, str) or (partner_cc or 'outbox' in values['services']) and (
            not re.fullmatch(r"[\w.+'-]+@[\w-]+(?:\.[\w-]+)+", partner_cc)
            or partner_cc != partner_cc.lower() or partner_cc == values['account'])):
        fail('partner_cc', 'outbox requires a lowercase partner email different from account')
    for unknown in values.keys() - expected:
        fail(unknown, 'unknown field; credential values do not belong in settings')
    if not re.fullmatch(r"[\w.+'-]+@[\w-]+(?:\.[\w-]+)+", values['account']):
        fail('account', 'must be one email address')
    if not re.fullmatch(r'[a-z0-9-]+(?:\.[a-z0-9-]+)+', values['internal_domain']):
        fail('internal_domain', 'must be a lowercase domain without @')
    if not re.fullmatch(r'[a-z0-9][a-z0-9_-]*', values['owner']):
        fail('owner', 'must be an owner principal')
    if values['account'] != values['account'].lower():
        fail('account', 'must be lowercase')
    try:
        values['timezone'] = ZoneInfo(values['timezone'])
    except (ZoneInfoNotFoundError, ValueError):
        fail('timezone', 'must be an IANA timezone')
    if any(not part.startswith('/') for part in values['executable_path'].split(':')):
        fail('executable_path', 'must contain only absolute directories separated by colons')
    for name in ('deal_prefix', 'vendor_prefix'):
        if not values[name].endswith('/'):
            fail(name, 'must end with /')
    for name in ('mcp_credentials', 'database_credentials', 'folder_guide'):
        values[name] = values[name].resolve()
        if values[name] == path.resolve() or values[name] == values['repo'] or values['repo'] in values[name].parents:
            fail(name, 'must point outside the repository and settings file')
    if values['repo'] in path.resolve().parents:
        fail('repo', 'settings must live outside the repository')
    return OutlookSettings(**values, source=path)


def main() -> int:
    try:
        command = sys.argv[1:]
        if command == ['write']:
            path = Path.home() / '.config/carr/outlook.json'
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            try:
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            except OSError:
                raise SettingsError(f'{path}: field file: cannot create settings; an existing file is never overwritten') from None
            try:
                with os.fdopen(fd, 'w') as handle:
                    os.fchmod(handle.fileno(), 0o600)
                    handle.write(sys.stdin.read())
                load_settings()
            except (SettingsError, OSError):
                path.unlink()
                raise
            print(f'outlook settings written and validated: {path}')
            return 0
        settings = load_settings()
        if command == ['validate']:
            print(f'outlook settings valid: {settings.source}')
        elif len(command) == 2 and command[0] == 'field' and command[1] in _PATHS | {'executable_path'}:
            print(getattr(settings, command[1]))
        elif len(command) == 2 and command[0] == 'render':
            settings.require_repo(Path.cwd())
            sys.stdout.buffer.write(settings.render_plist(Path(command[1])))
        elif len(command) == 2 and command[0] == 'service':
            settings.require_service(command[1])
            settings.require_repo(Path.cwd())
        else:
            raise SettingsError('usage: python -m lib.outlook_settings write | validate | field NAME | service NAME | render SOURCE')
        return 0
    except SettingsError as error:
        print(error, file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
