"""User-local, authenticated registration evidence shared by mint and admission."""
import fcntl
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import stat
import tempfile
import time

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


class RegistrationCache:
    def __init__(self, clock=None):
        self.clock = clock or time.time
        self.directory = Path.home() / '.cache/carr/agent-card-cache-v1'
        self.scope = [os.getuid(), os.environ.get('CARR_MCP_URL') or 'https://api.doctorcre.com/mcp',
                      os.environ.get('CARR_MCP_CLIENT_PROFILE') or 'joe-local',
                      os.environ.get('CARR_MCP_ENV') or str(Path.home() / '.config/carr/mcp-tokens.env')]

    @staticmethod
    def _open(path, flags):
        fd = os.open(path, flags | os.O_NOFOLLOW, 0o600)
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            os.close(fd)
            raise ValueError('cache file is not private')
        return fd

    def _transaction(self, operation):
        temporary = None
        try:
            self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            info = self.directory.lstat()
            if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
                return False
            with os.fdopen(self._open(self.directory / 'lock', os.O_RDWR | os.O_CREAT), 'r+') as lock:
                deadline = time.monotonic() + 0.1
                while True:
                    try:
                        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        break
                    except BlockingIOError:
                        if time.monotonic() >= deadline:
                            return False
                        time.sleep(0.005)
                key_path = self.directory / 'integrity'
                try:
                    fd = self._open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
                except FileExistsError:
                    pass
                else:
                    with os.fdopen(fd, 'wb') as output:
                        output.write(os.urandom(32))
                with os.fdopen(self._open(key_path, os.O_RDONLY), 'rb') as source:
                    key = source.read()
                if len(key) != 32:
                    return False
                path = self.directory / 'entries.json'
                try:
                    with os.fdopen(self._open(path, os.O_RDONLY)) as source:
                        entries = json.load(source)
                except (FileNotFoundError, ValueError):
                    entries = {}
                if not isinstance(entries, dict):
                    entries = {}
                now = self.clock()
                valid = {}
                for card, entry in entries.items():
                    if not isinstance(entry, dict):
                        continue
                    body = entry.get('body', {})
                    if not isinstance(body, dict):
                        continue
                    signature = hmac.new(key, json.dumps(body, sort_keys=True).encode(), hashlib.sha256).hexdigest()
                    seen = body.get('seen')
                    if (CARD.fullmatch(card) and body.get('card_id') == card and body.get('scope') == self.scope
                            and type(seen) in (int, float) and 0 <= now - seen < TTL
                            and type(body.get('minted')) is bool
                            and isinstance(entry.get('signature'), str)
                            and hmac.compare_digest(signature, entry['signature'])):
                        valid[card] = body
                result = operation(valid, now)
                signed = {card: {'body': body, 'signature': hmac.new(
                    key, json.dumps(body, sort_keys=True).encode(), hashlib.sha256).hexdigest()}
                          for card, body in valid.items()}
                with tempfile.NamedTemporaryFile(mode='w', dir=self.directory, delete=False) as output:
                    temporary = Path(output.name)
                    json.dump(signed, output)
                temporary.replace(path)
                return result
        except (OSError, ValueError, TypeError):
            return False
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def contains(self, card):
        return self._transaction(lambda entries, now: card in entries)

    def consume_mint(self, card):
        def consume(entries, now):
            if card not in entries or not entries[card]['minted']:
                return False
            entries[card]['minted'] = False
            return True
        return self._transaction(consume)

    def record(self, card, *, minted=False):
        def record(entries, now):
            if not CARD.fullmatch(card):
                return False
            entries[card] = {'card_id': card, 'scope': self.scope, 'seen': now, 'minted': minted}
            return True
        return self._transaction(record)

    def remove(self, card):
        return self._transaction(lambda entries, now: entries.pop(card, None))
