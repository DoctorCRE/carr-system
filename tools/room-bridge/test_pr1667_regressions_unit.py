"""Review reproductions for ownership, registry updates and Codex turn receipts."""
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import codex_checkout
import codex_wire
import desks
import dispatch
import write_ownership as ownership
from ops.git_env import fixture_env
import tempfile


class ReviewRegressions(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_registry_migration_cannot_overwrite_thread_update(self):
        path = self.root / 'legacy.json'
        path.write_text(json.dumps({'desks': {'sol': {'kind': 'codex-session',
            'model': 'gpt-5.1-codex-mini', 'thread_id': 'old'}}}))
        reader, writer = desks.Registry(path), desks.Registry(path)
        paused, resume = threading.Event(), threading.Event()
        save = reader._save
        errors = []
        def paused_save(data):
            paused.set()
            if not resume.wait(3):
                raise AssertionError('migration not resumed')
            save(data)
        def read():
            try:
                reader.entries()
            except Exception as exc:
                errors.append(exc)
        with patch.object(reader, '_save', side_effect=paused_save):
            first = threading.Thread(target=read)
            first.start()
            self.assertTrue(paused.wait(3))
            second = threading.Thread(target=writer.remember_thread, args=('sol', 'new-thread'))
            second.start()
            second.join(.15)
            resume.set()
            first.join(3)
            second.join(3)
        self.assertFalse(errors)
        self.assertEqual(writer.entries()['sol']['thread_id'], 'new-thread')


class TurnRegressions(unittest.TestCase):
    wire_run_turn = staticmethod(codex_wire.run_turn)
    def run_turn(self, messages, on_executor=None):
        class Wire:
            def __init__(self, *args, **kwargs):
                self.sock = type('Socket', (), {'close': lambda self: None})()
            thread_id = None
            turn_id = None
            turn_requested = False
            deadline = None
            def send_json(self, value):
                pass
            def receive_json(self):
                value = next(stream)
                if isinstance(value, Exception):
                    raise value
                return value
        stream = iter(messages)
        run = self.wire_run_turn
        with patch.object(codex_wire, '_initialize'), patch.object(codex_wire, 'Wire', Wire):
            return run('/fixture.sock', 'build', thread_id=None, cwd=None,
                model='fixture', sandbox='workspace-write', approval_policy='never',
                timeout=1, on_executor=on_executor)

    def test_rejection_setup_failure_and_lost_start_have_distinct_ownership(self):
        opened = {'id': 'thread-open', 'result': {'thread': {'id': 'thread'}}}
        for name, messages, no_launch in (
            ('rejected', [opened, {'id': 'turn-start', 'error': {'code': -32602, 'message': 'invalid parameters'}}], True),
            ('setup', [EOFError('setup lost')], True),
            ('lost-start', [opened, EOFError('start response lost')], False)):
            identities = []
            with self.subTest(name=name):
                with self.assertRaises((RuntimeError, EOFError)):
                    self.run_turn(messages, identities.append)
                self.assertTrue(identities, 'ownership needs a setup/no-launch receipt')
                self.assertEqual(identities[-1]['kind'] == 'no_launch', no_launch)

    def test_answers_match_exact_thread_and_turn_before_and_after_start_response(self):
        def answer(thread, turn, text):
            return {'method': 'item/completed', 'params': {'threadId': thread,
                'turnId': turn, 'item': {'type': 'agentMessage', 'text': text}}}
        start = {'id': 'turn-start', 'result': {'turn': {'id': 'current'}}}
        opened = {'id': 'thread-open', 'result': {'thread': {'id': 'thread'}}}
        end = {'method': 'turn/completed', 'params': {'threadId': 'thread',
               'turn': {'id': 'current', 'status': 'completed'}}}
        for current in (False, True):
            for early in (False, True):
                with self.subTest(current=current, early=early):
                    notifications = [answer('thread', 'old', 'stale'), answer('other', 'current', 'foreign')]
                    if current:
                        notifications.append(answer('thread', 'current', 'current answer'))
                    messages = [opened, *notifications, start, end] if early else [opened, start, *notifications, end]
                    row = self.run_turn(messages)
                    self.assertEqual(row['result'], 'current answer' if current else '')


if __name__ == '__main__':
    unittest.main()
