#!/usr/bin/env python3
"""Save a mailto draft in the configured Outlook application session.

Use an explicit application to avoid the system mailto handler. Encode spaces
with quote(), since Outlook preserves urlencode()'s plus signs. The AppleScript
marks the message modified before saving, because untouched drafts disappear
when closed. This command never sends.
"""

import argparse
import json
import subprocess
import sys
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib.outlook_settings import load_settings

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

    load_settings()
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
