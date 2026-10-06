#!/usr/bin/env python3
"""ops/session-trace-archive.py -- nightly local archive of CARR agent session
transcripts, so old CARR sessions are no longer lost.

WHY THIS EXISTS. Claude Code keeps session transcripts at
~/.claude/projects/<project-dir>/*.jsonl, and Codex keeps its sessions under
~/.codex/sessions if that directory exists. Both clients prune old sessions,
and for this project only 8 transcripts survived, the oldest from Sept 23.
That is raw agent-trace history -- worth keeping for a later curator and
search slice -- and there was no durable copy anywhere. This job makes one.

SCOPE: CARR SESSIONS ONLY. Everything else on this Mac -- personal (Life AI)
sessions, unrelated projects, scratchpads -- is never archived. The rule lives
in ONE place, lib/carr_session_scope.py, shared with the displacement
baselines' _project_roots():

  * Claude Code: only project directories is_carr_claude_project() accepts
    (vault projects named with "CARR-AI", the repo project, repo worktrees).
    Other project directories are counted and never opened.
  * Codex: a session is archived only when the working directory recorded in
    its own session-metadata line (the FIRST line) is inside the checkout or
    the vault. Only that one line is read; if it is missing, unreadable, or
    carries no working directory, the session is skipped and counted, never
    archived. No other line of a transcript is ever parsed or logged.

DATA-CLASS BOUNDARY. Beyond that one Codex metadata line, this script never
parses, prints, or summarises transcript CONTENT: it streams bytes through
gzip and sha256. Error lines name a file by basename plus a short hash of its
archive key, never by its full source path, and never echo an OSError's text
(which embeds the path).

WHERE THE ARCHIVE LIVES. ~/carr-local/session-trace-archive/, outside this
repo. Layout:

    ~/carr-local/session-trace-archive/
        claude/<project-dir>/<file>.jsonl.gz
        codex/<same relative path under ~/.codex/sessions>.gz
        manifest.jsonl        <- one line per archive write, append-only

COPY RULE. A file is (re)archived when it is new, or when its size OR its
mtime_ns differs from the last manifest row for it (size alone misses a
same-size rewrite). Each write streams gzip into a sibling
"<name>.partial-<pid>" temp file and renames it into place atomically, so the
final path only ever holds a complete archive. When a re-archive is not a
growth (same size or smaller -- a rewrite, not an append), the previous
archive is kept beside it as <name>.superseded-<mtime_ns>.jsonl.gz rather
than overwritten, so nothing already preserved is lost.

HOUSEKEEPING. At run start, "*.partial-<pid>" temp files left by a run whose
pid is no longer alive are unlinked.

THE 10-MINUTE QUIET WINDOW. A file whose mtime is under 10 minutes old may
still be written by a live client; it is skipped and picked up next run.

PERMISSIONS. Directories this script creates are 0700; files it writes 0600.

FAILURE CONTRACT. Per-file failures are collected and the run continues; the
exit is nonzero when any occurred. A missing ~/.codex/sessions is an expected
state on a Claude-only machine, not a failure. Source files are never
modified or deleted.
"""

from __future__ import annotations

import argparse
import errno
import gzip
import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lib.carr_session_scope import is_carr_claude_project, is_carr_cwd  # noqa: E402

DIR_MODE = 0o700
FILE_MODE = 0o600
QUIET_WINDOW_SECONDS = 10 * 60
COPY_CHUNK = 1024 * 1024
META_MAX_BYTES = 2 * 1024 * 1024
GZIP_LEVEL = 6

DEFAULT_CLAUDE_PROJECTS_DIR = Path.home() / ".claude" / "projects"
DEFAULT_CODEX_SESSIONS_DIR = Path.home() / ".codex" / "sessions"
DEFAULT_ARCHIVE_ROOT = Path.home() / "carr-local" / "session-trace-archive"

PARTIAL_RE = re.compile(r"\.partial-(\d+)$")

COUNT_KEYS = (
    "copied", "skipped_unchanged", "skipped_recent", "superseded_kept",
    "skipped_out_of_scope_projects", "skipped_codex_out_of_scope",
    "skipped_codex_no_metadata", "stale_partials_removed",
)


class ArchiveError(RuntimeError):
    """A real I/O failure. Its message never carries a full source path."""


def ref(key: str) -> str:
    """How a file is named in any error: basename plus a hash of its key."""
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:12]
    return f"{key.rsplit('/', 1)[-1]} #{digest}"


def _why(exc: OSError) -> str:
    # Never str(exc): an OSError's text embeds the full filename.
    return errno.errorcode.get(exc.errno or 0, type(exc).__name__)


def _ensure_dir(path: Path, key: str) -> None:
    """mkdir -p, forcing 0700 only on directories this call creates."""
    if path.is_dir():
        return
    if path.parent != path:
        _ensure_dir(path.parent, key)
    try:
        path.mkdir(mode=DIR_MODE)
    except FileExistsError:
        return
    except OSError as exc:
        raise ArchiveError(f"mkdir failed for {ref(key)}: {_why(exc)}") from None
    try:
        os.chmod(path, DIR_MODE)
    except OSError as exc:
        raise ArchiveError(f"chmod failed for {ref(key)}: {_why(exc)}") from None


def _pid_alive(pid: int) -> bool:
    if pid <= 0 or pid == os.getpid():
        # Our own pid has no temp file in flight at run start: anything
        # carrying it is a dead run's leftover whose pid was recycled to us.
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return True
    return True


def remove_stale_partials(archive_root: Path) -> int:
    removed = 0
    if not archive_root.is_dir():
        return 0
    for path in archive_root.rglob("*.partial-*"):
        match = PARTIAL_RE.search(path.name)
        if not match or not path.is_file() or path.is_symlink():
            continue
        if _pid_alive(int(match.group(1))):
            continue
        try:
            path.unlink()
            removed += 1
        except FileNotFoundError:
            pass
    return removed


def codex_session_cwd(path: Path) -> str | None:
    """The working directory from a Codex session's metadata line, or None.

    Reads ONLY the first line, capped at META_MAX_BYTES. Accepts the rollout
    shape {"type": "session_meta", "payload": {"cwd": ...}} and a bare
    top-level "cwd". Anything else -- no line, a line over the cap, invalid
    JSON, no cwd -- is None, which the caller counts and never archives.
    """
    try:
        with open(path, "rb") as fh:
            line = fh.readline(META_MAX_BYTES)
    except OSError:
        return None
    if not line.endswith(b"\n") and len(line) >= META_MAX_BYTES:
        return None
    try:
        meta = json.loads(line)
    except ValueError:
        return None
    if not isinstance(meta, dict):
        return None
    payload = meta.get("payload") if meta.get("type") == "session_meta" else meta
    if not isinstance(payload, dict):
        return None
    cwd = payload.get("cwd")
    return cwd if isinstance(cwd, str) else None


def _iter_jsonl(root: Path):
    for path in sorted(root.rglob("*.jsonl")):
        if path.is_file() and not path.is_symlink():
            yield path


def _load_state(manifest_path: Path) -> dict[str, dict]:
    """Last manifest row per archive key: the size/mtime_ns archived last."""
    state: dict[str, dict] = {}
    try:
        with open(manifest_path, "r", encoding="utf-8") as fh:
            for line in fh:
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if isinstance(row, dict) and isinstance(row.get("key"), str):
                    state[row["key"]] = row
    except FileNotFoundError:
        pass
    return state


def gzip_stream(src: Path, dest: Path, key: str) -> str:
    """Stream src through gzip into dest via a temp file and atomic rename.

    Memory stays at one COPY_CHUNK plus zlib's window whatever the file size.
    Returns the sha256 of the SOURCE bytes, computed on the same pass.
    """
    tmp = dest.with_name(dest.name + f".partial-{os.getpid()}")
    digest = hashlib.sha256()
    try:
        with open(src, "rb") as rf, open(tmp, "wb") as wf:
            os.chmod(tmp, FILE_MODE)
            with gzip.GzipFile(filename="", mode="wb", fileobj=wf,
                               compresslevel=GZIP_LEVEL, mtime=0) as gz:
                while True:
                    chunk = rf.read(COPY_CHUNK)
                    if not chunk:
                        break
                    digest.update(chunk)
                    gz.write(chunk)
            wf.flush()
            os.fsync(wf.fileno())
        os.replace(tmp, dest)
    except OSError as exc:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise ArchiveError(f"archive failed for {ref(key)}: {_why(exc)}") from None
    return digest.hexdigest()


def unchanged_since(prior: dict, st: os.stat_result) -> bool:
    """Size AND mtime_ns both match the last archive: size alone misses a
    same-size rewrite."""
    return prior.get("size") == st.st_size and prior.get("mtime_ns") == st.st_mtime_ns


def _archive_one(src: Path, key: str, archive_root: Path, state: dict,
                 manifest_fh, *, now: float, source_label: str) -> list[str]:
    try:
        st = src.stat()
    except OSError as exc:
        raise ArchiveError(f"stat failed for {ref(key)}: {_why(exc)}") from None
    if (now - st.st_mtime) < QUIET_WINDOW_SECONDS:
        return ["skipped_recent"]

    dest = archive_root / (key + ".gz")
    prior = state.get(key)
    if prior and dest.exists() and unchanged_since(prior, st):
        return ["skipped_unchanged"]

    _ensure_dir(dest.parent, key)
    outcomes = ["copied"]
    keep_as = None
    if dest.exists() and prior and st.st_size <= int(prior.get("size") or 0):
        # Not a growth: a rewrite or truncation. Keep the earlier archive.
        stem = dest.name[: -len(".jsonl.gz")] if dest.name.endswith(".jsonl.gz") else dest.name
        keep_as = dest.with_name(f"{stem}.superseded-{prior.get('mtime_ns', 0)}.jsonl.gz")

    if keep_as is not None:
        # Write the new archive fully first, then move the old one aside, so
        # a failed write never leaves the key without an archive. The staged
        # name keeps the .partial-<pid> shape so a crash here is swept too.
        staged = dest.with_name(dest.name + f".next.partial-{os.getpid()}")
        sha = gzip_stream(src, staged, key)
        try:
            os.replace(dest, keep_as)
            os.replace(staged, dest)
        except OSError as exc:
            raise ArchiveError(f"supersede failed for {ref(key)}: {_why(exc)}") from None
        outcomes.append("superseded_kept")
    else:
        sha = gzip_stream(src, dest, key)

    manifest_fh.write(json.dumps({
        "source": source_label,
        "key": key,
        "archived_path": key + ".gz",
        "size": st.st_size,
        "mtime_ns": st.st_mtime_ns,
        "sha256": sha,
        "archived_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
    }) + "\n")
    manifest_fh.flush()
    return outcomes


def run_archive(*, claude_projects_dir: Path, codex_sessions_dir: Path,
                archive_root: Path, now: float | None = None,
                home: str | None = None, checkout: str | None = None) -> dict:
    """Archive every in-scope, quiet, changed session file once.

    `home` and `checkout` exist for the selftest; production leaves them None
    so lib/carr_session_scope derives this machine's roots.
    """
    now = time.time() if now is None else now
    counts = {k: 0 for k in COUNT_KEYS}
    errors: list[str] = []

    _ensure_dir(archive_root, "archive-root")
    counts["stale_partials_removed"] = remove_stale_partials(archive_root)
    manifest_path = archive_root / "manifest.jsonl"
    state = _load_state(manifest_path)

    jobs: list[tuple[Path, str, str]] = []
    if claude_projects_dir.is_dir():
        for project in sorted(claude_projects_dir.iterdir()):
            if not project.is_dir() or project.is_symlink():
                continue
            if not is_carr_claude_project(project.name, checkout=checkout):
                counts["skipped_out_of_scope_projects"] += 1
                continue
            for src in _iter_jsonl(project):
                rel = src.relative_to(claude_projects_dir).as_posix()
                jobs.append((src, f"claude/{rel}", "claude"))
    # A missing ~/.codex/sessions is expected on a Claude-only machine.
    if codex_sessions_dir.is_dir():
        for src in _iter_jsonl(codex_sessions_dir):
            cwd = codex_session_cwd(src)
            if cwd is None:
                counts["skipped_codex_no_metadata"] += 1
                continue
            if not is_carr_cwd(cwd, home=home, checkout=checkout):
                counts["skipped_codex_out_of_scope"] += 1
                continue
            rel = src.relative_to(codex_sessions_dir).as_posix()
            jobs.append((src, f"codex/{rel}", "codex"))

    try:
        manifest_fh = open(manifest_path, "a", encoding="utf-8")
    except OSError as exc:
        raise ArchiveError(f"could not open manifest: {_why(exc)}") from None
    try:
        os.chmod(manifest_path, FILE_MODE)
        for src, key, label in jobs:
            try:
                for outcome in _archive_one(src, key, archive_root, state, manifest_fh,
                                            now=now, source_label=label):
                    counts[outcome] += 1
            except ArchiveError as exc:
                errors.append(str(exc))
    finally:
        manifest_fh.close()

    return {**counts, "errors": errors}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--claude-projects-dir", type=Path, default=DEFAULT_CLAUDE_PROJECTS_DIR)
    parser.add_argument("--codex-sessions-dir", type=Path, default=DEFAULT_CODEX_SESSIONS_DIR)
    parser.add_argument("--archive-root", type=Path, default=DEFAULT_ARCHIVE_ROOT)
    args = parser.parse_args(argv)

    try:
        summary = run_archive(claude_projects_dir=args.claude_projects_dir,
                              codex_sessions_dir=args.codex_sessions_dir,
                              archive_root=args.archive_root)
    except ArchiveError as exc:
        print(f"session-trace-archive: FAILED: {exc}", file=sys.stderr)
        return 1

    print("session-trace-archive: " + " ".join(
        f"{k}={summary[k]}" for k in COUNT_KEYS) + f" errors={len(summary['errors'])}")
    for err in summary["errors"]:
        print(f"session-trace-archive: error: {err}", file=sys.stderr)
    return 1 if summary["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
