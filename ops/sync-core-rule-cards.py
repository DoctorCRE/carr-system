#!/usr/bin/env python3
"""Render the core cards in CLAUDE.md and AGENTS.md from their shared source."""
import argparse
import re
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from lib.rule_cards import load_cards


def render(text):
    start = text.index('## Always-on rules\n')
    end = list(re.finditer(r'^\[K\d+\].*\n', text, re.M))[-1].end()
    cards = sorted((c for c in load_cards() if c['placement'] == 'CORE'),
                   key=lambda c: c['core_rank'])
    block = ('## Always-on rules\n\n'
             '<!-- Generated from ops/config/rule-cards.v1.json by ops/sync-core-rule-cards.py. -->\n'
             'These twelve core cards are loaded natively with this file. No boot fetch is required.\n'
             'Before acting when a card applies, answer one line with applies yes/no and the action or reason.\n\n')
    block += '\n\n'.join(f"[{c['id']}] {c['title']}: {c['wording']}" for c in cards) + '\n'
    return text[:start] + block + text[end:]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    stale = []
    for name in ('CLAUDE.md', 'AGENTS.md'):
        path = ROOT / name
        text = path.read_text()
        output = render(text)
        if output != text:
            if args.check:
                stale.append(name)
            else:
                path.write_text(output)
    if stale:
        print(f"Stale core cards: {', '.join(stale)}; run python3 ops/sync-core-rule-cards.py")
    return int(bool(stale))


if __name__ == '__main__':
    sys.exit(main())
