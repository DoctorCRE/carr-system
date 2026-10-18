"""What a Flash-written script may import while tools/flash-script.py runs it: llm(prompt) asks the local Flash
model one question. Standard library only, since the scripts run with nothing else installed."""

from __future__ import annotations

import json
import os
import contextlib
import fcntl
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from datetime import datetime

URL = os.environ.get("CARR_FLASH_URL", "http://127.0.0.1:8000") + "/v1/chat/completions"
MODEL = os.environ.get("CARR_FLASH_MODEL", "qwen3.8-flash-next")


def llm(prompt: str, max_tokens: int = 8192) -> str:
    """Ask the local Flash model one question and return its visible answer."""
    body = {"model": MODEL, "max_tokens": max_tokens, "messages": [{"role": "user", "content": prompt}]}
    req = urllib.request.Request(URL, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    with request_scope(URL.rsplit("/v1/", 1)[0]), urllib.request.urlopen(req, timeout=600) as r:
        reply = json.load(r)
    return (reply["choices"][0]["message"].get("content") or "").split("</think>")[-1].strip()


SERVER_LABEL = "local.ds4-flash-next"
DESK_LABEL = "local.flash-desk"
REPO = Path(__file__).resolve().parents[1]
LOCAL_URL = "http://127.0.0.1:8000"
IDLE_SECONDS = 15 * 60
ENSURE_TIMEOUT = 180
STOP_TIMEOUT = 4 * 15 + 30
OFF_REASON = "flash is switched off"


class FlashSwitchedOff(RuntimeError):
    pass


def state_dir():
    return Path(os.environ.get("CARR_FLASH_STATE_DIR", str(Path.home() / ".local/state/carr/flash")))


def off_switch_path():
    override = os.environ.get("CARR_FLASH_STATE_DIR")
    directory = Path(override) if override else Path.home() / ".config/carr"
    return directory / "flash.off"


def is_switched_off():
    return os.path.isfile(off_switch_path())


def _require_enabled():
    if is_switched_off():
        raise FlashSwitchedOff(OFF_REASON)


@contextlib.contextmanager
def lifecycle_lock(*, shared=False, blocking=True, deadline=None, clock=time.monotonic, sleep=time.sleep):
    directory = state_dir()
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / "lifecycle.lock").open("a") as lock:
        operation = fcntl.LOCK_SH if shared else fcntl.LOCK_EX
        if deadline is None:
            fcntl.flock(lock, operation | (0 if blocking else fcntl.LOCK_NB))
        else:
            while True:
                try:
                    fcntl.flock(lock, operation | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if clock() >= deadline:
                        raise TimeoutError("Flash lifecycle lock exceeded readiness deadline")
                    sleep(min(1, deadline - clock()))
        yield


def is_ready(url=LOCAL_URL, *, timeout=2):
    if is_switched_off():
        return False
    try:
        with urllib.request.urlopen(url.rstrip("/") + "/v1/models", timeout=timeout) as response:
            return response.status == 200
    except (OSError, ValueError):
        return False


def _launch(*args):
    return subprocess.run(["/bin/launchctl", *args], capture_output=True, text=True, timeout=15)


def _domain():
    return f"gui/{os.getuid()}"


def _start(label, *, launch=_launch):
    domain = _domain()
    enabled = launch("enable", f"{domain}/{label}")
    if enabled.returncode:
        raise RuntimeError(f"could not enable {label}: {enabled.stderr.strip()}")
    boot = launch("bootstrap", domain, str(Path.home() / f"Library/LaunchAgents/{label}.plist"))
    kick = launch("kickstart", f"{domain}/{label}")
    if kick.returncode:
        raise RuntimeError(f"could not start {label}: {kick.stderr.strip()}; bootstrap: {boot.stderr.strip()}")


def _stop(*, launch=_launch):
    errors = []
    for label in (DESK_LABEL, SERVER_LABEL):
        disabled = launch("disable", f"{_domain()}/{label}")
        if disabled.returncode:
            errors.append(f"disable {label}: {disabled.stderr.strip()}")
        result = launch("bootout", f"{_domain()}/{label}")
        if result.returncode not in (0, 3, 113):
            errors.append(f"bootout {label}: {result.stderr.strip()}")
    result = subprocess.run([sys.executable, str(REPO / "tools/room-bridge/dispatch.py"),
                             "stop", "flash"], capture_output=True, text=True, timeout=30)
    if result.returncode:
        errors.append(f"stop flash desk: {result.stderr.strip()}")
    if errors:
        raise RuntimeError("; ".join(errors))


def ensure(*, timeout=ENSURE_TIMEOUT, ready=is_ready, launch=_launch,
           clock=time.monotonic, sleep=time.sleep):
    """Start once across concurrent callers, with bounded cold readiness."""
    if is_switched_off():
        print(OFF_REASON, file=sys.stderr)
        return False
    deadline = clock() + timeout
    if ready(timeout=min(2, max(0.01, deadline - clock()))):
        return True
    with lifecycle_lock(deadline=deadline, clock=clock, sleep=sleep):
        if is_switched_off():
            print(OFF_REASON, file=sys.stderr)
            return False
        if clock() >= deadline:
            return False
        if ready(timeout=min(2, deadline - clock())):
            return True
        try:
            _start(SERVER_LABEL, launch=launch)
            while clock() < deadline:
                if ready(timeout=min(2, deadline - clock())):
                    return True
                sleep(min(1, max(0, deadline - clock())))
        except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
            print(f"flash-ensure: {exc}", file=sys.stderr)
        # A late load after a failed caller must not remain resident.
        _stop(launch=launch)
        return False


def ensure_server(url=LOCAL_URL, *, timeout=ENSURE_TIMEOUT):
    _require_enabled()
    if url.rstrip("/") != LOCAL_URL:
        return
    try:
        result = subprocess.run([str(REPO / "bin/flash-ensure"), "--timeout", str(timeout)], timeout=timeout + STOP_TIMEOUT + 10)
    except subprocess.TimeoutExpired as exc:
        raise TimeoutError("Flash readiness command timed out") from exc
    if result.returncode:
        raise TimeoutError("Flash did not become ready within 180 seconds")


def ensure_desk(ready, *, launch=_launch, clock=time.monotonic, sleep=time.sleep):
    _require_enabled()
    ensure_server()
    deadline = clock() + 120
    with lifecycle_lock(shared=True, deadline=deadline, clock=clock, sleep=sleep):
        _require_enabled()
        if ready():
            return
    with lifecycle_lock(deadline=deadline, clock=clock, sleep=sleep):
        _require_enabled()
        if ready():
            return
        try:
            _start(DESK_LABEL, launch=launch)
            while clock() < deadline:
                if ready():
                    return
                sleep(min(1, deadline - clock()))
        except (OSError, RuntimeError, subprocess.TimeoutExpired):
            _stop(launch=launch)
            raise
        _stop(launch=launch)
        raise TimeoutError("Flash desk did not start within 120 seconds")


@contextlib.contextmanager
def activity_scope():
    _require_enabled()
    with lifecycle_lock(shared=True):
        activity = state_dir() / "last-request"
        activity.touch()
        try:
            yield
        finally:
            activity.touch()


@contextlib.contextmanager
def request_scope(url=LOCAL_URL, *, opener=None):
    """Injected transports and sandbox children do not control launchd."""
    _require_enabled()
    if (url.rstrip("/") != LOCAL_URL
            or (opener is not None and opener is not urllib.request.urlopen)
            or os.environ.get("CARR_FLASH_PREPARED") == "1"):
        yield
        return
    deadline = time.monotonic() + ENSURE_TIMEOUT
    ensure_server(url, timeout=max(0.01, deadline - time.monotonic()))
    with lifecycle_lock(shared=True, deadline=deadline):
        remaining = deadline - time.monotonic()
        if remaining <= 0 or not is_ready(url, timeout=min(2, remaining)):
            raise TimeoutError("Flash stopped before the request; readiness deadline exhausted")
        activity = state_dir() / "last-request"
        activity.touch()
        try:
            yield
        finally:
            activity.touch()


def server_started(*, launch=_launch, clock=time.time):
    info = launch("print", f"{_domain()}/{SERVER_LABEL}")
    if info.returncode:
        return None
    match = re.search(r"^\s*pid = (\d+)\s*$", info.stdout, re.M)
    if not match:
        return None
    result = subprocess.run(["/bin/ps", "-o", "etime=", "-p", match[1]],
                            capture_output=True, text=True, timeout=5)
    elapsed = result.stdout.strip()
    if result.returncode or not elapsed:
        raise RuntimeError("ds4-server elapsed time unavailable")
    days, separator, remainder = elapsed.partition("-")
    seconds = 0
    for part in (remainder if separator else days).split(":"):
        seconds = seconds * 60 + int(part)
    if separator:
        seconds += int(days) * 86400
    return clock() - seconds


def idle_seconds(started, last_request, now):
    return None if started is None else max(0, now - max(started, last_request or started))


def _activity(now=None):
    try:
        touched = (state_dir() / "last-request").stat().st_mtime
    except FileNotFoundError:
        touched = None
    # Detached Claude desk requests cannot hold our lock. Read inference lines,
    # rather than log mtime, so startup, KV saves and health probes stay passive.
    log = Path(os.environ.get("CARR_FLASH_LOG", str(Path.home() / "Library/Logs/ds4-flash-next.log")))
    try:
        with log.open("rb") as handle:
            handle.seek(0, 2)
            handle.seek(max(0, handle.tell() - 256 * 1024))
            lines = handle.read().decode("utf-8", "replace").splitlines()
    except FileNotFoundError:
        return touched
    now = time.time() if now is None else now
    year = datetime.fromtimestamp(now).year
    for line in reversed(lines):
        match = re.match(r"(\d{4} \d{2}:\d{2}:\d{2}) ds4-server: chat .*?(?:prompt start|decoding|finish=)", line)
        if not match:
            continue
        try:
            stamp = datetime.strptime(f"{year} {match[1]}", "%Y %m%d %H:%M:%S").timestamp()
            if stamp > now + 86400:
                stamp = datetime.strptime(f"{year - 1} {match[1]}", "%Y %m%d %H:%M:%S").timestamp()
            return max(touched or stamp, stamp)
        except ValueError:
            continue
    return touched


def idle_stop(*, now=None, started=server_started, stop=_stop):
    try:
        with lifecycle_lock(blocking=False):
            now = time.time() if now is None else now
            age = idle_seconds(started(), _activity(now), now)
            if age is None:
                stop()
                return False
            if age < IDLE_SECONDS:
                return False
            stop()
            return True
    except BlockingIOError:
        return False


def health_row(*, now=None, started=server_started):
    action = ("on breach: owner Platform Engineer · remediation bin/flash-idle-stop; repair the idle watcher "
              "if still resident · verify launchctl print gui/$UID/local.ds4-flash-next reports no running pid "
              "· auto-clear when stopped or a request arrives · response health finding flash_residency "
              "deduplicated by release pipeline; com.carr.flash-idle-stop every 5m")
    if is_switched_off():
        return f"OK flash residency · switched off (by choice) · accepted state flash.off · {action}"
    try:
        with lifecycle_lock(blocking=False):
            now = time.time() if now is None else now
            start = started()
            age = idle_seconds(start, _activity(now), now)
            warn = age is not None and now - start > 1800 and age > 1800
            detail = "stopped" if age is None else f"idle {int(age)}s"
            return f"{'WARN' if warn else 'OK'} flash residency · {detail} · {action}"
    except BlockingIOError:
        return f"OK flash residency · request/start in progress · {action}"
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
        return f"WARN flash residency · read failed: {exc} · {action}"


def main(command, *, timeout=ENSURE_TIMEOUT):
    try:
        if command == "ensure":
            if is_switched_off():
                print(OFF_REASON, file=sys.stderr)
                return 1
            if ensure(timeout=timeout):
                return 0
            print(f"flash-ensure: readiness timed out after {timeout:g} seconds", file=sys.stderr)
            return 1
        if command == "idle-stop":
            if idle_stop():
                print("flash-idle-stop: stopped idle server and desk; both disabled")
            return 0
        raise ValueError(f"unknown Flash lifecycle command: {command}")
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
        print(f"flash-{command}: {exc}", file=sys.stderr)
        return 1
