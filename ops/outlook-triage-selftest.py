#!/usr/bin/env python3
"""Offline tests for the evening Outlook triage. A fake cua-driver plays Outlook; nothing real is touched.

The fake mirrors the shapes read from Joe's live Outlook on 2026-10-20: a folder
pane (AXOutline) that View > Sidebar toggles, an AXTable "Message List" whose
AXRow children each hold one AXCell labelled "Sender: ..., Subject: ...", group
header cells directly under the table, and snapshot-scoped element tokens that go
stale. Like Outlook it marks a message read when it is selected and when the
selection moves on, and shows an unread row's label starting with "Unread".
Every name here is invented.
"""
from contextlib import redirect_stdout
from datetime import datetime
import io
import json
import os
from pathlib import Path
import plistlib
import shutil
import stat
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from lib import outlook_triage as triage
from lib.launchd_calendar import calendar_for_window
from lib.outlook_ui import Row, Waits

ZONE = triage.ZONE
EVENING = datetime(2026, 10, 20, 19, 15, tzinfo=ZONE)
LAST = datetime(2026, 10, 20, 21, 0, tzinfo=ZONE)
GUIDE = '''### Choosing the folder
The folder tree (all folders live under Inbox; "/" separates levels):
Deals
Deals/Active Deals
Deals/Active Deals/_National Accounts
Deals/Active Deals/_National Accounts/Zephyrologie
Deals/Active Deals/Quillfeather
Deals/Active Deals/Connor.Kevin
Deals/Active Deals/Ambrose
Deals/Prospects
Deals/Prospects/Brightwater Dental
Deals/Prospects/Lark
Networking
Networking/Vendors
Networking/Vendors/A.List
Networking/Vendors/A.List/Vera Example
CARR
CARR/Company Wide
Reference
Reference/Ambrose   (old correspondence)
map: Firm Announcements -> CARR/Company Wide
map: @carr.test -> CARR/Company Wide

Matching guidance:
- Deal folders are named for the client: surname or business.
'''
MAILBOX = 'Inbox • owner@example.com'


def window(window_id, title, width, height, on_current_space=True):
    return {'window_id': window_id, 'title': title, 'on_current_space': on_current_space,
            'bounds': {'x': 0, 'y': 33, 'width': width, 'height': height}}


TOOLBAR = window(11, '', 1512, 32, False)
FULL_SCREEN = window(12, MAILBOX, 1512, 949, False)
ORDINARY = window(13, MAILBOX, 1230, 768)
COMPOSE = window(14, 'Re: example subject', 917, 911)


def msg(sender, subject, when='10:02 AM', unread=False):
    return {'label': f'Sender: {sender}, Subject: {subject},      {when},        '
                     f'Message preview: invented preview text', 'unread': unread}


QUILL = msg('Quillfeather, Pat', 'Lease draft')
VENDOR = msg('Vera Example', 'Invoice 12')
FIRM = msg('Firm Announcements', 'Office closed Friday')
NEWS = msg('Weekly Digest', 'Ten things this week')
UNREAD = msg('Quillfeather, Pat', 'Follow up', '9:41 AM', unread=True)
UNCERTAIN = {'label': 'Meeting request, 9:30 AM', 'unread': False}
AMBIGUOUS = msg('Ann Lark', 'Two folders')
NOT_UNIQUE = msg('Ambrose, Ida', 'Same name twice')

FIND = {
    'Pat Quillfeather': {'parties': [{'name': 'Pat Quillfeather', 'kind': 'client', 'ref': 'C-1',
                                      'org_name': None, 'merged': False}], 'deals': []},
    'Ann Lark': {'parties': [{'name': 'Ann Lark', 'kind': 'client', 'ref': 'C-2',
                              'org_name': 'Brightwater Dental', 'merged': False}], 'deals': []},
    'Ida Ambrose': {'parties': [{'name': 'Ida Ambrose', 'kind': 'lead', 'ref': 'L-3',
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
    return lists()[state['tab'] or 'only']
def mark_read(label):
    for rows in lists().values():
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
    state['running'] = False
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
    top = add('AXWindow', MAILBOX, 0)
    for label in ('New Mail', 'Delete', 'Archive', 'Move', 'Flag', 'Mark Unread'):
        add('AXButton', label, 1, top, ['button', label])
    for _ in range(3):
        add('AXTextField', 'Search', 1, top)
    if state['sidebar']:
        outline = add('AXOutline', '', 2, top)
        for folder in ['Favorites', 'Inbox'] + state['folder_rows']:
            row = add('AXRow', '', 3, outline, selected=False)
            add('AXStaticText', folder, 6, row, value=folder)
            if folder == 'Inbox':
                unread = state.get('badge')
                if unread is None:
                    unread = sum(r['unread'] for rows in lists().values() for r in rows)
                if unread:
                    add('AXStaticText', str(unread), 6, row, value=str(unread))
    if state['tab']:
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
        state['running'] = False
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
        if kind != ['picker_field']:
            fail('not a text field')
        state['picker'] = payload['text']
    elif kind and kind[0] == 'row':
        select(kind[1])
    elif kind == ['button', 'Move']:
        state['presses'].append('Move')
        if state.get('selected'):
            state['picker'] = ''
    elif kind and kind[0] == 'button':
        state['presses'].append(kind[1])
    elif kind and kind[0] == 'folder' and state.get('picker') is not None:
        state['picker'] = None
        if state['move_works']:
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
    save()
    print(json.dumps({'effect': 'unverifiable'}))
elif tool == 'press_key':
    state['picker'] = None
    save()
    print(json.dumps({'effect': 'unverifiable'}))
else:
    fail('unexpected tool ' + tool)
sys.exit(0)
'''.replace('MAILBOX', repr(MAILBOX))


class OutlookTriageTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='outlook-triage-selftest-'))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.repo, self.home, bin_dir = self.tmp / 'repo', self.tmp / 'home', self.tmp / 'bin'
        (self.home / triage.GUIDE).parent.mkdir(parents=True)
        (self.home / triage.GUIDE).write_text(GUIDE)
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
            'idle_seconds': 3600, 'sidebar': True, 'tab': 'focused' if tabs else None,
            'lists': lists, 'folder_rows': ['Deals', 'Active Deals', 'Quillfeather'],
            'picker_folders': ['Quillfeather', 'Vera Example', 'Company Wide', 'Ambrose', 'Lark',
                               'Brightwater Dental', 'Zephyrologie', 'Connor.Kevin'],
            'move_works': True, 'snapshot': 0, 'selections': [], 'presses': [], 'moved': [],
            'find': FIND, **state}))

    def run_triage(self, now=EVENING):
        with patch.dict(os.environ, self.env, clear=True), redirect_stdout(io.StringIO()):
            outcome = triage.run(self.repo, self.home, now, Waits(0, 0, 0, 0, 0, 0))
        return outcome, json.loads(self.state.read_text())

    def called(self, name):
        if not self.calls.exists():
            return []
        rows = [json.loads(line) for line in self.calls.read_text().splitlines()]
        return [row[1:] for row in rows if row[0] == name]

    def report(self):
        return (self.repo / triage.OUT / '2026-10-20.report.md').read_text()

    def settled(self):
        return (self.repo / triage.OUT / '2026-10-20.settled')

    def unread_labels(self, state):
        return {r['label'] for rows in state['lists'].values() for r in rows if r['unread']}

    def test_files_read_email_by_each_rule_and_never_selects_unread(self):
        self.given([QUILL, VENDOR, FIRM, NEWS, UNCERTAIN, UNREAD])
        outcome, state = self.run_triage()
        self.assertEqual((outcome.state, outcome.reason), ('succeeded', 'filed'))
        self.assertEqual(state['moved'], [[QUILL['label'], 'Quillfeather'],
                                          [VENDOR['label'], 'Vera Example'],
                                          [FIRM['label'], 'Company Wide']])
        self.assertNotIn(UNREAD['label'], state['selections'])
        self.assertNotIn(UNCERTAIN['label'], state['selections'])
        self.assertEqual(self.unread_labels(state), {UNREAD['label']})
        self.assertEqual(set(state['presses']), {'Move'})
        report = self.report()
        self.assertIn('-> Deals/Active Deals/Quillfeather (deal)', report)
        self.assertIn('-> Networking/Vendors/A.List/Vera Example (vendor)', report)
        self.assertIn('-> CARR/Company Wide (map)', report)
        self.assertIn('no_rule_matched: 1', report)
        self.assertIn('uncertain: 1', report)
        self.assertIn('unread: 1', report)
        self.assertEqual(stat.S_IMODE((self.repo / triage.OUT / '2026-10-20.report.md').stat().st_mode), 0o600)
        self.assertEqual(self.settled().read_text(), 'succeeded filed\n')
        self.assertTrue(state['sidebar'], 'folder pane restored')

    def test_reads_and_files_both_focused_and_other_tabs(self):
        self.given(tabs={'focused': [NEWS, QUILL], 'other': [FIRM, NEWS, UNREAD]})
        outcome, state = self.run_triage()
        self.assertEqual(outcome.state, 'succeeded')
        self.assertEqual([m[1] for m in state['moved']], ['Quillfeather', 'Company Wide'])
        self.assertEqual(self.unread_labels(state), {UNREAD['label']})

    def test_row_next_to_unread_is_left_even_when_outlook_opens_the_next_message(self):
        self.given([QUILL, UNREAD, VENDOR], open_next_after_move=True)
        outcome, state = self.run_triage()
        self.assertEqual(outcome.state, 'succeeded')
        self.assertEqual(state['moved'], [])
        self.assertEqual(state['selections'], [])
        self.assertEqual(self.unread_labels(state), {UNREAD['label']})
        self.assertIn('next_to_unread: 2', self.report())

    def test_move_proceeds_when_both_neighbours_are_read(self):
        self.given([NEWS, QUILL, NEWS | {'label': NEWS['label'] + ' 2'}, UNREAD], open_next_after_move=True)
        outcome, state = self.run_triage()
        self.assertEqual(outcome.state, 'succeeded')
        self.assertEqual([m[0] for m in state['moved']], [QUILL['label']])
        self.assertEqual(self.unread_labels(state), {UNREAD['label']})

    def test_ambiguous_or_shared_folder_names_stay_in_the_inbox(self):
        self.given([AMBIGUOUS, NEWS, NOT_UNIQUE])
        outcome, state = self.run_triage()
        self.assertEqual(outcome.state, 'succeeded')
        self.assertEqual(state['moved'], [])
        report = self.report()
        self.assertIn('deal_ambiguous: 1', report)
        self.assertIn('folder_name_not_unique: 1', report)

    def test_two_failed_moves_stop_the_run(self):
        self.given([QUILL, VENDOR, FIRM], move_works=False)
        outcome, state = self.run_triage()
        self.assertEqual((outcome.state, outcome.reason), ('failed', 'move_failed_twice'))
        self.assertEqual(state['presses'], ['Move', 'Move'])
        self.assertEqual(state['moved'], [])
        self.assertEqual(triage.EXIT[outcome.state], 1)

    def test_unread_email_read_during_the_run_fails_it_and_names_the_email(self):
        self.given([QUILL, NEWS, UNREAD], read_elsewhere_after_move=[UNREAD['label']])
        outcome, state = self.run_triage()
        self.assertEqual((outcome.state, outcome.reason), ('failed', 'unread_set_changed'))
        self.assertIn('Sender: Quillfeather, Pat, Subject: Follow up, 9:41 AM', self.report())
        self.assertNotIn('invented preview text', self.report())

    def test_unread_rows_that_do_not_add_up_to_the_badge_block_every_move(self):
        self.given([QUILL, VENDOR, UNREAD], badge=2)
        outcome, state = self.run_triage()
        self.assertEqual((outcome.state, outcome.reason), ('failed', 'unread_set_unverified'))
        self.assertEqual(state['selections'], [])
        self.assertEqual(state['presses'], [])

    def test_a_selected_unread_row_blocks_every_move(self):
        self.given([QUILL, VENDOR, UNREAD], selected=UNREAD['label'])
        outcome, state = self.run_triage()
        self.assertEqual((outcome.state, outcome.reason), ('failed', 'unread_row_selected'))
        self.assertEqual(state['selections'], [])

    def test_active_user_skips_without_touching_outlook_until_the_last_fire(self):
        self.given([QUILL], idle_seconds=120)
        outcome, state = self.run_triage()
        self.assertEqual((outcome.state, outcome.reason), ('skipped', 'user_active'))
        self.assertEqual(self.called('cua-driver') + self.called('open'), [])
        self.assertFalse(self.settled().exists())
        outcome, _ = self.run_triage(LAST)
        self.assertEqual(outcome.state, 'skipped')
        self.assertEqual(self.settled().read_text(), 'skipped user_active\n')

    def test_open_draft_skips_the_night(self):
        self.given([QUILL], windows=[TOOLBAR, ORDINARY, COMPOSE])
        outcome, state = self.run_triage()
        self.assertEqual((outcome.state, outcome.reason), ('skipped', 'compose_window_open'))
        self.assertTrue(state['running'], 'Outlook left running')
        self.assertEqual(state['moved'], [])

    def test_missing_folder_guide_fails_before_outlook(self):
        self.given([QUILL])
        (self.home / triage.GUIDE).unlink()
        outcome, _ = self.run_triage()
        self.assertEqual((outcome.state, outcome.reason), ('failed', 'folder_guide_missing'))
        self.assertEqual(self.called('cua-driver') + self.called('pgrep'), [])
        self.assertTrue(self.settled().exists())

    def test_record_layer_failure_stops_the_run(self):
        self.given([QUILL, VENDOR], find_fails=True)
        outcome, state = self.run_triage()
        self.assertEqual((outcome.state, outcome.reason), ('failed', 'carr_find_failed'))
        self.assertEqual(state['moved'], [])

    def test_folder_pane_that_will_not_hide_fails_loudly(self):
        self.given([QUILL], sidebar_stuck=True)
        outcome, state = self.run_triage()
        self.assertEqual((outcome.state, outcome.reason), ('failed', 'folder_pane_toggle_failed'))
        self.assertEqual(state['selections'], [])

    def test_output_carries_counts_not_names(self):
        self.given([QUILL, VENDOR, NEWS, UNREAD])
        with patch.dict(os.environ, self.env, clear=True), redirect_stdout(io.StringIO()) as out:
            triage.run(self.repo, self.home, EVENING, Waits(0, 0, 0, 0, 0, 0))
        printed = out.getvalue()
        self.assertRegex(printed, r'^outlook-triage succeeded filed moved=2 left=2\s*$')

    def test_fire_exits_quietly_once_the_day_is_settled_and_hands_over_otherwise(self):
        folder = self.repo / triage.OUT
        with patch('os.execv') as execv, patch('os.chdir'):
            self.assertIsNone(triage.fire(self.repo, EVENING))
            program, argv = execv.call_args.args
            self.assertEqual(argv[1:5], [str(self.repo / 'bin/run-scheduled.sh'), 'outlook-triage',
                                         'outlook.triage', sys.executable])
            folder.mkdir(parents=True)
            (folder / '2026-10-20.settled').write_text('succeeded filed\n')
            execv.reset_mock()
            self.assertEqual(triage.fire(self.repo, LAST), 0)
            execv.assert_not_called()


class DecideTest(unittest.TestCase):
    guide = triage.FolderGuide.parse(GUIDE)

    def decide(self, sender, find=None):
        row = Row(msg(sender, 'x')['label'], 't', False)
        return triage.decide(row, self.guide, lambda name: (find or {}).get(name, {'parties': [], 'deals': []}))

    def party(self, name, **extra):
        return {name: {'parties': [{'name': name, 'kind': 'client', 'ref': 'C-9', 'merged': False, **extra}],
                       'deals': []}}

    def test_business_name_must_match_the_folder_exactly(self):
        self.assertEqual(self.decide('Zephyrology', self.party('Zephyrology')), triage.Leave('no_rule_matched'))
        self.assertEqual(self.decide('Zephyrologie', self.party('Zephyrologie')),
                         triage.Move('Deals/Active Deals/_National Accounts/Zephyrologie', 'deal'))

    def test_surname_does_not_match_a_dotted_folder(self):
        self.assertEqual(self.decide('Kevin Connor', self.party('Kevin Connor')), triage.Leave('no_rule_matched'))

    def test_two_exact_party_matches_are_not_a_match(self):
        twice = {'Pat Quillfeather': {'parties': FIND['Pat Quillfeather']['parties'] * 2, 'deals': []}}
        self.assertEqual(self.decide('Pat Quillfeather', twice), triage.Leave('no_rule_matched'))

    def test_vendor_party_never_matches_a_deal(self):
        vendor = {'Pat Quillfeather': {'parties': [{'name': 'Pat Quillfeather', 'kind': 'vendor'}], 'deals': []}}
        self.assertEqual(self.decide('Pat Quillfeather', vendor), triage.Leave('no_rule_matched'))

    def test_linked_deal_name_matches(self):
        linked = {'Rae Holt': {'parties': [{'name': 'Rae Holt', 'kind': 'client', 'ref': 'C-5'}],
                               'deals': [{'name': 'Brightwater Dental', 'client_ref': 'C-5'},
                                         {'name': 'Lark', 'client_ref': 'C-6'}]}}
        self.assertEqual(self.decide('Rae Holt', linked),
                         triage.Move('Deals/Prospects/Brightwater Dental', 'deal'))

    def test_map_matches_display_name_and_sender_domain(self):
        self.assertEqual(self.decide('Firm Announcements'), triage.Move('CARR/Company Wide', 'map'))
        self.assertEqual(self.decide('news@carr.test'), triage.Move('CARR/Company Wide', 'map'))
        self.assertEqual(self.decide('news@other.test'), triage.Leave('no_rule_matched'))

    def test_unread_and_unparseable_rows_are_never_candidates(self):
        unread = Row('Unread, ' + msg('Firm Announcements', 'x')['label'], 't', False)
        self.assertEqual(triage.decide(unread, self.guide, None), triage.Leave('unread'))
        self.assertEqual(triage.decide(Row('Meeting request', 't', False), self.guide, None),
                         triage.Leave('uncertain'))

    def test_guide_without_deals_or_with_a_dangling_map_is_refused(self):
        with self.assertRaises(ValueError):
            triage.FolderGuide.parse('CARR\nCARR/Company Wide\n')
        with self.assertRaises(ValueError):
            triage.FolderGuide.parse(GUIDE + 'map: someone -> CARR/Nowhere\n')


class InstallTest(unittest.TestCase):
    def test_plist_fires_every_quarter_hour_from_19_15_to_21_00_through_the_gate(self):
        plist = plistlib.loads((ROOT / 'ops/launchd/com.carr.outlook-triage.plist').read_bytes())
        self.assertEqual(plist['StartCalendarInterval'], calendar_for_window((19, 15), (21, 0), 15))
        self.assertNotIn('RunAtLoad', plist)
        self.assertEqual(plist['ProgramArguments'][1:],
                         ['-c', 'from lib.outlook_triage import fire; raise SystemExit(fire())'])

    def test_service_runs_on_the_secondary_mac_daily(self):
        services = json.loads((ROOT / 'ops/config/services.json').read_text())['services']
        service = next(s for s in services if s['key'] == triage.SERVICE)
        env = service['environments'][0]
        self.assertEqual(env['runs_on'], 'secondary')
        self.assertEqual(env['deploy_mechanism'], 'ops/launchd/com.carr.outlook-triage.plist')
        self.assertGreaterEqual(env['expected_cadence_seconds'] + env['cadence_grace_seconds'], 27.5 * 3600)


if __name__ == '__main__':
    unittest.main()
