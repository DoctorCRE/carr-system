"""
speaker_split.py — within-track speaker turns for the CARR dictation rig.

Imported by transcribe_session.py; not a script of its own.

The rig's two capture channels already separate "me" (mic) from "them"
(system audio). That breaks down in two places: several people on the far end
of a call all land on the system track as one "Other participant", and an
in-person meeting picked up by one mic has no split at all. This module asks
FluidAudio's offline diarizer (the pyannote segmentation + WeSpeaker + VBx
pipeline, Core ML, on-device) who spoke when WITHIN one track, and relabels
that track's transcript segments from the answer.

Labels are per-recording only. The diarizer's JSON carries a per-segment
voice embedding, so its output file lives in the caller's temp directory and
is deleted the moment it is parsed: nothing that could identify a voice
across recordings is ever kept, which is the rig's no-third-party-voiceprint
rule (README, "No third-party voiceprints — structural").

Every failure path degrades to the channel label the rig used before this
existed: a missing binary, a non-zero exit, a timeout, no speech detected, or
fewer than two speakers all return the base label for every segment and log
why. Pure standard library, like the rest of the rig's Python.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Protocol, Sequence

# Built by bin/build-quill.sh from vendor/quill's pinned FluidAudio checkout,
# so the diarizer version is the same reviewed dependency quill itself uses.
FLUIDAUDIO_CLI = (
    Path(__file__).resolve().parent.parent
    / "vendor"
    / "quill"
    / ".build"
    / "release"
    / "fluidaudiocli"
)

# A 52-minute recording diarized in ~12s on an M1 Pro (2026-09-23), so this
# only bounds a hang; it is not a performance budget.
DIARIZE_TIMEOUT_S = 900

MIC_OTHER_LABEL = "In-room speaker"

METHOD_NOTE = (
    "within-track speaker turns from on-device FluidAudio diarization; "
    "per-recording labels, no voiceprint kept"
)

LogFunc = Callable[[str], None]


class Timed(Protocol):
    @property
    def start_ms(self) -> int: ...

    @property
    def end_ms(self) -> int: ...


@dataclass(frozen=True)
class Turn:
    """One diarizer turn, offsets relative to its own track."""

    speaker_id: str
    start_ms: int
    end_ms: int


def parse_turns(data: Any) -> list[Turn]:
    """Turns from fluidaudiocli's `process --output` JSON. Malformed entries
    are skipped rather than failing the whole track."""
    turns: list[Turn] = []
    segments = data.get("segments", []) if isinstance(data, dict) else []
    for item in segments:
        if not isinstance(item, dict):
            continue
        try:
            speaker_id = str(item["speakerId"])
            start_ms = int(float(item["startTimeSeconds"]) * 1000)
            end_ms = int(float(item["endTimeSeconds"]) * 1000)
        except (KeyError, TypeError, ValueError):
            continue
        if end_ms > start_ms:
            turns.append(Turn(speaker_id, start_ms, end_ms))
    return turns


def run_diarizer(
    wav_path: Path,
    tmp_dir: Path,
    log: LogFunc,
    cli: Path = FLUIDAUDIO_CLI,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> list[Turn]:
    """Diarize one 16kHz mono WAV. Returns [] on any failure, logged."""
    if not cli.exists():
        log(f"SPEAKERS skipped {wav_path.name}: diarizer not built at {cli} (run bin/build-quill.sh)")
        return []

    out_path = tmp_dir / f"{wav_path.stem}.speakers.json"
    cmd = [str(cli), "process", str(wav_path), "--mode", "offline", "--output", str(out_path)]
    log(f"SPEAKERS {wav_path.name}")
    try:
        try:
            result = runner(cmd, capture_output=True, text=True, timeout=DIARIZE_TIMEOUT_S)
        except (OSError, subprocess.TimeoutExpired) as exc:
            log(f"SPEAKERS skipped {wav_path.name}: {type(exc).__name__}: {exc}")
            return []
        if result.returncode != 0 or not out_path.exists():
            detail = " | ".join((result.stderr or result.stdout or "").strip()[-300:].splitlines())
            log(f"SPEAKERS skipped {wav_path.name} (rc={result.returncode}): {detail}")
            return []
        try:
            data = json.loads(out_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            log(f"SPEAKERS skipped {wav_path.name}: unreadable output: {exc}")
            return []
        return parse_turns(data)
    finally:
        # The output carries per-segment voice embeddings. Never keep it.
        out_path.unlink(missing_ok=True)


def _overlap_ms(a_start: int, a_end: int, b_start: int, b_end: int) -> int:
    return max(0, min(a_end, b_end) - max(a_start, b_start))


def label_segments(
    segments: Sequence[Timed],
    turns: Sequence[Turn],
    base_label: str,
    channel: str,
) -> list[str]:
    """One label per transcript segment, by largest overlap with a turn.

    Fewer than two distinct speakers means the channel label stands, unchanged.
    On the mic the speaker with the most talk time keeps the channel label
    (the person at this Mac) and everyone else is "In-room speaker N". On the
    system track everyone is "<channel label> N". N follows order of first
    appearance. A segment no turn overlaps keeps the channel label.
    """
    talk_ms: dict[str, int] = {}
    first_seen: dict[str, int] = {}
    for turn in turns:
        talk_ms[turn.speaker_id] = talk_ms.get(turn.speaker_id, 0) + turn.end_ms - turn.start_ms
        first_seen[turn.speaker_id] = min(first_seen.get(turn.speaker_id, turn.start_ms), turn.start_ms)
    if len(talk_ms) < 2:
        return [base_label] * len(segments)

    order = sorted(first_seen, key=lambda sid: (first_seen[sid], sid))
    names: dict[str, str] = {}
    if channel == "mic":
        dominant = max(order, key=lambda sid: (talk_ms[sid], -order.index(sid)))
        names[dominant] = base_label
        others = [sid for sid in order if sid != dominant]
        for n, sid in enumerate(others, start=1):
            names[sid] = f"{MIC_OTHER_LABEL} {n}"
    else:
        for n, sid in enumerate(order, start=1):
            names[sid] = f"{base_label} {n}"

    labels: list[str] = []
    for seg in segments:
        best_sid = ""
        best_ms = 0
        for turn in turns:
            ms = _overlap_ms(seg.start_ms, seg.end_ms, turn.start_ms, turn.end_ms)
            if ms > best_ms:
                best_sid, best_ms = turn.speaker_id, ms
        labels.append(names[best_sid] if best_sid else base_label)
    return labels
