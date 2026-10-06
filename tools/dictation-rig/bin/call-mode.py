#!/usr/bin/env python3
"""Local Deal Room control surface for Quill meeting mode.

The browser cannot start a macOS menu-bar process directly. This loopback-only
companion gives the Deal Room a narrow, observable bridge to the Quill instance
that is already installed on the partner's Mac. It never records audio itself,
never reads transcript text, and has no send path.

GET  /api/state              current recording/transcription state
GET  /api/post-call          private local review report for one session
POST /api/start {mode}       mode = weekly_deal_call | other_call
POST /api/stop               stop the active Quill recording
POST /api/call-context       exact active-deal/participant index
POST /api/post-call/sync     verify Cloudflare candidate dispositions
POST /api/post-call/drafts/<id>/create   create an Outlook draft, never send
GET  /                       standalone Call Mode control surface

Security boundary:
* binds only to 127.0.0.1;
* state contains no transcript or deal data;
* state-changing cross-origin requests are accepted only from the Deal Room
  (app.doctorcre.com or dealroom.doctorcre.com);
* POST requires a non-simple header, so another site cannot submit a form at it.
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import json
import os
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

import post_call


HOST = "127.0.0.1"
PORT = 4682
RECORDINGS = Path.home() / "Recordings"
CAPTURE_CONFIG = Path.home() / ".config" / "carr-capture" / "config.json"
LOG_PATH = Path.home() / "Library" / "Logs" / "carr-call-mode.log"
LOCK_PATH = Path.home() / "Library" / "Application Support" / "CARR Call Mode" / "operation.lock"
CONTEXT_FILE = "call-context.json"
# The Deal Room is served from both hosts: partners now open it at
# app.doctorcre.com (the doctorcre-app Worker, whose CSP already admits
# connect-src http://127.0.0.1:4682); dealroom.doctorcre.com remains the
# record layer's own host. Any other origin is refused.
ALLOWED_ORIGINS = {
    "https://app.doctorcre.com",
    "https://dealroom.doctorcre.com",
    "http://127.0.0.1:8787",
    "http://localhost:8787",
    "http://127.0.0.1:4682",
    "http://localhost:4682",
}
POST_HEADER = "X-CARR-Call-Mode"
POST_HEADER_VALUE = "deal-room-v1"
TERMINAL_AGE_SECONDS = 6 * 60 * 60
ACTIVE_FILE_WINDOW_SECONDS = 90
SMALL_BODY_LIMIT = 4096
CONTEXT_BODY_LIMIT = 256 * 1024


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_utc(value: datetime | None = None) -> str:
    return (value or utc_now()).strftime("%Y-%m-%dT%H:%M:%SZ")


def log(message: str) -> None:
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with LOG_PATH.open("a", encoding="utf-8") as handle:
            handle.write(f"{iso_utc()} {message}\n")
    except OSError:
        pass


def read_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def partner_from_device() -> str | None:
    config = read_json(CAPTURE_CONFIG) or {}
    device = str(config.get("device_id") or "").lower()
    if "joe" in device:
        return "Joe"
    if "dell" in device:
        return "Dell"
    return None


def session_dirs(recordings: Path = RECORDINGS) -> list[Path]:
    try:
        return sorted(
            (path for path in recordings.iterdir() if path.is_dir()),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
    except OSError:
        return []


def partial_session_age(path: Path) -> float | None:
    tracks = [path / name for name in ("mic.caf", "system.caf") if (path / name).exists()]
    if not tracks or (path / "meta.json").exists():
        return None
    try:
        return max(0.0, time.time() - max(track.stat().st_mtime for track in tracks))
    except OSError:
        return None


def is_recording(path: Path) -> bool:
    age = partial_session_age(path)
    return age is not None and age <= ACTIVE_FILE_WINDOW_SECONDS


@contextlib.contextmanager
def operation_lock():
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOCK_PATH.open("a+", encoding="utf-8") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("another Call Mode action is still in progress") from exc
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def started_at(path: Path) -> str:
    context = read_json(path / CONTEXT_FILE) or {}
    if context.get("started_at"):
        return str(context["started_at"])
    try:
        stamp = getattr(path.stat(), "st_birthtime", path.stat().st_mtime)
        return iso_utc(datetime.fromtimestamp(stamp, timezone.utc))
    except OSError:
        return iso_utc()


def labels_for(mode: str, local_partner: str | None) -> tuple[str, str]:
    mic = local_partner or "Me"
    if mode == "weekly_deal_call" and local_partner == "Joe":
        return mic, "Dell"
    if mode == "weekly_deal_call" and local_partner == "Dell":
        return mic, "Joe"
    return mic, "Other participant"


def write_call_context(path: Path, mode: str, local_partner: str | None) -> dict[str, Any]:
    mic, system = labels_for(mode, local_partner)
    context = {
        "schema": 1,
        "mode": mode,
        "started_at": iso_utc(),
        "recorder": local_partner,
        "speaker_labels": {"mic": mic, "system": system},
        "speaker_method": "separate audio channels; no third-party voiceprint",
    }
    context_path = path / CONTEXT_FILE
    context_path.write_text(
        json.dumps(context, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    context_path.chmod(0o600)
    return context


def current_state(recordings: Path = RECORDINGS) -> dict[str, Any]:
    local_partner = partner_from_device()
    directories = session_dirs(recordings)
    active = next((path for path in directories if is_recording(path)), None)
    if active:
        context = read_json(active / CONTEXT_FILE) or {}
        labels = context.get("speaker_labels") or {
            "mic": local_partner or "Me",
            "system": "Other participant",
        }
        return {
            "state": "recording",
            "started_at": started_at(active),
            "session": active.name,
            "mode": context.get("mode") or "other_call",
            "speaker_labels": labels,
            "local_partner": local_partner,
        }

    newest = directories[0] if directories else None
    if newest:
        try:
            age = time.time() - newest.stat().st_mtime
        except OSError:
            age = TERMINAL_AGE_SECONDS + 1
        if age <= TERMINAL_AGE_SECONDS:
            context = read_json(newest / CONTEXT_FILE) or {}
            base = {
                "session": newest.name,
                "mode": context.get("mode") or "other_call",
                "speaker_labels": context.get("speaker_labels") or {},
                "local_partner": local_partner,
            }
            if (newest / "ingested.json").exists():
                return {"state": "filed", **base}
            if (newest / "transcript.json").exists():
                return {"state": "ready_to_extract", **base}
            if (newest / "meta.json").exists():
                return {"state": "transcribing", **base}
            if partial_session_age(newest) is not None:
                return {"state": "state_unknown", **base}

    return {"state": "idle", "local_partner": local_partner}


def run_quill_menu_item(title: str) -> None:
    """Click Quill's existing Start/Stop menu item through macOS accessibility."""
    uid = os.getuid()
    subprocess.run(
        ["/bin/launchctl", "kickstart", f"gui/{uid}/com.digimata.quill"],
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )
    script = f'''
tell application "System Events"
  if not (exists process "quill") then error "Quill is not running"
  tell process "quill"
    set targetItem to missing value
    repeat with aBar in menu bars
      repeat with aButton in menu bar items of aBar
        try
          click aButton
          delay 0.15
          if exists menu item "{title}" of menu 1 of aButton then
            set targetItem to menu item "{title}" of menu 1 of aButton
            exit repeat
          end if
          key code 53
        end try
      end repeat
      if targetItem is not missing value then exit repeat
    end repeat
    if targetItem is missing value then error "Quill menu item not found: {title}"
    click targetItem
  end tell
end tell
'''
    result = subprocess.run(
        ["/usr/bin/osascript", "-e", script],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "macOS refused the menu action").strip()
        raise RuntimeError(detail[-500:])


def wait_for(predicate, timeout: float = 6.0) -> Path | None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        found = predicate()
        if found:
            return found
        time.sleep(0.15)
    return None


def start_recording(mode: str, recordings: Path = RECORDINGS) -> dict[str, Any]:
    if mode not in {"weekly_deal_call", "other_call"}:
        raise ValueError("mode must be weekly_deal_call or other_call")
    with operation_lock():
        already = next((path for path in session_dirs(recordings) if is_recording(path)), None)
        if already:
            return current_state(recordings)
        before = {path.name for path in session_dirs(recordings)}
        run_quill_menu_item("Start recording")

        def new_active() -> Path | None:
            return next(
                (path for path in session_dirs(recordings) if path.name not in before and is_recording(path)),
                None,
            )

        active = wait_for(new_active)
        if not active:
            raise RuntimeError("Quill did not create a recording session")
        write_call_context(active, mode, partner_from_device())
        log(f"START mode={mode} session={active.name}")
        return current_state(recordings)


def stop_recording(recordings: Path = RECORDINGS) -> dict[str, Any]:
    with operation_lock():
        active = next((path for path in session_dirs(recordings) if is_recording(path)), None)
        if not active:
            return current_state(recordings)
        run_quill_menu_item("Stop recording")
        stopped = wait_for(lambda: active if (active / "meta.json").exists() else None)
        if not stopped:
            raise RuntimeError("Quill did not finish the recording session")
        log(f"STOP session={active.name}")
        return current_state(recordings)


def session_path(session: str, recordings: Path | None = None) -> Path:
    """Resolve one opaque local session name without permitting path traversal."""
    if not session or session in {".", ".."} or "/" in session or "\\" in session:
        raise ValueError("invalid recording session")
    root = (recordings or RECORDINGS).resolve()
    path = (root / session).resolve()
    if path.parent != root or not path.is_dir():
        raise ValueError("recording session not found")
    return path


def sync_post_call(path: Path, runner: Any = subprocess.run) -> dict[str, Any]:
    """Verify remote dispositions through the device-authenticated local bridge."""
    bridge = Path(__file__).with_name("capture-bridge.py")
    result = runner(
        [sys.executable, str(bridge), "poll", str(path)],
        capture_output=True, text=True, timeout=20, check=False,
    )
    if result.returncode != 0:
        raise RuntimeError("Call Mode could not verify the post-call review state")
    return post_call.report_for_deal_room(path)


CALL_MODE_HTML = r'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>CARR Call Mode</title><style>
:root{--navy:#002f6c;--deep:#00224D;--orange:#f57f29;--paper:#f4eddf;--card:#fffaf0;--ink:#172235;--muted:#687486;--line:#ddd1bc;--red:#c23b32}
*{box-sizing:border-box}body{margin:0;background:var(--paper);color:var(--ink);font:15px "Avenir Next","Segoe UI",sans-serif;min-height:100vh;display:grid;place-items:center;padding:18px}
main{width:min(560px,100%);background:var(--card);border:1px solid var(--line);border-radius:20px;box-shadow:0 22px 70px rgba(0,31,73,.2);overflow:hidden}
header{background:var(--deep);color:white;padding:18px 22px;display:flex;align-items:center;justify-content:space-between}.brand{font-weight:850;letter-spacing:.05em;text-transform:uppercase}.bridge{font-size:11px;color:#b9c7d9}
.stage{padding:30px 24px;text-align:center}.orb{width:116px;height:116px;margin:0 auto 16px;border-radius:50%;display:grid;place-items:center;background:#e9e4da;border:1px solid var(--line);font-size:46px}.recording .orb{background:#f8dfdc;color:var(--red);border:8px solid rgba(194,59,50,.18);animation:pulse 1.7s infinite}.timer{font:800 40px ui-monospace,SFMono-Regular,monospace;letter-spacing:.03em}.status{font-size:13px;color:var(--muted);margin-top:7px}.speakers{margin:22px 0 0;padding:12px;border-radius:12px;background:#f3ecdf;color:var(--muted);font-size:13px}.speakers b{color:var(--ink)}
.actions{display:grid;gap:10px;padding:0 24px 24px}.row{display:grid;grid-template-columns:1fr 1fr;gap:10px}button{min-height:50px;border-radius:11px;border:1px solid var(--line);font:800 14px inherit;cursor:pointer;padding:10px 14px}.primary{background:var(--navy);color:white;border-color:var(--navy)}.danger{background:var(--red);color:white;border-color:var(--red)}button:disabled{opacity:.55;cursor:not-allowed}.note{font-size:11px;color:var(--muted);line-height:1.5;text-align:center;padding:0 24px 24px}
@keyframes pulse{50%{box-shadow:0 0 0 16px rgba(194,59,50,0)}}@media(prefers-reduced-motion:reduce){*{animation:none!important}}
</style></head><body><main><header><span class="brand">CARR Call Mode</span><span class="bridge">Quill local recorder</span></header>
<section class="stage" id="stage"><div class="orb">●</div><div class="timer" id="timer">Ready</div><div class="status" id="status">Quill is ready on this Mac.</div><div class="speakers" id="speakers">Separate tracks identify the local partner and the other side without a third-party voiceprint.</div></section>
<section class="actions"><label id="consentRow" style="display:flex;gap:10px;align-items:flex-start;font-size:12px;color:var(--muted);line-height:1.4"><input id="consent" type="checkbox" style="margin-top:2px">I have told everyone on this call that it will be recorded.</label><div class="row" id="startRow"><button class="primary" data-start="weekly_deal_call">Start weekly deal call</button><button data-start="other_call">Start another call</button></div><button class="danger" id="stop" hidden>Stop and process</button></section>
<p class="note">The audible recording announcement remains part of Quill. Stopping the call starts local transcription. Deal updates stay in review until Joe or Dell confirms them.</p></main>
<script>
const $=(s)=>document.querySelector(s);let snapshot={state:'idle'};
const elapsed=(iso)=>{if(!iso)return'0:00';const n=Math.max(0,Math.floor((Date.now()-Date.parse(iso))/1000));return Math.floor(n/60)+':'+String(n%60).padStart(2,'0')};
function paint(s){snapshot=s;const rec=s.state==='recording';$('#stage').classList.toggle('recording',rec);$('#startRow').hidden=rec;$('#consentRow').hidden=rec;$('#stop').hidden=!rec;
  $('#timer').textContent=rec?elapsed(s.started_at):({idle:'Ready',transcribing:'Processing',ready_to_extract:'Transcript ready',filed:'Summary saved',state_unknown:'Check Quill'}[s.state]||s.state);
  $('#status').textContent=rec?'Recording is live.':s.state==='transcribing'?'Quill is transcribing both audio tracks locally.':s.state==='ready_to_extract'?'The local transcript is ready for extraction.':s.state==='filed'?'The confirmed meeting summary has been saved.':s.state==='state_unknown'?'A partial session was found, but Call Mode cannot verify that Quill is still recording.':'Quill is ready on this Mac.';
  const l=s.speaker_labels||{};$('#speakers').innerHTML=l.mic?'<b>'+l.mic+'</b> on the microphone · <b>'+l.system+'</b> on system audio':'Separate tracks identify the local partner and the other side without a third-party voiceprint.'}
async function api(path,body){const r=await fetch('/api/'+path,{method:body?'POST':'GET',headers:body?{'content-type':'application/json','X-CARR-Call-Mode':'deal-room-v1'}:{},body:body?JSON.stringify(body):undefined});const d=await r.json();if(!r.ok)throw new Error(d.error||'Call Mode failed');return d}
async function refresh(){try{paint(await api('state'))}catch(e){$('#status').textContent=e.message}}
document.addEventListener('click',async(e)=>{const b=e.target.closest('[data-start]');try{if(b){if(!$('#consent').checked)throw new Error('Confirm that everyone has been told before recording.');b.disabled=true;paint(await api('start',{mode:b.dataset.start,consent_confirmed:true}))}if(e.target.id==='stop'){e.target.disabled=true;paint(await api('stop',{}))}}catch(err){alert(err.message)}finally{document.querySelectorAll('button').forEach((button)=>{button.disabled=false});refresh()}});
setInterval(()=>{if(snapshot.state==='recording')$('#timer').textContent=elapsed(snapshot.started_at)},250);setInterval(refresh,1200);refresh();
</script></body></html>'''


class CallModeHandler(BaseHTTPRequestHandler):
    server_version = "CARRCallMode/1.0"

    def log_message(self, fmt: str, *args: Any) -> None:
        log("HTTP " + (fmt % args))

    def allowed_origin(self) -> str | None:
        origin = self.headers.get("Origin")
        return origin if origin in ALLOWED_ORIGINS else None

    def send_common(self, content_type: str, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        origin = self.allowed_origin()
        if origin:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
            self.send_header("Access-Control-Allow-Private-Network", "true")

    def send_json(self, value: dict[str, Any], status: int = 200) -> None:
        body = json.dumps(value).encode("utf-8")
        self.send_common("application/json; charset=utf-8", status)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def require_deal_room(self) -> bool:
        if not self.allowed_origin():
            self.send_json({"error": "origin_not_allowed"}, 403)
            return False
        if self.headers.get(POST_HEADER) != POST_HEADER_VALUE:
            self.send_json({"error": "call_mode_header_required"}, 403)
            return False
        return True

    def read_body(self, limit: int) -> dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError as exc:
            raise ValueError("invalid content length") from exc
        if length < 0 or length > limit:
            raise ValueError("request body too large")
        value = json.loads(self.rfile.read(length) or b"{}")
        if not isinstance(value, dict):
            raise ValueError("request body must be an object")
        return value

    def do_OPTIONS(self) -> None:  # noqa: N802
        if not self.allowed_origin():
            self.send_json({"error": "origin_not_allowed"}, 403)
            return
        self.send_common("text/plain; charset=utf-8", 204)
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", f"Content-Type, {POST_HEADER}")
        self.send_header("Access-Control-Allow-Private-Network", "true")
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/api/state":
            self.send_json(current_state())
            return
        if path == "/api/post-call":
            if not self.require_deal_room():
                return
            try:
                session = (parse_qs(parsed.query).get("session") or [""])[0]
                self.send_json(post_call.report_for_deal_room(session_path(session)))
            except (ValueError, post_call.ContractError) as exc:
                self.send_json({"error": str(exc)}, 400)
            return
        if path in {"/", "/index.html"}:
            body = CALL_MODE_HTML.encode("utf-8")
            self.send_common("text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_json({"error": "not_found"}, 404)

    def do_POST(self) -> None:  # noqa: N802
        if not self.require_deal_room():
            return
        try:
            path = urlparse(self.path).path
            limit = CONTEXT_BODY_LIMIT if path == "/api/call-context" else SMALL_BODY_LIMIT
            body = self.read_body(limit)
            if path == "/api/start":
                if body.get("consent_confirmed") is not True:
                    raise ValueError("confirm that everyone has been told before recording")
                self.send_json(start_recording(str(body.get("mode") or "weekly_deal_call")))
                return
            if path == "/api/stop":
                self.send_json(stop_recording())
                return
            if path == "/api/call-context":
                target = session_path(str(body.get("session") or ""))
                context = post_call.store_context(target, body)
                if (target / "transcript.json").exists():
                    threading.Thread(
                        target=post_call.process_session, args=(target,), daemon=True,
                    ).start()
                self.send_json({"ok": True, "session": context["session"]})
                return
            if path == "/api/post-call/sync":
                target = session_path(str(body.get("session") or ""))
                self.send_json(sync_post_call(target))
                return
            parts = path.split("/")
            if len(parts) == 6 and parts[1:4] == ["api", "post-call", "drafts"] and parts[5] == "create":
                target = session_path(str(body.get("session") or ""))
                sync_post_call(target)
                result = post_call.create_outlook_draft(
                    target, unquote(parts[4]), str(body.get("approved_content_hash") or ""),
                )
                self.send_json(result)
                return
            self.send_json({"error": "not_found"}, 404)
        except post_call.ContractError as exc:
            log(f"ERROR {type(exc).__name__}: {exc}")
            self.send_json({"error": str(exc)}, 400)
        except (ValueError, RuntimeError, OSError, subprocess.SubprocessError) as exc:
            log(f"ERROR {type(exc).__name__}: {exc}")
            self.send_json({"error": str(exc)}, 409)


def serve(host: str = HOST, port: int = PORT) -> None:
    RECORDINGS.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer((host, port), CallModeHandler)
    log(f"READY http://{host}:{port}")
    server.serve_forever()


def main() -> int:
    parser = argparse.ArgumentParser(description="CARR Deal Room Call Mode companion")
    sub = parser.add_subparsers(dest="command", required=True)
    serve_parser = sub.add_parser("serve")
    serve_parser.add_argument("--host", default=HOST)
    serve_parser.add_argument("--port", type=int, default=PORT)
    sub.add_parser("state")
    start_parser = sub.add_parser("start")
    start_parser.add_argument("--mode", choices=["weekly_deal_call", "other_call"], default="weekly_deal_call")
    start_parser.add_argument("--consent-confirmed", action="store_true")
    sub.add_parser("stop")
    args = parser.parse_args()
    if args.command == "serve":
        if args.host not in {"127.0.0.1", "localhost"}:
            raise SystemExit("Call Mode refuses to bind beyond loopback")
        serve(args.host, args.port)
    elif args.command == "state":
        print(json.dumps(current_state(), indent=2))
    elif args.command == "start":
        if not args.consent_confirmed:
            raise SystemExit("refusing to record until --consent-confirmed is present")
        print(json.dumps(start_recording(args.mode), indent=2))
    elif args.command == "stop":
        print(json.dumps(stop_recording(), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
