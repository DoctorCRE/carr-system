"""Explicit local job execution and shared local ledger helpers."""
import contextlib
import fcntl
import json
import math
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone

SOURCE = Path(__file__).resolve().parents[1]


def load_config(path=None):
    return json.loads(Path(path or SOURCE / "ops/config/local-jobs.json").read_text())


def command(argv, config, cwd=None):
    result = subprocess.run(argv, cwd=cwd, stdin=subprocess.DEVNULL, capture_output=True,
                            text=True, timeout=config["thresholds"]["command_timeout_seconds"])
    if result.returncode:
        raise RuntimeError(f"{argv[0]} exit {result.returncode}: {result.stderr.strip()}")
    return result.stdout


def repository_roots(config=None):
    config = config or load_config(SOURCE / "ops/config/local-jobs.json")
    return {repo: Path(config["repository_roots"][repo]).expanduser() for repo in config["repositories"]}


def protected_branch(config, name):
    return name in config["protected_branches"]


def epoch(value):
    if isinstance(value, (float, int)):
        return float(value)
    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


def stamp(now=None):
    return datetime.fromtimestamp(time.time() if now is None else now, timezone.utc).isoformat()


def path_at(root, configured):
    path = Path(configured).expanduser()
    return path if path.is_absolute() else root / path


@contextlib.contextmanager
def locked(path, blocking=True):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        yield handle


def append(path, row):
    with locked(path) as handle:
        handle.seek(0, os.SEEK_END)
        handle.write(json.dumps(row, separators=(",", ":")) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def read_latest(path):
    if not path.exists():
        return {}
    with path.open() as handle:
        fcntl.flock(handle, fcntl.LOCK_SH)
        rows = {}
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            key = row.get("key", row.get("id"))
            rows[key] = {**rows.get(key, {}), **row}
        return rows


def tail(path, limit):
    with path.open("rb") as handle:
        handle.seek(max(0, path.stat().st_size - limit))
        return handle.read().decode("utf-8", errors="replace")


def process_identity(pid, config=None):
    if not isinstance(pid, int) or pid <= 1:
        return None
    config = config or load_config(SOURCE / "ops/config/local-jobs.json")
    result = subprocess.run(["ps", "-p", str(pid), "-o", "lstart=", "-o", "pgid="],
                            stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=config["thresholds"]["process_probe_seconds"])
    return result.stdout.strip() if result.returncode == 0 and result.stdout.strip() else None


def process_group_alive(pgid):
    try:
        os.killpg(pgid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        # EPERM does not prove absence. Keep polling through group teardown;
        # an actual TERM/KILL denial still refuses recovery before relaunch.
        return True


def board_task(root, config, card, executor, status, note, health=None, reason=None, next_action=None):
    project = config["board"]
    board = root / "out/boards" / (project + ".json")
    env = dict(os.environ, PROGRESS_BOARD_ROOT=str(root / "out"), PROGRESS_BOARD_LOCAL_ONLY="1")
    if not config.get("board_github_enabled", True):
        env["PROGRESS_BOARD_SKIP_GH"] = "1"
    with locked(root / "out/jobs/board.lock"):
        if not board.exists():
            result = subprocess.run([sys.executable, str(SOURCE / "tools/progress_board.py"), "init",
                                     project, "--title", "Agent jobs"],
                                    env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True,
                                    timeout=config["thresholds"]["command_timeout_seconds"])
            if result.returncode:
                raise RuntimeError(result.stderr)
        prior = json.loads(board.read_text()).get("tasks", {}).get(card)
        argv = [sys.executable, str(SOURCE / "tools/progress_board.py"), "task", project, card,
                "--status", status, "--health", health or ("blocked" if status == "blocked" else "healthy"),
                "--note", note, "--receipt", "--lane", "status"]
        if prior is None:
            argv.extend(["--title", card, "--executor", executor])
        elif executor != "orchestrator":
            argv.extend(["--executor", executor])
        if status == "blocked":
            argv.extend(["--reason", reason or note,
                         "--next-action", next_action or config["next_actions"]["job_failed"]])
        result = subprocess.run(argv, env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True,
                                timeout=config["thresholds"]["command_timeout_seconds"])
        if result.returncode:
            raise RuntimeError(result.stderr)
        try:
            receipt = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError("progress board returned an invalid task receipt") from exc
        if (not isinstance(receipt, dict) or type(receipt.get("applied")) is not bool
                or receipt.get("before") is not None and not isinstance(receipt.get("before"), dict)
                or receipt.get("after") is not None and not isinstance(receipt.get("after"), dict)):
            raise RuntimeError("progress board returned an invalid task receipt")
        return receipt.get("before") or {}


def terminate_group(proc, grace):
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    deadline = time.monotonic() + grace
    while process_group_alive(proc.pid) and time.monotonic() < deadline:
        proc.poll()
        time.sleep(min(0.05, max(0, deadline - time.monotonic())))
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    proc.wait(timeout=max(1, grace))


def run_job(root, config, card, executor, minutes, argv):
    if not math.isfinite(minutes) or minutes <= 0 or not argv or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", card):
        raise ValueError("positive minutes, safe card id, and command required")
    job_id = os.environ.get("CARR_JOB_ID") or uuid.uuid4().hex
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", job_id):
        raise ValueError("unsafe job id")
    registry = path_at(root, config["paths"]["registry"])
    if job_id in read_latest(registry):
        raise ValueError("job id already registered; refusing duplicate execution")
    log = path_at(root, config["paths"]["job_logs"]) / (job_id + ".log")
    log.parent.mkdir(parents=True, exist_ok=True)
    board_task(root, config, card, executor, "running", "Job starting; log " + str(log))
    started = time.time()
    proc = None
    timed_out = False
    cancelled = None
    row = {"id": job_id, "card": card, "executor": executor, "start": started,
           "limit": minutes * 60, "log_path": str(log), "cwd": str(Path.cwd()),
           "command": argv, "root_id": os.environ.get("CARR_JOB_ROOT_ID", job_id),
           "wrapper_pid": os.getpid(), "board": config["board"]}
    for key in ("repo", "pr", "head"):
        value = os.environ.get("CARR_JOB_" + key.upper())
        if value:
            row[key] = int(value) if key == "pr" else value
    try:
        with log.open("wb") as output:
            proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT,
                                    start_new_session=True)
            row.update(pid=proc.pid, pgid=proc.pid, process_identity=process_identity(proc.pid, config))
            append(registry, row)
            def stop(signum, frame):
                nonlocal cancelled
                cancelled = signum
            previous = {sig: signal.signal(sig, stop) for sig in (signal.SIGTERM, signal.SIGINT)}
            try:
                deadline = time.monotonic() + minutes * 60
                while proc.poll() is None and not cancelled:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        timed_out = True
                        break
                    try:
                        proc.wait(timeout=min(0.2, remaining))
                    except subprocess.TimeoutExpired:
                        pass
                # Reap descendants even when their leader exited successfully.
                if process_group_alive(proc.pid):
                    terminate_group(proc, config["thresholds"]["kill_grace_seconds"])
                code = 124 if timed_out else (128 + cancelled if cancelled else proc.returncode)
            finally:
                for sig, handler in previous.items():
                    signal.signal(sig, handler)
    except OSError as exc:
        code = 127
        row.update(pid=None, process_identity=None)
        append(registry, row)
        with log.open("ab") as output:
            output.write(str(exc).encode())
    except BaseException:
        if proc:
            terminate_group(proc, config["thresholds"]["kill_grace_seconds"])
        raise
    code = 128 - code if code < 0 else code
    end = {"id": job_id, "exit_code": code, "ended_at": stamp(), "timed_out": timed_out,
           "log_tail": tail(log, config["thresholds"]["log_tail_bytes"])}
    append(registry, end)
    board_task(root, config, card, executor, "done" if code == 0 else "blocked",
               f"Exit {code}; log {log}; next: " + ("orchestrator verify result" if code == 0 else config["next_actions"]["job_failed"]))
    print(json.dumps({**row, **end}))
    return code
