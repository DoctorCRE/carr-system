"""Open PR migration ownership through the GitHub read adapter."""
import unittest
import json
import os
import sys
from pathlib import Path
from unittest.mock import patch
from urllib.error import URLError
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib.release_reader import Reply
from migration_claims import claims, github


class ClaimsTests(unittest.TestCase):
    def test_earlier_pr_owns_slot_and_all_peer_slots_block_allocation(self):
        data = {
            'repos/DoctorCRE/carr-system/pulls?state=open&per_page=100': [
                [{'number': 7}, {'number': 9}, {'number': 11}]],
            'repos/DoctorCRE/carr-system/pulls/7/files?per_page=100': [[
                {'filename': 'migrations/0891_earlier.sql', 'status': 'added'},
                {'filename': 'migrations/0800_history.sql', 'status': 'modified'}]],
            'repos/DoctorCRE/carr-system/pulls/11/files?per_page=100': [[
                {'filename': 'migrations/0892_later.sql', 'status': 'renamed'}]],
        }
        result = claims(9, data.__getitem__)
        self.assertEqual(result.earlier, {891})
        self.assertEqual(result.occupied, {891, 892})
        self.assertEqual(result.owners, {891: 7, 892: 11})

    def test_missing_or_truncated_provider_data_is_not_an_empty_claim_set(self):
        for payload in ({}, [[{'number': '7'}]], [[{'number': 7}]]):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                claims(9, lambda _: payload)

    def test_default_command_recognizes_its_own_open_branch(self):
        def read(path):
            self.assertTrue(path.endswith('pulls?state=open&per_page=100'))
            return [[{'number': 9, 'head': {'ref': 'feature'}}]]
        result = claims(None, read, branch='feature')
        self.assertEqual(result.earlier, set())
        self.assertEqual(result.occupied, set())


class RestTests(unittest.TestCase):
    def test_paginated_peers_and_files_keep_ownership(self):
        def read(url, **kwargs):
            self.assertTrue(url.startswith('https://api.github.com/repos/DoctorCRE/carr-system/pulls'))
            self.assertEqual(kwargs['headers']['Authorization'], 'Bearer synthetic')
            self.assertLessEqual(kwargs['timeout'], 15)
            self.assertEqual(kwargs['max_bytes'], 8 * 1024 * 1024)
            if '/files?' in url:
                data = [{'filename': 'migrations/0891_peer.sql', 'status': 'renamed'}]
            elif parse_qs(urlsplit(url).query)['page'] == ['1']:
                return Reply(200, {'Link': '<https://api.github.com/unused>; rel="next"'}, b'[]')
            else:
                data = [{'number': 7}, {'number': 9}]
            return Reply(200, {}, json.dumps(data).encode())
        with patch.dict(os.environ, {'GITHUB_TOKEN': 'synthetic', 'PATH': ''}), \
                patch('lib.release_reader.read', side_effect=read):
            result = claims(9, read=github)
        self.assertEqual(result.earlier, {891})
        self.assertEqual(result.occupied, {891})
        self.assertEqual(result.owners, {891: 7})

    def test_unreadable_pages_never_become_empty_claims(self):
        for response in [Reply(401, {}, b'[]'), Reply(403, {}, b'[]'),
                         Reply(429, {}, b'[]'), Reply(500, {}, b'[]'),
                         Reply(302, {'Location': 'https://example.com'}, b'[]'),
                         Reply(200, {}, b'not-json'), Reply(200, {}, b'{}'),
                         Reply(200, {}, b' ' * (8 * 1024 * 1024 + 1)),
                         URLError('offline'), TimeoutError('deadline')]:
            with self.subTest(response=type(response).__name__), \
                    patch.dict(os.environ, {'GITHUB_TOKEN': 'synthetic'}), \
                    patch('lib.release_reader.read', **(
                        {'side_effect': response} if isinstance(response, Exception)
                        else {'return_value': response})), \
                    self.assertRaises((ValueError, OSError)):
                github('repos/DoctorCRE/carr-system/pulls?state=open')

    def test_missing_token_refuses_without_cli_fallback(self):
        with patch.dict(os.environ, {}, clear=True), patch('lib.release_reader.read') as read, \
                self.assertRaisesRegex(ValueError, 'token required'):
            github('repos/DoctorCRE/carr-system/pulls')
        read.assert_not_called()

    def test_gh_token_workflow_alias_and_pagination_cap(self):
        response = Reply(200, {}, json.dumps([{'number': 7}] * 100).encode())
        with patch.dict(os.environ, {'GH_TOKEN': 'synthetic'}, clear=True), \
                patch('lib.release_reader.read', return_value=response) as read, \
                self.assertRaisesRegex(ValueError, 'pagination limit'):
            github('repos/DoctorCRE/carr-system/pulls')
        self.assertEqual(read.call_count, 100)

    def test_later_page_failure_refuses_partial_results(self):
        with patch.dict(os.environ, {'GITHUB_TOKEN': 'synthetic'}), \
                patch('lib.release_reader.read', side_effect=[
                    Reply(200, {'link': '<unused>; rel="next"'}, b'[{"number":7}]'),
                    Reply(503, {}, b'[]')]), self.assertRaises(ValueError):
            claims(9, read=github)

    def test_file_limit_still_refuses(self):
        with self.assertRaisesRegex(ValueError, 'GitHub limits'):
            claims(9, read=lambda path: [[{'number': 7}]] if '/files?' not in path else [[
                {'filename': 'migrations/0891_peer.sql', 'status': 'added'}] * 3000])

    def test_deadline_refuses_before_another_request(self):
        with patch.dict(os.environ, {'GITHUB_TOKEN': 'synthetic'}), \
                patch('migration_claims.time.monotonic', side_effect=[0, 121]), \
                patch('lib.release_reader.read') as read, \
                self.assertRaisesRegex(ValueError, 'deadline exceeded'):
            github('repos/DoctorCRE/carr-system/pulls')
        read.assert_not_called()

    def test_later_file_page_owns_slot(self):
        with patch.dict(os.environ, {'GITHUB_TOKEN': 'synthetic'}), \
                patch('lib.release_reader.read', side_effect=[
                    Reply(200, {}, b'[{"number":7}]'),
                    Reply(200, {'Link': '<unused>; rel="next"'}, b'[]'),
                    Reply(200, {}, b'[{"filename":"migrations/0891_peer.sql","status":"added"}]')]):
            result = claims(9, read=github)
        self.assertEqual(result.earlier, {891})


if __name__ == '__main__':
    unittest.main()
