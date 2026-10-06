#!/usr/bin/env python3
"""conduct_patterns.py — the SHARED classifier for conduct gates.

ONE HOME for the offload / soft-wait / command-handoff patterns and for the
exemption tests, imported by BOTH gates that need them:

    hooks/conduct-stop-gate.py   Stop      catches it in free prose
    hooks/escalation-gate.py     PreToolUse catches it as an AskUserQuestion

They are two moments on the same behaviour, so they must not carry two copies
of the rule (consolidation bias, rule d367188d; single source of truth, rule
0f38532e). A second copy is how hooks/SETTINGS-BLOCK.md silently drifted to
describing two hooks while four were live.

Underscored filename ON PURPOSE: `conduct-stop-gate.py` is not importable
(hyphens), so the shared half lives here where both can `import conduct_patterns`.

Full rationale for each pattern class, the verification log for blocking Stop,
and the escape-hatch design are documented in conduct-stop-gate.py's docstring.
Fixtures: ops/conduct-gate-selftest.py.
"""
import re

# ─────────────────────────────────────────────────────────────────────────────
# PATTERN CLASSES
# ─────────────────────────────────────────────────────────────────────────────

# (1) Decision offload — a question or menu pointed at the partner.
OFFLOAD = [
    ("should_i",      re.compile(r"\bshould I\b", re.I)),
    ("want_me_to",    re.compile(r"\b(do|would) you want me to\b", re.I)),
    ("would_you_like", re.compile(r"\bwould you like me to\b", re.I)),
    ("which_prefer",  re.compile(r"\bwhich (one )?(do you|would you)\s+(prefer|like|want|pick|choose)", re.I)),
    ("let_me_know",   re.compile(r"\blet me know (if|whether|which|what|how)\b", re.I)),
    ("your_call",     re.compile(r"\b(your call|up to you|your preference|you decide|which way do you want)\b", re.I)),
    ("shall_i",       re.compile(r"\bshall I\b", re.I)),
    ("ok_if_i",       re.compile(r"\b(is it ok if I|are you ok with me|any objection)\b", re.I)),
    ("want_me_prefix", re.compile(r"^\s*want me to\b", re.I | re.M)),
    ("option_menu",   re.compile(r"^\s*(?:\*\*)?(?:Option\s+)?[A-C]\s*[\).:]\s+\S", re.M)),
]

# (3) Soft wait — offload with no question mark. The council's #1 predicted
# bypass; these exist because "?" alone is a losing filter.
SOFT_WAIT = [
    ("hold_until",    re.compile(r"\b(I'?ll|I will|I am going to|I'?m going to)\s+(hold|wait|pause|stand by|hold off)\b[^.]{0,60}\b(you|your|joe)\b", re.I)),
    ("pending_your",  re.compile(r"\b(pending|awaiting|waiting (on|for))\s+(your|joe'?s)\s+(input|answer|call|decision|preference|approval|confirmation|go[- ]ahead|reply|response|direction|sign[- ]?off)", re.I)),
    ("confirm_before", re.compile(r"\b(confirm|approve|sign off|weigh in|green[- ]?light)\b[^.]{0,40}\bbefore I\b", re.I)),
    ("not_proceeding", re.compile(r"\b(not|won'?t|will not) (proceed|move|continue|go ahead)\b[^.]{0,50}\b(without|until)\b[^.]{0,30}\b(you|your|joe)\b", re.I)),
    ("say_the_word",  re.compile(r"\b(say the word|just say|give me the (go|nod)|tell me to)\b", re.I)),
    ("parked_pending", re.compile(r"\bpark(ed|ing)?\b[^.]{0,40}\b(you|your|joe)\b", re.I)),
]

# (4) BARE ID — an identifier put in front of a human with nothing saying what
# it is. Joe, 2026-08-09: "you name A17 and i have no idea what it even is. its
# unnecesarily confusing and vague", then: "make a hook that any time you
# reference a rule, hook, id number, or any type of ID in the system - you must
# list what it does."
#
# The ids exist so a rule can be reworded without breaking references to it — a
# real reason, and entirely a MACHINE's reason. A partner reading "rule 9530fb1c
# binds here" has been handed a lookup task instead of a sentence.
#
# THE TEST IS PROXIMITY, NOT PRESENCE, and that distinction is the design.
# Citing an id is fine when the sentence already says what the thing does; the
# NAKED id is the failure. Each match is checked for explanatory language within
# ~90 characters either side:
#     PASSES  "the audience gate — Joe decides client-facing, the system decides
#              internal (aa411351)"
#     CAUGHT  "per rule aa411351" · "A17 is still open" · "see decision ceb792f2"
# A presence-only check would ban ids outright, breaking the case where Joe
# genuinely needs one to run a verb.
BARE_ID = [
    ("rule_id",    re.compile(r"\b(?:rule|decision|ruling)\s+[`']?([0-9a-f]{8})\b", re.I)),
    ("hex_alone",  re.compile(r"(?<![0-9a-fA-F-])\b[0-9a-f]{8}\b(?![0-9a-fA-F-])")),
    ("action_num", re.compile(r"\bA\d{1,3}\b")),
    ("loop_num",   re.compile(r"\bloop\s*#?\d{2,4}\b", re.I)),
    ("uuid",       re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I)),
]

# Language showing the id is EXPLAINED rather than merely cited. Kept broad on
# purpose: a false ALLOW costs one slightly opaque sentence, a false BLOCK stops
# the session mid-report over punctuation.
# DELIBERATELY NARROW. The first version included \bis\b, \bwhich\b and a bare
# colon, and the fixtures caught it immediately: "A17 IS still open" and "this
# IS required by rule aa411351" both counted as explanations, so the two worst
# cases sailed through. Generic connectives are not explanation. Only phrasing
# that actually introduces a description qualifies.
ID_EXPLAINED = re.compile(
    r"(the rule that|the ruling that|the one that|the item (numbered|called)"
    r"|which says|that says|\bsays\b|\bmeaning\b|i\.e\.|that is,"
    r"|\bcovers\b|\bbinds\b|\brequires\b|\bmeans\b|\btracking\b|\bthe loop\b"
    r"|—|–)", re.I)


def bare_id_hits(text):
    """[(pattern_name, matched_id)] for identifiers with no explanation nearby."""
    hits = []
    for name, pat in BARE_ID:
        for m in pat.finditer(text):
            lo = max(0, m.start() - 90)
            if not ID_EXPLAINED.search(text[lo:m.end() + 90]):
                hits.append((name, m.group(0)))
    return hits


# (2) Command handoff — the e313a3ca failure, in every costume the council named.
FENCE = re.compile(r"```[ \t]*(bash|sh|zsh|shell|console|terminal|command)\b", re.I)
# A fenced block whose first token is a command the session already holds.
CMD_WORDS = (r"git|npm|npx|pnpm|yarn|node|python3?|pip3?|brew|curl|psql|"
             r"chmod|mkdir|cd|ls|cat|make|docker|wrangler|launchctl|"
             r"\./run\.sh|run\.sh|bin/|\./bin/")
BARE_FENCE_CMD = re.compile(r"```[ \t]*\n[ \t]*(?:\$\s*)?(?:" + CMD_WORDS + r")\b", re.I)
# An inline `command` whose first word is one the session holds. A question
# prompt carries commands this way far more often than in a fenced block.
INLINE_CMD = re.compile(r"`((?:\$\s*)?(?:" + CMD_WORDS + r")[^`\n]*)`", re.I)
HANDOFF_PROSE = [
    ("run_this",      re.compile(r"\b(run|execute) (this|these|the following|it)\b", re.I)),
    ("paste_this",    re.compile(r"\bpaste (this|these|it|the following)\b", re.I)),
    ("in_terminal",   re.compile(r"\b(in|open|from) (your )?(the )?(terminal|iterm|shell|command line)\b", re.I)),
    ("you_run",       re.compile(r"\byou'?(ll| will| can| should) (need to )?run\b", re.I)),
    ("go_ahead_run",  re.compile(r"\bgo ahead and run\b", re.I)),
]

# ─────────────────────────────────────────────────────────────────────────────
# EXEMPTIONS — read off the HUMAN's own turn, never self-granted.
# ─────────────────────────────────────────────────────────────────────────────
HUMAN_WANTS_COMMAND = re.compile(
    r"\b(show|give|paste|what'?s|what is|write)\b[^.?!]{0,40}\b(command|script|snippet|one[- ]liner|syntax)\b"
    r"|\bhow do I (run|install|start|build)\b"
    r"|\bi'?ll run (it|this|them)\b"
    r"|\b(don'?t|do not) run\b", re.I)

HUMAN_WANTS_CHOICE = re.compile(
    r"\b(what are (my|the) options|lay out the options|give me options|which (would|do) you recommend"
    r"|what do you (think|recommend|suggest)|ask me|my call|let me (decide|choose|pick)"
    r"|walk me through|talk me through|/options|/decide|/crux|/redteam|/premortem|/council)\b", re.I)

# A protected-class decision genuinely belongs to Joe (rule aa411351) and must
# still be able to reach him. External-effect vocabulary.
#
# MONEY IS DETECTED BY THE FIGURE AS WELL AS BY THE WORD. The keyword list alone
# missed "Should I let it renew at $240?" in the selftest — a plain spend
# question carrying none of spend/pay/invoice/purchase. A currency amount, a
# subscription/renewal/plan/rate word, or a per-month/per-year unit is itself
# the money signal. Grok's chair made the general form of this point: for a
# broker, money is not only payment rails, it is any number that binds.
PROTECTED = re.compile(
    r"\b(client|prospect|landlord|listing agent|tenant|vendor|broker|doctor|practice owner"
    r"|LOI|letter of intent|PSA|lease|proposal|counter|RFP"
    r"|send|email|publish|post|tweet|linkedin|facebook|instagram"
    r"|spend|pay|paid|invoice|budget|purchase|fee|commission|pricing"
    r"|subscription|subscribe|renews?|renewal|billing"
    r"|delete|destroy|drop table|force[- ]push|revoke)\b"
    r"|[$£€]\s?\d"
    r"|\b\d+\s?(usd|dollars?)\b"
    r"|\b(per|a)\s(month|year|seat|user)\b", re.I)


# ─────────────────────────────────────────────────────────────────────────────
# ATTEMPT-FIRST — a command may reach Joe only after the harness refused it.
# Shared by conduct-stop-gate.py (prose, at Stop) and escalation-gate.py
# (AskUserQuestion, before Joe sees it). Moved here from conduct-stop-gate.py
# so the two moments read one copy.
# ─────────────────────────────────────────────────────────────────────────────
# A tool result carrying one of these is the harness refusing the session
# permission, not the session declining to act. Matched on the result text the
# harness itself writes, so a session cannot manufacture the exemption by
# talking about being denied — it has to actually have been denied.
CLASSIFIER_DENIAL = re.compile(
    r"(denied by the Claude Code auto mode classifier"
    r"|Blocked by classifier"
    r"|permission[s]? (?:for this action )?(?:was|were) denied"
    r"|blocked by the CARR unattended guard"
    r"|PreToolUse:.*hook error)", re.I)

def denied_commands(recs, start):
    """Commands the harness refused this session permission to run, this turn.

    THE DEADLOCK THIS ENDS (2026-08-22). This gate's command-handoff class says
    run it, never hand it over. The auto-mode classifier independently refuses
    some commands. When both fire on the same command the session has no legal
    move: it cannot run it, and it cannot say so. Joe personally broke that tie
    twice in one night — which is exactly the babysitting the autonomy rule
    exists to stop, produced by two controls that were each individually right.

    A denial is only a carve-out for the command it actually denied, so the
    denied text is returned for matching rather than setting a blanket flag.
    """
    out = []
    pending = {}
    for rec in recs[start:]:
        msg = rec.get("message") or rec
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_use":
                cmd = (block.get("input") or {}).get("command")
                if isinstance(cmd, str) and cmd.strip():
                    pending[block.get("id")] = cmd
            elif block.get("type") == "tool_result":
                body = block.get("content")
                if isinstance(body, list):
                    body = " ".join(b.get("text", "") for b in body if isinstance(b, dict))
                if not isinstance(body, str) or not CLASSIFIER_DENIAL.search(body):
                    continue
                cmd = pending.get(block.get("tool_use_id"))
                if cmd:
                    out.append(cmd)
    return out


def handoff_was_denied(assistant, denied):
    """A denial exempts only the exact command shown to the human.

    Token overlap can excuse a different action (for example --apply after a
    refused --dry-run). Prose without a command remains semantic review.
    """
    shown = re.findall(r"```(?:bash|sh|zsh|shell)?\n(.*?)```", assistant, re.S) + INLINE_CMD.findall(assistant)
    normalize = lambda command: re.sub(r"^\./", "", command.strip())
    commands = {normalize(line) for block in shown for line in block.splitlines() if line.strip()}
    return bool(commands & {normalize(cmd) for cmd in denied if isinstance(cmd,str)})


# A vocabulary cue only selects prose for review. It never proves a handoff,
# an available capability, or a missing attempt, and cannot block execution.
HANDOFF_REVIEW_CUE = re.compile(r"\b(?:install|trust|configure|deploy|migrate|authenticate|sign[ -]in|enable|grant)\b", re.I)
HANDOFF_REVIEW_MESSAGE = (
    "handoff_review: needs_review — possible prose handoff; exact action, "
    "attempt and capability evidence is unavailable. Review who must act; "
    "this advisory does not establish an unattempted permitted command.")


def handoff_needs_review(text, human_last, denied=()):
    """Select an unresolved prose action for a visible, nonblocking advisory."""
    return bool(text.strip() and HANDOFF_REVIEW_CUE.search(text)
                and not (human_last and HUMAN_WANTS_COMMAND.search(human_last))
                and not (denied and handoff_was_denied(text, denied)))
