#!/usr/bin/env python3
"""Pin tools/mail-extract.py's contract without touching Apple Mail.

The extractor's value is that the matcher can read what it writes, and its
RISK is that it quietly starts carrying message bodies. Both are pinned here.
"""
import importlib.util
import inspect
import json
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
FAILS: list[str] = []


def check(label, ok, detail=""):
    print(("ok   " if ok else "FAIL ") + label)
    if not ok:
        FAILS.append(f"{label}: {detail}")


def load(name, rel):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


mx = load("mail_extract", "tools/mail-extract.py")

check("a display-name sender resolves to the bare address",
      mx.addr_of("Joe Bookout <Joe.Bookout@carr.us>") == "joe.bookout@carr.us",
      mx.addr_of("Joe Bookout <Joe.Bookout@carr.us>"))
check("a bare address survives unchanged and is lowercased",
      mx.addr_of("  RFrancis@practice.example ") == "rfrancis@practice.example")
check("an empty recipient field yields no addresses, never one empty string",
      mx.split_addrs("") == [] and mx.split_addrs(None) == [])
check("a multi-recipient field splits on the comma the script joins with",
      mx.split_addrs("a@x.com,B@Y.com") == ["a@x.com", "b@y.com"])

# Deleted Items alone is larger than the real corpus on Joe's account (2,434
# against ~1,600 filed), so including it would swamp every count downstream.
for box in ("Deleted Items", "Junk Email", "Drafts", "Outbox"):
    check(f"{box} is excluded from the walk", box.lower() in mx.SKIP_MAILBOXES)
check("Sent Items is NOT excluded — it is the outbound half",
      "sent items" not in mx.SKIP_MAILBOXES)

src = (ROOT / "tools/mail-extract.py").read_text()
# Decision 745ab4aa admits derived facts only. A body read here would copy the
# mailbox wholesale, so the absence of one is a contract, not an omission.
check("the extractor never reads a message body",
      "content of messages" not in src and "source of messages" not in src and
      '"body"' not in src, "a body read appeared in the extractor")
check("the walk never uses a `whose` filter or a per-message Mail loop",
      "whose sender" not in src and "whose" not in src.split("EXTRACT_SCRIPT")[1],
      "a whose filter would never return on a 1,300-message mailbox")
check("Mail is probed before it is addressed, and never launched",
      "pgrep" in src and "mail_is_running" in src and
      "EX_CONFIG" in inspect.getsource(mx.main))

# The live nightly run timed out while listing mailboxes. Reproduce the list
# format without opening Mail, including the index gap left by a skipped box.
listing = mx.RS.join([
    mx.US.join(("1", "1", "Synthetic Account", "Inbox")),
    mx.US.join(("1", "2", "Synthetic Account", "Junk Email")),
    mx.US.join(("1", "3", "Synthetic Account", "Sent Items")),
])
original_osa = mx.osa
try:
    calls: list[tuple[str, tuple[str, ...], int]] = []

    def fake_osa(script, *args, timeout=0):
        calls.append((script, args, timeout))
        if script == mx.LIVENESS_SCRIPT:
            return "1"
        if script == mx.ACCOUNT_MAILBOX_SCRIPT:
            return listing
        raise AssertionError("unexpected mocked AppleScript")

    mx.osa = fake_osa
    boxes, status, slow_accounts = mx.list_mailboxes()
finally:
    mx.osa = original_osa
check("mailbox listing keeps Mail's indices while excluding junk",
      boxes == [(1, 1, "Synthetic Account", "Inbox"),
                (1, 3, "Synthetic Account", "Sent Items")], boxes)
check("mailbox listing probes liveness, then enumerates each account",
      calls[0][0] == mx.LIVENESS_SCRIPT and
      calls[1][0] == mx.ACCOUNT_MAILBOX_SCRIPT and
      calls[1][1] == ("1",) and status == "ready" and slow_accounts == 0,
      calls)
check("mailbox enumeration performs no per-mailbox message count",
      "count of messages" not in mx.ACCOUNT_MAILBOX_SCRIPT and
      "name of every mailbox of acct" in mx.ACCOUNT_MAILBOX_SCRIPT)

# A short liveness timeout is classified separately from a slow account
# enumeration. Both cases are mocked; this test never addresses Apple Mail.
try:
    def unresponsive_osa(script, *args, timeout=0):
        if script == mx.LIVENESS_SCRIPT:
            raise subprocess.TimeoutExpired("osascript", timeout)
        raise AssertionError("account enumeration should not start after probe timeout")

    mx.osa = unresponsive_osa
    boxes, status, slow_accounts = mx.list_mailboxes()
    check("liveness timeout is classified as mail_unresponsive",
          boxes == [] and status == "mail_unresponsive" and slow_accounts == 0,
          (boxes, status, slow_accounts))
finally:
    mx.osa = original_osa

try:
    def slow_account_osa(script, *args, timeout=0):
        if script == mx.LIVENESS_SCRIPT:
            return "2"
        if script == mx.ACCOUNT_MAILBOX_SCRIPT:
            if args == ("1",):
                return listing
            raise subprocess.TimeoutExpired("osascript", timeout)
        raise AssertionError("unexpected mocked AppleScript")

    mx.osa = slow_account_osa
    boxes, status, slow_accounts = mx.list_mailboxes()
    check("account timeout is classified as enumeration_slow",
          len(boxes) == 2 and status == "enumeration_slow" and slow_accounts == 1,
          (boxes, status, slow_accounts))
finally:
    mx.osa = original_osa
check("mail capture AppleScript contains no outbound send command",
      not re.search(r"\bsend\b", mx.LIVENESS_SCRIPT +
                    mx.ACCOUNT_MAILBOX_SCRIPT + mx.EXTRACT_SCRIPT,
                    flags=re.IGNORECASE))

# THE JOIN THAT MATTERS: every key the matcher reads off a message must be a key
# the extractor writes. This is the seam loop #169 found dead — the matcher was
# pointed at a scratchpad file nothing produced.
matcher_src = (ROOT / "tools/mail-touch-matcher.py").read_text()
emitted = {"from", "to", "cc", "date", "subject", "mailbox", "direction"}
consumed = {k for k in ("from", "to", "cc", "date", "subject", "direction")
            if f'msg.get("{k}")' in matcher_src or f'"{k}"' in matcher_src}
check("the matcher reads only keys the extractor emits",
      consumed <= emitted, f"matcher wants {sorted(consumed - emitted)}")
check("the extractor's default output is the matcher's default input",
      pathlib.Path(mx.DEFAULT_OUT).name == "mail-extract.json" and
      "out/mail-extract.json" in matcher_src)

if FAILS:
    print("\nFAIL: mail extract contract")
    for f in FAILS:
        print("  - " + f)
    sys.exit(1)
print("\nPASS: mail extract contract")
