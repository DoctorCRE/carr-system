#!/usr/bin/env python3
"""Deliver current store-scoped rules for prompt, tool, and response events."""
from __future__ import annotations
import json
import re
from pathlib import Path
import subprocess
import sys
import time

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
from lib.rule_scope_lookup import lookup, moment_judge
from lib.rule_delivery_preuse import (LOOKUP_RECEIPT_SCHEMA, LOOKUP_INSTRUCTION,
    canonical, digest, receipt_id, source_sha256, valid_local_identity, _claude_user_text)
from lib import rule_routes
from lib.hook_runtime import decision, run

FAILURE = "RULE LOOKUP INCOMPLETE. Rules were not fully delivered. Ordinary actions must wait. Recover through standing-context or the read-only rule verbs; report unusable scope as a data defect."


def _transcript_lines(path):
    """Read newest lines first without dropping tasks outside a fixed tail."""
    with open(path, 'rb') as handle:
        position = handle.seek(0, 2)
        pending = b''
        while position:
            length = min(position, 65536)
            position -= length
            handle.seek(position)
            lines = (handle.read(length) + pending).split(b'\n')
            pending = lines[0]
            yield from reversed(lines[1:])
        if pending:
            yield pending


def task_text(payload):
    for key in ('prompt', 'task'):
        if isinstance(payload.get(key), str):
            return payload[key]
    path = payload.get('transcript_path') or payload.get('transcriptPath')
    if not path:
        return ''
    path = Path(path)
    agent = payload.get('agent_id') or payload.get('agentId')
    if agent:
        if not isinstance(agent, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', agent):
            raise ValueError('invalid_agent_id')
        child_name = f'agent-{agent}.jsonl'
        if path.name != child_name:
            path = path.with_suffix('') / 'subagents' / child_name
    for line in _transcript_lines(path):
        if not line.strip():
            continue
        record = json.loads(line)
        text = _claude_user_text(record)
        part = record.get('payload', {})
        if record.get('type') == 'event_msg' and part.get('type') == 'user_message':
            text = part.get('message')
        if isinstance(text, str) and text:
            if len(text) > 90000:
                raise ValueError('task_too_large')
            return text
    raise ValueError('task_context_unavailable')


def context(text, event):
    return {'hookSpecificOutput': {'hookEventName': event, 'additionalContext': text}}


def refused(event, text=FAILURE):
    if event == 'PreToolUse':
        return {'hookSpecificOutput': {'hookEventName': event,
                'permissionDecision': 'deny', 'permissionDecisionReason': text}}
    return {'decision': 'block', 'reason': text}


def delivery_receipt(payload, selection, task, *, dedupe=True):
    event = payload['hook_event_name']
    session = payload['session_id']
    agent = payload.get('agent_id') or payload.get('agentId')
    tool = payload.get('tool_name', '')
    ids = [r['id'] for r in selection['rules']]
    if dedupe:
        ids = rule_routes.fresh_ids(session, tool or event, ids, agent_id=agent, corpus_digest=selection["corpus_digest"])
    rules = [r for r in selection['rules'] if r['id'] in ids]
    if not rules and selection['complete']:
        return None
    base = {'schema': LOOKUP_RECEIPT_SCHEMA,
            'client': 'codex' if payload.get('turn_id') else 'claude',
            'session_id': session, 'turn_id': payload.get('turn_id') or None,
            'event_name': event, 'tool_use_id': payload.get('tool_use_id'),
            'tool_name': tool, 'tool_input_sha256': digest(payload.get('tool_input', {})),
            'task_sha256': digest(task), 'corpus_digest': selection['corpus_digest'],
            'source_digest': source_sha256(REPO), 'identity': selection['identity'],
            'rule_ids': sorted(ids), 'data_defects': selection['data_defects'],
            'unresolved': selection['unresolved'], 'models': selection['models'],
            'complete': selection['complete'], 'instruction': LOOKUP_INSTRUCTION}
    def render(full, overflow):
        row = dict(base, rules=full, overflow=overflow)
        row['receipt_id'] = receipt_id(row)
        return canonical(row).decode()
    full, overflow = rule_routes.fit_rules(rules, render, always_on=set())
    text = render(full, overflow)
    if not rule_routes.within_cap(text) and not selection['complete']:
        for field in ('data_defects', 'unresolved'):
            grouped = {}
            for issue in selection[field]:
                grouped.setdefault(issue['reason'], []).append(issue['rule_id'][:8])
            base[field] = [{'reason': reason, 'rule_ids': rule_ids}
                           for reason, rule_ids in sorted(grouped.items())]
        full, overflow = rule_routes.fit_rules(rules, render, always_on=set())
        text = render(full, overflow)
    if not rule_routes.within_cap(text):
        if not selection['complete']:
            return context('RULE LOOKUP INCOMPLETE. Full text could not fit the delivery budget. '
                           f"Data defects: {len(selection['data_defects'])}; unresolved judgments: {len(selection['unresolved'])}. "
                           'Fetch matched rule IDs with standing-context: ' + ', '.join(rid[:8] for rid in ids), event)
        return refused(event)
    if dedupe:
        rule_routes.record_delivered(session, tool or event, [r['id'] for r in full], agent_id=agent, corpus_digest=selection["corpus_digest"])
    if event == 'Stop' and rules:
        return refused(event, text)
    return context(text, event)


def process(payload, *, runner=subprocess.run, judge=None, dedupe=True):
    event = payload.get('hook_event_name')
    if event not in {'PreToolUse', 'UserPromptSubmit', 'Stop'}:
        return None
    if event == 'PreToolUse':
        from lib.rule_boot_gate import classify, parse_bash_fetch, READ_ONLY_RULE_VERBS
        if classify(payload.get('tool_name'), payload.get('tool_input'), payload.get('cwd', str(REPO)))[0] != 'other':
            return None
        if (payload.get('tool_name') == 'Bash' and isinstance(payload.get('tool_input'), dict)
                and parse_bash_fetch(payload['tool_input'].get('command'), payload.get('cwd', str(REPO)),
                                     verbs=READ_ONLY_RULE_VERBS) is not None):
            return None
    if not payload.get('session_id') or (event == 'PreToolUse' and not payload.get('tool_use_id')):
        return refused(event)
    deadline = time.monotonic() + 18
    try:
        task = task_text(payload)
        current = {'hook_event_name': event, 'task': task,
                   'tool_name': payload.get('tool_name', ''), 'tool_input': payload.get('tool_input', {})}
        if payload.get('last_assistant_message'):
            current['response'] = payload['last_assistant_message']
        selection = lookup(current, runner=runner,
                           judge=judge or moment_judge(payload['session_id'], deadline=deadline))
        if not valid_local_identity(selection['identity']):
            return refused(event)
        return delivery_receipt(payload, selection, task, dedupe=dedupe)
    except Exception:
        return refused(event)


@decision(failure='raise')
def decide(payload):
    output = process(payload)
    if output is not None:
        print(json.dumps(output, sort_keys=True, separators=(',', ':')))
    return 0


def main():
    from hooks.grok_invocation import grok_session
    if grok_session():
        return 0
    return run(decide)


if __name__ == '__main__':
    raise SystemExit(main())
