"""Business work reaches only the record-layer desk, with the business brief."""
import contextlib
import io
import json
import os
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1]))
import claude_desktop_wire
import desks
import dispatch
import work_runner
from work_queue import WorkQueue
from lib import business_dispatch

FAKE_CLAUDE = '''#!/usr/bin/env python3
import json, os, sys
if sys.argv[1:2] == ["agents"]:
    print(json.dumps([{"id": "abcdef12", "sessionId": "abcdef12-0000-4000-8000-000000000000",
                       "kind": "background", "state": "running"}]))
    raise SystemExit(0)
with open(os.environ["FAKE_CLAUDE_LOG"], "a") as log:
    log.write(json.dumps(sys.argv[1:]) + "\\n")
print("backgrounded · abcdef12")
'''


def blocks(name):
    return (HERE / name).read_text(encoding='utf-8').strip().split('\n\n')


class BusinessDispatchTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.db = self.root / 'queue.sqlite3'
        self.claude_ledger_read = dispatch.claude_api_wire.Ledger.read
        for p in (patch.dict(os.environ, {'CARR_WORK_QUEUE_DB': str(self.db)}),
                  patch.object(work_runner, 'kick'),
                  patch.object(dispatch.claude_api_wire.Ledger, 'read', return_value={
                      'spent_usd': '0', 'reserved_usd': '0', 'budget_usd': '200', 'stop': None}),
                  patch.object(dispatch.nous_budget.Ledger, 'read', return_value={
                      'spent_usd': '0', 'reserved_usd': '0', 'budget_usd': '22', 'stop': None}),
                  patch.object(dispatch.seat_gate, 'check', return_value=None)):
            p.start()
            self.addCleanup(p.stop)
        self.reg = desks.Registry(self.root / 'desks.json')
        self.reg.register('biz-desk', 'claude-desktop', model='claude-sonnet-5-5', effort='medium',
                          cwd=str(self.root), posture='record-layer')
        self.reg.register('coder', 'claude-desktop', model='claude-sonnet-5-5', effort='medium', cwd=str(self.root))
        self.results = self.root / 'results.jsonl'

    def send(self, argv, stdin=''):
        out, err = io.StringIO(), io.StringIO()
        with patch.object(sys, 'stdin', io.StringIO(stdin)), \
             contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = dispatch.main(['--registry', str(self.reg.path), '--results', str(self.results)] + argv)
        return subprocess.CompletedProcess(argv, code, out.getvalue(), err.getvalue())

    def in_process(self, argv, *, input, **_):
        return self.send(argv[2:], input)

    def task(self, card):
        with WorkQueue(self.db) as q:
            return q._item(card)['task']

    def test_business_brief_replaces_the_engineering_footer(self):
        self.assertEqual(self.send(['send', 'biz-desk', 'Draft a reply.', '--brief', 'business',
                                    '--card-id', 'b1']).returncode, 0)
        task = self.task('b1')
        for block in blocks('business-brief-footer.md'):
            self.assertIn(block, task)
        for block in blocks('brief-footer.md'):
            self.assertNotIn(block, task)

    def test_engineering_brief_is_unchanged_by_default(self):
        self.assertEqual(self.send(['send', 'coder', 'Fix the build.', '--card-id', 'e1']).returncode, 0)
        expected = 'Fix the build.' + ''.join('\n\n' + b for b in blocks('brief-footer.md'))
        self.assertEqual(self.task('e1'), expected)

    def test_briefs_and_desks_must_pair(self):
        wrong = self.send(['send', 'coder', 'Draft a reply.', '--brief', 'business', '--card-id', 'x1'])
        self.assertEqual((wrong.returncode, 'brief_desk_mismatch' in wrong.stderr), (2, True))
        wrong = self.send(['send', 'biz-desk', 'Fix the build.', '--card-id', 'x2'])
        self.assertEqual((wrong.returncode, 'brief_desk_mismatch' in wrong.stderr), (2, True))
        with WorkQueue(self.db) as q:
            self.assertEqual(q.export()['items'], [])

    def test_registration_change_cannot_widen_a_queued_business_task(self):
        self.send(['send', 'business', 'Read the record.', '--brief', 'business', '--card-id', 'changed'])
        self.reg.register('biz-desk', 'claude-desktop', model='claude-sonnet-5-5', effort='medium',
                          cwd=str(self.root))
        with WorkQueue(self.db) as q, patch.object(dispatch, '_execute') as execute:
            claim = q.claim(eligible_desks={'biz-desk'}, actor='runner', owner={})
            options = json.loads(q.execution('changed', claim['claim'])['options'])
            self.assertEqual((options.get('posture'), options.get('brief_kind')), ('record-layer', 'business'))
            work_runner.worker(q, 'changed', claim['claim'], self.reg.path, self.results)
            item = q.get('changed')
            self.assertEqual((item['stage'], execute.call_count,
                              'brief_desk_mismatch' in json.dumps(item)), ('failed', 0, True))

    def test_worker_rechecks_the_ledger_after_business_enqueue(self):
        ledger_path = self.root / 'spend.json'
        with patch.object(dispatch.claude_api_wire.config, 'LEDGER_PATH', ledger_path), patch.object(
                dispatch.claude_api_wire.Ledger, 'read', self.claude_ledger_read):
            ledger = dispatch.claude_api_wire.Ledger()
            for card, spent, stop, reason in (
                    ('spent-after-enqueue', '200', None, 'provider budget spent or reserved'),
                    ('stopped-after-enqueue', '0', 'billing_error', 'billing_error')):
                with self.subTest(card=card):
                    ledger_path.write_text(json.dumps({'months': {}}))
                    result = self.send(['send', 'business', 'Read the record.', '--brief', 'business',
                                        '--card-id', card])
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(self.route(card)['chosen'], 'biz-desk')
                    with ledger.locked() as data:
                        data['months'][dispatch.claude_api_wire.month_now()] = {
                            'spent_usd': spent, 'reservations': {}}
                        data['stop'] = stop
                    with WorkQueue(self.db) as q, patch.object(dispatch, '_execute', return_value={
                            'status': 'failed', 'detail': 'executor reached', 'termination_confirmed': True}) as execute:
                        claim = q.claim(eligible_desks={'biz-desk'}, actor='runner', owner={})
                        work_runner.worker(q, card, claim['claim'], self.reg.path, self.results)
                        item = q.get(card)
                        self.assertEqual(item['stage'], 'failed')
                        self.assertIn('business_desk_unavailable', json.dumps(item))
                        self.assertIn(reason, json.dumps(item))
                        execute.assert_not_called()

    def test_codex_isolation_refusal_is_preserved_on_the_business_routing_receipt(self):
        self.reg.register('codex', 'codex-session', posture='record-layer')
        result = self.send(['send', 'business', 'Draft for someone@example.test.',
                            '--brief', 'business', '--card-id', 'codex-isolation'])
        self.assertEqual(result.returncode, 0, result.stderr)
        route = self.route('codex-isolation')
        self.assertEqual(route['chosen'], 'biz-desk')
        refusal = next(row['reason'] for row in route['skips'] if row['desk'] == 'codex')
        self.assertIn('record_layer_unsupported', refusal)
        self.assertIn('CARR-only tool allowlist', refusal)
        self.assertIn('apply_patch', refusal)

    def test_queued_codex_business_job_fails_closed_without_launch_or_retry(self):
        result = self.send(['send', 'business', 'Draft for someone@example.test.',
                            '--brief', 'business', '--card-id', 'codex-worker'])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.reg.register('biz-desk', 'codex-session', posture='record-layer')
        with WorkQueue(self.db) as q, patch.object(dispatch.subprocess, 'run') as run, \
                patch.object(dispatch.subprocess, 'Popen') as popen:
            claim = q.claim(eligible_desks={'biz-desk'}, actor='runner', owner={})
            work_runner.worker(q, 'codex-worker', claim['claim'], self.reg.path, self.results)
            item = q.get('codex-worker')
            self.assertEqual(item['stage'], 'failed')
            self.assertIn('record_layer_unsupported', json.dumps(item))
            self.assertIn('apply_patch', json.dumps(item))
            self.assertIsNone(q.claim(eligible_desks={'biz-desk'}, actor='runner', owner={}))
            run.assert_not_called()
            popen.assert_not_called()

    def test_business_footer_passes_the_phrasing_lint(self):
        self.assertEqual(dispatch._phrasing_hint((HERE / 'business-brief-footer.md').read_text()), '')

    def test_business_footer_uses_the_partner_role(self):
        self.assertIn('the partner', (HERE / 'business-brief-footer.md').read_text())

    def test_dispatch_business_instructions_use_the_partner_role(self):
        self.assertIn('Business work for the partner', dispatch.__doc__)

    def test_record_layer_posture_is_stored_for_every_business_runtime(self):
        for kind in ('codex-session', 'grok-cli', 'hermes-nous', 'flash-local', 'claude-desktop'):
            with self.subTest(kind=kind):
                options = {'model': 'claude-sonnet-5-5', 'effort': 'medium'} if kind == 'claude-desktop' else {}
                entry = self.reg.register('record-' + kind, kind, posture='record-layer', **options)
                self.assertEqual(entry['posture'], 'record-layer')
                self.assertEqual(self.reg.resolve('record-' + kind)['posture'], 'record-layer')
        with self.assertRaises(desks.DeskError):
            self.reg.register('nope', 'claude-api', posture='record-layer')

    def add_business_desks(self):
        for name, kind in [('nous', 'hermes-nous'), ('grok', 'grok-cli'), ('codex', 'codex-session'), ('flash', 'flash-local')]:
            self.reg.register(name, kind, posture='record-layer')

    def route(self, card):
        with WorkQueue(self.db) as q:
            return json.loads(q.get(card)['description'].split('Business routing: ')[1])

    def test_business_fallback_order_and_skips_are_on_the_card(self):
        self.add_business_desks()
        order = ['nous', 'grok', 'codex', 'biz-desk']
        for index, expected in enumerate(order):
            blocked = set(order[:index])
            with patch.dict(dispatch.record_layer.REFUSALS, {}, clear=True), patch.object(
                    dispatch.seat_gate, 'check', side_effect=lambda e: ('provider_capped', 'provider capped')
                    if e['name'] in blocked else None):
                result = self.send(['send', 'business', 'Read the record.', '--brief', 'business',
                                    '--card-id', f'order-{index}'])
            self.assertEqual(result.returncode, 0, result.stderr)
            route = self.route(f'order-{index}')
            self.assertEqual(route['chosen'], expected)
            self.assertEqual(route['skips'], [{'desk': name, 'reason': 'provider_capped: provider capped'}
                                             for name in order[:index]])

    def test_flash_is_excluded_by_default_and_requires_both_switches(self):
        self.add_business_desks()
        for flag, off, expected in [('0', False, 'codex'), ('1', True, 'codex'), ('1', False, 'codex')]:
            card = f'flash-{flag}-{off}'
            with patch.dict(os.environ, {'CARR_BUSINESS_FLASH_ENABLED': flag}), patch.dict(
                    dispatch.record_layer.REFUSALS, {}, clear=True), patch.object(
                    dispatch.business_routing.flashlib, 'is_switched_off', return_value=off), patch.object(
                    dispatch.seat_gate, 'check', side_effect=lambda e: ('provider_capped', 'cap')
                    if e['name'] in ('nous', 'grok') else None):
                result = self.send(['send', 'business', 'Read.', '--brief', 'business', '--card-id', card])
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(self.route(card)['chosen'], expected)

    def test_enabled_flash_is_last_after_every_other_business_desk(self):
        self.add_business_desks()
        order = ['nous', 'grok', 'codex', 'biz-desk', 'flash']
        for index, expected in enumerate(order):
            blocked = set(order[:index])
            with patch.dict(os.environ, {'CARR_BUSINESS_FLASH_ENABLED': '1'}), patch.dict(
                    dispatch.record_layer.REFUSALS, {}, clear=True), patch.object(
                    dispatch.business_routing.flashlib, 'is_switched_off', return_value=False), patch.object(
                    dispatch.seat_gate, 'check', side_effect=lambda e: ('provider_capped', 'cap')
                    if e['name'] in blocked else None):
                card = f'enabled-order-{index}'
                result = self.send(['send', 'business', 'Read.', '--brief', 'business', '--card-id', card])
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(self.route(card), {'chosen': expected, 'skips': [
                {'desk': name, 'reason': 'provider_capped: cap'} for name in order[:index]]})

    def test_nous_budget_stop_is_recorded_and_claude_is_selected(self):
        self.reg.register('nous', 'hermes-nous', posture='record-layer')
        with patch.object(dispatch.nous_budget.Ledger, 'read', return_value={
                'spent_usd': '0', 'reserved_usd': '0', 'budget_usd': '22', 'stop': 'nous credits exhausted'}):
            result = self.send(['send', 'business', 'Read.', '--brief', 'business', '--card-id', 'cap'])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.route('cap')['chosen'], 'biz-desk')
        self.assertIn({'desk': 'nous', 'reason': 'nous credits exhausted'}, self.route('cap')['skips'])

    def test_every_desk_unavailable_returns_the_named_refusal_and_keeps_evidence(self):
        self.add_business_desks()
        with patch.object(dispatch.seat_gate, 'check', return_value=('provider_capped', 'cap')):
            receipt = business_dispatch.Run(run=self.in_process).enqueue('party:7', 'draft_reply', 'Draft.', 'mail:1')
        self.assertEqual((receipt.status, receipt.detail), ('refused', 'no_record_layer_desk'))
        self.assertEqual(self.route(receipt.card_id), {'chosen': None, 'skips': [
            {'desk': name, 'reason': 'provider_capped: cap'} for name in ('nous', 'grok', 'codex', 'biz-desk')]})
        with WorkQueue(self.db) as q:
            self.assertEqual(q.get(receipt.card_id)['stage'], 'failed')

    def test_budget_reservations_sticky_stop_and_provider_limit_state(self):
        self.add_business_desks()
        states = [
            ('nous-reserved', 'hermes-nous', {'spent_usd': '20', 'reserved_usd': '2', 'budget_usd': '22', 'stop': None}, 'provider budget spent or reserved'),
            ('claude-stopped', 'claude-desktop', {'spent_usd': '0', 'reserved_usd': '0', 'budget_usd': '200', 'stop': 'billing_error'}, 'billing_error')]
        for card, kind, state, reason in states:
            target = dispatch.nous_budget.Ledger if kind == 'hermes-nous' else dispatch.claude_api_wire.Ledger
            with patch.object(target, 'read', return_value=state), patch.object(
                    dispatch.seat_gate, 'check', side_effect=lambda e: ('cap', 'cap')
                    if kind == 'claude-desktop' and e['name'] == 'nous' else None):
                result = self.send(['send', 'business', 'Read.', '--brief', 'business', '--card-id', card])
            route = self.route(card)
            if kind == 'hermes-nous':
                self.assertEqual(route['chosen'], 'biz-desk')
            else:
                self.assertEqual((result.returncode, route['chosen']), (2, None))
            self.assertIn({'desk': 'nous' if kind == 'hermes-nous' else 'biz-desk', 'reason': reason}, route['skips'])
        self.results.write_text(json.dumps({'kind': 'hermes-nous', 'status': 'quota_exhausted'}) + '\n')
        result = self.send(['send', 'business', 'Read.', '--brief', 'business', '--card-id', 'limited'])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn({'desk': 'nous', 'reason': 'quota_exhausted'}, self.route('limited')['skips'])

    def test_nous_accepts_exact_remaining_reservation_and_codex_limits_cover_live_kind(self):
        self.reg.register('nous', 'hermes-nous', posture='record-layer')
        with patch.dict(dispatch.record_layer.REFUSALS, {}, clear=True), patch.object(dispatch.nous_budget.Ledger, 'read', return_value={
                'spent_usd': '20', 'reserved_usd': '1', 'budget_usd': '22', 'stop': None}):
            result = self.send(['send', 'business', 'Read.', '--brief', 'business', '--card-id', 'exact'])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.route('exact')['chosen'], 'nous')
        self.reg.forget('nous')
        self.reg.register('codex', 'codex-session', posture='record-layer')
        self.results.write_text(json.dumps({'kind': 'codex-live', 'status': 'quota_exhausted'}) + '\n')
        with patch.dict(dispatch.record_layer.REFUSALS, {}, clear=True):
            result = self.send(['send', 'business', 'Read.', '--brief', 'business', '--card-id', 'codex-limit'])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.route('codex-limit')['chosen'], 'biz-desk')
        self.assertIn({'desk': 'codex', 'reason': 'quota_exhausted'}, self.route('codex-limit')['skips'])

    def test_card_id_is_deterministic_per_action_record_and_source(self):
        one = business_dispatch.card_id('draft_reply', 'party:7', 'mail:1')
        self.assertEqual(one, business_dispatch.card_id('draft_reply', 'party:7', 'mail:1'))
        self.assertNotEqual(one, business_dispatch.card_id('draft_reply', 'party:7', 'mail:2'))
        self.assertNotEqual(one, business_dispatch.card_id('pull_comps', 'party:7', 'mail:1'))

    def test_delimiters_in_record_and_source_do_not_collide(self):
        self.assertNotEqual(
            business_dispatch.card_id('draft_reply', 'party:7|mail:1', 'mail:2'),
            business_dispatch.card_id('draft_reply', 'party:7', 'mail:1|mail:2'))

    def test_duplicate_is_refused_cleanly_and_does_not_use_the_cap(self):
        run = business_dispatch.Run(cap=1, run=self.in_process)
        first = run.enqueue('party:7', 'draft_reply', 'Reply to the latest email.', 'mail:1')
        again = business_dispatch.Run(cap=1, run=self.in_process).enqueue(
            'party:7', 'draft_reply', 'Reply to the latest email.', 'mail:1')
        self.assertEqual((first.status, again.status), ('queued', 'duplicate'))
        self.assertEqual(first.card_id, again.card_id)
        with WorkQueue(self.db) as q:
            self.assertEqual([i['card_id'] for i in q.export()['items']], [first.card_id])

    def test_cap_holds_per_run(self):
        calls = []
        def ok(argv, **kwargs):
            calls.append(argv)
            return subprocess.CompletedProcess(argv, 0, '{}', '')
        run = business_dispatch.Run(cap=3, run=ok)
        receipts = [run.enqueue(f'party:{n}', 'research_contact', 'Research this contact.', 'mail:1')
                    for n in range(5)]
        self.assertEqual([r.status for r in receipts], ['queued'] * 3 + ['capped'] * 2)
        self.assertEqual(len(calls), 3)

    def test_overlapping_calls_reserve_capacity_atomically(self):
        start = threading.Barrier(12)
        release = threading.Event()
        def ok(argv, **kwargs):
            release.wait(1)
            return subprocess.CompletedProcess(argv, 0, '{}', '')
        run = business_dispatch.Run(cap=3, run=ok)
        def enqueue(n):
            start.wait(2)
            return run.enqueue(f'party:{n}', 'draft_reply', 'Draft.', 'mail:1').status
        with ThreadPoolExecutor(max_workers=12) as pool:
            futures = [pool.submit(enqueue, n) for n in range(12)]
            statuses = [future.result(10) for future in futures]
        self.assertEqual(sorted(statuses), ['capped'] * 9 + ['queued'] * 3)

    def test_failures_return_refusals_instead_of_raising(self):
        def broken(argv, **kwargs):
            raise OSError('no python')
        run = business_dispatch.Run(run=broken)
        self.assertEqual(run.enqueue('party:7', 'draft_reply', 'Reply.', 'mail:1').status, 'refused')
        self.assertEqual(run.enqueue('party:7', 'send_wire', 'Reply.', 'mail:1').status, 'refused')
        self.assertEqual(run.enqueue('', 'draft_reply', 'Reply.', 'mail:1').status, 'refused')
        unregistered = business_dispatch.Run(desk='missing', run=self.in_process)
        self.assertEqual(unregistered.enqueue('party:7', 'draft_reply', 'Reply.', 'mail:1').status, 'refused')

    def test_invalid_inputs_are_refused_before_subprocess(self):
        defaults = dict(record_ref='party:7', action='draft_reply', instructions='Draft.', source_id='mail:1')
        for field, limit in [('record_ref', 1000), ('action', 64), ('instructions', 32000),
                             ('source_id', 1000), ('desk', 128), ('actor', 128)]:
            for value in [None, [], '', 'x\x00y', 'x\x1fy', 'x\x7fy', 'x\x85y', '\ud800', 'x' * (limit + 1)]:
                with self.subTest(field=field, value=repr(value)[:30]):
                    invoke = Mock(return_value=subprocess.CompletedProcess([], 0, '', ''))
                    args, options = dict(defaults), {'run': invoke}
                    (options if field in ('desk', 'actor') else args)[field] = value
                    receipt = business_dispatch.Run(**options).enqueue(**args)
                    self.assertEqual((receipt.status, invoke.call_count), ('refused', 0))

    def test_subprocess_value_error_is_a_refusal(self):
        run = business_dispatch.Run(run=Mock(side_effect=ValueError('embedded null byte')))
        self.assertEqual(run.enqueue('party:7', 'draft_reply', 'Draft.', 'mail:1').status, 'refused')

    def test_invalid_caps_are_refused(self):
        for cap in [None, '3', -1, 1.5, True]:
            with self.subTest(cap=cap):
                run = business_dispatch.Run(cap=cap)
                self.assertEqual(run.enqueue('party:7', 'draft_reply', 'Draft.', 'mail:1').status, 'refused')

    def test_record_layer_refuses_an_untrusted_carr_endpoint_before_launch(self):
        for url in ('https://other.example.test/mcp',
                    'https://api.doctorcre.com.example.test/mcp',
                    'http://api.doctorcre.com/mcp',
                    'https://api.doctorcre.com:8443/mcp',
                    'https://api.doctorcre.com/other'):
            with self.subTest(url=url), patch.dict(os.environ, {'CARR_MCP_URL': url}):
                launch = Mock()
                with self.assertRaises(claude_desktop_wire.ClaudeDesktopError) as refused:
                    claude_desktop_wire.launch_background(
                        self.reg.resolve('biz-desk'), 'Read the record.', run=launch)
                self.assertEqual(refused.exception.code, 'untrusted_carr_endpoint')
                self.assertIn('configured CARR endpoint', str(refused.exception))
                launch.assert_not_called()

    def test_inherited_untrusted_endpoint_is_refused_in_a_fresh_process(self):
        code = """
import claude_desktop_wire as wire
from unittest.mock import Mock
launch = Mock()
try:
    wire.launch_background({'model': 'sonnet', 'effort': 'medium', 'cwd': '/tmp',
                            'posture': 'record-layer'}, 'Read.', run=launch)
except wire.ClaudeDesktopError as exc:
    assert exc.code == 'untrusted_carr_endpoint', exc.code
else:
    raise AssertionError('untrusted endpoint accepted')
launch.assert_not_called()
"""
        child = subprocess.run([sys.executable, '-c', code], cwd=HERE,
                               env={**os.environ, 'CARR_MCP_URL': 'https://other.example.test/mcp'},
                               capture_output=True, text=True)
        self.assertEqual(child.returncode, 0, child.stderr)

    def test_end_to_end_the_desk_receives_the_business_brief_with_only_carr_tools(self):
        bin_dir = self.root / 'bin'
        bin_dir.mkdir()
        fake = bin_dir / 'claude'
        fake.write_text(FAKE_CLAUDE)
        fake.chmod(0o755)
        log = self.root / 'claude-argv.jsonl'
        receipt = business_dispatch.Run(run=self.in_process).enqueue(
            'party:7', 'draft_reply', 'Reply to the email from someone@example.test.', 'mail:1')
        self.assertEqual(receipt.status, 'queued')
        with WorkQueue(self.db) as q, patch.dict(os.environ, {
                'PATH': f'{bin_dir}{os.pathsep}{os.environ["PATH"]}', 'FAKE_CLAUDE_LOG': str(log)}):
            claim = q.claim(eligible_desks={'biz-desk'}, actor='runner', owner={})
            work_runner.worker(q, receipt.card_id, claim['claim'], self.reg.path, self.results)
        argv = json.loads(log.read_text().splitlines()[0])
        prompt = argv[-1]
        self.assertTrue(prompt.startswith(desks.DESK_INSTRUCTION))
        self.assertIn('Draft reply for record party:7 (source mail:1).', prompt)
        for block in blocks('business-brief-footer.md'):
            self.assertIn(block, prompt)
        for block in blocks('brief-footer.md'):
            self.assertNotIn(block, prompt)
        posture = argv[argv.index('--effort') + 2:argv.index('--permission-mode')]
        self.assertEqual(posture, ['--bare', '--setting-sources', '', '--settings',
            '{"disableAllHooks": true, "enabledPlugins": {}}', '--disable-slash-commands',
            '--tools', '', '--strict-mcp-config', '--mcp-config',
            json.dumps({'mcpServers': {'carr': {'type': 'http', 'url': claude_desktop_wire.CARR_MCP_URL}}}),
            '--allowedTools', 'mcp__carr'])
        self.assertEqual(posture[posture.index('--tools') + 1], '')
        servers = json.loads(posture[posture.index('--mcp-config') + 1])['mcpServers']
        self.assertEqual(list(servers), ['carr'])
        self.assertEqual(posture[-2:], ['--allowedTools', 'mcp__carr'])
        self.assertEqual(argv[argv.index('--permission-mode') + 1], 'dontAsk')
        self.assertEqual(argv[argv.index('--model') + 1], 'claude-sonnet-5-5')


if __name__ == '__main__':
    unittest.main()
