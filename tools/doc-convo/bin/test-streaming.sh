#!/usr/bin/env bash
# Test streaming sentence handling for speech output.
set -euo pipefail

BIN="$(cd "$(dirname "$0")" && pwd)"
python3 - "$BIN" <<'PY'
import pathlib
import sys

sys.path.insert(0, sys.argv[1])
import speak

text = ("This opening sentence is comfortably long. "
        "This middle sentence is comfortably long! "
        "This closing sentence is comfortably long?")
units = speak.split_sentences(text)
assert units == [
    "This opening sentence is comfortably long.",
    "This middle sentence is comfortably long!",
    "This closing sentence is comfortably long?",
]

units = speak.split_sentences(
    "Too short. This neighbouring sentence is long enough to render alone.")
assert units == [
    "Too short. This neighbouring sentence is long enough to render alone."
]

units = speak.split_sentences(
    "Brief. Still brief. This neighbouring sentence is long enough by itself.")
assert units == [
    "Brief. Still brief. This neighbouring sentence is long enough by itself."
]
PY
echo "streaming sentence tests passed"
