#!/usr/bin/env python3
"""Hermetic side-effect checks for the isolated Calendar canary.

The Notes canary sender was retired; bin/notes-sweep-post.sh must refuse every
canary invocation instead of falling through to a live post.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import threading
import json
import hashlib
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

REPO = Path(__file__).resolve().parents[1]
CALENDAR = REPO / "bin" / "pull-gmail-calendar.py"
NOTES = REPO / "bin" / "notes-sweep-post.sh"


def main() -> int:
    failed: list[str] = []
    total = 0

    def check(label: str, value: bool) -> None:
        nonlocal total
        total += 1
        print(f"  {'ok  ' if value else 'FAIL'} {label}")
        if not value:
            failed.append(label)

    with tempfile.TemporaryDirectory(prefix="carr-canary-") as raw:
        tmp = Path(raw)
        home = tmp / "home"; home.mkdir()
        root = REPO / "out" / "canary" / f"selftest-{os.getpid()}"
        config = tmp / "calendar-canary.env"
        base = {"HOME": str(home), "CARR_CONTROL_PLANE_MODE": "canary",
                "CARR_CALENDAR_CANARY_ENV": str(config),
                "CARR_CALENDAR_CANARY_ROOT": str(root / "calendar"),
                "CARR_VAULT": str(tmp / "empty-vault"),
                "CARR_INGEST_URL": "https://live.invalid/ingest"}

        def run_calendar() -> subprocess.CompletedProcess[str]:
            return subprocess.run([sys.executable, str(CALENDAR), "--canary"], env={**os.environ, **base}, text=True, capture_output=True)

        def rejected(label: str, writer) -> None:
            before = list(root.rglob("*")) if root.exists() else []
            result = writer()
            after = list(root.rglob("*")) if root.exists() else []
            check(label, result.returncode == 78 and before == after)

        def destination_for(url: str) -> str:
            if len(url) >= 2 and url[0] == url[-1] and url[0] in {"'", '"'}:
                url = url[1:-1]
            parsed = urlsplit(url)
            scheme = parsed.scheme.lower()
            host = parsed.hostname.lower() if parsed.hostname else ""
            if ":" in host and not host.startswith("["):
                host = f"[{host}]"
            port = parsed.port
            authority = host if port is None or (scheme, port) in (("http", 80), ("https", 443)) else f"{host}:{port}"
            normalized = urlunsplit((scheme, authority, parsed.path or "/", parsed.query, ""))
            return hashlib.sha256(normalized.encode("utf-8")).hexdigest()

        def configs(url: str, destination: str | None = None) -> None:
            destination = destination_for(url) if destination is None else destination
            config.write_text(f"CARR_CANARY_INGEST_URL={url}\nCARR_CANARY_INGEST_TOKEN_CALENDAR=calendar-token\nCARR_CANARY_DESTINATION_ID={destination}\n")
            config.chmod(0o600)

        rejected("calendar missing config refuses before isolated writes", run_calendar)
        normal = subprocess.run([sys.executable, str(CALENDAR)],
                                env={**os.environ, **base, "CARR_RECOVERY_REASON": "ambient-bypass"},
                                text=True, capture_output=True)
        check("calendar normal mode refuses before ambient Drive or recovery state",
              normal.returncode == 69 and "MISSING_CANONICAL_SEAM" in normal.stderr
              and "empty-vault" not in normal.stderr)
        missing_root = subprocess.run([sys.executable, str(CALENDAR), "--recovery", "--reason", "direct"],
                                      env={**os.environ, **base}, text=True, capture_output=True)
        check("calendar legacy recovery requires a directly supplied root",
              missing_root.returncode == 2 and "--recovery-root" in missing_root.stderr)
        recovery_root = tmp / "recovery"
        recovery = subprocess.run([sys.executable, str(CALENDAR), "--recovery", "--reason", "direct",
                                   "--recovery-root", str(recovery_root), "--dry-run"],
                                  env={**os.environ, **base, "CARR_CONTROL_PLANE_MODE": ""},
                                  text=True, capture_output=True)
        check("calendar direct recovery is explicit and visibly noncanonical",
              recovery.returncode == 0 and "RECOVERY NONCANONICAL" in recovery.stderr)
        live_log = REPO / "out" / "capture-lanes.log"
        before_live = live_log.read_bytes() if live_log.exists() else None
        gmail = subprocess.run([sys.executable, str(CALENDAR), "--gmail"], env={**os.environ, **base}, text=True, capture_output=True)
        after_live = live_log.read_bytes() if live_log.exists() else None
        check("calendar canary-mode Gmail boundary refuses before any live write", gmail.returncode == 78 and before_live == after_live)

        configs("https://live.invalid/ingest", "isolated")
        rejected("calendar equal live URL refuses before network/local state", run_calendar)

        config.write_text("CARR_CANARY_INGEST_URL=$(bad)\n"); config.chmod(0o600)
        rejected("calendar malformed config refuses before network/local state", run_calendar)

        configs("https://canary.invalid/ingest", "isolated"); config.chmod(0o644)
        rejected("calendar insecure config refuses before network/local state", run_calendar)

        configs("https://canary.invalid/ingest"); config.write_text(config.read_text()+"UNRELATED_TOKEN=x\n")
        rejected("calendar unknown config key refuses before writes", run_calendar)
        configs("https://canary.invalid/ingest"); config.write_text(config.read_text()+"CARR_CANARY_INGEST_URL=https://other.invalid\n")
        rejected("calendar duplicate config key refuses before writes", run_calendar)
        configs("https://canary.invalid/ingest?x=1&y=2")
        outside = tmp / "not-isolated"
        hostile_base = {**base, "CARR_CALENDAR_CANARY_ROOT": str(outside)}
        old_base = base.copy(); base.update(hostile_base)
        rejected("calendar ambient root outside out/canary refuses before writes", run_calendar)
        base.clear(); base.update(old_base)
        root.mkdir(parents=True, exist_ok=True)
        (root / "escape").symlink_to(outside, target_is_directory=True)
        hostile_base = {**base, "CARR_CALENDAR_CANARY_ROOT": str(root / "escape")}
        old_base = base.copy(); base.update(hostile_base)
        rejected("calendar symlink parent escape refuses before writes", run_calendar)
        base.clear(); base.update(old_base)
        (root / "escape").unlink(); root.rmdir()
        configs("https://canary.invalid/ingest", "bad destination")
        rejected("calendar unsafe destination refuses before writes", run_calendar)
        configs("'https://canary.invalid/ingest?x=1&y=2'")
        opaque_destination = destination_for("https://canary.invalid/ingest?x=1&y=2")
        calendar = run_calendar()
        check("calendar valid canary has redacted identity marker", calendar.returncode == 0 and f"mode=canary destination={opaque_destination}" in calendar.stdout and "calendar-token" not in calendar.stdout and "https://canary.invalid" not in calendar.stdout)
        check("calendar valid canary creates only isolated log state", (root / "calendar" / "calendar-pull.log").is_file())
        captured: list[tuple[str, str, dict]] = []
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args): pass
            def do_POST(self):
                body = self.rfile.read(int(self.headers["content-length"]))
                captured.append((self.path, self.headers.get("authorization", ""), json.loads(body)))
                self.send_response(200); self.send_header("content-type", "application/json"); self.end_headers(); self.wfile.write(b'{}')
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler); thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        endpoint = f"http://127.0.0.1:{server.server_port}/canary"
        try:
            root.mkdir(parents=True, exist_ok=True)
            configs(endpoint)
            vault = root / "calendar" / "calendar-input" / "DNA" / "Team"; vault.mkdir(parents=True)
            today = date.today().strftime("%Y%m%d")
            (vault / "calendar-latest.ics").write_text(f"BEGIN:VCALENDAR\nBEGIN:VEVENT\nUID:canary-event\nDTSTART:{today}T120000Z\nDTEND:{today}T130000Z\nSUMMARY:Private Test\nEND:VEVENT\nEND:VCALENDAR\n")
            calendar = run_calendar()
            check("calendar loopback posts one isolated event with exact auth", bool(calendar.returncode == 0 and len(captured) == 1 and captured[0][0] == "/canary" and captured[0][1] == "Bearer calendar-token" and captured[0][2].get("external_id") and "calendar-token" not in calendar.stdout))
        finally:
            server.shutdown(); thread.join(); server.server_close()
        shutil.rmtree(root)

    # The Notes canary sender is gone.  Every canary-shaped invocation must
    # refuse (EX_CONFIG) rather than reach the live post path, and must do so
    # before any osascript scan, network call or state write.
    with tempfile.TemporaryDirectory(prefix="carr-notes-retired-") as raw:
        tmp = Path(raw)
        home = tmp / "home"; home.mkdir()
        live_state = REPO / "out" / "notes-sweep"
        before = sorted(p.name for p in live_state.rglob("*")) if live_state.exists() else None
        env = {**os.environ, "HOME": str(home), "CARR_INGEST_URL": "http://127.0.0.1:9/ingest"}
        for label, argv, mode in (
            ("--canary flag", ["--canary"], None),
            ("--canary flag with control-plane canary mode", ["--canary"], "canary"),
            ("control-plane canary mode without the flag", ["--scheduled"], "canary"),
        ):
            run_env = dict(env)
            if mode:
                run_env["CARR_CONTROL_PLANE_MODE"] = mode
            result = subprocess.run(["zsh", str(NOTES), *argv], env=run_env, text=True,
                                    capture_output=True, timeout=60)
            check(f"retired Notes canary refuses before any scan or post ({label})",
                  result.returncode == 78 and "retired" in result.stderr
                  and "notes-sweep:" not in result.stdout)
        after = sorted(p.name for p in live_state.rglob("*")) if live_state.exists() else None
        check("retired Notes canary refusal leaves live Notes state untouched", before == after)
        source = NOTES.read_text(encoding="utf-8")
        check("notes-sweep-post.sh carries no canary sender code",
              all(token not in source for token in (
                  "CARR_CANARY_INGEST", "CARR_NOTES_CANARY", "emit_canary_result", "notes-canary-result")))

    print(f"control-plane-isolated-canary-selftest — {total-len(failed)}/{total} passed")
    if failed:
        print("FAILED: " + "; ".join(failed)); return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
