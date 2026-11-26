"""Outlook recovery dry run: re-read old engine emails from a store copy to fill their split.

Step 2 of "One-time recovery from Outlook" in the engine's docs/new-database.md. Input is the
private candidates CSV that the engine's schema/email-text-recovery-candidates.sql writes, and a
copy of one mailbox's HxStore.hxd with its Files folder, never the live profile. hxprobe decodes
the copy, and its body/split.rs split is the same one intake sends for new mail. Each `locator`
candidate whose one store item matches its sender, sent time and subject, and the same Message-ID
when the candidate has one, becomes a row of the load file for schema/recover-email-text.sql:

    python3 -m lib.outlook_email_recovery --candidates FILE.csv --store COPY/HxStore.hxd \\
        --files COPY/Files --mailbox ADDRESS --out PRIVATE/load.sql

The load file holds email text, so it is written 0600 and refused inside a git checkout.
Nothing reaches Outlook or a database. Stdout is only a count per outcome.
"""
from __future__ import annotations

import argparse
from collections import Counter
import csv
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import sqlite3
import sys
import tempfile

from lib import outlook_intake as intake
from lib.engine_mail import decoded_email_identity
from lib.outlook_settings import load_settings

LOCATOR = 'hxstore-key:'
# The input schema/recover-email-text.sql reads.
LOAD_TABLE = ('create temporary table email_text_recovery (activity_id uuid, user_id uuid, sender text, '
              'happened_at text, subject text, body_sha256 text, authored text, history text, message_id text);')


@dataclass(frozen=True)
class Candidate:
    activity_id: str
    user_id: str
    sender: str
    happened_at: str
    subject: str
    body_sha256: str
    message_id: str
    mailbox_account: str
    item_locator: str
    reason: str


@dataclass(frozen=True)
class Item:
    sender: str
    subject: str
    message_id: str | None
    sent_unix: int | None
    complete: bool
    authored: str | None
    history: str | None


def read_candidates(path: Path) -> list[Candidate]:
    names = Candidate.__dataclass_fields__.keys()
    with path.open(newline='', encoding='utf-8') as handle:
        return [Candidate(**{name: row.get(name) or '' for name in names}) for row in csv.DictReader(handle)]


def read_items(db: Path) -> dict[str, list[Item]]:
    """Decoded store items by store-local key."""
    con = sqlite3.connect(f'file:{db}?mode=ro', uri=True)
    con.row_factory = sqlite3.Row
    try:
        items: dict[str, list[Item]] = {}
        for row in con.execute('SELECT sender, subject, message_id, sent_unix, body, body_kind, body_completeness, '
                               'local_keys, authored, history FROM messages'):
            mail = dict(row)
            item = Item(sender=(mail['sender'] or '').strip().lower(), subject=(mail['subject'] or '').strip(),
                        message_id=decoded_email_identity(mail).get('message_id'), sent_unix=mail['sent_unix'],
                        complete=intake._body_complete(mail), authored=mail['authored'], history=mail['history'])
            for key in set((mail['local_keys'] or '').split(',')) - {''}:
                items.setdefault(key, []).append(item)
        return items
    finally:
        con.close()


def outcome(candidate: Candidate, items: dict[str, list[Item]], mailbox: str) -> tuple[str, Item | None]:
    """Classify one candidate; only `verified` carries the item to load."""
    if candidate.reason != 'locator':
        return candidate.reason or 'no_reason', None
    if candidate.mailbox_account.lower() != mailbox.lower():
        return 'other_mailbox', None
    if not candidate.item_locator.startswith(LOCATOR):
        return 'item_missing', None
    keys = set(candidate.item_locator.removeprefix(LOCATOR).split(',')) - {''}
    found = [item for key in sorted(keys) for item in items.get(key, [])]
    if not found:
        return 'item_missing', None
    if len(found) > 1:
        return 'item_ambiguous', None
    item = found[0]
    if item.sender != candidate.sender.strip().lower():
        return 'sender_differs', None
    if item.sent_unix is None or datetime.fromisoformat(candidate.happened_at).timestamp() != item.sent_unix:
        return 'sent_time_differs', None
    if item.subject != candidate.subject.strip():
        return 'subject_differs', None
    known = decoded_email_identity({'message_id': candidate.message_id}).get('message_id')
    stored = decoded_email_identity({'message_id': item.message_id}).get('message_id')
    if known and not stored:
        return 'message_id_missing', None
    if known and stored != known:
        return 'message_id_differs', None
    if not item.complete:
        return 'body_incomplete', None
    if item.authored is None or item.history is None:
        return 'no_split', None
    return 'verified', item


def _literal(value: str | None) -> str:
    return 'null' if value is None else "'" + value.replace("'", "''") + "'"


def load_sql(rows: list[tuple[Candidate, Item]]) -> str:
    values = [
        '(' + ', '.join(_literal(v) for v in (c.activity_id, c.user_id, c.sender, c.happened_at, c.subject,
                                               c.body_sha256, item.authored, item.history, item.message_id)) + ')'
        for c, item in rows]
    lines = ['-- Private: holds email text. Load it in the psql session that runs schema/recover-email-text.sql.',
             'set standard_conforming_strings = on;', LOAD_TABLE]
    if values:
        lines.append('insert into email_text_recovery values\n' + ',\n'.join(values) + ';')
    return '\n'.join(lines) + '\n'


def recover(candidates: list[Candidate], items: dict[str, list[Item]], mailbox: str) -> tuple[Counter[str], str]:
    counts: Counter[str] = Counter(candidates=len(candidates))
    verified = []
    for candidate in candidates:
        name, item = outcome(candidate, items, mailbox)
        counts[name] += 1
        if item is not None:
            verified.append((candidate, item))
            counts['message_id_found'] += bool(item.message_id and not candidate.message_id)
    return counts, load_sql(verified)


def _refusal(args: argparse.Namespace, profile: Path) -> str | None:
    out = args.out.resolve()
    if any((folder / '.git').exists() for folder in out.parents):
        return 'the load file holds email text; name a path outside any git checkout'
    if args.store.resolve().is_relative_to(profile.resolve()):
        return 'name a copy of the store, not the live Outlook profile'
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--candidates', type=Path, required=True)
    parser.add_argument('--store', type=Path, required=True, help='a copy of HxStore.hxd')
    parser.add_argument('--files', type=Path, required=True, help="the copy's Files folder")
    parser.add_argument('--mailbox', required=True, help='the address the copied store belongs to')
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args(argv)
    settings = load_settings()
    refusal = _refusal(args, settings.profile)
    if refusal:
        print(f'outlook-email-recovery refused: {refusal}', file=sys.stderr)
        return 2
    candidates = read_candidates(args.candidates)
    with tempfile.TemporaryDirectory(dir=args.out.resolve().parent) as work:
        db = Path(work) / 'store.db'
        intake._decoder(Path.home(), settings)(args.store, args.files, db)
        counts, load = recover(candidates, read_items(db), args.mailbox)
    intake._private_write(args.out, load)
    print('outlook-email-recovery dry_run ' + ' '.join(f'{k}={v}' for k, v in sorted(counts.items())))
    return 0


if __name__ == '__main__':
    sys.exit(main())
