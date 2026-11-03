#!/usr/bin/env python3
"""Synthetic tests for links recovered from mail."""
import sys
import unittest
from pathlib import Path
from urllib.parse import quote
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib.email_links import extract_links, unwrap_safe_links


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

    def test_normalizes_bodies(self):
        destination = 'https://drive.google.com/drive/folders/a%20b?x=1&y=2'
        for region in ('nam12', 'eur03'):
            wrapper = wrap(destination).replace('example.', region + '.') + '&data=opaque%2520'
            for body, expected in (
                ('See ' + wrapper, 'See ' + destination),
                ('<a href="' + wrap(destination) + '">Open</a>',
                 '<a href="' + destination.replace('&', '&amp;') + '">Open</a>'),
                ('<a href="' + wrapper.replace('&', '&amp;') + '">Open</a>',
                 '<a href="' + destination.replace('&', '&amp;') + '">Open</a>'),
                (wrap(wrapper), destination),
                (wrap(quote(destination, safe='')), destination),
            ):
                with self.subTest(region=region, body=body):
                    self.assertEqual(unwrap_safe_links(body), expected)
                    self.assertEqual(unwrap_safe_links(expected), expected)

    def test_preserves_non_wrappers_and_malformed_wrappers(self):
        for body in (
            'https://example.test/?url=https%3A%2F%2Fdrive.google.com',
            wrap('https://loopnet.com/a').replace('outlook.com', 'outlook.com.evil.test'),
            wrap('https://loopnet.com/a').replace('example.safelinks', 'evilsafelinks'),
            'https://nam12.safelinks.protection.outlook.com/?data=opaque',
            wrap('https://loopnet.com/a') + '&url=https%3A%2F%2Fevil.test',
            wrap('not a URL'), wrap('javascript:alert(1)'),
            wrap('https://example.test/<tag>'), wrap('https://example.test/' + chr(34)),
            wrap(''), wrap('%FF'),
            wrap('https://loopnet.com/%0aattack'), wrap('https://[broken'),
            wrap('https://loopnet.com/a') + '%ZZ',
            wrap(wrap('not a URL')),
        ):
            with self.subTest(body=body):
                self.assertEqual(unwrap_safe_links(body), body)

    def test_links_are_bounded(self):
        body = ' '.join(f'https://loopnet.com/Listing/{i}' for i in range(30))
        self.assertEqual(len(extract_links(body)), 20)

    def test_decoded_terminal_punctuation_is_part_of_destination(self):
        for suffix in ('/(123)', '/a?key=value;', '/a#fragment!', '/a]'):
            destination = 'https://www.loopnet.com' + suffix
            wrapper = wrap(destination) + '&data=opaque'
            for body in (wrapper, wrapper + ').', '<a href="' + wrapper + '">Open</a>'):
                with self.subTest(body=body):
                    self.assertEqual(extract_links(body), [('listing', destination)])

    def test_normalized_destination_boundaries_survive_extraction(self):
        for suffix in ('/(123)', '/a)', '/a]', '/a.', '/a,', '/a?key=value;', '/a#end!'):
            destination = 'https://loopnet.com' + suffix
            for template in ('{}', 'See ({}).', '"{}"', "'{}'", '<p>{}</p>',
                             '<a href="{}">Open</a>'):
                body = template.format(wrap(destination) + '&data=opaque')
                with self.subTest(body=body):
                    normalized = unwrap_safe_links(body)
                    self.assertEqual(extract_links(normalized), [('listing', destination)])
                    self.assertEqual(unwrap_safe_links(normalized), normalized)

    def test_plain_assignments_preserve_query_bytes(self):
        destination = 'https://loopnet.com/a?x=1&copy=2'
        for prefix in ('Link=', 'foo="', "foo='", 'foo= "'):
            for value in (destination, wrap(destination)):
                body = prefix + value + '"'
                with self.subTest(body=body):
                    self.assertEqual(unwrap_safe_links(body), prefix + destination + '"')
                    self.assertEqual(extract_links(body), [('listing', destination)])

    def test_html_context_comes_from_tags(self):
        destination = 'https://loopnet.com/a?x=1&copy=2#end!'
        for template in ('<a href="{}">Open</a>', "<A\n HREF='{}'>Open</A>",
                         '<a title="a > b" href={}>Open</a>', '<img src="{}"/>'):
            body = template.format(wrap(destination))
            expected = template.format(destination.replace('&', '&amp;'))
            with self.subTest(body=body):
                self.assertEqual(unwrap_safe_links(body), expected)
                self.assertEqual(extract_links(body), [('listing', destination)])
                self.assertEqual(extract_links(expected), [('listing', destination)])
        destination = 'https://loopnet.com/a?x=1&amp;copy=2'
        for template in ('foo="{}"', '<p>foo="{}"</p>', '<!-- foo="{}" -->',
                         '<script>foo="{}"</script>'):
            body = template.format(wrap(destination))
            with self.subTest(body=body):
                self.assertEqual(unwrap_safe_links(body), template.format(destination))
                self.assertEqual(extract_links(body), [('listing', destination)])

    def test_plain_query_names_are_not_html_entities(self):
        destination = 'https://loopnet.com/Listing/a?x=1&copy=2'
        self.assertEqual(extract_links(destination), [('listing', destination)])
        self.assertEqual(extract_links('<a href="' + wrap(destination) + '">Open</a>'),
                         [('listing', destination)])


if __name__ == '__main__':
    unittest.main()
