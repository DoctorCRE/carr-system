#!/usr/bin/env python3
"""Check synthetic build evidence against the actual hxprobe SQLite export."""
import copy
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "spec/outlook/build-fixtures.v1.json"
FIXTURES = ROOT / "ops/fixtures/outlook-builds"
COLUMNS = {
    "folders": ["id", "account_id", "name", "parent_status", "parent_id"],
    "message_objects": ["local_key", "account_id", "folder_id", "subject", "recipients_status"],
    "messages": ["id", "local_keys", "sender", "subject", "body", "html", "body_kind",
                 "body_completeness", "body_outcome", "body_source", "copies", "flag_status", "flag_due_unix", "flag_completed_unix",
                 "folder_links_unresolved", "sent_unix", "received_unix"],
    "recipients": ["local_key", "kind", "kind_code", "position", "address", "name"],
    "message_folders": ["message_id", "folder_id"],
}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate(manifest):
    assert manifest["schema"] == "outlook-build-fixtures/v1"
    assert manifest["fixture_root"] == str(FIXTURES.relative_to(ROOT))
    assert manifest["observed_builds"] and manifest["fixtures"]
    builds = set()
    for build in manifest["observed_builds"]:
        evidence = build["evidence"]
        value = json.loads((ROOT / evidence["path"]).read_text())
        for part in evidence["pointer"].strip("/").split("/"):
            value = value[part]
        assert value == build["build"]
        assert build["layout_association"] is None
        assert value not in builds
        builds.add(value)
    ids = set()
    for fixture in manifest["fixtures"]:
        assert fixture["id"] not in ids
        ids.add(fixture["id"])
        assert fixture["observed_build"] is None
        assert fixture["schema"] == "hxprobe-db-fixture/v1"
        assert fixture["assertions"] and fixture["claim"]
        assert (ROOT / fixture["layout_evidence"]).is_file()
        assert Path(fixture["input"]).name == fixture["input"]
        assert digest(FIXTURES / fixture["input"]) == fixture["sha256"]
        assert fixture["outcome"] in {"decoded", "partial", "rejected"}
        if fixture["outcome"] == "rejected":
            assert fixture["expected"] is None and fixture["stderr_contains"]
        else:
            assert Path(fixture["expected"]).name == fixture["expected"]
            path = FIXTURES / fixture["expected"]
            assert digest(path) == fixture["expected_sha256"]
            expected = json.loads(path.read_text())
            assert expected["schema"] == fixture["schema"]
            assert set(expected["tables"]) == set(COLUMNS)
            for table, columns in COLUMNS.items():
                assert expected["tables"][table]["columns"] == columns
                assert all(len(row) == len(columns) for row in expected["tables"][table]["rows"])
    return manifest["fixtures"]


def decode(binary, fixture, directory):
    source = directory / "synthetic.hxd"
    source.write_bytes((FIXTURES / fixture["input"]).read_bytes())
    source.chmod(0o600)
    database = directory / "decoded.db"
    result = subprocess.run([str(binary), "db", str(source), str(database)],
                            capture_output=True, text=True, timeout=30)
    if fixture["outcome"] == "rejected":
        assert result.returncode != 0
        assert fixture["stderr_contains"] in result.stderr
        assert not database.exists()
        return None
    assert result.returncode == 0, result.stderr
    with sqlite3.connect(database) as db:
        tables = {}
        for table, columns in COLUMNS.items():
            selection = ",".join(columns)
            rows = db.execute(f"SELECT {selection} FROM {table} ORDER BY {selection}").fetchall()
            tables[table] = {"columns": columns, "rows": [list(row) for row in rows]}
        meta = dict(db.execute("SELECT key,value FROM meta"))
        expected = json.loads((FIXTURES / fixture["expected"]).read_text())
        return {"schema": fixture["schema"], "tables": tables,
                "meta": {key: meta[key] for key in expected["meta"]}}


class BuildFixtures(unittest.TestCase):
    def test_build_fixture_manifest_requires_evidence_and_exact_decoder_output(self):
        fixtures = validate(json.loads(MANIFEST.read_text()))
        binary = ROOT / "vendor/hxprobe/target/debug/hxprobe"
        subprocess.run(["cargo", "build", "--locked", "--manifest-path",
                        str(ROOT / "vendor/hxprobe/Cargo.toml")], check=True,
                       capture_output=True, timeout=180)
        self.assertTrue(binary.is_file())
        for fixture in fixtures:
            with self.subTest(fixture=fixture["id"]), tempfile.TemporaryDirectory() as temp:
                directory = Path(temp)
                self.assertEqual(directory.stat().st_mode & 0o777, 0o700)
                actual = decode(binary, fixture, directory)
                if fixture["expected"]:
                    expected = json.loads((FIXTURES / fixture["expected"]).read_text())
                    self.assertEqual(actual, expected)

    def test_manifest_rejects_missing_evidence_and_unreviewed_artifact_changes(self):
        original = json.loads(MANIFEST.read_text())
        for mutation in ("evidence", "build", "input", "golden", "schema", "association"):
            manifest = copy.deepcopy(original)
            if mutation == "evidence":
                manifest["observed_builds"][0]["evidence"]["path"] = "absent-evidence.json"
            elif mutation == "build":
                manifest["observed_builds"][0]["build"] = "unobserved"
            elif mutation == "association":
                manifest["fixtures"][0]["observed_build"] = "16.114.26101123"
            elif mutation == "schema":
                manifest["fixtures"][0]["schema"] = "unknown"
            else:
                field = "sha256" if mutation == "input" else "expected_sha256"
                manifest["fixtures"][0][field] = "0" * 64
            with self.subTest(mutation=mutation), self.assertRaises((AssertionError, OSError)):
                validate(manifest)

    def test_committed_inputs_reproduce_without_decoder_or_golden_edits(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            writer = directory / "write-inputs.py"
            writer.write_bytes((FIXTURES / writer.name).read_bytes())
            subprocess.run([os.sys.executable, str(writer)], check=True, timeout=10)
            for fixture in validate(json.loads(MANIFEST.read_text())):
                self.assertEqual(digest(directory / fixture["input"]), fixture["sha256"])
            self.assertEqual(list(directory.glob("*.json")), [])


if __name__ == "__main__":
    unittest.main()
