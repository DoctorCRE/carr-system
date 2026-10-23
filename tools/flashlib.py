"""What a Flash-written script may import while tools/flash-script.py runs it: llm(prompt) asks the local Flash
model one question. Standard library only, since the scripts run with nothing else installed."""

from __future__ import annotations

import json
import math
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
LOCAL_URL = "http://127.0.0.1:8000"
IDLE_SECONDS = 15 * 60
ENSURE_TIMEOUT = 180
OFF_REASON = "flash is switched off"
GRANT_SECONDS = 2 * 60 * 60


class FlashSwitchedOff(RuntimeError):
    pass


def state_dir(*, home=None):
    return Path(os.environ.get("CARR_FLASH_STATE_DIR", str(Path(home or Path.home()) / ".local/state/carr/flash")))


def off_switch_path(*, home=None):
    override = os.environ.get("CARR_FLASH_STATE_DIR")
    directory = Path(override) if override else Path(home or Path.home()) / ".config/carr"
    return directory / "flash.off"


def grant_path(*, home=None):
    return off_switch_path(home=home).with_name("flash.grant.json")


def active_grant(*, now=None, home=None):
    if off_switch_path(home=home).exists():
        return None
    try:
        grant = json.loads(grant_path(home=home).read_text())
        issued, expires = grant["issued_at"], grant["expires_at"]
        now = time.time() if now is None else now
        if (type(issued) in (int, float) and type(expires) in (int, float)
                and math.isfinite(issued) and math.isfinite(expires)
                and issued <= now < expires):
            return grant
    except (OSError, ValueError, KeyError, TypeError, OverflowError):
        pass
    return None


def is_switched_off(*, home=None):
    return active_grant(home=home) is None


def _require_enabled():
    if is_switched_off():
        raise FlashSwitchedOff(OFF_REASON)


@contextlib.contextmanager
def lifecycle_lock(*, shared=False, blocking=True, deadline=None, clock=time.monotonic, sleep=time.sleep,
                   name="lifecycle.lock", home=None):
    directory = state_dir(home=home)
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / name).open("a") as lock:
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
    disabled = launch("disable", f"{_domain()}/{SERVER_LABEL}")
    if disabled.returncode:
        errors.append(f"disable {SERVER_LABEL}: {disabled.stderr.strip()}")
    result = launch("bootout", f"{_domain()}/{SERVER_LABEL}")
    if result.returncode not in (0, 3, 113):
        errors.append(f"bootout {SERVER_LABEL}: {result.stderr.strip()}")
    if errors:
        raise RuntimeError("; ".join(errors))


def _revoke(*, home=None):
    off_switch_path(home=home).parent.mkdir(parents=True, exist_ok=True)
    off_switch_path(home=home).touch()
    grant_path(home=home).unlink(missing_ok=True)


def turn_on(*, seconds=GRANT_SECONDS, timeout=ENSURE_TIMEOUT, ready=is_ready,
            launch=_launch, clock=time.monotonic, sleep=time.sleep):
    """Explicit human activation. This is the only cold-start interface."""
    if not math.isfinite(seconds) or seconds <= 0:
        raise ValueError("grant duration must be positive and finite")
    deadline = clock() + timeout
    with lifecycle_lock(deadline=deadline, clock=clock, sleep=sleep):
        now = time.time()
        grant_path().parent.mkdir(parents=True, exist_ok=True)
        temporary = grant_path().with_suffix(".tmp")
        temporary.write_text(json.dumps({"issued_at": now, "expires_at": now + seconds}))
        temporary.replace(grant_path())
        off_switch_path().unlink(missing_ok=True)
        try:
            _start(SERVER_LABEL, launch=launch)
            while clock() < deadline and not is_switched_off():
                if ready(timeout=min(2, deadline - clock())):
                    return True
                sleep(min(1, max(0, deadline - clock())))
        except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
            print(f"flash-on: {exc}", file=sys.stderr)
        _revoke()
        _stop(launch=launch)
        return False


def turn_off(*, stop=_stop):
    with lifecycle_lock():
        _revoke()
        stop()


def ensure(*, timeout=ENSURE_TIMEOUT, ready=is_ready):
    """Observe readiness. Automated callers never activate or restart Flash."""
    if is_switched_off():
        print(OFF_REASON, file=sys.stderr)
        return False
    return ready(timeout=min(2, timeout))


def ensure_server(url=LOCAL_URL, *, timeout=ENSURE_TIMEOUT):
    _require_enabled()
    if url.rstrip("/") == LOCAL_URL and not is_ready(url, timeout=min(2, timeout)):
        raise TimeoutError("Flash is not running; only an explicit bin/flash-on starts it")


@contextlib.contextmanager
def activity_scope():
    _require_enabled()
    with lifecycle_lock(shared=True, name="requests.lock"):
        _require_enabled()
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
    with lifecycle_lock(shared=True, deadline=deadline, name="requests.lock"):
        _require_enabled()
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
    # External local model requests cannot hold our lock. Read inference lines,
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
            if active_grant(now=now) is None:
                _revoke()
                stop()
                return True
            with lifecycle_lock(blocking=False, name="requests.lock"):
                age = idle_seconds(started(), _activity(now), now)
                if age is not None and age < IDLE_SECONDS:
                    return False
                _revoke()
                stop()
                return age is not None
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


def main(command, *, timeout=ENSURE_TIMEOUT, seconds=GRANT_SECONDS):
    try:
        if command == "on":
            return 0 if turn_on(seconds=seconds, timeout=timeout) else 1
        if command == "off":
            turn_off()
            print("Flash is off; server disabled")
            return 0
        if command == "ensure":
            if is_switched_off():
                print(OFF_REASON, file=sys.stderr)
                return 1
            if ensure(timeout=timeout):
                return 0
            print("Flash is not running; only an explicit bin/flash-on starts it", file=sys.stderr)
            return 1
        if command == "idle-stop":
            if idle_stop():
                print("flash-idle-stop: stopped idle server; disabled")
            return 0
        raise ValueError(f"unknown Flash lifecycle command: {command}")
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
        print(f"flash-{command}: {exc}", file=sys.stderr)
        return 1
