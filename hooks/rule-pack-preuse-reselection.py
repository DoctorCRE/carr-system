#!/usr/bin/env python3
"""Deliver current store-scoped rules for prompt, tool, and response events."""
from __future__ import annotations
import fcntl
import hashlib
import json
import os
import re
from pathlib import Path
import subprocess
import sys
import time

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
from lib.rule_scope_lookup import fetch_catalog, select_rules
from lib.rule_delivery_preuse import (LOOKUP_RECEIPT_SCHEMA, LOOKUP_INSTRUCTION, LOOKUP_COMPLETE_INSTRUCTION,
    canonical, digest, receipt_id, source_sha256, valid_local_identity, _claude_user_text)
from lib import rule_cards, rule_routes
from lib.hook_runtime import decision, run

CARD_TOOL = 'rule-cards'
GAP_LOG = REPO / 'out/rule-lookup/selection-gaps.jsonl'

FAILURE = "RULE LOOKUP INCOMPLETE. Rules were not fully delivered. Ordinary actions must wait. Recover through standing-context or the read-only rule verbs; report unusable scope as a data defect."
# The scope catalog is read from the store once per user prompt and reused by that
# prompt's tool calls for at most CATALOG_MAX_SECONDS (WR573.S1.J2): the hook fires
# on every tool call, the corpus changes only when a rule does. A rule changed
# elsewhere reaches a session at its next prompt or within the window, whichever
# comes first, so prompt-less jobs still get stop rules; this session's own rule
# writes clear the cache at once.
RULE_WRITING_VERBS = frozenset({'adopt-rule', 'approve-rule', 'retire-rule', 'bind-rule-context-contract'})
CATALOG_MAX_SECONDS = 600    # a cached catalog older than this is read again
CATALOG_LOCK_SECONDS = 2.0   # bounded wait; past it, read the store directly
CATALOG_MAX_AGE = 86400      # catalog files older than a day are removed on write

UNAVAILABLE = "RULE LOOKUP UNAVAILABLE. The rule store could not be reached for this action; it was not blocked. Recover rules through standing-context if the work depends on them."


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
        if payload.get('turn_id') and path.name.startswith('rollout-'):
            if not path.name.endswith(f'-{agent}.jsonl'):
                raise ValueError('agent_transcript_mismatch')
        elif path.name != child_name:
            path = path.with_suffix('') / 'subagents' / child_name
    for line in _transcript_lines(path):
        if not line.strip():
            continue
        record = json.loads(line)
        text = _claude_user_text(record)
        part = record.get('payload', {})
        if record.get('type') == 'event_msg' and part.get('type') == 'user_message':
            text = part.get('message')
        if record.get('type') == 'response_item' and part.get('type') == 'message' and part.get('role') == 'user':
            # Codex 0.159+ rollouts record user turns only in this shape.
            text = '\n'.join(c.get('text', '') for c in part.get('content') or []
                             if isinstance(c, dict) and c.get('type') == 'input_text')
        if isinstance(text, str) and text:
            if len(text) > 90000:
                raise ValueError('task_too_large')
            return text
    raise ValueError('task_context_unavailable')


def _catalog_root():
    return Path(os.environ.get('CARR_RULE_SCOPE_CACHE_DIR',
                               Path.home() / '.config/carr/claude-rule-delivery/scope-catalog'))


def _catalog_path(session_id):
    return _catalog_root() / (hashlib.sha256(session_id.encode('utf-8')).hexdigest() + '.json')


def _writes_rules(payload):
    """Whether this tool call runs a verb that changes the rule corpus."""
    from lib.rule_boot_gate import _carr_verb
    tool_input = payload.get('tool_input') if isinstance(payload.get('tool_input'), dict) else {}
    verb = _carr_verb(payload.get('tool_name') or '')
    if verb == 'call-verb':
        verb = tool_input.get('verb')
    if verb in RULE_WRITING_VERBS:
        return True
    command = tool_input.get('command') or tool_input.get('cmd') or ''
    return isinstance(command, str) and any(
        re.search(r'run\.sh\s+call\s+' + re.escape(v) + r'\b', command) for v in RULE_WRITING_VERBS)


def _validated(catalog):
    select_rules(catalog, {'tool_name': ''})  # raises on any catalog it would refuse
    return catalog


def _write_catalog(path, catalog):
    temp = path.with_suffix('.tmp')
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as handle:
        json.dump(catalog, handle)
    os.replace(temp, path)
    cutoff = time.time() - CATALOG_MAX_AGE
    for old in path.parent.iterdir():
        try:
            if old != path and old.stat().st_mtime < cutoff:
                old.unlink()
        except OSError:
            pass


def session_catalog(payload, runner):
    """This session's scope catalog: a copy of the sponsor's rule corpus, rule text
    included, kept at 0600 under ~/.config/carr (directory 0700).

    UserPromptSubmit always reads the store; tool calls and Stop reuse what the
    prompt read until it is CATALOG_MAX_SECONDS old. A catalog is written only after it passes select_rules'
    validation, so a refused or failed read is never served. When another hook
    in the session holds the lock past CATALOG_LOCK_SECONDS this call reads the
    store directly instead of queueing behind it.
    """
    path = _catalog_path(payload['session_id'])
    if payload['hook_event_name'] == 'PreToolUse' and _writes_rules(payload):
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        return _validated(fetch_catalog(runner))
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with open(path.with_suffix('.lock'), 'a', encoding='utf-8') as lock:
        deadline = time.monotonic() + CATALOG_LOCK_SECONDS
        while True:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    return _validated(fetch_catalog(runner))
                time.sleep(0.05)
        if payload['hook_event_name'] != 'UserPromptSubmit':
            try:
                if 0 <= time.time() - path.stat().st_mtime < CATALOG_MAX_SECONDS:
                    return json.loads(path.read_text(encoding='utf-8'))
            except (OSError, ValueError):
                pass
        catalog = _validated(fetch_catalog(runner))
        _write_catalog(path, catalog)
        return catalog


def context(text, event):
    return {'hookSpecificOutput': {'hookEventName': event, 'additionalContext': text}}


def refused(event, text=FAILURE):
    if event == 'Stop' and text == FAILURE:
        # A blocked Stop re-opens the turn, and a lookup failure repeats every time: endless loop.
        return None
    if event == 'PreToolUse':
        return {'hookSpecificOutput': {'hookEventName': event,
                'permissionDecision': 'deny', 'permissionDecisionReason': text}}
    return {'decision': 'block', 'reason': text}


def select_cards(payload, inherited_rule_ids, identity, cards, *, corpus_digest, dedupe=True):
    """Fresh PLANNING/TOOL/PROMPT cards for this event, at most three, once per context epoch."""
    session = payload['session_id']
    agent = payload.get('agent_id') or payload.get('agentId')
    keys = {rule_cards.card_key(c['id']): c['id'] for c in cards if c['placement'] != 'CORE'}
    seen = set()
    if dedupe:
        fresh = set(rule_routes.fresh_ids(session, CARD_TOOL, list(keys), agent_id=agent,
                                          corpus_digest=corpus_digest))
        seen = {card_id for key, card_id in keys.items() if key not in fresh}
    event = {k: payload[k] for k in ('hook_event_name', 'tool_name', 'tool_input', 'prompt') if k in payload}
    chosen, _ = rule_cards.routed_cards(event, inherited_rule_ids, seen, cards=cards,
                                        scope=rule_cards.personal_scope(identity))
    return chosen


def apply_cards(payload, selection, *, dedupe=True):
    """Card-covered rules leave the rule receipt and arrive only as cards.

    Fails open: if the cards cannot load, the selection is delivered as before;
    if card selection fails, no cards are added. Neither ever blocks a tool.
    """
    try:
        cards = rule_cards.load_cards()
        covered = rule_cards.covered_rule_ids(cards)
    except Exception:
        return selection, []
    inherited = sorted({r['id'][:8] for r in selection['rules']})
    unresolved = [i for i in selection['unresolved'] if i['rule_id'][:8] not in covered]
    trimmed = dict(selection, rules=[r for r in selection['rules'] if r['id'][:8] not in covered],
                   unresolved=unresolved, complete=not selection['data_defects'] and not unresolved)
    chosen = []
    if payload['hook_event_name'] in rule_cards.ROUTED_EVENTS:
        try:
            chosen = select_cards(payload, inherited, selection['identity'], cards,
                                  corpus_digest=selection['corpus_digest'], dedupe=dedupe)
        except Exception:
            chosen = []
    return trimmed, chosen


def delivery_receipt(payload, selection, task, *, dedupe=True):
    event = payload['hook_event_name']
    session = payload['session_id']
    agent = payload.get('agent_id') or payload.get('agentId')
    tool = payload.get('tool_name', '')
    selection, cards = apply_cards(payload, selection, dedupe=dedupe)
    ids = [r['id'] for r in selection['rules']]
    if dedupe:
        ids = rule_routes.fresh_ids(session, tool or event, ids, agent_id=agent, corpus_digest=selection["corpus_digest"])
    rules = [r for r in selection['rules'] if r['id'] in ids]
    if selection['data_defects'] or selection['unresolved']:
        try:
            GAP_LOG.parent.mkdir(parents=True, exist_ok=True)
            with GAP_LOG.open('a') as handle:
                handle.write(json.dumps({'session_id': session, 'event_name': event,
                    'tool_use_id': payload.get('tool_use_id'), 'corpus_digest': selection['corpus_digest'],
                    'data_defects': selection['data_defects'], 'unresolved': selection['unresolved']},
                    sort_keys=True) + '\n')
        except OSError:
            pass  # Advisory diagnostics must not deny an otherwise valid action.
    if not rules and not cards and event != 'PreToolUse':
        return None
    base = {'schema': LOOKUP_RECEIPT_SCHEMA,
            'client': 'codex' if payload.get('turn_id') else 'claude',
            'session_id': session, 'turn_id': payload.get('turn_id') or None,
            'event_name': event, 'tool_use_id': payload.get('tool_use_id'),
            'tool_name': tool, 'tool_input_sha256': digest(payload.get('tool_input', {})),
            'task_sha256': digest(task), 'corpus_digest': selection['corpus_digest'],
            'source_digest': source_sha256(REPO), 'identity': selection['identity'],
            'rule_ids': sorted(ids), 'data_defects': selection['data_defects'],
            'unresolved': selection['unresolved'],
            'selection_mode': selection['selection_mode'],
            'models': selection['models'],
            'complete': selection['complete'],
            'instruction': LOOKUP_COMPLETE_INSTRUCTION if selection['complete'] else LOOKUP_INSTRUCTION}
    if selection['complete'] and selection['selection_mode'] == 'deterministic':
        for field in ('data_defects', 'unresolved', 'models'):
            del base[field]
        if not rules:
            del base['instruction']
    if cards:
        base['cards'] = [rule_cards.card_view(c) for c in cards]
    def render(full, overflow):
        row = dict(base, rules=full, overflow=overflow)
        row['receipt_id'] = receipt_id(row)
        return canonical(row).decode()
    full, overflow = rule_routes.fit_rules(rules, render, always_on=set())
    text = render(full, overflow)
    if not rule_routes.within_cap(text) and not selection['complete']:
        grouped = {}
        for issue in selection['data_defects']:
            grouped.setdefault(issue['reason'], []).append(issue['rule_id'][:8])
        base['data_defects'] = [{'reason': reason, 'rule_ids': rule_ids}
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
        if cards:
            rule_routes.record_delivered(session, CARD_TOOL, [rule_cards.card_key(c['id']) for c in cards],
                                         agent_id=agent, corpus_digest=selection["corpus_digest"])
    if event == 'Stop' and rules:
        return refused(event, text)
    return context(text, event)


def process(payload, *, runner=subprocess.run, dedupe=True, cache=True):
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
    try:
        task = task_text(payload)
        current = {'hook_event_name': event, 'task': task,
                   'tool_name': payload.get('tool_name', ''), 'tool_input': payload.get('tool_input', {})}
        if payload.get('last_assistant_message'):
            current['response'] = payload['last_assistant_message']
        catalog = session_catalog(payload, runner) if cache else fetch_catalog(runner)
        selection = select_rules(catalog, current)
        if not valid_local_identity(selection['identity']):
            return refused(event)
        return delivery_receipt(payload, selection, task, dedupe=dedupe)
    except RuntimeError as error:
        # A store outage is advisory: never block work on a transient store or network failure.
        # Integrity failures (sponsor mismatch, invalid catalog) still refuse below.
        if str(error) != 'scope_catalog_unavailable':
            return refused(event)
        return None if event == 'Stop' else context(canonical({
            'complete': False, 'reason': 'scope_catalog_unavailable', 'instruction': UNAVAILABLE}).decode(), event)
    except Exception:
        return refused(event)


@decision(failure='raise')
def decide(payload):
    output = process(payload)
    if output is not None:
        text = output.get('hookSpecificOutput', {}).get('additionalContext')
        if text:
            try:
                row = json.loads(text)
            except ValueError:
                row = {}
            if (row.get('schema') == LOOKUP_RECEIPT_SCHEMA and row.get('complete')
                    and not any(row.get(key) for key in ('rules', 'rule_ids', 'overflow', 'cards'))):
                return 0
        print(json.dumps(output, sort_keys=True, separators=(',', ':')))
    return 0


def main():
    return run(decide)


if __name__ == '__main__':
    raise SystemExit(main())
