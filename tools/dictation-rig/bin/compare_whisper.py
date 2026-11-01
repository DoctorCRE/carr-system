#!/usr/bin/env python3
"""Compare local Whisper models on fixed synthetic meeting speech."""
import argparse
import hashlib
import json
import os
import platform
import re
import statistics
import subprocess
import sys
import wave
from datetime import datetime, timezone
from pathlib import Path

import transcribe_session


CASES = (
    ('lease', 'Samantha', 'The lease is ready for review. The tenant needs six operatories and a separate sterilization room. Please confirm the renewal option before we sign.'),
    ('followup', 'Daniel', 'Do not send the proposal yet. First ask the landlord about parking and tenant improvements. We can discuss the build out allowance at the next meeting.'),
    ('debrief', 'Samantha', 'The tour went well. The second floor is acceptable if the elevator works. Follow up on the inspection and request the updated floor plan.'),
    ('silence', None, ''),
)


def score(reference: str, hypothesis: str) -> dict:
    def words(text):
        return re.findall(r"[a-z0-9]+(?:'[a-z0-9]+)*", text.lower().replace('’', "'"))

    expected, actual = words(reference), words(hypothesis.replace('[BLANK_AUDIO]', ''))
    row = list(range(len(actual) + 1))
    for i, left in enumerate(expected, 1):
        following = [i]
        for j, right in enumerate(actual, 1):
            following.append(min(row[j] + 1, following[j - 1] + 1,
                                 row[j - 1] + (left != right)))
        row = following
    return {'errors': row[-1], 'reference_words': len(expected),
            'wer': row[-1] / len(expected) if expected else None}


def sha256(path: Path) -> str:
    with path.open('rb') as source:
        return hashlib.file_digest(source, 'sha256').hexdigest()


def summarize(rows: list[dict], models: list[dict], trials: int) -> list[dict]:
    result = []
    for model in models:
        rates = []
        silence_insertions = []
        for trial in range(trials):
            samples = [row for row in rows if row['model'] == model['name'] and row['trial'] == trial]
            speech = [row for row in samples if row['reference_words']]
            rates.append(sum(row['errors'] for row in speech) /
                         sum(row['reference_words'] for row in speech))
            silence_insertions.append(sum(row['errors'] for row in samples if not row['reference_words']))
        result.append({'model': model['name'], 'corpus_wer_by_trial': rates,
                       'median_wer': statistics.median(rates),
                       'wer_range': [min(rates), max(rates)],
                       'silence_insertions_by_trial': silence_insertions})
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model', type=Path, action='append', required=True)
    parser.add_argument('--output', type=Path, required=True, help='new directory for synthetic audio and results')
    parser.add_argument('--trials', type=int, default=5)
    args = parser.parse_args(argv)
    try:
        if len(args.model) < 2 or not 1 <= args.trials <= 20:
            raise ValueError('Supply at least two models and 1..20 trials')
        models = [{'name': path.name, 'sha256': sha256(path)} for path in args.model]
        if len({m['name'] for m in models}) != len(models) or len({m['sha256'] for m in models}) != len(models):
            raise ValueError('Models must have distinct names and bytes')
        args.output.mkdir(parents=True, exist_ok=False)
        prompt_path = Path(__file__).resolve().parents[1] / 'vocab-prompt.txt'
        prompt = prompt_path.read_text().strip()
        result = {'schema': 'carr.whisper-comparison.v1', 'complete': False,
                  'created_at': datetime.now(timezone.utc).isoformat(),
                  'platform': f'{platform.system()} {platform.release()}', 'machine': platform.machine(),
                  'synthetic_only': True, 'trials': args.trials,
                  'models': models, 'whisper_cli_sha256': sha256(Path(transcribe_session.WHISPER_CLI_BIN)),
                  'prompt_sha256': sha256(prompt_path),
                  'normalization': 'lowercase ASCII words, preserve apostrophes, ignore punctuation; strip Whisper [BLANK_AUDIO]; no number expansion',
                  'cases': [], 'rows': [], 'failures': 0, 'summary': None}
        receipt = args.output / 'results.json'
        receipt.write_text(json.dumps(result, indent=2) + '\n')
        for name, voice, reference in CASES:
            wav = args.output / f'{name}.wav'
            if voice:
                aiff = args.output / f'{name}.aiff'
                subprocess.run(['/usr/bin/say', '-v', voice, '-r', '160', '-o', str(aiff), reference],
                               check=True, capture_output=True, timeout=60, env={'PATH': os.defpath})
                subprocess.run(['/usr/bin/afconvert', '-f', 'WAVE', '-d', 'LEI16@16000', '-c', '1', str(aiff), str(wav)],
                               check=True, capture_output=True, timeout=60, env={'PATH': os.defpath})
            else:
                with wave.open(str(wav), 'wb') as audio:
                    audio.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
                    audio.writeframes(b'\0\0' * 16000 * 5)
            with wave.open(str(wav), 'rb') as audio:
                if (audio.getnchannels(), audio.getsampwidth(), audio.getframerate()) != (1, 2, 16000) or audio.getnframes() == 0:
                    raise ValueError('Expected nonempty mono 16 kHz PCM16 audio')
                duration = audio.getnframes() / audio.getframerate()
            result['cases'].append({'id': name, 'voice': voice, 'reference': reference,
                                    'audio_sha256': sha256(wav), 'duration_seconds': duration})
        for trial in range(args.trials):
            order = list(range(len(models)))
            if trial % 2:
                order.reverse()
            for index in order:
                for name, _, reference in CASES:
                    output = args.output / f'trial-{trial}' / str(index)
                    output.mkdir(parents=True, exist_ok=True)
                    row = {'trial': trial, 'model': models[index]['name'], 'case': name}
                    try:
                        segments = transcribe_session.run_whisper(
                            args.output / f'{name}.wav', args.model[index], prompt, output,
                            lambda _: None, timeout=300, env={'PATH': os.defpath})
                        hypothesis = ' '.join(segment.text for segment in segments)
                        row.update(status='ok', hypothesis=hypothesis, **score(reference, hypothesis))
                    except (OSError, ValueError, transcribe_session.TranscribeError, subprocess.SubprocessError) as error:
                        row.update(status='error', error=type(error).__name__)
                        result['failures'] += 1
                    result['rows'].append(row)
                    receipt.write_text(json.dumps(result, indent=2) + '\n')
            print(f'Completed trial {trial + 1}/{args.trials}', flush=True)
        result['complete'] = result['failures'] == 0
        if result['complete']:
            result['summary'] = summarize(result['rows'], models, args.trials)
        receipt.write_text(json.dumps(result, indent=2) + '\n')
        return 0 if result['complete'] else 1
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print(f'Comparison refused or interrupted ({type(error).__name__})', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
