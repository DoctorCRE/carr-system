#!/usr/bin/env python3
"""Require a registered work queue Card before launching any agent or Codex job."""
import json
import http.client
import os
import signal
from pathlib import Path
import re
import runpy
import sys
import subprocess
import ssl
import stat
from urllib.parse import urlsplit
import time

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lib.hook_runtime import Verdict, decision, run

MINT = """Start with /pstack:poteto-mode as a poteto-agent worker on every provider.
Read ~/.agents/skills/pstack/skills/poteto-mode/SKILL.md on Codex/Grok, or the installed Claude pstack skill.
Use tools/room-bridge/brief-footer.md and include the standing orders from
/Users/booko/.claude/orchestrate/doctorcre-delivery/preferences.md verbatim in remote briefs and every resume.
Mint the job first, then put the returned card_id on its own Card: line in the prompt:
./run.sh call dispatch-job '{"idempotency_key":"<fresh UUID>","work_request":"<work request ref>","slice":1,"executor":"claude","model":"<model>","effort":"<effort>","card_title":"Review assigned change","description":"Review the assigned change."}'"""


T3_PROMPT_FIELDS = {
    'mcp__t3-code__delegate_task': 'task',
    'mcp__t3-code__t3_thread_launch': 'message',
    'mcp__t3-code__t3_thread_send': 'message',
    'mcp__t3-code__t3_thread_send_attachments': 'message',
    'mcp__t3-code__schedule_task': 'prompt',
}


def refuse(reason):
    return Verdict.refuse('agent-card-gate: ' + reason + '\n' + MINT)


@decision(on_error=lambda exc: refuse('Cannot validate launch: ' + str(exc)))
def decide(event):
    if event.get('hook_event_name') != 'PreToolUse':
        return
    inputs = event.get('tool_input', {})
    tool = event.get('tool_name')
    if tool == 'mcp__t3-code__run_scheduled_task_now':
        return refuse('Cannot inspect a stored T3 task prompt. Use a dispatch tool with an inline Card-bearing prompt.')
    if tool == 'mcp__t3-code__t3_pending_request':
        action = inputs.get('action')
        if action in {'list', 'read'}:
            return
        if action != 'respond':
            return refuse('Cannot inspect this T3 pending-request action.')
        answers = inputs.get('answers')
        if not isinstance(answers, dict) or not answers or not all(
                isinstance(answer, str) for answer in answers.values()):
            return refuse('A T3 response requires inspectable string answers with a Card.')
        if len(answers) != 1:
            return refuse('Cannot inspect which stored T3 questions consume these answers. Respond to one question with a Card-bearing answer.')
        prompts = ['\n\n'.join(answers.values())]
    elif tool == 'mcp__t3-code__t3_queue':
        action = inputs.get('action')
        if action == 'promote_to_steer':
            return refuse('Cannot inspect a stored T3 queue prompt. Use t3_thread_send with an inline Card-bearing message.')
        if action in {'list', 'read', 'cancel', 'reorder'}:
            return
        if action != 'edit':
            return refuse('Cannot inspect this T3 queue action.')
        prompts = [inputs.get('text', '')]
    elif tool in T3_PROMPT_FIELDS:
        prompts = [inputs.get(T3_PROMPT_FIELDS[tool], '')]
    elif tool == 'mcp__t3-code__create_threads':
        threads = inputs.get('threads')
        if not isinstance(threads, list) or not threads:
            return refuse('A T3 batch requires threads with Card-bearing prompts.')
        prompts = [thread.get('prompt', '') for thread in threads]
    elif tool == 'Agent':
        # These built-in agents only read and do not dispatch work.
        if inputs.get('subagent_type') in ('web-fetch', 'Explore'):
            return
        prompts = [inputs.get('prompt', '')]
    elif event.get('tool_name') == 'Bash':
        command = inputs.get('command', '')
        if 'codex' not in command:
            return
        invocations = runpy.run_path(str(Path(__file__).with_name('guard-unattended.py')))['executed_invocations']
        prompts = []
        for executable, args in invocations(command):
            if executable != 'codex' or not any(arg in {'exec', 'e'} for arg in args):
                continue
            if '--help' in args or '-h' in args:
                continue
            words = codex_operands(args)
            if words[:2] == ['cloud', 'exec']:
                words = words[1:]
            if words in (['exec', 'resume'], ['e', 'resume']):
                continue
            if words and words[0] in {'exec', 'e'}:
                if len(words) != 2 or words[1] == '-':
                    return refuse('Use one literal inline Codex prompt so its Card can be checked.')
                prompts.append(words[1])
    else:
        return
    return check_prompts(prompts, event.get('cwd'))


VALUE_OPTIONS = frozenset({
    '-c', '--config', '--enable', '--disable', '-m', '--model', '-p', '--profile',
    '-s', '--sandbox', '-C', '--cd', '--add-dir', '-a', '--ask-for-approval',
    '--local-provider', '--remote', '--remote-auth-token-env', '--thread-source',
    '--cyber-access-program', '--output-schema', '--color', '-o', '--output-last-message',
    '-i', '--image', '--env', '--branch', '--attempts',
})
FLAG_OPTIONS = frozenset({
    '--oss', '--strict-config', '--approve-for-me', '--dangerously-bypass-approvals-and-sandbox',
    '--dangerously-bypass-hook-trust', '--worktree', '--search', '--no-alt-screen',
    '--no-daemon', '--skip-git-repo-check', '--ephemeral', '--ignore-user-config',
    '--ignore-rules', '--json',
})


def codex_operands(args):
    words = []
    index = 0
    while index < len(args):
        word = args[index]
        if word == '--':
            return words + args[index + 1:]
        if word in VALUE_OPTIONS:
            if index + 1 >= len(args):
                raise ValueError('Missing Codex option value.')
            index += 2
            continue
        if word.split('=', 1)[0] in VALUE_OPTIONS and '=' in word:
            pass
        elif word in FLAG_OPTIONS:
            pass
        elif word.startswith('-') and word != '-':
            raise ValueError('Cannot inspect Codex option; use a literal inline prompt.')
        else:
            words.append(word)
            if words in (['exec', 'resume'], ['e', 'resume']):
                return words
        index += 1
    return words


def check_prompts(prompts, cwd=None):
    cards = []
    for prompt in prompts:
        found = re.findall(r'^Card:[ \t]*(WR[1-9]\d*\.S[1-9]\d*\.J[1-9]\d*)[ \t]*$', prompt, re.M)
        if len(found) != 1 or len(re.findall(r'^Card:', prompt, re.M)) != 1:
            return refuse('The launch prompt requires one Card: WRn.Sn.Jn line.')
        cards.extend(found)
    if not cards:
        return
    deadline = time.monotonic() + 8
    repository = launch_repository(cwd)
    for card in dict.fromkeys(cards):
        try:
            registration = lookup_registration(card, min(deadline, time.monotonic() + 3))
            if time.monotonic() >= deadline:
                raise TimeoutError
        except (OSError, ValueError, TypeError, KeyError, http.client.HTTPException) as exc:
            reason = 'timeout' if isinstance(exc, TimeoutError) else 'transport or invalid response'
            if isinstance(exc, ValueError) and str(exc) in {'credential', 'endpoint', 'invalid registration'}:
                reason = str(exc)
            if isinstance(exc, ValueError) and re.fullmatch(r'HTTP [1-5]\d\d', str(exc)):
                reason = str(exc)
            return refuse('Card registration lookup unreachable or invalid (' + reason + '). '
                          'Successful online verification is required for ' + card + '; launch blocked.')
        if not registration['registered']:
            return refuse(card + ' is not registered in the work queue.')
        registered_repository = registration['repository']
        if registered_repository is not None:
            if repository is None or repository_name(registered_repository) != repository:
                return refuse(card + ' is registered for a different repository.')


def repository_name(value):
    if not isinstance(value, str):
        return None
    match = re.fullmatch(r'(?:https://github\.com/|ssh://git@github\.com/|git@github\.com:)?'
                         r'([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+?)(?:\.git)?/?', value)
    return match[1].lower() if match else None


def launch_repository(cwd=None):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'ops'))
    from git_env import scrubbed_env

    try:
        result = subprocess.run(['git', '-C', str(cwd or Path.cwd()), 'remote', 'get-url', 'origin'],
                                capture_output=True, text=True, timeout=0.5, env=scrubbed_env())
        return repository_name(result.stdout.strip()) if result.returncode == 0 else None
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


TOKEN_VARIABLES = {
    'local': 'CARR_MCP_LOCAL_TOKEN',
    'unattended': 'CARR_MCP_UNATTENDED_TOKEN',
    'codex-continuity': 'CARR_CODEX_CONTINUITY_MCP_TOKEN',
    'hermes-projector': 'CARR_HERMES_MCP_TOKEN',
    'hermes-cos': 'CARR_HERMES_COS_MCP_TOKEN',
}


def credential():
    path = os.environ.get('CARR_MCP_ENV') or str(Path.home() / '.config/carr/mcp-tokens.env')
    raw = ''
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        pass
    else:
        with os.fdopen(fd) as source:
            info = os.fstat(source.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o777 != 0o600:
                raise ValueError('credential')
            raw = source.read()
    variable = TOKEN_VARIABLES.get(os.environ.get('CARR_MCP_CLIENT_PROFILE') or 'local')
    if variable is None:
        raise ValueError('credential')
    token = os.environ.get(variable)
    if not token:
        for line in raw.split('\n'):
            line = line.strip()
            if line.startswith(variable + '='):
                token = re.sub(r"^['\"]|['\"]$", '', line[len(variable) + 1:].strip())
                break
    if not token:
        raise ValueError('credential')
    return token


def lookup_registration(card, deadline):
    def expired(*args):
        raise TimeoutError

    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError
    previous_handler = signal.getsignal(signal.SIGALRM)
    previous_timer = signal.getitimer(signal.ITIMER_REAL)
    started = time.monotonic()
    connection = None
    signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, remaining)
    try:
        token = credential()
        url = urlsplit(os.environ.get('CARR_MCP_URL') or 'https://api.doctorcre.com/mcp')
        if url.scheme != 'https' or not url.hostname or url.username or url.password or url.fragment:
            raise ValueError('endpoint')
        connection = http.client.HTTPSConnection(url.hostname, url.port, timeout=remaining,
                                                 context=ssl.create_default_context())
        payload = {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
                   'params': {'name': 'job-card-registration', 'arguments': {'card_id': card}}}
        connection.request('POST', (url.path or '/') + ('?' + url.query if url.query else ''),
                           body=json.dumps(payload), headers={'Content-Type': 'application/json',
                                                             'Authorization': 'Bearer ' + token})
        response = connection.getresponse()
        if response.status != 200:
            raise ValueError('HTTP ' + str(response.status))
        raw = response.read(65537)
        if len(raw) > 65536:
            raise ValueError('invalid registration')
        envelope = json.loads(raw)
        result = envelope['result']
        if envelope.get('error') is not None or result.get('isError'):
            raise ValueError('invalid registration')
        registration = json.loads(result['content'][0]['text'])
        if (not isinstance(registration, dict) or registration.get('ok') is not True
                or registration.get('card_id') != card
                or type(registration.get('registered')) is not bool):
            raise ValueError('invalid registration')
        if registration['registered'] and (
                not all(isinstance(registration.get(key), str) and registration[key]
                        for key in ('executor', 'model', 'work_request_state'))
                or 'repository' not in registration
                or not (registration['repository'] is None or isinstance(registration['repository'], str))):
            raise ValueError('invalid registration')
        if time.monotonic() >= deadline:
            raise TimeoutError
        return registration
    except (IndexError, AttributeError, UnicodeError) as exc:
        raise ValueError('invalid registration') from exc
    finally:
        if connection is not None:
            connection.close()
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)
        if previous_timer[0]:
            signal.setitimer(signal.ITIMER_REAL, max(0.000001, previous_timer[0] -
                                                   (time.monotonic() - started)), previous_timer[1])


if __name__ == '__main__':
    sys.exit(run(decide, parse_error=lambda exc: refuse('Invalid hook input.')))
