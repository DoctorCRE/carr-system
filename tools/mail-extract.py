#!/usr/bin/env python3
"""Walk Apple Mail and write out/mail-extract.json — the input tools/mail-touch-matcher.py reads.

WHY THIS EXISTS. The calendar pass proves a meeting happened. Mail carries the
contact that never becomes a meeting, which is most follow-up, and it is the
half of vendor-capture coverage the calendar lane cannot see (loop #169).

WHAT IT DOES NOT DO, on purpose: it does not read message BODIES. Decision
745ab4aa admits derived facts only, so this pass emits envelope metadata —
who, when, which mailbox, subject line — and the matcher decides which handful
of messages are about someone in the record. Substance capture is a second,
narrower pass over that shortlist, never a wholesale copy of the mailbox.

IT WRITES NOTHING TO THE RECORD. The output is a file the matcher reads; the
matcher in turn emits proposals. Both halves are read-only against CARR's store.

THE ONE LINE THAT DECIDES WHETHER IT FINISHES. Never loop over messages in
AppleScript and never use a `whose` filter: both re-resolve the message per
property and a 1,300-message mailbox never returns (the same trap the calendar
backfill burned two wrong theories on, loop #305). Read each property as a
WHOLE LIST in one Apple event -- `sender of messages of m` -- then assemble the
records in Python. Measured on Joe's Sent Items: 1,342 messages, three
properties, 0.4 seconds.

Mailbox discovery is separate from extraction. A short account-count probe
distinguishes an unresponsive Mail process from a responsive one whose account
enumeration is slow. Each account's mailbox names are requested independently,
so one slow account cannot hide which phase failed.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_OUT = os.path.abspath(os.path.join(HERE, "..", "out", "mail-extract.json"))

# A mailbox whose contents are not evidence of a live relationship. Deleted and
# Junk in particular are larger than the real corpus and would swamp it.
SKIP_MAILBOXES = {"deleted items", "junk email", "drafts", "outbox", "sendlater",
                  "trash", "junk", "spam"}

JOE = {"joe.bookout@carr.us", "josephbookout@outlook.com"}

# EX_CONFIG. bin/nightly.sh treats 78 as "the step ran, found what it needs is
# absent, wrote nothing and said so" — not a failed night.
EX_CONFIG = 78

US = "\x1f"   # between fields
RS = "\x1e"   # between records
AS_SEP = ","  # between addresses inside one field

MAIL_LIVENESS_TIMEOUT = 5
ACCOUNT_ENUM_TIMEOUT = 20

LIVENESS_SCRIPT = '''
tell application "Mail"
  return count of every account
end tell
'''

ACCOUNT_MAILBOX_SCRIPT = '''
on run argv
  set ai to (item 1 of argv) as integer
  tell application "Mail"
    set acct to account ai
    set accountName to name of acct
    -- Names only. Message counts and message properties belong to extraction.
    set mailboxNames to name of every mailbox of acct
  end tell
  set out to ""
  set bi to 0
  repeat with mailboxName in mailboxNames
    set bi to bi + 1
    set out to out & (ai as string) & "@@US@@" & (bi as string) & "@@US@@" & accountName & "@@US@@" & (contents of mailboxName) & "@@RS@@"
  end repeat
  return out
end run
'''

# THE MAILBOX IS ADDRESSED BY INDEX, never by name: Mail nests mailboxes inside
# folders, so `mailbox "Ryan Fielding" of account X` raises -1728 for every child
# folder — which on Joe's account is most of them. Each property is one bulk
# read; the repeat loops below walk LOCAL lists, never the Mail object model.
EXTRACT_SCRIPT = '''
on run argv
  set ai to (item 1 of argv) as integer
  set bi to (item 2 of argv) as integer
  tell application "Mail"
    set m to mailbox bi of account ai
    set senders to (sender of messages of m)
    set subjects to (subject of messages of m)
    set stamps to (date sent of messages of m)
    set tos to (address of to recipients of messages of m)
    set ccs to (address of cc recipients of messages of m)
  end tell
  set n to count of senders
  set out to ""
  set oldDelims to AppleScript's text item delimiters
  set AppleScript's text item delimiters to ","
  repeat with i from 1 to n
    set dt to item i of stamps
    set iso to ((year of dt) as string) & "-" & my pad(my monthNum(dt)) & "-" & my pad(day of dt) & "T" & my pad(hours of dt) & ":" & my pad(minutes of dt) & ":" & my pad(seconds of dt)
    set toList to item i of tos
    set ccList to item i of ccs
    if class of toList is not list then set toList to {toList}
    if class of ccList is not list then set ccList to {ccList}
    set out to out & (item i of senders) & "@@US@@" & (toList as string) & "@@US@@" & (ccList as string) & "@@US@@" & iso & "@@US@@" & (item i of subjects) & "@@RS@@"
  end repeat
  set AppleScript's text item delimiters to oldDelims
  return out
end run

on pad(v)
  set s to (v as integer) as string
  if length of s is 1 then return "0" & s
  return s
end pad

on monthNum(dt)
  return (month of dt) as integer
end monthNum
'''


def mail_is_running() -> bool:
    """Never LAUNCH Mail. This runs at 02:05 with nobody at the machine, and
    `tell application "Mail"` starts the app if it is not already up — an
    unattended side effect on Joe's desktop that no capture is worth. When Mail
    is down the step declines with EX_CONFIG and the chain stays green.
    Checked with pgrep rather than System Events so the probe itself needs no
    automation grant of its own."""
    return subprocess.run(["pgrep", "-x", "Mail"],
                          capture_output=True).returncode == 0


def osa(script: str, *args: str, timeout: int = 600) -> str:
    # AppleScript has no \x escape, so the separators are injected as the real
    # control characters rather than written into the script source.
    script = script.replace("@@US@@", US).replace("@@RS@@", RS)
    p = subprocess.run(["osascript", "-", *args], input=script,
                       capture_output=True, text=True, timeout=timeout)
    if p.returncode:
        raise RuntimeError((p.stderr or "osascript failed").strip()[:400])
    return p.stdout


def _is_apple_event_timeout(exc: Exception) -> bool:
    text = str(exc).lower()
    return (isinstance(exc, subprocess.TimeoutExpired) or
            "-1712" in text or "timed out" in text)


def list_mailboxes():
    """Return (boxes, status, problem_count) without reading message data."""
    try:
        account_count = int(osa(LIVENESS_SCRIPT,
                                timeout=MAIL_LIVENESS_TIMEOUT).strip())
    except Exception as exc:
        # Do not print the AppleScript error: it may contain mailbox/account
        # details. The aggregate status is enough for the nightly diagnosis.
        status = "mail_unresponsive" if _is_apple_event_timeout(exc) else "mail_probe_failed"
        return [], status, 0

    boxes = []
    slow_accounts = 0
    failed_accounts = 0
    for ai in range(1, account_count + 1):
        try:
            raw = osa(ACCOUNT_MAILBOX_SCRIPT, str(ai),
                      timeout=ACCOUNT_ENUM_TIMEOUT)
        except Exception as exc:
            if _is_apple_event_timeout(exc):
                slow_accounts += 1
            else:
                failed_accounts += 1
            continue
        for rec in raw.split(RS):
            cols = rec.split(US)
            if len(cols) != 4:
                continue
            account_index, mailbox_index, account, name = (c.strip() for c in cols)
            if not account or name.lower() in SKIP_MAILBOXES:
                continue
            try:
                boxes.append((int(account_index), int(mailbox_index), account, name))
            except ValueError:
                continue

    if slow_accounts:
        return boxes, "enumeration_slow", slow_accounts + failed_accounts
    if failed_accounts:
        return boxes, "enumeration_failed", failed_accounts
    return boxes, "ready", 0


def addr_of(value: str) -> str:
    """Mail hands back either a bare address or `Display Name <addr>`."""
    value = (value or "").strip()
    if "<" in value and ">" in value:
        value = value[value.rindex("<") + 1:value.rindex(">")]
    return value.strip().lower()


def split_addrs(value: str):
    return [addr_of(a) for a in (value or "").split(AS_SEP) if addr_of(a)]


def extract_mailbox(ai: int, bi: int, account: str, name: str, since: datetime):
    raw = osa(EXTRACT_SCRIPT, str(ai), str(bi))
    rows = []
    for rec in raw.split(RS):
        cols = rec.split(US)
        if len(cols) != 5:
            continue
        sender, to_raw, cc_raw, iso, subject = cols
        sender = addr_of(sender)
        try:
            when = datetime.fromisoformat(iso.strip())
        except ValueError:
            continue
        if when < since:
            continue
        rows.append({
            "from": sender,
            "to": split_addrs(to_raw),
            "cc": split_addrs(cc_raw),
            "date": when.isoformat(),
            "subject": subject.strip(),
            "mailbox": f"{account}/{name}",
            "direction": "out" if sender in JOE else "in",
        })
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--days", type=int, default=540,
                    help="only messages this recent (default 540)")
    ap.add_argument("--mailbox", action="append", default=None,
                    help="limit to this mailbox name; repeatable")
    args = ap.parse_args()

    if not mail_is_running():
        print(json.dumps({"skipped": "Mail is not running; declining rather than "
                                     "launching it unattended",
                          "messages_written": 0, "output_file": args.out}, indent=1))
        return EX_CONFIG

    since = datetime.now() - timedelta(days=args.days)
    wanted = {m.lower() for m in (args.mailbox or [])}

    boxes, enumeration_status, problem_accounts = list_mailboxes()
    if enumeration_status != "ready":
        print(json.dumps({
            "status": enumeration_status,
            "mailboxes_discovered": len(boxes),
            "problem_accounts": problem_accounts,
            "messages_written": 0,
            "bodies_captured": 0,
            "output_file_written": False,
        }, indent=1))
        return 1
    if wanted:
        boxes = [b for b in boxes if b[3].lower() in wanted]
    if not boxes:
        print("no mailboxes matched", file=sys.stderr)
        return 1

    messages, failures = [], []
    for ai, bi, account, name in boxes:
        try:
            messages.extend(extract_mailbox(ai, bi, account, name, since))
        except Exception as exc:                     # one bad mailbox is not the run
            failures.append(f"{account}/{name}: {exc}")

    messages.sort(key=lambda m: m.get("date") or "")
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(messages, fh, indent=1)

    # Console output carries NO client content — counts, mailbox names and the
    # output path only, the same contract tools/mail-touch-matcher.py holds.
    print(json.dumps({
        "mailboxes_read": len(boxes),
        "messages_written": len(messages),
        "outbound": sum(1 for m in messages if m["direction"] == "out"),
        "inbound": sum(1 for m in messages if m["direction"] == "in"),
        "window_days": args.days,
        "oldest": messages[0]["date"] if messages else None,
        "newest": messages[-1]["date"] if messages else None,
        "bodies_captured": 0,
        "failed_mailboxes": failures,
        "output_file": args.out,
    }, indent=1))
    return 1 if failures and not messages else 0


if __name__ == "__main__":
    sys.exit(main())
