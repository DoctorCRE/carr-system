"""State-bound R03 restore authoring and admission.

Only explicitly approved tracked dirt may be restored to a pinned regular
file. Unapproved paths, renames/copies, added paths without a pinned blob and
unsupported pinned objects refuse the whole set. Attribution is diagnostic.
Git calls are bounded and use the shared repository-location scrubber; status
and index bytes preserve literal filenames, including newline and non-UTF-8
bytes. Each entry binds the current index stages, filesystem mode/type and
worktree bytes, which the runner rechecks at admission. Execution with a
nonempty restore set is held until enforced writer exclusion is implemented.
"""
from __future__ import annotations

import hashlib
import os
import stat
import subprocess
import sys
from pathlib import Path
from typing import Iterable, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ops"))
from git_env import scrubbed_env

GIT_TIMEOUT_SECONDS = 60


class RestoreSetRefusal(RuntimeError):
    """The restore set cannot safely be authored or consumed."""


def _git_stdout(repository: Path, *args: str) -> bytes:
    """Bounded, repository-local Git output without text/newline translation."""
    try:
        result = subprocess.run(
            ["git", "--literal-pathspecs", "-C", str(repository), *args],
            env=scrubbed_env(), capture_output=True, check=True,
            timeout=GIT_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        raise RestoreSetRefusal("Git timed out; no restore set was authored") from exc
    except (OSError, subprocess.CalledProcessError) as exc:
        raise RestoreSetRefusal(f"Git failed; no restore set was authored: {exc}") from exc
    return result.stdout


def dirty_paths(repository: Path) -> list[str]:
    """Tracked dirty literal names, decoded losslessly; refuse renames/copies.

    Restoration only supports existing pinned files. A rename/copy carries a
    second path and different operations, so refuse it rather than omit a side.
    Untracked files belong to the separately admitted clean/park sets.
    """
    raw = _git_stdout(repository, "status", "--porcelain=v1", "-z", "--untracked-files=no")
    out = []
    for entry in raw.split(b"\0"):
        if not entry:
            continue
        status, path = entry[:2], entry[3:]
        if b"R" in status or b"C" in status:
            raise RestoreSetRefusal("unsupported rename/copy; no restore set was authored")
        if len(entry) < 4 or entry[2:3] != b" ":
            raise RestoreSetRefusal("malformed Git status; no restore set was authored")
        if status != b"??":
            out.append(path.decode("utf-8", "surrogateescape"))
    return sorted(set(out))


def _observed_state(repository: Path, path: str) -> str:
    """Bind the exact index stages, filesystem type/mode and worktree bytes."""
    index = _git_stdout(repository, "ls-files", "--stage", "-z", "--", path)
    target = repository / path
    try:
        metadata = target.lstat()
    except FileNotFoundError:
        worktree = b"missing"
    else:
        if stat.S_ISLNK(metadata.st_mode):
            body = os.fsencode(os.readlink(target))
        elif stat.S_ISREG(metadata.st_mode):
            body = target.read_bytes()
        else:
            raise RestoreSetRefusal(f"unsupported restore file type: {path!r}")
        worktree = str(metadata.st_mode).encode("ascii") + b"\0" + body
    return "sha256:" + hashlib.sha256(index + b"\0" + worktree).hexdigest()


def attribute(repository: Path, paths: Sequence[str]) -> dict[str, str]:
    """Best-effort diagnostic only; failures never grant restore authority."""
    tool = repository / "ops" / "worktree-attribution.py"
    owners = {p: "unattributed" for p in paths}
    if not paths or not tool.exists():
        return owners
    try:
        result = subprocess.run(
            ["python3", str(tool), *paths], env=scrubbed_env(),
            capture_output=True, text=True, errors="surrogateescape",
            cwd=str(repository), timeout=60,
        )
    except (OSError, subprocess.SubprocessError):
        return owners
    if result.returncode:
        return owners
    current = None
    for line in result.stdout.splitlines():
        stripped = line.strip()
        if stripped in owners:
            current = stripped
        elif current and stripped:
            owners[current] = stripped
            current = None
    return owners


def build_restore_set(repository: Path, pin: str,
                      allowed: Iterable[str]) -> list[dict[str, str]]:
    """Author only explicitly allowed tracked dirt, bound to observed state.

    No result escapes on failure, timeout, unsupported operations or a changing
    tree. Approved-but-clean names add nothing. Consumers compare the complete
    authored entries again at admission. State binding detects earlier drift;
    it does not exclude writers during disposal or authorize restoration alone.
    """
    if len(pin) not in (40, 64) or any(c not in "0123456789abcdef" for c in pin):
        raise RestoreSetRefusal("restore pin must be a full hexadecimal object id")
    allow = set(allowed)
    candidates = dirty_paths(repository)
    unapproved = [p for p in candidates if p not in allow]
    if unapproved:
        owners = attribute(repository, unapproved)
        lines = "\n".join(f"    {p!r}: {owners[p]}" for p in unapproved)
        raise RestoreSetRefusal(f"dirty paths are not on the approved restore allow-list:\n{lines}")
    states = {path: _observed_state(repository, path) for path in candidates}
    restored = []
    for path in candidates:
        raw = _git_stdout(repository, "ls-tree", "-z", pin, "--", path)
        records = [record for record in raw.split(b"\0") if record]
        if len(records) != 1:
            raise RestoreSetRefusal(f"restore path has no single pinned blob: {path!r}")
        header, found = records[0].split(b"\t", 1)
        mode, kind, oid = header.split(b" ")
        if found != os.fsencode(path) or kind != b"blob" or mode not in (b"100644", b"100755"):
            raise RestoreSetRefusal(f"unsupported pinned restore object: {path!r}")
        restored.append({"path": path, "blob_oid": oid.decode("ascii"), "observed_state": states[path]})
    if dirty_paths(repository) != candidates or any(
            _observed_state(repository, path) != states[path] for path in candidates):
        raise RestoreSetRefusal("observed state changed during authoring; no restore set was authored")
    return restored
