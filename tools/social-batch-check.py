#!/usr/bin/env python3
"""Read Blotato's scheduled output before declaring a weekly batch complete."""
import argparse
from datetime import date, datetime, time, timedelta
import json
import os
from pathlib import Path
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
CENTRAL = ZoneInfo('America/Chicago')


def check(friday, api_key):
    if friday.weekday() != 4:
        raise ValueError('friday_required')
    start = datetime.combine(friday + timedelta(days=3), time(), CENTRAL)
    end = start + timedelta(days=7)
    if start <= datetime.now(CENTRAL):
        raise ValueError('window_already_started')
    accounts = json.loads((ROOT / 'claude-tree/skills/social-media-manager/config/blotato-accounts.json').read_text())
    owned = {entry['accountId'] for entry in accounts.values()}
    posts, seen = {}, set()
    cursor = None
    for _ in range(100):
        query = {'limit': 50}
        if cursor:
            query['cursor'] = cursor
        request = Request('https://backend.blotato.com/v2/schedules?' + urlencode(query),
                          headers={'blotato-api-key': api_key})
        with urlopen(request, timeout=30) as response:
            page = json.load(response)
        if not isinstance(page, dict) or not isinstance(page.get('items'), list):
            raise ValueError('invalid_schedule_response')
        for item in page['items']:
            if not isinstance(item, dict):
                raise ValueError('invalid_schedule_item')
            account = item.get('account')
            if account is None:
                continue
            if not isinstance(account, dict) or not isinstance(account.get('id'), str):
                raise ValueError('invalid_schedule_account')
            if account['id'] not in owned:
                continue
            scheduled = datetime.fromisoformat(item['scheduledAt'].replace('Z', '+00:00'))
            if scheduled.tzinfo is None:
                raise ValueError('schedule_timezone_required')
            if start <= scheduled < end:
                identifier = item['id']
                if not isinstance(identifier, str) or not identifier:
                    raise ValueError('schedule_id_required')
                posts[identifier] = {'id': identifier, 'account_id': account['id'],
                                     'scheduled_at': scheduled.isoformat()}
        cursor = page.get('cursor')
        if cursor is None:
            break
        if not isinstance(cursor, str) or not cursor or cursor in seen:
            raise ValueError('invalid_schedule_cursor')
        seen.add(cursor)
    else:
        raise ValueError('schedule_page_limit')
    return {'status': 'verified' if posts else 'failed',
            'reason': 'posts_next_week' if posts else 'no_posts_next_week',
            'week_start': start.isoformat(), 'week_end_exclusive': end.isoformat(),
            'posts': sorted(posts.values(), key=lambda post: (post['scheduled_at'], post['id']))}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--friday', type=date.fromisoformat,
                        help='Friday firing date; defaults to the most recent Friday in America/Chicago')
    args = parser.parse_args(argv)
    today = datetime.now(CENTRAL).date()
    friday = args.friday or today - timedelta(days=(today.weekday() - 4) % 7)
    try:
        api_key = os.environ.get('BLOTATO_API_KEY')
        if not api_key:
            raise ValueError('blotato_auth_unavailable')
        result = check(friday, api_key)
    except (OSError, URLError, ValueError, KeyError, TypeError, AttributeError):
        result = {'status': 'failed', 'reason': 'output_unverifiable', 'posts': []}
    print(json.dumps(result))
    return 0 if result['status'] == 'verified' else 1


if __name__ == '__main__':
    raise SystemExit(main())
