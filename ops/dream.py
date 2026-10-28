#!/usr/bin/env python3
"""Clone, verify and apply a dream through the doctrine verbs.

The caller supplies selected sessions and edits. Scheduling and selection belong
in the nightly orchestrator. Each note maps a change unit to its input sessions.
"""
import hashlib
import importlib.util
import json
import tempfile
import re
import subprocess
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from lib.transcript_read import load_transcript
from ops.git_env import fixture_env

spec = importlib.util.spec_from_file_location('secret_scan', ROOT / 'ops/ci-secret-scan.py')
assert spec and spec.loader
secret_scan = importlib.util.module_from_spec(spec)
spec.loader.exec_module(secret_scan)
class VersionConflict(Exception):
    def __init__(self, section_key=None):
        self.section_key = section_key


KEY = re.compile(r'^[^/\\\x00-\x1f]+(?:/[^/\\\x00-\x1f]+)*$')


def condense(path):
    """Extract transcript messages in order without tool payloads or metadata."""
    result = []
    for record in load_transcript(path, hook='dream'):
        message = record.get('message', record.get('payload', {}))
        if not isinstance(message, dict):
            continue
        role = message.get('role', record.get('type'))
        if role not in ('user', 'assistant'):
            continue
        content = message.get('content', '')
        if isinstance(content, list):
            content = '\n'.join(part.get('text', '') for part in content if isinstance(part, dict) and part.get('type') in ('text', 'input_text', 'output_text'))
        if isinstance(content, str) and content.strip():
            result.append(f'{role}: {content.strip()}')
    return '\n\n'.join(result)


def _path(root, key):
    if (not isinstance(key, str) or not KEY.fullmatch(key)
            or any(part in ('.', '..', '.git', '_dream') for part in key.split('/'))):
        raise ValueError('invalid section key')
    return root / (key + '.md')


def _serialize(section):
    metadata = {k: section.get(k) for k in ('section_id', 'section_key', 'title', 'version', 'content_hash')}
    return '---\n' + json.dumps(metadata, sort_keys=True) + '\n---\n' + section['body']['text']


def _parse(text):
    parts = text.split('\n---\n', 1)
    if len(parts) != 2 or not parts[0].startswith('---\n'):
        raise ValueError('invalid front matter')
    return json.loads(parts[0][4:]), parts[1]


def _git(root, *args):
    return subprocess.run(['git', '-C', str(root), *args], check=True, capture_output=True, text=True, env=fixture_env()).stdout


def clone(store, root, key=None):
    root = Path(root)
    state = root.parent / ('.' + root.name + '-dream-state')
    state.mkdir(mode=0o700, parents=True, exist_ok=False)
    root.mkdir(parents=True, exist_ok=False)
    snapshot = store.read()
    paths = []
    folded = [section['section_key'].casefold() for section in snapshot['sections']]
    if len(folded) != len(set(folded)):
        if key is not None:
            store.log({'error': 'case_colliding_section_keys', 'notes': [], 'dropped': [], 'changed': 0}, key)
        raise ValueError('case_colliding_section_keys')
    for section in snapshot['sections']:
        path = _path(root, section['section_key'])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_serialize(section), encoding='utf-8')
        paths.append(str(path.relative_to(root)))
    _git(root, 'init', '-q')
    if paths:
        _git(root, 'add', '--', *paths)
    _git(root, '-c', 'user.name=Dream', '-c', 'user.email=dream@invalid', 'commit', '-qm', 'Clone memory', '--allow-empty')
    return root


def _write_state(path, value):
    with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent, delete=False) as handle:
        json.dump(value, handle)
        temporary = Path(handle.name)
    temporary.replace(path)


def apply(store, root, notes, input_sessions, attach, key, *, _attempt=0, _conflicts=frozenset()):
    root = Path(root)
    state = root.parent / ('.' + root.name + '-dream-state')
    receipt = state / 'applied.json'
    if receipt.exists():
        saved = json.loads(receipt.read_text(encoding='utf-8'))
        if saved['key'] != key:
            raise ValueError('clone already applied under another key')
        return saved['report']
    journal = state / 'pending.json'
    if journal.exists():
        pending = json.loads(journal.read_text(encoding='utf-8'))
        if pending['key'] != key or pending['attach'] != attach:
            raise ValueError('another dream is pending')
        return _finish(store, root, notes, input_sessions, attach, key, journal, receipt, pending, _attempt, _conflicts)
    if notes is None:
        notes = json.loads((root / '_dream' / 'notes.md').read_text(encoding='utf-8'))
    baseline = _git(root, 'ls-tree', '-r', '-z', '--name-only', 'HEAD').split('\0')
    baseline = [name for name in baseline if name]
    originals = {}
    for name in baseline:
        if not name.endswith('.md'):
            raise ValueError('invalid clone')
        meta, body = _parse(_git(root, 'show', f'HEAD:{name}'))
        if str(_path(root, meta['section_key']).relative_to(root)) != name:
            raise ValueError('invalid clone path')
        originals[meta['section_key']] = (meta, body)
    files = [p for p in root.rglob('*') if '.git' not in p.relative_to(root).parts and p.is_file()]
    if any(p.is_symlink() for p in root.rglob('*') if '.git' not in p.relative_to(root).parts):
        raise ValueError('symlinks are not allowed')
    output = {}
    for path in files:
        name = str(path.relative_to(root))
        if name.startswith('_dream/'):
            continue
        if not name.endswith('.md'):
            raise ValueError('invalid output file')
        meta, body = _parse(path.read_text(encoding='utf-8'))
        section_key = meta.get('section_key')
        if _path(root, section_key) != path:
            raise ValueError('invalid output path')
        if section_key in originals and meta != originals[section_key][0]:
            raise ValueError('changed front matter')
        if section_key not in originals and (meta.get('section_id') is not None or meta.get('version') != 0 or meta.get('content_hash') is not None):
            raise ValueError('invalid new section metadata')
        output[section_key] = (meta, body)
    current = {s['section_key']: s for s in store.read()['sections']}
    changed_keys = {k for k in set(originals) | set(output) if originals.get(k) != output.get(k)}
    if not isinstance(notes, list) or any(not isinstance(unit, dict) or not isinstance(unit.get('sections'), list) or not unit.get('note') or not unit.get('sessions') for unit in notes):
        raise ValueError('invalid change unit')
    notes = [{**unit, 'sections': [key for key in unit['sections'] if key in changed_keys]} for unit in notes]
    notes = [unit for unit in notes if unit['sections']]
    units = {}
    for unit in notes:
        if not isinstance(unit, dict) or not unit.get('note') or not unit.get('sessions') or not unit.get('sections'):
            raise ValueError('invalid change unit')
        if any(s not in input_sessions for s in unit['sessions']):
            raise ValueError('invalid change provenance')
        for section_key in unit['sections']:
            if section_key in units or section_key not in changed_keys:
                raise ValueError('invalid change unit sections')
            units[section_key] = unit
    if set(units) != changed_keys:
        raise ValueError('missing change provenance')
    moved = set()
    for section_key in changed_keys:
        old, live = originals.get(section_key), current.get(section_key)
        if section_key in _conflicts or (old and (not live or live['section_id'] != old[0]['section_id'] or live['version'] != old[0]['version'] or live['content_hash'] != old[0]['content_hash'])) or (not old and live):
            moved.update(units[section_key]['sections'])
    changes = []
    dropped = [unit for unit in notes if any(k in moved for k in unit['sections'])]
    applied = [unit for unit in notes if unit not in dropped]
    for section_key in sorted(set(originals) | set(output)):
        old = originals.get(section_key)
        new = output.get(section_key)
        if old == new:
            continue
        if section_key.startswith('conventions/proposed/'):
            raise ValueError('dream cannot change convention proposals')
        if section_key in moved:
            continue
        item = {'section_key': section_key, 'base_version': old[0]['version'] if old else 0, 'op': 'write' if new else 'retire'}
        if new:
            item['body_text'] = new[1]
            if new[0]['title'] is not None:
                item['title'] = new[0]['title']
        changes.append(item)
    # This is a soft bound; concurrent additions can occur after the read.
    if len(current) + sum(1 if i['section_key'] not in current else -1 if i['op'] == 'retire' else 0 for i in changes) > 300:
        raise ValueError('memory exceeds 300 sections')
    # Apply the scanner patterns directly: untrusted dream output cannot grant itself an exemption.
    for text in [str(value) for unit in notes for value in (unit['note'], *unit['sections'], *unit['sessions'])] + [item['body_text'] for item in changes if item['op'] == 'write']:
        if any(rx.search(text) for _, rx, _, _ in secret_scan.PATTERNS):
            raise ValueError('secret scan failed')
    for item in changes:
        if not store.gate(item, attach):
            raise ValueError('doctrine gate failed')
    report = {'notes': applied, 'dropped': dropped, 'changed': len(changes)}
    receipt.parent.mkdir(parents=True, exist_ok=True)
    _write_state(journal, {'key': key, 'attach': attach, 'changes': changes, 'report': report})
    return _finish(store, root, notes, input_sessions, attach, key, journal, receipt,
                   {'changes': changes, 'report': report}, _attempt, _conflicts)


def _finish(store, root, notes, input_sessions, attach, key, journal, receipt, pending, attempt, conflicts):
    if pending['changes']:
        try:
            store.apply(pending['changes'], attach, key)
        except VersionConflict as conflict:
            if attempt >= 2:
                raise
            journal.unlink()
            return apply(store, root, notes, input_sessions, attach, key, _attempt=attempt + 1,
                         _conflicts=conflicts | ({conflict.section_key} if conflict.section_key else set()))
    store.log(pending['report'], key)
    _write_state(receipt, {'key': key, 'report': pending['report']})
    journal.unlink()
    return pending['report']


class VerbStore:
    def __init__(self, call=None):
        self.transport = call or self._run

    @staticmethod
    def _run(verb, args):
        return subprocess.run([str(ROOT / 'run.sh'), 'call', verb, json.dumps(args)], cwd=ROOT, capture_output=True, text=True)

    def call(self, verb, args):
        process = self.transport(verb, args)
        if process.returncode:
            marker = 'TOOL ERROR '
            if marker in process.stderr:
                try:
                    payload, _ = json.JSONDecoder().raw_decode(process.stderr.split(marker, 1)[1])
                    if payload.get('error') == 'version_conflict':
                        raise VersionConflict(payload.get('section_key'))
                except (ValueError, TypeError):
                    pass
            raise ValueError(f'{verb} failed')
        try:
            result = json.loads(process.stdout[process.stdout.index('{'):])
        except (ValueError, KeyError):
            raise ValueError(f'{verb} failed') from None
        if result.get('error') == 'version_conflict':
            raise VersionConflict(result.get('section_key'))
        if not result.get('ok'):
            raise ValueError(f'{verb} refused')
        return result
    def read(self):
        return self.call('read-doctrine', {'document': 'team-memory'})
    def gate(self, item, attach):
        snapshot = self.read()
        section = next((s for s in snapshot['sections'] if s['section_key'] == item['section_key']), None)
        args = {'document': 'team-memory', 'op': item['op'], 'attach_id': attach}
        if section: args['section_id'] = section['section_id']
        if item['op'] == 'write': args['body_text'] = item['body_text']
        result = self.call('dry-run-doctrine-gates', args)
        return result.get('result') == 'pass'
    def apply(self, items, attach, key):
        self.call('change-doctrine-sections', {'document': 'team-memory', 'attach_id': attach, 'idempotency_key': str(uuid.uuid5(uuid.NAMESPACE_URL, key)), 'commit_message': 'Dream memory consolidation', 'items': items})
    def log(self, report, key):
        kind = 'error' if 'error' in report else 'success'
        section_key = 'dreams/' + hashlib.sha256((key + '-' + kind).encode()).hexdigest()
        self.call('write-doctrine-section', {'document': 'dreams-log', 'section_key': section_key, 'title': 'Dream report', 'body_text': json.dumps(report, sort_keys=True), 'base_version': 0, 'idempotency_key': str(uuid.uuid5(uuid.NAMESPACE_URL, key + '-' + kind + '-log'))})
