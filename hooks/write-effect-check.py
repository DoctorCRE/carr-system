#!/usr/bin/env python3
"""write-effect-check.py — what CHANGED, rather than what was intended.

WHY THIS EXISTS, and it is the lesson of 2026-08-14 rather than a new idea. Every
write control in this system parses INTENT: record-home-gate.py reads a tool's
file_path, bash-write-gate.py extracts targets out of a shell command. That is
the only thing a PreToolUse hook CAN do, and it cannot be complete against
arbitrary shell. On the day bash-write-gate shipped it produced two false
refusals of real work in one afternoon — an unexpanded `$D` judged as literal
text, and a path merely MENTIONED beside an unrelated write — and both came from
the gate answering a question it could not answer instead of declining.

This asks a question that has an answer. Not "what is this command going to
write" but "what actually changed on disk". That is COMPLETE with respect to how
a write happened: a heredoc, an interpreter nothing knows about, a script invoked
by name that writes on its own, a path assembled from variables at runtime — none
of them can hide from the filesystem. It closes the residual named in
bash-write-gate's docstring, from the other side.

HOW IT WORKS. PreToolUse on Bash stamps a marker with the time. PostToolUse
sweeps for watched files modified since that stamp and asks the EXISTING
record-home policy about each. No new judgement lives here, same as
bash-write-gate: one policy, more doors.

WHAT IT WATCHES, and this scoping is what makes it quiet enough to keep. Only
vault markdown the policy says NOTHING may write — 564 of 620 files at the time
of writing, against 44 generated renders and 12 machine-required exemptions.
RENDERS ARE DELIBERATELY EXCLUDED. The exporter writes them several times a day,
frequently from launchd while a session's Bash command is in flight, and effects
alone cannot distinguish the exporter's write from a session's. Watching only
files with no legitimate writer at all means no allowlist is needed and the
hourly refresh cannot generate a single false report. An allowlist of sanctioned
writer commands was the alternative, and it was rejected because it would have
smuggled intent-parsing back in through the door this check exists to avoid.

WHAT IT IS NOT, said plainly so nobody reads more into it. IT CANNOT PREVENT. The
write has already landed when this runs; the tool contract offers nothing else at
this layer. It reports. Prevention at this completeness needs enforcement in the
filesystem or in the record layer, which is a larger build and a separate
decision. What it closes is the SILENT failure — a write landing where no query
finds it and nobody ever learns it happened, which is the shape Joe called "the
worst mistake that can be made in this database system".

DIRECTION OF ERROR, chosen deliberately after today: this is a detector, so its
worst failure is noise, not blocked work. That is the right way round for a first
version and the exact opposite of what bash-write-gate got wrong twice.

COST: about 1ms on the pre pass (a timestamp) and about 8ms on the post pass (a
scandir sweep with cached stats over ~620 files). Measured before building,
because the alternative — stat()ing every file twice — was 80ms per Bash call and
would have been the wrong design for a reason nobody would have noticed later.

FAILS OPEN ON EVERYTHING. It never blocks and never errors a session out.
"""

import hashlib
import json
import os
import sys
import time

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
STATE = os.environ.get("CARR_WRITE_EFFECT_STATE") or os.path.join(
    REPO, "out", "write-effect")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:                                    # telemetry only — never load-bearing
    import hook_meter
    LOG = hook_meter.guard_log_path(REPO)
except Exception:                       # a missing meter must not change a verdict
    LOG = os.path.join(REPO, "out", "hook-guard.log")

# Archives, backups and derived trees. Not watched because nothing in them is a
# live record, and the generation archives in particular churn on every export.
SKIP_DIRS = {"_to_delete", "Backups", "Graph", "Graph-System", "node_modules",
             ".git"}
# MATCHED AS A SUFFIX, and the difference was worth 237 false reports. The archive
# directories are named after the file they shadow — `compiled-rules-shared.md.generations`,
# not `.generations` — so a set membership test skipped none of them. Measured
# against the real vault before this shipped: a 24-hour window reported 237 files,
# every one of them an archived generation the exporter had just written, and the
# hourly refresh would have produced five fresh noise lines every hour. A detector
# that cries on every export is one somebody mutes, and then it is not a detector.
SKIP_SUFFIXES = (".generations",)


def skip_dir(name):
    return (name in SKIP_DIRS or name.startswith(".")
            or name.endswith(SKIP_SUFFIXES))

_policy = None


def log(line):
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        with open(LOG, "a") as fh:
            fh.write(f"write-effect-check {line}\n")
    except Exception:
        pass


def policy():
    """The record-home policy, imported once. None if it cannot be loaded."""
    global _policy
    if _policy is None:
        try:
            import importlib.util
            spec = importlib.util.spec_from_file_location(
                "carr_record_home_effect",
                os.path.join(os.path.dirname(__file__), "record-home-gate.py"))
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            _policy = module
        except Exception as exc:
            log(f"policy unavailable: {exc}")
            _policy = False
    return _policy or None


def vault_root():
    """Where the policy believes the vault is — asked, never assumed."""
    module = policy()
    return getattr(module, "VAULT", "") if module else ""


def watched_root():
    return os.environ.get("CARR_WRITE_EFFECT_ROOT") or vault_root()


def changed_since(root, cutoff_ns):
    """Markdown under `root` modified after `cutoff_ns`.

    scandir with the entry's cached stat rather than a fresh os.stat per file:
    measured at ~8ms over 620 files against ~40ms for the naive version, and this
    runs on every Bash call.
    """
    hits, stack = [], [root]
    while stack:
        current = stack.pop()
        try:
            entries = list(os.scandir(current))
        except OSError:
            continue
        for entry in entries:
            try:
                if entry.is_dir(follow_symlinks=False):
                    if not skip_dir(entry.name):
                        stack.append(entry.path)
                elif entry.name.endswith(".md"):
                    if entry.stat(follow_symlinks=False).st_mtime_ns > cutoff_ns:
                        hits.append(entry.path)
            except OSError:
                continue
    return hits


def violations(paths):
    """Of `paths`, the ones NOTHING is allowed to write.

    A generated render is excluded even though the policy refuses hand-editing
    it: the exporter writes those legitimately and often, and an effect-based
    check cannot tell one writer from another. Watching only what has no
    legitimate writer is what keeps this quiet enough to be worth running.
    """
    module = policy()
    if not module:
        return []
    out = []
    for path in paths:
        try:
            verdict = module.check("Write", {"file_path": path})
        except Exception:
            continue
        if verdict and "generated" not in verdict.lower():
            out.append(path)
    return out


def marker_path(session_id):
    digest = hashlib.sha256((session_id or "nosession").encode()).hexdigest()[:16]
    return os.path.join(STATE, f"{digest}.json")


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from lib.hook_runtime import decision, run


def _decision_error(exc):
    log(f"ALLOW(internal-error) {exc}")
    return 0


@decision(on_error=_decision_error)
def decide(payload):
    if (payload.get("tool_name") or payload.get("toolName") or "") not in (
            "Bash", "functions.exec"):
        sys.exit(0)
    event = payload.get("hook_event_name") or payload.get("hookEventName") or ""
    session = payload.get("session_id") or payload.get("sessionId") or ""
    path = marker_path(session)

    if event == "PreToolUse":
        os.makedirs(STATE, exist_ok=True)
        with open(path, "w") as fh:
            json.dump({"at": time.time_ns()}, fh)
        sys.exit(0)

    if event != "PostToolUse":
        sys.exit(0)

    try:
        with open(path) as fh:
            cutoff = int(json.load(fh)["at"])
    except Exception:
        sys.exit(0)                     # no marker: silent, never an error
    try:
        os.remove(path)
    except OSError:
        pass

    root = watched_root()
    if not root or not os.path.isdir(root):
        sys.exit(0)

    hit = violations(changed_since(root, cutoff))
    if not hit:
        sys.exit(0)                     # silence when there is nothing to say

    listed = "\n".join(f"  · {p}" for p in hit[:10])
    more = f"\n  … and {len(hit) - 10} more" if len(hit) > 10 else ""
    log(f"CHANGED {len(hit)} watched file(s) :: {hit[:5]}")
    print(
        "WRITE EFFECT CHECK — a file nothing is allowed to write CHANGED "
        f"during that command:\n{listed}{more}\n"
        "This is measured from the filesystem, not guessed from the command, "
        "so it does not matter which tool or syntax carried the write.\n"
        "The write has already landed — this reports, it cannot undo. Put the "
        "content where it belongs through the record verbs (rule 76a53dfe) "
        "and leave the file as the record renders it.",
        file=sys.stderr)
    sys.exit(0)


def main():
    sys.exit(run(decide))


if __name__ == "__main__":
    sys.exit(main())
