#!/usr/bin/env bash
set -euo pipefail

BIN="$(cd "$(dirname "$0")" && pwd)"
TMP="$(mktemp -d)"
cleanup() {
  rm -rf "$TMP"
}
trap cleanup EXIT

cat >"$TMP/claude" <<'PY'
#!/usr/bin/env python3
import json
import os
import pathlib
import sys
import time
import uuid

count = pathlib.Path(os.environ["FAKE_CLAUDE_COUNT"])
count.write_text(str(int(count.read_text()) + 1 if count.exists() else 1))
session_id = str(uuid.uuid4())
remembered = ""

def emit(value):
    print(json.dumps(value), flush=True)

for line in sys.stdin:
    request = json.loads(line)
    text = request["message"]["content"][0]["text"]
    emit({"type": "system", "subtype": "init", "session_id": session_id})
    if text == "die now":
        sys.exit(7)
    if "Remember" in text:
        remembered = "ORCHID"
        chunks = [
            "First sentence complete. ",
            "Second sentence complete.\n",
            'CARD: {"title": "Details. Here", "rows": []}',
        ]
    elif "one before that" in text:
        chunks = [f"The one before that was {remembered}."]
    else:
        chunks = ["Fresh process answered safely."]
    full = ""
    for index, chunk in enumerate(chunks):
        full += chunk
        emit({
            "type": "stream_event",
            "event": {
                "type": "content_block_delta",
                "delta": {"type": "text_delta", "text": chunk},
            },
            "session_id": session_id,
        })
        if index == 0 and len(chunks) > 1:
            time.sleep(0.35)
    emit({
        "type": "result", "subtype": "success", "is_error": False,
        "result": full, "session_id": session_id,
    })
PY
chmod +x "$TMP/claude"

HOME="$TMP" DOC_ROUTE_SHADOW=0 PATH="$TMP:$PATH" FAKE_CLAUDE_COUNT="$TMP/count" python3 - "$BIN" "$TMP" <<'PY'
import pathlib
import sys
import time

sys.path.insert(0, sys.argv[1])
import convo_core

tmp = pathlib.Path(sys.argv[2])
convo_core.SESSION_FILE = tmp / "session-id"
sentences = []
completed = []
started = time.monotonic()

def on_sentence(sentence):
    sentences.append((sentence, time.monotonic()))

reply, brain = convo_core.ask_brain_streaming(
    "Remember ORCHID", "test prompt", on_sentence=on_sentence,
    on_complete=completed.append,
)
returned = time.monotonic()
assert brain.returncode == 0
assert sentences[0][0] == "First sentence complete."
assert sentences[0][1] < returned
assert returned - sentences[0][1] >= 0.25
assert [item[0] for item in sentences] == [
    "First sentence complete.", "Second sentence complete.",
]
assert "CARD:" in reply
assert completed == [reply]
assert sentences[0][1] - started < returned - started

for silent_text in ("", " ", "\n\t"):
    silent_completed = []
    silent, brain = convo_core.ask_brain_streaming(
        silent_text, "test prompt", on_complete=silent_completed.append,
    )
    assert brain.returncode == 0
    assert silent == ""
    assert silent_completed == [""]
assert (tmp / "count").read_text() == "1"

memory, brain = convo_core.ask_brain_streaming(
    "what about the one before that?", "test prompt",
)
assert brain.returncode == 0
assert memory == "The one before that was ORCHID."
assert (tmp / "count").read_text() == "1"

partial, brain = convo_core.ask_brain_streaming("die now", "test prompt")
assert brain.returncode != 0
assert partial == ""
fresh, brain = convo_core.ask_brain_streaming("are you back?", "test prompt")
assert brain.returncode == 0
assert fresh == "Fresh process answered safely."
assert (tmp / "count").read_text() == "2"
convo_core._BRAIN.close()
PY

python3 "$BIN/test-convo-shutdown.py"
echo "brain streaming tests passed"
