#!/usr/bin/env python3
"""reflexes.py — Doc's instant answers (loop #250, Joe 2026-08-08).

Some turns are not questions about the book: "can you hear me," "thanks,"
"say that again." Sending those through transcription -> brain -> render costs
~30 seconds to say a sentence Doc could say instantly. A reflex answers them
from cached speech with no model call at all.

THE SAFETY PROPERTY, and the reason this is a lookup rather than a classifier:
matching is on the WHOLE normalized utterance, never a substring. "Can you hear
me" is a reflex; "can you hear me on the Ferris renewal" is NOT — it falls
through to the brain, because it is a different whole utterance. A reflex can
therefore never intercept a question about a client, a number, or a deal, which
is the only failure that would matter (dialogue law 10: competence first, and a
wrong answer delivered instantly is still wrong).

Adding a reflex: the trigger must be a complete utterance that CANNOT carry
business content, and the reply must be true regardless of context.
"""

import re

# reply -> the whole utterances that trigger it. Replies are Doc's own voice
# (brevity is the personality); they are pre-rendered by render-reflexes.sh so
# every one of these is instant.
REFLEXES: dict[str, tuple[str, ...]] = {
    "I hear you.": (
        "can you hear me", "are you there", "you there", "hello",
        "hey doc", "hi doc", "doc", "you awake", "are you listening",
        "hey doc can you hear me", "doc can you hear me", "testing",
        "test", "is this thing on", "can you hear me now",
    ),
    "Anytime.": (
        "thanks", "thank you", "thanks doc", "thank you doc",
        "appreciate it", "much appreciated", "nice work", "good job",
        "perfect", "awesome", "great", "nice",
    ),
    "Say again?": (
        "what", "huh", "say that again", "come again", "repeat that",
        "one more time", "i missed that", "what was that",
    ),
    "Standing by.": (
        "never mind", "nevermind", "forget it", "hold on", "one second",
        "wait", "hang on", "give me a minute", "nothing",
    ),
    "Talk soon.": (
        "goodbye", "bye", "bye doc", "see you", "later", "that's all",
        "thats all", "we're done", "were done", "im done", "i'm done",
        "goodnight", "good night",
    ),
    "Doing fine. What do you need?": (
        "how are you", "how's it going", "hows it going", "what's up",
        "whats up", "how are you doing", "you good",
    ),
}

# built once: normalized utterance -> reply
_LOOKUP: dict[str, str] = {}
for _reply, _triggers in REFLEXES.items():
    for _t in _triggers:
        _LOOKUP[_t] = _reply


def normalize_utterance(text: str) -> str:
    """Lowercase, strip punctuation and filler-leading words, collapse space.
    Whisper's output carries trailing punctuation and occasional leading
    "um"/"uh"; neither should defeat a reflex."""
    out = text.lower().strip()
    out = re.sub(r"^(um|uh|er|ah|okay|ok|so|well|hey)\b[\s,]*", "", out)
    out = re.sub(r"[^\w\s']", " ", out)
    return re.sub(r"\s+", " ", out).strip()


def match(text: str) -> str | None:
    """The reflex reply for a whole utterance, or None to use the brain."""
    if not text:
        return None
    return _LOOKUP.get(normalize_utterance(text))


def all_replies() -> list[str]:
    """Every reply, for pre-rendering."""
    return list(REFLEXES.keys())


if __name__ == "__main__":
    import sys
    for arg in sys.argv[1:]:
        print(f"{arg!r} -> {match(arg)!r}")
