#!/usr/bin/env python3
"""Probe live Codex cwd-scoped AGENTS loading using an authenticated provider account."""
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
        shutil.copyfile(ROOT / "AGENTS.md", root / "AGENTS.md")
        shutil.copyfile(ROOT / "AGENT_OPERATING_GUIDE.md", root / "AGENT_OPERATING_GUIDE.md")
        target = root / "tools/room-bridge"
        target.mkdir(parents=True)
        shutil.copyfile(ROOT / "tools/room-bridge/AGENTS.md", target / "AGENTS.md")
        skill = root / ".agents/skills/carr-before-send/SKILL.md"
        skill.parent.mkdir(parents=True)
        shutil.copyfile(ROOT / ".agents/skills/carr-before-send/SKILL.md", skill)
        cwd = target if nested else root
        prompt = ("This is a synthetic native loading test. Do not run tools or send messages. "
                  "From the instructions already supplied, list every short rule ID you see. "
                  "Also list the available before-send checkpoint skill name.")
        result = subprocess.run(
            ["codex", "exec", "--json", "--sandbox", "read-only", "-C", str(cwd), prompt],
            cwd=cwd, capture_output=True, text=True, timeout=180, env=scrubbed_env())
        if result.returncode:
            raise RuntimeError(f"Codex probe failed: {result.stderr[-1000:]} {result.stdout[-1000:]}")
        events = [json.loads(line) for line in result.stdout.splitlines() if line.startswith("{")]
        messages = "\n".join(e.get("item", {}).get("text", "") for e in events)
        assert "179be4b8" in messages, "CORE start not observed"
        assert "fa217e48" in messages, "CORE end not observed"
        assert ("6da539b5" in messages) == nested, "wrong nested scope"
        assert "carr-before-send" in messages, "checkpoint not listed"
        return {"nested_cwd": nested, "core_observed": True, "path_observed": nested,
                "checkpoint_listed": True, "evidence": "model response, no instruction-load event API"}


if __name__ == "__main__":
    print(json.dumps({"cli": subprocess.check_output(["codex", "--version"], text=True).strip(),
                      "probes": [probe(False), probe(True)]}, indent=2))
