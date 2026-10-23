"""Check recovery instructions without executing recovery or reading backup data."""
from pathlib import Path
import ast
import re
import shlex
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[2]


class RecoveryRunbookTest(unittest.TestCase):
    def test_f1_recovery_keeps_data_security_and_cutover_separate(self):
        text = (ROOT / "RECOVERY.md").read_text()
        headings = re.findall(r"^## (.+)$", text, re.MULTILINE)
        for phase in ("Data recovery", "Structure reconstruction", "Security bootstrap", "Service cutover"):
            self.assertIn(phase, headings)
        self.assertIn(".venv/bin/python tools/migrate.py --apply --yes", text)
        self.assertNotIn("bin/migrate-prod.sh", text)
        for condition in ("restore_rehearse", "neondb", "readable production", "retains failed runs"):
            self.assertIn(condition, text)
        security = text.split("## Security bootstrap", 1)[1].split("## Service cutover", 1)[0]
        self.assertLess(security.index("allowed and denied"), security.index("tools/migrate.py --apply"))

    def test_f8_survival_and_recovery_proof_have_explicit_limits(self):
        text = (ROOT / "RECOVERY.md").read_text()
        for claim in ("Nothing important is only on the Mac", "no unique data", "no failure point",
                      "covers the first six hours", "off-Mac liveness check on"):
            self.assertNotIn(claim, text)
        for item in ("out/outlook-intake/state.json", "out/outlook-intake/needs-refresh.json",
                     "out/outlook-send/ledger.jsonl", "out/calendar-archive/", "out/boards/",
                     "exporters/templates/", "exporters/dossier-roster.local.json",
                     "tools/dictation-rig/vocab-local.txt", "machine-role marker",
                     "matching age identity", "account recovery factors"):
            self.assertIn(item, text)
        for limit in ("no provider-bound restore receipt", "evidence_producer_unavailable",
                      "workflow identity", "provider's current restore window", "historical result"):
            self.assertIn(limit, text)

    def test_shell_examples_parse_and_reference_retained_commands(self):
        text = (ROOT / "RECOVERY.md").read_text()
        blocks = re.findall(r"```sh\n(.*?)```", text, re.DOTALL)
        self.assertTrue(blocks, "The operator must have concrete command examples")
        for block in blocks:
            for shell in ("bash", "zsh"):
                with self.subTest(shell=shell, block=block):
                    result = subprocess.run([shell, "-n"], input=block, text=True, capture_output=True)
                    self.assertEqual(result.returncode, 0, result.stderr)
            for line in block.replace("\\\n", " ").strip().splitlines():
                words = shlex.split(line)
                self.assertIn(words[0], ("./run.sh", ".venv/bin/python"))
                if words[0] == "./run.sh":
                    self.assertIn(words[1], ("restore-rehearse", "health"))
                    self.assertTrue((ROOT / "run.sh").is_file())
                elif words[1] != "-c":
                    self.assertTrue((ROOT / words[1]).is_file(), words[1])
                if words[1] == "tools/migrate.py":
                    source = ast.parse((ROOT / words[1]).read_text())
                    options = {arg.value for node in ast.walk(source)
                               if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                               and node.func.attr == "add_argument" for arg in node.args
                               if isinstance(arg, ast.Constant) and isinstance(arg.value, str)}
                    for word in words[2:]:
                        if word.startswith("--"):
                            self.assertIn(word, options)
                    boundary = words[words.index("--through") + 1]
                    self.assertTrue((ROOT / "migrations" / boundary).is_file())


if __name__ == "__main__":
    unittest.main()
