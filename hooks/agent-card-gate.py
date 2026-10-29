#!/usr/bin/env python3
"""Require a registered work queue Card before launching an agent."""
import json
import os
import signal
from pathlib import Path
import re
import runpy
import sys
import subprocess

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lib.hook_runtime import Verdict, decision, run

MINT = """Mint the job first, then put the returned card_id on its own Card: line in the prompt:
./run.sh call dispatch-job '{"idempotency_key":"<fresh UUID>","work_request":"<work request ref>","slice":1,"executor":"claude","model":"<model>","effort":"<effort>","card_title":"Agent review","description":"Review the assigned change."}'"""


def refuse(reason):
    return Verdict.refuse('agent-card-gate: ' + reason + '\n' + MINT)


@decision(on_error=lambda exc: refuse('Cannot validate launch: ' + str(exc)))
def decide(event):
    if event.get('hook_event_name') != 'PreToolUse':
        return
    inputs = event.get('tool_input', {})
    if event.get('tool_name') == 'Agent':
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
            raise ValueError('Cannot inspect Codex option; use bin/codex-job or a literal inline prompt.')
        else:
            words.append(word)
        index += 1
    return words


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
        if queue.get('ok') is not True or not isinstance(queue.get('jobs'), list):
            raise ValueError('invalid queue response')
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        return refuse('Work queue lookup unreachable or invalid (' + type(exc).__name__ + ').')
    registered = {job['card_id'] for job in queue['jobs']}
    for card in cards:
        if card not in registered:
            return refuse(card + ' is not registered in the work queue.')


if __name__ == '__main__':
    sys.exit(run(decide, parse_error=lambda exc: refuse('Invalid hook input.')))
