#!/usr/bin/env python3
"""Guards the dictation rig's within-track speaker labels.

Two promises are tested, not just the happy path. First, any diarizer failure
leaves the transcript exactly as the rig wrote it before diarization existed:
one label per channel. Second, the diarizer's raw output, which carries voice
embeddings, never survives the call that read it.

Offline: no audio tools, no whisper, no diarizer binary. The subprocess and the
audio steps are stubbed.

Run: python3 tools/test_dictation_speaker_split.py
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path
from unittest import mock

RIG_BIN = Path(__file__).resolve().parent / "dictation-rig" / "bin"
sys.path.insert(0, str(RIG_BIN))

import speaker_split  # noqa: E402
import transcribe_session  # noqa: E402


@dataclass(frozen=True)
class Seg:
    start_ms: int
    end_ms: int


def turn(sid: str, start_s: float, end_s: float) -> speaker_split.Turn:
    return speaker_split.Turn(sid, int(start_s * 1000), int(end_s * 1000))


class LabelSegmentsTest(unittest.TestCase):
    def test_one_speaker_keeps_the_channel_label(self) -> None:
        segs = [Seg(0, 1000), Seg(2000, 3000)]
        turns = [turn("S1", 0, 1), turn("S1", 2, 3)]
        self.assertEqual(
            speaker_split.label_segments(segs, turns, "Other participant", "system"),
            ["Other participant", "Other participant"],
        )

    def test_no_turns_keeps_the_channel_label(self) -> None:
        self.assertEqual(speaker_split.label_segments([Seg(0, 1000)], [], "Me", "mic"), ["Me"])

    def test_system_track_numbers_speakers_by_first_appearance(self) -> None:
        segs = [Seg(0, 1000), Seg(1000, 2000), Seg(2000, 3000)]
        # S7 speaks first even though its id sorts last.
        turns = [turn("S7", 0, 1), turn("S2", 1, 2), turn("S7", 2, 3)]
        self.assertEqual(
            speaker_split.label_segments(segs, turns, "Other participant", "system"),
            ["Other participant 1", "Other participant 2", "Other participant 1"],
        )

    def test_mic_track_keeps_the_dominant_voice_as_the_channel_label(self) -> None:
        segs = [Seg(0, 500), Seg(1000, 9000)]
        # S1 speaks first but briefly; S2 does most of the talking at this Mac.
        turns = [turn("S1", 0, 0.5), turn("S2", 1, 9)]
        self.assertEqual(
            speaker_split.label_segments(segs, turns, "Joe", "mic"),
            ["In-room speaker 1", "Joe"],
        )

    def test_segment_takes_the_turn_it_overlaps_most(self) -> None:
        segs = [Seg(900, 2000)]
        turns = [turn("S1", 0, 1), turn("S2", 1, 3)]
        self.assertEqual(
            speaker_split.label_segments(segs, turns, "Other participant", "system"),
            ["Other participant 2"],
        )

    def test_segment_overlapping_no_turn_keeps_the_channel_label(self) -> None:
        segs = [Seg(10_000, 11_000)]
        turns = [turn("S1", 0, 1), turn("S2", 1, 2)]
        self.assertEqual(
            speaker_split.label_segments(segs, turns, "Other participant", "system"),
            ["Other participant"],
        )


class ParseTurnsTest(unittest.TestCase):
    def test_malformed_entries_are_skipped(self) -> None:
        data = {
            "segments": [
                {"speakerId": "S1", "startTimeSeconds": 1.5, "endTimeSeconds": 2.25},
                {"speakerId": "S2"},
                {"speakerId": "S3", "startTimeSeconds": "x", "endTimeSeconds": 1},
                {"speakerId": "S4", "startTimeSeconds": 3, "endTimeSeconds": 3},
                "not a dict",
            ]
        }
        self.assertEqual(speaker_split.parse_turns(data), [speaker_split.Turn("S1", 1500, 2250)])

    def test_non_object_output_yields_nothing(self) -> None:
        self.assertEqual(speaker_split.parse_turns([1, 2]), [])


class RunDiarizerTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.cli = self.tmp / "fluidaudiocli"
        self.cli.write_text("stub")
        self.wav = self.tmp / "system.wav"
        self.wav.write_bytes(b"")
        self.lines: list[str] = []

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def run_with(self, runner):
        return speaker_split.run_diarizer(self.wav, self.tmp, self.lines.append, cli=self.cli, runner=runner)

    def output_path(self, cmd: list[str]) -> Path:
        return Path(cmd[cmd.index("--output") + 1])

    def test_success_parses_turns_and_deletes_the_embedding_file(self) -> None:
        written: list[Path] = []

        def runner(cmd, **_kwargs):
            out = self.output_path(cmd)
            out.write_text(json.dumps({"segments": [
                {"speakerId": "S1", "startTimeSeconds": 0, "endTimeSeconds": 1, "embedding": [0.1, 0.2]},
            ]}))
            written.append(out)
            return subprocess.CompletedProcess(cmd, 0, "", "")

        self.assertEqual(self.run_with(runner), [speaker_split.Turn("S1", 0, 1000)])
        self.assertEqual(len(written), 1)
        self.assertFalse(written[0].exists(), "diarizer output with embeddings was left on disk")

    def test_failed_run_still_deletes_partial_output(self) -> None:
        written: list[Path] = []

        def runner(cmd, **_kwargs):
            out = self.output_path(cmd)
            out.write_text('{"segments": [')
            written.append(out)
            return subprocess.CompletedProcess(cmd, 1, "", "noSpeechDetected")

        self.assertEqual(self.run_with(runner), [])
        self.assertFalse(written[0].exists())
        self.assertTrue(any("rc=1" in line and "noSpeechDetected" in line for line in self.lines))

    def test_unreadable_output_is_a_logged_skip(self) -> None:
        def runner(cmd, **_kwargs):
            self.output_path(cmd).write_text("not json")
            return subprocess.CompletedProcess(cmd, 0, "", "")

        self.assertEqual(self.run_with(runner), [])
        self.assertTrue(any("unreadable output" in line for line in self.lines))

    def test_timeout_is_a_logged_skip(self) -> None:
        def runner(cmd, **_kwargs):
            raise subprocess.TimeoutExpired(cmd, 1)

        self.assertEqual(self.run_with(runner), [])
        self.assertTrue(any("TimeoutExpired" in line for line in self.lines))

    def test_missing_binary_never_runs_anything(self) -> None:
        self.cli.unlink()

        def runner(cmd, **_kwargs):
            raise AssertionError("runner must not be called without a binary")

        self.assertEqual(self.run_with(runner), [])
        self.assertTrue(any("not built" in line for line in self.lines))


class ProcessSessionWiringTest(unittest.TestCase):
    """The diarizer's labels reach transcript.json, and its absence changes nothing."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.session = Path(self._tmp.name)
        (self.session / "meta.json").write_text(json.dumps({
            "files": {"mic": "mic.caf", "system": "system.caf"},
            "start_offset_ms": {"mic": 0, "system": 500},
        }))
        (self.session / "mic.caf").write_bytes(b"")
        (self.session / "system.caf").write_bytes(b"")
        self.addCleanup(self._tmp.cleanup)
        self.stub(transcribe_session, "convert_caf_to_wav",
                  lambda caf, out_dir, log: out_dir / (caf.stem + ".wav"))
        self.stub(transcribe_session, "run_whisper", lambda wav, *_a: [
            transcribe_session.RawSegment(0, 1000, f"{wav.stem} one"),
            transcribe_session.RawSegment(1000, 2000, f"{wav.stem} two"),
        ])
        self.stub(transcribe_session, "resolve_model", lambda log: Path("/models/stub.bin"))
        self.stub(transcribe_session, "load_prompt", lambda: "")
        self.stub(transcribe_session.post_call, "process_session", lambda session_dir: {"state": "stubbed"})

    def stub(self, module: object, name: str, value: object) -> None:
        patcher = mock.patch.object(module, name, value)
        patcher.start()
        self.addCleanup(patcher.stop)

    def transcript(self) -> dict:
        transcribe_session.process_session(self.session, lambda _line: None)
        return json.loads((self.session / "transcript.json").read_text())

    def test_two_voices_on_the_system_track_get_numbered_labels(self) -> None:
        def diarize(wav, _tmp, _log):
            if wav.stem == "system":
                return [turn("S1", 0, 1), turn("S2", 1, 2)]
            return [turn("S1", 0, 2)]

        self.stub(speaker_split, "run_diarizer", diarize)
        t = self.transcript()
        by_text = {seg["text"]: seg["speaker"] for seg in t["segments"]}
        self.assertEqual(by_text, {
            "mic one": "Me", "mic two": "Me",
            "system one": "Other participant 1", "system two": "Other participant 2",
        })
        self.assertIn("system: " + speaker_split.METHOD_NOTE, t["speaker_method"])
        # The shared clock still applies after relabeling.
        self.assertEqual([s["start_ms"] for s in t["segments"] if s["text"] == "system one"], [500])

    def test_no_diarizer_output_is_the_old_transcript(self) -> None:
        self.stub(speaker_split, "run_diarizer", lambda *_a: [])
        t = self.transcript()
        self.assertEqual({seg["speaker"] for seg in t["segments"]}, {"Me", "Other participant"})
        self.assertEqual(t["speaker_method"], "separate audio channels; no third-party voiceprint")


if __name__ == "__main__":
    unittest.main()
