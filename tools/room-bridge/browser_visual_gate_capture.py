#!/usr/bin/env python3
"""Capture real Chrome measurements for a CARR Design Kernel visual-gate report.

No Playwright/Puppeteer dependency is required: Chrome's built-in DevTools
Protocol drives a temporary, loopback-only rendered artifact.  A missing or
unlaunchable browser emits a valid ``browser_unavailable`` report whose
critical gates are ``not_verified``; it never guesses a pass from source code.
``--model-room`` measures the real Model Room bundle plus one synthetic typed
wire turn from ``model_room_preview_server`` instead of a single artifact file.
"""
from __future__ import annotations

import argparse
import base64
import contextlib
import http.server
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import socket
import struct
import subprocess
import tempfile
import threading
import time
from collections.abc import Iterator
from datetime import UTC, datetime
from functools import partial
from typing import Any
from urllib.request import urlopen

import design_kernel


ROOT = Path(__file__).resolve().parents[2]
SYSTEM_CHROME = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")


def _disposable_chrome_candidates() -> list[Path]:
    candidates = []
    for name in ("CHROME_FOR_TESTING_PATH", "PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH"):
        if os.environ.get(name):
            candidates.append(Path(os.environ[name]))
    cache = Path(os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "")) if os.environ.get(
        "PLAYWRIGHT_BROWSERS_PATH") else Path.home() / "Library/Caches/ms-playwright"
    releases = [path for path in cache.glob("chromium-*")
                if path.name.removeprefix("chromium-").isdigit()]
    for release in sorted(
            releases,
            key=lambda path: int(path.name.removeprefix("chromium-")),
            reverse=True):
        candidates.extend([
            release / "chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing",
            release / "chrome-mac/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing",
            release / "chrome-linux/chrome",
            release / "chrome-linux64/chrome",
        ])
    return candidates


CHROME_CANDIDATES = _disposable_chrome_candidates()
INTERACTIVE = "button,summary,[href],input,select,textarea,[tabindex]"


def now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _accepted_chrome(candidate: str | Path | None) -> str | None:
    if not candidate:
        return None
    path = Path(candidate)
    try:
        if not path.is_file() or path.resolve() == SYSTEM_CHROME.resolve():
            return None
    except OSError:
        return None
    return str(path)


def chrome_binary(explicit: str | None) -> str | None:
    if explicit:
        return _accepted_chrome(explicit)
    for path in CHROME_CANDIDATES:
        accepted = _accepted_chrome(path)
        if accepted:
            return accepted
    for name in ("google-chrome", "chromium", "chromium-browser"):
        accepted = _accepted_chrome(shutil.which(name))
        if accepted:
            return accepted
    return None


class _SilentHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *_: Any) -> None:
        pass


class DevTools:
    """Minimal local WebSocket DevTools client using only Python stdlib."""

    def __init__(self, url: str):
        host_port, path = url.removeprefix("ws://").split("/", 1)
        host, port = host_port.rsplit(":", 1)
        self.sock = socket.create_connection((host, int(port)), timeout=5)
        key = base64.b64encode(os.urandom(16)).decode()
        request = (
            f"GET /{path} HTTP/1.1\r\nHost: {host_port}\r\nUpgrade: websocket\r\n"
            f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n"
        ).encode()
        self.sock.sendall(request)
        response = self._read_until(b"\r\n\r\n")
        if b" 101 " not in response.split(b"\r\n", 1)[0]:
            raise RuntimeError("Chrome DevTools WebSocket handshake refused")
        self.next_id = 1

    def _read_until(self, marker: bytes) -> bytes:
        data = b""
        while marker not in data:
            part = self.sock.recv(4096)
            if not part:
                raise RuntimeError("DevTools closed during handshake")
            data += part
        return data

    def _recv_exact(self, length: int) -> bytes:
        chunks = b""
        while len(chunks) < length:
            part = self.sock.recv(length - len(chunks))
            if not part:
                raise RuntimeError("DevTools closed")
            chunks += part
        return chunks

    def _send(self, payload: dict[str, Any]) -> None:
        data = json.dumps(payload, separators=(",", ":")).encode()
        mask = os.urandom(4)
        length = len(data)
        prefix = bytes([0x81])
        if length < 126:
            prefix += bytes([0x80 | length])
        elif length < 65536:
            prefix += bytes([0x80 | 126]) + struct.pack("!H", length)
        else:
            prefix += bytes([0x80 | 127]) + struct.pack("!Q", length)
        masked = bytes(byte ^ mask[index % 4] for index, byte in enumerate(data))
        self.sock.sendall(prefix + mask + masked)

    def _receive(self) -> dict[str, Any]:
        header = self._recv_exact(2)
        opcode = header[0] & 0x0F
        masked = bool(header[1] & 0x80)
        length = header[1] & 0x7F
        if length == 126:
            length = struct.unpack("!H", self._recv_exact(2))[0]
        elif length == 127:
            length = struct.unpack("!Q", self._recv_exact(8))[0]
        mask = self._recv_exact(4) if masked else None
        data = self._recv_exact(length)
        if mask:
            data = bytes(byte ^ mask[index % 4] for index, byte in enumerate(data))
        if opcode == 0x8:
            raise RuntimeError("DevTools socket closed")
        if opcode == 0x9:
            self.sock.sendall(b"\x8A\x00")
            return self._receive()
        if opcode != 0x1:
            return self._receive()
        return json.loads(data)

    def call(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        identifier = self.next_id
        self.next_id += 1
        self._send({"id": identifier, "method": method, "params": params or {}})
        while True:
            response = self._receive()
            if response.get("id") == identifier:
                if "error" in response:
                    raise RuntimeError(f"DevTools {method}: {response['error'].get('message', 'unknown error')}")
                return response.get("result", {})

    def evaluate(self, expression: str) -> Any:
        result = self.call("Runtime.evaluate", {"expression": expression, "returnByValue": True, "awaitPromise": True})
        outcome = result.get("result", {})
        if "value" not in outcome:
            raise RuntimeError("DevTools evaluation returned no value")
        return outcome["value"]

    def close(self) -> None:
        with contextlib.suppress(Exception):
            self.sock.close()


MEASURE = r"""
(() => {
  const interactive = [...document.querySelectorAll(%(interactive)s)].filter(node => {
    const style = getComputedStyle(node); const box = node.getBoundingClientRect();
    return style.display !== 'none' && style.visibility !== 'hidden' && box.width > 0 && box.height > 0;
  });
  const all = [...document.querySelectorAll('*')];
  const overflowing = all.filter(node => {
    const box = node.getBoundingClientRect(); const style = getComputedStyle(node);
    return style.display !== 'none' && box.right > innerWidth + 1 && style.position !== 'fixed';
  });
  const clipped = overflowing.filter(node => {
    for (let parent = node.parentElement; parent; parent = parent.parentElement) {
      const overflow = getComputedStyle(parent).overflowX;
      if (overflow === 'hidden' || overflow === 'clip') return true;
    }
    return false;
  });
  const root = getComputedStyle(document.documentElement);
  const body = getComputedStyle(document.body);
  const active = document.activeElement;
  const activeStyle = active ? getComputedStyle(active) : null;
  const status = document.querySelector(%(status)s);
  const directColorDeclarations = [...document.styleSheets].flatMap(sheet => {
    try { return [...sheet.cssRules].map(rule => rule.cssText); } catch (_) { return []; }
  }).flatMap(text => text.match(/(?:^|[;{])\s*(?!-)[a-z-]+\s*:\s*(?:#[0-9a-f]{3,8}\b|rgba?\()/gi) || []);
  const hidden = all.filter(node => { const style = getComputedStyle(node); return Number(style.opacity) === 0 && style.visibility !== 'hidden'; }).length;
  return {
    viewport_width_px: innerWidth,
    document_scroll_width_px: Math.max(document.documentElement.scrollWidth, document.body.scrollWidth),
    overflowing_elements: overflowing.length,
    unintended_clip_count: clipped.length,
    interactive_count: interactive.length,
    minimum_target_px: interactive.length ? Math.min(...interactive.map(node => Math.min(node.getBoundingClientRect().width, node.getBoundingClientRect().height))) : null,
    active_tag: active ? active.tagName.toLowerCase() : null,
    active_outline_width: activeStyle ? activeStyle.outlineWidth : null,
    active_outline_style: activeStyle ? activeStyle.outlineStyle : null,
    active_outline_color: activeStyle ? activeStyle.outlineColor : null,
    content_visible_count: all.length - hidden,
    hidden_animated_candidate_count: hidden,
    running_animation_count: document.getAnimations().filter(animation => animation.playState === 'running').length,
    body_background: body.backgroundColor,
    body_color: body.color,
    theme_surface_value: root.getPropertyValue('--surface-canvas').trim(),
    semantic_token_values: ['--surface-canvas','--surface-raised','--content-primary','--component-card-background','--component-control-focus-outline'].map(key => [key, root.getPropertyValue(key).trim()]),
    unapproved_direct_color_declarations: directColorDeclarations.length,
    status_text: status ? status.textContent.trim() : '',
    status_selector_found: Boolean(status)
  };
})()
"""


def _capture(cdp: DevTools, url: str, width: int, *, dark: bool = False, reduced: bool = False, tab: bool = False) -> dict[str, Any]:
    cdp.call("Emulation.setDeviceMetricsOverride", {"width": width, "height": 900, "deviceScaleFactor": 1, "mobile": False})
    cdp.call("Emulation.setEmulatedMedia", {"features": [
        {"name": "prefers-color-scheme", "value": "dark" if dark else "light"},
        {"name": "prefers-reduced-motion", "value": "reduce" if reduced else "no-preference"},
    ]})
    cdp.call("Page.navigate", {"url": url})
    for _ in range(80):
        if cdp.evaluate("document.readyState") == "complete":
            break
        time.sleep(0.05)
    cdp.evaluate("document.documentElement.dataset.theme = " + json.dumps("dark" if dark else "light"))
    if tab:
        cdp.call("Input.dispatchKeyEvent", {"type": "keyDown", "key": "Tab", "code": "Tab", "windowsVirtualKeyCode": 9, "nativeVirtualKeyCode": 9})
        cdp.call("Input.dispatchKeyEvent", {"type": "keyUp", "key": "Tab", "code": "Tab", "windowsVirtualKeyCode": 9, "nativeVirtualKeyCode": 9})
    return cdp.evaluate(MEASURE % {"interactive": json.dumps(INTERACTIVE), "status": json.dumps(".state,[data-non-color-status]")})


def _result(gate_id: str, status: str, measurement: dict[str, Any], report_id: str) -> dict[str, Any]:
    return {"gate_id": gate_id, "status": status, "evidence_refs": [f"browser_measurement:{report_id}:{gate_id}"], "measurement": measurement}


def _unavailable_report(kernel: dict[str, Any], args: argparse.Namespace, reason: str) -> dict[str, Any]:
    intent = next(row for row in kernel["design_intents"] if row["intent_id"] == args.intent)
    required = next(row["required_gates"] for row in kernel["visual_gate_portfolio"]["profiles"] if row["profile_id"] == intent["evaluation_profile"])
    results = [_result(gate, "not_applicable" if gate == "rtl_when_relevant" and args.rtl == "not_applicable" else "not_verified", {"reason": reason}, args.report_id) for gate in required]
    critical = set(kernel["visual_gate_portfolio"]["critical_gate_ids"])
    blockers = sorted(row["gate_id"] for row in results if row["gate_id"] in critical and row["status"] != "passed")
    return {
        "schema_version": "carr-visual-gate-report.v1", "report_id": args.report_id,
        "kernel_binding": {"contract_id": kernel["contract_id"], "version": kernel["version"], "content_digest": design_kernel.canonical_digest(kernel), "adapter_id": args.adapter_id},
        "target": {"intent_id": args.intent, "surface_family": args.surface_family, "work_request_id": args.work_request_id, "projection_digest": args.projection_digest},
        "evidence": {"runner": "browser_unavailable", "browser": "unavailable", "captured_at": now(), "refs": [f"browser_unavailable:{reason}"]},
        "gate_results": results,
        "aesthetic_critique": {"verdict": "not_run", "evidence_refs": [f"browser_unavailable:{reason}"], "authority": "advisory_never_promotion"},
        "admission": {"aggregate_score": None, "state": "not_admitted", "critical_blockers": blockers, "promotion": "not_performed"},
    }


@contextlib.contextmanager
def serving_target(args: argparse.Namespace) -> Iterator[str]:
    @contextlib.contextmanager
    def _loopback(handler: Any) -> Iterator[http.server.ThreadingHTTPServer]:
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            yield server
        finally:
            server.shutdown()
            server.server_close()

    if args.model_room:
        import model_room_preview_server

        with _loopback(model_room_preview_server.PreviewHandler) as server:
            yield f"http://127.0.0.1:{server.server_port}/room.html"
        return
    artifact = Path(args.artifact).resolve()
    if not artifact.is_file():
        raise FileNotFoundError(f"artifact does not exist: {artifact}")
    with tempfile.TemporaryDirectory(prefix="carr-visual-gate-") as directory:
        (Path(directory) / "artifact.html").write_bytes(artifact.read_bytes())
        with _loopback(partial(_SilentHandler, directory=directory)) as server:
            yield f"http://127.0.0.1:{server.server_port}/artifact.html"


def capture(args: argparse.Namespace) -> dict[str, Any]:
    kernel = design_kernel.validate_design_kernel(json.loads(Path(args.kernel).read_text()))
    if not any(row["intent_id"] == args.intent for row in kernel["design_intents"]):
        raise design_kernel.DesignKernelError("unknown design intent")
    browser = chrome_binary(args.chrome)
    if browser is None:
        return _unavailable_report(kernel, args, "chrome_not_found")
    with tempfile.TemporaryDirectory(prefix="carr-visual-gate-") as directory, serving_target(args) as url:
        profile = Path(directory) / "chrome-profile"
        command = [browser, "--headless=new", "--no-first-run", "--disable-gpu", "--remote-debugging-port=0", f"--user-data-dir={profile}", "about:blank"]
        process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        cdp: DevTools | None = None
        try:
            active_file = profile / "DevToolsActivePort"
            for _ in range(100):
                if active_file.is_file():
                    break
                if process.poll() is not None:
                    return _unavailable_report(kernel, args, "chrome_launch_failed")
                time.sleep(0.05)
            if not active_file.is_file():
                return _unavailable_report(kernel, args, "chrome_devtools_timeout")
            port = active_file.read_text().splitlines()[0]
            targets = json.loads(urlopen(f"http://127.0.0.1:{port}/json/list", timeout=5).read())
            target = next((item for item in targets if item.get("type") == "page"), None)
            if target is None:
                return _unavailable_report(kernel, args, "chrome_page_target_missing")
            cdp = DevTools(target["webSocketDebuggerUrl"])
            cdp.call("Page.enable")
            narrow = {width: _capture(cdp, url, width, tab=(width == 280)) for width in (280, 320, 414)}
            light = _capture(cdp, url, 1024, dark=False)
            dark = _capture(cdp, url, 1024, dark=True)
            reduced = _capture(cdp, url, 1024, reduced=True)
        except Exception as exc:
            return _unavailable_report(kernel, args, "browser_measurement_failed:" + re.sub(r"[^a-z0-9_-]+", "_", str(exc).lower())[:80])
        finally:
            if cdp is not None:
                cdp.close()
            process.terminate()
            with contextlib.suppress(Exception):
                process.wait(timeout=3)

    profile = next(row["evaluation_profile"] for row in kernel["design_intents"] if row["intent_id"] == args.intent)
    required = next(row["required_gates"] for row in kernel["visual_gate_portfolio"]["profiles"] if row["profile_id"] == profile)
    results: list[dict[str, Any]] = []
    for gate in required:
        if gate in design_kernel.VIEWPORT_GATES:
            measurement = narrow[design_kernel.VIEWPORT_GATES[gate]]
            status = "passed" if measurement["document_scroll_width_px"] <= measurement["viewport_width_px"] + 1 and not measurement["unintended_clip_count"] else "failed"
        elif gate == "overflow_clip":
            measurement = {str(width): {"overflowing_elements": row["overflowing_elements"], "unintended_clip_count": row["unintended_clip_count"]} for width, row in narrow.items()}
            status = "passed" if all(row["unintended_clip_count"] == 0 and row["document_scroll_width_px"] <= row["viewport_width_px"] + 1 for row in narrow.values()) else "failed"
        elif gate == "target_size":
            measurement = {"minimum_target_px": light["minimum_target_px"], "interactive_count": light["interactive_count"]}
            status = "passed" if measurement["interactive_count"] > 0 and measurement["minimum_target_px"] >= 44 else "failed"
        elif gate == "keyboard":
            measurement = {key: narrow[280][key] for key in ("interactive_count", "active_tag", "active_outline_width", "active_outline_style", "active_outline_color")}
            status = "passed" if measurement["interactive_count"] and measurement["active_tag"] not in {"body", "html", None} else "failed"
        elif gate == "focus_visible":
            measurement = {key: narrow[280][key] for key in ("active_tag", "active_outline_width", "active_outline_style", "active_outline_color")}
            status = "passed" if measurement["active_outline_style"] not in {"none", None} and measurement["active_outline_width"] not in {"0px", None} else "failed"
        elif gate == "reduced_motion":
            measurement = {key: reduced[key] for key in ("running_animation_count", "content_visible_count", "hidden_animated_candidate_count")}
            status = "passed" if measurement["running_animation_count"] == 0 and measurement["hidden_animated_candidate_count"] == 0 else "failed"
        elif gate in {"light_theme", "dark_theme"}:
            chosen = light if gate == "light_theme" else dark
            measurement = {"theme": "light" if gate == "light_theme" else "dark", "body_background": chosen["body_background"], "body_color": chosen["body_color"], "theme_surface_value": chosen["theme_surface_value"], "other_theme_background": (dark if chosen is light else light)["body_background"]}
            status = "passed" if measurement["body_background"] != measurement["other_theme_background"] else "not_verified"
        elif gate == "semantic_tokens":
            measurement = {"semantic_token_values": light["semantic_token_values"]}
            status = "passed" if all(value for _, value in measurement["semantic_token_values"]) else "failed"
        elif gate == "hardcode_lint":
            measurement = {"unapproved_direct_color_declarations": light["unapproved_direct_color_declarations"]}
            status = "passed" if measurement["unapproved_direct_color_declarations"] == 0 else "failed"
        elif gate == "non_color_meaning":
            measurement = {"status_selector_found": light["status_selector_found"], "status_text": light["status_text"]}
            status = "passed" if measurement["status_selector_found"] and measurement["status_text"] else "failed"
        elif gate == "rtl_when_relevant":
            measurement = {"relevance": args.rtl}
            status = "not_applicable" if args.rtl == "not_applicable" else "not_verified"
        else:
            measurement, status = {"reason": "unimplemented_gate"}, "not_verified"
        results.append(_result(gate, status, measurement, args.report_id))
    critical = set(kernel["visual_gate_portfolio"]["critical_gate_ids"])
    blockers = sorted(row["gate_id"] for row in results if row["gate_id"] in critical and row["status"] != "passed")
    report = {
        "schema_version": "carr-visual-gate-report.v1", "report_id": args.report_id,
        "kernel_binding": {"contract_id": kernel["contract_id"], "version": kernel["version"], "content_digest": design_kernel.canonical_digest(kernel), "adapter_id": args.adapter_id},
        "target": {"intent_id": args.intent, "surface_family": args.surface_family, "work_request_id": args.work_request_id, "projection_digest": args.projection_digest},
        "evidence": {"runner": "real_browser_measurement", "browser": Path(browser).name, "captured_at": now(), "refs": [f"browser_measurement:{args.report_id}:run"]},
        "gate_results": results,
        "aesthetic_critique": {"verdict": "not_run", "evidence_refs": [f"browser_measurement:{args.report_id}:run"], "authority": "advisory_never_promotion"},
        "admission": {"aggregate_score": None, "state": "not_admitted" if blockers else "eligible_for_controller_review", "critical_blockers": blockers, "promotion": "not_performed"},
    }
    return design_kernel.validate_visual_gate_report(report, kernel)


def main() -> int:
    parser = argparse.ArgumentParser(description="Capture exact-bound real-browser CARR visual gate evidence")
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--artifact", type=Path)
    target.add_argument("--model-room", action="store_true",
                        help="serve the real Model Room bundle plus one synthetic typed wire turn from "
                             "tools/room-bridge/model_room_preview_server.py on an ephemeral loopback port "
                             "(test-only; never writes a room turn)")
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--kernel", default=ROOT / "design" / "carr-design-kernel.v1.json", type=Path)
    parser.add_argument("--intent", default="intent:job-passport")
    parser.add_argument("--surface-family", default="model-room")
    parser.add_argument("--work-request-id", required=True)
    parser.add_argument("--projection-digest", required=True)
    parser.add_argument("--adapter-id", default="adapter:job-passport-html")
    parser.add_argument("--report-id", default="report:browser-visual-gate")
    parser.add_argument("--rtl", choices=["not_applicable", "relevant"], default="not_applicable")
    parser.add_argument("--chrome")
    args = parser.parse_args()
    report = capture(args)
    args.out.write_text(json.dumps(report, sort_keys=True, separators=(",", ":")) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
