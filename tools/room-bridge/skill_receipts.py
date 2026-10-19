"""Session-local skill evidence. Never emit transcript text or tool output.

Catch an honest-but-careless agent claiming a skill it never opened.
Deliberate forgery, including replaced system tools, symlinks, or wrapper code
that rewrites tool results, is outside this check's scope.
"""
from __future__ import annotations

import ast
from datetime import datetime
import json
from pathlib import Path
import re
import shlex

POLICY = json.loads((Path(__file__).resolve().parents[2] / 'ops/skill-evidence-policy.json').read_text())
MARKER = '<!-- skill-run-receipt/v1 -->'


def canonical(value):
    value = re.sub(r'\([^)]*\)', '', value).strip().strip('`/ ')
    value = re.split(r'\s+for\s+', value, maxsplit=1)[0].split(':')[-1]
    value = re.sub(r'[^a-z0-9]+', '-', value.lower()).strip('-')
    return POLICY['aliases'].get(value, value)


def claims(text):
    match = re.search(r'^Skills(?: used)?:[ \t]*(.+)$', text or '', re.I | re.M)
    if not match:
        return []
    line = re.split(r'\.\s+(?=[A-Z])', match[1], maxsplit=1)[0]
    return list(dict.fromkeys(canonical(item) for item in re.split(r'[,;]', line) if canonical(item)))


def _skill_path(value):
    return isinstance(value, str) and value.endswith('/SKILL.md') and not any(c in value for c in '*?${}')


def _read_paths(name, args):
    if name.lower() in ('read', 'read_file'):
        path = args.get('file_path') or args.get('path')
        return [path] if _skill_path(path) else []
    if name.split('.')[-1] not in ('exec_command', 'Bash', 'shell', 'shell_command', 'exec'):
        return []
    command = args.get('cmd') or args.get('command')
    if command is None and isinstance(args.get('code'), str):
        # A wrapper's first unconditional, literal call can be paired with its first result.
        # Later calls and arbitrary JavaScript need native execution evidence, not source matching.
        match = re.match(r'\s*(?:// @exec:[^\n]*\n)?\s*text\(await tools\.exec_command\(\{\s*cmd\s*:\s*("(?:\\.|[^"\\])*"|\x27(?:\\.|[^\x27\\])*\x27)\s*\}\)\)\s*;', args['code'])
        if match:
            try:
                return _read_paths('exec_command', {'cmd': ast.literal_eval(match[1])})
            except (ValueError, SyntaxError):
                pass
        return []
    if not isinstance(command, str):
        return []
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=';&|<>\n')
        lexer.whitespace_split = True
        lexer.commenters = ''
        tokens = list(lexer)
    except ValueError:
        return []
    # One shell status proves only one unconditional read, never a compound command.
    if any(char in command for char in '\n\r`$') or any(
            any(char in token for char in ';&|<>()') for token in tokens):
        return []
    if not tokens or Path(tokens[0]).name not in ('cat', 'head', 'tail', 'sed'):
        return []
    reader, operands = Path(tokens[0]).name, tokens[1:]
    if reader == 'sed':
        if len(operands) < 3 or operands[0] != '-n' or not re.fullmatch(r'[1-9]\d*(?:,[1-9]\d*)?p', operands[1]):
            return []
        operands = operands[2:]
    if operands[:1] == ['--']:
        operands = operands[1:]
    return operands if operands and all(_skill_path(path) and not path.startswith('-') for path in operands) else []


def _success(output):
    if isinstance(output, dict):
        if 'exit_code' in output:
            return output['exit_code'] == 0
        return _success(output.get('content') or output.get('text'))
    if isinstance(output, list):
        for item in output:
            if not isinstance(item, dict) or not isinstance(item.get('text'), str):
                continue
            text = item['text']
            try:
                result = json.loads(text)
            except json.JSONDecodeError:
                result = None
            if isinstance(result, dict) and 'exit_code' in result:
                return result['exit_code'] == 0
            for match in re.finditer(r'\{"chunk_id"\s*:', text):
                try:
                    result, _ = json.JSONDecoder().raw_decode(text[match.start():])
                except json.JSONDecodeError:
                    continue
                if isinstance(result.get('exit_code'), int):
                    return result['exit_code'] == 0
        return False
    if not isinstance(output, str):
        return False
    while '\\"' in output:
        output = output.replace('\\"', '"')
    if re.search(r'"exit_code"\s*:\s*[1-9]|Process exited with code [1-9]|Script failed', output):
        return False
    return bool(re.search(r'"exit_code"\s*:\s*0\b|Process exited with code 0\b|Exit code: 0\b', output))


def _stamp(value):
    try:
        return datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()
    except (ValueError, TypeError, AttributeError):
        return None


def validate_window(since, until):
    cutoff, end = _stamp(since), _stamp(until)
    if (since is not None and cutoff is None or until is not None and end is None
            or cutoff is not None and end is not None and cutoff > end):
        raise ValueError('invalid skill receipt window')
    return cutoff, end


def scan(path, *, since=None, until=None):
    available, opened, pending = set(), set(), {}
    cutoff, end = validate_window(since, until)
    with Path(path).open(encoding='utf-8') as handle:
        for line in handle:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(row, dict):
                continue
            payload = row.get('payload') or row
            if not isinstance(payload, dict):
                continue
            content = payload.get('content') or (row.get('message') or {}).get('content') or []
            if not isinstance(content, list):
                content = []
            if payload.get('role') in ('developer', 'system', 'user') or row.get('type') in ('system', 'user'):
                for block in content:
                    text = block.get('text', '') if isinstance(block, dict) else ''
                    if 'Available skills' in text or '<available_skills>' in text:
                        available.update(canonical(name) for name in re.findall(r'^- ([\w:.-]+):', text, re.M))
                if row.get('subtype') == 'init':
                    available.update(canonical(item) for item in row.get('skills', []) if isinstance(item, str))
            stamp = _stamp(row.get('timestamp'))
            current = ((cutoff is None and end is None) or stamp is not None
                       and (cutoff is None or stamp >= cutoff) and (end is None or stamp <= end))
            if payload.get('type') in ('function_call', 'custom_tool_call') and current:
                try:
                    args = ({'code': payload.get('input', '')} if payload['type'] == 'custom_tool_call'
                            else json.loads(payload.get('arguments', '{}')))
                    pending[payload.get('call_id')] = _read_paths(payload.get('name', ''), args)
                except (json.JSONDecodeError, TypeError, AttributeError):
                    continue
            elif payload.get('type') in ('function_call_output', 'custom_tool_call_output'):
                paths = pending.pop(payload.get('call_id'), [])
                if current and _success(payload.get('output')):
                    opened.update(paths)
            for block in content:
                if not isinstance(block, dict):
                    continue
                if block.get('type') == 'tool_use' and current:
                    pending[block.get('id')] = (_read_paths(block.get('name', ''), block.get('input') or {}),
                                                block.get('name') in ('Read', 'read_file', 'Bash'))
                elif block.get('type') == 'tool_result':
                    paths, direct = pending.pop(block.get('tool_use_id'), ([], False))
                    if current and not block.get('is_error') and (direct or _success(block.get('content'))):
                        opened.update(paths)
    return {'available': sorted(available), 'opened': sorted(opened)}


def completion_time(path, *, since, result):
    cutoff = _stamp(since)
    if cutoff is None or not isinstance(result, str) or not result.strip():
        return None
    with Path(path).open(encoding='utf-8') as handle:
        for line in handle:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(row, dict):
                continue
            stamp = _stamp(row.get('timestamp'))
            if stamp is None or stamp < cutoff:
                continue
            message = row.get('message') if row.get('type') == 'assistant' else row.get('payload')
            if not isinstance(message, dict):
                continue
            terminal = (row.get('type') == 'assistant' and message.get('stop_reason') == 'end_turn'
                        or message.get('role') == 'assistant' and message.get('channel') == 'final')
            content = message.get('content')
            if not terminal or not isinstance(content, list):
                continue
            text = '\n'.join(block['text'] for block in content if isinstance(block, dict)
                             and block.get('type') in ('text', 'output_text') and isinstance(block.get('text'), str))
            if text.strip() == result.strip():
                return row['timestamp']
    return None


def session_path(session_id, runtime, *, home=None):
    if not re.fullmatch(r'[a-fA-F0-9-]{36}', session_id or ''):
        return None
    home = Path(home or Path.home())
    root = home / ('.codex/sessions' if runtime == 'codex' else '.claude/projects')
    pattern = f'*/*/*/rollout-*-{session_id}.jsonl' if runtime == 'codex' else f'*/{session_id}.jsonl'
    matches = list(root.glob(pattern))
    return matches[0] if len(matches) == 1 else None


def require_skills(task, inventory):
    required = claims(task)
    if not required:
        return
    if not inventory or not inventory.get('available'):
        raise ValueError('skill inventory unknown; observe the desk session with dispatch.py skills first')
    missing = sorted(set(required) - set(inventory['available']))
    if missing:
        raise ValueError('skills not available in desk session: ' + ', '.join(missing))


def claude_listing(path):
    listing = None
    if Path(path).exists():
        with Path(path).open() as handle:
            for line in handle:
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if row.get('type') == 'system' and row.get('subtype') == 'init':
                    listing = {'session_id': row.get('session_id'), 'opened': [],
                               'available': sorted({canonical(item) for item in row.get('skills', []) if isinstance(item, str)})}
    return listing


def receipt(row, *, repo, head, body, observation):
    paths = observation['opened']
    return {'schema': 'skill-run-receipt/v1', 'repo': repo, 'head': head,
            'session_id': row.get('thread_id') or row.get('session_id'),
            'dispatch_id': row['msg_id'], 'available': observation['available'],
            'skills': [{'skill': skill, 'opened': any(canonical(Path(path).parent.name) == skill for path in paths),
                        'paths': [path for path in paths if canonical(Path(path).parent.name) == skill]}
                       for skill in claims(body)]}


def comment_body(value):
    lines = [MARKER, 'Observed successful skill-file reads for this PR revision.', '',
             '| Claimed skill | Session evidence |', '| --- | --- |']
    lines.extend(f"| {item['skill']} | {'opened' if item['opened'] else 'not opened'} |" for item in value['skills'])
    return '\n'.join([*lines, '', '```json', json.dumps(value, sort_keys=True), '```'])
