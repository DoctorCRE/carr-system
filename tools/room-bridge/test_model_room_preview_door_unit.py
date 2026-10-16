"""The visual gate harness is the door for the Model Room preview server.

No browser is launched: every capture here passes a path that is not a browser,
so the harness takes its browser_unavailable branch. What is proven is the
plumbing around the browser, on real loopback sockets.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))

import browser_visual_gate_capture as capture  # noqa: E402
import design_kernel  # noqa: E402

KERNEL = ROOT / "design" / "carr-design-kernel.v1.json"
DIGEST = "sha256:7ef1df546040c0aa000a437902fdc4d83decaf1960f63001790b11a422b5dfb9"
FAILURES: list[str] = []


def args(*, artifact: Path | None = None, model_room: bool = False) -> argparse.Namespace:
    return argparse.Namespace(
        artifact=artifact, model_room=model_room, out=None, kernel=KERNEL,
        intent="intent:job-passport", surface_family="model-room", work_request_id="wr-synthetic-read-only",
        projection_digest=DIGEST, adapter_id="adapter:job-passport-html",
        report_id="report:model-room-preview-door", rtl="not_applicable", chrome="/not/a/browser",
    )


def refused(url: str) -> bool:
    try:
        urlopen(url, timeout=2)
    except URLError:
        return True
    return False


def test_model_room_mode_serves_the_real_bundle_and_the_wire_turn():
    with capture.serving_target(args(model_room=True)) as url:
        match = re.fullmatch(r"http://127\.0\.0\.1:(\d+)/room\.html", url)
        assert match, url
        assert int(match.group(1)) not in (0, 8765), url
        page = urlopen(url, timeout=5).read().decode()
        assert "<title>Observatory · The Deal Room</title>" in page
        wire = json.loads(urlopen(url.replace("/room.html", "/api/room/turns"), timeout=30).read())
    assert wire["latest_seq"] == "1"
    assert [turn["kind"] for turn in wire["turns"]] == ["receipt"]
    assert json.loads(wire["turns"][0]["body"])["job_passport"]["payload"]["projection_digest"] == DIGEST
    assert refused(url), "preview server still answering after the capture"


def test_artifact_mode_still_serves_the_rendered_artifact():
    with tempfile.TemporaryDirectory() as directory:
        artifact = Path(directory) / "passport.html"
        artifact.write_text("<!doctype html><button>gate</button>")
        with capture.serving_target(args(artifact=artifact)) as url:
            assert re.fullmatch(r"http://127\.0\.0\.1:\d+/artifact\.html", url), url
            assert urlopen(url, timeout=5).read() == b"<!doctype html><button>gate</button>"
    assert refused(url), "artifact server still answering after the capture"


def test_server_is_torn_down_when_the_capture_raises():
    try:
        with capture.serving_target(args(model_room=True)) as url:
            raise RuntimeError("capture aborted")
    except RuntimeError:
        pass
    assert refused(url), "preview server leaked past a failed capture"


def test_model_room_cli_without_a_browser_writes_a_typed_unavailable_report():
    with tempfile.TemporaryDirectory() as directory:
        out = Path(directory) / "report.json"
        run = subprocess.run(
            [sys.executable, str(HERE / "browser_visual_gate_capture.py"), "--model-room", "--chrome", "/not/a/browser",
             "--out", str(out), "--work-request-id", "wr-synthetic-read-only", "--projection-digest", DIGEST],
            capture_output=True, text=True, timeout=60)
        assert run.returncode == 0, run.stderr
        report = json.loads(out.read_text())
    assert design_kernel.validate_visual_gate_report(report, json.loads(KERNEL.read_text())) == report
    assert report["evidence"]["runner"] == "browser_unavailable"
    assert report["evidence"]["refs"] == ["browser_unavailable:chrome_not_found"]
    assert report["admission"]["state"] == "not_admitted"


def test_model_room_and_artifact_are_exclusive():
    run = subprocess.run(
        [sys.executable, str(HERE / "browser_visual_gate_capture.py"), "--model-room", "--artifact", "x.html",
         "--out", "unused.json", "--work-request-id", "wr", "--projection-digest", DIGEST],
        capture_output=True, text=True, timeout=60)
    assert run.returncode == 2
    assert "not allowed with argument" in run.stderr


def check(name, fn):
    try:
        fn()
        print(f"ok  {name}")
    except Exception as exc:  # noqa: BLE001 - report every check
        FAILURES.append(name)
        print(f"FAIL  {name}: {type(exc).__name__}: {exc}")


def main() -> int:
    checks = [
        ("model room mode serves the real bundle and the wire turn", test_model_room_mode_serves_the_real_bundle_and_the_wire_turn),
        ("artifact mode still serves the rendered artifact", test_artifact_mode_still_serves_the_rendered_artifact),
        ("server is torn down when the capture raises", test_server_is_torn_down_when_the_capture_raises),
        ("model room CLI without a browser writes a typed unavailable report", test_model_room_cli_without_a_browser_writes_a_typed_unavailable_report),
        ("model room and artifact are exclusive", test_model_room_and_artifact_are_exclusive),
    ]
    for name, fn in checks:
        check(name, fn)
    print(f"model-room-preview-door unit: {len(checks) - len(FAILURES)}/{len(checks)} passed")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    raise SystemExit(main())
