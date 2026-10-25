#!/usr/bin/env python3
# ci: selftest
"""Hermetic checks for the video asset boundary."""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
env = os.environ.copy()
env["CARR_VAULT"] = "/poison/ambient-drive"


def run(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=REPO, env=env, text=True, capture_output=True)


def run_env(args: list[str], extra: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args, cwd=REPO, env={**env, **extra}, text=True, capture_output=True
    )


listed = run([sys.executable, str(REPO / "video/plan-animated-static.py"), "--list"])
assert listed.returncode == 0 and "concepts:" in listed.stdout

for script in ("build-operatory-math.py", "build-video.py", "make-premiere-cut.py"):
    proc = run([sys.executable, str(REPO / "video" / script)])
    combined = proc.stdout + proc.stderr
    assert proc.returncode != 0 and "normal mode refuses Drive assets" in combined, (script, combined)
    assert "/poison/ambient-drive" not in combined

stock = run(["zsh", str(REPO / "video/make-stock-clip.sh")])
assert stock.returncode != 0 and "normal mode refuses Drive assets" in (stock.stdout + stock.stderr)

recovery = run([
    sys.executable, str(REPO / "video/recovery-asset-root.py"),
    "--recovery", "--reason", "asset restore drill", "--vault", "/tmp/recovery-vault",
])
assert recovery.returncode == 0
assert recovery.stdout.strip() == "/tmp/recovery-vault/Marketing/Brand Assets"
assert "NONCANONICAL" in recovery.stderr and "asset restore drill" in recovery.stderr

with tempfile.TemporaryDirectory() as tmp:
    layer_dir = Path(tmp) / "layers"
    layers = run([sys.executable, str(REPO / "video/make-example-layers.py"), str(layer_dir)])
    assert layers.returncode == 0, layers.stderr
    assert (layer_dir / "08_logo_scale.png").is_file()

    log = Path(tmp) / "choreography.tsv"
    job = Path(tmp) / "job.json"
    plan = Path(tmp) / "plan.env"
    recovery_controls = ["--recovery", "--reason", "asset restore drill", "--vault", str(Path(tmp) / "vault")]
    initial = run([
        sys.executable, str(REPO / "video/plan-animated-static.py"), str(layer_dir), str(log),
        "--name", "contract-proof", "--json-out", str(job), "--plan-out", str(plan),
        *recovery_controls,
    ])
    assert initial.returncode == 0 and job.is_file() and plan.is_file(), initial.stderr
    values = dict(re.findall(r'^(\w+)="(.*)"$', plan.read_text(), re.MULTILINE))
    commit = run([
        sys.executable, str(REPO / "video/plan-animated-static.py"), str(layer_dir), str(log),
        "--name", "contract-proof", "--concept", values["CONCEPT"], "--sfx", values["SFX_KEY"],
        "--commit", *recovery_controls,
    ])
    assert commit.returncode == 0 and "contract-proof" in log.read_text(), commit.stderr
    assert "NONCANONICAL" in initial.stderr and "NONCANONICAL" in commit.stderr

with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    layer_dir = root / "layers"
    layer_dir.mkdir()
    fake_sfx = root / "landing.wav"
    fake_sfx.write_bytes(b"sound")
    calls_path = root / "planner-calls.jsonl"
    fake_planner = root / "planner.py"
    fake_planner.write_text(
        """#!/usr/bin/env python3
import json, os, sys
from pathlib import Path

args = sys.argv[1:]
with Path(os.environ["CARR_FAKE_CALLS"]).open("a") as capture:
    capture.write(json.dumps(args) + "\\n")
if "--commit" in args:
    if os.environ.get("CARR_FAKE_PLANNER_FAIL_COMMIT") == "1":
        raise SystemExit(17)
    with Path(args[1]).open("a") as log:
        log.write("2026-08-20\\ttransaction-proof\\treading-order\\ttick-ui-soft\\n")
    raise SystemExit(0)
plan = Path(args[args.index("--plan-out") + 1])
job = Path(args[args.index("--json-out") + 1])
job.write_text("{}")
plan.write_text(
    'CONCEPT="reading-order"\\n'
    'SFX_KEY="tick-ui-soft"\\n'
    f'SFX_FILE="{os.environ["CARR_FAKE_SFX"]}"\\n'
    'SFX_VOL="0.3"\\n'
    'DUR="1"\\n'
    'LANDINGS="0.1"\\n'
    'CANVAS="1x1"\\n'
    'HERO="headline"\\n'
)
"""
    )
    fake_osascript = root / "osascript"
    fake_osascript.write_text(
        """#!/bin/zsh
[ "${CARR_FAKE_OSASCRIPT_FAIL:-0}" -ne 1 ] || exit 19
print -r -- rendered > "$CARR_ANIMSTATIC_RAW"
print -r -- project > "$CARR_ANIMSTATIC_AEP"
"""
    )
    fake_ffmpeg = root / "ffmpeg"
    fake_ffmpeg.write_text(
        """#!/usr/bin/env python3
import os, sys
from pathlib import Path
if os.environ.get("CARR_FAKE_FFMPEG_FAIL") == "1":
    raise SystemExit(23)
Path(sys.argv[-1]).write_bytes(b"rendered")
"""
    )
    fake_osascript.chmod(0o755)
    fake_ffmpeg.chmod(0o755)
    recovery_controls = [
        "--recovery", "--reason", "asset restore drill", "--vault", str(root / "vault")
    ]

    def wrapper_run(pipe: Path, extra: dict[str, str]) -> subprocess.CompletedProcess[str]:
        return run_env(
            [
                "zsh", str(REPO / "video/make-animated-static.sh"), str(layer_dir),
                "transaction-proof", "--email", *recovery_controls,
            ],
            {
                "CARR_VIDEO_PIPE": str(pipe),
                "CARR_ANIMSTATIC_PLANNER": str(fake_planner),
                "CARR_OSASCRIPT": str(fake_osascript),
                "CARR_FFMPEG": str(fake_ffmpeg),
                "CARR_FAKE_CALLS": str(calls_path),
                "CARR_FAKE_SFX": str(fake_sfx),
                **extra,
            },
        )

    def assert_no_delivery(pipe: Path) -> None:
        assert not (pipe / "03_Output/transaction-proof.mp4").exists()
        assert not (pipe / "03_Output/transaction-proof.gif").exists()
        assert not (pipe / "03_Output/transaction-proof_email.gif").exists()
        assert not (pipe / "AE_Templates/AnimatedStatic_last_generated.aep").exists()
        assert not (pipe / "choreography-log.tsv").exists()
        workspaces = list((pipe / "Scripts").glob(".animstatic.transaction-proof.*"))
        assert not workspaces, workspaces
        assert not (pipe / "Scripts/animstatic-job.json").exists()
        assert not (pipe / "Scripts/animstatic-plan.env").exists()

    early_pipe = root / "early-pipe"
    early = wrapper_run(early_pipe, {"CARR_FAKE_OSASCRIPT_FAIL": "1"})
    assert early.returncode != 0 and "OK:" not in early.stdout, (early.stdout, early.stderr)
    assert_no_delivery(early_pipe)

    commit_pipe = root / "commit-pipe"
    late = wrapper_run(commit_pipe, {"CARR_FAKE_PLANNER_FAIL_COMMIT": "1"})
    assert late.returncode != 0 and "choreography commit failed" in late.stderr
    assert "OK:" not in late.stdout
    assert_no_delivery(commit_pipe)

    success_pipe = root / "success-pipe"
    good = wrapper_run(success_pipe, {})
    assert good.returncode == 0 and good.stdout.count("OK:") == 3, (good.stdout, good.stderr)
    assert (success_pipe / "03_Output/transaction-proof.mp4").read_bytes() == b"rendered"
    assert (success_pipe / "03_Output/transaction-proof.gif").read_bytes() == b"rendered"
    assert (success_pipe / "03_Output/transaction-proof_email.gif").read_bytes() == b"rendered"
    assert (success_pipe / "AE_Templates/AnimatedStatic_last_generated.aep").read_bytes() == b"project\n"
    assert "transaction-proof" in (success_pipe / "choreography-log.tsv").read_text()
    assert not list((success_pipe / "Scripts").glob(".animstatic.transaction-proof.*"))
    planner_calls = [json.loads(line) for line in calls_path.read_text().splitlines()]
    successful_calls = planner_calls[-2:]
    assert len(successful_calls) == 2
    assert all(call[-len(recovery_controls):] == recovery_controls for call in successful_calls)

wrapper = (REPO / "video/make-animated-static.sh").read_text()
assert '"${RECOVERY_ARGS[@]}"' in wrapper
assert "choreography commit failed; removed uncommitted outputs" in wrapper
commit_boundary = wrapper.index("if ! python3 \"$PLANNER\"")
assert 'echo "OK:' not in wrapper[:commit_boundary]

inventory = json.loads((REPO / "ops/config/drive-dependencies.v1.json").read_text())
rows = {row["id"]: row for row in inventory["entries"]}


def _normal_path(entry):
    """What the registry says a Drive dependency's NORMAL path now does.

    Accepting an entry for retirement sets replacement.status to "accepted"
    and moves the descriptive value to replacement.normal_path, because one
    field cannot carry both "what the normal path does" and "has Joe accepted
    this for retirement". Reading both keys keeps these assertions true either
    side of an acceptance, rather than passing only while the entry is
    un-accepted and raising KeyError the day it is.
    """
    replacement = entry["replacement"]
    return replacement.get("normal_path", replacement.get("status"))

assert _normal_path(rows["normal-video-assets"]) == "normal_path_uses_versioned_brand_assets_and_refuses_missing_media_seam"

print("video asset selftest passed")
