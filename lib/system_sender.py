"""system_sender — the system's own outbound sends, behind one policy chain.

Joe ruled on 2026-10-12 (decision cbe91287-10a2-48d0-bda7-3b1784b4bb93) that
the system's own outbound sends must not wait for his hand, and rule c66dc739
was amended the same day: sending is no longer a human gate. This module is
what replaces the human gate. Two calls, one chain:

    send_mail(to=[...], subject=..., body=..., deal_id=None, follow_up=None)
    send_social(platform=..., text=..., media_urls=())

Every call runs the SAME chain, in this order, before anything leaves:

  0. config        the whole config is validated when the Sender is built
                   (live_from, caps, platforms, addresses, file names); a bad
                   config refuses every send with reason config_invalid and
                   claims nothing.
  a. kill switch   the stop file in the state directory, or an open or claimed
                   record-layer signal of kind system_send_stop (read through
                   next-signals). A read that fails refuses: an unreadable
                   switch is not "off".
  b. writing lint  `run.sh lint <file> --surface email|social`. ANY finding
                   refuses, REVIEW and RATION included, because no human reads
                   the draft after this point.
  c. allowlist     mail: a non-empty subject (the transport finds the compose
                   window by its exact title), then Joe's or Dell's own address,
                   or an exact participant
                   email on the named deal (get-call-context, read live). A
                   participant send also needs follow_up {next_on, next_step},
                   because log-outreach refuses a sent touch with no next step.
                   Social: a platform in the config that also has an accountId
                   in claude-tree/.../blotato-accounts.json.
  d. dedupe        a key claimed in the ledger BEFORE the send and released
                   only on a DEFINITE non-send. A crash or an ambiguous transport
                   answer leaves it held, so a retry is refused instead of
                   sending twice (resolve_claim settles it). The default key is
                   a hash of the message AND its Central calendar date, so the
                   same words may go again on a later day; a caller's explicit
                   dedupe_key is used as given.
  e. daily caps    from config (20 mail, 5 social) per local day in the
                   config's timezone. The slot is counted and reserved in the
                   SAME ledger lock as the dedupe claim, so concurrent senders
                   cannot overshoot; a released claim frees its slot.
  f. shadow mode   while live_from is null or in the future, the call does all
                   of the above, records would_send, and sends nothing. The
                   shipped config is live (Joe, 2026-10-12: no shadow period
                   for an obvious improvement); shadow stays available by
                   moving live_from forward.
  g. receipts      every attempt, refusals included, appends one line to
                   sends.jsonl in the state directory, then reaches the record
                   layer: a live mail to a deal participant through
                   log-outreach (ref = the deal's id), everything else through
                   record-signal at severity info.

STATE IS PER MACHINE. The dedupe ledger, sends.jsonl and the stop file live in
one machine-wide directory OUTSIDE the repo, default ~/carr-local/system-sender/
(config key state_dir; environment variable CARR_SYSTEM_SENDER_STATE_DIR
overrides it, which is how the selftest points at a temp dir). It is created
mode 700 on first use. Every checkout and worktree on one machine shares it, so
dedupe and the caps are per MACHINE: two machines could each send the same
message and each has its own cap. The record-layer system_send_stop signal is
the GLOBAL kill switch; the stop file stops this machine only.

LIVE ON MERGE, AND THE FIRST CALLER IS THE LIVE TEST. Joe ruled there is no
shadow period for an obvious improvement, so live_from is in the past. The
Outlook transport had never pressed Send when this shipped; the first live send
should be send_self_test(subject, body), a message to Joe's own address through
the whole chain, so the transport is proven on a self-send before any
participant mail goes out.

Outcomes (SendResult.outcome): sent · would_send · refused (the chain said no)
· failed (DEFINITELY not sent: the key is released and the receipt names the
reason, e.g. outlook_open_failed, outlook_not_frontmost,
compose_window_refused, mailto_builder_unavailable, blotato_unreachable,
blotato_key_unavailable) · send_unknown (the transport may have sent; the
dedupe key stays held).

Seams, all injectable through Sender(...) and all defaulted to the real thing:
`verb` (record-layer door, default `./run.sh call` via lib.record_call),
`lint`, `outlook` (open the mailto compose window and run
bin/outlook-send.applescript), `blotato_http`, `blotato_key`, `now`, `root`
(the repo, for the Blotato accounts file) and `state_dir`. This is a library: no shebang and no main, so it adds no
script ingress.
"""
from __future__ import annotations

import contextlib
import datetime as dt
import fcntl
import hashlib
import importlib.util
import json
import os
import re
import secrets
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from lib.record_call import VerbResult, call_verb

REPO = Path(__file__).resolve().parents[1]
CONFIG_PATH = REPO / "ops" / "config" / "system-sender.v1.json"
SEND_SCRIPT = REPO / "bin" / "outlook-send.applescript"
DRAFT_HELPER = REPO / "bin" / "outlook-draft.py"

DEFAULT_STATE_DIR = "~/carr-local/system-sender"
STATE_DIR_ENV = "CARR_SYSTEM_SENDER_STATE_DIR"

SENT, WOULD_SEND, REFUSED, FAILED, UNKNOWN = (
    "sent", "would_send", "refused", "failed", "send_unknown")
# A cap slot is reserved at claim time and counts until it is released. Live:
# reserved (in flight, or held after an ambiguous answer) and sent. Shadow: the
# rehearsal (would_send), so a shadow week shows where the cap would bite.
_SLOT_COUNTS = ("reserved", "sent", "would_send")
# Slots older than this are dropped from the ledger; only today's count.
_SLOT_KEEP_DAYS = 7

# bin/outlook-draft.py's ceiling for the mailto route: a silently truncated
# message is worse than a refusal.
MAILTO_LIMIT = 1900
# The FULL subject goes to bin/outlook-send.applescript, which matches the
# compose window by exact title. A subject Outlook truncates in the title bar
# matches no window and is refused, never matched on a prefix.

_EMAIL = re.compile(r"[^@\s,;<>]+@[^@\s,;<>]+\.[A-Za-z]{2,}")
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_LINT_COUNTS = re.compile(r"(\d+) HARD, (\d+) REVIEW, (\d+) RATION")
_PROBE = re.compile(r"COUNT (\d+) (\d+)")
_BARE_NAME = re.compile(r"[A-Za-z0-9_-][A-Za-z0-9._-]*")

VerbDoor = Callable[[str, Mapping[str, Any]], VerbResult]
Linter = Callable[[str, str], "tuple[bool, str]"]
OutlookRunner = Callable[[str, str], str]
BlotatoHttp = Callable[[str, Mapping[str, str], bytes], "tuple[int, Any]"]
KeySource = Callable[[], "str | None"]
Clock = Callable[[], dt.datetime]
ProcessRunner = Callable[..., "subprocess.CompletedProcess[str]"]


@dataclass(frozen=True)
class SendResult:
    outcome: str
    reason: str
    attempt_id: str
    dedupe_key: str
    detail: str = ""


@dataclass(frozen=True)
class KillSwitch:
    state: str          # clear | engaged | unreadable
    detail: str = ""


@dataclass(frozen=True)
class _Delivery:
    state: str          # sent | not_sent | unknown
    detail: str = ""
    ref: str = ""
    reason: str = "transport_refused"   # the receipt's reason on a definite non-send


@dataclass
class _Attempt:
    channel: str        # mail | social
    target: str         # comma-joined recipients, or the platform
    dedupe_key: str
    subject: str = ""
    text_sha256: str = ""
    attempt_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    deal: dict[str, str] | None = None
    follow_up: dict[str, str] | None = None
    platform_account: dict[str, str] | None = None


class LedgerError(Exception):
    """The dedupe ledger exists but cannot be read as a ledger."""


class TransportNotSent(Exception):
    """Raised by a transport seam when it KNOWS nothing left the machine (the
    connection never opened, the key could not be read). The key is released."""

    def __init__(self, reason: str, detail: str) -> None:
        super().__init__(detail)
        self.reason = reason


# ── default adapters ────────────────────────────────────────────────────────

def _default_verb(verb: str, args: Mapping[str, Any]) -> VerbResult:
    return call_verb(verb, args, timeout=60)


def run_lint(text: str, surface: str) -> tuple[bool, str]:
    """The existing writing-lint tool, the one `run.sh lint` runs, called directly
    (run.sh has a zsh shebang, absent on CI and fresh Linux checkouts).
    Clean means exit 0 AND zero findings of every severity."""
    fd, path = tempfile.mkstemp(prefix="system-sender-", suffix=".txt")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        proc = subprocess.run([sys.executable, str(REPO / "tools" / "writing-lint.py"), path,
                               "--surface", surface],
                              cwd=str(REPO), capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"lint could not run ({type(exc).__name__})"
    finally:
        with contextlib.suppress(OSError):
            os.unlink(path)
    counts = _LINT_COUNTS.search(proc.stdout or "")
    if not counts:
        return False, "lint output unreadable"
    clean = proc.returncode == 0 and all(int(n) == 0 for n in counts.groups())
    return clean, counts.group(0)


def _mailto_builder() -> Callable[[Mapping[str, Any]], str]:
    """bin/outlook-draft.py's build_url, loaded rather than copied, so the
    draft path and the send path encode a message the same way (rule a8c55a47:
    quote() not urlencode(), recipients kept literal)."""
    spec = importlib.util.spec_from_file_location("outlook_draft_helper", DRAFT_HELPER)
    if spec is None or spec.loader is None:
        raise ImportError("bin/outlook-draft.py unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    builder: Callable[[Mapping[str, Any]], str] = module.build_url
    return builder


def _osascript(run: ProcessRunner, *args: str, timeout: int,
               env: Mapping[str, str] | None = None) -> tuple[int, str]:
    proc = run(["osascript", str(SEND_SCRIPT), *args], capture_output=True, text=True,
               timeout=timeout, env=None if env is None else dict(env))
    return proc.returncode, (proc.stdout or proc.stderr or "").strip()


# THE SEND TOKEN. bin/outlook-send.applescript's `send` step presses nothing
# unless it is handed the token in this file (state_dir/SEND_TOKEN_FILE), for
# the same subject, before it expires; it deletes the file before it touches
# Outlook, so a token works once. _outlook_send writes the token only after the
# whole policy chain has passed, while it holds SEND_LOCK_FILE, and deletes it
# again in a finally block. A direct `osascript bin/outlook-send.applescript
# send ...` therefore refuses: it has no token. This stops a caller that skips
# the library by accident or habit; a same-user process that deliberately
# forges the file can still get through, which is a deliberate circumvention,
# not a path a session can take without noticing.
SEND_TOKEN_FILE = "send-token"
SEND_LOCK_FILE = "send-token.lock"
SEND_TOKEN_TTL_SECONDS = 60


def _write_send_token(path: Path, token: str, subject: str, expires: int) -> None:
    """Write `<token>\\n<expiry epoch seconds>\\n<subject>` mode 600, atomically
    (the script must never read half a token)."""
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(f"{token}\n{expires}\n{subject}")
    os.replace(tmp, path)


def _outlook_send(url: str, subject: str, *, state_dir: Path,
                  run: ProcessRunner = subprocess.run) -> str:
    """Open the compose window in the signed-in Outlook app (never a bare
    `open mailto:`, which lands in Joe's personal default handler) and press
    Send through bin/outlook-send.applescript. Called only from Sender._run,
    after the whole policy chain has passed. Under the machine-wide send lock
    (state_dir/SEND_LOCK_FILE, so two senders never interleave windows), three
    steps:

      1. probe: count Outlook's windows, and refuse if one is ALREADY titled
         with this subject (it could be Joe's own draft) or Outlook is not
         running (a launch adds a main window too, so "one new window" could
         not be proven).
      2. `open -a`: a non-zero exit means no compose window; nothing pressed.
      3. send: a fresh single-use token is written to state_dir/SEND_TOKEN_FILE
         and passed to the script, which refuses without it (see SEND_TOKEN_FILE
         above). The script then proceeds only if exactly ONE new window
         appeared and exactly one window carries this exact subject, and only
         while Outlook is frontmost. The token file is deleted afterwards
         whatever happened.

    Every answer before step 3's keystroke is a definite non-send ("ERROR:",
    "REFUSED:", "NOT_FRONTMOST:"); a script that failed in step 3 is
    "AMBIGUOUS:" because it may have failed after pressing Send."""
    state = Path(state_dir)
    if not state.is_dir():
        state.mkdir(parents=True, mode=0o700, exist_ok=True)
        os.chmod(state, 0o700)
    with open(state / SEND_LOCK_FILE, "a", encoding="utf-8") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        return _outlook_send_locked(url, subject, state, run)


def _outlook_send_locked(url: str, subject: str, state: Path, run: ProcessRunner) -> str:
    try:
        code, probe = _osascript(run, "probe", subject, timeout=30)
    except (OSError, subprocess.SubprocessError) as exc:
        return f"ERROR: window probe could not run ({type(exc).__name__}); nothing opened"
    if code == 0 and probe.startswith("NOT_RUNNING"):
        return "ERROR: Outlook is not running; nothing opened"
    counts = _PROBE.fullmatch(probe) if code == 0 else None
    if counts is None:
        return f"ERROR: window probe unreadable ({probe[:120]}); nothing opened"
    before, already = (int(n) for n in counts.groups())
    if already:
        return (f"REFUSED: {already} window(s) already titled with this subject; "
                "nothing opened, nothing pressed")
    try:
        opened = run(["open", "-a", "Microsoft Outlook", url], capture_output=True,
                     text=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as exc:
        return f"ERROR: open failed ({type(exc).__name__}); Send never pressed"
    if opened.returncode != 0:
        return f"ERROR: open failed (exit {opened.returncode}); Send never pressed"
    token_path = state / SEND_TOKEN_FILE
    token = secrets.token_hex(32)
    try:
        try:
            _write_send_token(token_path, token, subject,
                              int(time.time()) + SEND_TOKEN_TTL_SECONDS)
        except OSError as exc:
            return f"ERROR: send token could not be written ({type(exc).__name__}); Send never pressed"
        code, answer = _osascript(run, "send", subject, str(before), token, timeout=120,
                                  env={**os.environ, STATE_DIR_ENV: str(state)})
    finally:
        with contextlib.suppress(FileNotFoundError):
            token_path.unlink()
    if code != 0:
        return f"AMBIGUOUS: outlook-send exited {code}: {answer[:120]}"
    return answer


def _blotato_post(url: str, headers: Mapping[str, str], body: bytes) -> tuple[int, Any]:
    request = urllib.request.Request(url, data=body, method="POST", headers=dict(headers))
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            status = response.status
            raw = response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, None
    except urllib.error.URLError as exc:
        # urlopen wraps only failures while connecting and writing the request
        # (DNS, refused, TLS) as URLError; a failure reading the answer raises
        # raw and stays ambiguous. A request that was never fully written
        # cannot have created a post.
        raise TransportNotSent("blotato_unreachable",
                               f"Blotato unreachable ({type(exc.reason).__name__})") from exc
    try:
        return status, json.loads(raw)
    except ValueError:
        return status, None


def _blotato_key() -> str | None:
    """BLOTATO_API_KEY from the environment, else from ~/.zprofile, where the
    social-media-manager skill has kept it (credential-inventory row
    blotato-api-key). The value is held in memory only, never printed."""
    key = os.environ.get("BLOTATO_API_KEY")
    if key:
        return key
    profile = Path.home() / ".zprofile"
    if not profile.is_file():
        return None
    proc = subprocess.run(
        ["/bin/zsh", "-c", 'source "$HOME/.zprofile" >/dev/null 2>&1; printf %s "${BLOTATO_API_KEY-}"'],
        capture_output=True, text=True, timeout=15)
    return proc.stdout or None


def _utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def load_config(path: Path = CONFIG_PATH) -> dict[str, Any]:
    config: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return config


def _parse_live_from(value: Any, tz: dt.tzinfo) -> dt.datetime | None:
    """The instant sending goes live, or None for shadow. Raises ValueError on
    a value that is not null, a date, or an ISO timestamp."""
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise TypeError("live_from must be null, YYYY-MM-DD or an ISO timestamp")
    if _DATE.fullmatch(value):
        # A bare date means midnight of that day in the config's timezone.
        return dt.datetime.fromisoformat(value).replace(tzinfo=tz)
    start = dt.datetime.fromisoformat(value)
    # An offset-less time of day is ambiguous: stay in shadow.
    return start if start.tzinfo is not None else None


def _is_count(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def validate_config(config: Any) -> str | None:
    """Every problem with a sender config, joined, or None when it is sound.
    Run once when a Sender is built, so a bad config refuses every send before
    anything is claimed instead of raising halfway through one."""
    if not isinstance(config, Mapping):
        return "config is not a JSON object"
    problems: list[str] = []
    tz: dt.tzinfo | None = None
    try:
        tz = ZoneInfo(str(config.get("timezone")))
    except (ValueError, KeyError, OSError):
        problems.append(f"timezone {config.get('timezone')!r} is not an IANA zone")
    try:
        _parse_live_from(config.get("live_from"), tz or dt.timezone.utc)
    except (TypeError, ValueError):
        problems.append(f"live_from {config.get('live_from')!r} is not null, a date or an ISO timestamp")
    caps = config.get("daily_caps")
    if not (isinstance(caps, Mapping) and all(_is_count(caps.get(ch)) for ch in ("mail", "social"))):
        problems.append("daily_caps needs mail and social as non-negative integers")
    mail = config.get("mail")
    own = mail.get("self_addresses") if isinstance(mail, Mapping) else None
    if not (isinstance(own, list) and own and all(isinstance(a, str) and _EMAIL.fullmatch(a) for a in own)):
        problems.append("mail.self_addresses needs one or more email addresses")
    elif not (isinstance(mail, Mapping) and isinstance(mail.get("self_test_address"), str)
              and mail["self_test_address"].lower() in {a.lower() for a in own}):
        problems.append("mail.self_test_address is required and must be one of mail.self_addresses")
    social = config.get("social")
    platforms = social.get("platforms") if isinstance(social, Mapping) else None
    if not (isinstance(platforms, list)
            and all(isinstance(p, str) and p.strip() and p == p.strip().lower() for p in platforms)):
        problems.append("social.platforms must be a list of lower-case platform names")
    if not (isinstance(social, Mapping) and isinstance(social.get("accounts_file"), str)
            and str(social.get("api_base", "")).startswith("https://")):
        problems.append("social needs accounts_file and an https api_base")
    switch = config.get("kill_switch")
    if not (isinstance(switch, Mapping) and isinstance(switch.get("signal_kind"), str)
            and switch["signal_kind"].strip()
            and _BARE_NAME.fullmatch(str(switch.get("stop_file", "")))):
        problems.append("kill_switch needs signal_kind and a bare stop_file name")
    for name in ("receipts_file", "ledger_file"):
        if not _BARE_NAME.fullmatch(str(config.get(name, ""))):
            problems.append(f"{name} must be a bare file name inside state_dir")
    state_dir = config.get("state_dir")
    if state_dir is not None and not (isinstance(state_dir, str)
                                      and Path(state_dir).expanduser().is_absolute()):
        problems.append("state_dir must be an absolute path or start with ~")
    return "; ".join(problems) or None


def resolve_state_dir(config: Mapping[str, Any], override: Path | str | None = None) -> Path:
    """The machine-wide state directory: an explicit argument, else the
    CARR_SYSTEM_SENDER_STATE_DIR environment variable, else config state_dir,
    else ~/carr-local/system-sender. Never inside the repo by default."""
    chosen = override or os.environ.get(STATE_DIR_ENV) or config.get("state_dir")
    if not isinstance(chosen, (str, Path)) or not str(chosen):
        chosen = DEFAULT_STATE_DIR
    return Path(chosen).expanduser()


def _bare(value: Any, default: str) -> str:
    return value if isinstance(value, str) and _BARE_NAME.fullmatch(value) else default


# ── the dedupe ledger ───────────────────────────────────────────────────────

_State = dict[str, dict[str, dict[str, str]]]


class _Ledger:
    """One JSON object of claimed and sent keys plus today's cap slots, changed
    only under an exclusive lock and replaced atomically, so two senders cannot
    both claim one key or both take the last slot, and a crash mid-write never
    leaves half a ledger.

    keys:  dedupe key -> {state claimed|sent, attempt_id, channel, at}
    slots: attempt_id -> {channel, day, mode live|shadow, state, key}; state
           reserved (claimed, in flight or held) | sent | would_send."""

    def __init__(self, path: Path, ensure_dir: Callable[[], None]) -> None:
        self.path = path
        self._ensure_dir = ensure_dir

    @contextlib.contextmanager
    def _locked(self) -> Iterator[_State]:
        self._ensure_dir()
        with open(self.path.with_suffix(".lock"), "a", encoding="utf-8") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            state = self._read()
            before = json.dumps(state, sort_keys=True)
            yield state
            if json.dumps(state, sort_keys=True) != before:
                self._write(state)

    def _read(self) -> _State:
        try:
            raw = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return {"keys": {}, "slots": {}}
        except OSError as exc:
            raise LedgerError(f"ledger unreadable ({type(exc).__name__})") from exc
        try:
            data = json.loads(raw)
        except ValueError as exc:
            raise LedgerError("ledger is not JSON") from exc
        keys = data.get("keys") if isinstance(data, dict) else None
        if not isinstance(keys, dict):
            raise LedgerError("ledger has no keys object")
        slots = data.get("slots", {})      # a v1 ledger has keys only
        if not isinstance(slots, dict):
            raise LedgerError("ledger slots is not an object")
        return {"keys": keys, "slots": slots}

    def _write(self, state: _State) -> None:
        fd, tmp = tempfile.mkstemp(dir=str(self.path.parent), prefix=".ledger-")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump({"schema": "carr-system-sender-ledger/v2", **state}, fh,
                      indent=1, sort_keys=True)
            fh.write("\n")
        os.replace(tmp, self.path)

    @staticmethod
    def _used(slots: Mapping[str, Mapping[str, str]], channel: str, day: str, mode: str) -> int:
        return sum(1 for v in slots.values() if v.get("channel") == channel and v.get("day") == day
                   and v.get("mode") == mode and v.get("state") in _SLOT_COUNTS)

    def claim(self, key: str, attempt: _Attempt, at: dt.datetime, *, day: str, mode: str,
              cap: int) -> tuple[str, int]:
        """("claimed" | "duplicate" | "capped", slots used before this one).
        The dedupe check, the cap count and the reservation are one locked step."""
        with self._locked() as state:
            keys, slots = state["keys"], state["slots"]
            if key in keys:
                return "duplicate", 0
            used = self._used(slots, attempt.channel, day, mode)
            if used >= cap:
                return "capped", used
            keys[key] = {"state": "claimed", "attempt_id": attempt.attempt_id,
                         "channel": attempt.channel, "at": at.isoformat()}
            oldest = (dt.date.fromisoformat(day) - dt.timedelta(days=_SLOT_KEEP_DAYS)).isoformat()
            for stale in [a for a, v in slots.items() if str(v.get("day", "")) < oldest]:
                del slots[stale]
            slots[attempt.attempt_id] = {"channel": attempt.channel, "day": day, "mode": mode,
                                         "state": "reserved", "key": key}
            return "claimed", used

    def release(self, key: str, attempt_id: str) -> None:
        """A definite non-send: the key and its cap slot are both freed."""
        with self._locked() as state:
            if state["keys"].get(key, {}).get("attempt_id") == attempt_id:
                del state["keys"][key]
            state["slots"].pop(attempt_id, None)

    def rehearsed(self, key: str, attempt_id: str) -> None:
        """A shadow would_send: the key is freed (nothing left), the slot keeps
        counting against the shadow cap."""
        with self._locked() as state:
            if state["keys"].get(key, {}).get("attempt_id") == attempt_id:
                del state["keys"][key]
            if attempt_id in state["slots"]:
                state["slots"][attempt_id]["state"] = "would_send"

    def settle(self, key: str, attempt_id: str | None, at: dt.datetime) -> bool:
        with self._locked() as state:
            entry = state["keys"].get(key)
            if entry is None or (attempt_id is not None and entry.get("attempt_id") != attempt_id):
                return False
            entry.update(state="sent", at=at.isoformat())
            slot = state["slots"].get(str(entry.get("attempt_id")))
            if slot is not None:
                slot["state"] = "sent"
            return True

    def used(self, channel: str, day: str, mode: str) -> int:
        # A plain read, no lock: writes replace the file atomically, and a
        # health read must not create state files as a side effect.
        return self._used(self._read()["slots"], channel, day, mode)

    def held(self) -> list[tuple[str, dict[str, str]]]:
        return [(k, dict(v)) for k, v in sorted(self._read()["keys"].items())
                if v.get("state") == "claimed"]


# ── the sender ──────────────────────────────────────────────────────────────

class Sender:
    def __init__(self, *, config: Mapping[str, Any] | None = None, root: Path = REPO,
                 state_dir: Path | str | None = None,
                 verb: VerbDoor | None = None, lint: Linter | None = None,
                 outlook: OutlookRunner | None = None, blotato_http: BlotatoHttp | None = None,
                 blotato_key: KeySource | None = None, now: Clock | None = None) -> None:
        unreadable: str | None = None
        try:
            loaded: Any = dict(config) if config is not None else load_config()
        except (OSError, ValueError, TypeError) as exc:
            loaded, unreadable = {}, f"config unreadable ({type(exc).__name__})"
        self.config: dict[str, Any] = loaded if isinstance(loaded, dict) else {}
        # Validated whole, here, before any send can claim anything (finding 5).
        self.config_error = unreadable or validate_config(loaded)
        self.root = Path(root)
        self._verb = verb or _default_verb
        self._lint = lint or run_lint
        self._outlook = outlook or self._default_outlook
        self._http = blotato_http or _blotato_post
        self._key = blotato_key or _blotato_key
        self._now = now or _utc_now
        try:
            self.tz: dt.tzinfo = ZoneInfo(str(self.config.get("timezone")))
        except (ValueError, KeyError, OSError):
            self.tz = ZoneInfo("America/Chicago")   # receipts only: the config already refuses
        try:
            self._live_start = _parse_live_from(self.config.get("live_from"), self.tz)
        except (TypeError, ValueError):
            self._live_start = None                 # shadow: the config already refuses
        self.state_dir = resolve_state_dir(self.config, state_dir)
        self.receipts = self.state_dir / _bare(self.config.get("receipts_file"), "sends.jsonl")
        switch = self.config.get("kill_switch")
        self.stop_file = self.state_dir / _bare(
            switch.get("stop_file") if isinstance(switch, Mapping) else None, "system-send.stop")
        self.ledger = _Ledger(self.state_dir / _bare(self.config.get("ledger_file"),
                                                     "system-sender-ledger.json"),
                              self._ensure_state_dir)

    def _default_outlook(self, url: str, subject: str) -> str:
        # The real transport, writing its single-use send token into THIS
        # sender's state directory.
        return _outlook_send(url, subject, state_dir=self.state_dir)

    def _ensure_state_dir(self) -> None:
        """Create the machine-wide state directory mode 700 on first use. Only
        writes call this; reads (health, kill switch) never create it."""
        if not self.state_dir.is_dir():
            self.state_dir.mkdir(parents=True, mode=0o700, exist_ok=True)
            os.chmod(self.state_dir, 0o700)

    # ── public interface ──

    def send_mail(self, *, to: Sequence[str] | str, subject: str, body: str,
                  deal_id: str | None = None, follow_up: Mapping[str, str] | None = None,
                  dedupe_key: str | None = None) -> SendResult:
        recipients = sorted({r.strip().lower() for r in ([to] if isinstance(to, str) else to)
                             if r and r.strip()})
        text = f"{subject}\n\n{body}"
        attempt = _Attempt(channel="mail", target=",".join(recipients), subject=subject,
                           text_sha256=_sha256(text),
                           dedupe_key=dedupe_key or _sha256(json.dumps(
                               ["mail", self._today(), recipients, subject, body])))
        return self._run(attempt, text, "email",
                         lambda: self._allow_mail(attempt, recipients, subject, deal_id, follow_up),
                         lambda: self._deliver_mail(recipients, subject, body))

    def send_self_test(self, *, subject: str, body: str,
                       dedupe_key: str | None = None) -> SendResult:
        """A mail to Joe's own address (config mail.self_test_address) through
        the WHOLE chain. The sender shipped live over a transport that had never
        pressed Send; this is the first live send, so the transport is proven
        on a self-send before any participant mail."""
        mail = self.config.get("mail")
        address = mail.get("self_test_address") if isinstance(mail, Mapping) else None
        # No fallback address: validate_config requires self_test_address to be
        # one of mail.self_addresses, and a config without one refuses this
        # call as config_invalid inside send_mail.
        return self.send_mail(to=[str(address or "")], subject=subject, body=body,
                              dedupe_key=dedupe_key)

    def send_social(self, *, platform: str, text: str, media_urls: Sequence[str] = (),
                    dedupe_key: str | None = None) -> SendResult:
        platform = platform.strip().lower()
        media = [u for u in media_urls if u]
        attempt = _Attempt(channel="social", target=platform, text_sha256=_sha256(text),
                           dedupe_key=dedupe_key or _sha256(json.dumps(
                               ["social", self._today(), platform, text, media])))
        return self._run(attempt, text, "social",
                         lambda: self._allow_social(attempt, platform),
                         lambda: self._deliver_social(attempt, platform, text, media))

    def is_live(self, at: dt.datetime | None = None) -> bool:
        if self._live_start is None:
            return False
        return (at or self._now()) >= self._live_start

    def kill_switch(self) -> KillSwitch:
        spec = self.config["kill_switch"]
        try:
            if self.stop_file.exists():
                return KillSwitch("engaged", f"stop file {self.stop_file}")
        except OSError as exc:
            return KillSwitch("unreadable", f"stop file ({type(exc).__name__})")
        for status in ("open", "claimed"):
            result = self._verb("next-signals", {"status": status, "signal_kind": spec["signal_kind"],
                                                 "limit": 1})
            count = result.reply.get("count") if result.ok and isinstance(result.reply, dict) else None
            if not isinstance(count, int):
                return KillSwitch("unreadable", f"next-signals {status}: {result.describe()}")
            if count > 0:
                return KillSwitch("engaged", f"{status} {spec['signal_kind']} signal")
        return KillSwitch("clear")

    def sends_today(self, channel: str, *, live: bool | None = None) -> int:
        """Today's reserved cap slots for one channel, in the config's timezone,
        read from the ledger the cap is enforced in. Raises LedgerError or
        OSError when the ledger exists and cannot be read."""
        now = self._now()
        live = self.is_live(now) if live is None else live
        return self.ledger.used(channel, self._today(now), "live" if live else "shadow")

    def held_claims(self) -> list[tuple[str, dict[str, str]]]:
        return self.ledger.held()

    def _today(self, at: dt.datetime | None = None) -> str:
        return (at or self._now()).astimezone(self.tz).date().isoformat()

    # ── the chain ──

    def _run(self, attempt: _Attempt, text: str, surface: str,
             allow: Callable[[], "tuple[str, str] | None"],
             deliver: Callable[[], _Delivery]) -> SendResult:
        if self.config_error:
            return self._finish(attempt, REFUSED, "config_invalid", self.config_error)
        switch = self.kill_switch()
        if switch.state != "clear":
            return self._finish(attempt, REFUSED, f"kill_switch_{switch.state}", switch.detail)
        clean, lint_detail = self._lint(text, surface)
        if not clean:
            return self._finish(attempt, REFUSED, "lint", lint_detail)
        verdict = allow()
        if verdict is not None:
            return self._finish(attempt, REFUSED, *verdict)

        now = self._now()
        key = attempt.dedupe_key
        live = self.is_live(now)
        cap = int(self.config["daily_caps"][attempt.channel])
        try:
            claimed, used = self.ledger.claim(key, attempt, now, day=self._today(now),
                                              mode="live" if live else "shadow", cap=cap)
        except (LedgerError, OSError) as exc:
            return self._finish(attempt, REFUSED, "ledger_unreadable", str(exc))
        if claimed == "duplicate":
            return self._finish(attempt, REFUSED, "duplicate", "dedupe key already held or sent")
        if claimed == "capped":
            return self._finish(attempt, REFUSED, "daily_cap", f"{used}/{cap} today")
        if not live:
            with contextlib.suppress(LedgerError, OSError):
                self.ledger.rehearsed(key, attempt.attempt_id)
            return self._finish(attempt, WOULD_SEND, "shadow",
                                f"live_from {self.config.get('live_from')}")

        try:
            delivery = deliver()
        except TransportNotSent as exc:
            delivery = _Delivery("not_sent", str(exc), reason=exc.reason)
        except Exception as exc:   # a transport that raised may have sent: hold the key
            delivery = _Delivery("unknown", f"transport raised {type(exc).__name__}")
        if delivery.state == "sent":
            detail = delivery.detail
            try:
                self.ledger.settle(key, attempt.attempt_id, self._now())
            except (LedgerError, OSError) as exc:
                # The message went. The receipt must still be written; the key
                # stays claimed, which refuses a resend: the safe side.
                detail += f"; ledger settle failed ({type(exc).__name__}), key left claimed"
            return self._finish(attempt, SENT, "sent", detail, ref=delivery.ref)
        if delivery.state == "not_sent":
            self._release(key, attempt)
            return self._finish(attempt, FAILED, delivery.reason, delivery.detail)
        return self._finish(attempt, UNKNOWN, "transport_ambiguous", delivery.detail)

    def _release(self, key: str, attempt: _Attempt) -> None:
        """Release after a definite non-send. A ledger that broke since the
        claim leaves the key held, which can only refuse a later resend."""
        with contextlib.suppress(LedgerError, OSError):
            self.ledger.release(key, attempt.attempt_id)

    # ── (c) allowlists ──

    def _allow_mail(self, attempt: _Attempt, recipients: list[str], subject: str,
                    deal_id: str | None,
                    follow_up: Mapping[str, str] | None) -> tuple[str, str] | None:
        if not subject.strip():
            # The transport finds the compose window by its exact title; an
            # empty title would identify no window of its own.
            return "empty_subject", "a mail needs a subject"
        if not recipients:
            return "no_recipient", ""
        malformed = [r for r in recipients if not _EMAIL.fullmatch(r)]
        if malformed:
            return "recipient_malformed", f"{len(malformed)} malformed address(es)"
        own = {a.lower() for a in self.config["mail"]["self_addresses"]}
        outside = [r for r in recipients if r not in own]
        if not outside:
            return None
        if not deal_id:
            return "recipient_not_allowed", (
                f"{len(outside)} recipient(s) are not a partner address and no deal was named")
        result = self._verb("get-call-context", {"deal_ids": [deal_id]})
        deals = result.reply.get("deals") if result.ok and isinstance(result.reply, dict) else None
        if not isinstance(deals, list):
            return "deal_context_unreadable", result.describe()
        deal = next((d for d in deals if isinstance(d, dict) and str(d.get("id")) == deal_id), None)
        if deal is None:
            return "deal_context_unreadable", "get-call-context did not return the named deal"
        participants = {str(p.get("email")).strip().lower() for p in deal.get("participants") or []
                        if isinstance(p, dict) and p.get("email")}
        strangers = [r for r in outside if r not in participants]
        if strangers:
            return "recipient_not_allowed", (
                f"{len(strangers)} recipient(s) are not a participant on deal {deal_id}")
        follow = dict(follow_up or {})
        if not (_DATE.fullmatch(str(follow.get("next_on", ""))) and str(follow.get("next_step", "")).strip()):
            return "follow_up_required", (
                "a send to a deal participant is logged through log-outreach, which needs "
                "follow_up next_on (YYYY-MM-DD) and next_step")
        attempt.deal = {"id": deal_id, "name": str(deal.get("name") or deal_id)}
        attempt.follow_up = {"next_on": str(follow["next_on"]), "next_step": str(follow["next_step"])}
        return None

    def _allow_social(self, attempt: _Attempt, platform: str) -> tuple[str, str] | None:
        social = self.config["social"]
        if platform not in social["platforms"]:
            return "platform_not_allowed", f"{platform!r} is not in the sender config"
        try:
            accounts = json.loads((self.root / social["accounts_file"]).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            return "accounts_unreadable", type(exc).__name__
        account = accounts.get(platform) if isinstance(accounts, dict) else None
        if not isinstance(account, dict) or not str(account.get("accountId") or "").strip():
            return "platform_not_allowed", f"{platform!r} has no accountId in the accounts file"
        if platform == "facebook" and not str(account.get("pageId") or "").strip():
            return "platform_not_allowed", "facebook has no pageId in the accounts file"
        attempt.platform_account = {k: str(v) for k, v in account.items()}
        return None

    # ── (f) the final send ──

    def _deliver_mail(self, recipients: list[str], subject: str, body: str) -> _Delivery:
        try:
            url = _mailto_builder()({"to": recipients, "subject": subject, "body": body})
        except (Exception, SystemExit) as exc:   # build_url refuses with SystemExit
            return _Delivery("not_sent", f"bin/outlook-draft.py build_url failed ({type(exc).__name__})",
                             reason="mailto_builder_unavailable")
        if len(url) > MAILTO_LIMIT:
            return _Delivery("not_sent", f"mailto URL {len(url)} chars exceeds {MAILTO_LIMIT}")
        # The FULL subject: the script matches the compose window by exact title.
        answer = self._outlook(url, subject)
        if answer == "SENT":
            return _Delivery("sent", "outlook compose window closed after Send")
        # Every answer below was given before the Send keystroke: definite non-sends.
        if answer.startswith("ERROR: open failed"):
            return _Delivery("not_sent", answer[:160], reason="outlook_open_failed")
        if answer.startswith("NOT_FRONTMOST:"):
            return _Delivery("not_sent", answer[:160], reason="outlook_not_frontmost")
        if answer.startswith("REFUSED:"):
            return _Delivery("not_sent", answer[:160], reason="compose_window_refused")
        if answer.startswith("ERROR:"):
            return _Delivery("not_sent", answer[:160])
        # NOT_SENT (pressed, window still open), AMBIGUOUS, or anything else.
        return _Delivery("unknown", answer[:160] or "no answer from outlook-send")

    def _deliver_social(self, attempt: _Attempt, platform: str, text: str,
                        media: list[str]) -> _Delivery:
        try:
            key = self._key()
        except Exception as exc:
            return _Delivery("not_sent", f"blotato key lookup failed ({type(exc).__name__})",
                             reason="blotato_key_unavailable")
        if not key:
            return _Delivery("not_sent", "blotato key not available")
        account = attempt.platform_account or {}
        target: dict[str, str] = {"targetType": platform}
        if platform in ("linkedin", "facebook") and account.get("pageId"):
            target["pageId"] = account["pageId"]
        post = {"post": {"accountId": account["accountId"],
                         "content": {"platform": platform, "text": text, "mediaUrls": media},
                         "target": target}}
        url = str(self.config["social"]["api_base"]).rstrip("/") + "/posts"
        status, reply = self._http(url, {"Content-Type": "application/json",
                                         "blotato-api-key": key},
                                   json.dumps(post).encode("utf-8"))
        submission = reply.get("postSubmissionId") if isinstance(reply, dict) else None
        if 200 <= status < 300 and submission:
            return _Delivery("sent", f"HTTP {status}", ref=str(submission))
        if 400 <= status < 500:
            return _Delivery("not_sent", f"HTTP {status}")
        return _Delivery("unknown", f"HTTP {status}")

    # ── (g) receipts ──

    def _finish(self, attempt: _Attempt, outcome: str, reason: str, detail: str = "",
                ref: str = "") -> SendResult:
        at = self._now()
        row = {"kind": "attempt", "attempt_id": attempt.attempt_id, "at": at.isoformat(),
               "channel": attempt.channel, "target": attempt.target, "mode":
               "live" if self.is_live(at) else "shadow", "outcome": outcome, "reason": reason,
               "detail": detail, "dedupe_key": attempt.dedupe_key, "subject": attempt.subject,
               "text_sha256": attempt.text_sha256, "deal_id": (attempt.deal or {}).get("id"),
               "ref": ref}
        self._append(row)
        verb, args = self._record_call(attempt, row)
        result = self._verb(verb, args)
        self._append({"kind": "record", "attempt_id": attempt.attempt_id, "verb": verb,
                      "result": result.kind, "detail": result.detail[:160]})
        return SendResult(outcome, reason, attempt.attempt_id, attempt.dedupe_key, detail)

    def _record_call(self, attempt: _Attempt, row: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
        if row["outcome"] == SENT and attempt.channel == "mail" and attempt.deal and attempt.follow_up:
            return "log-outreach", {
                "idempotency_key": attempt.attempt_id, "ref": attempt.deal["id"],
                "channel": "email", "outcome": "sent", "occurred_at": row["at"],
                "summary": f"System sender emailed {attempt.target}: {attempt.subject}",
                "next_on": attempt.follow_up["next_on"], "next_step": attempt.follow_up["next_step"],
                "detail": f"system-sender attempt {attempt.attempt_id}; deal {attempt.deal['name']}"}
        return "record-signal", {
            "idempotency_key": attempt.attempt_id, "producer": "system-sender",
            "signal_key": f"send:{attempt.attempt_id}", "signal_kind": "system_send",
            "subject_type": "system_send",
            "subject_ref": attempt.channel if attempt.channel == "mail" else f"social:{attempt.target}",
            "metric_name": "send_attempt", "observed_value": 1, "threshold_value": 1,
            "comparison": "gte", "severity": "info", "detected_at": row["at"],
            "evidence_refs": [f"{self.receipts}#{attempt.attempt_id}"],
            "payload": {k: row[k] for k in ("outcome", "reason", "mode", "target", "deal_id",
                                             "dedupe_key", "ref")}}

    def _append(self, row: Mapping[str, Any]) -> None:
        self._ensure_state_dir()
        with open(self.receipts, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, sort_keys=True) + "\n")


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ── module-level doors ──────────────────────────────────────────────────────

def send_mail(**kwargs: Any) -> SendResult:
    return Sender().send_mail(**kwargs)


def send_social(**kwargs: Any) -> SendResult:
    return Sender().send_social(**kwargs)


def send_self_test(subject: str, body: str, **kwargs: Any) -> SendResult:
    """The first live send: a mail to Joe's own address through the whole chain."""
    return Sender().send_self_test(subject=subject, body=body, **kwargs)


def resolve_claim(dedupe_key: str, *, sent: bool, state_dir: Path | str | None = None) -> bool:
    """Settle a key left held by a crash or an ambiguous send, after checking
    Outlook's Sent folder or Blotato: sent=True keeps it as sent (no resend),
    sent=False releases it and its cap slot (the message may be sent again)."""
    sender = Sender(state_dir=state_dir)
    held = dict(sender.held_claims())
    if dedupe_key not in held:
        return False
    if sent:
        return sender.ledger.settle(dedupe_key, None, _utc_now())
    sender.ledger.release(dedupe_key, held[dedupe_key]["attempt_id"])
    return True


def health_row(*, sender: Sender | None = None) -> tuple[str, bool, bool]:
    """(line, breach, hard_error) for tools/health-check.py's `sends` row.
    Breach: a cap reached, a held claim, or a kill switch that cannot be read.
    Hard error: the config is invalid, or the ledger cannot be read at all.
    State is the machine-wide state_dir, so the row is per MACHINE."""
    s = sender or Sender()
    on_breach = ("on breach: owner orchestrator · cap hit: the sender refuses that channel until "
                 f"midnight Central, read {s.receipts} for a runaway caller · held claim: check "
                 "Outlook Sent or Blotato for that key, then lib.system_sender.resolve_claim · "
                 "kill switch unreadable: restore the next-signals read, sends stay refused meanwhile "
                 "· config invalid: fix ops/config/system-sender.v1.json, every send is refused meanwhile "
                 "· verify: run.sh health --section sends · auto-clear: next local day or after resolve")
    if s.config_error:
        return (f"UNKNOWN system sends — config invalid ({s.config_error}) · " + on_breach, True, True)
    caps = s.config["daily_caps"]
    live = s.is_live()
    mode = "LIVE" if live else f"shadow (live_from {s.config.get('live_from') or 'unset'})"
    word = "sent" if live else "would_send"
    try:
        counts = {ch: s.sends_today(ch, live=live) for ch in ("mail", "social")}
        held = len(s.held_claims())
    except (OSError, LedgerError) as exc:
        return (f"UNKNOWN system sends — ledger unreadable ({type(exc).__name__}) · "
                + on_breach, True, True)
    switch = s.kill_switch()
    capped = [ch for ch in counts if counts[ch] >= int(caps[ch])]
    breach = bool(capped) or held > 0 or switch.state == "unreadable"
    switch_text = switch.state + (f" ({switch.detail})" if switch.detail else "")
    line = (f"{'WARN' if breach else 'OK'} system sends — {mode} · mail {counts['mail']}/{caps['mail']} "
            f"{word} · social {counts['social']}/{caps['social']} {word} · kill switch {switch_text} "
            f"· held claims {held} · " + on_breach)
    return line, breach, False
