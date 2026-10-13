#!/usr/bin/env python3
# doctrine: engineering-workflow-sop
"""unfiled-bug-stop-gate.py — a turn that reports a bug it saw must file it.

WHY THIS EXISTS. Joe, 2026-10-12, after a status report that listed a rule-boot
re-arm, a brittle preflight test, a merge-conflict magnet and undeliverable seat
reports as plain observations: "you cant detect a bug and then step over it and
keep going without addressing the issue". Told a rule had been taught for it:
"pretty sure it was already a rule. thats the problem." He was right. Rule
1b8e7f43 (a blocked action is filed, never dropped) and the file-what-you-find
practice were both active and both advisory, and rule ab814a26 says the first
question after a violation is "why was this not a gate". Rule 19ebbeb3 (a
detected bug is recorded and addressed in the same session, never stepped over)
was admitted as judgment with no carrying control. This file is that control.

WHAT IT DOES, at Stop. It reads THE TURN: every record after the previous
end-of-turn assistant message, up to the message now ending. If the assistant
TEXT of that turn reports a defect the session itself observed (a gate that
fired wrongly, a failing or brittle test, a verb refusing with an error, a
reader filing duplicates, a recurring merge conflict, an undeliverable report, a
crash, a wrong default) and the same turn carries no receipt for it, the turn is
held once with the sentence quoted and the two ways to close it named.

A RECEIPT is either:
  (1) a tool call in the same turn to record-defect, report-problem or add-loop
      that the record layer accepted: the MCP form under any server prefix, the
      `./run.sh call <verb> '<json>'` / tools/call-verb.py Bash forms, or the
      call-verb passthrough, counted only when that call's own tool RESULT in
      the transcript shows the write ("ok": true, a defect id, a WR number). An
      empty-argument schema probe (`'{}'`) is not a receipt, a call the verb
      refused (missing field, bad vocabulary) recorded nothing, and a seat
      brief or any other prose that only NAMES a verb filed nothing either.
  (2) the sentence's own paragraph naming the record: a WR number, a loop
      number, a defect id, "recorded", "filed", "on record", or a fix named by
      PR or commit.
Each defect is matched to a filing by shared words first; filings that match
nothing then cover open defects one for one, and a filing whose record the
prose names elsewhere ("WR-000264 filed") is already spoken for. So two bugs and
one filing still holds the turn, which is exactly the turn Joe objected to: it
filed the rule-boot re-arm and stepped over the brittle preflight check and the
conflict magnet.

PRECISION OVER RECALL, by design. A gate that fires on every mention of "bug"
in a status relay gets ignored, then removed (memory: keyword gates fail by
silence; Joe's 2026-10-12 red-tape mandate judges a gate by measured catches
against cost). So a sentence counts only when it is an OBSERVATION: a past or
present predicate of breakage ("was failing", "refused with", "threw an
exception", "had gone conflicting again", "misread", "pins the literal",
"cannot deliver"). It does NOT count when it is:
  - hypothetical or future ("would fail", "if it breaks", "will crash"),
  - negated ("no regressions", "isn't what broke"),
  - quoted, inside a code fence, a table row, or a fragment under five words,
  - a relay of someone else's finding ("PR 1715 also fixes a bug", "relayed"),
  - already being fixed ("fixed", "repairing", "written up", "dispatched"),
  - the session's own slip or work in progress ("my change broke", "I misread"),
  - the normal build loop: a red run or review finding on a pull request,
    unless the sentence traces it outside the change (flaky, unrelated, main),
  - an environment fault (outage, network, Tailscale, "expected").
Each screen came from a false hold in the replay; the counts are in the PR
that added this file. Re-measure against real transcripts before widening any
pattern or dropping any screen.

TURN BOUNDARY. A turn starts after the previous assistant message whose
stop_reason ended a turn. That is the same boundary the harness uses to fire
Stop, so a sentence is judged once, at the Stop of the turn that said it, and a
receipt filed anywhere in that turn counts. Reading only a bounded tail of the
transcript keeps the Stop cheap on a 20 MB session.

NEVER LOOPS AND FAILS OPEN. stop_hook_active lets the turn end: the session got
told once, and a wedged session is worse than one unfiled bug. Every error
allows. Every hold appends one row to out/unfiled-bug-gate.jsonl, which is the
catch metric gate-lifecycle.json reads.

Fixtures: ops/unfiled-bug-stop-gate-selftest.py
"""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timezone

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG = os.environ.get("CARR_UNFILED_BUG_LOG") or os.path.join(REPO, "out", "unfiled-bug-gate.jsonl")
HOOK = "unfiled-bug-stop-gate"

# A Stop on a long session must not parse the whole file. 12 MB holds several
# hundred tool round-trips, far longer than any one turn measured in replay.
TAIL_BYTES = 12 * 1024 * 1024
END_REASONS = {"end_turn", "stop_sequence"}
RECEIPT_VERBS = ("record-defect", "report-problem", "add-loop")

# ------------------------------------------------------------------ patterns
# Each DEFECT pattern is an observation of breakage, not a topic word. The
# names reach the audit row and the hold message.
DEFECT = [
    ("failing", re.compile(
        r"\b(?:was|were|is|are|kept|keeps|started|has been|had been|been|still)\s+"
        r"(?:still\s+|now\s+)?(?:failing|crashing|broken|flaky|brittle)\b", re.I)),
    ("failed", re.compile(
        r"\bfail(?:ed|s)\s+(?:on|with|because|every|again|at\s+(?:pre-?push|push|commit|merge|startup|boot))\b", re.I)),
    ("refused", re.compile(
        r"\brefus(?:ed|es|ing)\s+with\b|\bnam(?:es|ing)\s+no\s+field\b|\bkeeps?\s+refusing\b", re.I)),
    ("exception", re.compile(
        r"\b(?:threw|throws|raised|raises|hit|got|returned)\s+(?:an?\s+|the\s+)?(?:[\w-]+\s+){0,2}"
        r"(?:exception|traceback|stack\s+trace|internal\s+(?:server\s+)?error)\b|\btraceback\b", re.I)),
    ("crash", re.compile(r"(?<!\ba\s)(?<!\ban\s)(?<!\bthe\s)\bcrash(?:ed|es|ing)\b", re.I)),
    ("broke", re.compile(
        r"\bbroke\s+(?:on|when|because|again|every|after|for|down)\b"
        r"|\b(?:is|was|are|were|got|still|now)\s+broken\b|\bbroken\s+(?:for|since)\b", re.I)),
    # A single conflict is ordinary rebase work; a conflict that RECURS, or that
    # lands on the same line every time, is a file shaped to keep causing them.
    ("conflict", re.compile(
        r"\b(?:gone|went|is|was|keeps?)\s+conflicting\s+again\b|\bconflict(?:ed|s|ing)?\s+again\b"
        r"|\b(?:recurring|repeated|another|same)\s+(?:merge\s+)?conflict\b|\bkeeps?\s+conflicting\b"
        r"|\bconflict(?:ed|s)?\s+(?:with\s+main\s+)?(?:on|in)\s+(?:the|its)\s+[\w .-]{0,30}\bline\b", re.I)),
    ("misfire", re.compile(
        r"\bfired\s+(?:wrongly|falsely|by\s+mistake)\b|\bmis(?:fired|fires|read|reads)\b"
        r"|\bfalse\s+positives?\b|\bfalse\s+(?:block|fire|alarm|hold)s?\b", re.I)),
    # "flaky" alone is a topic word ("the flaky-test fix (PR 1678)"); it is an
    # observation when it is predicated or tied to a failure it caused.
    ("brittle", re.compile(
        r"\b(?:is|was|are|were|been|looks?|seems?|proved)\s+(?:\w+\s+)?(?:brittle|flaky)\b"
        r"|\bpins?\s+the\s+(?:literal|wrong)\b"
        r"|\b(?:brittle|flaky)\b.*\b(?:failed|fails|failing|bumps?|broke|red)\b"
        r"|\b(?:failed|fails|fail|failing)\b.*\b(?:brittle|flaky)\b", re.I)),
    ("undeliverable", re.compile(
        r"\bundeliverable\b|\b(?:cannot|can't|could\s+not|couldn't|never)\s+(?:be\s+)?deliver(?:ed)?\b"
        r"|\bwas\s+never\s+delivered\b|\bnever\s+(?:arrived|reached\s+(?:me|its\s+(?:recipient|parent|caller)"
        r"|the\s+(?:parent|caller|orchestrator|recipient)))\b", re.I)),
    ("duplicate", re.compile(
        r"\b(?:filed|files|filing|opened|opens|created|creates|wrote|writes)\s+"
        r"(?:a\s+|two\s+|\d+\s+)?duplicates?\b|\bduplicate\s+(?:loops?|work\s+requests?|rows?|filings?|defects?)\b"
        r"|\bmy\s+own\s+duplicate\b", re.I)),
    ("wrong_default", re.compile(r"\bwrong\s+default\b|\bdefaults?\s+to\s+the\s+wrong\b", re.I)),
    ("regression", re.compile(r"\bregressed\b|\bis\s+a\s+regression\b|\bcaused\s+a\s+regression\b", re.I)),
    ("silent", re.compile(
        r"\bsilently\s+(?:dropped|ignored|skipped|failed|refused|wrote|swallowed|lost|discarded)\b", re.I)),
    ("found_bug", re.compile(
        r"\b(?:found|hit|caught|noticed|spotted|discovered)\s+(?:a|another|the|one\s+more)\s+"
        r"(?:\w+\s+)?(?:bug|defect|regression)\b", re.I)),
    # A red check on work in progress is the normal loop; a red check traced to
    # a CAUSE outside the change is a brittle test or a broken check.
    ("went_red", re.compile(
        r"\bred\s+check\s+(?:from|because|caused\s+by|on\s+an?\s+unrelated)\b"
        r"|\b(?:went|turned)\s+red\s+(?:because|from|on\s+an?\s+unrelated)\b", re.I)),
]

# Hypothetical, conditional or future framing: not an observation.
HYPOTHETICAL = re.compile(
    r"\b(?:if|would|might|may|unless|whenever|in\s+case|otherwise|should|whether|will|going\s+to|"
    r"deliberately|plan(?:s|ned)?\s+to|could(?!\s+(?:not|never)\b)|"
    r"so\s+that|to\s+(?:avoid|prevent|stop|catch|detect)|prevents?|guards?\s+against)\b"
    # A test written to fail on the old code is the fix's evidence, not a bug.
    r"|\b(?:selftests?|tests?|cases?|mutants?|fixtures?)\s+(?:that\s+)?(?:fails?|failed)\s+(?:on|against|first)\b"
    r"|\bold\s+(?:code|behaviou?r|version)\b|\b(?:before|without)\s+the\s+fix\b",
    re.I)
# Negated observation: "no regressions", "nothing failed", "isn't what broke".
NEGATION = re.compile(
    r"(?:\b(?:no|not|never|nothing|zero|none|without|neither|nor|0)\b|n't\b)[\w\s,'-]{0,24}$", re.I)
# The defect already has its fix in hand (made, merged, being built): that turn
# addressed it, which is what rule 19ebbeb3 asks; it did not step over it.
FIXED = re.compile(
    r"\b(?:fix(?:ed|es|ing)?|repair(?:ed|s|ing)?|solved|resolved|patched|corrected|written\s+up|"
    r"diagnos\w*|dispatch(?:ed)?|(?:is|are)\s+gone|dropped\s+from|quiet\s+now|pass(?:ed|es))\b", re.I)
# The cause is outside the system (an outage, the network, a known and expected
# state). Real, but not a defect a Work Request can fix.
ENVIRONMENT = re.compile(
    r"\b(?:expected|outage|offline|network|wi-?fi|hotspot|tailscale|ssh|tls|dns|power|transient)\b", re.I)
# The session's own slip or its own work in progress: not a system defect.
OWN_WORK = re.compile(
    r"\b(?:my|our)\s+(?:own\s+)?(?:change|fix|edit|commit|code|branch|loop|script|test|fake|dispatch|push|"
    r"first|attempt|patch|guess|reading|command)\b|\bI\s+(?:misread|misjudged|mistyped|forgot|was\s+wrong|broke)\b"
    r"|\bhaving\s+misread\b|^correction\b|\bI\s+(?:pulled|copied|extracted)\b"
    r"|\bclaimed\s+(?:regression|bug|false\s+positive)\b", re.I)
# A red run, a crash or a review finding on a pull request is the normal build
# loop: the change under review is what is broken, and fixing it is the work.
# It is a system defect only when the sentence traces it to a cause outside the
# change (flaky, unrelated, main), which the went_red and brittle classes test
# for themselves.
PR_REF = re.compile(
    r"\b(?:pr|pull\s+request|change)\s*#?\d+|\bhead\b|\bbranch\b|\b(?:this|the|its|a\s+held)\s+(?:pr|draft)\b"
    r"|\breviewed\s+version\b|\bits\s+own\s+change\b|\bits\s+ci\b|\bgithub'?s?\s+checks\b"
    r"|\bhosted\s+(?:ci|checks?)\b|\breview(?:er)?\s+(?:found|flagged|caught)\b", re.I)
CI_DEFECT = re.compile(r"\b(?:flaky|brittle|unrelated|main(?:'s)?\s+(?:build|ci|run))\b", re.I)
PR_LOOP_CLASSES = frozenset({"failing", "failed", "broke", "crash", "misfire", "found_bug"})
# A fragment too short to carry a subject and a predicate ("Still broken.", a
# link title, a heading) cannot be judged as an observation.
MIN_WORDS = 5
LINK_ITEM = re.compile(r"^\[[^\]]+\]\(")
# Someone else's finding or fix, relayed: not this session's observation.
RELAY = re.compile(
    r"\b(?:it|this\s+pr|that\s+pr|the\s+pr|(?:pull\s+request|pr)\s+#?\d+|the\s+(?:seat|subagent|reviewer|"
    r"orchestrator|builder|desk)|(?:a|that|this|its)\s+seat|codex|grok|jev|sol|terra)\s+"
    r"(?:also\s+|now\s+)?(?:fixes|fixed|found|finds|reports?|reported|flagged|flags|caught|catches|says|said|"
    r"notes?|noted|adds|added|removes|removed|retires|retired|closes|closed|repairs|repaired)\b"
    r"|\brelay(?:ed|ing)?\b|\b(?:per|from|in)\s+the\s+(?:seat|subagent|reviewer)'s\s+(?:report|finding)", re.I)
# The sentence names its own record or fix.
SELF_RECEIPT = re.compile(
    r"\bWR-\d{3,}\b|\bwork\s+request\b|\bdefect\s+(?:[0-9a-f]{6,}|row|id)\b"
    r"|\b(?:recorded|filed|logged|captured|on\s+record|in\s+the\s+(?:bug|defect)\s+log)\b"
    r"|\bloop\s*#?\d+\b|\bloop\s+(?:filed|opened)\b"
    r"|\b(?:fixed|fix(?:es)?|repaired)\s+(?:it\s+)?in\s+(?:pr|pull\s+request|commit|#)\s*#?[0-9a-f]{2,}"
    r"|\brecord-defect\b|\breport-problem\b|\badd-loop\b", re.I)

FENCE = re.compile(r"```.*?```", re.S)
QUOTE_LINE = re.compile(r"^\s*>.*$", re.M)
QUOTED = re.compile(r"\"[^\"\n]{3,200}\"|“[^”\n]{3,200}”")
SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9*(\[`\"'])|\n+")
SUBJECT = re.compile(
    r"\b(?:pr|pull\s+request)\s*#?(\d{2,})\b|#(\d{3,})\b|\b([\w./-]+\.(?:py|sh|mjs|js|json|sql|ts|toml))\b", re.I)
WORD = re.compile(r"[a-z0-9][a-z0-9_.-]{3,}", re.I)
STOP_WORDS = frozenset("""
    that this with from have been were which there their they them then than when what will would
    about into after before while again also just only still every other some more most such each
    because since until over under same your yours ours mine here where these those being does done
    session turn gate test check main branch merge pushed push fixed fixing failing failed""".split())

# ------------------------------------------------------------------ transcript


def read_recent(path, limit=TAIL_BYTES):
    """The transcript's last `limit` bytes as records. A torn first line and any
    non-object line are skipped; skipping never changes the verdict shape."""
    size = os.path.getsize(path)
    with open(path, "rb") as handle:
        if size > limit:
            handle.seek(size - limit)
            handle.readline()
        raw = handle.read()
    records = []
    for line in raw.decode("utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if isinstance(obj, dict):
            records.append(obj)
    return records


def _message(rec):
    msg = rec.get("message")
    return msg if isinstance(msg, dict) else {}


def _blocks(rec):
    content = _message(rec).get("content")
    if isinstance(content, list):
        return [b for b in content if isinstance(b, dict)]
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    return []


def turn_window(recs):
    """Records of the turn now ending: after the previous end-of-turn assistant
    message (a different message id from the final one), through the end."""
    final = None
    for i in range(len(recs) - 1, -1, -1):
        if recs[i].get("type") == "assistant":
            final = i
            break
    if final is None:
        return []
    final_id = _message(recs[final]).get("id")
    start = 0
    for i in range(final - 1, -1, -1):
        rec = recs[i]
        if rec.get("type") != "assistant":
            continue
        msg = _message(rec)
        if msg.get("stop_reason") in END_REASONS and (final_id is None or msg.get("id") != final_id):
            start = i + 1
            break
    return recs[start:]


def _has_arguments(value):
    if isinstance(value, dict):
        return any(_has_arguments(v) for v in value.values())
    if isinstance(value, (list, tuple)):
        return any(_has_arguments(v) for v in value)
    if isinstance(value, str):
        return bool(value.strip())
    return value is not None


_BASH_RECEIPT = re.compile(
    r"(?:run\.sh\s+call|call-verb(?:\.py)?)\s+(record-defect|report-problem|add-loop)\b(?!\s+'\{\s*\}')")
# What a verb answers when it wrote something, and when it refused. A receipt
# needs the first in the call's own result: a filing the record layer refused
# recorded nothing, and a call with no result shows nothing was recorded.
_WROTE = re.compile(r'"ok"\s*:\s*true|"defect_id"|"human_ref"|"loop_id"|\bWR-\d{4,}\b')
_FAILED = re.compile(r'TOOL ERROR|"ok"\s*:\s*false')
# Record references a sentence can name: a Work Request, a loop, a defect id.
_REF = re.compile(r'\bWR-\d{4,}\b|"number"\s*:\s*"?(\d{3,})|"defect_id"\s*:\s*"([0-9a-f]{8})')


def _results(window):
    """tool_use_id -> (result text, is_error) for every tool result in the turn."""
    results = {}
    for rec in window:
        if rec.get("type") != "user":
            continue
        for block in _blocks(rec):
            if block.get("type") != "tool_result":
                continue
            content = block.get("content")
            if isinstance(content, list):
                content = "\n".join(str(c.get("text") or "") for c in content if isinstance(c, dict))
            results[block.get("tool_use_id")] = (str(content or ""), bool(block.get("is_error")))
    return results


def _refs(text):
    return {(m.group(1) or m.group(2) or m.group(0)).lower() for m in _REF.finditer(text)}


def receipts(window):
    """Every capture call in the turn whose result shows the record layer wrote it:
    [{"verb", "text" (its arguments, for matching), "refs" (records it wrote)}].
    An Agent or Task brief that names a capture verb is not a call to it: the
    seat may never run it, so only the verb's own result counts."""
    results = _results(window)
    found = []
    for rec in window:
        if rec.get("type") != "assistant":
            continue
        for block in _blocks(rec):
            if block.get("type") != "tool_use":
                continue
            name = str(block.get("name") or "")
            args = block.get("input") if isinstance(block.get("input"), dict) else {}
            base = name.rsplit("__", 1)[-1]
            verb, text = None, ""
            if base in RECEIPT_VERBS and _has_arguments(args):
                verb, text = base, json.dumps(args, sort_keys=True)
            elif base == "call-verb" and args.get("verb") in RECEIPT_VERBS:
                verb, text = str(args["verb"]), json.dumps(args, sort_keys=True)
            elif name == "Bash":
                match = _BASH_RECEIPT.search(str(args.get("command") or ""))
                if match:
                    verb, text = match.group(1), str(args.get("command") or "")
            if verb is None:
                continue
            result, is_error = results.get(block.get("id"), ("", False))
            if is_error or not _WROTE.search(result) or _FAILED.search(result):
                continue
            found.append({"verb": verb, "text": text, "refs": _refs(result)})
    return found


def assistant_text(window):
    parts = []
    for rec in window:
        if rec.get("type") != "assistant":
            continue
        for block in _blocks(rec):
            if block.get("type") == "text" and str(block.get("text") or "").strip():
                parts.append(str(block["text"]))
    return parts


# ------------------------------------------------------------------ sentences


def sentences(text):
    """[(sentence, receipted)] for the prose of one message. `receipted` is true
    when the sentence's own paragraph names a record ("X kept failing. The fix
    is tracked as WR-000230."): the receipt does not have to share its sentence."""
    text = FENCE.sub(" ", text)
    text = QUOTE_LINE.sub(" ", text)
    text = QUOTED.sub(" ", text)
    out = []
    for paragraph in text.split("\n"):
        # A table row is a cell grid, not a sentence: its cells read as clauses
        # with no subject ("Keeps failing | After 5 failed runs it stops").
        if paragraph.count("|") >= 2:
            continue
        receipted = bool(SELF_RECEIPT.search(paragraph))
        for piece in SENTENCE_END.split(paragraph):
            piece = re.sub(r"[*_`]+", "", piece).strip(" -•\t")
            if len(piece) >= 12:
                out.append((piece, receipted))
    return out


def defect_class(sentence):
    """The DEFECT pattern this sentence observes, or None when it is not an
    observation of breakage (hypothetical, negated, relayed)."""
    for screen in (HYPOTHETICAL, RELAY, FIXED, OWN_WORK, ENVIRONMENT):
        if screen.search(sentence):
            return None
    if len(sentence.split()) < MIN_WORDS or LINK_ITEM.match(sentence):
        return None
    pr_loop = bool(PR_REF.search(sentence) and not CI_DEFECT.search(sentence))
    for name, pattern in DEFECT:
        if pr_loop and name in PR_LOOP_CLASSES:
            continue
        for match in pattern.finditer(sentence):
            if NEGATION.search(sentence[:match.start()][-40:]):
                continue
            return name
    return None


def _subjects(sentence):
    keys = set()
    for match in SUBJECT.finditer(sentence):
        key = next(g for g in match.groups() if g)
        keys.add(key.lower())
    return keys


def _salient(text):
    return {w.lower().strip(".-") for w in WORD.findall(text)} - STOP_WORDS


def findings(window):
    """[(class, sentence)] for every observed defect the turn did not receipt."""
    observed = []
    seen = set()
    for text in assistant_text(window):
        for sentence, receipted in sentences(text):
            if receipted:
                continue
            klass = defect_class(sentence)
            if not klass:
                continue
            norm = " ".join(sentence.lower().split())
            if norm in seen:
                continue
            seen.add(norm)
            observed.append((klass, sentence))
    if not observed:
        return []

    # One defect told twice (a progress line, then the closing report) is one
    # defect: sentences that name the same PR or file collapse together.
    groups = []
    for klass, sentence in observed:
        keys = _subjects(sentence)
        for group in groups:
            if keys and keys & group["keys"]:
                group["keys"] |= keys
                group["sentences"].append(sentence)
                break
        else:
            groups.append({"keys": set(keys), "class": klass, "sentences": [sentence]})

    # A filing whose record the turn's own prose names ("WR-000264 filed")
    # belongs to that sentence, which SELF_RECEIPT already excused.
    prose = " ".join(assistant_text(window)).lower()
    spare = [r for r in receipts(window)
             if not (r["refs"] and any(ref in prose for ref in r["refs"]))]
    open_groups = []
    used = set()
    for group in groups:
        words = set()
        for sentence in group["sentences"]:
            words |= _salient(sentence)
        hits = [i for i, r in enumerate(spare) if len(words & _salient(r["text"])) >= 3]
        if hits:
            used.update(hits)
        else:
            open_groups.append(group)
    # Filings that match no defect by content still count, one for one. When
    # they are fewer than the open defects, every open one is named: the gate
    # cannot tell which of them a stray filing was for.
    if len(open_groups) <= len(spare) - len(used):
        return []
    return [(g["class"], g["sentences"][0]) for g in open_groups]


# ------------------------------------------------------------------ verdict


def is_carr(payload):
    cwd = payload.get("cwd") or payload.get("working_directory") or payload.get("workingDirectory")
    if not isinstance(cwd, str) or not cwd.strip():
        return True
    if "carr-system" in cwd or "carr" in os.path.basename(cwd.rstrip("/")).lower():
        return True
    try:
        root = os.path.realpath(REPO)
        return os.path.commonpath((root, os.path.realpath(cwd))) == root
    except (OSError, ValueError):
        return False


def hold_message(found):
    lines = [f'  - "{" ".join(s.split())[:300]}" ({klass})' for klass, s in found]
    return (
        "UNFILED BUG — this turn reports a defect it observed and files nothing for it "
        "(rule 19ebbeb3: a detected bug is recorded and addressed in the same session, never "
        "stepped over; rule 1b8e7f43: filed, never dropped).\n\n"
        "Matched:\n" + "\n".join(lines) + "\n\n"
        "Close it with ONE of two receipts, in this turn:\n"
        "  1. File it now: record-defect (claimed / actual / detected_by), plus report-problem "
        "for the Work Request or add-loop when the fix is not yours to make here. The MCP verb or "
        "`./run.sh call record-defect '<json>'` both count.\n"
        "  2. If it is already on record, say so in that sentence with its id (WR-000123, the "
        "defect id, the loop number, or the PR or commit that fixed it).\n\n"
        "Then send only the delta. If the sentence is not a defect you observed (a relay, a "
        "hypothetical), say so in one line; this gate speaks once per turn."
    )


def audit(row):
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        with open(LOG, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(row) + "\n")
    except Exception:
        pass


sys.path.insert(0, REPO)
from lib.hook_runtime import Verdict, decision, run  # noqa: E402


@decision
def decide(payload):
    if payload.get("stop_hook_active"):
        return 0
    path = payload.get("transcript_path") or payload.get("transcriptPath")
    if not path or not os.path.exists(path) or not is_carr(payload):
        return 0
    found = findings(turn_window(read_recent(path)))
    if not found:
        return 0
    session = payload.get("session_id") or payload.get("sessionId")
    if session != "selftest":
        audit({"ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
               "hook": HOOK, "session": session,
               "classes": sorted({k for k, _ in found}),
               "sentences": [" ".join(s.split())[:300] for _, s in found]})
    return Verdict.block(hold_message(found))


def main():
    return run(decide)


if __name__ == "__main__":
    raise SystemExit(main())
