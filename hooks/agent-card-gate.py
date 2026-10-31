#!/usr/bin/env python3
"""Require a registered work queue Card before launching any agent or Codex job."""
import json
import os
import signal
from pathlib import Path
import re
import runpy
import sys
import subprocess
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lib.hook_runtime import Verdict, decision, run

MINT = """Mint the job first, then put the returned card_id on its own Card: line in the prompt:
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
            words = codex_operands(args)
            if words in (['exec', 'resume'], ['e', 'resume']):
                continue
            if words and words[0] in {'exec', 'e'}:
                if len(words) != 2 or words[1] == '-':
                    return refuse('Use one literal inline Codex prompt so its Card can be checked.')
                prompts.append(words[1])
    else:
        return
    return check_prompts(prompts)


VALUE_OPTIONS = frozenset({
    '-c', '--config', '--enable', '--disable', '-m', '--model', '-p', '--profile',
    '-s', '--sandbox', '-C', '--cd', '--add-dir', '-a', '--ask-for-approval',
    '--local-provider', '--remote', '--remote-auth-token-env', '--thread-source',
    '--cyber-access-program', '--output-schema', '--color', '-o', '--output-last-message',
    '-i', '--image',
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


def registration_cache(registered=None):
    path = Path.home() / '.cache/carr/agent-card-registrations.json'
    temporary = None
    try:
        if registered is None:
            entries = json.loads(path.read_text())
            now = time.time()
            return {card for card, seen in entries.items()
                    if isinstance(seen, (int, float)) and 0 <= now - seen < 24 * 60 * 60}
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode='w', dir=path.parent, delete=False) as output:
            temporary = Path(output.name)
            json.dump(dict.fromkeys(registered, time.time()), output)
        temporary.replace(path)
    except (OSError, ValueError, AttributeError, TypeError):
        return set()
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return set()


def check_prompts(prompts):
    cards = []
    for prompt in prompts:
        found = re.findall(r'^Card:[ \t]*(WR[1-9]\d*\.S[1-9]\d*\.J[1-9]\d*)[ \t]*$', prompt, re.M)
        if len(found) != 1:
            return refuse('The launch prompt requires one Card: WRn.Sn.Jn line.')
        cards.extend(found)
    if not cards:
        return
    try:
        with subprocess.Popen([sys.executable, str(Path(__file__).resolve().parent.parent / 'tools/call-verb.py'),
                               'work-queue', '{}'], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              text=True, stdin=subprocess.DEVNULL, start_new_session=True) as lookup:
            try:
                stdout, _ = lookup.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(lookup.pid, signal.SIGKILL)
                lookup.communicate()
                raise
            if lookup.returncode:
                raise ValueError('queue call failed')
        queue = json.loads(stdout)
        if not isinstance(queue, dict) or queue.get('ok') is not True or not isinstance(queue.get('jobs'), list):
            raise ValueError('invalid queue response')
        registered = {job['card_id'] for job in queue['jobs']}
        if not all(isinstance(card, str) for card in registered):
            raise ValueError('invalid queue Card')
    except (OSError, ValueError, TypeError, KeyError, subprocess.SubprocessError) as exc:
        cached = registration_cache()
        if all(card in cached for card in cards):
            return
        unseen = ', '.join(card for card in cards if card not in cached)
        return refuse('Work queue lookup unreachable or invalid (' + type(exc).__name__ + '). '
                      'No registration cached within 24 hours for ' + unseen + '.')
    registration_cache(registered)
    for card in cards:
        if card not in registered:
            return refuse(card + ' is not registered in the work queue.')


if __name__ == '__main__':
    sys.exit(run(decide, parse_error=lambda exc: refuse('Invalid hook input.')))
