#!/usr/bin/env python3
"""drift-claim-gate.py — before asserting something drifted, show the decision.

THE FAILURE CLASS THIS TARGETS is the most common one this system has:
"dated-artifact-read-as-present-state", running since 2026-08-04, with the
majority caught by a human rather than by the system. The shape is always the
same.

NO COUNT IS WRITTEN HERE, and that is a correction. This file carried "8
occurrences" in its docstring and in the message it prints; by 2026-08-15 the
ledger read twelve. A gate whose whole job is catching a stale figure quoted as
present state cannot quote a stale figure as present state in the sentence meant
to persuade — rule b01edd26 bans exactly that, and a session that spots the wrong
number has been handed a reason to discount everything after it. A PreToolUse
hook must stay fast and offline, so it cannot ask the record layer for the live
value; `standing-context` returns it to any session that wants the integer.
Fixed alongside hooks/drift-assertion-gate.py, which carried the identical
defect — the Stop door and this write door are one mechanism and drift together.
A session reads a CURRENT artifact that is perfectly accurate about the present
(a status line, a file's contents, a script's output, a count) and concludes
that something has drifted, regressed, or was never finished, without checking
whether the present state was CHOSEN.

THE INSTANCE THAT MOTIVATED THIS, 2026-08-13. A session read the gate-integrity
banner and harden-gates.sh reporting "0 hardened · 18 unprotected", concluded
the enforcement layer had silently regressed, filed a defect asserting a
completed loop had been closed without its work landing, and put a hot item in
front of Joe asking him to run the hardening. All wrong. Joe had authorized
that hardening on 2026-08-09, watched it work, and deliberately REVERSED it on
2026-08-10 because a buggy gate would then need his password to fix. The
reversal was recorded in decision-history the whole time. Two artifacts were
read; the one that explained them was not.

WHY A NAG WOULD NOT WORK. The failure classes are already surfaced at every
session start, including this one, and the session still failed. "Remember to
check" is the same prose that has failed repeatedly before. So this hook does
not remind anyone of anything: it RUNS THE SEARCH ITSELF and puts the matching
decisions in front of the session at the moment it is about to make the claim.

IT SPEAKS ONLY WHEN IT HAS SOMETHING TO SAY, which is the whole anti-alarm-
fatigue design. Two conditions must both hold: the text reads like a drift
claim, AND the decision history actually contains something about the subject.
A drift claim with no matching decision is probably a real finding and passes
in silence. That silence is deliberate: a gate that fires on everything gets
clicked past, and then it catches nothing at all (the same argument
rule-shape-gate.py makes).

IT NEVER DENIES. Real drift must always be filable. Blocking a defect filing
would break the one mechanism this system has for learning from its own errors.
It attaches context and gets out of the way.

FAILS OPEN on any error. Logged to out/hook-guard.log.
"""

import json
import os
import re
import sys
from datetime import datetime, timezone

# Script-relative, NOT expanduser("~/carr-system") — same fix as
# hooks/record-home-gate.py and the tools/test-*.py suites (commit fad87a4).
# Log path only here, so a clone outside $HOME loses the audit trail rather
# than mis-enforcing.  Decisions are read from the canonical record view; this
# hook never discovers a mounted file tree through its environment.
REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:                                    # telemetry only — never load-bearing
    import hook_meter
    LOG = hook_meter.guard_log_path(REPO)
except Exception:                       # a missing meter must not change a verdict
    LOG = os.path.join(REPO, "out", "hook-guard.log")
NONCANONICAL_DECISIONS_PATH = "CARR_NONCANONICAL_DECISIONS_PATH"

# Fields worth reading across the verbs this watches.
TEXT_FIELDS = ("claimed", "actual", "body", "title", "outcome", "blocker_detail",
               "source_note", "unblocks", "cost_note", "source_unread")

# Language that asserts a present state is WRONG rather than chosen.
DRIFT = re.compile(
    r"\b(never (?:ran|applied|landed|happened|shipped|executed)|"
    r"was not (?:run|applied|done|actually)|not actually|"
    r"closed without|without its work|did not land|"
    r"drift(?:ed|ing)?|regress(?:ed|ion)|reverted|rolled back|"
    r"silently (?:un|re|dis)|no longer (?:match|hold|appl)|"
    r"still (?:not|un)|stale|out of date|contradict(?:s|ed)|"
    r"should have been|was supposed to|unprotected|never took effect)\b", re.I)

# Tokens worth searching the decision log for: hyphenated names, filenames,
# and long words. Short and generic words would match everything.
TOKEN = re.compile(r"\b([a-z][a-z0-9]*(?:[-_][a-z0-9]+)+(?:\.[a-z]+)?|[a-z]{6,})\b", re.I)

STOP = {
    "should", "actually", "without", "because", "however", "already", "session",
    "sessions", "system", "record", "records", "recorded", "closed", "closing",
    "filed", "filing", "finding", "findings", "before", "against", "instead",
    "reported", "reports", "report", "nothing", "something", "anything", "another",
    "current", "present", "artifact", "artifacts", "verified", "confirm", "confirmed",
    "message", "messages", "written", "writing", "thing", "things", "matter", "matters",
    "change", "changed", "changes", "working", "working-tree", "produce", "produced",
    "result", "results", "wrong", "correct", "correctly", "itself", "which", "whose",
    "state", "states", "stated", "would", "could", "still", "never", "drift", "drifted",
    # Adverbs and connectives, added after they beat the real subject on the
    # 2026-08-13 replay. "afterwards" happened to be rarer in the log than
    # "harden" and so outranked the governing ruling. These carry no subject,
    # so no match on them can ever be the decision you are looking for.
    "afterwards", "therefore", "otherwise", "previously", "currently", "entirely",
    "deliberately", "immediately", "eventually", "generally", "obviously",
    "running", "either", "status", "reported", "concretely", "explicitly",
    "apparently", "presumably", "accordingly", "meanwhile", "regardless",
}

MAX_TOKENS = 12
MAX_HITS = 6


def log(msg):
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        ts = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
        with open(LOG, "a") as fh:
            fh.write(f"{ts} drift-claim-gate {msg.rstrip()}\n")
    except Exception:
        pass


def claim_text(ti):
    parts = []
    for k in TEXT_FIELDS:
        v = ti.get(k)
        if isinstance(v, str) and v.strip():
            parts.append(v)
    return "\n".join(parts)


_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+|\n{2,}|\n(?=\s*(?:[-*•]|\d+[.)])\s)")


def drift_window(text):
    """The sentences that carry the drift language, each with the sentence
    before it — the subject the claim is ABOUT lives there, not three
    paragraphs away.

    Added 2026-10-12 after the chat door blocked six times in one orchestrator
    session on rulings tagged "chasin", "pauses", "undone", "panels",
    "forged": every one of them a rare stem lifted from an UNRELATED paragraph
    of a long status update that happened to contain one drift phrase. Rarity
    weighting was right about which stems to trust; it was wrong about where
    to look for them. A long reply is many subjects, and only the sentence
    around the drift phrase names the one being called wrong. Text with no
    drift phrase comes back whole, so the write door's short claims are
    unchanged."""
    parts = [p for p in _SENTENCE_SPLIT.split(text or "") if p and p.strip()]
    if len(parts) < 3:
        return text or ""
    keep = set()
    for i, part in enumerate(parts):
        if DRIFT.search(part):
            keep.update((i - 1, i))
    if not keep:
        return text or ""
    return " ".join(parts[i] for i in sorted(keep) if 0 <= i < len(parts))


def salient_tokens(text):
    """Distinctive tokens, most specific first: hyphenated and dotted names
    before plain long words, because 'harden-gates.sh' identifies a subject and
    'authorized' does not."""
    seen, hyphen, plain = set(), [], []
    for m in TOKEN.finditer(text):
        t = m.group(1).lower()
        if t in STOP or t in seen:
            continue
        seen.add(t)
        (hyphen if ("-" in t or "_" in t or "." in t) else plain).append(t)
    return (hyphen + plain)[:MAX_TOKENS]


# A token earns its place by being RARE in the decision log, not by its shape.
# Two earlier designs failed on the 2026-08-13 replay and are recorded so they
# are not retried: matching any single token surfaced four unrelated July
# entries because the word "claude" is everywhere; weighting hyphenated names
# above plain words missed the ruling entirely, because the ruling reads "OS
# hardening of the gates is REVERSED" and never names a file, so the only
# useful word in it was an ordinary one.
#
# Rarity handles both. A token in more than this share of lines says nothing
# about which line matters, so it is discarded; anything rarer is
# discriminative enough that one match is worth showing. "harden" appears in 4
# of 536 lines and identifies the subject exactly; "claude" appears in dozens
# and identifies nothing.
GENERIC_SHARE = 0.01
GENERIC_FLOOR = 3

# A line must clear this weighted score to be shown at all. 0.2 means "at least
# one stem that appears in five lines or fewer". Below that the match is a
# common word coincidence, and showing it is worse than showing nothing.
MIN_SCORE = 0.2

# Stems, so "hardening", "hardened" and "harden-gates.sh" are one subject. The
# ruling and the claim rarely use the same inflection.
STEM_LEN = 6


def stem(token):
    alpha = re.match(r"[a-z]+", token)
    return (alpha.group(0)[:STEM_LEN] if alpha else token[:STEM_LEN])


def _record_decision_lines():
    """Read decision text from the canonical record view, newest first.

    This is intentionally a small, fail-open hook read: unavailable record
    credentials or an unreachable reader simply produce no context, as the old
    file reader did.  The named noncanonical path is test/recovery injection
    only; it never consults an ambient synced-root setting.
    """
    fixture = os.environ.get(NONCANONICAL_DECISIONS_PATH)
    if fixture:
        try:
            with open(fixture, "r", encoding="utf-8", errors="replace") as fh:
                return [line for line in fh.read().splitlines() if len(line.strip()) >= 20]
        except OSError:
            return []
    try:
        sys.path.insert(0, REPO)
        from lib.record_sources import _connect
        with _connect() as conn, conn.cursor() as cur:
            cur.execute("""
                select entry_date, title, human_quote, agent_rationale, cause
                  from v_decision_entry
                 order by occurred_at desc nulls last, event_id desc
                 limit 2000
            """)
            rows = cur.fetchall()
    except Exception:
        return []
    lines = []
    for entry_date, title, human_quote, rationale, cause in rows:
        text = " — ".join(str(value).strip() for value in
                           (entry_date, title, human_quote, rationale, cause)
                           if isinstance(value, str) and value.strip())
        if len(text) >= 20:
            lines.append(text)
    return lines


def search_decisions(tokens):
    """Lines whose subject the claim is actually about, strongest first.

    ORDER NOTE, measured rather than assumed: this render is NEWEST-FIRST. The
    2026-08-10 hardening reversal sits at line 382 while a 2026-07-08 entry is
    at line 727. An earlier version scanned in reverse "to get the most
    recent", which did exactly the opposite. File order IS recency order, and
    is the tiebreak between equally-scoring lines.
    """
    lines = _record_decision_lines()
    if not lines:
        return []
    lowered = [l.lower() for l in lines]
    cutoff = max(GENERIC_FLOOR, int(len(lines) * GENERIC_SHARE))

    stems = []
    for t in tokens:
        s = stem(t)
        if s and s not in stems and len(s) >= 4:
            stems.append(s)

    # Weight each surviving stem by its RARITY, not by counting matches equally.
    # Counting equally was the third failed design, on the same replay: junk
    # stems from ordinary words ("runnin" from running, "either", "status")
    # each matched a dozen lines, so a line carrying three of them outscored
    # the one line carrying "harden", and the actual ruling was pushed off the
    # end of the list. One rare word beats three common ones, which is the only
    # ranking that puts the governing decision first.
    weights = {}
    for s in stems:
        n = sum(1 for l in lowered if s in l)
        if 0 < n <= cutoff:
            weights[s] = 1.0 / n
    if not weights:
        return []

    scored = []
    for idx, low in enumerate(lowered):
        matched = [s for s in weights if s in low]
        if not matched:
            continue
        score = sum(weights[s] for s in matched)
        if score < MIN_SCORE:
            continue
        matched.sort(key=lambda s: -weights[s])
        scored.append((-score, idx, ", ".join(matched[:3]), lines[idx].strip()))
    scored.sort()
    return [(m, line) for _, _, m, line in scored[:MAX_HITS]]


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from lib.hook_runtime import decision, run


def _parse_error(exc):
    log(f"ALLOW(parse-error) {exc}")
    return 0


def _decision_error(exc):
    log(f"ALLOW(internal-error) {exc}")
    return 0


@decision(on_error=_decision_error)
def decide(payload):
    tool = payload.get("tool_name") or payload.get("toolName") or ""
    if not re.search(r"(record-defect|add-loop)$", tool):
        sys.exit(0)

    ti = payload.get("tool_input") or payload.get("toolInput") or {}
    if not isinstance(ti, dict):
        sys.exit(0)

    text = claim_text(ti)
    if len(text) < 60 or not DRIFT.search(text):
        sys.exit(0)

    hits = search_decisions(salient_tokens(drift_window(text)))
    if not hits:
        # A drift claim with no matching ruling is probably a real finding.
        # Silence is the correct output here.
        sys.exit(0)

    body = "\n".join(f"  · [{t}] {line[:300]}" for t, line in hits)
    log(f"CONTEXT tool={tool} hits={len(hits)}")
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "allow",
            "additionalContext": (
                "DRIFT CLAIM — THE DECISION LOG HAS SOMETHING ON THIS SUBJECT. Read it before "
                "this write lands.\n\n"
                "You are asserting that a present state is wrong rather than chosen. That is the "
                "most common way this system has been wrong, running since 2026-08-04, most of "
                "them caught by a human — `standing-context` returns the live count if you want "
                "it. The pattern is always a CURRENT artifact read accurately and a DECISION "
                "behind it left unread. On 2026-08-13 a session read a status banner as evidence of "
                "regression and asked Joe to redo something he had deliberately reversed three days "
                "earlier.\n\nMatching rulings, newest first:\n\n" + body +
                "\n\nIf one of these explains the state you are calling drift, this write is wrong: "
                "the state was chosen, and the finding is either nothing or a stale prompt that "
                "should be corrected instead. If none of them apply, proceed — a drift claim with no "
                "governing decision is usually real."
            ),
        }
    }))
    sys.exit(0)


def main():
    sys.exit(run(decide, parse_error=_parse_error))


if __name__ == "__main__":
    sys.exit(main())
