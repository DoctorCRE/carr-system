"""Evaluate caller-owned acceptance criteria against artifacts the verifier reads.

Criteria are supplied before execution, never learned from output. This module
reads artifacts; it never executes acceptance commands or invokes models. A worker's claim that a
check ran is not evidence, so there is no check predicate: a criterion of any
kind other than the artifact predicates abstains. Missing, unreadable or
contradictory evidence fails.
"""
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import time

MAX_BYTES = 96000
SHA = re.compile(r'[0-9a-f]{64}')
ARRAY_INDEX = re.compile(r'0|[1-9][0-9]*')
POINTER_ESCAPE = re.compile(r'~(?![01])')
CHUNK = 65536


def contract(instructions):
    """Extract the explicit acceptance_contract JSON object, including a fence.

Natural language is deliberately not compiled into a permission to complete.
"""
    try:
        if isinstance(instructions, dict):
            value = instructions
        else:
            text = str(instructions or '').strip()
            if text.startswith('```json\n') and text.endswith('```'):
                text = text[8:-3]
            value = json.loads(text)
        result = value.get('acceptance_contract')
        return result if isinstance(result, dict) else {}
    except (ValueError, TypeError, AttributeError):
        return {}


READ_TIMEOUT = 1.0
# Captured at import so no call-time filesystem lookup precedes the deadline.
_MODULE = os.path.abspath(__file__)
_READER_PROGRAM = """
import importlib.util, json, sys
spec = importlib.util.spec_from_file_location('acceptance_reader', sys.argv[1])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
json.dump(module._observe(json.loads(sys.argv[2])), sys.stdout)
"""


def _read_regular(path, limit):
    """Read in the disposable process; fstat checks the opened object itself."""
    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError('artifact is not a regular file')
        chunks, size = [], 0
        while size <= limit:
            chunk = os.read(fd, min(CHUNK, limit + 1 - size))
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
        return b''.join(chunks)
    finally:
        os.close(fd)


def _error(exc):
    return {'error': str(exc) or type(exc).__name__, 'os': isinstance(exc, OSError)}


def _observe(request):
    """Runs in the disposable process: every filesystem touch of a verification.

    Resolves root, each path to read and each path only to resolve (contained
    in root unless root is None), then reads each distinct resolved path.
    """
    paths, reads = {}, {}
    try:
        base = None if request['root'] is None else Path(request['root']).resolve()
        base_error = None
    except (OSError, ValueError) as exc:
        base, base_error = None, _error(exc)
    for value, read in [(v, True) for v in request['read']] + [(v, False) for v in request['resolve']]:
        if value not in paths:
            try:
                if base_error:
                    raise OSError(base_error['error'])
                paths[value] = {'path': value if request['root'] is None else str(_path(value, base))}
            except (OSError, ValueError) as exc:
                paths[value] = _error(exc)
        resolved = paths[value].get('path')
        if read and resolved is not None and resolved not in reads:
            try:
                reads[resolved] = {'b64': base64.b64encode(_read_regular(resolved, request['limit'])).decode()}
            except (OSError, ValueError) as exc:
                reads[resolved] = _error(exc)
    return {'paths': paths, 'reads': reads}


def _observe_bounded(request, deadline):
    """Bound resolution and open/read time as well as bytes, including from threads.

    A stalled filesystem operation cannot hold the verifier: subprocess.run
    kills and reaps the disposable process at the deadline. No signal handlers
    or lingering I/O threads are installed in the caller.
    """
    remaining = READ_TIMEOUT if deadline is None else min(READ_TIMEOUT, deadline - time.monotonic())
    if remaining <= 0:
        raise ValueError('artifact read deadline expired')
    try:
        result = subprocess.run([sys.executable, '-c', _READER_PROGRAM, _MODULE, json.dumps(request)],
                                capture_output=True, timeout=remaining)
    except subprocess.TimeoutExpired as exc:
        raise ValueError('artifact read deadline expired') from exc
    if result.returncode:
        raise ValueError('artifact reader failed')
    return json.loads(result.stdout)


def _raise(row):
    raise (OSError if row.get('os') else ValueError)(row['error'])


def read_regular(path, limit, *, deadline=None):
    """Read one regular file, path used as given, within the deadline."""
    path = str(path)
    row = _observe_bounded({'root': None, 'read': [path], 'resolve': [], 'limit': limit}, deadline)['reads'][path]
    if 'error' in row:
        _raise(row)
    return base64.b64decode(row['b64'])


def _path(value, root):
    path = Path(value)
    path = (path if path.is_absolute() else root / path).resolve()
    path.relative_to(root)
    return path


def _pointer(value, pointer):
    """RFC 6901: only '~0'/'~1' escapes; array tokens are canonical indexes."""
    if not isinstance(pointer, str) or (pointer and not pointer.startswith('/')):
        raise ValueError('invalid JSON pointer')
    if pointer == '':
        return value
    for part in pointer[1:].split('/'):
        if POINTER_ESCAPE.search(part):
            raise ValueError('invalid JSON pointer escape')
        key = part.replace('~1', '/').replace('~0', '~')
        if isinstance(value, list):
            if not ARRAY_INDEX.fullmatch(key) or int(key) >= len(value):
                raise ValueError('JSON pointer array index unresolved')
            value = value[int(key)]
        elif isinstance(value, dict):
            if key not in value:
                raise ValueError('JSON pointer member unresolved')
            value = value[key]
        else:
            raise ValueError('JSON pointer traverses a scalar')
    return value


def _json_equal(actual, expected):
    """Equality with JSON types at every depth: true is not 1, 1.0 is not 1."""
    if type(actual) is not type(expected):
        return False
    if isinstance(actual, dict):
        return actual.keys() == expected.keys() and all(_json_equal(actual[k], expected[k]) for k in actual)
    if isinstance(actual, list):
        return len(actual) == len(expected) and all(map(_json_equal, actual, expected))
    return actual == expected


def evaluate(criteria, *, root='.', receipts=None, deadline=None):
    """Return {status: passed|failed|needs_review, criteria: [{id,status,reason}]}.

Supported predicates: artifact (expected digest), contains (literal required
output) and json_equals (RFC 6901 pointer and expected JSON value). The
verifier reads each artifact itself. When receipts is a list, a worker named
its artifacts, and each criterion also needs exactly one matching receipt whose
digest equals the bytes read; with None the caller is the observer.
"""
    deadline = min(deadline, time.monotonic() + READ_TIMEOUT) if deadline is not None else time.monotonic() + READ_TIMEOUT
    if not isinstance(criteria, list) or not criteria:
        return {'status':'needs_review','criteria':[], 'reason':'explicit acceptance criteria required'}
    # Every filesystem touch happens in one bounded pass before any predicate.
    wanted = [c['path'] for c in criteria if isinstance(c, dict)
              and c.get('kind') in {'artifact','contains','json_equals'} and isinstance(c.get('path'), str)]
    named = [item.get('path') for item in receipts or [] if isinstance(item, dict)]
    observed, observe_error = {'paths':{}, 'reads':{}}, None
    if wanted:
        try:
            observed = _observe_bounded({'root':str(root), 'read':wanted, 'limit':MAX_BYTES,
                                         'resolve':[p for p in named if isinstance(p, str)]}, deadline)
        except ValueError as exc:
            observe_error = exc

    def resolved(value):
        if not isinstance(value, str):
            raise TypeError('artifact path must be a string')
        if observe_error:
            raise observe_error
        row = observed['paths'][value]
        if 'error' in row:
            _raise(row)
        return row['path']

    rows, seen = [], set()
    for index, criterion in enumerate(criteria):
        cid = criterion.get('id', str(index)) if isinstance(criterion, dict) else str(index)
        status, reason = 'needs_review', 'semantic or unknown criterion; needs review'
        try:
            if not isinstance(cid, str) or cid in seen:
                raise ValueError('duplicate or invalid criterion id')
            seen.add(cid)
            if not isinstance(criterion, dict):
                rows.append({'id':cid,'status':status,'reason':reason})
                continue
            kind = criterion.get('kind')
            if kind in {'artifact','contains','json_equals'}:
                path = resolved(criterion['path'])
                read = observed['reads'][path]
                if 'error' in read:
                    _raise(read)
                raw = base64.b64decode(read['b64'])
                if not raw or len(raw) > MAX_BYTES:
                    raise ValueError('artifact empty or over byte budget')
                digest = hashlib.sha256(raw).hexdigest()
                if receipts is not None:
                    matches = [item for item in receipts
                               if isinstance(item, dict) and resolved(item.get('path')) == path]
                    if len(matches) != 1 or not SHA.fullmatch(str(matches[0].get('sha256',''))):
                        raise ValueError('one digest-bound artifact receipt required')
                    if digest != matches[0]['sha256']:
                        raise ValueError('artifact digest mismatch')
                if criterion.get('sha256') and digest != criterion['sha256']:
                    raise ValueError('expected artifact digest mismatch')
                if kind == 'contains':
                    expected = criterion['text']
                    if not isinstance(expected,str) or not expected or expected not in raw.decode('utf-8'):
                        raise ValueError('required literal output missing')
                if kind == 'json_equals':
                    actual = _pointer(json.loads(raw), criterion['pointer'])
                    if not _json_equal(actual, criterion['value']):
                        raise ValueError('expected JSON value mismatch')
                if kind == 'artifact' and not criterion.get('sha256'):
                    status, reason = 'needs_review', 'artifact identity alone does not establish acceptance; expected digest required'
                else:
                    status, reason = 'passed', 'artifact read and predicate matched'
        except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
            status, reason = 'failed', str(exc)
        rows.append({'id':cid,'status':status,'reason':reason})
    status = ('failed' if any(r['status'] == 'failed' for r in rows) else
              'needs_review' if any(r['status'] == 'needs_review' for r in rows) else 'passed')
    return {'status':status,'criteria':rows}
