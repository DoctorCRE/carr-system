#!/usr/bin/env python3
"""Weekly output verification through the command interface and synthetic HTTP."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


class SocialBatchTests(unittest.TestCase):
    def run_check(self, pages):
        spec = importlib.util.spec_from_file_location('social_batch_check', ROOT / 'tools/social-batch-check.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        calls = []
        def respond(request, **kwargs):
            calls.append((request.get_method(), request.full_url))
            response = pages[len(calls) - 1]
            if isinstance(response, Exception):
                raise response
            return contextlib.closing(io.BytesIO(json.dumps(response).encode()))
        output = io.StringIO()
        with patch.object(module, 'urlopen', side_effect=respond), \
             patch.dict(module.os.environ, {'BLOTATO_API_KEY': 'synthetic-only'}, clear=True), \
             contextlib.redirect_stdout(output):
            status = module.main(['--friday', '2026-10-30'])
        return status, json.loads(output.getvalue()), calls

    def test_empty_output_fails_instead_of_trusting_scheduler_success(self):
        status, result, calls = self.run_check([{'items': [], 'count': '0'}])
        self.assertEqual(status, 1)
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['reason'], 'no_posts_next_week')
        self.assertEqual(result['posts'], [])
        self.assertEqual(calls, [('GET', 'https://backend.blotato.com/v2/schedules?limit=50')])


if __name__ == '__main__':
    unittest.main()
