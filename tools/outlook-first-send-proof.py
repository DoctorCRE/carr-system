#!/usr/bin/env python3
"""Read-only proof of this partner's first submitted Outlook send. No mail content is printed.

Run through tools/db-tap.py run after installation and the first partner Submit.
An absent Sent entry is inconclusive, never permission to send again.
"""
import json
import os
from pathlib import Path
import re
import sys
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib import outlook_send as send
from lib.outlook_settings import load_settings

SQL = """
WITH first_submission AS (
  SELECT * FROM email_proposal
  WHERE kind='reply_draft' AND owner=%s AND submitted_at IS NOT NULL
  ORDER BY submitted_at,id LIMIT 1
)
SELECT p.id::text AS proposal_id,p.version,p.status,p.resolved_by=partner.id AS partner_submitted,
  partner.email AS account,copy.email AS partner_cc,
  p.submitted_email->>'subject' AS subject,p.submitted_email->'to' AS "to",
  p.submitted_email->'cc' AS cc,p.send_result,
  COALESCE((SELECT jsonb_agg(a.email_context) FROM activity a
    WHERE a.kind='email_out' AND a.email_context->>'send_id'=p.id::text), '[]'::jsonb) AS correspondence
FROM first_submission p
JOIN actor partner ON partner.slug=p.owner
JOIN actor copy ON copy.slug=CASE p.owner WHEN 'joe' THEN 'dell' ELSE 'joe' END
"""


def prove(row, ledger, snapshot, settings):
    if row is None:
        return dict(ok=False, reason='no_partner_submission')
    key = str(uuid.uuid5(uuid.NAMESPACE_URL,
        f"outlook-outbox|{row['proposal_id']}|{row['version'] - 1}"))
    rows = [r for r in ledger if r.get('key') == key]
    prints = {r['fingerprint'] for r in rows}
    presses = [r for r in ledger if r.get('fingerprint') in prints and r.get('state') == 'pressing']
    done = [r for r in rows if r.get('state') == 'done']
    result = row.get('send_result') or {}
    item_id, sent_at = result.get('sent_item_id'), result.get('sent_at')
    since = presses[0]['at'] - send.CLOCK_SKEW if presses else 0
    until = since + send.DUPLICATE_WINDOW + send.CLOCK_SKEW
    recipients = set(row['to'] + row['cc'])
    matches = {r.item_id: r for r in snapshot.sent
               if r.sender.lower() == settings.account and r.subject == row['subject']
               and since <= r.sent_unix <= until
               and set(re.findall(r"[\w.+'-]+@[\w-]+(?:\.[\w-]+)+", (r.recipients or '').lower())) == recipients}
    expected_recipients = sorted([(a, 'to') for a in row['to']] + [(a, 'cc') for a in row['cc']])
    records = row['correspondence']
    checks = dict(
        partner_submitted=row['partner_submitted'] is True,
        partner_cc=row['cc'] == [settings.partner_cc] and row['partner_cc'] == settings.partner_cc,
        recorded_sent=row['status'] == 'sent' and result.get('status') == 'sent',
        sender_account=row['account'] == settings.account == result.get('sender_account'),
        one_press=len(presses) == 1,
        ledger_confirmed=bool(done) and all(r.get('sent_item_id') == item_id and r.get('sent_at') == sent_at for r in done),
        one_sent_item=len(matches) == 1 and item_id in matches and matches[item_id].sent_at == sent_at,
        one_correspondence=len(records) == 1 and all(
            r.get('sent_item_id') == item_id and r.get('sent_at') == sent_at
            and r.get('sender', {}).get('email') == settings.account
            and sorted((v.get('email'), v.get('kind')) for v in r.get('recipients', [])) == expected_recipients
            for r in records))
    return dict(ok=all(checks.values()), proposal_id=row['proposal_id'], checks=checks)


def main():
    import psycopg
    from psycopg.rows import dict_row
    try:
        settings = load_settings()
        settings.require_repo(Path(__file__).resolve().parents[1])
        if settings.account.rsplit('@', 1)[-1] != 'carr.us':
            raise ValueError('work_account_required')
        with psycopg.connect(os.environ['DATABASE_URL'], row_factory=dict_row,
                              options='-c default_transaction_read_only=on') as conn:
            with conn.cursor() as cur:
                cur.execute(SQL, (settings.owner,))
                row = cur.fetchone()
        ledger = [json.loads(line) for line in (settings.send_state / send.LEDGER).read_text().splitlines()]
        snapshot = send.DecodedMailbox(Path.home(), settings.repo).snapshot()
        result = prove(row, ledger, snapshot, settings)
    except Exception as exc:
        result = dict(ok=False, reason='proof_unavailable', error_type=type(exc).__name__)
    print(json.dumps(result, sort_keys=True))
    return 0 if result['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
