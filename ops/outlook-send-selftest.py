"""Offline tests for lib/outlook_send.py. A fake cua-driver plays Outlook and a fake decoded mailbox
plays Sent Items and the calendar; nothing real is touched and no Send is ever pressed.

The fake mirrors the observed compose window shape. It has the
title "SUBJECT • account", AXTextFields "To Recipients", "Cc Recipients" and
"Bcc Recipients" whose AXStaticText children read "ADDRESS, Presence Unknown", an
AXTextField "Subject", depth-1 Send and Discard buttons, and a Draft > Send menu.
"""
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import logging
import traceback
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
import plistlib
import importlib.util
import os
from pathlib import Path
import stat
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from lib import outlook_send as send_mod
from lib import outlook_ui as ui
from lib.outlook_settings import load_settings
from outlook_test_support import write_settings
from lib.outlook_send import EventRow, Ports, SentRow, Snapshot, State

ACCOUNT = 'sender@example.test'
NOW = 1_800_000_000.0
MAIN = {'window_id': 1, 'title': f'Inbox • {ACCOUNT}', 'on_current_space': True, 'is_on_screen': True,
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
        self.front = False
        self.background = False
        self.press_count = 0
        self.unresolved: set[int] = set()
        self.new_mail_label = 'New Mail'

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
        self.windows[wid] = {'window_id': wid, 'title': f'{form["subject"]} • {ACCOUNT}', 'on_current_space': True, 'is_on_screen': True,
                             'bounds': {'x': 298, 'y': 52, 'width': 917, 'height': 911}}

    def elements(self, wid: int) -> list[dict]:
        if wid == 1:
            return [{'element_index': 0, 'role': 'AXWindow', 'label': MAIN['title'], 'depth': 0},
                    {'element_index': 1, 'role': 'AXButton', 'label': self.new_mail_label, 'depth': 1,
                     'element_token': '1:1'},
                    {'role': 'AXTable', 'label': 'Message List', 'depth': 4}]
        form = self.forms[wid]
        out: list[dict] = []

        def add(role, label, value=None, parent=None, depth=1):
            out.append({'element_index': len(out), 'role': role, 'label': label, 'value': value,
                        'parent_index': parent, 'depth': depth, 'element_token': f'{wid}:{len(out)}'})
            return len(out) - 1

        root = add('AXWindow', self.windows[wid]['title'], depth=0)
        if 'start' in form:
            add('AXButton', 'Send' if form['attendees'] else 'Save', parent=root)
            add('AXButton', 'Discard', parent=root)
            for key, label in {'subject': 'Subject', 'start': 'Start', 'end': 'End', 'location': 'Location', 'body': 'Message body', 'attendees': 'Required attendees'}.items():
                add('AXTextField', label, form[key], root, 2)
            return out
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
            if args['window_id'] in self.unresolved:
                return {'elements': [], 'degraded_reason': 'ax_window_unresolved'}
            return {'elements': self.elements(args['window_id'])}
        if tool == 'click':
            wid, index = map(int, args['element_token'].split(':'))
            label = self.elements(wid)[index]['label']
            if wid == 1 and index == 1:
                self.open_url('mailto:?subject=&body=')
                return {'ok': True}
            if label in ('Save', 'Send'):
                if not self.front and not self.background:
                    return {'status': 'refused', 'refusal': {'code': 'background_unavailable'}}
                self.press(wid)
                return {'ok': True}
            if label == 'Discard':
                self.windows.pop(wid)
                self.forms.pop(wid)
                return {'ok': True}
            raise AssertionError(f'unexpected click on {label}')
        if tool == 'set_value':
            wid, index = map(int, args['element_token'].split(':'))
            label = self.elements(wid)[index]['label']
            if 'start' in self.forms[wid]:
                key = {'Subject': 'subject', 'Start': 'start', 'End': 'end', 'Location': 'location',
                       'Message body': 'body', 'Required attendees': 'attendees'}[label]
                self.forms[wid][key] = args['value']
            else:
                key = {'To Recipients': 'to', 'Cc Recipients': 'cc', 'Bcc Recipients': 'bcc',
                       'Subject': 'subject', 'Message body': 'body'}[label]
                self.forms[wid][key] = args['value'].split('; ') if key in ('to', 'cc', 'bcc') and args['value'] else ([] if key in ('to', 'cc', 'bcc') else args['value'])
            if self.corrupt and self.corrupt[0] == key:
                self.forms[wid][key] = self.corrupt[1]
            return {'ok': True}
        if tool == 'press_key' and args['key'] == 'tab':
            return {'ok': True}
        if tool == 'bring_to_front':
            self.front = True
            return {'ok': True}
        if tool == 'invoke_menu':
            if args['path'] == ['File', 'New', 'Event']:
                wid = self.next_id
                self.next_id += 1
                self.forms[wid] = {k: '' for k in ('subject', 'start', 'end', 'location', 'body', 'attendees')}
                self.windows[wid] = {'window_id': wid, 'title': f'New Event • {ACCOUNT}',
                                    'on_current_space': True, 'is_on_screen': True, 'bounds': {'width': 900, 'height': 800}}
                return {'ok': True}
            if args['path'] == ['Draft', 'Send']:
                self.press(args['window_id'])
                return {'ok': True}
            raise AssertionError(f'unexpected menu {args["path"]}')
        raise AssertionError(f'unexpected tool {tool}')

    def press(self, wid: int) -> None:
        self.press_count += 1
        form = self.forms[wid]
        if self.on_send == 'stays_open':
            return
        self.windows.pop(wid)
        if self.on_send == 'sends' and 'start' in form:
            self.mailbox.events.append(EventRow(form['subject'], int(datetime.fromisoformat(form['start']).timestamp())))
        elif self.on_send == 'sends':
            self.mailbox.sent.append(SentRow(ACCOUNT, ', '.join(form['to'] + form['cc'] + form['bcc']), form['subject'],
                                             int(self.clock.t) + 30, 'hxstore-key:42'))

    def presses(self) -> int:
        return self.press_count


MAIL = dict(to=['person-a@example.test'], cc=[ACCOUNT], bcc=[ACCOUNT],
            subject='subject-001', body='Line one.\nLine two.')


class Harness:
    def __init__(self, out: Path, on_send='sends', corrupt=None, idle=600.0, sent=(), events=()):
        self.clock = Clock()
        self.mailbox = FakeMailbox(sent, events)
        self.fake = FakeOutlook(self.clock, self.mailbox, on_send, corrupt)
        self.idle = [idle]
        self.handler = send_mod.BUNDLE.lower()
        self.transport = None
        self.previous = 'com.example.editor'
        self.front_idle = 600
        self.frontmost = lambda: self.previous
        self.carr_calls: list[tuple[str, dict]] = []
        self.ports = Ports(open_form=lambda item: send_mod.open_form(item, ui.Waits(settle_seconds=0,
                                                                                   poll_seconds=0,
                                                                                   new_window_seconds=0)),
                           mailbox=self.mailbox, idle_seconds=lambda: self.idle[0] if len(self.idle) == 1
                           else self.idle.pop(0), carr=self.carr, now=self.clock.now, sleep=self.clock.sleep,
                           out=out, close_seconds=30, confirm_seconds=300, poll_seconds=20)

    def carr(self, verb: str, args: dict) -> dict:
        self.carr_calls.append((verb, args))
        if verb == 'next-signals':
            return {'count': 0}
        if verb == 'mail-contact-policy':
            return {'recipients': [{'email': email, 'blocked': False} for email in args['emails']]}
        if verb == 'get-call-context':
            return {'deals': [{'id': '11111111-2222-3333-4444-555555555555', 'name': 'Synthetic deal',
                               'participants': [{'email': 'person-a@example.test'}]}]}
        if verb == 'find':
            return {'parties': [{'name': 'client-001', 'ref': 'C-1', 'merged': False}]}
        return {'ok': True}

    def send(self, key='key-1', record=None, send_requested=True, **overrides):
        spec = {**MAIL, **overrides}
        with patch.object(send_mod, 'account', lambda: ACCOUNT), patch.object(ui, 'cua', self.transport or self.fake), patch.object(ui, 'outlook_pid', lambda: 42), \
                patch.object(send_mod, '_mailto_handler', lambda: self.handler), \
                patch.object(send_mod, '_open_url', self.fake.open_url), \
                patch.object(send_mod, '_idle_seconds', lambda: self.front_idle() if callable(self.front_idle) else self.front_idle), patch.object(send_mod, '_frontmost', lambda: self.frontmost()), \
                patch.object(send_mod, '_activate', lambda bundle: self.fake.calls.append(('activate', bundle))), \
                patch.object(send_mod.time, 'sleep', lambda s: None):
            return send_mod.send(spec['to'], spec['cc'], spec['bcc'], spec['subject'], spec['body'],
                                 idempotency_key=key, record=record, ports=self.ports,
                                 **({} if send_requested is None else {'send_requested': send_requested}))


EVENT = dict(subject='event-001', start=datetime(2030, 1, 2, 15, tzinfo=timezone.utc),
             end=datetime(2030, 1, 2, 16, tzinfo=timezone.utc), location='Room 1', body='Event details.',
             attendees=['person-a@example.test'])


def event(h, key='event-key', send_requested=True, **overrides):
    with patch.object(send_mod, 'account', lambda: ACCOUNT), patch.object(ui, 'cua', h.fake), \
            patch.object(ui, 'outlook_pid', lambda: 42), patch.object(send_mod.time, 'sleep', lambda _: None), \
            patch.object(send_mod, '_idle_seconds', lambda: 600), patch.object(send_mod, '_frontmost', lambda: 'com.example.editor'), \
            patch.object(send_mod, '_activate', lambda _: None):
        return send_mod.create_event(**{**EVENT, **overrides}, idempotency_key=key,
                                     record='client-001', ports=h.ports,
                                     **({} if send_requested is None else {'send_requested': send_requested}))


class SendTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.out = Path(self.tmp.name) / 'out'
        self.home = Path(self.tmp.name) / 'home'
        self.config = write_settings(self.home, ROOT, account=ACCOUNT, internal_domain='company.test')
        self.enterContext(patch.object(send_mod, 'load_settings', lambda: load_settings(home=self.home)))
        self.enterContext(patch.object(ui, 'load_settings', lambda: load_settings(home=self.home)))
        self.dialog = self.enterContext(patch.object(send_mod.subprocess, 'run',
            return_value=subprocess.CompletedProcess(['osascript'], 0, 'true\n', '')))

    def tearDown(self):
        self.tmp.cleanup()

    def ledger(self) -> list[dict]:
        path = self.out / send_mod.LEDGER
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def test_pressed_new_mail_button_completes_the_send_flow(self):
        h = Harness(self.out)
        h.handler = 'com.example.browser'
        h.fake.new_mail_label = 'New Mail, pressed'
        self.assertEqual(h.send().state, State.DONE)
        self.assertEqual(h.fake.presses(), 1)
        self.assertEqual(h.mailbox.sent[0].subject, MAIL['subject'])

    def test_new_mail_rule_button_does_not_open_a_compose_window(self):
        h = Harness(self.out)
        h.handler = 'com.example.browser'
        h.fake.new_mail_label = 'New Mail Rule'
        self.assertEqual(h.send().state, State.FAILED)
        self.assertEqual(h.fake.forms, {})
        self.assertEqual(h.fake.presses(), 0)

    def test_default_call_prepares_a_draft_without_pressing_send(self):
        h = Harness(self.out)
        result = h.send(send_requested=None)
        self.assertEqual(result.state.value, 'draft')
        self.assertEqual(h.fake.presses(), 0)
        self.assertEqual(len(h.fake.forms), 1)
        self.assertEqual(self.ledger(), [])
        self.dialog.assert_not_called()

    def test_cancel_confirmation_leaves_draft_without_dispatch(self):
        self.dialog.return_value.stdout = 'false\n'
        h = Harness(self.out)
        result = h.send()
        self.assertEqual(result.state, State.DRAFT)
        self.assertEqual(h.fake.presses(), 0)
        self.assertEqual(len(h.fake.forms), 1)
        self.assertEqual(self.ledger(), [])
        self.assertFalse(any(verb == 'log-activity' for verb, _ in h.carr_calls))

    def test_confirmation_failures_leave_drafts_for_mail_and_events(self):
        for i, failure in enumerate((subprocess.CalledProcessError(1, 'osascript'),
                subprocess.TimeoutExpired('osascript', 125), OSError('dialog unavailable'))):
            for dispatch in (lambda h: h.send(), lambda h: event(h)):
                with self.subTest(failure=type(failure).__name__, dispatch=dispatch):
                    h = Harness(self.out / f'{i}-{id(dispatch)}')
                    self.dialog.side_effect = failure
                    self.assertEqual(dispatch(h).state, State.DRAFT)
                    self.assertEqual(h.fake.presses(), 0)
                    self.assertEqual(len(h.fake.forms), 1)
                    self.assertFalse((h.ports.out / send_mod.LEDGER).exists())

    def test_dialog_timeout_or_unrecognized_reply_never_dispatches(self):
        for i, reply in enumerate(('false\n', '', 'Send', 'true\nfalse')):
            with self.subTest(reply=reply):
                self.dialog.return_value.stdout = reply
                h = Harness(self.out / str(i))
                self.assertEqual(h.send().state, State.DRAFT)
                self.assertEqual(h.fake.presses(), 0)
                self.assertEqual(len(h.fake.forms), 1)

    def test_approval_shows_exact_account_recipients_and_subject_before_send(self):
        h = Harness(self.out)
        subject = 'Synthetic "quote" \\ path café'
        def approve(command, **kwargs):
            self.assertEqual(command, ['osascript', '-'])
            self.assertEqual(kwargs['timeout'], 125)
            script = kwargs['input']
            message = json.loads(script.split('display dialog ', 1)[1].split(' with title ', 1)[0])
            self.assertEqual(message, 'Account: sender@example.test\nTo: person-a@example.test\n'
                             'Cc: sender@example.test\nBcc: sender@example.test\nSubject: ' + subject)
            self.assertIn('buttons {"Cancel", "Send"} default button "Cancel"', script)
            self.assertIn('cancel button "Cancel" giving up after 120', script)
            self.assertIn('not (gave up of answer)', script)
            self.assertIn('button returned of answer is "Send"', script)
            self.assertEqual(h.fake.presses(), 0)
            self.assertEqual(next(iter(h.fake.forms.values())), dict(to=['person-a@example.test'],
                cc=['sender@example.test'], bcc=['sender@example.test'], subject=subject, body=MAIL['body']))
            return subprocess.CompletedProcess(command, 0, 'true\n', '')
        self.dialog.side_effect = approve
        self.assertEqual(h.send(subject=subject, cc=['sender@example.test'], bcc=['sender@example.test']).state, State.DONE)
        self.assertEqual(h.fake.presses(), 1)
        self.dialog.assert_called_once()

    def test_edit_during_confirmation_invalidates_approval(self):
        for field, value in (('subject', 'changed'), ('body', 'changed'), ('to', ['other@example.test'])):
            with self.subTest(field=field):
                h = Harness(self.out / field)
                def approve(command, **kwargs):
                    next(iter(h.fake.forms.values()))[field] = value
                    return subprocess.CompletedProcess(command, 0, 'true\n', '')
                self.dialog.side_effect = approve
                self.assertEqual(h.send().state, State.DRAFT)
                self.assertEqual(h.fake.presses(), 0)

    def test_approval_click_allows_fallback_but_later_input_blocks_it(self):
        sleep = time.sleep
        for resumed in (False, True):
            with self.subTest(resumed=resumed):
                h = Harness(self.out / str(resumed))
                def approve(command, **kwargs):
                    clicked = time.monotonic()
                    def idle():
                        sampled = time.monotonic() - clicked
                        sleep(0.01)
                        return sampled
                    h.front_idle = idle
                    return subprocess.CompletedProcess(command, 0, 'true\n', '')
                self.dialog.side_effect = approve
                original = h.fake.__call__
                def transport(tool, args):
                    if resumed and tool == 'bring_to_front':
                        h.front_idle = 0
                    return original(tool, args)
                h.transport = transport
                result = h.send()
                self.assertEqual(result.state, State.NOT_IDLE if resumed else State.DONE)
                self.assertEqual(h.fake.presses(), 0 if resumed else 1)


    def test_eleventh_requested_send_is_refused_by_daily_cap(self):
        h = Harness(self.out)
        for i in range(10):
            self.assertTrue(h.send(key=f'cap-{i}', subject=f'Individual note {i}').ok)
        result = h.send(key='cap-11', subject='Individual note eleven')
        self.assertEqual(result.reason, 'daily_cap')
        self.assertEqual(h.fake.press_count, 10)

    def test_daily_cap_uses_the_configured_local_date(self):
        write_settings(self.home, ROOT, account=ACCOUNT, internal_domain='company.test', timezone='Asia/Tokyo')
        h = Harness(self.out)
        h.clock.t = datetime(2030, 1, 2, 14, 59, tzinfo=timezone.utc).timestamp()
        for i in range(10):
            self.assertTrue(h.send(key=f'cap-{i}', subject=f'Individual note {i}').ok)
        self.assertEqual(h.send(key='eleventh', subject='Eleventh').reason, 'daily_cap')
        h.clock.t = datetime(2030, 1, 2, 15, 1, tzinfo=timezone.utc).timestamp()
        self.assertEqual(h.send(key='next-day', subject='Next local day').state, State.DONE)

    def test_do_not_contact_refuses_every_recipient_field(self):
        for field in ('to', 'cc', 'bcc'):
            with self.subTest(field=field):
                h = Harness(self.out / field)
                original = h.ports.carr
                def verb(name, args):
                    if name == 'mail-contact-policy':
                        return {'recipients': [
                            {'email': email, 'blocked': email == 'person-a@example.test'}
                            for email in args['emails']]}
                    return original(name, args)
                h.ports.carr = verb
                spec = {'to': [ACCOUNT], 'cc': [], 'bcc': [], field: ['person-a@example.test']}
                result = h.send(**spec)
                self.assertEqual(result.reason, 'do_not_contact')
                self.assertEqual(h.fake.press_count, 0)
                self.assertEqual(h.mailbox.reads, 0)




    def test_several_external_recipients_send(self):
        h = Harness(self.out)
        result = h.send(to=['person-a@example.test', 'other@example.test'], cc=['third@example.test'])
        self.assertEqual(result.state, State.DONE)
        self.assertEqual(h.fake.press_count, 1)

    def test_unreadable_suppression_fails_closed_for_mail_and_invites(self):
        replies = [None, {}, {'recipients': []}, {'recipients': [{'email': 'person-a@example.test'}]},
                   {'recipients': [{'email': 'person-a@example.test', 'blocked': 'false'}]}]
        for i, reply in enumerate(replies):
            for kind in ('mail', 'invite'):
                with self.subTest(reply=reply, kind=kind):
                    h = Harness(self.out / f'{i}-{kind}')
                    original = h.ports.carr
                    h.ports.carr = lambda name, args: (reply if
                        name == 'mail-contact-policy' else original(name, args))
                    result = h.send() if kind == 'mail' else event(h)
                    self.assertEqual(result.reason, 'contact_policy_unreadable')
                    self.assertEqual(h.fake.press_count, 0)
                    self.assertEqual(h.mailbox.reads, 0)

    def test_do_not_contact_blocks_invite(self):
        h = Harness(self.out)
        original = h.ports.carr
        h.ports.carr = lambda name, args: ({'recipients': [
            {'email': 'person-a@example.test', 'blocked': True}]} if
            name == 'mail-contact-policy' else original(name, args))
        self.assertEqual(event(h).reason, 'do_not_contact')
        self.assertEqual(h.fake.press_count, 0)



    def test_default_calendar_call_prepares_a_draft_without_inviting(self):
        h = Harness(self.out)
        result = event(h, send_requested=None)
        self.assertEqual(result.state, State.DRAFT)
        self.assertEqual(h.fake.presses(), 0)
        self.assertEqual(h.mailbox.reads, 0)
        self.assertEqual(self.ledger(), [])

    def test_send_intent_must_be_a_boolean(self):
        with self.assertRaises(ValueError):
            Harness(self.out).send(send_requested='false')

    def test_transport_timeout_never_exposes_message_in_diagnostics(self):
        h = Harness(self.out)
        h.fake.open_url = send_mod._open_url
        stderr, stdout, logs = StringIO(), StringIO(), StringIO()
        handler = logging.StreamHandler(logs)
        logging.getLogger().addHandler(handler)
        def timeout(command, **kwargs):
            raise subprocess.TimeoutExpired(command, 60, output=MAIL['body'], stderr=MAIL['subject'])
        path = Path(self.tmp.name) / 'input.json'
        path.write_text(json.dumps({**MAIL, 'idempotency_key': 'timeout-key'}))
        path.chmod(0o600)
        sender = send_mod.send
        def dispatch(*args, **kwargs):
            with patch.object(send_mod, 'send', sender):
                return h.send()
        try:
            with redirect_stderr(stderr), redirect_stdout(stdout), \
                    patch.object(send_mod.subprocess, 'run', timeout), \
                    patch.object(send_mod, 'send', dispatch):
                try:
                    code = send_mod.main(['send', '--send', '--json', str(path)])
                except Exception:
                    traceback.print_exc()
                    code = 1
        finally:
            logging.getLogger().removeHandler(handler)
        diagnostics = stderr.getvalue() + stdout.getvalue() + logs.getvalue()
        diagnostics += ''.join(p.read_text() for p in self.out.glob('*') if p.is_file())
        for sentinel in (*MAIL['to'], *MAIL['cc'], *MAIL['bcc'], MAIL['subject'],
                         'Line one.', 'Line two.', 'mailto:'):
            self.assertNotIn(sentinel, diagnostics)
        self.assertEqual(code, 1)
        self.assertRegex(stdout.getvalue(), r'transport_TimeoutExpired_[0-9a-f]{12}')

    def test_configured_internal_domain_allows_one_external_recipient(self):
        write_settings(self.home, ROOT, account=ACCOUNT, internal_domain='practice.test')
        result = Harness(self.out).send(to=['person-a@example.test', 'colleague@practice.test'])
        self.assertEqual(result.state, State.DONE)

    def test_account_comes_from_private_settings(self):
        self.assertEqual(send_mod.account(), ACCOUNT)
        write_settings(self.home, ROOT, account='another@practice.test')
        self.assertEqual(send_mod.account(), 'another@practice.test')
        self.config.chmod(0o644)
        with self.assertRaises(ValueError) as refusal:
            send_mod.account()
        self.assertIn(str(self.config), str(refusal.exception))

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
        self.assertEqual(logged[0].get('email_identity'),
                         {'mailbox_owner': 'fixture-owner', 'mailbox_account': ACCOUNT, 'item_id': 'hxstore-key:42'})

    def test_send_waits_for_store_identity_before_logging(self):
        h = Harness(self.out)
        snapshot = h.mailbox.snapshot
        h.mailbox.snapshot = lambda: replace(snapshot(),
            sent=tuple(replace(row, item_id=None) for row in h.mailbox.sent))
        result = h.send(record='client-001')
        self.assertEqual(result.state, State.PRESSED_UNCONFIRMED)
        self.assertFalse(any(verb == 'log-activity' for verb, _ in h.carr_calls))

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

    def test_uncertain_send_with_new_key_is_still_refused(self):
        h = Harness(self.out, on_send='closes_without_sending')
        self.assertEqual(h.send().state, State.PRESSED_UNCONFIRMED)
        self.assertEqual(h.send(key='new-key', body='changed body').state, State.DUPLICATE_KEY)
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

    def test_failed_activity_log_exits_nonzero_and_never_resends(self):
        for failure in ('reply', 'exception'):
            with self.subTest(failure=failure):
                h = Harness(self.out / failure)
                original = h.ports.carr
                def failed_log(verb, args):
                    if verb == 'log-activity':
                        if failure == 'exception':
                            raise OSError('synthetic-log-failure')
                        return {'ok': False}
                    return original(verb, args)
                h.ports.carr = failed_log
                result = h.send(record='client-001')
                self.assertFalse(result.ok)
                self.assertTrue(result.state.pressed)
                self.assertEqual(result.state.value, 'activity_log_failed')
                self.assertEqual(result.reason, 'confirmed_in_mailbox:log_failed')
                self.assertEqual(result.logged, ('log_failed',))
                path = Path(self.tmp.name) / 'input.json'
                path.write_text(json.dumps({**MAIL, 'idempotency_key': 'key-1'}))
                path.chmod(0o600)
                stdout = StringIO()
                with redirect_stdout(stdout), patch.object(send_mod, 'send', return_value=result):
                    self.assertEqual(send_mod.main(['send', '--json', str(path)]), 4)
                self.assertIn('activity_log_failed confirmed_in_mailbox:log_failed', stdout.getvalue())
                self.assertEqual(h.send().state, State.DUPLICATE_KEY)
                self.assertEqual(h.send(key='new-key').state, State.DUPLICATE_KEY)
                self.assertEqual(h.fake.presses(), 1)
                self.assertEqual(len(h.mailbox.sent), 1)

    def test_unmatched_record_still_reports_the_send(self):
        h = Harness(self.out)
        original = h.carr
        h.carr = lambda verb, args: ({'parties': []} if verb == 'find' else original(verb, args))
        h.ports.carr = h.carr
        result = h.send(record='client-404')
        self.assertEqual((result.state, result.logged), (State.DONE, ('record_not_matched',)))

    def test_default_adapter_provides_mailbox_and_record_ports(self):
        ports = send_mod.default_ports(ROOT)
        self.assertTrue(callable(ports.carr))
        self.assertTrue(callable(ports.mailbox.snapshot))

    def test_default_adapter_uses_configured_state_and_credential_pointer(self):
        ports = send_mod.default_ports()
        self.assertEqual(ports.out, self.home / '.local/state/carr/outlook-send')
        self.dialog.return_value.stdout = '{"ok": true}'
        self.assertEqual(ports.carr('find', {'query': 'fixture'}), {'ok': True})
        self.assertEqual(self.dialog.call_args.kwargs['env']['CARR_MCP_ENV'],
                         str((self.home / '.config/carr/mcp-tokens.env').resolve()))
        self.assertEqual(self.dialog.call_args.kwargs['cwd'], ROOT)

    def test_legacy_draft_keeps_mailto_bytes_and_refuses_missing_settings_before_open(self):
        spec = importlib.util.spec_from_file_location('outlook_draft_fixture', ROOT / 'bin/outlook-draft.py')
        draft = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(draft)
        self.assertEqual(draft.build_url({'to': ['recipient@example.test'], 'subject': 'A test',
                                          'body': 'One.\nTwo.', 'cc': ACCOUNT}),
                         'mailto:recipient@example.test?subject=A%20test&body=One.%0ATwo.&cc=sender%40example.test')
        self.config.unlink()
        with patch.object(draft, 'load_settings', lambda: load_settings(home=self.home)), \
                patch.object(sys, 'argv', ['outlook-draft.py', '--to', 'recipient@example.test', '--subject', 'A test']), \
                patch.object(draft.subprocess, 'run') as command:
            with self.assertRaises(ValueError) as refusal:
                draft.main()
            self.assertIn(str(self.config), str(refusal.exception))
        command.assert_not_called()

    def test_cua_refusal_is_a_failure_before_press(self):
        h = Harness(self.out)
        original = h.fake.__call__
        def refused(tool, args):
            if tool == 'get_window_state' and args['window_id'] != 1:
                return {'status': 'refused', 'refusal': {'code': 'window_unavailable'}}
            return original(tool, args)
        with patch.object(ui, 'cua', refused), patch.object(send_mod, 'account', lambda: ACCOUNT):
            with self.assertRaises(send_mod.UiFailure):
                send_mod.ComposeWindow(42, 100, ui.Waits()).elements()

    def test_event_with_attendees_is_saved_once_and_logged(self):
        h = Harness(self.out)
        result = event(h)
        self.assertEqual(result.state, State.DONE)
        logs = [args for verb, args in h.carr_calls if verb == 'log-activity']
        self.assertEqual(len(logs), 1)
        self.assertEqual(logs[0]['kind'], 'meeting')
        self.assertIn(EVENT['body'], logs[0]['detail'])
        self.assertEqual(event(h).state, State.DUPLICATE_KEY)
        self.assertEqual(len(h.mailbox.events), 1)

    def test_event_without_attendees_saves_without_invitations(self):
        h = Harness(self.out)
        self.assertEqual(event(h, attendees=[]).state, State.DONE)
        self.assertEqual(h.fake.presses(), 1)
        labels = [h.fake.calls[i][1] for i in range(len(h.fake.calls)) if h.fake.calls[i][0] == 'click']
        self.assertTrue(labels)

    def test_existing_calendar_event_prevents_opening_form(self):
        h = Harness(self.out, events=[EventRow(EVENT['subject'], int(EVENT['start'].timestamp()))])
        self.assertEqual(event(h).state, State.ALREADY_IN_MAILBOX)
        self.assertEqual(h.fake.calls, [])

    def test_event_mismatch_discards_without_inviting(self):
        h = Harness(self.out, corrupt=('location', 'wrong room'))
        result = event(h)
        self.assertEqual((result.state, result.reason), (State.FAILED, 'event_field_mismatch:location'))
        self.assertEqual((h.fake.presses(), h.fake.forms, self.ledger()), (0, {}, []))

    def test_event_unconfirmed_is_never_repeated_under_another_key(self):
        h = Harness(self.out, on_send='closes_without_sending')
        self.assertEqual(event(h).state, State.PRESSED_UNCONFIRMED)
        self.assertEqual(event(h, key='other', body='changed').state, State.DUPLICATE_KEY)
        self.assertEqual(h.fake.presses(), 1)

    def test_background_send_does_not_front_the_app(self):
        h = Harness(self.out)
        h.fake.background = True
        self.assertEqual(h.send().mode, 'background')
        self.assertFalse(any(tool == 'bring_to_front' for tool, _ in h.fake.calls))
        self.assertEqual(h.fake.presses(), 1)

    def test_accepted_background_noop_never_falls_back_to_another_press(self):
        h = Harness(self.out, on_send='stays_open')
        h.fake.background = True
        self.assertEqual(h.send().state, State.WINDOW_NOT_CLOSED)
        self.assertEqual(h.fake.presses(), 1)
        self.assertFalse(any(tool == 'bring_to_front' for tool, _ in h.fake.calls))

    def test_invalid_event_times_do_not_open_a_form(self):
        h = Harness(self.out)
        for overrides in ({'end': EVENT['start']}, {'start': EVENT['start'].replace(tzinfo=None)}):
            with self.assertRaises(ValueError):
                event(h, **overrides)
        self.assertEqual(h.fake.calls, [])

    def test_cli_refuses_a_public_message_file(self):
        path = Path(self.tmp.name) / 'message.json'
        path.write_text(json.dumps({**MAIL, 'idempotency_key': 'cli-key'}))
        path.chmod(0o644)
        with patch.object(send_mod, 'send') as sender:
            self.assertEqual(send_mod.main(['send', '--json', str(path)]), 1)
            sender.assert_not_called()

    def test_empty_idempotency_key_is_rejected(self):
        with self.assertRaises(ValueError):
            Harness(self.out).send(key='')

    def test_send_finds_the_exact_recipient_record_when_not_supplied(self):
        h = Harness(self.out)
        h.ports.contacts = lambda: [('person-a@example.test', 'client-001', 'C-1')]
        result = h.send()
        self.assertEqual(result.logged, ('C-1',))
        self.assertTrue(any(verb == 'find' for verb, _ in h.carr_calls))

    def test_non_outlook_handler_uses_new_mail_button(self):
        h = Harness(self.out)
        h.handler = 'com.example.browser'
        self.assertEqual(h.send().state, State.DONE)
        self.assertEqual(h.fake.presses(), 1)
        self.assertTrue(any(tool == 'click' and args.get('element_token') == '1:1'
                            for tool, args in h.fake.calls))

    def test_mailbox_with_twelve_toolbar_strips_allows_send(self):
        h = Harness(self.out)
        for wid in range(20, 32):
            h.fake.windows[wid] = {'window_id': wid, 'title': '', 'is_on_screen': False,
                                   'bounds': {'width': 1920, 'height': 30}}
        self.assertEqual(h.send().state, State.DONE)
        self.assertEqual(h.fake.presses(), 1)

    def test_mailbox_requires_both_controls(self):
        for missing in ('New Mail', 'Message List'):
            with self.subTest(missing=missing):
                h = Harness(self.out / missing)
                elements = h.fake.elements
                with patch.object(h.fake, 'elements', side_effect=lambda wid: [
                        e for e in elements(wid) if e.get('label') != missing]):
                    self.assertEqual(h.send().state, State.FAILED)
                self.assertEqual(h.fake.presses(), 0)

    def test_lone_draft_is_refused_without_quit_or_send(self):
        for kind in ('mail', 'event'):
            with self.subTest(kind=kind):
                h = Harness(self.out / kind)
                if kind == 'mail':
                    h.fake.open_url('mailto:person-z@example.test?subject=leave-alone')
                else:
                    h.fake('invoke_menu', {'path': ['File', 'New', 'Event']})
                del h.fake.windows[1]
                before = json.dumps(h.fake.forms, sort_keys=True)
                self.assertEqual(h.send().state, State.FAILED)
                self.assertEqual(h.fake.presses(), 0)
                self.assertEqual(json.dumps(h.fake.forms, sort_keys=True), before)
                self.assertFalse(any(tool == 'invoke_menu' and args.get('path') == ui.QUIT
                                     for tool, args in h.fake.calls))

    def test_existing_compose_is_left_untouched(self):
        h = Harness(self.out)
        h.fake.open_url('mailto:person-z@example.test?subject=other-draft&body=leave-alone')
        result = h.send()
        self.assertEqual((result.state, result.reason), (State.FAILED, 'compose_window_open'))
        self.assertEqual(h.fake.presses(), 0)
        self.assertEqual(len(h.fake.forms), 1)

    def test_unreadable_unassigned_window_does_not_hold_send(self):
        h = Harness(self.out)
        h.fake.windows[15] = {'window_id': 15, 'title': '', 'is_on_screen': False, 'on_current_space': None,
                              'bounds': {'x': 0, 'y': 580, 'width': 500, 'height': 500}}
        h.fake.unresolved.add(15)
        self.assertEqual(h.send().state, State.DONE)
        self.assertEqual(h.fake.presses(), 1)

    def test_untitled_compose_and_unassigned_shape_with_draft_controls_hold_send(self):
        for title, bounds, on_screen, space in (
                ('', {'x': 298, 'y': 52, 'width': 917, 'height': 911}, True, True),
                ('', {'x': 0, 'y': 580, 'width': 500, 'height': 500}, False, None)):
            with self.subTest(bounds=bounds):
                h = Harness(self.out / str(bounds['width']))
                h.fake.open_url('mailto:person-z@example.test?subject=&body=leave-alone')
                h.fake.windows[100].update(title=title, bounds=bounds, is_on_screen=on_screen,
                                           on_current_space=space)
                result = h.send()
                self.assertEqual((result.state, result.reason), (State.FAILED, 'compose_window_open'))
                self.assertEqual(h.fake.presses(), 0)
                self.assertEqual(len(h.fake.forms), 1)

    def test_failed_discard_is_reported(self):
        h = Harness(self.out, corrupt=('subject', 'changed'))
        with patch.object(send_mod.ComposeWindow, 'discard', lambda _: None):
            self.assertEqual(h.send().reason, 'discard_failed:mismatch:subject')
        self.assertEqual(h.fake.presses(), 0)

    def test_vendor_reference_is_accepted(self):
        h = Harness(self.out)
        self.assertEqual(h.send(record='V-CPA-001').logged, ('V-CPA-001',))

    def test_decoder_reads_only_sent_items_and_removes_private_copy(self):
        home = Path(self.tmp.name) / 'home'
        repo = Path(self.tmp.name) / 'checkout'
        (repo / '.git').mkdir(parents=True)
        workspaces = []
        write_settings(home, repo, account=ACCOUNT, internal_domain='company.test')
        vendor = home / 'carr-local/hxstore-reverse-engineering'
        vendor.mkdir(parents=True)
        store = home / 'Library/Group Containers/UBF8T346G9.Office/Outlook/Outlook 15 Profiles/Main Identity/HxStore.hxd'
        store.parent.mkdir(parents=True)
        store.write_bytes(b'fixture-store')
        def decode(command, **kwargs):
            work = Path(command[2]).parent
            workspaces.append(work)
            self.assertTrue(work.is_relative_to((repo / 'out/outlook-intake').resolve()))
            self.assertTrue(work.name.startswith('work-'))
            self.assertEqual(stat.S_IMODE(work.stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE(Path(command[3]).stat().st_mode), 0o600)
            self.assertEqual(Path(command[2]).read_bytes(), b'fixture-store')
            self.assertEqual(stat.S_IMODE(Path(command[2]).stat().st_mode), 0o600)
            con = sqlite3.connect(command[3])
            con.executescript("""
                CREATE TABLE messages(id, sender, recipients, subject, sent_unix, message_id, local_keys);
                CREATE TABLE message_folders(message_id, folder_id);
                CREATE TABLE folders(id, name);
                CREATE TABLE events(subject, start_unix);
                INSERT INTO folders VALUES(1, 'Sent Items'), (2, 'Drafts');
                INSERT INTO messages VALUES(1, 'sender@example.test', 'person-a@example.test', 'sent-001', 1800000000, '<sent-001@example.test>', '42'),
                    (2, 'sender@example.test', 'person-a@example.test', 'draft-001', 1800000000, null, '43'),
                    (3, 'sender@example.test', 'person-a@example.test', 'sent-001', 1800000001, '<sent-001@example.test>', '44');
                INSERT INTO message_folders VALUES(1, 1), (2, 2), (3, 1);
                INSERT INTO events VALUES('event-001', 1893596400);
            """)
            con.close()
            return subprocess.CompletedProcess(command, 0)
        def copy(home_arg, work):
            self.assertEqual(home_arg, home)
            (work / 'store.hxd').write_bytes(b'fixture-store')
            (work / 'store.hxd').chmod(0o600)
        with patch('lib.outlook_intake._snapshot', side_effect=copy) as copier, patch.object(send_mod.subprocess, 'run', decode):
            snap = send_mod.DecodedMailbox(home, repo).snapshot()
        copier.assert_called_once()
        self.assertEqual([m.subject for m in snap.sent], ['sent-001', 'sent-001'])
        self.assertEqual([m.item_id for m in snap.sent], ['hxstore-key:42', 'hxstore-key:44'])
        self.assertEqual(snap.events, (EventRow('event-001', 1893596400),))
        self.assertFalse(any(work.exists() for work in workspaces))
        self.assertEqual(list((repo / 'out/outlook-intake').iterdir()), [])
        self.assertEqual(store.read_bytes(), b'fixture-store')

    def test_decoder_failure_removes_copies_outside_git(self):
        home = Path(self.tmp.name) / 'home'
        repo = Path(self.tmp.name) / 'checkout'
        repo.mkdir()
        write_settings(home, repo, account=ACCOUNT, internal_domain='company.test')
        vendor = home / 'carr-local/hxstore-reverse-engineering'
        vendor.mkdir(parents=True)
        store = home / 'Library/Group Containers/UBF8T346G9.Office/Outlook/Outlook 15 Profiles/Main Identity/HxStore.hxd'
        store.parent.mkdir(parents=True)
        store.write_bytes(b'synthetic-mailbox')
        workspaces = []
        def timeout(command, **kwargs):
            workspaces.append(Path(command[2]).parent)
            raise subprocess.TimeoutExpired(command, 1)
        from lib.outlook_intake import DecodeError
        with patch('lib.outlook_intake._snapshot'), patch.object(send_mod.subprocess, 'run', timeout):
            with self.assertRaises(DecodeError):
                send_mod.DecodedMailbox(home, repo).snapshot()
        self.assertTrue(workspaces)
        self.assertFalse(any(work.exists() for work in workspaces))
        self.assertEqual(list((repo / 'out/outlook-intake').iterdir()), [])



    def test_transport_exception_after_latching_key_is_not_retried(self):
        h = Harness(self.out)
        with patch.object(send_mod.ComposeWindow, 'press', side_effect=ui.CuaError('lost reply')):
            self.assertEqual(h.send().state, State.WINDOW_NOT_CLOSED)
        self.assertEqual(h.send().state, State.DUPLICATE_KEY)

    def test_typing_during_foreground_lookup_discards_without_taking_focus(self):
        h = Harness(self.out)
        def resumed_typing():
            h.front_idle = 0
            return h.previous
        h.frontmost = resumed_typing
        result = h.send()
        self.assertEqual(result.state, State.NOT_IDLE)
        self.assertEqual(h.fake.presses(), 0)
        self.assertEqual(h.fake.forms, {})
        self.assertFalse(any(tool == 'bring_to_front' for tool, _ in h.fake.calls))

    def test_typing_after_taking_focus_discards_and_restores_previous_app(self):
        h = Harness(self.out)
        original = h.fake.__call__
        def resumed_typing(tool, args):
            result = original(tool, args)
            if tool == 'bring_to_front':
                h.front_idle = 0
            return result
        h.transport = resumed_typing
        result = h.send()
        self.assertEqual(result.state, State.NOT_IDLE)
        self.assertEqual(h.fake.presses(), 0)
        self.assertEqual(h.fake.forms, {})
        self.assertIn(('activate', h.previous), h.fake.calls)

    def test_foreground_failures_always_restore_previous_app(self):
        for failed_tool in ('bring_to_front', 'invoke_menu'):
            with self.subTest(tool=failed_tool):
                h = Harness(self.out / failed_tool)
                original = h.fake.__call__
                def failure(tool, args):
                    result = original(tool, args) if tool != failed_tool else None
                    if tool == failed_tool:
                        raise subprocess.TimeoutExpired(['synthetic-transport'], 1)
                    return result
                h.transport = failure
                self.assertEqual(h.send().state, State.WINDOW_NOT_CLOSED)
                self.assertEqual(h.fake.presses(), 0)
                self.assertIn(('activate', h.previous), h.fake.calls)

    def test_front_fallback_requires_readable_previous_app(self):
        h = Harness(self.out)
        h.previous = None
        self.assertEqual(h.send().state, State.WINDOW_NOT_CLOSED)
        self.assertEqual(h.fake.presses(), 0)
        self.assertFalse(any(tool == 'bring_to_front' for tool, _ in h.fake.calls))

    def test_front_fallback_rechecks_idle_after_background_refusal(self):
        h = Harness(self.out)
        h.front_idle = 10
        self.assertEqual(h.send().state, State.NOT_IDLE)
        self.assertEqual(h.fake.forms, {})
        self.assertEqual(h.fake.presses(), 0)
        self.assertFalse(any(tool == 'bring_to_front' for tool, _ in h.fake.calls))

    def test_status_line_carries_no_address_or_subject(self):
        result = Harness(self.out).send(record='client-001')
        line = result.line('send')
        self.assertEqual(line, 'outlook-send done confirmed_in_mailbox mode=front logged=C-1')

    def test_invalid_address_is_refused_before_anything_runs(self):
        with self.assertRaises(ValueError):
            Harness(self.out).send(to=['not an address'])


class OutboxTests(unittest.TestCase):
    def setUp(self):
        from lib import outlook_outbox
        self.worker = outlook_outbox
        self.tmp = self.enterContext(tempfile.TemporaryDirectory())
        self.home = Path(self.tmp) / 'home'
        write_settings(self.home, ROOT, owner='joe', account=ACCOUNT,
                       internal_domain='example.test', services=['outbox'],
                       partner_cc='partner@example.test',
                       send_state=str(Path(self.tmp) / 'send'))
        self.settings = load_settings(self.home)
        self.enterContext(patch.object(send_mod, 'load_settings', lambda: self.settings))
        self.mailbox = FakeMailbox()
        self.sent = []
        self.calls = []
        self.draft = dict(proposal_id='11111111-2222-4333-8444-555555555555', version=2,
                          subject='Submitted subject', body='Submitted final text.',
                          to=['client@example.test'], cc=['partner@example.test'])
        import hashlib
        self.draft['sha256'] = hashlib.sha256(json.dumps(
            dict(subject=self.draft['subject'], body=self.draft['body']),
            ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()

        def open_form(item):
            test = self
            class Form:
                def read(self):
                    return send_mod.fields(item)
                def press(self, **kwargs):
                    test.sent.append(item)
                    test.mailbox.sent.append(SentRow(ACCOUNT, '; '.join(item.recipients),
                                                    item.subject, int(NOW), 'sent-42'))
                    return 'fake'
                def closed(self):
                    return True
                def discard(self):
                    pass
            return Form()
        self.clock = Clock()
        self.ports = Ports(open_form, self.mailbox, lambda: 600, self.carr,
                           now=self.clock.now, sleep=self.clock.sleep, out=self.settings.send_state)

    def carr(self, verb, args):
        self.calls.append((verb, dict(args)))
        if verb == 'outbox-for-sender':
            return dict(schema_version='email-outbox.v1', owner='joe', drafts=[self.draft])
        if verb == 'mail-contact-policy':
            return {'recipients': [{'email': e, 'blocked': False} for e in args['emails']]}
        return dict(ok=True, proposal_id=self.draft['proposal_id'], status=args.get('status'))

    def test_unreadable_attachment_fails_one_draft_and_next_draft_still_sends(self):
        root = Path(self.tmp).resolve() / 'approved'
        root.mkdir()
        packet = root / 'synthetic-unreadable.pdf'
        packet.write_bytes(b'synthetic bytes')
        self.settings = replace(self.settings, attachment_root=root, attachment_max_bytes=1000)
        self.draft['attachments'] = [dict(path=str(packet), sha256=send_mod._digest(packet), size=packet.stat().st_size)]
        second = {**self.draft, 'proposal_id': '22222222-2222-4333-8444-555555555555', 'attachments': []}
        carr = self.ports.carr
        def two_drafts(verb, args):
            if verb == 'outbox-for-sender':
                return dict(schema_version='email-outbox.v1', owner='joe', drafts=[self.draft, second])
            return carr(verb, args)
        self.ports.carr = two_drafts
        real_open = open
        def unreadable(path, *args, **kwargs):
            if Path(path) == packet:
                raise PermissionError('synthetic private path must not escape')
            return real_open(path, *args, **kwargs)
        packet.chmod(0)
        output = StringIO()
        with patch('builtins.open', side_effect=unreadable), redirect_stdout(output), redirect_stderr(output):
            counts = self.worker.run(self.settings, self.ports)
        self.assertEqual((counts['failed'], counts['sent']), (1, 1))
        receipts = [args for verb, args in self.calls if verb == 'record-email-sent']
        self.assertEqual([(r['proposal_id'], r['status']) for r in receipts], [
            (self.draft['proposal_id'], 'failed'), (second['proposal_id'], 'sent')])
        self.assertEqual(receipts[0]['reason'], 'attachment_unreadable')
        self.assertEqual(len(self.sent), 1)
        private = json.dumps(receipts) + output.getvalue()
        for value in (packet.name, str(packet), self.draft['subject'], 'synthetic private path'):
            self.assertNotIn(value, private)

    def test_recovered_older_ledger_retains_the_observed_sent_time(self):
        self.worker.run(self.settings, self.ports)
        ledger = self.ports.out / send_mod.LEDGER
        rows = [json.loads(line) for line in ledger.read_text().splitlines()]
        for row in rows:
            row.pop('sent_at', None)
        ledger.write_text(''.join(json.dumps(row) + '\n' for row in rows))
        self.assertEqual(self.worker.run(self.settings, self.ports)['sent'], 1)
        self.mailbox.sent.clear()
        self.assertEqual(self.worker.run(self.settings, self.ports)['sent'], 1)
        self.assertEqual(len(self.sent), 1)

    def test_old_confirmed_sent_without_time_never_records_failed_after_a_day(self):
        with patch.object(self.worker, '_save', side_effect=OSError('synthetic crash before receipt')):
            with self.assertRaises(OSError):
                self.worker.run(self.settings, self.ports)
        ledger = self.ports.out / send_mod.LEDGER
        rows = [json.loads(line) for line in ledger.read_text().splitlines()]
        for row in rows:
            row.pop('sent_at', None)
        ledger.write_text(''.join(json.dumps(row) + '\n' for row in rows))
        self.mailbox.sent.clear()
        self.clock.t += 25 * 3600
        for broken in (False, True):
            self.mailbox.broken = broken
            output = StringIO()
            logger = logging.getLogger('lib.outlook_outbox')
            handler = logging.StreamHandler(output)
            logger.addHandler(handler)
            try:
                counts = self.worker.run(self.settings, self.ports)
            finally:
                logger.removeHandler(handler)
            self.assertEqual(counts['failed'], 0)
            self.assertEqual((counts['deferred'], counts['sent_unrecorded']), (1, 1))
            self.assertIn('sent_time_unavailable', output.getvalue())
            for value in (self.draft['subject'], *self.draft['to'], *self.draft['cc']):
                self.assertNotIn(value, output.getvalue())
        self.assertEqual(len(self.sent), 1)
        self.assertFalse(any(verb == 'record-email-sent' for verb, _ in self.calls))
        self.assertFalse(list((self.ports.out / 'outbox').glob('*.json')))

    def test_pending_sent_receipt_recovers_observed_time_without_resending(self):
        draft = self.worker.SubmittedDraft.parse(self.draft, self.settings)
        root = self.ports.out / 'outbox'
        root.mkdir(parents=True)
        receipt = dict(idempotency_key=draft.key, proposal_id=draft.proposal_id,
                       base_version=draft.version, sha256=draft.sha256,
                       status='sent', sent_item_id='synthetic-old-receipt')
        path = root / f'{draft.key}.json'
        path.write_text(json.dumps(receipt))
        self.mailbox.sent.append(SentRow(ACCOUNT, '; '.join(draft.mail.recipients), draft.mail.subject,
                                         int(NOW), 'synthetic-old-receipt'))
        self.worker.run(self.settings, self.ports)
        [recorded] = [args for verb, args in self.calls if verb == 'record-email-sent']
        self.assertEqual(recorded['sent_at'], datetime.fromtimestamp(NOW, timezone.utc).isoformat())
        self.assertEqual(recorded['sender_account'], ACCOUNT)
        self.assertFalse(path.exists())
        self.assertEqual(self.sent, [])

    def test_unconfirmed_submitted_files_defer_without_a_sent_receipt(self):
        root = Path(self.tmp).resolve() / 'approved'
        root.mkdir()
        packet = root / 'synthetic-packet.pdf'
        packet.write_bytes(b'synthetic attachment')
        from dataclasses import replace
        self.settings = replace(self.settings, attachment_root=root, attachment_max_bytes=1000)
        self.draft['attachments'] = [dict(path=str(packet), sha256=send_mod._digest(packet), size=packet.stat().st_size)]
        self.ports.script_send = lambda mail: 'sent'
        with patch.object(send_mod, 'default_sender', lambda: f'{ACCOUNT}_ActiveSyncExchange_HxS'):
            counts = self.worker.run(self.settings, self.ports)
        self.assertEqual(counts['deferred'], 1)
        self.assertFalse(any(v == 'record-email-sent' for v, _ in self.calls))

    def test_approved_attachment_identity_cannot_change_before_sending(self):
        from dataclasses import replace
        root = Path(self.tmp).resolve() / 'approved'
        root.mkdir()
        packet = root / 'synthetic-packet.pdf'
        packet.write_bytes(b'original synthetic bytes')
        self.settings = replace(self.settings, attachment_root=root, attachment_max_bytes=1000)
        digest = send_mod._digest(packet)
        self.draft['attachments'] = [dict(path=str(packet), sha256=digest, size=packet.stat().st_size)]
        packet.write_bytes(b'changed synthetic bytes')
        counts = self.worker.run(self.settings, self.ports)
        self.assertEqual(counts['failed'], 1)
        self.assertEqual(self.sent, [])
        packet.write_bytes(b'original synthetic bytes')
        alias = root / 'alias.pdf'
        alias.symlink_to(packet)
        self.draft['attachments'] = [dict(path=str(alias), sha256=digest, size=packet.stat().st_size)]
        with self.assertRaisesRegex(ValueError, '^submission_attachment_mismatch$'):
            self.worker.SubmittedDraft.parse(self.draft, self.settings)

    def test_submitted_attachment_size_must_match_frozen_submission(self):
        from dataclasses import replace
        root = Path(self.tmp).resolve() / 'approved'
        root.mkdir()
        packet = root / 'synthetic.pdf'
        packet.write_bytes(b'synthetic attachment')
        self.settings = replace(self.settings, attachment_root=root, attachment_max_bytes=1000)
        self.draft['attachments'] = [dict(path=str(packet), sha256=send_mod._digest(packet), size=20)]
        draft = self.worker.SubmittedDraft.parse(self.draft, self.settings)
        self.assertEqual(draft.mail.attachments[0].size, 20)
        for size in (19, True, -1, '20'):
            with self.subTest(size=size):
                self.draft['attachments'][0]['size'] = size
                with self.assertRaisesRegex(ValueError, '^submission_attachment_mismatch$'):
                    self.worker.SubmittedDraft.parse(self.draft, self.settings)
        del self.draft['attachments'][0]['size']
        with self.assertRaisesRegex(ValueError, '^invalid_submission$'):
            self.worker.SubmittedDraft.parse(self.draft, self.settings)

    def test_confirmed_attachment_receipt_replays_after_file_changes(self):
        root = Path(self.tmp).resolve() / 'approved'
        root.mkdir()
        packet = root / 'synthetic.pdf'
        packet.write_bytes(b'abc')
        digest = 'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad'
        self.settings = replace(self.settings, attachment_root=root, attachment_max_bytes=1000)
        self.draft['attachments'] = [dict(path=str(packet), sha256=digest, size=3)]
        def dispatch(mail):
            self.assertEqual(mail.attachments[0].path.read_bytes(), b'abc')
            self.sent.append(mail)
            self.mailbox.sent.append(SentRow(ACCOUNT, '; '.join(mail.recipients), mail.subject,
                                             int(NOW), 'synthetic-changed-file'))
            packet.write_bytes(b'changed after dispatch')
            return 'sent'
        self.ports.script_send = dispatch
        carr = self.ports.carr
        def offline_receipt(verb, args):
            reply = carr(verb, args)
            if verb == 'record-email-sent':
                raise OSError('synthetic outage')
            return reply
        self.ports.carr = offline_receipt
        with patch.object(send_mod, 'default_sender', lambda: f'{ACCOUNT}_ActiveSyncExchange_HxS'):
            first = self.worker.run(self.settings, self.ports)
        self.assertEqual((first['sent'], first['receipt_failed']), (1, 1))
        self.ports.carr = carr
        self.worker.run(self.settings, self.ports)
        self.assertEqual(len(self.sent), 1)
        self.assertEqual((self.sent[0].attachments[0].sha256, self.sent[0].attachments[0].size), (digest, 3))
        receipts = [a for v, a in self.calls if v == 'record-email-sent']
        self.assertEqual(len(receipts), 2)
        self.assertEqual(receipts[0], receipts[1])
        self.assertEqual(receipts[0]['status'], 'sent')
        self.assertEqual(list((self.ports.out / 'outbox').glob('*.json')), [])

    def test_confirmed_files_keep_names_out_of_ledger_receipts_and_diagnostics(self):
        from dataclasses import replace
        import contextlib
        import io
        root = Path(self.tmp).resolve() / 'approved'
        root.mkdir()
        paths = [root / 'synthetic-packet.pdf', root / 'synthetic-plan.pdf']
        for path in paths:
            path.write_bytes(b'synthetic attachment')
        self.settings = replace(self.settings, attachment_root=root, attachment_max_bytes=1000)
        self.draft['attachments'] = [dict(path=str(p), sha256=send_mod._digest(p), size=p.stat().st_size) for p in paths]
        def dispatch(mail):
            self.sent.append(mail)
            self.mailbox.sent.append(SentRow(ACCOUNT, '; '.join(mail.recipients), mail.subject,
                                             int(NOW), 'synthetic-files-sent'))
            return 'sent'
        self.ports.script_send = dispatch
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output), patch.object(
                send_mod, 'default_sender', lambda: f'{ACCOUNT}_ActiveSyncExchange_HxS'):
            counts = self.worker.run(self.settings, self.ports)
            self.worker.run(self.settings, self.ports)
        self.assertEqual(counts['sent'], 1)
        self.assertEqual(len(self.sent), 1)
        receipts = [args for verb, args in self.calls if verb == 'record-email-sent']
        self.assertEqual(receipts[0]['sender_account'], ACCOUNT)
        self.assertEqual(receipts[0]['sent_at'], datetime.fromtimestamp(NOW, timezone.utc).isoformat())
        ledger = (self.ports.out / send_mod.LEDGER).read_text()
        self.assertIn('"count": 2', ledger)
        for path in paths:
            self.assertIn(send_mod._digest(path), ledger)
            self.assertNotIn(path.name, ledger + json.dumps(receipts) + output.getvalue())

    def test_submitted_reply_sends_exact_text_once_with_partner_copy_and_receipt(self):
        with patch.object(send_mod, '_confirm_dispatch', side_effect=AssertionError('native dialog')):
            self.assertEqual(self.worker.run(self.settings, self.ports)['sent'], 1)
            self.worker.run(self.settings, self.ports)
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.sent[0], send_mod.Mail(('client@example.test',),
            ('partner@example.test',), (), 'Submitted subject', 'Submitted final text.'))
        receipts = [args for verb, args in self.calls if verb == 'record-email-sent']
        self.assertEqual(receipts[0]['status'], 'sent')
        self.assertEqual(receipts[0]['sent_item_id'], 'sent-42')
        self.assertEqual(receipts[0]['base_version'], 2)
        self.assertEqual(receipts[0]['sha256'], self.draft['sha256'])

    def test_open_partner_draft_defers_without_quit_relaunch_or_failure_receipt(self):
        for title in (f'Partner draft • {ACCOUNT}', '', None):
            for recovery in (False, True):
                with self.subTest(title=title, recovery=recovery):
                    fake = FakeOutlook(self.clock, self.mailbox)
                    fake.open_url('mailto:other@example.test?subject=Partner%20draft&body=keep')
                    fake.windows[100]['title'] = title
                    fake.windows[1]['is_on_screen'] = not recovery
                    fake.windows[100]['is_on_screen'] = not recovery
                    self.calls.clear()
                    self.ports.open_form = lambda item: send_mod.open_form(item, ui.Waits(
                        quit_seconds=0, sync_seconds=0, new_window_seconds=0))
                    with patch.object(ui, 'cua', fake), \
                            patch.object(ui, 'outlook_pid', return_value=42), \
                            patch.object(ui.subprocess, 'run', return_value=
                                subprocess.CompletedProcess([], 1, b'', b'')) as command:
                        counts = self.worker.run(self.settings, self.ports)
                    self.assertEqual(counts['deferred'], 1)
                    self.assertEqual(counts['compose_window_open'], 1)
                    self.assertEqual((counts['failed'], counts['sent']), (0, 0))
                    self.assertEqual([v for v, _ in self.calls if v == 'record-email-sent'], [])
                    self.assertEqual(fake.presses(), 0)
                    self.assertEqual(len(fake.forms), 1)
                    self.assertFalse(any(tool == 'invoke_menu' for tool, _ in fake.calls))
                    if recovery:
                        command.assert_called_once_with(
                            ['ioreg', '-a', '-d1', '-k', 'IOConsoleUsers', '-r'],
                            capture_output=True, timeout=5)
                    else:
                        command.assert_not_called()

    def test_locked_screen_defers_without_failure_receipt(self):
        locked = send_mod.Result(send_mod.State.FAILED, 'screen_locked')
        with patch.object(send_mod, 'send_submitted', return_value=locked):
            counts = self.worker.run(self.settings, self.ports)
        self.assertEqual((counts['deferred'], counts['failed'], counts['sent']), (1, 0, 0))
        self.assertEqual([v for v, _ in self.calls if v == 'record-email-sent'], [])

    def test_invalid_rows_record_failure_and_do_not_block_later_draft(self):
        bad = dict(self.draft, cc=['wrong@example.test'])
        good = dict(self.draft, proposal_id='22222222-2222-4222-8222-222222222222')
        carr = self.ports.carr
        self.ports.carr = lambda verb, args: (dict(schema_version='email-outbox.v1', owner='joe', drafts=[bad, {'version': 0}, good]) if verb == 'outbox-for-sender' else carr(verb, args))
        counts = self.worker.run(self.settings, self.ports)
        self.assertEqual((counts['failed'], counts['sent']), (2, 1))
        receipts = [args for verb, args in self.calls if verb == 'record-email-sent']
        self.assertEqual(receipts[0]['reason'], 'submission_recipients_invalid')
        self.assertEqual(receipts[1]['status'], 'sent')
        self.assertEqual(len(self.sent), 1)

    def test_tampered_and_malformed_rows_fail_without_sending(self):
        for change, reason in ((dict(body='tampered'), 'submission_hash_mismatch'),
                               (dict(subject=''), 'invalid_submission'),
                               (dict(to=[42]), 'invalid_submission')):
            with self.subTest(change=change):
                original = self.draft
                self.draft = dict(original, **change)
                self.assertEqual(self.worker.run(self.settings, self.ports)['failed'], 1)
                self.assertEqual(self.calls[-1][1]['reason'], reason)
                self.draft = original
        self.assertEqual(self.sent, [])

    def test_aborted_press_records_failure(self):
        original = self.ports.open_form
        def open_form(item):
            form = original(item)
            def abort(**kwargs):
                raise send_mod.UserActive('user_active')
            form.press = abort
            return form
        self.ports.open_form = open_form
        self.assertEqual(self.worker.run(self.settings, self.ports)['failed'], 1)
        [receipt] = [args for verb, args in self.calls if verb == 'record-email-sent']
        self.assertEqual(receipt['reason'], 'aborted_before_dispatch')
        self.assertEqual(self.sent, [])

    def test_aborted_reply_resubmission_sends_once_and_blocks_another(self):
        original = self.ports.open_form
        def abort_form(item):
            form = original(item)
            def abort(**kwargs):
                raise send_mod.UserActive('user_active')
            form.press = abort
            return form
        self.ports.open_form = abort_form
        self.assertEqual(self.worker.run(self.settings, self.ports)['failed'], 1)
        self.assertEqual(self.calls[-1][1]['reason'], 'aborted_before_dispatch')
        self.ports.open_form = original
        self.draft['version'] = 4
        counts = self.worker.run(self.settings, self.ports)
        self.assertEqual(counts['sent'], 1, self.calls[-1][1].get('reason'))
        self.draft['version'] = 6
        self.assertEqual(self.worker.run(self.settings, self.ports)['failed'], 1)
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.calls[-1][1]['reason'], 'duplicate_key:key_already_pressed')

    def test_ambiguous_latch_expires_without_resending(self):
        self.settings.send_state.mkdir(parents=True)
        draft = self.worker.SubmittedDraft.parse(self.draft, self.settings)
        send_mod.Ledger(self.settings.send_state / send_mod.LEDGER).append(
            draft.key, 'send', 'pressing', send_mod.fingerprint(draft.mail), NOW)
        self.assertEqual(self.worker.run(self.settings, self.ports)['deferred'], 1)
        self.clock.t += send_mod.DUPLICATE_WINDOW
        self.assertEqual(self.worker.run(self.settings, self.ports)['failed'], 1)
        [receipt] = [args for verb, args in self.calls if verb == 'record-email-sent']
        self.assertEqual(receipt['reason'], f'unconfirmed_after_{send_mod.DUPLICATE_WINDOW}')
        self.assertEqual(self.sent, [])

    def test_press_refusal_remains_ambiguous_until_window_expires(self):
        original = self.ports.open_form
        def open_form(item):
            form = original(item)
            def refuse(**kwargs):
                raise send_mod.UiFailure('press_refused')
            form.press = refuse
            form.closed = lambda: False
            return form
        self.ports.open_form = open_form
        self.assertEqual(self.worker.run(self.settings, self.ports)['deferred'], 1)
        self.clock.t += send_mod.DUPLICATE_WINDOW
        self.assertEqual(self.worker.run(self.settings, self.ports)['failed'], 1)
        [receipt] = [args for verb, args in self.calls if verb == 'record-email-sent']
        self.assertEqual(receipt['reason'], f'unconfirmed_after_{send_mod.DUPLICATE_WINDOW}')
        self.assertEqual(self.sent, [])

    def test_aborted_latch_survives_receipt_save_failure(self):
        original = self.ports.open_form
        def open_form(item):
            form = original(item)
            def abort(**kwargs):
                raise send_mod.UserActive('user_active')
            form.press = abort
            return form
        self.ports.open_form = open_form
        with patch.object(self.worker, '_save', side_effect=OSError('crash')):
            with self.assertRaises(OSError):
                self.worker.run(self.settings, self.ports)
        self.assertEqual(self.worker.run(self.settings, self.ports)['failed'], 1)
        [receipt] = [args for verb, args in self.calls if verb == 'record-email-sent']
        self.assertEqual(receipt['reason'], 'aborted_before_dispatch')
        self.assertEqual(self.sent, [])

    def test_freshness_stamp_failure_does_not_change_completed_outbox_outcome(self):
        with patch.object(self.worker, 'load_settings', return_value=self.settings), \
                patch.object(type(self.settings), 'require_repo'), \
                patch.object(self.worker.send, 'default_ports', return_value=self.ports), \
                patch('lib.outlook_runtime.stamp', side_effect=RuntimeError('synthetic stamp failure')):
            self.assertEqual(self.worker.main(), 0)
        self.assertEqual(len(self.sent), 1)
        self.assertEqual([args['status'] for verb, args in self.calls if verb == 'record-email-sent'], ['sent'])

    def test_main_reports_real_failure_reason(self):
        import io
        output = io.StringIO()
        with patch.object(self.worker, 'load_settings', return_value=self.settings), \
                patch.object(type(self.settings), 'require_repo'), \
                patch.object(self.worker, 'run', return_value=dict(sent=1, failed=1, deferred=0, receipt_failed=0)), \
                patch('sys.stdout', output):
            self.assertEqual(self.worker.main(), 1)
        receipt = json.loads(output.getvalue().split('outlook-outbox-receipt ', 1)[1])
        self.assertEqual((receipt['state'], receipt['reason']), ('failed', 'send_failed'))

    def test_main_reports_deferred(self):
        with patch.object(self.worker, 'load_settings', return_value=self.settings), patch.object(self.settings.__class__, 'require_repo'), patch.object(self.worker, 'run', return_value=dict(sent=0, failed=0, deferred=1, receipt_failed=0)):
            self.assertEqual(self.worker.main(), 1)

    def test_other_partner_identity_cannot_send_on_this_machine(self):
        carr = self.ports.carr
        self.ports.carr = lambda verb, args: (dict(schema_version='email-outbox.v1', owner='dell',
            drafts=[self.draft]) if verb == 'outbox-for-sender' else carr(verb, args))
        with self.assertRaises(ValueError):
            self.worker.run(self.settings, self.ports)
        self.assertEqual(self.sent, [])

    def test_lost_receipt_ack_replays_exact_receipt_after_draft_leaves_outbox(self):
        carr = self.ports.carr
        receipts = []
        def flaky(verb, args):
            if verb == 'record-email-sent':
                receipts.append(dict(args))
                if len(receipts) == 1:
                    raise OSError('lost acknowledgement')
            if verb == 'outbox-for-sender' and receipts:
                return dict(schema_version='email-outbox.v1', owner='joe', drafts=[])
            return carr(verb, args)
        self.ports.carr = flaky
        self.assertEqual(self.worker.run(self.settings, self.ports)['receipt_failed'], 1)
        [pending] = (self.settings.send_state / 'outbox').glob('*.json')
        self.assertEqual(stat.S_IMODE(pending.stat().st_mode), 0o600)
        text = pending.read_text()
        self.assertNotIn(self.draft['body'], text)
        self.worker.run(self.settings, self.ports)
        self.assertEqual(receipts, [receipts[0], receipts[0]])
        self.assertEqual(len(self.sent), 1)
        self.assertFalse(pending.exists())

    def test_crash_after_send_before_receipt_recovers_without_sending_again(self):
        with patch.object(self.worker, '_save', side_effect=OSError('crash')):
            with self.assertRaises(OSError):
                self.worker.run(self.settings, self.ports)
        self.assertEqual(self.worker.run(self.settings, self.ports)['sent'], 1)
        self.assertEqual(len(self.sent), 1)
        [receipt] = [args for verb, args in self.calls if verb == 'record-email-sent']
        self.assertEqual(receipt['sent_item_id'], 'sent-42')

    def test_crash_after_press_recovers_only_with_exact_sent_confirmation(self):
        append = send_mod.Ledger.append
        def crash_on_done(ledger, key, kind, state, *args):
            if state == 'done':
                raise OSError('crash before done latch')
            append(ledger, key, kind, state, *args)
        with patch.object(send_mod.Ledger, 'append', crash_on_done):
            with self.assertRaises(OSError):
                self.worker.run(self.settings, self.ports)
        self.assertEqual(self.worker.run(self.settings, self.ports)['sent'], 1)
        self.assertEqual(len(self.sent), 1)

    def test_ambiguous_attempt_waits_without_another_send_or_false_receipt(self):
        self.settings.send_state.mkdir(parents=True)
        draft = self.worker.SubmittedDraft.parse(self.draft, self.settings)
        send_mod.Ledger(self.settings.send_state / send_mod.LEDGER).append(
            draft.key, 'send', 'pressing', send_mod.fingerprint(draft.mail), NOW)
        self.mailbox.sent.append(SentRow(ACCOUNT, 'wrong@example.test; partner@example.test',
                                        draft.mail.subject, int(NOW), 'unrelated-42'))
        self.assertEqual(self.worker.run(self.settings, self.ports)['deferred'], 1)
        self.assertEqual(self.sent, [])
        self.assertFalse(any(verb == 'record-email-sent' for verb, _ in self.calls))

    def test_delayed_sent_items_visibility_reconciles_next_run_without_resending(self):
        self.ports.mailbox = FakeMailbox()
        self.assertEqual(self.worker.run(self.settings, self.ports)['deferred'], 1)
        self.ports.mailbox = self.mailbox
        self.assertFalse(any(verb == 'record-email-sent' for verb, _ in self.calls))
        self.assertEqual(self.worker.run(self.settings, self.ports)['sent'], 1)
        self.assertEqual(len(self.sent), 1)
        [receipt] = [args for verb, args in self.calls if verb == 'record-email-sent']
        self.assertEqual(receipt['sent_item_id'], 'sent-42')

    def test_contact_policy_refusal_records_failed_receipt_without_sending(self):
        carr = self.ports.carr
        def blocked(verb, args):
            if verb == 'mail-contact-policy':
                return {'recipients': [{'email': e, 'blocked': True} for e in args['emails']]}
            return carr(verb, args)
        self.ports.carr = blocked
        self.assertEqual(self.worker.run(self.settings, self.ports)['failed'], 1)
        self.assertEqual(self.sent, [])
        [receipt] = [args for verb, args in self.calls if verb == 'record-email-sent']
        self.assertEqual(receipt['status'], 'failed')
        self.assertEqual(receipt['reason'], 'refused:do_not_contact')

    def test_failed_receipt_ack_replays_without_retrying_the_send(self):
        self.ports.open_form = lambda mail: (_ for _ in ()).throw(send_mod.UiFailure('synthetic_failure'))
        carr = self.ports.carr
        receipts = []
        def flaky(verb, args):
            if verb == 'record-email-sent':
                receipts.append(dict(args))
                if len(receipts) == 1:
                    raise OSError('lost acknowledgement')
            if verb == 'outbox-for-sender' and receipts:
                return dict(schema_version='email-outbox.v1', owner='joe', drafts=[])
            return carr(verb, args)
        self.ports.carr = flaky
        self.assertEqual(self.worker.run(self.settings, self.ports)['failed'], 1)
        self.worker.run(self.settings, self.ports)
        self.assertEqual(receipts[0]['status'], 'failed')
        self.assertEqual(receipts, [receipts[0], receipts[0]])
        self.assertEqual(self.sent, [])

    def test_existing_sent_message_blocks_a_new_submission_without_false_success(self):
        self.mailbox.sent.append(SentRow(ACCOUNT, 'client@example.test; partner@example.test',
            self.draft['subject'], int(NOW) - 60, 'earlier-message'))
        self.assertEqual(self.worker.run(self.settings, self.ports)['failed'], 1)
        self.assertEqual(self.sent, [])
        [receipt] = [args for verb, args in self.calls if verb == 'record-email-sent']
        self.assertEqual(receipt['reason'], 'already_in_mailbox:matching_item_found')

    def test_confirmation_requires_a_stable_identifier_and_exact_subject(self):
        draft = self.worker.SubmittedDraft.parse(self.draft, self.settings)
        for row in (SentRow(ACCOUNT, '; '.join(draft.mail.recipients), draft.mail.subject, int(NOW)),
                    SentRow(ACCOUNT, '; '.join(draft.mail.recipients), 'submitted subject', int(NOW), 'other')):
            with self.subTest(row=row):
                self.assertIsNone(send_mod.confirmed_mail(Snapshot((row,), ()), draft.mail, NOW - 5))

    def test_user_activity_and_existing_daily_cap_defer_submitted_drafts(self):
        self.ports.idle_seconds = lambda: 10
        self.assertEqual(self.worker.run(self.settings, self.ports)['deferred'], 1)
        self.ports.idle_seconds = lambda: 600
        ledger = send_mod.Ledger(self.settings.send_state / send_mod.LEDGER)
        for number in range(10):
            ledger.append(str(number), 'send', 'pressing', str(number), NOW)
        self.assertEqual(self.worker.run(self.settings, self.ports)['deferred'], 1)
        self.assertEqual(self.sent, [])
        self.assertFalse(any(verb == 'record-email-sent' for verb, _ in self.calls))

    def test_overlapping_worker_does_not_send(self):
        import fcntl
        root = self.settings.send_state / 'outbox'
        root.mkdir(parents=True)
        with open(root / 'lock', 'a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.assertEqual(self.worker.run(self.settings, self.ports)['deferred'], 1)
        self.assertEqual(self.sent, [])
        self.assertEqual(self.calls, [])


class AttachmentTests(unittest.TestCase):
    """Attachments ride only on submitted mail and go out through one fake AppleScript run."""

    def setUp(self):
        self.tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.home, self.root = self.tmp / 'home', self.tmp / 'deals'
        (self.root / 'deal-001').mkdir(parents=True)
        self.file = self.root / 'deal-001' / 'packet-001.pdf'
        self.file.write_bytes(b'%PDF-synthetic')
        self.settings_for(attachment_root=str(self.root), attachment_max_bytes=1000)
        self.enterContext(patch.object(send_mod, 'account', lambda: ACCOUNT))
        self.default, self.real_default_sender = f'{ACCOUNT}_ActiveSyncExchange_HxS', send_mod.default_sender
        self.enterContext(patch.object(send_mod, 'default_sender', lambda: self.default))
        self.mailbox, self.clock, self.scripts, self.forms = FakeMailbox(), Clock(), [], []
        self.reply = 'sent'
        self.ports = Ports(lambda item: self.forms.append(item), self.mailbox, lambda: 600,
                           lambda verb, args: {'recipients': [{'email': e, 'blocked': False} for e in args['emails']]},
                           now=self.clock.now, sleep=self.clock.sleep, out=self.tmp / 'send',
                           script_send=self.script_send)

    def settings_for(self, **overrides):
        write_settings(self.home, ROOT, account=ACCOUNT, internal_domain='example.test',
                       partner_cc='partner@example.test', **overrides)
        self.settings = load_settings(self.home)
        self.enterContext(patch.object(send_mod, 'load_settings', lambda: self.settings))

    def script_send(self, mail):
        self.scripts.append(mail)
        if self.reply == 'sent':
            self.mailbox.sent.append(SentRow(ACCOUNT, '; '.join(mail.recipients), mail.subject,
                                             int(self.clock.t) + 30, 'sent-7'))
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply

    def mail(self, *paths):
        return send_mod.Mail(('person-a@example.test',), ('partner@example.test',), (), 'subject-001',
                             'Body text.', send_mod.attachments(paths or [str(self.file)]))

    def ledger_text(self):
        return (self.tmp / 'send' / send_mod.LEDGER).read_text()

    def dispatch(self, mail, key):
        return send_mod._write_transport(mail, key, self.ports, send_requested=True, submitted=True)

    def test_submitted_mail_sends_through_one_script_and_confirms(self):
        result = self.dispatch(self.mail(), 'k1')
        self.assertEqual((result.state, result.mode, result.sent_item_id), (State.DONE, 'script', 'sent-7'))
        self.assertEqual(len(self.scripts), 1)
        self.assertEqual(self.forms, [])
        self.assertNotIn('packet-001', self.ledger_text())
        self.assertNotIn('deal-001', self.ledger_text())
        rows = [json.loads(line) for line in self.ledger_text().splitlines()]
        self.assertEqual([r['attachments'] for r in rows],
                         [{'count': 1, 'sha256': [send_mod._digest(self.file)]}] * 2)
        again = self.dispatch(self.mail(), 'k1')
        self.assertEqual(again.state, State.DONE)
        self.assertEqual(len(self.scripts), 1)

    def test_plain_send_and_cli_refuse_attachments_before_anything_runs(self):
        result = send_mod.send(['person-a@example.test'], [], [], 'subject-001', 'Body.',
                               idempotency_key='k2', ports=self.ports, send_requested=True,
                               attachments=[str(self.file)])
        self.assertEqual((result.state, result.reason), (State.REFUSED, 'attachments_require_submission'))
        direct = send_mod._write_transport(self.mail(), 'k3', self.ports, send_requested=True)
        self.assertEqual(direct.reason, 'attachments_require_submission')
        self.assertEqual((self.scripts, self.forms), ([], []))
        self.assertFalse((self.tmp / 'send' / send_mod.LEDGER).exists())

    def test_partial_attachment_set_aborts_without_sending(self):
        self.reply = 'aborted'
        result = self.dispatch(self.mail(), 'k4')
        self.assertEqual((result.state, result.reason), (State.FAILED, 'aborted_before_dispatch'))
        self.assertEqual(self.mailbox.sent, [])
        self.assertIn('aborted_before_dispatch', self.ledger_text())

    def test_only_a_validated_submission_reaches_the_transport(self):
        mail = self.mail()
        self.assertTrue(send_mod.partner_copied(mail, self.settings))
        result = send_mod.send_submitted(mail, ports=self.ports)
        self.assertEqual((result.state, result.reason), (State.REFUSED, 'submission_required'))
        self.assertEqual((self.scripts, self.forms), ([], []))
        self.assertFalse((self.tmp / 'send' / send_mod.LEDGER).exists())

    def test_partner_copy_rule(self):
        for cc in ((), ('someone@example.test',), ('partner@example.test', 'someone@example.test')):
            with self.subTest(cc=cc):
                self.assertFalse(send_mod.partner_copied(replace(self.mail(), cc=cc), self.settings))
        self.assertFalse(send_mod.partner_copied(replace(self.mail(), bcc=('x@example.test',)), self.settings))

    def test_another_default_sender_is_refused_before_dispatch(self):
        for default in ('someone@example.test_ActiveSyncMicrosoft_HxS', None, ACCOUNT):
            with self.subTest(default=default):
                self.default = default
                result = self.dispatch(self.mail(), 'k10')
                self.assertEqual((result.state, result.reason), (State.FAILED, 'default_sender_mismatch'))
        self.assertEqual(self.scripts, [])
        self.assertFalse((self.tmp / 'send' / send_mod.LEDGER).exists())

    def test_default_sender_reads_the_profile_preference(self):
        profile = self.tmp / 'profile-a'
        self.assertIsNone(self.real_default_sender(profile))
        profile.mkdir()
        prefs = profile / 'ProfilePreferences.plist'
        prefs.write_bytes(plistlib.dumps({'DefaultAccountIdentifier': 'a@example.test_ActiveSyncExchange_HxS'}))
        self.assertEqual(self.real_default_sender(profile), 'a@example.test_ActiveSyncExchange_HxS')
        prefs.write_bytes(b'not a plist')
        self.assertIsNone(self.real_default_sender(profile))

    def test_configured_profile_controls_the_sender_check(self):
        main, alternate = self.tmp / 'Main Identity', self.tmp / 'Alternate Identity'
        for profile, default in ((main, 'someone@example.test_ActiveSyncMicrosoft_HxS'),
                                 (alternate, f'{ACCOUNT}_ActiveSyncExchange_HxS')):
            profile.mkdir()
            (profile / 'ProfilePreferences.plist').write_bytes(
                plistlib.dumps({'DefaultAccountIdentifier': default}))
        self.enterContext(patch.object(send_mod, 'default_sender', self.real_default_sender))
        for profile, state, k in ((main, State.FAILED, 'k12'), (alternate, State.DONE, 'k11')):
            with self.subTest(profile=profile.name):
                self.settings_for(attachment_root=str(self.root), attachment_max_bytes=1000, profile=str(profile))
                self.assertEqual(self.dispatch(self.mail(), k).state, state)
        self.assertEqual(len(self.scripts), 1)

    def test_failed_cleanup_is_its_own_failure_and_never_resends(self):
        self.ports.script_send = None
        with patch.object(send_mod.subprocess, 'run',
                          return_value=subprocess.CompletedProcess([], 0, 'cleanup_failed\n', '')) as run:
            first = self.dispatch(self.mail(), 'k9')
            second = self.dispatch(self.mail(), 'k9')
        self.assertEqual((first.state, first.reason), (State.FAILED, 'abort_cleanup_failed'))
        self.assertEqual((second.state, second.reason), (State.FAILED, 'abort_cleanup_failed'))
        self.assertEqual(run.call_count, 1)
        self.assertEqual(self.mailbox.sent, [])
        script = run.call_args.kwargs['input']
        self.assertIn('delete m\non error\nreturn "cleanup_failed"', script)

    def test_transport_failure_is_unconfirmed_and_never_retried(self):
        self.reply = send_mod.UiFailure('transport_TimeoutExpired_000000000000')
        first = self.dispatch(self.mail(), 'k5')
        self.assertEqual(first.state, State.PRESSED_UNCONFIRMED)
        second = self.dispatch(self.mail(), 'k5')
        self.assertEqual(second.state, State.PRESSED_UNCONFIRMED)
        self.assertEqual(len(self.scripts), 1)

    def test_file_changed_after_admission_is_refused_before_dispatch(self):
        mail = self.mail()
        self.file.write_bytes(b'%PDF-changed!!')
        result = self.dispatch(mail, 'k6')
        self.assertEqual((result.state, result.reason), (State.FAILED, 'attachment_changed'))
        self.assertEqual(self.scripts, [])

    def test_admission_resolves_links_and_dots_before_the_root_check(self):
        outside = self.tmp / 'outside.pdf'
        outside.write_bytes(b'x')
        (self.root / 'deal-001' / 'link.pdf').symlink_to(outside)
        for path, reason in ((str(self.root / 'deal-001' / '..' / '..' / 'outside.pdf'), 'attachment_outside_root'),
                             (str(self.root / 'deal-001' / 'link.pdf'), 'attachment_outside_root'),
                             (str(self.root / 'deal-001' / 'missing.pdf'), 'attachment_missing'),
                             (str(self.root / 'deal-001'), 'attachment_not_regular_file'),
                             ('deal-001/packet-001.pdf', 'attachment_path_not_absolute')):
            with self.subTest(reason=reason), self.assertRaisesRegex(ValueError, f'^{reason}$'):
                send_mod.attachments([path])

    def test_size_cap_and_missing_settings_fail_closed(self):
        self.file.write_bytes(b'x' * 1001)
        with self.assertRaisesRegex(ValueError, '^attachments_too_large$'):
            send_mod.attachments([str(self.file)])
        self.settings_for()
        with self.assertRaisesRegex(ValueError, '^attachment_settings_missing$'):
            send_mod.attachments([str(self.file)])
        self.settings_for(attachment_root=str(self.tmp / 'gone'), attachment_max_bytes=1000)
        with self.assertRaisesRegex(ValueError, '^attachment_root_missing$'):
            send_mod.attachments([str(self.file)])

    def test_settings_reject_relative_root_and_nonpositive_cap(self):
        for overrides in (dict(attachment_root='deals'), dict(attachment_root=str(self.root) + '/../x'),
                          dict(attachment_max_bytes=0), dict(attachment_max_bytes='10')):
            with self.subTest(overrides=overrides):
                write_settings(self.home, ROOT, **overrides)
                with self.assertRaises(ValueError):
                    load_settings(self.home)

    def test_fingerprint_binds_content_and_leaves_plain_mail_unchanged(self):
        with_file = self.mail()
        plain = replace(with_file, attachments=())
        self.assertNotEqual(send_mod.fingerprint(with_file), send_mod.fingerprint(plain))
        identity = ['send', ACCOUNT, 'subject-001', sorted(plain.recipients)]
        self.assertEqual(send_mod.fingerprint(plain),
                         __import__('hashlib').sha256(json.dumps(identity).encode()).hexdigest())

    def test_script_attaches_every_file_verifies_count_and_keeps_paths_off_argv(self):
        mail = replace(self.mail(), subject='Quote " and \\ slash')
        script = send_mod.applescript(mail)
        self.assertIn('set f0 to POSIX file "' + str(self.file.resolve()) + '"', script)
        self.assertIn('subject:"Quote \\" and \\\\ slash"', script)
        self.assertLess(script.index('is not 1 then error'), script.index('send m'))
        self.assertLess(script.index('delete m'), script.index('send m'))
        self.assertIn('make new cc recipient at m', script)
        with patch.object(send_mod.subprocess, 'run',
                          return_value=subprocess.CompletedProcess([], 0, 'sent\n', '')) as run:
            self.assertEqual(send_mod.applescript_send(mail), 'sent')
        self.assertEqual(run.call_args.args[0], ['osascript', '-'])
        self.assertIn(str(self.file.resolve()), run.call_args.kwargs['input'])

    def test_control_characters_are_refused_before_dispatch(self):
        mail = replace(self.mail(), body='Bad \x07 bell')
        result = self.dispatch(mail, 'k7')
        self.assertEqual((result.state, result.reason), (State.FAILED, 'unscriptable_text'))
        self.assertEqual(self.scripts, [])


if __name__ == '__main__':
    unittest.main()
