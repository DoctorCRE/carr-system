#!/usr/bin/env python3
"""Exercise Hot rendering and refusal through synthetic records-mode builds."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
BUILDERS = ("generators/build-lead-board.py", "shared/build-lead-board-template.py")
SOURCES = '''
MODE_FILES, MODE_RECORDS = "files", "records"
ROUTER_SOURCE = "lead-router"
LANE_SOURCES = ()
def pool_reach(want): return True, "synthetic", None, None
def load_pool(want): return {"lead-router": []}
def load_leads(root, mode): return []
def resolve_mode(argv, default=MODE_RECORDS): return MODE_RECORDS, []
def source_note(mode): return "synthetic records"
'''


class HotBuildTest(unittest.TestCase):
    def build(self, builder, hot, *, unreadable=False, missing_record=False):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            script = root / builder
            script.parent.mkdir(parents=True)
            shutil.copyfile(ROOT / builder, script)
            lib = root / "lib"
            lib.mkdir()
            (lib / "__init__.py").touch()
            (lib / "record_sources.py").write_text(SOURCES)
            for name in ("sheets.py", "drive_recovery.py"):
                shutil.copyfile(ROOT / "lib" / name, lib / name)
            template = root / "generators" / "lead-board-template.html"
            template.parent.mkdir(exist_ok=True)
            shutil.copyfile(ROOT / "generators/lead-board-template.html", template)
            auto = root / "Automation"
            auto.mkdir()
            source = auto / "lead-board-hot.json"
            encoded = json.dumps(hot) if isinstance(hot, dict) else (hot or "")
            if builder.startswith("shared/") and unreadable:
                source.mkdir()
            elif builder.startswith("shared/") and hot is not None:
                source.write_text(encoded)
            marker = "HOT OUTPUT (ranked / proposed / holds / signals):\n"
            loops = [] if missing_record else [{
                "number": "T-SYNTH", "kind": "team_loop", "domain": "prospecting",
                "owner": "claude", "title": "NPI sweep 2026-10-19: synthetic ranked Hot output",
                "since_text": "2026-10-19",
            }]
            responses = {"loop-board": {"count": len(loops), "loops": loops}}
            if not missing_record:
                read_response = {"loop": {
                    "number": "T-SYNTH",
                    "body": marker + encoded,
                    "created_at": "2026-10-19T14:22:00Z",
                }}
                if unreadable:
                    read_response = {"error": "synthetic read refusal"}
                responses["read-loop"] = read_response
            (root / "record-responses.json").write_text(json.dumps(responses))
            (script.parent / "openpyxl.py").write_text("")
            (root / "run.sh").write_text('''#!/usr/bin/env python3
import json
from pathlib import Path
import sys

root = Path(__file__).parent
verb = sys.argv[2]
args = json.loads(sys.stdin.read() if sys.argv[3] == "-" else sys.argv[3])
with (root / "record-calls.jsonl").open("a") as log:
    log.write(json.dumps({"verb": verb, "args": args}) + "\\n")
reply = json.loads((root / "record-responses.json").read_text())[verb]
print(json.dumps(reply))
''')
            (root / "run.sh").chmod(0o755)
            (lib / "record_call.py").write_text((ROOT / "lib/record_call.py").read_text())
            (lib / "secret_redaction.py").write_text((ROOT / "lib/secret_redaction.py").read_text())
            output = (root / "out" if builder.startswith("generators/") else auto) / "lead-board.html"
            output.parent.mkdir(exist_ok=True)
            output.write_text("previous board")
            result = subprocess.run([sys.executable, str(script)], cwd=root,
                                    capture_output=True, text=True, timeout=20)
            calls_path = root / "record-calls.jsonl"
            calls = [json.loads(line) for line in calls_path.read_text().splitlines()] if calls_path.exists() else []
            return result, output.read_text(), source.name, calls

    def test_missing_hot_refuses_to_overwrite_board(self):
        for builder in BUILDERS:
            with self.subTest(builder=builder):
                result, output, source, _ = self.build(builder, None, missing_record=True)
                self.assertNotEqual(result.returncode, 0, result.stdout)
                if builder.startswith("shared/"):
                    self.assertIn(source, result.stderr)
                self.assertIn("Hot source unavailable", result.stderr)
                self.assertEqual(output, "previous board")

    def test_unreadable_hot_refuses_to_overwrite_board(self):
        for builder in BUILDERS:
            with self.subTest(builder=builder):
                result, output, source, _ = self.build(builder, None, unreadable=True)
                self.assertNotEqual(result.returncode, 0)
                if builder.startswith("shared/"):
                    self.assertIn(source, result.stderr)
                self.assertIn("Hot source unavailable", result.stderr)
                self.assertEqual(output, "previous board")

    def test_invalid_json_refuses_to_overwrite_board(self):
        for builder in BUILDERS:
            with self.subTest(builder=builder):
                result, output, source, _ = self.build(builder, "{broken")
                self.assertNotEqual(result.returncode, 0)
                if builder.startswith("shared/"):
                    self.assertIn(source, result.stderr)
                self.assertIn("Hot source unavailable", result.stderr)
                self.assertEqual(output, "previous board")

    def test_valid_hot_payload_and_count_reach_rendered_board(self):
        hot = {"ranked": [{"id": "L-TEST", "name": "Synthetic Ranked"}],
               "proposed": [{"name": "Synthetic Proposal"}],
               "holds": [{"name": "Synthetic Hold"}],
               "signals": [{"name": "Synthetic Signal"}]}
        for builder in BUILDERS:
            with self.subTest(builder=builder):
                result, output, _, _ = self.build(builder, hot)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn(json.dumps(hot, separators=(",", ":")), output)
                self.assertNotIn("__HOTN__", output)
                self.assertTrue('data-p="hot">Hot <span class="ct">2</span>' in output, 'Hot count must be 2')

    def test_confirmed_empty_hot_can_render_zero(self):
        hot = dict.fromkeys(("ranked", "proposed", "holds", "signals"), [])
        for builder in BUILDERS:
            with self.subTest(builder=builder):
                result, output, _, _ = self.build(builder, hot)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertTrue('data-p="hot">Hot <span class="ct">0</span>' in output, 'Confirmed empty Hot count must be 0')

    def test_records_mode_reads_sweep_groups_and_renders_their_fields(self):
        hot = {
            "ranked": [{"score": 7, "heat": "warm", "name": "Synthetic Clinic",
                        "entity": "NPI TEST", "stage": "VERIFY", "why": "synthetic evidence"}],
            "proposed": "Synthetic ranked rows await a partner verdict.",
            "holds": [{"name": "Synthetic hold", "reason": "synthetic reason"}],
            "signals": [{"name": "Synthetic signal", "ref": "L-TEST", "note": "synthetic note"}],
        }
        result, output, _, calls = self.build("generators/build-lead-board.py", hot)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Synthetic Clinic", output)
        self.assertIn("Synthetic ranked rows await a partner verdict.", output)
        self.assertIn("Synthetic hold", output)
        self.assertIn("synthetic reason", output)
        self.assertIn("Synthetic signal", output)
        self.assertIn("synthetic note", output)
        self.assertEqual([call["verb"] for call in calls], ["loop-board", "read-loop"])
        self.assertEqual(calls[0]["args"], {
            "kind": "team_loop", "domain": "prospecting", "owner": "claude",
            "status": "any", "search": "ranked Hot output", "limit": 300,
        })
        self.assertEqual(calls[1]["args"], {"number": "T-SYNTH", "kind": "team_loop"})
        self.assertIn('data-p="hot">Hot <span class="ct">1</span>', output)

        result, output, _, _ = self.build("shared/build-lead-board-template.py", hot)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('data-p="hot">Hot <span class="ct">1</span>', output)

    def test_missing_group_refuses_to_overwrite_board(self):
        hot = {"ranked": [], "proposed": [], "holds": []}
        result, output, _, _ = self.build("generators/build-lead-board.py", hot)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Hot group signals is not a list", result.stderr)
        self.assertEqual(output, "previous board")


if __name__ == "__main__":
    unittest.main()
