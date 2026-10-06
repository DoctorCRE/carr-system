"""Requirements from explicit acceptance contracts; semantic clauses need review."""
import importlib.util
import os
import re

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

MAX_REQUIREMENTS = 12
MAX_REQUIREMENT_CHARS = 300


GREETING = re.compile(
    r"^(?:hi|hey|hello|yo|good\s+(?:morning|afternoon|evening)|thanks|thank\s+you|thx|cheers)\b", re.I)
FILLER = re.compile(
    r"^(?:ok(?:ay)?|great|cool|nice|perfect|sounds\s+good|lgtm|got\s+it|sure|yes|yep|no|nope|"
    r"awesome|alright|all\s+right|go(?:\s+ahead)?|continue|proceed)[\s.!,]*$", re.I)
REQUEST_QUESTION = re.compile(r"^(?:can|could|would|will)\s+you\b|^please\b", re.I)
BULLET = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+")
SENTENCE_END = re.compile(r"(?<=[.!?;])\s+(?=[A-Z0-9`\"'(])")
SYSTEM_TAG = re.compile(r"<(system-reminder|command-[a-z-]+|local-command-[a-z-]+)>.*?</\1>", re.S)
FENCE = re.compile(r"```.*?```", re.S)
# The same markers hooks/conduct-stop-gate.py is_harness_injected() refuses to
# read as the partner's words.
HARNESS_MARKERS = ("<system-reminder>", "<task-notification>", "[SYSTEM NOTIFICATION",
                   "<local-command", "<command-name>", "Caveat:", "<user-prompt-submit-hook>")


def _keep(sentence):
    text = sentence.strip().strip("-*• ").strip()
    if len(text.split()) < 3:
        return None
    if GREETING.match(text) and len(text.split()) <= 8:
        return None
    if re.match(r"^(?:thanks|thank\s+you)\b", text, re.I) or text.endswith(":"):
        return None
    if FILLER.match(text):
        return None
    if text.endswith("?") and not REQUEST_QUESTION.match(text):
        return None
    return text[:MAX_REQUIREMENT_CHARS]


def split_requirements(prompt):
    """Deterministic clause split of a human request. Crude by design; see top."""
    if not prompt:
        return []
    text = SYSTEM_TAG.sub(" ", prompt)
    text = FENCE.sub(" ", text)
    out = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if BULLET.match(line):
            pieces = [BULLET.sub("", line)]
        else:
            pieces = SENTENCE_END.split(line)
        for piece in pieces:
            kept = _keep(piece)
            if kept and kept not in out:
                out.append(kept)
    return out[:MAX_REQUIREMENTS]


def _content(rec):
    message = rec.get("message") if isinstance(rec.get("message"), dict) else {}
    return message.get("content", rec.get("content"))


def _human_text(rec):
    """The text of a human prompt record, or None for tool results and meta."""
    if rec.get("type") != "user" or rec.get("isMeta") or rec.get("isSidechain"):
        return None
    # Harness-injected turns are not the partner's keystrokes (the conduct gate's
    # is_harness_injected() rule). Once this check could reopen a turn, a
    # background-task notice's boilerplate "If this event is something the user
    # would act on now, send a PushNotification" was read as Joe's request and
    # held a turn open (2026-09-24, the first day it acted).
    origin = rec.get("origin") if isinstance(rec.get("origin"), dict) else {}
    if origin.get("kind") not in (None, "", "user", "keyboard", "human") or rec.get("isCompactSummary"):
        return None
    content = _content(rec)
    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        if any(isinstance(b, dict) and b.get("type") == "tool_result" for b in content):
            return None
        text = "\n".join(b.get("text", "") for b in content
                         if isinstance(b, dict) and b.get("type") == "text")
    else:
        return None
    if text.lstrip().startswith(HARNESS_MARKERS):
        return None
    stripped = SYSTEM_TAG.sub(" ", text).strip()
    return stripped or None


def last_request(recs):
    """The text of the last human prompt, or None."""
    for rec in reversed(recs):
        text = _human_text(rec)
        if text:
            return text
    return None


def _acceptance():
    spec = importlib.util.spec_from_file_location("acceptance_checks",os.path.join(REPO,"lib","acceptance_checks.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def check(payload, recs):
    """Evaluate the last human request's explicit contract; prose needs review.

    The contract's artifacts are read from the session's working directory.
    Nothing in the hook payload or the assistant's close counts as evidence.
    """
    prompt = last_request(recs or [])
    if not prompt:
        return None
    acceptance = _acceptance()
    criteria = acceptance.contract(prompt).get("criteria")
    if not criteria:
        requirements = split_requirements(prompt)
        if not requirements:
            return None
        return {"status":"needs_review","advisory":"Semantic requirement acceptance needs review.",
                "unmet":[],"needs_review":requirements}
    result = acceptance.evaluate(criteria,root=(payload or {}).get("cwd") or REPO)
    unmet = [{"index":i+1,"text":str(criteria[i].get("id",i)),"reason":row["reason"]}
             for i,row in enumerate(result["criteria"]) if row["status"] == "failed"]
    return {**result,"unmet":unmet,"advisory":"Acceptance needs review." if result["status"] == "needs_review" else None}
