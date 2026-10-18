#!/usr/bin/env python3
"""refused_content.py — a blocked markdown write cannot be hidden elsewhere
(rule 76a53dfe).

THE RULE: a blocked markdown write is RE-ROUTED THROUGH THE VERBS, never
written somewhere the gate does not look.

THE HOLE, named in the rule's own text and still open when probed on
2026-08-14. The record-home gate refuses record content aimed at the vault,
through the file tool and — since the Bash hole closed the same day — through
the shell as well. The identical content written to a scratchpad or the temp
directory was refused by nothing. The content lands somewhere the record layer
never sees, which is the outcome the rule exists to prevent; only the
destination changed.

WHY THE OBVIOUS FIX IS THE WRONG ONE, and this is the whole design. Refusing
markdown writes to scratch paths would refuse the pull-request bodies, probe
scripts and backups every session writes constantly — the session that built
this wrote nine such files while building it. That gate gets removed within a
day, and then the hole is open again with a note saying it was tried.

So the memory is SEQUENTIAL rather than positional. What is remembered is the
content a gate just REFUSED; what is stopped is substantially the same content
reappearing anywhere afterwards. A scratch write unrelated to any refusal stays
free, which is almost all of them.

MATCHING IS ON THE BODY, NOT THE BYTES. Whitespace, case and a swapped heading
must not launder a record past this, so comparison is over normalised content
lines with the majority of them shared. A single quoted line is deliberately
NOT a match: quoting one bullet into a note is discussion, not hiding, and a
gate that refused that would be the noise-generating version of itself.

FAILS OPEN everywhere, and never raises into a caller: a memory that cannot be
read costs one comparison, and a gate that wedges on its own bookkeeping is
worse than the hole it closes.
"""

import hashlib
import json
import os
import re

# Storage lives beside the other gate state, under the repo's out/ directory.
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_HOME = os.path.join(REPO, "out", "refused-content")

# Bounded on purpose: a session that gets refused repeatedly must not grow an
# unbounded file, and only recent refusals are plausibly being re-routed.
MAX_REMEMBERED = 25

# Below this many content lines a document is too small to fingerprint without
# matching everything; a short note is not how a record gets hidden.
MIN_LINES = 3
# Share of the NEW document's lines that must already have been refused. High,
# because the failure is the same content moved, not a document that happens to
# quote some of it.
MATCH_SHARE = 0.6


def _lines(text):
    """Normalised content lines: the body, with formatting removed.

    Headings are dropped rather than normalised — swapping the title is the
    cheapest possible laundering, so the heading must not be part of identity.
    """
    out = []
    for raw in (text or "").splitlines():
        line = raw.strip().lower()
        if not line or line.startswith("#"):
            continue
        line = re.sub(r"^[-*+]\s+", "", line)          # list markers
        line = re.sub(r"[*_`]+", "", line)             # emphasis
        line = " ".join(line.split())                  # runs of whitespace
        if len(line) > 12:
            out.append(line)
    return out


def _fingerprint(text):
    return [hashlib.sha256(l.encode()).hexdigest()[:16] for l in _lines(text)]


def _path(session_id, home):
    safe = "".join(c for c in (session_id or "") if c.isalnum() or c in "-_")
    return os.path.join(home, f"{safe or 'unknown'}.json")


def remember_refusal(content, session_id, home=DEFAULT_HOME):
    """Record content a gate just refused. NEVER raises into the caller."""
    try:
        marks = _fingerprint(content)
        if len(marks) < MIN_LINES:
            return
        os.makedirs(home, exist_ok=True)
        path = _path(session_id, home)
        try:
            with open(path) as fh:
                seen = json.load(fh).get("seen", [])
        except Exception:                                     # noqa: BLE001
            seen = []
        seen.append(marks)
        with open(path, "w") as fh:
            json.dump({"seen": seen[-MAX_REMEMBERED:]}, fh)
    except Exception:                                         # noqa: BLE001
        pass


def was_refused(content, session_id, home=DEFAULT_HOME):
    """(bool, share) — is this substantially content a gate already refused?

    Fails OPEN: an unreadable or absent memory is no opinion, never a block.
    """
    try:
        marks = _fingerprint(content)
        if len(marks) < MIN_LINES:
            return False, 0.0
        with open(_path(session_id, home)) as fh:
            seen = json.load(fh).get("seen", [])
        best = 0.0
        for earlier in seen:
            prior = set(earlier)
            if not prior:
                continue
            share = sum(1 for m in marks if m in prior) / len(marks)
            best = max(best, share)
        return best >= MATCH_SHARE, best
    except Exception:                                         # noqa: BLE001
        return False, 0.0


REFUSAL = (
    "REFUSED — this is content a gate already blocked from the vault, and "
    "writing it here would hide it rather than re-route it (rule 76a53dfe).\n\n"
    "{pct}% of this document is text refused earlier in this session. The "
    "block was not about WHERE the file goes; it was that a record belongs in "
    "the record layer, where catch-me-up and every render can see it. A copy "
    "in a scratch file is invisible to all of them.\n\n"
    "ROUTE IT THROUGH THE VERBS instead — log-decision for a settled call, "
    "record-finding for something learned, add-loop for work that must "
    "survive, write-doctrine-section for doctrine. If this genuinely is a "
    "throwaway rather than a record, it should not be the same words the "
    "vault refused."
)
