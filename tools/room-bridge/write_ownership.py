"""Reserve repository write globs before an executor starts.

Machine IDs read from the OS are authoritative across hostname changes. Legacy
process records lacking an ID require a hostname observed by process_owner on
this machine, in a separate machine-bound journal. Supplied identities never
teach aliases; corrupt, ambiguous or unavailable observations fail closed.
Legacy provider-only records without host metadata retain their existing exact
thread/turn terminal readback policy, not permission to probe local processes.
"""
from __future__ import annotations

from contextlib import contextmanager
import fcntl
from fnmatch import fnmatchcase
from functools import lru_cache
import json
import os
from pathlib import Path
import re
import shlex
import sys
import socket
import ctypes
import pwd
import select
import signal
import subprocess
import time
from datetime import datetime, timezone

from desks import DeskError

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from lib.github_reader import GitHubReader
from ops.git_env import scrubbed_env

LEDGER = Path(pwd.getpwuid(os.getuid()).pw_dir) / '.config' / 'carr' / 'hermes-write-ownership.jsonl'


def _valid_machine_id(value) -> str | None:
    if isinstance(value, str) and re.fullmatch(
            r'(?:[0-9a-fA-F]{32}|[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12})', value):
        return value.lower()
    return None


@lru_cache(maxsize=1)
def machine_id() -> str | None:
    """Cache only the OS machine identity; a hostname is never its substitute."""
    try:
        if sys.platform == 'darwin':
            result = subprocess.run(['/usr/sbin/ioreg', '-r', '-d', '1', '-c',
                                     'IOPlatformExpertDevice'],
                                    capture_output=True, text=True, check=True, timeout=2)
            match = re.search(r'"IOPlatformUUID"\s*=\s*"([^"\n]+)"', result.stdout)
            return _valid_machine_id(match[1]) if match else None
        if sys.platform == 'linux':
            return _valid_machine_id(Path('/etc/machine-id').read_text().strip())
    except (OSError, subprocess.SubprocessError):
        pass
    return None


def _alias_path() -> Path:
    return Path(str(LEDGER) + '.host-aliases.jsonl')


def _aliases(path: Path) -> dict[str, set[str]]:
    aliases: dict[str, set[str]] = {}
    if path.exists():
        for line in path.read_text(encoding='utf-8').splitlines():
            row = json.loads(line)
            observed = (_valid_machine_id(row.get('machine_id'))
                        if isinstance(row, dict) else None)
            if (not isinstance(row, dict) or set(row) != {'host', 'machine_id'}
                    or not isinstance(row['host'], str) or not row['host']
                    or observed is None):
                raise ValueError('invalid hostname observation')
            aliases.setdefault(row['host'], set()).add(observed)
    return aliases


def _observe_host(host: str, current: str | None) -> None:
    if not current:
        return
    path = _alias_path()
    try:
        with _locked(path, timeout=0):
            if current not in _aliases(path).get(host, set()):
                _append(path, {'host': host, 'machine_id': current})
    except (OSError, ValueError, TypeError, DeskError):
        pass


def _local_identity(identity: dict) -> bool:
    current = _valid_machine_id(machine_id())
    if not current:
        return False
    if 'machine_id' in identity:
        return _valid_machine_id(identity['machine_id']) == current
    host = identity.get('host')
    if not isinstance(host, str) or not host:
        return False
    path = _alias_path()
    try:
        with _locked(path):
            return _aliases(path).get(host) == {current}
    except (OSError, ValueError, TypeError, DeskError):
        return False


def _same_process(identity: dict, current: dict) -> bool:
    return (_local_identity(identity) and type(identity.get('pid')) is int
            and identity['pid'] == current['pid'] and bool(identity.get('start_time'))
            and identity['start_time'] == current['start_time'])


def _executor_identity(identity: dict, current: dict) -> dict:
    if not current['machine_id'] or ('machine_id' in identity
            and _valid_machine_id(identity['machine_id']) != current['machine_id']):
        raise DeskError('claim_identity_conflict', 'executor machine identity is not local')
    if 'machine_id' not in identity and 'host' in identity and not _local_identity(identity):
        raise DeskError('claim_identity_conflict', 'executor hostname has no local observation')
    return {**identity, 'host': current['host'], 'machine_id': current['machine_id']}


def process_start(pid: int) -> str | None:
    """Read kernel start time; an unavailable identity never authorizes release."""
    try:
        if sys.platform == 'linux':
            fields = Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()
            boot = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
            return f'{boot}:{fields[19]}'
        if sys.platform == 'darwin':
            # PROC_PIDTBSDINFO: two uint64 start fields follow 120 bytes.
            buf = ctypes.create_string_buffer(136)
            lib = ctypes.CDLL('/usr/lib/libproc.dylib', use_errno=True)
            size = lib.proc_pidinfo(pid, 3, 0, buf, len(buf))
            if size == len(buf):
                import struct
                sec, usec = struct.unpack_from('=QQ', buf.raw, 120)
                return f'{sec}:{usec}'
    except (OSError, ValueError, IndexError):
        pass
    return None


def process_owner() -> dict:
    host, current = socket.gethostname(), _valid_machine_id(machine_id())
    _observe_host(host, current)
    return {'host': host, 'machine_id': current, 'pid': os.getpid(),
            'start_time': process_start(os.getpid())}


def process_terminated(identity: dict, *, group: bool = False) -> bool:
    pid = identity.get('pid')
    if (not _local_identity(identity) or type(pid) is not int or pid <= 0
            or not identity.get('start_time')):
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        dead = True
    except OSError:
        return False
    else:
        current = process_start(pid)
        dead = current is not None and current != identity['start_time']
    if not dead or not group:
        return dead
    pgid = identity.get('pgid')
    if type(pgid) is not int or pgid <= 0:
        return False
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return True
    except OSError:
        pass
    return False


def termination_evidence(row: dict) -> str | None:
    executor = row.get('executor') or {}
    owner = row.get('owner_process') or {}
    for identity in (owner, executor):
        if (('machine_id' in identity or 'host' in identity)
                and not _local_identity(identity)):
            return None
    if not row.get('launch_marker'):
        if executor.get('kind') not in ('reservation', 'unconfirmed'):
            return None
        if process_terminated(row.get('owner_process') or {}):
            return 'dispatcher terminated; no executor launch recorded'
        return None
    kind = executor.get('kind')
    if kind == 'no_launch' and executor.get('reason') in ('setup_pending', 'desktop_not_live', 'start_rejected'):
        return 'executor did not launch: ' + executor['reason']
    if row.get('gated_launch') and process_terminated(row.get('owner_process') or {}):
        if (kind == 'unconfirmed'
                and set(executor) <= {'kind', 'host', 'machine_id'}):
            return 'dispatcher terminated; no executor identity recorded behind launch gate'
        if kind == 'launch_gate' and process_terminated(executor, group=True):
            return 'dispatcher and unopened launch gate terminated; executor never authorized'
    if kind == 'process_group' and process_terminated(executor, group=True):
        return 'recorded executor identity terminated and process group absent'
    if kind == 'codex_turn' and executor.get('socket') and executor.get('turn_id'):
        import codex_wire
        if codex_wire.turn_terminated(executor['socket'], executor['thread_id'], executor['turn_id']):
            return 'Codex turn terminated (thread/read)'
    if kind == 'codex_desktop' and executor.get('thread_id') and executor.get('marker'):
        import codex_wire
        if codex_wire.desktop_turn_terminated(executor['thread_id'], executor['marker']):
            return 'Codex Desktop turn terminated (marked thread/read)'
    return None


def declaration(task: str, explicit: list[str] | None) -> list[str]:
    patterns = list(explicit or [])
    for line in task.splitlines():
        match = re.match(r'^\s*Writes:\s*(.*)$', line, re.I)
        if match:
            lexer = shlex.shlex(match[1].replace('`', ''), posix=True)
            lexer.whitespace += ','
            lexer.whitespace_split = True
            lexer.commenters = ''
            try:
                patterns.extend(lexer)
            except ValueError as exc:
                raise DeskError('bad_write_set', 'Writes: has invalid quoting') from exc
    result = []
    for pattern in patterns:
        if not isinstance(pattern, str):
            raise DeskError('bad_write_set', 'write globs must be strings')
        pattern = pattern.removeprefix('./')
        if (not pattern or pattern.startswith('/') or '..' in pattern.split('/')
                or any(part in ('', '.') for part in pattern.split('/'))
                or '\n' in pattern or '\r' in pattern):
            raise DeskError('bad_write_set', 'write globs must be nonempty repository-relative paths')
        if pattern not in result:
            result.append(pattern)
    return result


def own_pr(task: str, repo: str) -> int | None:
    match = re.search(r'^\s*(?:Own PR|PR|Pull request):\s*(\S+)', task, re.I | re.M)
    if not match:
        match = re.search(r'\b(?:continue (?:your )?own|own|update) PR\s*#?(\d+)\b', task, re.I)
    if not match:
        return None
    value = match[1].strip('`.,')
    url = re.fullmatch(r'https://github\.com/([^/]+/[^/]+)/pull/(\d+)', value)
    if url:
        if url[1].lower() != repo.lower():
            raise DeskError('bad_own_pr', 'the brief names a PR in a different repository')
        value = url[2]
    if not re.fullmatch(r'#?[1-9]\d*', value):
        raise DeskError('bad_own_pr', 'Own PR: must name a PR number or its GitHub URL')
    return int(value.lstrip('#'))


def open_prs(cwd: str) -> tuple[str, list[dict]]:
    env = scrubbed_env()
    env.pop('GH_REPO', None)
    reader = GitHubReader(cwd=cwd, env=env)
    try:
        repo = reader.json(['repo', 'view', '--json', 'nameWithOwner'])['nameWithOwner']
        if not isinstance(repo, str) or not re.fullmatch(r'[\w.-]+/[\w.-]+', repo):
            raise ValueError('invalid repository')
        prs = reader.api(f'repos/{repo}/pulls?state=open', paginate=True)
        owners = []
        for pr in prs:
            number = pr['number']
            if type(number) is not int or number <= 0:
                raise ValueError('invalid PR number')
            files = reader.api(f'repos/{repo}/pulls/{number}/files', paginate=True)
            paths = []
            for item in files:
                for key in ('filename', 'previous_filename'):
                    if key in item:
                        if not isinstance(item[key], str) or not item[key]:
                            raise ValueError('invalid PR file')
                        paths.append(item[key])
            # A draft labelled do_not_merge is parked, not being built: its
            # overlap is reported but does not refuse the job.
            held = pr.get('draft') is True and any(
                isinstance(label, dict) and label.get('name') == 'do_not_merge'
                for label in pr.get('labels') or [])
            owners.append({'number': number, 'title': pr['title'], 'files': paths, 'held': held})
        return repo, owners
    except (RuntimeError, OSError, ValueError, KeyError, TypeError) as exc:
        raise DeskError('ownership_unreadable', f'cannot verify open PR ownership: {exc}') from exc


def _tokens(pattern: str) -> list[str]:
    tokens: list[str] = []
    i = 0
    while i < len(pattern):
        end = i + 1
        if pattern[i] == '[':
            j = end
            if j < len(pattern) and pattern[j] == '!':
                j += 1
            if j < len(pattern) and pattern[j] == ']':
                j += 1
            close = pattern.find(']', j)
            if close >= 0:
                end = close + 1
        token = pattern[i:end]
        if token != '*' or not tokens or tokens[-1] != '*':
            tokens.append(token)
        i = end
    return tokens


def overlaps(left: str, right: str) -> bool:
    """Intersect shell-style fnmatch languages, including future filenames.

    A star can consume a character or advance without one. Literal/class
    transitions are intersected over character boundaries from both patterns;
    range endpoints and their neighbors represent every membership interval.
    """
    a, b = _tokens(left), _tokens(right)
    points = {0, 0x10ffff}
    for char in left + right:
        value = ord(char)
        points.update(x for x in (value - 1, value, value + 1) if 0 <= x <= 0x10ffff)
    alphabet = [chr(x) for x in points]
    seen = set()
    todo = [(0, 0)]
    while todo:
        i, j = todo.pop()
        if (i, j) in seen:
            continue
        seen.add((i, j))
        if i == len(a) and j == len(b):
            return True
        if i < len(a) and a[i] == '*':
            todo.append((i + 1, j))
        if j < len(b) and b[j] == '*':
            todo.append((i, j + 1))
        if i < len(a) and j < len(b) and any(
                fnmatchcase(char, a[i]) and fnmatchcase(char, b[j]) for char in alphabet):
            todo.append((i if a[i] == '*' else i + 1, j if b[j] == '*' else j + 1))
    return False


@contextmanager
def _locked(path: Path, *, timeout: float = 5):
    path.parent.mkdir(parents=True, exist_ok=True)
    with Path(str(path) + '.lock').open('a') as lock:
        deadline = time.monotonic() + timeout
        while True:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise DeskError('ownership_locked', 'ownership lock busy; retry reconcile')
                time.sleep(0.01)
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def _append(path: Path, row: dict) -> None:
    with path.open('a', encoding='utf-8') as fh:
        fh.write(json.dumps(row, separators=(',', ':')) + '\n')
        fh.flush()
        os.fsync(fh.fileno())


def check_results_path(path: Path) -> None:
    """Refuse a results path that aliases ownership authority; callers run this BEFORE reserving."""
    protected = (LEDGER, _alias_path())
    if path.resolve() in {target.resolve() for authority in protected
                          for target in (authority, Path(str(authority) + '.lock'))}:
        raise DeskError('bad_results_path', 'results cannot overwrite the ownership authority')


def record(path: Path, row: dict) -> None:
    """Result snapshots are never used to decide ownership."""
    check_results_path(path)
    with _locked(path):
        _append(path, row)


def _claims() -> dict[str, dict]:
    claims = {}
    try:
        if LEDGER.exists():
            for line in LEDGER.read_text(encoding='utf-8').splitlines():
                row = json.loads(line)
                if (not isinstance(row, dict) or not row.get('msg_id')
                        or row.get('ownership_state') not in ('held', 'released')
                        or not isinstance(row.get('repo'), str)
                        or not re.fullmatch(r'[\w.-]+/[\w.-]+', row['repo'])
                        or not isinstance(row.get('writes'), list) or not row['writes']
                        or any(not isinstance(p, str) for p in row['writes'])
                        or declaration('', row['writes']) != row['writes']):
                    raise ValueError('invalid claim')
                claims[row['msg_id']] = row
    except (OSError, ValueError, TypeError) as exc:
        raise DeskError('ownership_unreadable', 'cannot verify fixed ownership ledger') from exc
    return claims


def _persist(row: dict) -> dict:
    row = {**row, 'ownership_updated_at': datetime.now(timezone.utc).isoformat()}
    _append(LEDGER, row)
    return row


def _claim(msg_id: str) -> dict:
    row = _claims().get(msg_id)
    if not row:
        raise DeskError('claim_missing', 'claim not found in ownership authority')
    return row


def reserve(row: dict, cwd: str, writes: list[str]) -> dict:
    writes = declaration('', writes)
    # The PR scan makes one paced gh call per open PR, so it runs outside the
    # ledger lock. Any claim not already released when the scan began (held
    # then, or created during the scan) may publish its PR and release before
    # the scan ends, after the snapshot missed that PR; such a claim still
    # counts as an owner below, so the overlap is refused rather than missed.
    with _locked(LEDGER):
        released_before_scan = {k for k, v in _claims().items()
                                if v['ownership_state'] == 'released'}
    repo, prs = open_prs(cwd)
    mine = own_pr(row['task'], repo)
    with _locked(LEDGER):
        claims = _claims()
        if row['msg_id'] in claims:
            raise DeskError('claim_exists', 'a job cannot reserve twice')
        for pr in prs:
            if pr['number'] != mine and any(fnmatchcase(file, pattern)
                    for file in pr['files'] for pattern in writes):
                if pr.get('held'):
                    print(f"warning: write set overlaps held draft PR {pr['number']} "
                          f"({pr['title']}); it must rebase before it can merge", file=sys.stderr)
                    continue
                raise DeskError('write_set_overlap', f"write set owned by PR {pr['number']} "
                    f"({pr['title']}); build on top of PR {pr['number']}")
        for previous in claims.values():
            released = previous['msg_id'] in released_before_scan
            if released or previous.get('repo', '').lower() != repo.lower():
                continue
            if any(overlaps(a, b) for a in writes for b in previous['writes']):
                owner_pr = previous.get('own_pr')
                advice = f'build on top of PR {owner_pr}' if owner_pr else "build on top of the owner's PR once it is opened"
                raise DeskError('write_set_overlap', f"write set owned by in-flight job "
                    f"{previous['msg_id']} (desk {previous.get('desk', '?')}); "
                    f"stuck or running: claim held; run dispatch.py reconcile --claim {previous['msg_id']}; {advice}")
        current = process_owner()
        return _persist({**row, 'repo': repo, 'writes': writes, 'own_pr': mine,
                         'claimed_at': datetime.now(timezone.utc).isoformat(),
                         'owner_process': current,
                         'executor': _executor_identity({'kind': 'reservation'}, current),
                         'ownership_state': 'held', 'status': 'running'})


def launch(msg_id: str, request: dict | None = None, *, on_bound=None) -> dict:
    """Mark handoff, then bind a gated child before any adapter can launch work."""
    with _locked(LEDGER):
        row = _claim(msg_id)
        current = process_owner()
        if (row['ownership_state'] != 'held' or row.get('launch_marker')
                or not _same_process(row['owner_process'], current)):
            raise DeskError('claim_launch_conflict', 'claim is not awaiting its first launch')
        row = _persist({**row, 'launch_marker': datetime.now(timezone.utc).isoformat(),
                        'gated_launch': True,
                        'executor': _executor_identity({'kind': 'unconfirmed'}, current)})
    if request is None:
        return row
    return _run_gated(msg_id, request, on_bound)


def _run_gated(msg_id: str, request: dict, on_bound=None) -> dict:
    receipt_read, receipt_write = os.pipe()
    gate_read, gate_write = os.pipe()
    proc = None
    try:
        proc = subprocess.Popen([sys.executable, str(Path(__file__).with_name('ownership_executor.py')),
            str(receipt_write), str(gate_read), '--dispatch'], stdin=subprocess.PIPE,
            start_new_session=True, pass_fds=(receipt_write, gate_read))
    finally:
        os.close(receipt_write)
        os.close(gate_read)
        if proc is None:
            os.close(receipt_read)
            os.close(gate_write)
    try:
        assert proc.stdin is not None
        proc.stdin.write(json.dumps(request).encode())
        proc.stdin.close()
        if not select.select([receipt_read], [], [], 5)[0]:
            raise RuntimeError('executor identity handshake timed out')
        with os.fdopen(receipt_read) as receipt:
            receipt_read = -1
            outcome = None
            initial = True
            for line in receipt:
                message = json.loads(line)
                if message['type'] == 'executor':
                    identity = message['identity']
                    if initial:
                        if (identity.get('kind') != 'launch_gate' or identity.get('pid') != proc.pid
                                or identity.get('pgid') != proc.pid
                                or not _local_identity(identity)
                                or not identity.get('start_time')
                                or identity.get('start_time') != process_start(proc.pid)):
                            raise RuntimeError('executor kernel identity unavailable')
                        initial = False
                    bound = bind_executor(msg_id, identity)
                    if on_bound:
                        on_bound(bound)
                    os.write(gate_write, b'1')
                elif message['type'] == 'result':
                    outcome = message['outcome']
                elif message['type'] == 'error':
                    if message.get('code'):
                        raise DeskError(message['code'], message['detail'])
                    raise RuntimeError(message['detail'])
                else:
                    raise RuntimeError('invalid executor receipt')
        proc.wait(timeout=2)
        if outcome is None or proc.returncode:
            raise RuntimeError('gated executor exited without a result')
        return outcome
    except BaseException:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except OSError:
            pass
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            pass
        raise
    finally:
        if receipt_read >= 0:
            os.close(receipt_read)
        os.close(gate_write)
        if proc.stdin is not None:
            proc.stdin.close()


def bind_executor(msg_id: str, identity: dict) -> dict:
    with _locked(LEDGER):
        row = _claim(msg_id)
        current = process_owner()
        if (row['ownership_state'] != 'held' or not row.get('launch_marker')
                or not _same_process(row['owner_process'], current)):
            raise DeskError('claim_launch_conflict', 'executor needs a held launch claim')
        identity = _executor_identity(identity, current)
        old = row['executor']
        if (('machine_id' in old or 'host' in old) and not _local_identity(old)):
            raise DeskError('claim_identity_conflict', 'recorded executor identity is not local')
        if old.get('machine_id') == identity['machine_id']:
            old = {**old, 'host': identity['host']}
        gate_handoff = (old.get('kind') == 'launch_gate' and identity == {**old, 'kind': 'unconfirmed'})
        no_launch_transition = (identity.get('kind') == 'no_launch' and (
            (old.get('kind') == 'codex_desktop'
             and identity == {**old, 'kind': 'no_launch', 'reason': 'desktop_not_live'})
            or (old.get('kind') == 'codex_turn' and old.get('turn_id') is None
                and identity == {**old, 'kind': 'no_launch', 'reason': 'start_rejected'})))
        if (not gate_handoff and not no_launch_transition
                and old.get('kind') not in ('unconfirmed', 'no_launch')
                and any(v is not None and identity.get(k) != v for k, v in old.items())):
            raise DeskError('claim_identity_conflict', 'cannot replace an executor identity')
        return _persist({**row, 'executor': dict(identity)})


def release(msg_id: str, *, reason: str) -> dict:
    """The ONLY release transition: reread identity and prove termination under lock."""
    with _locked(LEDGER):
        row = _claim(msg_id)
        if row['ownership_state'] == 'released':
            return row
        evidence = termination_evidence(row)
        if evidence:
            return _persist({**row, 'ownership_state': 'released',
                             'ownership_detail': evidence, 'release_reason': reason})
        return _persist({**row, 'status': 'stuck',
                         'ownership_detail': 'stuck: executor termination unconfirmed',
                         'reconcile_reason': reason})


def held_since(row: dict) -> datetime | None:
    """When the claim was taken: claimed_at (reserve, 2026-10-12 on), else the
    launch marker; None when neither parses as a timezone-aware time."""
    for key in ('claimed_at', 'launch_marker'):
        try:
            at = datetime.fromisoformat(str(row.get(key)))
        except ValueError:
            continue
        if at.tzinfo is not None:
            return at
    return None


def _due(row: dict, older_than_hours: float, now: datetime) -> bool:
    """A held claim an unattended sweep may examine: its writer is verifiably
    gone, or the claim is older than the bound. A young claim whose writer is
    alive or unproven is a running job and is left alone (release() would mark
    it stuck)."""
    if termination_evidence(row):
        return True
    at = held_since(row)
    return at is not None and (now - at).total_seconds() > older_than_hours * 3600


def reconcile(msg_id: str | None = None, *, limit: int = 20,
              older_than_hours: float | None = None) -> list[dict]:
    """Bounded, idempotent, append-only recovery; never accepts caller-supplied proof.

    `older_than_hours` (sweeps without msg_id only) restricts the sweep to
    claims _due() says may be examined; without it every held claim is."""
    if not 1 <= limit <= 100:
        raise DeskError('bad_reconcile_limit', 'reconcile limit must be 1..100')
    if older_than_hours is not None and (msg_id or not older_than_hours > 0):
        raise DeskError('bad_reconcile_filter', 'older_than_hours needs a sweep and a positive bound')
    now = datetime.now(timezone.utc)
    with _locked(LEDGER):
        claims = _claims()
        if msg_id and msg_id not in claims:
            raise DeskError('claim_missing', 'claim not found in ownership authority')
        ids = [msg_id] if msg_id else [key for key, row in claims.items()
                                      if row['ownership_state'] == 'held'
                                      and (older_than_hours is None
                                           or _due(row, older_than_hours, now))][:limit]
    return [release(key, reason='explicit reconcile') for key in ids]
