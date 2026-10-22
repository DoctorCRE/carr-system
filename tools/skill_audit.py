"""Audit recorded tool evidence without executing transcript content."""
import ast
import json
from pathlib import Path
import re
import shlex
import warnings


def _json(value):
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except (ValueError, TypeError):
        return value


def _text(value):
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return '\n'.join(_text(item) for item in value)
    if isinstance(value, dict):
        return _text(value.get('text', value.get('content', '')))
    return ''


def _result_objects(value):
    value = _json(value)
    if isinstance(value, list):
        objects = []
        for item in value:
            if _text(item).startswith(('Script completed\n', 'Script running with cell ID')):
                continue
            result = _result_objects(item)
            if not result:
                return []
            objects.extend(result)
        return objects
    if isinstance(value, dict):
        if 'exit_code' in value or 'session_id' in value:
            return [value]
        if 'text' in value or 'content' in value:
            return _result_objects(value.get('text', value.get('content')))
    if isinstance(value, str):
        text = re.sub(r'\AWarning: truncated output \(original token count: \d+\)\nTotal output lines: \d+\n\n', '', value)
        objects = []
        decoder = json.JSONDecoder()
        while text.strip():
            try:
                obj, end = decoder.raw_decode(text.lstrip())
            except ValueError:
                return []
            if not isinstance(obj, dict) or not ('exit_code' in obj or 'session_id' in obj):
                return []
            objects.append(obj)
            text = text.lstrip()[end:]
        return objects
    return []


def _result(value, claude=False, error=False):
    objects = _result_objects(value)
    if len(objects) == 1:
        value = objects[0]
    elif len(objects) > 1:
        return None, '', None
    value = _json(value)
    if isinstance(value, list):
        items = [item for item in value if not _text(item).startswith(('Script completed\n', 'Script running with cell ID'))]
        if len(items) == 1:
            return _result(items[0], claude, error)
        return None, _text(value), None
    if isinstance(value, dict):
        if 'exit_code' in value or 'session_id' in value:
            code = value.get('exit_code')
            return (code if isinstance(code, int) and not isinstance(code, bool) else None,
                    str(value.get('output', '')), value.get('session_id'))
        if 'text' in value:
            return _result(value['text'], claude, error)
        if 'content' in value:
            return _result(value['content'], claude, error or value.get('isError', False))
    text = _text(value)
    match = re.search(r'(?m)^(?:Process exited with code|Exit code)[: ]+(-?\d+)\s*$', text)
    code = int(match[1]) if match else (1 if error else 0 if claude else None)
    return code, text, None


def _js_tokens(script):
    tokens = []
    i = 0
    while i < len(script):
        char = script[i]
        if char.isspace():
            i += 1
        elif script.startswith('//', i):
            end = script.find('\n', i)
            i = len(script) if end < 0 else end + 1
        elif script.startswith('/*', i):
            end = script.find('*/', i + 2)
            i = len(script) if end < 0 else end + 2
        elif char in ('"', "'", '`'):
            quote = char
            end = i + 1
            while end < len(script):
                if script[end] == '\\':
                    end += 2
                elif script[end] == quote:
                    break
                else:
                    end += 1
            raw = script[i:end+1]
            try:
                with warnings.catch_warnings(action='ignore', category=SyntaxWarning):
                    value = json.loads(raw) if quote == '"' else ast.literal_eval(raw) if quote == "'" else raw[1:-1] if '${' not in raw else None
            except (ValueError, SyntaxError):
                value = None
            tokens.append(('string', value))
            i = end + 1
        elif char.isalpha() or char in '_$':
            end = i + 1
            while end < len(script) and (script[end].isalnum() or script[end] in '_$'):
                end += 1
            tokens.append(('word', script[i:end]))
            i = end
        else:
            tokens.append(('symbol', char))
            i += 1
    return tokens


def _js_commands(script):
    tokens = _js_tokens(script)
    commands = []
    for i in range(len(tokens) - 5):
        if tokens[i:i+2] != [('word', 'tools'), ('symbol', '.')]:
            continue
        if tokens[i+2][0] != 'word' or tokens[i+3] != ('symbol', '('):
            continue
        name = tokens[i+2][1]
        if tokens[i+4][0] == 'string':
            commands.extend(_commands(name, tokens[i+4][1]))
            continue
        if tokens[i+4] != ('symbol', '{'):
            continue
        args = {}
        j = i + 5
        while j + 2 < len(tokens) and tokens[j] != ('symbol', '}'):
            key, colon, value = tokens[j:j+3]
            if key[0] not in ('word', 'string') or colon != ('symbol', ':'):
                break
            if value[0] == 'string':
                args[key[1]] = value[1]
                j += 3
            else:
                end = j + 2
                while end < len(tokens) and tokens[end] not in (('symbol', ','), ('symbol', '}')):
                    end += 1
                literal = ''.join(t[1] or '' for t in tokens[j+2:end])
                try:
                    args[key[1]] = json.loads(literal)
                except ValueError:
                    pass
                j = end
            if tokens[j:j+1] == [('symbol', ',')]:
                j += 1
            elif tokens[j:j+1] != [('symbol', '}')]:
                break
        commands.extend(_commands(name, args))
    return commands

def _commands(name, args):
    args = _json(args)
    name = name.rsplit('.', 1)[-1]
    if name == 'exec' and isinstance(args, str):
        return _js_commands(args)
    if name in ('Bash', 'exec_command') and isinstance(args, dict):
        command = args.get('command', args.get('cmd', ''))
        return [(command, name, args)] if isinstance(command, str) and command else []
    if name == 'Read' and isinstance(args, dict):
        return [('Read ' + str(args.get('file_path', args.get('path', ''))), name, args)]
    if name == 'Skill' and isinstance(args, dict):
        return [('Skill ' + str(args.get('skill', args.get('name', ''))), name, args)]
    if name in ('apply_patch', 'Edit', 'Write', 'MultiEdit'):
        return [(args if isinstance(args, str) else json.dumps(args), name, args)]
    if name == 'write_stdin' and isinstance(args, dict):
        return [('', name, args)]
    return []


def load_session(path, start=None, end=None):
    """Normalize Codex or Claude JSONL, keeping only events inside the window."""
    session = {'id': Path(path).stem, 'cwd': '', 'brief': '', 'events': []}
    pending = {}
    running = {}
    with Path(path).open(errors='replace') as handle:
        for order, line in enumerate(handle):
            try:
                row = json.loads(line)
            except ValueError:
                continue
            payload = row.get('payload', {})
            if row.get('type') == 'session_meta' and isinstance(payload, dict):
                session['id'] = payload.get('id', session['id'])
                session['cwd'] = payload.get('cwd', session['cwd'])
            session['cwd'] = row.get('cwd', session['cwd'])
            session['id'] = row.get('sessionId', session['id'])
            timestamp = row.get('timestamp', '')
            if start and timestamp < start or end and timestamp > end:
                continue
            if not isinstance(payload, dict):
                payload = {}
            kind = payload.get('type')
            if kind == 'message':
                role = payload.get('role')
                if role == 'user':
                    message = _text(payload.get('content', ''))
                    session['events'].append(dict(timestamp=timestamp, command='', exit_code=None,
                                                  output='', kind='message', role=role, text=message))
                    session['brief'] += ('\n' if session['brief'] else '') + message
            if row.get('type') in ('assistant', 'user'):
                content = row.get('message', {}).get('content', [])
                if isinstance(content, str):
                    content = [{'type': 'text', 'text': content}]
                for item in content:
                    if not isinstance(item, dict):
                        continue
                    if item.get('type') == 'tool_use':
                        pending[item['id']] = _start(item.get('name', ''), item.get('input', {}),
                                                      timestamp, order, session['events'])
                    elif item.get('type') == 'tool_result':
                        _finish(pending.get(item.get('tool_use_id'), []), item.get('content', ''),
                                timestamp, order, running, claude=True, error=item.get('is_error', False))
                    elif row['type'] == 'user' and item.get('type') == 'text':
                        message = item.get('text', '')
                        session['events'].append(dict(timestamp=timestamp, command='', exit_code=None,
                                                      output='', kind='message', role='user', text=message))
                        session['brief'] += ('\n' if session['brief'] else '') + message
            if kind in ('function_call', 'custom_tool_call'):
                pending[payload.get('call_id')] = _start(payload.get('name', ''),
                    payload.get('arguments', payload.get('input', {})), timestamp, order, session['events'])
            elif kind in ('function_call_output', 'custom_tool_call_output'):
                _finish(pending.get(payload.get('call_id'), []), payload.get('output', ''),
                        timestamp, order, running)
    for event in session['events']:
        if event['kind'] == 'tool':
            event['edits'], _ = _edits(event)
            event['source_edit'] = any(_source(path) for path in event['edits'])
    return session


def _start(name, args, timestamp, order, events):
    result = []
    for command, tool, arguments in _commands(name, args):
        event = dict(timestamp=timestamp, command=command, exit_code=None, output='',
                     kind='tool', tool=tool, arguments=arguments, order=order)
        events.append(event)
        result.append(event)
    return result


def _bounded_output(text, command):
    if len(text) <= 16000:
        return text
    revisions = []
    if re.search(r'\bgit\s+(?:rev-parse|log|show)\b', command):
        revisions = re.findall(r'(?m)^(?:commit )?([0-9a-f]{40})\s*$', text)
    return text[:7800] + '\n[truncated output]\n' + '\n'.join(dict.fromkeys(revisions))[:400] + '\n' + text[-7800:]


def _finish(events, output, timestamp, order, running, claude=False, error=False):
    objects = _result_objects(output)
    for event in events:
        code, text, session_id = _result(output, claude, error)
        if len(events) != 1:
            code = 0 if len(objects) == len(events) and all(type(item.get('exit_code')) is int and item['exit_code'] == 0 for item in objects) else None
            text, session_id = '', None
        if event['tool'] == 'write_stdin':
            original = running.get(event['arguments'].get('session_id'))
            if original is not None:
                original.update(exit_code=code, output=_bounded_output(original['output'] + text, original['command']),
                                completed_order=order, completed_timestamp=timestamp)
            continue
        event.update(exit_code=code, output=_bounded_output(text, event['command']), completed_order=order,
                     completed_timestamp=timestamp)
        if session_id is not None:
            running[session_id] = event



def redact(text):
    """Remove common credential forms before any evidence leaves the engine."""
    text = str(text)
    text = re.sub(r'(?i)([a-z][a-z0-9+.-]*://)[^/\s@]+:[^/\s@]+@', r'\1[REDACTED]@', text)
    text = re.sub(r'\bgithub_pat_[A-Za-z0-9_]{12,}\b|\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b', '[REDACTED]', text)
    text = re.sub(r'''(?i)((?:api[_-]?key|access[_-]?token|secret|password|token)["']?\s*[:=]\s*)(["'])(.*?)\2''', r'\1[REDACTED]', text)
    text = re.sub(r'(?is)-----BEGIN [^-]*PRIVATE KEY-----.*?-----END [^-]*PRIVATE KEY-----', '[REDACTED PRIVATE KEY]', text)
    text = re.sub(r'(?i)(authorization[\s:=]+(?:(?:bearer|basic)\s+)?)[^\s"\']+', r'\1[REDACTED]', text)
    text = re.sub(r'(?i)((?:api[_-]?key|access[_-]?token|secret|password|token)["\']?\s*[:=]\s*)["\']?[^\s"\';,}]+', r'\1[REDACTED]', text)
    text = re.sub(r'\b(?:sk-[A-Za-z0-9_-]{12,}|gh[pousr]_[A-Za-z0-9_]{12,}|AKIA[A-Z0-9]{16})\b', '[REDACTED]', text)
    return text


def _diff_files(diff):
    files = []
    current = None
    for line in diff.splitlines():
        if line.startswith('diff --git '):
            parts = shlex.split(line)
            current = {'path': parts[-1].removeprefix('b/'), 'added': False, 'deleted': False}
            files.append(current)
        elif current is not None:
            if line.startswith('+++ b/'):
                current['path'] = line[6:]
            elif line.startswith('+') and not line.startswith('+++'):
                current['added'] = True
            elif line.startswith('deleted file mode') or line == '+++ /dev/null':
                current['deleted'] = True
    return files


def _source(path):
    path = str(path).lower()
    name = Path(path).name
    if any(part in ('tests', 'test', '__tests__', 'fixtures', 'docs', 'audits') for part in Path(path).parts):
        return False
    if name.startswith(('test_', 'test-')) or re.search(r'[._-](test|spec|selftest)\.', name):
        return False
    return Path(path).suffix in {'.py', '.js', '.jsx', '.ts', '.tsx', '.sh', '.bash', '.zsh',
        '.sql', '.go', '.rs', '.c', '.h', '.cpp', '.cc', '.java', '.swift', '.rb', '.php', '.vue', '.svelte'}


def _shell_parts(command):
    # Heredoc bodies are data, even when they contain plausible shell commands.
    lines = command.splitlines()
    kept = []
    delimiter = None
    for line in lines:
        if delimiter is not None:
            if line.strip() == delimiter:
                delimiter = None
            continue
        kept.append(line)
        match = re.search(r"<<-?\s*['\"]?([A-Za-z_][A-Za-z_0-9]*)", line)
        if match:
            delimiter = match[1]
    try:
        lexer = shlex.shlex('; '.join(kept), posix=True, punctuation_chars=';&|><')
        lexer.whitespace_split = True
        return list(lexer)
    except ValueError:
        return []


def _edits(event):
    if event.get('kind') != 'tool':
        return [], False
    command = event.get('command', '')
    tool = event.get('tool', '')
    if tool == 'apply_patch':
        return re.findall(r'^\*\*\* (?:Add|Update|Delete) File: (.+)$', command, re.M), False
    if tool in ('Edit', 'Write', 'MultiEdit'):
        args = event.get('arguments', _json(command))
        return ([args['file_path']] if isinstance(args, dict) and args.get('file_path') else []), False
    if tool not in ('Bash', 'exec_command', ''):
        return [], False
    tokens = _shell_parts(command)
    paths = []
    commit = False
    for i, token in enumerate(tokens):
        if token in ('tee', 'cp', 'mv') and (i == 0 or tokens[i - 1] in (';', '&&', '||', '|')):
            end = next((j for j in range(i + 1, len(tokens)) if tokens[j] in (';', '&&', '||', '|')), len(tokens))
            operands = [arg for arg in tokens[i + 1:end] if not arg.startswith('-')]
            if token == 'tee':
                paths.extend(operands)
            elif len(operands) >= 2:
                target = operands[-1]
                paths.extend([target] if Path(target).suffix else
                             [str(Path(target) / Path(arg).name) for arg in operands[:-1]])
                if token == 'mv':
                    paths.extend(operands[:-1])
        if token == 'git' and i + 1 < len(tokens) and tokens[i + 1] == 'commit':
            commit = True
        if token in ('>', '>>') and i + 1 < len(tokens):
            paths.append(tokens[i + 1])
        if token in ('sed', 'perl') and any(t.startswith('-i') or 'i' in t[1:] for t in tokens[i+1:] if t.startswith('-')):
            paths.extend(t for t in tokens[i+1:] if '/' in t or Path(t).suffix)
        if token in ('rm', 'touch'):
            paths.extend(t for t in tokens[i+1:] if not t.startswith('-') and t not in (';', '&&', '||'))
        if token == 'git' and i + 1 < len(tokens) and tokens[i + 1] == 'apply':
            paths.append('__unknown_source__.py')
    if tokens and Path(tokens[0]).name.startswith('python'):
        script = tokens[tokens.index('-c')+1] if '-c' in tokens and tokens.index('-c')+1 < len(tokens) else ''
        if '<<' in tokens:
            script = '\n'.join(command.splitlines()[1:-1])
        try:
            with warnings.catch_warnings(action='ignore', category=SyntaxWarning):
                tree = ast.parse(script)
        except SyntaxError:
            tree = None
        if tree:
            path_vars = {}
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and getattr(node.func, 'id', '') == 'open':
                    filename = node.args[0] if node.args else next((kw.value for kw in node.keywords if kw.arg == 'file'), None)
                    mode = node.args[1] if len(node.args) > 1 else next((kw.value for kw in node.keywords if kw.arg == 'mode'), ast.Constant('r'))
                    if not isinstance(mode, ast.Constant) or any(flag in str(mode.value) for flag in 'wax+'):
                        paths.append(filename.value if isinstance(filename, ast.Constant) and isinstance(filename.value, str)
                                     else '__unknown_source__.py')
                if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call) and getattr(node.value.func, 'id', '') == 'Path':
                    if node.value.args and isinstance(node.value.args[0], ast.Constant) and isinstance(node.value.args[0].value, str):
                        for target in node.targets:
                            if isinstance(target, ast.Name):
                                path_vars[target.id] = node.value.args[0].value
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in ('write_text', 'write_bytes', 'unlink'):
                    owner = node.func.value
                    if isinstance(owner, ast.Call) and getattr(owner.func, 'id', '') == 'Path' and owner.args and isinstance(owner.args[0], ast.Constant):
                        paths.append(str(owner.args[0].value))
                    elif isinstance(owner, ast.Name):
                        paths.append(path_vars.get(owner.id, '__unknown_source__.py'))
    return paths, commit


def is_source_edit(event, diff=None):
    return any(_source(path) for path in _edits(event)[0])


def _opened(event):
    if event.get('kind') != 'tool' or event.get('exit_code') != 0:
        return []
    if re.search(r'(?im)^(?:cat|head|sed):.*(?:no such file|permission denied|cannot (?:open|read)|not found)', event.get('output', '')):
        return []
    command = event.get('command', '')
    tool = event.get('tool', '')
    if tool == 'Read':
        return [command.removeprefix('Read ')]
    if tool == 'Skill':
        return ['skill:' + command.removeprefix('Skill ')]
    tokens = _shell_parts(command)
    if any(token in ('||', '|', '&') for token in tokens):
        return []
    segments = [[]]
    separators = [';']
    for token in tokens:
        if token in (';', '&&'):
            segments.append([])
            separators.append(token)
        else:
            segments[-1].append(token)
    opened = []
    previous = []
    reachable = True
    for separator, segment in zip(separators, segments):
        if separator == ';':
            reachable = True
        elif not reachable or not previous or previous[0] not in ('true', 'pwd', 'cat', 'head', 'sed'):
            reachable = False
        previous = segment
        if not reachable:
            continue
        if not segment or segment[0] not in ('cat', 'head', 'sed'):
            continue
        if any(token in ('>', '>>', '<<') for token in segment):
            continue
        if segment[0] == 'sed' and any(token.startswith('-i') for token in segment[1:]):
            continue
        opened.extend(token for token in segment[1:] if token.endswith('/SKILL.md'))
    return opened


def _matches_skill(path, name):
    if path.startswith('skill:'):
        value = path[6:]
        if name == 'tdd':
            return 'mattpocock' in value and value.endswith(':tdd')
        return value.rsplit(':', 1)[-1] == name.replace('_', '-')
    if not path.endswith('/SKILL.md'):
        return False
    leaf = path.rsplit('/', 2)[-2]
    expected = {'prove_it_works': 'principle-prove-it-works'}.get(name, name.replace('_', '-'))
    if leaf != expected:
        return False
    return name != 'tdd' or 'mattpocock' in path


def _evidence(event):
    paths, _ = _edits(event)
    visible_paths = [path for path in paths if re.fullmatch(r'[A-Za-z0-9_./ @+()-]+', path)
                     and not re.match(r'^s[/|#]', path) and not path.endswith('/')]
    command = (event.get('tool', 'shell') + ' edits ' + ', '.join(dict.fromkeys(visible_paths)) + ' [content omitted]'
               if paths else event.get('command', ''))
    result = {'timestamp': event.get('timestamp', ''), 'command': redact(command)}
    if event.get('exit_code') is not None:
        result['exit_code'] = event['exit_code']
    return result



def _test_suite(event):
    if event.get('kind') != 'tool' or event.get('tool', 'Bash') not in ('Bash', 'exec_command', ''):
        return None
    tokens = _shell_parts(event.get('command', ''))
    if any(t in (';', '||', '|', '>', '>>', '<<', '&') for t in tokens):
        return None
    if '&&' in tokens:
        if tokens.count('&&') != 1 or tokens[0] != 'cd':
            return None
        tokens = tokens[tokens.index('&&') + 1:]
    while tokens and ('=' in tokens[0] or tokens[0] == 'env'):
        tokens.pop(0)
    if not tokens:
        return None
    runner = Path(tokens[0]).name
    args = tokens[1:]
    if runner.startswith('python'):
        if args[:2] in (['-m', 'pytest'], ['-m', 'unittest']):
            runner = args[1]
            args = args[2:]
        elif args and (Path(args[0]).name.startswith(('test_', 'test-')) or re.search(r'[._-](test|spec|selftest)\.py$', args[0])):
            return ('python-test', args[0], *args[1:])
        else:
            return None
    if runner == 'node' and '--test' in args:
        return ('node-test', *(arg for arg in args if not arg.startswith('-')))
    if runner in ('pytest', 'unittest'):
        return (runner, *(arg for arg in args if not arg.startswith('-')))
    if runner in ('npm', 'pnpm', 'yarn', 'bun') and args and (args[0] == 'test' or args[:2] == ['run', 'test']):
        return (runner, *args)
    if runner in ('cargo', 'go', 'swift') and args and args[0] == 'test':
        return (runner, *args)
    if runner == 'ci.sh' or runner in ('bash', 'sh') and args and Path(args[0]).name == 'ci.sh':
        return ('ci', *args)
    return None



def _relevant_verification(event, files):
    suite = _test_suite(event)
    targets = [part for part in (suite or ())[1:] if '/' in part or Path(part).suffix in ('.py', '.js', '.ts', '.sh')]
    if suite and not targets:
        return True
    changed = [item['path'] for item in files]
    changed_tests = [path for path in changed if not _source(path) and Path(path).suffix in ('.py', '.js', '.ts', '.sh')]
    if suite:
        if any(target.rstrip('/') == path or target.endswith('/' + path)
               or path.startswith(target.rstrip('/') + '/') for target in targets for path in changed_tests):
            return True
        if changed_tests:
            return False
        stems = [Path(path).stem.removeprefix('test_').removesuffix('-selftest') for path in changed if _source(path)]
        return any(stem in Path(target).stem for stem in stems for target in targets)
    tokens = _shell_parts(event.get('command', ''))
    if any(t in (';', '&&', '||', '|', '>', '>>', '<<', '&') for t in tokens):
        return False
    if tokens and Path(tokens[0]).name in ('python', 'python3', 'node', 'bash', 'sh'):
        tokens = tokens[1:]
    return bool(tokens and any(tokens[0] == path or tokens[0].endswith('/' + path)
                               for path in changed if _source(path)))

def _completed_before(event, index, other, other_index):
    return event.get('completed_order', event.get('order', index)) < other.get('order', other_index)

def audit_events(events, *, diff, brief):
    """Return conservative evidence verdicts for one job and its exact diff."""
    tools = [(i, event) for i, event in enumerate(events) if event.get('kind') == 'tool']
    edits = [(i, event, *_edits(event)) for i, event in tools]
    first_edit = next((i for i, _, paths, commit in edits if paths or commit), len(events))
    source_edits = [(i, event) for i, event, paths, _ in edits if any(_source(p) for p in paths)]
    first_source = source_edits[0][0] if source_edits else len(events)
    files = _diff_files(diff)
    source_files = [item for item in files if _source(item['path'])]
    needs_tdd = any(not item['deleted'] for item in source_files)
    checks = {}
    sizing = re.search(r'(?im)\bsizing\s*[:=]\s*(tiny|small|medium|large|big|xl|huge)\b', brief)
    size = sizing[1].lower() if sizing else None
    for name in ('poteto_mode', 'prove_it_works', 'tdd', 'grill_with_docs', 'codebase_design'):
        limit = first_edit if name in ('poteto_mode', 'prove_it_works') else first_source
        required = name not in ('tdd', 'codebase_design') or needs_tdd
        reason = 'No source addition or modification; documentation or pure deletion.'
        if name == 'grill_with_docs':
            required = size not in ('tiny', 'small')
            reason = 'Explicit tiny/small sizing.'
        if not required:
            checks[name] = dict(verdict='NOT_APPLICABLE', reason=reason, evidence=[])
            continue
        found = []
        for i, event in tools:
            if i >= limit or not any(_matches_skill(path, name) for path in _opened(event)):
                continue
            if limit < len(events) and not _completed_before(event, i, events[limit], limit):
                continue
            found.append(event)
        if name == 'grill_with_docs' and size is None:
            checks[name] = dict(verdict='MISSING', reason='Job sizing is absent or unrecognized.', evidence=[])
        else:
            checks[name] = dict(verdict='VERIFIED' if found else 'MISSING',
                reason='Successful skill open before the required edit.' if found else 'No successful skill open before the required edit.',
                evidence=[_evidence(e) for e in found])
    checks['tdd_behavior'] = dict(verdict='MISSING' if needs_tdd else 'NOT_APPLICABLE',
        reason='No matching red then green test with a source edit between.' if needs_tdd else 'No source addition or modification; documentation or pure deletion.', evidence=[])
    tests = [(i, event, _test_suite(event)) for i, event in tools if _test_suite(event)]
    if needs_tdd:
        for red_i, red, suite in tests:
            if red.get('exit_code') in (0, None):
                continue
            for green_i, green, green_suite in tests:
                if green.get('exit_code') != 0 or green_suite != suite:
                    continue
                between = [edit for edit_i, edit in source_edits if red_i < edit_i < green_i
                           and _completed_before(red, red_i, edit, edit_i)
                           and _completed_before(edit, edit_i, green, green_i)
                           and any(path == item['path'] or path.endswith('/' + item['path'])
                                   for path in _edits(edit)[0] for item in source_files)]
                if between:
                    checks['tdd_behavior'] = dict(verdict='VERIFIED',
                        reason='Same test suite failed, source changed, and the suite passed.',
                        evidence=[_evidence(red), _evidence(between[0]), _evidence(green)])
                    break
            if checks['tdd_behavior']['verdict'] == 'VERIFIED':
                break
    checks['prove_behavior'] = dict(verdict='MISSING', reason='No successful test after the last source edit.', evidence=[])
    if not source_files:
        checks['prove_behavior'] = dict(verdict='NOT_APPLICABLE', reason='No source changes in the diff.', evidence=[])
    elif source_edits:
        last_edit_i, last_edit = source_edits[-1]
        successful = [event for i, event in tools if i > last_edit_i and event.get('exit_code') == 0
                      and _relevant_verification(event, files)
                      and _completed_before(last_edit, last_edit_i, event, i)]
        if successful:
            checks['prove_behavior'] = dict(verdict='VERIFIED', reason='Successful test after the last source edit.',
                                            evidence=[_evidence(successful[-1])])
    return {'verdict': 'MISSING' if any(c['verdict'] == 'MISSING' for c in checks.values()) else 'VERIFIED', 'checks': checks}
