#!/usr/bin/env python3
"""Clone, verify and apply a dream through the doctrine verbs.

The caller supplies selected sessions and proposals. Scheduling and selection belong
in the nightly orchestrator. Each note maps a change unit to its input sessions.
"""
import hashlib
import importlib.util
import json
import os
import stat
import tempfile
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from lib.transcript_read import load_transcript
from ops.git_env import fixture_env

spec = importlib.util.spec_from_file_location('secret_scan', ROOT / 'ops/ci-secret-scan.py')
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


def clone(store, root):
    root = Path(root)
    state = root.parent / ('.' + root.name + '-dream-state')
    state.mkdir(mode=0o700, parents=True, exist_ok=False)
    root.mkdir(parents=True, exist_ok=False)
    snapshot = store.read()
    paths = []
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
    try:
        state_info = state.lstat()
    except FileNotFoundError:
        raise ValueError('missing private dream state') from None
    if not stat.S_ISDIR(state_info.st_mode) or state_info.st_uid != os.geteuid() or stat.S_IMODE(state_info.st_mode) & 0o077:
        raise ValueError('unsafe dream state directory')
    for filename in ('pending.json', 'applied.json'):
        path = state / filename
        if path.is_symlink():
            raise ValueError('unsafe dream state file')
        if path.exists():
            info = path.stat()
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) & 0o077:
                raise ValueError('unsafe dream state file')
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
        if pending['changes']:
            try:
                store.apply(pending['changes'], attach, key)
            except VersionConflict as conflict:
                if _attempt >= 2:
                    raise
                journal.unlink()
                return apply(store, root, notes, input_sessions, attach, key, _attempt=_attempt + 1,
                             _conflicts=_conflicts | ({conflict.section_key} if conflict.section_key else set()))
        store.log(pending['report'], key)
        _write_state(receipt, {'key': key, 'report': pending['report']})
        journal.unlink()
        return pending['report']
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
    changes, dropped = [], []
    for section_key in sorted(set(originals) | set(output)):
        old = originals.get(section_key)
        new = output.get(section_key)
        if old == new:
            continue
        if section_key.startswith('conventions/proposed/'):
            raise ValueError('dream cannot change convention proposals')
        if section_key in moved:
            dropped.append(section_key)
            continue
        item = {'section_key': section_key, 'base_version': old[0]['version'] if old else 0, 'op': 'write' if new else 'retire'}
        if new:
            item['body_text'] = new[1]
            if new[0]['title'] is not None:
                item['title'] = new[0]['title']
        changes.append(item)
    if len(current) + sum(1 if i['section_key'] not in current else -1 if i['op'] == 'retire' else 0 for i in changes) > 300:
        raise ValueError('memory exceeds 300 sections')
    # Apply the scanner patterns directly: untrusted dream output cannot grant itself an exemption.
    for text in [json.dumps(notes)] + [p.read_text(encoding='utf-8') for p in files]:
        if any(rx.search(text) for _, rx, _, _ in secret_scan.PATTERNS):
            raise ValueError('secret scan failed')
    for item in changes:
        if not store.gate(item, attach):
            raise ValueError('doctrine gate failed')
    report = {'notes': notes, 'dropped': dropped, 'changed': len(changes)}
    receipt.parent.mkdir(parents=True, exist_ok=True)
    _write_state(journal, {'key': key, 'attach': attach, 'changes': changes, 'report': report})
    if changes:
        try:
            store.apply(changes, attach, key)
        except VersionConflict as conflict:
            if _attempt >= 2:
                raise
            journal.unlink()
            return apply(store, root, notes, input_sessions, attach, key, _attempt=_attempt + 1,
                             _conflicts=_conflicts | ({conflict.section_key} if conflict.section_key else set()))
    store.log(report, key)
    _write_state(receipt, {'key': key, 'report': report})
    journal.unlink()
    return report


class VerbStore:
    def call(self, verb, args):
        process = subprocess.run([str(ROOT / 'run.sh'), 'call', verb, json.dumps(args)], cwd=ROOT, capture_output=True, text=True)
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
        self.call('change-doctrine-sections', {'document': 'team-memory', 'attach_id': attach, 'idempotency_key': key, 'commit_message': 'Dream memory consolidation', 'items': items})
    def log(self, report, key):
        section_key = 'dreams/' + hashlib.sha256(key.encode()).hexdigest()
        self.call('write-doctrine-section', {'document': 'dreams-log', 'section_key': section_key, 'title': 'Dream report', 'body_text': json.dumps(report, sort_keys=True), 'base_version': 0, 'idempotency_key': key + '-log'})


class ClaudeAgent:
    """Headless subscription-backed proposer; tests inject a synthetic proposer."""
    def propose(self, session_id, transcript, memories):
        prompt = ('Suggest memory changes supported by this session. Return only JSON '
                  'with a units array. Each unit has note and changes; each change has '
                  'section_key, op (write or retire), and for write, title and body_text. '
                  'Merge duplicate lessons, retire stale entries, discover cross-session patterns in memory, '
                  'and add dated verification notes citing the session where confirmed. '
                  'Never modify conventions/proposed/. Group dependent changes in one unit.\n' +
                  json.dumps({'session_id': session_id, 'transcript': transcript, 'memory': memories}))
        result = subprocess.run(['claude', '-p', '--model', 'sonnet', '--output-format', 'json', '--tools', ''],
                                input=prompt, text=True, capture_output=True, check=True)
        response = json.loads(result.stdout)
        if response.get('is_error'):
            raise ValueError('dream proposer failed')
        return json.loads(response['result'])['units']


def propose(root, sessions, agent):
    """Apply per-transcript proposals to the clone and return change-unit notes."""
    root = Path(root)
    notes = []
    for session_id, transcript_path in sessions:
        memories = {}
        for path in root.rglob('*.md'):
            if '.git' in path.relative_to(root).parts or '_dream' in path.relative_to(root).parts:
                continue
            meta, body = _parse(path.read_text(encoding='utf-8'))
            memories[meta['section_key']] = {'title': meta['title'], 'body_text': body}
        units = agent.propose(session_id, condense(transcript_path), memories)
        if not isinstance(units, list):
            raise ValueError('invalid proposer output')
        for unit in units:
            changes = unit['changes']
            keys = [change['section_key'] for change in changes]
            if not keys or len(set(keys)) != len(keys):
                raise ValueError('invalid change unit')
            for change in changes:
                key = change['section_key']
                if key.startswith('conventions/proposed/'):
                    raise ValueError('dream cannot change convention proposals')
                path = _path(root, key)
                if change['op'] == 'retire':
                    path.unlink()
                elif change['op'] == 'write':
                    if path.exists():
                        meta, _ = _parse(path.read_text(encoding='utf-8'))
                        if change['title'] != meta['title']:
                            raise ValueError('existing title cannot change')
                    else:
                        meta = {'section_id': None, 'section_key': key, 'title': change['title'], 'version': 0, 'content_hash': None}
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text('---\n' + json.dumps(meta, sort_keys=True) + '\n---\n' + change['body_text'], encoding='utf-8')
                else:
                    raise ValueError('invalid proposal operation')
            merged = {'note': unit['note'], 'sections': keys, 'sessions': [session_id]}
            overlap = [old for old in notes if set(old['sections']).intersection(keys)]
            for old in overlap:
                notes.remove(old)
                merged['sections'] = sorted(set(merged['sections']) | set(old['sections']))
                merged['sessions'] = sorted(set(merged['sessions']) | set(old['sessions']))
                merged['note'] += '\n' + old['note']
            notes.append(merged)
    notes_path = root / '_dream' / 'notes.md'
    notes_path.parent.mkdir(parents=True, exist_ok=True)
    notes_path.write_text(json.dumps(notes, indent=2), encoding='utf-8')
    return notes


def run(store, root, sessions, agent, attach, key):
    """Execute a dream for already-selected sessions."""
    clone(store, root)
    propose(root, sessions, agent)
    return apply(store, root, None, [session_id for session_id, _ in sessions], attach, key)
