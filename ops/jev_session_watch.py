"""jev_session_watch.py — DURING-the-task supervision checks for a coding agent.

Open loop #629, checks #8 (stuck/drift), #9 (runaway thinking), #16 (bug locator), #19 (already exists), #20
(wrong path/name repair), #21 (test picker), #22 (failure triage). A Claude
Code hook dispatcher calls these on PostToolUse (and on Stop, for the
transcript helper), passing tool_name / tool_input / tool_response / the
transcript path — never the transcript itself, which can be many MB.

THE SHAPE, same one every check here follows. A cheap deterministic pattern
over the evidence decides WHETHER to ask Jev at all — a hook fires on every
tool call, and a round trip is 0.5-2s, so the trigger is the whole reason this
is affordable to run inline. Only when the trigger fires does a check build
ONE request (independent questions about the same tool result together) and
hand it to ops/jev_judge.py — this module
never talks to typesafe_client.ask() directly and never opens a socket. Jev is
asked to judge, never to count, sort, or diff; every number a trigger needs
(a repeat count, a byte length, an edit distance) is computed in code first
and handed to the judgment as a named fact.

SHADOW FIRST. Every check calls jev_judge.record() beside its verdict and
NEVER blocks anything: the return value is a plain dict a caller reads and
decides what, if anything, to do about. A caller that treats `escalate: True`
as "stop the session" has built something this module does not claim to be.

THRESHOLDS ARE PROVISIONAL. Every *_AT constant below is a starting guess
following ops/jev_judge.py's own warning that the generic 0.6 default does not
transfer: replace these once out/jev-judge.jsonl has real supervise.* rows to
measure against, per check, because the cost of a wrong "you look stuck" is
nothing like the cost of a wrong "this line is the bug".

IT IS A LIBRARY AND MUST STAY ONE. No shebang and no main guard: either turns
a .py file into a registered script entrypoint in the sealed source inventory,
moves the frontier, and owes a forward-only registry successor. The detector
is a regex over the whole file with no notion of docstrings, so the construct
is described here and never spelled. ops/typesafe_client.py carries the long
form of why. ops/jev-session-watch-selftest.py is exempt by its own name.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# --- transcript reading: bounded, never a full parse of a multi-MB file ----

TAIL_BYTES = 4 * 1024 * 1024
TAIL_EVENTS = 400


def _module(name):
    path = os.path.join(REPO, "ops", name + ".py")
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load " + path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _client():
    return _module("typesafe_client")


def _judge():
    return _module("jev_judge")


def _result(check_id, verdict, confidence, escalate, detail):
    return {"check": check_id, "verdict": verdict, "confidence": confidence,
            "escalate": escalate, "detail": detail}


def _record(jj, check_id, subject_ref, answer, existing_decision, *, error=None,
            log_path=None):
    """jev_judge.record(), never allowed to raise into a caller of this module."""
    kwargs = {} if log_path is None else {"log_path": log_path}
    try:
        jj.record("supervise." + check_id, subject_ref, answer, existing_decision,
                  error=error, **kwargs)
    except Exception:          # the shadow log must never break a check
        pass


def _noul_value(answer, key):
    import math
    value = float(answer["answers"][key]["noul"])
    if not math.isfinite(value) or not 0.0 <= value <= 1.0:
        raise ValueError(f"{key}: invalid noul probability")
    return value


def _choice_value(answer, key):
    import math
    body = answer["answers"][key]
    confidence = body.get("confidence")
    if confidence is not None:
        confidence = float(confidence)
        if not math.isfinite(confidence) or not 0.0 <= confidence <= 1.0:
            raise ValueError(f"{key}: invalid choice confidence")
    return body.get("choice"), confidence


def _tail_lines(path, tail_bytes=TAIL_BYTES):
    try:
        with open(path, "rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - tail_bytes))
            data = handle.read()
    except OSError:
        return []
    text = data.decode("utf-8", errors="replace")
    lines = text.splitlines()
    # A byte-offset seek can land mid-line; that first fragment is not a whole
    # row and json.loads on it is expected to fail, so drop it rather than let
    # it silently pollute a repeat count.
    if size > tail_bytes and lines:
        lines = lines[1:]
    return lines


def _tail_events(transcript_path, max_events=TAIL_EVENTS, tail_bytes=TAIL_BYTES):
    """The last `max_events` well-formed JSONL rows of a transcript.

    Reads only the last `tail_bytes` bytes, so a multi-megabyte transcript
    costs a bounded seek instead of a full parse. Malformed lines, and rows
    that are not JSON objects, are skipped. Returns [] for a missing, empty,
    or unreadable path — never raises, because a check that cannot read its
    own evidence reports "nothing to trigger on", not a crash.
    """
    if not isinstance(transcript_path, str) or not transcript_path:
        return []
    events = []
    for line in _tail_lines(transcript_path, tail_bytes):
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            events.append(row)
    return events[-max_events:]


def _content_blocks(event):
    message = event.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    if isinstance(content, list):
        return [b for b in content if isinstance(b, dict)]
    return []


def _result_text(block):
    content = block.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(part.get("text", "") for part in content
                         if isinstance(part, dict) and part.get("type") == "text")
    return None


def normalize_input(value):
    """A stable string for 'the same tool call', independent of key order."""
    try:
        return json.dumps(value, sort_keys=True, default=str)
    except TypeError:
        return str(value)


def tool_calls(events):
    """Every tool_use block in `events`, oldest first, paired with its result.

    Each entry is {"name", "input", "id", "result", "is_error"}; `result` is the matching
    tool_result's text, or None when no result has arrived yet (the call is
    still in flight, or the transcript tail cut it off).
    """
    results = {}
    for event in events:
        if event.get("type") != "user":
            continue
        for block in _content_blocks(event):
            if block.get("type") == "tool_result":
                results[block.get("tool_use_id")] = (_result_text(block), bool(block.get("is_error")))
    calls = []
    for event in events:
        if event.get("type") != "assistant":
            continue
        for block in _content_blocks(event):
            if block.get("type") == "tool_use":
                calls.append({"name": block.get("name"), "input": block.get("input"),
                              "id": block.get("id"),
                              "result": results.get(block.get("id"), (None, False))[0],
                              "is_error": results.get(block.get("id"), (None, False))[1]})
    return calls


def last_assistant_text(transcript_path):
    """The text blocks of the FINAL assistant message, joined. "" if none.

    Tail-read like everything else here. This is the "what did the agent just
    say" read the Stop-hook dispatcher (hooks/jev-supervisor.py) uses; it never
    raises, so a Stop hook that calls it always has something to act on.
    """
    events = _tail_events(transcript_path)
    for event in reversed(events):
        if event.get("type") != "assistant":
            continue
        texts = [b.get("text") for b in _content_blocks(event)
                if b.get("type") == "text" and isinstance(b.get("text"), str)]
        return "\n".join(texts)
    return ""


# =====================================================================
# #8 — stuck and drift watch
# =====================================================================
#
# Repeated completed calls/failures and elapsed budgets are local predicates.
# Missing artifact progress without those signatures needs semantic review.

LOOP_WINDOW = 12
LOOP_REPEAT_MIN = 3
STALE_EDIT_CALLS = 25
EDIT_TOOL_NAMES = {"edit", "write", "notebookedit"}

# Signals are emitted once per pattern and edit. Turn count alone does not
# create new evidence.
STALE_STATE_DIR = os.path.join(REPO, "out", "jev-session-watch-state")
FAILURE_MARKERS = re.compile(
    r"Traceback \(most recent call last\)|AssertionError|FAILED |ERROR\b|Error:")

# Provisional; see the module docstring.




def _repeated_failures(calls):
    counts = {}
    for call in calls[-LOOP_WINDOW:]:
        text = call.get("result")
        if isinstance(text, str) and FAILURE_MARKERS.search(text):
            key = text.strip()[:500]
            counts[key] = counts.get(key, 0) + 1
    repeated = [(key, n) for key, n in counts.items() if n >= 2]
    repeated.sort(key=lambda item: -item[1])
    return repeated


def _successful_edit(call):
    return ((call.get("name") or "").lower() in EDIT_TOOL_NAMES
            and call.get("result") is not None and not call.get("is_error")
            and not FAILURE_MARKERS.search(call["result"]))


def _calls_since_last_edit(calls):
    for i, call in enumerate(reversed(calls)):
        if _successful_edit(call):
            return i
    return len(calls)


def _last_edit_id(calls):
    for call in reversed(calls):
        if _successful_edit(call):
            return call.get("id") or "edit-without-id"
    return None




def _event_ask_due(transcript_path, signature, *, state_dir=None):
    import hashlib
    folder = state_dir or STALE_STATE_DIR
    key = hashlib.sha256(os.path.abspath(transcript_path).encode()).hexdigest()[:32]
    path = os.path.join(folder, key + ".event.json")
    try:
        with open(path, encoding="utf-8") as fh:
            prior = json.load(fh)
    except (OSError, ValueError):
        prior = {}
    if prior.get("signature") == signature:
        return False
    try:
        os.makedirs(folder, exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"signature": signature}, fh)
        os.replace(tmp, path)
    except OSError:
        pass
    return True


def watch_progress(transcript_path, task_text, *, client=None, log_path=None, state_dir=None,
                   elapsed_seconds=None, budget_seconds=None, artifact_before=None, artifact_after=None):
    """Count repeated completed calls, unchanged artifacts and an elapsed budget.

    Repetition with changing output is progress. An edit or observed artifact
    delta resets the watch; absence of edits alone is a review signal, not drift.
    """
    check_id = "stuck_and_drift"
    calls = tool_calls(_tail_events(transcript_path))
    if artifact_before is not None and artifact_after is not None and artifact_before != artifact_after:
        return _result(check_id, "ok", None, False, {"artifact_delta": True})
    unchanged = artifact_before is not None and artifact_after is not None and artifact_before == artifact_after
    stale = len(calls) if unchanged else _calls_since_last_edit(calls)
    # Cut the call sequence at the edit before dropping pending calls, so a
    # call still awaiting its result never pulls a pre-edit result forward.
    window = [c for c in calls[len(calls) - stale:] if c.get("result") is not None][-LOOP_WINDOW:]
    counts = {}
    for call in window:
        key = (call["name"], normalize_input(call["input"]), call["result"])
        counts[key] = counts.get(key, 0) + 1
    repeated = sorted(((key,n) for key,n in counts.items() if n >= LOOP_REPEAT_MIN),key=lambda x:-x[1])
    failure = _repeated_failures(window)
    trigger = "repeated_tool_call" if repeated else "repeated_failure_output" if failure else None
    if budget_seconds is not None and elapsed_seconds is not None and elapsed_seconds >= budget_seconds:
        trigger = "elapsed_budget"
    verdict = "stuck" if trigger else "ok"
    if not trigger and stale >= STALE_EDIT_CALLS:
        trigger, verdict = "no_artifact_delta_observed", "needs_review"
    if not trigger:
        return _result(check_id,"ok",None,False,{"tool_calls_seen":len(calls)})
    signature = json.dumps([trigger,repeated,failure,_last_edit_id(calls),task_text,artifact_after],sort_keys=True)
    if not _event_ask_due(transcript_path,signature,state_dir=state_dir):
        return _result(check_id,"ok",None,False,{"repeated_already_asked":True})
    return _result(check_id,verdict,None,verdict == "needs_review",{
        "trigger":trigger,"calls_since_last_file_edit":stale,"tool_calls_seen":len(calls),
        "elapsed_seconds":elapsed_seconds,"budget_seconds":budget_seconds,
        "advice":"Repeated work without observed progress: inspect the evidence and choose the next action."
                 if verdict == "stuck" else "No artifact delta observed; needs review."})


# =====================================================================
# #9 — runaway-thinking cutoff
# =====================================================================

THINKING_CHAR_LIMIT = 6000
THINKING_RATIO = 4
THINKING_WINDOW_TURNS = 5



def _assistant_turns(events, limit=THINKING_WINDOW_TURNS):
    turns = [e for e in events if e.get("type") == "assistant"]
    return turns[-limit:]


def _turn_chars(turn):
    """(thinking_chars, action_chars, thinking_text) for one assistant turn."""
    thinking_chars = 0
    action_chars = 0
    thinking_parts = []
    for block in _content_blocks(turn):
        btype = block.get("type")
        if btype == "thinking":
            text = block.get("thinking")
            if not isinstance(text, str):
                text = block.get("text") if isinstance(block.get("text"), str) else ""
            thinking_chars += len(text)
            thinking_parts.append(text)
        elif btype == "text" and isinstance(block.get("text"), str):
            action_chars += len(block["text"])
        elif btype == "tool_use":
            action_chars += len(normalize_input(block.get("input")))
    return thinking_chars, action_chars, "\n".join(thinking_parts)


def check_thinking(transcript_path, *, client=None, log_path=None):
    """A reasoning budget is countable; whether reasoning circles needs review."""
    turns = _assistant_turns(_tail_events(transcript_path))
    stats = [_turn_chars(t) for t in turns]
    total = sum(t for t,a,x in stats)
    action = sum(a for t,a,x in stats)
    exceeded = bool(stats and (stats[-1][0] > THINKING_CHAR_LIMIT or
                    total > max(THINKING_CHAR_LIMIT, THINKING_RATIO * action)))
    return _result("runaway_thinking","needs_review" if exceeded else "ok",None,exceeded,
                   {"total_thinking_chars":total,"total_action_chars":action,
                    "advice":"Reasoning budget exceeded; needs review." if exceeded else None})


# =====================================================================
# #16 — bug locator
# =====================================================================

TRACEBACK_MARKERS = re.compile(
    r"Traceback \(most recent call last\)|AssertionError|FAILED |ERROR\b|Error:")
LINE_REF = re.compile(r"line (\d+)|:(\d+):")
MAX_SOURCE_LINES = 250
LINE_WINDOW = 40
OPTION_CHARS = 160
NONE_OF_THESE_LINE = "none of these lines"

BUG_LOCATE_MIN_CONFIDENCE = 0.35


def _numbered_lines(source_text):
    return list(enumerate((source_text or "").splitlines(), start=1))


def _mentioned_lines(failure_output, max_line):
    nums = set()
    for m in LINE_REF.finditer(failure_output or ""):
        for group in m.groups():
            if group and 1 <= int(group) <= max_line:
                nums.add(int(group))
    return nums


def locate_bug(source_text, path, failure_output, *, client=None, log_path=None):
    """Which numbered line of `source_text` most likely caused `failure_output`.

    Trigger: `failure_output` actually looks like a traceback/assertion/test
    failure (checked here, not assumed from the caller). All lines are
    numbered; when the file is over 250 lines, only a window around the lines
    the traceback mentions is kept. One Choice over the (windowed) lines, each
    option truncated, plus "none of these lines". Verdict "line_located" or
    "none"; never raises.
    """
    check_id = "bug_locator"
    if not TRACEBACK_MARKERS.search(failure_output or ""):
        return _result(check_id, "not_a_failure", None, False, {"trigger": None})

    all_lines = _numbered_lines(source_text)
    if not all_lines:
        return _result(check_id, "no_source", None, False, {"trigger": "traceback_seen"})

    mentioned = sorted(_mentioned_lines(failure_output, len(all_lines)))
    if len(all_lines) > MAX_SOURCE_LINES and mentioned:
        keep = set()
        for n in mentioned:
            keep.update(range(max(1, n - LINE_WINDOW), min(len(all_lines), n + LINE_WINDOW) + 1))
        window_lines = [(n, t) for n, t in all_lines if n in keep]
    else:
        window_lines = all_lines[:MAX_SOURCE_LINES]

    line_text = {n: t for n, t in window_lines}
    options = {str(n): f"{n}: {t.strip()[:OPTION_CHARS]}" for n, t in window_lines if t.strip()}
    if not options:
        return _result(check_id, "no_source", None, False, {"trigger": "traceback_seen"})
    options[NONE_OF_THESE_LINE] = (
        "None of the numbered lines shown caused this failure — the cause is "
        "elsewhere, in a different file, or in the test's own expectation "
        "rather than in this code.")

    tsc = client or _client()
    subject = {
        "path": path,
        "failure_output": (failure_output or "")[:4000],
        "numbered_lines": "\n".join(f"{n}: {t}" for n, t in window_lines)[:12000],
        "mentioned_line_numbers": mentioned,
    }
    question = tsc.choice(
        "`failure_output` is a traceback or assertion from running a test "
        "against `path`. `numbered_lines` is that file, numbered. Which "
        "numbered line is most likely the actual CAUSE of the failure — the "
        "line whose fix would make the failure go away — rather than merely a "
        "line that happens to appear in the call stack?", options)

    jj = _judge()
    subject_ref = {"path": path, "trigger": "traceback_seen", "mentioned_lines": mentioned,
                   "window_lines": len(window_lines)}
    try:
        answer = _module("jev_semantic").ask(
            subject, {"culprit_line": question}, caller="jev_session_watch",
            version="bug-locator-v1", client=client, transport=jj.judge, retries=0)
    except Exception as exc:
        _record(jj, check_id, subject_ref, {}, None, error=exc, log_path=log_path)
        return _result(check_id, "unavailable", None, True, subject_ref)

    chosen, confidence = _choice_value(answer, "culprit_line")
    escalate = confidence is None or confidence < BUG_LOCATE_MIN_CONFIDENCE
    if chosen == NONE_OF_THESE_LINE or chosen is None:
        verdict = "none"
        detail = dict(subject_ref, chosen_line=None, advice=None)
    else:
        verdict = "line_located"
        try:
            chosen_line = int(chosen)
        except (TypeError, ValueError):
            chosen_line = None
        snippet = line_text.get(chosen_line, "").strip()[:OPTION_CHARS]
        advice = f"line {chosen_line} `{snippet}` looks like the likely cause — check it first."
        detail = dict(subject_ref, chosen_line=chosen_line, advice=advice)
    _record(jj, check_id, subject_ref, answer, None, log_path=log_path)
    return _result(check_id, verdict, confidence, escalate, detail)


# =====================================================================
# #19 — already-exists
# =====================================================================

NEW_FUNC_SHORTLIST = 20

_TOKEN = re.compile(r"[A-Za-z][a-z0-9]*")
_TOP_LEVEL_DEF = re.compile(r"\bdef\s+\w+\s*\(|\bfunction\s+\w+\s*\(")
_DEF_LINE = re.compile(r"(?:def|function)\s+(\w+)")


def _name_tokens(name):
    return {p.lower() for p in _TOKEN.findall(name or "") if len(p) >= 3}


def _repo_files(repo_root, runner=None):
    try:
        if runner is None:
            result = subprocess.run(["git", "ls-files"], capture_output=True, text=True,
                                    cwd=repo_root, timeout=30)
        else:
            result = runner(["git", "ls-files"])
    except Exception:
        return []
    if getattr(result, "returncode", 1) != 0:
        return []
    return (result.stdout or "").splitlines()


def _git_grep_candidates(tokens, repo_root, runner=None):
    if not tokens:
        return []
    pattern = "|".join(re.escape(t) for t in sorted(tokens))
    # git grep -E uses the host's regex engine. POSIX classes keep the
    # shortlist identical on macOS and Linux; \s and \w differ there.
    args = ["git", "grep", "-n", "-i", "-E",
            rf"(def|function)[[:space:]]+[[:alnum:]_]*({pattern})[[:alnum:]_]*"]
    try:
        result = runner(args) if runner else subprocess.run(
            args, capture_output=True, text=True, cwd=repo_root, timeout=30)
    except Exception:
        return []
    if getattr(result, "returncode", 1) not in (0, 1):
        return []
    out = result.stdout or ""
    candidates = []
    for line in out.splitlines():
        m = re.match(r"^([^:]+):(\d+):(.*)$", line)
        if not m:
            continue
        path, lineno, content = m.group(1), int(m.group(2)), m.group(3)
        name_m = _DEF_LINE.search(content)
        if not name_m:
            continue
        candidates.append({"path": path, "line": lineno, "name": name_m.group(1),
                           "signature": content.strip()[:200]})
    return candidates


def check_existing(new_function_name, new_function_body, repo_root, *, client=None,
                   log_path=None, runner=None):
    """Collect existing definitions; behavioral equivalence needs review."""
    if not _TOP_LEVEL_DEF.search(new_function_body or ""):
        return _result("already_exists","not_a_new_function",None,False,{"trigger":None})
    candidates = _git_grep_candidates(_name_tokens(new_function_name),repo_root,runner=runner)[:NEW_FUNC_SHORTLIST]
    return _result("already_exists","needs_review" if candidates else "no_candidates",None,bool(candidates),
                   {"candidates":candidates,"advice":"Review existing definitions before adding one." if candidates else None})


# =====================================================================
# #20 — wrong path/name repair
# =====================================================================

PATH_SHORTLIST = 20
NAME_SHORTLIST = 20


def _levenshtein(a, b):
    """Plain edit distance. No dependency; the corpora here are small."""
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        cur = [i] + [0] * len(b)
        for j, cb in enumerate(b, start=1):
            cost = 0 if ca == cb else 1
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost)
        prev = cur
    return prev[-1]


def repair_path(bad_path, repo_root, *, client=None, log_path=None, files=None):
    """Only exact/unique basename matches resolve; fuzzy candidates need review."""
    pool = sorted(set(files if files is not None else _repo_files(repo_root)))
    normalized = os.path.normpath(bad_path or "")
    if os.path.isabs(normalized):
        normalized = os.path.relpath(normalized, repo_root)
    exact = [p for p in pool if os.path.normpath(p) == normalized]
    same = [p for p in pool if os.path.basename(p) == os.path.basename(normalized)]
    candidates = sorted(pool,key=lambda p:(_levenshtein(normalized,p),p))[:PATH_SHORTLIST]
    chosen = exact[0] if exact else same[0] if len(same) == 1 else None
    verdict = "path_found" if chosen else "needs_review" if pool else "no_candidates"
    return _result("path_repair",verdict,None,verdict == "needs_review",{
        "bad_path":bad_path,"repaired_path":chosen,"candidates":same or candidates,
        "advice":f"check {chosen}" if chosen else "Path candidates need review." if pool else None})


def repair_name(bad_name, candidates, context, *, client=None, log_path=None):
    """A known name resolves exactly; spelling proximity does not prove intent."""
    pool = sorted(set(c for c in candidates or [] if isinstance(c,str) and c))
    shortlist = sorted(pool,key=lambda c:(_levenshtein(bad_name or "",c),c))[:NAME_SHORTLIST]
    chosen = bad_name if bad_name in pool else None
    verdict = "name_found" if chosen else "needs_review" if pool else "no_candidates"
    return _result("name_repair",verdict,None,verdict == "needs_review",{
        "repaired_name":chosen,"candidates":shortlist,"advice":"Name candidates need review." if pool and not chosen else None})


# =====================================================================
# #21 — test picker
# =====================================================================

# Covers both common conventions and this repository's own: a tests/
# directory, test_foo.py / foo_test.py, and CARR's own "-selftest.py" suffix
# (see ops/jev-*-selftest.py throughout this same package).
TEST_PATH_HINT = re.compile(
    r"(^|/)tests?(/|$)"
    r"|(^|/)(test_[^/]+|[^/]+[-_]test|[^/]+[-_]selftest)\.(py|js|ts|jsx|tsx)$")


def _module_stem(path):
    return os.path.splitext(os.path.basename(path or ""))[0]


def _dash_fold(text):
    """"-" and "_" compare equal: ops/jev_judge.py <-> ops/jev-judge-selftest.py."""
    return (text or "").replace("-", "_")


def _read_repo_file(repo_root, rel_path):
    try:
        with open(os.path.join(repo_root, rel_path), "r", encoding="utf-8",
                  errors="ignore") as handle:
            return handle.read()
    except OSError:
        return None


def _shortlist_tests(changed_paths, repo_root, files=None):
    all_files = files if files is not None else _repo_files(repo_root)
    test_files = [f for f in all_files if TEST_PATH_HINT.search(f)]
    stems = {_dash_fold(_module_stem(p)) for p in (changed_paths or []) if p}
    stems = {s for s in stems if s and s != "__init__"}
    if not stems:
        return []
    hits = []
    for tf in test_files:
        tf_base = _dash_fold(os.path.basename(tf))
        if any(stem in tf_base for stem in stems):
            hits.append(tf)
            continue
        text = _read_repo_file(repo_root, tf)
        if text and any(stem in _dash_fold(text) for stem in stems):
            hits.append(tf)
    return hits


def pick_tests(changed_paths, repo_root, *, max_tests=5, client=None, log_path=None, files=None):
    """Map changed module names and test references deterministically."""
    shortlist = sorted(set(_shortlist_tests(changed_paths,repo_root,files=files)))
    picked = shortlist[:max(0,max_tests)]
    return _result("test_picker","picked" if picked else "no_tests_found",None,False,{
        "shortlist":shortlist,"tests":picked,"advice":"run: " + ", ".join(picked) if picked else None,
        "overflow":shortlist[len(picked):]})


# =====================================================================
# #22 — failure-type triage
# =====================================================================



TRIAGE_HINTS = {
    "environment": "Check the environment first — dependencies, versions, missing "
                   "services, configuration — before assuming the code is wrong.",
    "code_bug": "Read the traceback and fix the code at the location it points to.",
    "flaky": "Re-run the command once before changing anything; only dig further "
             "if it fails again.",
    "permission": "Check file/directory permissions and ownership, or whether "
                  "the command needs access it does not have.",
    "none": "No single class fits confidently; read the output yourself before acting.",
}


def triage_failure(command, output, exit_code, *, client=None, log_path=None):
    """Classify exact exit/error signatures; novel causes need review."""
    if exit_code in (0,"0"):
        return _result("failure_triage","no_failure",None,False,{"trigger":None})
    text = str(output or "")
    if re.search(r"PermissionError|Permission denied|Operation not permitted|HTTP (?:401|403)\b|permission_denied",text,re.I):
        verdict = "permission"
    elif exit_code in (127,"127") or re.search(r"ModuleNotFoundError|ImportError|command not found|ECONNREFUSED|No such file or directory|ENOENT",text,re.I):
        verdict = "environment"
    elif re.search(r"AssertionError|SyntaxError|NameError|TypeError|ReferenceError",text):
        verdict = "code_bug"
    else:
        verdict = "needs_review"
    hint = TRIAGE_HINTS.get(verdict,"Failure has no recognized signature; needs review.")
    return _result("failure_triage",verdict,None,verdict == "needs_review",{
        "recovery_hint":hint,"advice":hint,"exit_code":exit_code})


# One tool result is one evidence boundary. The legacy per-family entry points
# above remain callable, but the hook uses this batch so independent judgments
# over the same result share one bounded request.
def inspect_tool_event(tool_name, tool_input, output, exit_code, task_text, repo_root,
                       transcript_path="", *, client=None, judge_module=None,
                       receipt_path=None):
    import hashlib
    from pathlib import Path

    name = (tool_name or "").lower()
    tool_input = tool_input if isinstance(tool_input, dict) else {}
    output = (output or "")[-12000:]
    command = str(tool_input.get("command") or "")[:2000]
    state = {"task": (task_text or "")[:2000], "tool": name,
             "output": output[:6000], "command": command}
    questions = {}
    triggers = []
    tsc = client or _client()
    results = []
    failure_due = name == "bash" and (exit_code not in (None, 0) or
                    bool(FAILURE_MARKERS.search(output)))
    if failure_due:
        triggers.append("failure")
        state["exit_code"] = exit_code
        results.append(triage_failure(command, output, exit_code))
        # A failed test is a code fact. Jev may suggest a cause but cannot
        # convert this into a passing CI result.
        if re.search(r"\b(pytest|selftest|npm (?:run )?test|node --test|go test|cargo test)\b", command):
            results.append(_result("ci_result", "failed", None, False,
                                   {"advice": "the test command failed; inspect its output"}))
    missing = bool(re.search(r"No such file or directory|does not exist|File not found|ENOENT", output, re.I))
    bad_path = str(tool_input.get("file_path") or "")
    if not bad_path and missing:
        found = re.findall(r"(?:\.{0,2}/)?[\w.\-]+(?:/[\w.\-]+)+", command)
        bad_path = found[0] if found else ""
    if missing and bad_path and name in {"read", "edit", "write", "multiedit", "bash"}:
        triggers.append("missing_path")
        results.append(repair_path(bad_path, repo_root))
    frame_re = re.compile(r'File "([^"]+)", line (\d+)|\(?(/[^\s():]+\.(?:m?js|ts)):(\d+):\d+\)?')
    frames = []
    if failure_due:
        for m in frame_re.finditer(output):
            path, line = (m.group(1), m.group(2)) if m.group(1) else (m.group(3), m.group(4))
            full = Path(path) if Path(path).is_absolute() else Path(repo_root) / path
            try:
                full.resolve().relative_to(Path(repo_root).resolve())
                source = full.read_text(errors="replace").splitlines()
                n = int(line)
                if 1 <= n <= len(source):
                    frames.append((path, n, source[n-1][:180]))
            except (OSError, ValueError):
                continue
        if frames:
            triggers.append("bug_location")
            state["frames"] = frames[-8:]
            questions["bug_frame"] = tsc.choice(
                "Which `frames` entry most directly identifies the code defect?",
                {**{f"frame_{i}": f"{p}:{n} {line}" for i, (p,n,line) in enumerate(frames[-8:])},
                 "none": "The output does not identify a reliable culprit."})
    added = str(tool_input.get("content") or tool_input.get("new_string") or "")
    if name == "multiedit":
        added = "\n".join(str(e.get("new_string") or "") for e in tool_input.get("edits") or [])
    if name in {"edit", "write", "multiedit"} and added:
        path = str(tool_input.get("file_path") or "")
        rel = os.path.relpath(path, repo_root) if path.startswith(repo_root) else path
        state["changed_path"] = rel[:300]
        functions = re.findall(r"^\s*(?:async\s+)?(?:def|function)\s+([A-Za-z_]\w*)\s*\(", added, re.M)
        if functions:
            # A replacement Edit naturally finds the function already present
            # at its own path. Only other definitions are duplicate candidates.
            candidates = [item for item in _git_grep_candidates(
                _name_tokens(functions[0]), repo_root)
                if not (item["path"] == rel and item["name"] == functions[0])][:6]
            if candidates:
                triggers.append("duplicate_function")
                state["new_function"] = functions[0]
                state["duplicate_candidates"] = candidates
                results.append(_result("duplicate_function", "needs_review", None, True,
                                       {"candidates": candidates, "advice": "Review existing definitions."}))
        tests = _shortlist_tests([rel], repo_root)[:5]
        if tests:
            triggers.append("test_selection")
            state["test_candidates"] = tests
            results.append(pick_tests([rel], repo_root))
        if re.search(r"(^|/)(tests?/|test[-_]|[^/]*[-_.]test\.|[^/]*selftest)", rel):
            results.append(_result("test_quality", "needs_review", None, True,
                                   {"advice": "Test behavioral coverage needs review."}))
    if not questions:
        return results
    subject_digest = hashlib.sha256(json.dumps(state, sort_keys=True, default=str).encode()).hexdigest()
    jj = judge_module or _judge()
    try:
        answer = _module("jev_semantic").ask(
            state, questions, caller="jev_session_watch", version="tool-result-v1",
            client=client, transport=jj.judge, retries=0)
        bodies = answer.get("answers") or {}
        if not all(k in bodies for k in questions):
            raise ValueError("missing typed boundary answer")
        for key, question in questions.items():
            if question["type"] == "noul":
                _noul_value(answer, key)
            else:
                chosen, _ = _choice_value(answer, key)
                if chosen not in question["criteria"]:
                    raise ValueError(f"{key}: choice outside offered options")
        status = "answered"
    except Exception as exc:
        answer, bodies, status = {}, {}, "unavailable"
        results.append(_result("boundary_judgment", "unavailable", None, True,
                               {"reason": getattr(exc, "reason", "inspection_error"),
                                "advice": "Jev boundary judgment unavailable; inspect this result manually"}))
    if status == "answered":
        if "bug_frame" in questions:
            chosen, confidence = _choice_value(answer, "bug_frame")
            if chosen != "none" and confidence is not None and confidence >= 0.35:
                path, n, _ = state["frames"][int(chosen.split("_")[1])]
                results.append(_result("bug_location", "line_located", confidence, False,
                                       {"advice": f"inspect {path}:{n}"}))
    receipt = {"schema": "jev-boundary-decision/v1", "family": "tool_result",
               "status": status, "state_sha256": subject_digest,
               "questions": sorted(questions), "triggers": sorted(set(triggers)),
               "model": answer.get("model"), "usage": answer.get("usage"),
               "outcomes": [{"check": r["check"], "verdict": r["verdict"]} for r in results]}
    try:
        target = receipt_path or os.path.join(REPO, "out", "jev-boundary-decisions.jsonl")
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(receipt, sort_keys=True) + "\n")
    except OSError:
        pass
    _record(jj, "tool_boundary", subject_digest[:16], answer, receipt["outcomes"])
    if not results:
        results.append(_result("tool_boundary", "clear", None, False, {}))
    return results
