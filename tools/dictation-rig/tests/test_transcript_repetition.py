"""Verify Whisper repetition recovery before call summaries."""
import json
import subprocess
import sys
import wave
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'bin'))
import transcribe_session as transcription


def segment(start, end, text):
    return transcription.RawSegment(start, end, text)


def run_decoder(tmp_path, monkeypatch, initial, recovered):
    audio = tmp_path / 'synthetic.wav'
    with wave.open(str(audio), 'wb') as output:
        output.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
        output.writeframes(b'\0\0' * 16000 * 90)
    calls = []

    def decode(cmd, **kwargs):
        calls.append((cmd, kwargs))
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
def test_loop_is_redecoded_and_normal_neighbors_survive(tmp_path, monkeypatch, packed):
    before = segment(0, 1000, 'The synthetic proposal is ready.')
    after = segment(61000, 62000, 'Review the revised plan next week.')
    phrase = 'Please revise the synthetic plan.'
    looping = ([segment(1000, 61000, ' '.join([phrase] * 150))] if packed else
               [segment(1000 + i * 400, 1400 + i * 400, phrase) for i in range(150)])
    recovered = [segment(0, 1000, 'The recovered speech has useful details.')]
    result, calls = run_decoder(tmp_path, monkeypatch, [before, *looping, after], recovered)
    assert result == [before, segment(1000, 2000, recovered[0].text),
                      segment(31000, 32000, recovered[0].text), after]
    assert len(calls) == 3
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

    def fail(*_):
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


if __name__ == '__main__':
    import tempfile

    with tempfile.TemporaryDirectory() as root:
        result = pytest.main([__file__, '-q', '--basetemp', str(Path(root) / 'cases')])
    raise SystemExit(result)
