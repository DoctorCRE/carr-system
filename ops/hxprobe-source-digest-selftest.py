#!/usr/bin/env python3
"""The installed hxprobe stays bound to its source without an in-tree pin (WR-000515, WR-000440)."""
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
        self.vendor = self.root / 'installed'
        self.decoder = self.vendor / 'hxprobe/target/release/hxprobe'

    def cargo_calls(self) -> list[str]:
        log = self.root / 'tools/calls.jsonl'
        return [json.loads(line)[0] for line in log.read_text().splitlines()] if log.exists() else []

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
            return gate.main(['--crate', str(self.crate), '--build-into', str(self.vendor)])

    def test_other_machine_build_records_the_local_artifact_and_source(self):
        self.assertEqual(self.build(), 0)
        self.assertEqual(self.decoder.read_bytes(), b'synthetic other-machine binary')
        receipt = json.loads((self.vendor / '.carr-build.json').read_text())
        self.assertEqual(receipt['source_digest'], gate.digest(self.crate))
        self.assertEqual(receipt['binary_sha256'], hashlib.sha256(b'synthetic other-machine binary').hexdigest())
        self.assertIn('aarch64-apple-darwin', receipt['rustc'])
        self.assertEqual(receipt['cargo'], 'cargo synthetic-other-machine')
        calls = [json.loads(line) for line in (self.root / 'tools/calls.jsonl').read_text().splitlines()]
        self.assertEqual([row[0] for row in calls], ['test', 'build'])
        self.assertTrue(all('--locked' in row and '--release' in row for row in calls))

    def test_receipted_decoder_for_this_source_is_kept_without_cargo(self):
        self.assertEqual(self.build(), 0)
        (self.root / 'tools/calls.jsonl').unlink()
        self.assertEqual(self.build(), 0)
        self.assertEqual(self.cargo_calls(), [])

    def test_source_change_rebuilds(self):
        self.assertEqual(self.build(), 0)
        (self.crate / 'src/main.rs').write_text('fn main() { println!("new"); }\n')
        self.assertEqual(self.build(), 0)
        self.assertEqual(self.cargo_calls(), ['test', 'build', 'test', 'build'])
        receipt = json.loads((self.vendor / '.carr-build.json').read_text())
        self.assertEqual(receipt['source_digest'], gate.digest(self.crate))

    def test_a_decoder_that_does_not_match_its_receipt_is_removed_and_rebuilt(self):
        self.assertEqual(self.build(), 0)
        self.decoder.write_bytes(b'altered decoder')
        self.assertEqual(self.build(), 0)
        self.assertEqual(self.decoder.read_bytes(), b'synthetic other-machine binary')
        self.decoder.write_bytes(b'altered decoder')
        self.assertEqual(self.build('build'), 1)
        self.assertFalse(self.decoder.exists(), 'a failed rebuild never leaves the altered decoder in place')

    def test_failed_tests_or_build_keep_the_previous_receipted_decoder(self):
        self.assertEqual(self.build(), 0)
        old_receipt = (self.vendor / '.carr-build.json').read_bytes()
        (self.crate / 'src/main.rs').write_text('fn main() { panic!("changed"); }\n')
        for failure in ('test', 'build'):
            with self.subTest(failure=failure):
                self.assertEqual(self.build(failure), 1)
                self.assertEqual(self.decoder.read_bytes(), b'synthetic other-machine binary')
                self.assertEqual((self.vendor / '.carr-build.json').read_bytes(), old_receipt)

    def test_every_build_input_changes_the_digest(self):
        for path in ('vendor/hxprobe/build.rs', 'spec/outlook/hxstore-atlas.json', 'ops/hxstore-atlas.py'):
            with self.subTest(path=path):
                before = gate.digest(self.crate)
                source = self.root / path
                source.write_text((source.read_text() if source.exists() else '') + '// changed\n')
                self.assertNotEqual(gate.digest(self.crate), before)

    def test_docs_and_build_output_do_not_change_the_digest(self):
        before = gate.digest(self.crate)
        (self.crate / 'UPSTREAM.md').write_text('notes\n')
        (self.crate / 'target/release').mkdir(parents=True)
        (self.crate / 'target/release/hxprobe').write_bytes(b'\0binary')
        self.assertEqual(gate.digest(self.crate), before)


class NoTrackedPinTest(unittest.TestCase):
    def test_no_tracked_file_records_the_source_digest(self):
        # Two decoder PRs conflict on any committed file derived from the source (WR-000440).
        want = gate.digest(gate.CRATE)
        holders = [str(path) for folder in (gate.CRATE, ROOT / 'spec/outlook') for path in folder.rglob('*')
                   if path.is_file() and 'target' not in path.relative_to(folder).parts
                   and want.encode() in path.read_bytes()]
        self.assertEqual(holders, [])

if __name__ == '__main__':
    unittest.main()
