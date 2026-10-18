#!/usr/bin/env python3
"""Collect a bounded correction corpus through verbs and native user records.

Raw evidence stays in a private directory outside every checkout. A regex marks
review candidates; it never decides whether a partner corrected the session.
Every retained turn needs a disposition before transcript review is complete.
"""
import argparse
from collections import Counter
from datetime import date, datetime, timedelta, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import uuid

REPO = Path(__file__).resolve().parents[1]


def load_script(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f'{name}: dependency could not be loaded')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


BASELINES = load_script('correction_baselines', REPO / 'tools/displacement-baselines.py')
HISTORY = load_script('correction_history', REPO / 'ops/codex-history.py')
CORRECTION = re.compile(r"\b(?:wrong|incorrect|correction|corrected|overruled|actually|already|supposed|"
                        r"why|stop|misunderstanding|missed|forgot|not what|never made|you keep)\b", re.I)
MACHINE = re.compile(r'^\s*(?:<hook_prompt\b|<subagent_notification\b|<task-notification\b|'
                     r'</task-notification>|<cross-session-message\b|Another Claude session sent a message:|'
                     r'The following is the Codex agent history|\[/usr/bin/|'
                     r'SEND THE DELTA, NOT THE MESSAGE AGAIN\b)', re.I)
BOOTSTRAP = ('# AGENTS.md instructions for ', '<environment_context>', '<INSTRUCTIONS>', '<recommended_plugins>')
ROUTES = {'rule_delivery', 'stale_source', 'missing_context', 'tool_default',
          'source_discovery', 'verification', 'capture', 'workflow_scope', 'unknown'}
MECHANISMS = {'delivery_contract', 'source_version_check', 'context_contract',
              'tool_contract', 'source_enumeration', 'artifact_readback',
              'capture_contract', 'scope_contract', 'proposal'}
READ_VERBS = {'standing-context', 'find-precedent', 'read-doc-activity'}
PROSE_CREDENTIALS = re.compile(
    r'(\b(?:make|set|change|use)\s+(?:(?:the|a|new)\s+)*'
    r'(?:password|passphrase|api[ _-]?key|access[ _-]?token)\s+(?:(?:to|as)\s+)?|'
    r'\b(?:password|passphrase|api[ _-]?key|access[ _-]?token)\s+(?:is|was)\s+)'
    r'(?!\b(?:policy|protection|requirements?|reset|field|to|as)\b)'
    r'(?:"[^"\r\n]*"|\'[^\'\r\n]*\'|`[^`\r\n]*`|'
    r'[^\r\n]+?(?=[.!?;](?:\s|$)|[\r\n]|$))', re.I)


def redact_text(text):
    text, count = HISTORY._redact_text(text)
    text, prose_count = PROSE_CREDENTIALS.subn(r'\1<REDACTED>', text)
    return text, count + prose_count


def redact_evidence(value):
    if isinstance(value, str):
        return redact_text(value)[0]
    if isinstance(value, list):
        return [redact_evidence(item) for item in value]
    if isinstance(value, dict):
        return {key: redact_evidence(item) for key, item in value.items()}
    return value


def user_text(row, family, meta):
    if family == 'claude':
        if row.get('type') != 'user' or row.get('isSidechain'):
            return None
        message = row.get('message', {})
        content = message.get('content', []) if isinstance(message, dict) else []
        if isinstance(content, list) and any(isinstance(b, dict) and b.get('type') == 'tool_result' for b in content):
            return None
        text = BASELINES.INJECTED.sub('', BASELINES.text_of(row)).strip()
    else:
        if not isinstance(meta, dict):
            return None
        if isinstance(meta.get('source'), dict) and 'subagent' in meta['source']:
            return None
        payload = row.get('payload', {})
        if not isinstance(payload, dict):
            return None
        if row.get('type') != 'response_item' or payload.get('type') != 'message' or payload.get('role') != 'user':
            return None
        content = payload.get('content', [])
        if not isinstance(content, list):
            return None
        text = '\n'.join(b['text'] for b in content if isinstance(b, dict) and b.get('type') in {'input_text', 'text'} and isinstance(b.get('text'), str)).strip()
    if not text or text.startswith(BOOTSTRAP) or MACHINE.search(text) or BASELINES.is_machine_origin(text):
        return None
    return text


def utc_day(value):
    if not isinstance(value, str):
        return None
    try:
        instant = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if instant.tzinfo is None:
            return None
        return instant.astimezone(timezone.utc).date().isoformat()
    except ValueError:
        return None


def scan_transcripts(files, since, until, cwd_roots=None):
    date.fromisoformat(since)
    date.fromisoformat(until)
    if since >= until:
        raise ValueError('since must be earlier than until')
    turns = {}
    coverage = {}
    gaps = []
    for family, paths in files.items():
        counts = Counter(files_found=len(paths))
        days = []
        for path in sorted(map(Path, paths)):
            meta = {}
            try:
                before = path.stat()
                if path.is_symlink():
                    gaps.append({'source': str(path), 'reason': 'symlink not scanned'})
                    continue
                with path.open(encoding='utf-8') as handle:
                    for number, line in enumerate(handle, 1):
                        try:
                            row = json.loads(line)
                        except (ValueError, UnicodeError):
                            gaps.append({'source': str(path), 'line': number, 'reason': 'unreadable JSON row'})
                            continue
                        if not isinstance(row, dict):
                            gaps.append({'source': str(path), 'line': number, 'reason': 'non-object row'})
                            continue
                        if row.get('type') == 'session_meta':
                            payload = row.get('payload')
                            if not isinstance(payload, dict):
                                gaps.append({'source': str(path), 'line': number, 'reason': 'invalid session metadata'})
                                meta = {}
                                continue
                            meta = payload
                        if family == 'codex' and cwd_roots is not None:
                            raw_cwd = meta.get('cwd')
                            if not isinstance(raw_cwd, str) or not raw_cwd:
                                gaps.append({'source': str(path), 'line': number, 'reason': 'invalid session cwd'})
                                continue
                            cwd = Path(raw_cwd).resolve()
                            if not any(cwd == root or root in cwd.parents for root in cwd_roots):
                                continue
                        try:
                            text = user_text(row, family, meta)
                        except (AttributeError, TypeError):
                            gaps.append({'source': str(path), 'line': number, 'reason': 'invalid native user record'})
                            continue
                        if text is None:
                            continue
                        day = utc_day(row.get('timestamp'))
                        if day is None:
                            gaps.append({'source': str(path), 'line': number, 'reason': 'user timestamp unavailable'})
                            continue
                        if not since <= day < until:
                            continue
                        counts['user_records'] += 1
                        days.append(day)
                        session = row.get('sessionId') or meta.get('id') or path.stem
                        native_id = row.get('uuid') or row.get('id') or row.get('timestamp')
                        identity = [family, session, native_id, text]
                        key = hashlib.sha256(json.dumps(identity, ensure_ascii=False).encode()).hexdigest()
                        source = {'path': str(path), 'line': number, 'timestamp': row['timestamp']}
                        if key in turns:
                            turns[key]['sources'].append(source)
                            counts['copies'] += 1
                            continue
                        redacted, redactions = redact_text(text)
                        turns[key] = {'id': key, 'family': family, 'text': redacted,
                                      'sources': [source], 'origin': 'user-role; human attribution needs review',
                                      'candidate': bool(CORRECTION.search(text) or text.lower().strip('.!? ') == 'no'),
                                      'redactions': redactions}
                        counts['retained_turns'] += 1
                after = path.stat()
                if (before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_ino, after.st_size, after.st_mtime_ns):
                    gaps.append({'source': str(path), 'reason': 'source changed during scan'})
                counts['files_read'] += 1
            except (OSError, UnicodeError) as exc:
                gaps.append({'source': str(path), 'reason': type(exc).__name__})
        coverage[family] = {**counts, 'first_user_day': min(days) if days else None,
                            'last_user_day': max(days) if days else None}
    return {'turns': list(turns.values()), 'coverage': coverage, 'gaps': gaps}


def read_verb(verb, args):
    if verb not in READ_VERBS:
        raise ValueError('verb is outside the read-only correction corpus')
    result = subprocess.run([str(REPO / 'run.sh'), 'call', verb, json.dumps(args)],
                            text=True, capture_output=True, timeout=45)
    try:
        value = json.loads(result.stdout)
    except ValueError:
        raise RuntimeError(f'{verb}: invalid read response') from None
    if not isinstance(value, dict) or result.returncode or value.get('ok') is not True:
        raise RuntimeError(f'{verb}: read refused')
    return value


def collect_records(since, until, call=read_verb):
    records = {}
    gaps = []
    jobs = [('active_rules', 'standing-context', {'detail': 'full'})]
    jobs += [('precedent_' + query, 'find-precedent', {'query': query, 'since': since, 'limit': 25})
             for query in ('correction', 'overruled', 'Joe corrected', 'Dell corrected', 'vendor network')]
    for key, verb, args in jobs:
        try:
            value = call(verb, args)
            records[key] = value
            if verb == 'find-precedent' and value.get('count', 0) >= 25:
                gaps.append({'source': key, 'reason': 'precedent search reached result cap; not an exhaustive history'})
        except (RuntimeError, OSError, subprocess.TimeoutExpired) as exc:
            gaps.append({'source': key, 'reason': type(exc).__name__})
    for kind in ('rule', 'decision', 'doctrine_section'):
        entries = []
        cursor = None
        cursors = set()
        try:
            while True:
                args = {'record_type': kind, 'since': since + 'T00:00:00Z',
                        'until': until + 'T00:00:00Z', 'limit': 100}
                if cursor:
                    args['cursor'] = cursor
                page = call('read-doc-activity', args)
                entries.extend(page.get('entries', []))
                cursor = page.get('next_cursor')
                if not cursor:
                    break
                encoded = json.dumps(cursor, sort_keys=True)
                if encoded in cursors:
                    raise RuntimeError('non-advancing activity cursor')
                cursors.add(encoded)
        except (RuntimeError, OSError, subprocess.TimeoutExpired) as exc:
            gaps.append({'source': kind + '_activity', 'reason': type(exc).__name__})
        records[kind + '_activity'] = entries
    gaps.append({'source': 'record_history', 'reason': 'activity includes autonomous events only; search and current rules cannot prove complete human decisions or rule amendments'})
    return {'records': records, 'gaps': gaps}


def scan_conduct(path, since, until):
    counts = Counter()
    gaps = []
    try:
        with Path(path).open() as handle:
            for number, line in enumerate(handle, 1):
                try:
                    row = json.loads(line)
                except ValueError:
                    gaps.append({'source': str(path), 'line': number, 'reason': 'unreadable conduct row'})
                    continue
                if not isinstance(row, dict):
                    gaps.append({'source': str(path), 'line': number, 'reason': 'non-object conduct row'})
                    continue
                day = utc_day(row.get('ts'))
                if day and since <= day < until:
                    labels = row.get('classes', [row.get('hook', 'unknown')])
                    if not isinstance(labels, list) or not labels or not all(isinstance(label, str) and label for label in labels):
                        gaps.append({'source': str(path), 'line': number, 'reason': 'invalid conduct classes'})
                        continue
                    counts.update(labels)
    except OSError as exc:
        gaps.append({'source': str(path), 'reason': type(exc).__name__})
    return {'class_counts': dict(counts), 'meaning': 'gate events, not human corrections', 'gaps': gaps}


def review_gaps(turns, review):
    known = {row['id'] for row in turns}
    seen = set()
    gaps = []
    if not isinstance(review, list):
        gaps.append({'reason': 'review must be a list'})
        review = []
    for row in review:
        if not isinstance(row, dict):
            gaps.append({'reason': 'review entry must be an object'})
            continue
        key = row.get('id')
        if not isinstance(key, str) or key not in known or key in seen:
            gaps.append({'id': key, 'reason': 'unknown or duplicate review id'})
            continue
        seen.add(key)
        disposition = row.get('disposition')
        if disposition not in {'correction', 'not_correction', 'machine', 'uncertain'}:
            gaps.append({'id': key, 'reason': 'disposition required'})
        elif disposition == 'uncertain':
            gaps.append({'id': key, 'reason': 'human attribution or correction unresolved'})
        elif disposition == 'correction':
            if row.get('partner') not in {'joe', 'dell'}:
                gaps.append({'id': key, 'reason': 'correction must be attributed to Joe or Dell'})
            if row.get('route') not in ROUTES - {'unknown'} or row.get('mechanism') not in MECHANISMS:
                gaps.append({'id': key, 'reason': 'an upstream route and mechanism are required; output patches do not qualify'})
            refs = row.get('source_refs')
            fix = row.get('fix')
            if not isinstance(refs, list) or not refs or not all(isinstance(ref, str) and ref.strip() for ref in refs) or not isinstance(fix, str) or not fix.strip():
                gaps.append({'id': key, 'reason': 'source evidence and a fix or proposal are required'})
    gaps.extend({'id': key, 'reason': 'turn has not been reviewed'} for key in sorted(known - seen))
    return gaps


def write_private(path, value):
    path = Path(path).expanduser().absolute()
    if path.is_symlink():
        raise ValueError('evidence file must not be a symlink')
    path = path.resolve()
    for parent in (path.parent, *path.parents):
        if (parent / '.git').exists() or parent == REPO:
            raise ValueError('correction evidence must stay outside checkouts')
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.parent.stat().st_mode & 0o077:
        raise ValueError('evidence directory must be private (mode 700)')
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0)
    with os.fdopen(os.open(path, flags, 0o600), 'w') as handle:
        handle.write(json.dumps(redact_evidence(value), ensure_ascii=False, indent=2, default=str) + '\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    until_default = datetime.now(timezone.utc).date() + timedelta(days=1)
    parser.add_argument('--since', default=(until_default - timedelta(days=30)).isoformat())
    parser.add_argument('--until', default=until_default.isoformat(), help='Exclusive UTC date')
    parser.add_argument('--all-projects', action='store_true', help='Explicitly include all local Claude and Codex projects')
    parser.add_argument('--offline', action='store_true', help='Skip record reads and report the gap')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--review', type=Path, help='Private JSON list of dispositions keyed by evidence id')
    args = parser.parse_args()
    claude_roots = [Path.home() / '.claude/projects'] if args.all_projects else BASELINES._project_roots()
    files = {'claude': sorted({path for root in claude_roots for path in root.rglob('*.jsonl')}),
             'codex': sorted((Path.home() / '.codex/sessions').rglob('*.jsonl'))}
    cwd_roots = None if args.all_projects else {REPO, (Path.home() / 'carr-system').resolve()}
    result = scan_transcripts(files, args.since, args.until, cwd_roots=cwd_roots)
    result.update(schema='correction-routes.v1', since=args.since, until=args.until)
    if args.offline:
        result['gaps'].append({'source': 'records', 'reason': 'record reads skipped'})
    else:
        remote = collect_records(args.since, args.until)
        result['records'] = remote['records']
        result['gaps'].extend(remote['gaps'])
    result['conduct'] = scan_conduct(REPO / 'out/conduct-gate.jsonl', args.since, args.until)
    result['gaps'].extend(result['conduct']['gaps'])
    result['review'] = json.loads(args.review.read_text()) if args.review else []
    review_missing = review_gaps(result['turns'], result['review'])
    result['review_gaps'] = review_missing
    result['complete'] = not result['gaps'] and not review_missing
    path = args.output or Path.home() / '.local/state/carr/correction-sweeps' / (str(uuid.uuid4()) + '.json')
    write_private(path, result)
    print(json.dumps({'evidence': str(path), 'coverage': result['coverage'],
                      'candidate_turns': sum(row['candidate'] for row in result['turns']),
                      'unreviewed_or_invalid': len(review_missing), 'source_gaps': len(result['gaps']),
                      'complete': result['complete']}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
