#!/usr/bin/env python3
"""outlook-draft.py — put a REAL draft in Joe's carr.us Drafts folder.

THE ROUTE, and it is the only one that works. Every API into this mailbox is
closed: New Outlook exposes no mailbox to AppleScript (0 of 157 folders carry an
account), Graph consent is blocked tenant-wide with no request button
(AADSTS90094), and IMAP basic auth is refused. So this does not use an API. It
drives the Outlook APPLICATION, which Joe is already signed into, via a mailto
URL — and lets Outlook do the writing. Joe's insight, after the session had
wrongly declared the problem unsolvable: "I'm already signed in to my carr email
on the desktop app... it seems like you'd be able to make a email right into that
desktop app." Full record: decision 496bc309.

THREE THINGS THAT ARE NOT GUESSABLE AND COST AN AFTERNOON TO FIND:

1. USE `open -a "Microsoft Outlook"`, NEVER a bare `open "mailto:..."`. Joe's
   DEFAULT mail handler is his personal outlook.live.com in a browser, so a bare
   open composes into the wrong account entirely. The first test looked like a
   total failure and had actually succeeded in the wrong place.

2. PERCENT-ENCODE WITH quote(), NEVER urlencode(). urlencode writes '+' for a
   space and Outlook does not decode it, so subjects arrive as "CARR+draft+test".

3. TYPE A CHARACTER BEFORE SAVING. New Outlook DISCARDS a mailto-spawned draft on
   close if it considers the message untouched — it shows in the Drafts list while
   the window is open, then vanishes. `File > Save` does not help. Cmd+S does not
   help. Typing one character marks it modified; after that File > Save commits it
   and it survives. The AppleScript types one character and deletes it, so the net
   content change is zero. Verified: draft persisted, typed character absent.

WHAT IT WILL NOT DO: send. There is no send path in this file or the AppleScript.
It writes a draft and stops. Sending is a separate step that happens when Joe
asks (decision 41906a30); with no ask, Joe opens Drafts, reads it, presses Send.

USAGE
  echo '{"to":"a@b.com","subject":"Hi","body":"line one\\nline two"}' | bin/outlook-draft.py
  bin/outlook-draft.py --to a@b.com --subject Hi --body 'text'
  bin/outlook-draft.py ... --leave-open     leave the compose window on screen
"""

import argparse
import json
import subprocess
import sys
import urllib.parse
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent / "outlook-draft.applescript"

# Outlook titles a compose window "<subject> • <account>". A very long subject is
# truncated in the title bar, so match on a prefix short enough to survive that.
TITLE_MATCH_LEN = 40


def as_list(value):
    if value is None:
        return []
    parts = value.split(",") if isinstance(value, str) else list(value)
    return [p.strip() for p in parts if str(p).strip()]


def build_url(payload):
    to = as_list(payload.get("to"))
    if not to:
        raise SystemExit("refusing to create a draft with no recipient")
    q = urllib.parse.quote          # %20 for spaces — see note 2 above
    parts = [f"subject={q(payload.get('subject') or '')}",
             f"body={q(payload.get('body') or '')}"]
    if as_list(payload.get("cc")):
        parts.append("cc=" + q(",".join(as_list(payload["cc"]))))
    if as_list(payload.get("bcc")):
        parts.append("bcc=" + q(",".join(as_list(payload["bcc"]))))
    return "mailto:" + q(",".join(to), safe="@,") + "?" + "&".join(parts)


def main():
    ap = argparse.ArgumentParser(description="Create a real Outlook draft. Never sends.")
    ap.add_argument("--to")
    ap.add_argument("--cc")
    ap.add_argument("--bcc")
    ap.add_argument("--subject")
    ap.add_argument("--body")
    ap.add_argument("--leave-open", action="store_true",
                    help="leave the compose window on screen instead of closing it")
    args = ap.parse_args()

    if args.to or args.subject or args.body:
        payload = {k: v for k, v in vars(args).items() if v is not None}
    else:
        raw = sys.stdin.read().strip()
        if not raw:
            raise SystemExit("no input: pass flags or pipe JSON on stdin")
        payload = json.loads(raw)

    url = build_url(payload)
    if len(url) > 1900:
        # mailto has a practical length ceiling and a silently TRUNCATED draft is
        # far worse than a refusal: it looks finished and is not.
        raise SystemExit(
            f"body too long for the mailto route ({len(url)} chars of URL). "
            f"A truncated draft would look complete and would not be. Shorten it, "
            f"or use bin/gmail-handover.py for long text.")

    subprocess.run(["open", "-a", "Microsoft Outlook", url], check=False)

    title_prefix = (payload.get("subject") or "")[:TITLE_MATCH_LEN]
    mode = "leave" if args.leave_open else "close"
    r = subprocess.run(["osascript", str(SCRIPT), title_prefix, mode],
                       capture_output=True, text=True)
    out = (r.stdout or r.stderr).strip()
    if not out.startswith("OK"):
        raise SystemExit(f"draft may not have been saved: {out}")
    print(f"draft saved to Drafts: {payload.get('subject')!r}"
          + ("" if args.leave_open else " (window closed)"))


if __name__ == "__main__":
    main()
