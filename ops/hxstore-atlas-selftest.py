#!/usr/bin/env python3
"""Atlas contracts and hxprobe output, using generated synthetic bytes only."""

import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
import zlib

ROOT = Path(__file__).resolve().parents[1]
ATLAS = ROOT / "spec/outlook/hxstore-atlas.json"


def synthetic_store():
    text = "IPM.Note\0<fixture@example.test>\0Synthetic subject\0Synthetic subject\0"
    data = b"\0" * 32 + text.encode("utf-16le")
    extra = len(data) - 15
    compressed = bytes([0xf0]) + bytes([255]) * (extra // 255) + bytes([extra % 255]) + data
    block = bytearray(40) + compressed
    struct.pack_into("<QIIII", block, 8, 0x5d0245643b706a05, 8, len(compressed), len(data), 4)
    struct.pack_into("<I", block, 4, zlib.crc32(block[8:]))
    struct.pack_into("<I", block, 0, zlib.crc32(block[4:32]))
    header = bytearray(64)
    header[:8] = b"Nostromo"
    header[8] = ord("i")
    struct.pack_into("<Q", header, 56, 4096)
    return header + block


class DecoderNameTests(unittest.TestCase):
    def test_renaming_atlas_entry_changes_rebuilt_hxprobe_map_and_tally(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            crate = root / "vendor/hxprobe"
            shutil.copytree(ROOT / "vendor/hxprobe", crate,
                            ignore=shutil.ignore_patterns("target", ".git"))
            (root / "ops").mkdir()
            generator = ROOT / "ops/hxstore-atlas.py"
            shutil.copyfile(generator, root / "ops/hxstore-atlas.py")
            target = root / "spec/outlook/hxstore-atlas.json"
            target.parent.mkdir(parents=True)
            atlas = json.loads(ATLAS.read_text())
            target.write_text(json.dumps(atlas))
            fixture = root / "synthetic.hxd"
            fixture.write_bytes(synthetic_store())
            env = dict(os.environ, CARGO_TARGET_DIR=str(ROOT / "vendor/hxprobe/target/atlas-selftest"))

            def output():
                build = subprocess.run(["cargo", "build", "--locked"], cwd=crate, env=env,
                                       capture_output=True, text=True, timeout=180)
                self.assertEqual(build.returncode, 0, build.stderr)
                binary = Path(env["CARGO_TARGET_DIR"]) / "debug/hxprobe"
                result = subprocess.run([str(binary), "map", str(fixture), "1"],
                                        capture_output=True, text=True, timeout=10)
                self.assertEqual(result.returncode, 0, result.stderr)
                return result.stdout

            before = output()
            self.assertIn("ItemClass", before)
            entry = next(e for e in atlas["entries"] if e.get("decoder_key") == "ItemClass")
            entry["name"] = "RenamedClass"
            target.write_text(json.dumps(atlas))
            after = output()
            self.assertNotIn("ItemClass", after)
            self.assertEqual(after.count("RenamedClass"), 2, after)
            self.assertEqual(
                sorted(" ".join(line.split()) for line in before.replace("ItemClass", "RenamedClass").splitlines()),
                sorted(" ".join(line.split()) for line in after.splitlines()))


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
        field = next(e for e in self.atlas["entries"] if e.get("decoder_key") == "ItemClass")
        field["name"] = "Example renamed class"
        result = self.run_cli(self.atlas)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("## Example renamed class (mail.selector.ItemClass)", result.stdout)
        self.assertIn('"kind": "string_run_selector"', result.stdout)
        self.assertIn('"first_seen_version": null', result.stdout)

    def test_duplicate_display_names_cannot_collapse_tally_keys(self):
        fields = [e for e in self.atlas["entries"] if "decoder_key" in e]
        fields[1]["name"] = fields[0]["name"]
        result = self.run_cli(self.atlas, "rust")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "store atlas is invalid\n")

    def test_reference_matches_committed_atlas(self):
        result = self.run_cli(self.atlas)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, (ROOT / "spec/outlook/hxstore-atlas.md").read_text())

    def test_evidence_binds_exact_vendor_sources(self):
        for source, binding in self.atlas["sources"].items():
            raw = subprocess.run(["git", "show", f"{binding['revision']}:{source}"], cwd=ROOT,
                                 capture_output=True, check=True).stdout
            self.assertEqual(hashlib.sha256(raw).hexdigest(), binding["sha256"], source)

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
