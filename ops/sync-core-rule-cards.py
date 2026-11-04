#!/usr/bin/env python3
"""Render shared core cards and validate paths in AGENTS.md and CLAUDE.md.

CLAUDE.md imports AGENTS.md, so it carries no cards of its own.

--from-store validates the complete store projection, then rewrites
ops/config/rule-cards.v1.json from adopted rule cards in the record layer (the rule-cards verb, WR-000562 D16), so the
always-on block renders from the store. Missing metadata or omissions without
retirement receipts refuse before any write. --check stays offline for CI: it fails
when AGENTS.md drifts from the cards file or CLAUDE.md stops importing it.
"""
import argparse
import json
import re
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from lib.rule_cards import CardSelector, load_cards


ATTACH_SOURCE = ROOT / 'mcp-server/src/doctrine.js'
CARDS = ROOT / 'ops/config/rule-cards.v1.json'


def read_store(verb, arguments):
    out = subprocess.run([str(ROOT / 'run.sh'), 'call', verb, json.dumps(arguments)], cwd=ROOT,
                         capture_output=True, text=True, check=True, timeout=120).stdout
    result = json.loads(out[out.index('{'):])
    result = result.get('result', result)
    if not result.get('ok'):
        raise ValueError(f"store read failed: {verb}")
    return result


def store_cards():
    return read_store('rule-cards', {})['cards']


def read_retirements(card):
    return read_store('find-rule', {'text': card['wording'], 'status': 'retired',
                                    'limit': 100})['rules']


def cards_from_store(document, adopted, retirement_reader=None):
    """Validate the complete store projection before replacing any local cards."""
    if not isinstance(adopted, list):
        raise ValueError('rule-cards: cards must be a list')
    errors = []
    ids = set()
    for index, card in enumerate(adopted):
        if not isinstance(card, dict):
            errors.append(f'card {index}: expected object')
            continue
        label = card.get('id', f'card {index}')
        fields = {'id': str, 'title': str, 'wording': str, 'source_rule_ids': list,
                  'acknowledgement': str, 'scope': str, 'placement': str, 'triggers': dict}
        if card.get('placement') == 'CORE':
            fields['core_rank'] = int
        else:
            fields['delivery_priority'] = int
        invalid = [key for key, kind in fields.items()
                   if not isinstance(card.get(key), kind) or isinstance(card.get(key), bool)
                   or (kind != int and not card.get(key))]
        if invalid:
            errors.append(f"{label}: missing or invalid fields: {', '.join(invalid)}")
            continue
        if card['id'] in ids:
            errors.append(f"{label}: duplicate card id")
        ids.add(card['id'])
        if card['placement'] not in ('CORE', 'TOOL', 'PROMPT', 'PLANNING'):
            errors.append(f"{label}: invalid placement")
        if card['scope'] not in ('shared', 'joe-personal', 'dell-personal'):
            errors.append(f"{label}: invalid scope")
        if any(not isinstance(rid, str) or not re.fullmatch(r'[0-9a-f]{8}', rid)
               for rid in card['source_rule_ids']):
            errors.append(f"{label}: invalid source_rule_ids")
        if card['placement'] != 'CORE':
            try:
                if not isinstance(card['triggers'].get('matchers'), list) or not card['triggers']['matchers']:
                    raise ValueError('expected nonempty matchers list')
                if any(not isinstance(matcher, dict) for matcher in card['triggers']['matchers']):
                    raise ValueError('expected matcher objects')
                CardSelector([card], personal_scope=card['scope'])
            except (KeyError, TypeError, ValueError, re.error) as error:
                errors.append(f"{label}: invalid triggers.matchers: {error}")
    projected_ids = {row['id'] for row in adopted
                     if isinstance(row, dict) and isinstance(row.get('id'), str)}
    missing = [card for card in document['cards'] if card['id'] not in projected_ids]
    metadata_valid = not errors
    for card in missing:
        records = retirement_reader(card) if retirement_reader and metadata_valid else []
        retired = {row['id'][:8] for row in records
                   if row.get('status') == 'retired' and (row.get('retirement') or {}).get('receipt_id')}
        unproven = sorted(set(card['source_rule_ids']) - retired)
        if unproven:
            errors.append(f"{card['id']}: omitted without retirement receipts for {', '.join(unproven)}")
    if errors:
        raise ValueError('Refusing incomplete rule-cards projection; nothing written:\n' + '\n'.join(errors))
    order = {card['id']: index for index, card in enumerate(document['cards'])}
    cards = [{k: v for k, v in card.items() if k != 'rule_id'} for card in adopted]
    cards.sort(key=lambda card: (order.get(card['id'], len(order)), card['id']))
    return {**document, 'cards': cards}


def attach_line():
    source = ATTACH_SOURCE.read_text()
    return json.loads(re.search(r'^export const MEMORY_ATTACH_LINE = (".+");$', source, re.M)[1])


def render(text):
    start = text.index('## Always-on rules\n')
    end = list(re.finditer(
        r'^\[K\d+\].*\n|^Before acting when a card applies, answer one line with applies yes/no and the action or reason\.\n',
        text, re.M))[-1].end()
    cards = sorted((c for c in load_cards() if c['placement'] == 'CORE'),
                   key=lambda c: c['core_rank'])
    block = ('## Always-on rules\n\n'
             '<!-- Generated from mcp-server/src/doctrine.js and ops/config/rule-cards.v1.json by ops/sync-core-rule-cards.py. -->\n'
             f'{attach_line()}\n\n'
             'These twelve core cards are loaded natively with this file.\n'
             'Before acting when a card applies, answer one line with applies yes/no and the action or reason.\n\n')
    block += '\n\n'.join(f"[{c['id']}] {c['title']}: {c['wording']}" for c in cards) + '\n'
    return text[:start] + block.rstrip() + '\n' + text[end:]


def missing_paths(text):
    # Store statements describe multiple machines; their paths are not repository links.
    text = re.sub(r'(?ms)^<!-- carr-native-rules:start -->\n.*?^<!-- carr-native-rules:end -->\n?',
                  '', text)
    references = {path.lstrip('/') for pair in re.findall(
        r'\[[^]\n]*\]\(([^)\s]+)\)|^\s*\[[^]\n]+\]:\s*<?([^\s>]+)',
        text, re.M) for path in pair if path}
    references.update(path for path in re.findall(r'`([^`\s]+/[^`\s]+)`', text)
                      if not path.startswith(('/', '~')))
    text = re.sub(r'https?://[^\s`<>]+|(?<![\w.])(?:~/|/)[^\s`]+', '', text)
    references.update(re.findall(
        r'(?<![\w/.-])(?:[\w.-]+/)+[\w.-]+\.[a-zA-Z][a-zA-Z0-9]*\b', text))
    roots = '|'.join(re.escape(p.name) for p in ROOT.iterdir() if p.is_dir())
    references.update(re.findall(
        rf'(?<![\w/.-])(?:\./)?(?:{roots})/[^\s`\[\]()]+', text))
    references.update(re.findall(r'(?<![\w/.-])(?:[\w.-]+/)*[\w.-]+\.(?:md|py|sh|json|yml|yaml|toml)\b', text))
    missing = []
    while references:
        reference = references.pop().split('#', 1)[0].rstrip('.,;:')
        if not reference or re.match(r'(?:[a-z]+:|[~/])', reference):
            continue
        brace = re.search(r'\{([^{}]+)\}', reference)
        if brace:
            references.update(reference[:brace.start()] + choice + reference[brace.end():]
                              for choice in brace[1].split(','))
            continue
        if any(char in reference for char in '<>*$'):
            continue
        if not (ROOT / reference).exists():
            missing.append(reference)
    return sorted(set(missing))


def imports_agents(text):
    return text.split('\n', 1)[0] == "@AGENTS.md"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--check', action='store_true')
    mode.add_argument('--from-store', action='store_true')
    args = parser.parse_args()
    if args.from_store:
        try:
            document = cards_from_store(json.loads(CARDS.read_text()), store_cards(), read_retirements)
        except (ValueError, KeyError, OSError, subprocess.SubprocessError) as error:
            print(str(error), file=sys.stderr)
            return 1
        CARDS.write_text(json.dumps(document, indent=2, ensure_ascii=False) + '\n')
    stale = []
    broken = []
    for name in ('CLAUDE.md', 'AGENTS.md'):
        broken.extend(f"{name}: {path}" for path in missing_paths((ROOT / name).read_text()))
    path = ROOT / 'AGENTS.md'
    text = path.read_text()
    output = render(text)
    if output != text:
        if args.check:
            stale.append('AGENTS.md')
        else:
            path.write_text(output)
    unimported = not imports_agents((ROOT / 'CLAUDE.md').read_text())
    if unimported:
        print('CLAUDE.md must begin with the line @AGENTS.md')
    if stale:
        print(f"Stale core cards: {', '.join(stale)}; run python3 ops/sync-core-rule-cards.py")
    for reference in broken:
        print(f"Missing guidance path: {reference}")
    return int(bool(stale or broken or unimported))


if __name__ == '__main__':
    sys.exit(main())
