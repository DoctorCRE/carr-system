#!/usr/bin/env python3
"""Require turn-local enforcement evidence for durable-rule claims."""
import json
import fcntl
import hashlib
from pathlib import Path
import re
import shlex
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lib.hook_runtime import Verdict, decision, run
from ops.git_env import scrubbed_env

CLAIM = re.compile(
    r"\b(?:saved|added|created)\b[^.!?\n]*\b(?:rule|lesson)\b"
    r"|\b(?:rule|lesson)\b\s+(?:is|was|has been)\s+(?:now\s+)?(?:saved|added|created|in place)\b"
    r"|\bI['’]ll remember\b|\bfrom now on I will\b"
    r"|\brule is now in place\b|\bwon['’]t happen again\b", re.I)


def text_of(record):
    content = record.get('message', {}).get('content', '')
    if isinstance(content, str):
        return content
    return '\n'.join(b.get('text', '') for b in content if b.get('type') == 'text')


def is_summary(record):
    return (record.get('isCompactSummary') or record.get('type') == 'summary'
            or record.get('subtype') == 'compact_boundary'
            or text_of(record).lstrip().startswith(
                'This session is being continued from a previous conversation'))


def is_human(record):
    if record.get('type') != 'user' or record.get('isMeta') or is_summary(record):
        return False
    text = text_of(record).lstrip()
    return bool(text) and not text.startswith((
        '<system-reminder>', '<task-notification>', '[SYSTEM NOTIFICATION',
        '<local-command', '<command-name>', 'Caveat:'))


def prose(text):
    text = re.sub(r'<(?:summary|compaction_summary)\b[^>]*>.*?</(?:summary|compaction_summary)>',
                  '', text, flags=re.S | re.I)
    lines, fence = [], None
    for line in text.splitlines():
        marker = re.match(r'^ {0,3}(`{3,}|~{3,})', line)
        if marker:
            token = marker[1]
            if fence is None:
                fence = token
            elif token[0] == fence[0] and len(token) >= len(fence):
                fence = None
            continue
        if fence or re.match(r'^(?: {0,3}>| {4}|\t)', line):
            continue
        lines.append(line)
    text = '\n'.join(lines)
    text = re.sub(r'(`+).*?\1', '', text, flags=re.S)
    text = re.sub(r'"[^"]*"|“[^”]*”|‘(?:[^’]|(?<=\w)’(?=\w))*’', '', text)
    text = re.sub(r"(?<!\w)'(?:[^']|(?<=\w)'(?=\w))*'(?!\w)", '', text)
    return text


def asserted_claim(sentence):
    match = CLAIM.search(sentence)
    if not match or sentence.rstrip().endswith('?'):
        return False
    context = sentence[:match.end()]
    return not re.search(
        r"\b(?:if|unless|not|never|haven['’]t|hasn['’]t|hadn['’]t|would|could|should)\b",
        context, re.I)


def enforcement_path(path):
    directory = path.parent
    while not directory.exists():
        directory = directory.parent
    env = scrubbed_env()
    try:
        root = Path(subprocess.run(
            ['git', '-C', str(directory), 'rev-parse', '--show-toplevel'],
            check=True, capture_output=True, text=True, timeout=5, env=env).stdout.strip()).resolve()
        origin = subprocess.run(
            ['git', '-C', str(root), 'config', '--get', 'remote.origin.url'],
            check=True, capture_output=True, text=True, timeout=5, env=env).stdout.strip()
        if re.fullmatch(r'(?:https?://github\.com/|git@github\.com:|ssh://git@github\.com/)'
                        r'DoctorCRE/carr-system(?:\.git)?/?', origin):
            return path.relative_to(root).as_posix()
    except (OSError, ValueError, subprocess.SubprocessError):
        pass
    return ''


def background_build(command, cwd):
    lexer = shlex.shlex(command, posix=True, punctuation_chars=';&|\n')
    lexer.whitespace = ' \t\r'
    lexer.whitespace_split = True
    segments, segment = [], []
    for token in lexer:
        if token and all(c in ';&|\n' for c in token):
            segments.append(segment)
            segment = []
        else:
            segment.append(token)
    segments.append(segment)
    for args in segments:
        if len(args) == 2 and args[0] == 'cd':
            cwd = (Path(cwd) / Path(args[1]).expanduser()).resolve()
            continue
        if args and re.fullmatch(r'python(?:3(?:\.\d+)?)?', Path(args[0]).name):
            args = args[1:]
        if len(args) < 4:
            continue
        if (Path(cwd) / args[0]).resolve() != (Path(cwd) / 'bin/codex-job').resolve():
            continue
        if re.search(
            r'\b(?:build|add|create|implement|write)\b[^.!?]*\b(?:hook|gate|test|CI check)\b',
            args[3], re.I):
            return True
    return False


def log(event, action, **details):
    row = json.dumps({'event': action, 'session': event.get('session_id'), **details})
    try:
        out = Path(event.get('cwd') or Path.cwd()) / 'out'
        out.mkdir(parents=True, exist_ok=True)
        with (out / 'rule-claim-gate.jsonl').open('a') as handle:
            handle.write(row + '\n')
    except OSError:
        pass
    print('rule-claim-gate: ' + row, file=sys.stderr)


def parse_error(exc, event=None):
    log(event or {}, 'parse_error', error=str(exc))


def already_blocked(event, boundary, index, *, mark=False):
    identity = [event.get('session_id'), str(Path(event['transcript_path']).resolve()),
                boundary.get('uuid') or [index, boundary]]
    key = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    directory = Path(event['cwd']) / 'out/rule-claim-gate'
    path = directory / (key + '.state')
    if not mark and not path.exists():
        return False
    directory.mkdir(parents=True, exist_ok=True)
    with path.open('a+') as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        handle.seek(0)
        if handle.read():
            log(event, 'repeat_stop_allowed', turn=key)
            return True
        if mark:
            handle.write('blocked\n')
            handle.flush()
    if mark:
        log(event, 'claim_blocked', turn=key)
    return False


@decision(on_error=parse_error)
def decide(event):
    if event.get('hook_event_name') != 'Stop':
        return
    try:
        records = [json.loads(line) for line in Path(event['transcript_path']).read_text().splitlines()
                   if line.strip()]
        if not all(isinstance(r, dict) for r in records):
            raise ValueError('transcript record is not an object')
        return check_turn(event, records)
    except Exception as exc:
        parse_error(exc, event)


def check_turn(event, records):
    boundaries = [i for i, r in enumerate(records) if is_human(r) or is_summary(r)]
    if not boundaries:
        return
    start = boundaries[-1]
    boundary = records[start]
    if already_blocked(event, boundary, start):
        return
    user_text = '\n'.join(text_of(r) for r in records if is_human(r))
    records = records[start + 1:]
    replies = [r for r in records if r.get('type') == 'assistant' and not is_summary(r)
               and text_of(r)]
    if not replies:
        return
    text = prose(text_of(replies[-1]))
    claims = [s.strip() for s in re.split(r'(?<=[.!?])\s+|\n+', text)
              if asserted_claim(s) and s.strip().casefold() not in user_text.casefold()]
    if not claims:
        return
    if (re.search(r'\bnot enforced\b', text, re.I)
            and re.search(r'\b(?:because|requires|depends on)\b|not enforced\s*:', text, re.I)
            and re.search(r'\b(?:subjective|human judg?ment|qualitative)\b'
                          r'|\b(?:cannot|can[’\x27]t)\b[^.!?]*(?:check[^.!?]*mechanic|mechanic[^.!?]*check)',
                          text, re.I)):
        return
    for record in records:
        if record.get('type') != 'assistant':
            continue
        content = record['message']['content']
        for block in content if isinstance(content, list) else []:
            if (block.get('type') == 'tool_use' and block.get('name') == 'Bash'
                    and re.search(r'\b(?:running|started)\b', text, re.I)
                    and re.search(r'\bnot (?:yet )?enforced\b', text, re.I)
                    and background_build(block['input'].get('command', ''), event['cwd'])):
                return
            if block.get('type') != 'tool_use' or block.get('name') not in {'Edit', 'Write'}:
                continue
            path = Path(block['input'].get('file_path', ''))
            if path.suffix.lower() == '.md' or any(
                p.lower() in {'memory', 'memories'} for p in path.parts
            ):
                continue
            path = (Path(event['cwd']) / path).resolve()
            relative = enforcement_path(path)
            if path.suffix.lower() != '.md' and (
                relative == '.claude/settings.json' or relative.startswith(
                    ('hooks/', 'tools/', 'tests/', '.github/workflows/', 'mcp-server/src/'))
            ) and not any(p.lower() in {'memory', 'memories'} for p in path.parts):
                return
    if already_blocked(event, boundary, start, mark=True):
        return
    return Verdict.block('Rule claim lacks enforcement evidence: ' + claims[0]
                         + '\nEdit/Write non-Markdown enforcement code in hooks/, tools/, tests/, '
                         '.github/workflows/, .claude/settings.json or mcp-server/src/; '
                         'launch bin/codex-job with a build brief for a hook, gate, test '
                         'or CI check and say running and not yet enforced; or say not enforced '
                         'and explain why it cannot be checked mechanically. '
                         'Guidance files and skills document judgment; machine-enforceable requirements need the relevant code and CI checks in their PR.')


if __name__ == '__main__':
    sys.exit(run(decide, parse_error=parse_error))
