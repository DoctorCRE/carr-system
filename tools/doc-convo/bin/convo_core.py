#!/usr/bin/env python3
"""Shared conversation pipeline for the terminal loop and panel engine."""

import atexit
import datetime
import hashlib
import importlib.util
import json
import os
import pathlib
import queue
import socket as socketlib
import uuid
import re
import subprocess
import sys
import threading
import time
from collections import deque
from collections.abc import Callable
from typing import IO

import speak

TOOL = pathlib.Path(__file__).resolve().parent.parent
TOOLS_ROOT = TOOL.parent
if str(TOOLS_ROOT) not in sys.path:
    sys.path.insert(0, str(TOOLS_ROOT))
import credential_env  # noqa: E402 — shared long-lived-token loader

RIG = TOOL.parent / "dictation-rig"
WHISPER = "/opt/homebrew/bin/whisper-cli"
MODEL = pathlib.Path.home() / ".cache/whisper-cpp/models/ggml-large-v3-turbo.bin"
VOCAB = RIG / "vocab-prompt.txt"
EARCON = TOOL / "assets" / "earcon-ack.wav"
CONTEXT = TOOL / "assets" / "hot-context.md"
PREAMBLE = TOOL / "prompt" / "preamble.md"
SESSION_FILE = TOOL / "assets" / ".brain-session-id"
SPEAK = TOOL / "bin" / "speak.py"
VENV_PY = TOOL / ".venv-tts" / "bin" / "python"
RENDER_DAEMON = TOOL / "bin" / "render-daemon.py"
RENDER_SOCKET = TOOL / "assets" / ".render.sock"
RENDER_LOG = TOOL / "assets" / "render-daemon.log"
BRAIN_MODEL = os.environ.get("DOC_BRAIN_MODEL", "sonnet")
REPO = TOOLS_ROOT.parent
ROUTE_MODULE = REPO / "ops" / "jev_model_route.py"
ROUTE_POLICY = REPO / "ops" / "config" / "model-routes.v1.json"
ROUTE_LOG = REPO / "out" / "model-routes.jsonl"
# What Jev is told about a voice turn, beside the utterance itself. Constant on
# purpose: the hot-context snapshot is large and is deal data, not task shape.
ROUTE_CONTEXT = ("A spoken question to Dr. CRE, CARR's voice assistant, answered in one short spoken reply "
                 "from a read-only context snapshot, with no live tools and no files opened.")
MIN_BYTES = 20_000  # ~0.6s at 16kHz mono s16 — shorter is a misfire, not speech


def _pipe(stream: IO[str] | None, name: str) -> IO[str]:
    """Narrow a Popen pipe from Optional to real, once and loudly.

    Popen types stdin/stdout/stderr as Optional because they are only real when
    you asked for PIPE. Every Popen in this file asks for all three, so None here
    means the process was constructed wrong — a programming error worth a clear
    exception, not a silent AttributeError deep in a read loop. Narrowing at the
    boundary keeps the call sites readable instead of scattering `if x is None`
    through the streaming loop."""
    if stream is None:
        raise RuntimeError(f"brain process has no {name} pipe — Popen was not given PIPE")
    return stream


class BrainProcess:
    def __init__(self) -> None:
        self.process: subprocess.Popen[str] | None = None
        self.system_prompt: str | None = None
        self.session_id: str | None = None
        self.stderr: deque[str] = deque(maxlen=100)
        self.lock = threading.Lock()

    def _stop(self) -> None:
        process = self.process
        self.process = None
        if process is None or process.poll() is not None:
            return
        try:
            _pipe(process.stdin, "stdin").close()
            process.wait(timeout=2)
        except (OSError, subprocess.TimeoutExpired):
            process.kill()
            process.wait()

    def _drain_stderr(self, process: subprocess.Popen[str]) -> None:
        for line in _pipe(process.stderr, "stderr"):
            self.stderr.append(line.rstrip())

    def _start(self, system_prompt: str) -> subprocess.Popen[str]:
        self._stop()
        cmd = [
            "claude", "-p", "--verbose", "--model", BRAIN_MODEL,
            "--tools", "", "--permission-mode", "dontAsk",
            "--append-system-prompt", system_prompt,
            "--input-format", "stream-json",
            "--output-format", "stream-json",
            "--include-partial-messages",
        ]
        if SESSION_FILE.exists():
            cmd += ["--resume", SESSION_FILE.read_text().strip()]
        # doc-engine (ops/launchd/com.carr.doc-engine.plist) runs this
        # unattended, with no interactive session to refresh a keychain
        # login. Merge the long-lived Claude login into THIS child's env
        # only; os.environ is left untouched. Absent-safe: with no token
        # configured this is exactly the current-process environment, same
        # as before.
        child_env, warning = credential_env.claude_child_env()
        if warning:
            print(f"doc-convo brain: {warning}", flush=True)
        process = subprocess.Popen(
            cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, bufsize=1, env=child_env,
        )
        self.process = process
        self.system_prompt = system_prompt
        self.stderr.clear()
        threading.Thread(
            target=self._drain_stderr, args=(process,), daemon=True,
        ).start()
        return process

    def _ensure(self, system_prompt: str) -> subprocess.Popen[str]:
        if (self.process is None or self.process.poll() is not None or
                self.system_prompt != system_prompt):
            return self._start(system_prompt)
        return self.process

    def ask(self, text: str, system_prompt: str,
            on_text: Callable[[str], None]) -> subprocess.CompletedProcess:
        with self.lock:
            # A silent or noise-only transcription becomes a whitespace-only
            # message, which the stream-json API rejects with a 400.  The
            # doc-convo rig calls `claude` by name, so it may run a PATH binary
            # without the runtime's newer guard.  Keep the rig safe at any
            # version and avoid starting or resuming a brain process for no-op
            # input.
            if not text or not text.strip():
                return subprocess.CompletedProcess([], 0, "", "")
            try:
                process = self._ensure(system_prompt)
            except OSError as exc:
                return subprocess.CompletedProcess([], 1, "", str(exc))
            payload = {
                "type": "user",
                "message": {
                    "role": "user",
                    "content": [{"type": "text", "text": text}],
                },
            }
            try:
                stdin = _pipe(process.stdin, "stdin")
                stdin.write(json.dumps(payload) + "\n")
                stdin.flush()
            except (BrokenPipeError, OSError) as exc:
                self._stop()
                return subprocess.CompletedProcess([], 1, "", str(exc))

            stdout = _pipe(process.stdout, "stdout")
            streamed = False
            result = ""
            is_error = False
            while True:
                line = stdout.readline()
                if not line:
                    try:
                        code = process.wait(timeout=1)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        code = process.wait()
                    self.process = None
                    error = "\n".join(self.stderr) or "brain process ended mid-turn"
                    return subprocess.CompletedProcess([], code or 1, result, error)
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                sid = event.get("session_id") or ""
                if sid:
                    self.session_id = sid
                    SESSION_FILE.parent.mkdir(parents=True, exist_ok=True)
                    SESSION_FILE.write_text(sid)
                if event.get("type") == "stream_event":
                    inner = event.get("event") or {}
                    delta = inner.get("delta") or {}
                    if (inner.get("type") == "content_block_delta" and
                            delta.get("type") == "text_delta"):
                        chunk = delta.get("text") or ""
                        if chunk:
                            streamed = True
                            result += chunk
                            on_text(chunk)
                elif event.get("type") == "result":
                    final = event.get("result") or result
                    is_error = bool(event.get("is_error"))
                    if not streamed and final and not is_error:
                        result = final
                        on_text(final)
                    elif not streamed:
                        result = final
                    error = "\n".join(self.stderr)
                    return subprocess.CompletedProcess(
                        [], 1 if is_error else 0, result, error,
                    )

    def close(self) -> None:
        with self.lock:
            self._stop()


class SentenceStream:
    def __init__(self, callback: Callable[[str], None]) -> None:
        self.callback = callback
        self.pending = ""
        self.card = False

    def feed(self, chunk: str) -> None:
        if self.card:
            return
        self.pending += chunk
        card_at = self.pending.find("\nCARD: ")
        if card_at >= 0:
            self.pending = self.pending[:card_at]
            self._emit_complete()
            self.finish()
            self.card = True
            return
        self._emit_complete()

    def _emit_complete(self) -> None:
        while True:
            if ("CARD: ".startswith(self.pending) or
                    self.pending.startswith("CARD: ")):
                return
            boundary = None
            for index in range(1, len(self.pending)):
                if (self.pending[index - 1] in ".!?" and
                        self.pending[index].isspace()):
                    boundary = index
                    break
            if boundary is None:
                return
            complete = self.pending[:boundary].strip()
            self.pending = self.pending[boundary:].lstrip()
            if complete.startswith("CARD: "):
                continue
            for sentence in speak.split_sentences(complete):
                self.callback(sentence)

    def finish(self) -> None:
        complete = self.pending.strip()
        self.pending = ""
        if not complete or complete.startswith("CARD: "):
            return
        for sentence in speak.split_sentences(complete):
            self.callback(sentence)


def jev_router(judge=None) -> Callable[[str], dict]:
    """The default shadow router: ops/jev_model_route.dispatch() over the one policy file, read fresh per call.

    Loaded lazily, inside the shadow worker, so importing it never costs the voice loop anything. dispatch() runs
    with log_path=None because its own rows carry the task text; RouteShadow writes the hash-only row instead.
    Jev being down is dispatch()'s job: it returns the policy's abstain route with jev_error set."""
    loaded: dict = {}

    def route(text: str) -> dict:
        if "module" not in loaded:
            spec = importlib.util.spec_from_file_location("jev_model_route", ROUTE_MODULE)
            if spec is None or spec.loader is None:
                raise RuntimeError(f"cannot load {ROUTE_MODULE}")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            loaded["module"] = module
        raw = ROUTE_POLICY.read_bytes()
        policy = json.loads(raw)
        out = loaded["module"].dispatch(text, ROUTE_CONTEXT, policy=policy, judge=judge, log_path=None)
        return {**out, "policy_version": policy.get("version"),
                "policy_sha256": hashlib.sha256(raw).hexdigest()}

    return route


class RouteShadow:
    """Records, per voice turn, the model route Jev WOULD pick. Shadow only: nothing here reaches BrainProcess, so
    the answering model stays BRAIN_MODEL whatever the stamp says.

    Off the reply path by construction: submit() is one non-blocking put onto a bounded queue, called after the
    reply has been streamed to speech. One daemon worker routes and writes; a full queue drops the turn and the
    next row counts the drops. Every failure is swallowed into the row or ignored — never raised to the loop.
    DOC_ROUTE_SHADOW=0 disables it (read per turn, default on)."""

    QUEUE_MAX = 8

    def __init__(self, router: Callable[[str], dict] | None = None,
                 log_path: pathlib.Path | None = None) -> None:
        self.router = router
        self.log_path = log_path or ROUTE_LOG
        self.queue: queue.Queue = queue.Queue(maxsize=self.QUEUE_MAX)
        self.dropped = 0
        self._worker: threading.Thread | None = None
        self._start_lock = threading.Lock()

    @staticmethod
    def enabled() -> bool:
        return os.environ.get("DOC_ROUTE_SHADOW", "1").strip() != "0"

    def submit(self, text: str, *, session_id: str | None, brain_returncode: int) -> None:
        try:
            if not text or not text.strip() or not self.enabled():
                return
            self._ensure_worker()
            self.queue.put_nowait({"text": text, "turn_id": uuid.uuid4().hex, "session_id": session_id,
                                   "brain_returncode": brain_returncode})
        except queue.Full:
            self.dropped += 1
        except Exception:
            pass

    def drain(self, timeout: float) -> bool:
        """Wait until every queued turn is written. For tests and orderly shutdown; the loop never calls it."""
        deadline = time.monotonic() + timeout
        while self.queue.unfinished_tasks:
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.01)
        return True

    def _ensure_worker(self) -> None:
        if self._worker is not None and self._worker.is_alive():
            return
        with self._start_lock:
            if self._worker is None or not self._worker.is_alive():
                self._worker = threading.Thread(target=self._run, name="doc-route-shadow", daemon=True)
                self._worker.start()

    def _run(self) -> None:
        while True:
            job = self.queue.get()
            try:
                self._record(job)
            except Exception:
                pass
            finally:
                self.queue.task_done()

    @staticmethod
    def _error_class(message) -> str | None:
        """Only the exception class names and an HTTP status: a vendor error body may echo the request, and the
        request is the utterance (review of #1323)."""
        if not message:
            return None
        names = list(dict.fromkeys(re.findall(r"\b[A-Z][A-Za-z]*(?:Error|Exception|Unavailable|Timeout)\b",
                                              str(message))))
        status = re.search(r"\bHTTP (\d{3})\b", str(message))
        return (": ".join(names) or "error") + (f" HTTP {status.group(1)}" if status else "")

    def _record(self, job: dict) -> None:
        text = job.pop("text")
        row = {"at": datetime.datetime.now(datetime.timezone.utc).isoformat(), "kind": "doc-voice",
               "shadow": True, "turn_id": job["turn_id"], "session_id": job["session_id"],
               "utterance_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
               "used_model": BRAIN_MODEL, "brain_returncode": job["brain_returncode"],
               "route": None, "target": None, "would_model": None, "effort": None, "abstained": None,
               "scores": {}, "jev_error": None, "policy_version": None, "policy_sha256": None, "error": None}
        dropped, self.dropped = self.dropped, 0
        if dropped:
            row["dropped_before"] = dropped
        try:
            out = (self.router or jev_router())(text)
            row.update(route=out.get("route"), target=out.get("target"), would_model=out.get("subagent_model"),
                       effort=out.get("effort"), abstained=bool(out.get("fallback")),
                       scores=out.get("scores") or {}, jev_error=self._error_class(out.get("jev_error")),
                       policy_version=out.get("policy_version"), policy_sha256=out.get("policy_sha256"))
        except Exception as exc:
            row["error"] = self._error_class(f"{type(exc).__name__}: {exc}")
        del text
        try:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with self.log_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(row) + "\n")
        except OSError:
            pass


_BRAIN = BrainProcess()
atexit.register(_BRAIN.close)
_SHADOW = RouteShadow()


def pick_mic() -> str:
    """Find the real microphone by name. Device :0 is NOT safe as a default —
    on Joe's Mac it's "Microsoft Teams Audio", a virtual device that records
    pure silence outside a call (found live, 2026-08-08, -91dB)."""
    if "DOC_MIC_DEVICE" in os.environ:
        return os.environ["DOC_MIC_DEVICE"]
    out = subprocess.run(
        ["ffmpeg", "-f", "avfoundation", "-list_devices", "true", "-i", ""],
        capture_output=True, text=True,
    ).stderr
    devices = []
    in_audio = False
    for line in out.splitlines():
        if "audio devices" in line:
            in_audio = True
            continue
        if in_audio and "] [" in line:
            idx = line.split("] [")[1].split("]")[0]
            name = line.rsplit("]", 1)[1].strip()
            devices.append((idx, name))
    for idx, name in devices:  # the built-in mic is the desk default
        if "MacBook" in name and "Microphone" in name:
            return f":{idx}"
    for idx, name in devices:  # else: any real microphone, never virtual audio
        if "Microphone" in name and "Teams" not in name:
            return f":{idx}"
    return ":0"


def mean_volume(wav: pathlib.Path) -> float | None:
    out = subprocess.run(
        ["ffmpeg", "-i", str(wav), "-af", "volumedetect", "-f", "null", "-"],
        capture_output=True, text=True,
    ).stderr
    for line in out.splitlines():
        if "mean_volume:" in line:
            try:
                return float(line.split("mean_volume:")[1].split("dB")[0])
            except ValueError:
                return None
    return None


def warm_voice() -> None:
    if not VENV_PY.exists():
        return
    if RENDER_SOCKET.exists():
        # A socket FILE is not a live daemon (crash leaves it behind, and then
        # nothing would ever respawn). Probe; unlink the corpse on refusal.
        try:
            with socketlib.socket(socketlib.AF_UNIX, socketlib.SOCK_STREAM) as s:
                s.settimeout(1)
                s.connect(str(RENDER_SOCKET))
            return  # alive — nothing to do
        except OSError:
            RENDER_SOCKET.unlink(missing_ok=True)
    RENDER_LOG.parent.mkdir(parents=True, exist_ok=True)
    with RENDER_LOG.open("a") as log:
        subprocess.Popen(
            [str(VENV_PY), str(RENDER_DAEMON)],
            stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    print("· warming Doc's voice (background)")


def refresh_hot_context() -> str | None:
    age = time.time() - CONTEXT.stat().st_mtime if CONTEXT.exists() else 1e9
    if age > 1800:
        print("· refreshing hot context ...")
        if subprocess.run([str(TOOL / "bin" / "refresh-context.sh")]).returncode != 0:
            print("· refresh failed — using last snapshot if any")
    if not CONTEXT.exists():
        print("no hot-context snapshot and refresh failed")
        return None
    return PREAMBLE.read_text() + "\n" + CONTEXT.read_text()


def transcribe(wav: pathlib.Path) -> str:
    return subprocess.run(
        [WHISPER, "-m", str(MODEL), "-f", str(wav),
         "--prompt", VOCAB.read_text(), "-nt", "-np"],
        capture_output=True, text=True,
    ).stdout.replace("\n", " ").strip()


def ask_brain_streaming(
        text: str, system_prompt: str,
        on_sentence: Callable[[str], None] | None = None,
        on_complete: Callable[[str], None] | None = None,
) -> tuple[str, subprocess.CompletedProcess]:
    sentences = SentenceStream(on_sentence or (lambda _sentence: None))
    brain = _BRAIN.ask(text, system_prompt, sentences.feed)
    if brain.returncode == 0:
        sentences.finish()
    reply = brain.stdout.strip()
    if on_complete is not None:
        on_complete(reply)
    # Shadow routing stamp: the reply is already out; this is one non-blocking enqueue.
    _SHADOW.submit(text, session_id=getattr(_BRAIN, "session_id", None), brain_returncode=brain.returncode)
    return reply, brain


def ask_brain(text: str, system_prompt: str) -> tuple[str, subprocess.CompletedProcess]:
    return ask_brain_streaming(text, system_prompt)
