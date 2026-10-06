#!/usr/bin/env python3
"""Phase 0 displacement baselines 1, 2, 3 and 5, measured from local session records.

Doctrine (carr-workspace-bduf s01a) requires these be INSTRUMENTED OR INTERVIEWED
and never invented, and that each carries its evidence method, observation window,
result, bound product action and post-launch comparison. Measure 4 was captured
separately by timed tasks because it measures human comprehension time. These four
are mechanical and need nothing from the partner.

WHAT THIS DOES AND DOES NOT CLAIM
---------------------------------
Everything here is counted, not judged. Where a measure's doctrine wording needs a
judgment call the script reports the mechanical substrate and says so plainly
rather than guessing a number. Doctrine objective 8 explicitly permits heavy
sessions for exploring, investigating genuinely complex problems, and constructing
something new, so total session time is NOT the baseline — only routine-operation
time is, and separating those two is the judgment this script deliberately leaves
open rather than faking.

ACTIVE TIME, NOT WALL CLOCK
---------------------------
A session's first-to-last timestamp span is worthless: a window left open
overnight would read as sixteen hours. Active time is the sum of gaps between
consecutive records within one session, with any gap longer than IDLE_CAP treated
as the partner having walked away and contributing nothing. This undercounts
thinking time spent staring at a screen and overcounts nothing, so it is the
conservative direction.
"""

import json
import os
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lib.carr_session_scope import is_carr_vault_project  # noqa: E402

PROJECTS = Path.home() / ".claude" / "projects"

# THE CARR PROJECT LIVES UNDER TWO TRANSCRIPT ROOTS, NOT ONE, and reading only one
# of them silently measured a third of the corpus. The vault is reachable by two
# paths — the short ~/My Drive/CARR AI and the real Google Drive mount under
# ~/Library/CloudStorage/... — and Claude Code derives a project directory from
# whichever path the session was launched with. Measured 2026-08-13: the
# CloudStorage root holds 99 files and 541.6 MB, the short root holds 137 files and
# 250.0 MB, and the two share ZERO filenames, so the union is clean and no session
# is counted twice. Pinning a single root name measured 250 of 792 MB, 32%.
#
# Discovered rather than hard-coded, so a future mount-path change cannot silently
# shrink the corpus again. Scratchpad projects are excluded: they are throwaway
# session directories, not the business project.
#
# The name test is the vault clause of lib/carr_session_scope.py, the one CARR
# session-scope predicate the nightly session-trace archive also uses. This
# reader deliberately takes ONLY the vault clause, not the repo/worktree
# clauses the archive adds: these baselines measure the partner's business
# sessions, and folding engineering sessions in would silently change a
# recorded measurement's corpus (and the corrections sweep's rule sources).
def _project_roots():
    roots = sorted(
        p
        for p in PROJECTS.iterdir()
        if p.is_dir() and is_carr_vault_project(p.name)
    )
    if not roots:
        sys.exit("no CARR project transcript directory found under %s" % PROJECTS)
    return roots
WINDOW_DAYS = 28
IDLE_CAP = timedelta(minutes=10)

NOW = datetime.now(timezone.utc)
CUTOFF = NOW - timedelta(days=WINDOW_DAYS)


def parse_ts(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def text_of(record):
    """Best-effort plain text of a record, for counting shapes only.

    Never printed. Used to classify a turn's SHAPE (is it a question, does it name
    a verb) and then discarded, so no client content leaves this process.
    """
    msg = record.get("message")
    if isinstance(msg, dict):
        content = msg.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    parts.append(block.get("text") or "")
            return "\n".join(parts)
    content = record.get("content")
    return content if isinstance(content, str) else ""


def tool_names(record):
    msg = record.get("message")
    if not isinstance(msg, dict):
        return []
    content = msg.get("content")
    if not isinstance(content, list):
        return []
    return [
        b.get("name")
        for b in content
        if isinstance(b, dict) and b.get("type") == "tool_use" and b.get("name")
    ]


def load_sessions():
    """Return {session_id: [records]} for Joe's CARR project inside the window.

    Reads EVERY CARR transcript root, not one — see _project_roots above. Records
    key on sessionId, so a session that somehow appeared under both roots would
    merge rather than double-count; measured overlap is zero filenames either way.
    """
    sessions = defaultdict(list)
    files = sorted(f for root in _project_roots() for f in root.glob("*.jsonl"))
    for path in files:
        try:
            with path.open(encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    ts = parse_ts(rec.get("timestamp"))
                    if ts is None or ts < CUTOFF:
                        continue
                    sid = rec.get("sessionId") or path.stem
                    rec["_ts"] = ts
                    sessions[sid].append(rec)
        except OSError:
            continue
    for recs in sessions.values():
        recs.sort(key=lambda r: r["_ts"])
    return sessions, len(files)


def active_intervals(records):
    """Active stretches within one session, as (start, end) pairs.

    Returned as intervals rather than a scalar because summing per-session totals
    DOUBLE COUNTS: sessions run concurrently — subagents, background tasks, more
    than one window — and the first version of this script summed them, producing
    87 active hours per week for one person, which is over twelve hours a day
    every day. Wall clock cannot be added up; it has to be unioned.
    """
    spans = []
    for earlier, later in zip(records, records[1:]):
        gap = later["_ts"] - earlier["_ts"]
        if timedelta(0) <= gap <= IDLE_CAP:
            spans.append((earlier["_ts"], later["_ts"]))
    return spans


def union_seconds(spans):
    """Total wall-clock covered by a set of possibly overlapping intervals."""
    if not spans:
        return 0.0
    spans = sorted(spans)
    total = 0.0
    cur_start, cur_end = spans[0]
    for start, end in spans[1:]:
        if start > cur_end:
            total += (cur_end - cur_start).total_seconds()
            cur_start, cur_end = start, end
        elif end > cur_end:
            cur_end = end
    total += (cur_end - cur_start).total_seconds()
    return total


def is_typed_prompt(record):
    """True when this looks like the partner actually typing, not machinery.

    Excludes tool results, SDK-driven prompts (subagent and scripted traffic), and
    the hook/system feedback that arrives wearing a user role. This is a filter on
    SHAPE, never on content, and it is imperfect — it is reported as a floor
    rather than an exact count.
    """
    if record.get("type") != "user":
        return False
    if record.get("isSidechain"):
        return False
    # promptSource is NOT a usable signal for "the partner typed this", and
    # excluding sdk here was the single most damaging error in this instrument.
    # Measured across the whole corpus on 2026-08-13: promptSource is ABSENT on
    # 15,433 user records (hook feedback, harness notices, tool traffic), reads
    # "sdk" on 1,394, and reads "typed" on exactly 2. The partner's own prompts —
    # verified against known turns he typed, including the one that authorised
    # this fix — carry "sdk", because the desktop app drives Claude Code through
    # the SDK. So the old rule discarded nearly every genuine partner turn while
    # keeping the machine text that carries no promptSource at all: the filter was
    # inverted, and no amount of tuning the strip pattern could have fixed it.
    #
    # Subagent traffic is already excluded above by isSidechain, tool output is
    # excluded below by tool_result, and scripted or scheduled prompts are machine
    # text caught by is_machine_origin(). promptSource adds nothing beyond that.
    msg = record.get("message")
    if isinstance(msg, dict) and isinstance(msg.get("content"), list):
        if any(
            isinstance(b, dict) and b.get("type") == "tool_result"
            for b in msg["content"]
        ):
            return False
    return bool(partner_text(record))


# Hook feedback, system reminders and harness notifications are PREPENDED to the
# partner's own message rather than arriving as separate turns. An earlier version
# discarded any turn beginning with one, which threw away 90 real turns in a
# 60-file sample and drove the partner-turn count down to an impossible 46. Strip
# the injected block; keep whatever the human actually typed after it.
INJECTED = re.compile(
    r"^(?:\s*(?:<[^>]+>[\s\S]*?</[^>]+>"
    r"|Stop hook[^\n]*\n?"
    r"|PreToolUse[^\n]*\n?"
    r"|PostToolUse[^\n]*\n?"
    r"|\[SYSTEM NOTIFICATION[\s\S]*?\n\n"
    r"|Caveat: The messages below[^\n]*\n?"
    r"))+"
)

# THIRD INSTRUMENT ERROR, corrected 2026-08-13, same family as the two above and
# the same shape: the script ran clean and the number was wrong.
#
# INJECTED strips only a LEADING HEADER — a "Stop hook feedback:" line, a tag
# block, a PreToolUse/PostToolUse line. But this project's conduct gates arrive as
# "Stop hook feedback:\nCONDUCT GATE — ...". The header was stripped and the ENTIRE
# GATE BODY survived as the tail, cleared MIN_TYPED, and was counted as the partner
# typing. Other machine texts — harness retry notices, interruption markers, skill
# preambles, image descriptors — matched no pattern at all and were counted whole.
# Measured contamination at the time of the fix: 194 of 207 counted partner turns,
# 94%, were machine-authored. This inflated baselines 1, 2 and 5. Baseline 3 counts
# tool invocations and baseline 4 is human-timed, so neither was affected.
#
# Baseline 5 was contaminated twice over: its HANDOFF pattern matches the word
# "handoff", and the harness's own CONTEXT HANDOFF GATE contains it, so the
# machinery's notice was counted as evidence the partner hand-carried context.
#
# A whole-body veto is therefore required, not a leading-header strip.
# CASE-SENSITIVE ON PURPOSE. The gates shout in capitals, and that is the whole
# discriminator. An earlier draft compiled this with IGNORECASE, which turned
# "[A-Z][A-Z ]{3,}GATE" into a trap for any turn STARTING with a word ending in
# "gate" — it ate the partner's real turns "investigate…", "navigate…",
# "propagate…", "yea loosen the gate" and "commite the gate". Three false
# positives in a 1,380-turn corpus is small but it is the partner's own words, and
# the regression test below pins it.
MACHINE_SHOUTED = re.compile(
    r"^(?:"
    r"[A-Z][A-Z ]{3,}GATE\b"          # CONDUCT GATE, COMPLETION EVIDENCE GATE, ...
    r"|DELEGATION TRIPWIRE\b"
    r"|WRITE LAW\b"
    r")"
)

# Fixed machine strings from the harness, the skill loader and the tool layer.
# Safe under IGNORECASE because none is a prefix of ordinary partner speech.
MACHINE_LITERAL = re.compile(
    r"^(?:"
    r"The previous response failed to produce"
    r"|This session is being continued from"
    r"|Please continue the conversation from"
    r"|Continue from where you left off"
    r"|\[Request interrupted"
    r"|\[Image:"
    r"|Base directory for this skill:"
    r"|Caveat: The messages below"
    r"|<[a-z-]+>"
    r"|#\s*\w[\w ]*Skill\b"           # bundled-skill preamble text
    r"|Approach this as the\b"        # skill-injected persona preamble
    r")",
    re.IGNORECASE,
)


def is_machine_origin(text):
    """True when this text was authored by the machinery, not the partner."""
    return bool(MACHINE_SHOUTED.match(text) or MACHINE_LITERAL.match(text))

# The old floor of 12 characters discarded the partner's genuinely short turns —
# "lets do it" is ten characters, "go ahead" is eight — while doing nothing to stop
# the machine text above, which is always long. It errs in BOTH directions at once.
# The veto now does the discriminating, so the floor only has to reject empties and
# single stray characters.
MIN_TYPED = 2


def partner_text(record):
    """What the human actually typed in this record, or empty string."""
    body = text_of(record)
    if not body.strip():
        return ""
    tail = INJECTED.sub("", body).strip()
    if len(tail) < MIN_TYPED:
        return ""
    # Whatever survived the header strip must still be the partner's own words.
    if is_machine_origin(tail):
        return ""
    return tail


# A question shape, not a topic. Deliberately crude and stated as such: it counts
# turns that ASK rather than instruct, which is the substrate for measure 2.
QUESTION = re.compile(
    r"(\?|^\s*(what|where|when|why|how|which|who|is|are|do|does|did|can|could|should|would|will)\b)",
    re.IGNORECASE | re.MULTILINE,
)

# Phrases that mark a turn as asking the SYSTEM to explain its own state or
# behaviour, which is what measure 2 is actually about.
EXPLAIN = re.compile(
    r"\b(explain|what does|what is|why did|why is|how do(es)? (it|this|the)|"
    r"what happened|show me|status|where (is|are|did)|did (it|that|you)|"
    r"is (it|this|that) (working|running|done|correct)|what changed)\b",
    re.IGNORECASE,
)

# Marks of context being carried by hand between sessions rather than by record.
HANDOFF = re.compile(
    r"\b(handoff|hand off|catch ?me ?up|continue (from|where)|as (i|we) (said|mentioned)|"
    r"last (session|time)|previous session|earlier (you|we|i)|"
    r"picking (this |it )?back up|resume|carry over|remind you)\b",
    re.IGNORECASE,
)


def main():
    sessions, file_count = load_sessions()
    if not sessions:
        sys.exit("no session records inside the window")

    all_ts = [r["_ts"] for recs in sessions.values() for r in recs]
    span_start, span_end = min(all_ts), max(all_ts)

    # ---- measure 1 substrate: active time -------------------------------
    per_session_spans = {sid: active_intervals(recs) for sid, recs in sessions.items()}
    per_session_secs = {sid: union_seconds(sp) for sid, sp in per_session_spans.items()}
    summed_secs = sum(per_session_secs.values())
    all_spans = [sp for spans in per_session_spans.values() for sp in spans]
    total_secs = union_seconds(all_spans)
    partner_sessions = {
        sid for sid, recs in sessions.items() if any(is_typed_prompt(r) for r in recs)
    }
    partner_spans = [sp for sid in partner_sessions for sp in per_session_spans[sid]]
    partner_secs = union_seconds(partner_spans)
    observed_days = max((span_end - span_start).total_seconds() / 86400, 1)
    weeks = observed_days / 7

    # ---- measure 2 substrate: questions asked of the system --------------
    user_turns = 0
    question_turns = 0
    explain_turns = 0
    for recs in sessions.values():
        for r in recs:
            if r.get("type") != "user":
                continue
            body = partner_text(r)
            if not body:
                continue
            # Tool results arrive as user-role records; they are not the partner
            # asking anything, so they must not inflate this count.
            msg = r.get("message")
            if isinstance(msg, dict) and isinstance(msg.get("content"), list):
                if any(
                    isinstance(b, dict) and b.get("type") == "tool_result"
                    for b in msg["content"]
                ):
                    continue
            if not is_typed_prompt(r):
                continue
            user_turns += 1
            if QUESTION.search(body):
                question_turns += 1
                if EXPLAIN.search(body):
                    explain_turns += 1

    # ---- measure 3 substrate: actions that needed a session ---------------
    verbs = Counter()
    tools = Counter()
    for recs in sessions.values():
        for r in recs:
            for name in tool_names(r):
                tools[name] += 1
                if "__" in name:  # record-layer verbs arrive as mcp__<server>__<verb>
                    verbs[name.rsplit("__", 1)[-1]] += 1

    # ---- measure 5 substrate: hand-carried context ------------------------
    handoff_turns = 0
    for recs in sessions.values():
        for r in recs:
            if not is_typed_prompt(r):
                continue
            if HANDOFF.search(partner_text(r)):
                handoff_turns += 1

    out = {
        "window": {
            "requested_days": WINDOW_DAYS,
            "actual_span_days": round(observed_days, 2),
            "first_record": span_start.isoformat(),
            "last_record": span_end.isoformat(),
            "note": (
                "The transcript corpus begins 2026-07-16, so a longer window is not "
                "measurable. Rates below are per-week over the ACTUAL span, not "
                "extrapolated."
            ),
        },
        "corpus": {
            "transcript_files_in_project": file_count,
            "sessions_in_window": len(sessions),
            "records_in_window": sum(len(r) for r in sessions.values()),
        },
        "measure_1_weekly_heavy_session_time": {
            "method": "active time as a UNION of inter-record gaps <= 10 minutes, "
            "restricted to sessions the partner actually typed in",
            "partner_active_hours": round(partner_secs / 3600, 1),
            "weekly_partner_active_hours": round((partner_secs / 3600) / weeks, 1),
            "all_session_union_hours": round(total_secs / 3600, 1),
            "naive_summed_hours_DO_NOT_USE": round(summed_secs / 3600, 1),
            "double_counting_factor": (
                round(summed_secs / total_secs, 2) if total_secs else None
            ),
            "sessions_total": len(sessions),
            "sessions_with_a_typed_prompt": len(partner_sessions),
            "median_session_minutes": round(
                sorted(per_session_secs.values())[len(per_session_secs) // 2] / 60, 1
            ),
            "JUDGMENT_STILL_REQUIRED": (
                "This is TOTAL active time. The baseline doctrine asks for ROUTINE "
                "OPERATION time only, because objective 8 permits heavy sessions for "
                "exploration, investigation and construction. Splitting the two is a "
                "judgment call this script will not fake."
            ),
        },
        "measure_2_questions_needing_agent_explanation": {
            "method": "user turns matching a question shape, excluding tool results",
            "partner_turns": user_turns,
            "question_turns": question_turns,
            "questions_asking_the_system_to_explain_itself": explain_turns,
            "weekly_explain_questions": round(explain_turns / weeks, 1),
            "share_of_turns_that_are_questions": (
                round(question_turns / user_turns, 3) if user_turns else None
            ),
        },
        "measure_3_actions_not_completable_visually": {
            "method": "record-layer verb invocations; every one is an action that "
            "required an AI session because no visual control exists for it",
            "distinct_verbs_used": len(verbs),
            "total_verb_calls": sum(verbs.values()),
            "weekly_verb_calls": round(sum(verbs.values()) / weeks, 1),
            "top_20_verbs": verbs.most_common(20),
            "CONFIRMATION_REQUIRED": (
                "Joe confirms which of these genuinely had no visual route. A verb "
                "called by a session on its own initiative is not the same as an "
                "action he could not otherwise perform."
            ),
        },
        "measure_5_hand_carried_context_transfers": {
            "method": "sessions in window, plus partner turns carrying context by hand",
            "sessions_total": len(sessions),
            "sessions_with_a_typed_prompt": len(partner_sessions),
            "weekly_partner_sessions": round(len(partner_sessions) / weeks, 1),
            "turns_restating_prior_context": handoff_turns,
            "weekly_hand_carried_turns": round(handoff_turns / weeks, 1),
            "note": (
                "Each session boundary is a potential transfer; a turn matching the "
                "handoff shape is one that actually carried context by hand."
            ),
        },
    }
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
