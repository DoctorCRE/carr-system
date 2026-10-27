#!/usr/bin/env python3
"""A stale hxprobe BUILD-SHA256 is red; a refreshed one is green (WR-000515)."""
import contextlib
import importlib.util
import io
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('hxprobe_source_digest', ROOT / 'ops/hxprobe-source-digest.py')
assert spec is not None and spec.loader is not None
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)

BINARY_LINE = f'{"ab" * 32}  target/release/hxprobe'


class ReceiptTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.crate = self.root / 'vendor/hxprobe'
        self.crate.mkdir(parents=True)
        (self.root / 'spec/outlook').mkdir(parents=True)
        (self.root / 'ops').mkdir()
        (self.root / 'spec/outlook/hxstore-atlas.json').write_text('{}\n')
        (self.root / 'ops/hxstore-atlas.py').write_text('# generator\n')
        (self.crate / 'src').mkdir()
        (self.crate / 'src/main.rs').write_text('fn main() {}\n')
        (self.crate / 'Cargo.toml').write_text('[package]\nname = "hxprobe"\n')
        (self.crate / 'BUILD-SHA256').write_text(f'{BINARY_LINE}\n\nCommand: cargo build --release --locked\n')
        gate.write(self.crate)

    def check(self) -> int:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return gate.main(['--crate', str(self.crate)])

    def test_refreshed_receipt_is_green(self):
        self.assertEqual(self.check(), 0)

    def test_source_change_with_previous_receipt_is_red_until_refreshed(self):
        (self.crate / 'src/main.rs').write_text('fn main() { println!("new"); }\n')
        self.assertEqual(self.check(), 1)
        gate.write(self.crate)
        self.assertEqual(self.check(), 0)

    def test_new_build_input_is_covered_without_a_list_edit(self):
        (self.crate / 'build.rs').write_text('fn main() {}\n')
        self.assertEqual(self.check(), 1)

    def test_external_build_inputs_stale_the_receipt(self):
        for path in ('spec/outlook/hxstore-atlas.json', 'ops/hxstore-atlas.py'):
            with self.subTest(path=path):
                source = self.root / path
                source.write_text(source.read_text() + 'changed\n')
                self.assertEqual(self.check(), 1)
                gate.write(self.crate)
                self.assertEqual(self.check(), 0)

    def test_receipt_without_a_digest_is_red(self):
        (self.crate / 'BUILD-SHA256').write_text(f'{BINARY_LINE}\n')
        self.assertEqual(self.check(), 1)

    def test_docs_and_build_output_do_not_stale_the_receipt(self):
        (self.crate / 'UPSTREAM.md').write_text('notes\n')
        (self.crate / 'target/release').mkdir(parents=True)
        (self.crate / 'target/release/hxprobe').write_bytes(b'\0binary')
        self.assertEqual(self.check(), 0)

    def test_refresh_keeps_the_installer_hash_on_line_one_and_one_digest_line(self):
        (self.crate / 'src/main.rs').write_text('fn main() { println!("again"); }\n')
        gate.write(self.crate)
        text = (self.crate / 'BUILD-SHA256').read_text()
        self.assertEqual(text.splitlines()[0], BINARY_LINE)
        self.assertEqual(text.count('Source digest: '), 1)
        self.assertIn('Command: cargo build --release --locked', text)


class CommittedReceiptTest(unittest.TestCase):
    def test_committed_receipt_describes_the_committed_source(self):
        # The gate itself: the gates class runs every ops/*-selftest.py on Linux.
        self.assertEqual(gate.recorded(gate.CRATE), gate.digest(gate.CRATE),
                         'vendor/hxprobe changed without a refreshed BUILD-SHA256; rebuild on the Studio, '
                         'update line 1, then run python3 ops/hxprobe-source-digest.py --write')


if __name__ == '__main__':
    unittest.main()
