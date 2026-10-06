"""Timestamp-only credential projection and a reconciled Grok sign-in incident."""
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
from pathlib import Path
import re
import subprocess
import sys
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parent))
from jev_spend_health import ROOT, _run_verb, _state, _save_state

AUTH = Path.home() / ".grok" / "auth.json"
STATE = ROOT / "out" / "grok-session-alert.json"
ACTION = ("on breach: one incident loop; one alert attempt/channel/UTC day · owner Joe · "
          "remediation run grok login · verify grok models succeeds · "
          "auto-clear after fresh successful authentication under the incident lock")


def read_timestamps(path=AUTH):
    """Validate all JSON while retaining only one supported object's timestamps.

    Supported layouts are a root credential or one scoped credential in the
    root map. Unknown strings, keys and scalar values are never retained.
    More than one candidate, or a partial credential, is ambiguous and refused.
    """
    with Path(path).open(encoding="utf-8") as stream:
        char = stream.read(1)
        pairs = []

        def advance():
            nonlocal char
            char = stream.read(1)

        def whitespace():
            while char and char in " \r\n\t":
                advance()

        def expect(value):
            if char != value:
                raise ValueError("invalid credential structure")
            advance()
            whitespace()

        def string(retain=False, key=False):
            if char != '"':
                raise ValueError("invalid credential string")
            advance()
            kept = []
            candidates = ["create_time", "expires_at"] if key else []
            index = 0
            while char and char != '"':
                value = char
                if ord(value) < 32:
                    raise ValueError("invalid credential string")
                advance()
                if value == "\\":
                    escape = char
                    if not escape or escape not in '"\\/bfnrtu':
                        raise ValueError("invalid credential escape")
                    advance()
                    if escape == "u":
                        code = 0
                        for _ in range(4):
                            if not char or char not in "0123456789abcdefABCDEF":
                                raise ValueError("invalid credential escape")
                            code = code * 16 + int(char, 16)
                            advance()
                        value = chr(code)
                    else:
                        value = {'"': '"', "\\": "\\", "/": "/", "b": "\b",
                                 "f": "\f", "n": "\n", "r": "\r", "t": "\t"}[escape]
                if retain:
                    if len(kept) >= 128:
                        raise ValueError("invalid timestamp field")
                    kept.append(value)
                if key:
                    candidates = [name for name in candidates if index < len(name) and name[index] == value]
                    index += 1
            expect('"')
            if key:
                return next((name for name in candidates if len(name) == index), None)
            return "".join(kept) if retain else None

        def digits():
            if not char or char not in "0123456789":
                raise ValueError("invalid credential number")
            while char and char in "0123456789":
                advance()

        def scalar():
            if char in ("t", "f", "n"):
                literal = {"t": "true", "f": "false", "n": "null"}[char]
                for value in literal:
                    if char != value:
                        raise ValueError("invalid credential literal")
                    advance()
            else:
                if char == "-":
                    advance()
                if char == "0":
                    advance()
                else:
                    if not char or char not in "123456789":
                        raise ValueError("invalid credential number")
                    digits()
                if char == ".":
                    advance()
                    digits()
                if char and char in "eE":
                    advance()
                    if char and char in "+-":
                        advance()
                    digits()
            if char and char not in ",}] \r\n\t":
                raise ValueError("invalid credential scalar")
            whitespace()

        def value(depth=0, location="unknown"):
            if depth > 32:
                raise ValueError("invalid credential structure")
            if char == '"':
                string()
            elif char in ("{", "["):
                opener = char
                closer = "}" if opener == "{" else "]"
                expect(opener)
                pair = {}
                if char != closer:
                    while True:
                        field = None
                        if opener == "{":
                            field = string(key=True)
                            expect(":")
                        if field in ("create_time", "expires_at"):
                            if location not in ("root", "credential") or field in pair:
                                raise ValueError("unsupported or duplicate timestamps")
                            pair[field] = string(retain=True)
                        else:
                            child = "unknown"
                            if location == "root" and char == "{":
                                child = "credential"
                            value(depth + 1, child)
                        if char == closer:
                            break
                        expect(",")
                expect(closer)
                if pair:
                    if set(pair) != {"create_time", "expires_at"}:
                        raise ValueError("incomplete credential timestamps")
                    pairs.append(pair)
            else:
                scalar()

        whitespace()
        if char != "{":
            raise ValueError("invalid credential structure")
        value(location="root")
        if char or len(pairs) != 1:
            raise ValueError("missing or ambiguous credential timestamps")
        return pairs[0]


def authentication_result(result):
    """Classify CLI diagnostics without returning or logging their contents."""
    if re.search(r"not authenticated|unauthenticated|authentication required|sign.?in|grok login",
                 result.stdout + result.stderr, re.I):
        return "refused"
    return "succeeded" if result.returncode == 0 else "unavailable"


def authenticate():
    """Read-only CLI authentication/refresh check; never a model-work call."""
    try:
        result = subprocess.run(["grok", "models"], stdin=subprocess.DEVNULL,
                                capture_output=True, text=True, timeout=60)
        return authentication_result(result)
    except (OSError, subprocess.SubprocessError, UnicodeError):
        return "unavailable"


def inspect_session(path=AUTH, *, now=None, authenticate=None):
    """Access expiry is informational; only an authentication refusal needs login."""
    now = now or datetime.now(timezone.utc)
    try:
        times = read_timestamps(path)
        created, expires = (datetime.fromisoformat(times[key].replace("Z", "+00:00"))
                            for key in ("create_time", "expires_at"))
        if not created.tzinfo or not expires.tzinfo or created > now or expires <= created:
            raise ValueError("invalid credential timestamps")
        age = (now-created).total_seconds()/86400
        left = (expires-now).total_seconds()/86400
        auth = authenticate() if authenticate else "unchecked"
        status = {"succeeded": "OK", "refused": "FAIL", "unavailable": "WARN",
                  "unchecked": "OK" if left > 0 else "WARN"}[auth]
        return {"status": status, "age_days": age, "days_left": left, "authentication": auth,
                "detail": f"access-token age {age:.2f}d; {left:.2f}d left; authentication {auth} "
                          "(refresh-session expiry undisclosed)"}
    except (OSError, ValueError, TypeError, UnicodeError, KeyError):
        return {"status": "FAIL", "authentication": "unchecked",
                "detail": "credential timestamps unavailable or invalid"}


def _read_loop(loop_id):
    row = _run_verb("read-loop", {"loop_id": loop_id})
    row = row.get("loop", row)
    if (row.get("loop_id") != loop_id or type(row.get("version")) is not int
            or row.get("status") not in ("open", "done", "dropped")):
        raise RuntimeError("loop read unconfirmed")
    return row


def _recover_loop(state, path):
    """Replay an uncertain create with the persisted identity and exact payload."""
    if state.get("incident") and not state.get("loop_id"):
        answer = _run_verb("add-loop", state["create_payload"])
        if not answer.get("loop_id"):
            raise RuntimeError("loop write unconfirmed")
        state["loop_id"] = answer["loop_id"]
        _save_state(path, state)
    if state.get("loop_id"):
        row = _read_loop(state["loop_id"])
        if row["status"] == "open":
            return row
        # A confirmed closed record cannot poison the next incident.
        state.clear()
        _save_state(path, state)
    return None


def _reconcile_breach(message):
    path = ROOT / "out" / "grok-session-loop.json"
    state = _state(path)
    row = _recover_loop(state, path)
    if row:
        return
    incident = str(uuid.uuid4())
    payload = {"kind": "open_loop", "domain": "system", "owner": "joe", "marker": "none",
               "blocker": "human_only", "blocker_detail": "Grok sign-in needs authentication verification",
               "idempotency_key": incident, "body": message}
    state = {"incident": incident, "create_payload": payload}
    # Durable intent precedes the external write. A timeout or process death
    # replays the same key/payload until its ID is recovered, even across days.
    _save_state(path, state)
    _recover_loop(state, path)


def _deliver(message, *, now=None):
    """Reconcile the incident every time; notification limits are independent."""
    day = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).date().isoformat()
    failures = []
    try:
        _reconcile_breach(message)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
        failures.append("record")
    delivery_path = ROOT / "out" / "grok-session-delivery.json"
    delivery = _state(delivery_path)
    if delivery.get("day") != day:
        delivery = {"day": day}
    commands = [["/usr/bin/osascript", "-e", 'on run argv\ndisplay notification (item 1 of argv) with title "CARR Grok login"\nend run', message],
                [sys.executable, str(ROOT / "bin/gmail-handover.py"), "--to", "joe",
                 "--subject", "CARR Grok login needs attention", "--body", message]]
    for channel, command in zip(("local", "mail"), commands):
        if channel in delivery:
            if delivery[channel] != "sent":
                failures.append(channel)
            continue
        delivery[channel] = "attempted"
        _save_state(delivery_path, delivery)
        try:
            result = subprocess.run(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL, timeout=35)
            delivery[channel] = "failed" if result.returncode else "sent"
        except (OSError, subprocess.SubprocessError):
            delivery[channel] = "failed"
        if delivery[channel] != "sent":
            failures.append(channel)
        _save_state(delivery_path, delivery)
    if failures:
        raise RuntimeError("alert transport unavailable")


def _clear_loop():
    path = ROOT / "out" / "grok-session-loop.json"
    state = _state(path)
    row = _recover_loop(state, path)
    if row:
        _run_verb("close-loop", {"loop_id": row["loop_id"], "base_version": row["version"],
                  "idempotency_key": str(uuid.uuid5(uuid.NAMESPACE_URL, f"carr-grok-session:clear:{row['loop_id']}")),
                  "resolution": "done", "outcome": "Auto-cleared: fresh Grok authentication succeeded."})
        _save_state(path, {})


@contextmanager
def _locked(state_path):
    path = Path(str(state_path)+".lock")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def sign_in_alert(*, state_path=STATE, now=None):
    with _locked(state_path):
        _deliver("Grok needs sign-in; run grok login. Verify grok models succeeds.", now=now)
    return "incident reconciled; daily alert attempts deduplicated"


def health_row(*, state_path=STATE, now=None, observe=None):
    """Read authentication inside the same lock as runner incident writes.

    No pre-lock observation is accepted. Holding this lock through the fresh
    authentication check binds successful recovery to the current generation;
    a waiting runner refusal opens its own incident after this check completes.
    """
    with _locked(state_path):
        row = observe() if observe else inspect_session(now=now, authenticate=authenticate)
        line = f"{row['status']} Grok session — {row['detail']} · {ACTION}"
        try:
            if row["status"] == "FAIL":
                _deliver("Grok session: " + row["detail"] + "; run grok login. Verify grok models succeeds.", now=now)
                line += " · incident reconciled; daily alert attempts deduplicated"
            elif row["status"] == "OK" and row.get("authentication") == "succeeded":
                _clear_loop()
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
            line += " · incident action FAILED"
        return line
