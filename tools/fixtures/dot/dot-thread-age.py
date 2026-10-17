#!/usr/bin/env python3
"""Authenticated watchdog snapshot; only the finished relay can complete a job."""
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from lib import dot_relay
from lib.secret_redaction import redact_text


def snapshot(messages, state, sender, thread, *, now=None):
    dot_relay._validate_messages(messages)
    complete_ts = state.get('brief_complete_ts', thread)
    replies = [m for m in sorted(messages, key=lambda m: dot_relay._timestamp_key(m['ts']))
               if dot_relay._timestamp_key(m['ts']) > dot_relay._timestamp_key(complete_ts)
               and sender in (m.get('user'), m.get('bot_id')) and not m.get('edited')
               and m.get('subtype') in (None, 'bot_message') and m['ts'] not in state.get('outgoing', [])]
    now = time.time() if now is None else now
    consumed = [m for m in replies if m['ts'] in state.get('messages', [])]
    complete = bool(state.get('finished') and any(dot_relay._protocol(m['text'])[1] is not None for m in consumed))
    parts = []
    for message in consumed:
        commands, report = dot_relay._protocol(message['text'])
        if not commands:
            parts.append(report if report is not None else message['text'] + '\n')
    return {'replies': len(replies), 'silent': max(0, int(now - float(replies[-1]['ts'] if replies else thread))),
            'age': max(0, int(now - float(thread))), 'complete': complete, 'report': ''.join(parts)}


def main():
    thread, report = sys.argv[1:]
    if dot_relay._timestamp_key(thread) is None:
        raise ValueError('invalid thread')
    cfg = dot_relay.read_config(Path.home()/'.hermes/.env', ROOT)
    transport = dot_relay.SlackTransport(cfg['token'], cfg['channel'])
    directory = Path.home()/'.local/state/dot-relay'/thread
    state = json.loads((directory/'state.json').read_text())
    result = snapshot(transport.replies(thread), state, cfg['sender'], thread)
    if result['complete']:
        # Publication errors leave the relay nonzero; the feeder checks that exit before filing.
        Path(report).write_text(redact_text(result['report'], known_secrets=(cfg['token'],)))
    print(result['replies'], result['silent'], result['age'], int(result['complete']))


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, dot_relay.SlackError):
        print('err unreadable')
        raise SystemExit(1)
