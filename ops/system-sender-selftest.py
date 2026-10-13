#!/usr/bin/env python3
"""Selftest for lib/system_sender.py, the system's own outbound sender.

Hermetic: a fake Outlook runner (and a fake process runner under the real
_outlook_send, so the probe/open/send sequencing is exercised without
osascript), a fake Blotato HTTP call, a fake record-layer verb door, a fixed
clock and a temporary state_dir. Nothing here opens
Outlook, reaches Blotato, or calls the record layer. The one real collaborator
is the writing lint (tools/writing-lint.py through run.sh lint), which is
local, read-only and deterministic, and is exercised once on purpose so the
lint step is proved against the real door, not only a stub.

What it proves, one block per link of the chain: every refusal (kill switch by
stop file, open signal, claimed signal and unreadable read; lint; recipient and
platform allowlists; follow-up; cap; ledger), shadow mode, dedupe including a
crash mid-send, the definite-vs-ambiguous transport split, receipts on every
attempt, the record-layer routing (log-outreach vs record-signal), the health
row, and that the SHIPPED config is live (Joe, 2026-10-12) while a future
live_from still shadows.
"""
from __future__ import annotations

import datetime as dt
import fcntl
import json
import os
import shutil
import subprocess
import urllib.error
import sys
import tempfile
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from lib import system_sender as ss  # noqa: E402
from lib.record_call import VerbResult  # noqa: E402
from lib.selftest_harness import Checker  # noqa: E402

CHECK = Checker()
check = CHECK.check

T0 = dt.datetime(2026, 10, 13, 15, 0, tzinfo=dt.timezone.utc)   # 10:00 Central
DEAL = "11111111-2222-3333-4444-555555555555"
PARTICIPANT = "Dr.Lee@Example-Clinic.com"
JOE = "joe.bookout@carr.us"
ACCOUNTS = {"linkedin": {"accountId": "27007", "pageId": "27007"},
            "facebook": {"accountId": "39262", "pageId": "102825118368840"},
            "twitter": {"accountId": "21335"}}


class Clock:
    def __init__(self, at: dt.datetime) -> None:
        self.at = at

    def __call__(self) -> dt.datetime:
        return self.at


class Fakes:
    """Every seam, recording what it was asked."""

    def __init__(self) -> None:
        self.verb_calls: list[tuple[str, dict[str, Any]]] = []
        self.signals = {"open": 0, "claimed": 0}
        self.signal_read_ok = True
        self.call_context_ok = True
        self.lint_calls: list[tuple[str, str]] = []
        self.lint_clean = True
        self.outlook_calls: list[tuple[str, str]] = []
        self.outlook_answer: Any = "SENT"
        self.http_calls: list[tuple[str, dict[str, str], bytes]] = []
        self.http_answer: Any = (200, {"postSubmissionId": "sub-1"})
        self.key: str | None = "fixture-blotato-key"

    def verb(self, name: str, args: Any) -> VerbResult:
        self.verb_calls.append((name, dict(args)))
        if name == "next-signals":
            if not self.signal_read_ok:
                return VerbResult(name, "transient", detail="could not reach the deployed Worker")
            return VerbResult(name, "ok", reply={"ok": True, "count": self.signals[args["status"]],
                                                 "signals": []})
        if name == "get-call-context":
            if not self.call_context_ok:
                return VerbResult(name, "refused", reply={"error": "invalid_call_context_deals"})
            return VerbResult(name, "ok", reply={"deals": [{
                "id": DEAL, "name": "Lee Clinic Relocation", "participants": [
                    {"email": PARTICIPANT, "role": "client", "name": "Dr. Lee"}]}]})
        return VerbResult(name, "ok", reply={"ok": True})

    def lint(self, text: str, surface: str) -> tuple[bool, str]:
        self.lint_calls.append((text, surface))
        return self.lint_clean, "0 HARD, 0 REVIEW, 0 RATION" if self.lint_clean else "1 HARD, 0 REVIEW, 0 RATION"

    def outlook(self, url: str, title: str) -> str:
        self.outlook_calls.append((url, title))
        if isinstance(self.outlook_answer, BaseException):
            raise self.outlook_answer
        return str(self.outlook_answer)

    def http(self, url: str, headers: Any, body: bytes) -> tuple[int, Any]:
        self.http_calls.append((url, dict(headers), body))
        if isinstance(self.http_answer, BaseException):
            raise self.http_answer
        status, reply = self.http_answer
        return status, reply

    def verbs(self, name: str) -> list[dict[str, Any]]:
        return [a for n, a in self.verb_calls if n == name]


class Env:
    """A temp root with out/ and an accounts file, plus a Sender over fakes."""

    def __init__(self, *, live_from: str | None = "2026-10-01T00:00:00-05:00",
                 caps: dict[str, int] | None = None) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="system-sender-selftest-"))
        self.state = self.root / "state"
        accounts = self.root / "accounts.json"
        accounts.write_text(json.dumps(ACCOUNTS))
        self.config = ss.load_config()
        self.config.update(live_from=live_from, daily_caps=caps or {"mail": 20, "social": 5})
        self.config["social"] = {**self.config["social"], "accounts_file": str(accounts)}
        self.fakes = Fakes()
        self.clock = Clock(T0)

    def sender(self, *, lint: Any = None, **kw: Any) -> ss.Sender:
        f = self.fakes
        args: dict[str, Any] = {"config": self.config, "root": self.root, "state_dir": self.state,
                                "verb": f.verb, "lint": lint or f.lint, "outlook": f.outlook,
                                "blotato_http": f.http, "blotato_key": lambda: f.key,
                                "now": self.clock}
        args.update(kw)
        return ss.Sender(**args)

    def mail(self, **kw: Any) -> ss.SendResult:
        args: dict[str, Any] = {"to": [JOE], "subject": "Tour schedule", "body": "The tour is set for Tuesday."}
        args.update(kw)
        return self.sender().send_mail(**args)

    def social(self, **kw: Any) -> ss.SendResult:
        args: dict[str, Any] = {"platform": "linkedin", "text": "Three questions to ask before signing a clinic lease."}
        args.update(kw)
        return self.sender().send_social(**args)

    def receipts(self, kind: str = "attempt") -> list[dict[str, Any]]:
        path = self.state / "sends.jsonl"
        if not path.exists():
            return []
        rows = [json.loads(line) for line in path.read_text().splitlines()]
        return [r for r in rows if r["kind"] == kind]

    def held(self) -> list[str]:
        return [k for k, _ in self.sender().held_claims()]

    def close(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)


def scenario(fn):  # a crash inside one scenario is a failed check, never a stopped suite
    def run() -> None:
        print(f"\n{fn.__name__}")
        try:
            fn()
        except Exception as exc:
            check(f"{fn.__name__} ran to completion", False, repr(exc))
    run.__name__ = fn.__name__
    return run


# ── (f) shadow mode ──────────────────────────────────────────────────────────

@scenario
def test_shadow_mode_does_everything_but_send() -> None:
    for label, live_from in (("null", None), ("future", "2026-12-01T00:00:00-06:00"),
                             ("future date", "2026-10-14"), ("no offset", "2026-10-01T00:00:00")):
        env = Env(live_from=live_from)
        try:
            r = env.mail(to=[PARTICIPANT], deal_id=DEAL,
                         follow_up={"next_on": "2026-10-20", "next_step": "Confirm the tour"})
            s = env.social()
            f = env.fakes
            check(f"shadow ({label}): mail answers would_send", r.outcome == ss.WOULD_SEND, r)
            check(f"shadow ({label}): social answers would_send", s.outcome == ss.WOULD_SEND, s)
            check(f"shadow ({label}): Outlook never driven", f.outlook_calls == [], f.outlook_calls)
            check(f"shadow ({label}): Blotato never called", f.http_calls == [], f.http_calls)
            check(f"shadow ({label}): the chain still ran (lint and deal read)",
                  len(f.lint_calls) == 2 and len(f.verbs("get-call-context")) == 1, f.verb_calls)
            rows = env.receipts()
            check(f"shadow ({label}): receipts say mode shadow, outcome would_send",
                  [(x["mode"], x["outcome"]) for x in rows] == [("shadow", "would_send")] * 2, rows)
            check(f"shadow ({label}): a rehearsal goes to record-signal, never log-outreach",
                  len(f.verbs("record-signal")) == 2 and f.verbs("log-outreach") == [], f.verb_calls)
            check(f"shadow ({label}): the dedupe key is released (a non-send)", env.held() == [], env.held())
        finally:
            env.close()


@scenario
def test_shipped_config_is_live() -> None:
    config = ss.load_config()
    sender = ss.Sender(config=config, verb=Fakes().verb)   # reads nothing, writes nothing
    check("shipped config: live_from is 2026-10-12", config["live_from"] == "2026-10-12", config["live_from"])
    check("shipped config: the sender is live", sender.is_live(T0))
    check("shipped config: a bare date starts at midnight Central, not UTC",
          sender.is_live(dt.datetime(2026, 10, 12, 5, 0, tzinfo=dt.timezone.utc))
          and not sender.is_live(dt.datetime(2026, 10, 12, 4, 59, tzinfo=dt.timezone.utc)))
    env = Env(live_from=config["live_from"])
    try:
        r = env.mail()
        check("shipped live_from: a clean partner mail is actually sent (fake Outlook driven once)",
              r.outcome == ss.SENT and len(env.fakes.outlook_calls) == 1
              and env.receipts()[0]["mode"] == "live", r)
    finally:
        env.close()
    check("shipped config: caps are 20 mail and 5 social",
          config["daily_caps"] == {"mail": 20, "social": 5}, config["daily_caps"])
    check("shipped config cites decision cbe91287",
          "cbe91287-10a2-48d0-bda7-3b1784b4bb93" in config["_comment"])
    accounts = json.loads((ss.REPO / config["social"]["accounts_file"]).read_text())
    check("shipped config: every allowed platform has an account in the Blotato accounts file",
          all(p in accounts for p in config["social"]["platforms"]), accounts.keys())
    check("the send script is the separate file, and the draft script still has no send keystroke",
          ss.SEND_SCRIPT.name == "outlook-send.applescript"
          and "command down" not in (ss.REPO / "bin/outlook-draft.applescript").read_text())


# ── (a) kill switch ──────────────────────────────────────────────────────────

@scenario
def test_kill_switch_refuses_before_anything_else() -> None:
    cases = (("stop file", lambda e: e.state.mkdir() or (e.state / "system-send.stop").touch(),
              "kill_switch_engaged"),
             ("open signal", lambda e: e.fakes.signals.update(open=1), "kill_switch_engaged"),
             ("claimed signal", lambda e: e.fakes.signals.update(claimed=1), "kill_switch_engaged"),
             ("unreadable signal read", lambda e: setattr(e.fakes, "signal_read_ok", False),
              "kill_switch_unreadable"))
    for label, arm, reason in cases:
        env = Env()
        try:
            arm(env)
            r, s = env.mail(), env.social()
            f = env.fakes
            check(f"kill switch ({label}): mail refused with {reason}",
                  (r.outcome, r.reason) == (ss.REFUSED, reason), r)
            check(f"kill switch ({label}): social refused with {reason}",
                  (s.outcome, s.reason) == (ss.REFUSED, reason), s)
            check(f"kill switch ({label}): no lint, no transport", f.lint_calls == [] and
                  f.outlook_calls == [] and f.http_calls == [], (f.lint_calls, f.outlook_calls))
            check(f"kill switch ({label}): the refusal still leaves a receipt", len(env.receipts()) == 2)
        finally:
            env.close()


# ── (b) writing lint ─────────────────────────────────────────────────────────

@scenario
def test_lint_finding_refuses() -> None:
    env = Env()
    try:
        env.fakes.lint_clean = False
        r, s = env.mail(), env.social()
        f = env.fakes
        check("lint: mail with a finding is refused", (r.outcome, r.reason) == (ss.REFUSED, "lint"), r)
        check("lint: social with a finding is refused", (s.outcome, s.reason) == (ss.REFUSED, "lint"), s)
        check("lint: surfaces are email and social",
              [x[1] for x in f.lint_calls] == ["email", "social"], f.lint_calls)
        check("lint: the subject is linted with the body",
              bool(f.lint_calls) and f.lint_calls[0][0].startswith("Tour schedule\n"), f.lint_calls)
        check("lint: nothing reached a transport", f.outlook_calls == [] and f.http_calls == [])
    finally:
        env.close()


@scenario
def test_real_lint_door_refuses_every_severity() -> None:
    for label, body, clean in (("clean text", "The tour is set for Tuesday at ten.", True),
                               ("HARD em dash", "The tour is set — Tuesday at ten.", False),
                               ("REVIEW word", "The market landscape changed this year.", False)):
        ok, detail = ss.run_lint(body, "email")
        check(f"real lint ({label}): clean={clean}", ok is clean, detail)
    env = Env()
    try:
        r = env.sender(lint=ss.run_lint).send_mail(to=[JOE], subject="Tour",
                                                   body="The tour is set — Tuesday.")
        check("real lint wired through the chain: an em dash refuses the send",
              (r.outcome, r.reason) == (ss.REFUSED, "lint") and env.fakes.outlook_calls == [], r)
    finally:
        env.close()


# ── (c) allowlists ───────────────────────────────────────────────────────────

@scenario
def test_mail_allowlist() -> None:
    env = Env()
    try:
        r = env.mail(to=["stranger@example.com"])
        check("mail: a stranger with no deal is refused",
              (r.outcome, r.reason) == (ss.REFUSED, "recipient_not_allowed"), r)
        r = env.mail(to=["stranger@example.com"], deal_id=DEAL,
                     follow_up={"next_on": "2026-10-20", "next_step": "x"})
        check("mail: a non-participant on a named deal is refused",
              (r.outcome, r.reason) == (ss.REFUSED, "recipient_not_allowed"), r)
        r = env.mail(to=[JOE, "stranger@example.com"])
        check("mail: one stranger among partner addresses refuses the whole send",
              r.reason == "recipient_not_allowed", r)
        r = env.mail(to=["not-an-address"])
        check("mail: a malformed address is refused", r.reason == "recipient_malformed", r)
        r = env.mail(to=[])
        check("mail: no recipient is refused", r.reason == "no_recipient", r)
        r = env.mail(to=[PARTICIPANT.lower()], deal_id=DEAL)
        check("mail: a participant send with no follow-up is refused",
              (r.outcome, r.reason) == (ss.REFUSED, "follow_up_required"), r)
        env.fakes.call_context_ok = False
        r = env.mail(to=[PARTICIPANT], deal_id=DEAL,
                     follow_up={"next_on": "2026-10-20", "next_step": "x"})
        check("mail: an unreadable deal context refuses", r.reason == "deal_context_unreadable", r)
        check("mail: no refused mail reached Outlook", env.fakes.outlook_calls == [])
        env.fakes.call_context_ok = True
        r = env.mail(to=["Joe.Bookout@CARR.us", "dell.mccraney@carr.us"], subject="Status")
        check("mail: the partners' own addresses send (case-insensitive)", r.outcome == ss.SENT, r)
        r = env.mail(to=[PARTICIPANT.upper()], deal_id=DEAL, subject="Tour for Dr Lee",
                     follow_up={"next_on": "2026-10-20", "next_step": "Confirm the tour"})
        check("mail: an exact deal participant sends", r.outcome == ss.SENT, r)
        outreach = env.fakes.verbs("log-outreach")
        check("mail: a live participant send is logged through log-outreach on the deal",
              len(outreach) == 1 and outreach[0]["ref"] == DEAL
              and "Lee Clinic Relocation" in outreach[0]["detail"]
              and outreach[0]["outcome"] == "sent" and outreach[0]["channel"] == "email"
              and outreach[0]["next_on"] == "2026-10-20", outreach)
        url = env.fakes.outlook_calls[-1][0]
        check("mail: the compose URL uses bin/outlook-draft.py's encoding (%20, literal @)",
              url.startswith("mailto:dr.lee@example-clinic.com?") and "Tour%20for%20Dr%20Lee" in url, url)
    finally:
        env.close()


@scenario
def test_social_allowlist_and_payload() -> None:
    env = Env()
    try:
        r = env.social(platform="tiktok")
        check("social: a platform outside the config is refused",
              (r.outcome, r.reason) == (ss.REFUSED, "platform_not_allowed"), r)
        r = env.social(platform="instagram")
        check("social: a configured platform with no account in the accounts file is refused",
              (r.outcome, r.reason) == (ss.REFUSED, "platform_not_allowed"), r)
        check("social: refused posts never reached Blotato", env.fakes.http_calls == [])
        r = env.social(platform="LinkedIn", media_urls=["https://cdn.example/card.png"])
        check("social: an allowed platform sends", r.outcome == ss.SENT, r)
        url, headers, body = env.fakes.http_calls[0]
        post = json.loads(body)["post"]
        check("social: posts to the Blotato posts endpoint", url == "https://backend.blotato.com/v2/posts", url)
        check("social: the key travels in the blotato-api-key header only",
              headers.get("blotato-api-key") == "fixture-blotato-key"
              and "fixture-blotato-key" not in body.decode(), headers)
        check("social: account and page come from the accounts file",
              post["accountId"] == "27007" and post["target"] == {"targetType": "linkedin", "pageId": "27007"}
              and post["content"]["mediaUrls"] == ["https://cdn.example/card.png"], post)
        check("social: a live post is recorded through record-signal, never log-outreach",
              env.fakes.verbs("log-outreach") == []
              and env.fakes.verbs("record-signal")[-1]["payload"]["outcome"] == "sent")
        env.fakes.key = None
        r = env.social(platform="twitter")
        check("social: no key is a definite non-send", (r.outcome, r.reason) == (ss.FAILED, "transport_refused"), r)
    finally:
        env.close()


# ── (d) dedupe ───────────────────────────────────────────────────────────────

@scenario
def test_dedupe() -> None:
    env = Env()
    try:
        first, second = env.mail(), env.mail()
        check("dedupe: the first send goes", first.outcome == ss.SENT, first)
        check("dedupe: the identical second send is refused",
              (second.outcome, second.reason) == (ss.REFUSED, "duplicate"), second)
        check("dedupe: Outlook was driven once", len(env.fakes.outlook_calls) == 1, env.fakes.outlook_calls)
        a, b = env.mail(dedupe_key="weekly-brief-2026-10-13"), env.mail(
            subject="Different words", dedupe_key="weekly-brief-2026-10-13")
        check("dedupe: a caller key dedupes even when the words change",
              a.outcome == ss.SENT and b.reason == "duplicate", (a, b))
        s1, s2 = env.social(), env.social()
        check("dedupe: social dedupes too", s1.outcome == ss.SENT and s2.reason == "duplicate", (s1, s2))
    finally:
        env.close()


@scenario
def test_definite_non_send_releases_the_key() -> None:
    env = Env()
    try:
        env.fakes.outlook_answer = "ERROR: no compose window titled Tour schedule"
        r = env.mail()
        check("non-send: no compose window is a definite failure",
              (r.outcome, r.reason) == (ss.FAILED, "transport_refused"), r)
        check("non-send: the key is released", env.held() == [], env.held())
        env.fakes.outlook_answer = "SENT"
        check("non-send: the same message can then be sent", env.mail().outcome == ss.SENT)
        env.fakes.http_answer = (422, {"message": "bad request"})
        r = env.social()
        check("non-send: Blotato 4xx is a definite failure and releases",
              r.outcome == ss.FAILED and env.held() == [], (r, env.held()))
        r = env.mail(body="x" * 2000)
        check("non-send: a mailto too long is refused before Outlook is driven",
              r.outcome == ss.FAILED and len(env.fakes.outlook_calls) == 2 and env.held() == [], r)
    finally:
        env.close()


@scenario
def test_ambiguous_send_holds_the_key() -> None:
    for label, arm in (("window still open", lambda f: setattr(f, "outlook_answer",
                                                                "NOT_SENT: compose window still open after Send")),
                       ("transport raised", lambda f: setattr(f, "outlook_answer", RuntimeError("osascript died"))),
                       ("Blotato 503", None)):
        env = Env()
        try:
            if arm is None:
                env.fakes.http_answer = (503, None)
                r, retry = env.social(), None
                env.fakes.http_answer = (200, {"postSubmissionId": "sub-2"})
                retry = env.social()
                calls = len(env.fakes.http_calls)
            else:
                arm(env.fakes)
                r = env.mail()
                env.fakes.outlook_answer = "SENT"
                retry = env.mail()
                calls = len(env.fakes.outlook_calls)
            check(f"ambiguous ({label}): outcome send_unknown", r.outcome == ss.UNKNOWN, r)
            check(f"ambiguous ({label}): the retry is refused as a duplicate",
                  retry.reason == "duplicate" and calls == 1, (retry, calls))
            check(f"ambiguous ({label}): the key stays held", len(env.held()) == 1, env.held())
        finally:
            env.close()


@scenario
def test_crash_mid_send_holds_the_key_across_processes() -> None:
    env = Env()
    try:
        env.fakes.outlook_answer = KeyboardInterrupt()
        crashed = False
        try:
            env.mail()
        except KeyboardInterrupt:
            crashed = True
        check("crash: the interrupt propagates (the process dies mid-send)", crashed)
        env.fakes.outlook_answer = "SENT"
        retry = env.mail()           # a fresh Sender: a new process reading the same ledger
        check("crash: a fresh sender refuses the resend",
              (retry.outcome, retry.reason) == (ss.REFUSED, "duplicate")
              and len(env.fakes.outlook_calls) == 1, retry)
        key = env.held()[0]
        check("crash: resolve_claim(sent=False) releases a held key",
              ss.resolve_claim(key, sent=False, state_dir=env.state))
        check("crash: after release the message sends", env.mail().outcome == ss.SENT)
    finally:
        env.close()


@scenario
def test_corrupt_ledger_refuses() -> None:
    env = Env()
    try:
        env.state.mkdir()
        (env.state / "system-sender-ledger.json").write_text("{not json")
        r = env.mail()
        check("ledger: an unreadable ledger refuses the send",
              (r.outcome, r.reason) == (ss.REFUSED, "ledger_unreadable") and env.fakes.outlook_calls == [], r)
    finally:
        env.close()
    env = Env()
    try:
        ledger = env.state / "system-sender-ledger.json"

        def send_then_break_ledger(url: str, title: str) -> str:
            ledger.write_text("{not json")
            return "SENT"
        env.fakes.outlook = send_then_break_ledger  # type: ignore[method-assign]
        r = env.mail()
        check("ledger: a ledger that breaks mid-send still yields SENT and a receipt",
              r.outcome == ss.SENT and "key left claimed" in r.detail
              and [x["outcome"] for x in env.receipts()] == ["sent"], (r, env.receipts()))
    finally:
        env.close()


# ── (e) caps ─────────────────────────────────────────────────────────────────

@scenario
def test_daily_caps() -> None:
    env = Env(caps={"mail": 2, "social": 1})
    try:
        results = [env.mail(subject=f"Note {i}") for i in range(3)]
        check("cap: two mails send", [r.outcome for r in results[:2]] == [ss.SENT, ss.SENT], results)
        check("cap: the third is refused daily_cap",
              (results[2].outcome, results[2].reason) == (ss.REFUSED, "daily_cap"), results[2])
        check("cap: the capped key is released, so it can go tomorrow", env.held() == [], env.held())
        s = [env.social(text=f"Post {i}") for i in range(2)]
        check("cap: social is capped on its own count",
              s[0].outcome == ss.SENT and s[1].reason == "daily_cap", s)
        env.clock.at = T0 + dt.timedelta(hours=7)    # 17:00 Central, still today
        check("cap: later the same Central day is still capped", env.mail(subject="Note 9").reason == "daily_cap")
        env.clock.at = dt.datetime(2026, 10, 14, 5, 30, tzinfo=dt.timezone.utc)   # 00:30 Central next day
        check("cap: the count resets at midnight Central", env.mail(subject="Note 2").outcome == ss.SENT)
    finally:
        env.close()
    shadow = Env(live_from=None, caps={"mail": 1, "social": 1})
    try:
        a, b = shadow.mail(subject="A"), shadow.mail(subject="B")
        check("cap: shadow mode rehearses the cap on would_send",
              a.outcome == ss.WOULD_SEND and b.reason == "daily_cap", (a, b))
    finally:
        shadow.close()


# ── (g) receipts ─────────────────────────────────────────────────────────────

@scenario
def test_receipts_on_every_attempt() -> None:
    env = Env()
    try:
        env.fakes.lint_clean = False
        env.mail()
        env.fakes.lint_clean = True
        env.mail()
        env.mail()
        env.social()
        attempts, records = env.receipts(), env.receipts("record")
        check("receipts: one attempt line per call, refusals included",
              [r["outcome"] for r in attempts] == ["refused", "sent", "refused", "sent"], attempts)
        check("receipts: every attempt has a record-layer line",
              [r["attempt_id"] for r in records] == [r["attempt_id"] for r in attempts], records)
        check("receipts: no body text is stored, only its hash",
              all("body" not in r and len(r["text_sha256"]) == 64 for r in attempts), attempts[0])
        signals = env.fakes.verbs("record-signal")
        check("receipts: record-signal is severity info, threshold met, evidence points at sends.jsonl",
              all(s["severity"] == "info" and s["observed_value"] >= s["threshold_value"]
                  and s["evidence_refs"][0].startswith(f"{env.state}/sends.jsonl#") for s in signals),
              signals[0])
        check("receipts: a self-addressed live mail is not a deal touch (record-signal, not log-outreach)",
              env.fakes.verbs("log-outreach") == [])
    finally:
        env.close()


# ── the health row ───────────────────────────────────────────────────────────

@scenario
def test_health_row() -> None:
    env = Env(live_from=None, caps={"mail": 1, "social": 5})
    try:
        line, breach, hard = ss.health_row(sender=env.sender())
        check("health: an idle shadow sender is OK", line.startswith("OK system sends — shadow")
              and not breach and not hard, line)
        check("health: the row names its bound action", "on breach: owner orchestrator" in line
              and "verify: run.sh health --section sends" in line, line)
        env.mail()
        line, breach, _ = ss.health_row(sender=env.sender())
        check("health: a reached cap is a breach", line.startswith("WARN") and breach
              and "mail 1/1 would_send" in line, line)
    finally:
        env.close()
    env = Env()
    try:
        env.fakes.outlook_answer = "NOT_SENT: compose window still open after Send"
        env.mail()
        line, breach, _ = ss.health_row(sender=env.sender())
        check("health: a held claim is a breach", breach and "held claims 1" in line, line)
        env.fakes.signal_read_ok = False
        line, breach, _ = ss.health_row(sender=env.sender())
        check("health: an unreadable kill switch is a breach", breach and "kill switch unreadable" in line, line)
        env.fakes.signal_read_ok = True
        (env.state / "system-send.stop").touch()
        line, _, _ = ss.health_row(sender=env.sender())
        check("health: an engaged stop file is shown", "kill switch engaged (stop file" in line, line)
        (env.state / "system-sender-ledger.json").write_text("[]")
        line, breach, hard = ss.health_row(sender=env.sender())
        check("health: an unreadable ledger is a hard error", line.startswith("UNKNOWN") and breach and hard, line)
    finally:
        env.close()

# ── finding 1/2/8: the Outlook transport picks one checked window ───────────

class FakeProcs:
    """Stands in for subprocess.run under the REAL _outlook_send: answers the
    probe, the `open -a` and the send step the way osascript and open would."""

    def __init__(self, *, probe: str = "COUNT 2 0", open_rc: int = 0, send: str = "SENT",
                 send_rc: int = 0, state: Path | None = None) -> None:
        self.probe, self.open_rc, self.send, self.send_rc = probe, open_rc, send, send_rc
        self.state = state
        self.calls: list[list[str]] = []
        # What the send step saw at the moment it ran: the token file's text,
        # its mode, the env's state dir, and whether the send lock was held.
        self.at_send: dict[str, Any] = {}

    def __call__(self, argv: list[str], **kw: Any) -> subprocess.CompletedProcess[str]:
        self.calls.append(list(argv))
        if argv[0] == "open":
            return subprocess.CompletedProcess(argv, self.open_rc, "", "")
        if argv[2] == "probe":
            return subprocess.CompletedProcess(argv, 0, self.probe + "\n", "")
        if self.state is not None:
            token_file = self.state / ss.SEND_TOKEN_FILE
            self.at_send = {"env_state": (kw.get("env") or {}).get(ss.STATE_DIR_ENV),
                            "token_text": token_file.read_text(encoding="utf-8")
                            if token_file.exists() else None,
                            "token_mode": oct(token_file.stat().st_mode & 0o777)
                            if token_file.exists() else None,
                            "lock_held": _lock_is_held(self.state / ss.SEND_LOCK_FILE)}
        return subprocess.CompletedProcess(argv, self.send_rc, self.send + "\n", "")

    def steps(self) -> list[str]:
        return ["open" if c[0] == "open" else c[2] for c in self.calls]


def _lock_is_held(path: Path) -> bool:
    """True when another open file description holds an exclusive flock."""
    with open(path, "a", encoding="utf-8") as fh:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        fcntl.flock(fh, fcntl.LOCK_UN)
        return False


def _real_outlook(procs: FakeProcs, state: Path) -> Any:
    procs.state = state
    return lambda url, subject: ss._outlook_send(url, subject, state_dir=state, run=procs)


@scenario
def test_empty_subject_refused_before_any_claim() -> None:
    for label, subject in (("empty", ""), ("whitespace", "   \t")):
        env = Env()
        try:
            r = env.mail(subject=subject)
            check(f"empty subject ({label}): refused empty_subject",
                  (r.outcome, r.reason) == (ss.REFUSED, "empty_subject"), r)
            check(f"empty subject ({label}): no transport call", env.fakes.outlook_calls == [])
            check(f"empty subject ({label}): nothing claimed (no ledger written)",
                  not (env.state / "system-sender-ledger.json").exists())
            check(f"empty subject ({label}): the refusal has a receipt",
                  [x["reason"] for x in env.receipts()] == ["empty_subject"], env.receipts())
        finally:
            env.close()


@scenario
def test_outlook_transport_window_choice() -> None:
    long_subject = "Tour schedule for the Lee Clinic relocation, Tuesday and Wednesday"
    cases = (
        # label, procs, outcome, reason, steps
        ("one new window, exact match", FakeProcs(), ss.SENT, "sent", ["probe", "open", "send"]),
        ("a window already titled with the subject", FakeProcs(probe="COUNT 3 1"),
         ss.FAILED, "compose_window_refused", ["probe"]),
        ("Outlook not running", FakeProcs(probe="NOT_RUNNING"), ss.FAILED, "transport_refused", ["probe"]),
        ("probe unreadable", FakeProcs(probe="garbage"), ss.FAILED, "transport_refused", ["probe"]),
        ("open -a failed", FakeProcs(open_rc=1), ss.FAILED, "outlook_open_failed", ["probe", "open"]),
        ("zero matching windows", FakeProcs(send="REFUSED: no window titled exactly with the subject; nothing pressed"),
         ss.FAILED, "compose_window_refused", ["probe", "open", "send"]),
        ("duplicate matching windows", FakeProcs(send="REFUSED: 2 windows titled with the subject; nothing pressed"),
         ss.FAILED, "compose_window_refused", ["probe", "open", "send"]),
        ("two new windows", FakeProcs(send="REFUSED: 2 new windows appeared, expected exactly one; nothing pressed"),
         ss.FAILED, "compose_window_refused", ["probe", "open", "send"]),
        ("no new window", FakeProcs(send="REFUSED: no new compose window appeared; nothing pressed"),
         ss.FAILED, "compose_window_refused", ["probe", "open", "send"]),
        ("Outlook not frontmost", FakeProcs(send="NOT_FRONTMOST: Outlook or its compose window is not frontmost after re-activation; nothing pressed"),
         ss.FAILED, "outlook_not_frontmost", ["probe", "open", "send"]),
        ("send step exited non-zero", FakeProcs(send="execution error", send_rc=1),
         ss.UNKNOWN, "transport_ambiguous", ["probe", "open", "send"]),
        ("pressed, window still open", FakeProcs(send="NOT_SENT: compose window still open after Send"),
         ss.UNKNOWN, "transport_ambiguous", ["probe", "open", "send"]),
    )
    for label, procs, outcome, reason, steps in cases:
        env = Env()
        try:
            r = env.sender(outlook=_real_outlook(procs, env.state)).send_mail(
                to=[JOE], subject=long_subject, body="The tour is set for Tuesday.")
            check(f"outlook ({label}): {outcome}/{reason}", (r.outcome, r.reason) == (outcome, reason), r)
            check(f"outlook ({label}): steps {steps}", procs.steps() == steps, procs.calls)
            held = env.held()
            check(f"outlook ({label}): key {'held' if outcome == ss.UNKNOWN else 'not held'}",
                  bool(held) == (outcome == ss.UNKNOWN), held)
            scripts = [c for c in procs.calls if c[0] == "osascript"]
            check(f"outlook ({label}): the script gets the FULL subject, never a prefix",
                  all(c[3] == long_subject for c in scripts), scripts)
            if "send" in steps:
                check(f"outlook ({label}): send carries the probe's window count and a token",
                      scripts[-1][2:5] == ["send", long_subject, "3" if procs.probe == "COUNT 3 0" else "2"]
                      and len(scripts[-1]) == 6 and len(scripts[-1][5]) == 64, scripts[-1])
            check(f"outlook ({label}): no token file is left behind",
                  not (env.state / ss.SEND_TOKEN_FILE).exists())
        finally:
            env.close()


def _script_code() -> str:
    script = ss.SEND_SCRIPT.read_text(encoding="utf-8")
    return "\n".join(line for line in script.splitlines() if not line.lstrip().startswith("--"))


def _handler_body(code: str, name: str) -> list[str]:
    """The stripped non-comment lines between `on <name>` and `end <name>`."""
    lines = [line.strip() for line in code.splitlines()]
    start = next(i for i, line in enumerate(lines)
                 if line.startswith((f"on {name}(", f"on {name} ")))
    end = lines.index(f"end {name}", start)
    return lines[start + 1:end]


# The AppleScript titleMatches handler, verbatim, line by line. The Python
# mirror below is tied to it: test_send_script_shape asserts every one of these
# lines is present in the handler, so changing the script without changing the
# mirror (and its table) fails the selftest.
TITLE_MATCHES_LINES = (
    'set sep to subj & " " & (character id 8226) & " "',
    "considering case",
    "if t is subj then return true",
    "if (length of t) > (length of sep) and t starts with sep then",
    "set rest to text ((length of sep) + 1) thru -1 of t",
    'if rest contains "@" and rest does not contain " " then return true',
    "end considering",
    "return false",
)
MORE_THAN_ONE_NEW_WINDOW = ('if total > priorCount + 1 then return "REFUSED: " & (total - priorCount)'
                            ' & " new windows appeared, expected exactly one; nothing pressed"')


def title_matches(t: str, subj: str) -> bool:
    """Python mirror of bin/outlook-send.applescript's titleMatches (see
    TITLE_MATCHES_LINES): the exact subject, or the subject, " • " and a
    single address-like token. Case-sensitive; never a prefix."""
    sep = subj + " " + chr(8226) + " "
    if t == subj:
        return True
    if len(t) > len(sep) and t.startswith(sep):
        rest = t[len(sep):]
        if "@" in rest and " " not in rest:
            return True
    return False


@scenario
def test_title_matches_mirror() -> None:
    subj = "Tour schedule"
    bullet = " " + chr(8226) + " "
    table = (
        ("exact title", subj, True),
        ("prefix-only title (subject is a prefix of the title)", subj + " for the Lee Clinic", False),
        ("truncated title (title is a prefix of the subject)", subj[:-3], False),
        ("case variant", subj.upper(), False),
        ("case variant with address", subj.lower() + bullet + "joe@example.com", False),
        ("subject • addr", subj + bullet + "joe@example.com", True),
        ("subject • text with spaces", subj + bullet + "a draft @ noon", False),
        ("subject • text without @", subj + bullet + "draft", False),
        ("subject • nothing", subj + bullet, False),
        ("reply prefix", "Re: " + subj, False),
        ("subject + plain space suffix", subj + " joe@example.com", False),
    )
    for label, title, want in table:
        check(f"titleMatches mirror: {label} -> {want}", title_matches(title, subj) is want, title)


@scenario
def test_send_script_shape() -> None:
    script = ss.SEND_SCRIPT.read_text(encoding="utf-8")
    code = _script_code()
    check("script: no prefix match on the title", "starts with titlePrefix" not in code
          and "titlePrefix" not in code)
    body = _handler_body(code, "titleMatches")
    check("script: titleMatches compares the exact title", "if t is subj then return true" in body, body)
    check("script: titleMatches carries every line its Python mirror is built from",
          [line for line in TITLE_MATCHES_LINES if line not in body] == [], body)
    check("script: no `starts with subj` anywhere in code", "starts with subj" not in code)
    starts = [line.strip() for line in code.splitlines() if "starts with" in line]
    check("script: the only `starts with` is titleMatches' `t starts with sep`",
          starts == ["if (length of t) > (length of sep) and t starts with sep then"], starts)
    run_body = _handler_body(code, "run")
    check("script: refuses when more than one new window appeared",
          MORE_THAN_ONE_NEW_WINDOW in run_body, run_body)
    wait = run_body.index("repeat 20 times")
    check("script: that refusal sits inside the wait for the new window, before its exit",
          wait < run_body.index(MORE_THAN_ONE_NEW_WINDOW)
          < run_body.index("if total = priorCount + 1 then exit repeat"), run_body)
    check("script: never falls back to the first or front window for the send",
          "first window" not in code and "set found to window 1" not in code)
    check("script: refuses zero and more than one match",
          "(count of matches) is 0" in code and "(count of matches) > 1" in code)
    check("script: requires exactly one new window",
          'if total < priorCount + 1 then return "REFUSED: no new compose window appeared; nothing pressed"' in code
          and 'if total is not priorCount + 1 then return "REFUSED: window count changed to "' in code)
    press = code.index("keystroke return using {command down}")
    check("script: frontmost is verified (with one re-activation) before the keystroke",
          code.index("outlookReady(targetName)") < press
          and code.rindex("if not frontmost then return", 0, press) < press)
    check("script: the stale 'never pressed Send' claim is gone",
          "has never pressed Send" not in script)
    check("lib: no 40-character title prefix remains", not hasattr(ss, "TITLE_MATCH_LEN"))


# ── review BLOCK finding 2: the send step needs a single-use token ───────────

SCRATCH_TOKEN = "f" * 64


@scenario
def test_send_token_python_side() -> None:
    env = Env()
    try:
        procs = FakeProcs()
        r = env.sender(outlook=_real_outlook(procs, env.state)).send_mail(
            to=[JOE], subject="Tour schedule", body="The tour is set for Tuesday.")
        check("token: the send went through the chain", r.outcome == ss.SENT, r)
        token = procs.calls[-1][5]
        text = procs.at_send.get("token_text") or ""
        parts = text.split("\n", 2)
        check("token: written before the send step, as token, expiry, subject",
              len(parts) == 3 and parts[0] == token and parts[2] == "Tour schedule", text)
        now = int(__import__("time").time())
        check("token: expires within the TTL", len(parts) == 3 and parts[1].isdigit()
              and now < int(parts[1]) <= now + ss.SEND_TOKEN_TTL_SECONDS + 1, parts)
        check("token: the file is mode 600", procs.at_send.get("token_mode") == "0o600", procs.at_send)
        check("token: written while the send lock is held", procs.at_send.get("lock_held") is True,
              procs.at_send)
        check("token: the script is told where the state dir is",
              procs.at_send.get("env_state") == str(env.state), procs.at_send)
        check("token: deleted after the send", not (env.state / ss.SEND_TOKEN_FILE).exists())
        check("token: the lock is released after the send",
              not _lock_is_held(env.state / ss.SEND_LOCK_FILE))
        procs2 = FakeProcs()
        env.sender(outlook=_real_outlook(procs2, env.state)).send_mail(
            to=[JOE], subject="Tour schedule 2", body="Second message.")
        check("token: a fresh token per send", procs2.calls[-1][5] != token)
    finally:
        env.close()
    for label, procs in (("send step raised", None),
                         ("send step exited non-zero", FakeProcs(send="boom", send_rc=1))):
        env = Env()
        try:
            if procs is None:
                procs = FakeProcs()
                original = procs.__call__

                def raising(argv: list[str], _orig: Any = original, **kw: Any) -> Any:
                    if argv[0] == "osascript" and argv[2] == "send":
                        _orig(argv, **kw)
                        raise subprocess.TimeoutExpired(argv, 120)
                    return _orig(argv, **kw)
                runner: Any = raising
            else:
                runner = procs
            procs.state = env.state
            env.sender(outlook=lambda url, subject: ss._outlook_send(
                url, subject, state_dir=env.state, run=runner)).send_mail(
                to=[JOE], subject="Tour schedule", body="The tour is set for Tuesday.")
            check(f"token ({label}): the token existed when the send step ran",
                  bool(procs.at_send.get("token_text")), procs.at_send)
            check(f"token ({label}): the finally block still deleted it",
                  not (env.state / ss.SEND_TOKEN_FILE).exists())
        finally:
            env.close()


@scenario
def test_send_token_script_shape() -> None:
    code = _script_code()
    run_body = _handler_body(code, "run")
    token_lines = ('if (count of argv) < 4 then return "REFUSED: no valid send token; nothing pressed"',
                   'if not my consumeSendToken(item 4 of argv, subj) then return "REFUSED: no valid send token; nothing pressed"')
    check("token script: send checks the token", all(line in run_body for line in token_lines), run_body)
    consume = run_body.index(token_lines[1])
    first_tell = min(i for i, line in enumerate(run_body)
                     if "tell application" in line and i > run_body.index('if mode is not "send" then return "REFUSED: unknown mode " & mode & "; nothing pressed"'))
    check("token script: the token is checked before any `tell` in the send step", consume < first_tell,
          (consume, first_tell))
    check("token script: the Send keystroke comes after the consume",
          consume < run_body.index("keystroke return using {command down}"))
    body = _handler_body(code, "consumeSendToken")
    check("token script: the token handler never tells an application",
          not any("tell " in line for line in body), body)
    check("token script: reads the token file",
          "set stored to read (POSIX file tokenPath) as \u00abclass utf8\u00bb" in body
          and 'set tokenPath to stateDir & "/' + ss.SEND_TOKEN_FILE + '"' in body
          and 'set stateDir to system attribute "' + ss.STATE_DIR_ENV + '"' in body, body)
    check("token script: the default state dir matches the library's",
          '(POSIX path of (path to home folder)) & "' + ss.DEFAULT_STATE_DIR.removeprefix("~/") + '"'
          in "\n".join(body))
    check("token script: requires token, subject and expiry",
          "if storedToken is not offered then return false" in body
          and "if storedSubject is not subj then return false" in body
          and "if nowSecs > expirySecs then return false" in body, body)
    delete = 'do shell script "/bin/rm " & quoted form of tokenPath & " && test ! -e " & quoted form of tokenPath'
    check("token script: consumes (deletes) the file before returning true",
          delete in body and body.index(delete) < body.index("return true")
          and body.index("if nowSecs > expirySecs then return false") < body.index(delete), body)


def _osascript_or_skip(label: str) -> bool:
    if shutil.which("osascript") is None:
        print(f"  skip {label}: osascript is not on this machine (macOS only)")
        return False
    return True


@scenario
def test_send_token_runtime() -> None:
    """Runs the REAL script with tokens that must be refused. Every case is
    refused inside consumeSendToken, before the script's first `tell`, so
    Outlook and System Events are never addressed."""
    if not _osascript_or_skip("send token runtime"):
        return
    state = Path(tempfile.mkdtemp(prefix="system-sender-token-"))
    try:
        env = {**os.environ, ss.STATE_DIR_ENV: str(state)}

        def send(*args: str) -> str:
            proc = subprocess.run(["osascript", str(ss.SEND_SCRIPT), "send", *args],
                                  capture_output=True, text=True, timeout=30, env=env)
            return (proc.stdout or proc.stderr).strip()

        refused = "REFUSED: no valid send token; nothing pressed"
        future = int(__import__("time").time()) + 60
        check("token runtime: a direct send with a bad token is refused",
              send("x", "0", "badtoken") == refused)
        check("token runtime: a direct send with no token is refused", send("x", "0") == refused)
        token_file = state / ss.SEND_TOKEN_FILE
        ss._write_send_token(token_file, SCRATCH_TOKEN, "x", future)
        check("token runtime: a wrong token is refused", send("x", "0", "e" * 64) == refused)
        check("token runtime: a token for another subject is refused",
              send("X", "0", SCRATCH_TOKEN) == refused)
        check("token runtime: refusals leave the stored token alone", token_file.exists())
        ss._write_send_token(token_file, SCRATCH_TOKEN, "x", future - 120)
        check("token runtime: an expired token is refused", send("x", "0", SCRATCH_TOKEN) == refused)
    finally:
        shutil.rmtree(state, ignore_errors=True)


@scenario
def test_send_token_handler_consumes_once() -> None:
    """The consumeSendToken handler, lifted verbatim from the real script and
    run alone (no `tell`, no Outlook): a valid token is accepted once and its
    file is gone; the same token again is refused."""
    if not _osascript_or_skip("send token handler"):
        return
    source = ss.SEND_SCRIPT.read_text(encoding="utf-8")
    start = source.index("on consumeSendToken(")
    handler = source[start:source.index("end consumeSendToken", start) + len("end consumeSendToken")]
    state = Path(tempfile.mkdtemp(prefix="system-sender-handler-"))
    try:
        harness = state / "harness.applescript"
        harness.write_text("on run argv\n  return consumeSendToken(item 1 of argv, item 2 of argv)\nend run\n\n"
                           + handler + "\n", encoding="utf-8")
        env = {**os.environ, ss.STATE_DIR_ENV: str(state)}

        def consume(token: str, subject: str) -> str:
            proc = subprocess.run(["osascript", str(harness), token, subject],
                                  capture_output=True, text=True, timeout=30, env=env)
            return (proc.stdout or proc.stderr).strip()

        token_file = state / ss.SEND_TOKEN_FILE
        subject = 'Tour "schedule" • Tuesday'
        ss._write_send_token(token_file, SCRATCH_TOKEN, subject, int(__import__("time").time()) + 60)
        check("token handler: a valid token for the exact subject is accepted",
              consume(SCRATCH_TOKEN, subject) == "true")
        check("token handler: the file is consumed", not token_file.exists())
        check("token handler: the same token a second time is refused",
              consume(SCRATCH_TOKEN, subject) == "false")
    finally:
        shutil.rmtree(state, ignore_errors=True)


# ── finding 3: the self-test send ───────────────────────────────────────────

@scenario
def test_send_self_test_goes_through_the_chain() -> None:
    env = Env()
    try:
        r = env.sender().send_self_test(subject="System sender self-test", body="First live send.")
        f = env.fakes
        check("self-test: sent", r.outcome == ss.SENT, r)
        check("self-test: addressed to Joe's own address only",
              f.outlook_calls and f.outlook_calls[0][0].startswith("mailto:joe.bookout@carr.us?"), f.outlook_calls)
        check("self-test: ran the kill switch and lint", len(f.verbs("next-signals")) == 2
              and len(f.lint_calls) == 1, f.verb_calls)
        env.fakes.signals.update(open=1)
        r = env.sender().send_self_test(subject="System sender self-test 2", body="Second.")
        check("self-test: the kill switch still refuses it", r.reason == "kill_switch_engaged", r)
        bare = Env()
        try:
            bare.config["mail"] = {"self_addresses": [JOE]}
            r = bare.sender().send_self_test(subject="System sender self-test", body="First live send.")
            check("self-test: no self_test_address means config_invalid, never a fallback address",
                  (r.outcome, r.reason) == (ss.REFUSED, "config_invalid")
                  and bare.fakes.outlook_calls == [], (r, bare.fakes.outlook_calls))
        finally:
            bare.close()
        check("self-test: the library carries no hard-coded address",
              JOE not in ss.REPO.joinpath("lib/system_sender.py").read_text(encoding="utf-8"))
        check("self-test: the shipped config names Joe's address and says the first caller is the live test",
              ss.load_config()["mail"]["self_test_address"] == JOE
              and "FIRST CALLER IS THE LIVE TEST" in ss.load_config()["_note"])
    finally:
        env.close()


# ── finding 4: machine-wide state ───────────────────────────────────────────

@scenario
def test_state_dir_is_machine_wide() -> None:
    env = Env()
    try:
        config = ss.load_config()
        saved = os.environ.pop(ss.STATE_DIR_ENV, None)
        try:
            default = ss.Sender(config=config, verb=Fakes().verb)
            check("state: the shipped default is ~/carr-local/system-sender, outside the repo",
                  default.state_dir == Path.home() / "carr-local" / "system-sender"
                  and ss.REPO not in default.state_dir.parents, default.state_dir)
            os.environ[ss.STATE_DIR_ENV] = str(env.root / "from-env")
            check("state: the environment variable overrides config state_dir",
                  ss.Sender(config=config, verb=Fakes().verb).state_dir == env.root / "from-env")
        finally:
            os.environ.pop(ss.STATE_DIR_ENV, None)
            if saved is not None:
                os.environ[ss.STATE_DIR_ENV] = saved
        a = env.sender(root=env.root / "checkout-a")
        b = env.sender(root=env.root / "checkout-b")
        first = a.send_mail(to=[JOE], subject="Shared", body="Once per machine.")
        second = b.send_mail(to=[JOE], subject="Shared", body="Once per machine.")
        check("state: two checkouts on one machine share dedupe",
              first.outcome == ss.SENT and second.reason == "duplicate", (first, second))
        check("state: created mode 700", (env.state.stat().st_mode & 0o777) == 0o700,
              oct(env.state.stat().st_mode))
        check("state: ledger, receipts and stop file all live in state_dir",
              {x.name for x in env.state.iterdir()} >= {"system-sender-ledger.json", "sends.jsonl"}
              and a.stop_file == env.state / "system-send.stop")
        (env.state / "system-send.stop").touch()
        r = b.send_mail(to=[JOE], subject="Other", body="Stopped.")
        check("state: one stop file stops every checkout on the machine", r.reason == "kill_switch_engaged", r)
    finally:
        env.close()


# ── finding 5: config validated before any claim ────────────────────────────

@scenario
def test_bad_config_refuses_and_claims_nothing() -> None:
    bad = (("malformed live_from", {"live_from": "next tuesday"}),
           ("negative cap", {"daily_caps": {"mail": -1, "social": 5}}),
           ("boolean cap", {"daily_caps": {"mail": True, "social": 5}}),
           ("string cap", {"daily_caps": {"mail": "20", "social": 5}}),
           ("platforms not a list", {"social": {"platforms": "linkedin", "accounts_file": "x",
                                                "api_base": "https://backend.blotato.com/v2"}}),
           ("bad timezone", {"timezone": "Mars/Olympus"}),
           ("receipts path escapes state_dir", {"receipts_file": "../sends.jsonl"}),
           ("receipts name is a dot path", {"receipts_file": ".."}),
           ("no self_test_address", {"mail": {"self_addresses": [JOE]}}),
           ("self_test_address not an own address", {"mail": {"self_addresses": [JOE],
                                                              "self_test_address": PARTICIPANT}}))
    for label, change in bad:
        env = Env()
        try:
            env.config.update(change)
            sender = env.sender()
            check(f"config ({label}): the Sender reports it", bool(sender.config_error), sender.config_error)
            r, s = sender.send_mail(to=[JOE], subject="Hi", body="x"), sender.send_social(platform="linkedin", text="x")
            check(f"config ({label}): mail and social refused config_invalid",
                  r.reason == s.reason == "config_invalid" and r.outcome == ss.REFUSED, (r, s))
            check(f"config ({label}): nothing claimed, nothing linted, no transport",
                  not (env.state / "system-sender-ledger.json").exists() and env.fakes.lint_calls == []
                  and env.fakes.outlook_calls == [] and env.fakes.http_calls == [])
            check(f"config ({label}): the refusal still has a receipt",
                  [x["reason"] for x in env.receipts()] == ["config_invalid"] * 2, env.receipts())
            line, breach, hard = ss.health_row(sender=sender)
            check(f"config ({label}): health is a hard UNKNOWN", line.startswith("UNKNOWN") and breach and hard, line)
        finally:
            env.close()
    check("config: the shipped config validates", ss.validate_config(ss.load_config()) is None,
          ss.validate_config(ss.load_config()))


# ── finding 6: the cap slot is reserved inside the ledger lock ──────────────

@scenario
def test_cap_reserved_with_the_claim() -> None:
    env = Env(caps={"mail": 1, "social": 5})
    try:
        inner: list[ss.SendResult] = []

        def concurrent(url: str, title: str) -> str:
            # A second sender runs while the first is mid-delivery.
            inner.append(env.sender().send_mail(to=[JOE], subject="Second", body="Other words."))
            return "SENT"
        first = env.sender(outlook=concurrent).send_mail(to=[JOE], subject="First", body="Words.")
        check("cap race: the first send goes", first.outcome == ss.SENT, first)
        check("cap race: the concurrent second, with one slot left, is refused daily_cap",
              len(inner) == 1 and (inner[0].outcome, inner[0].reason) == (ss.REFUSED, "daily_cap"), inner)
    finally:
        env.close()
    env = Env(caps={"mail": 1, "social": 5})
    try:
        sender = env.sender()
        a = ss._Attempt(channel="mail", target=JOE, dedupe_key="k1")
        b = ss._Attempt(channel="mail", target=JOE, dedupe_key="k2")
        one = sender.ledger.claim("k1", a, T0, day="2026-10-13", mode="live", cap=1)
        two = sender.ledger.claim("k2", b, T0, day="2026-10-13", mode="live", cap=1)
        check("cap ledger: two claims when one slot remains, the second is capped",
              one == ("claimed", 0) and two == ("capped", 1), (one, two))
        sender.ledger.release("k1", a.attempt_id)
        check("cap ledger: a released claim frees its slot",
              sender.ledger.claim("k2", b, T0, day="2026-10-13", mode="live", cap=1)[0] == "claimed")
    finally:
        env.close()
    env = Env(caps={"mail": 1, "social": 5})
    try:
        env.fakes.outlook_answer = "ERROR: open failed (exit 1); Send never pressed"
        failed = env.mail(subject="Fails")
        env.fakes.outlook_answer = "SENT"
        after = env.mail(subject="Goes")
        check("cap: a definite non-send frees the slot for the next send",
              failed.outcome == ss.FAILED and after.outcome == ss.SENT, (failed, after))
        env.fakes.outlook_answer = "NOT_SENT: compose window still open after Send"
        env.clock.at = T0 + dt.timedelta(days=1)
        held = env.mail(subject="Held")
        env.fakes.outlook_answer = "SENT"
        nxt = env.mail(subject="Next")
        check("cap: an ambiguous (held) send keeps its slot", held.outcome == ss.UNKNOWN
              and nxt.reason == "daily_cap", (held, nxt))
    finally:
        env.close()


# ── finding 8: definite non-sends release the key ───────────────────────────

@scenario
def test_definite_non_send_classes() -> None:
    env = Env()
    real_builder = ss._mailto_builder
    try:
        def broken_builder() -> Any:
            raise ImportError("bin/outlook-draft.py unavailable")
        ss._mailto_builder = broken_builder   # type: ignore[assignment]
        r = env.mail(subject="Builder")
        check("non-send class: mailto builder import failure is definite",
              (r.outcome, r.reason) == (ss.FAILED, "mailto_builder_unavailable") and env.held() == []
              and env.fakes.outlook_calls == [], r)
    finally:
        ss._mailto_builder = real_builder     # type: ignore[assignment]
    try:
        env.fakes.outlook_answer = "NOT_FRONTMOST: Outlook or its compose window is not frontmost after re-activation; nothing pressed"
        r = env.mail(subject="Front")
        check("non-send class: Outlook not frontmost is definite, with its own reason",
              (r.outcome, r.reason) == (ss.FAILED, "outlook_not_frontmost") and env.held() == [], r)
        env.fakes.outlook_answer = "ERROR: open failed (exit 1); Send never pressed"
        r = env.mail(subject="Open")
        check("non-send class: open -a failure is definite",
              (r.outcome, r.reason) == (ss.FAILED, "outlook_open_failed") and env.held() == [], r)

        def key_raises() -> str:
            raise subprocess.TimeoutExpired("zsh", 15)
        r = env.sender(blotato_key=key_raises).send_social(platform="linkedin", text="Key lookup breaks.")
        check("non-send class: a key-lookup error is definite",
              (r.outcome, r.reason) == (ss.FAILED, "blotato_key_unavailable") and env.held() == []
              and env.fakes.http_calls == [], r)

        real_urlopen = ss.urllib.request.urlopen

        def no_dns(*_: Any, **__: Any) -> Any:
            raise urllib.error.URLError(OSError("nodename nor servname provided"))
        ss.urllib.request.urlopen = no_dns   # type: ignore[assignment]
        try:
            r = env.sender(blotato_http=ss._blotato_post).send_social(platform="linkedin", text="DNS fails.")
        finally:
            ss.urllib.request.urlopen = real_urlopen   # type: ignore[assignment]
        check("non-send class: a Blotato DNS/URLError is definite",
              (r.outcome, r.reason) == (ss.FAILED, "blotato_unreachable") and env.held() == [], r)
        reasons = [x["reason"] for x in env.receipts()]
        check("non-send class: every one wrote a receipt naming its reason",
              reasons == ["mailto_builder_unavailable", "outlook_not_frontmost", "outlook_open_failed",
                          "blotato_key_unavailable", "blotato_unreachable"], reasons)
    finally:
        env.close()


# ── finding 9: the default dedupe key carries the Central date ──────────────

@scenario
def test_default_dedupe_key_is_per_day() -> None:
    env = Env()
    try:
        first, again = env.mail(), env.mail()
        explicit = env.mail(subject="Weekly", dedupe_key="weekly-2026-10-13")
        env.clock.at = T0 + dt.timedelta(days=1)
        tomorrow = env.mail()
        explicit_tomorrow = env.mail(subject="Weekly", dedupe_key="weekly-2026-10-13")
        check("dedupe by day: the same message twice in one day is refused",
              first.outcome == ss.SENT and again.reason == "duplicate", (first, again))
        check("dedupe by day: the same message on a later Central day sends", tomorrow.outcome == ss.SENT, tomorrow)
        check("dedupe by day: an explicit dedupe_key is unchanged (still refused)",
              explicit.outcome == ss.SENT and explicit_tomorrow.reason == "duplicate", explicit_tomorrow)
        env.clock.at = dt.datetime(2026, 10, 15, 4, 59, tzinfo=dt.timezone.utc)   # 23:59 Central, Oct 14
        check("dedupe by day: the day is Central, not UTC", env.mail().reason == "duplicate")
    finally:
        env.close()


def main() -> int:
    for test in (test_shadow_mode_does_everything_but_send,
                 test_shipped_config_is_live,
                 test_kill_switch_refuses_before_anything_else,
                 test_lint_finding_refuses,
                 test_real_lint_door_refuses_every_severity,
                 test_mail_allowlist,
                 test_social_allowlist_and_payload,
                 test_dedupe,
                 test_definite_non_send_releases_the_key,
                 test_ambiguous_send_holds_the_key,
                 test_crash_mid_send_holds_the_key_across_processes,
                 test_corrupt_ledger_refuses,
                 test_daily_caps,
                 test_receipts_on_every_attempt,
                 test_health_row,
                 test_empty_subject_refused_before_any_claim,
                 test_outlook_transport_window_choice,
                 test_title_matches_mirror,
                 test_send_script_shape,
                 test_send_token_python_side,
                 test_send_token_script_shape,
                 test_send_token_runtime,
                 test_send_token_handler_consumes_once,
                 test_send_self_test_goes_through_the_chain,
                 test_state_dir_is_machine_wide,
                 test_bad_config_refuses_and_claims_nothing,
                 test_cap_reserved_with_the_claim,
                 test_definite_non_send_classes,
                 test_default_dedupe_key_is_per_day):
        test()
    return CHECK.summary()


if __name__ == "__main__":
    sys.exit(main())
