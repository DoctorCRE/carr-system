"""Claude hook event transport and verdicts, shared by gates and fixtures."""
from contextlib import redirect_stderr, redirect_stdout
from functools import wraps
import io
import json
import os
import sys


class Verdict:
    """An immutable (code, stdout, stderr) triple.

    Hand-written rather than a frozen dataclass: every hook process imports
    this module, and `dataclasses` (with `inspect` behind it) was about 20 ms
    of CPU per hook start on the 2026-10-12 measurement, paid dozens of times
    per tool call for three fields.
    """
    __slots__ = ('code', 'stdout', 'stderr')

    def __init__(self, code=0, stdout='', stderr=''):
        object.__setattr__(self, 'code', code)
        object.__setattr__(self, 'stdout', stdout)
        object.__setattr__(self, 'stderr', stderr)

    def __setattr__(self, name, value):
        raise AttributeError(f"cannot assign to field {name!r}")

    def __delattr__(self, name):
        raise AttributeError(f"cannot delete field {name!r}")

    def _fields(self):
        return (self.code, self.stdout, self.stderr)

    def __eq__(self, other):
        if other.__class__ is not self.__class__:
            return NotImplemented
        return self._fields() == other._fields()

    def __hash__(self):
        return hash(self._fields())

    def __repr__(self):
        return (f"{self.__class__.__qualname__}(code={self.code!r}, "
                f"stdout={self.stdout!r}, stderr={self.stderr!r})")

    @classmethod
    def block(cls, reason):
        return cls(stdout=json.dumps({'decision': 'block', 'reason': reason}) + '\n')

    @classmethod
    def refuse(cls, message):
        return cls(code=2, stderr=message + '\n')

    @classmethod
    def announce(cls, message, event='Stop'):
        return cls(stdout=json.dumps({'hookSpecificOutput': {
            'hookEventName': event, 'additionalContext': message}}) + '\n')

    @property
    def decision(self):
        if self.code == 2:
            return 'deny'
        if self.code:
            return 'error'
        for line in self.stdout.splitlines():
            try:
                value = json.loads(line)
            except ValueError:
                continue
            if not isinstance(value, dict):
                continue
            specific = value.get('hookSpecificOutput')
            specific = specific if isinstance(specific, dict) else {}
            permission = specific.get('permissionDecision')
            if permission in ('deny', 'ask'):
                return permission
            if value.get('decision') in ('block', 'deny') or value.get('continue') is False:
                return 'deny'
        return 'allow'

    def emit(self, stdout, stderr):
        stdout.write(self.stdout)
        stderr.write(self.stderr)
        return self.code


class Event(dict):
    def transcript(self, *, hook, log_path=None):
        from lib.transcript_read import load_transcript
        path = self.get('transcript_path') or self.get('transcriptPath')
        if not path:
            return []
        return load_transcript(path, hook=hook,
                               session=self.get('session_id') or self.get('sessionId'),
                               log_path=log_path)

    def latch(self, hook, reason, claims):
        from hooks import stop_latch
        session = self.get('session_id') or self.get('sessionId')
        identity = stop_latch.claim_identity(hook, reason, claims)
        # Fixtures relocate state before invoking this method; existing gates
        # can keep their imported latch state and individual banking order.
        saved = stop_latch.STATE
        stop_latch.STATE = os.environ.get('CARR_STOP_LATCH_STATE') or saved
        try:
            if stop_latch.latched(session, identity):
                return True
            stop_latch.record_fire(session, identity)
            return False
        finally:
            stop_latch.STATE = saved


def decision(function=None, *, on_error=None, failure="open"):
    """Adapt gate effects into a verdict without writing to the caller's streams.

    Existing gates intentionally mix plain prose, JSON, stderr and exit codes.
    Capture is at that transport seam, so policy helpers keep their ordering.
    Error policy can log or return a visible refusal without emitting twice.
    """
    def decorate(function):
        @wraps(function)
        def decide(event):
            if isinstance(event, dict) and not isinstance(event, Event):
                event = Event(event)
            out, err = io.StringIO(), io.StringIO()
            code = 0
            caller_out, caller_err = sys.stdout, sys.stderr
            with redirect_stdout(out), redirect_stderr(err):
                try:
                    result = function(event)
                    if isinstance(result, Verdict):
                        result.emit(out, err)
                        code = result.code
                    elif isinstance(result, int):
                        code = result
                except SystemExit as exc:
                    code = exc.code if isinstance(exc.code, int) else (0 if exc.code is None else 1)
                    if exc.code is not None and not isinstance(exc.code, int):
                        print(exc.code, file=err)
                except Exception as exc:
                    if failure == "raise":
                        caller_out.write(out.getvalue())
                        caller_err.write(err.getvalue())
                        raise
                    if on_error is not None:
                        result = on_error(exc)
                        if isinstance(result, Verdict):
                            code = result.emit(out, err)
                        elif isinstance(result, int):
                            code = result
            return Verdict(code, out.getvalue(), err.getvalue())
        return decide
    return decorate(function) if function is not None else decorate


def run(decide, *, stdin=None, stdout=None, stderr=None, parse_error=None,
        invalid_event=None):
    stdin = sys.stdin if stdin is None else stdin
    stdout = sys.stdout if stdout is None else stdout
    stderr = sys.stderr if stderr is None else stderr
    try:
        payload = json.load(stdin)
    except Exception as exc:
        if invalid_event is not None:
            return decide(Event(invalid_event)).emit(stdout, stderr)
        if parse_error is None:
            return 0
        result = decision(lambda event: parse_error(exc), failure="raise")(Event({}))
        return result.emit(stdout, stderr)
    # Keep scalar/array handling in the gate's policy. Several hooks distinguish
    # syntactically valid JSON from an unreadable event in their diagnostic log.
    event = Event(payload) if isinstance(payload, dict) else payload
    return decide(event).emit(stdout, stderr)
