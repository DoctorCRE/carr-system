#!/usr/bin/env python3
"""lint-gate.py — the PostToolUse writing-lint gate (idea-bank #32, job b).

WHY. `run.sh lint` is a gate in doctrine and a habit in practice: it only fires
when someone remembers it. `DNA/writing-rules.md` binds EVERY surface a prospect
could ever see, and the expensive failures are the ones that ship. This makes the
gate mechanical for the one moment that matters, the write itself.

WHAT IT DOES. After a Write or Edit lands on a plausibly client-facing vault file,
it runs the linter and puts the result back in front of the session. It NEVER
blocks: the linter's own doctrine is that HARD is blocked and REVIEW must be
cleared consciously, and "consciously" means a human or the session deciding, not
a regex. A clean lint run is also explicitly NOT the writing-audit; that judgment
pass still belongs to the audit skill, and the output says so.

SCOPE, deliberately narrow. Only the CARR vault, only surfaces a prospect could
see. Repo code, scratchpad files, generated renders and internal ledgers are
skipped, because a linter that fires on everything is a linter people learn to
ignore. Generated files are skipped for a second reason: they are never
hand-edited, so linting them would only ever report the exporter's output.

FAILS OPEN AND SILENT. Any error, missing linter, or timeout exits 0 with no
output. This is an advisory gate on top of an existing doctrine, and it must
never be the reason a write appears to fail.
"""

import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
# Derived from HOME, not typed in (2026-09-23 audit): the literal /Users/booko
# spelling was right on one machine and silently matched nothing on any other.
VAULT = os.path.join(os.path.expanduser("~"), "Library", "CloudStorage",
                     "GoogleDrive-joe.bookout.carr.us@gmail.com", "My Drive", "CARR AI")
CANONICAL = os.environ.get("CARR_ROOT") or os.path.join(os.path.expanduser("~"), "carr-system")
RUN_SH = os.path.join(CANONICAL, "run.sh")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lib.rule_delivery_preuse import (  # noqa:E402
    POSTWRITE_RECEIPT_SCHEMA, digest, postwrite_reviewer_digest, receipt_id,
    validate_postwrite_receipt,
)
from lib.rule_delivery_shadow import file_sha256  # noqa:E402
try:                                    # telemetry only — never load-bearing
    import hook_meter
    LOG = hook_meter.guard_log_path(os.path.expanduser("~/carr-system"))
except Exception:                       # a missing meter must not change a verdict
    LOG = os.path.expanduser("~/carr-system/out/hook-guard.log")
TIMEOUT = 25
PATCH_FILE = re.compile(r"^\*\*\* (?:Add|Update|Delete) File: (.+)$", re.M)
PATCH_MOVE = re.compile(r"^\*\*\* Move to: (.+)$", re.M)


def log(msg):
    """Added 2026-08-03 by the IT hook-coverage sweep, which found this was the
    ONLY hook of the five that wrote nothing, ever. That made the one gate
    enforcing writing-rules.md on client-facing surfaces unauditable: nobody
    could answer "has it ever fired", or "did it skip that draft or pass it".
    A check that cannot be seen is a defect even while the thing it watches is
    fine, and it is the same silent-success shape as the markdown-write defect
    this whole night was about."""
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        ts = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
        with open(LOG, "a") as fh:
            fh.write(f"{ts} lint-gate {msg.rstrip()}\n")
    except Exception:
        pass

# Generated renders — never hand-edited, so never linted here.
GENERATED = (
    "open-loops.md", "open-loops-backlog.md", "action-required.md", "team-loops.md",
    "compiled-rules-shared.md", "compiled-rules-joe.md", "compiled-rules-dell.md",
    "introduction-rules.md", "clients-active.md", "hunt-ledger.md",
    "deals-reciprocity.generated.md", "record-layer-dictionary.md",
)

# INTERNAL BY CONSTRUCTION — never linted, checked BEFORE the surface map.
#
# Added 2026-08-03. `("Deal Management", "proposal")` below is a broad fragment
# and it swallowed `DNA/Deal Management/record-layer/`, which is the ENGINEERING
# folder: work orders, design memos, onboarding runbooks. Those got the full
# prospect-facing ruleset, so a MARKETING rule enforcing solo-Joe framing fired
# on `dell-onboarding-runbook`, a document whose entire subject is Dell, and a
# style rule about colons fired on a spec. Six "hard ban" hits on a file no
# prospect will ever see.
#
# Rule ede4c735 is explicit that writing-rules binds PROSPECT-VISIBLE SURFACES
# ONLY, so this is not a relaxation — the gate was overreaching its own charter.
# The cost of overreach is not noise, it is that a human learns the alarm is
# usually wrong and starts clicking past it, and then it catches nothing on the
# day it is right. That is the same failure the façade check (rule 28) names for
# health checks reporting everything at one severity.
#
# Deliberately narrow: only folders that are internal by their nature. Anything
# that could plausibly reach a prospect keeps its surface, because a false
# NEGATIVE here is far worse than a false positive.
INTERNAL = (
    "/record-layer/",      # work orders, design memos, specs, runbooks
    "/dna/team/",          # protocol, twin-system playbook, the starter kit
    "/00_context/",        # operating notes, decision history, loops, handoffs
    "/automation/",        # scripts, job docs
    "/archive/",           # snapshots and retired material
    "/idea-inbox/",        # raw capture, never client-facing
    "/_to_delete/",        # staging for deletion
    "/_asset_staging/",    # raw intake
)

# Path fragment -> linter surface. First match wins, most specific first.
SURFACES = (
    ("Marketing/Social Media", "social"),
    ("DNA/Marketing", "social"),
    ("/Marketing/", "social"),
    ("Outreach/", "email"),
    ("templates.md", "email"),
    ("intake/", "proposal"),
    ("Output/", "proposal"),
    ("benefit-summary", "proposal"),
    ("proposals", "proposal"),
    ("Deal Management", "proposal"),
    ("GBP", "web"),
    ("SEO", "web"),
    ("landing", "web"),
)


def surface_for(path):
    low = path.lower()
    if "scratchpad" in low or "/out/" in low or ".generations" in low:
        return None
    if os.path.basename(path) in GENERATED:
        return None
    if not path.startswith(VAULT):
        return None
    if not path.endswith((".md", ".txt", ".html")):
        return None
    if any(frag in low for frag in INTERNAL):
        return None
    for frag, surf in SURFACES:
        if frag.lower() in low:
            return surf
    return None


CODE_SUFFIXES = (
    ".py", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".sql", ".sh",
    ".go", ".rs", ".java", ".c", ".h", ".cpp", ".rb",
)
REVIEW_AT = 0.85


def _changed_code_paths(payload):
    """Return exact paths named by one native edit or patch operation.

    Codex can call apply_patch inside functions.exec. In that case the outer
    tool is the hook event, so inspect its script for patch file headers too.
    Only explicit patch targets qualify; arbitrary shell commands do not.
    """
    tool = payload.get("tool_name") or payload.get("toolName") or ""
    ti = payload.get("tool_input") or payload.get("toolInput") or {}
    if tool in ("Write", "Edit", "MultiEdit"):
        path = ti.get("file_path") or ti.get("filePath") or ""
        return [path] if path else []
    if tool not in ("apply_patch", "functions.apply_patch", "functions.exec"):
        return []
    if isinstance(ti, str):
        patch = ti
    elif isinstance(ti, dict):
        patch = (ti.get("code") if tool == "functions.exec" else None) or \
            ti.get("command") or ti.get("patch") or ti.get("input") or ""
    else:
        patch = ""
    if not isinstance(patch, str):
        return []
    if tool == "functions.exec":
        if "tools.apply_patch" not in patch:
            return []
        # A JS string often contains escaped line breaks instead of literal
        # newlines. Normalize only for locating patch headers, never execute it.
        patch = patch.replace("\\n", "\n")
    cwd = payload.get("cwd") or os.getcwd()
    found = PATCH_FILE.findall(patch) + PATCH_MOVE.findall(patch)
    paths = []
    for raw in found:
        path = raw.strip()
        if not os.path.isabs(path):
            path = os.path.join(cwd, path)
        path = os.path.normpath(path)
        if path not in paths:
            paths.append(path)
    return paths


def _review_context(payload, body):
    client = "codex" if isinstance(payload.get("turn_id"), str) \
        and payload["turn_id"].strip() else "claude"
    receipt = {
        "schema": POSTWRITE_RECEIPT_SCHEMA,
        "client": client,
        "session_id": payload.get("session_id"),
        "turn_id": payload.get("turn_id") if client == "codex" else None,
        "tool_use_id": payload.get("tool_use_id"),
        "tool_name": payload.get("tool_name") or payload.get("toolName"),
        "tool_input_sha256": digest(
            payload.get("tool_input") or payload.get("toolInput") or {}),
        "configuration_digest": digest({
            relative: file_sha256(REPO / relative)
            for relative in ("ops/config/hooks.json", "ops/config/codex-hooks.json")
        }),
        "reviewer_digest": postwrite_reviewer_digest(REPO),
        "status": body["status"],
        "paths": body.get("paths", []),
        "findings": body.get("findings", []),
        "models": body.get("models", []),
        "reason": body.get("reason"),
        "instruction": body.get("instruction"),
    }
    receipt["receipt_id"] = receipt_id(receipt)
    if not validate_postwrite_receipt(receipt, repo=REPO):
        raise RuntimeError("post-write receipt failed local validation")
    return json.dumps({"hookSpecificOutput": {
        "hookEventName": "PostToolUse",
        "additionalContext": json.dumps(receipt, sort_keys=True,
                                        separators=(",", ":")),
    }})


def code_review(payload):
    """Judge the lines just written, at the moment they are written.

    JOE, 2026-09-18: "make sure you are using jev to assist you during all
    these activities. if its not automatically doing that at this point the
    first thing you need to do is fix it so that jev is automatically involved
    without you having to remember or ask."

    That is the gap this closes. Three judgments already fire on their own --
    before a shell command, before a defect is filed, before a push -- and none
    of them looks at code while it is being written. ops/jev_code_review.py
    existed all day and ran exactly twice, both times because a session
    remembered it. A capability that depends on being remembered is the failure
    this whole session has been about.

    SCOPED TO THE DIFF, never the file. A changed hunk plus context is a few
    hundred tokens; a file is thousands of tokens of code nobody touched, and
    accuracy falls as a state fills with detail unrelated to the decision.

    ADVISORY AND POST-WRITE. It runs AFTER the edit has already landed, so it
    cannot block, cannot refuse, and cannot lose work. It returns on every
    failure -- no credential, no network, no git, bad payload -- and a session
    editing while the judgment is down edits exactly as it does today.

    CARR_JEV_WORKER=off suppresses this automatic review in unattended Codex
    workers. Deterministic writing lint below still runs; an explicit review
    requested by a brief uses the shared client directly and retains its cap.
    """
    if os.environ.get("CARR_JEV_WORKER") == "off":
        return
    tool = payload.get("tool_name") or payload.get("toolName") or ""
    ti = payload.get("tool_input") or payload.get("toolInput") or {}
    paths = _changed_code_paths(payload)
    if not paths:
        return
    receipt = {"status": "reviewed", "paths": [], "findings": [], "models": [],
               "reason": None, "instruction": None}
    if not any(path.endswith(CODE_SUFFIXES) for path in paths):
        receipt["status"] = "skipped"
        receipt["reason"] = "no_supported_code_paths"
        receipt["paths"] = [
            {"path": os.path.basename(path), "status": "not_reviewed",
             "reason": "unsupported_extension"} for path in paths]
        print(_review_context(payload, receipt))
        return
    try:
        import importlib.util
        root = subprocess.run(["git", "rev-parse", "--show-toplevel"],
                              capture_output=True, text=True,
                              cwd=os.path.dirname(paths[0]) or ".",
                              timeout=15).stdout.strip()
        if not root:
            # A path outside any git repo (a scratchpad write) has no diff to
            # review. It stays "unavailable" -- supported code went unreviewed,
            # and #1123 made these receipts truthful so no review is ever
            # claimed for it -- but it now says WHY. It used to be raised as a
            # bare RuntimeError and reported by class name only, so every
            # scratchpad write read "unavailable: RuntimeError", which looked
            # like an outage (2026-09-24 audit).
            log(f"UNAVAILABLE outside_repo paths={[os.path.basename(p) for p in paths]}")
            print(_review_context(payload, {
                "status": "unavailable",
                "reason": "outside_repo",
                "instruction": "The edit is saved outside any git repository, so there is no diff "
                               "to review and no Jev review may be claimed for it.",
            }))
            return
        # This hook is installed from CARR for every code home. The edited
        # repository supplies the diff; CARR supplies the Jev reviewer.
        spec = importlib.util.spec_from_file_location(
            "jev_code_review", REPO / "ops" / "jev_code_review.py")
        if spec is None or spec.loader is None:
            raise RuntimeError("review_module_unavailable")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        hits, acted = [], []
        for path in paths:
            rel = os.path.relpath(os.path.realpath(path), os.path.realpath(root))
            path_receipt = {"path": rel}
            receipt["paths"].append(path_receipt)
            if not path.endswith(CODE_SUFFIXES):
                path_receipt.update(status="not_reviewed", reason="unsupported_extension")
                continue
            # The hunk just written, with enough around it to be judged.
            diff = subprocess.run(["git", "diff", "-U12", "--", path],
                                  capture_output=True, text=True, cwd=root,
                                  timeout=20).stdout
            if not diff.strip():
                diff = subprocess.run(
                    ["git", "diff", "--cached", "-U12", "--", path],
                    capture_output=True, text=True, cwd=root, timeout=20).stdout
            added = [line[1:] for line in diff.splitlines()
                     if line.startswith("+") and not line.startswith("+++")]
            if not added and tool == "Write" and isinstance(ti, dict):
                content = ti.get("content")
                if isinstance(content, str):
                    added = content.splitlines()
            if not added and os.path.isfile(path):
                tracked = subprocess.run(
                    ["git", "ls-files", "--error-unmatch", "--", path],
                    capture_output=True, text=True, cwd=root, timeout=20)
                if tracked.returncode != 0:
                    with open(path, encoding="utf-8", errors="replace") as handle:
                        added = handle.read().splitlines()
            if not added:
                path_receipt.update(status="not_reviewed", reason="no_git_diff")
                continue
            if len(added) > 400:
                path_receipt.update(status="not_reviewed", reason="diff_over_400_added_lines")
                continue
            code = "\n".join(added)[:2600]
            candidate_kinds = [kind for kind, pattern in module.SIGNATURES
                               if pattern.search(code)]
            if not candidate_kinds:
                path_receipt.update(status="clear", reason="no_ambiguous_candidate")
                continue
            region = {"path": rel, "line": 0,
                      "kind": "just written by this session",
                      "code": code}
            # Semantic findings are review advice pending labeled calibration.
            scores = module.review_for_edit(region, payload)
            model = scores.get("_model")
            if not isinstance(model, str) or not model.strip():
                raise RuntimeError("missing_model_readback")
            if model not in receipt["models"]:
                receipt["models"].append(model)
            path_receipt.update(status="jev_reviewed", candidates=candidate_kinds)
            for name, value in scores.items():
                if not name.startswith("_") and value >= REVIEW_AT:
                    hits.append((rel, name, value))
            if scores.get("_review_task_fit"):
                acted.append({"path": rel, "question": "task_fit_mismatch",
                              "probability": scores["_review_task_fit"], "effect": "advisory"})
        hits.sort(key=lambda item: -item[2])
        for rel, name, value in hits:
            receipt["findings"].append({
                "path": rel, "question": name, "probability": value,
                "effect": "advisory",
            })
        receipt["findings"].extend(acted)
        if not receipt["models"]:
            receipt["status"] = "skipped"
            receipt["reason"] = "no_jev_candidate"
        print(_review_context(payload, receipt))
    except Exception as exc:
        # The class name alone ("RuntimeError") told nobody what happened; the
        # audit log and everyone reading this receipt need the message too.
        detail = str(exc).strip()
        reason = f"{type(exc).__name__}: {detail}" if detail else type(exc).__name__
        log(f"UNAVAILABLE {reason}")
        print(_review_context(payload, {
            "status": "unavailable",
            "reason": reason,
            "instruction": "The edit is saved, but no Jev review may be claimed for it.",
        }))


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception:
        sys.exit(0)

    # The receipt half fails open too (ops/lint-gate-selftest.py): a payload
    # with no session_id, or a tool_input that is not a dict, used to raise
    # out of here with a traceback and exit 1 -- before the lint ever ran.
    try:
        code_review(payload)
    except Exception as exc:
        log(f"RECEIPT(internal-error) {type(exc).__name__}: {exc}")

    try:
        tool = payload.get("tool_name") or payload.get("toolName") or ""
        if tool not in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
            sys.exit(0)
        ti = payload.get("tool_input") or payload.get("toolInput") or {}
        path = ti.get("file_path") or ti.get("filePath") or ""
        if not path:
            sys.exit(0)

        surface = surface_for(path)
        if not surface:
            sys.exit(0)
        if not (os.path.exists(RUN_SH) and os.path.exists(path)):
            sys.exit(0)

        res = subprocess.run(
            [RUN_SH, "lint", path, "--surface", surface],
            capture_output=True, text=True, timeout=TIMEOUT,
            cwd=CANONICAL,
        )
        out = (res.stdout or "") + (res.stderr or "")
        if not out.strip():
            sys.exit(0)

        tail = "\n".join(out.strip().splitlines()[-25:])
        rel = path[len(VAULT):].lstrip("/")
        if "FAIL" in out or "hard-ban" in out:
            msg = (f"WRITING-LINT: HARD BAN HIT on {rel} (surface: {surface}). "
                   f"writing-rules.md says do not ship until these are zero. Fix before this "
                   f"reaches Joe or a prospect.\n\n{tail}")
        elif "REVIEW" in out:
            msg = (f"WRITING-LINT: REVIEW items on {rel} (surface: {surface}). No hard bans. "
                   f"Clear each one consciously or fix it; do not ignore silently. A clean lint "
                   f"run is not the writing-audit.\n\n{tail}")
        else:
            sys.exit(0)

        # PostToolUse reaches the session ONLY through structured JSON. Plain text
        # on stdout is not injected into context, so the earlier draft of this hook
        # would have run the linter and thrown the result away. additionalContext
        # arrives as a system reminder the session reads.
        log(f"REPORT {msg.splitlines()[0][:180] if msg else '(empty)'}")
        print(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "PostToolUse",
                "additionalContext": msg,
            }
        }))
        sys.exit(0)
    except Exception as exc:
        # fails open and silent to the session, per the docstring — but NOT
        # silent to the log, which is the whole point of adding one.
        log(f"ALLOW(internal-error) {type(exc).__name__}: {exc}")
        sys.exit(0)


if __name__ == "__main__":
    main()
