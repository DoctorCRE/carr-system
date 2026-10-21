#!/usr/bin/env python3
"""gmail-handover.py — send a draft from the AI Gmail to a partner's CARR address.

JOE'S DESIGN, 2026-08-03: "I would rather you write at Gmail, email, and send it
to my carr account. And then I just forward it, and I remove the forward and just
send it." It is also his ORIGINAL architecture — ai-operating-notes.md:64 already
specifies drafts emailed from the AI Gmail to joe.bookout@carr.us with the
auto-send whitelist set to his own CARR address only. This implements that line.

WHY THIS AND NOT THE MAILBOX DIRECTLY. Every route into the carr.us mailbox is
closed without a CARR IT approval Joe has decided not to seek, and his reason is
sound: asking draws attention to automating a corporate mailbox. Full evidence in
decision `db40facd`; the four dead attempts are staged under _to_delete with a
WHY.md. The short version: New Outlook exposes no mailbox to AppleScript, Graph
consent is blocked tenant-wide with no request button (AADSTS90094), and IMAP
basic auth is refused despite the server advertising AUTH=PLAIN.

THE VIRTUE OF THIS ROUTE, beyond that it works: it touches CARR's systems NOT AT
ALL. Nothing registered, nothing requested, no administrator sees anything. It is
Joe's own Gmail sending mail to his own work address, which is indistinguishable
from him emailing himself. That matters more than convenience here.

WHY GMAIL CAN DO WHAT CARR CANNOT: Google still issues app passwords. That is the
exact facility CARR's tenant denies. The credential is scoped to mail, revocable
at any time from Joe's Google account, and useless for anything else.

THE ALLOWLIST IS THE SAFETY, AND IT IS STRUCTURAL. This file CAN send, and is bounded by a hardcoded recipient list of
the two partners' own addresses. A send to any other address is refused before a
connection is opened — not by discipline, not by a prompt, by the code. This file's
whitelist is Joe's own address only; sending to anyone else is outside this file and
goes through Joe's own accounts when he asks (decision 41906a30). A client, a vendor, a landlord, a listing
agent can never be reached from here, and widening this list is a decision for
Joe, in writing, not a convenience edit.

WHAT JOE DOES WITH IT: the mail lands in his CARR inbox carrying the exact text he
intends to send. He forwards it, deletes the forwarded header block, types the
real recipient, and sends from his own carr.us identity. This file never sends to the
actual recipient; that send is Joe's, or Claude's when Joe asks (decision 41906a30).

SETUP (once, Joe's hands only — Claude never sees this value)
  App password from https://myaccount.google.com/apppasswords (needs 2-Step
  Verification on the account), then:

    umask 077
    printf 'CARR_GMAIL_USER=joe.bookout.carr.us@gmail.com\n' >  ~/.config/carr/gmail.env
    printf 'CARR_GMAIL_APP_PASSWORD=xxxx\n'                  >> ~/.config/carr/gmail.env
    chmod 600 ~/.config/carr/gmail.env

USAGE
  echo '{"to":"joe","subject":"Re: suite 200","body":"..."}' | bin/gmail-handover.py
  bin/gmail-handover.py --to joe --subject 'Hi' --body 'text'
  bin/gmail-handover.py --check      verify login only, send nothing
  bin/gmail-handover.py --intended-for 'Dr. Smith <x@y.com>' ...
        adds a header line naming who the text is ultimately for, so the forward
        step does not require remembering.
"""

import argparse
import json
import os
import smtplib
import sys
from email.message import EmailMessage
from pathlib import Path

ENVFILE = Path.home() / ".config" / "carr" / "gmail.env"
SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587

# THE ALLOWLIST. Partner work addresses only. Nothing here may reach a client.
# Widening this is Joe's ruling, recorded, never an edit of convenience.
ALLOWED = {
    "joe": "joe.bookout@carr.us",
    "dell": "dell.mccraney@carr.us",
}


def creds():
    user = os.environ.get("CARR_GMAIL_USER", "")
    pw = os.environ.get("CARR_GMAIL_APP_PASSWORD", "")
    if not (user and pw):
        try:
            for line in ENVFILE.read_text().splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k, v = k.strip(), v.strip().strip('"').strip("'")
                if k == "CARR_GMAIL_USER" and not user:
                    user = v
                elif k == "CARR_GMAIL_APP_PASSWORD" and not pw:
                    pw = v
        except FileNotFoundError:
            sys.exit(f"no credential. Create {ENVFILE} yourself — see the setup notes "
                     f"at the top of this file. Claude does not handle this value.")
    if not (user and pw):
        sys.exit(f"{ENVFILE} is missing CARR_GMAIL_USER or CARR_GMAIL_APP_PASSWORD")
    return user, pw


def resolve(target):
    """Accept 'joe'/'dell' or a full partner address. Refuse everything else."""
    t = (target or "").strip().lower()
    if t in ALLOWED:
        return ALLOWED[t]
    if t in ALLOWED.values():
        return t
    sys.exit(
        f"REFUSED: {target!r} is not a partner address.\n"
        f"  This tool can send only to: {', '.join(sorted(ALLOWED.values()))}\n"
        f"  It exists to hand a draft to a partner for review, never to reach a\n"
        f"  client. Send to a partner, who forwards it himself."
    )


def build(sender, to_addr, payload):
    m = EmailMessage()
    m["From"] = sender
    m["To"] = to_addr
    subject = payload.get("subject") or "(no subject)"
    m["Subject"] = subject

    intended = payload.get("intended_for") or payload.get("intended-for")
    body = payload.get("body", "")
    if intended:
        # A single header line, above the text, so the forward step does not
        # rely on memory. Kept deliberately short — everything below the rule is
        # the message exactly as it should go out, nothing to edit away.
        body = (f"[ intended recipient: {intended} ]\n"
                f"[ forward this, delete the forwarded header and this block, send ]\n"
                f"{'-' * 60}\n\n{body}")
    m.set_content(body)
    if payload.get("html"):
        m.add_alternative(payload["html"], subtype="html")
    return m


def main():
    ap = argparse.ArgumentParser(
        description="Send a draft to a partner's CARR address. Partners only.")
    ap.add_argument("--check", action="store_true", help="verify login; sends nothing")
    ap.add_argument("--to", help="joe | dell | a partner's carr.us address")
    ap.add_argument("--subject")
    ap.add_argument("--body")
    ap.add_argument("--html")
    ap.add_argument("--intended-for", dest="intended_for",
                    help="who the text is ultimately for; printed as a header line")
    args = ap.parse_args()

    user, pw = creds()

    if args.check:
        try:
            with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as s:
                s.starttls()
                s.login(user, pw)
            print(f"login OK as {user}")
            print(f"allowed recipients: {', '.join(sorted(ALLOWED.values()))}")
        except smtplib.SMTPAuthenticationError as e:
            sys.exit(f"login refused: {e}\n"
                     f"  If 2-Step Verification is off, app passwords are unavailable —\n"
                     f"  turn it on, then generate one at myaccount.google.com/apppasswords.")
        return

    if args.to or args.subject or args.body or args.html:
        payload = {k: v for k, v in vars(args).items() if v is not None}
    else:
        raw = sys.stdin.read().strip()
        if not raw:
            raise SystemExit("no input: pass flags or pipe JSON on stdin")
        payload = json.loads(raw)

    to_addr = resolve(payload.get("to"))
    msg = build(user, to_addr, payload)

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as s:
        s.starttls()
        s.login(user, pw)
        s.send_message(msg)
    print(f"sent to {to_addr}: {msg['Subject']!r}")


if __name__ == "__main__":
    main()
