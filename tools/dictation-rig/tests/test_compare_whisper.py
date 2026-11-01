"""Synthetic-only accuracy scoring through the comparison's public interface."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'bin'))
import compare_whisper as comparison


@pytest.mark.parametrize(('reference', 'hypothesis', 'errors', 'words'), [
    ('The lease is ready.', 'the LEASE is ready!', 0, 4),
    ('the lease is ready', 'the lease was ready', 1, 4),
    ('the lease is ready', 'the lease ready', 1, 4),
    ('the lease is ready', 'the new lease is ready now', 2, 4),
    ('lease', 'one two three', 3, 1),
    ('we cannot sign', '', 3, 3),
    ('', '[BLANK_AUDIO]', 0, 0),
    ("Don't sign yet", 'don’t sign yet.', 0, 3),
])
def test_word_errors(reference, hypothesis, errors, words):
    assert comparison.score(reference, hypothesis) == {
        'errors': errors, 'reference_words': words, 'wer': errors / words if words else None,
    }


def test_silence_is_scored_without_dividing_by_zero():
    assert comparison.score('', 'Thank you.') == {
        'errors': 2, 'reference_words': 0, 'wer': None,
    }


@pytest.mark.parametrize('fail', [True, False])
def test_comparison_cli_runs_both_models_and_keeps_failures_visible(tmp_path, monkeypatch, fail):
    import json
    import subprocess
    import wave
    import transcribe_session

    models = [tmp_path / 'first.bin', tmp_path / 'second.bin']
    for model in models:
        model.write_bytes(model.name.encode())
    binary = tmp_path / 'whisper-cli'
    binary.write_bytes(b'synthetic executable')
    monkeypatch.setattr(transcribe_session, 'WHISPER_CLI_BIN', str(binary))
    seen = []

    def external(cmd, **kwargs):
        if cmd[0] == transcribe_session.WHISPER_CLI_BIN:
            model = Path(cmd[cmd.index('-m') + 1]).name
            audio = Path(cmd[cmd.index('-f') + 1])
            seen.append((model, audio.name))
            if fail and model == 'second.bin' and audio.stem == 'lease':
                return subprocess.CompletedProcess(cmd, 1, '', 'synthetic failure')
            target = Path(cmd[cmd.index('-of') + 1]).with_suffix('.json')
            target.write_text(json.dumps({'transcription': [
                {'text': 'The lease is ready.', 'offsets': {'from': 0, 'to': 1000}}
            ]}))
        elif cmd[0] == '/usr/bin/say':
            Path(cmd[cmd.index('-o') + 1]).write_bytes(b'synthetic')
        elif cmd[0] == '/usr/bin/afconvert':
            with wave.open(cmd[-1], 'wb') as audio:
                audio.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
                audio.writeframes(b'\0\0' * 16000)
        else:
            raise AssertionError(cmd)
        return subprocess.CompletedProcess(cmd, 0, '', '')

    monkeypatch.setattr(subprocess, 'run', external)
    output = tmp_path / 'comparison'
    assert comparison.main(['--model', str(models[0]), '--model', str(models[1]),
                            '--trials', '1', '--output', str(output)]) == int(fail)
    result = json.loads((output / 'results.json').read_text())
    assert result['complete'] is (not fail)
    assert result['failures'] == int(fail)
    if fail:
        assert result['summary'] is None
    else:
        assert len(result['summary']) == 2
        assert result['summary'][0]['silence_insertions_by_trial'] == [4]
        assert result['summary'][0]['corpus_wer_by_trial'] == [67 / 74]
    assert len(result['rows']) == 8
    assert {model for model, _ in seen} == {'first.bin', 'second.bin'}
    assert all(row['audio_sha256'] for row in result['cases'])


def test_comparison_rejects_identical_models_before_creating_output(tmp_path):
    model = tmp_path / 'model.bin'
    model.write_bytes(b'identical')
    output = tmp_path / 'comparison'
    assert comparison.main(['--model', str(model), '--model', str(model),
                            '--output', str(output)]) == 1
    assert not output.exists()


if __name__ == '__main__':
    import tempfile

    with tempfile.TemporaryDirectory() as root:
        result = pytest.main([__file__, '-q', '--basetemp', str(Path(root) / 'cases')])
    raise SystemExit(result)
