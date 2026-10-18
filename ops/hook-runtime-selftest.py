#!/usr/bin/env python3
"""Characterize hook events through the runtime decision interface."""
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib.hook_runtime import Event, Verdict, decision, run

class RuntimeTests(unittest.TestCase):
    def test_envelopes_preserve_hook_registers(self):
        self.assertEqual(Verdict.block('fixture').stdout,
                         '{"decision": "block", "reason": "fixture"}\n')
        self.assertEqual(Verdict.refuse('fixture'), Verdict(2, '', 'fixture\n'))
        self.assertEqual(Verdict.announce('fixture').stdout,
                         '{"hookSpecificOutput": {"hookEventName": "Stop", "additionalContext": "fixture"}}\n')

    def test_decision_preserves_ordered_output_and_exit(self):
        @decision
        def decide(event):
            print(json.dumps({'systemMessage': event['prompt']}))
            print('refused', file=sys.stderr)
            raise SystemExit(2)
        result = decide(Event({'prompt': 'fixture'}))
        self.assertIsInstance(result, Verdict)
        self.assertEqual(result.stdout, '{"systemMessage": "fixture"}\n')
        self.assertEqual(result.stderr, 'refused\n')
        self.assertEqual(result.code, 2)
        self.assertEqual(result.decision, 'deny')

    def test_parse_failure_obeys_declared_policy(self):
        def decide(event):
            self.fail('invalid stdin must not call the gate')
        out, err = io.StringIO(), io.StringIO()
        self.assertEqual(run(decide, stdin=io.StringIO('{'), stdout=out, stderr=err), 0)
        self.assertEqual((out.getvalue(), err.getvalue()), ('', ''))
        self.assertEqual(run(decide, stdin=io.StringIO('{'), stdout=out, stderr=err,
                             parse_error=lambda exc: Verdict(code=2, stderr='invalid\n')), 2)
        self.assertEqual(err.getvalue(), 'invalid\n')

    def test_transcript_keeps_good_objects_past_bad_lines(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'turn.jsonl'
            path.write_text('{"type":"user"}\ninvalid\n42\n{"type":"assistant"}\n')
            event = Event({'transcript_path': str(path), 'session_id':'selftest'})
            self.assertEqual(event.transcript(hook='fixture'),
                             [{'type':'user'}, {'type':'assistant'}])

    def test_gate_error_policy_is_applied_once(self):
        @decision(on_error=lambda exc: Verdict(stdout='degraded\n'))
        def decide(event):
            print('before')
            raise ValueError('fixture')
        result = decide(Event({}))
        self.assertEqual((result.code,result.stdout), (0,'before\ndegraded\n'))


if __name__ == '__main__':
    unittest.main()
