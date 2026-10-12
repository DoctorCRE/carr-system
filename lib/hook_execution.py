"""In-process hook dispatch and optional telemetry. Verdicts pass through unchanged."""
import time as _time
_T0 = _time.monotonic() * 1000.0        # first statement: the meter's own clock

import io                               # noqa: E402 — all three are resident
import os                               # noqa: E402   before this file is read
import sys                              # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

try:
    sys.path.insert(0, os.path.join(REPO, "hooks"))
    import hook_meter                   # noqa: E402
except Exception:                       # recording is optional; the gate is not
    hook_meter = None                   # type: ignore[assignment]

# A gate may declare a stable class for a refusal by printing this line once.
# None do today, so the field stays null rather than being inferred from prose.
DENY_MARKER = "DENY-CLASS:"

STOP_EVENTS = ("Stop", "SubagentStop")

MAX_FIELD = 300
INVOCATION_REPO_ENV = "CARR_HOOK_INVOCATION_REPO"

def grok_session():
    # Grok owns its context lifecycle in every sandbox; effect guards still run.
    sys.path.insert(0, REPO)
    try:
        from hooks.grok_invocation import grok_session as probe
    except ImportError:
        return False
    return probe()

# Grok sessions have no CARR session lifecycle. Keep effect guards running;
# suppress only context delivery/state hooks imported through Claude settings.
GROK_CONTEXT_HOOKS = frozenset({
    "gate-integrity.py", "rule-boot-gate.py", "context-handoff-gate.py",
    "session-presence-hook.py", "rule-pack-preuse-reselection.py",
    "rule-pack-drift-gate.py", "chat-lint-carryover.py",
})


class Tee(io.TextIOBase):
    """Pass output through untouched while keeping a bounded copy to classify.

    The copy is how a decision carried in JSON on stdout (a Stop gate's
    {"decision":"block"}) gets counted without the meter re-deciding anything.
    The pass-through is the part that must not fail: the harness reads the real
    stream, so every write reaches it and a failure to record stays silent.
    """

    def __init__(self, real, cap=64 * 1024):
        self._real = real
        self._buf = []
        self._size = 0
        self._cap = cap

    def write(self, s):
        try:
            if self._size < self._cap:
                self._buf.append(s)
                self._size += len(s)
        except Exception:
            pass
        return self._real.write(s)

    def flush(self):
        return self._real.flush()

    def isatty(self):
        try:
            return self._real.isatty()
        except Exception:
            return False

    def fileno(self):
        return self._real.fileno()

    @property
    def captured(self):
        try:
            return "".join(self._buf)
        except Exception:
            return ""


def replacement_stdin(raw):
    """A re-readable stdin carrying `raw`, with a .buffer like the real one.

    The wrapper reads the payload so it can name the event; the gate then has to
    read the same bytes. A bare StringIO would break any gate reaching for
    sys.stdin.buffer, so the substitute is a real TextIOWrapper — and it is
    built with `io` here rather than in hook_meter, because a gate that receives
    an empty stdin reaches a DIFFERENT verdict (most fail open and allow). This
    one line is the difference between instrumentation and an outage.
    """
    return io.TextIOWrapper(io.BytesIO(raw), encoding="utf-8", errors="replace")


def _clip(value):
    if value is None:
        return None
    text = str(value)
    return text if len(text) <= MAX_FIELD else text[:MAX_FIELD] + "…"


def _scan_field(raw, key):
    """The string value of a top-level scalar key, without parsing the payload.

    A Write payload carries the entire file being written and a Stop payload can
    reference a large transcript; json.loads on those costs real time to
    retrieve three short strings that are always plain, escape-free scalars (an
    event name, a tool name, a uuid). This finds them with str.find, and returns
    None the moment anything is unusual so the caller falls back to a real parse
    — the fast path is an optimisation, never a second parser whose
    disagreements with json would go unnoticed.
    """
    try:
        needle = b'"' + key + b'":'
        idx = raw.find(needle)
        if idx < 0:
            needle = b'"' + key + b'": '
            idx = raw.find(needle)
            if idx < 0:
                return None
        rest = raw[idx + len(needle):idx + len(needle) + 128].lstrip()
        if not rest.startswith(b'"'):
            return None
        end = rest.find(b'"', 1)
        if end < 0:
            return None
        value = rest[1:end]
        if b"\\" in value:
            return None
        return value.decode("utf-8", "replace")
    except Exception:
        return None


def _top_level_cwd(raw):
    """Return only the root object's ``cwd`` string, without parsing payload bodies.

    Tool input can contain arbitrary source text, including a seeded ``"cwd"``
    key.  A substring search would let that fixture select the evidence writer.
    This small scanner tracks JSON nesting and accepts the key only at depth one;
    unusual escapes fall back to no context, which makes hook_meter use canonical.
    """
    try:
        stack = []
        in_string = False
        escaped = False
        start = None
        tokens = []
        root_started = False
        root_closed = False
        for index, byte in enumerate(raw):
            if in_string:
                if escaped:
                    escaped = False
                elif byte == 0x5C:
                    escaped = True
                elif byte == 0x22:
                    token = raw[start:index]
                    in_string = False
                    if len(stack) == 1:
                        tokens.append((index, token))
                continue
            if not root_started:
                if byte in b" \t\r\n":
                    continue
                if byte != 0x7B:
                    return None
                root_started = True
                stack.append(0x7D)
                continue
            if byte == 0x22:
                in_string = True
                start = index + 1
            elif byte == 0x7B:
                stack.append(0x7D)
            elif byte == 0x5B:
                stack.append(0x5D)
            elif byte in (0x7D, 0x5D):
                if not stack or stack[-1] != byte:
                    return None
                stack.pop()
                if not stack:
                    root_closed = True
                    if raw[index + 1:].strip():
                        return None
                    break
        if in_string or not root_started or not root_closed:
            return None
        cwd = None
        cwd_count = 0
        for end, token in tokens:
            if token != b"cwd":
                continue
            pos = end + 1
            while pos < len(raw) and raw[pos] in b" \t\r\n":
                pos += 1
            if pos >= len(raw) or raw[pos] != 0x3A:
                continue
            cwd_count += 1
            if cwd_count > 1:
                return None
            pos += 1
            while pos < len(raw) and raw[pos] in b" \t\r\n":
                pos += 1
            if pos >= len(raw) or raw[pos] != 0x22:
                return None
            pos += 1
            value_start = pos
            while pos < len(raw):
                if raw[pos] == 0x5C:
                    return None
                if raw[pos] == 0x22:
                    cwd = raw[value_start:pos].decode("utf-8", "strict")
                    break
                pos += 1
            else:
                return None
        return cwd
    except Exception:
        pass
    return None


def _payload_facts(raw):
    """The identifying fields from the harness payload. Never raises.

    tool_use_id IS THE CORRELATION KEY, and recording it is what makes the
    per-event cost exact instead of inferred. An earlier version of this file
    said the harness gives a hook no per-event id and grouped firings by
    session + event + timestamp-second to compensate. That was wrong:
    hooks/staging-observation-tracker.py has depended on `tool_use_id` since it
    was written, and its docstring records the finding that the field is present
    on both the Pre and Post call of one invocation and identical across them.
    Every gate that fires for one tool call now carries that id, so the rollup
    can add up exactly the hooks that one Bash call paid for — and can say which
    gate actually sentenced it — rather than guessing from a shared timestamp.

    prompt_id does the same job for Stop, which has no tool call: it identifies
    the TURN, so a turn reopened three times is visibly three reopens of one
    turn rather than three separate events on the same day.

    Called AFTER the gate has run: nothing above needs these, and by then the
    gate has usually imported json itself, so the fallback costs little.
    """
    facts = {"event": None, "tool": None, "session": None,
             "tool_use_id": None, "prompt_id": None}
    if not raw:
        return facts
    try:
        facts["event"] = _scan_field(raw, b"hook_event_name")
        facts["tool"] = _scan_field(raw, b"tool_name")
        facts["session"] = _scan_field(raw, b"session_id")
        facts["tool_use_id"] = _scan_field(raw, b"tool_use_id")
        facts["prompt_id"] = _scan_field(raw, b"prompt_id")
        if facts["event"]:
            return facts
        import json
        data = json.loads(raw.decode("utf-8", "replace"))
        if isinstance(data, dict):
            facts["event"] = data.get("hook_event_name") or data.get("hookEventName")
            facts["tool"] = data.get("tool_name") or data.get("toolName")
            facts["session"] = data.get("session_id") or data.get("sessionId")
            facts["tool_use_id"] = data.get("tool_use_id") or data.get("toolUseId")
            facts["prompt_id"] = data.get("prompt_id") or data.get("promptId")
    except Exception:
        pass
    return facts


def _register_from_output(text, event, code, crashed):
    """WHICH REGISTER THE GATE SPOKE IN — recorded, never re-inferred later.

    Three registers, and the difference between the first two is the only
    latency number in this whole exercise that is measured in model turns
    rather than milliseconds:

      "reopen"    the gate BLOCKED. exit 2, or {"decision":"block"}. On Stop
                  that hands the turn back to the model and costs a whole
                  extra assistant message.
      "announce"  the gate spoke without blocking — it returned
                  hookSpecificOutput.additionalContext, which reaches the model
                  as context inside the turn it already paid for. Costs nothing
                  extra.
      "silent"    the gate fired and emitted nothing.

    WHY THIS IS ITS OWN FIELD rather than something a rollup derives from the
    exit code. Five Stop gates — map-architecture, context-handoff, stale-claim,
    loose-work and unread-artifact — were demoted on 2026-08-23 from blocking to
    announcing. A0c deliberately restores context-handoff as the fourth admitted
    reopener at a measured lifecycle threshold; the other four still announce
    and charge nothing. A reader inferring from the exit code sees exit 0 and
    records "allow" for those announcements, which is true about the DECISION
    and silent about the INTERVENTION, so four gates doing real work would look
    like four gates that had gone quiet — and the retire rule keys on denies, so
    each would drift toward being a candidate for precisely the reason it is
    working.

    It also settles a misreading already in the record: the council brief counted
    "eight chat-lint reopens" when chat-lint has not blocked since 2026-08-16 —
    it parks a note for the next UserPromptSubmit instead. A ledger that cannot
    tell a block from a line will keep producing that mistake.
    """
    try:
        if crashed or code not in (0, 2):
            return "error"          # it fell over; it did not speak
        if code == 2:
            return "reopen" if event in STOP_EVENTS else "block"
        stripped = (text or "").strip()
        if stripped.startswith("{"):
            import json
            data = json.loads(stripped)
            if isinstance(data, dict):
                if data.get("decision") in ("block", "deny"):
                    return "reopen" if event in STOP_EVENTS else "block"
                specific = data.get("hookSpecificOutput")
                if isinstance(specific, dict):
                    if specific.get("permissionDecision") in ("deny", "ask"):
                        return "block"
                    if specific.get("additionalContext"):
                        return "announce"
        if stripped:
            return "announce"
        return "silent"
    except Exception:
        return "silent"


def _decision_from_output(text):
    """The gate's own JSON verdict, when it published one. None otherwise.

    Exit codes carry most verdicts here, but the harness also honours structured
    output, and a gate that says {"decision":"block"} with exit 0 has blocked
    just as hard as one that exited 2. Reading it is how the meter stays right
    if a gate changes spellings later.
    """
    try:
        stripped = (text or "").strip()
        if not stripped.startswith("{"):
            return None
        import json
        data = json.loads(stripped)
        if not isinstance(data, dict):
            return None
        specific = data.get("hookSpecificOutput")
        if isinstance(specific, dict):
            decision = specific.get("permissionDecision")
            if decision in ("deny", "ask", "allow"):
                return decision
        decision = data.get("decision")
        if decision in ("block", "deny"):
            return "deny"
        if decision == "approve":
            return "allow"
        if data.get("continue") is False:
            return "deny"
    except Exception:
        pass
    return None


def _deny_class(text):
    """The declared class, found without importing re."""
    try:
        idx = (text or "").find(DENY_MARKER)
        if idx < 0:
            return None
        tail = (text[idx + len(DENY_MARKER):]).split()
        token = tail[0][:64] if tail else ""
        return token if token and all(c.isalnum() or c in "._-" for c in token) else None
    except Exception:
        return None


def _structured_reason(text):
    """Stable reason code carried by a canonical structured Stop refusal."""
    try:
        import json
        outer = json.loads((text or "").strip())
        reason = outer.get("reason") if isinstance(outer, dict) else None
        if isinstance(reason, str) and reason.lstrip().startswith("{"):
            inner = json.loads(reason)
            reason = inner.get("reason") if isinstance(inner, dict) else None
        if (isinstance(reason, str) and reason
                and all(c.isalnum() or c in "._-" for c in reason)):
            return reason[:64]
    except Exception:
        pass
    return None


def _error_tail(text, lines=6, cap=1200):
    """The last `lines` non-empty stderr lines, capped, for an error row."""
    try:
        kept = [line.rstrip() for line in (text or "").splitlines() if line.strip()]
        tail = "\n".join(kept[-lines:])
        return tail if len(tail) <= cap else "…" + tail[-cap:]
    except Exception:
        return None


def _headline(text):
    """First non-empty line of a refusal — the de-facto class gates already have."""
    try:
        for line in (text or "").splitlines():
            line = line.strip()
            if line:
                return line
    except Exception:
        pass
    return None


def _resolve_target(name):
    return name if os.path.isabs(name) else os.path.join(REPO, name)


def _grok_skips(target):
    return (os.path.dirname(os.path.abspath(target)) == os.path.join(REPO, "hooks")
            and os.path.basename(target) in GROK_CONTEXT_HOOKS
            and grok_session())


def _read_payload():
    try:
        return sys.stdin.buffer.read()
    except Exception:
        return b""


def _prepare(raw):
    """Process-wide setup shared by every gate this interpreter runs.

    The two steps that would change a verdict if they failed — handing the
    gate its stdin, and passing its output through — use io alone and cannot
    be affected by the recording layer being absent.
    """
    try:
        # Never inherit an earlier wrapper's routing hint.  Only this payload's
        # existing top-level cwd may nominate an invocation checkout.
        os.environ.pop(INVOCATION_REPO_ENV, None)
        # Jev calls the gate makes for itself go straight to the vendor; only
        # the build advisory takes the server path (ops/typesafe_client.py
        # IN_HOOK_ENV), so no hook spends its time budget on a receipt.
        os.environ["CARR_JEV_IN_HOOK"] = "1"
        invocation_cwd = _top_level_cwd(raw)
        if invocation_cwd and os.path.isdir(invocation_cwd):
            os.environ[INVOCATION_REPO_ENV] = os.path.abspath(invocation_cwd)
    except Exception:
        pass
    if hook_meter is not None:
        try:
            # Before the gate runs, so the gate's OWN human-readable log line
            # lands in the same stream as the telemetry line about it.
            hook_meter.mark_live()
        except Exception:
            pass


class _MemberTimeout(BaseException):
    """A chained gate overran its own declared timeout (see run_chain)."""


def _exec_gate(target, args):
    """Run one gate in this interpreter exactly as `python3 gate.py args` would.

    Returns (code, crashed, timed_out, elapsed_ms). The gate is unguarded on
    purpose: its SystemExit is its verdict and anything else it raises is a
    crash, printed the way the interpreter would print it.
    """
    saved_argv = sys.argv
    sys.argv = [target] + list(args)
    scope = {
        "__name__": "__main__",
        "__file__": target,
        "__doc__": None,
        "__package__": None,
        "__loader__": None,
        "__spec__": None,
        "__cached__": None,
    }
    started = _time.monotonic() * 1000.0
    code = 0
    crashed = False
    timed_out = False
    try:
        with open(target, "rb") as fh:
            source = fh.read()
        exec(compile(source, target, "exec"), scope)     # noqa: S102 — this IS the gate
    except _MemberTimeout:
        code = TIMEOUT_EXIT
        timed_out = True
    except SystemExit as exc:
        value = exc.code
        code = 0 if value is None else (value if isinstance(value, int) else 1)
        if not isinstance(value, (int, type(None))):
            sys.stderr.write(f"{value}\n")
    except BaseException:                       # noqa: BLE001 — mimic `python3 gate.py`
        import traceback
        traceback.print_exc()
        code = 1
        crashed = True
    elapsed = _time.monotonic() * 1000.0 - started
    sys.argv = saved_argv
    return code, crashed, timed_out, elapsed


def _record(raw, target, args, code, crashed, captured_out, captured_err,
            elapsed, origin, extra=None, timed_out=False):
    """Build and emit the one telemetry row a gate firing owes. Never raises."""
    if hook_meter is None:
        return
    try:
        facts = _payload_facts(raw)
        published = _decision_from_output(captured_out)
        if crashed or timed_out or code not in (0, 2):
            outcome = "error"
        elif code == 2:
            outcome = "deny"
        elif published:
            outcome = published
        else:
            outcome = "allow"

        # For a malformed payload, JSON cannot name the hook event. The tracked
        # wiring supplies it independently; it is also authoritative when a
        # semantically corrupt payload claims a different event.
        event = os.environ.get("CARR_CONTEXT_HOOK_EVENT") or facts["event"] or ""
        record = {
            "ts": _time.strftime("%Y-%m-%dT%H:%M:%SZ", _time.gmtime()),
            "event": event or None,
            "hook": os.path.basename(target),
            "arg": (os.path.basename(args[0]) if args else None),
            "tool": facts["tool"],
            "session": facts["session"],
            "tool_use_id": facts["tool_use_id"],
            "prompt_id": facts["prompt_id"],
            "elapsed_ms": round(elapsed, 2),
            "outcome": outcome,
            "exit": code,
            "register": _register_from_output(captured_out, event, code,
                                              crashed or timed_out),
            "reopen": bool(event in STOP_EVENTS and outcome == "deny"),
            "deny_class": (_deny_class(captured_err) or _deny_class(captured_out)
                           or _structured_reason(captured_out)),
            "deny_headline": (_clip(_headline(captured_err))
                              if outcome in ("deny", "ask", "error") else None),
            # The last lines of stderr when the gate fell over. One headline
            # was not enough: on 2026-09-23 two gates died at import on every
            # call for a day and the row said only "Traceback (most recent
            # call last):", which names nothing. The tail names the file and
            # the exception.
            "error_tail": (_error_tail(captured_err) if outcome == "error" else None),
            "pid": os.getpid(),
        }
        if extra:
            record.update(extra)
        record["meter_ms"] = round(_time.monotonic() * 1000.0 - origin - elapsed, 2)
        hook_meter.emit(REPO, record)
    except Exception:
        pass


def main():
    argv = sys.argv[1:]
    if not argv:
        sys.stderr.write("hook-meter-run.py: no gate named\n")
        return 1
    if argv[0] == CHAIN_FLAG:
        return run_chain(argv[1:])

    target = _resolve_target(argv[0])
    if _grok_skips(target):
        return 0

    raw = _read_payload()
    _prepare(raw)
    try:
        sys.stdin = replacement_stdin(raw)
    except Exception:
        pass

    real_out, real_err = sys.stdout, sys.stderr
    try:
        sys.stdout = Tee(real_out)
        sys.stderr = Tee(real_err)
    except Exception:
        sys.stdout, sys.stderr = real_out, real_err

    # ── the gate, unguarded on purpose ──
    code, crashed, _timed_out, elapsed = _exec_gate(target, argv[1:])

    # ── recording, which cannot change what already happened above ──
    captured_out = captured_err = ""
    try:
        captured_out = getattr(sys.stdout, "captured", "")
        captured_err = getattr(sys.stderr, "captured", "")
    except Exception:
        pass
    try:
        sys.stdout.flush()
        sys.stderr.flush()
    except Exception:
        pass
    sys.stdout, sys.stderr = real_out, real_err

    _record(raw, target, argv[1:], code, crashed, captured_out, captured_err,
            elapsed, _T0)
    return code


# ── the chain: N gates of one event and matcher in ONE interpreter ──────────
#
# WHY (WR-000259). Every Bash tool call paid one interpreter start-up per wired
# hook — 17 of them, measured 2026-10-12 by this module's own telemetry — and
# each start-up costs ~45 ms of CPU before a gate reads a byte, plus the import
# of json/re/shlex/subprocess that every gate repeats. The gates themselves
# already run in-process under main() above, so a chain is the same exec loop
# run N times in one process, with the harness-visible result merged the way
# the harness merges N separate hooks.
#
# WHAT A MEMBER STILL GETS, unchanged from its own process:
#   * the same payload bytes on a fresh stdin, the same argv shape, a fresh
#     __main__ scope, and its own Tee'd stdout/stderr;
#   * its own timeout (SIGALRM at the member's declared seconds) and the same
#     failure mode as a killed process: a member that overruns is an error that
#     fails OPEN, its output discarded, exactly what the harness does to a hook
#     it cancels;
#   * os.environ, the working directory and sys.path restored after it, so one
#     gate's process-global edits cannot leak into the next gate's verdict;
#   * its own telemetry row with every field main() writes, plus chain_pos and
#     chain_len (rows share a pid, which is how the scorecard counts processes).
# Members run in the declared order and ALL of them run even after a deny,
# because the harness runs separate hooks in parallel and none of them is
# skipped when another refuses (some record state the next event reads).
#
# THE MERGE (merge_chain) is the separate-process outcome, composed:
#   * any member exit 2  -> chain exit 2; stderr is the denying members' own
#     stderr, in order, so one deny reaches the model byte-for-byte; a member
#     that refused through JSON on exit 0 contributes its reason at its place;
#   * otherwise exit 0 and stdout is the ONE speaking member's stdout verbatim,
#     or, when several spoke, one JSON object whose additionalContext and
#     reasons are joined in order and whose permissionDecision is the most
#     restrictive (deny > ask > allow);
#   * a member that crashed or timed out is a non-blocking error as before; the
#     chain exits 1 with its stderr only when nobody else has output to deliver
#     (exit 1 makes the harness drop stdout).
# The admission rules for membership (no os._exit, signals, threads, chdir;
# PreToolUse/PostToolUse only; plain solo command) live in
# lib/gate_declarations.py, where the wiring is rendered.

CHAIN_FLAG = "--chain"
TIMEOUT_EXIT = 124          # recorded exit for a member cancelled at its timeout
CHAIN_JOIN = "\n\n"
_SEVERITY = {"allow": 1, "ask": 2, "deny": 3}


def parse_chain(tokens):
    """[(timeout_seconds, absolute_gate_path)] from `T:PATH` tokens, or ValueError."""
    members = []
    for token in tokens:
        seconds, sep, path = token.partition(":")
        if not sep or not seconds.isdigit() or int(seconds) <= 0 or not path:
            raise ValueError(f"malformed chain member {token!r}; expected SECONDS:PATH")
        members.append((int(seconds), _resolve_target(path)))
    if not members:
        raise ValueError("empty chain")
    return members


class ChainMember:
    __slots__ = ("target", "code", "out", "err", "captured_out", "captured_err",
                 "crashed", "timed_out", "elapsed", "origin", "timeout")

    def __init__(self, target, timeout):
        self.target, self.timeout = target, timeout
        self.code, self.out, self.err = 0, "", ""
        self.captured_out = self.captured_err = ""
        self.crashed = self.timed_out = False
        self.elapsed = 0.0
        self.origin = 0.0


def _json_object(text):
    stripped = (text or "").strip()
    if not stripped.startswith("{"):
        return None
    try:
        import json
        value = json.loads(stripped)
    except Exception:
        return None
    return value if isinstance(value, dict) else None


def _json_refusal(text):
    """The reason a member gave when it refused through JSON on exit 0."""
    data = _json_object(text)
    if not data:
        return None
    specific = data.get("hookSpecificOutput")
    if isinstance(specific, dict) and specific.get("permissionDecision") == "deny":
        return str(specific.get("permissionDecisionReason") or "denied")
    if data.get("decision") in ("block", "deny"):
        return str(data.get("reason") or "blocked")
    return None


def _merge_json(event, outputs):
    """One harness-readable JSON object from several members' stdout."""
    import json
    merged, specific = {}, {}
    contexts, messages, plain = [], [], []
    decision = None
    reasons = {}
    block_reasons = []
    for text in outputs:
        data = _json_object(text)
        if data is None:
            # Plain stdout on a tool event is transcript-only in the
            # separate-process path; it stays out of the model's channel here.
            plain.append(text)
            continue
        for key, value in data.items():
            if key == "hookSpecificOutput" and isinstance(value, dict):
                for skey, svalue in value.items():
                    if skey == "additionalContext":
                        if svalue:
                            contexts.append(str(svalue))
                    elif skey == "permissionDecision":
                        if _SEVERITY.get(svalue, 0) > _SEVERITY.get(decision, 0):
                            decision = svalue
                    elif skey == "permissionDecisionReason":
                        reasons.setdefault(value.get("permissionDecision"), []).append(str(svalue))
                    else:
                        specific.setdefault(skey, svalue)
            elif key == "decision" and value in ("block", "deny"):
                merged["decision"] = "block"
                block_reasons.append(str(data.get("reason") or ""))
            elif key == "reason" and data.get("decision") in ("block", "deny"):
                continue
            elif key == "continue":
                if value is False:
                    merged["continue"] = False
            elif key == "systemMessage":
                if value:
                    messages.append(str(value))
            elif key == "suppressOutput":
                merged["suppressOutput"] = bool(merged.get("suppressOutput")) or bool(value)
            else:
                merged.setdefault(key, value)
    if merged.get("decision") == "block":
        merged["reason"] = CHAIN_JOIN.join(r for r in block_reasons if r)
    if messages:
        merged["systemMessage"] = CHAIN_JOIN.join(messages)
    if decision:
        specific["permissionDecision"] = decision
        if reasons.get(decision):
            specific["permissionDecisionReason"] = CHAIN_JOIN.join(reasons[decision])
    if contexts:
        specific["additionalContext"] = CHAIN_JOIN.join(contexts)
    if specific:
        specific.setdefault("hookEventName", event or "")
        merged["hookSpecificOutput"] = specific
    return (json.dumps(merged) + "\n" if merged else ""), "".join(plain)


def _error_text(member):
    if member.timed_out:
        return (f"{os.path.basename(member.target)}: cancelled after its "
                f"{member.timeout}s timeout (fails open)\n")
    return member.err


def merge_chain(event, members):
    """(exit, stdout, stderr) the harness would have acted on for these members."""
    oks = [m for m in members if m.code == 0 and not m.timed_out]
    errors = [m for m in members if m.timed_out or m.code not in (0, 2)]
    denies = [m for m in members if m.code == 2 and not m.timed_out]
    if denies:
        parts = []
        for m in members:                       # declared order, both refusal forms
            if m.code == 2 and not m.timed_out:
                parts.append(m.err)
            elif m in oks:
                reason = _json_refusal(m.out)
                if reason:
                    parts.append(reason if reason.endswith("\n") else reason + "\n")
        return 2, "", "".join(parts)
    spoken = [m for m in oks if m.out.strip()]
    notes = "".join(m.err for m in oks)
    if len(spoken) == 1:
        out = spoken[0].out
    elif spoken:
        out, plain = _merge_json(event, [m.out for m in spoken])
        notes += plain
    else:
        out = ""
    failures = "".join(_error_text(m) for m in errors)
    if errors and not out:
        return 1, "", notes + failures
    return 0, out, notes + failures


def _on_alarm(signum, frame):                   # noqa: ARG001
    raise _MemberTimeout()


def run_chain(tokens):
    """Run each `T:PATH` member in order in this interpreter; merge; record."""
    try:
        specs = parse_chain(tokens)
    except ValueError as exc:
        sys.stderr.write(f"hook-meter-run.py --chain: {exc}\n")
        return 1
    import signal

    raw = _read_payload()
    _prepare(raw)
    real_out, real_err = sys.stdout, sys.stderr
    members = []
    origin = _T0
    try:
        previous = signal.signal(signal.SIGALRM, _on_alarm)
    except Exception:
        previous = None
    for timeout, target in specs:
        member = ChainMember(target, timeout)
        member.origin = origin
        if _grok_skips(target):
            origin = _time.monotonic() * 1000.0
            continue
        saved_env = dict(os.environ)
        saved_path = list(sys.path)
        try:
            saved_cwd = os.getcwd()
        except OSError:
            saved_cwd = None
        out_sink, err_sink = io.StringIO(), io.StringIO()
        try:
            sys.stdin = replacement_stdin(raw)
        except Exception:
            pass
        sys.stdout, sys.stderr = Tee(out_sink), Tee(err_sink)
        try:
            try:
                signal.setitimer(signal.ITIMER_REAL, timeout)
                (member.code, member.crashed, member.timed_out,
                 member.elapsed) = _exec_gate(target, [])
            finally:
                signal.setitimer(signal.ITIMER_REAL, 0)
        except _MemberTimeout:
            # The alarm landed after the gate returned, before it was disarmed.
            pass
        try:
            member.captured_out = sys.stdout.captured
            member.captured_err = sys.stderr.captured
        except Exception:
            pass
        sys.stdout, sys.stderr = real_out, real_err
        member.out, member.err = out_sink.getvalue(), err_sink.getvalue()
        if member.timed_out:
            member.captured_err += _error_text(member)
        if os.environ != saved_env:
            os.environ.clear()
            os.environ.update(saved_env)
        sys.path[:] = saved_path
        if saved_cwd is not None:
            try:
                os.chdir(saved_cwd)
            except OSError:
                pass
        members.append(member)
        origin = _time.monotonic() * 1000.0
    if previous is not None:
        try:
            signal.signal(signal.SIGALRM, previous)
        except Exception:
            pass

    event = (os.environ.get("CARR_CONTEXT_HOOK_EVENT")
             or _scan_field(raw, b"hook_event_name") or "")
    code, out, err = merge_chain(event, members)
    try:
        real_out.write(out)
        real_err.write(err)
        real_out.flush()
        real_err.flush()
    except Exception:
        pass

    # Recording happens after the verdict is on the wire, so it costs the
    # harness nothing; each row is the row the member's own process wrote.
    for pos, member in enumerate(members):
        _record(raw, member.target, [], member.code, member.crashed,
                member.captured_out, member.captured_err, member.elapsed,
                member.origin, extra={"chain_pos": pos, "chain_len": len(members)},
                timed_out=member.timed_out)
    return code


def cli():
    # os._exit is deliberate: a gate may leave a non-daemon thread or an atexit
    # handler behind and the harness is waiting on this process. The streams are
    # flushed above; nothing else here owns unflushed state.
    try:
        rc = main()
    except BaseException:                       # noqa: BLE001
        import traceback
        traceback.print_exc()
        rc = 1
    try:
        sys.stdout.flush()
        sys.stderr.flush()
    except Exception:
        pass
    os._exit(rc if isinstance(rc, int) else 1)
