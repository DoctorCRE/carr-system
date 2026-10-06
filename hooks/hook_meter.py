#!/usr/bin/env python3
"""hook_meter.py — one structured line per hook firing, and the live/fixture split.

WHY THIS EXISTS. The 2026-08-23 process-audit council was asked where the line
is at which enforcement costs more than the defects it prevents, and could not
answer from the evidence this system had. out/hook-guard.log held 182,715 lines
over 9 days — 134,595 of them DENY — but every gate's selftest fires the same
gate with attack fixtures and writes to the SAME file, so the DENY count is an
upper bound on real friction mixed with an unknown volume of test traffic. Grok,
in the transcript: "That log is not a measurement." Codex: "Until [fixture and
operational logs are separated], the 134,595 DENY events are not an enforcement
metric." Both chairs marked measuring safe and marked the consolidation it would
justify (one dispatcher process per event instead of 13 interpreter launches per
Bash call) needs-design UNTIL this data exists. This module is that measurement.

WHAT IT DOES NOT DO, and this is the whole constraint: it never participates in
a decision. It records elapsed time, an outcome the caller already reached, and
which log a line belongs in. Every function here returns a value or swallows its
own exception; none of them can deny, allow, delay past a few hundred
microseconds, or raise into a gate's control flow. If this file were deleted
mid-run every gate would still reach the same verdict.

THE LIVE / FIXTURE / UNCLASSIFIED SPLIT, and why the default is not "live".
Attribution comes from the environment, because the environment is the only
thing that knows:

  CARR_HOOK_LIVE=1      set by hooks/hook-meter-run.py, which the harness — and
                        only the harness — invokes with a real event payload.
  CARR_HOOK_FIXTURE=1   set by a selftest, by ops/ci.sh's gates class, or by
                        anything else deliberately firing a gate at a fixture.
                        PYTEST_CURRENT_TEST counts as the same signal.
  neither               UNCLASSIFIED. Not "live", not "fixture" — a third
                        stream.

Defaulting an unmarked run to "live" is how the current log got into this state:
one unattributed source silently inflates the number the decision rests on.
Defaulting it to "fixture" would be the same mistake pointed the other way — it
would quietly DELETE operational evidence, which matters here because a gate
fired through some door that is not the wrapper (a stale settings.json, the
Codex adapter in ops/config/codex-hooks.json, a hand-run gate during an
incident) is exactly the case where somebody is reading the log for real. So
unmarked traffic goes to its own file and the rollup prints its volume. If that
number is large, the wiring is wrong and it says so instead of averaging in.

Fixtures: ops/hook-meter-selftest.py
"""

import os

# `os` and nothing else. This module is imported by 13 processes per Bash tool
# call and by 21 gates that import it purely to learn a path, so importing it
# costs a file read and no new module. `json` is deliberately absent from the
# top: it is imported inside emit(), which runs after a gate has finished and
# has usually imported json already, so the cost is paid by somebody else.
# Measured here with -X importtime, a top-level `import json` would have added
# ~4ms to every one of those processes to serialise one line at the end.

LIVE = "live"
FIXTURE = "fixture"
UNCLASSIFIED = "unclassified"

# Size cap per stream before rotation to `<name>.1`. A log nobody rotates is the
# 38MB file that started this; the size is read from the handle emit() already
# holds, so the check costs nothing.
ROTATE_BYTES = 16 * 1024 * 1024
INVOCATION_REPO_ENV = "CARR_HOOK_INVOCATION_REPO"


def _checkout(root):
    """Return (checkout, canonical, common-dir, linked) using Git's files only."""
    try:
        candidate = os.path.abspath(os.path.expanduser(str(root)))
        while True:
            marker = os.path.join(candidate, ".git")
            if os.path.isdir(marker):
                return candidate, candidate, os.path.realpath(marker), False
            if os.path.isfile(marker):
                line = open(marker, encoding="utf-8").read().strip()
                if not line.startswith("gitdir: "):
                    return None
                admin = os.path.realpath(os.path.join(candidate, line[8:].strip()))
                common_file = os.path.join(admin, "commondir")
                common = os.path.realpath(os.path.join(
                    admin, open(common_file, encoding="utf-8").read().strip()
                ))
                return candidate, os.path.dirname(common), common, True
            parent = os.path.dirname(candidate)
            if parent == candidate:
                return None
            candidate = parent
    except Exception:
        return None


def _registered_helper(path, canonical, common):
    """Resolve a Git-registered helper below canonical .claude/worktrees."""
    try:
        checkout = _checkout(path)
        if not checkout:
            return None
        root, owner, helper_common, linked = checkout
        helpers = os.path.realpath(os.path.join(canonical, ".claude", "worktrees"))
        if (not linked or os.path.realpath(owner) != os.path.realpath(canonical)
                or helper_common != os.path.realpath(common)
                or os.path.commonpath([os.path.realpath(root), helpers]) != helpers):
            return None
        marker = os.path.join(root, ".git")
        line = open(marker, encoding="utf-8").read().strip()
        admin = os.path.realpath(os.path.join(root, line[8:].strip()))
        registry = os.path.realpath(os.path.join(common, "worktrees"))
        if os.path.commonpath([admin, registry]) != registry:
            return None
        back = open(os.path.join(admin, "gitdir"), encoding="utf-8").read().strip()
        if os.path.realpath(back) != os.path.realpath(marker):
            return None
        return root
    except Exception:
        return None


def routing_repo(repo, invocation=None):
    """Select one evidence tree; ambiguity or invalid context means canonical."""
    base = _checkout(repo)
    if not base:
        return os.path.abspath(os.path.expanduser(str(repo)))
    _root, canonical, common, _linked = base
    requested = (os.environ.get(INVOCATION_REPO_ENV)
                 if invocation is None else invocation)
    if requested:
        return _registered_helper(requested, canonical, common) or canonical
    return _registered_helper(repo, canonical, common) or canonical


def _truthy(value):
    return str(value or "").strip().lower() not in ("", "0", "false", "no")


def source():
    """LIVE, FIXTURE or UNCLASSIFIED for THIS process. Never raises.

    A fixture marker beats the live marker, and the order is the point: a
    selftest that drives the wrapper end-to-end is still a selftest, and the one
    thing this split must never do is let test traffic back into the number.
    """
    try:
        if _truthy(os.environ.get("CARR_HOOK_FIXTURE")):
            return FIXTURE
        if os.environ.get("PYTEST_CURRENT_TEST"):
            return FIXTURE
        if _truthy(os.environ.get("CARR_HOOK_LIVE")):
            return LIVE
        return UNCLASSIFIED
    except Exception:
        return UNCLASSIFIED


def mark_live():
    """Declare this process a live firing, for the gate about to run in it.

    Only hooks/hook-meter-run.py calls this, and only the harness invokes that,
    so "live" means an event the harness delivered — not a gate somebody ran by
    hand. A fixture marker already in the environment is left to win.
    """
    try:
        if source() == FIXTURE:
            return FIXTURE
        os.environ["CARR_HOOK_LIVE"] = "1"
        return LIVE
    except Exception:
        return UNCLASSIFIED


def out_dir(repo):
    return os.path.join(routing_repo(repo), "out")


def guard_log_path(repo, src=None):
    """Where THIS process's human-readable gate log line belongs.

    Every gate that writes out/hook-guard.log routes its path through here, so
    the separation is one decision in one place rather than twenty copies that
    drift (rule a8c55a47 — a manual path and an automated path that do the same
    job must be the same code).

    CARR_HOOK_GUARD_LOG overrides the answer outright, which is how a selftest
    points a gate at a temp file without also having to reason about streams.
    """
    try:
        override = os.environ.get("CARR_HOOK_GUARD_LOG")
        if override:
            return override
        src = src or source()
        base = out_dir(repo)
        if src == LIVE:
            return os.path.join(base, "hook-guard.log")
        if src == FIXTURE:
            return os.path.join(base, "fixtures", "hook-guard-fixture.log")
        return os.path.join(base, "hook-guard-unclassified.log")
    except Exception:
        # A gate that cannot resolve its log path still has to reach a verdict.
        return os.path.join(repo, "out", "hook-guard.log")


def telemetry_path(repo, src=None):
    """The structured stream — one JSON object per hook firing."""
    try:
        override = os.environ.get("CARR_HOOK_TELEMETRY")
        if override:
            return override
        src = src or source()
        base = out_dir(repo)
        if src == LIVE:
            return os.path.join(base, "hook-telemetry.jsonl")
        if src == FIXTURE:
            return os.path.join(base, "fixtures", "hook-telemetry-fixture.jsonl")
        return os.path.join(base, "hook-telemetry-unclassified.jsonl")
    except Exception:
        return os.path.join(repo, "out", "hook-telemetry.jsonl")


def emit(repo, record, src=None):
    """Append one JSON line. Returns the path written, or None. Never raises.

    A single small write in append mode is what keeps thirteen concurrent hook
    processes from interleaving halves of two records.

    The hot path is one open, one write and one tell. Rotation reads the size
    from the file handle it already holds rather than stat-ing the path, and the
    directory is created only after an open actually fails — on all but the
    first firing after a fresh checkout, both of those cost nothing.
    """
    try:
        import json
        src = src or source()
        path = telemetry_path(repo, src)
        rec = dict(record)
        rec.setdefault("source", src)
        line = json.dumps(rec, separators=(",", ":"), default=str) + "\n"
        try:
            fh = open(path, "a", encoding="utf-8")
        except (FileNotFoundError, NotADirectoryError):
            os.makedirs(os.path.dirname(path), exist_ok=True)
            fh = open(path, "a", encoding="utf-8")
        with fh:
            fh.write(line)
            try:
                if fh.tell() >= ROTATE_BYTES:
                    rotate = True
                else:
                    rotate = False
            except Exception:
                rotate = False
        if rotate:
            try:
                os.replace(path, path + ".1")
            except Exception:
                pass
        return path
    except Exception:
        return None
