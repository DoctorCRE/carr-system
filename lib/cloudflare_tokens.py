"""Cloudflare token verification and fleet transport. Health returns only names and states."""
from __future__ import annotations

import json
from pathlib import Path
import shlex
import subprocess
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener
from datetime import datetime, timedelta, timezone

from lib.credential_file import read_env_file
from lib.token_store import write_env_line

TOKEN_NAMES = ('CLOUDFLARE_API_TOKEN', 'CLOUDFLARE_SPEND_GUARD_TOKEN')
MACHINES = ('studio', 'macbook', 'dell')
VERIFY_URL = 'https://api.cloudflare.com/client/v4/user/tokens/verify'


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def verify(value, *, endpoint=VERIFY_URL):
    if not value:
        return 'missing'
    try:
        request = Request(endpoint, headers={'Authorization': 'Bearer ' + value,
                                             'User-Agent': 'carr-cloudflare-health', 'Accept': 'application/json'})
        with build_opener(NoRedirect).open(request, timeout=10) as response:
            body = json.loads(response.read(65536))
        result = body.get('result') or {}
        if body.get('success') is not True or result.get('status') != 'active':
            return 'failed'
        expiry = result.get('expires_on')
        if expiry:
            expires = datetime.fromisoformat(expiry.replace('Z', '+00:00'))
            if expires <= datetime.now(timezone.utc) + timedelta(days=3):
                return 'expiring_soon'
        return 'ok'
    except HTTPError as exc:
        exc.close()
        return 'failed' if exc.code in (401, 403) else 'unverifiable'
    except (OSError, URLError, ValueError, TypeError, AttributeError):
        return 'unverifiable'


def check_values(values, *, endpoint=VERIFY_URL):
    return {name: verify(values.get(name), endpoint=endpoint) for name in TOKEN_NAMES}


def check_file(path, *, endpoint=VERIFY_URL):
    try:
        if Path(path).stat().st_mode & 0o077:
            return dict.fromkeys(TOKEN_NAMES, 'insecure_permissions')
        values = read_env_file(path)
    except (OSError, UnicodeError):
        return dict.fromkeys(TOKEN_NAMES, 'unreadable')
    return check_values(values, endpoint=endpoint)


def install(path, name, value, *, endpoint=VERIFY_URL):
    if name not in TOKEN_NAMES or not isinstance(value, str) or not value or '\n' in value or '\r' in value:
        return 'invalid_input'
    if verify(value, endpoint=endpoint) != 'ok':
        return 'candidate_failed'
    path = Path(path)
    if path.is_symlink():
        return 'unsafe_file'
    write_env_line(path, name, value)
    return verify(read_env_file(path).get(name), endpoint=endpoint)


def remote_source(*, export=False):
    import lib.credential_file as parser
    import lib.token_store as writer
    program = ('import sys, types\n'
            'package = types.ModuleType("lib"); package.__path__ = []; sys.modules["lib"] = package\n'
            'parser = types.ModuleType("lib.credential_file"); parser.__file__ = "/dev/null"\n'
            'sys.modules["lib.credential_file"] = parser\n'
            + 'exec(' + repr(parser.SOURCE_BYTES.decode()) + ', parser.__dict__)\n'
            + 'writer = types.ModuleType("lib.token_store"); sys.modules["lib.token_store"] = writer\n'
            + 'exec(' + repr(Path(writer.__file__).read_text()) + ', writer.__dict__)\n'
            + 'exec(' + repr(Path(__file__).read_text()) + ', globals())\n'
            )
    if export:
        return program + ('values = read_env_file(Path.home() / ".config/carr/tokens.env")\n'
                          'selected = {name: values.get(name, "") for name in TOKEN_NAMES}\n'
                          'if any(state != "ok" for state in check_values(selected).values()): sys.exit(1)\n'
                          'print(json.dumps(selected))\n')
    return (program + 'payload = json.load(sys.stdin)\n'
            + 'path = Path.home() / ".config/carr/tokens.env"\n'
            + 'try:\n'
            + ' result = check_file(path) if payload["operation"] == "check" else {payload["name"]: install(path, payload["name"], payload["value"])}\n'
            + 'except Exception:\n result = dict.fromkeys(TOKEN_NAMES, "unverifiable")\n'
            + 'print(json.dumps(result))\n')


def on_machine(machine, payload, *, runner=subprocess.run):
    if machine not in MACHINES:
        raise ValueError('unknown machine')
    try:
        result = runner(['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=5', machine,
                         'python3 -c ' + shlex.quote(remote_source())],
                        input=json.dumps(payload), capture_output=True, text=True, timeout=40)
        if result.returncode:
            return dict.fromkeys(TOKEN_NAMES, 'unreachable')
        rows = json.loads(result.stdout)
        allowed = {'ok', 'failed', 'missing', 'unreadable', 'unverifiable', 'expiring_soon',
                   'candidate_failed', 'unsafe_file', 'invalid_input', 'insecure_permissions'}
        names = TOKEN_NAMES if payload['operation'] == 'check' else (payload['name'],)
        return {name: rows[name] if rows.get(name) in allowed else 'unverifiable' for name in names}
    except (OSError, subprocess.TimeoutExpired, ValueError, TypeError, AttributeError):
        return dict.fromkeys(TOKEN_NAMES, 'unreachable')


def health_rows(*, transport=on_machine):
    rows = []
    for machine in MACHINES:
        states = transport(machine, {'operation': 'check'})
        for name in TOKEN_NAMES:
            state = states.get(name, 'unverifiable')
            subject = machine + ':' + name
            line = (f'{"OK" if state == "ok" else "WARN"} Cloudflare {machine} {name} {state}'
                    f' · on breach: open/update one deduplicated loop cloudflare-token:{subject}'
                    ' · owner orchestrator · remediation sync from a machine where it verifies or rotate'
                    ' with python3 tools/cloudflare-tokens.py sync'
                    ' · verify rerun python3 tools/cloudflare-tokens.py check'
                    ' · auto-clear on successful verification of this machine and token')
            rows.append({'subject': subject, 'status': state, 'line': line})
    return rows
