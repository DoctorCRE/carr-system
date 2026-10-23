"""Synthetic capture outages must remain visible without releasing recordings."""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

BIN_DIR = Path(__file__).resolve().parents[1] / "bin"
sys.path.insert(0, str(BIN_DIR))
SPEC = importlib.util.spec_from_file_location("capture_outcomes_bridge", BIN_DIR / "capture-bridge.py")
assert SPEC and SPEC.loader
bridge = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = bridge
SPEC.loader.exec_module(bridge)


class CaptureOutcomesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.session = Path(self.tmp.name) / "synthetic-session"
        self.session.mkdir()
        self.cfg = {"base_url": "https://capture.invalid", "device_id": "synthetic", "token": "fixture"}
        self.log = patch.object(bridge, "LOG_PATH", Path(self.tmp.name) / "capture.log")
        self.log.start()
        (self.session / ".capture.json").write_text(json.dumps({"session_token": "fixture-session"}))
        (self.session / "mic.caf").write_bytes(b"synthetic audio fixture")

    def tearDown(self) -> None:
        self.log.stop()
        self.tmp.cleanup()

    def test_poll_distinguishes_verified_pending_from_unavailable(self) -> None:
        with patch.object(bridge, "http_request", return_value=(200, {"state": "recording", "meeting_record": None})):
            self.assertEqual(bridge.poll_cmd(self.cfg, self.session), 0)
        with patch.object(bridge, "http_request", side_effect=bridge.BridgeNetworkError("synthetic outage")):
            self.assertNotEqual(bridge.poll_cmd(self.cfg, self.session), 0)
        self.assertTrue((self.session / ".capture.json").exists())
        self.assertTrue((self.session / "mic.caf").exists())
        self.assertFalse((self.session / "ingested.json").exists())

    def test_poll_auth_refusal_and_bad_reply_fail_without_releasing_files(self) -> None:
        for reply in ((401, {}), (503, {}), (200, {})):
            with self.subTest(reply=reply), patch.object(bridge, "http_request", return_value=reply):
                self.assertNotEqual(bridge.poll_cmd(self.cfg, self.session), 0)
                self.assertFalse((self.session / "ingested.json").exists())
        self.assertTrue((self.session / "mic.caf").exists())

    def test_claim_outage_remains_retryable_with_same_nonce(self) -> None:
        (self.session / ".capture.json").unlink()
        (self.session / "announcement.json").write_text(json.dumps({"announcement_fired_at": "2026-01-05T12:00:00Z"}))
        (self.session / "meta.json").write_text(json.dumps({"started": "2026-01-05T12:00:00Z"}))
        (self.session / "call-context.json").write_text(json.dumps({"mode": "other_call"}))
        claims = []

        def request(_method, url, body, _headers):
            if url.endswith("/capture/claim"):
                claims.append(body.copy())
                if len(claims) == 1:
                    raise bridge.BridgeNetworkError("synthetic outage")
                return 200, {"session_token": "fixture-session", "ttl_seconds": 60}
            return 200, {"ok": True}

        with patch.object(bridge, "http_request", side_effect=request):
            self.assertNotEqual(bridge.claim_cmd(self.cfg, self.session), 0)
            self.assertFalse((self.session / ".capture.json").exists())
            self.assertEqual(bridge.claim_cmd(self.cfg, self.session), 0)
            self.assertEqual(bridge.claim_cmd(self.cfg, self.session), 0)
        self.assertEqual(len(claims), 2)
        self.assertEqual(claims[0], claims[1])
        self.assertTrue((self.session / "mic.caf").exists())

    def test_last_verified_poll_does_not_advance_during_outage(self) -> None:
        with patch.object(bridge, "http_request", return_value=(200, {"state": "recording", "meeting_record": None})):
            self.assertEqual(bridge.poll_cmd(self.cfg, self.session), 0)
        health_path = self.session / ".capture-health.json"
        verified = health_path.read_text()
        self.assertIn("last_verified_poll_at", verified)
        with patch.object(bridge, "http_request", side_effect=bridge.BridgeNetworkError("synthetic outage")):
            self.assertNotEqual(bridge.poll_cmd(self.cfg, self.session), 0)
        self.assertEqual(health_path.read_text(), verified)

    def test_missing_config_is_unavailable_not_empty_queue(self) -> None:
        with patch.object(bridge, "load_config", return_value=None):
            self.assertEqual(bridge.main(["capture-bridge.py", "poll", str(self.session)]), 78)

    def test_poll_requires_local_marker_persistence_after_verified_reply(self) -> None:
        (self.session / "ingested.json").mkdir()
        with patch.object(bridge, "http_request", return_value=(200, {"state": "done", "meeting_record": "synthetic-narrative"})):
            self.assertNotEqual(bridge.poll_cmd(self.cfg, self.session), 0)
        self.assertTrue((self.session / ".capture.json").exists())

    def test_claim_requires_consent_before_any_remote_request(self) -> None:
        (self.session / ".capture.json").unlink()
        with patch.object(bridge, "http_request", side_effect=AssertionError("unconsented claim")):
            self.assertEqual(bridge.claim_cmd(self.cfg, self.session), 0)
        self.assertFalse((self.session / ".capture-request.json").exists())

    def test_status_failure_is_not_success(self) -> None:
        with patch.object(bridge, "http_request", return_value=(503, {})):
            self.assertNotEqual(bridge.status_cmd(self.cfg, self.session, "recording", None), 0)


class CaptureSweepTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.recordings = self.root / "Recordings"
        self.recordings.mkdir()
        self.commands = self.root / "commands"
        self.commands.mkdir()
        self.attempts = self.root / "attempts"
        self.env = {"HOME": str(self.root), "PATH": f"{self.commands}:/usr/bin:/bin", "CARR_RECORDINGS_DIR": str(self.recordings)}
        self.command("python3", f"#!/bin/bash\nprintf '%s\\n' \"$3\" >> '{self.attempts}'\ncase \"$3\" in *first*) exit 1;; esac\n")
        self.command("date", "#!/bin/bash\nif [ \"$1\" = -j ]; then printf '1\\n'; else /bin/date \"$@\"; fi\n")
        self.command("sleep", "#!/bin/bash\nexit 0\n")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def command(self, name: str, text: str) -> None:
        path = self.commands / name
        path.write_text(text)
        path.chmod(0o755)

    def run_script(self, name: str) -> subprocess.CompletedProcess[str]:
        script = BIN_DIR / name
        if name == "consent-watch.sh":
            script = self.root / "tool" / "bin" / name
            script.parent.mkdir(parents=True)
            text = (BIN_DIR / name).read_text()
            text = text.replace('LOG_FILE="/Users/booko/carr-system/out/dictation-consent.log"', 'LOG_FILE="$HOME/Library/Logs/dictation-consent.log"')
            self.assertNotIn("/Users/booko/carr-system", text)
            script.write_text(text)
            assets = script.parent.parent / "assets"
            assets.mkdir()
            (assets / "now-recording.aiff").touch()
        return subprocess.run(["/bin/bash", str(script)], env=self.env, capture_output=True, text=True, timeout=5)

    def session(self, name: str, marker: str) -> Path:
        session = self.recordings / name
        session.mkdir()
        (session / marker).write_text("{}")
        return session

    def test_poll_sweep_reports_failed_child_and_continues_siblings(self) -> None:
        for name in ("first", "second"):
            self.session(name, ".capture.json")
        result = self.run_script("capture-poll.sh")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(len(self.attempts.read_text().splitlines()), 2)

    def test_empty_poll_sweep_succeeds(self) -> None:
        self.assertEqual(self.run_script("capture-poll.sh").returncode, 0)
        self.assertFalse(self.attempts.exists())

    def test_attempted_old_claim_retries_but_untouched_backlog_stays_skipped(self) -> None:
        for name in ("first", "second", "untouched"):
            session = self.session(name, "announcement.json")
            if name != "untouched":
                (session / ".capture-request.json").write_text('{"nonce":"fixture"}')
        result = self.run_script("capture-watch.sh")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual([Path(p).name for p in self.attempts.read_text().splitlines()], ["first", "second"])

    def consent_fixture(self, failure: str) -> None:
        hook = self.root / "bash-startup.sh"
        hook.write_text(
            "function /usr/bin/afplay() {\n"
            f"  printf 'played\\n' >> '{self.attempts}'\n"
            + ("  [ ! -d \"$CARR_RECORDINGS_DIR/first\" ] || return 3\n" if failure == "playback" else "")
            + "  return 0\n}\n"
            "function /usr/bin/osascript() { return 0; }\n"
            "function mv() {\n"
            + ("  case \"$*\" in *first/announcement.json*) return 1;; esac\n" if failure == "marker" else "")
            + "  command mv \"$@\"\n}\n"
        )
        self.env["BASH_ENV"] = str(hook)

    def test_consent_playback_failure_does_not_create_proof(self) -> None:
        self.session("first", "mic.caf")
        self.consent_fixture("playback")
        result = self.run_script("consent-watch.sh")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.recordings / "first" / "announcement.json").exists())

    def test_consent_marker_failure_does_not_suppress_other_sessions(self) -> None:
        for name in ("first", "second"):
            self.session(name, "mic.caf")
        self.consent_fixture("marker")
        result = self.run_script("consent-watch.sh")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.recordings / "first" / "announcement.json").exists())
        self.assertTrue((self.recordings / "second" / "announcement.json").exists())


if __name__ == "__main__":
    unittest.main()
