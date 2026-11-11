#!/usr/bin/env python3
"""Commit-bound CI groups and stable deployment serialization contracts."""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = ['ci.yml', 'doc-drift.yml']
LOCKS = {'deploy.yml': ['concurrency:\n  group: production\n  cancel-in-progress: false\n',
                '    concurrency:\n'
                '      group: production-pr-comments\n'
                '      cancel-in-progress: false\n'],
 'main-canary.yml': ['concurrency:\n  group: main-canary\n  cancel-in-progress: false\n'],
 'backup-nightly.yml': ['concurrency:\n'
                        '  group: carr-nightly-backup-cloud\n'
                        '  cancel-in-progress: false\n'],
 'edge-liveness.yml': ['concurrency:\n'
                       '  group: carr-edge-liveness\n'
                       '  cancel-in-progress: false\n']}


def concurrency_blocks(source):
    return [m.group(0) for m in re.finditer(
        r"^([ \t]*)concurrency:\n(?:\1[ \t]+[^\n]*\n)+", source, re.M)]


def group(source):
    block = concurrency_blocks(source)[0]
    return re.search(r"group: (.+)", block)[1]


def render(template, context):
    def expression(match):
        for path in match[1].split("||"):
            value = context.get(path.strip(), "")
            if value:
                return str(value)
        return ""
    return re.sub(r"\$\{\{(.*?)\}\}", expression, template)


class WorkflowConcurrencyTest(unittest.TestCase):
    def test_distinct_heads_and_events_are_isolated_same_head_can_cancel(self):
        for filename in WORKFLOWS:
            with self.subTest(workflow=filename):
                template = group((ROOT / ".github/workflows" / filename).read_text())
                context = {"github.workflow": "CI", "github.event_name": "pull_request",
                           "github.event.pull_request.number": 7, "github.ref": "refs/pull/7/merge",
                           "github.event.pull_request.head.sha": "a" * 40, "github.sha": "merge-a"}
                key = render(template, context)
                self.assertNotEqual(key, render(template, {**context, "github.event.pull_request.head.sha": "b" * 40}))
                self.assertEqual(key, render(template, {**context, "github.sha": "different-merge", "github.run_id": 2}))
                for field, value in [("github.workflow", "Other"), ("github.event.pull_request.number", 8)]:
                    self.assertNotEqual(key, render(template, {**context, field: value}))
                keys = [key]
                for event in ["push", "merge_group"]:
                    event_context = {**context, "github.event_name": event, "github.event.pull_request.number": "",
                                     "github.event.pull_request.head.sha": "", "github.ref": "refs/heads/main"}
                    event_key = render(template, event_context)
                    keys.append(event_key)
                    self.assertNotEqual(event_key, render(template, {**event_context, "github.sha": "merge-b"}))
                    self.assertNotEqual(event_key, render(template, {**event_context, "github.ref": "refs/heads/other"}))
                self.assertEqual(3, len(set(keys)))

    def test_serialization_locks_are_unchanged(self):
        for filename, expected in LOCKS.items():
            self.assertEqual(expected, concurrency_blocks((ROOT / ".github/workflows" / filename).read_text()), filename)


if __name__ == "__main__":
    unittest.main()
