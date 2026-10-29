#!/usr/bin/env python3
"""Pin historical credential syntax with fake files and literal expectations."""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib.credential_file import read_env_file

SYNTAXES = ('prefix', 'prefix-double-single', 'trimmed', 'paired')
CASES = [
    ('', (None, None, None, None)),
    ('KEY=fake\n', ('fake', 'fake', 'fake', 'fake')),
    (' KEY=leading\n', (None, None, 'leading', 'leading')),
    ('KEY =spaced\n', (None, None, 'spaced', 'spaced')),
    ('export KEY=exported\n', (None, None, None, None)),
    ('#KEY=comment\ninvalid line\n', (None, None, None, None)),
    ("KEY='quoted'\n", ('quoted', 'quoted', 'quoted', 'quoted')),
    ('KEY="quoted"\n', ('quoted', 'quoted', 'quoted', 'quoted')),
    ("KEY='mixed\"\n", ('mixed', 'mixed', 'mixed', '\'mixed"')),
    ("KEY=\"'nested'\"\n", ('nested', 'nested', 'nested', "'nested'")),
    ("KEY='\"nested\"'\n", ('nested', '"nested"', 'nested', '"nested"')),
    ("KEY='''repeated'''\n", ('repeated', 'repeated', 'repeated', "''repeated''")),
    ("KEY='unterminated\n", ('unterminated', 'unterminated', 'unterminated', "'unterminated")),
    ('KEY=value # comment\n', ('value # comment',) * 4),
    ("KEY='with spaces'\n", ('with spaces',) * 4),
    ("KEY='fake'\\''value'\n", ("fake'\\''value",) * 4),
    ('KEY="fake\\\\value"\n', ('fake\\\\value',) * 4),
    ('KEY=fake\\ value\n', ('fake\\ value',) * 4),
    ('KEY=fake\vvalue\n', ('fake\vvalue', 'fake\vvalue', 'fake', 'fake')),
    ('KEY=fake\fvalue\n', ('fake\fvalue', 'fake\fvalue', 'fake', 'fake')),
    ('KEY=fake\x85value\n', ('fake\x85value', 'fake\x85value', 'fake', 'fake')),
    ('KEY=fake\u2028value\n', ('fake\u2028value', 'fake\u2028value', 'fake', 'fake')),
]


class LegacyCredentials(unittest.TestCase):
    def test_literal_syntax(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'fixture.env'
            for fixture, expected in CASES:
                path.write_text(fixture, encoding='utf-8')
                for index, syntax in enumerate(SYNTAXES):
                    for duplicates in ('first', 'last'):
                        with self.subTest(syntax=syntax, duplicates=duplicates, fixture=fixture):
                            self.assertEqual(read_env_file(path, syntax=syntax, duplicates=duplicates).get('KEY'),
                                             expected[index])

    def test_duplicate_selection(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'fixture.env'
            for syntax in (*SYNTAXES, 'shell'):
                for fixture, first, last in [('KEY=first\nKEY=last\n', 'first', 'last'),
                                              ('KEY=\nKEY=second\n', '', 'second')]:
                    path.write_text(fixture, encoding='utf-8')
                    with self.subTest(syntax=syntax, fixture=fixture):
                        self.assertEqual(read_env_file(path, syntax=syntax, duplicates='first')['KEY'], first)
                        self.assertEqual(read_env_file(path, syntax=syntax)['KEY'], last)

    def test_first_match_does_not_decode_unused_tail(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'fixture.env'
            path.write_bytes(b'KEY=fake\n' + b'x' * 20000 + b'\xff')
            for syntax in ('prefix', 'prefix-double-single'):
                with self.subTest(syntax=syntax):
                    self.assertEqual(read_env_file(path, syntax=syntax, duplicates='first', names=('KEY',)),
                                     {'KEY': 'fake'})
                    with self.assertRaises(UnicodeDecodeError):
                        read_env_file(path, syntax=syntax, names=('KEY',))
                    with self.assertRaises(UnicodeDecodeError):
                        read_env_file(path, syntax=syntax, duplicates='first', names=('OTHER',))

    def test_missing_file(self):
        with tempfile.TemporaryDirectory() as directory:
            for syntax in SYNTAXES:
                with self.subTest(syntax=syntax), self.assertRaises(FileNotFoundError):
                    read_env_file(Path(directory) / 'missing.env', syntax=syntax)


if __name__ == '__main__':
    unittest.main()
