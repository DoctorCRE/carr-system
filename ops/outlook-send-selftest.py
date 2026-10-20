#!/usr/bin/env python3
"""Offline tests for lib/outlook_send.py. A fake cua-driver plays Outlook and a fake decoded mailbox
plays Sent Items and the calendar; nothing real is touched and no Send is ever pressed.

The fake mirrors the compose window read from Joe's live Outlook on 2026-10-20: the
title "SUBJECT • account", AXTextFields "To Recipients", "Cc Recipients" and
"Bcc Recipients" whose AXStaticText children read "ADDRESS, Presence Unknown", an
AXTextField "Subject", depth-1 Send and Discard buttons, and a Draft > Send menu.
"""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from lib import outlook_send as send_mod
from lib import outlook_ui as ui
from lib.outlook_send import EventRow, Ports, SentRow, Snapshot, State

ACCOUNT = send_mod.ACCOUNT
NOW = 1_800_000_000.0
MAIN = {'window_id': 1, 'title': f'Inbox • {ACCOUNT}', 'on_current_space': True,
        'bounds': {'x': 0, 'y': 33, 'width': 1230, 'height': 768}}


class Clock:
    def __init__(self):
        self.t = NOW

    def now(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.t += seconds


class FakeMailbox:
    def __init__(self, sent=(), events=()):
        self.sent, self.events, self.reads, self.broken = list(sent), list(events), 0, False

    def snapshot(self) -> Snapshot:
        self.reads += 1
        if self.broken:
            raise OSError('decode failed')
        return Snapshot(tuple(self.sent), tuple(self.events))


class FakeOutlook:
    """A cua-driver stand-in: windows, an accessibility walk per window, clicks and menus."""

    def __init__(self, clock: Clock, mailbox: FakeMailbox, on_send='sends', corrupt=None):
        self.clock, self.mailbox, self.on_send, self.corrupt = clock, mailbox, on_send, corrupt
        self.windows = {1: dict(MAIN)}
        self.forms: dict[int, dict] = {}
        self.calls: list[tuple] = []
        self.next_id = 100

    def open_url(self, url: str) -> None:
        parts = urlsplit(url)
        query = {k: v[0] for k, v in parse_qs(parts.query).items()}
        form = {'to': unquote(parts.path).split(','), 'cc': query.get('cc', '').split(','),
                'bcc': query.get('bcc', '').split(','), 'subject': query.get('subject', ''),
                'body': query.get('body', '')}
        form = {k: [a for a in v if a] if isinstance(v, list) else v for k, v in form.items()}
        if self.corrupt:
            form[self.corrupt[0]] = self.corrupt[1]
        wid = self.next_id
        self.next_id += 1
        self.forms[wid] = form
        self.windows[wid] = {'window_id': wid, 'title': f'{form["subject"]} • {ACCOUNT}', 'on_current_space': True,
                             'bounds': {'x': 298, 'y': 52, 'width': 917, 'height': 911}}

    def elements(self, wid: int) -> list[dict]:
        if wid == 1:
            return [{'element_index': 0, 'role': 'AXWindow', 'label': MAIN['title'], 'depth': 0}]
        form, out = self.forms[wid], []

        def add(role, label, value=None, parent=None, depth=1):
            out.append({'element_index': len(out), 'role': role, 'label': label, 'value': value,
                        'parent_index': parent, 'depth': depth, 'element_token': f'{wid}:{len(out)}'})
            return len(out) - 1

        root = add('AXWindow', self.windows[wid]['title'], depth=0)
        add('AXButton', 'Send', parent=root)
        add('AXButton', 'Discard', parent=root)
        for field, label in (('to', 'To Recipients'), ('cc', 'Cc Recipients'), ('bcc', 'Bcc Recipients')):
            index = add('AXTextField', label, '￼', root, 2)
            for address in form[field]:
                add('AXStaticText', f'{address}, Presence Unknown', parent=index, depth=3)
        add('AXTextField', 'Subject', form['subject'], root, 2)
        add(send_mod.BODY_ROLE, send_mod.BODY_LABEL, form['body'], root, 2)
        add('AXMenuItem', 'Send', depth=3)
        return out

    def __call__(self, tool: str, args: dict) -> dict:
        self.calls.append((tool, args))
        if tool == 'list_windows':
            return {'windows': list(self.windows.values())}
        if tool == 'get_window_state':
            return {'elements': self.elements(args['window_id'])}
        if tool == 'click':
            wid, index = map(int, args['element_token'].split(':'))
            label = self.elements(wid)[index]['label']
            if label == 'Discard':
                self.windows.pop(wid)
                self.forms.pop(wid)
                return {'ok': True}
            raise AssertionError(f'unexpected click on {label}')
        if tool == 'bring_to_front':
            return {'ok': True}
        if tool == 'invoke_menu':
            if args['path'] == ['Draft', 'Send']:
                self.press(args['window_id'])
                return {'ok': True}
            raise AssertionError(f'unexpected menu {args["path"]}')
        raise AssertionError(f'unexpected tool {tool}')

    def press(self, wid: int) -> None:
        form = self.forms[wid]
        if self.on_send == 'stays_open':
            return
        self.windows.pop(wid)
        if self.on_send == 'sends':
            self.mailbox.sent.append(SentRow(ACCOUNT, ', '.join(form['to'] + form['cc']), form['subject'],
                                             int(self.clock.t) + 30))

    def presses(self) -> int:
        return sum(1 for tool, args in self.calls if tool == 'invoke_menu' and args['path'] == ['Draft', 'Send'])


MAIL = dict(to=['person-a@example.test'], cc=['person-b@example.test'], bcc=['person-c@example.test'],
            subject='subject-001', body='Line one.\nLine two.')


class Harness:
    def __init__(self, out: Path, on_send='sends', corrupt=None, idle=600.0, sent=(), events=()):
        self.clock = Clock()
        self.mailbox = FakeMailbox(sent, events)
        self.fake = FakeOutlook(self.clock, self.mailbox, on_send, corrupt)
        self.idle = [idle]
        self.carr_calls: list[tuple[str, dict]] = []
        self.ports = Ports(open_form=lambda item: send_mod.open_form(item, ui.Waits(settle_seconds=0,
                                                                                   poll_seconds=0,
                                                                                   new_window_seconds=0)),
                           mailbox=self.mailbox, idle_seconds=lambda: self.idle[0] if len(self.idle) == 1
                           else self.idle.pop(0), carr=self.carr, now=self.clock.now, sleep=self.clock.sleep,
                           out=out, close_seconds=30, confirm_seconds=300, poll_seconds=20)

    def carr(self, verb: str, args: dict) -> dict:
        self.carr_calls.append((verb, args))
        if verb == 'find':
            return {'parties': [{'name': 'client-001', 'ref': 'C-1', 'merged': False}]}
        return {'ok': True}

    def send(self, key='key-1', record=None, **overrides):
        spec = {**MAIL, **overrides}
        with patch.object(ui, 'cua', self.fake), patch.object(ui, 'outlook_pid', lambda: 42), \
                patch.object(send_mod, '_open_url', self.fake.open_url), \
                patch.object(send_mod, '_frontmost', lambda: 'com.example.editor'), \
                patch.object(send_mod, '_activate', lambda bundle: self.fake.calls.append(('activate', bundle))), \
                patch.object(send_mod.time, 'sleep', lambda s: None):
            return send_mod.send(spec['to'], spec['cc'], spec['bcc'], spec['subject'], spec['body'],
                                 idempotency_key=key, record=record, ports=self.ports)


class SendTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.out = Path(self.tmp.name) / 'out'

    def tearDown(self):
        self.tmp.cleanup()

    def ledger(self) -> list[dict]:
        path = self.out / send_mod.LEDGER
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def test_sends_once_and_confirms_from_sent_items(self):
        h = Harness(self.out)
        result = h.send(record='client-001')
        self.assertEqual((result.state, result.mode), (State.DONE, 'front'))
        self.assertEqual(h.fake.presses(), 1)
        self.assertEqual([r['state'] for r in self.ledger()], ['pressing', 'done'])
        self.assertEqual(stat.S_IMODE(os.stat(self.out / send_mod.LEDGER).st_mode), 0o600)
        self.assertIn(('activate', 'com.example.editor'), h.fake.calls)
        logged = [args for verb, args in h.carr_calls if verb == 'log-activity']
        self.assertEqual(len(logged), 1)
        self.assertEqual((logged[0]['ref'], logged[0]['kind'], logged[0]['detail']),
                         ('C-1', 'email_out', MAIL['body']))
        self.assertEqual(result.logged, ('C-1',))

    def test_field_read_back_mismatch_discards_and_never_presses(self):
        h = Harness(self.out, corrupt=('subject', 'subject-00'))
        result = h.send()
        self.assertEqual((result.state, result.reason), (State.FIELD_MISMATCH, 'mismatch:subject'))
        self.assertEqual(h.fake.presses(), 0)
        self.assertEqual(h.fake.forms, {})
        self.assertEqual(self.ledger(), [])

    def test_dropped_recipient_is_a_mismatch(self):
        h = Harness(self.out, corrupt=('bcc', []))
        self.assertEqual(h.send().reason, 'mismatch:bcc')
        self.assertEqual(h.fake.presses(), 0)

    def test_duplicate_key_is_refused_before_outlook_is_touched(self):
        h = Harness(self.out)
        self.assertEqual(h.send().state, State.DONE)
        calls = len(h.fake.calls)
        again = h.send(subject='subject-002')
        self.assertEqual(again.state, State.DUPLICATE_KEY)
        self.assertEqual(len(h.fake.calls), calls)
        self.assertEqual(h.fake.presses(), 1)

    def test_message_already_in_sent_items_is_refused(self):
        earlier = SentRow(ACCOUNT, 'person-a@example.test', 'Subject-001', int(NOW) - 3600)
        h = Harness(self.out, sent=[earlier])
        result = h.send(key='key-2')
        self.assertEqual(result.state, State.ALREADY_IN_MAILBOX)
        self.assertEqual(h.fake.calls, [])

    def test_sent_row_with_unreadable_recipients_still_refuses(self):
        h = Harness(self.out, sent=[SentRow(ACCOUNT, None, 'subject-001', int(NOW) - 60)])
        self.assertEqual(h.send().state, State.ALREADY_IN_MAILBOX)

    def test_old_or_other_sender_rows_do_not_refuse(self):
        rows = [SentRow(ACCOUNT, 'person-a@example.test', 'subject-001', int(NOW) - 2 * 86400),
                SentRow('person-a@example.test', None, 'subject-001', int(NOW) - 60),
                SentRow(ACCOUNT, 'person-z@example.test', 'subject-001', int(NOW) - 60)]
        self.assertEqual(Harness(self.out, sent=rows).send().state, State.DONE)

    def test_compose_not_closing_after_the_press_is_not_retried(self):
        h = Harness(self.out, on_send='stays_open')
        result = h.send()
        self.assertEqual(result.state, State.WINDOW_NOT_CLOSED)
        self.assertEqual(h.fake.presses(), 1)
        self.assertEqual(len(h.fake.forms), 1)
        self.assertEqual([r['state'] for r in self.ledger()], ['pressing', 'window_not_closed'])
        self.assertEqual(h.send(key='key-1').state, State.DUPLICATE_KEY)
        self.assertEqual(h.fake.presses(), 1)

    def test_pressed_but_not_in_sent_items_is_its_own_result(self):
        h = Harness(self.out, on_send='closes_without_sending')
        result = h.send(record='client-001')
        self.assertEqual((result.state, result.reason), (State.PRESSED_UNCONFIRMED, 'not_in_mailbox_yet'))
        self.assertEqual([r['state'] for r in self.ledger()], ['pressing', 'pressed_unconfirmed'])
        self.assertGreaterEqual(h.mailbox.reads, 300 // 20)
        self.assertFalse([v for v, _ in h.carr_calls if v == 'log-activity'])
        self.assertEqual(h.send().state, State.DUPLICATE_KEY)

    def test_a_draft_row_does_not_confirm_a_send(self):
        draft = SentRow('person-a@example.test', None, 'subject-001', int(NOW))
        h = Harness(self.out, on_send='closes_without_sending')
        h.mailbox.sent.append(draft)
        self.assertEqual(h.send().state, State.PRESSED_UNCONFIRMED)

    def test_idle_guard_blocks_before_the_window_opens(self):
        h = Harness(self.out, idle=120.0)
        self.assertEqual((h.send().state, h.fake.calls), (State.NOT_IDLE, []))

    def test_idle_guard_rechecks_before_the_press(self):
        h = Harness(self.out)
        h.idle[:] = [600.0, 10.0]
        result = h.send()
        self.assertEqual(result.state, State.NOT_IDLE)
        self.assertEqual((h.fake.presses(), h.fake.forms, self.ledger()), (0, {}, []))
        h.idle[:] = [600.0]
        self.assertEqual(h.send().state, State.DONE)

    def test_unreadable_idle_time_fails_closed(self):
        h = Harness(self.out)
        h.ports.idle_seconds = lambda: None
        self.assertEqual(h.send().reason, 'idle_time_unreadable')

    def test_unreadable_mailbox_fails_closed(self):
        h = Harness(self.out)
        h.mailbox.broken = True
        self.assertEqual((h.send().reason, h.fake.calls), ('mailbox_unreadable', []))

    def test_wrong_account_compose_is_discarded(self):
        h = Harness(self.out)
        original = h.fake.open_url

        def other_account(url):
            original(url)
            wid = max(h.fake.windows)
            h.fake.windows[wid]['title'] = 'subject-001 • other@example.test'
        h.fake.open_url = other_account
        result = h.send()
        self.assertEqual((result.state, result.reason), (State.FAILED, 'wrong_account'))
        self.assertEqual((h.fake.presses(), h.fake.forms), (0, {}))

    def test_unmatched_record_still_reports_the_send(self):
        h = Harness(self.out)
        h.carr = lambda verb, args: {'parties': []}
        h.ports.carr = h.carr
        result = h.send(record='client-404')
        self.assertEqual((result.state, result.logged), (State.DONE, ('record_not_matched',)))

    def test_status_line_carries_no_address_or_subject(self):
        result = Harness(self.out).send(record='client-001')
        line = result.line('send')
        self.assertEqual(line, 'outlook-send done confirmed_in_mailbox mode=front logged=C-1')

    def test_invalid_address_is_refused_before_anything_runs(self):
        with self.assertRaises(ValueError):
            Harness(self.out).send(to=['not an address'])


if __name__ == '__main__':
    unittest.main()
