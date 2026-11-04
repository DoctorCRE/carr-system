#!/usr/bin/env python3
"""Create an exact Outlook draft and claim persistence only after private store readback."""

import argparse
import json
import subprocess
import os
import tempfile
import time
import sys
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib.outlook_settings import load_settings
from lib import outlook_send as send


def as_list(value):
    if value is None:
        return []
    parts = value.split(",") if isinstance(value, str) else list(value)
    return [p.strip() for p in parts if str(p).strip()]


def build_url(payload):
    to = as_list(payload.get("to"))
    if not to:
        raise SystemExit("refusing to create a draft with no recipient")
    q = urllib.parse.quote
    parts = [f"subject={q(payload.get('subject') or '')}",
             f"body={q(payload.get('body') or '')}"]
    if as_list(payload.get("cc")):
        parts.append("cc=" + q(",".join(as_list(payload["cc"]))))
    if as_list(payload.get("bcc")):
        parts.append("bcc=" + q(",".join(as_list(payload["bcc"]))))
    return "mailto:" + q(",".join(to), safe="@,") + "?" + "&".join(parts)


def draft_observations(settings):
    from lib.outlook_intake import OUT, WORK_PREFIX, _snapshot, _decoder, _load
    if settings.folder_account_id is None:
        raise ValueError('draft_account_identity_required')
    out = settings.repo / OUT
    out.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(out, 0o700)
    with tempfile.TemporaryDirectory(prefix=WORK_PREFIX, dir=out) as folder:
        work = Path(folder)
        _snapshot(Path.home(), work)
        db = work / 'store.db'
        fd = os.open(db, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
        _decoder(Path.home())(work / 'store.hxd', work / 'Files', db)
        _, mails, _ = _load(db, settings.folder_account_id, settings.account)
        return [m for m in mails if any(p.lower() == 'drafts' for p in m.get('folders', []))]


def matching_draft_ids(rows, mail):
    import re
    return {str(m['local_keys']) for m in rows
            if str(m.get('local_keys') or '').isdigit()
            and m.get('subject') == mail.subject and m.get('body_kind') == 'full'
            and m.get('body') == mail.body
            and set(re.findall(r"[\w.+'-]+@[\w-]+(?:\.[\w-]+)+", (m.get('recipients') or '').lower())) == set(mail.recipients)}


def save_draft(payload, settings, leave_open=False):
    mail = send.Mail(*(send._addresses(as_list(payload.get(k))) for k in ('to', 'cc', 'bcc')),
                     payload.get('subject') or '', payload.get('body') or '')
    url = build_url(payload)
    if len(url) > 1900:
        raise SystemExit('draft URL exceeds safe length')
    before_drafts = {str(m.get('local_keys')) for m in draft_observations(settings)}
    waits = send.ui.Waits()
    pid, _ = send._main_window(waits)
    before_windows = {w['window_id'] for w in send._cua('list_windows', {'pid': pid}).get('windows', [])}
    opened = subprocess.run(['open', '-a', 'Microsoft Outlook', url], capture_output=True, timeout=60)
    if opened.returncode != 0:
        raise SystemExit('draft launch failed; persistence unverified')
    window = send.ComposeWindow(pid, send._new_window(pid, before_windows, waits), waits)
    if window.read() != send.fields(mail):
        raise SystemExit('draft fields differ; persistence unverified')
    send._cua('invoke_menu', {'pid': pid, 'window_id': window.window_id, 'path': ['File', 'Save']})
    if not leave_open:
        send._cua('invoke_menu', {'pid': pid, 'window_id': window.window_id, 'path': ['File', 'Close']})
        if not send._wait(window.closed, 10, send.Ports(None, None, None, None)):
            raise SystemExit('draft window did not close; persistence unverified')
    deadline = time.monotonic() + 30
    while True:
        found = matching_draft_ids(draft_observations(settings), mail) - before_drafts
        if len(found) == 1:
            return next(iter(found))
        if len(found) > 1 or time.monotonic() >= deadline:
            raise SystemExit('draft persistence unverified')
        time.sleep(2)


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

    settings = load_settings()
    try:
        save_draft(payload, settings, args.leave_open)
    except Exception as exc:
        raise SystemExit(f'draft persistence unverified: {type(exc).__name__}') from None
    print('draft saved to Drafts' + ('' if args.leave_open else ' (window closed)'))


if __name__ == "__main__":
    main()
