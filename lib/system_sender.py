"""system_sender — the system's own outbound sends, behind one policy chain.

Joe ruled on 2026-10-12 (decision cbe91287-10a2-48d0-bda7-3b1784b4bb93) that
the system's own outbound sends must not wait for his hand, and rule c66dc739
was amended the same day: sending is no longer a human gate. This module is
what replaces the human gate. Two calls, one chain:

    send_mail(to=[...], subject=..., body=..., deal_id=None, follow_up=None)
    send_social(platform=..., text=..., media_urls=())

Every call runs the SAME chain, in this order, before anything leaves:

  a. kill switch   the stop file under out/, or an open or claimed record-layer
                   signal of kind system_send_stop (read through next-signals).
                   A read that fails refuses: an unreadable switch is not "off".
  b. writing lint  `run.sh lint <file> --surface email|social`. ANY finding
                   refuses, REVIEW and RATION included, because no human reads
                   the draft after this point.
  c. allowlist     mail: Joe's or Dell's own address, or an exact participant
                   email on the named deal (get-call-context, read live). A
                   participant send also needs follow_up {next_on, next_step},
                   because log-outreach refuses a sent touch with no next step.
                   Social: a platform in the config that also has an accountId
                   in claude-tree/.../blotato-accounts.json.
  d. dedupe        a key claimed in out/system-sender-ledger.json BEFORE the
                   send and released only on a DEFINITE non-send. A crash or an
                   ambiguous transport answer leaves it held, so a retry is
                   refused instead of sending twice (resolve_claim settles it).
  e. daily caps    from config (20 mail, 5 social), counted per local day in
                   the config's timezone from out/sends.jsonl.
  f. shadow mode   while live_from is null or in the future, the call does all
                   of the above, records would_send, and sends nothing.
  g. receipts      every attempt, refusals included, appends one line to
                   out/sends.jsonl, then reaches the record layer: a live mail
                   to a deal participant through log-outreach, everything else
                   through record-signal at severity info.

Outcomes (SendResult.outcome): sent · would_send · refused (the chain said no)
· failed (the transport said no, definitely) · send_unknown (the transport
may have sent; the dedupe key stays held).

Seams, all injectable through Sender(...) and all defaulted to the real thing:
`verb` (record-layer door, default `./run.sh call` via lib.record_call),
`lint`, `outlook` (open the mailto compose window and run
bin/outlook-send.applescript), `blotato_http`, `blotato_key`, `now`, and `root`
(where out/ lives). This is a library: no shebang and no main, so it adds no
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
import subprocess
import tempfile
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

SENT, WOULD_SEND, REFUSED, FAILED, UNKNOWN = (
    "sent", "would_send", "refused", "failed", "send_unknown")
# What a cap counts. Live: anything that did or may have gone out. Shadow: the
# rehearsal of the same, so a shadow week shows where the cap would bite.
_CAP_COUNTS = {True: (SENT, UNKNOWN), False: (WOULD_SEND,)}

# bin/outlook-draft.py's ceiling for the mailto route: a silently truncated
# message is worse than a refusal.
MAILTO_LIMIT = 1900
# Outlook titles a compose window "<subject> • <account>" and truncates a long
# subject in the title bar; match on a prefix short enough to survive that.
TITLE_MATCH_LEN = 40

_EMAIL = re.compile(r"[^@\s,;<>]+@[^@\s,;<>]+\.[A-Za-z]{2,}")
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_LINT_COUNTS = re.compile(r"(\d+) HARD, (\d+) REVIEW, (\d+) RATION")

VerbDoor = Callable[[str, Mapping[str, Any]], VerbResult]
Linter = Callable[[str, str], "tuple[bool, str]"]
OutlookRunner = Callable[[str, str], str]
BlotatoHttp = Callable[[str, Mapping[str, str], bytes], "tuple[int, Any]"]
KeySource = Callable[[], "str | None"]
Clock = Callable[[], dt.datetime]


@dataclass(frozen=True)
class SendResult:
    outcome: str
    reason: str
    attempt_id: str
    dedupe_key: str
    detail: str = ""

    @property
    def sent(self) -> bool:
        return self.outcome == SENT


@dataclass(frozen=True)
class KillSwitch:
    state: str          # clear | engaged | unreadable
    detail: str = ""


@dataclass(frozen=True)
class _Delivery:
    state: str          # sent | not_sent | unknown
    detail: str = ""
    ref: str = ""


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


# ── default adapters ────────────────────────────────────────────────────────

def _default_verb(verb: str, args: Mapping[str, Any]) -> VerbResult:
    return call_verb(verb, args, timeout=60)


def run_lint(text: str, surface: str) -> tuple[bool, str]:
    """The existing writing-lint door, `run.sh lint <file> --surface <s>`.
    Clean means exit 0 AND zero findings of every severity."""
    fd, path = tempfile.mkstemp(prefix="system-sender-", suffix=".txt")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        proc = subprocess.run([str(REPO / "run.sh"), "lint", path, "--surface", surface],
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


def _outlook_send(url: str, title_prefix: str) -> str:
    """Open the compose window in the signed-in Outlook app (never a bare
    `open mailto:`, which lands in Joe's personal default handler) and press
    Send through bin/outlook-send.applescript. Returns the script's answer."""
    subprocess.run(["open", "-a", "Microsoft Outlook", url], check=False,
                   capture_output=True, timeout=30)
    proc = subprocess.run(["osascript", str(SEND_SCRIPT), title_prefix],
                          capture_output=True, text=True, timeout=120)
    return (proc.stdout or proc.stderr or "").strip()


def _blotato_post(url: str, headers: Mapping[str, str], body: bytes) -> tuple[int, Any]:
    request = urllib.request.Request(url, data=body, method="POST", headers=dict(headers))
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            status = response.status
            raw = response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, None
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


# ── the dedupe ledger ───────────────────────────────────────────────────────

class _Ledger:
    """One JSON object of claimed and sent keys, changed only under an
    exclusive lock and replaced atomically, so two senders cannot both claim
    one key and a crash mid-write never leaves half a ledger."""

    def __init__(self, path: Path) -> None:
        self.path = path

    @contextlib.contextmanager
    def _locked(self) -> Iterator[dict[str, dict[str, str]]]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path.with_suffix(".lock"), "a", encoding="utf-8") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            keys = self._read()
            before = json.dumps(keys, sort_keys=True)
            yield keys
            if json.dumps(keys, sort_keys=True) != before:
                self._write(keys)

    def _read(self) -> dict[str, dict[str, str]]:
        try:
            raw = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return {}
        except OSError as exc:
            raise LedgerError(f"ledger unreadable ({type(exc).__name__})") from exc
        try:
            data = json.loads(raw)
        except ValueError as exc:
            raise LedgerError("ledger is not JSON") from exc
        keys = data.get("keys") if isinstance(data, dict) else None
        if not isinstance(keys, dict):
            raise LedgerError("ledger has no keys object")
        return keys

    def _write(self, keys: dict[str, dict[str, str]]) -> None:
        fd, tmp = tempfile.mkstemp(dir=str(self.path.parent), prefix=".ledger-")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump({"schema": "carr-system-sender-ledger/v1", "keys": keys}, fh,
                      indent=1, sort_keys=True)
            fh.write("\n")
        os.replace(tmp, self.path)

    def claim(self, key: str, attempt: _Attempt, at: dt.datetime) -> bool:
        with self._locked() as keys:
            if key in keys:
                return False
            keys[key] = {"state": "claimed", "attempt_id": attempt.attempt_id,
                         "channel": attempt.channel, "at": at.isoformat()}
            return True

    def release(self, key: str, attempt_id: str) -> None:
        with self._locked() as keys:
            if keys.get(key, {}).get("attempt_id") == attempt_id:
                del keys[key]

    def settle(self, key: str, attempt_id: str | None, at: dt.datetime) -> bool:
        with self._locked() as keys:
            entry = keys.get(key)
            if entry is None or (attempt_id is not None and entry.get("attempt_id") != attempt_id):
                return False
            entry.update(state="sent", at=at.isoformat())
            return True

    def held(self) -> list[tuple[str, dict[str, str]]]:
        # A plain read, no lock: writes replace the file atomically, and a
        # health read must not create out/ files as a side effect.
        return [(k, dict(v)) for k, v in sorted(self._read().items()) if v.get("state") == "claimed"]


# ── the sender ──────────────────────────────────────────────────────────────

class Sender:
    def __init__(self, *, config: Mapping[str, Any] | None = None, root: Path = REPO,
                 verb: VerbDoor | None = None, lint: Linter | None = None,
                 outlook: OutlookRunner | None = None, blotato_http: BlotatoHttp | None = None,
                 blotato_key: KeySource | None = None, now: Clock | None = None) -> None:
        self.config: dict[str, Any] = dict(config) if config is not None else load_config()
        self.root = Path(root)
        self._verb = verb or _default_verb
        self._lint = lint or run_lint
        self._outlook = outlook or _outlook_send
        self._http = blotato_http or _blotato_post
        self._key = blotato_key or _blotato_key
        self._now = now or _utc_now
        self.tz = ZoneInfo(self.config["timezone"])
        self.receipts = self.root / self.config["receipts_file"]
        self.ledger = _Ledger(self.root / self.config["ledger_file"])

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
                               ["mail", recipients, subject, body])))
        return self._run(attempt, text, "email",
                         lambda: self._allow_mail(attempt, recipients, deal_id, follow_up),
                         lambda: self._deliver_mail(recipients, subject, body))

    def send_social(self, *, platform: str, text: str, media_urls: Sequence[str] = (),
                    dedupe_key: str | None = None) -> SendResult:
        platform = platform.strip().lower()
        media = [u for u in media_urls if u]
        attempt = _Attempt(channel="social", target=platform, text_sha256=_sha256(text),
                           dedupe_key=dedupe_key or _sha256(json.dumps(
                               ["social", platform, text, media])))
        return self._run(attempt, text, "social",
                         lambda: self._allow_social(attempt, platform),
                         lambda: self._deliver_social(attempt, platform, text, media))

    def is_live(self, at: dt.datetime | None = None) -> bool:
        live_from = self.config.get("live_from")
        if not live_from:
            return False
        start = dt.datetime.fromisoformat(live_from)
        if start.tzinfo is None:
            return False      # an offset-less timestamp is ambiguous: stay in shadow
        return (at or self._now()) >= start

    def kill_switch(self) -> KillSwitch:
        spec = self.config["kill_switch"]
        try:
            if (self.root / spec["stop_file"]).exists():
                return KillSwitch("engaged", f"stop file {spec['stop_file']}")
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
        """Today's counted attempts for one channel, in the config's timezone.
        Raises OSError when the receipts file exists and cannot be read."""
        now = self._now()
        live = self.is_live(now) if live is None else live
        today = now.astimezone(self.tz).date()
        counted = _CAP_COUNTS[live]
        n = 0
        for row in self._receipt_rows():
            if row.get("kind") != "attempt" or row.get("channel") != channel:
                continue
            if row.get("outcome") not in counted:
                continue
            try:
                at = dt.datetime.fromisoformat(row["at"])
            except (KeyError, TypeError, ValueError):
                continue
            if at.astimezone(self.tz).date() == today:
                n += 1
        return n

    def held_claims(self) -> list[tuple[str, dict[str, str]]]:
        return self.ledger.held()

    # ── the chain ──

    def _run(self, attempt: _Attempt, text: str, surface: str,
             allow: Callable[[], "tuple[str, str] | None"],
             deliver: Callable[[], _Delivery]) -> SendResult:
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
        try:
            claimed = self.ledger.claim(key, attempt, now)
        except (LedgerError, OSError) as exc:
            return self._finish(attempt, REFUSED, "ledger_unreadable", str(exc))
        if not claimed:
            return self._finish(attempt, REFUSED, "duplicate", "dedupe key already held or sent")

        live = self.is_live(now)
        cap = int(self.config["daily_caps"][attempt.channel])
        try:
            used = self.sends_today(attempt.channel, live=live)
        except OSError as exc:
            self.ledger.release(key, attempt.attempt_id)
            return self._finish(attempt, REFUSED, "receipts_unreadable", type(exc).__name__)
        if used >= cap:
            self.ledger.release(key, attempt.attempt_id)
            return self._finish(attempt, REFUSED, "daily_cap", f"{used}/{cap} today")
        if not live:
            self.ledger.release(key, attempt.attempt_id)
            return self._finish(attempt, WOULD_SEND, "shadow",
                                f"live_from {self.config.get('live_from')}")

        try:
            delivery = deliver()
        except Exception as exc:   # a transport that raised may have sent: hold the key
            delivery = _Delivery("unknown", f"transport raised {type(exc).__name__}")
        if delivery.state == "sent":
            self.ledger.settle(key, attempt.attempt_id, self._now())
            return self._finish(attempt, SENT, "sent", delivery.detail, ref=delivery.ref)
        if delivery.state == "not_sent":
            self.ledger.release(key, attempt.attempt_id)
            return self._finish(attempt, FAILED, "transport_refused", delivery.detail)
        return self._finish(attempt, UNKNOWN, "transport_ambiguous", delivery.detail)

    # ── (c) allowlists ──

    def _allow_mail(self, attempt: _Attempt, recipients: list[str], deal_id: str | None,
                    follow_up: Mapping[str, str] | None) -> tuple[str, str] | None:
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
        url = _mailto_builder()({"to": recipients, "subject": subject, "body": body})
        if len(url) > MAILTO_LIMIT:
            return _Delivery("not_sent", f"mailto URL {len(url)} chars exceeds {MAILTO_LIMIT}")
        answer = self._outlook(url, subject[:TITLE_MATCH_LEN])
        if answer.startswith("SENT"):
            return _Delivery("sent", "outlook compose window closed after Send")
        if answer.startswith("ERROR: no compose window"):
            return _Delivery("not_sent", "no compose window; Send never pressed")
        return _Delivery("unknown", answer[:160] or "no answer from outlook-send")

    def _deliver_social(self, attempt: _Attempt, platform: str, text: str,
                        media: list[str]) -> _Delivery:
        key = self._key()
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
                "idempotency_key": attempt.attempt_id, "ref": attempt.deal["name"],
                "channel": "email", "outcome": "sent", "occurred_at": row["at"],
                "summary": f"System sender emailed {attempt.target}: {attempt.subject}",
                "next_on": attempt.follow_up["next_on"], "next_step": attempt.follow_up["next_step"],
                "detail": f"system-sender attempt {attempt.attempt_id}"}
        return "record-signal", {
            "idempotency_key": attempt.attempt_id, "producer": "system-sender",
            "signal_key": f"send:{attempt.attempt_id}", "signal_kind": "system_send",
            "subject_type": "system_send",
            "subject_ref": attempt.channel if attempt.channel == "mail" else f"social:{attempt.target}",
            "metric_name": "send_attempt", "observed_value": 1, "threshold_value": 1,
            "comparison": "gte", "severity": "info", "detected_at": row["at"],
            "evidence_refs": [f"{self.config['receipts_file']}#{attempt.attempt_id}"],
            "payload": {k: row[k] for k in ("outcome", "reason", "mode", "target", "deal_id",
                                             "dedupe_key", "ref")}}

    def _append(self, row: Mapping[str, Any]) -> None:
        self.receipts.parent.mkdir(parents=True, exist_ok=True)
        with open(self.receipts, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, sort_keys=True) + "\n")

    def _receipt_rows(self) -> Iterator[dict[str, Any]]:
        try:
            lines = self.receipts.read_text(encoding="utf-8").splitlines()
        except FileNotFoundError:
            return
        for line in lines:
            try:
                row = json.loads(line)
            except ValueError:
                continue      # a torn last line from a crash; the ledger still holds its key
            if isinstance(row, dict):
                yield row


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ── module-level doors ──────────────────────────────────────────────────────

def send_mail(**kwargs: Any) -> SendResult:
    return Sender().send_mail(**kwargs)


def send_social(**kwargs: Any) -> SendResult:
    return Sender().send_social(**kwargs)


def resolve_claim(dedupe_key: str, *, sent: bool, root: Path = REPO) -> bool:
    """Settle a key left held by a crash or an ambiguous send, after checking
    Outlook's Sent folder or Blotato: sent=True keeps it as sent (no resend),
    sent=False releases it (the message may be sent again)."""
    sender = Sender(root=root)
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
    Hard error: the receipts or the ledger cannot be read at all."""
    s = sender or Sender()
    caps = s.config["daily_caps"]
    live = s.is_live()
    mode = "LIVE" if live else f"shadow (live_from {s.config.get('live_from') or 'unset'})"
    word = "sent" if live else "would_send"
    on_breach = ("on breach: owner orchestrator · cap hit: the sender refuses that channel until "
                 "midnight Central, read out/sends.jsonl for a runaway caller · held claim: check "
                 "Outlook Sent or Blotato for that key, then lib.system_sender.resolve_claim · "
                 "kill switch unreadable: restore the next-signals read, sends stay refused meanwhile "
                 "· verify: run.sh health --section sends · auto-clear: next local day or after resolve")
    try:
        counts = {ch: s.sends_today(ch, live=live) for ch in ("mail", "social")}
        held = len(s.held_claims())
    except (OSError, LedgerError) as exc:
        return (f"UNKNOWN system sends — receipts or ledger unreadable ({type(exc).__name__}) · "
                + on_breach, True, True)
    switch = s.kill_switch()
    capped = [ch for ch in counts if counts[ch] >= int(caps[ch])]
    breach = bool(capped) or held > 0 or switch.state == "unreadable"
    switch_text = switch.state + (f" ({switch.detail})" if switch.detail else "")
    line = (f"{'WARN' if breach else 'OK'} system sends — {mode} · mail {counts['mail']}/{caps['mail']} "
            f"{word} · social {counts['social']}/{caps['social']} {word} · kill switch {switch_text} "
            f"· held claims {held} · " + on_breach)
    return line, breach, False
