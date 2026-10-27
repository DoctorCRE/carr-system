#!/usr/bin/env python3
"""A stale hxprobe BUILD-SHA256 is red; a refreshed one is green (WR-000515)."""
import contextlib
import importlib.util
import io
import hashlib
import json
import os
import sys
from unittest.mock import patch
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

    def build(self, failure=''):
        tools = self.root / 'tools'
        tools.mkdir(exist_ok=True)
        cargo = tools / 'cargo'
        cargo.write_text(f"#!{sys.executable}\n" + '''
import json
from pathlib import Path
import sys
args = sys.argv[1:]
if args == ['--version']:
    print('cargo synthetic-other-machine')
    raise SystemExit(0)
with (Path(__file__).parent / 'calls.jsonl').open('a') as log:
    log.write(json.dumps(args) + '\\n')
if args[0] == FAILURE:
    raise SystemExit(17)
if args[0] == 'build':
    built = Path(args[args.index('--target-dir') + 1]) / 'release/hxprobe'
    built.parent.mkdir(parents=True, exist_ok=True)
    built.write_bytes(b'synthetic other-machine binary')
'''.replace('FAILURE', repr(failure)))
        cargo.chmod(0o700)
        rustc = tools / 'rustc'
        rustc.write_text(f"#!{sys.executable}\nprint('rustc synthetic\\nhost: aarch64-apple-darwin')\n")
        rustc.chmod(0o700)
        with patch.dict(os.environ, {'PATH': str(tools)}), contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(io.StringIO()):
            return gate.main(['--crate', str(self.crate), '--build-into', str(self.root / 'installed')])

    def test_other_machine_build_uses_verified_source_and_records_local_artifact(self):
        self.assertEqual(self.build(), 0)
        vendor = self.root / 'installed'
        binary = vendor / 'hxprobe/target/release/hxprobe'
        self.assertEqual(binary.read_bytes(), b'synthetic other-machine binary')
        receipt = json.loads((vendor / '.carr-build.json').read_text())
        self.assertEqual(receipt['source_digest'], gate.digest(self.crate))
        self.assertEqual(receipt['binary_sha256'], hashlib.sha256(b'synthetic other-machine binary').hexdigest())
        self.assertIn('aarch64-apple-darwin', receipt['rustc'])
        self.assertEqual(receipt['cargo'], 'cargo synthetic-other-machine')
        calls = [json.loads(line) for line in (self.root / 'tools/calls.jsonl').read_text().splitlines()]
        self.assertEqual([row[0] for row in calls], ['test', 'build'])
        self.assertTrue(all('--locked' in row and '--release' in row for row in calls))

    def test_stale_source_refuses_before_running_cargo_or_creating_output(self):
        (self.crate / 'src/main.rs').write_text('fn main() { panic!("changed"); }\n')
        self.assertEqual(self.build(), 1)
        self.assertFalse((self.root / 'tools/calls.jsonl').exists())
        self.assertFalse((self.root / 'installed').exists())

    def test_failed_tests_or_build_keep_the_previous_decoder_and_receipt(self):
        self.assertEqual(self.build(), 0)
        vendor = self.root / 'installed'
        decoder = vendor / 'hxprobe/target/release/hxprobe'
        receipt = vendor / '.carr-build.json'
        decoder.write_bytes(b'previous decoder')
        old_receipt = receipt.read_bytes()
        for failure in ('test', 'build'):
            with self.subTest(failure=failure):
                self.assertEqual(self.build(failure), 1)
                self.assertEqual(decoder.read_bytes(), b'previous decoder')
                self.assertEqual(receipt.read_bytes(), old_receipt)

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

    def test_refresh_keeps_the_reference_hash_on_line_one_and_one_digest_line(self):
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
                         'vendor/hxprobe changed without a refreshed BUILD-SHA256; rebuild the reference artifact, '
                         'update line 1, then run python3 ops/hxprobe-source-digest.py --write')


if __name__ == '__main__':
    unittest.main()
