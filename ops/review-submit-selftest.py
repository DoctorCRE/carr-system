"""Tests for native App review submission."""
import importlib.util
import os
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('submit', Path(__file__).with_name('review-submit.py'))
assert spec is not None and spec.loader is not None
submit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(submit)

class Submit(unittest.TestCase):
    def test_review_binds_reviewed_head_and_app_token(self):
        with patch.object(submit, 'installation_token', return_value='secret'), patch.object(submit, 'request', side_effect=[{'head': {'sha': 'abc'}}, {'id': 1}, {}]) as request:
            submit.submit(12, 'approve', 'looks good', 'abc')
            self.assertEqual(request.call_args_list[1].args[2], {'event': 'APPROVE', 'body': 'looks good', 'commit_id': 'abc'})
            self.assertEqual(request.call_args_list[1].args[1], 'secret')

    def test_moved_head_never_posts(self):
        with patch.object(submit, 'installation_token', return_value='secret'), patch.object(submit, 'request', return_value={'head': {'sha': 'new'}}) as request:
            with self.assertRaises(ValueError): submit.submit(12, 'approve', 'body', 'old')
            self.assertEqual(request.call_count, 2)

    def test_token_exchange_uses_installation(self):
        with patch.object(submit, "private_key", return_value=b"test"), patch.object(submit.jwt, "encode", return_value="jwt"), patch.object(submit, "request", side_effect=[{"id": 42}, {"token": "app-token"}]) as request:
            self.assertEqual(submit.installation_token(), "app-token")
            self.assertEqual(request.call_args_list[1].args, ("/app/installations/42/access_tokens", "jwt", {"repositories": ["carr-system"], "permissions": {"pull_requests": "write", "metadata": "read"}}))

    def test_exact_key_mode(self):
        for mode in (0o400, 0o700, 0o1600):
            with self.subTest(mode=mode), patch.object(submit.Path, 'open'), patch.object(submit.os, 'fstat') as fstat:
                fstat.return_value.st_mode = mode
                with self.assertRaises(ValueError): submit.private_key()

    def test_comment_and_revoke(self):
        with patch.object(submit, 'installation_token', return_value='secret'), patch.object(submit, 'request', side_effect=[{'head': {'sha': 'abc'}}, {'id': 7}, {}]) as request:
            self.assertEqual(submit.submit(12, 'comment', 'App submission smoke test', 'abc'), 7)
            self.assertEqual(request.call_args_list[1].args[2]['event'], 'COMMENT')
            self.assertEqual(request.call_args.kwargs, {'method': 'DELETE'})
            self.assertEqual(request.call_args.args[0], '/installation/token')

    def test_revoke_after_submission_failure(self):
        with patch.object(submit, 'installation_token', return_value='secret'), patch.object(submit, 'request', side_effect=[{'head': {'sha': 'abc'}}, ValueError('failed'), {}]) as request:
            with self.assertRaises(ValueError): submit.submit(12, 'approve', 'body', 'abc')
            self.assertEqual(request.call_args.kwargs, {'method': 'DELETE'})

    def test_environment_key_does_not_override_missing_file(self):
        with tempfile.TemporaryDirectory() as home, patch.dict(os.environ, {"CARR_GITHUB_APP_PRIVATE_KEY": "ignored"}), patch.object(submit.Path, "home", return_value=Path(home)):
            with self.assertRaises(ValueError): submit.private_key()

    def test_missing_key_fails_closed(self):
        with patch.object(submit, 'private_key', side_effect=ValueError('missing')):
            with self.assertRaises(ValueError): submit.installation_token()

if __name__ == '__main__': unittest.main()
