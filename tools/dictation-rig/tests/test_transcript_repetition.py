"""Verify Whisper repetition recovery before call summaries."""
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
import wave
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'bin'))
import transcribe_session as transcription



class FakeClock:
    elapsed = 0.0

    def __call__(self):
        return self.elapsed


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    clock = FakeClock()
    monkeypatch.setattr(time, 'monotonic', clock)

    class FixedDatetime:
        @staticmethod
        def now(tz):
            return datetime(2026, 1, 1, tzinfo=timezone.utc)

    monkeypatch.setattr(transcription, 'datetime', FixedDatetime)
    return clock


@pytest.mark.parametrize('seconds_per_chunk', [100, 120])
def test_call_recovery_budget_preserves_unrecovered_speech(tmp_path, monkeypatch, clock,
                                                         seconds_per_chunk):
    audio = tmp_path / 'synthetic.wav'
    with wave.open(str(audio), 'wb') as output:
        output.setparams((1, 2, 100, 0, 'NONE', 'not compressed'))
        output.writeframes(b'\0\0' * 100 * 300)
    original = ' '.join(['Synthetic repeated phrase.'] * 30)
    calls = []

    def decode(cmd, **kwargs):
        recovery = '-mc' in cmd
        if recovery:
            elapsed = min(seconds_per_chunk, kwargs['timeout'])
            clock.elapsed += elapsed
            calls.append(kwargs['timeout'])
            if elapsed < seconds_per_chunk:
                raise subprocess.TimeoutExpired(cmd, kwargs['timeout'])
        rows = ([segment(0, 1000, f'Recovered useful speech number {len(calls)}.')] if recovery else
                [segment(0, 300000, original)])
        target = Path(cmd[cmd.index('-of') + 1]).with_suffix('.json')
        target.write_text(json.dumps({'transcription': [
            {'offsets': {'from': row.start_ms, 'to': row.end_ms}, 'text': row.text}
            for row in rows]}))
        return subprocess.CompletedProcess(cmd, 0, '', '')

    monkeypatch.setattr(transcription.subprocess, 'run', decode)
    result = transcription.run_whisper(audio, tmp_path / 'model.bin', '', tmp_path,
                                       lambda _: None)
    assert clock.elapsed <= 240, f'recovery took {clock.elapsed} synthetic seconds'
    assert len(calls) <= 3
    assert result[-1].text == original
    assert result[-1].unrecovered
    assert result[-1].start_ms == 60000
    assert result[-1].end_ms == 300000
    tagged = transcription.shift_and_tag(result, 500, ['Speaker'] * len(result))
    assert tagged[-1]['unrecovered'] is True
    assert tagged[-1]['start_ms'] == 60500
    assert tagged[-1]['text'] == original


def test_budget_is_shared_across_call_tracks_and_reaches_summary(tmp_path, monkeypatch, clock):
    for name in ('mic', 'system'):
        with wave.open(str(tmp_path / f'{name}.wav'), 'wb') as output:
            output.setparams((1, 2, 100, 0, 'NONE', 'not compressed'))
            output.writeframes(b'\0\0' * 100 * 90)
    (tmp_path / 'meta.json').write_text(json.dumps({
        'files': {'mic': 'mic.wav', 'system': 'system.wav'}, 'start_offset_ms': {'system': 500}}))
    model = tmp_path / 'model.bin'
    model.write_bytes(b'synthetic')
    monkeypatch.setattr(transcription, 'resolve_model', lambda _: model)
    monkeypatch.setattr(transcription, 'verify_model_pin', lambda *_: None)
    monkeypatch.setattr(transcription, 'load_prompt', lambda: '')
    monkeypatch.setattr(transcription, 'convert_caf_to_wav', lambda path, *_: path)
    monkeypatch.setattr(transcription.speaker_split, 'run_diarizer', lambda *_: [])
    recovered_calls = []
    original = ' '.join(['Synthetic looping speech.'] * 30)

    def decode(cmd, **kwargs):
        if '-mc' in cmd:
            recovered_calls.append(cmd)
            clock.elapsed += 120
            rows = [segment(0, 1000, f'Recovered passage {len(recovered_calls)}.')]
        else:
            clock.elapsed += 500
            rows = [segment(0, 90000, original)]
        Path(cmd[cmd.index('-of') + 1]).with_suffix('.json').write_text(json.dumps({
            'transcription': [{'offsets': {'from': row.start_ms, 'to': row.end_ms}, 'text': row.text}
                              for row in rows]}))
        return subprocess.CompletedProcess(cmd, 0, '', '')

    monkeypatch.setattr(transcription.subprocess, 'run', decode)
    summaries = []

    def summarize(path):
        summaries.append(json.loads((path / 'transcript.json').read_text()))
        return {'state': 'ready'}

    monkeypatch.setattr(transcription.post_call, 'run_session', summarize)
    transcription.process_session(tmp_path, lambda _: None)
    assert len(recovered_calls) == 2
    assert clock.elapsed == 1240
    assert len(summaries) == 1
    transcript = summaries[0]
    assert transcript['unrecovered_speech'].startswith('Some speech was not recovered')
    flagged = [row for row in transcript['segments'] if row.get('unrecovered')]
    assert [(row['start_ms'], row['end_ms'], row['text']) for row in flagged] == [
        (500, 90500, original), (60000, 90000, original)]

def segment(start, end, text):
    return transcription.RawSegment(start, end, text)


def run_decoder(tmp_path, monkeypatch, initial, recovered, *, clock=None):
    audio = tmp_path / 'synthetic.wav'
    with wave.open(str(audio), 'wb') as output:
        output.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
        output.writeframes(b'\0\0' * 16000 * 90)
    calls = []

    def decode(cmd, **kwargs):
        calls.append((cmd, kwargs))
        if clock is not None and len(calls) > 1:
            clock.elapsed += 10
        rows = initial if len(calls) == 1 else recovered
        target = Path(cmd[cmd.index('-of') + 1]).with_suffix('.json')
        target.write_text(json.dumps({'transcription': [
            {'offsets': {'from': row.start_ms, 'to': row.end_ms}, 'text': row.text}
            for row in rows
        ]}))
        return subprocess.CompletedProcess(cmd, 0, '', '')

    monkeypatch.setattr(transcription.subprocess, 'run', decode)
    result = transcription.run_whisper(audio, tmp_path / 'model.bin', 'vocabulary', tmp_path,
                                       lambda _: None, timeout=60, env={'PATH': '/usr/bin'})
    return result, calls


@pytest.mark.parametrize('packed', [False, True])
def test_loop_is_redecoded_and_normal_neighbors_survive(tmp_path, monkeypatch, packed, clock):
    before = segment(0, 1000, 'The synthetic proposal is ready.')
    after = segment(61000, 62000, 'Review the revised plan next week.')
    phrase = 'Please revise the synthetic plan.'
    looping = ([segment(1000, 61000, ' '.join([phrase] * 150))] if packed else
               [segment(1000 + i * 400, 1400 + i * 400, phrase) for i in range(150)])
    recovered = [segment(0, 1000, 'The recovered speech has useful details.')]
    result, calls = run_decoder(tmp_path, monkeypatch, [before, *looping, after], recovered, clock=clock)
    assert result == [before, segment(1000, 2000, recovered[0].text),
                      segment(31000, 32000, recovered[0].text), after]
    assert len(calls) == 3
    assert clock.elapsed == 20
    for cmd, kwargs in calls[1:]:
        assert cmd[cmd.index('-mc') + 1] == '0'
        assert '--prompt' not in cmd
        assert kwargs['timeout'] == 60
        assert kwargs['env'] == {'PATH': '/usr/bin'}
        with wave.open(cmd[cmd.index('-f') + 1], 'rb') as audio:
            assert audio.getnframes() == 30 * 16000


def test_non_looping_speech_is_unchanged(tmp_path, monkeypatch):
    normal = [segment(0, 1000, 'Yes, yes, please review the proposal.'),
              segment(1000, 2000, 'The proposal needs a revised schedule.'),
              segment(2000, 3000, 'Please review the proposal after the visit.')]
    result, calls = run_decoder(tmp_path, monkeypatch, normal, [])
    assert result == normal
    assert len(calls) == 1


def test_failed_recovery_never_returns_a_loop(tmp_path, monkeypatch):
    looping = [segment(1000, 2000, ' '.join(['Synthetic repeated phrase.'] * 30))]
    with pytest.raises(transcription.TranscribeError, match='repetition'):
        run_decoder(tmp_path, monkeypatch, looping, looping)


def test_empty_recovery_requires_attention(tmp_path, monkeypatch):
    looping = [segment(1000, 2000, ' '.join(['Synthetic repeated phrase.'] * 30))]
    with pytest.raises(transcription.TranscribeError, match='repetition'):
        run_decoder(tmp_path, monkeypatch, looping, [])


def test_unrecovered_loop_stops_before_summary(tmp_path, monkeypatch):
    track = tmp_path / 'synthetic.caf'
    track.write_bytes(b'synthetic')
    model = tmp_path / 'model.bin'
    model.write_bytes(b'synthetic')
    monkeypatch.setattr(transcription, 'read_meta', lambda _: {
        'files': {'mic': track.name, 'system': track.name}, 'start_offset_ms': {}})
    monkeypatch.setattr(transcription, 'read_consent', lambda _: {})
    monkeypatch.setattr(transcription, 'read_speaker_labels', lambda _: {'mic': 'A', 'system': 'B'})
    monkeypatch.setattr(transcription, 'resolve_model', lambda _: model)
    monkeypatch.setattr(transcription, 'verify_model_pin', lambda *_: None)
    monkeypatch.setattr(transcription, 'load_prompt', lambda: '')
    monkeypatch.setattr(transcription, 'convert_caf_to_wav', lambda *_: track)

    def fail(*_, **__):
        raise transcription.RepetitionError('Unresolved transcription repetition')

    monkeypatch.setattr(transcription, 'run_whisper', fail)
    summaries = []
    monkeypatch.setattr(transcription.post_call, 'run_session', summaries.append)
    with pytest.raises(transcription.TranscribeError, match='repetition'):
        transcription.process_session(tmp_path, lambda _: None)
    assert summaries == []
    assert not (tmp_path / 'transcript.json').exists()


def test_repetition_matching_ignores_case_and_punctuation():
    rows = [segment(i * 1000, (i + 1) * 1000, text)
            for i, text in enumerate(['Synthetic repeated phrase!', 'SYNTHETIC repeated phrase.'] * 15)]
    assert transcription.repetition_spans(rows) == [(0, 30)]


def test_recovery_cannot_return_loop_spread_across_chunks(tmp_path, monkeypatch):
    looping = [segment(0, 90000, ' '.join(['Synthetic repeated phrase.'] * 150))]
    repeated = [segment(0, 1000, ' '.join(['Another synthetic phrase.'] * 3))]
    with pytest.raises(transcription.TranscribeError, match='repetition'):
        run_decoder(tmp_path, monkeypatch, looping, repeated)


def test_hung_recovery_on_default_session_path_requires_attention(tmp_path, monkeypatch):
    import math
    import lib.call_settings

    track = tmp_path / 'synthetic.wav'
    with wave.open(str(track), 'wb') as output:
        output.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
        output.writeframes(b'\0\0' * 16000 * 2)
    model = tmp_path / 'model.bin'
    model.write_bytes(b'synthetic')
    monkeypatch.setattr(transcription, 'read_meta', lambda _: {
        'files': {'mic': track.name, 'system': track.name}, 'start_offset_ms': {}})
    monkeypatch.setattr(transcription, 'read_consent', lambda _: {})
    monkeypatch.setattr(transcription, 'read_speaker_labels', lambda _: {'mic': 'A', 'system': 'B'})
    monkeypatch.setattr(transcription, 'resolve_model', lambda _: model)
    monkeypatch.setattr(transcription, 'verify_model_pin', lambda *_: None)
    monkeypatch.setattr(transcription, 'load_prompt', lambda: '')
    monkeypatch.setattr(transcription, 'convert_caf_to_wav', lambda *_: track)
    monkeypatch.setattr(lib.call_settings, 'configure_environment', lambda: None)
    monkeypatch.setattr(transcription.post_call, 'now', lambda: '2026-01-01T00:00:00Z')
    summaries = []
    monkeypatch.setattr(transcription.post_call, 'run_session', summaries.append)
    processes = []

    class Decoder:
        def __init__(self, cmd, **kwargs):
            self.args = cmd
            self.returncode = None
            self.killed = False
            self.reaped = False
            self.timeout = None
            self.recovery = '-mc' in cmd
            processes.append(self)
            if not self.recovery:
                target = Path(cmd[cmd.index('-of') + 1]).with_suffix('.json')
                target.write_text(json.dumps({'transcription': [{
                    'offsets': {'from': 0, 'to': 1000},
                    'text': ' '.join(['Synthetic repeated phrase.'] * 150)}]}))

        def __enter__(self):
            return self

        def __exit__(self, *_):
            self.wait()

        def communicate(self, input=None, timeout=None):
            if self.recovery and not self.killed:
                self.timeout = timeout
                assert timeout is not None and math.isfinite(timeout) and timeout > 0, (
                    'recovery would wait forever without a finite subprocess timeout')
                raise subprocess.TimeoutExpired(self.args, timeout)
            self.returncode = -9 if self.killed else 0
            return '', ''

        def kill(self):
            self.killed = True

        def wait(self):
            self.reaped = True
            self.returncode = -9 if self.killed else 0
            return self.returncode

        def poll(self):
            return self.returncode

    monkeypatch.setattr(transcription.subprocess, 'Popen', Decoder)
    assert transcription.main(['transcribe_session.py', str(tmp_path)]) == 1
    assert len(processes) == 2
    recovery = processes[1]
    assert recovery.timeout is not None, 'default session path leaves recovery unbounded'
    assert recovery.killed and recovery.reaped
    assert 'RepetitionError' in (tmp_path / 'transcribe.log').read_text()
    status = json.loads((tmp_path / transcription.STATUS_FILE).read_text())
    assert status['state'] == 'needs_attention'
    assert summaries == []
    assert not (tmp_path / 'transcript.json').exists()
    assert track.exists()


if __name__ == '__main__':
    import tempfile

    with tempfile.TemporaryDirectory() as root:
        result = pytest.main([__file__, '-q', '--basetemp', str(Path(root) / 'cases')])
    raise SystemExit(result)
