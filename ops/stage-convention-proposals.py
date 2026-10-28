#!/usr/bin/env python3
"""Stage the rule cards and uncovered ORG conventions as team-memory proposals.

WR-000562 S4 (docs/memory/team-memory-design.md D1, D16). Each of the 60 cards
in ops/config/rule-cards.v1.json and each uncovered row of
ops/config/convention-proposal-ledger.v1.json becomes one team-memory section
under conventions/proposed/. Partners adopt them in person through
governance-queue and adopt-rule; staging binds nobody.

    python3 ops/stage-convention-proposals.py --print
    python3 ops/stage-convention-proposals.py --apply --attach-id <attach_id>

--apply writes through ./run.sh call with the caller's own read_write attach.
It skips keys already in team-memory, active or retired (adopted), so a rerun
after adoptions stages nothing new and revives nothing.
"""
import argparse
import json
from pathlib import Path
import subprocess
import uuid
import sys

ROOT = Path(__file__).resolve().parents[1]
CARDS = ROOT / 'ops/config/rule-cards.v1.json'
LEDGER = ROOT / 'ops/config/convention-proposal-ledger.v1.json'
PREFIX = 'conventions/proposed/'
STATUS = ('Status: a proposal. It binds nobody until a partner adopts it in person '
          'through governance-queue (adopt-rule).')


def body(statement, card=None, source=''):
    if '\n' in statement or not statement.strip():
        raise ValueError(f'a convention statement is one non-empty line: {statement!r}')
    lines = [f'Convention: {statement.strip()}']
    if card is not None:
        lines.append('Card: ' + json.dumps(card, ensure_ascii=False, separators=(',', ':')))
    return '\n'.join(lines + [f'Source: {source}', STATUS])


def plan(cards_path=CARDS, ledger_path=LEDGER):
    """Every proposal as {section_key, title, body_text}, cards first."""
    out = []
    for card in json.loads(Path(cards_path).read_text())['cards']:
        meta = {k: v for k, v in card.items() if k not in ('wording', 'word_count')}
        sources = ', '.join(card.get('source_rule_ids') or []) or 'none'
        out.append({'section_key': f"{PREFIX}card-{card['id'].lower()}",
                    'title': f"Card {card['id']}: {card['title']}",
                    'body_text': body(card['wording'], meta,
                                      f"rule card {card['id']} in ops/config/rule-cards.v1.json; cited rules {sources}")})
    for name, row in json.loads(Path(ledger_path).read_text())['files'].items():
        if row['covered_by']:
            continue
        out.append({'section_key': f'{PREFIX}{name}', 'title': f'Convention: {name}',
                    'body_text': body(row['statement'], None,
                                      f'{row["class"]} file {name} of the frozen private memory folder')})
    keys = [p['section_key'] for p in out]
    if len(keys) != len(set(keys)):
        raise ValueError('duplicate proposal keys')
    return out


class VerbRefused(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def stage(call, attach_id, proposals):
    """Write each proposal not yet in team-memory. call(verb, args) -> dict.

    Active keys are skipped from the snapshot. A key that exists only as a
    retired section (an adopted or withdrawn proposal) is absent from the
    snapshot, so its base_version 0 write returns version_conflict; that key
    is skipped too, and a retired proposal is never revived.
    """
    present = {s['section_key'] for s in call('read-doctrine', {'document': 'team-memory'})['sections']}
    written, retired = [], []
    for p in proposals:
        if p['section_key'] in present:
            continue
        try:
            call('write-doctrine-section', {
                'idempotency_key': str(uuid.uuid4()), 'document': 'team-memory',
                'section_key': p['section_key'], 'title': p['title'], 'body_text': p['body_text'],
                'base_version': 0, 'attach_id': attach_id})
        except VerbRefused as refusal:
            if refusal.code != 'version_conflict':
                raise
            retired.append(p['section_key'])
            continue
        written.append(p['section_key'])
    return written, retired


def run_sh_call(verb, args):
    done = subprocess.run([str(ROOT / 'run.sh'), 'call', verb, json.dumps(args)], cwd=ROOT,
                          capture_output=True, text=True, timeout=120)
    if done.returncode:
        marker = done.stderr.find('TOOL ERROR ')
        if marker < 0:
            raise RuntimeError(f'{verb} failed: {done.stderr.strip()[-500:]}')
        raise VerbRefused(json.loads(done.stderr[marker + len('TOOL ERROR '):].split('\n}', 1)[0] + '\n}')['error'])
    result = json.loads(done.stdout[done.stdout.index('{'):])
    return result.get('result', result)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--print', action='store_true', help='print the proposals as JSON')
    mode.add_argument('--apply', action='store_true', help='write missing proposals to team-memory')
    parser.add_argument('--attach-id', help='your read_write attach_id from attach-memory (required with --apply)')
    args = parser.parse_args()
    proposals = plan()
    if args.print:
        print(json.dumps(proposals, indent=2, ensure_ascii=False))
        return 0
    if not args.attach_id:
        parser.error('--apply needs --attach-id')
    written, retired = stage(run_sh_call, args.attach_id, proposals)
    print(f'staged {len(written)} of {len(proposals)} proposals; '
          f'{len(retired)} skipped as already adopted or retired')
    return 0


if __name__ == '__main__':
    sys.exit(main())
