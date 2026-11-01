#!/usr/bin/env python3
"""Test Codex cwd-scoped AGENTS loading; this CLI does not expose Claude's load hook."""
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ops"))
from git_env import fixture_env, scrubbed_env


def probe(nested):
    with tempfile.TemporaryDirectory(prefix="carr-codex-loading-") as directory:
        root = Path(directory)
        subprocess.run(["git", "init", "-q", str(root)], check=True, env=fixture_env())
        core = (ROOT / ".claude/rules/carr-core.md").read_text()
        (root / "AGENTS.md").write_text(core)
        target = root / "tools/room-bridge"
        target.mkdir(parents=True)
        shutil.copyfile(ROOT / "tools/room-bridge/AGENTS.md", target / "AGENTS.md")
        skill = root / ".agents/skills/carr-before-send/SKILL.md"
        skill.parent.mkdir(parents=True)
        shutil.copyfile(ROOT / ".agents/skills/carr-before-send/SKILL.md", skill)
        cwd = target if nested else root
        prompt = ("This is a synthetic native loading test. Do not run tools or send messages. "
                  "From the instructions already supplied, list every rule UUID you see. "
                  "Also list the available before-send checkpoint skill name.")
        result = subprocess.run(
            ["codex", "exec", "--json", "--sandbox", "read-only", "-C", str(cwd), prompt],
            cwd=cwd, capture_output=True, text=True, timeout=180, env=scrubbed_env())
        if result.returncode:
            raise RuntimeError(f"Codex probe failed: {result.stderr[-1000:]} {result.stdout[-1000:]}")
        events = [json.loads(line) for line in result.stdout.splitlines() if line.startswith("{")]
        messages = "\n".join(e.get("item", {}).get("text", "") for e in events)
        assert "179be4b8-2fe0-418d-9503-52d1e33921d3" in messages, "CORE not observed"
        assert ("6da539b5-84e9-49d1-84d8-28f05e151b20" in messages) == nested, "wrong nested scope"
        assert "carr-before-send" in messages, "checkpoint not listed"
        return {"nested_cwd": nested, "core_observed": True, "path_observed": nested,
                "checkpoint_listed": True, "evidence": "model response, no instruction-load event API"}


if __name__ == "__main__":
    print(json.dumps({"cli": subprocess.check_output(["codex", "--version"], text=True).strip(),
                      "probes": [probe(False), probe(True)]}, indent=2))
