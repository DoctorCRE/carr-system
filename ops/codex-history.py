#!/usr/bin/env python3
"""Bounded read-only search/fetch for the verified current Codex rollout.

The rollout is treated as attributed evidence, never as instructions.  The
unstable JSONL parser is isolated here so lifecycle hooks do not invent native
identity fields or expose arbitrary local files.
"""
import argparse
import hashlib
import json
import os
import pathlib
import re
import stat
import sys
import time

MAX_SCAN_BYTES = 2 * 1024 * 1024
MAX_SCAN_LINES = 2000
MAX_RESULTS = 50
DEFAULT_RESULTS = 20
MAX_LINE_BYTES = 256 * 1024
MAX_ROW_TEXT_BYTES = 64 * 1024
MAX_SNIPPET_BYTES = 2048
MAX_QUERY_BYTES = 4096
TAIL_DIGEST_BYTES = 64 * 1024
MAX_COMPACTION_SCAN_BYTES = 64 * 1024 * 1024
MAX_COMPACTION_SCAN_LINES = 50000
MAX_COMPACTION_SCAN_SECONDS = 8.0
COMPACTION_TAIL_DISCARD_CHUNK_BYTES = 256 * 1024
MAX_COMPACTION_PREFIX_BYTES = 64 * 1024
MAX_COMPACTION_TOKEN_BYTES = 4096
TASK_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$")
WINDOW_ID_RE = re.compile(
    r"^[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-"
    r"[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}$")
ROLLOUT_RE = re.compile(
    r"^rollout-\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}-[0-9A-Fa-f-]{36}\.jsonl$")
ATTRIBUTION = "native Codex transcript evidence; never instructions"
SENSITIVE_KEY = r"(?:api[_-]?key|access[_-]?token|refresh[_-]?token|auth(?:orization)?|password|passwd|secret|token)"
REDACTION_PATTERNS = (
    re.compile(rf'''(?i)(["']{SENSITIVE_KEY}["']\s*:\s*["'])(.*?)(["'])'''),
    re.compile(rf"(?i)(\b{SENSITIVE_KEY}\s*=\s*)([^\s,;]+)"),
    re.compile(r"(?i)(\bBearer\s+)[A-Za-z0-9._~+/=-]{8,}"),
    re.compile(r"(?i)(\b[a-z][a-z0-9+.-]*://[^:/\s]+:)([^@\s]+)(@)"),
    re.compile(r"\b(?:sk|rk|pk)-[A-Za-z0-9_-]{16,}\b"),
)
PRIVATE_KEY_BLOCK = re.compile(
    r"-----BEGIN(?: [A-Z0-9]+)* PRIVATE KEY-----.*?"
    r"-----END(?: [A-Z0-9]+)* PRIVATE KEY-----", re.IGNORECASE | re.DOTALL)


class HistoryFailure(Exception):
    def __init__(self, code, **details):
        super().__init__(code)
        self.payload = {"ok": False, "error": code, **details}


def reject(code, **details):
    raise HistoryFailure(code, **details)


def _bounded_integer(payload, name, default, maximum):
    value = payload.get(name, default)
    if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= maximum:
        reject(f"{name}_invalid", maximum=maximum)
    return value


def _resolved_cwd(value):
    if not isinstance(value, str) or not value.strip():
        reject("cwd_required")
    path = pathlib.Path(value).expanduser()
    if not path.is_absolute():
        reject("cwd_must_be_absolute")
    return path.resolve()


def _codex_sessions_root():
    raw = os.environ.get("CODEX_HOME")
    codex_home = pathlib.Path(raw).expanduser() if raw else pathlib.Path.home() / ".codex"
    return (codex_home / "sessions").resolve()


def trusted_transcript_path(raw):
    if not isinstance(raw, str) or not raw:
        reject("transcript_path_required")
    path = pathlib.Path(raw).expanduser()
    if not path.is_absolute():
        reject("transcript_path_must_be_absolute")
    try:
        link_stat = path.lstat()
    except OSError as exc:
        reject("transcript_unreadable", detail=exc.strerror or exc.__class__.__name__)
    if stat.S_ISLNK(link_stat.st_mode):
        reject("transcript_path_symlink")
    if not stat.S_ISREG(link_stat.st_mode):
        reject("transcript_path_not_regular")
    root = _codex_sessions_root()
    resolved = path.resolve()
    try:
        relative = resolved.relative_to(root)
    except ValueError:
        reject("transcript_path_untrusted")
    parts = relative.parts
    if (len(parts) != 4 or not all(part.isdigit() for part in parts[:3])
            or tuple(map(len, parts[:3])) != (4, 2, 2)
            or not ROLLOUT_RE.fullmatch(parts[3])):
        reject("transcript_path_untrusted")
    return resolved


def _verify_open_identity(handle, path, expected=None, minimum_size=None):
    current = os.fstat(handle.fileno())
    try:
        named = path.stat()
    except OSError:
        reject("transcript_rotated", detail="source disappeared")
    if (current.st_dev, current.st_ino) != (named.st_dev, named.st_ino):
        reject("transcript_rotated")
    if expected is not None and ((current.st_dev, current.st_ino)
                                 != (expected["device"], expected["inode"])):
        reject("transcript_rotated")
    floor = expected.get("size") if expected is not None else minimum_size
    if floor is not None and current.st_size < floor:
        reject("transcript_truncated", previous_bytes=floor,
               current_bytes=current.st_size)
    if minimum_size is not None and current.st_size < minimum_size:
        reject("transcript_truncated", previous_bytes=minimum_size,
               current_bytes=current.st_size)
    return current


def _open_verified(path, expected=None):
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        reject("transcript_unreadable", detail=exc.strerror or exc.__class__.__name__)
    handle = os.fdopen(descriptor, "rb")
    try:
        current = _verify_open_identity(handle, path, expected)
    except HistoryFailure:
        handle.close()
        raise
    return handle, current


def _project_identity(cwd):
    """Derive a stable local repository identity without invoking git."""
    candidates = (cwd, *cwd.parents)
    for candidate in candidates:
        marker = candidate / ".git"
        if marker.is_dir():
            return f"git:{marker.resolve()}"
        if marker.is_file():
            try:
                line = marker.read_text(encoding="utf-8", errors="strict").strip()
            except (OSError, UnicodeError):
                continue
            if not line.startswith("gitdir: "):
                continue
            gitdir = pathlib.Path(line[8:])
            if not gitdir.is_absolute():
                gitdir = (candidate / gitdir).resolve()
            else:
                gitdir = gitdir.resolve()
            if gitdir.parent.name == "worktrees":
                gitdir = gitdir.parent.parent
            return f"git:{gitdir}"
    return f"path:{cwd}"


def _read_header(handle):
    raw = handle.readline(MAX_LINE_BYTES + 2)
    if not raw:
        reject("native_session_meta_required")
    if len(raw) > MAX_LINE_BYTES or not raw.endswith(b"\n"):
        reject("native_session_meta_invalid")
    try:
        row = json.loads(raw.decode("utf-8", errors="strict"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        reject("native_session_meta_invalid")
    if not isinstance(row, dict) or row.get("type") != "session_meta":
        reject("native_session_meta_required")
    native = row.get("payload")
    if not isinstance(native, dict):
        reject("native_session_meta_invalid")
    native_id = native.get("id")
    native_cwd = native.get("cwd")
    if (not isinstance(native_id, str) or not TASK_RE.fullmatch(native_id)
            or not isinstance(native_cwd, str) or not native_cwd.strip()):
        reject("native_session_meta_invalid")
    # Subagent rollouts carry their own payload.id while payload.session_id can
    # name the parent.  The hook's common session_id must match payload.id;
    # lineage is typed evidence only and never substitutes for that identity.
    lineage_id = native.get("session_id")
    if lineage_id is not None and (not isinstance(lineage_id, str) or not lineage_id):
        reject("native_session_meta_invalid")
    return native, handle.tell(), raw


def validate_native_rollout(payload):
    """Return server verb identity derived only from a trusted native rollout."""
    if not isinstance(payload, dict):
        reject("payload_object_required")
    runtime = payload.get("runtime")
    if runtime is not None and runtime != "codex":
        reject("native_codex_required", runtime=runtime)
    session_id = payload.get("session_id")
    if not isinstance(session_id, str) or not TASK_RE.fullmatch(session_id):
        reject("session_id_required")
    cwd = _resolved_cwd(payload.get("cwd"))
    path = trusted_transcript_path(payload.get("transcript_path"))
    handle, source_stat = _open_verified(path)
    try:
        native, header_end, header_raw = _read_header(handle)
        source_stat = _verify_open_identity(handle, path, minimum_size=source_stat.st_size)
    finally:
        handle.close()
    if native["id"] != session_id:
        reject("transcript_session_mismatch")
    try:
        native_cwd = pathlib.Path(native["cwd"]).expanduser()
        if not native_cwd.is_absolute() or native_cwd.resolve() != cwd:
            reject("transcript_cwd_mismatch")
    except (OSError, RuntimeError):
        reject("transcript_cwd_mismatch")
    return {
        "runtime": "codex", "native_task_id": session_id,
        "project_id": _project_identity(cwd), "cwd": str(cwd),
        "transcript_path": str(path), "path": path,
        "device": source_stat.st_dev, "inode": source_stat.st_ino,
        "size": source_stat.st_size, "header_end": header_end,
        "header_digest": hashlib.sha256(header_raw).hexdigest(),
    }


def source_highwater(meta):
    """Return a bounded content-derived cursor for deterministic event replay."""
    path = meta["path"]
    for _ in range(2):
        handle, source_stat = _open_verified(path, meta)
        try:
            start = max(meta["header_end"], source_stat.st_size - TAIL_DIGEST_BYTES)
            handle.seek(start)
            tail = handle.read(TAIL_DIGEST_BYTES)
            final_stat = _verify_open_identity(handle, path, meta,
                                               minimum_size=source_stat.st_size)
        finally:
            handle.close()
        if final_stat.st_size != source_stat.st_size:
            continue
        digest = hashlib.sha256()
        digest.update(meta["header_digest"].encode("ascii"))
        digest.update(str(start).encode("ascii"))
        digest.update(tail)
        return {"byte_offset": source_stat.st_size,
                "source_digest": digest.hexdigest(),
                "device": source_stat.st_dev, "inode": source_stat.st_ino,
                "tail_complete": source_stat.st_size == 0 or tail.endswith(b"\n")}
    reject("transcript_changed_during_read")


class _CompactedEnvelopeParser:
    """Streaming JSON structure check that retains only compact metadata."""

    TARGETS = frozenset(("window_number", "first_window_id",
                         "previous_window_id", "window_id"))

    def __init__(self):
        self.stack = []
        self.root_started = False
        self.root_done = False
        self.outer_type = None
        self.payload_started = False
        self.metadata = {}
        self.mode = "normal"
        self.token = bytearray()
        self.token_large = False
        self.escaped = False

    def _fail(self):
        reject("native_compaction_row_invalid")

    def _close(self, kind):
        if not self.stack or self.stack[-1]["kind"] != kind:
            self._fail()
        context = self.stack[-1]
        empty_state = "key" if kind == "object" else "value"
        if (context["state"] != "comma"
                and not (context["state"] == empty_state and context["empty"])):
            self._fail()
        self.stack.pop()
        if not self.stack:
            self.root_done = True

    def _capture(self, context, value):
        key = context.get("key")
        if context["role"] == "root" and key == "type":
            if self.outer_type is not None or not isinstance(value, str):
                self._fail()
            self.outer_type = value
        elif context["role"] == "payload" and key in self.TARGETS:
            if key in self.metadata or value is None:
                self._fail()
            self.metadata[key] = value

    def _value(self, token_kind, value=None):
        if not self.stack:
            self._fail()
        context = self.stack[-1]
        if context["state"] != "value":
            self._fail()
        if token_kind in {"object", "array"}:
            role = "other"
            if (context["kind"] == "object" and context["role"] == "root"
                    and context.get("key") == "payload"):
                if (token_kind != "object" or self.payload_started
                        or self.outer_type is None):
                    self._fail()
                self.payload_started = True
                role = "payload"
            context["state"] = "comma"
            context["empty"] = False
            self.stack.append({"kind": token_kind, "role": role,
                               "state": "key" if token_kind == "object" else "value",
                               "key": None, "empty": True})
        else:
            self._capture(context, value)
            context["state"] = "comma"
            context["empty"] = False

    def _deliver(self, kind, value=None):
        if self.root_done:
            self._fail()
        if not self.root_started:
            if kind != "punct" or value != "{":
                self._fail()
            self.root_started = True
            self.stack.append({"kind": "object", "role": "root",
                               "state": "key", "key": None, "empty": True})
            return
        if kind == "punct" and value in "[{":
            self._value("array" if value == "[" else "object")
            return
        context = self.stack[-1] if self.stack else None
        if context is None:
            self._fail()
        if context["kind"] == "object":
            if context["state"] == "key":
                if kind == "punct" and value == "}":
                    self._close("object")
                elif kind == "string" and isinstance(value, str):
                    context["key"] = value
                    context["state"] = "colon"
                    context["empty"] = False
                else:
                    self._fail()
            elif context["state"] == "colon":
                if kind != "punct" or value != ":":
                    self._fail()
                context["state"] = "value"
            elif context["state"] == "value":
                if kind in {"string", "scalar"}:
                    self._value(kind, value)
                else:
                    self._fail()
            else:
                if kind == "punct" and value == ",":
                    context["state"] = "key"
                    context["key"] = None
                elif kind == "punct" and value == "}":
                    self._close("object")
                else:
                    self._fail()
        else:
            if context["state"] == "value":
                if kind == "punct" and value == "]":
                    self._close("array")
                elif kind in {"string", "scalar"}:
                    context["state"] = "comma"
                    context["empty"] = False
                else:
                    self._fail()
            else:
                if kind == "punct" and value == ",":
                    context["state"] = "value"
                elif kind == "punct" and value == "]":
                    self._close("array")
                else:
                    self._fail()

    def _finish_token(self):
        if self.mode == "string":
            try:
                value = None if self.token_large else json.loads(
                    b'"' + bytes(self.token) + b'"')
            except (UnicodeDecodeError, json.JSONDecodeError):
                self._fail()
            self._deliver("string", value)
        else:
            try:
                value = json.loads(bytes(self.token))
            except (UnicodeDecodeError, json.JSONDecodeError):
                self._fail()
            self._deliver("scalar", value)
        self.mode = "normal"
        self.token.clear()
        self.token_large = False

    def feed(self, raw):
        for byte in raw:
            if self.mode == "string":
                if self.escaped:
                    self.escaped = False
                elif byte == 0x5C:
                    self.escaped = True
                elif byte == 0x22:
                    self._finish_token()
                    continue
                elif byte < 0x20:
                    self._fail()
                if not self.token_large:
                    if len(self.token) >= MAX_COMPACTION_TOKEN_BYTES:
                        self.token_large = True
                        self.token.clear()
                    else:
                        self.token.append(byte)
                continue
            if self.mode == "atom":
                if byte not in b" \t\r\n,]}:":
                    if len(self.token) >= 64:
                        self._fail()
                    self.token.append(byte)
                    continue
                self._finish_token()
            if byte in b" \t\r\n":
                continue
            if byte == 0x22:
                self.mode = "string"
                self.escaped = False
                continue
            character = chr(byte)
            if character in "{}[],:":
                self._deliver("punct", character)
            elif character in "-0123456789tfn":
                self.mode = "atom"
                self.token.append(byte)
            else:
                self._fail()

    def finish(self):
        if self.mode == "atom":
            self._finish_token()
        if self.mode != "normal" or not self.root_done or self.stack:
            self._fail()
        if self.outer_type != "compacted" or not self.payload_started:
            self._fail()
        if set(self.metadata) != self.TARGETS:
            self._fail()
        return self.metadata


def compaction_occurrence(meta, phase, deadline=None):
    """Return the immutable native context-window boundary for one hook.

    Codex's compact-hook schema exposes a turn id but no event or attempt id.
    The rollout's context-window chain supplies the missing occurrence identity:
    PreCompact observes the window being compacted, and PostCompact observes the
    newly installed window. Ordinary transcript appends do not change either.
    """
    if phase not in {"pre", "post"}:
        reject("compaction_phase_invalid")
    path = meta["path"]
    for _ in range(2):
        handle, source_stat = _open_verified(path, meta)
        try:
            scan_deadline = time.monotonic() + MAX_COMPACTION_SCAN_SECONDS
            if deadline is not None:
                scan_deadline = min(scan_deadline, deadline)
            native, _, header_raw = _read_header(handle)
            if (hashlib.sha256(header_raw).hexdigest() != meta["header_digest"]
                    or native.get("id") != meta["native_task_id"]):
                reject("transcript_changed_during_read")
            context_window = native.get("context_window")
            window_id = (context_window.get("window_id")
                         if isinstance(context_window, dict) else None)
            if not isinstance(window_id, str) or not WINDOW_ID_RE.fullmatch(window_id):
                reject("native_context_window_invalid")
            first_window_id = window_id
            window_number = 0

            # Large native rollouts are ordinary. Read only the bounded suffix
            # captured by the opening stat, while retaining the separately
            # verified header as the chain's immutable first-window anchor.
            tail_start = max(meta["header_end"],
                             source_stat.st_size - MAX_COMPACTION_SCAN_BYTES)
            handle.seek(tail_start)
            scanned_bytes = 0
            scanned_lines = 0
            if tail_start > meta["header_end"]:
                handle.seek(tail_start - 1)
                aligned = handle.read(1) == b"\n"
                handle.seek(tail_start)
                if not aligned:
                    # tail_start intentionally truncates an existing row. Its
                    # prefix may be arbitrarily large, so discard it in bounded
                    # chunks without parsing or applying the complete-row cap.
                    while handle.tell() < source_stat.st_size:
                        if time.monotonic() > scan_deadline:
                            reject("native_compaction_scan_timeout")
                        remaining = source_stat.st_size - handle.tell()
                        partial = handle.readline(min(
                            COMPACTION_TAIL_DISCARD_CHUNK_BYTES, remaining))
                        if not partial:
                            break
                        scanned_bytes += len(partial)
                        if scanned_bytes > MAX_COMPACTION_SCAN_BYTES:
                            reject("native_compaction_scan_limit")
                        if partial.endswith(b"\n"):
                            break
                    else:
                        reject("native_compaction_tail_boundary_missing")
                    if not partial.endswith(b"\n"):
                        reject("native_compaction_tail_boundary_missing")

            visible = []
            while handle.tell() < source_stat.st_size:
                if time.monotonic() > scan_deadline:
                    reject("native_compaction_scan_timeout")
                remaining = source_stat.st_size - handle.tell()
                prefix = handle.readline(min(MAX_COMPACTION_PREFIX_BYTES,
                                              remaining))
                if not prefix:
                    break
                scanned_bytes += len(prefix)
                if scanned_bytes > MAX_COMPACTION_SCAN_BYTES:
                    reject("native_compaction_scan_limit")
                parser = None
                # The verified native envelope places its depth-one type before
                # payload.  Every compacted row therefore carries this exact
                # string in the bounded prefix.  Rows without it can be skipped
                # without parsing or retaining an arbitrarily large payload.
                if b'"compacted"' in prefix:
                    parser = _CompactedEnvelopeParser()
                    parser.feed(prefix)
                    # A payload string can contain a spoofed marker. Trust only
                    # the structurally parsed outer type, which must precede the
                    # outer payload in the native envelope.
                    if parser.outer_type is None:
                        reject("native_compaction_row_invalid")
                    if parser.outer_type != "compacted":
                        parser = None
                    elif not parser.payload_started:
                        reject("native_compaction_row_invalid")

                complete = prefix.endswith(b"\n")
                while not complete and handle.tell() < source_stat.st_size:
                    if time.monotonic() > scan_deadline:
                        reject("native_compaction_scan_timeout")
                    remaining = source_stat.st_size - handle.tell()
                    chunk = handle.readline(min(
                        COMPACTION_TAIL_DISCARD_CHUNK_BYTES, remaining))
                    if not chunk:
                        break
                    scanned_bytes += len(chunk)
                    if scanned_bytes > MAX_COMPACTION_SCAN_BYTES:
                        reject("native_compaction_scan_limit")
                    if parser is not None:
                        parser.feed(chunk)
                    complete = chunk.endswith(b"\n")
                if not complete:
                    # The captured EOF may hold a row still being appended. It
                    # is never parsed or allowed to define a boundary.
                    break
                scanned_lines += 1
                if scanned_lines > MAX_COMPACTION_SCAN_LINES:
                    reject("native_compaction_scan_limit")
                if parser is None:
                    continue
                compacted = parser.finish()
                observed_number = compacted["window_number"]
                observed_first = compacted["first_window_id"]
                observed_previous = compacted["previous_window_id"]
                observed_window = compacted["window_id"]
                if (not isinstance(observed_number, int)
                        or isinstance(observed_number, bool)
                        or observed_number < 1
                        or observed_first != first_window_id
                        or not isinstance(observed_previous, str)
                        or not WINDOW_ID_RE.fullmatch(observed_previous)
                        or not isinstance(observed_window, str)
                        or not WINDOW_ID_RE.fullmatch(observed_window)
                        or observed_window == observed_previous):
                    reject("native_compaction_chain_invalid")
                if visible:
                    previous_number, previous_window = visible[-1]
                    if (observed_number != previous_number + 1
                            or observed_previous != previous_window):
                        reject("native_compaction_chain_invalid")
                elif tail_start == meta["header_end"]:
                    if observed_number != 1 or observed_previous != first_window_id:
                        reject("native_compaction_chain_invalid")
                visible.append((observed_number, observed_window))
                window_number = observed_number
                window_id = observed_window
            if time.monotonic() > scan_deadline:
                reject("native_compaction_scan_timeout")
            final_stat = _verify_open_identity(handle, path, meta,
                                               minimum_size=source_stat.st_size)
        finally:
            handle.close()
        if final_stat.st_size != source_stat.st_size:
            continue
        if window_number == 0 and (phase == "post"
                                   or tail_start > meta["header_end"]):
            reject("native_post_compaction_boundary_missing")
        return {"source_window_id": window_id,
                "source_window_number": window_number}
    reject("transcript_changed_during_read")


def _cursor(payload, meta):
    cursor = payload.get("cursor")
    if cursor is None:
        return {"byte_offset": meta["header_end"], "line": 1,
                "device": meta["device"], "inode": meta["inode"]}
    if not isinstance(cursor, dict):
        reject("cursor_invalid")
    required = ("byte_offset", "line", "device", "inode")
    if any(not isinstance(cursor.get(key), int) or isinstance(cursor.get(key), bool)
           for key in required):
        reject("cursor_invalid")
    if (cursor["device"], cursor["inode"]) != (meta["device"], meta["inode"]):
        reject("transcript_rotated")
    if meta["size"] < cursor["byte_offset"]:
        reject("transcript_truncated", previous_offset=cursor["byte_offset"],
               current_bytes=meta["size"])
    if cursor["byte_offset"] < meta["header_end"] or cursor["line"] < 1:
        reject("cursor_invalid")
    return {key: cursor[key] for key in required}


def _redact_text(value):
    redactions = 0
    value, count = PRIVATE_KEY_BLOCK.subn("<REDACTED>", value)
    redactions += count
    for index, pattern in enumerate(REDACTION_PATTERNS):
        if index == 0:
            value, count = pattern.subn(r"\1<REDACTED>\3", value)
        elif index in (1, 2):
            value, count = pattern.subn(r"\1<REDACTED>", value)
        elif index == 3:
            value, count = pattern.subn(r"\1<REDACTED>\3", value)
        else:
            value, count = pattern.subn("<REDACTED>", value)
        redactions += count
    return value, redactions


def _content_text(value):
    """Extract only native text blocks; arbitrary object values stay opaque."""
    if isinstance(value, str):
        return value, 0
    if not isinstance(value, list):
        return "", 1
    values = []
    unsupported = 0
    for item in value:
        if isinstance(item, str):
            values.append(item)
            continue
        if not isinstance(item, dict):
            unsupported += 1
            continue
        if item.get("type") not in {"text", "input_text", "output_text",
                                    "summary_text"}:
            unsupported += 1
            continue
        text = item.get("text")
        if isinstance(text, str):
            values.append(text)
        else:
            unsupported += 1
    return "\n".join(values), unsupported


def row_evidence_text(row):
    """Extract allowlisted native text shapes with explicit coverage metadata."""
    row_type = row.get("type")
    payload = row.get("payload")
    if not isinstance(payload, dict):
        return {"supported": False, "text": "", "redactions": 0,
                "unsupported_fragments": 1, "truncated": False}
    payload_type = payload.get("type")
    unsupported = 0
    if row_type == "response_item" and payload_type in {"message", "agent_message"}:
        text, unsupported = _content_text(payload.get("content"))
    elif row_type == "response_item" and payload_type == "reasoning":
        text, unsupported = _content_text(payload.get("summary"))
    elif row_type == "response_item" and payload_type in {
            "custom_tool_call", "function_call", "mcp_tool_call"}:
        tool_text = payload.get("input") if payload_type == "custom_tool_call" else payload.get("arguments")
        text = "\n".join(part for part in (payload.get("name"), tool_text)
                         if isinstance(part, str))
        unsupported = 0 if isinstance(tool_text, str) else 1
    elif row_type == "response_item" and payload_type in {
            "custom_tool_call_output", "function_call_output", "mcp_tool_call_output"}:
        text, unsupported = _content_text(payload.get("output"))
    elif row_type == "event_msg" and payload_type in {"user_message", "agent_message"}:
        text = payload.get("message") if isinstance(payload.get("message"), str) else ""
        unsupported = 0 if text else 1
    elif row_type == "event_msg" and payload_type == "task_complete":
        value = payload.get("last_agent_message")
        text = value if isinstance(value, str) else ""
        unsupported = 0 if text else 1
    elif row_type == "turn_context" and "summary" in payload:
        text, unsupported = _content_text(payload.get("summary"))
    else:
        return {"supported": False, "text": "", "redactions": 0,
                "unsupported_fragments": 0, "truncated": False}
    text, redactions = _redact_text(text)
    text, truncated = _utf8_clip(text, MAX_ROW_TEXT_BYTES)
    return {"supported": True, "text": text, "redactions": redactions,
            "unsupported_fragments": unsupported, "truncated": truncated}


def _utf8_clip(value, limit):
    raw = value.encode("utf-8")
    if len(raw) <= limit:
        return value, False
    return raw[:limit].decode("utf-8", errors="ignore"), True


def _snippet(text, query):
    index = text.find(query)
    if index < 0:
        return "", False
    start = max(0, index - MAX_SNIPPET_BYTES // 3)
    return _utf8_clip(text[start:], MAX_SNIPPET_BYTES)


def _row_ref(row, raw, line, byte_start, byte_end, meta):
    payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
    return {"line": line, "byte_start": byte_start, "byte_end": byte_end,
            "device": meta["device"], "inode": meta["inode"],
            "row_digest": hashlib.sha256(raw.rstrip(b"\r\n")).hexdigest(),
            "row_type": row.get("type"), "payload_type": payload.get("type")}


def _add_gap(gaps, coverage, gap):
    coverage["gaps_total"] += 1
    if len(gaps) < MAX_RESULTS:
        gaps.append(gap)


def command_search(payload):
    meta = validate_native_rollout(payload)
    query = payload.get("query")
    if not isinstance(query, str) or not query:
        reject("query_required")
    if len(query.encode("utf-8")) > MAX_QUERY_BYTES:
        reject("query_too_large", maximum_bytes=MAX_QUERY_BYTES)
    limit = _bounded_integer(payload, "limit", DEFAULT_RESULTS, MAX_RESULTS)
    max_bytes = _bounded_integer(payload, "max_bytes", MAX_SCAN_BYTES, MAX_SCAN_BYTES)
    max_lines = _bounded_integer(payload, "max_lines", MAX_SCAN_LINES, MAX_SCAN_LINES)
    handle, opened_stat = _open_verified(meta["path"], meta)
    current_meta = {**meta, "size": opened_stat.st_size}
    cursor = _cursor(payload, current_meta)
    matches, gaps, warnings = [], [], []
    detail = {"gaps_total": 0, "unsupported_rows": 0,
              "unsupported_fragments": 0, "redactions": 0,
              "extraction_truncated_rows": 0}
    scanned_bytes = scanned_lines = 0
    offset, line = cursor["byte_offset"], cursor["line"]
    complete = False
    try:
        handle.seek(offset)
        while scanned_lines < max_lines and scanned_bytes < max_bytes:
            byte_start = handle.tell()
            remaining = max_bytes - scanned_bytes
            raw = handle.readline(min(MAX_LINE_BYTES + 1, remaining))
            if not raw:
                complete = True
                break
            if not raw.endswith(b"\n"):
                at_eof = handle.tell() >= opened_stat.st_size
                if len(raw) >= MAX_LINE_BYTES + 1:
                    chunks = [raw]
                    while not chunks[-1].endswith(b"\n") and sum(map(len, chunks)) < remaining:
                        chunk = handle.readline(min(64 * 1024, remaining - sum(map(len, chunks))))
                        if not chunk:
                            break
                        chunks.append(chunk)
                    raw = b"".join(chunks)
                    if not raw.endswith(b"\n") and handle.tell() < opened_stat.st_size:
                        handle.seek(byte_start)
                        warnings.extend(["oversize_line", "byte_limit_reached"])
                        break
                    _add_gap(gaps, detail, {"line": line + 1,
                                           "byte_start": byte_start,
                                           "byte_end": handle.tell(),
                                           "kind": "oversize_line"})
                    warnings.append("oversize_line")
                elif at_eof:
                    # Retry the live final line once.  If it remains incomplete,
                    # keep the cursor at its start so a later append can recover it.
                    handle.seek(byte_start)
                    retry = handle.readline(min(MAX_LINE_BYTES + 1, remaining))
                    if retry.endswith(b"\n"):
                        raw = retry
                    else:
                        handle.seek(byte_start)
                        _add_gap(gaps, detail, {"line": line + 1,
                                               "byte_start": byte_start,
                                               "byte_end": opened_stat.st_size,
                                               "kind": "partial_final_line"})
                        warnings.append("partial_final_line")
                        break
                else:
                    handle.seek(byte_start)
                    warnings.append("byte_limit_reached")
                    break
            byte_end = handle.tell()
            scanned_bytes += byte_end - byte_start
            scanned_lines += 1
            line += 1
            offset = byte_end
            if len(raw) > MAX_LINE_BYTES:
                continue
            try:
                row = json.loads(raw.decode("utf-8", errors="strict"))
            except UnicodeDecodeError:
                _add_gap(gaps, detail, {"line": line, "byte_start": byte_start,
                                        "byte_end": byte_end,
                                        "kind": "invalid_utf8"})
                continue
            except json.JSONDecodeError:
                _add_gap(gaps, detail, {"line": line, "byte_start": byte_start,
                                        "byte_end": byte_end,
                                        "kind": "malformed_json"})
                continue
            if not isinstance(row, dict):
                _add_gap(gaps, detail, {"line": line, "byte_start": byte_start,
                                        "byte_end": byte_end,
                                        "kind": "unknown_format"})
                continue
            extracted = row_evidence_text(row)
            detail["redactions"] += extracted["redactions"]
            detail["unsupported_fragments"] += extracted["unsupported_fragments"]
            detail["extraction_truncated_rows"] += int(extracted["truncated"])
            if not extracted["supported"]:
                detail["unsupported_rows"] += 1
                _add_gap(gaps, detail, {"line": line, "byte_start": byte_start,
                                        "byte_end": byte_end,
                                        "kind": "unsupported_format",
                                        "row_type": row.get("type"),
                                        "payload_type": ((row.get("payload") or {}).get("type")
                                                         if isinstance(row.get("payload"), dict)
                                                         else None)})
                continue
            if extracted["unsupported_fragments"]:
                _add_gap(gaps, detail, {"line": line, "byte_start": byte_start,
                                        "byte_end": byte_end,
                                        "kind": "unsupported_content_fragment",
                                        "count": extracted["unsupported_fragments"]})
            if query not in extracted["text"]:
                continue
            snippet, snippet_truncated = _snippet(extracted["text"], query)
            matches.append({"source": _row_ref(row, raw, line, byte_start,
                                                byte_end, meta),
                            "evidence_text": snippet,
                            "evidence_truncated": extracted["truncated"] or snippet_truncated,
                            "redactions_applied": extracted["redactions"]})
            if len(matches) >= limit:
                warnings.append("result_limit_reached")
                break
        if scanned_lines >= max_lines and offset < opened_stat.st_size:
            warnings.append("line_limit_reached")
        if scanned_bytes >= max_bytes and offset < opened_stat.st_size:
            warnings.append("byte_limit_reached")
        if offset >= opened_stat.st_size and "partial_final_line" not in warnings:
            complete = True
        final_stat = _verify_open_identity(handle, meta["path"], meta,
                                           minimum_size=opened_stat.st_size)
        if final_stat.st_size > opened_stat.st_size:
            warnings.append("source_grew_during_read")
    finally:
        handle.close()
    gaps_omitted = detail["gaps_total"] - len(gaps)
    if gaps_omitted:
        warnings.append("gaps_omitted")
    if detail["unsupported_rows"] or detail["unsupported_fragments"]:
        warnings.append("unsupported_format")
    if detail["redactions"]:
        warnings.append("redacted_evidence")
    if detail["extraction_truncated_rows"]:
        warnings.append("evidence_extraction_truncated")
    warnings = list(dict.fromkeys(warnings))
    next_cursor = {"byte_offset": offset, "line": line,
                   "device": opened_stat.st_dev, "inode": opened_stat.st_ino}
    return {"ok": True, "runtime": "codex", "native_task_id": meta["native_task_id"],
            "project_id": meta["project_id"], "transcript_ref": meta["transcript_path"],
            "attribution": ATTRIBUTION, "matches": matches, "count": len(matches),
            "next_cursor": next_cursor, "gaps": gaps,
            "coverage": {"start_byte": cursor["byte_offset"], "end_byte": offset,
                         "start_line": cursor["line"] + 1, "end_line": line,
                         "file_bytes": opened_stat.st_size,
                         "bytes_scanned": scanned_bytes, "lines_scanned": scanned_lines,
                         "complete": complete and not warnings, "warnings": warnings,
                         "gaps_total": detail["gaps_total"],
                         "gaps_omitted": gaps_omitted,
                         "unsupported_rows": detail["unsupported_rows"],
                         "unsupported_fragments": detail["unsupported_fragments"],
                         "redactions": detail["redactions"],
                         "extraction_truncated_rows": detail["extraction_truncated_rows"],
                         "limits": {"max_bytes": max_bytes, "max_lines": max_lines,
                                    "max_results": limit}}}


def command_fetch(payload):
    meta = validate_native_rollout(payload)
    ref = payload.get("ref")
    if not isinstance(ref, dict):
        reject("source_ref_required")
    integer_fields = ("line", "byte_start", "byte_end", "device", "inode")
    if any(not isinstance(ref.get(key), int) or isinstance(ref.get(key), bool)
           for key in integer_fields):
        reject("source_ref_invalid")
    if (ref["device"], ref["inode"]) != (meta["device"], meta["inode"]):
        reject("transcript_rotated")
    if (ref["byte_start"] < meta["header_end"] or ref["byte_end"] > meta["size"]
            or ref["byte_end"] <= ref["byte_start"]
            or ref["byte_end"] - ref["byte_start"] > MAX_LINE_BYTES):
        reject("source_ref_invalid")
    handle, opened_stat = _open_verified(meta["path"], meta)
    try:
        if ref["byte_start"]:
            handle.seek(ref["byte_start"] - 1)
            if handle.read(1) != b"\n":
                reject("source_ref_invalid")
        handle.seek(ref["byte_start"])
        raw = handle.read(ref["byte_end"] - ref["byte_start"])
        _verify_open_identity(handle, meta["path"], meta,
                              minimum_size=opened_stat.st_size)
    finally:
        handle.close()
    digest = hashlib.sha256(raw.rstrip(b"\r\n")).hexdigest()
    if not isinstance(ref.get("row_digest"), str) or digest != ref["row_digest"]:
        reject("source_ref_changed")
    try:
        row = json.loads(raw.decode("utf-8", errors="strict"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        reject("transcript_gap")
    if not isinstance(row, dict):
        reject("transcript_gap")
    extracted = row_evidence_text(row)
    if not extracted["supported"]:
        reject("unsupported_transcript_row", row_type=row.get("type"),
               payload_type=((row.get("payload") or {}).get("type")
                             if isinstance(row.get("payload"), dict) else None))
    evidence, snippet_truncated = _utf8_clip(extracted["text"], MAX_SNIPPET_BYTES)
    source = _row_ref(row, raw, ref["line"], ref["byte_start"],
                      ref["byte_end"], meta)
    return {"ok": True, "runtime": "codex", "native_task_id": meta["native_task_id"],
            "project_id": meta["project_id"], "transcript_ref": meta["transcript_path"],
            "attribution": ATTRIBUTION, "source": source, "evidence_text": evidence,
            "evidence_truncated": extracted["truncated"] or snippet_truncated,
            "coverage": {"redactions": extracted["redactions"],
                         "unsupported_fragments": extracted["unsupported_fragments"]}}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("search", "fetch"))
    parser.add_argument("payload", nargs="?", default="-")
    args = parser.parse_args()
    raw = sys.stdin.read() if args.payload == "-" else args.payload
    try:
        payload = json.loads(raw)
        result = command_search(payload) if args.command == "search" else command_fetch(payload)
    except json.JSONDecodeError:
        result = {"ok": False, "error": "invalid_json"}
    except HistoryFailure as exc:
        result = exc.payload
    print(json.dumps(result, separators=(",", ":"), ensure_ascii=False))
    return 0 if result.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
