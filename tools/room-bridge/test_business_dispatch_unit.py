"""Business work reaches only the record-layer desk, with the business brief."""
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

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
        for p in (patch.dict(os.environ, {'CARR_WORK_QUEUE_DB': str(self.db)}),
                  patch.object(work_runner, 'kick'),
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

    def test_business_footer_passes_the_phrasing_lint(self):
        self.assertEqual(dispatch._phrasing_hint((HERE / 'business-brief-footer.md').read_text()), '')

    def test_record_layer_posture_belongs_to_claude_desktop_only(self):
        with self.assertRaises(desks.DeskError) as caught:
            self.reg.register('nope', 'grok-cli', posture='record-layer')
        self.assertEqual(caught.exception.code, 'bad_posture')

    def test_card_id_is_deterministic_per_action_record_and_source(self):
        one = business_dispatch.card_id('draft_reply', 'party:7', 'mail:1')
        self.assertEqual(one, business_dispatch.card_id('draft_reply', 'party:7', 'mail:1'))
        self.assertNotEqual(one, business_dispatch.card_id('draft_reply', 'party:7', 'mail:2'))
        self.assertNotEqual(one, business_dispatch.card_id('pull_comps', 'party:7', 'mail:1'))

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

    def test_failures_return_refusals_instead_of_raising(self):
        def broken(argv, **kwargs):
            raise OSError('no python')
        run = business_dispatch.Run(run=broken)
        self.assertEqual(run.enqueue('party:7', 'draft_reply', 'Reply.', 'mail:1').status, 'refused')
        self.assertEqual(run.enqueue('party:7', 'send_wire', 'Reply.', 'mail:1').status, 'refused')
        self.assertEqual(run.enqueue('', 'draft_reply', 'Reply.', 'mail:1').status, 'refused')
        unregistered = business_dispatch.Run(desk='missing', run=self.in_process)
        self.assertEqual(unregistered.enqueue('party:7', 'draft_reply', 'Reply.', 'mail:1').status, 'refused')

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
        posture = argv[argv.index('--tools'):argv.index('--permission-mode')]
        self.assertEqual(posture, claude_desktop_wire.TOOL_POSTURES['record-layer'])
        self.assertEqual(posture[1], '')
        servers = json.loads(posture[posture.index('--mcp-config') + 1])['mcpServers']
        self.assertEqual(list(servers), ['carr'])
        self.assertEqual(posture[-2:], ['--allowedTools', 'mcp__carr'])
        self.assertEqual(argv[argv.index('--permission-mode') + 1], 'dontAsk')
        self.assertEqual(argv[argv.index('--model') + 1], 'claude-sonnet-5-5')


if __name__ == '__main__':
    unittest.main()
