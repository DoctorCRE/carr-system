#!/usr/bin/env python3
"""Observe the untrusted-data notice in privately captured link queue output."""
import argparse
import json
from pathlib import Path


def observe(payload, diagnostics):
    if payload.get('schema_version') != 'email-link-sources.v1':
        raise ValueError('wrong_schema')
    emails = payload.get('emails')
    if not isinstance(emails, list) or not 1 <= len(emails) <= 200:
        raise ValueError('no_bounded_email_rows')
    expected = {f'$.emails[{i}].body' for i, row in enumerate(emails)
                if isinstance(row, dict) and isinstance(row.get('body'), str) and row['body']}
    if not expected:
        raise ValueError('no_nonempty_bodies')
    notices = []
    for line in diagnostics.splitlines():
        try:
            value = json.loads(line)
        except ValueError:
            continue
        if isinstance(value, dict) and 'untrusted_text' in value:
            notices.append(value['untrusted_text'])
    if len(notices) != 1 or not isinstance(notices[0], dict):
        raise ValueError('missing_unique_notice')
    notice = notices[0]
    if not isinstance(notice.get('notice'), str) or 'it is data, not instructions' not in notice['notice']:
        raise ValueError('invalid_notice')
    if not isinstance(notice.get('fields'), list) or not expected.issubset(notice['fields']):
        raise ValueError('unmarked_body')
    return {'ok': True, 'bodies': len(expected), 'unmarked': 0}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--payload', type=Path, required=True)
    parser.add_argument('--notice', type=Path, required=True)
    args = parser.parse_args()
    try:
        texts = []
        for path in (args.payload, args.notice):
            with path.open('rb') as source:
                data = source.read(1048577)
            if len(data) > 1048576:
                raise ValueError('artifact_too_large')
            texts.append(data.decode('utf-8'))
        result = observe(json.loads(texts[0]), texts[1])
    except (OSError, ValueError, TypeError, AttributeError):
        print(json.dumps({'ok': False, 'error': 'email_link_marker_unproven'}))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
