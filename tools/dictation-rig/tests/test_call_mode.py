"""Offline regression tests for Deal Room Call Mode.

They exercise only filesystem-based state and context contracts.  Quill and
macOS accessibility are intentionally never invoked here.
"""

from __future__ import annotations

import importlib.util
import io
import json
import os
import stat
import shlex
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
from email.message import Message
from pathlib import Path
from unittest.mock import Mock, patch
from types import SimpleNamespace
from typesafe_sdk import TypeSafeError


BIN_DIR = Path(__file__).resolve().parents[1] / "bin"
sys.path.insert(0, str(BIN_DIR))
import post_call_jev as runtime_jev


def load_module(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, BIN_DIR / filename)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


call_mode = load_module("call_mode_under_test", "call-mode.py")
transcribe_session = load_module("transcribe_session_under_test", "transcribe_session.py")
post_call = load_module("post_call_under_test", "post_call.py")
capture_bridge = load_module("capture_bridge_under_test", "capture-bridge.py")
post_call_jev = load_module("post_call_jev_under_test", "post_call_jev.py")


_OFFLINE_CLIENT = Mock()
_OFFLINE_CLIENT.system_one.side_effect = TypeSafeError("live SDK refused inside the offline suite")
_OFFLINE_CLIENT.__enter__ = Mock(return_value=_OFFLINE_CLIENT)
_OFFLINE_CLIENT.__exit__ = Mock(return_value=False)
_CLIENT_PATCHES = (
    patch.object(post_call_jev, "TypeSafeClient", return_value=_OFFLINE_CLIENT, create=True),
    patch.object(runtime_jev, "TypeSafeClient", return_value=_OFFLINE_CLIENT, create=True),
)


def setUpModule() -> None:
    # Stop before provider selection, credential reads, or network transport.
    # Injected ask functions and loopback distiller fakes still run normally.
    for client_patch in _CLIENT_PATCHES:
        client_patch.start()


def tearDownModule() -> None:
    for client_patch in reversed(_CLIENT_PATCHES):
        client_patch.stop()


class CallModeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.recordings = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def session(self, name: str) -> Path:
        path = self.recordings / name
        path.mkdir()
        return path

    def state(self) -> dict[str, object]:
        with patch.object(call_mode, "partner_from_device", return_value="Joe"):
            return call_mode.current_state(self.recordings)

    def test_weekly_deal_labels_pair_joe_and_dell(self) -> None:
        self.assertEqual(call_mode.labels_for("weekly_deal_call", "Joe"), ("Joe", "Dell"))
        self.assertEqual(call_mode.labels_for("weekly_deal_call", "Dell"), ("Dell", "Joe"))

    def test_other_call_has_generic_other_participant_label(self) -> None:
        self.assertEqual(
            call_mode.labels_for("other_call", "Joe"), ("Joe", "Other participant")
        )
        self.assertEqual(
            call_mode.labels_for("weekly_deal_call", None), ("Me", "Other participant")
        )

    def test_write_call_context_persists_mode_recorder_and_channel_labels(self) -> None:
        session = self.session("weekly")
        context = call_mode.write_call_context(session, "weekly_deal_call", "Joe")

        persisted = json.loads((session / call_mode.CONTEXT_FILE).read_text())
        self.assertEqual(context, persisted)
        self.assertEqual(persisted["mode"], "weekly_deal_call")
        self.assertEqual(persisted["recorder"], "Joe")
        self.assertEqual(persisted["speaker_labels"], {"mic": "Joe", "system": "Dell"})
        self.assertEqual(
            persisted["speaker_method"], "separate audio channels; no third-party voiceprint"
        )
        self.assertRegex(persisted["started_at"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
        self.assertEqual(stat.S_IMODE((session / call_mode.CONTEXT_FILE).stat().st_mode), 0o600)

    def test_start_and_stop_drive_only_the_existing_quill_menu_contract(self) -> None:
        session = self.recordings / "started-by-quill"

        def quill_action(title: str) -> None:
            if title == "Start recording":
                session.mkdir()
                (session / "mic.caf").touch()
            elif title == "Stop recording":
                (session / "meta.json").write_text("{}")

        with (
            patch.object(call_mode, "LOCK_PATH", self.recordings / "operation.lock"),
            patch.object(call_mode, "run_quill_menu_item", side_effect=quill_action) as menu,
            patch.object(call_mode, "partner_from_device", return_value="Joe"),
        ):
            started = call_mode.start_recording("weekly_deal_call", self.recordings)
            stopped = call_mode.stop_recording(self.recordings)

        self.assertEqual(started["state"], "recording")
        self.assertEqual(stopped["state"], "transcribing")
        self.assertEqual(
            [call.args[0] for call in menu.call_args_list],
            ["Start recording", "Stop recording"],
        )

    def test_current_state_detects_recording(self) -> None:
        session = self.session("recording")
        (session / "mic.caf").touch()
        call_mode.write_call_context(session, "weekly_deal_call", "Joe")

        result = self.state()

        self.assertEqual(result["state"], "recording")
        self.assertEqual(result["session"], "recording")
        self.assertEqual(result["mode"], "weekly_deal_call")
        self.assertEqual(result["speaker_labels"], {"mic": "Joe", "system": "Dell"})

    def test_current_state_detects_transcribing_ready_to_extract_and_filed(self) -> None:
        session = self.session("terminal")
        (session / "meta.json").write_text("{}")
        call_mode.write_call_context(session, "other_call", "Joe")

        self.assertEqual(self.state()["state"], "transcribing")
        (session / "transcript.json").write_text("{}")
        self.assertEqual(self.state()["state"], "ready_to_extract")
        (session / "ingested.json").write_text("{}")
        self.assertEqual(self.state()["state"], "filed")

    def test_stale_partial_session_is_not_reported_as_recording(self) -> None:
        session = self.session("stale-partial")
        track = session / "mic.caf"
        track.touch()
        stale = time.time() - call_mode.ACTIVE_FILE_WINDOW_SECONDS - 1
        import os

        os.utime(track, (stale, stale))
        self.assertEqual(self.state()["state"], "state_unknown")

    def test_current_state_is_idle_without_sessions_or_when_newest_session_is_stale(self) -> None:
        self.assertEqual(self.state(), {"state": "idle", "local_partner": "Joe"})

        session = self.session("old-terminal")
        (session / "meta.json").write_text("{}")
        stale = time.time() - call_mode.TERMINAL_AGE_SECONDS - 1
        session.touch()
        import os

        os.utime(session, (stale, stale))
        self.assertEqual(self.state(), {"state": "idle", "local_partner": "Joe"})


class SpeakerLabelTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.session = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_speaker_labels_fall_back_for_missing_malformed_or_invalid_context(self) -> None:
        expected = {"mic": "Me", "system": "Other participant"}
        self.assertEqual(transcribe_session.read_speaker_labels(self.session), expected)

        (self.session / "call-context.json").write_text("not json")
        self.assertEqual(transcribe_session.read_speaker_labels(self.session), expected)

        (self.session / "call-context.json").write_text(
            json.dumps({"speaker_labels": {"mic": "  ", "system": "x" * 81}})
        )
        self.assertEqual(transcribe_session.read_speaker_labels(self.session), expected)

    def test_speaker_labels_override_defaults_per_valid_channel_and_trim_whitespace(self) -> None:
        (self.session / "call-context.json").write_text(
            json.dumps({"speaker_labels": {"mic": " Joe ", "system": "Dell"}})
        )
        self.assertEqual(
            transcribe_session.read_speaker_labels(self.session),
            {"mic": "Joe", "system": "Dell"},
        )

        (self.session / "call-context.json").write_text(
            json.dumps({"speaker_labels": {"mic": "Dell", "system": 4}})
        )
        self.assertEqual(
            transcribe_session.read_speaker_labels(self.session),
            {"mic": "Dell", "system": "Other participant"},
        )

    def test_transcript_outputs_are_private_to_the_local_user(self) -> None:
        transcript = {
            "session": "test",
            "engine": "test",
            "model": "test",
            "vocab_prompt": False,
            "generated": "2026-08-10T00:00:00Z",
            "consent": None,
            "speaker_labels": {"mic": "Joe", "system": "Dell"},
            "speaker_method": "separate audio channels; no third-party voiceprint",
            "segments": [],
        }
        transcribe_session.write_outputs(self.session, transcript)
        self.assertEqual(
            stat.S_IMODE((self.session / "transcript.json").stat().st_mode), 0o600
        )
        self.assertFalse((self.session / "transcript.md").exists())


class CallModeHttpTests(unittest.TestCase):
    def handler(self, path: str, body: bytes, origin: str | None):
        handler = object.__new__(call_mode.CallModeHandler)
        headers = Message()
        headers["Content-Type"] = "application/json"
        headers["Content-Length"] = str(len(body))
        headers[call_mode.POST_HEADER] = call_mode.POST_HEADER_VALUE
        if origin:
            headers["Origin"] = origin
        handler.headers = headers
        handler.path = path
        handler.rfile = io.BytesIO(body)
        handler.send_json = Mock()
        return handler

    def test_post_requires_an_allowlisted_browser_origin(self) -> None:
        handler = self.handler("/api/stop", b"{}", None)
        handler.do_POST()
        handler.send_json.assert_called_once_with({"error": "origin_not_allowed"}, 403)

    def test_allowed_origin_can_stop_without_invoking_real_quill(self) -> None:
        handler = self.handler("/api/stop", b"{}", "http://127.0.0.1:4682")
        with patch.object(call_mode, "stop_recording", return_value={"state": "idle"}) as stop:
            handler.do_POST()
        handler.send_json.assert_called_once_with({"state": "idle"})
        stop.assert_called_once_with()

    def test_start_requires_explicit_consent_even_from_allowed_origin(self) -> None:
        handler = self.handler(
            "/api/start",
            b'{"mode":"weekly_deal_call"}',
            "https://dealroom.doctorcre.com",
        )
        with patch.object(call_mode, "start_recording") as start:
            handler.do_POST()
        handler.send_json.assert_called_once_with(
            {"error": "confirm that everyone has been told before recording"}, 409
        )
        start.assert_not_called()

    def test_app_doctorcre_origin_is_allowed_alongside_dealroom(self) -> None:
        # Partners open the Deal Room at app.doctorcre.com; the record layer's
        # own host keeps working. The explicitly bound staging Worker origin
        # also needs the same exact-origin browser bridge for V1 qualification.
        self.assertIn("https://app.doctorcre.com", call_mode.ALLOWED_ORIGINS)
        self.assertIn("https://dealroom.doctorcre.com", call_mode.ALLOWED_ORIGINS)
        self.assertIn(
            "https://doctorcre-app-staging.joe-bookout-carr-us.workers.dev",
            call_mode.ALLOWED_ORIGINS,
        )
        for origin in (
            "https://app.doctorcre.com",
            "https://dealroom.doctorcre.com",
            "https://doctorcre-app-staging.joe-bookout-carr-us.workers.dev",
        ):
            handler = self.handler("/api/stop", b"{}", origin)
            with patch.object(call_mode, "stop_recording", return_value={"state": "idle"}) as stop:
                handler.do_POST()
            handler.send_json.assert_called_once_with({"state": "idle"})
            stop.assert_called_once_with()

    def test_staging_origin_preflight_and_state_read_carry_exact_cors_headers(self) -> None:
        origin = "https://doctorcre-app-staging.joe-bookout-carr-us.workers.dev"
        headers: list[tuple[str, str]] = []
        handler = object.__new__(call_mode.CallModeHandler)
        message = Message()
        message["Origin"] = origin
        message["Access-Control-Request-Method"] = "POST"
        message["Access-Control-Request-Headers"] = "content-type, x-carr-call-mode"
        handler.headers = message
        handler.path = "/api/state"
        handler.send_response = Mock()
        handler.send_header = lambda name, value: headers.append((name, value))
        handler.end_headers = Mock()

        handler.do_OPTIONS()

        handler.send_response.assert_called_once_with(204)
        self.assertIn(("Access-Control-Allow-Origin", origin), headers)
        self.assertIn(("Access-Control-Allow-Methods", "GET, POST, OPTIONS"), headers)
        self.assertIn(("Access-Control-Allow-Headers", "Content-Type, X-CARR-Call-Mode"), headers)
        self.assertIn(("Access-Control-Allow-Private-Network", "true"), headers)

        headers.clear()
        handler.wfile = io.BytesIO()
        with patch.object(call_mode, "current_state", return_value={"state": "idle"}):
            handler.do_GET()
        self.assertIn(("Access-Control-Allow-Origin", origin), headers)
        self.assertIn(("Access-Control-Allow-Private-Network", "true"), headers)

    def test_unapproved_staging_lookalikes_and_arbitrary_origins_fail_preflight(self) -> None:
        for origin in (
            "http://doctorcre-app-staging.joe-bookout-carr-us.workers.dev",
            "https://doctorcre-app-staging.joe-bookout-carr-us.workers.dev:8443",
            "https://doctorcre-app-staging.joe-bookout-carr-us.workers.dev.evil.test",
            "https://elsewhere.test",
        ):
            handler = object.__new__(call_mode.CallModeHandler)
            headers = Message()
            headers["Origin"] = origin
            handler.headers = headers
            handler.path = "/api/state"
            handler.send_json = Mock()
            handler.wfile = io.BytesIO()
            handler.do_OPTIONS()
            handler.send_json.assert_called_once_with({"error": "origin_not_allowed"}, 403)

    def test_staging_origin_does_not_bypass_call_mode_header_or_consent(self) -> None:
        origin = "https://doctorcre-app-staging.joe-bookout-carr-us.workers.dev"
        body = b'{"mode":"weekly_deal_call","consent_confirmed":true}'
        handler = self.handler("/api/start", body, origin)
        handler.headers.replace_header(call_mode.POST_HEADER, "wrong")
        with patch.object(call_mode, "start_recording") as start:
            handler.do_POST()
        handler.send_json.assert_called_once_with({"error": "call_mode_header_required"}, 403)
        start.assert_not_called()

        handler = self.handler("/api/start", b'{"mode":"weekly_deal_call"}', origin)
        with patch.object(call_mode, "start_recording") as start:
            handler.do_POST()
        handler.send_json.assert_called_once_with(
            {"error": "confirm that everyone has been told before recording"}, 409
        )
        start.assert_not_called()

    def test_lookalike_origins_are_refused(self) -> None:
        for origin in (
            "http://app.doctorcre.com",
            "https://app.doctorcre.com.evil.test",
            "https://evil.doctorcre.com",
            "https://doctorcre.com",
            "https://app.doctorcre.com:8443",
        ):
            handler = self.handler("/api/start", b'{"mode":"weekly_deal_call","consent_confirmed":true}', origin)
            with patch.object(call_mode, "start_recording") as start:
                handler.do_POST()
            handler.send_json.assert_called_once_with({"error": "origin_not_allowed"}, 403)
            start.assert_not_called()

    def test_app_origin_preflight_and_state_read_carry_cors_for_that_origin_only(self) -> None:
        headers: list[tuple[str, str]] = []
        handler = object.__new__(call_mode.CallModeHandler)
        message = Message()
        message["Origin"] = "https://app.doctorcre.com"
        handler.headers = message
        handler.path = "/api/state"
        handler.send_response = Mock()
        handler.send_header = lambda name, value: headers.append((name, value))
        handler.end_headers = Mock()
        handler.wfile = io.BytesIO()
        handler.do_OPTIONS()
        handler.send_response.assert_called_once_with(204)
        self.assertIn(("Access-Control-Allow-Origin", "https://app.doctorcre.com"), headers)
        self.assertIn(("Access-Control-Allow-Private-Network", "true"), headers)
        headers.clear()
        with patch.object(call_mode, "current_state", return_value={"state": "idle"}):
            handler.do_GET()
        self.assertIn(("Access-Control-Allow-Origin", "https://app.doctorcre.com"), headers)

        message.replace_header("Origin", "https://elsewhere.test")
        headers.clear()
        with patch.object(call_mode, "current_state", return_value={"state": "idle"}):
            handler.do_GET()
        self.assertFalse(any(name == "Access-Control-Allow-Origin" for name, _ in headers))

    def test_post_call_report_requires_origin_and_non_simple_header(self) -> None:
        handler = self.handler("/api/post-call?session=s1", b"", None)
        handler.do_GET()
        handler.send_json.assert_called_once_with({"error": "origin_not_allowed"}, 403)

    def test_call_context_is_stored_and_draft_route_syncs_before_local_creation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            recordings = Path(tmp)
            session = recordings / "s1"
            session.mkdir()
            context = {
                "session": "s1", "workspace_kind": "team", "generated_at": "now", "deals": [],
            }
            body = json.dumps(context).encode()
            handler = self.handler("/api/call-context", body, "https://dealroom.doctorcre.com")
            with patch.object(call_mode, "RECORDINGS", recordings):
                handler.do_POST()
            handler.send_json.assert_called_once_with({"ok": True, "session": "s1"})
            self.assertEqual(stat.S_IMODE((session / post_call.CONTEXT_FILE).stat().st_mode), 0o600)

            draft_body = json.dumps({"session": "s1", "approved_content_hash": "a" * 64}).encode()
            draft = self.handler(
                "/api/post-call/drafts/draft-1/create", draft_body,
                "https://dealroom.doctorcre.com",
            )
            with (
                patch.object(call_mode, "RECORDINGS", recordings),
                patch.object(call_mode, "sync_post_call", return_value={"status": {"state": "ready_review"}}) as sync,
                patch.object(call_mode.post_call, "create_outlook_draft", return_value={"draft_id": "draft-1", "idempotent": False}) as create,
            ):
                draft.do_POST()
            sync.assert_called_once_with(session.resolve())
            create.assert_called_once_with(session.resolve(), "draft-1", "a" * 64)
            draft.send_json.assert_called_once_with({"draft_id": "draft-1", "idempotent": False})

    def test_context_body_over_limit_is_rejected_not_truncated(self) -> None:
        handler = self.handler(
            "/api/call-context", b"{}", "https://dealroom.doctorcre.com",
        )
        handler.headers.replace_header("Content-Length", str(call_mode.CONTEXT_BODY_LIMIT + 1))
        handler.do_POST()
        handler.send_json.assert_called_once_with({"error": "request body too large"}, 409)


class PostCallTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.session = Path(self.tmp.name) / "2026.08.10-1234"
        self.session.mkdir()
        self.context = {
            "session": self.session.name,
            "workspace_kind": "deal_room",
            "generated_at": "2026-08-10T12:34:00Z",
            "deals": [
                {"id": "deal-a", "name": "A", "owner": "Joe", "operating_state": "active", "participants": [
                    {"party_id": "p-1", "ref": "party/p-1", "name": "Vendor A", "email": "a@example.test", "role": "vendor"}
                ]},
                {"id": "deal-b", "name": "B", "owner": "Dell", "operating_state": "active", "participants": [
                    {"party_id": "p-1", "ref": "party/p-1", "name": "Vendor A", "email": "a@example.test", "role": "vendor"}
                ]},
            ],
        }
        (self.session / "transcript.json").write_text(json.dumps({"segments": [{"speaker": "Joe", "text": "Call Vendor A"}]}))

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def output(self, **overrides: object) -> dict[str, object]:
        result: dict[str, object] = {
            "schema_version": 1,
            "report": {"summary": "Summary", "decisions": [], "open_questions": []},
            "joe_tasks": [{"title": "Call vendor", "deal_id": "deal-a", "participant_ids": ["p-1"], "evidence": "Joe agreed to call."}],
            "dell_tasks": [],
            "deal_updates": [{"deal_id": "deal-a", "summary": "Vendor follow-up", "participant_ids": ["p-1"], "evidence": "Vendor requested a follow-up."}],
            "draft_proposals": [{"recipient_party_id": "p-1", "deal_id": "deal-a", "subject": "Next steps", "body": "Thank you.", "participant_ids": ["p-1"], "evidence": "Vendor requested a follow-up."}],
            "review_questions": [],
        }
        result.update(overrides)
        return result

    def test_context_allows_same_party_on_different_deals_but_exact_output_attaches_to_its_deal(self) -> None:
        post_call.validate_context(self.context, self.session.name)
        bad = self.output(joe_tasks=[{"title": "Wrong", "deal_id": "deal-a", "participant_ids": ["missing"], "evidence": "One two."}])
        normalized = post_call.normalize_distillation(bad, self.context, self.session.name)
        self.assertEqual(normalized["joe_tasks"], [])
        self.assertIn("unknown or ambiguous participant_ids", normalized["review_questions"][0]["question"])

    def test_record_layer_context_is_stored_instead_of_stalling_in_awaiting_context(self) -> None:
        # The 2026-08-17 stall, reproduced with the shapes get-call-context
        # really returns (measured live 2026-09-23: 28 of 50 active deals had
        # owner null; 22 of 23 participant rows were partner "lead" rows with
        # party_id/ref/name/email null; the one party row had email null).
        live = {**self.context, "deals": [
            {"id": "deal-a", "name": "A", "owner": None, "operating_state": "active", "participants": [
                {"party_id": None, "ref": None, "name": None, "email": None, "role": "lead"},
            ]},
            {"id": "deal-b", "name": "B", "owner": "dell", "operating_state": "active", "participants": [
                {"party_id": None, "ref": None, "name": None, "email": None, "role": "lead"},
                {"party_id": "p-1", "ref": "P-0001", "name": "Vendor A", "email": None, "role": "listing_side"},
                {"party_id": "p-1", "ref": "P-0001", "name": "Vendor A", "email": None, "role": "vendor"},
            ]},
        ]}
        # The strict contract alone refuses it: that refusal was the stall.
        with self.assertRaises(post_call.ContractError):
            post_call.validate_context(live, self.session.name)
        stored = post_call.store_context(self.session, live)
        self.assertEqual(stored["deals"][0]["owner"], "")
        self.assertEqual(stored["deals"][0]["participants"], [])
        self.assertEqual(stored["deals"][1]["participants"], [
            {"party_id": "p-1", "ref": "P-0001", "name": "Vendor A", "email": "", "role": "listing_side, vendor"},
        ])
        self.assertIsNotNone(post_call.context_from_session(self.session))
        status = post_call.process_session(self.session, distiller=lambda _request: self.output(
            deal_updates=[], draft_proposals=[], joe_tasks=[],
        ))
        self.assertEqual(status["state"], "ready_review")

    def test_shaping_never_invents_ids_or_accepts_a_half_identified_party(self) -> None:
        half = {**self.context, "deals": [
            {"id": "deal-a", "name": "A", "owner": "joe", "operating_state": "active", "participants": [
                {"party_id": "p-9", "ref": None, "name": "Half", "email": "", "role": "vendor"},
            ]},
        ]}
        with self.assertRaises(post_call.ContractError):
            post_call.store_context(self.session, half)
        extra = {**self.context, "deals": [
            {"id": "deal-a", "name": "A", "owner": None, "operating_state": "active", "participants": [], "notes": "x"},
        ]}
        with self.assertRaises(post_call.ContractError):
            post_call.store_context(self.session, extra)
        self.assertIsNone(post_call.context_from_session(self.session))

    def test_context_that_arrives_after_the_transcript_still_produces_the_review_pack(self) -> None:
        status = post_call.process_session(self.session, distiller=lambda _request: self.output())
        self.assertEqual(status["state"], "awaiting_context")
        self.assertFalse((self.session / post_call.REPORT_FILE).exists())
        post_call.store_context(self.session, self.context)
        status = post_call.process_session(self.session, distiller=lambda _request: self.output())
        self.assertEqual(status["state"], "ready_review")
        report = post_call.report_for_deal_room(self.session)
        self.assertEqual(report["status"]["state"], "ready_review")
        self.assertEqual(len(report["report"]["draft_proposals"]), 1)

    def test_evidence_is_bounded_and_context_is_consumed_after_local_processing(self) -> None:
        bad = self.output(joe_tasks=[{"title": "Too much", "deal_id": "deal-a", "participant_ids": [], "evidence": "one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen"}])
        bounded = post_call.normalize_distillation(bad, self.context, self.session.name)
        self.assertEqual(len(bounded["joe_tasks"][0]["evidence"].split()), 15)

        post_call.store_context(self.session, self.context)
        status = post_call.process_session(self.session, distiller=lambda _request: self.output())
        self.assertEqual(status["state"], "ready_review")
        self.assertIsNone(post_call.context_from_session(self.session))
        for name in (post_call.REPORT_FILE, post_call.STATUS_FILE, post_call.CONTEXT_FILE):
            self.assertEqual(stat.S_IMODE((self.session / name).stat().st_mode), 0o600)

    def test_oversize_outlook_draft_becomes_a_review_question_not_a_dead_candidate(self) -> None:
        oversized = self.output(draft_proposals=[{
            "recipient_party_id": "p-1", "deal_id": "deal-a", "subject": "Next steps",
            "body": "📌" * 1000, "participant_ids": ["p-1"],
            "evidence": "Vendor requested a follow-up.",
        }])
        normalized = post_call.normalize_distillation(oversized, self.context, self.session.name)
        self.assertEqual(normalized["draft_proposals"], [])
        self.assertIn("Outlook draft size limit", normalized["review_questions"][0]["question"])

    def test_sanitized_candidates_never_include_email_body_or_recipient_email(self) -> None:
        post_call.store_context(self.session, self.context)
        post_call.process_session(self.session, distiller=lambda _request: self.output())
        shaped = post_call.sanitized_candidates(self.session)
        raw = json.dumps(shaped)
        self.assertNotIn("Thank you.", raw)
        self.assertNotIn("a@example.test", raw)
        self.assertEqual(
            [item["kind"] for item in shaped["post_call_items"]],
            ["assigned_action", "email_draft"],
        )
        self.assertEqual([item["kind"] for item in shaped["legacy_items"]], ["activity"])
        self.assertEqual(shaped["legacy_items"][0]["payload"]["ref"], "deal-a")
        self.assertIsNone(shaped["post_call_items"][0]["due_on"])
        remote_items = shaped["post_call_items"] + shaped["legacy_items"]
        self.assertLessEqual(max(len(item["evidence_quote"].split()) for item in remote_items), 15)

    def test_explicit_next_step_reaches_review_and_existing_candidate_lane(self) -> None:
        post_call.store_context(self.session, self.context)
        output = self.output(joe_tasks=[], dell_tasks=[], draft_proposals=[], deal_updates=[{
            "deal_id": "deal-a", "summary": "Landlord follow-up agreed.",
            "participant_ids": ["p-1"], "evidence": "Call Vendor A on February 29, 2028.",
            "next_step": {"text": "Call Vendor A", "next_date": "2028-02-29"},
        }])
        status = post_call.process_session(self.session, distiller=lambda _request: output)
        self.assertEqual(status["state"], "ready_review")
        update = post_call.report_for_deal_room(self.session)["report"]["deal_updates"][0]
        self.assertIn("Call Vendor A", update["summary"])
        self.assertIn("2028-02-29", update["summary"])
        shaped = post_call.sanitized_candidates(self.session)
        self.assertEqual(shaped["legacy_items"], [{
            "kind": "activity", "payload": {"ref": "deal-a", "kind": "note", "summary": "Landlord follow-up agreed."},
            "evidence_quote": "Call Vendor A on February 29, 2028.", "confidence": 0.5,
        }, {
            "kind": "next_step", "payload": {
                "deal": "deal-a", "text": "Call Vendor A", "next_date": "2028-02-29",
            }, "evidence_quote": "Call Vendor A on February 29, 2028.", "confidence": 0.5,
        }])
        binding = [item for item in shaped["bindings"] if item["remote"] == "legacy"]
        post_call.apply_candidate_ids(self.session, binding[:1], ["note-candidate"])
        remaining = post_call.sanitized_candidates(self.session)
        self.assertEqual([item["kind"] for item in remaining["legacy_items"]], ["next_step"])
        post_call.apply_candidate_ids(self.session, [item for item in remaining["bindings"] if item["remote"] == "legacy"], ["next-step-candidate"])
        self.assertEqual(post_call.report_for_deal_room(self.session)["report"]["deal_updates"][0]["candidate_status"], "pending")
        self.assertEqual(post_call.sanitized_candidates(self.session)["legacy_items"], [])
        post_call.apply_candidate_statuses(self.session, {"note-candidate": "confirmed"})
        self.assertIsNone(post_call.retention_ready(self.session, "report-hash"))
        post_call.apply_candidate_statuses(self.session, {"next-step-candidate": "skipped"})
        update = post_call.report_for_deal_room(self.session)["report"]["deal_updates"][0]
        self.assertEqual(update["candidate_status"], "confirmed")
        self.assertEqual(update["next_step"]["candidate_status"], "skipped")
        self.assertIsNotNone(post_call.retention_ready(self.session, "report-hash"))

    def test_explicit_phase_move_reaches_review_and_waits_for_its_own_disposition(self) -> None:
        post_call.store_context(self.session, self.context)
        output = self.output(joe_tasks=[], dell_tasks=[], draft_proposals=[], deal_updates=[{
            "deal_id": "deal-a", "summary": "LOI terms agreed.", "participant_ids": [],
            "evidence": "Move the deal to legal.", "phase_move": {"value": "legal"},
        }])
        self.assertEqual(post_call.process_session(self.session, distiller=lambda _request: output)["state"], "ready_review")
        update = post_call.report_for_deal_room(self.session)["report"]["deal_updates"][0]
        self.assertIn("Move phase to: legal", update["summary"])
        shaped = post_call.sanitized_candidates(self.session)
        self.assertEqual(shaped["legacy_items"], [{
            "kind": "activity", "payload": {"ref": "deal-a", "kind": "note", "summary": "LOI terms agreed."},
            "evidence_quote": "Move the deal to legal.", "confidence": 0.5,
        }, {
            "kind": "phase_move", "payload": {"deal": "deal-a", "field": "phase", "value": "legal"},
            "evidence_quote": "Move the deal to legal.", "confidence": 0.5,
        }])
        post_call.apply_candidate_ids(self.session, shaped["bindings"], ["note-candidate", "phase-candidate"])
        self.assertEqual(post_call.sanitized_candidates(self.session)["legacy_items"], [])
        post_call.apply_candidate_statuses(self.session, {"note-candidate": "confirmed"})
        self.assertIsNone(post_call.retention_ready(self.session, "report-hash"))
        post_call.apply_candidate_statuses(self.session, {"phase-candidate": "skipped"})
        self.assertEqual(post_call.report_for_deal_room(self.session)["report"]["deal_updates"][0]["phase_move"]["candidate_status"], "skipped")
        self.assertIsNotNone(post_call.retention_ready(self.session, "report-hash"))

    def test_phase_proposals_reject_unsupported_shapes_and_quarantine_unknown_deals(self) -> None:
        invalid_phases: tuple[object, ...] = ({}, "legal", {"value": "closed"}, {"value": "invoiced"},
                      {"value": "not-a-phase"}, {"value": True}, {"value": []},
                      {"value": "legal", "base_event_id": None})
        for phase in invalid_phases:
            with self.subTest(phase=phase):
                (self.session / post_call.REPORT_FILE).unlink(missing_ok=True)
                post_call.store_context(self.session, self.context)
                output = self.output(deal_updates=[{
                    "deal_id": "deal-a", "summary": "Update", "participant_ids": [],
                    "evidence": "Move the deal to legal", "phase_move": phase,
                }])
                self.assertEqual(post_call.process_session(self.session, distiller=lambda _request: output)["state"], "blocked")
                self.assertFalse((self.session / post_call.REPORT_FILE).exists())
        output = self.output(deal_updates=[{
            "deal_id": "unknown-deal", "summary": "Update", "participant_ids": [],
            "evidence": "Move the deal to legal", "phase_move": {"value": "legal"},
        }])
        self.assertEqual(post_call.process_session(self.session, distiller=lambda _request: output)["state"], "ready_review")
        report = post_call.report_for_deal_room(self.session)["report"]
        self.assertEqual(report["deal_updates"], [])
        self.assertTrue(any("unknown-deal" in row["question"] for row in report["review_questions"]))

    def test_maximum_length_next_step_keeps_activity_summary_unchanged(self) -> None:
        post_call.store_context(self.session, self.context)
        output = self.output(deal_updates=[{
            "deal_id": "deal-a", "summary": "Landlord follow-up agreed.",
            "participant_ids": [], "evidence": "Follow-up agreed",
            "next_step": {"text": "x" * 500, "next_date": None},
        }])
        self.assertEqual(post_call.process_session(self.session, distiller=lambda _request: output)["state"], "ready_review")
        candidates = post_call.sanitized_candidates(self.session)["legacy_items"]
        self.assertEqual(candidates[0]["payload"]["summary"], "Landlord follow-up agreed.")
        self.assertEqual(candidates[1]["payload"]["text"], "x" * 500)
        visible = post_call.report_for_deal_room(self.session)["report"]["deal_updates"][0]
        self.assertIn("clears the current date", visible["summary"])
        self.assertEqual(post_call.report_for_deal_room(self.session)["report"]["deal_updates"][0], visible)

    def test_invalid_next_steps_block_without_creating_a_review_pack(self) -> None:
        invalid_steps: tuple[object, ...] = (
            {}, "Call Vendor A", {"text": "Call Vendor A"},
            {"text": " ", "next_date": None}, {"text": "x" * 501, "next_date": None},
            {"text": "Call Vendor A", "next_date": "2027-02-29"},
            {"text": "Call Vendor A", "next_date": "tomorrow"},
            {"text": "Call Vendor A", "next_date": True},
            {"text": "Call Vendor A", "next_date": "2026-04-31"},
            {"text": "Call Vendor A", "next_date": None, "phase": "legal"},
        )
        for step in invalid_steps:
            with self.subTest(step=step):
                (self.session / post_call.REPORT_FILE).unlink(missing_ok=True)
                post_call.store_context(self.session, self.context)
                output = self.output(deal_updates=[{
                    "deal_id": "deal-a", "summary": "Update", "participant_ids": [],
                    "evidence": "Call Vendor A", "next_step": step,
                }])
                status = post_call.process_session(self.session, distiller=lambda _request: output)
                self.assertEqual(status["state"], "blocked")
                self.assertFalse((self.session / post_call.REPORT_FILE).exists())

    def test_undated_next_step_discloses_cleared_date_and_quarantines_unknown_deal(self) -> None:
        post_call.store_context(self.session, self.context)
        note = {"deal_id": "deal-b", "summary": "Lease reviewed", "participant_ids": [],
                "evidence": "Lease reviewed"}
        step = {"deal_id": "deal-a", "summary": "Follow-up agreed", "participant_ids": [],
                "evidence": "Call landlord", "next_step": {"text": "Call landlord", "next_date": None}}
        output = self.output(deal_updates=[note, step, {**step, "deal_id": "unknown-deal"}])
        status = post_call.process_session(self.session, distiller=lambda _request: output)
        self.assertEqual(status["state"], "ready_review")
        report = post_call.report_for_deal_room(self.session)["report"]
        self.assertEqual(len(report["deal_updates"]), 2)
        self.assertIn("clears the current date", report["deal_updates"][1]["summary"])
        self.assertTrue(any("unknown-deal" in question["question"] for question in report["review_questions"]))
        candidates = post_call.sanitized_candidates(self.session)["legacy_items"]
        self.assertEqual(candidates[0]["payload"], {"ref": "deal-b", "kind": "note", "summary": "Lease reviewed"})
        self.assertEqual(candidates[1]["payload"]["summary"], "Follow-up agreed")
        self.assertEqual(candidates[2]["payload"], {"deal": "deal-a", "text": "Call landlord", "next_date": None})

    def test_explicit_action_dates_reach_review_and_candidate_submission(self) -> None:
        (self.session / "transcript.json").write_text(json.dumps({"segments": [
            {"speaker": "Joe", "text": "I will call Vendor A on February 29, 2028."},
            {"speaker": "Dell", "text": "I will review the proposal."},
        ]}))
        post_call.store_context(self.session, self.context)
        output = self.output(joe_tasks=[{
            "title": "Call vendor", "deal_id": "deal-a", "participant_ids": [],
            "evidence": "Joe will call on February 29, 2028.", "due_on": "2028-02-29",
        }], dell_tasks=[{
            "title": "Review proposal", "deal_id": "deal-b", "participant_ids": [],
            "evidence": "Dell will review the proposal.", "due_on": None,
        }])
        status = post_call.process_session(self.session, distiller=lambda _request: output)
        self.assertEqual(status["state"], "ready_review")
        report = post_call.report_for_deal_room(self.session)["report"]
        self.assertEqual(report["joe_tasks"][0]["due_on"], "2028-02-29")
        self.assertIsNone(report["dell_tasks"][0]["due_on"])
        self.assertEqual(report["joe_tasks"][0]["candidate_status"], "unfiled")
        actions = [item for item in post_call.sanitized_candidates(self.session)["post_call_items"]
                   if item["kind"] == "assigned_action"]
        self.assertEqual([(item["assignee"], item["due_on"]) for item in actions],
                         [("joe", "2028-02-29"), ("dell", None)])

    def test_invalid_action_dates_block_without_creating_candidates(self) -> None:
        invalid_dates: tuple[object, ...] = (
            "2027-02-29", "2026-04-31", "20261025", "2026-1-01",
            "tomorrow", "2026-10-25T12:00:00Z", "", True, 23, {},
        )
        for owner in ("joe", "dell"):
            for due_on in invalid_dates:
                with self.subTest(owner=owner, due_on=due_on):
                    (self.session / post_call.REPORT_FILE).unlink(missing_ok=True)
                    post_call.store_context(self.session, self.context)
                    output = self.output(joe_tasks=[], dell_tasks=[])
                    output[f"{owner}_tasks"] = [{
                        "title": "Call vendor", "deal_id": "deal-a", "participant_ids": [],
                        "evidence": "Call vendor", "due_on": due_on,
                    }]
                    status = post_call.process_session(self.session, distiller=lambda _request: output)
                    self.assertEqual(status["state"], "blocked")
                    self.assertIn("due_on", status["reason"])
                    self.assertFalse((self.session / post_call.REPORT_FILE).exists())

    def test_candidate_receipts_are_bound_to_private_report_items(self) -> None:
        post_call.store_context(self.session, self.context)
        post_call.process_session(self.session, distiller=lambda _request: self.output())
        shaped = post_call.sanitized_candidates(self.session)
        post_bindings = [binding for binding in shaped["bindings"] if binding["remote"] == "post_call"]
        receipt = ["candidate-action", "candidate-draft"]
        post_call.apply_candidate_ids(self.session, post_bindings, receipt)
        report = post_call.read_json(self.session / post_call.REPORT_FILE)
        assert report is not None
        self.assertEqual(report["joe_tasks"][0]["candidate_id"], "candidate-action")
        self.assertEqual(report["draft_proposals"][0]["candidate_id"], "candidate-draft")
        self.assertEqual(report["draft_proposals"][0]["candidate_status"], "pending")

    def test_retention_requires_final_candidate_dispositions(self) -> None:
        post_call.store_context(self.session, self.context)
        post_call.process_session(self.session, distiller=lambda _request: self.output())
        self.assertIsNone(post_call.retention_ready(self.session, "backend-report-hash"))
        report = post_call.read_json(self.session / post_call.REPORT_FILE)
        assert report is not None
        for list_name in ("joe_tasks", "dell_tasks", "deal_updates", "draft_proposals"):
            for index, item in enumerate(report[list_name]):
                item["candidate_id"] = f"{list_name}-{index}"
                item["candidate_status"] = "confirmed"
        post_call.write_private(self.session / post_call.REPORT_FILE, report)
        receipt = post_call.retention_ready(self.session, "backend-report-hash")
        self.assertEqual(receipt["pending_items"], 0)
        self.assertTrue(receipt["aggregate_report_hash"])

    def test_command_distiller_is_injectable_and_never_runs_a_real_command_in_tests(self) -> None:
        class Result:
            returncode = 0
            stdout = '{"schema_version": 1}'

        runner = Mock(return_value=Result())
        result = post_call.command_distiller({"session": self.session.name}, command="fixture-distiller", runner=runner)
        self.assertEqual(result, {"schema_version": 1})
        self.assertEqual(runner.call_args.args[0], ["fixture-distiller"])
        self.assertFalse(runner.call_args.kwargs["shell"])

    def test_default_sonnet_command_produces_a_review_pack(self) -> None:
        post_call.store_context(self.session, self.context)
        reply = {"is_error": False, "structured_output": self.output()}
        overrides = {name: "synthetic-override" for name in (
            "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL",
            "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY",
            "CLAUDE_CODE_API_KEY_HELPER_TTL_MS", "CLAUDECODE")}
        with patch.dict(os.environ, {**overrides, "CARR_POST_CALL_DISTILLER_COMMAND": ""}), \
             patch.object(post_call.subprocess, "run", return_value=SimpleNamespace(
                 returncode=0, stdout=json.dumps(reply))) as runner:
            status = post_call.process_session(self.session)
        self.assertEqual(status["state"], "ready_review")
        report = post_call.read_json(self.session / post_call.REPORT_FILE)
        self.assertEqual(report["joe_tasks"][0]["title"], "Call vendor")
        argv = runner.call_args.args[0]
        self.assertEqual(argv[:4], ["claude", "-p", "--model", "sonnet"])
        self.assertIn("--no-session-persistence", argv)
        self.assertEqual(argv[argv.index("--tools") + 1], "")
        self.assertEqual(argv[argv.index("--setting-sources") + 1], "")
        self.assertIn("--strict-mcp-config", argv)
        self.assertIn("--disable-slash-commands", argv)
        self.assertIn("--system-prompt", argv)
        self.assertFalse(Path(runner.call_args.kwargs["cwd"]).exists())
        self.assertNotEqual(str(self.session), runner.call_args.kwargs["cwd"])
        for name in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL",
                     "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY",
                     "CLAUDE_CODE_API_KEY_HELPER_TTL_MS", "CLAUDECODE"):
            self.assertFalse(name in runner.call_args.kwargs["env"], name)
        self.assertEqual(argv[argv.index("--output-format") + 1], "json")
        self.assertEqual(json.loads(argv[argv.index("--json-schema") + 1])["required"],
                         ["schema_version", "report", "joe_tasks", "dell_tasks",
                          "deal_updates", "draft_proposals", "review_questions"])
        schema = json.loads(argv[argv.index("--json-schema") + 1])
        for owner in ("joe", "dell"):
            action = schema["properties"][f"{owner}_tasks"]["items"]
            self.assertIn("due_on", action["required"])
            self.assertEqual(action["properties"]["due_on"]["type"], ["string", "null"])
        update = schema["properties"]["deal_updates"]["items"]
        self.assertIn("next_step", update["required"])
        step = update["properties"]["next_step"]["anyOf"][1]
        self.assertEqual(step["required"], ["text", "next_date"])
        self.assertFalse(step["additionalProperties"])
        self.assertEqual(step["properties"]["next_date"]["type"], ["string", "null"])
        source = json.loads(runner.call_args.kwargs["input"])
        self.assertEqual(source["CONTEXT"], self.context)
        self.assertEqual(source["TRANSCRIPT"]["segments"][0]["text"], "Call Vendor A")

    def test_invalid_sonnet_output_blocks_without_writing_a_review_pack(self) -> None:
        bad_shape = self.output(review_questions="not an array")
        bad_item = self.output(dell_tasks=[{"title": "Incomplete"}])
        cases = [
            "not JSON", "[]", "{}",
            json.dumps({"is_error": True, "structured_output": self.output()}),
            json.dumps({"is_error": False, "structured_output": bad_shape}),
            json.dumps({"is_error": False, "structured_output": bad_item}),
        ]
        for stdout in cases:
            with self.subTest(stdout=stdout):
                post_call.store_context(self.session, self.context)
                with patch.dict(os.environ, {"CARR_POST_CALL_DISTILLER_COMMAND": ""}), \
                     patch.object(post_call.subprocess, "run", return_value=SimpleNamespace(
                         returncode=0, stdout=stdout)):
                    status = post_call.process_session(self.session)
                self.assertEqual(status["state"], "blocked")
                self.assertTrue(status["reason"])
                self.assertFalse((self.session / post_call.REPORT_FILE).exists())
                self.assertFalse((self.session / post_call.DRAFTS_FILE).exists())
                self.assertIsNotNone(post_call.context_from_session(self.session))
                self.assertTrue((self.session / "transcript.json").exists())

    def test_sonnet_execution_failures_block_without_a_report(self) -> None:
        for failure in (OSError("missing CLI"), subprocess.TimeoutExpired("claude", 180)):
            with self.subTest(failure=type(failure).__name__):
                post_call.store_context(self.session, self.context)
                with patch.dict(os.environ, {"CARR_POST_CALL_DISTILLER_COMMAND": ""}), \
                     patch.object(post_call.subprocess, "run", side_effect=failure):
                    status = post_call.process_session(self.session)
                self.assertEqual(status["state"], "blocked")
                self.assertFalse((self.session / post_call.REPORT_FILE).exists())

    def test_nonzero_sonnet_exit_blocks_even_with_valid_json(self) -> None:
        post_call.store_context(self.session, self.context)
        with patch.dict(os.environ, {"CARR_POST_CALL_DISTILLER_COMMAND": ""}), \
             patch.object(post_call.subprocess, "run", return_value=SimpleNamespace(
                 returncode=1, stdout=json.dumps({"structured_output": self.output()}))):
            status = post_call.process_session(self.session)
        self.assertEqual(status["state"], "blocked")
        self.assertFalse((self.session / post_call.REPORT_FILE).exists())

    def test_injected_command_wins_and_keeps_the_request_contract(self) -> None:
        post_call.store_context(self.session, self.context)
        fixture = Path(self.tmp.name) / "fixture-distiller"
        fixture.write_text(
            f"#!{sys.executable}\nimport json, pathlib, sys\n"
            f"pathlib.Path({str(self.session / 'request.json')!r}).write_text(sys.stdin.read())\n"
            f"print({json.dumps(self.output())!r})\n"
        )
        fixture.chmod(0o700)
        with patch.dict(os.environ, {"CARR_POST_CALL_DISTILLER_COMMAND": shlex.quote(str(fixture))}):
            status = post_call.process_session(self.session)
        self.assertEqual(status["state"], "ready_review")
        request = json.loads((self.session / "request.json").read_text())
        self.assertEqual(request["context"], self.context)
        self.assertEqual(request["session"], self.session.name)

    def test_post_call_path_has_no_retired_model_dependency(self) -> None:
        for name in ("post_call.py", "post_call_jev.py"):
            self.assertNotRegex((BIN_DIR / name).read_text(), r"(?i)flash|llama")

    def test_draft_creator_is_injectable_and_requires_a_confirmed_candidate(self) -> None:
        post_call.store_context(self.session, self.context)
        post_call.process_session(self.session, distiller=lambda _request: self.output())
        report = post_call.read_json(self.session / post_call.REPORT_FILE)
        assert report is not None
        draft = report["draft_proposals"][0]
        draft["candidate_id"] = "email-candidate-1"
        draft["candidate_status"] = "confirmed"
        post_call.write_private(self.session / post_call.REPORT_FILE, report)

        class Result:
            returncode = 0
            stdout = "draft only"
            stderr = ""

        runner = Mock(return_value=Result())
        created = post_call.create_outlook_draft(self.session, draft["draft_id"], draft["content_hash"], runner=runner)
        self.assertFalse(created["idempotent"])
        self.assertEqual(Path(runner.call_args.args[0][-1]).name, "outlook-draft.py")
        again = post_call.create_outlook_draft(self.session, draft["draft_id"], draft["content_hash"], runner=runner)
        self.assertTrue(again["idempotent"])
        self.assertEqual(runner.call_count, 1)


class CaptureBridgePostCallTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.session = Path(self.tmp.name) / "2026.08.10-bridge"
        self.session.mkdir()
        post_call.write_private(
            self.session / ".capture.json", {"session_token": "opaque-session-token"},
        )
        report = {
            "schema_version": 1, "session": self.session.name, "generated_at": "now",
            "report": {"summary": "Weekly summary", "decisions": [], "open_questions": []},
            "joe_tasks": [{"title": "Call vendor", "deal_id": "11111111-1111-4111-8111-111111111111", "participant_ids": [], "evidence": "Joe will call vendor.", "candidate_id": None, "candidate_status": "unfiled"}],
            "dell_tasks": [],
            "deal_updates": [{"deal_id": "11111111-1111-4111-8111-111111111111", "summary": "Waiting on vendor", "participant_ids": [], "evidence": "Vendor owes an update.", "candidate_id": None, "candidate_status": "unfiled"}],
            "draft_proposals": [{
                "recipient_party_id": "22222222-2222-4222-8222-222222222222",
                "recipient_ref": "P-001", "deal_id": "11111111-1111-4111-8111-111111111111",
                "subject": "Next steps", "body": "Private body", "participant_ids": [],
                "evidence": "Vendor requested a follow-up.", "recipient_email": "vendor@example.test",
                "recipient_name": "Vendor", "deal_name": "Deal", "draft_id": "draft-1",
                "content_hash": "a" * 64, "candidate_id": None, "candidate_status": "unfiled",
            }],
            "review_questions": [],
        }
        post_call.write_private(self.session / post_call.REPORT_FILE, report)
        post_call.write_private(
            self.session / post_call.STATUS_FILE,
            {"schema_version": 1, "session": self.session.name, "state": "ready_review"},
        )
        self.cfg = {"base_url": "https://worker.test", "device_id": "joe", "token": "device"}

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_claim_wait_helper_observes_late_call_mode_context(self) -> None:
        self.assertIsNone(capture_bridge.wait_for_call_mode(self.session, timeout=0))
        ticks = iter([0.0, 0.01])

        def late_context(_seconds: float) -> None:
            post_call.write_private(
                self.session / "call-context.json", {"mode": "weekly_deal_call"},
            )

        with (
            patch.object(capture_bridge.time, "monotonic", side_effect=lambda: next(ticks)),
            patch.object(capture_bridge.time, "sleep", side_effect=late_context),
        ):
            self.assertEqual(
                capture_bridge.wait_for_call_mode(self.session, timeout=1),
                "weekly_deal_call",
            )

    def test_claim_marks_weekly_workflow_only_after_bounded_mode_resolution(self) -> None:
        (self.session / ".capture.json").unlink()
        post_call.write_private(
            self.session / "announcement.json",
            {"announcement_fired_at": "2026-08-10T12:00:00Z"},
        )
        post_call.write_private(
            self.session / "meta.json", {"started": "2026-08-10T12:00:00Z"},
        )
        claims: list[dict[str, object]] = []

        def request(_method, url, body, _headers):
            if url.endswith("/capture/claim"):
                claims.append(body)
                return 200, {"session_token": "opaque", "ttl_seconds": 60}
            if url.endswith("/capture/status"):
                return 200, {"ok": True}
            raise AssertionError(url)

        with (
            patch.object(capture_bridge, "wait_for_call_mode", return_value="weekly_deal_call"),
            patch.object(capture_bridge, "http_request", side_effect=request),
        ):
            self.assertEqual(capture_bridge.claim_cmd(self.cfg, self.session), 0)
        self.assertEqual(claims[0]["workflow"], "post_call")

    def test_publish_exports_only_sanitized_metadata_and_binds_all_receipts(self) -> None:
        calls: list[tuple[str, str, dict[str, object] | None]] = []

        def request(method, url, body, _headers):
            calls.append((method, url, body))
            if url.endswith("/capture/status"):
                return 200, {"ok": True}
            if url.endswith("/capture/post-call/candidates"):
                return 200, {"candidate_ids": ["action-id", "draft-id"]}
            if url.endswith("/capture/candidates"):
                return 200, {"candidate_ids": ["update-id"]}
            raise AssertionError(url)

        with patch.object(capture_bridge, "http_request", side_effect=request):
            self.assertEqual(capture_bridge.publish_cmd(self.cfg, self.session), 0)
        exported = json.dumps(calls)
        self.assertNotIn("Private body", exported)
        self.assertNotIn("vendor@example.test", exported)
        report = post_call.read_json(self.session / post_call.REPORT_FILE)
        assert report is not None
        self.assertEqual(report["joe_tasks"][0]["candidate_id"], "action-id")
        self.assertEqual(report["draft_proposals"][0]["candidate_id"], "draft-id")
        self.assertEqual(report["deal_updates"][0]["candidate_id"], "update-id")
        self.assertEqual(stat.S_IMODE((self.session / post_call.PUSH_FILE).stat().st_mode), 0o600)

    def test_publish_status_failure_does_not_mask_successful_sibling_batches(self) -> None:
        def request(_method, url, _body, _headers):
            if url.endswith("/capture/status"):
                return 503, {}
            if url.endswith("/capture/post-call/candidates"):
                return 200, {"candidate_ids": ["action-id", "draft-id"]}
            if url.endswith("/capture/candidates"):
                return 200, {"candidate_ids": ["update-id"]}
            raise AssertionError(url)

        with patch.object(capture_bridge, "http_request", side_effect=request):
            self.assertEqual(capture_bridge.publish_cmd(self.cfg, self.session), 1)
        report = post_call.read_json(self.session / post_call.REPORT_FILE)
        self.assertEqual(report["joe_tasks"][0]["candidate_id"], "action-id")
        self.assertEqual(report["deal_updates"][0]["candidate_id"], "update-id")

    def test_poll_syncs_all_lanes_files_report_then_marks_purge_safe(self) -> None:
        report = post_call.read_json(self.session / post_call.REPORT_FILE)
        assert report is not None
        ids = ["action-id", "update-id", "draft-id"]
        for item, candidate_id in zip(
            [report["joe_tasks"][0], report["deal_updates"][0], report["draft_proposals"][0]], ids,
        ):
            item["candidate_id"] = candidate_id
            item["candidate_status"] = "pending"
        post_call.write_private(self.session / post_call.REPORT_FILE, report)
        report_posts: list[dict[str, object]] = []

        def request(method, url, body, _headers):
            if url.endswith("/capture/status"):
                return 200, {"ok": True}
            if url.endswith("/capture/session"):
                return 200, {
                    "state": "distilling", "post_call": True,
                    "candidates": {"pending": 0, "confirmed": 3, "skipped": 0},
                    "candidate_statuses": [
                        {"id": "action-id", "kind": "assigned_action", "status": "confirmed", "source": "post_call", "resulting_ref": "action-ref"},
                        {"id": "update-id", "kind": "activity", "status": "confirmed", "source": "legacy", "resulting_ref": "activity-ref"},
                        {"id": "draft-id", "kind": "email_draft", "status": "confirmed", "source": "post_call", "resulting_ref": None},
                    ],
                    "post_call_report": {"filed": False}, "meeting_record": None,
                }
            if url.endswith("/capture/post-call/report"):
                report_posts.append(body)
                return 200, {"filed": True, "candidate_count": 3}
            raise AssertionError(url)

        with patch.object(capture_bridge, "http_request", side_effect=request):
            self.assertEqual(capture_bridge.poll_cmd(self.cfg, self.session), 0)
        self.assertEqual(report_posts[0]["candidate_count"], 3)
        self.assertTrue((self.session / "ingested.json").exists())
        self.assertFalse((self.session / ".capture.json").exists())
        final = post_call.read_json(self.session / post_call.REPORT_FILE)
        assert final is not None
        self.assertTrue(final["backend_report_sha256"])
        for key in ("joe_tasks", "deal_updates", "draft_proposals"):
            self.assertEqual(final[key][0]["candidate_status"], "confirmed")

    def test_poll_never_files_zero_count_report_when_candidate_publish_fails(self) -> None:
        calls: list[str] = []

        def request(_method, url, _body, _headers):
            calls.append(url)
            if url.endswith("/capture/status"):
                return 200, {"ok": True}
            if url.endswith("/capture/post-call/candidates"):
                return 503, {"error": "unavailable"}
            if url.endswith("/capture/candidates"):
                return 200, {"candidate_ids": ["update-id"]}
            raise AssertionError(url)

        with patch.object(capture_bridge, "http_request", side_effect=request):
            self.assertEqual(capture_bridge.poll_cmd(self.cfg, self.session), 1)
        self.assertFalse(any(url.endswith("/capture/session") for url in calls))
        self.assertFalse(any(url.endswith("/capture/post-call/report") for url in calls))
        self.assertTrue((self.session / ".capture.json").exists())


class PostCallJevChecksTests(unittest.TestCase):
    def setUp(self) -> None:
        self.context = {
            "deals": [
                {"id": "deal-a", "name": "Acme Clinic", "owner": "Joe", "operating_state": "active", "participants": []},
                {"id": "deal-b", "name": "Beta Medical", "owner": "Dell", "operating_state": "active", "participants": []},
            ],
            "speaker_labels": {"mic": "Joe", "system": "Dell"},
            "local_partner": "Joe",
        }
        self.transcript = {"segments": [
            {"speaker": "Joe", "text": "I will call the Acme Clinic vendor about the lease renewal."},
            {"speaker": "Dell", "text": "Sounds good, let me know how it goes."},
        ]}

    def result(self, **overrides: object) -> dict[str, object]:
        base: dict[str, object] = {
            "session": "2026.09.23-jev",
            "joe_tasks": [{
                "title": "Call the Acme Clinic vendor", "deal_id": "deal-a",
                "participant_ids": [], "evidence": "I will call the Acme Clinic vendor",
                "candidate_id": None, "candidate_status": "unfiled",
            }],
            "dell_tasks": [], "deal_updates": [], "draft_proposals": [],
            "review_questions": [],
        }
        base.update(overrides)
        return base

    @staticmethod
    def fake_client(*, deal_choice: str = "deal-a", deal_confidence: float = 0.9,
                 speaker_probability: float = 0.9, details_probability: float = 0.9):
        def _ask(*, state: object, questions: dict[str, object]) -> SimpleNamespace:
            body = {
                "deal_match": {"type": "choice", "choice": deal_choice, "confidence": deal_confidence},
                "speaker_right": {"type": "noul", "noul": speaker_probability},
                "details_supported": {"type": "noul", "noul": details_probability},
            }
            return SimpleNamespace(answers={key: SimpleNamespace(**body[key.split(":")[-1]]) for key in questions})
        return Mock(system_one=_ask)

    def test_pack_uses_one_official_sdk_call_for_all_items(self) -> None:
        client = Mock()
        client.system_one.return_value = SimpleNamespace(answers={
            "i0:deal_match": SimpleNamespace(choice="deal-a", confidence=0.2),
            "i0:speaker_right": SimpleNamespace(noul=0.9),
            "i0:details_supported": SimpleNamespace(noul=0.9),
            "i1:deal_match": SimpleNamespace(choice="deal-b", confidence=0.2),
            "i1:speaker_right": SimpleNamespace(noul=0.9),
            "i1:details_supported": SimpleNamespace(noul=0.9),
        })
        result = post_call_jev.check_distillation(self.result(dell_tasks=[{
            "title": "Send the revised LOI", "deal_id": "deal-b", "evidence": "Dell will send the revised LOI",
        }]), self.context, self.transcript, client=client)
        self.assertTrue(result["joe_tasks"][0]["checks"]["deal"]["pass"])
        self.assertTrue(result["dell_tasks"][0]["checks"]["deal"]["pass"])
        self.assertEqual(client.system_one.call_count, 1)
        self.assertEqual(len(client.system_one.call_args.kwargs["questions"]), 6)

    def test_an_accurate_item_passes_all_three_checks_and_is_not_flagged(self) -> None:
        result = post_call_jev.check_distillation(
            self.result(), self.context, self.transcript, client=self.fake_client(),
        )
        checks = result["joe_tasks"][0]["checks"]
        self.assertTrue(checks["flagged"])
        self.assertTrue(any("review" in reason.lower() for reason in checks["reasons"]))
        self.assertTrue(checks["deal"]["pass"])
        self.assertTrue(checks["speaker"]["pass"])
        self.assertTrue(checks["details"]["pass"])
        self.assertEqual(result["review_questions"], [])

    def test_a_wrong_deal_match_is_flagged_with_a_plain_english_reason(self) -> None:
        result = post_call_jev.check_distillation(
            self.result(), self.context, self.transcript,
            client=self.fake_client(deal_choice="deal-b"),
        )
        checks = result["joe_tasks"][0]["checks"]
        self.assertTrue(checks["flagged"])
        self.assertFalse(checks["deal"]["pass"])
        self.assertTrue(checks["speaker"]["pass"])
        self.assertTrue(checks["details"]["pass"])
        self.assertTrue(any("Right deal" in reason for reason in checks["reasons"]))

    def test_a_wrong_speaker_attribution_is_flagged_with_a_plain_english_reason(self) -> None:
        result = post_call_jev.check_distillation(
            self.result(), self.context, self.transcript,
            client=self.fake_client(speaker_probability=0.05),
        )
        checks = result["joe_tasks"][0]["checks"]
        self.assertTrue(checks["flagged"])
        self.assertTrue(checks["deal"]["pass"])
        self.assertFalse(checks["speaker"]["pass"])
        self.assertTrue(any("Right speaker" in reason for reason in checks["reasons"]))

    def test_unsupported_details_are_flagged_with_a_plain_english_reason(self) -> None:
        result = post_call_jev.check_distillation(
            self.result(), self.context, self.transcript,
            client=self.fake_client(details_probability=0.1),
        )
        checks = result["joe_tasks"][0]["checks"]
        self.assertTrue(checks["flagged"])
        self.assertTrue(checks["deal"]["pass"])
        self.assertTrue(checks["speaker"]["pass"])
        self.assertFalse(checks["details"]["pass"])
        self.assertTrue(any("Right details" in reason for reason in checks["reasons"]))

    def test_jev_unavailable_never_fails_the_pack_and_flags_every_item_unavailable(self) -> None:
        def raising_ask(**_kwargs) -> dict[str, object]:
            raise TypeSafeError("network unreachable")

        result = post_call_jev.check_distillation(
            self.result(dell_tasks=[{
                "title": "Send the updated LOI", "deal_id": "deal-b",
                "participant_ids": [], "evidence": "Dell will send the updated LOI",
                "candidate_id": None, "candidate_status": "unfiled",
            }]),
            self.context, self.transcript, client=Mock(system_one=raising_ask),
        )
        self.assertEqual(result["joe_tasks"][0]["checks"], {"unavailable": True})
        self.assertEqual(result["dell_tasks"][0]["checks"], {"unavailable": True})
        self.assertEqual(len(result["review_questions"]), 1)
        self.assertIn("Jev", result["review_questions"][0]["question"])
        self.assertFalse(result["review_questions"][0]["resolved"])

    def test_evidence_segments_are_matched_by_token_overlap_within_a_small_window(self) -> None:
        segments = [
            {"speaker": "Joe", "text": "unrelated small talk about parking"},
            {"speaker": "Joe", "text": "I will call the Acme Clinic vendor about the lease renewal."},
            {"speaker": "Dell", "text": "Sounds good, let me know how it goes."},
            {"speaker": "Dell", "text": "completely unrelated closing remarks"},
        ]
        matched = post_call_jev._evidence_segments("I will call the Acme Clinic vendor", segments)
        self.assertIn(segments[1], matched)
        self.assertLessEqual(len(matched), post_call_jev.MAX_SEGMENTS_SENT)

    def test_candidate_deals_never_include_the_full_recorded_deal_list(self) -> None:
        many_deals = [{"id": f"deal-{n}", "name": f"Deal {n}", "owner": "Joe",
                        "operating_state": "active", "participants": []} for n in range(56)]
        many_deals.append({"id": "deal-a", "name": "Acme Clinic", "owner": "Joe",
                            "operating_state": "active", "participants": []})
        candidates = post_call_jev._candidate_deals(
            "deal-a", many_deals, [{"speaker": "Joe", "text": "Acme Clinic vendor lease"}],
        )
        self.assertLessEqual(len(candidates), post_call_jev.MAX_CANDIDATE_DEALS)
        self.assertEqual(candidates[0]["id"], "deal-a")


if __name__ == "__main__":
    unittest.main()
