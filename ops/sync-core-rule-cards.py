#!/usr/bin/env python3
"""Render the attach line and core cards, and validate repository paths in CLAUDE.md and AGENTS.md.

--from-store first rewrites ops/config/rule-cards.v1.json from the adopted
rule cards in the record layer (the rule-cards verb, WR-000562 D16), so the
always-on block renders from the store. --check stays offline for CI: it fails
when CLAUDE.md or AGENTS.md drifts from the cards file.
"""
import argparse
import json
import re
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from lib.rule_cards import load_cards


ATTACH_SOURCE = ROOT / 'mcp-server/src/doctrine.js'
CARDS = ROOT / 'ops/config/rule-cards.v1.json'


def store_cards():
    out = subprocess.run([str(ROOT / 'run.sh'), 'call', 'rule-cards', '{}'], cwd=ROOT,
                         capture_output=True, text=True, check=True, timeout=120).stdout
    result = json.loads(out[out.index('{'):])
    return result.get('result', result)['cards']


def cards_from_store(document, adopted):
    """The cards file with its cards replaced by the adopted ones, in file order."""
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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--check', action='store_true')
    mode.add_argument('--from-store', action='store_true')
    args = parser.parse_args()
    if args.from_store:
        document = cards_from_store(json.loads(CARDS.read_text()), store_cards())
        CARDS.write_text(json.dumps(document, indent=2, ensure_ascii=False) + '\n')
    stale = []
    broken = []
    for name in ('CLAUDE.md', 'AGENTS.md'):
        path = ROOT / name
        text = path.read_text()
        output = render(text)
        broken.extend(f"{name}: {path}" for path in missing_paths(text))
        if output != text:
            if args.check:
                stale.append(name)
            else:
                path.write_text(output)
    if stale:
        print(f"Stale core cards: {', '.join(stale)}; run python3 ops/sync-core-rule-cards.py")
    for reference in broken:
        print(f"Missing guidance path: {reference}")
    return int(bool(stale or broken))


if __name__ == '__main__':
    sys.exit(main())
