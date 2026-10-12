#!/usr/bin/env python3
"""Selftest for lib/system_sender.py, the system's own outbound sender.

Hermetic: a fake Outlook runner, a fake Blotato HTTP call, a fake record-layer
verb door, a fixed clock and a temporary out/ directory. Nothing here opens
Outlook, reaches Blotato, or calls the record layer. The one real collaborator
is the writing lint (tools/writing-lint.py through run.sh lint), which is
local, read-only and deterministic, and is exercised once on purpose so the
lint step is proved against the real door, not only a stub.

What it proves, one block per link of the chain: every refusal (kill switch by
stop file, open signal, claimed signal and unreadable read; lint; recipient and
platform allowlists; follow-up; cap; ledger), shadow mode, dedupe including a
crash mid-send, the definite-vs-ambiguous transport split, receipts on every
attempt, the record-layer routing (log-outreach vs record-signal), the health
row, and that the SHIPPED config is in shadow mode.
"""
from __future__ import annotations

import datetime as dt
import json
import shutil
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
        accounts = self.root / "accounts.json"
        accounts.write_text(json.dumps(ACCOUNTS))
        self.config = ss.load_config()
        self.config.update(live_from=live_from, daily_caps=caps or {"mail": 20, "social": 5})
        self.config["social"] = {**self.config["social"], "accounts_file": str(accounts)}
        self.fakes = Fakes()
        self.clock = Clock(T0)

    def sender(self, *, lint: Any = None) -> ss.Sender:
        f = self.fakes
        return ss.Sender(config=self.config, root=self.root, verb=f.verb, lint=lint or f.lint,
                         outlook=f.outlook, blotato_http=f.http, blotato_key=lambda: f.key,
                         now=self.clock)

    def mail(self, **kw: Any) -> ss.SendResult:
        args: dict[str, Any] = {"to": [JOE], "subject": "Tour schedule", "body": "The tour is set for Tuesday."}
        args.update(kw)
        return self.sender().send_mail(**args)

    def social(self, **kw: Any) -> ss.SendResult:
        args: dict[str, Any] = {"platform": "linkedin", "text": "Three questions to ask before signing a clinic lease."}
        args.update(kw)
        return self.sender().send_social(**args)

    def receipts(self, kind: str = "attempt") -> list[dict[str, Any]]:
        path = self.root / "out" / "sends.jsonl"
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
                             ("no offset", "2026-10-01T00:00:00")):
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
def test_shipped_config_is_shadow() -> None:
    config = ss.load_config()
    sender = ss.Sender(config=config, verb=Fakes().verb)   # reads nothing, writes nothing
    check("shipped config: live_from is null", config["live_from"] is None, config["live_from"])
    check("shipped config: the sender is in shadow mode", not sender.is_live(T0))
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
    cases = (("stop file", lambda e: (e.root / "out").mkdir() or (e.root / "out/system-send.stop").touch(),
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
              len(outreach) == 1 and outreach[0]["ref"] == "Lee Clinic Relocation"
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
              ss.resolve_claim(key, sent=False, root=env.root))
        check("crash: after release the message sends", env.mail().outcome == ss.SENT)
    finally:
        env.close()


@scenario
def test_corrupt_ledger_refuses() -> None:
    env = Env()
    try:
        (env.root / "out").mkdir()
        (env.root / "out/system-sender-ledger.json").write_text("{not json")
        r = env.mail()
        check("ledger: an unreadable ledger refuses the send",
              (r.outcome, r.reason) == (ss.REFUSED, "ledger_unreadable") and env.fakes.outlook_calls == [], r)
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
                  and s["evidence_refs"][0].startswith("out/sends.jsonl#") for s in signals), signals[0])
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
        (env.root / "out/system-send.stop").touch()
        line, _, _ = ss.health_row(sender=env.sender())
        check("health: an engaged stop file is shown", "kill switch engaged (stop file" in line, line)
        (env.root / "out/system-sender-ledger.json").write_text("[]")
        line, breach, hard = ss.health_row(sender=env.sender())
        check("health: an unreadable ledger is a hard error", line.startswith("UNKNOWN") and breach and hard, line)
    finally:
        env.close()


def main() -> int:
    for test in (test_shadow_mode_does_everything_but_send,
                 test_shipped_config_is_shadow,
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
                 test_health_row):
        test()
    return CHECK.summary()


if __name__ == "__main__":
    sys.exit(main())
