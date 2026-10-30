#!/usr/bin/env python3
"""Atlas contracts, checked against the committed atlas and its pinned sources."""

import copy
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
ATLAS = ROOT / "spec/outlook/hxstore-atlas.json"


class AtlasContractTests(unittest.TestCase):
    def setUp(self):
        self.atlas = json.loads(ATLAS.read_text())

    def run_cli(self, atlas, command="render"):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "atlas.json"
            path.write_text(json.dumps(atlas))
            return subprocess.run([sys.executable, str(ROOT / "ops/hxstore-atlas.py"), command, str(path)],
                                  capture_output=True, text=True, timeout=10)

    def test_reference_changes_when_atlas_name_changes(self):
        field = next(e for e in self.atlas["entries"] if e["id"] == "object.class.00c9.offset.718")
        field["name"] = "Example renamed send time"
        result = self.run_cli(self.atlas)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("## Example renamed send time (object.class.00c9.offset.718)", result.stdout)
        self.assertIn('"confidence": "proven"', result.stdout)
        self.assertIn('"first_seen_version": null', result.stdout)

    def test_reference_matches_committed_atlas(self):
        result = self.run_cli(self.atlas)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, (ROOT / "spec/outlook/hxstore-atlas.md").read_text())

    def test_evidence_binds_exact_vendor_sources(self):
        for source, binding in self.atlas["sources"].items():
            revision = binding["revision"]
            reachable = subprocess.run(["git", "merge-base", "--is-ancestor", revision, "origin/main"],
                                       cwd=ROOT, capture_output=True).returncode == 0
            self.assertTrue(reachable, f"{source}: {revision} not reachable from origin/main")
            result = subprocess.run(["git", "show", f"{revision}:{source}"], cwd=ROOT, capture_output=True)
            self.assertEqual(result.returncode, 0, f"{source}: missing at {revision}")
            self.assertEqual(hashlib.sha256(result.stdout).hexdigest(), binding["sha256"],
                             f"{source}: digest mismatch at {revision}")

    def test_mail_evidence_names_functions_in_the_pinned_reader_and_exporter(self):
        message = next(e for e in self.atlas['entries'] if e['id'] == 'object.class.00c9')
        self.assertIn({'source': 'vendor/hxprobe/src/main.rs', 'symbol': 'cmd_db'}, message['evidence'])
        for source in ('vendor/hxprobe/src/main.rs', 'vendor/hxprobe/src/message.rs'):
            revision = self.atlas['sources'][source]['revision']
            code = subprocess.check_output(['git', 'show', f'{revision}:{source}'], cwd=ROOT, text=True)
            symbols = {ev['symbol'] for entry in self.atlas['entries'] for ev in entry['evidence']
                       if ev['source'] == source}
            for symbol in symbols:
                with self.subTest(source=source, symbol=symbol):
                    self.assertIsNotNone(re.search(r'\bfn\s+' + re.escape(symbol.split('::')[-1]) + r'\s*\(', code),
                                         f'{source}: {symbol} absent at {revision}')

    def test_evidence_rejects_unreachable_revision(self):
        source = "vendor/hxprobe/src/attachment.rs"
        self.atlas["sources"] = {source: self.atlas["sources"][source]}
        tree = subprocess.check_output(["git", "rev-parse", "origin/main^{tree}"], cwd=ROOT, text=True).strip()
        commit = subprocess.check_output(["git", "-c", "user.name=Atlas Test", "-c", "user.email=atlas@example.test",
                                          "commit-tree", tree, "-m", "unreachable atlas test"], cwd=ROOT, text=True).strip()
        for revision in (commit, "0" * 40):
            self.atlas["sources"][source]["revision"] = revision
            with self.subTest(revision=revision), self.assertRaisesRegex(AssertionError, "not reachable from origin/main"):
                self.test_evidence_binds_exact_vendor_sources()

    def test_evidence_rejects_digest_mismatch(self):
        source = "vendor/hxprobe/src/attachment.rs"
        self.atlas["sources"] = {source: self.atlas["sources"][source]}
        self.atlas["sources"][source]["sha256"] = "0" * 64
        with self.assertRaisesRegex(AssertionError, "digest mismatch"):
            self.test_evidence_binds_exact_vendor_sources()

    def test_invalid_contracts_emit_no_partial_reference_or_source_excerpts(self):
        cases = []
        for key, value in [("schema", "unsupported")]:
            changed = copy.deepcopy(self.atlas)
            changed[key] = value
            cases.append(changed)
        for key, value in [("parent", "missing"), ("confidence", "certain"),
                           ("first_seen_version", "unknown"), ("evidence", []), ("wire_type", 42),
                           ("identifier", {"kind": "envelope_tag", "value": -1}),
                           ("identifier", {"kind": "numeric-field-from-offset", "value": 574})]:
            changed = copy.deepcopy(self.atlas)
            changed["entries"][0][key] = value
            cases.append(changed)
        for changed in cases:
            with self.subTest(changed=changed["entries"][0]["id"]):
                result = self.run_cli(changed)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, "")
                self.assertEqual(result.stderr, "store atlas is invalid\n")

    def test_wire_ids_are_not_fabricated_from_offsets_or_selectors(self):
        entries = self.atlas["entries"]
        wire = [e for e in entries if e["identifier"]["kind"] == "wire_field_id"]
        self.assertTrue(wire)
        self.assertTrue(all(e["identifier"]["value"] is None and e["confidence"] == "unknown" for e in wire))
        start = next(e for e in entries if e["id"] == "object.class.006b.offset.574")
        self.assertEqual(start["identifier"], {"kind": "byte_offset", "value": 574})
        self.assertEqual(start["wire_type"], "u64le-dotnet-ticks")
        self.assertTrue(all(e["first_seen_version"] is None for e in entries))


if __name__ == "__main__":
    unittest.main()
