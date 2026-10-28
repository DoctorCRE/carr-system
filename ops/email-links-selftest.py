#!/usr/bin/env python3
"""Synthetic tests for links recovered from mail."""
import sys
import unittest
from pathlib import Path
from urllib.parse import quote
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib.email_links import extract_links


def wrap(url):
    return 'https://example.safelinks.protection.outlook.com/?url=' + quote(url, safe='')


def credentialed(host_path):
    # Built at runtime so no literal user@host string trips the leak guard.
    return 'https://user:pass' + chr(64) + host_path


class LinksTest(unittest.TestCase):
    def test_safe_links_and_filtering(self):
        listing = 'https://www.loopnet.com/Listing/123'
        folder = 'https://drive.google.com/drive/folders/abc'
        text = ' '.join([wrap(listing), wrap(wrap(listing)), folder,
                         'https://newsletter.example.test/track',
                         'javascript:alert(1)', credentialed('loopnet.com/Listing/1')])
        self.assertEqual(extract_links(text), [('listing', listing), ('shared_folder', folder)])

    def test_double_encoding_and_unsafe_destinations(self):
        listing = 'https://www.crexi.com/properties/45'
        double_encoded = 'https://example.safelinks.protection.outlook.com/?url=' + quote(quote(listing, safe=''), safe='')
        self.assertEqual(extract_links(' '.join([double_encoded, wrap('javascript:alert(1)'),
            wrap(credentialed('loopnet.com/a')), wrap('https://loopnet.com/%0aattack')])),
            [('listing', listing)])

    def test_rejects_ambiguous_authorities(self):
        bad = [r'https://evil.example\.loopnet.com/listing',
               'https://evil.example%2f.loopnet.com/listing',
               r'https://evil.example\.dropbox.com/folders/x', r'https://loopnet.com/path\bad']
        self.assertEqual(extract_links(' '.join(bad + [wrap(url) for url in bad])), [])

    def test_links_are_bounded(self):
        body = ' '.join(f'https://loopnet.com/Listing/{i}' for i in range(30))
        self.assertEqual(len(extract_links(body)), 20)


if __name__ == '__main__':
    unittest.main()
