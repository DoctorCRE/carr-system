"""User-local, authenticated registration evidence shared by mint and admission."""
from enum import Enum
import fcntl
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'ops'))
from git_env import scrubbed_env

CARD = re.compile(r'WR([1-9]\d*)\.S([1-9]\d*)\.J([1-9]\d*)\Z')
TTL = 24 * 60 * 60


def mint_card(args, reply):
    if not isinstance(args, dict) or not isinstance(reply, dict) or reply.get('ok') is not True:
        return None
    card = reply.get('card_id')
    match = CARD.fullmatch(card) if isinstance(card, str) else None
    work = re.fullmatch(r'WR-?0*([1-9]\d*)', str(args.get('work_request', '')))
    if not match or not work or match[1] != work[1]:
        return None
    if int(match[2]) != args.get('slice', 1) or int(match[3]) != args.get('job', int(match[3])):
        return None
    if not (type(reply.get('priority')) is int or isinstance(reply.get('runner_job_id'), str)):
        return None
    return card


def repository_name(value):
    if not isinstance(value, str):
        return None
    match = re.fullmatch(r'(?:https://github\.com/|ssh://git@github\.com/|git@github\.com:)?'
                         r'([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+?)(?:\.git)?/?', value)
    return match[1].lower() if match else None


def launch_repository(cwd=None):
    try:
        result = subprocess.run(['git', '-C', str(cwd or Path.cwd()), 'remote', 'get-url', 'origin'],
                                capture_output=True, text=True, timeout=0.5, env=scrubbed_env())
        return repository_name(result.stdout.strip()) if result.returncode == 0 else None
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


class MintClaim(Enum):
    FRESH = 'fresh'
    ONLINE_REQUIRED = 'online_required'
    INCOMPLETE = 'incomplete'


class RegistrationCache:
    def __init__(self, clock=None, repository=''):
        self.clock = clock or time.time
        self.directory = Path.home() / '.cache/carr/agent-card-cache-v1'
        self.scope = [os.getuid(), os.environ.get('CARR_MCP_URL') or 'https://api.doctorcre.com/mcp',
                      os.environ.get('CARR_MCP_CLIENT_PROFILE') or 'local',
                      os.environ.get('CARR_MCP_ENV') or str(Path.home() / '.config/carr/mcp-tokens.env')]
        self.scope.append('consume-before-lookup-v3')
        self.scope.append(launch_repository() if repository == '' else repository_name(repository))
        self.scope.append(self._credential_digest())

    def _credential_digest(self):
        try:
            try:
                with os.fdopen(self._open(self.scope[3], os.O_RDONLY), 'rb') as source:
                    if os.fstat(source.fileno()).st_mode & 0o777 != 0o600:
                        return None
                    raw = source.read()
            except FileNotFoundError:
                raw = b''
            overrides = {name: value for name, value in os.environ.items()
                         if name.startswith('CARR_') and 'TOKEN' in name}
            digest = hashlib.sha256(json.dumps(overrides, sort_keys=True).encode())
            digest.update(b'\0' + raw)
            return digest.hexdigest()
        except (OSError, ValueError):
            return None

    def _prepare_directory(self):
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        info = self.directory.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o777 != 0o700:
            raise ValueError('cache directory is not private or has been disabled')

    def _revoked(self, card):
        try:
            (self.directory / ('revoked-' + card)).lstat()
            return True
        except FileNotFoundError:
            return False
        except OSError:
            return True

    @staticmethod
    def _open(path, flags):
        fd = os.open(path, flags | os.O_NOFOLLOW, 0o600)
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            os.close(fd)
            raise ValueError('cache file is not private')
        return fd

    def _transaction(self, operation, *, lock_timeout=0.1):
        temporary = None
        try:
            if self.scope[-2] is None or self.scope[-1] is None or self._credential_digest() != self.scope[-1]:
                return None
            self._prepare_directory()
            with os.fdopen(self._open(self.directory / 'lock', os.O_RDWR | os.O_CREAT), 'r+') as lock:
                deadline = time.monotonic() + lock_timeout
                while True:
                    try:
                        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        break
                    except BlockingIOError:
                        if time.monotonic() >= deadline:
                            return None
                        time.sleep(0.005)
                key_path = self.directory / 'integrity'
                new_key = False
                try:
                    fd = self._open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
                except FileExistsError:
                    pass
                else:
                    new_key = True
                    with os.fdopen(fd, 'wb') as output:
                        output.write(os.urandom(32))
                        output.flush()
                        os.fsync(output.fileno())
                with os.fdopen(self._open(key_path, os.O_RDONLY), 'rb') as source:
                    key = source.read()
                if len(key) != 32:
                    return None
                path = self.directory / 'entries.json'
                try:
                    with os.fdopen(self._open(path, os.O_RDONLY)) as source:
                        entries = json.load(source)
                except FileNotFoundError:
                    if not new_key:
                        return None
                    entries = {}
                if not isinstance(entries, dict):
                    return None
                now = self.clock()
                valid = {}
                for card, entry in entries.items():
                    if not isinstance(entry, dict) or not isinstance(entry.get('body'), dict):
                        return None
                    body = entry['body']
                    signature = hmac.new(key, json.dumps(body, sort_keys=True).encode(), hashlib.sha256).hexdigest()
                    if (not CARD.fullmatch(card) or body.get('card_id') != card
                            or not isinstance(entry.get('signature'), str)
                            or not hmac.compare_digest(signature, entry['signature'])):
                        return None
                    valid[card] = body
                result = operation(valid, now)
                signed = {card: {'body': body, 'signature': hmac.new(
                    key, json.dumps(body, sort_keys=True).encode(), hashlib.sha256).hexdigest()}
                          for card, body in valid.items()}
                with tempfile.NamedTemporaryFile(mode='w', dir=self.directory, delete=False) as output:
                    temporary = Path(output.name)
                    json.dump(signed, output)
                    output.flush()
                    os.fsync(output.fileno())
                temporary.replace(path)
                directory_fd = os.open(self.directory, os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
                if self.directory.stat().st_mode & 0o777 != 0o700:
                    return None
                return result
        except (OSError, ValueError, TypeError):
            return None
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass

    def claim_mint(self, card):
        def consume(entries, now):
            entry = entries.get(card, {})
            seen = entry.get('seen')
            fresh = (entry.get('state') == 'fresh' and not self._revoked(card) and entry.get('scope') == self.scope
                     and type(seen) in (int, float) and 0 <= now - seen < TTL)
            entries[card] = {'card_id': card, 'state': 'retired'}
            return MintClaim.FRESH if fresh else MintClaim.ONLINE_REQUIRED

        return self._transaction(consume) or MintClaim.INCOMPLETE

    def record_mint(self, card):
        def record(entries, now):
            if not CARD.fullmatch(card) or card in entries or self._revoked(card):
                return False
            entries[card] = {'card_id': card, 'scope': self.scope, 'seen': now, 'state': 'fresh'}
            return True
        return bool(self._transaction(record, lock_timeout=2))
