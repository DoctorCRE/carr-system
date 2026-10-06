"""Machine-local launchd holds and repo definitions that must stay off.

~/.config/carr/launchd-hold contains one `label reason` per line. Blank lines
and # comments are ignored. `label @2026-10-05T19:31:11Z reason` pins an
individual hold's start; health persists first-seen age for undated labels.
Removing a hold permits the next installer run to activate the job again.
"""
from __future__ import annotations

import os
import re
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

LABEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\Z")
# Source definitions that have not passed their activation cutover.
DEFINITION_ONLY: dict[str, str] = {
    "com.carr.repo-hygiene-janitor.plist":
        "the repo-hygiene janitor plans branch, worktree and cache cleanup; its "
        "gate is a separately reviewed live-effect packet, so the definition is "
        "written down and left uninstalled until that packet is approved",
}


@dataclass(frozen=True)
class Hold:
    label: str
    reason: str
    since: float
    age_source: str

    def describe(self, now=None):
        days = max(0, (time.time() if now is None else now) - self.since) / 86400
        return f"HELD {self.label}: {self.reason} · age {days:.1f}d ({self.age_source})"


def read_holds(home=None):
    path = (Path(home) if home is not None else Path.home()) / ".config/carr/launchd-hold"
    try:
        with path.open(encoding="utf-8") as source:
            mtime = os.fstat(source.fileno()).st_mtime
            text = source.read()
    except FileNotFoundError:
        return {}
    holds = {}
    for number, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        fields = line.split(None, 1)
        label, reason = fields[0], fields[1] if len(fields) == 2 else ""
        since, age_source = mtime, "hold file age"
        if reason.startswith("@"):
            parts = reason.split(None, 1)
            timestamp, reason = parts[0], parts[1] if len(parts) == 2 else ""
            try:
                at = datetime.fromisoformat(timestamp[1:].replace("Z", "+00:00"))
                if at.tzinfo is None or at.timestamp() > time.time():
                    raise ValueError("timestamp must have a timezone and must not be in the future")
                since, age_source = at.timestamp(), "hold start"
            except ValueError as exc:
                raise ValueError(f"{path}:{number}: invalid hold timestamp") from exc
        if not LABEL.fullmatch(label) or not reason.strip() or label in holds:
            raise ValueError(f"{path}:{number}: expected one unique label and a reason")
        holds[label] = Hold(label, reason.strip(), since, age_source)
    return holds


def off_reason(label, home=None, holds=None):
    holds = read_holds(home) if holds is None else holds
    hold = holds.get(label)
    if hold:
        return hold.describe()
    reason = DEFINITION_ONLY.get(label + ".plist")
    return f"DEFINITION ONLY {label}: {reason}" if reason else None


def reconcile_off(home, apply, launchctl="launchctl", domain=None, definition_labels=()):
    """One reconciliation pass, including held labels with no tracked plist."""
    holds = read_holds(home)
    labels = set(holds) | set(definition_labels)
    for label in sorted(labels):
        print(f"  {off_reason(label, holds=holds)}")
        if apply:
            ensure_off(label, launchctl, domain)
    return labels


def activate(label, argv, *, home=None, launchctl="launchctl", domain=None):
    """The sole guard before load, bootstrap, or kickstart."""
    reason = off_reason(label, home)
    if reason:
        print(f"  {reason}")
        ensure_off(label, launchctl, domain)
        result = subprocess.CompletedProcess(argv, 0, "", "")
        result.held = True
        return result
    result = subprocess.run(argv, capture_output=True, text=True, check=False)
    result.held = False
    return result


def ensure_off(label, launchctl="launchctl", domain=None):
    """Disable, boot out if loaded, and verify both launchd states."""
    if not LABEL.fullmatch(label):
        raise ValueError("invalid launchd label")
    domain = domain or f"gui/{os.getuid()}"
    target = f"{domain}/{label}"
    def run(*args):
        return subprocess.run([launchctl, *args], capture_output=True, text=True,
                              check=False, timeout=15)
    disabled = run("disable", target)
    if disabled.returncode:
        raise RuntimeError(f"disable failed for {label}: {disabled.stderr.strip()[:120]}")
    loaded = run("print", target)
    if loaded.returncode == 0:
        out = run("bootout", target)
        if out.returncode:
            raise RuntimeError(f"bootout failed for held {label}: {out.stderr.strip()[:120]}")
        loaded = run("print", target)
    detail = (loaded.stderr or "") + (loaded.stdout or "")
    if loaded.returncode != 113 or f'Could not find service "{label}"' not in detail:
        raise RuntimeError(f"held {label} is not verified unloaded")
    state = run("print-disabled", domain)
    key = re.escape(label)
    overrides = re.findall(r'(?m)^\s*(?:"' + key + r'"|' + key + r')\s*=>\s*([A-Za-z]+)\b', state.stdout)
    if state.returncode or len(overrides) != 1 or overrides[0].lower() not in {"true", "disabled"}:
        raise RuntimeError(f"held {label} is not verified disabled")


if __name__ == "__main__":
    import sys
    try:
        label = sys.argv[1]
        reason = off_reason(label)
        if reason is None:
            raise SystemExit(3)
        print(reason)
        ensure_off(label, sys.argv[2], sys.argv[3])
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        print(f"launchd hold: REFUSED {exc}", file=sys.stderr)
        raise SystemExit(1)
