"""Tests for native App review submission."""
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('submit', Path(__file__).with_name('review-submit.py'))
submit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(submit)

class Submit(unittest.TestCase):
    def test_review_binds_reviewed_head_and_app_token(self):
        with patch.object(submit, 'installation_token', return_value='secret'), patch.object(submit, 'request', side_effect=[{'head': {'sha': 'abc'}}, {'id': 1}]) as request:
            submit.submit(12, 'approve', 'looks good', 'abc')
            self.assertEqual(request.call_args_list[1].args[2], {'event': 'APPROVE', 'body': 'looks good', 'commit_id': 'abc'})
            self.assertEqual(request.call_args_list[1].args[1], 'secret')

    def test_moved_head_never_posts(self):
        with patch.object(submit, 'installation_token', return_value='secret'), patch.object(submit, 'request', return_value={'head': {'sha': 'new'}}) as request:
            with self.assertRaises(ValueError): submit.submit(12, 'approve', 'body', 'old')
            self.assertEqual(request.call_count, 1)

    def test_token_exchange_uses_installation(self):
        with patch.object(submit, "private_key", return_value=b"test"), patch.object(submit.jwt, "encode", return_value="jwt"), patch.object(submit, "request", side_effect=[{"id": 42}, {"token": "app-token"}]) as request:
            self.assertEqual(submit.installation_token(), "app-token")
            self.assertEqual(request.call_args_list[1].args, ("/app/installations/42/access_tokens", "jwt", {}))

    def test_missing_key_fails_closed(self):
        with patch.object(submit, 'private_key', side_effect=ValueError('missing')):
            with self.assertRaises(ValueError): submit.installation_token()

if __name__ == '__main__': unittest.main()
