#!/usr/bin/env python3
"""Exercise Claude native loading in disposable synthetic projects; no CARR hooks or data."""
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ops"))
from git_env import fixture_env, scrubbed_env


def probe(matching):
    with tempfile.TemporaryDirectory(prefix="carr-native-loading-") as directory:
        root = Path(directory)
        subprocess.run(["git", "init", "-q", str(root)], check=True, env=fixture_env())
        for rel in (".claude/rules/carr-core.md", ".claude/rules/rule-6da539b5.md",
                    ".claude/skills/carr-before-send/SKILL.md"):
            target = root / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / rel, target)
        fixture = "tools/room-bridge/example.txt" if matching else "example.txt"
        (root / fixture).parent.mkdir(parents=True, exist_ok=True)
        (root / fixture).write_text("Synthetic loading fixture.\n")
        (root / "record.py").write_text(
            "import json,sys\nfrom pathlib import Path\n"
            "d=json.load(sys.stdin)\n"
            "p=Path(__file__).with_name('loaded.jsonl')\n"
            "with p.open('a') as f: f.write(json.dumps({k:d.get(k) for k in "
            "('file_path','load_reason')})+'\\n')\n")
        settings = {"hooks": {"InstructionsLoaded": [{"hooks": [
            {"type": "command", "command": f"python3 {root}/record.py"}]}]}}
        (root / ".claude/settings.json").write_text(json.dumps(settings))
        prompt = (f"Use Read to read only {fixture}. Then prepare a synthetic email draft "
                  "for an outbound send review. Use the applicable checkpoint skill. "
                  "Do not send anything. Do not run other tools or read other files.")
        result = subprocess.run(
            ["claude", "-p", prompt, "--verbose", "--output-format", "stream-json",
             "--setting-sources", "project", "--allowedTools", "Read", "Skill",
             "--max-budget-usd", "1"], cwd=root, capture_output=True, text=True, timeout=180, env=scrubbed_env())
        if result.returncode:
            raise RuntimeError(f"Claude probe failed ({result.returncode}): {result.stderr[-1000:]} {result.stdout[-1000:]}")
        events = [json.loads(line) for line in result.stdout.splitlines() if line.startswith("{")]
        loads = [json.loads(line) for line in (root / "loaded.jsonl").read_text().splitlines()]
        loaded = [Path(e["file_path"]).name for e in loads]
        assert "carr-core.md" in loaded, loads
        assert ("rule-6da539b5.md" in loaded) == matching, loads
        uses = [c for e in events if e.get("type") == "assistant"
                for c in e.get("message", {}).get("content", []) if c.get("type") == "tool_use"]
        assert any(c["name"] == "Read" and c["input"].get("file_path", "").endswith(fixture)
                   for c in uses), "fixture was not actually read"
        assert any(c["name"] == "Skill" and "carr-before-send" in str(c["input"])
                   for c in uses), "checkpoint skill did not load"
        return {"matching": matching, "core_loaded": True, "path_loaded": matching,
                "checkpoint_invoked": True,
                "load_reasons": sorted({e["load_reason"] for e in loads})}


if __name__ == "__main__":
    version = subprocess.check_output(["claude", "--version"], text=True).strip()
    print(json.dumps({"cli": version, "probes": [probe(False), probe(True)]}, indent=2))
