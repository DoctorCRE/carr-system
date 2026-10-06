#!/usr/bin/env python3
"""escalation-gate.py — PreToolUse deny on AskUserQuestion for INTERNAL calls.

THE TOOL-CHANNEL HALF of the conduct gates. conduct-stop-gate.py catches an
offload phrased as free prose, which can only be caught at Stop — after the
words exist. This one catches an offload phrased as a structured question, at
PreToolUse — BEFORE Joe ever sees it. That difference matters: this is the only
one of the two that is genuinely preventive rather than corrective.

RULE aa411351 (taught 2026-08-09) draws the gate BY AUDIENCE, NOT BY DIFFICULTY:

    Joe decides   client-facing · public-facing · money · irreversible
    System decides everything internal — schema, records, renders, jobs, config,
                  rules, refactors, agent/skill design, its own procedure

Internal implementation choices are the session's to make. Direct approval of
a named rule, build or plan is Joe's (rule a3da3d39, 2026-10-04), even when its
subject is internal; that AskUserQuestion must reach him in the same turn.

═══════════════════════════════════════════════════════════════════════════════
THE THING THAT NEARLY BROKE A WORKING SKILL, AND WHY THIS IS NOT A BLANKET DENY
═══════════════════════════════════════════════════════════════════════════════
The review council (Grok + Codex, 2026-08-09) both proposed denying
AskUserQuestion for internal-class calls. Neither knew that AskUserQuestion is
ALREADY LOAD-BEARING for legitimate work: skills/network-debrief/SKILL.md uses
it deliberately as a tap-through for verdicts during a debrief — the vendor
PURSUE/TABLE call, a deal-stage change, an intro Delivery grade — because
tapping an option beats typing, and it "cuts the friction of a debrief roughly
in half on a multi-meeting day."

That is NOT a decision being offloaded. It is FACT CAPTURE: the session is
asking Joe for information that exists only in his head, because he is the one
who was in the room. No amount of research substitutes for it — which is
precisely the test rule aa411351 sets ("research until confident" presumes the
answer is findable). A gate that blocked those would break a working skill and
be switched off within a week, which is the same outcome as never building it.

    ASKING JOE TO DECIDE SOMETHING INTERNAL   -> refuse; he delegated that
    ASKING JOE WHAT HE ALONE KNOWS            -> allow; research cannot reach it

ALLOW CLASSES, all narrow, none self-granted by the session:
  1. FACT CAPTURE — what happened, what they said, who was there, how it went,
     a grade or verdict on a real-world event. Detected by past-tense/event
     vocabulary about the world rather than about the system.
  2. PROTECTED CLASS — client-facing, public-facing, money, irreversible. His by
     rule; must still reach him.
  3. HE ASKED — his own last turn requested a choice or options (/options,
     "what are my options", "which would you recommend", "ask me"). Read off
     his keystrokes, never assertable by the session.
  4. BOUNDARY CHANGE — anything that weakens a gate, widens permissions, edits
     hooks, or expands what the system may do unattended. See below; this is
     the one place the council overruled Joe's own framing, on purpose.
  5. DIRECT APPROVAL — a named rule, restoration/retirement, build or plan,
     asked explicitly with approve/not approve or approve/do not approve
     option labels. Applies per AskUserQuestion item, never to parked loops.

THE CONSTITUTIONAL CARVE-OUT. Joe's instruction was "internal is yours". Both
council chairs independently, without being asked, refused that at exactly one
point: a change to the boundary ITSELF is internal by subject and
constitutional by effect. Grok: "If the system can 'internally decide' to loosen
its own collar, you do not have a boundary." Codex: "the agent may be the
operator, but it cannot also be the root authority that decides whether its own
actions were allowed." So NARROWING the system's own authority is internal and
free; WIDENING it is Joe's, permanently, and this hook lets that question
through rather than refusing it.

FAILS OPEN on any error, same reasoning as conduct-stop-gate.py: a wedged
session is worse than an unnecessary question.

THE ASYNC SPELLING OF THE SAME OFFLOAD (rule e065aa82, extended 2026-08-14).
AskUserQuestion is the synchronous way to park a decision on Joe; an add-loop
call with marker='decision' (or blocker='ruling') is the asynchronous one — the
❓ surfaces in the Monday brief and waits for a ruling. Same audience test, same
four allow classes, applied to the loop's own text. This deliberately does NOT
touch record-defect or any other loop kind: drift-claim-gate's rule that a
defect must always be filable stands, and a backlog/bell/dated loop is work,
not a parked question. Only the two spellings that literally ask Joe to decide
are classified.

AUDIT SIGNAL: every fire appends to out/conduct-gate.jsonl, the same ledger the
Stop gate writes, so one count covers both moments.

Fixtures: ops/escalation-gate-selftest.py.
"""
import json
import os
import re
import sys
from datetime import datetime, timezone

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG = os.path.join(REPO, "out", "conduct-gate.jsonl")
DEBUG = os.path.join(REPO, "out", "conduct-gate.log")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from conduct_patterns import (  # noqa: E402
    PROTECTED, HUMAN_WANTS_CHOICE, HUMAN_WANTS_COMMAND, FENCE, BARE_FENCE_CMD,
    INLINE_CMD, HANDOFF_PROSE, denied_commands, handoff_was_denied,
    handoff_needs_review, HANDOFF_REVIEW_MESSAGE,
)

# ── (1) FACT CAPTURE — only Joe was in the room. Research cannot reach it. ────
FACT_CAPTURE = re.compile(
    r"\b(what happened|how did it go|how'?d it go|what did (he|she|they|it) say"
    r"|did (he|she|they) (say|mention|agree|commit|respond|show|come|call|reply)"
    r"|who (was|were|did) (there|you|attend)|were you|did you (meet|call|visit|tour|talk|speak|see)"
    r"|how many people|when did (he|she|they|you)"
    r"|pursue or table|worth a follow[- ]?up|any good|what'?s your read|your read on"
    r"|grade|rating|rate (him|her|them|the vendor)|deliver(y|ed)"
    r"|stage (change|now|for)|still (active|live|warm|interested)|is (he|she|they) still"
    r"|did (it|that|the deal) (close|sign|die|stall)"
    r"|which of these did you|have you (met|spoken|talked|heard))\b", re.I)

# ── (4) BOUNDARY CHANGE — constitutional; must reach Joe. ────────────────────
BOUNDARY = re.compile(
    r"\b(disable|weaken|loosen|relax|bypass|turn off|switch off|remove|widen|expand"
    r"|opt out of|make .{0,20}optional)\b[^.?]{0,60}"
    r"\b(gate|guard|hook|rule|check|constraint|permission|allowlist|deny list|denylist"
    r"|boundary|approval|escalation|firewall|limit|cap|restriction)\b"
    r"|\b(hook|gate|guard|allowlist|permission|settings\.json|denylist|deny list)\b"
    r"[^.?]{0,50}\b(edit|change|modify|update|add to|grant|escalate|elevate|root|sudo)\b"
    r"|\bgrant (myself|itself|the system|sessions?)\b"
    r"|\bunattended\b[^.?]{0,40}\b(authority|permission|allow|expand)\b", re.I)

# ── INTERNAL SUBJECT — the vocabulary of the system talking about itself. ────
#
# TRAILING `s?` ON EVERY COUNTABLE NOUN, and it is not cosmetic. The first
# version wrote `|loop|` and the selftest caught "Should loops sort by created
# date or by severity?" sailing straight through: `\bloop\b` cannot match
# "loops", because \b after "loop" needs a non-word character and "s" is a word
# character. Every plural in this list was therefore invisible — one silent leak
# per noun. Prefer `s?` (or `\w*`) over a bare singular anywhere in this file.
INTERNAL = re.compile(
    r"\b(schemas?|migrations?|tables?|columns?|indexe?s?|constraints?|triggers?"
    r"|views?|queries|query|sql"
    r"|verbs?|endpoints?|workers?|connectors?|mcp|apis?"
    r"|hooks?|scripts?|modules?|functions?|refactor\w*|renam\w+|repos?|branch\w*"
    r"|commits?|merges?"
    r"|renders?|exporters?|exports?|pipelines?|jobs?|crons?|launchd"
    r"|scheduled tasks?|nightly"
    r"|folders?|director(y|ies)|file (names?|structure|layout)|naming|structure"
    r"|architecture"
    r"|rule stores?|doctrine|loops?|record layer|detectors?|selftests?|fixtures?"
    r"|tests?|migrat\w+"
    r"|configs?|settings?|flags?|env|variables?|caches?|logs?|formats?|layouts?"
    r"|sort order|sort by|ordering|sorting)\b",
    re.I)

# Read approval intent only from the question stem and option LABELS. A word
# buried in a header/description must not turn an approach choice into consent.
APPROVAL_REQUEST = re.compile(
    r"^(?:joe[, :]\s*)?(?:(?:do|would|will|can) you|should (?:you|joe)) "
    r"(?:not )?approve\s+|^(?:approve|do not approve|don't approve)\s+", re.I)
# A name is a noun phrase, not a question, a method or an unnamed action.
# Apply the same grammar inside quotes and to scope qualifiers. Every -ing
# word is a method except the listed nouns; base-form verbs are enumerated.
ING_NOUNS = r"onboarding"
NAME_START = (
    r"(?!(?:the|a|an|proposed|new|this|that|it|them|these|those|"
    r"use|choose|pick|select|run|schedule|build|add|insert|create|configure|"
    r"edit|change|remove|replace|modify|refactor|migrate|install|rename|"
    r"delete|drop|update|implement|(?!(?:" + ING_NOUNS + r")\b)\w+ing)\b)"
)
NAME_WORD = r"(?!(?:for|in|with|at|by|on|to|as)\b)[\w-]+"
NAME_PHRASE = NAME_START + NAME_WORD + r"(?:\s+" + NAME_WORD + r"){0,7}"
ARTIFACT_NAME = rf'''(?:{NAME_PHRASE}|"{NAME_PHRASE}"|'{NAME_PHRASE}')'''
APPROVAL_CHOICE = re.compile(
    r"\b(which|what|when|where|why|how|should|could|would|can|may|must|"
    r"whether|or|instead|versus)\b", re.I)
APPROVAL_ARTIFACT = re.compile(
    r"(?:(?:restor(?:e|ing)|retir(?:e|ing)|(?:restoration|retirement) of)\s+"
    r"(?:the\s+)?" + ARTIFACT_NAME + r"\s+rule"
    r"|(?:the\s+)?(?:proposed\s+)?" + ARTIFACT_NAME + r"\s+rule"
    r"|build(?:ing)?\s+(?:all\s+|the\s+)?" + ARTIFACT_NAME +
    r"|(?:the\s+)?" + ARTIFACT_NAME + r"\s+(?:build|plan))"
    r"(?:\s+for\s+(?:the\s+)?" + ARTIFACT_NAME + r")?"
    r"(?:\s+as written)?", re.I)


def direct_approval(question):
    """A consent request for a named rule/build/plan, not an approach choice."""
    stem = str(question.get("question", "")).strip().rstrip("?").strip()
    request = APPROVAL_REQUEST.match(stem)
    if request:
        stem = stem[request.end():]
    else:
        labels = {re.sub(r"\s*\(recommended\)$", "", str(o.get("label", "")),
                         flags=re.I).strip().lower()
                  for o in (question.get("options") or []) if isinstance(o, dict)}
        if "approve" not in labels or not labels.intersection({"don't approve", "do not approve"}):
            return False
    return (not APPROVAL_CHOICE.search(stem)
            and APPROVAL_ARTIFACT.fullmatch(stem) is not None)


def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def dlog(msg):
    try:
        os.makedirs(os.path.dirname(DEBUG), exist_ok=True)
        with open(DEBUG, "a") as fh:
            fh.write(f"{now()}  {msg}\n")
    except Exception:
        pass


def audit(record):
    """Shares out/conduct-gate.jsonl with conduct-stop-gate.py, so one count
    covers both moments. FIXTURES DO NOT COUNT — see that file's audit() for
    why: a metric its own test suite inflates is not a metric."""
    if record.get("session") == "selftest":
        return
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        with open(LOG, "a") as fh:
            fh.write(json.dumps(record) + "\n")
    except Exception:
        pass


def question_text(tool_input):
    """Flatten every question, header, option label and description into one
    blob. Classification reads the WHOLE call — a session cannot hide an
    internal subject in the option descriptions while keeping the question
    stem neutral."""
    if not isinstance(tool_input, dict):
        return ""
    parts = []
    for q in (tool_input.get("questions") or []):
        if not isinstance(q, dict):
            continue
        parts.append(str(q.get("question", "")))
        parts.append(str(q.get("header", "")))
        for o in (q.get("options") or []):
            if isinstance(o, dict):
                parts.append(str(o.get("label", "")))
                parts.append(str(o.get("description", "")))
    return "\n".join(p for p in parts if p)


def loop_text(tool_input):
    """Flatten the fields of an add-loop call that carry the question. Same
    whole-call principle as question_text: the subject cannot hide in
    blocker_detail while the title stays neutral."""
    if not isinstance(tool_input, dict):
        return ""
    fields = ("title", "body", "unblocks", "source_note", "blocker_detail")
    return "\n".join(str(tool_input.get(f) or "") for f in fields
                     if tool_input.get(f))


def parks_a_decision(tool_input):
    """Only the two spellings that literally await Joe's ruling."""
    if not isinstance(tool_input, dict):
        return False
    return (tool_input.get("marker") == "decision"
            or tool_input.get("blocker") == "ruling")


def classify_per_item(tool_input, human_last):
    """Classify a multi-question call ONE ITEM AT A TIME, and deny if ANY
    item is internal.

    THE BLIND SPOT THIS CLOSES. classify() reads one flattened blob, so in a
    two-question interview the FIRST item's allow class exempted the whole
    call: "what did the vendor say" (fact capture — legitimately Joe's) rode
    in the same AskUserQuestion as "should we rename the loop table"
    (internal — the system's to decide), and the internal question reached
    Joe wearing the first question's exemption. The audience gate is drawn
    per DECISION, not per tool call; a batch is allowed only when every item
    in it is.
    """
    worst = (True, "empty")
    for q in (tool_input.get("questions") or []) if isinstance(tool_input, dict) else []:
        if not isinstance(q, dict):
            continue
        parts = [str(q.get("question", "")), str(q.get("header", ""))]
        for o in (q.get("options") or []):
            if isinstance(o, dict):
                parts.append(str(o.get("label", "")))
                parts.append(str(o.get("description", "")))
        blob = "\n".join(p for p in parts if p)
        allow, why = ((True, "direct_approval_is_joes") if direct_approval(q)
                      else classify(blob, human_last))
        if not allow:
            return False, why      # one internal item refuses the whole call
        if blob.strip():
            worst = (allow, why)   # remember the last non-empty classification
    return worst


def classify(blob, human_last):
    """Return (allow: bool, why: str)."""
    if not blob.strip():
        return True, "empty"
    if HUMAN_WANTS_CHOICE.search(human_last or ""):
        return True, "human_asked_for_choice"
    if BOUNDARY.search(blob):
        return True, "boundary_change_is_constitutional"
    if FACT_CAPTURE.search(blob):
        return True, "fact_capture_only_joe_knows"
    if PROTECTED.search(blob):
        return True, "protected_class_is_joes"
    if INTERNAL.search(blob):
        return False, "internal_decision"
    # Neither internal nor protected. Do NOT refuse on an unclassified call —
    # a gate that denies what it does not understand trains sessions to phrase
    # around it, which is the laundering failure the council ranked #2.
    return True, "unclassified_allowed"


REASON = (
    "ESCALATION GATE — refused. This question is an INTERNAL decision, and "
    "internal decisions are yours (rules aa411351 + 14e0408b).\n\n"
    "The gate is drawn by AUDIENCE, not by difficulty. Joe decides "
    "client-facing, public-facing, money and irreversible. Everything internal "
    "— schema, records, renders, jobs, config, rules, refactors, procedure — "
    "you decide and report.\n\n"
    "IF YOU ARE NOT CONFIDENT, THE MOVE IS RESEARCH, NOT ASKING. Read the "
    "actual code. Run the query. Test the surface live. Read the full verb "
    "list. Convene the council. His words: \"if you truly aren't confident in "
    "what decision to make you need to research the problem harder until you "
    "are confident about what to do... you will be more effective at "
    "researching the solutions yourself than relying on me for every single "
    "thing.\"\n\n"
    "DO THIS INSTEAD: pick the smallest reversible option that advances the "
    "objective, execute it, log it with log-decision, and tell Joe in ONE LINE "
    "what you did and why. If it turns out wrong, that is recoverable; his "
    "attention is not.\n\n"
    "This gate does NOT block: asking him what only he knows (what happened in "
    "a meeting, what someone said, a vendor grade), anything client-facing, "
    "public-facing, money or irreversible, or anything that would widen the "
    "system's own authority, or direct approval of a named rule, build or plan "
    "(rule a3da3d39). Those still reach him."
)

LOOP_REASON = (
    "ESCALATION GATE — refused. This loop parks an INTERNAL decision on Joe "
    "(rule e065aa82), and internal decisions are yours to make and record.\n\n"
    "marker='decision' and blocker='ruling' both mean the ❓ waits in the "
    "Monday brief for Joe to rule. The gate is drawn by AUDIENCE, not by "
    "difficulty: he decides client-facing, public-facing, money and "
    "irreversible. Everything internal — schema, records, renders, jobs, "
    "config, rules, refactors, procedure — you decide and report.\n\n"
    "DO THIS INSTEAD: research until confident, pick the smallest reversible "
    "option, execute it, record it with log-decision, and tell Joe in ONE "
    "LINE what you did and why. If the work itself must wait on something "
    "real, file the loop with the blocker that names that thing — not "
    "'ruling' for a question you can answer.\n\n"
    "This gate does NOT block: decision loops that are genuinely his "
    "(client-facing, public, money, irreversible, or a boundary widening), "
    "defect filings, or any bell/dated/backlog loop. Those still land."
)


# ── ATTEMPT FIRST (Joe, 2026-09-23) ───────────────────────────────────────────
# "if you can run it yourself you should do that before you ever ask me." A
# question that hands Joe a command is refused here, before he sees it, unless
# the harness already refused the session that command this turn. This is the
# preventive twin of conduct-stop-gate.py's command_handoff class, which can
# only act at Stop, after the prose has been read.
ATTEMPT_FIRST_REASON = (
    "ATTEMPT FIRST (Joe, 2026-09-23). This question asks Joe to run a command or "
    "do a computer action you have not tried. Run it yourself with the Bash tool "
    "first. Only if the harness refuses it (a classifier or permission denial), "
    "or it genuinely needs a password, Face ID or a browser sign-in, may you ask "
    "him, and then say which refusal you hit. His words: \"if you can run it "
    "yourself you should do that before you ever ask me.\"")


def hands_off_unattempted(blob, human_last, denied):
    """Return the finding name when the question hands Joe an untried command,
    else None; unresolved prose action cues return handoff_review (nonblocking)."""
    if not blob.strip():
        return None
    if human_last and HUMAN_WANTS_COMMAND.search(human_last):
        return None
    if denied and handoff_was_denied(blob, denied):
        return None
    if FENCE.search(blob) or BARE_FENCE_CMD.search(blob) or INLINE_CMD.search(blob):
        return "command"
    for name, pat in HANDOFF_PROSE:
        if pat.search(blob):
            return name
    return "handoff_review" if handoff_needs_review(blob, human_last, denied) else None


def read_turn(path, limit=400):
    """(records, index just after the last genuine human turn, that turn's text)."""
    recs = []
    with open(path, "r", errors="replace") as fh:
        for line in fh.readlines()[-limit:]:
            try:
                recs.append(json.loads(line.strip()))
            except Exception:
                continue
    for i in range(len(recs) - 1, -1, -1):
        rec = recs[i]
        if rec.get("type") not in ("user", "human"):
            continue
        if rec.get("isMeta") or rec.get("isCompactSummary"):
            continue
        msg = rec.get("message") or rec
        c = msg.get("content")
        t = c if isinstance(c, str) else "\n".join(
            b.get("text", "") for b in c
            if isinstance(b, dict) and b.get("type") == "text"
        ) if isinstance(c, list) else ""
        if not t or t.lstrip().startswith((
                "<system-reminder>", "<task-notification>",
                "[SYSTEM NOTIFICATION", "<local-command",
                "<command-name>", "Caveat:")):
            continue
        return recs, i + 1, t
    return recs, 0, ""


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception as exc:
        dlog(f"ALLOW(parse-error) {exc}")
        sys.exit(0)

    try:
        tool = payload.get("tool_name") or payload.get("toolName") or ""
        is_ask = tool == "AskUserQuestion"
        is_loop = tool.startswith("mcp__") and tool.endswith("__add-loop")
        if not (is_ask or is_loop):
            sys.exit(0)

        ti = payload.get("tool_input") or payload.get("toolInput") or {}

        # The human's own last turn, for the "he asked" exemption. Best-effort:
        # if the transcript is unreadable we simply lose one exemption and the
        # other three still apply.
        human_last, recs, start = "", [], 0
        path = payload.get("transcript_path")
        if path and os.path.exists(path):
            try:
                recs, start, human_last = read_turn(path)
            except Exception:
                pass

        if is_ask:
            blob = question_text(ti)
            finding = hands_off_unattempted(
                blob, human_last, denied_commands(recs, start))
            if finding == "handoff_review":
                audit({"ts": now(), "hook": "escalation-gate", "classes": ["handoff_review"],
                       "patterns": ["needs_review"], "session": payload.get("session_id")})
                print(json.dumps({"systemMessage": HANDOFF_REVIEW_MESSAGE,
                    "hookSpecificOutput": {"hookEventName": "PreToolUse",
                                           "additionalContext": HANDOFF_REVIEW_MESSAGE}}))
            elif finding:
                audit({
                    "ts": now(),
                    "hook": "escalation-gate",
                    "classes": ["command_handoff"],
                    "patterns": [f"attempt_first:{finding}"],
                    "session": payload.get("session_id"),
                    "excerpt": " ".join(blob.split())[:400],
                })
                dlog(f"DENY(attempt_first:{finding}) :: {' '.join(blob.split())[:200]}")
                print(ATTEMPT_FIRST_REASON, file=sys.stderr)
                sys.exit(2)

        if is_ask:
            # Per-item classification: one fact-capture question must not
            # exempt an internal question riding in the same interview.
            allow, why = classify_per_item(ti, human_last)
            blob = question_text(ti)
        else:
            if not parks_a_decision(ti):
                sys.exit(0)
            blob = loop_text(ti)
            allow, why = classify(blob, human_last)

        if allow:
            dlog(f"ALLOW({why}) :: {' '.join(blob.split())[:160]}")
            sys.exit(0)

        audit({
            "ts": now(),
            "hook": "escalation-gate",
            "classes": ["internal_ask" if is_ask else "internal_loop_parked"],
            "patterns": [f"escalation:{why}"],
            "session": payload.get("session_id"),
            "excerpt": " ".join(blob.split())[:400],
        })
        dlog(f"DENY({why}) :: {' '.join(blob.split())[:200]}")
        # Exit 2, not JSON. Same reasoning as guard-unattended.py: on any build
        # that does not parse the structured contract, exit 0 reads as ALLOW and
        # the gate fails open silently. Exit 2 blocks everywhere and hands
        # stderr back as the reason.
        print(REASON if is_ask else LOOP_REASON, file=sys.stderr)
        sys.exit(2)

    except Exception as exc:
        dlog(f"ALLOW(internal-error) {exc}")
        sys.exit(0)


if __name__ == "__main__":
    main()
