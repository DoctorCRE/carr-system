# Local Whisper meeting comparison

WR-000639 C5 starts with a synthetic accuracy baseline. This experiment compares
large-v3-turbo with the installed small.en fallback through the rig's existing
`transcribe_session.run_whisper`. It changes no capture defaults.

The fixed corpus has 74 spoken words across lease review, follow-up instructions,
and a tour debrief, plus five seconds of silence. macOS `say` produces the speech
using Samantha and Daniel at 160 words per minute. Each model receives identical
mono 16 kHz PCM16 audio and the tracked vocabulary prompt. The private vocabulary
file is never read. No meeting, microphone, calendar, contact, or network input is used.

`results.json` records all hypotheses, per-case errors, repeated corpus scores,
audio hashes, model hashes, the executable hash, the prompt hash, and the platform.
Five trials alternate model order. Repeats use the same audio and measure decoding
repeatability, not new speakers or statistical independence. The report retains
silence insertions separately from speech word error rate.

Word error rate is word-level edit distance divided by reference word count.
The [speech accuracy documentation](https://docs.cloud.google.com/speech-to-text/docs/v1/speech-accuracy)
describes this measure. This scorer lowercases ASCII words, keeps apostrophes,
ignores punctuation, and removes only the literal `[BLANK_AUDIO]` marker from
hypotheses. Number spellings are not expanded. The marker is a
[Whisper non-speech description](https://github.com/ggml-org/whisper.cpp/issues/2363).
Silence has no WER denominator. Actual words on silence still count as insertions.

## Run the comparison

Both steps are required on a Mac with the two local models already installed.
The first command requires a new output directory and retains its generated audio.
Choose a new directory for another run. The second command verifies completion.

1. Run the fixed synthetic comparison from the repository root.

   ```sh
   .venv/bin/python tools/dictation-rig/bin/compare_whisper.py \
     --model /Users/booko/.cache/whisper-cpp/models/ggml-large-v3-turbo.bin \
     --model /Users/booko/.cache/whisper-cpp/models/ggml-small.en.bin \
     --output out/meeting-comparison
   ```

2. Verify all forty inferences succeeded and the report contains two model summaries.

   ```sh
   .venv/bin/python -c 'import json; r=json.load(open("out/meeting-comparison/results.json")); assert r["complete"] and r["failures"] == 0 and len(r["rows"]) == 40 and len(r["summary"]) == 2'
   ```

Inference failures exit nonzero, record an error type, and suppress the summary.
An interrupted audio-generation run leaves an incomplete receipt when initialization
succeeded. An existing output directory is refused to prevent stale transcript reuse.
The script sends no inherited credentials to audio or inference subprocesses.

## Interpretation

This is a small clean TTS diagnostic, not a production accuracy estimate. It has no
overlapping speech, room noise, accents beyond the two selected voices, or Teams
or Zoom transport artifacts. Model selection must not change from this result.
Both models use the current capture CLI defaults and prompt. No latency or speed
claim is measured. Generated audio stays under `out`; its hashes bind this run.
A Mac with different voice assets can produce different audio and different scores.

C5's synthetic comparison is independently runnable. Finishing WR-000639 still
requires bot capture, the dictation trial, drive-time capture, and validation of
realistic meeting conditions. See the PR's acceptance mapping for those follow-ups.
