#!/usr/bin/env python3
"""conduct-stop-gate.py — the Stop HARD GATE for session conduct.

WHY THIS EXISTS, IN ONE PARAGRAPH. On 2026-08-09 Joe re-issued three
instructions that were ALREADY ACTIVE TAUGHT RULES, recited to the session at
boot, and violated anyway:
  - 14e0408b "stop asking me to decide everything"          (taught 2026-08-02)
  - e313a3ca "no more asking me to run terminal commands"   (taught 2026-08-03)
  - 179be4b8 + 647f843d "no more adding loops"              (taught twice)
His words that sitting: "you have become lazy and are trying to offload many
tasks and decisions to me... If you were a real COO, I would have replaced you
by now for being so insecure with decision making." A prose rule in a context
window is ADVISORY no matter how forcefully worded or how often recited. The
only rule of the three that ever changed behaviour is 647f843d, and it changed
behaviour because it got CODE: a verb refusal plus a check constraint. This
hook is that same move applied to the other two.

THE CHANNEL PROBLEM, STATED HONESTLY. "Asking Joe to decide" and "handing Joe a
command to paste" have NO TOOL CALL. They are free prose in an assistant turn.
PreToolUse cannot see them; there is no PreAssistantMessage deny in this
product. So generation-time prevention is IMPOSSIBLE here, and this file does
not pretend otherwise. What IS possible — live-verified on this machine
2026-08-09, see VERIFICATION below — is that a Stop hook returning
{"decision":"block","reason":...} BLOCKS the session from ending and the model
then obeys the reason text. So the honest guarantee is:

    NOT "impossible to say"  —  but "impossible to LEAVE SAID and walk away".

The session is forced back to work and must replace the offload with the
finished thing before it is allowed to stop. That is the real ceiling on a
prose channel, it is a large improvement over a nudge, and calling it anything
stronger would be the "hardness cosplay" the review council explicitly warned
against.

VERIFICATION (rule 97326357 — a claim about a surface becomes doctrine only
after a live test FROM that surface). Probe run 2026-08-09 in a scratch dir: a
Stop hook emitting {"decision":"block","reason":"output the token ZZQQ7"}
against a session instructed to say "HELLO" produced "ZZQQ7", and the hook
fired twice (block, then allow). Blocking Stop works and the injected reason is
obeyed. Re-run that probe before trusting this file on any new Claude Code
build.

WHAT IT CATCHES, and why each pattern class is here:
  (1) DECISION OFFLOAD — "should I", "would you like me to", "do you want me
      to", "which do you prefer", "let me know if", option menus addressed to
      the partner. Rule 14e0408b + aa411351.
  (2) COMMAND HANDOFF — fenced bash/sh/zsh/console blocks, "run this", "paste
      this into Terminal", or a bare line starting with a command the session
      already holds permission to run. Rule e313a3ca.
  (3) SOFT WAIT — "I'll hold here until you weigh in", "parked pending your
      preference", "confirm before I proceed". The review council flagged this
      as the #1 six-week bypass: pressure selects for offload phrased OUTSIDE
      a question mark. Catching only "?" loses within weeks.

THE ESCAPE HATCHES, deliberately narrow and all STRUCTURAL rather than
vibes-based, so this gate never blocks legitimate work:
  - The partner ASKED. If the human's own last turn requested a command, a
    recommendation, or a choice ("show me the command", "what are my options",
    "which would you pick"), the corresponding class is exempt for that turn.
    A session cannot grant itself this; it comes off the human's keystrokes.
  - A PROTECTED-CLASS question. Client-facing, public-facing, money and
    irreversible decisions are Joe's BY RULE (aa411351) and must still reach
    him. Detected by external vocabulary — client, landlord, listing agent,
    LOI, send, publish, post, spend, delete. Those turns pass.
  - The gate already fired this turn (`stop_hook_active`) — never loop.

FAILS OPEN on every error. A conduct gate that wedges a session is worse than
the conduct it prevents; the egress guard fails CLOSED because it protects
against exfiltration, this one fails OPEN because it protects against rudeness.

AUDIT SIGNAL (the thing the rule-shape gate demands, and the reason the
development-kit ledgers died silently): every fire appends one JSON line to
out/conduct-gate.jsonl. Zero fires and zero instrumentation look identical
without it. `run.sh health` reads the count; the return-brief prints it.

WR-000019 SLICE S8 — THE WRITING SHADOW CHECK (rule 5be2f462). The 47-message
carry corpus in out/chat-lint-carry/ is chat-lint-gate.py's own post-hoc
record: it fires at Stop but only ever CARRIES a note to the next turn
(hooks/chat-lint-carryover.py), because the finding surfaces after the message
has already reached Joe and "cannot be unsent" — the exact phrase its own
carried note uses. Of that corpus, 25 of 48 recorded findings are rule
5be2f462's own vocab / contrast-reframe / contrast-reframe-split
constructions — the only class in that corpus this gate does not already
reach some other way (bare ids are conduct's own bare_id_hits() below,
already blocking; the rest — multi-clause-task, unlinked-file-ask,
unnamed-deal-question, confidentiality-access-boundary — are rules
38b15dc6/8c1e6057/c315befa/d7f74c93, out of this slice's DoD).

shadow_writing_check() reuses chat-lint-gate.py's OWN writing_rules() /
strip_fences() — the identical RULES-table lookup, not a second copy of the
regex (rule a8c55a47) — so the SAME 5be2f462 detection that carries a note
post-send there also runs PRE-SEND here, at the Stop boundary this file
already owns. It is folded into THIS gate rather than given its own Stop
reopener, because Joe's 2026-08-23 rationing capped Stop reopeners at three
(core conduct — this file — completion-evidence, drift-assertion) and a
banned-construction finding on the turn's own final message is a conduct
shape, not a new one.

BORN SHADOW, LOG-ONLY, NEVER A BLOCKING INPUT. A gate-lifecycle.json entry
keyed "conduct-stop-gate.py:chat-writing-shadow" carries the mode flag;
shadow_writing_check() consults it and, only when it reads "shadow", appends
a finding row to the DEDICATED out/conduct-gate-shadow.jsonl — never to
out/conduct-gate.jsonl, whose catch_metric for this gate's OWN lifecycle
entry already treats every row there as "genuine detection" (row_exists);
mixing an unpromoted, log-only finding into that stream would corrupt the
existing enforcing metric. The finding never enters `classes`/`findings` and
never changes whether this turn blocks — Joe promotes it from
ops/config/gate-lifecycle.json once the shadow window shows an acceptable
false-positive rate, the same lifecycle every other shadow gate here follows.
"""
import json
import os
import re
import sys
from datetime import datetime, timezone

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOOKS = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.join(REPO, "out", "conduct-gate.jsonl")
DEBUG = os.path.join(REPO, "out", "conduct-gate.log")

# PATTERNS AND EXEMPTIONS LIVE IN conduct_patterns.py — shared verbatim with
# hooks/escalation-gate.py, which catches the same behaviour one moment earlier
# (as an AskUserQuestion tool call rather than as prose). Two moments, one rule,
# one copy.
sys.path.insert(0, HOOKS)
sys.path.insert(0, REPO)
from conduct_patterns import (  # noqa: E402
    OFFLOAD, SOFT_WAIT, FENCE, BARE_FENCE_CMD, HANDOFF_PROSE,
    HUMAN_WANTS_COMMAND, HUMAN_WANTS_CHOICE, PROTECTED, bare_id_hits,
    CLASSIFIER_DENIAL, denied_commands, handoff_was_denied,
    handoff_needs_review, HANDOFF_REVIEW_MESSAGE,
)

# ── WR-000019 S8: the writing shadow check (rule 5be2f462) ─────────────────
# Overridable by env for the selftest, same convention chat-lint-gate.py's
# repo_root() and stop_latch.py's CARR_STOP_LATCH_STATE use: a fixture gets its
# own files so two concurrent runs — or a real session running alongside a
# selftest — never share, and can never leave a real trace behind.
GATE_LIFECYCLE_PATH = (os.environ.get("CARR_GATE_LIFECYCLE_PATH")
                        or os.path.join(REPO, "ops", "config", "gate-lifecycle.json"))
SHADOW_LOG = (os.environ.get("CARR_CONDUCT_SHADOW_LOG")
              or os.path.join(REPO, "out", "conduct-gate-shadow.jsonl"))
SHADOW_GATE_KEY = "conduct-stop-gate.py:chat-writing-shadow"


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
    """One JSON line per fire. This is the audit signal — without it a gate
    that never fires and a gate that is not installed look identical.

    FIXTURES DO NOT COUNT. The selftests spawn this hook for real, so without
    this skip they write real-looking rows into the ledger — 169 of them on the
    first run, which would have made the fire count meaningless the moment
    anyone tried to read it. A metric its own test suite inflates is not a
    metric (rule 590b11e1: no metric without a bound action)."""
    if record.get("session") == "selftest":
        return
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        with open(LOG, "a") as fh:
            fh.write(json.dumps(record) + "\n")
    except Exception:
        pass


# ── THE RESEND CARRIES THE DELTA (rule 1d50a3bb) ─────────────────────────────
#
# Joe, 2026-08-13, after reading a ~40-line report, having this gate block the
# turn for one missing verification, and getting the same ~40 lines back with
# five new ones on top: "why did you just say the same thing twice in a row? I
# feel like you're just wasting my tokens on purpose." The gate asked for the
# verification. It did not ask for the report again.
#
# It compounds, which is why it needs a mechanism rather than care: these gates
# fire on long working turns, so the naive response re-sends the biggest
# possible message at the worst possible moment, and again on the next block.
#
# AT MOST ONCE, and that bound is the design. This gate's oldest promise is
# that it never wedges a session — `stop_hook_active` short-circuits it for
# exactly that reason. So a blocked message is remembered, used for one
# comparison, and marked spent in the same read. Whatever the session sends
# after that, this check stays silent for that turn. A gate that can refuse
# twice for the same reason is one bad regex away from a session nobody can
# close.
DELTA_HOME = os.path.join(REPO, "out", "conduct-gate-blocked")
# Above this share of the resend already read, it is a repeat rather than a
# delta. Two thirds leaves generous room for a genuine recap line or two.
REPEAT_SHARE = 0.66
# Short replies are all delta by definition; comparing them produces noise.
MIN_SENTENCES = 4


def _sentences(text):
    """Content lines, normalised. Whitespace and case only — the failure is a
    message pasted back, not paraphrase, and a fuzzy matcher here would start
    refusing honest restatements of a fact that genuinely has one wording."""
    out = []
    for chunk in re.split(r"(?<=[.!?])\s+|\n+", text or ""):
        s = " ".join(chunk.split()).strip().lower()
        if len(s) > 15:                    # skip headers, bullets, one-word lines
            out.append(s)
    return out


def _delta_path(session_id, home):
    safe = "".join(c for c in (session_id or "") if c.isalnum() or c in "-_")
    return os.path.join(home, f"{safe or 'unknown'}.json")


def remember_blocked(assistant, session_id, home=DELTA_HOME):
    """Record the message this gate just blocked. NEVER raises: failing to
    remember costs one comparison, and raising would cost the session."""
    try:
        os.makedirs(home, exist_ok=True)
        with open(_delta_path(session_id, home), "w") as fh:
            json.dump({"sentences": _sentences(assistant), "spent": False}, fh)
    except Exception as exc:                                  # noqa: BLE001
        dlog(f"delta: could not remember blocked message ({exc})")


def repeats_blocked(assistant, session_id, home=DELTA_HOME):
    """(is_repeat, overlap) for a resend against the remembered blocked message.

    Reading is also SPENDING: the record is marked used before this returns, so
    the answer can be True at most once per blocked message. Fails OPEN — a
    missing, empty or corrupt record means no opinion, never a block."""
    try:
        path = _delta_path(session_id, home)
        with open(path) as fh:
            record = json.load(fh)
        if record.get("spent"):
            return False, 0.0
        before = set(record.get("sentences") or [])
        now_lines = _sentences(assistant)
        if not before or len(now_lines) < MIN_SENTENCES:
            return False, 0.0
        already_read = sum(1 for s in now_lines if s in before)
        overlap = already_read / len(now_lines)
        if overlap < REPEAT_SHARE:
            return False, overlap
        record["spent"] = True
        with open(path, "w") as fh:
            json.dump(record, fh)
        return True, overlap
    except Exception:                                         # noqa: BLE001
        return False, 0.0


DELTA_REASON = (
    "SEND THE DELTA, NOT THE MESSAGE AGAIN (rule 1d50a3bb).\n\n"
    "{pct}% of this reply is text Joe has already read. A gate reopened this "
    "turn to ask for ONE missing thing; supply exactly that, plus at most a "
    "one-line pointer to what was already said.\n\n"
    "His words, 2026-08-13: \"why did you just say the same thing twice in a "
    "row? I feel like you're just wasting my tokens on purpose.\" He had read a "
    "40-line report, and got the same 40 lines back with a fix on top. "
    "Everything he had already read was pure cost.\n\n"
    "THE TEST: which sentences here has he already read? Cut those. If the "
    "answer is \"all of them except four lines\", send four lines.\n\n"
    "This check speaks once per turn and will not stop you again."
)


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


def is_harness_injected(rec, text):
    """System-reminders, task notifications and tool results are NOT the
    partner's keystrokes. ledger-sweep.py learned this the hard way on
    2026-08-05/06 when it quoted a task-notification block as 'his words'."""
    origin = (rec.get("origin") or {})
    if isinstance(origin, dict) and origin.get("kind") not in (None, "", "user", "keyboard"):
        return True
    if rec.get("isMeta") or rec.get("isCompactSummary"):
        return True
    if not text:
        return True
    t = text.lstrip()
    for marker in ("<system-reminder>", "<task-notification>", "[SYSTEM NOTIFICATION",
                   "<local-command", "<command-name>", "Caveat:", "<user-prompt-submit-hook>"):
        if t.startswith(marker):
            return True
    return False


def strip_noise(text):
    """Remove fenced code and quoted blocks before scanning PROSE patterns, so
    a bash snippet the session legitimately shows inside a quoted rule, or a
    question inside a quoted email draft, does not trip the offload class."""
    text = re.sub(r"```.*?```", " ", text, flags=re.S)
    text = re.sub(r"^\s*>.*$", " ", text, flags=re.M)
    return text


def scan(assistant, human_last, denied=()):
    """Return (fired, findings). findings = list of (klass, name).

    Command handoffs are found by the fence and HANDOFF_PROSE patterns only.
    Prose action cues those patterns miss produce a nonblocking review residual;
    no observed capability or permission evidence exists here to decide it.
    """
    findings = []
    prose = strip_noise(assistant)

    human_ok_cmd = bool(human_last and HUMAN_WANTS_COMMAND.search(human_last))
    human_ok_choice = bool(human_last and HUMAN_WANTS_CHOICE.search(human_last))
    protected = bool(PROTECTED.search(prose))

    # (2) COMMAND HANDOFF — exempt if the human asked to see a command, or if
    # the harness refused this session permission to run this very command.
    if denied and handoff_was_denied(assistant, denied):
        human_ok_cmd = True

    if not human_ok_cmd:
        if FENCE.search(assistant) or BARE_FENCE_CMD.search(assistant):
            findings.append(("command_handoff", "shell_fence"))
        for name, pat in HANDOFF_PROSE:
            if pat.search(prose):
                findings.append(("command_handoff", name))

    # (1)+(3) OFFLOAD — exempt if the human asked for a choice, or if the
    # decision is genuinely a protected class that belongs to Joe by rule.
    if not human_ok_choice and not protected:
        for name, pat in OFFLOAD:
            if pat.search(prose):
                findings.append(("decision_offload", name))
        for name, pat in SOFT_WAIT:
            if pat.search(prose):
                findings.append(("soft_wait", name))

    # (4) BARE ID — scanned on PROSE ONLY, so fenced code, quoted tool output
    # and command examples never trip it. Joe asked for this after being handed
    # "A17" with no idea what it was.
    for name, ident in bare_id_hits(prose):
        findings.append(("bare_id", f"{name}:{ident}"))

    if not any(k == "command_handoff" for k, _ in findings) and handoff_needs_review(prose, human_last, denied):
        findings.append(("handoff_review", "needs_review"))
    return any(k != "handoff_review" for k, _ in findings), findings


def _shadow_mode_enabled():
    """Read the SHADOW_GATE_KEY's mode from ops/config/gate-lifecycle.json.

    FAILS CLOSED TO "DO NOTHING", the opposite direction from every blocking
    check in this file. Every other fail-open in this module means "let the
    turn end" because a wedged session is the worse failure; here the worse
    failure would be a missing or corrupt config silently turning ON a scan
    every turn pays for. So unreadable, missing, or malformed config all mean
    the same thing: the shadow check does not run this turn, same as if the
    entry did not exist. Never raises.
    """
    try:
        with open(GATE_LIFECYCLE_PATH) as fh:
            data = json.load(fh)
        entry = (data.get("gates") or {}).get(SHADOW_GATE_KEY) or {}
        return entry.get("mode") == "shadow"
    except Exception:
        return False


def _writing_shadow_findings(assistant):
    """[(rule_id, quote)] for 5be2f462's vocab / contrast-reframe /
    contrast-reframe-split constructions, on the SAME final assistant message
    this gate already scans.

    Imports chat-lint-gate.py's own writing_rules()/strip_fences() through
    lib.loadpy (the same route loose-work-gate.py uses for delegation-gate.py
    and ledger-sweep.py) rather than a second copy of writing-lint.py's RULES
    table — one detector, two moments (rule a8c55a47). A rule added to that
    table reaches both surfaces with nobody editing this file.
    """
    try:
        from lib.loadpy import load_module_from_path
        chat_lint = load_module_from_path(
            "_csg_chat_lint", os.path.join(HOOKS, "chat-lint-gate.py"))
    except Exception as exc:
        dlog(f"shadow-writing ALLOW(import-failed) {exc}")
        return []
    try:
        prose = chat_lint.strip_fences(assistant)
        rules, mask = chat_lint.writing_rules()
        masked = mask(prose)
        found = []
        for rid, pat, what, fix in rules:
            m = re.search(pat, masked, re.I)
            if m:
                quote = prose[max(0, m.start() - 20):m.end() + 20].replace("\n", " ")
                found.append((rid, quote.strip()[:90]))
        return found
    except Exception as exc:
        dlog(f"shadow-writing ALLOW(scan-failed) {exc}")
        return []


def shadow_writing_check(assistant, session):
    """Log-only pre-send measurement for rule 5be2f462, born SHADOW this slice
    (WR-000019 S8). NEVER contributes to `classes`/`findings` and NEVER blocks
    — it exists so Joe can see the false-positive rate before promoting it
    from ops/config/gate-lifecycle.json, the same lifecycle every shadow gate
    in that file follows. Writes to the DEDICATED out/conduct-gate-shadow.jsonl,
    never to out/conduct-gate.jsonl (see module docstring for why mixing them
    would be wrong). Fixtures (session 'selftest') are skipped, same convention
    audit() uses elsewhere in this file — the selftest for this feature
    redirects CARR_GATE_LIFECYCLE_PATH/CARR_CONDUCT_SHADOW_LOG to its own
    files and uses a distinct session id instead of relying on this skip.
    """
    if session == "selftest":
        return
    if not _shadow_mode_enabled():
        return
    try:
        findings = _writing_shadow_findings(assistant)
        if not findings:
            return
        os.makedirs(os.path.dirname(SHADOW_LOG), exist_ok=True)
        with open(SHADOW_LOG, "a") as fh:
            fh.write(json.dumps({
                "ts": now(),
                "hook": "conduct-stop-gate-shadow",
                "rule": "5be2f462",
                "classes": sorted({f[0] for f in findings}),
                "session": session,
                "excerpt": findings[0][1][:200],
                "would_have_blocked": True,
            }) + "\n")
    except Exception as exc:
        dlog(f"shadow-writing ALLOW(log-failed) {exc}")


REASON_HEAD = (
    "CONDUCT GATE — this turn is not finished. The session is being held open, "
    "not punished; fix the turn and it will close.\n\n"
)

REMEDY = {
    "command_handoff": (
        "COMMAND HANDOFF (rule e313a3ca). You put a shell command in front of Joe "
        "instead of running it. This machine is defaultMode:auto with ~74 allowed "
        "Bash patterns; the deny list is only sudo, diskutil, dd. RUN IT NOW with "
        "the Bash tool and report the result in one line. If it is genuinely "
        "denied, needs a credential, a browser login or Face ID, say THAT and file "
        "the blocker — but do not hand him the command. His words: \"I can't run "
        "commands every 5 minutes.\""
    ),
    "decision_offload": (
        "DECISION OFFLOAD (rules 14e0408b + aa411351). You asked Joe to decide "
        "something internal. The gate is drawn by AUDIENCE, not difficulty: only "
        "client-facing, public-facing, money and irreversible calls are his. "
        "Everything internal — schema, records, renders, jobs, config, rules, "
        "refactors, procedure — is YOURS to decide and report. If you are not "
        "confident, the required move is to RESEARCH until you are: read the code, "
        "run the query, test the surface, convene the council. Decide it, do it, "
        "then tell him in one line what you did and why."
    ),
    "bare_id": (
        "BARE ID. You put an identifier in front of Joe with nothing saying what "
        "it is. His words: \"you name A17 and i have no idea what it even is. its "
        "unnecesarily confusing and vague.\" The ids exist so a rule can be "
        "reworded without breaking references — a machine's reason, not his. "
        "SAY WHAT THE THING DOES, in ordinary language. Not \"per rule aa411351\" "
        "but \"the rule that says Joe decides client-facing calls and the system "
        "decides internal ones\". Not \"A17 is open\" but \"Dell still has to "
        "acknowledge the doctrine store is live\". If he genuinely needs the id "
        "to run a verb, give the plain name FIRST and the id in parentheses "
        "after — never the id alone, never the id first."
    ),
    "soft_wait": (
        "SOFT WAIT (rules 14e0408b + aa411351). You parked the turn on Joe without "
        "asking a literal question. Waiting on him IS asking him. Same test: if "
        "the call is internal, make it and report it. If it is genuinely "
        "client-facing, public-facing, money or irreversible, say so plainly and "
        "name the one thing you need — do not leave it as an open-ended hold."
    ),
}


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception as exc:
        dlog(f"ALLOW(parse-error) {exc}")
        sys.exit(0)

    try:
        # NEVER loop on the CONDUCT classes. If we already blocked once this
        # turn, those let it through — unchanged, and the reason is unchanged:
        # a wedged session is worse than one offload reaching Joe.
        #
        # The delta check is the one thing that runs here instead, because a
        # resend is the only moment it can possibly apply, and it is bounded to
        # speak once per blocked message by repeats_blocked() marking the
        # record spent as it reads it (rule 1d50a3bb).
        if payload.get("stop_hook_active"):
            path = payload.get("transcript_path")
            if not path or not os.path.exists(path):
                sys.exit(0)
            recs = read_tail(path)
            resend = ""
            for r in recs:
                t = text_of(r, ("assistant",))
                if t and t.strip():
                    resend = t.strip()
            session_id = payload.get("session_id")
            repeat, overlap = repeats_blocked(resend, session_id)
            if repeat:
                audit({
                    "ts": now(),
                    "hook": "conduct-stop-gate",
                    "classes": ["repeated_message"],
                    "patterns": [f"delta:overlap={overlap:.2f}"],
                    "session": session_id,
                    "excerpt": " ".join(resend.split())[:400],
                })
                dlog(f"BLOCK delta :: overlap={overlap:.2f}")
                print(json.dumps({"decision": "block",
                                  "reason": DELTA_REASON.format(pct=round(overlap * 100))}))
            sys.exit(0)

        path = payload.get("transcript_path")
        if not path or not os.path.exists(path):
            dlog("ALLOW(no-transcript)")
            sys.exit(0)

        recs = read_tail(path)
        if not recs:
            sys.exit(0)

        # Last genuine human turn (for exemptions), and every assistant text
        # emitted after it (that is "this turn").
        last_human_idx, last_human = None, None
        for i in range(len(recs) - 1, -1, -1):
            t = text_of(recs[i], ("user", "human"))
            if t is None or is_harness_injected(recs[i], t):
                continue
            last_human_idx, last_human = i, t
            break

        # SCAN THE FINAL MESSAGE ONLY, not every message since the human spoke.
        # The first version joined them all, and the audit ledger showed why that
        # is wrong: once this gate blocks a turn, the harness-injected feedback is
        # correctly skipped as not-the-human, so the window keeps stretching back
        # across messages ALREADY DELIVERED AND ACCEPTED. An id or a command
        # mentioned several messages ago then re-fires on every later turn and the
        # session can never close. Measured 2026-08-09: one fire listed A11-A17
        # and four hex ids that appeared nowhere in the closing prose — they came
        # from verb-call arguments and from older messages Joe had already read.
        #
        # Only assistant TEXT blocks count; text_of() excludes tool_use inputs on
        # purpose. A verb call legitimately carries raw ids (update-loop needs
        # "A15") — that is machine-to-machine, and this rule is about what
        # reaches Joe's eyes, not what crosses the wire.
        start = (last_human_idx + 1) if last_human_idx is not None else 0
        assistant = ""
        for r in recs[start:]:
            t = text_of(r, ("assistant",))
            if t and t.strip():
                assistant = t.strip()   # keep overwriting; the last one wins
        if not assistant:
            sys.exit(0)

        fired, findings = scan(assistant, last_human, denied_commands(recs, start))

        # WR-000019 S8: the writing shadow check runs regardless of whether
        # any OTHER conduct class fired — it is measuring its own catch rate
        # independently, and it never influences `fired` either way.
        shadow_writing_check(assistant, payload.get("session_id"))

        review_advisory = ("handoff_review", "needs_review") in findings
        if review_advisory:
            audit({"ts": now(), "hook": "conduct-stop-gate", "classes": ["handoff_review"],
                   "patterns": ["needs_review"], "session": payload.get("session_id")})
            if not fired:
                print(json.dumps({"systemMessage": HANDOFF_REVIEW_MESSAGE}))
            findings = [(k, n) for k, n in findings if k != "handoff_review"]
        if not fired:
            sys.exit(0)

        classes = []
        for k, _ in findings:
            if k not in classes:
                classes.append(k)

        audit({
            "ts": now(),
            "hook": "conduct-stop-gate",
            "classes": classes,
            "patterns": [f"{k}:{n}" for k, n in findings],
            "session": payload.get("session_id"),
            "excerpt": " ".join(assistant.split())[-400:],
        })

        body = REASON_HEAD + "\n\n".join(REMEDY[c] for c in classes if c in REMEDY)
        body += (
            "\n\nDO THIS NOW, in this same turn: carry out the work you were about "
            "to hand over, then send ONLY THE DELTA — the result of that work, plus "
            "at most a one-line pointer to what you already said. Rule 1d50a3bb "
            "binds here: Joe has ALREADY READ the blocked message, so restating it "
            "with the fix folded in charges him twice for the same text, which is "
            "the exact thing he taught against on 2026-08-13. Do not re-explain "
            "this gate to Joe and do not apologise — just deliver the finished "
            "result.\n\n"
            "This gate exists because rules 14e0408b, e313a3ca and 179be4b8 were "
            "all ACTIVE and all recited at the start of this session, and were "
            "violated anyway. Prose does not bind; this does."
        )

        # Remember what is being blocked, so the resend can be checked against
        # it for repetition (rule 1d50a3bb). Never raises; a memory that fails
        # to write costs one comparison, not the session.
        remember_blocked(assistant, payload.get("session_id"))

        dlog(f"BLOCK {classes} :: {[n for _, n in findings]}")
        response = {"decision": "block", "reason": body}
        if review_advisory:
            response["systemMessage"] = HANDOFF_REVIEW_MESSAGE
        print(json.dumps(response))
        sys.exit(0)

    except Exception as exc:
        dlog(f"ALLOW(internal-error) {exc}")
        sys.exit(0)


if __name__ == "__main__":
    main()
