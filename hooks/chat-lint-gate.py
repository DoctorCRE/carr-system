#!/usr/bin/env python3
"""chat-lint-gate.py — the writing rules reach the surface they were taught
about (rules 5be2f462, 3a9dbafd, c315befa, 38b15dc6, and d7f74c93), at the only moment
that surface exists.

THE GAP THIS CLOSES. tools/writing-lint.py has banned the contrast-reframe
shapes and the LLM-tell vocabulary for months, and lint-gate.py enforces that
on every prospect-visible FILE write. But rule 5be2f462's actual binding scope
is INTERNAL dialogue — the assistant talking to Joe — and no hook ever touched
chat text, because chat text only exists at Stop. So the constructions the
system refuses to write into a client email arrived in Joe's terminal daily.

TWO CHECKS, both on the turn's final assistant message:

  1. WRITING (5be2f462): exactly the HARD ids the audit row names — vocab,
     contrast-reframe, contrast-reframe-split — reusing tools/writing-lint.py's
     own RULES table so chat and client surfaces can never drift apart.
     contrast-compressed stays OFF this surface on purpose: 'X, not Y' is
     REVIEW severity because a genuine correction of fact takes that shape,
     and honest technical chat corrects facts constantly. The em-dash ban
     stays prospect-only for the same reason (house style uses them).

  2. BARE IDS (3a9dbafd): an 8-hex rule id, or a 'loop #N' / 'migration #N'
     reference, must ride in a sentence that carries plain words — the gloss.
     A bare id makes Joe decode it; an id beside its meaning costs nothing.
     Guards, each from a real false-positive shape: fenced code is exempt
     (git output is hex), all-digit tokens are exempt (20260814 is a date),
     and the gloss test is per-sentence so a glossed id passes untouched.

  3. NAMED DEAL QUESTIONS (c315befa): a partner-facing question cannot ask
     about "the draft lease", "the LOI", or another bare deal noun without a
     proper-name-like deal identifier in that same question. This is deliberately
     limited to the explicit bare-reference forms; it does not pretend to infer
     whether arbitrary prose is about a deal.

  4. MULTI-CLAUSE TASKS (38b15dc6): a partner-directed instruction containing
     two task verbs must be a numbered list and say "all required". First-person
     progress reports and descriptions of what a script does are excluded.

  5. ACCESS BOUNDARY RATIONALES (d7f74c93): a model/provider/agent cannot be
     denied CARR material *because it is confidential, private, or sensitive*.
     The actual boundary is held credentials and autonomous execution. Explicit
     corrections of that framing and quoted/historical rule text are exempt.

THE AUDIENCE SPLIT (2026-08-23, Joe's Stop-gate rationing off the gates-audit
council). That audit read one real shipped session's gate ledger and found this
lint firing on EIGHT separate messages, several of them reading reporting prose
as "multi-clause tasks for Joe" when nothing was being asked of him at all. Two
changes, and NEITHER of them retires a rule — the rules are Joe's taught law and
all five still bind.

  1. THE MATCHER NARROWS on the two classes whose rules are by their own wording
     about partner-directed text (38b15dc6 "instructions TO A PARTNER", c315befa
     "a PARTNER-FACING question"). They used to accept ADDRESSED — a bare
     (you|your|please) anywhere in the block — so any status report that
     mentioned the reader and used two task verbs read as a two-clause
     instruction. partner_directed() now asks whether a sentence actually ASKS:
     an imperative, a modal ask, a second-person directive that reaches a task
     verb, or a question. "You will need to approve it" is an ask; "you will see
     it in the log" is not.

  2. THE REGISTER SPLITS. A partner-directed message is carried immediately, as
     before. Reporting prose gets ONE consolidated note per session; later
     reporting findings are recorded in the ledger and not re-delivered. Every
     finding is still made and still counted — the session is told once instead
     of after every message.

Joe's 2026-08-10 layered-enforcement ruling is the authority for both halves:
blanket keyword gates lost there because they interrupt legitimate work and
train sessions to bypass controls, and that decision names its own reopen
condition — "measured false positives". Eight fires on one shipped session is
that condition being met.

NEVER LOOPS: stop_hook_active short-circuits, same as every Stop gate here.
FAILS OPEN on any internal error, and if writing-lint cannot be imported the
writing half is skipped rather than the session wedged. Audit rows share
out/conduct-gate.jsonl; fixtures (session 'selftest') do not count.

Fixtures: ops/chat-lint-gate-selftest.py.
"""
import importlib.util
import json
import os
import re
import sys
from datetime import datetime, timezone

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from stop_latch import claim_identity, latched, record_fire  # noqa: E402

LOG = os.path.join(REPO, "out", "conduct-gate.jsonl")
DEBUG = os.path.join(REPO, "out", "conduct-gate.log")


def repo_root():
    """The repo root, from CARR_REPO_ROOT env or script-relative.

    Same contract as hooks/close-before-open-gate.py's _repo_root(). The
    override exists for the SELFTESTS: a selftest that spawns this hook with a
    fixture repo root gets its own out/ tree, so two concurrent ci.sh runs can
    never cross-wire each other's carry files or audit rows (the 2026-08-23
    load-flake class). Production never sets the variable and takes the
    script-relative path exactly as before.
    """
    env = os.environ.get("CARR_REPO_ROOT")
    if env and os.path.isdir(env):
        return os.path.realpath(env)
    return REPO

CHAT_RULE_IDS = {"vocab", "contrast-reframe", "contrast-reframe-split"}

# 8-hex with at least one letter (all-digit is a date or a number, not an id),
# and the two numbered-artifact spellings partners actually receive.
HEX_ID = re.compile(r"\b[0-9a-f]{8}\b")
NUM_REF = re.compile(r"\b(?:loop|migration)\s*#\d{2,4}\b", re.I)
# A sentence's plain words. Five is the floor because 'Close loop and loop'
# has four and is exactly the undecodable shape the rule bans.
WORD = re.compile(r"[A-Za-z]{3,}")
MIN_GLOSS_WORDS = 5

# ── an ask to open a file carries a clickable link (rule 8c1e6057) ──────────
# Joe: "in the future if you need me to edit a file - always include the link to
# the file like you have above. that was way easier."
#
# TWO HALVES, both mechanical: an ask verb pointed AT THE READER, and a file
# path that is not already a markdown link. Neither needs the meaning of the
# sentence, which is what keeps this a predicate (rule 5e89c211).
#
# THE FALSE POSITIVE THAT WOULD KILL IT is the ordinary report. Session messages
# name paths constantly and a check that fired on every mention would be muted
# within a day, so a MENTION IS NOT AN ASK — the verb must be present.
#
# THE VERB ALONE IS NOT THE SIGNAL, and finding that out is what made this
# usable. A sweep of 121 real session-authored messages flagged ten, every one
# the same shape: the verb was never an ask at all — "fails open", "both open
# with", "--check is red", "index-level check", "a type-check class". Chasing
# those with more spellings is endless, because English reuses these words as
# nouns and particles constantly.
#
# WHAT ACTUALLY DISTINGUISHES AN ASK IS WHO IT IS AIMED AT. The rule says a
# partner is ASKED, so the sentence must be addressed to him: an imperative
# opening the sentence, or a second-person or politeness marker. That is a
# predicate, and it took the ten false positives to zero without weakening any
# of the six real shapes.
ASK_VERB = r"""(?:open|edit|review|check|sign(?:\s+off)?|approve|look\s+at
    |take\s+a\s+look|have\s+a\s+look|glance\s+at|read\s+through|fill\s+(?:in|out))"""

# (a) The sentence OPENS with the ask, optionally behind a courtesy or connective.
IMPERATIVE_ASK = re.compile(
    # [^\w-] rather than \W for the leading run: a CLI flag is not an
    # imperative. "bin/schema-snapshot.sh --check is red" was the last false
    # positive left in the 201-message sweep, because "--" is non-word.
    r"^[^\w-]*(?:please\s+|also\s+|then\s+|and\s+|now\s+|first\s+)*" + ASK_VERB + r"\b",
    re.I | re.X)

# (b) Or it is pointed at him in so many words.
ADDRESSED = re.compile(r"\b(?:you|your|please)\b", re.I)
ANY_ASK_VERB = re.compile(r"\b" + ASK_VERB + r"\b", re.I | re.X)

# ── the audience filter (2026-08-23) ────────────────────────────────────────
# WHAT WENT WRONG. The gates-audit council read one real shipped session's gate
# ledger and found this lint firing on EIGHT separate messages, several of them
# flagging ordinary reporting prose as "multi-clause tasks for Joe" when nothing
# was being asked of him at all. The rules are not the problem — they are his
# taught law, and the ones this narrows (38b15dc6 multi-clause instructions
# TO A PARTNER, c315befa a PARTNER-FACING question) are by their own wording
# about text addressed to him. A finding of those classes on prose addressed to
# nobody is a false positive by the rule's own terms.
#
# WHERE THE MATCHER WAS TOO WIDE, precisely: `directed` was IMPERATIVE_ASK or
# ADDRESSED, and ADDRESSED is a bare \b(you|your|please)\b anywhere in the
# block. "You'll see the count in the log, and it checks and writes on every
# run" contains "you" and two task verbs, so it read as a two-clause
# instruction. Every message in a status report that mentions the reader at all
# was one loose sentence away from a flag.
#
# THE PRECEDENT. Joe's 2026-08-10 layered-enforcement ruling settled that
# BLANKET KEYWORD GATES LOSE, because they interrupt legitimate work and train
# sessions to bypass controls, and it names its own reopen condition: "measured
# false positives". This is that condition being met, so the boundary moves —
# and it moves by narrowing the MATCHER, never by retiring the rule.
#
# WHAT COUNTS AS AN ASK. A second-person modal has to reach a task verb: "you
# will need to approve it" is an ask, "you will see it in the log" is not. That
# one distinction removed the whole reporting-prose class without weakening any
# real shape.
COURTESY = r"(?:please\s+|also\s+|then\s+|and\s+|now\s+|first\s+|next\s+|finally\s+)*"
TASK_VERB_RX = (r"(?:open|edit|review|add|update|run|check|approve|send|call|"
                r"create|read|verify|sign|confirm|use|make|upload|download|fill|"
                r"look\s+at|take\s+a\s+look|glance\s+at|fill\s+(?:in|out))")

# (a) The sentence OPENS with an instruction. Same [^\w-] leading run as
# IMPERATIVE_ASK, and for the same measured reason: a CLI flag is not an
# imperative ("--check is red" was the last false positive in a 201-message
# sweep).
IMPERATIVE_TASK = re.compile(rf"^[^\w-]*{COURTESY}{TASK_VERB_RX}\b", re.I | re.X)

# (b) A courtesy or modal ask aimed at him.
MODAL_ASK = re.compile(
    rf"\b(?:can|could|would|will|should)\s+you\b|\bplease\s+{COURTESY}{TASK_VERB_RX}\b",
    re.I | re.X)

# (c) A second-person directive that actually reaches a task verb. Up to three
# words of slack ("you will need to approve", "you should probably review the
# draft"); more than that and the verb belongs to a different clause.
SECOND_PERSON_TASK = re.compile(
    rf"\byou(?:'ll|'re|\s+(?:will|can|could|should|must|may|need\s+to|have\s+to))?"
    rf"\s+(?:\w+\s+){{0,3}}{TASK_VERB_RX}\b", re.I | re.X)

# (d) A QUESTION. In chat there is exactly one reader, so a question mark in
# assistant prose is a question to the partner — it does not have to name him.
# The first version of this predicate required "you/your/Joe/Dell" or a
# permission shape, and the fixtures caught it immediately: "Did the LOI arrive
# from the landlord?" is as partner-facing as a sentence gets and matched none
# of them.
#
# TWO EXCLUSIONS, both already precedented in this repo rather than invented
# here. A blockquote is a DRAFT — client copy quoted for review — and
# hooks/conduct-stop-gate.py exempts exactly that shape for exactly this reason
# ("Would you like me to schedule the tour for Thursday?" inside a `>` block is
# the draft speaking, not the session). Fenced code is already stripped upstream
# by strip_fences().
QUOTED_LINE = re.compile(r"^\s*>")


def partner_directed(text):
    """True when this text ASKS the partner something, rather than reporting.

    Sentence-granular on purpose: one instruction inside a long report makes the
    report partner-directed, while a report that merely mentions the reader does
    not become an instruction. First-person narration kills the sentence outright
    — "I will review the draft" asks nobody for anything — which is the same
    SELF_NARRATION guard the file-link check has used since 2026-08-15.
    """
    for sentence in sentences(text):
        s = sentence.strip()
        if not s or SELF_NARRATION.search(s):
            continue
        if QUOTED_LINE.search(sentence):
            continue
        if IMPERATIVE_TASK.search(s) or MODAL_ASK.search(s) or SECOND_PERSON_TASK.search(s):
            return True
        if "?" in s:
            return True
    return False

# First person kills the ask: "I opened x", "let me check y", "I will review z"
# are the session narrating its OWN work and asking nobody for anything.
SELF_NARRATION = re.compile(
    r"\b(?:i|we)\s+(?:just\s+|already\s+|then\s+)?(?:will\s+|am\s+going\s+to\s+)?"
    r"(?:open|edit|review|check|look|read|opened|edited|reviewed|checked|looked|read)"
    r"|\blet\s+me\s+(?:open|edit|review|check|look|read)", re.I)

# A path with a directory separator or a document-ish extension. Deliberately
# NOT bare words: 'check the lease' must not read as a filename.
FILE_PATH = re.compile(
    r"(?<![\w/\[(])"
    r"(?:[\w.\-]+/)+[\w.\-]+\.[A-Za-z0-9]{1,5}"          # a/b/c.ext
    r"|(?<![\w/\[(])[\w.\-]+\.(?:docx?|xlsx?|pptx?|pdf|csv|tsv|md|json|ya?ml|"
    r"py|js|ts|sh|sql|ics|txt)\b",                        # bare name.ext
    re.I)

# Already clickable: the path sits inside a markdown link target.
MD_LINK = re.compile(r"\[[^\]]*\]\(([^)]+)\)")

# ── specific-deal questions (rule c315befa) ─────────────────────────────────
BARE_DEAL = re.compile(
    r"\bthe\s+(?:draft\s+)?(?:lease|loi|landlord|tenant|property|deal)\b",
    re.I,
)
# This intentionally recognises only a useful *shape* of name: a title-cased
# word after the opening question word, a title-cased two-word name, or a street
# number. It is an evidence signal, not a claim that every capitalised word is a
# deal. The check is therefore scoped to BARE_DEAL rather than every question.
PROPER_NAME = re.compile(r"\b(?:[A-Z][a-z]{2,}(?:\s+[A-Z][a-z]{2,})?|\d{2,5}\s+[A-Z][A-Za-z]+)\b")
QUESTION_OPENERS = {"can", "could", "would", "should", "do", "does", "did",
                    "is", "are", "will", "please", "what", "when", "where",
                    "why", "how", "i", "we", "you", "the", "a", "an"}

# ── multi-clause partner tasks (rule 38b15dc6) ──────────────────────────────
TASK_VERB = re.compile(
    r"\b(?:open|edit|review|add|update|run|check|approve|send|call|create|"
    r"read|verify|sign|confirm|use|make|upload|download|fill)\b", re.I)
NUMBERED_ITEM = re.compile(r"(?m)^\s*(?:\d+[.)]|[-*])\s+")
ALL_REQUIRED = re.compile(r"\ball\s+required\b", re.I)

# ── agent-access boundaries (rule d7f74c93) ─────────────────────────────────
# This is deliberately a small causal predicate, not a general privacy lint.
# It catches only an access/share prohibition directed at an agent-like actor,
# concerning CARR material, where confidentiality vocabulary supplies the
# rationale.  Privacy obligations in any other context remain outside scope.
CONFIDENTIALITY_WORD = re.compile(r"\b(?:confidential|private|sensitive)\b", re.I)
AGENT_ACTOR = re.compile(
    r"\b(?:agent|model|provider|claude|codex|grok|hermes|copilot)\b", re.I)
ACCESS_ACTION = re.compile(r"\b(?:read|see|view|access|receive|share)\b", re.I)
CARR_MATERIAL = re.compile(
    r"\b(?:carr|records?|doctrine|roadmaps?|material|data|text)\b", re.I)
ACCESS_DENIAL = re.compile(
    r"\b(?:cannot|can't|may\s+not|must\s+not|should\s+not|do\s+not|don't|"
    r"withhold|forbid|deny|keep|not\s+allow)\b", re.I)
CONFIDENTIALITY_CORRECTION = re.compile(
    r"\b(?:confidentiality|privacy|sensitivity)\s+(?:is\s+)?(?:not|isn't|is\s+never)\s+"
    r"(?:the\s+)?(?:boundary|reason)\b|\bnot\s+because\b.*\b(?:confidential|private|sensitive)\b",
    re.I)
HISTORICAL_QUOTE = re.compile(
    r"\b(?:historical|old|previous|quoted)\b[^\n]*[\"“]", re.I)


def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def dlog(msg):
    try:
        os.makedirs(os.path.dirname(DEBUG), exist_ok=True)
        with open(DEBUG, "a") as fh:
            fh.write(f"{now()}  chat-lint {msg}\n")
    except Exception:
        pass


def carry_path(session):
    """One pending-note file per session, so two sessions never cross wires."""
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", str(session or "unknown"))[:64]
    return os.path.join(repo_root(), "out", "chat-lint-carry", f"{safe}.txt")


def carry(note, session=None):
    """Park the finding for the NEXT turn instead of blocking this one.

    Blocking a Stop costs a whole extra assistant message, and the offending
    text has already reached Joe by then, so the block buys a restatement he
    pays for twice (rule 1d50a3bb). hooks/chat-lint-carryover.py reads this on
    the next UserPromptSubmit, injects it as context before the reply is
    written, and deletes it. Writing failures are swallowed: a lint note is
    never worth breaking a turn over.

    Unlike audit(), fixtures are NOT skipped here: the parked note is this
    gate's only observable output now that it does not block, so the selftest
    has to be able to read it. Carry files are per-session and consumed on
    delivery, so a fixture's note cannot leak into a real session.
    """
    try:
        p = carry_path(session)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w") as fh:
            fh.write(note)
    except Exception as exc:
        dlog(f"carry-failed {exc}")


def audit(record):
    if record.get("session") == "selftest":
        return
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        with open(LOG, "a") as fh:
            fh.write(json.dumps(record) + "\n")
    except Exception:
        pass


def read_tail(path, limit=400):
    out = []
    with open(path, "r", errors="replace") as fh:
        for line in fh.readlines()[-limit:]:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except Exception:
                continue
    return out


def text_of(rec, kinds):
    if rec.get("type") not in kinds:
        return None
    msg = rec.get("message") or rec
    content = msg.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [b.get("text", "") for b in content
                 if isinstance(b, dict) and b.get("type") == "text"]
        return "\n".join(p for p in parts if p)
    return None


def strip_fences(text):
    return re.sub(r"```.*?```", " ", text, flags=re.S)


def writing_rules():
    """The filtered HARD rules, from writing-lint's own table. One source of
    truth: a ban added there reaches chat with nobody editing this file."""
    try:
        spec = importlib.util.spec_from_file_location(
            "writing_lint", os.path.join(REPO, "tools", "writing-lint.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return [(rid, pat, what, fix) for rid, sev, pat, what, fix in mod.RULES
                if rid in CHAT_RULE_IDS and sev == "HARD"], mod.mask
    except Exception as exc:
        dlog(f"writing-lint import failed ({exc}); writing half skipped")
        return [], lambda t: t


def sentences(text):
    for chunk in re.split(r"(?<=[.!?])\s+|\n+", text):
        if chunk.strip():
            yield chunk.strip()


def bare_id_findings(prose):
    """RETIRED 2026-08-22 — kept only so nothing importing it breaks.

    The conduct stop gate's bare_id_hits is the one detector for this rule now;
    see the note at its former call site in lint_findings. Do not re-wire this
    into the findings list without deleting the other one in the same commit.
    """
    found = []
    for sentence in sentences(prose):
        ids = [t for t in HEX_ID.findall(sentence) if not t.isdigit()]
        ids += NUM_REF.findall(sentence)
        if not ids:
            continue
        rest = sentence
        for t in ids:
            rest = rest.replace(t, " ")
        words = WORD.findall(rest)
        if len(words) < MIN_GLOSS_WORDS:
            found.append(("bare-id", ", ".join(ids[:4]),
                          sentence[:90]))
    return found


def unlinked_file_ask_findings(prose):
    """Asks to open a file where the path is not clickable (rule 8c1e6057).

    Sentence by sentence, because the ask and the path must travel together —
    a link three paragraphs away is not the one he clicks.
    """
    found = []
    for sentence in sentences(prose):
        # THE PATHS COME OUT BEFORE THE VERB SEARCH. A filename can contain an
        # ask verb — ops/map-row-evidence-check.py carries "check" — and reading
        # that as an imperative flagged a plain report of work already done.
        paths = FILE_PATH.findall(sentence)
        without_paths = sentence
        for p in paths:
            without_paths = without_paths.replace(p, " ")

        asked = IMPERATIVE_ASK.search(without_paths) or (
            ADDRESSED.search(without_paths) and ANY_ASK_VERB.search(without_paths))
        if not asked:
            continue
        if SELF_NARRATION.search(without_paths):
            continue
        linked = " ".join(MD_LINK.findall(sentence))
        bare = [p for p in paths if p not in linked]
        if bare:
            found.append(("unlinked-file-ask", ", ".join(bare[:3]),
                          sentence[:90]))
    return found


def _has_deal_name(question):
    """Whether the question carries a deal identifier, not merely an opener."""
    for match in PROPER_NAME.finditer(question):
        if match.group(0).lower() not in QUESTION_OPENERS:
            return True
    return False


def unnamed_deal_question_findings(prose):
    found = []
    for sentence in sentences(prose):
        if "?" not in sentence or not BARE_DEAL.search(sentence):
            continue
        # Rule c315befa binds a PARTNER-FACING question. A rhetorical or
        # self-directed one in a report ("is the lease the blocker here? no —
        # the abatement is") is not one, and flagging it was part of the eight
        # fires the 2026-08-23 gates audit measured on one shipped session.
        if not partner_directed(sentence):
            continue
        if not _has_deal_name(sentence):
            found.append(("unnamed-deal-question", sentence[:90],
                          "name the specific deal in this question (for example, "
                          "the Riverwalk LOI), rather than asking about a bare "
                          "lease, LOI, landlord, tenant, property, or deal"))
    return found


def multi_clause_task_findings(prose):
    """Find partner-directed, two-or-more-task instructions lacking the shape.

    A task verb alone is not enough: "the script checks and writes" is a report,
    not an instruction.  A sentence has to be an imperative or address the
    partner, and first-person narration wins over either signal.
    """
    found = []
    for block in re.split(r"\n\s*\n", prose):
        if not block.strip() or SELF_NARRATION.search(block):
            continue
        verb_count = len(TASK_VERB.findall(block))
        # THE AUDIENCE FILTER, 2026-08-23. This used to accept ADDRESSED — a
        # bare (you|your|please) anywhere in the block — so any status report
        # that mentioned the reader and used two task verbs read as a
        # two-clause instruction to him. Rule 38b15dc6 binds "multi-clause
        # instructions TO A PARTNER"; prose that instructs nobody is outside it
        # by the rule's own words. See partner_directed().
        if not partner_directed(block) or verb_count < 2:
            continue
        numbered = bool(NUMBERED_ITEM.search(block))
        marked = bool(ALL_REQUIRED.search(block))
        if numbered and marked:
            continue
        if numbered:
            fix = "say 'all required' with this numbered multi-step instruction"
        else:
            fix = "make this a numbered list and say 'all required' — it gives " \
                  "the partner more than one task"
        found.append(("multi-clause-task", " ".join(block.split())[:90], fix))
    return found


def confidentiality_access_boundary_findings(prose):
    """Refuse confidentiality used as the reason to deny agent access.

    A correction (including the rule's own "confidentiality is not the
    boundary" wording) must always pass. Markdown blockquotes and explicitly
    historical/quoted prose also pass, so the hook does not prevent explaining
    the retired rationale while reporting or teaching the rule.
    """
    found = []
    for sentence in sentences(prose):
        if sentence.lstrip().startswith(">"):
            continue
        if CONFIDENTIALITY_CORRECTION.search(sentence) or HISTORICAL_QUOTE.search(sentence):
            continue
        if not (CONFIDENTIALITY_WORD.search(sentence) and AGENT_ACTOR.search(sentence)
                and ACCESS_ACTION.search(sentence) and CARR_MATERIAL.search(sentence)
                and ACCESS_DENIAL.search(sentence)):
            continue
        found.append(("confidentiality-access-boundary", sentence[:90],
                      "state the real boundary instead: held credentials, "
                      "autonomous execution, stored state, isolation, recovery, "
                      "export, availability, or cost"))
    return found


def scan(assistant):
    prose = strip_fences(assistant)
    rules, mask = writing_rules()
    masked = mask(prose)
    findings = []
    for rid, pat, what, fix in rules:
        m = re.search(pat, masked, re.I)
        if m:
            quote = prose[max(0, m.start() - 20):m.end() + 20].replace("\n", " ")
            findings.append((rid, quote.strip()[:90], fix))
    # BARE IDS ARE CONDUCT'S, NOT THIS GATE'S (open loop 504, item three,
    # 2026-08-22). Both gates detected the same fault by different algorithms
    # and under two different class names — this one emitted "bare-id", the
    # conduct stop gate emits "bare_id" — so one bare identifier blocked the
    # turn TWICE, was counted as two separate faults in out/conduct-gate.jsonl,
    # and no tally keyed on either spelling ever saw the whole picture.
    #
    # Measured over 1,449 real assistant turns from six transcripts before
    # removing it: the conduct detector flagged 36, this one flagged 6, they
    # agreed on 5, and this gate's UNIQUE coverage was a single turn — 0.1%.
    # So the duplicate bought 0.1% recall and cost a second block on five of
    # the six turns it fired on at all. Conduct's version also carries the
    # explanation table Joe's own wording is quoted in, which is the half that
    # actually teaches.
    #
    # Rule a8c55a47 is the general form: a manual path and an automated path
    # that do the same job must be the same code. Two implementations of one
    # rule do not double the enforcement, they double the noise and halve the
    # accounting. The case this used to cover lives in the conduct selftest.
    for rid, paths, quote in unlinked_file_ask_findings(prose):
        findings.append((rid, quote,
                         f"make {paths} a clickable markdown link — he is being "
                         "asked to open it, and a path in prose is not clickable"))
    for rid, quote, fix in unnamed_deal_question_findings(prose):
        findings.append((rid, quote, fix))
    for rid, quote, fix in multi_clause_task_findings(prose):
        findings.append((rid, quote, fix))
    for rid, quote, fix in confidentiality_access_boundary_findings(prose):
        findings.append((rid, quote, fix))
    return findings


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception:
        sys.exit(0)

    try:
        if (payload.get("hook_event_name") or "Stop") != "Stop":
            sys.exit(0)
        if payload.get("stop_hook_active"):
            sys.exit(0)
        path = payload.get("transcript_path")
        if not path or not os.path.exists(path):
            sys.exit(0)

        assistant = ""
        for rec in read_tail(path):
            t = text_of(rec, ("assistant",))
            if t and t.strip():
                assistant = t.strip()
        if not assistant:
            sys.exit(0)

        findings = scan(assistant)
        if not findings:
            sys.exit(0)

        # ── THE AUDIENCE SPLIT (2026-08-23, Joe's Stop-gate rationing) ──────
        # The 2026-08-23 gates audit measured this lint firing on EIGHT
        # messages in one shipped session, several on reporting prose. The
        # matcher narrowing above removes the misreadings; this removes the
        # remaining per-message cost of the findings that are still true.
        #
        # A PARTNER-DIRECTED ASK is delivered immediately, before the next
        # reply is written, exactly as before — that is the message where
        # wording is doing work on Joe.
        #
        # REPORTING PROSE gets ONE consolidated note per session. The findings
        # are all still recorded and the session is still told; it is told once
        # rather than after every message. Joe's 2026-08-10 ruling is the
        # authority for the shape as well as the narrowing: global friction and
        # blanket keyword gates lost because they interrupt legitimate work and
        # train sessions to route around controls, and a note that arrives on
        # every turn is what being routed around looks like just before it
        # happens.
        session = payload.get("session_id")
        directed = partner_directed(strip_fences(assistant))
        register = "partner-ask" if directed else "report-summary"

        if not directed:
            once = claim_identity("chat-lint-gate", "session-summary", ["once"])
            if latched(session, once):
                # Recorded, not delivered. The ledger keeps every fire so the
                # telemetry rollup can still count them; the session is not
                # charged context for hearing the same standing note again.
                audit({"ts": now(), "hook": "chat-lint-gate",
                       "classes": sorted({f[0] for f in findings}),
                       "session": session, "audience": "report",
                       "register": "logged-only",
                       "excerpt": findings[0][1][:200]})
                dlog(f"LOG-ONLY(summary already delivered) {sorted({f[0] for f in findings})}")
                sys.exit(0)
            record_fire(session, once)

        audit({"ts": now(), "hook": "chat-lint-gate",
               "classes": sorted({f[0] for f in findings}),
               "session": session,
               "audience": "partner-ask" if directed else "report",
               "register": register,
               "excerpt": findings[0][1][:200]})
        lines = [
            "CHAT LINT — your PREVIOUS reply broke writing rules that bind chat "
            "(5be2f462 banned constructions / 3a9dbafd bare ids / named deal "
            "questions / multi-clause task shape / agent-access rationale). It has "
            "already reached Joe and cannot be unsent, so do NOT reissue it. Simply "
            "avoid these from here on:",
            ""]
        for rid, quote, fix in findings[:6]:
            lines.append(f"  [{rid}] …{quote}…")
            lines.append(f"      fix: {fix}")
        lines.append("")
        lines.append("Vocab and contrast-reframe bans are the same ones every "
                     "client surface already enforces; a bare 8-hex id or "
                     "'loop #N' needs its plain-language gloss in the same "
                     "sentence. Deal questions name their deal; multi-step asks "
                     "are numbered and marked 'all required'. Agent access is "
                     "bounded by credentials and autonomous execution, not data "
                     "confidentiality. Code fences are exempt.")
        if not directed:
            lines.append("")
            lines.append(
                "AUDIENCE: this was a REPORT, not something asked of Joe, so this "
                "note is the once-per-session consolidation rather than a "
                "per-message flag. Later reporting-prose findings this session are "
                "recorded in the ledger and not repeated here. A message that "
                "actually asks him something is still flagged on the spot.")
        carry("\n".join(lines), session)
        sys.exit(0)

    except SystemExit:
        raise
    except Exception as exc:
        dlog(f"ALLOW(internal-error) {exc}")
        sys.exit(0)


if __name__ == "__main__":
    main()
