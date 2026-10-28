#!/usr/bin/env python3
"""Offline tests for Outlook triage using synthetic windows and messages.

The fake mirrors the shapes read from Joe's live Outlook on 2026-10-20: a folder
pane (AXOutline) that View > Sidebar toggles, the window title naming the open
folder ("Inbox • account"), an AXTable "Message List" whose AXRow children each
hold one AXCell labelled "Sender: ..., Subject: ...", group header cells directly
under the table, and snapshot-scoped element tokens that go stale. Like Outlook
it marks a message read when it is selected and when the selection moves on, and
shows an unread row's label starting with "Unread". Fixtures use neutral IDs
(client-001, folder-A) so no person-shaped name is committed.
"""
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime
from zoneinfo import ZoneInfo
import hashlib
import io
import json
import os
from pathlib import Path
import plistlib
import re
import shutil
import stat
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from lib import outlook_triage as triage
from outlook_test_support import write_settings
from lib.launchd_calendar import calendar_for_window
from lib.outlook_ui import Row, Waits, is_compose_window

ZONE = ZoneInfo('America/Chicago')
EVENING = datetime(2026, 10, 20, 19, 15, tzinfo=ZONE)
LAST = datetime(2026, 10, 20, 21, 0, tzinfo=ZONE)
GUIDE = '''### Choosing the folder
The folder tree (all folders live under Inbox; "/" separates levels):
Deals
Deals/Active Deals
Deals/Active Deals/_group-1
Deals/Active Deals/_group-1/national-folder-B
Deals/Active Deals/folder-A
Deals/Active Deals/folder-G.client-005
Deals/Active Deals/folder-F
Deals/Prospects
Deals/Prospects/folder-D
Deals/Prospects/folder-E
Deals/Prospects/folder-H
Networking
Networking/Vendors
Networking/Vendors/A.List
Networking/Vendors/A.List/vendor-001
CARR
CARR/folder-C
Reference
Reference/folder-F   (old correspondence)
map: sender-firm -> CARR/folder-C
map: @firm.test -> CARR/folder-C

Matching guidance:
- Deal folders are named for the client: surname or business.
'''
ACCOUNT = 'owner@example.com'
MAILBOX = f'Inbox • {ACCOUNT}'
NAMES = re.compile(r'(client|folder|sender|vendor|subject)-')


def window(window_id, title, width, height, on_current_space=True):
    return {'window_id': window_id, 'title': title, 'on_current_space': on_current_space, 'is_on_screen': on_current_space is True,
            'bounds': {'x': 0, 'y': 33, 'width': width, 'height': height}}


TOOLBAR = window(11, '', 1512, 32, False)
FULL_SCREEN = window(12, MAILBOX, 1512, 949, False)
ORDINARY = window(13, MAILBOX, 1230, 768)
COMPOSE = window(14, 'Re: subject-000', 917, 911)
UNTITLED_OFFSCREEN = window(15, '', 500, 500, None) | {'is_on_screen': False}
UNTITLED_OFFSCREEN['bounds']['y'] = 580


def msg(sender, subject, when='10:02 AM', unread=False, email=None):
    return {'label': f'Sender: {sender}, Subject: {subject},      {when},        '
                     f'Message preview: preview-text', 'unread': unread, 'email': email}


def short(label):
    return hashlib.sha256(label.encode()).hexdigest()[:12]


FILE_A = msg('folder-A, client-001', 'subject-001')
VENDOR = msg('vendor-001', 'subject-002')
FIRM = msg('sender-firm', 'subject-003')
NEWS = msg('sender-news', 'subject-004')
UNREAD = msg('folder-A, client-001', 'subject-005', '9:41 AM', unread=True)
UNCERTAIN = {'label': 'Meeting request, 9:30 AM', 'unread': False}
AMBIGUOUS = msg('client-003 folder-D', 'subject-006')
NOT_UNIQUE = msg('folder-F, client-004', 'subject-007')
ARRIVAL = msg('sender-news', 'subject-008', '7:01 PM', unread=True)
ELSEWHERE = msg('folder-A, client-001', 'subject-009')

FIND = {
    'client-001 folder-A': {'parties': [{'name': 'client-001 folder-A', 'kind': 'client', 'ref': 'C-1',
                                         'org_name': None, 'merged': False}], 'deals': []},
    'client-003 folder-D': {'parties': [{'name': 'client-003 folder-D', 'kind': 'client', 'ref': 'C-2',
                                         'org_name': 'folder-E', 'merged': False}], 'deals': []},
    'client-004 folder-F': {'parties': [{'name': 'client-004 folder-F', 'kind': 'lead', 'ref': 'L-3',
                                         'org_name': None, 'merged': False}], 'deals': []},
}

FAKE = r'''#!{python}
import json, os, sys
state_path = os.environ['FAKE_STATE']
state = json.load(open(state_path))
name = os.path.basename(sys.argv[0])
args = sys.argv[1:]
with open(os.environ['FAKE_CALLS'], 'a') as calls:
    calls.write(json.dumps([name] + args) + '\n')
def save():
    json.dump(state, open(state_path, 'w'))
def lists():
    return state['lists']
def current():
    if state.get('search'):
        return [r for rows in lists().values() for r in rows if r.get('email') == state['search']]
    if state['folder'] != 'Inbox':
        return state['elsewhere']
    return lists()[state['tab'] or 'only']
def mark_read(label):
    for rows in list(lists().values()) + [state['elsewhere']]:
        for r in rows:
            if r['label'] == label:
                r['unread'] = False
def select(label):
    previous = state.get('selected')
    if previous:
        mark_read(previous)
    state['selected'] = label
    state['selections'].append(label)
    if label:
        mark_read(label)
def fail(message):
    print(message)
    sys.exit(1)
if name == 'pgrep':
    if state['running']:
        print(4242)
        sys.exit(0)
    sys.exit(1)
if name == 'osascript':
    if state.get('osascript_quit_delay'):
        import time
        time.sleep(state['osascript_quit_delay'])
    state['running'] = state.get('osascript_quit_ignored', False)
    if 'after_quit' in state:
        state['windows'] = state['after_quit']
    save()
    sys.exit(0)
if name == 'open':
    state['running'] = True
    state['windows'] = state['after_launch']
    save()
    sys.exit(0)
if name == 'ioreg':
    print('    | |   "HIDIdleTime" = %d' % int(state['idle_seconds'] * 1e9))
    sys.exit(0)
if name == 'run.sh':
    query = json.loads(args[2])['query']
    if state.get('find_fails'):
        fail('record layer unreachable')
    print(json.dumps(state['find'].get(query, {'parties': [], 'deals': []})))
    sys.exit(0)
tool, payload = args[1], json.loads(args[2])
if tool == 'list_windows':
    print(json.dumps({'windows': state['windows']}))
elif tool == 'bring_to_front':
    print(json.dumps({'status': 'activated'}))
elif tool == 'get_window_state':
    state['snapshot'] += 1
    snap = 's%08x' % state['snapshot']
    elements, tokens = [], {}
    def add(role, label, depth, parent=None, kind=None, value=None, selected=None):
        index = len(elements)
        element = {'element_index': index, 'role': role, 'label': label, 'depth': depth,
                   'parent_index': parent, 'element_token': f'{snap}:{index}'}
        if value is not None:
            element['value'] = value
        if selected is not None:
            element['selected'] = selected
        elements.append(element)
        tokens[str(index)] = kind
        return index
    top = add('AXWindow', state['folder'] + ' • ' + ACCOUNT, 0)
    for label in ('New Mail', 'Delete', 'Archive', 'Move', 'Flag', 'Mark Unread'):
        add('AXButton', label, 1, top, ['button', label])
    for depth in (1, 2, 3):
        add('AXTextField', 'Search', depth, top, ['search_field'])
    if state['sidebar']:
        outline = add('AXOutline', '', 2, top)
        for folder in ['Favorites', 'Inbox', 'Sent Items'] + state['folder_rows']:
            row = add('AXRow', '', 3, outline, ['folder_row', folder], selected=folder == state['folder'])
            add('AXStaticText', folder, 6, row, value=folder)
            if folder == 'Inbox':
                unread = state.get('badge')
                if unread is None:
                    unread = sum(r['unread'] for rows in lists().values() for r in rows)
                if unread:
                    add('AXStaticText', str(unread), 6, row, value=str(unread))
    if state['tab'] and state['folder'] == 'Inbox':
        focused = 'Selected' if state['tab'] == 'focused' else 'Unselected'
        other = 'Unselected' if state['tab'] == 'focused' else 'Selected'
        add('AXCheckBox', f'Focused Inbox {focused};  Other Inbox {other}', 4, top, value='0')
    table = add('AXTable', 'Message List', 4, top)
    add('AXCell', 'Today, Expanded', 6, table)
    for r in current():
        row = add('AXRow', '', 5, table, selected=r['label'] == state.get('selected'))
        shown = ('Unread, ' if r['unread'] else '') + r['label']
        add('AXCell', shown, 6, row, ['row', r['label']], selected=r['label'] == state.get('selected'))
    if state.get('picker') is not None:
        add('AXTextField', 'Search for a folder', 2, top, ['picker_field'])
        typed = state['picker']
        if typed:
            for folder in state['picker_folders']:
                if typed.casefold() in folder.casefold():
                    add('AXRow', folder, 3, top, ['folder', folder])
    state['tokens'] = {'snap': snap, 'map': tokens}
    save()
    print(json.dumps({'snapshot_id': snap, 'elements': elements, 'degraded_reason': None}))
elif tool == 'invoke_menu':
    path = payload['path']
    if path == ['Outlook', 'Quit Outlook']:
        state['running'] = (state.get('menu_quit_ignored', False) or
                            payload['window_id'] != state.get('quit_window_id', payload['window_id']))
    elif path == ['View', 'Sidebar']:
        if not state.get('sidebar_stuck'):
            state['sidebar'] = not state['sidebar']
    elif path == ['View', 'Switch to Other'] and state['tab']:
        state['tab'] = 'other'
    elif path == ['View', 'Switch to Focused'] and state['tab']:
        state['tab'] = 'focused'
    elif path == ['File', 'New', 'Main Window'] and 'after_new_window' in state:
        state['windows'] = state['after_new_window']
    save()
    print(json.dumps({'effect': 'unverifiable'}))
elif tool in ('click', 'type_text'):
    snap, _, index = payload['element_token'].partition(':')
    if snap != state['tokens']['snap']:
        fail('stale element_token')
    kind = state['tokens']['map'].get(index)
    if tool == 'type_text':
        if kind == ['search_field']:
            state['search_typed'] = payload['text']
        elif kind == ['picker_field']:
            state['picker'] = payload['text']
        else:
            fail('not a text field')
    elif kind and kind[0] == 'row':
        if not (state.get('search') and state.get('search_click_ignored')):
            select(kind[1])
    elif kind and kind[0] == 'folder_row':
        state['presses'].append('folder:' + kind[1])
        if not state.get('inbox_click_ignored'):
            state['folder'] = kind[1]
    elif kind == ['button', 'Move']:
        state['presses'].append('Move')
        if state.get('selected'):
            state['picker'] = ''
    elif kind and kind[0] == 'button':
        state['presses'].append(kind[1])
    elif kind and kind[0] == 'folder' and state.get('picker') is not None:
        state['picker'] = None
        if state['failing_moves'] > 0:
            state['failing_moves'] -= 1
        else:
            label = state['selected']
            rows = current()
            at = next(i for i, r in enumerate(rows) if r['label'] == label)
            rows.pop(at)
            state['moved'].append([label, kind[1]])
            state['selected'] = None
            if state.get('open_next_after_move') and at < len(rows):
                select(rows[at]['label'])
            for other in state.get('read_elsewhere_after_move', []):
                mark_read(other)
            for arrival in state.pop('arrive_after_move', []):
                lists()[state['tab'] or 'only'].insert(0, arrival)
            if state.get('leave_inbox_after_move'):
                state['folder'] = 'Archive'
    save()
    print(json.dumps({'effect': 'unverifiable'}))
elif tool == 'press_key':
    if payload['key'] == 'return':
        state['search'] = state.get('search_typed')
    else:
        state['picker'] = None
        state['search'] = None
        if state.get('selected'):
            select(None)
            state['selections'].pop()
    save()
    print(json.dumps({'effect': 'unverifiable'}))
else:
    fail('unexpected tool ' + tool)
sys.exit(0)
'''.replace('ACCOUNT', repr(ACCOUNT))


class FakeOutlookCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='outlook-triage-selftest-'))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.repo, self.home, bin_dir = self.tmp / 'repo', self.tmp / 'home', self.tmp / 'bin'
        write_settings(self.home, self.repo, account=ACCOUNT, driver=str(self.tmp / 'bin/cua-driver'))
        local_home = patch('pathlib.Path.home', return_value=self.home)
        local_home.start()
        self.addCleanup(local_home.stop)
        self.guide = self.home / '.config/carr/outlook-nightly-sweep.folders.md'
        self.guide.parent.mkdir(parents=True, exist_ok=True)
        self.guide.write_text(GUIDE)
        self.guide.chmod(0o600)
        bin_dir.mkdir()
        self.repo.mkdir()
        fakes = [bin_dir / n for n in ('pgrep', 'osascript', 'open', 'cua-driver', 'ioreg')]
        for fake in fakes + [self.repo / 'run.sh']:
            fake.write_text(FAKE.replace('{python}', sys.executable))
            fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
        self.state = self.tmp / 'state.json'
        self.calls = self.tmp / 'calls.jsonl'
        self.env = {**os.environ, 'PATH': f'{bin_dir}:/usr/bin:/bin', 'HOME': str(self.home),
                    'FAKE_STATE': str(self.state), 'FAKE_CALLS': str(self.calls)}

    def given(self, rows=None, tabs=None, **state):
        lists = tabs if tabs is not None else {'only': rows or []}
        self.state.write_text(json.dumps({
            'running': True, 'windows': [TOOLBAR, FULL_SCREEN], 'after_launch': [TOOLBAR, ORDINARY],
            'idle_seconds': 3600, 'sidebar': True, 'tab': 'focused' if tabs else None, 'folder': 'Inbox',
            'lists': lists, 'elsewhere': [], 'folder_rows': ['Deals', 'Active Deals', 'folder-A'],
            'picker_folders': ['folder-A', 'vendor-001', 'folder-C', 'folder-D', 'folder-E', 'folder-F',
                               'national-folder-B', 'folder-H'],
            'failing_moves': 0, 'snapshot': 0, 'search': None, 'selections': [], 'presses': [], 'moved': [],
            'find': FIND, **state}))

    def run_triage(self, now=EVENING, limits=triage.Limits(), clock=None):
        with patch.dict(os.environ, self.env, clear=True), redirect_stdout(io.StringIO()), \
                redirect_stderr(io.StringIO()):
            outcome = triage.run(self.repo, self.home, now, Waits(2, 0, 0, 0, 0, 0, 0), limits,
                                 clock or (lambda: now))
        return outcome, json.loads(self.state.read_text())

    def called(self, name):
        if not self.calls.exists():
            return []
        rows = [json.loads(line) for line in self.calls.read_text().splitlines()]
        return [row[1:] for row in rows if row[0] == name]

    def refresh_list(self, entries):
        path = self.repo / triage.REFRESH_LIST
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(entries))
        return path

    def report_path(self):
        return self.repo / triage.OUT / '2026-10-20.report.md'

    def report(self):
        return self.report_path().read_text()

    def settled(self):
        return self.repo / triage.OUT / '2026-10-20.settled'

    def unread_labels(self, state):
        return {r['label'] for rows in state['lists'].values() for r in rows if r['unread']}


class PreflightQuitTest(FakeOutlookCase):
    def prepare(self, waits=Waits(2, 0, 0, 0.01, 0, 0, 0)):
        from lib.outlook_ui import prepare_outlook
        with patch.dict(os.environ, self.env, clear=True):
            return prepare_outlook(waits)

    def test_quit_uses_mailbox_when_unassigned_window_is_first(self):
        self.given(windows=[UNTITLED_OFFSCREEN, ORDINARY], quit_window_id=13)
        self.assertIsNone(self.prepare())
        menus = [json.loads(call[2]) for call in self.called('cua-driver')
                 if call[1] == 'invoke_menu']
        self.assertEqual(menus[0]['window_id'], 13)
        self.assertEqual(self.called('osascript'), [])


    def test_secondary_display_main_window_uses_menu_quit(self):
        for x, width, height in ((-1329, 1329, 1002), (-1024, 1024, 700)):
            with self.subTest(x=x, width=width, height=height):
                secondary = window(13, MAILBOX, width, height, None) | {'is_on_screen': True}
                secondary['bounds']['x'] = x
                self.given(windows=[TOOLBAR, secondary], osascript_quit_ignored=True,
                           after_launch=[secondary])
                self.assertIsNone(self.prepare(Waits(0.4, 0, 0, 0.01, 0, 0, 0)))
                self.assertEqual(self.called('osascript'), [])

    def test_successful_menu_that_leaves_outlook_running_falls_back(self):
        self.given(windows=[ORDINARY], menu_quit_ignored=True)
        self.assertIsNone(self.prepare())
        self.assertEqual(len(self.called('osascript')), 1)
        self.assertEqual(len(self.called('open')), 1)

    def test_no_ordinary_window_uses_graceful_quit(self):
        self.given(windows=[UNTITLED_OFFSCREEN, FULL_SCREEN])
        self.assertIsNone(self.prepare())
        self.assertEqual(len(self.called('osascript')), 1)
        self.assertFalse(any(call[1] == 'invoke_menu' for call in self.called('cua-driver')))

    def test_failed_menu_uses_graceful_quit(self):
        from lib.outlook_ui import CuaError, cua
        self.given(windows=[ORDINARY])
        def unavailable_menu(tool, arguments):
            if tool == 'invoke_menu':
                raise CuaError('menu unavailable')
            return cua(tool, arguments)
        with patch('lib.outlook_ui.cua', side_effect=unavailable_menu):
            self.assertIsNone(self.prepare())
        self.assertEqual(len(self.called('osascript')), 1)

    def test_refused_quit_times_out_without_relaunch(self):
        self.given(windows=[ORDINARY], menu_quit_ignored=True, osascript_quit_ignored=True)
        outcome = self.prepare(Waits(0.4, 0, 0, 0.01, 0, 0, 0))
        self.assertEqual((outcome.state, outcome.reason), ('failed', 'outlook_quit_timeout'))
        self.assertEqual(len(self.called('osascript')), 1)
        self.assertEqual(self.called('open'), [])
        self.assertTrue(json.loads(self.state.read_text())['running'])

    def test_quit_timeout_reports_fresh_sanitized_windows(self):
        sheet = window(16, 'private subject and address@example.test', 700, 240) | {'role': 'AXSheet'}
        dialog = window(17, 'private confirmation', 600, 300) | {'subrole': 'AXDialog'}
        self.given(windows=[ORDINARY], after_quit=[ORDINARY, COMPOSE, sheet, dialog, TOOLBAR],
                   menu_quit_ignored=True, osascript_quit_ignored=True)
        outcome = self.prepare(Waits(0.4, 0, 0, 0.01, 0, 0, 0))
        self.assertEqual(outcome.reason, 'outlook_quit_timeout')
        self.assertEqual(json.loads(outcome.detail), {'windows': [
            {'kind': 'main', 'width': 1230, 'height': 768, 'is_on_screen': True},
            {'kind': 'compose', 'width': 917, 'height': 911, 'is_on_screen': True},
            {'kind': 'sheet', 'width': 700, 'height': 240, 'is_on_screen': True},
            {'kind': 'dialog', 'width': 600, 'height': 300, 'is_on_screen': True},
            {'kind': 'dialog', 'width': 1512, 'height': 32, 'is_on_screen': False}]})
        for private in ('subject-000', ACCOUNT, 'private', 'address@example.test'):
            self.assertNotIn(private, outcome.detail)
        self.assertEqual(self.called('open'), [])

    def test_quit_timeout_survives_diagnostic_failure(self):
        from lib.outlook_ui import CuaError, cua
        self.given(windows=[ORDINARY], menu_quit_ignored=True, osascript_quit_ignored=True)
        snapshots = 0
        def fail_second_snapshot(tool, arguments):
            nonlocal snapshots
            if tool == 'list_windows':
                snapshots += 1
                if snapshots > 1:
                    raise CuaError('private window title')
            return cua(tool, arguments)
        with patch('lib.outlook_ui.cua', side_effect=fail_second_snapshot):
            outcome = self.prepare(Waits(0.4, 0, 0, 0.01, 0, 0, 0))
        self.assertEqual((outcome.reason, outcome.detail),
                         ('outlook_quit_timeout', 'windows_unavailable'))

    def test_refresh_cli_logs_timeout_detail(self):
        from lib import outlook_refresh
        from lib.outlook_ui import Outcome
        detail = '{"windows":[{"kind":"sheet","width":700,"height":240,"is_on_screen":true}]}'
        output = io.StringIO()
        with patch('lib.outlook_refresh.run', return_value=Outcome('failed', 'outlook_quit_timeout', detail)), \
                redirect_stdout(output):
            self.assertEqual(outlook_refresh.main(), 1)
        self.assertEqual(output.getvalue(), f'outlook-refresh failed outlook_quit_timeout {detail}\n')

    def test_hanging_graceful_quit_cannot_extend_quit_deadline(self):
        self.given(windows=[ORDINARY], menu_quit_ignored=True, osascript_quit_delay=5)
        started = time.monotonic()
        outcome = self.prepare(Waits(0.4, 0, 0, 0.01, 0, 0, 0))
        self.assertLess(time.monotonic() - started, 2)
        self.assertEqual((outcome.state, outcome.reason), ('failed', 'outlook_quit_timeout'))
        self.assertEqual(self.called('open'), [])

    def test_possible_draft_prevents_both_quit_routes(self):
        self.given(windows=[UNTITLED_OFFSCREEN, ORDINARY, COMPOSE])
        outcome = self.prepare()
        self.assertEqual((outcome.state, outcome.reason), ('skipped', 'compose_window_open'))
        self.assertEqual(self.called('osascript') + self.called('open'), [])
        self.assertFalse(any(call[1] == 'invoke_menu' for call in self.called('cua-driver')))
        self.assertTrue(json.loads(self.state.read_text())['running'])


class ComposeWindowTest(unittest.TestCase):
    def test_observed_untitled_offscreen_window_does_not_block_preflight(self):
        self.assertFalse(is_compose_window(UNTITLED_OFFSCREEN))

    def test_subjectless_and_resized_drafts_still_block_preflight(self):
        for title in ('', None, 'UNTITLED', 'subject-000'):
            for width, height in ((917, 911), (500, 501), (501, 500)):
                with self.subTest(title=title, width=width, height=height):
                    draft = window(16, title, width, height, None) | {'is_on_screen': False}
                    self.assertTrue(is_compose_window(draft))
        for draft in (UNTITLED_OFFSCREEN | {'is_on_screen': True},
                      UNTITLED_OFFSCREEN | {'on_current_space': False},
                      UNTITLED_OFFSCREEN | {'on_current_space': True},
                      UNTITLED_OFFSCREEN | {'title': 'subject-000'},
                      UNTITLED_OFFSCREEN | {'title': 'UNTITLED'}, UNTITLED_OFFSCREEN | {'title': None},
                      {k: v for k, v in UNTITLED_OFFSCREEN.items() if k != 'is_on_screen'},
                      {k: v for k, v in UNTITLED_OFFSCREEN.items() if k != 'on_current_space'},
                      window(16, '', 500, 500)):
            with self.subTest(draft=draft):
                self.assertTrue(is_compose_window(draft))


class OutlookTriageTest(FakeOutlookCase):
    def test_configured_driver_outside_shell_path_works(self):
        self.given([FILE_A, NEWS, UNREAD])
        local_driver = self.home / '.local/bin/cua-driver'
        local_driver.parent.mkdir(parents=True)
        (self.tmp / 'bin/cua-driver').rename(local_driver)
        write_settings(self.home, self.repo, account=ACCOUNT, driver=str(local_driver))
        outcome, state = self.run_triage()
        self.assertEqual((outcome.state, outcome.reason), ('succeeded', 'filed'))
        self.assertEqual(state['moved'], [[FILE_A['label'], 'folder-A']])
        self.assertNotIn(UNREAD['label'], state['selections'])

    def test_missing_driver_fails_without_selecting_mail(self):
        self.given([FILE_A, UNREAD])
        (self.tmp / 'bin/cua-driver').unlink()
        outcome, state = self.run_triage()
        self.assertEqual((outcome.state, outcome.reason), ('failed', 'cua_driver_unavailable'))
        self.assertEqual((state['moved'], state['selections']), ([], []))

    def test_files_read_email_by_each_rule_and_never_selects_unread(self):
        self.given([FILE_A, VENDOR, FIRM, NEWS, UNCERTAIN, UNREAD])
        outcome, state = self.run_triage()
        self.assertEqual((outcome.state, outcome.reason), ('succeeded', 'filed'))
        self.assertEqual(state['moved'], [[FILE_A['label'], 'folder-A'],
                                          [VENDOR['label'], 'vendor-001'],
                                          [FIRM['label'], 'folder-C']])
        self.assertNotIn(UNREAD['label'], state['selections'])
        self.assertNotIn(UNCERTAIN['label'], state['selections'])
        self.assertEqual(self.unread_labels(state), {UNREAD['label']})
        self.assertEqual({p for p in state['presses'] if not p.startswith('folder:')}, {'Move'})
        report = self.report()
        for row, rule in ((FILE_A, 'deal'), (VENDOR, 'vendor'), (FIRM, 'map')):
            self.assertIn(f'  {short(row["label"])} {rule}', report)
        self.assertIn('no_rule_matched: 1', report)
        self.assertIn('uncertain: 1', report)
        self.assertIn('unread: 1', report)
        self.assertEqual(stat.S_IMODE(self.report_path().stat().st_mode), 0o600)
        self.assertEqual(self.settled().read_text(), 'succeeded filed\n')
        self.assertTrue(state['sidebar'], 'folder pane restored')

    def test_report_carries_counts_and_hashes_but_no_names(self):
        self.given([FILE_A, VENDOR, FIRM, NEWS, UNREAD], read_elsewhere_after_move=[UNREAD['label']])
        self.run_triage()
        report = self.report()
        self.assertIn(short('Unread, ' + UNREAD['label']), report)
        self.assertNotRegex(report, NAMES)
        self.assertNotIn('Sender:', report)

    def test_existing_world_readable_report_is_made_private(self):
        self.given([NEWS])
        self.report_path().parent.mkdir(parents=True)
        self.report_path().write_text('')
        self.report_path().chmod(0o644)
        self.run_triage()
        self.assertEqual(stat.S_IMODE(self.report_path().stat().st_mode), 0o600)

    def test_reads_and_files_both_focused_and_other_tabs(self):
        self.given(tabs={'focused': [NEWS, FILE_A], 'other': [FIRM, NEWS, UNREAD]})
        outcome, state = self.run_triage()
        self.assertEqual(outcome.state, 'succeeded')
        self.assertEqual([m[1] for m in state['moved']], ['folder-A', 'folder-C'])
        self.assertEqual(self.unread_labels(state), {UNREAD['label']})

    def test_selects_the_inbox_before_reading_and_never_files_another_folder(self):
        self.given([FILE_A, NEWS], folder='Sent Items', elsewhere=[ELSEWHERE, VENDOR])
        outcome, state = self.run_triage()
        self.assertEqual(outcome.state, 'succeeded')
        self.assertIn('folder:Inbox', state['presses'])
        self.assertEqual(state['moved'], [[FILE_A['label'], 'folder-A']])

    def test_inbox_that_will_not_open_blocks_every_move(self):
        self.given([FILE_A, NEWS], folder='Sent Items', elsewhere=[ELSEWHERE, VENDOR], inbox_click_ignored=True)
        outcome, state = self.run_triage()
        self.assertEqual((outcome.state, outcome.reason), ('failed', 'inbox_not_selected'))
        self.assertEqual((state['moved'], state['selections']), ([], []))

    def test_leaving_the_inbox_mid_run_stops_before_the_next_move(self):
        self.given([FILE_A, NEWS, VENDOR], leave_inbox_after_move=True, elsewhere=[ELSEWHERE, VENDOR])
        outcome, state = self.run_triage()
        self.assertEqual((outcome.state, outcome.reason), ('failed', 'not_inbox'))
        self.assertEqual(state['moved'], [[FILE_A['label'], 'folder-A']])

    def test_row_next_to_unread_is_left_even_when_outlook_opens_the_next_message(self):
        self.given([FILE_A, UNREAD, VENDOR], open_next_after_move=True)
        outcome, state = self.run_triage()
        self.assertEqual(outcome.state, 'succeeded')
        self.assertEqual(state['moved'], [])
        self.assertEqual(state['selections'], [])
        self.assertEqual(self.unread_labels(state), {UNREAD['label']})
        self.assertIn('next_to_unread: 2', self.report())

    def test_move_proceeds_when_both_neighbours_are_read(self):
        self.given([NEWS, FILE_A, NEWS | {'label': NEWS['label'] + ' 2'}, UNREAD], open_next_after_move=True)
        outcome, state = self.run_triage()
        self.assertEqual(outcome.state, 'succeeded')
        self.assertEqual([m[0] for m in state['moved']], [FILE_A['label']])
        self.assertEqual(self.unread_labels(state), {UNREAD['label']})

    def test_ambiguous_or_shared_folder_names_stay_in_the_inbox(self):
        self.given([AMBIGUOUS, NEWS, NOT_UNIQUE])
        outcome, state = self.run_triage()
        self.assertEqual(outcome.state, 'succeeded')
        self.assertEqual(state['moved'], [])
        report = self.report()
        self.assertIn('deal_ambiguous: 1', report)
        self.assertIn('folder_name_not_unique: 1', report)

    def test_two_failed_moves_in_a_row_stop_the_run(self):
        self.given([FILE_A, VENDOR, FIRM], failing_moves=99)
        outcome, state = self.run_triage()
        self.assertEqual((outcome.state, outcome.reason), ('failed', 'move_failed_twice'))
        self.assertEqual(state['presses'].count('Move'), 2)
        self.assertEqual(state['moved'], [])
        self.assertEqual(triage.EXIT[outcome.state], 1)

    def test_one_failed_move_fails_the_run_and_leaves_the_day_open(self):
        self.given([FILE_A, VENDOR, NEWS], failing_moves=1)
        outcome, state = self.run_triage()
        self.assertEqual((outcome.state, outcome.reason), ('failed', 'move_failed'))
        self.assertEqual(state['moved'], [[VENDOR['label'], 'vendor-001']])
        self.assertFalse(self.settled().exists())

    def test_unread_email_read_during_the_run_fails_it(self):
        self.given([FILE_A, NEWS, UNREAD], read_elsewhere_after_move=[UNREAD['label']])
        outcome, _ = self.run_triage()
        self.assertEqual((outcome.state, outcome.reason), ('failed', 'unread_set_changed'))
        self.assertIn(short('Unread, ' + UNREAD['label']), self.report())

    def test_new_unread_mail_arriving_during_the_run_fails_it_for_a_later_retry(self):
        self.given([FILE_A, NEWS, VENDOR], arrive_after_move=[ARRIVAL])
        outcome, state = self.run_triage()
        self.assertEqual((outcome.state, outcome.reason), ('failed', 'unread_set_changed'))
        self.assertIn(short('Unread, ' + ARRIVAL['label']), self.report())
        self.assertFalse(self.settled().exists())

    def test_unread_rows_that_do_not_add_up_to_the_badge_block_every_move(self):
        self.given([FILE_A, VENDOR, UNREAD], badge=2)
        outcome, state = self.run_triage()
        self.assertEqual((outcome.state, outcome.reason), ('failed', 'unread_set_unverified'))
        self.assertEqual(state['selections'], [])
        self.assertNotIn('Move', state['presses'])

    def test_a_selected_unread_row_blocks_every_move(self):
        self.given([FILE_A, VENDOR, UNREAD], selected=UNREAD['label'])
        outcome, state = self.run_triage()
        self.assertEqual((outcome.state, outcome.reason), ('failed', 'unread_row_selected'))
        self.assertEqual(state['selections'], [])

    def test_active_user_skips_without_touching_outlook_until_the_last_fire(self):
        self.given([FILE_A], idle_seconds=120)
        outcome, state = self.run_triage()
        self.assertEqual((outcome.state, outcome.reason), ('skipped', 'user_active'))
        self.assertEqual(self.called('cua-driver') + self.called('open'), [])
        self.assertFalse(self.settled().exists())
        outcome, _ = self.run_triage(LAST)
        self.assertEqual(outcome.state, 'skipped')
        self.assertEqual(self.settled().read_text(), 'skipped user_active\n')

    def test_observed_untitled_offscreen_window_allows_triage(self):
        self.given([FILE_A], windows=[TOOLBAR, ORDINARY, UNTITLED_OFFSCREEN])
        outcome, state = self.run_triage()
        self.assertEqual((outcome.state, outcome.reason), ('succeeded', 'filed'))
        self.assertEqual(state['moved'], [[FILE_A['label'], 'folder-A']])

    def test_empty_subject_draft_skips_the_night(self):
        self.given([FILE_A], windows=[ORDINARY, COMPOSE | {'title': ''}])
        outcome, state = self.run_triage()
        self.assertEqual((outcome.state, outcome.reason), ('skipped', 'compose_window_open'))
        self.assertTrue(state['running'])
        self.assertEqual(state['moved'], [])

    def test_open_draft_skips_the_night(self):
        self.given([FILE_A], windows=[TOOLBAR, ORDINARY, COMPOSE])
        outcome, state = self.run_triage()
        self.assertEqual((outcome.state, outcome.reason), ('skipped', 'compose_window_open'))
        self.assertTrue(state['running'], 'Outlook left running')
        self.assertEqual(state['moved'], [])

    def test_missing_folder_guide_fails_before_outlook(self):
        self.given([FILE_A])
        self.guide.unlink()
        outcome, _ = self.run_triage()
        self.assertEqual((outcome.state, outcome.reason), ('failed', 'folder_guide_missing'))
        self.assertEqual(self.called('cua-driver') + self.called('pgrep'), [])

    def test_folder_guide_readable_by_others_is_refused_before_outlook(self):
        self.given([FILE_A])
        self.guide.chmod(0o644)
        outcome, _ = self.run_triage()
        self.assertEqual((outcome.state, outcome.reason), ('failed', 'folder_guide_not_0600'))
        self.assertEqual(self.called('cua-driver') + self.called('pgrep'), [])

    def test_an_early_failure_leaves_later_slots_to_retry_and_the_last_slot_settles(self):
        self.given([FILE_A, VENDOR], find_fails=True)
        outcome, _ = self.run_triage()
        self.assertEqual((outcome.state, outcome.reason), ('failed', 'carr_find_failed'))
        self.assertFalse(self.settled().exists())
        with patch('os.execv') as execv, patch('os.chdir'):
            triage.fire(self.repo, EVENING.replace(minute=30))
            execv.assert_called_once()
        self.run_triage(LAST)
        self.assertEqual(self.settled().read_text(), 'failed carr_find_failed\n')

    def test_record_layer_failure_stops_the_run(self):
        self.given([FILE_A, VENDOR], find_fails=True)
        outcome, state = self.run_triage()
        self.assertEqual((outcome.state, outcome.reason), ('failed', 'carr_find_failed'))
        self.assertEqual(state['moved'], [])

    def test_folder_pane_that_will_not_hide_fails_loudly(self):
        self.given([FILE_A], sidebar_stuck=True)
        outcome, state = self.run_triage()
        self.assertEqual((outcome.state, outcome.reason), ('failed', 'folder_pane_toggle_failed'))
        self.assertEqual(state['selections'], [])

    def test_window_closing_mid_run_stops_moving_cleanly(self):
        self.given([FILE_A, VENDOR, FIRM])
        closed = EVENING.replace(hour=21, minute=20)
        outcome, state = self.run_triage(clock=lambda: closed if json.loads(self.state.read_text())['moved']
                                         else EVENING)
        self.assertEqual((outcome.state, outcome.reason), ('timed_out', 'window_closed'))
        self.assertEqual(state['moved'], [[FILE_A['label'], 'folder-A']])
        self.assertEqual(state['selections'], [FILE_A['label']])
        self.assertTrue(state['sidebar'], 'folder pane restored')

    def test_a_folder_search_that_crosses_the_close_does_not_move(self):
        self.given([FILE_A, VENDOR])
        def clock():
            state = json.loads(self.state.read_text())
            return EVENING.replace(hour=21, minute=15) if state.get('picker') else EVENING
        outcome, state = self.run_triage(clock=clock)
        self.assertEqual((outcome.state, outcome.reason), ('timed_out', 'window_closed'))
        self.assertEqual(state['selections'], [FILE_A['label']])
        self.assertEqual(state['moved'], [])
        self.assertIsNone(state['picker'])
        self.assertTrue(state['sidebar'])

    def test_a_direct_run_outside_the_window_touches_nothing(self):
        self.given([FILE_A, VENDOR])
        morning = EVENING.replace(hour=10, minute=0)
        outcome, state = self.run_triage(morning)
        self.assertEqual((outcome.state, outcome.reason), ('skipped', 'outside_window'))
        self.assertEqual(triage.EXIT[outcome.state], 78)
        self.assertEqual(self.called('cua-driver') + self.called('pgrep') + self.called('ioreg'), [])
        self.assertEqual(state['moved'], [])
        self.assertFalse(self.settled().exists())

    def test_a_run_already_at_the_hard_stop_touches_nothing(self):
        self.given([FILE_A, VENDOR])
        outcome, state = self.run_triage(clock=lambda: EVENING.replace(hour=21, minute=15))
        self.assertEqual((outcome.state, outcome.reason), ('timed_out', 'window_closed'))
        self.assertEqual(self.called('cua-driver') + self.called('pgrep') + self.called('ioreg'), [])
        self.assertEqual((state['selections'], state['moved']), ([], []))
        self.assertFalse(self.settled().exists())

    def test_output_carries_counts_not_names(self):
        self.given([FILE_A, VENDOR, NEWS, UNREAD])
        with patch.dict(os.environ, self.env, clear=True), redirect_stdout(io.StringIO()) as out:
            triage.run(self.repo, self.home, EVENING, Waits(2, 0, 0, 0, 0, 0, 0), triage.Limits(),
                       lambda: EVENING)
        self.assertRegex(out.getvalue(), r'^outlook-triage succeeded filed moved=2 left=2\s*$')

    def test_fire_runs_only_inside_the_window_and_only_until_the_day_is_settled(self):
        folder = self.repo / triage.OUT
        with patch('os.execv') as execv, patch('os.chdir'):
            for outside in (EVENING.replace(hour=18, minute=0), EVENING.replace(minute=14),
                            LAST.replace(minute=10), EVENING.replace(hour=23, minute=30)):
                self.assertEqual(triage.fire(self.repo, outside), 0)
            execv.assert_not_called()
            self.assertIsNone(triage.fire(self.repo, EVENING))
            program, argv = execv.call_args.args
            self.assertEqual(argv[1:5], [str(self.repo / 'bin/run-scheduled.sh'), 'outlook-triage',
                                         'outlook.triage', sys.executable])
            execv.reset_mock()
            self.assertIsNone(triage.fire(self.repo, LAST))
            execv.assert_called_once()
            folder.mkdir(parents=True)
            (folder / '2026-10-20.settled').write_text('succeeded filed\n')
            execv.reset_mock()
            self.assertEqual(triage.fire(self.repo, LAST), 0)
            execv.assert_not_called()


def listed(message_id, row, sender_email, **extra):
    subject = Row(row['label'], '', False).subject
    return {'message_id': message_id, 'subject_hash': hashlib.sha256(subject.encode()).hexdigest(),
            'sent_utc': '2026-10-20T15:02:00Z', 'sender_email': sender_email, 'intake_note': 'kept', **extra}


class RefreshTest(FakeOutlookCase):
    READ_BODY = msg('sender-news', 'subject-010', email='news@news.test')
    UNREAD_BODY = msg('folder-A, client-001', 'subject-011', '8:15 AM', unread=True, email='c1@client.test')

    def test_refresh_worker_placement_and_entry_share_intake_host(self):
        catalog = json.loads((ROOT / 'ops/config/services.json').read_text())['services']
        services = {s['key']: s for s in catalog}
        intake_env = services['outlook-intake']['environments'][0]
        refresh_env = services['outlook-refresh']['environments'][0]
        self.assertEqual(refresh_env['runs_on'], intake_env['runs_on'])
        plist = plistlib.loads((ROOT / refresh_env['deploy_mechanism']).read_bytes())
        self.assertEqual(plist['WorkingDirectory'], '{{REPO}}')
        self.assertEqual(plist['Umask'], 0o077)
        self.assertNotIn('RunAtLoad', plist)
        self.assertIn('from lib.outlook_refresh import main; raise SystemExit(main())', plist['ProgramArguments'])
        self.assertEqual(plist['StartCalendarInterval'], calendar_for_window((7, 15), (19, 15), 30))
        installer = (ROOT / 'bin/install-outlook-intake.sh').read_text()
        self.assertIn('LABEL="com.carr.outlook-refresh"', installer)
        self.assertNotIn('launchctl bootstrap', '\n'.join(line for line in installer.splitlines()
                                                         if not line.lstrip().startswith(('print(', 'print '))))

    def test_same_host_refresh_worker_opens_only_read_mail_without_filing(self):
        from lib import outlook_refresh
        self.given([FILE_A, self.READ_BODY, self.UNREAD_BODY])
        path = self.refresh_list([listed('m1', self.READ_BODY, 'news@news.test'),
                                  listed('m2', self.UNREAD_BODY, 'c1@client.test')])
        self.guide.write_text('')
        write_settings(self.home, self.repo, account=ACCOUNT, driver=str(self.tmp / 'bin/cua-driver'))
        with patch.dict(os.environ, self.env, clear=True):
            outcome = outlook_refresh.run(self.repo, EVENING.replace(hour=10),
                                          waits=Waits(2, 0, 0, 0, 0, 0, 0),
                                          now_fn=lambda: EVENING.replace(hour=10))
        self.assertEqual(outcome.state, 'succeeded')
        state = json.loads(self.state.read_text())
        self.assertEqual(state['selections'], [self.READ_BODY['label']])
        self.assertEqual(state['moved'], [])
        entries = json.loads(path.read_text())
        self.assertIn('opened_at', entries[0])
        self.assertNotIn('opened_at', entries[1])
        self.assertEqual(len(entries), 2, 'opening is not a body-decoded acknowledgement')

    def test_refresh_ui_error_names_failed_step(self):
        from lib import outlook_refresh
        from lib.outlook_ui import UiError
        self.given([self.READ_BODY])
        self.refresh_list([listed('m1', self.READ_BODY, 'news@news.test')])
        output = io.StringIO()
        with patch.dict(os.environ, self.env, clear=True), \
                patch('lib.outlook_refresh.Window._inbox_rows',
                      side_effect=UiError('inbox_row_not_found', step='_inbox_rows')), \
                redirect_stderr(output):
            result = outlook_refresh.run(self.repo, EVENING.replace(hour=10),
                                        waits=Waits(2, 0, 0, 0, 0, 0, 0))
        self.assertEqual((result.state, result.reason), ('failed', 'ui_error'))
        self.assertIn('outlook-refresh UiError at _inbox_rows: inbox_row_not_found',
                      output.getvalue())
        self.assertEqual(result.detail, 'at _inbox_rows: inbox_row_not_found')

    def test_refresh_search_error_detail_reaches_cli(self):
        from lib import outlook_refresh
        from lib.outlook_ui import UiError
        self.given([self.READ_BODY])
        self.refresh_list([listed('m1', self.READ_BODY, 'news@news.test')])
        with patch.dict(os.environ, self.env, clear=True), \
                patch('lib.outlook_refresh.Window.search',
                      side_effect=UiError('search_field_not_found', step='search')):
            result = outlook_refresh.run(self.repo, EVENING.replace(hour=10),
                                        waits=Waits(2, 0, 0, 0, 0, 0, 0))
        self.assertEqual((result.state, result.reason), ('failed', 'search_field_not_found'))
        self.assertEqual(result.detail, 'at search: search_field_not_found')
        output = io.StringIO()
        with patch('lib.outlook_refresh.run', return_value=result), redirect_stdout(output):
            self.assertEqual(outlook_refresh.main(), 1)
        self.assertIn('at search: search_field_not_found', output.getvalue())

    def test_refresh_failure_logs_category_without_exception_content(self):
        from lib import outlook_refresh
        from lib.outlook_ui import CuaError, UiError
        self.given([self.READ_BODY])
        self.refresh_list([listed('m1', self.READ_BODY, 'news@news.test')])
        for error, reason, message in (
                (UiError, 'ui_error', 'Outlook UI interaction failed'),
                (OSError, 'os_error', 'Operating system operation failed'),
                (CuaError, 'cua_error', 'Computer automation call failed')):
            with self.subTest(error=error.__name__):
                output = io.StringIO()
                with patch.dict(os.environ, self.env, clear=True), \
                        patch('lib.outlook_refresh.Window.front',
                              side_effect=error('private subject sender body\nsecret')), \
                        redirect_stderr(output), redirect_stdout(output):
                    result = outlook_refresh.run(
                        self.repo, EVENING.replace(hour=10),
                        waits=Waits(2, 0, 0, 0, 0, 0, 0))
                self.assertEqual((result.state, result.reason), ('failed', reason))
                self.assertEqual(output.getvalue(),
                                 f'outlook-refresh {error.__name__}: {message}\n')

    def test_refresh_worker_refuses_wrong_account_and_active_user(self):
        from lib import outlook_refresh
        self.given([self.READ_BODY])
        self.refresh_list([listed('m1', self.READ_BODY, 'news@news.test')])
        write_settings(self.home, self.repo, account='wrong@example.test', driver=str(self.tmp / 'bin/cua-driver'))
        with patch.dict(os.environ, self.env, clear=True):
            result = outlook_refresh.run(self.repo, EVENING.replace(hour=10),
                                         waits=Waits(2, 0, 0, 0, 0, 0, 0))
        self.assertEqual(result.reason, 'wrong_account')
        self.assertEqual(json.loads(self.state.read_text())['selections'], [])
        self.given([self.READ_BODY], idle_seconds=1)
        with patch.dict(os.environ, self.env, clear=True):
            result = outlook_refresh.run(self.repo, EVENING.replace(hour=10))
        self.assertEqual(result.reason, 'user_active')
        self.assertEqual(json.loads(self.state.read_text())['selections'], [])

    def test_read_email_is_opened_unchanged_and_stamped(self):
        self.given([FILE_A, self.READ_BODY, NEWS])
        path = self.refresh_list([listed('m1', self.READ_BODY, 'news@news.test')])
        outcome, state = self.run_triage()
        self.assertEqual(outcome.state, 'succeeded')
        self.assertIn(self.READ_BODY['label'], state['selections'])
        self.assertNotIn('Mark Unread', state['presses'])
        entry = json.loads(path.read_text())[0]
        self.assertEqual(entry['intake_note'], 'kept')
        self.assertRegex(entry['opened_at'], r'^2\d{3}-')
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertIn('opened: 1', self.report())

    def test_unread_email_is_never_selected_and_waits_until_joe_reads_it(self):
        self.given([FILE_A, NEWS, self.UNREAD_BODY])
        path = self.refresh_list([listed('m2', self.UNREAD_BODY, 'c1@client.test')])
        outcome, state = self.run_triage()
        self.assertEqual((outcome.state, outcome.reason), ('succeeded', 'filed'))
        self.assertNotIn(self.UNREAD_BODY['label'], state['selections'])
        self.assertNotIn('Mark Unread', state['presses'])
        self.assertEqual(self.unread_labels(state), {self.UNREAD_BODY['label']})
        self.assertIn('unread_skipped: 1', self.report())
        self.assertNotIn('opened_at', json.loads(path.read_text())[0])
        self.assertEqual([m[0] for m in state['moved']], [FILE_A['label']])

    def test_a_search_that_crosses_the_close_opens_nothing(self):
        self.given([FILE_A, self.READ_BODY])
        path = self.refresh_list([listed('m5', self.READ_BODY, 'news@news.test')])
        closed = EVENING.replace(hour=21, minute=16)
        outcome, state = self.run_triage(
            clock=lambda: closed if json.loads(self.state.read_text()).get('search_typed') else EVENING)
        self.assertEqual((outcome.state, outcome.reason), ('timed_out', 'window_closed'))
        self.assertEqual(state['selections'], [])
        self.assertEqual(state['moved'], [])
        self.assertNotIn('opened_at', json.loads(path.read_text())[0])

    def test_an_ignored_click_is_not_counted_as_opened(self):
        self.given([FILE_A, self.READ_BODY], search_click_ignored=True)
        path = self.refresh_list([listed('m6', self.READ_BODY, 'news@news.test')])
        outcome, state = self.run_triage()
        self.assertEqual(outcome.state, 'succeeded')
        self.assertNotIn(self.READ_BODY['label'], state['selections'])
        self.assertIn('  not_opened: 1', self.report())
        self.assertNotRegex(self.report(), r'(?m)^  opened: ')
        self.assertNotIn('opened_at', json.loads(path.read_text())[0])

    def test_unmatched_entries_are_counted_and_left_alone(self):
        self.given([FILE_A, self.READ_BODY])
        path = self.refresh_list([listed('m3', self.READ_BODY, 'nobody@example.com')])
        outcome, state = self.run_triage()
        self.assertEqual(outcome.state, 'succeeded')
        self.assertIn('not_found: 1', self.report())
        self.assertNotIn('opened_at', json.loads(path.read_text())[0])

    def test_capped_per_night_never_opened_first_then_least_recently_opened(self):
        self.assertEqual(triage.Limits().max_refreshes, 50)
        self.given([self.READ_BODY])
        self.refresh_list([listed('a', self.READ_BODY, 'a@example.com', opened_at='2026-10-19T01:00:00Z'),
                           listed('b', self.READ_BODY, 'b@example.com'),
                           listed('c', self.READ_BODY, 'c@example.com', opened_at='2026-10-10T01:00:00Z'),
                           listed('d', self.READ_BODY, 'd@example.com')])
        self.run_triage(limits=triage.Limits(max_refreshes=3))
        searched = [json.loads(c[2])['text'] for c in self.called('cua-driver') if c[1] == 'type_text']
        self.assertEqual(searched, ['b@example.com', 'd@example.com', 'c@example.com'])

    def test_malformed_list_fails_loudly(self):
        self.given([FILE_A])
        self.refresh_list([{'message_id': 'm4'}])
        outcome, state = self.run_triage()
        self.assertEqual((outcome.state, outcome.reason), ('failed', 'refresh_list_invalid'))
        self.assertEqual(state['moved'], [])


class DecideTest(unittest.TestCase):
    guide = triage.FolderGuide.parse(GUIDE, deal_prefix='Deals/', vendor_prefix='Networking/Vendors/A.List/')

    def decide(self, sender, find=None):
        row = Row(msg(sender, 'subject-x')['label'], 't', False)
        return triage.decide(row, self.guide, lambda name: (find or {}).get(name, {'parties': [], 'deals': []}))

    def party(self, name, **extra):
        return {name: {'parties': [{'name': name, 'kind': 'client', 'ref': 'C-9', 'merged': False, **extra}],
                       'deals': []}}

    def test_business_name_must_match_the_folder_exactly(self):
        self.assertEqual(self.decide('national-folder-Bx', self.party('national-folder-Bx')),
                         triage.Leave('no_rule_matched'))
        self.assertEqual(self.decide('national-folder-B', self.party('national-folder-B')),
                         triage.Move('Deals/Active Deals/_group-1/national-folder-B', 'deal'))

    def test_surname_does_not_match_a_dotted_folder(self):
        self.assertEqual(self.decide('client-005 folder-G', self.party('client-005 folder-G')),
                         triage.Leave('no_rule_matched'))

    def test_two_exact_party_matches_are_not_a_match(self):
        twice = {'client-001 folder-A': {'parties': FIND['client-001 folder-A']['parties'] * 2, 'deals': []}}
        self.assertEqual(self.decide('client-001 folder-A', twice), triage.Leave('no_rule_matched'))

    def test_vendor_party_never_matches_a_deal(self):
        vendor = {'client-001 folder-A': {'parties': [{'name': 'client-001 folder-A', 'kind': 'vendor'}],
                                          'deals': []}}
        self.assertEqual(self.decide('client-001 folder-A', vendor), triage.Leave('no_rule_matched'))

    def test_linked_deal_name_matches(self):
        linked = {'client-006': {'parties': [{'name': 'client-006', 'kind': 'client', 'ref': 'C-5'}],
                                 'deals': [{'name': 'folder-H', 'client_ref': 'C-5'},
                                           {'name': 'folder-D', 'client_ref': 'C-6'}]}}
        self.assertEqual(self.decide('client-006', linked), triage.Move('Deals/Prospects/folder-H', 'deal'))

    def test_map_matches_display_name_and_sender_domain(self):
        self.assertEqual(self.decide('sender-firm'), triage.Move('CARR/folder-C', 'map'))
        self.assertEqual(self.decide('news@firm.test'), triage.Move('CARR/folder-C', 'map'))
        self.assertEqual(self.decide('news@other.test'), triage.Leave('no_rule_matched'))

    def test_unread_and_unparseable_rows_are_never_candidates(self):
        unread = Row('Unread, ' + msg('sender-firm', 'subject-x')['label'], 't', False)
        self.assertEqual(triage.decide(unread, self.guide, None), triage.Leave('unread'))
        self.assertEqual(triage.decide(Row('Meeting request', 't', False), self.guide, None),
                         triage.Leave('uncertain'))

    def test_guide_without_deals_or_with_a_dangling_map_is_refused(self):
        with self.assertRaises(ValueError):
            triage.FolderGuide.parse('CARR\nCARR/folder-C\n', deal_prefix='Deals/', vendor_prefix='Networking/Vendors/A.List/')
        with self.assertRaises(ValueError):
            triage.FolderGuide.parse(GUIDE + 'map: sender-x -> CARR/folder-Z\n', deal_prefix='Deals/', vendor_prefix='Networking/Vendors/A.List/')


class InstallTest(unittest.TestCase):
    """The installer is checked by reading it: running it here could load a real job on this Mac."""

    installer = (ROOT / 'bin/install-outlook-triage.sh').read_text()

    def test_plist_fires_every_quarter_hour_from_19_15_to_21_00_privately_through_the_gate(self):
        plist = plistlib.loads((ROOT / 'ops/launchd/com.carr.outlook-triage.plist').read_bytes())
        self.assertEqual(plist['StartCalendarInterval'], calendar_for_window((19, 15), (21, 0), 15))
        self.assertNotIn('RunAtLoad', plist)
        self.assertEqual(plist['Umask'], 0o077)
        self.assertEqual(plist['ProgramArguments'][1:],
                         ['-c', 'from lib.outlook_triage import fire; raise SystemExit(fire())'])

    def test_installer_never_loads_the_job_and_prints_the_command_instead(self):
        commands = [line.strip() for line in self.installer.splitlines()
                    if line.strip() and not line.lstrip().startswith(('#', 'print '))]
        self.assertEqual([c for c in commands if 'launchctl' in c], [])
        self.assertRegex(self.installer, r'print -- .*launchctl bootstrap gui/\$\(id -u\) \$DEST')

    def test_installer_creates_the_launchd_log_private_in_advance(self):
        plist = plistlib.loads((ROOT / 'ops/launchd/com.carr.outlook-triage.plist').read_bytes())
        log = plist['StandardOutPath'].replace('{{REPO}}', '$REPO')
        self.assertIn(f'LOG="{log}"', self.installer)
        self.assertIn('[ -e "$LOG" ] || /usr/bin/install -m 600 /dev/null "$LOG"', self.installer)
        self.assertIn('/bin/chmod 600 "$LOG"', self.installer)
        self.assertLess(self.installer.index('/bin/chmod 600 "$LOG"'), self.installer.index('print -- '))

    def test_installer_checks_machine_scope_before_private_mail_guide(self):
        self.assertLess(self.installer.index("-m lib.outlook_settings service triage"),
                        self.installer.index('if [ ! -s "$GUIDE"'))

    def test_service_runs_on_the_primary_mac_daily(self):
        services = json.loads((ROOT / 'ops/config/services.json').read_text())['services']
        service = next(s for s in services if s['key'] == triage.SERVICE)
        env = service['environments'][0]
        self.assertEqual(env['runs_on'], 'primary')
        self.assertEqual(env['deploy_mechanism'], 'ops/launchd/com.carr.outlook-triage.plist')
        from lib.launchd_scope import allowed_on_machine
        self.assertTrue(allowed_on_machine('com.carr.outlook-triage.plist', primary=True))
        self.assertFalse(allowed_on_machine('com.carr.outlook-triage.plist', primary=False))
        self.assertIn("-m lib.outlook_settings service triage", self.installer)
        self.assertGreaterEqual(env['expected_cadence_seconds'] + env['cadence_grace_seconds'], 27.5 * 3600)


if __name__ == '__main__':
    unittest.main()
