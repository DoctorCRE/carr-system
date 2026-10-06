"""lib/transcript_read.py — read a session transcript for a gate WITHOUT letting
one bad line switch the gate off.

THIS IS A LIBRARY, ON PURPOSE: no shebang and no main guard (see the header of
ops/typesafe_client.py for why that matters to the sealed source inventory).

WHY (bypass hunt on PR #1224, 2026-09-24). Four gates parsed the transcript with
a list comprehension over json.loads. A session can append to its own
transcript, so ONE non-JSON line (or a JSON line that is not an object) raised,
the gate's blanket except returned 0, and every check that gate makes passed:
fail-open by exception, reachable on purpose. Every other transcript reader in
hooks/ already skipped such lines; this module makes the four do the same, and
records each skip as a `transcript_tamper` event so the skip itself is visible.

A skipped line is never a verdict: the gate still evaluates every good record.
Logging is best-effort and never raises.
"""

import json
import os
from datetime import datetime, timezone

TAMPER_EVENT = "transcript_tamper"


def load_transcript(path, *, hook, session=None, log_path=None):
    """Every JSON-object record in `path`, in order. A blank line is ignored;
    a line that is not valid JSON, or valid JSON that is not an object, is
    skipped and counted. When any were skipped and `log_path` is given, one
    `transcript_tamper` row is appended there (unless session == "selftest").
    Raises only what open() raises for the file itself."""
    records, bad = [], []
    with open(path, errors="replace") as handle:
        for number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                obj = json.loads(line)
            except ValueError:
                bad.append(number)
                continue
            if isinstance(obj, dict):
                records.append(obj)
            else:
                bad.append(number)
    if bad and log_path and session != "selftest":
        try:
            os.makedirs(os.path.dirname(log_path), exist_ok=True)
            with open(log_path, "a", encoding="utf-8") as out:
                out.write(json.dumps({
                    "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "event": TAMPER_EVENT, "hook": hook, "session": session,
                    "transcript_path": path, "skipped_lines": len(bad),
                    "first_line_numbers": bad[:10]}) + "\n")
        except Exception:
            pass
    return records
