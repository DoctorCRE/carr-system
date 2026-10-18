"""Deterministic Slack relay primitives. See bin/dot-relay for setup/protocol."""
from __future__ import annotations

import re
import shlex
import fcntl
import json
import os
import selectors
import shutil
import signal
import stat
import subprocess
import sys
import time
import threading
from pathlib import Path, PurePosixPath
from lib.secret_redaction import redact_text


def _within(path, roots):
    return any(path == root or root in path.parents for root in roots)


def _path(value, cwd, repo, roots):
    if value == "~/carr-system" or value.startswith("~/carr-system/"):
        value = str(repo) + value[len("~/carr-system"):]
    path = PurePosixPath(value)
    if ".." in path.parts or value.startswith("~"):
        return None
    if any(part.startswith(".") and part not in (".",) for part in path.parts):
        return None
    path = path if path.is_absolute() else cwd / path
    return path if _within(path, roots) else None


def command_spec(command, cwd, repo, scratch_roots=()):
    """Pure, fail-closed parser: return argv and read paths, or None.

    No filesystem, environment, subprocess, or model access. The runner also
    resolves read paths to refuse symlinks escaping these lexical boundaries.
    Each fenced line is one argv command, never a shell program.
    """
    if not isinstance(command, str) or len(command) > 4096:
        return None
    if re.search(r"[;&|`$><\\\n\r\x00*?\[\]{}!]", command):
        return None
    cwd, repo = PurePosixPath(cwd), PurePosixPath(repo)
    roots = (repo,) + tuple(PurePosixPath(p) for p in scratch_roots)
    if not cwd.is_absolute() or ".." in cwd.parts or not _within(cwd, roots):
        return None
    try:
        argv = shlex.split(command)
    except ValueError:
        return None
    if not argv:
        return None
    paths = []

    def files(values):
        for value in values:
            path = _path(value, cwd, repo, roots)
            if path is None or value.startswith("-"):
                return False
            paths.append(str(path))
        return True

    def reader(values, flags, pattern=False):
        values = list(values)
        while values and values[0] in flags:
            values.pop(0)
        if values and values[0] == "--":
            values.pop(0)
        if pattern:
            if not values or values[0].startswith("-"):
                return False
            values.pop(0)
            if values and values[0] == "--":
                values.pop(0)
        return bool(values) and files(values)

    tool, args = argv[0], argv[1:]
    if tool == "git":
        if not args:
            return None
        verb, args = args[0], args[1:]
        if verb == "fetch":
            if args not in ([], ["origin"]):
                return None
        elif verb == "status":
            if not all(x in ("--short", "--branch", "--porcelain") for x in args):
                return None
        elif verb in ("log", "show", "diff", "ls-files"):
            flags = {"--oneline", "--stat", "--name-only", "--name-status", "--no-patch"}
            if verb == "ls-files":
                flags = set()
            rest = []
            i = 0
            while i < len(args):
                value = args[i]
                if value == "--":
                    rest = args[i+1:]
                    break
                if value in flags or (verb == "log" and re.fullmatch(r"--max-count=[1-9][0-9]{0,2}", value)):
                    i += 1
                    continue
                if verb == "log" and value == "-n" and i+1 < len(args) and re.fullmatch(r"[1-9][0-9]{0,2}", args[i+1]):
                    i += 2
                    continue
                if verb != "ls-files" and re.fullmatch(r"(?:HEAD(?:~[0-9]{1,3})?|main|origin/main|[0-9a-f]{7,40})(?:\.\.(?:HEAD|main|origin/main|[0-9a-f]{7,40}))?", value):
                    i += 1
                    continue
                rest = args[i:]
                break
            if not files(rest):
                return None
        elif verb == "grep":
            # -e is accepted as the separator before the single pattern.
            if not reader(args, {"-n", "-i", "-F", "-e"}, pattern=True):
                return None
        else:
            return None
    elif tool in ("cat", "ls", "wc"):
        flags = {"cat": {"-n"}, "ls": {"-l", "-a", "-la", "-al"}, "wc": {"-l", "-c", "-w"}}[tool]
        if not args and tool == "ls":
            paths.append(str(cwd))
        elif not reader(args, flags):
            return None
    elif tool == "head":
        if len(args) >= 2 and args[0] == "-n" and re.fullmatch(r"[1-9][0-9]{0,3}", args[1]):
            args = args[2:]
        if not reader(args, set()):
            return None
    elif tool == "sed":
        if len(args) < 3 or args[0] != "-n" or not re.fullmatch(r"[1-9][0-9]{0,5}(?:,[1-9][0-9]{0,5})?p", args[1]) or not files(args[2:]):
            return None
    elif tool in ("grep", "rg"):
        if not reader(args, {"-n", "-i", "-F", "-l", "--files"} if tool == "rg" else {"-n", "-i", "-F", "-l"}, pattern="--files" not in args):
            return None
    elif tool == "python3":
        if args[:2] == ["-m", "pytest"]:
            args = args[2:]
            while args and args[0] in ("-q", "-v", "-x", "--disable-warnings"):
                args = args[1:]
            if not args:
                paths.append(str(cwd))
            elif not files(args):
                return None
        elif len(args) == 1 and re.fullmatch(r"(?:tools/test[-_][A-Za-z0-9_-]+|ops/[A-Za-z0-9_-]+-selftest)\.py", args[0]):
            if not files(args) or not _within(PurePosixPath(paths[-1]), (repo,)):
                return None
        else:
            return None
    elif tool == "./ops/ci.sh" and args == ["--only", "unit"] and cwd == repo:
        paths.append(str(repo / "ops/ci.sh"))
    else:
        return None
    return argv, paths


def allowed(command, cwd, repo, scratch_roots=()):
    return command_spec(command, cwd, repo, scratch_roots) is not None


def run_command(command, cwd, repo, scratch_roots=(), *, timeout=60, known_secrets=()):
    """Run argv with an isolated environment; kill the process group at limits."""
    refused = {"allowed": False, "exit": None, "bytes": 0, "output": "held for orchestrator"}
    repo, cwd = Path(repo).resolve(), Path(cwd).resolve()
    scratch_roots = tuple(Path(p).resolve() for p in scratch_roots)
    spec = command_spec(command, str(cwd), str(repo), tuple(map(str, scratch_roots)))
    if spec is None:
        return refused
    argv, paths = spec
    roots = tuple(Path(p).resolve() for p in (repo, *scratch_roots))
    workdir = Path(cwd).resolve()
    if not _within(workdir, roots):
        return refused
    for path in paths:
        resolved = Path(path).resolve()
        if not _within(resolved, roots) or any(p.startswith(".") for p in resolved.relative_to(next(r for r in roots if _within(resolved, (r,)))).parts):
            return refused
    # Resolve executables locally, never from a remote-provided PATH or cwd.
    system_path = "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin"
    tool = argv[0]
    if tool == "python3":
        argv[0] = sys.executable
    elif tool == "./ops/ci.sh":
        argv[0] = str(Path(repo).resolve() / "ops/ci.sh")
    else:
        argv[0] = shutil.which(tool, path=system_path)
        if argv[0] is None:
            return {"allowed": True, "exit": 127, "bytes": 0, "output": "executable unavailable"}
    if tool == "git":
        argv[1:1] = ["--no-pager", "-c", "core.fsmonitor=false", "-c", "core.hooksPath=/dev/null",
                     "-c", "gc.auto=0", "-c", "protocol.ext.allow=never", "-c", "protocol.file.allow=never"]
        if "diff" in argv or "show" in argv or "log" in argv:
            index = next(i for i, x in enumerate(argv) if x in ("diff", "show", "log"))
            argv[index+1:index+1] = ["--no-ext-diff", "--no-textconv"]
    # Translate the one supported tilde prefix without invoking shell expansion.
    argv = [str(repo) + x[len("~/carr-system"):] if x.startswith("~/carr-system") else x for x in argv]
    environment = {"PATH": system_path, "LANG": "C.UTF-8", "GIT_TERMINAL_PROMPT": "0",
                   "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null",
                   "PYTHONNOUSERSITE": "1", "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"}
    if tool == "git":
        # Git's effective worktree and administrative paths are independent of
        # cwd. Discover with the same disabled hooks/config, then pin the result.
        verb_index = next(i for i, value in enumerate(argv) if value in
                          ("fetch", "status", "log", "show", "diff", "ls-files", "grep"))
        prefix = argv[:verb_index]
        try:
            discovery = subprocess.run(prefix + ["rev-parse", "--show-toplevel", "--absolute-git-dir", "--git-common-dir"],
                                       cwd=workdir, env=environment, stdin=subprocess.DEVNULL,
                                       capture_output=True, timeout=timeout, check=True)
            locations = discovery.stdout.decode().splitlines()
            if len(locations) != 3:
                return refused
            top, gitdir, common = (Path(value) if Path(value).is_absolute() else workdir / value
                                  for value in locations)
            top, gitdir, common = top.resolve(), gitdir.resolve(), common.resolve()
            if not all(_within(path, roots) for path in (top, gitdir, common, (common / "objects").resolve())):
                return refused
            if not _within(workdir, (top,)) or (common / "objects/info/alternates").exists():
                return refused
        except (OSError, ValueError, subprocess.SubprocessError):
            return refused
        argv[verb_index:verb_index] = ["--git-dir=" + str(gitdir), "--work-tree=" + str(top)]
        environment["GIT_COMMON_DIR"] = str(common)
    captured = bytearray()
    count = 0
    exit_code = None
    try:
        proc = subprocess.Popen(argv, cwd=workdir, env=environment, stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True)
    except OSError:
        return {"allowed": True, "exit": 127, "bytes": 0, "output": "command could not start"}
    deadline = time.monotonic() + timeout
    with selectors.DefaultSelector() as selector:
        selector.register(proc.stdout, selectors.EVENT_READ)
        try:
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    exit_code = 124
                    break
                if not selector.select(min(remaining, 0.1)):
                    continue
                chunk = os.read(proc.stdout.fileno(), 4096)
                if not chunk:
                    # A process may close stdout before finishing. Bound wait too.
                    try:
                        exit_code = proc.wait(timeout=max(0.001, deadline-time.monotonic()))
                    except subprocess.TimeoutExpired:
                        exit_code = 124
                    break
                count += len(chunk)
                captured.extend(chunk)
                if len(captured) >= 12000 or captured.count(b"\n") >= 150:
                    exit_code = 125
                    break
        finally:
            # Kill descendants too, including a child still holding the pipe.
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            except PermissionError:
                # macOS can return EPERM for an already-exited process group.
                # A live child still needs termination; never leave it running.
                if proc.poll() is None:
                    proc.kill()
            proc.wait()
            proc.stdout.close()
    text = redact_text(captured.decode("utf-8", errors="replace"), known_secrets=known_secrets)
    text = "\n".join(text.splitlines()[:150]).encode()[:12000].decode("utf-8", errors="ignore")
    return {"allowed": True, "exit": exit_code, "bytes": count, "output": text}


def _sync_directory(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _private_dir(path):
    path = Path(path)
    missing = []
    parent = path
    while not parent.exists():
        missing.append(parent)
        parent = parent.parent
    path.mkdir(parents=True, mode=0o700, exist_ok=True)
    if path.is_symlink() or path.stat().st_uid != os.getuid() or path.stat().st_mode & 0o077:
        raise ValueError("state directory must be owned by the current user and mode 700")
    # Persist each new directory and the parent entry that makes it reachable.
    for created in reversed(missing):
        _sync_directory(created)
        _sync_directory(created.parent)
    return path


def _write_json(path, value):
    # Atomic, crash-durable checkpoint; fail closed on symlinks.
    temporary = path.with_suffix(".tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as handle:
        json.dump(value, handle)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)
    _sync_directory(path.parent)


def _append(path, value):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as handle:
        handle.write(json.dumps(value) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def _protocol(text):
    """Return requests and report; accept an end-marker suffix outside fences."""
    commands, report_lines = [], []
    fence = None
    finished = False
    for line in text.splitlines():
        if line.strip().startswith("```"):
            fence = line.strip()[3:].strip() if fence is None else None
            report_lines.append(line)
            continue
        if fence == "mac-run" and line.strip():
            commands.append(line.strip())
        if fence is None:
            marker = re.search(r"(?:^|\s)DOT-REPORT-END(?: [A-Za-z0-9._-]{1,48})?$", line.rstrip())
            if marker:
                prefix = line[:marker.start()].rstrip()
                if prefix:
                    report_lines.append(prefix)
                finished = True
                break
        report_lines.append(line)
    return commands, "\n".join(report_lines).strip() + "\n" if finished else None


def _timestamp_key(value):
    """One fail-closed Slack timestamp rule, with exact numeric ordering."""
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{1,20}\.[0-9]{1,10}", value):
        return None
    seconds, fraction = value.split(".")
    return int(seconds), int(fraction.ljust(10, "0"))


def _brief_parts(text, limit=3499):
    """Keep lines intact where possible; bound even a single oversized line."""
    parts, current = [], ""
    for line in text.splitlines(keepends=True):
        if current and len(current) + len(line) > limit:
            parts.append(current)
            current = ""
        while len(line) > limit:
            parts.append(line[:limit])
            line = line[limit:]
        current += line
    if current or not parts:
        parts.append(current)
    return parts


class Relay:
    """Transport seam: post(text, thread=None) -> ts; replies(thread) -> messages.

    Persist a command claim BEFORE running it. An interrupted claim becomes a
    hold, never an automatic retry. Poll cursors are deliberately unnecessary:
    Slack pagination plus durable per-message keys catches late replies.
    """
    def __init__(self, transport, state_dir, repo, sender, *, cwd=None, scratch_roots=(), known_secrets=()):
        self.transport = transport
        self.repo = Path(repo).resolve()
        self.cwd = Path(cwd or repo).resolve()
        self.state_dir = _private_dir(state_dir)
        if _within(self.state_dir.resolve(), (self.repo,)):
            raise ValueError("state must live outside the repository")
        self.sender = sender
        self.scratch_roots = tuple(Path(p).resolve() for p in scratch_roots)
        self.secrets = tuple(known_secrets)
        self.binding = {"sender": sender, "channel": getattr(transport, "channel", ""),
                        "repo": str(self.repo), "cwd": str(self.cwd), "scratch_roots": list(map(str, self.scratch_roots))}

    def _directory(self, thread):
        if _timestamp_key(thread) is None:
            raise ValueError("invalid Slack thread timestamp")
        return _private_dir(self.state_dir / thread)

    def send_job(self, brief):
        # Redact before splitting: a secret spanning a cut must never leak.
        text = redact_text(brief, known_secrets=self.secrets)
        parts = _brief_parts(text)
        if len(parts) > 1:
            start = "\nMultipart brief: wait for DOT-BRIEF-END before responding.\n"
            end = "\nDOT-BRIEF-END\n"
            parts = _brief_parts(text, 3499 - max(len(start), len(end)))
            parts[0] += start
            parts[-1] += end
        pending_file = self.state_dir / "send-job.json"
        fd = os.open(self.state_dir / "send-job.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if pending_file.exists():
                raise ValueError("interrupted job post requires reconciliation")
            # The root has no thread directory yet. Persist its claim here.
            pending = {"posting_pending": "job:0", "binding": self.binding}
            _write_json(pending_file, pending)
            thread = self.transport.post(parts[0])
            if _timestamp_key(thread) is None:
                raise SlackError("Slack post timestamp invalid")
            pending["thread"] = thread
            _write_json(pending_file, pending)
            directory = self._directory(thread)
            fd = os.open(directory / "lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, "w") as thread_lock:
                fcntl.flock(thread_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                state_file = directory / "state.json"
                state = {"commands": {}, "messages": [], "binding": self.binding, "outgoing": [thread]}
                if len(parts) > 1:
                    state["posting_pending"] = "job:1"
                _write_json(directory / "job.json", {"thread": thread, "cwd": str(self.cwd),
                            "scratch_roots": list(map(str, self.scratch_roots)), "binding": self.binding})
                _write_json(state_file, state)
                # The known root and remaining claims now live in thread state.
                pending_file.unlink()
                _sync_directory(self.state_dir)
                for index, part in enumerate(parts[1:], 1):
                    posted_ts = self.transport.post(part, thread)
                    if _timestamp_key(posted_ts) is None:
                        raise SlackError("Slack post timestamp invalid")
                    state["outgoing"].append(posted_ts)
                    if index + 1 < len(parts):
                        state["posting_pending"] = f"job:{index + 1}"
                    else:
                        del state["posting_pending"]
                        state["brief_complete_ts"] = posted_ts
                    _write_json(state_file, state)
            return thread

    def poll(self, thread, *, execute=False):
        if _timestamp_key(thread) is None:
            raise ValueError("invalid Slack thread timestamp")
        pending_file = self.state_dir / "send-job.json"
        if pending_file.exists():
            fd = os.open(self.state_dir / "send-job.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, "w") as send_lock:
                try:
                    fcntl.flock(send_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    # Only an initialized job can be known to predate this send.
                    # The root may be visible remotely before its ack is stored.
                    if not (self.state_dir / thread / "job.json").exists():
                        return False
                    # A healthy sender owns this claim; other jobs can poll.
                else:
                    if pending_file.exists():
                        raise ValueError("interrupted job post requires reconciliation")
        directory = self._directory(thread)
        # flock covers execution AND checkpoints; two relay processes cannot race.
        fd = os.open(directory / "lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            state_file = directory / "state.json"
            job_file = directory / "job.json"
            if job_file.exists() and json.loads(job_file.read_text()).get("binding") != self.binding:
                raise ValueError("job binding changed")
            state = json.loads(state_file.read_text()) if state_file.exists() else {"commands": {}, "messages": [], "binding": self.binding}
            if state.get("binding") != self.binding:
                raise ValueError("thread state binding changed")
            if state.get("posting_pending"):
                raise ValueError("interrupted post requires reconciliation")
            if state.get("finished"):
                return True
            complete = state.get("brief_complete_ts")
            if complete is not None and _timestamp_key(complete) is None:
                raise ValueError("invalid brief completion timestamp")
            messages = self.transport.replies(thread)
            _validate_messages(messages)
            for message in sorted(messages, key=lambda m: _timestamp_key(m["ts"])):
                ts = message["ts"]
                if ts == thread or ts in state["messages"] or ts in state.get("outgoing", []):
                    continue
                if self.sender not in (message.get("user"), message.get("bot_id")) or message.get("edited") or message.get("subtype") not in (None, "bot_message"):
                    continue
                if complete is not None and _timestamp_key(ts) <= _timestamp_key(complete):
                    continue  # Neither commands nor reports may precede the full brief.
                commands, report = _protocol(message["text"])
                if commands and not execute:
                    continue  # watch does not consume requests needed by relay
                for i, command in enumerate(commands):
                    key = f"{ts}:{i}"
                    old = state["commands"].get(key)
                    if old == "done":
                        continue
                    state["commands"][key] = "claimed"
                    _write_json(state_file, state)
                    result = ({"allowed": False, "exit": None, "bytes": 0, "output": "held for orchestrator"}
                              if old else run_command(command, self.cwd, self.repo, self.scratch_roots, known_secrets=self.secrets))
                    row = {"ts": ts, "command": redact_text(command, known_secrets=self.secrets),
                           "cwd": str(self.cwd), "exit": result["exit"], "allowed": result["allowed"], "bytes": result["bytes"]}
                    _append(directory / "ledger.jsonl", row)
                    if not result["allowed"]:
                        _append(directory / "pending.jsonl", row)
                        output = "held for orchestrator"
                    else:
                        # Output remains data; its posted identity is excluded below.
                        output = f"mac-result: exit={result['exit']} bytes={result['bytes']}\n" + result["output"]
                    # An ambiguous post must stop history processing on restart.
                    state["posting_pending"] = key
                    _write_json(state_file, state)
                    posted_ts = self.transport.post(output, thread)
                    if _timestamp_key(posted_ts) is None:
                        raise SlackError("Slack post timestamp invalid")
                    state.setdefault("outgoing", []).append(posted_ts)
                    del state["posting_pending"]
                    state["commands"][key] = "done"
                    _write_json(state_file, state)
                if report is not None:
                    report = redact_text(report, known_secrets=self.secrets)
                    fd = os.open(directory / "report.txt", os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
                    with os.fdopen(fd, "w") as handle:
                        handle.write(report)
                        handle.flush()
                        os.fsync(handle.fileno())
                    state["report_observed"] = True
                state["messages"].append(ts)
                _write_json(state_file, state)
            if execute and state.get("report_observed"):
                state["finished"] = True
                _write_json(state_file, state)
            return bool(state.get("report_observed"))


class SlackError(Exception):
    def __init__(self, message, *, retry_after=0, transient=False):
        super().__init__(message)
        self.retry_after = retry_after
        self.transient = transient


def _validate_messages(messages):
    """Validate the whole snapshot before a relay can claim any work."""
    if not isinstance(messages, list):
        raise SlackError("Slack thread response invalid")
    seen = set()
    for message in messages:
        if not isinstance(message, dict):
            raise SlackError("Slack message invalid")
        ts, text = message.get("ts"), message.get("text")
        if (_timestamp_key(ts) is None
                or not isinstance(text, str) or ts in seen):
            raise SlackError("Slack message invalid")
        seen.add(ts)
        identities = [message[key] for key in ("user", "bot_id") if key in message]
        if not identities or any(not isinstance(value, str) or not value for value in identities):
            raise SlackError("Slack message identity invalid")
        if message.get("subtype") is not None and not isinstance(message["subtype"], str):
            raise SlackError("Slack message subtype invalid")
        if message.get("edited") is not None and not isinstance(message["edited"], dict):
            raise SlackError("Slack message edit invalid")


def _retry_after(headers):
    try:
        return max(1, int(headers.get("Retry-After", headers.get("retry-after", 30))))
    except (ValueError, TypeError):
        return 30


class SlackTransport:
    """Slack Web API adapter; reuse installed SDK, otherwise use stdlib HTTP.

    There are no model calls, socket connections, or implicit write retries.
    A failed post may have reached Slack, so the caller must reconcile it.
    """
    def __init__(self, token, channel, *, api=None, use_sdk=True):
        self.token, self.channel = token, channel
        self.api = api
        self.client = None
        self._post_lock = threading.Lock()
        self._next_post_at = 0.0
        if api is None and use_sdk:
            try:
                from slack_sdk import WebClient
            except ImportError:
                pass
            else:
                import logging
                logger = logging.Logger("dot-relay-slack", level=logging.CRITICAL)
                self.client = WebClient(token=token, timeout=20, retry_handlers=[], logger=logger)

    def _call(self, method, payload):
        import http.client
        import urllib.error
        import urllib.parse
        import urllib.request
        write = method == "chat.postMessage"
        try:
            if self.api is not None:
                response = self.api(method, payload)
            elif self.client is not None:
                response = dict(self.client.api_call(method, json=payload).data)
            else:
                headers = {"Authorization": "Bearer " + self.token}
                url = "https://slack.com/api/" + method
                data = None
                if write:
                    data = json.dumps(payload).encode()
                    headers["Content-Type"] = "application/json; charset=utf-8"
                else:
                    url += "?" + urllib.parse.urlencode(payload)
                request = urllib.request.Request(url, data=data, headers=headers)
                with urllib.request.urlopen(request, timeout=20) as result:
                    raw = result.read(2_000_001)
                    if len(raw) > 2_000_000:
                        raise SlackError("Slack response exceeds size limit")
                    response = json.loads(raw)
        except urllib.error.HTTPError as exc:
            code, headers = exc.code, exc.headers
            exc.close()
            raise SlackError("Slack HTTP request failed", retry_after=_retry_after(headers) if code == 429 else 0,
                             transient=not write and (code == 429 or code >= 500)) from None
        except (urllib.error.URLError, TimeoutError, OSError, http.client.HTTPException):
            raise SlackError("Slack network request failed", transient=not write) from None
        except (ValueError, TypeError):
            raise SlackError("Slack response invalid") from None
        except SlackError:
            raise
        except Exception as exc:
            # SDK exceptions can contain headers, payload, and credentials.
            response = getattr(exc, "response", None)
            status = getattr(response, "status_code", 0)
            headers = getattr(response, "headers", {}) or {}
            raise SlackError("Slack SDK request failed", retry_after=_retry_after(headers) if status == 429 else 0,
                             transient=not write and (status == 429 or status >= 500)) from None
        if not isinstance(response, dict) or response.get("ok") is not True:
            rate_limited = isinstance(response, dict) and response.get("error") == "ratelimited"
            raise SlackError("Slack API request refused", retry_after=30 if rate_limited else 0,
                             transient=not write and rate_limited)
        return response

    def post(self, text, thread=None):
        payload = {"channel": self.channel, "text": text, "mrkdwn": False,
                   "unfurl_links": False, "unfurl_media": False}
        if thread:
            payload["thread_ts"] = thread
        # Pace distinct writes, including result posts, without retrying any write.
        with self._post_lock:
            while True:
                delay = self._next_post_at - time.monotonic()
                if delay <= 0:
                    break
                time.sleep(delay)
            self._next_post_at = time.monotonic() + 1
            response = self._call("chat.postMessage", payload)
        ts = response.get("ts")
        if _timestamp_key(ts) is None:
            raise SlackError("Slack post did not return a thread timestamp")
        return ts

    def replies(self, thread):
        messages, cursor, seen = [], "", set()
        for _ in range(100):
            payload = {"channel": self.channel, "ts": thread, "limit": 15}
            if cursor:
                payload["cursor"] = cursor
            response = self._call("conversations.replies", payload)
            page = response.get("messages")
            if not isinstance(page, list) or any(not isinstance(m, dict) for m in page):
                raise SlackError("Slack thread response invalid")
            _validate_messages(page)
            messages.extend(page)
            metadata = response.get("response_metadata", {})
            has_more = response.get("has_more", False)
            if not isinstance(metadata, dict) or not isinstance(has_more, bool):
                raise SlackError("Slack pagination response invalid")
            cursor = metadata.get("next_cursor", "")
            if not isinstance(cursor, str):
                raise SlackError("Slack pagination cursor invalid")
            cursor = cursor.strip()
            if not cursor:
                if response.get("has_more"):
                    raise SlackError("Slack pagination cursor missing")
                _validate_messages(messages)
                return messages
            if cursor in seen:
                raise SlackError("Slack pagination cursor repeated")
            seen.add(cursor)
        raise SlackError("Slack pagination limit reached")


def read_config(path, repo):
    """Read the existing Hermes dotenv convention WITHOUT sourcing or expanding."""
    path = Path(path).expanduser()
    if _within(path.resolve(), (Path(repo).resolve(),)):
        raise ValueError("credential file must live outside the repository")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd) as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600:
            raise ValueError("credential file must be owned by the current user and mode 600")
        values = {}
        for line in handle:
            key, separator, value = line.strip().removeprefix("export ").partition("=")
            if not separator or key not in ("SLACK_USER_TOKEN", "SLACK_BOT_TOKEN", "SLACK_HOME_CHANNEL", "DOT_SLACK_SENDER", "DOT_SLACK_MENTION"):
                continue
            parts = shlex.split(value, comments=True)
            if len(parts) != 1 or re.search(r"[$`\r\n]", parts[0]):
                raise ValueError("Slack config must use literal values")
            values[key] = parts[0]
    token = values.get("SLACK_USER_TOKEN") or values.get("SLACK_BOT_TOKEN")
    if not token or not values.get("SLACK_HOME_CHANNEL") or not values.get("DOT_SLACK_SENDER"):
        raise ValueError("Slack credential, destination, and sender setup is incomplete")
    return {"token": token, "channel": values["SLACK_HOME_CHANNEL"], "sender": values["DOT_SLACK_SENDER"],
            "mention": values.get("DOT_SLACK_MENTION", ""), "user_token": bool(values.get("SLACK_USER_TOKEN"))}


def watch(engine, thread, *, execute=False, max_polls=720, sleep=time.sleep):
    backoff = 30
    for _ in range(max_polls):
        try:
            if engine.poll(thread, execute=execute):
                return 0
            backoff = 30
            delay = 30
        except SlackError as exc:
            if not exc.transient:
                raise
            delay = max(backoff, exc.retry_after)
            backoff = min(backoff * 2, 300)
        sleep(delay)
    return 2  # bounded polling ended without the report marker


def main(argv=None, *, repo=None, transport_factory=SlackTransport):
    import argparse
    import tempfile
    parser = argparse.ArgumentParser(description="No-model Slack relay; see script header for setup and protocol.")
    parser.add_argument("--credentials", type=Path, default=Path.home()/".hermes/.env")
    parser.add_argument("--state-dir", type=Path, default=Path.home()/".local/state/dot-relay")
    subcommands = parser.add_subparsers(dest="command", required=True)
    send = subcommands.add_parser("send-job", help="post a brief file and print its thread timestamp")
    send.add_argument("brief_file", type=Path)
    send.add_argument("--cwd", type=Path, help="repository subdirectory for requested commands")
    send.add_argument("--scratch", action="store_true", help="create an owned mktemp directory as the job cwd")
    for verb in ("watch", "relay"):
        poll = subcommands.add_parser(verb, help="poll only" if verb == "watch" else "poll and execute allowlisted commands")
        poll.add_argument("thread")
        poll.add_argument("--max-polls", type=int, default=720)
    args = parser.parse_args(argv)
    repo = Path(repo or Path.home()/"carr-system").resolve()
    try:
        cfg = read_config(args.credentials, repo)
        if not cfg["user_token"] and not cfg["channel"].startswith("D"):
            raise ValueError("channel thread reads require a user token; bot tokens support DMs")
        transport = transport_factory(cfg["token"], cfg["channel"])
        state_dir = _private_dir(args.state_dir.expanduser())
        cwd, scratch = repo, ()
        if args.command == "send-job":
            if args.scratch and args.cwd:
                raise ValueError("choose cwd or scratch")
            if args.scratch:
                cwd = Path(tempfile.mkdtemp(prefix="dot-relay-")).resolve()
                scratch = (cwd,)
            elif args.cwd:
                cwd = args.cwd.expanduser().resolve()
            if not allowed("ls", str(cwd), str(repo), tuple(map(str, scratch))):
                raise ValueError("cwd outside approved roots")
            brief = args.brief_file.read_text()
            if not brief.strip() or len(brief) > 30000:
                raise ValueError("brief must be nonempty and at most 30000 characters")
            if cfg["mention"]:
                brief = cfg["mention"] + "\n" + brief
            engine = Relay(transport, state_dir, repo, cfg["sender"], cwd=cwd,
                           scratch_roots=scratch, known_secrets=(cfg["token"],))
            print(engine.send_job(brief))
            return 0
        if not 1 <= args.max_polls <= 10000:
            raise ValueError("max polls outside supported range")
        if _timestamp_key(args.thread) is None:
            raise ValueError("invalid thread timestamp")
        job_file = state_dir / args.thread / "job.json"
        if job_file.exists():
            job = json.loads(job_file.read_text())
            cwd = Path(job["cwd"]).resolve()
            scratch = tuple(Path(p).resolve() for p in job.get("scratch_roots", []))
            for root in scratch:
                if root.parent != Path(tempfile.gettempdir()).resolve() or not root.name.startswith("dot-relay-"):
                    raise ValueError("scratch root is not relay-owned")
                _private_dir(root)
        engine = Relay(transport, state_dir, repo, cfg["sender"], cwd=cwd,
                       scratch_roots=scratch, known_secrets=(cfg["token"],))
        result = watch(engine, args.thread, execute=args.command == "relay", max_polls=args.max_polls)
        if result == 0:
            print("report saved")
        else:
            print("poll limit reached; report not received", file=sys.stderr)
        return result
    except (OSError, ValueError, KeyError, SlackError):
        # No exception body, request payload, token, brief, or output in logs.
        print("dot-relay stopped: check private setup/state and reconcile any interrupted post", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("dot-relay interrupted; reconcile pending state before resuming", file=sys.stderr)
        return 130
