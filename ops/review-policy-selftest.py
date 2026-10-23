"""Offline regressions for the shared review verdict and path policy."""
import json
from pathlib import Path
import sys
import unittest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from lib import review_policy as policy


class ReviewPolicy(unittest.TestCase):
    def test_trusted_latest_review_and_strict_sha_header(self):
        cfg = json.loads((REPO / 'ops/config/review-delivery.v1.json').read_text())['worker']
        approve = 'APPROVE\nReviewed-SHA: ' + 'a' * 40
        trusted = {'body': approve, 'author_association': 'OWNER', 'created_at': '2026-01-01', 'id': 1}
        outsider = {'body': 'BLOCK', 'author_association': 'NONE', 'created_at': '2026-01-02', 'id': 2}
        block = {**trusted, 'body': 'REVIEW: BLOCKED', 'id': 3}
        self.assertIs(policy.deciding_verdict([trusted, outsider], cfg), trusted)
        self.assertIs(policy.deciding_verdict([trusted, block], cfg), block)
        self.assertEqual(policy.verdict(block['body'], cfg), 'block')
        self.assertEqual(policy.reviewed_header_sha(approve), 'a' * 40)
        for body in (approve + '\nReviewed-SHA: ' + 'a' * 40, approve.replace('APPROVE', 'approve'),
                     'APPROVE\ntext\nReviewed-SHA: ' + 'a' * 40):
            self.assertIsNone(policy.reviewed_header_sha(body))

    def test_path_globs_keep_github_directory_boundaries(self):
        self.assertTrue(policy._glob_hit('README.md', '**/*.md'))
        self.assertTrue(policy._glob_hit('docs/README.md', 'docs/*.md'))
        self.assertFalse(policy._glob_hit('docs/deep/README.md', 'docs/*.md'))
        self.assertTrue(policy._glob_hit('docs/deep/README.md', 'docs/**/*.md'))


if __name__ == '__main__':
    unittest.main()
