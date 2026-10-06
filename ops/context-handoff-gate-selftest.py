#!/usr/bin/env python3
"""Executable acceptance for session_context_lifecycle_gate_v2."""

from __future__ import annotations

import datetime as dt
import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
from pathlib import Path
from types import SimpleNamespace

REPO = Path(__file__).resolve().parent.parent
HOOK = REPO / "hooks/context-handoff-gate.py"
RUNNER = REPO / "hooks/hook-meter-run.py"
MANIFEST = REPO / "ops/config/session-context-lifecycle.v2.json"
APPROVED_BASE = "01c3977580e8d9d490380f6c2135d1c4d7d20fd7"
PASS: list[str] = []
FAIL: list[str] = []


def check(name, condition, detail=""):
    (PASS if condition else FAIL).append(name)
    if not condition:
        print(f"  FAIL {name}: {detail}")


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode()


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def ownership_digest(owner):
    return digest({key: owner.get(key) for key in (
        "id", "surface", "generation", "state", "evidence_digest",
        "activation_init_digest", "activation_final_digest",
        "terminal_evidence_digest", "terminal_provenance_digest",
    )})


def write_jsonl(path: Path, rows):
    path.write_text("".join(json.dumps(row) + "\n" for row in rows),
                    encoding="utf-8")
    return path


def usage_row(total, *, model=None, window=None):
    usage = {
        "input_tokens": 2,
        "cache_creation_input_tokens": 98,
        "cache_read_input_tokens": max(0, total - 200),
        "output_tokens": 100,
    }
    row = {"type": "assistant", "message": {"usage": usage}}
    if model:
        row["message"]["model"] = model
    if window is not None:
        row["model_context_window"] = window
    return row


def base_env(root: Path, **extra):
    env = dict(os.environ)
    env.update({
        "CARR_SESSION_CONTEXT_STATE_DIR": str(root / "state"),
        "CARR_SESSION_CONTEXT_MANIFEST": str(root / "manifest.json"),
        "CARR_CONTEXT_AUDIT": str(root / "audit.jsonl"),
        "CARR_HOOK_TELEMETRY": str(root / "telemetry.jsonl"),
        "CARR_HOOK_FIXTURE": "1",
    })
    env.pop("CARR_CONTEXT_WINDOW", None)
    env.update({key: str(value) for key, value in extra.items()})
    return env


def run_hook(root: Path, event: str, transcript: Path | None, *,
             session="selftest", payload_extra=None, env_extra=None, wrapped=False):
    payload = {
        "hook_event_name": event,
        "session_id": session,
        "prompt_id": f"prompt-{session}",
        "tool_use_id": f"tool-{session}",
    }
    if transcript:
        payload["transcript_path"] = str(transcript)
    if payload_extra:
        payload.update(payload_extra)
    env = base_env(root, **(env_extra or {}))
    command = [sys.executable, str(HOOK)]
    if wrapped:
        command = [sys.executable, str(RUNNER), str(HOOK)]
    proc = subprocess.run(command, input=json.dumps(payload), text=True,
                          capture_output=True, env=env, cwd=REPO)
    parsed = None
    if proc.stdout.strip():
        try:
            parsed = json.loads(proc.stdout)
        except Exception:
            parsed = {"unparseable": proc.stdout}
    audit_path = root / "audit.jsonl"
    if isinstance(parsed, dict) and audit_path.exists():
        rows = [json.loads(line) for line in audit_path.read_text().splitlines() if line.strip()]
        matching = [row for row in rows if row.get("session") == session and row.get("event") == event]
        if matching:
            parsed["_audit"] = matching[-1]
    return proc, parsed


def run_cli(root: Path, *args):
    proc = subprocess.run([sys.executable, str(HOOK), *map(str, args)],
                          text=True, capture_output=True, env=base_env(root), cwd=REPO)
    parsed = None
    if proc.stdout.strip():
        try:
            parsed = json.loads(proc.stdout)
        except Exception:
            parsed = {"unparseable": proc.stdout}
    return proc, parsed


def reason(parsed):
    if not isinstance(parsed, dict):
        return None
    audit = parsed.get("_audit")
    if isinstance(audit, dict) and audit.get("action") == "ANNOUNCE":
        signal = audit.get("signal") if isinstance(audit.get("signal"), dict) else {}
        reason_code = audit.get("reason") or signal.get("reason")
        return {"reason": reason_code, "signal": {"window_tier": "control_error", **signal,
            "signal_reason": signal.get("signal_reason") or reason_code}}
    context = parsed.get("hookSpecificOutput", {}).get("additionalContext")
    if isinstance(context, str):
        warning = re.search(r"warning \(([A-Z_]+)\)", context)
        if warning:
            return {"reason": warning.group(1), "signal": {
                "signal_reason": warning.group(1), "window_tier": "control_error"}}
        if "headroom notice" in context:
            return {"reason": "CONTEXT_HANDOFF_REQUIRED", "signal": {
                "signal_reason": "CONTEXT_HANDOFF_REQUIRED"}}
    if parsed.get("decision") != "block":
        return None
    try:
        return json.loads(parsed["reason"])
    except Exception:
        return None


def fresh_root(prefix="context-lifecycle-"):
    root = Path(tempfile.mkdtemp(prefix=prefix))
    shutil.copy2(MANIFEST, root / "manifest.json")
    return root


def threshold_and_window_cases():
    print("thresholds, density, window tiers")
    root = fresh_root()
    try:
        t = write_jsonl(root / "normal-low.jsonl",
                        [usage_row(549_999, model="claude-opus-4-1")])
        proc, out = run_hook(root, "Stop", t, session="normal-low")
        check("normal 54.9999 percent allows", proc.returncode == 0 and out is None,
              (proc.returncode, out))

        # Sonnet 5.5 (claude-sonnet-5-5) has a 1M window. An unbound id falls
        # through to the transcript/compact tiers and the gate cannot count it.
        for model in ("claude-sonnet-5-5", "claude-sonnet-5"):
            slug = model.replace("claude-", "")
            t = write_jsonl(root / f"{slug}-low.jsonl",
                            [usage_row(549_999, model=model)])
            proc, out = run_hook(root, "Stop", t, session=f"{slug}-low")
            check(f"{model} 54.9999 percent of a 1M window allows",
                  proc.returncode == 0 and out is None, (proc.returncode, out))
            t = write_jsonl(root / f"{slug}-edge.jsonl",
                            [usage_row(550_000, model=model)])
            proc, out = run_hook(root, "Stop", t, session=f"{slug}-edge")
            why = reason(out)
            check(f"{model} resolves a 1M window by model and refuses at 55 percent",
                  why and why["signal"]["window"] == 1_000_000
                  and why["signal"]["window_tier"] == "model"
                  and why["signal"]["threshold"] == 55, out)

        t = write_jsonl(root / "normal-edge.jsonl",
                        [usage_row(550_000, model="claude-opus-4-1")])
        proc, out = run_hook(root, "Stop", t, session="normal-edge")
        why = reason(out)
        check("normal 55 percent refuses", why and why["signal"]["threshold"] == 55, out)

        t = write_jsonl(root / "dense-low.jsonl",
                        [usage_row(499_999, model="claude-opus-4-1")])
        proc, out = run_hook(root, "Stop", t, session="dense-low",
                             payload_extra={"risk": "R3"})
        check("dense below 50 percent allows", out is None, out)
        t = write_jsonl(root / "dense-edge.jsonl",
                        [usage_row(500_000, model="claude-opus-4-1")])
        proc, out = run_hook(root, "Stop", t, session="dense-edge",
                             payload_extra={"risk": "R3"})
        why = reason(out)
        check("R3 makes 50 percent the soft threshold",
              why and why["signal"]["dense"] is True
              and why["signal"]["threshold"] == 50, out)

        t = write_jsonl(root / "hard.jsonl",
                        [usage_row(700_000, model="claude-opus-4-1")])
        proc, out = run_hook(root, "Stop", t, session="hard")
        check("70 percent hard line refuses",
              reason(out) and reason(out)["signal"]["threshold"] == 70, out)

        # Operator/test override wins; a lower conflicting transcript tier is
        # deliberately not compared to the chosen higher tier.
        t = write_jsonl(root / "override.jsonl",
                        [usage_row(300_000, model="claude-opus-4-1", window=200_000)])
        proc, out = run_hook(root, "Stop", t, session="override",
                             env_extra={"CARR_CONTEXT_WINDOW": "500000"})
        why = reason(out)
        check("positive override is highest tier",
              why and why["signal"]["window"] == 500_000
              and why["signal"]["window_tier"] == "override", out)
        high = write_jsonl(root / "bad-override-high.jsonl",
                           [usage_row(900_000, model="claude-opus-4-1")])
        proc, out = run_hook(root, "Stop", high, session="bad-override",
                             env_extra={"CARR_CONTEXT_WINDOW": "not-positive"})
        why = reason(out)
        check("invalid override fails closed at high usage",
              proc.returncode == 0 and why
              and why["reason"] == "WINDOW_CONFIG_INVALID"
              and why["signal"]["signal_reason"] == "WINDOW_CONFIG_INVALID",
              (proc.returncode, out, proc.stderr))

        proc, out = run_hook(
            root, "Stop", t, session="missing-manifest",
            env_extra={"CARR_SESSION_CONTEXT_MANIFEST": root / "missing.json"})
        why = reason(out)
        check("Stop fails closed when the manifest is unavailable",
              why and why["reason"] == "WINDOW_CONFIG_INVALID", out)

        corrupt_state = root / "corrupt-state.json"
        corrupt_state.write_text("{broken", encoding="utf-8")
        proc, out = run_hook(
            root, "Stop", t, session="corrupt-state",
            env_extra={"CARR_CONTEXT_STATE": corrupt_state})
        why = reason(out)
        check("Stop fails closed when lifecycle state is corrupt",
              why and why["reason"] == "LIFECYCLE_INVALID", out)

        proc, out = run_hook(root, "Stop", None, session="missing-transcript")
        check("fresh Stop without a live transcript fails closed",
              proc.returncode == 0 and reason(out)
              and reason(out)["reason"] == "LIFECYCLE_INVALID",
              (proc.returncode, out, proc.stderr))
        proc, out = run_hook(
            root, "Stop", root / "does-not-exist.jsonl",
            session="unreadable-transcript")
        check("fresh Stop with an unreadable transcript fails closed",
              proc.returncode == 0 and reason(out)
              and reason(out)["reason"] == "LIFECYCLE_INVALID",
              (proc.returncode, out, proc.stderr))
        active_transcript = write_jsonl(
            root / "active-transcript.jsonl", [usage_row(1_000)])
        run_hook(root, "PostToolUse", active_transcript,
                 session="active-transcript")
        proc, out = run_hook(root, "Stop", None,
                             session="active-transcript")
        check("nonterminal callback cannot drop its live transcript",
              proc.returncode == 0 and reason(out)
              and reason(out)["reason"] == "LIFECYCLE_INVALID",
              (proc.returncode, out, proc.stderr))

        legacy_payload = json.dumps({
            "hook_event_name": "PostToolUse", "session_id": "legacy-repeat",
            "prompt_id": "legacy-repeat-callback",
            "transcript_path": str(active_transcript),
        })
        legacy_results = []
        for name in ("legacy-a.json", "legacy-b.json"):
            env = base_env(root, CARR_CONTEXT_HOOK_EVENT="PostToolUse",
                           CARR_CONTEXT_STATE=root / name)
            env.pop("CARR_SESSION_CONTEXT_STATE_DIR", None)
            legacy_results.append(subprocess.run(
                [sys.executable, str(HOOK)], input=legacy_payload, text=True,
                capture_output=True, env=env, cwd=REPO))
        check("legacy state overrides isolate immutable identity registries",
              all(proc.returncode == 0 for proc in legacy_results)
              and all((root / name).exists()
                      for name in ("legacy-a.json", "legacy-b.json")),
              [(proc.returncode, proc.stdout, proc.stderr)
               for proc in legacy_results])

        # Exact known model binding wins over lower explicit windows.
        t = write_jsonl(root / "model.jsonl",
                        [usage_row(600_000, model="claude-opus-4-1",
                                   window=2_000_000)])
        proc, out = run_hook(root, "Stop", t, session="model")
        why = reason(out)
        check("exact model binding is the second tier",
              why and why["signal"]["window"] == 1_000_000
              and why["signal"]["window_tier"] == "model", out)

        # A transcript retains older model declarations after a legitimate
        # switch. Only the newest authoritative row describes the live context
        # window; treating all history as simultaneous makes a 75% current
        # context ambiguous and allows it past the hard line.
        t = write_jsonl(root / "mixed-model-history.jsonl", [
            usage_row(40_000, model="claude-opus-4-1"),
            usage_row(150_000, model="claude-haiku-4-5-20251001"),
        ])
        proc, out = run_hook(root, "Stop", t, session="mixed-model-history")
        why = reason(out)
        check("latest model switch remains subject to the hard threshold",
              proc.returncode == 0 and why
              and why["signal"]["window"] == 200_000
              and why["signal"]["window_tier"] == "model"
              and why["signal"]["ratio"] == 75.0
              and why["signal"]["threshold"] == 70,
              (proc.returncode, out, proc.stderr))

        # A model requested inside an ordinary tool payload describes the child
        # invocation, not the session whose context window this gate measures.
        # Only authoritative transcript envelopes may select the window tier.
        t = write_jsonl(root / "nested-tool-model.jsonl", [
            usage_row(600_000, model="claude-opus-4-1"),
            {"type": "assistant", "message": {"content": [{
                "type": "tool_use", "name": "spawn_agent",
                "input": {"model": "gpt-5.6-sol"},
            }]}},
        ])
        proc, out = run_hook(root, "Stop", t, session="nested-tool-model")
        why = reason(out)
        check("nested tool model cannot make the session window ambiguous",
              proc.returncode == 0 and why
              and why["signal"]["window"] == 1_000_000
              and why["signal"]["window_tier"] == "model",
              (proc.returncode, out, proc.stderr))

        t = write_jsonl(root / "nested-tool-window.jsonl", [
            usage_row(110_000, window=200_000),
            {"type": "assistant", "message": {"content": [{
                "type": "tool_use", "name": "spawn_agent",
                "input": {"context_window": 300_000},
            }]}},
        ])
        proc, out = run_hook(root, "Stop", t, session="nested-tool-window")
        why = reason(out)
        check("nested tool window cannot make the session window ambiguous",
              proc.returncode == 0 and why
              and why["signal"]["window"] == 200_000
              and why["signal"]["window_tier"] == "transcript",
              (proc.returncode, out, proc.stderr))

        # Unknown model is not a default; a usable lower tier may still resolve.
        t = write_jsonl(root / "unknown-explicit.jsonl",
                        [usage_row(110_000, model="claude-unknown", window=200_000)])
        proc, out = run_hook(root, "Stop", t, session="unknown-explicit")
        why = reason(out)
        check("unknown model falls through to explicit window",
              why and why["signal"]["window_tier"] == "transcript", out)

        t = write_jsonl(root / "equal.jsonl",
                        [usage_row(110_000, window=200_000),
                         {"model_context_window": 200_000}])
        proc, out = run_hook(root, "Stop", t, session="equal")
        check("equal positive explicit repeats are accepted",
              reason(out) and reason(out)["signal"]["window"] == 200_000, out)

        unequal_row = usage_row(150_000, window=200_000)
        unequal_row["message"]["context_window"] = 300_000
        t = write_jsonl(root / "unequal.jsonl", [unequal_row])
        proc, out = run_hook(root, "Stop", t, session="unequal")
        why = reason(out)
        check("unequal current-row windows fail closed on the first Stop",
              proc.returncode == 0 and why
              and why["reason"] == "CONTEXT_SIGNAL_AMBIGUOUS"
              and why["signal"]["crossed"] is True
              and why["signal"]["signal_reason"]
              == "CONTEXT_SIGNAL_AMBIGUOUS",
              (proc.returncode, out, proc.stderr))

        same_window_row = usage_row(
            150_000, model="claude-opus-4-1")
        same_window_row["model"] = "claude-sonnet-5"
        t = write_jsonl(
            root / "same-window-model-conflict.jsonl", [same_window_row])
        proc, out = run_hook(
            root, "Stop", t, session="same-window-model-conflict")
        why = reason(out)
        check("distinct current models sharing a window remain ambiguous",
              proc.returncode == 0 and why
              and why["reason"] == "CONTEXT_SIGNAL_AMBIGUOUS"
              and why["signal"]["crossed"] is True,
              (proc.returncode, out, proc.stderr))

        known_unknown_row = usage_row(
            150_000, model="claude-haiku-4-5-20251001")
        known_unknown_row["model"] = "claude-unknown"
        t = write_jsonl(
            root / "known-unknown-model-conflict.jsonl", [known_unknown_row])
        proc, out = run_hook(
            root, "Stop", t, session="known-unknown-model-conflict")
        why = reason(out)
        check("known and unknown current models remain ambiguous",
              proc.returncode == 0 and why
              and why["reason"] == "CONTEXT_SIGNAL_AMBIGUOUS"
              and why["signal"]["crossed"] is True,
              (proc.returncode, out, proc.stderr))

        t = write_jsonl(root / "mixed-window-history.jsonl", [
            usage_row(40_000, window=1_000_000),
            usage_row(150_000, window=200_000),
        ])
        proc, out = run_hook(root, "Stop", t, session="mixed-window-history")
        why = reason(out)
        check("latest explicit window supersedes historical window",
              proc.returncode == 0 and why
              and why["signal"]["window"] == 200_000
              and why["signal"]["window_tier"] == "transcript"
              and why["signal"]["ratio"] == 75.0,
              (proc.returncode, out, proc.stderr))

        t = write_jsonl(root / "invalid.jsonl",
                        [usage_row(150_000), {"context_window": 0}])
        proc, out = run_hook(root, "Stop", t, session="invalid")
        check("all-invalid explicit windows initially allow", out is None, out)

        # compact_boundary.preTokens is both conservative window fallback and
        # usage high-water; minimum positive boundary wins.
        t = write_jsonl(root / "compact.jsonl", [
            usage_row(38_496),
            {"payload": {"type": "compact_boundary", "preTokens": 1_013_084}},
            {"payload": {"type": "compact_boundary", "preTokens": 1_100_000}},
        ])
        proc, out = run_hook(root, "Stop", t, session="compact")
        why = reason(out)
        check("minimum compact preTokens is final window tier",
              why and why["signal"]["window"] == 1_013_084
              and why["signal"]["used"] == 1_100_000, out)

        # Density from the other three independent predicates.
        for label, payloads in (
            ("tool-call density", [{}] * 20),
            ("mutated-path density",
             [{"tool_name": "Write", "tool_input": {"file_path": f"/tmp/p{i}"}} for i in range(8)]),
            ("worker-start density", [{"tool_name": "Agent"} for _ in range(3)]),
        ):
            session = label.replace(" ", "-")
            transcript = write_jsonl(root / f"{session}.jsonl",
                                     [usage_row(500_000, model="claude-opus-4-1")])
            for extra in payloads:
                run_hook(root, "PostToolUse", transcript, session=session,
                         payload_extra=extra)
            proc, out = run_hook(root, "Stop", transcript, session=session)
            check(label + " selects dense threshold",
                  reason(out) and reason(out)["signal"]["dense"] is True, out)
    finally:
        shutil.rmtree(root)


def fallback_and_hook_sequence():
    print("fallback and Claude event sequence")
    root = fresh_root()
    try:
        # Fresh unavailable signal announces once but does not block.
        unavailable = write_jsonl(root / "fallback-empty.jsonl", [])
        proc, first = run_hook(root, "PostToolUse", unavailable, session="fallback")
        proc, second = run_hook(root, "PostToolUse", unavailable, session="fallback")
        check("fresh unavailable signal notices once",
              first is not None and second is None, (first, second))
        proc, out = run_hook(root, "Stop", unavailable, session="fallback")
        check("unavailable below fallback cap allows first Stop", out is None, out)
        for _ in range(22):
            run_hook(root, "PostToolUse", unavailable, session="fallback")
        proc, out = run_hook(root, "Stop", unavailable, session="fallback")
        why = reason(out)
        check("fallback ANY invocation cap refuses",
              why and why["signal"]["fallback_level"] in {
                  "dense_soft", "normal_soft", "hard"}, out)
        check("fallback refusal preserves signal reason",
              why and why["signal"]["signal_reason"] == "CONTEXT_SIGNAL_UNAVAILABLE", why)

        transcript = write_jsonl(root / "sequence.jsonl",
                                 [usage_row(600_000, model="claude-opus-4-1")])
        proc, post = run_hook(root, "PostToolUse", transcript, session="sequence")
        proc, pre = run_hook(root, "PreCompact", transcript, session="sequence")
        proc, stop = run_hook(root, "Stop", transcript, session="sequence")
        check("PostToolUse emits one additionalContext notice",
              isinstance(post, dict)
              and "additionalContext" in post.get("hookSpecificOutput", {}), post)
        check("PreCompact always allows silently", pre is None, pre)
        check("Stop is the sole refusal seam", reason(stop) is not None, stop)

        malformed = subprocess.run(
            [sys.executable, str(HOOK)], input="{bad", text=True,
            capture_output=True, env=base_env(root), cwd=REPO)
        malformed_out = None
        if malformed.stdout.strip():
            try:
                malformed_out = json.loads(malformed.stdout)
            except Exception:
                malformed_out = {"unparseable": malformed.stdout}
        malformed_reason = reason(malformed_out)
        check("malformed hook input conservatively refuses the Stop seam",
              malformed.returncode == 0 and malformed_reason
              and malformed_reason["reason"] == "LIFECYCLE_INVALID",
              (malformed.returncode, malformed_out, malformed.stderr))
        malformed_post = subprocess.run(
            [sys.executable, str(HOOK)], input="{bad", text=True,
            capture_output=True,
            env=base_env(root, CARR_CONTEXT_HOOK_EVENT="PostToolUse"),
            cwd=REPO)
        check("out-of-band event keeps malformed observation non-blocking",
              malformed_post.returncode == 0
              and not malformed_post.stdout.strip(),
              (malformed_post.returncode, malformed_post.stdout,
               malformed_post.stderr))

        typed_bad = json.dumps({
            "hook_event_name": "Stop", "session_id": "semantic-bad",
            "transcript_path": {"not": "a path"},
        })
        typed_bad_proc = subprocess.run(
            [sys.executable, str(HOOK)], input=typed_bad, text=True,
            capture_output=True,
            env=base_env(root, CARR_CONTEXT_HOOK_EVENT="Stop"), cwd=REPO)
        typed_bad_out = (json.loads(typed_bad_proc.stdout)
                         if typed_bad_proc.stdout.strip() else None)
        check("semantically malformed Stop preprocessing fails closed",
              typed_bad_proc.returncode == 0 and reason(typed_bad_out)
              and reason(typed_bad_out)["reason"] == "LIFECYCLE_INVALID"
              and not typed_bad_proc.stderr.strip(),
              (typed_bad_proc.returncode, typed_bad_out,
               typed_bad_proc.stderr))

        # The Stop headroom notice fires once per task, so a repeated Stop on
        # the same task is silent; the spoof probe below therefore needs its
        # own fresh task to observe which event the hook actually honored.
        proc, repeat_stop = run_hook(root, "Stop", transcript, session="sequence")
        check("Stop headroom notice fires once per task",
              proc.returncode == 0 and repeat_stop is None, repeat_stop)

        spoofed_stop = json.dumps({
            "hook_event_name": "PostToolUse", "session_id": "spoofed-sequence",
            "prompt_id": "spoofed-stop", "tool_use_id": "spoofed-tool",
            "transcript_path": str(transcript),
        })
        spoofed_proc = subprocess.run(
            [sys.executable, str(HOOK)], input=spoofed_stop, text=True,
            capture_output=True,
            env=base_env(root, CARR_CONTEXT_HOOK_EVENT="Stop"), cwd=REPO)
        spoofed_out = (json.loads(spoofed_proc.stdout)
                       if spoofed_proc.stdout.strip() else None)
        check("wired Stop event is authoritative over payload event",
              spoofed_proc.returncode == 0 and reason(spoofed_out),
              (spoofed_proc.returncode, spoofed_out, spoofed_proc.stderr))

        fallback_hash = hashlib.sha256(b"claude:fallback").hexdigest()
        fallback_state = json.loads(
            (root / "state" / f"{fallback_hash}.json").read_text())
        fallback_owner = fallback_state["owners"]["fallback"]
        fallback_init = json.loads((
            root / "state/objects" / fallback_hash / "initialization"
            / f"{fallback_owner['activation_init_digest']}.json").read_text())
        hook_evidence = fallback_init.get("native_evidence")
        check("hook-created generation zero binds native callback evidence",
              isinstance(hook_evidence, dict)
              and hook_evidence.get("session_id") == "fallback"
              and hook_evidence.get("controller_callback_id") == "prompt-fallback"
              and fallback_init.get("native_evidence_digest") == digest(hook_evidence)
              and fallback_owner.get("evidence_digest") == digest(hook_evidence),
              (fallback_owner, fallback_init))

        # Complete the same task's handoff through immutable offer/declaration/
        # receipt/final objects, then prove recursive Stop allows.
        _, status = run_cli(root, "status", "--task-key", "claude:sequence")
        version = status["version"]
        pred_evidence = json.dumps({
            "session_id": "sequence", "transcript_path": str(transcript),
            "controller_callback_id": "callback-1", "status": "active",
            "successor_project_id": "p",
        })
        succ_evidence = json.dumps({
            "thread_id": "successor", "project_id": "p", "cwd": str(REPO),
            "status": "active", "pinnedIndex": 1, "event_id": "event-2",
        })
        proc, offer = run_cli(
            root, "handoff-offer-create", "--task-key", "claude:sequence",
            "--predecessor", "sequence", "--predecessor-surface", "claude",
            "--successor", "successor", "--successor-surface", "codex",
            "--evidence-json", pred_evidence, "--generation", "1",
            "--expected-version", str(version),
        )
        proc, declared = run_cli(
            root, "successor-declare", "--task-key", "claude:sequence",
            "--offer-digest", offer["offer_digest"], "--successor", "successor",
            "--evidence-json", succ_evidence,
            "--expected-version", str(offer["state"]["version"]),
        )
        proc, accepted = run_cli(
            root, "successor-accept", "--task-key", "claude:sequence",
            "--offer-digest", offer["offer_digest"], "--successor", "successor",
            "--evidence-json", succ_evidence,
            "--expected-version", str(declared["state"]["version"]),
        )
        task_hash = hashlib.sha256(b"claude:sequence").hexdigest()
        receipt_path = (root / "state/objects" / task_hash / "receipt"
                        / f"{accepted['receipt_digest']}.json")
        final_path = (root / "state/objects" / task_hash / "final"
                      / f"{accepted['final_digest']}.json")
        proc, verified = run_cli(root, "verify-handoff",
                                 "--task-key", "claude:sequence")
        check("offer receipt and final verify as one linked packet",
              proc.returncode == 0
              and verified["receipt_digest"] == accepted["receipt_digest"],
              verified)
        original_receipt = receipt_path.read_text()
        receipt_path.write_text(original_receipt.replace(
            '"successor":"successor"', '"successor":"tampered"', 1))
        proc, tampered = run_cli(root, "verify-handoff",
                                 "--task-key", "claude:sequence")
        check("receipt tamper is refused",
              proc.returncode == 2
              and tampered["reason"] == "HANDOFF_RECEIPT_INVALID", tampered)
        receipt_path.write_text(original_receipt)
        original_final = final_path.read_text()
        final_path.write_text(original_final.replace(
            '"successor":"successor"', '"successor":"tampered"', 1))
        proc, tampered = run_cli(root, "verify-handoff",
                                 "--task-key", "claude:sequence")
        check("final packet tamper is refused",
              proc.returncode == 2
              and tampered["reason"] == "HANDOFF_RECEIPT_INVALID", tampered)
        final_path.write_text(original_final)
        low_transcript = write_jsonl(
            root / "sequence-low.jsonl",
            [usage_row(1_000, model="claude-opus-4-1")],
        )
        receipt_path.write_text(original_receipt.replace(
            '"successor":"successor"', '"successor":"tampered"', 1))
        proc, tampered_stop = run_hook(
            root, "Stop", low_transcript, session="sequence")
        why = reason(tampered_stop)
        check("tampered verified handoff refuses low-signal predecessor Stop",
              proc.returncode == 0
              and why and why["reason"] == "HANDOFF_RECEIPT_INVALID",
              (proc.returncode, tampered_stop, proc.stderr))
        receipt_path.write_text(original_receipt)
        proc, after = run_hook(root, "Stop", transcript, session="sequence")
        check("verified takeover makes recursive Stop allow", after is None, after)
        proc, successor_stop = run_hook(
            root, "Stop", transcript, session="successor",
            payload_extra={"task_key": "claude:sequence"})
        check("Claude callback cannot stand in for the Codex successor",
              reason(successor_stop)
              and reason(successor_stop)["reason"] == "OWNERSHIP_MISMATCH",
              successor_stop)
        check("offer declaration receipt and final use four distinct digests",
              len({offer["offer_digest"], declared["declaration_digest"],
                   accepted["receipt_digest"], accepted["final_digest"]}) == 4,
              accepted)

        sequence_path = root / "state" / f"{task_hash}.json"
        original_state = sequence_path.read_text()
        evidence_tamper = json.loads(original_state)
        evidence_owner = evidence_tamper["owners"]["successor"]
        evidence_owner["evidence_digest"] = "0" * 64
        evidence_owner["ownership_digest"] = ownership_digest(evidence_owner)
        sequence_path.write_text(json.dumps(evidence_tamper))
        proc, evidence_mismatch = run_cli(
            root, "verify-handoff", "--task-key", "claude:sequence")
        check("verified owner evidence must match immutable acceptance",
              proc.returncode == 2
              and evidence_mismatch["reason"] == "HANDOFF_RECEIPT_INVALID",
              (proc.returncode, evidence_mismatch, proc.stderr))
        sequence_path.write_text(original_state)

        tampered_state = json.loads(sequence_path.read_text())
        successor_owner = tampered_state["owners"]["successor"]
        successor_owner["state"] = "TERMINAL"
        successor_owner["ownership_digest"] = ownership_digest(successor_owner)
        tampered_state["handoff"]["predecessor"] = "successor"
        sequence_path.write_text(json.dumps(tampered_state))
        proc, tampered_semantics = run_hook(
            root, "Stop", transcript, session="successor",
            payload_extra={"task_key": "claude:sequence"})
        why = reason(tampered_semantics)
        check("owner and verified-handoff semantic tamper refuses Stop",
              proc.returncode == 0 and why
              and why["reason"] == "LIFECYCLE_INVALID",
              (proc.returncode, tampered_semantics, proc.stderr))
        sequence_path.write_text(original_state)

        malformed_transcript = write_jsonl(
            root / "malformed-state.jsonl",
            [usage_row(1_000, model="claude-opus-4-1")],
        )
        run_hook(root, "PostToolUse", malformed_transcript, session="malformed")
        malformed_key = hashlib.sha256(b"claude:malformed").hexdigest()
        malformed_path = root / "state" / f"{malformed_key}.json"
        original_malformed = malformed_path.read_text()
        malformed_state = json.loads(malformed_path.read_text())
        malformed_state["signal"]["generation_tool_calls"] = "not-an-int"
        malformed_path.write_text(json.dumps(malformed_state))
        proc, malformed_stop = run_hook(
            root, "Stop", malformed_transcript, session="malformed")
        why = reason(malformed_stop)
        check("semantically malformed lifecycle state refuses Stop",
              proc.returncode == 0 and why
              and why["reason"] == "LIFECYCLE_INVALID"
              and why["signal"]["window_tier"] == "control_error",
              (proc.returncode, malformed_stop, proc.stderr))
        malformed_path.write_text(original_malformed)

        parseable_signal = json.loads(original_malformed)
        parseable_signal["signal"] = {}
        malformed_path.write_text(json.dumps(parseable_signal))
        proc, parseable_stop = run_hook(
            root, "Stop", malformed_transcript, session="malformed")
        why = reason(parseable_stop)
        check("parseable signal corruption cannot suppress Stop enforcement",
              proc.returncode == 0 and why
              and why["reason"] == "LIFECYCLE_INVALID",
              (proc.returncode, parseable_stop, proc.stderr))
        malformed_path.write_text(original_malformed)

        missing_owner_transcript = write_jsonl(
            root / "missing-owner-state.jsonl",
            [usage_row(1_000, model="claude-opus-4-1")],
        )
        run_hook(root, "PostToolUse", missing_owner_transcript,
                 session="missing-owner", wrapped=True)
        missing_owner_key = hashlib.sha256(b"claude:missing-owner").hexdigest()
        missing_owner_path = root / "state" / f"{missing_owner_key}.json"
        original_missing_owner = missing_owner_path.read_text()
        missing_owner_state = json.loads(missing_owner_path.read_text())
        missing_owner_state["owners"] = {}
        missing_owner_path.write_text(json.dumps(missing_owner_state))
        proc, missing_owner_stop = run_hook(
            root, "Stop", missing_owner_transcript,
            session="missing-owner", wrapped=True)
        why = reason(missing_owner_stop)
        check("missing active-owner record refuses wrapped low-signal Stop",
              proc.returncode == 0 and why
              and why["reason"] == "LIFECYCLE_INVALID"
              and why["signal"]["window_tier"] == "control_error",
              (proc.returncode, missing_owner_stop, proc.stderr))
        missing_owner_path.write_text(original_missing_owner)

        terminal_owner_transcript = write_jsonl(
            root / "terminal-owner-state.jsonl",
            [usage_row(1_000, model="claude-opus-4-1")],
        )
        run_hook(root, "PostToolUse", terminal_owner_transcript,
                 session="terminal-owner", wrapped=True)
        terminal_owner_key = hashlib.sha256(b"claude:terminal-owner").hexdigest()
        terminal_owner_path = root / "state" / f"{terminal_owner_key}.json"
        original_terminal_owner = terminal_owner_path.read_text()
        terminal_owner_state = json.loads(terminal_owner_path.read_text())
        terminal_owner = terminal_owner_state["owners"]["terminal-owner"]
        terminal_owner["state"] = "TERMINAL"
        terminal_owner["ownership_digest"] = ownership_digest(terminal_owner)
        terminal_owner_path.write_text(json.dumps(terminal_owner_state))
        proc, terminal_owner_stop = run_hook(
            root, "Stop", terminal_owner_transcript,
            session="terminal-owner", wrapped=True)
        why = reason(terminal_owner_stop)
        check("ACTIVE task with TERMINAL active owner refuses Stop",
              proc.returncode == 0 and why
              and why["reason"] == "LIFECYCLE_INVALID",
              (proc.returncode, terminal_owner_stop, proc.stderr))
        terminal_owner_path.write_text(original_terminal_owner)

        bad_handoff_transcript = write_jsonl(
            root / "bad-handoff-state.jsonl",
            [usage_row(1_000, model="claude-opus-4-1")],
        )
        run_hook(root, "PostToolUse", bad_handoff_transcript,
                 session="bad-handoff", wrapped=True)
        bad_handoff_key = hashlib.sha256(b"claude:bad-handoff").hexdigest()
        bad_handoff_path = root / "state" / f"{bad_handoff_key}.json"
        bad_handoff_state = json.loads(bad_handoff_path.read_text())
        bad_handoff_state["handoff"] = [{"state": "TAKEOVER_VERIFIED"}]
        bad_handoff_path.write_text(json.dumps(bad_handoff_state))
        proc, bad_handoff_stop = run_hook(
            root, "Stop", bad_handoff_transcript,
            session="bad-handoff", wrapped=True)
        why = reason(bad_handoff_stop)
        check("non-object handoff refuses wrapped low-signal Stop",
              proc.returncode == 0 and why
              and why["reason"] == "LIFECYCLE_INVALID"
              and why["signal"]["window_tier"] == "control_error",
              (proc.returncode, bad_handoff_stop, proc.stderr))
    finally:
        shutil.rmtree(root)


def codex_evidence(thread, *, status="active", pinned=1, event="e",
                   project="project", cwd=REPO):
    return json.dumps({
        "thread_id": thread, "project_id": project, "cwd": str(cwd),
        "status": status, "pinnedIndex": pinned, "event_id": event,
    })


def lifecycle_cas_and_tamper():
    print("immutable lifecycle, CAS, terminal irreversibility")
    root = fresh_root()
    try:
        proc, state = run_cli(root, "task-init", "--task-key", "task:cas",
                              "--owner", "old", "--surface", "codex",
                              "--evidence-json", codex_evidence("old"),
                              "--expected-version", "-1")
        check("task init creates version zero", proc.returncode == 0 and state["version"] == 0,
              (proc.returncode, state))

        proc, outside = run_cli(
            root, "task-init", "--task-key", "task:outside-checkout",
            "--owner", "outside", "--surface", "codex",
            "--evidence-json", codex_evidence("outside", cwd=root),
            "--expected-version", "-1")
        outside_hash = hashlib.sha256(b"task:outside-checkout").hexdigest()
        check("Codex task init refuses an owner outside the CARR checkout",
              proc.returncode == 2
              and outside["reason"] == "OWNERSHIP_MISMATCH"
              and not (root / "state" / f"{outside_hash}.json").exists(),
              (proc.returncode, outside, proc.stderr))

        _, stable = run_cli(
            root, "task-init", "--task-key", "task:stable-predecessor",
            "--owner", "stable", "--surface", "codex",
            "--evidence-json", codex_evidence("stable", project="project-a"),
            "--expected-version", "-1")
        proc, drifted_offer = run_cli(
            root, "handoff-offer-create", "--task-key", "task:stable-predecessor",
            "--predecessor", "stable", "--predecessor-surface", "codex",
            "--successor", "stable-next", "--successor-surface", "codex",
            "--evidence-json", codex_evidence("stable", project="project-b"),
            "--generation", "1", "--expected-version", str(stable["version"]))
        _, stable_after = run_cli(
            root, "status", "--task-key", "task:stable-predecessor")
        check("offer rebinds predecessor project and cwd to immutable activation",
              proc.returncode == 2
              and drifted_offer["reason"] == "OWNERSHIP_MISMATCH"
              and stable_after["version"] == stable["version"]
              and stable_after["handoff"] is None,
              (proc.returncode, drifted_offer, stable_after, proc.stderr))

        cas_hash = hashlib.sha256(b"task:cas").hexdigest()
        cas_path = root / "state" / f"{cas_hash}.json"
        original_cas = cas_path.read_text()
        forged_cas = json.loads(original_cas)
        forged_owner = forged_cas["owners"]["old"]
        forged_owner["evidence_digest"] = "0" * 64
        forged_owner["ownership_digest"] = ownership_digest(forged_owner)
        cas_path.write_text(json.dumps(forged_cas))
        proc, forged_init = run_cli(root, "status", "--task-key", "task:cas")
        check("generation-zero evidence is bound to immutable initialization",
              proc.returncode == 2
              and forged_init["reason"] == "HANDOFF_RECEIPT_INVALID",
              (proc.returncode, forged_init, proc.stderr))
        cas_path.write_text(original_cas)

        # Reject an unsupported target before creating an immutable offer or
        # changing the sole active owner to DRAINING.  Otherwise there is no
        # valid declaration or cancellation path out of the persisted state.
        proc, rejected = run_cli(
            root, "handoff-offer-create", "--task-key", "task:cas",
            "--predecessor", "old", "--predecessor-surface", "codex",
            "--successor", "bad", "--successor-surface", "unsupported",
            "--evidence-json", codex_evidence("old"), "--generation", "1",
            "--expected-version", "0",
        )
        _, after_reject = run_cli(root, "status", "--task-key", "task:cas")
        check("unsupported successor surface is rejected before DRAINING",
              proc.returncode == 2
              and rejected["reason"] == "SUCCESSOR_SURFACE_INVALID"
              and after_reject["version"] == 0
              and after_reject["active_owner"] == "old"
              and after_reject["owners"]["old"]["state"] == "ACTIVE"
              and after_reject["handoff"] is None,
              (proc.returncode, rejected, after_reject, proc.stderr))

        proc, first = run_cli(
            root, "handoff-offer-create", "--task-key", "task:cas",
            "--predecessor", "old", "--predecessor-surface", "codex",
            "--successor", "new", "--successor-surface", "codex",
            "--evidence-json", codex_evidence("old"), "--generation", "1",
            "--expected-version", "0",
        )
        proc2, lost = run_cli(
            root, "handoff-offer-create", "--task-key", "task:cas",
            "--predecessor", "old", "--predecessor-surface", "codex",
            "--successor", "other", "--successor-surface", "codex",
            "--evidence-json", codex_evidence("old"), "--generation", "1",
            "--expected-version", "0",
        )
        check("expected-version CAS has one winner",
              proc.returncode == 0 and proc2.returncode == 2
              and lost["reason"] == "LIFECYCLE_INVALID", lost)

        # Tamper an offer object and prove the declaration validates bytes,
        # never merely the filename/digest reference.
        task_hash = hashlib.sha256(b"task:cas").hexdigest()
        offer_path = (root / "state/objects" / task_hash / "offer"
                      / f"{first['offer_digest']}.json")
        original = offer_path.read_text()
        offer_path.write_text(original.replace('"successor":"new"',
                                               '"successor":"tampered"'))
        proc, rejected = run_cli(
            root, "successor-declare", "--task-key", "task:cas",
            "--offer-digest", first["offer_digest"], "--successor", "new",
            "--evidence-json", codex_evidence("new"), "--expected-version", "1",
        )
        check("tampered immutable offer is refused",
              proc.returncode == 2
              and rejected["reason"] == "HANDOFF_RECEIPT_INVALID", rejected)
        offer_path.write_text(original)

        # Surface and native identity are relationships, not caller labels.
        proc, bound = run_cli(root, "task-init", "--task-key", "task:binding",
                              "--owner", "bound-old", "--surface", "codex",
                              "--evidence-json", codex_evidence("bound-old"),
                              "--expected-version", "-1")
        claude_transcript = write_jsonl(root / "bound.jsonl", [usage_row(1)])
        wrong_surface = json.dumps({
            "session_id": "bound-old", "transcript_path": str(claude_transcript),
            "controller_callback_id": "callback", "status": "active",
        })
        proc, rejected = run_cli(
            root, "handoff-offer-create", "--task-key", "task:binding",
            "--predecessor", "bound-old", "--predecessor-surface", "claude",
            "--successor", "bound-new", "--successor-surface", "codex",
            "--evidence-json", wrong_surface, "--generation", "1",
            "--expected-version", str(bound["version"]),
        )
        check("offer surface must match the recorded owner",
              proc.returncode == 2 and rejected["reason"] == "OWNERSHIP_MISMATCH",
              rejected)

        proc, offer = run_cli(
            root, "handoff-offer-create", "--task-key", "task:binding",
            "--predecessor", "bound-old", "--predecessor-surface", "codex",
            "--successor", "bound-new", "--successor-surface", "codex",
            "--evidence-json", codex_evidence("bound-old"), "--generation", "1",
            "--expected-version", str(bound["version"]),
        )
        proc, rejected = run_cli(
            root, "successor-declare", "--task-key", "task:binding",
            "--offer-digest", offer["offer_digest"], "--successor", "bound-new",
            "--evidence-json", codex_evidence("unrelated"),
            "--expected-version", str(offer["state"]["version"]),
        )
        check("native successor id must match the lifecycle successor",
              proc.returncode == 2
              and rejected["reason"] == "SUCCESSOR_SURFACE_INVALID", rejected)
        proc, rejected = run_cli(
            root, "successor-declare", "--task-key", "task:binding",
            "--offer-digest", offer["offer_digest"], "--successor", "bound-new",
            "--evidence-json", codex_evidence("bound-new", project="other-project"),
            "--expected-version", str(offer["state"]["version"]),
        )
        check("Codex successor must stay in the offered project and cwd",
              proc.returncode == 2
              and rejected["reason"] == "OWNERSHIP_MISMATCH", rejected)
        proc, rejected = run_cli(
            root, "successor-declare", "--task-key", "task:binding",
            "--offer-digest", offer["offer_digest"], "--successor", "bound-new",
            "--evidence-json", codex_evidence("bound-new", cwd=root),
            "--expected-version", str(offer["state"]["version"]),
        )
        check("Codex successor must stay in the offered cwd",
              proc.returncode == 2
              and rejected["reason"] == "OWNERSHIP_MISMATCH", rejected)

        cross_evidence = json.dumps({
            "session_id": "cross-old", "transcript_path": str(claude_transcript),
            "controller_callback_id": "cross-callback", "status": "active",
            "successor_project_id": "project",
        })
        _, cross = run_cli(
            root, "task-init", "--task-key", "task:cross-binding",
            "--owner", "cross-old", "--surface", "claude",
            "--evidence-json", cross_evidence, "--expected-version", "-1")
        _, cross_offer = run_cli(
            root, "handoff-offer-create", "--task-key", "task:cross-binding",
            "--predecessor", "cross-old", "--predecessor-surface", "claude",
            "--successor", "cross-new", "--successor-surface", "codex",
            "--evidence-json", cross_evidence, "--generation", "1",
            "--expected-version", str(cross["version"]))
        proc, rejected = run_cli(
            root, "successor-declare", "--task-key", "task:cross-binding",
            "--offer-digest", cross_offer["offer_digest"],
            "--successor", "cross-new",
            "--evidence-json", codex_evidence(
                "cross-new", project="foreign-project", cwd=root),
            "--expected-version", str(cross_offer["state"]["version"]))
        check("cross-surface successor must stay in the CARR checkout",
              proc.returncode == 2
              and rejected["reason"] == "OWNERSHIP_MISMATCH", rejected)
        proc, rejected = run_cli(
            root, "successor-declare", "--task-key", "task:cross-binding",
            "--offer-digest", cross_offer["offer_digest"],
            "--successor", "cross-new",
            "--evidence-json", codex_evidence(
                "cross-new", project="foreign-project", cwd=REPO),
            "--expected-version", str(cross_offer["state"]["version"]))
        check("cross-surface successor must match predecessor-authorized project",
              proc.returncode == 2
              and rejected["reason"] == "OWNERSHIP_MISMATCH", rejected)
        _, missing_project_state = run_cli(
            root, "task-init", "--task-key", "task:cross-missing-project",
            "--owner", "missing-old", "--surface", "claude",
            "--evidence-json", json.dumps({
                "session_id": "missing-old",
                "transcript_path": str(claude_transcript),
                "controller_callback_id": "missing-callback",
                "status": "active",
            }), "--expected-version", "-1")
        proc, rejected = run_cli(
            root, "handoff-offer-create",
            "--task-key", "task:cross-missing-project",
            "--predecessor", "missing-old",
            "--predecessor-surface", "claude",
            "--successor", "missing-new",
            "--successor-surface", "codex",
            "--evidence-json", json.dumps({
                "session_id": "missing-old",
                "transcript_path": str(claude_transcript),
                "controller_callback_id": "missing-callback",
                "status": "active",
            }), "--generation", "1",
            "--expected-version", str(missing_project_state["version"]))
        check("cross-surface offer requires predecessor-authorized project",
              proc.returncode == 2
              and rejected["reason"] == "OWNERSHIP_MISMATCH", rejected)

        # The native Claude callback has only the new session id.  After a
        # verified Codex-to-Claude takeover it must discover the existing
        # same-task lifecycle rather than silently create claude:<session>.
        claude_successor_transcript = write_jsonl(
            root / "claude-successor.jsonl",
            [usage_row(1_000, model="claude-opus-4-1")])
        claude_successor_evidence = json.dumps({
            "session_id": "claude-new",
            "transcript_path": str(claude_successor_transcript),
            "controller_callback_id": "claude-successor-callback",
            "status": "active",
        })
        _, claude_binding = run_cli(
            root, "task-init", "--task-key", "task:claude-successor-binding",
            "--owner", "codex-old", "--surface", "codex",
            "--evidence-json", codex_evidence("codex-old"),
            "--expected-version", "-1")
        _, claude_offer = run_cli(
            root, "handoff-offer-create",
            "--task-key", "task:claude-successor-binding",
            "--predecessor", "codex-old", "--predecessor-surface", "codex",
            "--successor", "claude-new", "--successor-surface", "claude",
            "--evidence-json", codex_evidence("codex-old"), "--generation", "1",
            "--expected-version", str(claude_binding["version"]))
        _, claude_declared = run_cli(
            root, "successor-declare",
            "--task-key", "task:claude-successor-binding",
            "--offer-digest", claude_offer["offer_digest"],
            "--successor", "claude-new",
            "--evidence-json", claude_successor_evidence,
            "--expected-version", str(claude_offer["state"]["version"]))
        _, claude_accepted = run_cli(
            root, "successor-accept",
            "--task-key", "task:claude-successor-binding",
            "--offer-digest", claude_offer["offer_digest"],
            "--successor", "claude-new",
            "--evidence-json", claude_successor_evidence,
            "--expected-version", str(claude_declared["state"]["version"]))
        proc, callback = run_hook(
            root, "PostToolUse", claude_successor_transcript,
            session="claude-new")
        _, bound_after = run_cli(
            root, "status", "--task-key", "task:claude-successor-binding")
        derived_path = root / "state" / (
            hashlib.sha256(b"claude:claude-new").hexdigest() + ".json")
        check("Claude successor callback inherits the handed-off task key",
              proc.returncode == 0
              and bound_after["version"] == claude_accepted["state"]["version"] + 1
              and bound_after["active_owner"] == "claude-new"
              and bound_after["signal"]["invocations"] == 1
              and not derived_path.exists(),
              (proc.returncode, callback, bound_after, proc.stderr))

        bound_version = bound_after["version"]
        wrong_key = "task:wrong-claude-callback"
        wrong_path = root / "state" / (
            hashlib.sha256(wrong_key.encode()).hexdigest() + ".json")
        proc, wrong_callback = run_hook(
            root, "Stop", claude_successor_transcript,
            session="claude-new", payload_extra={"task_key": wrong_key})
        _, bound_after_wrong = run_cli(
            root, "status", "--task-key", "task:claude-successor-binding")
        check("accepted Claude binding overrides and rejects a conflicting callback key",
              proc.returncode == 0
              and reason(wrong_callback)
              and reason(wrong_callback)["reason"] == "OWNERSHIP_MISMATCH"
              and bound_after_wrong["version"] == bound_version
              and not wrong_path.exists(),
              (proc.returncode, wrong_callback, bound_after_wrong, proc.stderr))

        claude_successor_transcript.unlink()
        proc, historical_status = run_cli(
            root, "status", "--task-key", "task:claude-successor-binding")
        check("immutable accepted history survives transcript cleanup",
              proc.returncode == 0
              and historical_status["active_owner"] == "claude-new",
              (proc.returncode, historical_status, proc.stderr))

        proc, declared = run_cli(
            root, "successor-declare", "--task-key", "task:binding",
            "--offer-digest", offer["offer_digest"], "--successor", "bound-new",
            "--evidence-json", codex_evidence("bound-new", event="declare"),
            "--expected-version", str(offer["state"]["version"]),
        )
        proc, rejected = run_cli(
            root, "successor-accept", "--task-key", "task:binding",
            "--offer-digest", offer["offer_digest"], "--successor", "bound-new",
            "--evidence-json", codex_evidence("bound-new", event="accept-different"),
            "--expected-version", str(declared["state"]["version"]),
        )
        check("acceptance evidence must equal the declaration evidence",
              proc.returncode == 2
              and rejected["reason"] == "TAKEOVER_NOT_VERIFIED", rejected)

        # A completed handoff is historical evidence, not a one-generation
        # ceiling.  The new active owner can complete generation two while the
        # first predecessor's immutable terminal packet remains verifiable.
        _, multi = run_cli(
            root, "task-init", "--task-key", "task:multi-generation",
            "--owner", "g0", "--surface", "codex",
            "--evidence-json", codex_evidence("g0"),
            "--expected-version", "-1")
        _, multi_offer_1 = run_cli(
            root, "handoff-offer-create", "--task-key", "task:multi-generation",
            "--predecessor", "g0", "--predecessor-surface", "codex",
            "--successor", "g1", "--successor-surface", "codex",
            "--evidence-json", codex_evidence("g0"), "--generation", "1",
            "--expected-version", str(multi["version"]))
        _, multi_declared_1 = run_cli(
            root, "successor-declare", "--task-key", "task:multi-generation",
            "--offer-digest", multi_offer_1["offer_digest"],
            "--successor", "g1", "--evidence-json", codex_evidence("g1"),
            "--expected-version", str(multi_offer_1["state"]["version"]))
        _, multi_accepted_1 = run_cli(
            root, "successor-accept", "--task-key", "task:multi-generation",
            "--offer-digest", multi_offer_1["offer_digest"],
            "--successor", "g1", "--evidence-json", codex_evidence("g1"),
            "--expected-version", str(multi_declared_1["state"]["version"]))
        _, multi_terminal_1 = run_cli(
            root, "predecessor-terminal", "--task-key", "task:multi-generation",
            "--predecessor", "g0",
            "--evidence-json", codex_evidence("g0", status="terminal"),
            "--expected-version", str(multi_accepted_1["state"]["version"]))
        proc, multi_offer_2 = run_cli(
            root, "handoff-offer-create", "--task-key", "task:multi-generation",
            "--predecessor", "g1", "--predecessor-surface", "codex",
            "--successor", "g2", "--successor-surface", "codex",
            "--evidence-json", codex_evidence("g1"), "--generation", "2",
            "--expected-version", str(multi_terminal_1["version"]))
        check("completed handoff permits generation-two offer",
              proc.returncode == 0
              and multi_offer_2["state"]["handoff"]["generation"] == 2,
              (proc.returncode, multi_offer_2, proc.stderr))
        if proc.returncode == 0:
            _, multi_declared_2 = run_cli(
                root, "successor-declare", "--task-key", "task:multi-generation",
                "--offer-digest", multi_offer_2["offer_digest"],
                "--successor", "g2", "--evidence-json", codex_evidence("g2"),
                "--expected-version", str(multi_offer_2["state"]["version"]))
            _, multi_accepted_2 = run_cli(
                root, "successor-accept", "--task-key", "task:multi-generation",
                "--offer-digest", multi_offer_2["offer_digest"],
                "--successor", "g2", "--evidence-json", codex_evidence("g2"),
                "--expected-version", str(multi_declared_2["state"]["version"]))
            terminal_proc, multi_terminal_2 = run_cli(
                root, "predecessor-terminal", "--task-key", "task:multi-generation",
                "--predecessor", "g1",
                "--evidence-json", codex_evidence("g1", status="terminal"),
                "--expected-version", str(multi_accepted_2["state"]["version"]))
            completed_two = (terminal_proc.returncode == 0
                             and multi_terminal_2["generation"] == 2
                             and multi_terminal_2["active_owner"] == "g2"
                             and multi_terminal_2["owners"]["g2"]["state"] == "ACTIVE")
            completed_two_detail = (
                terminal_proc.returncode, multi_terminal_2, terminal_proc.stderr)
        else:
            completed_two = False
            completed_two_detail = (proc.returncode, multi_offer_2, proc.stderr)
        check("generation-two handoff completes with one active owner",
              completed_two, completed_two_detail)
        if completed_two:
            proc, reused_terminal = run_cli(
                root, "handoff-offer-create", "--task-key", "task:multi-generation",
                "--predecessor", "g2", "--predecessor-surface", "codex",
                "--successor", "g0", "--successor-surface", "codex",
                "--evidence-json", codex_evidence("g2"), "--generation", "3",
                "--expected-version", str(multi_terminal_2["version"]))
            _, multi_after_reuse = run_cli(
                root, "status", "--task-key", "task:multi-generation")
            check("terminal owner identity cannot be reused as a successor",
                  proc.returncode == 2
                  and reused_terminal["reason"] == "OWNERSHIP_MISMATCH"
                  and multi_after_reuse["version"] == multi_terminal_2["version"]
                  and multi_after_reuse["owners"]["g0"]["state"] == "TERMINAL"
                  and multi_after_reuse["active_owner"] == "g2",
                  (proc.returncode, reused_terminal, multi_after_reuse,
                   proc.stderr))

            multi_hash = hashlib.sha256(b"task:multi-generation").hexdigest()
            multi_path = root / "state" / f"{multi_hash}.json"
            intact_multi = multi_path.read_text()
            deleted_history = json.loads(intact_multi)
            del deleted_history["owners"]["g0"]
            multi_path.write_text(json.dumps(deleted_history))
            proc, deleted_reuse = run_cli(
                root, "handoff-offer-create", "--task-key", "task:multi-generation",
                "--predecessor", "g2", "--predecessor-surface", "codex",
                "--successor", "g0", "--successor-surface", "codex",
                "--evidence-json", codex_evidence("g2"), "--generation", "3",
                "--expected-version", str(multi_terminal_2["version"]))
            check("immutable lineage prevents deleted terminal identity reuse",
                  proc.returncode == 2
                  and deleted_reuse["reason"] in {
                      "LIFECYCLE_INVALID", "HANDOFF_RECEIPT_INVALID",
                      "OWNERSHIP_MISMATCH"},
                  (proc.returncode, deleted_reuse, proc.stderr))
            multi_path.write_text(intact_multi)

        # A consumer transition may not mint valid terminal provenance from a
        # verified owner whose mutable evidence was rewritten after takeover.
        _, laundering = run_cli(
            root, "task-init", "--task-key", "task:evidence-laundering",
            "--owner", "l0", "--surface", "codex",
            "--evidence-json", codex_evidence("l0"),
            "--expected-version", "-1")
        _, laundering_offer = run_cli(
            root, "handoff-offer-create", "--task-key", "task:evidence-laundering",
            "--predecessor", "l0", "--predecessor-surface", "codex",
            "--successor", "l1", "--successor-surface", "codex",
            "--evidence-json", codex_evidence("l0"), "--generation", "1",
            "--expected-version", str(laundering["version"]))
        _, laundering_declared = run_cli(
            root, "successor-declare", "--task-key", "task:evidence-laundering",
            "--offer-digest", laundering_offer["offer_digest"],
            "--successor", "l1", "--evidence-json", codex_evidence("l1"),
            "--expected-version", str(laundering_offer["state"]["version"]))
        _, laundering_accepted = run_cli(
            root, "successor-accept", "--task-key", "task:evidence-laundering",
            "--offer-digest", laundering_offer["offer_digest"],
            "--successor", "l1", "--evidence-json", codex_evidence("l1"),
            "--expected-version", str(laundering_declared["state"]["version"]))
        _, laundering_terminal = run_cli(
            root, "predecessor-terminal", "--task-key", "task:evidence-laundering",
            "--predecessor", "l0",
            "--evidence-json", codex_evidence("l0", status="terminal"),
            "--expected-version", str(laundering_accepted["state"]["version"]))
        laundering_hash = hashlib.sha256(b"task:evidence-laundering").hexdigest()
        laundering_path = root / "state" / f"{laundering_hash}.json"
        original_laundering = laundering_path.read_text()
        corrupted_multi = json.loads(laundering_path.read_text())
        corrupted_owner = corrupted_multi["owners"]["l1"]
        corrupted_owner["evidence_digest"] = "0" * 64
        corrupted_owner["ownership_digest"] = ownership_digest(corrupted_owner)
        laundering_path.write_text(json.dumps(corrupted_multi))
        proc, laundered_terminal = run_cli(
            root, "task-terminal", "--task-key", "task:evidence-laundering",
            "--owner", "l1",
            "--evidence-json", codex_evidence("l1", status="terminal"),
            "--expected-version", str(laundering_terminal["version"]))
        check("corrupted verified owner cannot launder terminal provenance",
              proc.returncode == 2
              and laundered_terminal["reason"] == "HANDOFF_RECEIPT_INVALID",
              (proc.returncode, laundered_terminal, proc.stderr))
        laundering_path.write_text(original_laundering)

        # Digest linkage alone cannot replace native evidence validation.  A
        # fully recomputed packet with required Codex fields removed refuses.
        _, packet_state = run_cli(
            root, "task-init", "--task-key", "task:packet-completeness",
            "--owner", "p0", "--surface", "codex",
            "--evidence-json", codex_evidence("p0"),
            "--expected-version", "-1")
        _, packet_offer = run_cli(
            root, "handoff-offer-create", "--task-key", "task:packet-completeness",
            "--predecessor", "p0", "--predecessor-surface", "codex",
            "--successor", "p1", "--successor-surface", "codex",
            "--evidence-json", codex_evidence("p0"), "--generation", "1",
            "--expected-version", str(packet_state["version"]))
        _, packet_declared = run_cli(
            root, "successor-declare", "--task-key", "task:packet-completeness",
            "--offer-digest", packet_offer["offer_digest"],
            "--successor", "p1", "--evidence-json", codex_evidence("p1"),
            "--expected-version", str(packet_offer["state"]["version"]))
        _, packet_accepted = run_cli(
            root, "successor-accept", "--task-key", "task:packet-completeness",
            "--offer-digest", packet_offer["offer_digest"],
            "--successor", "p1", "--evidence-json", codex_evidence("p1"),
            "--expected-version", str(packet_declared["state"]["version"]))
        packet_hash = hashlib.sha256(b"task:packet-completeness").hexdigest()
        packet_path = root / "state" / f"{packet_hash}.json"
        original_packet_state = packet_path.read_text()
        packet_value = json.loads(packet_path.read_text())
        object_root = root / "state/objects" / packet_hash

        def read_packet(kind, object_digest):
            return json.loads(
                (object_root / kind / f"{object_digest}.json").read_text())

        def write_packet(kind, value):
            object_digest = digest(value)
            path = object_root / kind / f"{object_digest}.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(canonical(value) + b"\n")
            return object_digest

        pending_packet = packet_value["handoff"]
        declaration_packet = read_packet(
            "declaration", pending_packet["declaration_digest"])
        receipt_packet = read_packet("receipt", pending_packet["receipt_digest"])
        final_packet = read_packet("final", pending_packet["final_digest"])
        incomplete_evidence = {"project_id": "project"}
        incomplete_digest = digest(incomplete_evidence)
        declaration_packet.update({
            "native_evidence": incomplete_evidence,
            "native_evidence_digest": incomplete_digest,
        })
        declaration_digest = write_packet("declaration", declaration_packet)
        acceptance_packet = receipt_packet["ownership_acceptance"]
        acceptance_packet.update({
            "native_evidence": incomplete_evidence,
            "native_evidence_digest": incomplete_digest,
        })
        receipt_packet["declaration_digest"] = declaration_digest
        receipt_digest = write_packet("receipt", receipt_packet)
        final_packet.update({
            "declaration": declaration_packet,
            "ownership_acceptance": acceptance_packet,
            "declaration_digest": declaration_digest,
            "receipt_digest": receipt_digest,
        })
        final_digest = write_packet("final", final_packet)
        pending_packet.update({
            "declaration_digest": declaration_digest,
            "receipt_digest": receipt_digest,
            "final_digest": final_digest,
        })
        packet_owner = packet_value["owners"]["p1"]
        packet_owner["evidence_digest"] = incomplete_digest
        packet_owner["activation_final_digest"] = final_digest
        packet_owner["ownership_digest"] = ownership_digest(packet_owner)
        packet_path.write_text(json.dumps(packet_value))
        proc, incomplete_packet = run_cli(
            root, "status", "--task-key", "task:packet-completeness")
        check("recomputed final packet still requires complete native evidence",
              proc.returncode == 2
              and incomplete_packet["reason"] == "HANDOFF_RECEIPT_INVALID",
              (proc.returncode, incomplete_packet, proc.stderr, packet_accepted))
        packet_path.write_text(original_packet_state)

        # Once a predecessor has supplied terminal evidence, its completed
        # handoff cannot strand the successor when dispatcher recovery begins.
        _, recoverable = run_cli(
            root, "task-init", "--task-key", "task:post-handoff-recovery",
            "--owner", "r0", "--surface", "codex",
            "--evidence-json", codex_evidence("r0"),
            "--expected-version", "-1")
        _, recover_offer = run_cli(
            root, "handoff-offer-create", "--task-key", "task:post-handoff-recovery",
            "--predecessor", "r0", "--predecessor-surface", "codex",
            "--successor", "r1", "--successor-surface", "codex",
            "--evidence-json", codex_evidence("r0"), "--generation", "1",
            "--expected-version", str(recoverable["version"]))
        _, recover_declared = run_cli(
            root, "successor-declare", "--task-key", "task:post-handoff-recovery",
            "--offer-digest", recover_offer["offer_digest"],
            "--successor", "r1", "--evidence-json", codex_evidence("r1"),
            "--expected-version", str(recover_offer["state"]["version"]))
        _, recover_accepted = run_cli(
            root, "successor-accept", "--task-key", "task:post-handoff-recovery",
            "--offer-digest", recover_offer["offer_digest"],
            "--successor", "r1", "--evidence-json", codex_evidence("r1"),
            "--expected-version", str(recover_declared["state"]["version"]))
        _, recover_terminal = run_cli(
            root, "predecessor-terminal", "--task-key", "task:post-handoff-recovery",
            "--predecessor", "r0",
            "--evidence-json", codex_evidence("r0", status="terminal"),
            "--expected-version", str(recover_accepted["state"]["version"]))
        recover_snapshot = snapshot(
            "task:post-handoff-recovery",
            recover_terminal["owners"]["r1"]["ownership_digest"],
            "CAPACITY_EXHAUSTED", source="event-post-handoff-recovery",
            capacity="CONTEXT_EXHAUSTED", worker_id="r1", generation=1)
        proc, recovered = run_cli(
            root, "dispatcher-evaluate", "--task-key", "task:post-handoff-recovery",
            "--snapshot-json", json.dumps(recover_snapshot), "--apply",
            "--expected-version", str(recover_terminal["version"]))
        check("completed handoff permits successor recovery",
              proc.returncode == 0
              and recovered["state"]["owners"]["r1"]["state"] == "DRAINING"
              and recovered["state"]["recovery_intent"]["state"] == "PENDING",
              (proc.returncode, recovered, proc.stderr))
        proc, aborted_recovery = run_cli(
            root, "recovery-abort", "--task-key", "task:post-handoff-recovery",
            "--owner", "r1", "--nonce", recovered["nonce"],
            "--expected-version", str(recovered["state"]["version"]))
        check("post-handoff recovery can abort to the verified owner",
              proc.returncode == 0
              and aborted_recovery["owners"]["r1"]["state"] == "ACTIVE",
              (proc.returncode, aborted_recovery, proc.stderr))
        recovery_hash = hashlib.sha256(b"task:post-handoff-recovery").hexdigest()
        recovery_path = root / "state" / f"{recovery_hash}.json"
        aborted_text = recovery_path.read_text()
        tampered_aborted = json.loads(aborted_text)
        aborted_owner = tampered_aborted["owners"]["r1"]
        aborted_owner["evidence_digest"] = "0" * 64
        aborted_owner["ownership_digest"] = ownership_digest(aborted_owner)
        recovery_path.write_text(json.dumps(tampered_aborted))
        proc, aborted_tamper = run_cli(
            root, "status", "--task-key", "task:post-handoff-recovery")
        check("aborted recovery retains immutable successor binding",
              proc.returncode == 2
              and aborted_tamper["reason"] == "HANDOFF_RECEIPT_INVALID",
              (proc.returncode, aborted_tamper, proc.stderr))
        recovery_path.write_text(aborted_text)
        proc, post_abort_offer = run_cli(
            root, "handoff-offer-create",
            "--task-key", "task:post-handoff-recovery",
            "--predecessor", "r1", "--predecessor-surface", "codex",
            "--successor", "r2", "--successor-surface", "codex",
            "--evidence-json", codex_evidence("r1"), "--generation", "2",
            "--expected-version", str(aborted_recovery["version"]))
        check("aborted recovery does not block the next ordinary handoff",
              proc.returncode == 0
              and post_abort_offer["state"]["handoff"]["generation"] == 2
              and post_abort_offer["state"]["recovery_intent"] is None,
              (proc.returncode, post_abort_offer, proc.stderr))

        # Native Claude callbacks cannot impersonate a same-ID Codex owner.
        _, isolated = run_cli(
            root, "task-init", "--task-key", "task:surface-isolation",
            "--owner", "same-id", "--surface", "codex",
            "--evidence-json", codex_evidence("same-id"),
            "--expected-version", "-1")
        proc, crossed_surface = run_hook(
            root, "Stop", None, session="same-id",
            payload_extra={"task_key": "task:surface-isolation"})
        _, isolated_after = run_cli(
            root, "status", "--task-key", "task:surface-isolation")
        check("Claude callback cannot mutate a same-ID Codex owner",
              proc.returncode == 0
              and reason(crossed_surface)
              and reason(crossed_surface)["reason"] == "OWNERSHIP_MISMATCH"
              and isolated_after["version"] == isolated["version"]
              and isolated_after["signal"]["cycles"] == 0,
              (proc.returncode, crossed_surface, isolated_after, proc.stderr))

        # Independent task terminal is evidence-bound and irreversible.
        proc, state = run_cli(root, "task-init", "--task-key", "task:terminal",
                              "--owner", "only", "--surface", "codex",
                              "--evidence-json", codex_evidence("only"),
                              "--expected-version", "-1")
        proc, terminal = run_cli(
            root, "task-terminal", "--task-key", "task:terminal",
            "--owner", "only",
            "--evidence-json", codex_evidence("only", status="terminal"),
            "--expected-version", "0",
        )
        check("task terminal requires native terminal evidence",
              proc.returncode == 0
              and terminal["task_status"] == "TERMINAL"
              and terminal["active_owner"] is None, terminal)
        proc, reused_global = run_cli(
            root, "task-init", "--task-key", "task:terminal-reuse",
            "--owner", "only", "--surface", "codex",
            "--evidence-json", codex_evidence("only"),
            "--expected-version", "-1")
        reused_global_hash = hashlib.sha256(b"task:terminal-reuse").hexdigest()
        check("terminal native identity cannot be reused by another task",
              proc.returncode == 2
              and reused_global["reason"] == "OWNERSHIP_MISMATCH"
              and not (root / "state" / f"{reused_global_hash}.json").exists(),
              (proc.returncode, reused_global, proc.stderr))
        terminal_version = terminal["version"]
        run_hook(root, "Stop", None, session="intruder",
                 payload_extra={"task_key": "task:terminal"})
        _, unchanged_terminal = run_cli(
            root, "status", "--task-key", "task:terminal")
        check("terminal observations are read-only",
              unchanged_terminal["version"] == terminal_version,
              unchanged_terminal)
        proc, resurrect = run_cli(
            root, "handoff-offer-create", "--task-key", "task:terminal",
            "--predecessor", "only", "--predecessor-surface", "codex",
            "--successor", "again", "--successor-surface", "codex",
            "--evidence-json", codex_evidence("only"), "--generation", "1",
            "--expected-version", "1",
        )
        check("terminal task cannot recover or resurrect",
              proc.returncode == 2
              and resurrect["reason"] == "LIFECYCLE_INVALID", resurrect)

        proc, relative = run_cli(
            root, "task-init", "--task-key", "task:canonical-cwd",
            "--owner", "relative-cwd", "--surface", "codex",
            "--evidence-json", codex_evidence("relative-cwd", cwd="."),
            "--expected-version", "-1")
        relative_hash = hashlib.sha256(b"task:canonical-cwd").hexdigest()
        relative_packet = None
        if proc.returncode == 0:
            init_digest = relative["owners"]["relative-cwd"]["activation_init_digest"]
            relative_packet = json.loads((
                root / "state/objects" / relative_hash / "initialization"
                / f"{init_digest}.json").read_text())
        check("Codex cwd is canonicalized before immutable publication",
              proc.returncode == 0 and relative_packet
              and relative_packet["native_evidence"]["cwd"] == str(REPO.resolve()),
              (proc.returncode, relative_packet, proc.stderr))

        late_transcript = write_jsonl(
            root / "late-terminal.jsonl",
            [usage_row(1_000, model="claude-opus-4-1")])
        late_active_evidence = json.dumps({
            "session_id": "late-terminal", "transcript_path": str(late_transcript),
            "controller_callback_id": "late-terminal-callback", "status": "active",
        })
        _, late_state = run_cli(
            root, "task-init", "--task-key", "task:late-terminal",
            "--owner", "late-terminal", "--surface", "claude",
            "--evidence-json", late_active_evidence, "--expected-version", "-1")
        late_terminal_evidence = json.dumps({
            "session_id": "late-terminal", "transcript_path": str(late_transcript),
            "controller_callback_id": "late-terminal-callback", "status": "terminal",
        })
        _, late_terminal_state = run_cli(
            root, "task-terminal", "--task-key", "task:late-terminal",
            "--owner", "late-terminal", "--evidence-json", late_terminal_evidence,
            "--expected-version", str(late_state["version"]))
        proc, late_callback = run_hook(
            root, "Stop", None, session="late-terminal")
        _, late_after = run_cli(
            root, "status", "--task-key", "task:late-terminal")
        late_derived_path = root / "state" / (
            hashlib.sha256(b"claude:late-terminal").hexdigest() + ".json")
        check("late terminal Claude callback no-ops without a live transcript",
              proc.returncode == 0 and late_callback is None
              and late_after["version"] == late_terminal_state["version"]
              and not late_derived_path.exists(),
              (proc.returncode, late_callback, late_after, proc.stderr))

        chain_transcript = write_jsonl(
            root / "claude-history.jsonl",
            [usage_row(1_000, model="claude-opus-4-1")])

        def claude_evidence(owner, status="active"):
            return json.dumps({
                "session_id": owner,
                "transcript_path": str(chain_transcript),
                "controller_callback_id": f"callback-{owner}",
                "status": status,
            })

        _, chain = run_cli(
            root, "task-init", "--task-key", "task:claude-history",
            "--owner", "cg0", "--surface", "claude",
            "--evidence-json", claude_evidence("cg0"),
            "--expected-version", "-1")
        current_chain = chain
        for generation, predecessor, successor in (
            (1, "cg0", "cg1"), (2, "cg1", "cg2"),
        ):
            _, chain_offer = run_cli(
                root, "handoff-offer-create", "--task-key", "task:claude-history",
                "--predecessor", predecessor, "--predecessor-surface", "claude",
                "--successor", successor, "--successor-surface", "claude",
                "--evidence-json", claude_evidence(predecessor),
                "--generation", str(generation), "--expected-version",
                str(current_chain["version"]))
            _, chain_declared = run_cli(
                root, "successor-declare", "--task-key", "task:claude-history",
                "--offer-digest", chain_offer["offer_digest"],
                "--successor", successor,
                "--evidence-json", claude_evidence(successor),
                "--expected-version", str(chain_offer["state"]["version"]))
            _, chain_accepted = run_cli(
                root, "successor-accept", "--task-key", "task:claude-history",
                "--offer-digest", chain_offer["offer_digest"],
                "--successor", successor,
                "--evidence-json", claude_evidence(successor),
                "--expected-version", str(chain_declared["state"]["version"]))
            _, current_chain = run_cli(
                root, "predecessor-terminal", "--task-key", "task:claude-history",
                "--predecessor", predecessor,
                "--evidence-json", claude_evidence(predecessor, "terminal"),
                "--expected-version", str(chain_accepted["state"]["version"]))
        proc, old_callback = run_hook(
            root, "Stop", chain_transcript, session="cg0")
        _, chain_after = run_cli(
            root, "status", "--task-key", "task:claude-history")
        check("older terminal Claude callback is a verified no-op",
              proc.returncode == 0 and old_callback is None
              and chain_after["version"] == current_chain["version"],
              (proc.returncode, old_callback, chain_after, proc.stderr))

        _, tamper_terminal = run_cli(
            root, "task-init", "--task-key", "task:terminal-tamper",
            "--owner", "tamper-owner", "--surface", "codex",
            "--evidence-json", codex_evidence("tamper-owner"),
            "--expected-version", "-1")
        terminal_hash = hashlib.sha256(b"task:terminal-tamper").hexdigest()
        terminal_path = root / "state" / f"{terminal_hash}.json"
        original_terminal = terminal_path.read_text()
        terminal_state = json.loads(terminal_path.read_text())
        terminal_owner = terminal_state["owners"]["tamper-owner"]
        terminal_state["task_status"] = "TERMINAL"
        terminal_state["active_owner"] = None
        terminal_owner["state"] = "TERMINAL"
        terminal_owner.pop("terminal_evidence_digest", None)
        terminal_owner["ownership_digest"] = ownership_digest(terminal_owner)
        terminal_path.write_text(json.dumps(terminal_state))
        proc, terminal_stop = run_hook(
            root, "Stop", None, session="intruder",
            payload_extra={"task_key": "task:terminal-tamper"})
        why = reason(terminal_stop)
        check("terminal task without immutable provenance refuses Stop",
              proc.returncode == 0 and why
              and why["reason"] == "LIFECYCLE_INVALID",
              (proc.returncode, terminal_stop, proc.stderr, tamper_terminal))
        terminal_path.write_text(original_terminal)

        corrupt_path = root / "state" / ("f" * 64 + ".json")
        corrupt_path.write_text(json.dumps({"owners": []}))
        proc, corrupt_callback = run_hook(
            root, "Stop", None, session="corrupt-scan")
        check("malformed lifecycle file fails Claude binding scan closed",
              proc.returncode == 0 and reason(corrupt_callback)
              and reason(corrupt_callback)["reason"] == "LIFECYCLE_INVALID"
              and not proc.stderr.strip(),
              (proc.returncode, corrupt_callback, proc.stderr))
    finally:
        shutil.rmtree(root)


def rollout_resolver_cases():
    print("Codex rollout task resolver")
    root = fresh_root()
    try:
        fixtures = {
            "root": [
                {"type": "session_meta",
                 "payload": {"id": "root", "session_id": "root"}},
            ],
            "subagent": [
                {"type": "session_meta",
                 "payload": {"id": "child", "session_id": "root"}},
            ],
            "legacy": [
                {"type": "session_meta", "payload": {"session_id": "legacy"}},
            ],
            "turn": [
                {"type": "event_msg",
                 "payload": {"type": "task_started", "turn_id": "turn-1"}},
            ],
            "ambiguous": [
                {"type": "session_meta", "payload": {"id": "one"}},
                {"type": "session_meta", "payload": {"id": "two"}},
            ],
            "archived": [
                {"type": "session_meta",
                 "payload": {"id": "archived", "status": "archived"}},
            ],
        }
        expected = {
            "root": ("codex:root", "payload.id"),
            "subagent": ("codex:child", "payload.id"),
            "legacy": ("codex:legacy", "session_id"),
            "turn": ("codex-turn:turn-1", "task_started.turn_id"),
            "archived": ("codex:archived", "payload.id"),
        }
        for name, rows in fixtures.items():
            path = write_jsonl(root / f"{name}.jsonl", rows)
            proc, out = run_cli(root, "codex-task-key", "--rollout", str(path))
            if name == "ambiguous":
                check("multiple authoritative IDs are ambiguous",
                      out.get("ok") is False
                      and out["reason"] == "CONTEXT_SIGNAL_AMBIGUOUS", out)
            else:
                check(f"{name} fixture resolves through authoritative tier",
                      (out.get("task_key"), out.get("resolver")) == expected[name], out)
        path = root / "subagent.jsonl"
        _, out = run_cli(root, "codex-task-key", "--rollout", str(path))
        check("unequal session_id lineage is ignored",
              out.get("ignored_lineage") == ["root"], out)
        _, out = run_cli(root, "task-key", "--surface", "claude",
                         "--session-id", "claude-root")
        check("generic task-key resolver is read-only and stable",
              out.get("task_key") == "claude:claude-root"
              and out.get("resolver") == "session_id", out)

        # Controller identity fields are typed protocol values. JSON numbers
        # and booleans must never be stringified into durable task keys.
        invalid_identity_rows = (
            [{"type": "session_meta", "payload": {"id": 7}}],
            [{"type": "session_meta", "payload": {"id": True}}],
            [{"type": "session_meta", "payload": {"session_id": 7}}],
            [{"type": "session_meta", "payload": {"session_id": False}}],
            [{"type": "event_msg", "payload": {
                "type": "task_started", "turn_id": 7}}],
            [{"type": "event_msg", "payload": {
                "type": "task_started", "turn_id": True}}],
            [{"type": "session_meta", "payload": {
                "id": "valid-authority", "session_id": 7}}],
        )
        invalid_identity_results = []
        for index, rows in enumerate(invalid_identity_rows):
            path = write_jsonl(root / f"invalid-identity-{index}.jsonl", rows)
            invalid_identity_results.append(
                run_cli(root, "codex-task-key", "--rollout", str(path)))
        check("Codex rollout resolver rejects scalar identity coercion",
              all(proc.returncode == 0 and out.get("ok") is False
                  and out.get("reason") == "CONTEXT_SIGNAL_INVALID"
                  and "task_key" not in out
                  for proc, out in invalid_identity_results),
              [(proc.returncode, out, proc.stderr)
               for proc, out in invalid_identity_results])

        # Codex exposes both current-turn usage and an account/session-wide
        # cumulative billing total.  Only the former measures the live context
        # that can be exhausted by this task.
        native = write_jsonl(root / "native-usage.jsonl", [
            {"type": "session_meta", "payload": {"id": "native-usage"}},
            {"type": "turn_context", "payload": {"model": "gpt-5.6-sol"}},
            {"type": "event_msg", "payload": {
                "type": "token_count",
                "info": {
                    "model_context_window": 258_400,
                    "last_token_usage": {
                        "input_tokens": 29_000,
                        "cached_input_tokens": 900,
                        "output_tokens": 121,
                        "total_tokens": 30_021,
                    },
                    "total_token_usage": {
                        "input_tokens": 49_000_000,
                        "cached_input_tokens": 1_000_000,
                        "output_tokens": 107_705,
                        "total_tokens": 50_107_705,
                    },
                },
            }},
        ])
        proc, out = run_cli(root, "codex-observe", "--rollout", str(native))
        check("Codex occupancy ignores cumulative billing usage",
              proc.returncode == 0
              and out["context"]["signal"]["highwater"] == 30_021
              and out["context"]["ratio"] == 11.618,
              (proc.returncode, out, proc.stderr))
    finally:
        shutil.rmtree(root)


def snapshot(task_key, ownership_digest, state, *, source="event-1",
             observed=None, attention="NONE", recoverable=False,
             capacity=None, worker_id="owner", last_progress=None,
             generation=0):
    observed = observed or dt.datetime.now(dt.timezone.utc).replace(
        microsecond=0).isoformat().replace("+00:00", "Z")
    last_progress = last_progress or observed
    worker = {
        "id": worker_id, "surface": "codex", "generation": generation,
        "normalized_state": state, "last_progress_at": last_progress,
        "task_terminal": state == "TERMINAL", "attention_kind": attention,
        "error_code": "ERR" if state == "ERROR" else None,
        "recoverable": recoverable, "capacity_code": capacity,
        "ownership_digest": ownership_digest,
    }
    value = {
        "task_key": task_key, "observed_at": observed,
        "source_event_id": source, "observer_id": "dispatcher",
        "source_surface": "codex", "evidence_ref": "native:event-1",
        "workers": [worker],
    }
    value["evidence_digest"] = digest(value)
    return value


def dispatcher_cases():
    print("dispatcher schema, predicates, nonce, replay")
    root = fresh_root()
    try:
        _, state = run_cli(root, "task-init", "--task-key", "task:dispatch",
                           "--owner", "owner", "--surface", "codex",
                           "--evidence-json", codex_evidence("owner"),
                           "--expected-version", "-1")
        own = state["owners"]["owner"]["ownership_digest"]

        _, provenance_state = run_cli(
            root, "task-init", "--task-key", "task:null-provenance",
            "--owner", "provenance-owner", "--surface", "codex",
            "--evidence-json", codex_evidence("provenance-owner"),
            "--expected-version", "-1")
        invalid_provenance = snapshot(
            "task:null-provenance",
            provenance_state["owners"]["provenance-owner"]["ownership_digest"],
            "IDLE", source="event-null-provenance",
            worker_id="provenance-owner")
        for field in ("source_event_id", "observer_id", "source_surface",
                      "evidence_ref"):
            invalid_provenance[field] = None
        invalid_provenance["evidence_digest"] = digest({
            key: value for key, value in invalid_provenance.items()
            if key != "evidence_digest"
        })
        proc, rejected = run_cli(
            root, "dispatcher-evaluate", "--task-key", "task:null-provenance",
            "--snapshot-json", json.dumps(invalid_provenance), "--apply",
            "--expected-version", "0")
        check("null recovery provenance is refused before mutation",
              proc.returncode == 2
              and rejected["reason"] == "RECOVERY_SNAPSHOT_INVALID", rejected)

        for label, worker_state, attention, recoverable, capacity, action, why in (
            ("running", "RUNNING", "NONE", False, None, "NOOP", "RECOVERY_ACTIVE"),
            ("waiting authority", "WAITING_ATTENTION", "AUTHORITY", False, None,
             "NOOP", "RECOVERY_WAITING"),
            ("capacity", "CAPACITY_EXHAUSTED", "NONE", False,
             "CONTEXT_EXHAUSTED", "RECOVER_SAME_TASK", "RECOVERY_CAPACITY"),
            ("recoverable error", "ERROR", "NONE", True, None,
             "RECOVER_SAME_TASK", "RECOVERY_ERROR"),
            ("idle", "IDLE", "NONE", False, None,
             "RECOVER_SAME_TASK", "RECOVERY_IDLE"),
            ("terminal", "TERMINAL", "NONE", False, None,
             "NOOP", "RECOVERY_TERMINAL"),
        ):
            snap = snapshot("task:dispatch", own, worker_state,
                            attention=attention, recoverable=recoverable,
                            capacity=capacity, source=f"event-{label}")
            proc, out = run_cli(root, "dispatcher-evaluate",
                                "--task-key", "task:dispatch",
                                "--snapshot-json", json.dumps(snap))
            check(label + " predicate is exact",
                  proc.returncode == 0 and out["action"] == action
                  and out["reason"] == why, out)

        stale = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=121)
                 ).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        snap = snapshot("task:dispatch", own, "RUNNING", observed=stale)
        proc, out = run_cli(root, "dispatcher-evaluate",
                            "--task-key", "task:dispatch",
                            "--snapshot-json", json.dumps(snap))
        check("snapshot older than 120 seconds is refused",
              proc.returncode == 2
              and out["reason"] == "RECOVERY_SNAPSHOT_STALE", out)

        future = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=30)
                  ).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        snap = snapshot("task:dispatch", own, "RUNNING", observed=future)
        proc, out = run_cli(root, "dispatcher-evaluate",
                            "--task-key", "task:dispatch",
                            "--snapshot-json", json.dumps(snap))
        check("future snapshot is invalid", proc.returncode == 2
              and out["reason"] == "RECOVERY_SNAPSHOT_INVALID", out)

        stale_progress = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=61)
                          ).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        snap = snapshot("task:dispatch", own, "RUNNING",
                        last_progress=stale_progress, source="event-stale-heartbeat")
        proc, out = run_cli(root, "dispatcher-evaluate",
                            "--task-key", "task:dispatch",
                            "--snapshot-json", json.dumps(snap))
        check("stale RUNNING heartbeat cannot claim recovery active",
              proc.returncode == 0 and out["action"] == "RECOVER_SAME_TASK"
              and out["reason"] == "RECOVERY_IDLE", out)

        snap = snapshot("task:dispatch", own, "CAPACITY_EXHAUSTED",
                        capacity="CONTEXT_EXHAUSTED", source="event-apply")
        proc, preview = run_cli(root, "dispatcher-evaluate",
                                "--task-key", "task:dispatch",
                                "--snapshot-json", json.dumps(snap))
        check("dispatcher evaluation is read-only without apply",
              proc.returncode == 0 and preview["applied"] is False
              and preview["nonce"], preview)
        _, unchanged = run_cli(root, "status", "--task-key", "task:dispatch")
        check("read-only dispatcher leaves version unchanged",
              unchanged["version"] == 0, unchanged["version"])
        proc, missing_cas = run_cli(root, "dispatcher-evaluate",
                                    "--task-key", "task:dispatch",
                                    "--snapshot-json", json.dumps(snap),
                                    "--apply")
        check("new dispatcher apply requires expected-version CAS",
              proc.returncode == 2
              and missing_cas["reason"] == "LIFECYCLE_INVALID", missing_cas)
        proc, applied = run_cli(root, "dispatcher-evaluate",
                                "--task-key", "task:dispatch",
                                "--snapshot-json", json.dumps(snap),
                                "--apply", "--expected-version", "0")
        check("apply fences old owner and records one intent",
              proc.returncode == 0 and applied["state"]["owners"]["owner"]["state"] == "DRAINING"
              and applied["state"]["recovery_intent"]["nonce"] == preview["nonce"], applied)
        proc, replay = run_cli(root, "dispatcher-evaluate",
                               "--task-key", "task:dispatch",
                               "--snapshot-json", json.dumps(snap),
                               "--apply", "--expected-version", "1")
        check("exact recovery replay returns same nonce",
              proc.returncode == 0 and replay["replay"] is True
              and replay["nonce"] == preview["nonce"]
              and replay["state"]["version"] == 1, replay)
        proc, missing_replay_cas = run_cli(
            root, "dispatcher-evaluate", "--task-key", "task:dispatch",
            "--snapshot-json", json.dumps(snap), "--apply")
        check("applied recovery replay still requires expected-version CAS",
              proc.returncode == 2
              and missing_replay_cas["reason"] == "LIFECYCLE_INVALID",
              missing_replay_cas)
        changed = snapshot("task:dispatch", own, "RUNNING", source="event-apply")
        proc, changed_replay = run_cli(
            root, "dispatcher-evaluate", "--task-key", "task:dispatch",
            "--snapshot-json", json.dumps(changed), "--apply",
            "--expected-version", "1")
        check("recovery replay is bound to the exact snapshot digest",
              proc.returncode == 2
              and changed_replay["reason"] == "LIFECYCLE_INVALID",
              changed_replay)
        malformed = {**snap, "unexpected": True}
        proc, refused = run_cli(root, "dispatcher-evaluate",
                                "--task-key", "task:dispatch",
                                "--snapshot-json", json.dumps(malformed),
                                "--apply", "--expected-version", "1")
        check("recovery replay still validates exact snapshot schema",
              proc.returncode == 2
              and refused["reason"] == "RECOVERY_SNAPSHOT_INVALID", refused)
        other = snapshot("task:dispatch", own, "IDLE", source="event-other")
        proc, refused = run_cli(root, "dispatcher-evaluate",
                                "--task-key", "task:dispatch",
                                "--snapshot-json", json.dumps(other),
                                "--apply", "--expected-version", "1")
        check("second recovery while one is pending is refused",
              proc.returncode == 2
              and refused["reason"] == "LIFECYCLE_INVALID", refused)

        proc, aborted = run_cli(
            root, "recovery-abort", "--task-key", "task:dispatch",
            "--owner", "owner", "--nonce", preview["nonce"],
            "--expected-version", "1")
        check("recovery abort restores the sole owner",
              proc.returncode == 0
              and aborted["owners"]["owner"]["state"] == "ACTIVE"
              and aborted["active_owner"] == "owner"
              and aborted["recovery_intent"]["state"] == "ABORTED", aborted)

        proc, resurrected = run_cli(
            root, "dispatcher-evaluate", "--task-key", "task:dispatch",
            "--snapshot-json", json.dumps(snap), "--apply",
            "--expected-version", str(aborted["version"]))
        _, after_resurrection = run_cli(
            root, "status", "--task-key", "task:dispatch")
        check("aborted recovery snapshot is consumed and cannot resurrect",
              proc.returncode == 2
              and resurrected["reason"] == "LIFECYCLE_INVALID"
              and after_resurrection["version"] == aborted["version"]
              and after_resurrection["recovery_intent"]["state"] == "ABORTED",
              (proc.returncode, resurrected, after_resurrection, proc.stderr))

        second_snap = snapshot(
            "task:dispatch", own, "IDLE", source="event-after-first-abort")
        proc, second_applied = run_cli(
            root, "dispatcher-evaluate", "--task-key", "task:dispatch",
            "--snapshot-json", json.dumps(second_snap), "--apply",
            "--expected-version", str(aborted["version"]))
        proc2, second_aborted = run_cli(
            root, "recovery-abort", "--task-key", "task:dispatch",
            "--owner", "owner", "--nonce", second_applied.get("nonce", "missing"),
            "--expected-version", str(
                (second_applied.get("state") or {}).get("version", -999)))
        proc3, replayed_first = run_cli(
            root, "dispatcher-evaluate", "--task-key", "task:dispatch",
            "--snapshot-json", json.dumps(snap), "--apply",
            "--expected-version", str(second_aborted.get("version", -999)))
        check("older aborted event stays consumed after a later abort",
              proc.returncode == 0 and proc2.returncode == 0
              and proc3.returncode == 2
              and replayed_first["reason"] == "LIFECYCLE_INVALID",
              (second_applied, second_aborted, replayed_first))
        dispatch_hash = hashlib.sha256(b"task:dispatch").hexdigest()
        dispatch_path = root / "state" / f"{dispatch_hash}.json"
        original_dispatch = dispatch_path.read_text()
        truncated_history = json.loads(original_dispatch)
        truncated_history["recovery_history"] = (
            truncated_history["recovery_history"][1:])
        dispatch_path.write_text(json.dumps(truncated_history))
        proc, truncated = run_cli(
            root, "status", "--task-key", "task:dispatch")
        check("immutable recovery history cannot be truncated",
              proc.returncode == 2
              and truncated["reason"] == "LIFECYCLE_INVALID",
              (proc.returncode, truncated, proc.stderr))
        dispatch_path.write_text(original_dispatch)

        # A separate applied recovery completes through the ordinary, receipt-
        # verified handoff path and leaves one active successor.
        _, recovery = run_cli(
            root, "task-init", "--task-key", "task:recovery-complete",
            "--owner", "failed", "--surface", "codex",
            "--evidence-json", codex_evidence("failed"),
            "--expected-version", "-1")
        recovery_snap = snapshot(
            "task:recovery-complete",
            recovery["owners"]["failed"]["ownership_digest"],
            "CAPACITY_EXHAUSTED", source="event-recovery-complete",
            capacity="CONTEXT_EXHAUSTED", worker_id="failed")
        _, applied_recovery = run_cli(
            root, "dispatcher-evaluate", "--task-key", "task:recovery-complete",
            "--snapshot-json", json.dumps(recovery_snap), "--apply",
            "--expected-version", "0")
        _, recovery_offer = run_cli(
            root, "handoff-offer-create", "--task-key", "task:recovery-complete",
            "--predecessor", "failed", "--predecessor-surface", "codex",
            "--successor", "replacement", "--successor-surface", "codex",
            "--evidence-json", codex_evidence("failed"), "--generation", "1",
            "--expected-version", str(applied_recovery["state"]["version"]))
        _, recovery_declared = run_cli(
            root, "successor-declare", "--task-key", "task:recovery-complete",
            "--offer-digest", recovery_offer["offer_digest"],
            "--successor", "replacement",
            "--evidence-json", codex_evidence("replacement"),
            "--expected-version", str(recovery_offer["state"]["version"]))
        _, recovery_accepted = run_cli(
            root, "successor-accept", "--task-key", "task:recovery-complete",
            "--offer-digest", recovery_offer["offer_digest"],
            "--successor", "replacement",
            "--evidence-json", codex_evidence("replacement"),
            "--expected-version", str(recovery_declared["state"]["version"]))
        proc, recovery_terminal = run_cli(
            root, "predecessor-terminal", "--task-key", "task:recovery-complete",
            "--predecessor", "failed",
            "--evidence-json", codex_evidence("failed", status="terminal"),
            "--expected-version", str(recovery_accepted["state"]["version"]))
        active = [owner for owner in recovery_terminal["owners"].values()
                  if owner["state"] == "ACTIVE"]
        check("applied recovery completes with one verified active successor",
              proc.returncode == 0 and len(active) == 1
              and active[0]["id"] == "replacement"
              and recovery_terminal["recovery_intent"]["state"] == "COMPLETED",
              recovery_terminal)

        # A verified successor can be active while the predecessor still owes
        # terminal evidence.  Dispatcher recovery must not turn that owner into
        # DRAINING/PENDING when the prior handoff would reject both advertised
        # recovery exits (a next offer and an abort).
        _, adjacent = run_cli(
            root, "task-init", "--task-key", "task:adjacent",
            "--owner", "old", "--surface", "codex",
            "--evidence-json", codex_evidence("old"),
            "--expected-version", "-1")
        _, adjacent_offer = run_cli(
            root, "handoff-offer-create", "--task-key", "task:adjacent",
            "--predecessor", "old", "--predecessor-surface", "codex",
            "--successor", "new", "--successor-surface", "codex",
            "--evidence-json", codex_evidence("old"), "--generation", "1",
            "--expected-version", str(adjacent["version"]))
        _, adjacent_declared = run_cli(
            root, "successor-declare", "--task-key", "task:adjacent",
            "--offer-digest", adjacent_offer["offer_digest"],
            "--successor", "new", "--evidence-json", codex_evidence("new"),
            "--expected-version", str(adjacent_offer["state"]["version"]))
        _, adjacent_accepted = run_cli(
            root, "successor-accept", "--task-key", "task:adjacent",
            "--offer-digest", adjacent_offer["offer_digest"],
            "--successor", "new", "--evidence-json", codex_evidence("new"),
            "--expected-version", str(adjacent_declared["state"]["version"]))
        adjacent_snap = snapshot(
            "task:adjacent",
            adjacent_accepted["state"]["owners"]["new"]["ownership_digest"],
            "CAPACITY_EXHAUSTED", source="event-adjacent-capacity",
            capacity="CONTEXT_EXHAUSTED", worker_id="new", generation=1)
        proc, adjacent_recovery = run_cli(
            root, "dispatcher-evaluate", "--task-key", "task:adjacent",
            "--snapshot-json", json.dumps(adjacent_snap), "--apply",
            "--expected-version", str(adjacent_accepted["state"]["version"]))
        _, adjacent_after = run_cli(
            root, "status", "--task-key", "task:adjacent")
        check("verified current owner is not stranded by adjacent recovery",
              proc.returncode == 2
              and adjacent_recovery["reason"] == "LIFECYCLE_INVALID"
              and adjacent_after["version"] == adjacent_accepted["state"]["version"]
              and adjacent_after["active_owner"] == "new"
              and adjacent_after["owners"]["new"]["state"] == "ACTIVE"
              and adjacent_after["recovery_intent"] is None
              and adjacent_after["handoff"]["state"] == "TAKEOVER_VERIFIED",
              (adjacent_recovery, adjacent_after))
    finally:
        shutil.rmtree(root)


def immutable_publication_concurrency():
    print("immutable object publication concurrency")
    root = fresh_root()
    try:
        spec = importlib.util.spec_from_file_location(
            "context_handoff_publication_test", HOOK)
        gate = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(gate)
        manifest = {"state_directory": str(root / "state")}
        task_key = "task:publication-race"
        value = {"schema_version": 1, "task_key": task_key,
                 "kind": "same-object", "payload": "x" * 4096}
        object_digest = gate.digest(value)
        target = gate.object_path(
            task_key, "race", object_digest, manifest).resolve()
        created = threading.Event()
        release = threading.Event()
        real_open = gate.os.open
        results = []
        errors = []

        def delayed_final_open(path, flags, mode=0o777, *, dir_fd=None):
            fd = real_open(path, flags, mode, dir_fd=dir_fd)
            if (dir_fd is None and Path(path).resolve() == target
                    and flags & os.O_CREAT and flags & os.O_EXCL):
                created.set()
                release.wait(timeout=5)
            return fd

        def publish():
            try:
                results.append(gate.write_immutable(
                    task_key, "race", value, manifest))
            except Exception as exc:  # the regression is an escaping JSON error
                errors.append(exc)

        gate.os.open = delayed_final_open
        first = threading.Thread(target=publish)
        first.start()
        exposed_partial = created.wait(timeout=1)
        second = threading.Thread(target=publish)
        second.start()
        second.join(timeout=2)
        release.set()
        first.join(timeout=5)
        second.join(timeout=5)
        gate.os.open = real_open
        check("concurrent identical immutable writes publish complete bytes",
              not errors and len(results) == 2
              and set(results) == {object_digest}
              and json.loads(target.read_text()) == value,
              (exposed_partial, results,
               [f"{type(exc).__name__}: {exc}" for exc in errors]))

        claim_results = []
        start = threading.Barrier(3)

        def claim(task_key):
            start.wait()
            claim_results.append(run_cli(
                root, "task-init", "--task-key", task_key,
                "--owner", "one-native-owner", "--surface", "codex",
                "--evidence-json", codex_evidence("one-native-owner"),
                "--expected-version", "-1"))

        claim_a = threading.Thread(target=claim, args=("task:claim-a",))
        claim_b = threading.Thread(target=claim, args=("task:claim-b",))
        claim_a.start()
        claim_b.start()
        start.wait()
        claim_a.join(timeout=10)
        claim_b.join(timeout=10)
        returncodes = sorted(proc.returncode for proc, _ in claim_results)
        failures = [value for proc, value in claim_results if proc.returncode == 2]
        check("concurrent task admissions have one native-identity winner",
              returncodes == [0, 2] and len(failures) == 1
              and failures[0]["reason"] == "OWNERSHIP_MISMATCH",
              [(proc.returncode, value) for proc, value in claim_results])
    finally:
        shutil.rmtree(root)


def wrapper_and_historical_defect():
    print("wrapper equivalence and historical compaction defect")
    root = fresh_root()
    try:
        transcript = write_jsonl(root / "wrapped.jsonl",
                                 [usage_row(600_000, model="claude-opus-4-1")])
        bare_root = root / "bare"; bare_root.mkdir()
        wrapped_root = root / "wrapped"; wrapped_root.mkdir()
        shutil.copy2(MANIFEST, bare_root / "manifest.json")
        shutil.copy2(MANIFEST, wrapped_root / "manifest.json")
        bare, bare_out = run_hook(bare_root, "Stop", transcript,
                                  session="equivalent", wrapped=False)
        wrapped, wrapped_out = run_hook(wrapped_root, "Stop", transcript,
                                        session="equivalent", wrapped=True)
        check("bare and wrapped candidate return identical exit",
              bare.returncode == wrapped.returncode, (bare.returncode, wrapped.returncode))
        check("bare and wrapped stdout/stderr are byte-identical",
              bare.stdout == wrapped.stdout and bare.stderr == wrapped.stderr,
              (bare.stdout, wrapped.stdout, bare.stderr, wrapped.stderr))

        # Historical ordering: usage reached 1,013,084, compacted to 38,496,
        # then Stop ran. The installed gate reads only the last assistant usage
        # and silently allows; the candidate preserves compact preTokens.
        historical = write_jsonl(root / "historical.jsonl", [
            usage_row(990_267),
            {"payload": {"type": "compact_boundary", "preTokens": 1_013_084,
                         "postTokens": 38_496,
                         "timestamp": "2026-08-30T13:15:30.668Z"}},
            usage_row(38_496),
        ])
        payload = json.dumps({"hook_event_name": "Stop", "session_id": "selftest",
                              "transcript_path": str(historical)})
        # Pin the historical side of the regression to the approved base.  The
        # canonical checkout is mutable and becomes the candidate after merge;
        # treating it as the old implementation would make this test expire at
        # the moment the fix is installed.
        historical_hook = root / "legacy-context-handoff-gate.py"
        historical_hook.write_bytes(subprocess.check_output(
            ["git", "show", f"{APPROVED_BASE}:hooks/context-handoff-gate.py"],
            cwd=REPO))
        env = base_env(root, CARR_CONTEXT_WINDOW="1000000",
                       CARR_CONTEXT_STATE=str(root / "installed-state.json"))
        proc = subprocess.run(
            ["/usr/bin/env", "python3", str(RUNNER), str(historical_hook)],
            input=payload, text=True, capture_output=True, env=env,
            cwd=REPO)
        check("approved-base command shape reproduces silent postcompact allow",
              proc.returncode == 0 and proc.stdout.strip() == "", proc.stdout)
        candidate, out = run_hook(
            root, "Stop", historical, session="historical", wrapped=True,
            env_extra={"CARR_CONTEXT_WINDOW": "1000000"})
        why = reason(out)
        check("candidate exact wrapped shape catches historical preTokens",
              why and why["signal"]["used"] == 1_013_084, out)
    finally:
        shutil.rmtree(root)


def independent_review_regressions():
    print("independent review regressions")
    root = fresh_root("context-lifecycle-review-")
    try:
        transcript = write_jsonl(
            root / "review.jsonl",
            [usage_row(1_000, model="claude-opus-4-1")])

        # A callback may consult only its permanent native-identity binding.
        # Corruption or volume in unrelated task files must never block the
        # innocent task's Stop or turn each tool call into a whole-root scan.
        run_hook(root, "PostToolUse", transcript, session="innocent")
        run_hook(root, "PostToolUse", transcript, session="foreign")
        foreign = (root / "state"
                   / f"{hashlib.sha256(b'claude:foreign').hexdigest()}.json")
        foreign.write_text('{"schema_version":1,"task_key":"claude:foreign"}\n',
                           encoding="utf-8")
        for index in range(500):
            (root / "state" / f"foreign-{index:04d}.json").write_text(
                "{broken\n", encoding="utf-8")
        proc, out = run_hook(
            root, "Stop", transcript, session="innocent")
        check("foreign lifecycle corruption cannot block unrelated Claude Stop",
              proc.returncode == 0 and out is None,
              (proc.returncode, out, proc.stderr))

        # Immutable Codex evidence is admitted relative to the checkout that
        # captured it. Reading that task from another worktree must not
        # reinterpret the historical cwd against the reader's own checkout.
        repo_a = root / "repo-a"
        repo_b = root / "repo-b"
        for repo in (repo_a, repo_b):
            (repo / "hooks").mkdir(parents=True)
            shutil.copy2(HOOK, repo / "hooks/context-handoff-gate.py")
            shutil.copy2(REPO / "hooks/stop_latch.py",
                         repo / "hooks/stop_latch.py")
            (repo / "work").mkdir()
        hook_a = repo_a / "hooks/context-handoff-gate.py"
        hook_b = repo_b / "hooks/context-handoff-gate.py"
        env = base_env(root)

        def copied_cli(hook, *args):
            return subprocess.run(
                [sys.executable, str(hook), *map(str, args)], text=True,
                capture_output=True, env=env, cwd=hook.parent.parent)

        first = {
            "thread_id": "review-codex-a", "project_id": "review-project",
            "cwd": str(repo_a / "work"), "status": "active",
            "event_id": "review-event-a", "pinnedIndex": 1,
        }
        second = {
            "thread_id": "review-codex-b", "project_id": "review-project",
            "cwd": str(repo_a / "work"), "status": "active",
            "event_id": "review-event-b", "pinnedIndex": 1,
        }
        init = copied_cli(
            hook_a, "task-init", "--task-key", "review-portable",
            "--owner", "review-codex-a", "--surface", "codex",
            "--evidence-json", json.dumps(first), "--expected-version", "-1")
        offer = copied_cli(
            hook_a, "handoff-offer-create", "--task-key", "review-portable",
            "--predecessor", "review-codex-a", "--predecessor-surface", "codex",
            "--successor", "review-codex-b", "--successor-surface", "codex",
            "--generation", "1", "--evidence-json", json.dumps(first),
            "--expected-version", "0")
        offer_out = json.loads(offer.stdout) if offer.returncode == 0 else {}
        offer_digest = offer_out.get("offer_digest", "missing")
        declare = copied_cli(
            hook_a, "successor-declare", "--task-key", "review-portable",
            "--offer-digest", offer_digest, "--successor", "review-codex-b",
            "--evidence-json", json.dumps(second), "--expected-version", "1")
        accept = copied_cli(
            hook_a, "successor-accept", "--task-key", "review-portable",
            "--offer-digest", offer_digest, "--successor", "review-codex-b",
            "--evidence-json", json.dumps(second), "--expected-version", "2")
        portable = copied_cli(
            hook_b, "status", "--task-key", "review-portable")
        portable_out = (json.loads(portable.stdout)
                        if portable.stdout.strip() else {})
        check("accepted Codex lifecycle is readable from another worktree",
              all(item.returncode == 0
                  for item in (init, offer, declare, accept, portable))
              and portable_out.get("task_key") == "review-portable",
              [(item.returncode, item.stdout, item.stderr)
               for item in (init, offer, declare, accept, portable)])

        terminal_evidence = {
            "thread_id": "review-terminal", "project_id": "review-project",
            "cwd": str(repo_a / "work"), "status": "active",
            "event_id": "review-terminal-start", "pinnedIndex": 1,
        }
        terminal_init = copied_cli(
            hook_a, "task-init", "--task-key", "review-terminal-portable",
            "--owner", "review-terminal", "--surface", "codex",
            "--evidence-json", json.dumps(terminal_evidence),
            "--expected-version", "-1")
        terminal_native = dict(
            terminal_evidence, status="terminal",
            event_id="review-terminal-finish")
        terminal_done = copied_cli(
            hook_a, "task-terminal", "--task-key", "review-terminal-portable",
            "--owner", "review-terminal", "--evidence-json",
            json.dumps(terminal_native), "--expected-version", "0")
        terminal_portable = copied_cli(
            hook_b, "status", "--task-key", "review-terminal-portable")
        terminal_portable_out = (
            json.loads(terminal_portable.stdout)
            if terminal_portable.stdout.strip() else {})
        check("terminal Codex history is readable from another worktree",
              all(item.returncode == 0 for item in (
                  terminal_init, terminal_done, terminal_portable))
              and terminal_portable_out.get("task_status") == "TERMINAL",
              [(item.returncode, item.stdout, item.stderr)
               for item in (terminal_init, terminal_done, terminal_portable)])

        # An invalid controller-wired event is itself a Stop control error and
        # must fail closed instead of using the invalid spelling as a NOOP.
        high = write_jsonl(
            root / "review-high.jsonl",
            [usage_row(900_000, model="claude-opus-4-1")])
        proc, out = run_hook(
            root, "Stop", high, session="invalid-wired-event",
            env_extra={"CARR_CONTEXT_HOOK_EVENT": "stop"})
        why = reason(out)
        check("invalid wired hook event fails closed as Stop",
              proc.returncode == 0 and why
              and why["reason"] == "LIFECYCLE_INVALID",
              (proc.returncode, out, proc.stderr))

        # Python's decoder accepts bare Infinity. The bare Codex adapter must
        # still return the canonical refusal envelope, never a traceback.
        rollout = root / "nonfinite-rollout.jsonl"
        rollout.write_text(
            '{"type":"session_meta","payload":{"id":"review-inf"},'
            '"timestamp":"2026-08-31T00:00:00Z"}\n'
            '{"type":"response_item","payload":{"type":"function_call",'
            '"name":"Write","arguments":{"path":"/tmp/review"}},'
            '"total_tokens":Infinity,"timestamp":"2026-08-31T00:00:01Z"}\n',
            encoding="utf-8")
        proc, out = run_cli(root, "codex-observe", "--rollout", rollout)
        check("Codex nonfinite token input returns canonical refusal",
              proc.returncode == 2 and out
              and out.get("action") == "REFUSE"
              and out.get("reason") == "LIFECYCLE_INVALID",
              (proc.returncode, out, proc.stderr))
    finally:
        shutil.rmtree(root)


def exact_head_review_regressions():
    print("exact-head review regression classes")
    root = fresh_root("context-lifecycle-exact-head-")
    checkout_path = None
    try:
        transcript = write_jsonl(
            root / "native-session.jsonl",
            [usage_row(1_000, model="claude-opus-4-1")])

        # A malformed fresh Stop must refuse before it can derive an "unknown"
        # owner, create state, or claim a permanent native identity. Exercise
        # the installed wrapper shape as well as the bare candidate.
        missing_payload = {
            "hook_event_name": "Stop", "prompt_id": "missing-session",
            "transcript_path": str(transcript),
        }
        missing = subprocess.run(
            [sys.executable, str(RUNNER), str(HOOK)],
            input=json.dumps(missing_payload), text=True, capture_output=True,
            env=base_env(root), cwd=REPO)
        missing_out = json.loads(missing.stdout) if missing.stdout.strip() else None
        scalar, scalar_out = run_hook(
            root, "Stop", transcript, session=7)
        public_json = list((root / "state").glob("*.json"))
        bindings = list((root / "state/identity-bindings").glob("*.json"))
        check("missing or scalar Claude session refuses before publication",
              missing.returncode == 0 and reason(missing_out)
              and reason(missing_out)["reason"] == "LIFECYCLE_INVALID"
              and scalar.returncode == 0 and reason(scalar_out)
              and reason(scalar_out)["reason"] == "LIFECYCLE_INVALID"
              and not public_json and not bindings,
              (missing.returncode, missing_out, missing.stderr,
               scalar.returncode, scalar_out, scalar.stderr,
               public_json, bindings))

        # Every controller-native scalar used as identity or activation
        # evidence must be a nonempty string. A bool is not an integer pin.
        codex_base = {
            "thread_id": "strict-codex", "project_id": "project",
            "cwd": str(REPO), "status": "active", "event_id": "strict-event",
            "pinnedIndex": 1,
        }
        strict_results = []
        for index, (field, bad_value) in enumerate((
                ("thread_id", 8), ("project_id", 8), ("cwd", 8),
                ("status", 8), ("event_id", 8), ("pinnedIndex", True))):
            evidence = dict(codex_base)
            evidence[field] = bad_value
            strict_results.append(run_cli(
                root, "task-init", "--task-key", f"strict-codex-{index}",
                "--owner", "strict-codex", "--surface", "codex",
                "--evidence-json", json.dumps(evidence),
                "--expected-version", "-1"))
        claude_base = {
            "session_id": "strict-claude",
            "transcript_path": str(transcript),
            "controller_callback_id": "strict-callback", "status": "active",
        }
        for index, field in enumerate((
                "session_id", "transcript_path",
                "controller_callback_id", "status")):
            evidence = dict(claude_base)
            evidence[field] = 8
            strict_results.append(run_cli(
                root, "task-init", "--task-key", f"strict-claude-{index}",
                "--owner", "strict-claude", "--surface", "claude",
                "--evidence-json", json.dumps(evidence),
                "--expected-version", "-1"))
        check("native evidence rejects scalar coercion and boolean pins",
              all(proc.returncode == 2 and out
                  and out.get("reason") in {
                      "SUCCESSOR_SURFACE_INVALID", "SUCCESSOR_NOT_PINNED"}
                  for proc, out in strict_results),
              [(proc.returncode, out, proc.stderr)
               for proc, out in strict_results])

        # Declaration is the durable admission point for a successor identity.
        # It must require the same controller pin that acceptance requires, or
        # it can publish a declaration that no later acceptance can reproduce.
        _, pin_init = run_cli(
            root, "task-init", "--task-key", "declare-requires-pin",
            "--owner", "declare-old", "--surface", "codex",
            "--evidence-json", codex_evidence("declare-old"),
            "--expected-version", "-1")
        _, pin_offer = run_cli(
            root, "handoff-offer-create", "--task-key", "declare-requires-pin",
            "--predecessor", "declare-old", "--predecessor-surface", "codex",
            "--successor", "declare-new", "--successor-surface", "codex",
            "--generation", "1", "--evidence-json",
            codex_evidence("declare-old"), "--expected-version",
            str(pin_init["version"]))
        unpinned = json.loads(codex_evidence("declare-new"))
        unpinned.pop("pinnedIndex")
        identities_before = set(
            (root / "state/identity-bindings").glob("*.json"))
        pin_declare, pin_declare_out = run_cli(
            root, "successor-declare", "--task-key", "declare-requires-pin",
            "--offer-digest", pin_offer["offer_digest"],
            "--successor", "declare-new", "--evidence-json",
            json.dumps(unpinned), "--expected-version",
            str(pin_offer["state"]["version"]))
        _, pin_after = run_cli(
            root, "status", "--task-key", "declare-requires-pin")
        declaration_dir = (
            root / "state/objects"
            / hashlib.sha256(b"declare-requires-pin").hexdigest()
            / "declaration")
        check("successor declaration requires acceptance-grade pinning",
              pin_declare.returncode == 2 and pin_declare_out
              and pin_declare_out.get("reason") == "SUCCESSOR_NOT_PINNED"
              and pin_after["version"] == pin_offer["state"]["version"]
              and "declare-new" not in pin_after["owners"]
              and set((root / "state/identity-bindings").glob("*.json"))
              == identities_before
              and not list(declaration_dir.glob("*.json")),
              (pin_declare.returncode, pin_declare_out, pin_after,
               list(declaration_dir.glob("*.json"))))

        # Both terminal transitions retain the project and checkout admitted
        # for that owner generation. Status alone cannot terminate another
        # project or a different checkout under the same thread id.
        _, initial = run_cli(
            root, "task-init", "--task-key", "terminal-binding",
            "--owner", "terminal-old", "--surface", "codex",
            "--evidence-json", codex_evidence(
                "terminal-old", project="terminal-project"),
            "--expected-version", "-1")
        _, offer = run_cli(
            root, "handoff-offer-create", "--task-key", "terminal-binding",
            "--predecessor", "terminal-old", "--predecessor-surface", "codex",
            "--successor", "terminal-new", "--successor-surface", "codex",
            "--evidence-json", codex_evidence(
                "terminal-old", project="terminal-project"),
            "--generation", "1", "--expected-version", str(initial["version"]))
        _, declared = run_cli(
            root, "successor-declare", "--task-key", "terminal-binding",
            "--offer-digest", offer["offer_digest"],
            "--successor", "terminal-new",
            "--evidence-json", codex_evidence(
                "terminal-new", project="terminal-project"),
            "--expected-version", str(offer["state"]["version"]))
        _, accepted = run_cli(
            root, "successor-accept", "--task-key", "terminal-binding",
            "--offer-digest", offer["offer_digest"],
            "--successor", "terminal-new",
            "--evidence-json", codex_evidence(
                "terminal-new", project="terminal-project"),
            "--expected-version", str(declared["state"]["version"]))
        pred_bad, pred_bad_out = run_cli(
            root, "predecessor-terminal", "--task-key", "terminal-binding",
            "--predecessor", "terminal-old",
            "--evidence-json", codex_evidence(
                "terminal-old", status="terminal", project="other-project"),
            "--expected-version", str(accepted["state"]["version"]))
        _, after_pred_bad = run_cli(
            root, "status", "--task-key", "terminal-binding")
        terminal_dir = (root / "state/objects"
                        / hashlib.sha256(b"terminal-binding").hexdigest()
                        / "terminal")
        check("predecessor terminal refuses project drift without publication",
              pred_bad.returncode == 2
              and pred_bad_out.get("reason") == "OWNERSHIP_MISMATCH"
              and after_pred_bad["version"] == accepted["state"]["version"]
              and not list(terminal_dir.glob("*.json")),
              (pred_bad.returncode, pred_bad_out, after_pred_bad,
               list(terminal_dir.glob("*.json"))))
        _, pred_done = run_cli(
            root, "predecessor-terminal", "--task-key", "terminal-binding",
            "--predecessor", "terminal-old",
            "--evidence-json", codex_evidence(
                "terminal-old", status="terminal", project="terminal-project"),
            "--expected-version", str(accepted["state"]["version"]))
        terminal_count = len(list(terminal_dir.glob("*.json")))
        task_bad, task_bad_out = run_cli(
            root, "task-terminal", "--task-key", "terminal-binding",
            "--owner", "terminal-new",
            "--evidence-json", codex_evidence(
                "terminal-new", status="terminal", project="terminal-project",
                cwd=REPO / "hooks"),
            "--expected-version", str(pred_done["version"]))
        _, after_task_bad = run_cli(
            root, "status", "--task-key", "terminal-binding")
        check("task terminal refuses checkout drift without publication",
              task_bad.returncode == 2
              and task_bad_out.get("reason") == "OWNERSHIP_MISMATCH"
              and after_task_bad["version"] == pred_done["version"]
              and len(list(terminal_dir.glob("*.json"))) == terminal_count,
              (task_bad.returncode, task_bad_out, after_task_bad,
               list(terminal_dir.glob("*.json"))))

        # Historical evidence is canonicalized once at admission. Changing the
        # filesystem node at that stored path must not rewrite signed bytes.
        checkout_path = Path(tempfile.mkdtemp(
            prefix=".context-historical-", dir=REPO))
        _, historical_init = run_cli(
            root, "task-init", "--task-key", "historical-cwd-bytes",
            "--owner", "historical-owner", "--surface", "codex",
            "--evidence-json", codex_evidence(
                "historical-owner", cwd=checkout_path),
            "--expected-version", "-1")
        shutil.rmtree(checkout_path)
        os.symlink(REPO / "hooks", checkout_path, target_is_directory=True)
        historical_proc, historical_state = run_cli(
            root, "status", "--task-key", "historical-cwd-bytes")
        check("historical Codex cwd bytes survive later symlink retargeting",
              historical_proc.returncode == 0
              and historical_init["version"] == 0
              and historical_state["active_owner"] == "historical-owner",
              (historical_proc.returncode, historical_state,
               historical_proc.stderr))
        checkout_path.unlink()
        checkout_path = None

        # Exercise the failure-atomic protocol directly so faults can be placed
        # at exact filesystem boundaries that a subprocess cannot name.
        spec = importlib.util.spec_from_file_location(
            "context_handoff_transaction_test", HOOK)
        gate = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(gate)
        manifest = json.loads((root / "manifest.json").read_text())
        manifest["state_directory"] = str(root / "transaction-state")
        task_key = "transaction-atomicity"
        init_args = SimpleNamespace(
            task_key=task_key, owner="transaction-owner", surface="codex",
            evidence_json=codex_evidence("transaction-owner"),
            expected_version=-1)
        state_path = gate.state_file(task_key, manifest)
        binding_path = gate.identity_binding_path(
            "codex", "transaction-owner", manifest)
        real_atomic = gate.atomic_write
        real_atomic_at = gate._atomic_write_at

        def fail_state_write_at(parent_fd, name, value):
            if name == state_path.name:
                raise OSError("injected state publication failure")
            return real_atomic_at(parent_fd, name, value)

        gate._atomic_write_at = fail_state_write_at
        init_error = None
        try:
            gate.lifecycle_init(init_args, manifest)
        except Exception as exc:
            init_error = exc
        finally:
            gate._atomic_write_at = real_atomic_at
        object_root = gate.state_root(manifest) / "objects"
        check("failed state publication exposes no identity or immutable object",
              isinstance(init_error, OSError)
              and not state_path.exists() and not binding_path.exists()
              and not list(object_root.rglob("*.json")),
              (repr(init_error), state_path.exists(), binding_path.exists(),
               list(object_root.rglob("*.json"))))
        atomic_state = gate.lifecycle_init(init_args, manifest)

        def recovery_change(source_event_id):
            def change(current):
                event = {
                    "schema_version": 1, "task_key": task_key,
                    "source_event_id": source_event_id,
                    "snapshot_digest": gate.digest({"snapshot": source_event_id}),
                    "nonce": gate.digest({"nonce": source_event_id}),
                    "generation": current["generation"],
                    "failed_owner": current["active_owner"],
                    "cause": "RECOVERY_ERROR",
                    "previous_digest": (current["recovery_history"][-1]
                                        if current["recovery_history"] else None),
                }
                event_digest = gate.write_immutable(
                    task_key, "recovery-event", event, manifest)
                current["recovery_history"].append(event_digest)
                return current
            return change

        gate._atomic_write_at = fail_state_write_at
        recovery_error = None
        try:
            gate.mutate_state(
                task_key, atomic_state["version"],
                recovery_change("failed-state-event"), manifest)
        except Exception as exc:
            recovery_error = exc
        finally:
            gate._atomic_write_at = real_atomic_at
        recovery_dir = gate.object_path(
            task_key, "recovery-event", "unused", manifest).parent
        check("failed recovery CAS exposes no orphan recovery event",
              isinstance(recovery_error, OSError)
              and not list(recovery_dir.glob("*.json")),
              (repr(recovery_error), list(recovery_dir.glob("*.json"))))
        stable_state = gate.read_state(task_key, manifest)
        before_cas_objects = set(object_root.rglob("*.json"))
        cas_error = None
        try:
            gate.mutate_state(
                task_key, stable_state["version"] - 1,
                recovery_change("failed-cas-event"), manifest)
        except Exception as exc:
            cas_error = exc
        check("failed expected-version CAS stages no side artifacts",
              isinstance(cas_error, gate.LifecycleError)
              and set(object_root.rglob("*.json")) == before_cas_objects,
              (repr(cas_error), before_cas_objects,
               set(object_root.rglob("*.json"))))

        real_publish = gate._publish_transaction_artifact
        publish_error = None

        def interrupt_publish(*_args, **_kwargs):
            raise OSError("injected interruption after state commit")

        gate._publish_transaction_artifact = interrupt_publish
        try:
            gate.mutate_state(
                task_key, stable_state["version"],
                recovery_change("interrupted-event"), manifest)
        except Exception as exc:
            publish_error = exc
        finally:
            gate._publish_transaction_artifact = real_publish
        absent_before_recovery = not list(recovery_dir.glob("*.json"))
        recovered_state = gate.read_state(task_key, manifest)
        check("reader completes interrupted side publication from journal",
              isinstance(publish_error, OSError) and absent_before_recovery
              and len(recovered_state["recovery_history"]) == 1
              and len(list(recovery_dir.glob("*.json"))) == 1
              and not list((gate.state_root(manifest)
                            / "transactions").glob("*")),
              (repr(publish_error), absent_before_recovery,
               recovered_state, list(recovery_dir.glob("*.json"))))

        entered_publish = threading.Event()
        release_publish = threading.Event()
        mutation_errors = []
        reader_errors = []
        reader_results = []

        def delayed_publish(*args, **kwargs):
            entered_publish.set()
            release_publish.wait(timeout=5)
            return real_publish(*args, **kwargs)

        def mutate_with_delay():
            try:
                gate.mutate_state(
                    task_key, recovered_state["version"],
                    recovery_change("concurrent-event"), manifest)
            except Exception as exc:
                mutation_errors.append(exc)

        def read_during_commit():
            try:
                reader_results.append(gate.read_state(task_key, manifest))
            except Exception as exc:
                reader_errors.append(exc)

        gate._publish_transaction_artifact = delayed_publish
        writer = threading.Thread(target=mutate_with_delay)
        writer.start()
        reached_commit_window = entered_publish.wait(timeout=2)
        reader = threading.Thread(target=read_during_commit)
        reader.start()
        reader.join(timeout=0.2)
        reader_blocked = reader.is_alive()
        release_publish.set()
        writer.join(timeout=5)
        reader.join(timeout=5)
        gate._publish_transaction_artifact = real_publish
        check("concurrent reader never observes state without recovery object",
              reached_commit_window and reader_blocked
              and not mutation_errors and not reader_errors
              and len(reader_results) == 1
              and len(reader_results[0]["recovery_history"]) == 2
              and len(list(recovery_dir.glob("*.json"))) == 2,
              (reached_commit_window, reader_blocked,
               [repr(exc) for exc in mutation_errors],
               [repr(exc) for exc in reader_errors], reader_results,
               list(recovery_dir.glob("*.json"))))

        # The transaction directory entry must be durable before state can
        # publish, and the artifact directory/link must be durable before the
        # recovery journal is removed. Instrument the controller's directory
        # sync boundary rather than relying on filesystem timing.
        durable_events = []
        durable_error = None
        durable_state = gate.read_state(task_key, manifest)
        if hasattr(gate, "_atomic_write_at"):
            real_fsync_for_order = gate.os.fsync
            real_atomic_at_for_order = gate._atomic_write_at
            real_cleanup_at_for_order = gate._cleanup_transaction_at
            transactions_root = gate.state_root(manifest) / "transactions"
            transaction_identity = os.stat(transactions_root)
            recovery_identity = os.stat(recovery_dir)

            def record_fsync_for_order(fd):
                observed = os.fstat(fd)
                identity = (observed.st_dev, observed.st_ino)
                if identity == (transaction_identity.st_dev,
                                transaction_identity.st_ino):
                    durable_events.append(
                        ("fsync", gate._absolute(transactions_root)))
                if identity == (recovery_identity.st_dev,
                                recovery_identity.st_ino):
                    durable_events.append(
                        ("fsync", gate._absolute(recovery_dir)))
                return real_fsync_for_order(fd)

            def record_atomic_at_for_order(parent_fd, name, value):
                if name == state_path.name:
                    durable_events.append(
                        ("state", gate._absolute(state_path)))
                return real_atomic_at_for_order(parent_fd, name, value)

            def record_cleanup_at_for_order(parent_fd, name,
                                            directory_fd=None):
                durable_events.append(
                    ("cleanup", gate._absolute(transactions_root / name)))
                return real_cleanup_at_for_order(
                    parent_fd, name, directory_fd)

            gate.os.fsync = record_fsync_for_order
            gate._atomic_write_at = record_atomic_at_for_order
            gate._cleanup_transaction_at = record_cleanup_at_for_order
            try:
                gate.mutate_state(
                    task_key, durable_state["version"],
                    recovery_change("durability-event"), manifest)
            except Exception as exc:
                durable_error = exc
            finally:
                gate.os.fsync = real_fsync_for_order
                gate._atomic_write_at = real_atomic_at_for_order
                gate._cleanup_transaction_at = real_cleanup_at_for_order
        else:
            durable_error = AttributeError("_atomic_write_at is absent")
        lifecycle_root = gate.state_root(manifest)
        transactions_root = lifecycle_root / "transactions"
        transaction_syncs = [
            index for index, event in enumerate(durable_events)
            if event == ("fsync", gate._absolute(transactions_root))]
        artifact_syncs = [
            index for index, event in enumerate(durable_events)
            if event == ("fsync", gate._absolute(recovery_dir))]
        state_events = [
            index for index, event in enumerate(durable_events)
            if event[0] == "state"]
        cleanup_events = [
            index for index, event in enumerate(durable_events)
            if event[0] == "cleanup"]
        check("transaction durability syncs bracket state and cleanup",
              durable_error is None and transaction_syncs and state_events
              and artifact_syncs and cleanup_events
              and transaction_syncs[0] < state_events[0]
              and artifact_syncs[-1] < cleanup_events[-1],
              (repr(durable_error), durable_events))

        # Ordinary commits need the same descriptor confinement as crash
        # recovery. Rename the locked root immediately after journal durability
        # and install a replacement at the configured pathname. Publication and
        # cleanup must remain entirely under the originally pinned root.
        ordinary_state = gate.read_state(task_key, manifest)
        lifecycle_root = gate.state_root(manifest)
        ordinary_pinned_root = root / "ordinary-pinned-lifecycle-root"
        ordinary_replacement_root = root / "ordinary-replacement-lifecycle-root"
        real_atomic_at_for_swap = gate._atomic_write_at
        ordinary_swapped = False
        ordinary_error = None
        ordinary_result = None

        def swap_root_after_journal(parent_fd, name, value):
            nonlocal ordinary_swapped
            result = real_atomic_at_for_swap(parent_fd, name, value)
            if name == "journal.json" and not ordinary_swapped:
                lifecycle_root.rename(ordinary_pinned_root)
                ordinary_replacement_root.mkdir()
                os.symlink(
                    ordinary_replacement_root, lifecycle_root,
                    target_is_directory=True)
                ordinary_swapped = True
            return result

        gate._atomic_write_at = swap_root_after_journal
        try:
            ordinary_result = gate.mutate_state(
                task_key, ordinary_state["version"],
                recovery_change("ordinary-root-swap-event"), manifest)
        except Exception as exc:
            ordinary_error = exc
        finally:
            gate._atomic_write_at = real_atomic_at_for_swap
        pinned_state = ordinary_pinned_root / state_path.name
        replacement_state = ordinary_replacement_root / state_path.name
        pinned_objects = list(
            (ordinary_pinned_root / "objects").rglob("*.json"))
        pinned_journals = list(
            (ordinary_pinned_root / "transactions").glob("*/journal.json"))
        check("ordinary mutation stays under pinned root after journal publish",
              ordinary_swapped and ordinary_error is None
              and isinstance(ordinary_result, dict)
              and ordinary_result["version"] == ordinary_state["version"] + 1
              and pinned_state.exists() and not replacement_state.exists()
              and pinned_objects and not pinned_journals,
              (ordinary_swapped, repr(ordinary_error), ordinary_result,
               pinned_state.exists(), replacement_state.exists(),
               pinned_objects, pinned_journals))
        if lifecycle_root.is_symlink():
            lifecycle_root.unlink()
        if ordinary_replacement_root.exists():
            shutil.rmtree(ordinary_replacement_root)
        if ordinary_pinned_root.exists():
            ordinary_pinned_root.rename(lifecycle_root)

        # Keep the exact staged inode verified across publication. Swapping the
        # staged directory entry after its digest check but before link(2) must
        # neither publish the substituted symlink nor permit journal cleanup.
        staged_swap_directory = transactions_root / "staged-entry-swap"
        staged_swap_path = staged_swap_directory / "staged/value.json"
        staged_swap_value = {"schema_version": 1, "value": "verified-inode"}
        staged_swap_external = root / "staged-swap-external.json"
        staged_swap_final = lifecycle_root / "manual/staged-swap.json"
        gate.atomic_write(staged_swap_path, staged_swap_value)
        gate.atomic_write(
            staged_swap_directory / "journal.json",
            {"schema_version": 1, "value": "keeps-cleanup-honest"})
        gate.atomic_write(
            staged_swap_external,
            {"schema_version": 1, "value": "unverified-substitute"})
        staged_swap_artifact = {
            "final": "manual/staged-swap.json",
            "staged": "staged/value.json",
            "value_digest": gate.digest(staged_swap_value),
        }
        real_link_for_staged_swap = gate.os.link
        staged_entry_swapped = False
        staged_swap_error = None

        def swap_staged_entry_before_link(*args, **kwargs):
            nonlocal staged_entry_swapped
            if not staged_entry_swapped and args[:2] == (
                    "value.json", "staged-swap.json"):
                staged_swap_path.unlink()
                os.symlink(staged_swap_external, staged_swap_path)
                staged_entry_swapped = True
            return real_link_for_staged_swap(*args, **kwargs)

        gate.os.link = swap_staged_entry_before_link
        try:
            gate._publish_transaction_artifact(
                lifecycle_root, staged_swap_directory, staged_swap_artifact)
        except Exception as exc:
            staged_swap_error = exc
        finally:
            gate.os.link = real_link_for_staged_swap
        check("staged artifact identity is continuous across hard-link publish",
              staged_entry_swapped
              and isinstance(staged_swap_error, gate.LifecycleError)
              and not staged_swap_final.exists()
              and not staged_swap_final.is_symlink()
              and (staged_swap_directory / "journal.json").exists(),
              (staged_entry_swapped, repr(staged_swap_error),
               staged_swap_final.exists(), staged_swap_final.is_symlink(),
               (staged_swap_directory / "journal.json").exists()))
        if staged_swap_final.exists() or staged_swap_final.is_symlink():
            staged_swap_final.unlink()
        if staged_swap_directory.exists():
            shutil.rmtree(staged_swap_directory)
        staged_swap_external.unlink()

        # Matching immutable content is not enough: the directory entry must
        # still name the inode whose bytes were verified when publication
        # returns. A replacement after the matching read must be rejected.
        existing_value = {"schema_version": 1, "value": "matching-existing"}
        replacement_value = {
            "schema_version": 1, "value": "substituted-after-check"}
        existing_digest = gate.digest(existing_value)
        existing_final = gate.object_path(
            task_key, "matching-existing", existing_digest, manifest)
        gate.atomic_write(existing_final, existing_value)
        real_load_for_existing_swap = gate._load_json_at
        existing_swapped = False
        existing_error = None

        def swap_matching_existing_after_load(parent_fd, name):
            nonlocal existing_swapped
            exists, value = real_load_for_existing_swap(parent_fd, name)
            if (not existing_swapped and name == existing_final.name
                    and value == existing_value):
                gate.atomic_write(existing_final, replacement_value)
                existing_swapped = True
            return exists, value

        gate._load_json_at = swap_matching_existing_after_load
        try:
            gate.write_immutable(
                task_key, "matching-existing", existing_value, manifest)
        except Exception as exc:
            existing_error = exc
        finally:
            gate._load_json_at = real_load_for_existing_swap
        check("matching immutable publication retains verified entry identity",
              existing_swapped
              and isinstance(existing_error, gate.LifecycleError)
              and json.loads(existing_final.read_text(encoding="utf-8"))
              == replacement_value,
              (existing_swapped, repr(existing_error),
               json.loads(existing_final.read_text(encoding="utf-8"))))

        # Recovery must not clear its journal or staged authority after the
        # same matching-destination substitution. The committed state makes
        # this a real replay path rather than a direct helper-only probe.
        collision_state = gate.read_state(task_key, manifest)
        collision_directory = transactions_root / "matching-entry-swap"
        collision_staged = collision_directory / "staged/value.json"
        collision_final = lifecycle_root / "manual/matching-entry-swap.json"
        collision_value = {
            "schema_version": 1, "value": "recoverable-authority"}
        collision_replacement = {
            "schema_version": 1, "value": "substituted-before-cleanup"}
        gate.atomic_write(collision_staged, collision_value)
        gate.atomic_write(collision_final, collision_value)
        gate.atomic_write(collision_directory / "journal.json", {
            "schema_version": 1,
            "task_key": task_key,
            "state_path": str(gate._absolute(state_path)),
            "target_state_digest": gate.digest(collision_state),
            "artifacts": [{
                "final": "manual/matching-entry-swap.json",
                "staged": "staged/value.json",
                "value_digest": gate.digest(collision_value),
            }],
        })
        real_load_for_recovery_swap = gate._load_json_at
        recovery_entry_swapped = False
        recovery_collision_error = None

        def swap_recovery_match_after_load(parent_fd, name):
            nonlocal recovery_entry_swapped
            exists, value = real_load_for_recovery_swap(parent_fd, name)
            if (not recovery_entry_swapped and name == collision_final.name
                    and value == collision_value):
                gate.atomic_write(collision_final, collision_replacement)
                recovery_entry_swapped = True
            return exists, value

        gate._load_json_at = swap_recovery_match_after_load
        try:
            gate.read_state(task_key, manifest)
        except Exception as exc:
            recovery_collision_error = exc
        finally:
            gate._load_json_at = real_load_for_recovery_swap
        check("recovery retains journal after matching entry substitution",
              recovery_entry_swapped
              and isinstance(recovery_collision_error, gate.LifecycleError)
              and (collision_directory / "journal.json").exists()
              and collision_staged.exists()
              and json.loads(collision_final.read_text(encoding="utf-8"))
              == collision_replacement,
              (recovery_entry_swapped, repr(recovery_collision_error),
               (collision_directory / "journal.json").exists(),
               collision_staged.exists(),
               json.loads(collision_final.read_text(encoding="utf-8"))))
        if collision_directory.exists():
            shutil.rmtree(collision_directory)
        if collision_final.exists():
            collision_final.unlink()

        # A crash can leave the final hard link present while its parent entry
        # is not yet durable. Recovery must sync that matching destination
        # before it removes the only journal capable of replaying the link.
        matching_directory = transactions_root / "matching-destination-sync"
        matching_directory.mkdir(parents=True)
        matching_value = {"schema_version": 1, "value": "matching-link"}
        matching_final = lifecycle_root / "manual" / "matching-link.json"
        gate.atomic_write(matching_final, matching_value)
        matching_artifact = {
            "final": "manual/matching-link.json",
            "staged": "staged/already-consumed.json",
            "value_digest": gate.digest(matching_value),
        }
        matching_parent = os.stat(matching_final.parent)
        matching_syncs = []
        matching_error = None
        real_fsync = gate.os.fsync

        def record_matching_fsync(fd):
            observed = os.fstat(fd)
            if ((observed.st_dev, observed.st_ino)
                    == (matching_parent.st_dev, matching_parent.st_ino)):
                matching_syncs.append(fd)
            return real_fsync(fd)

        gate.os.fsync = record_matching_fsync
        try:
            gate._publish_transaction_artifact(
                lifecycle_root, matching_directory, matching_artifact)
        except Exception as exc:
            matching_error = exc
        finally:
            gate.os.fsync = real_fsync
        check("matching recovery destination is synced before journal cleanup",
              matching_error is None and matching_syncs,
              (repr(matching_error), matching_syncs))
        shutil.rmtree(matching_directory)

        # Recovery must tolerate the exact cleanup crash left by the old order:
        # the public artifact is already correct, the staged link is gone, and
        # the committed journal still exists.
        recovery_state = gate.read_state(task_key, manifest)
        lifecycle_root = gate.state_root(manifest)
        wedge_directory = lifecycle_root / "transactions" / "cleanup-wedge"
        wedge_directory.mkdir(parents=True)
        public_value = {"schema_version": 1, "value": "already-published"}
        public_final = lifecycle_root / "manual" / "already-published.json"
        gate.atomic_write(public_final, public_value)
        gate.atomic_write(wedge_directory / "journal.json", {
            "schema_version": 1,
            "task_key": task_key,
            "state_path": str(gate._absolute(state_path)),
            "target_state_digest": gate.digest(recovery_state),
            "artifacts": [{
                "final": "manual/already-published.json",
                "staged": "staged/missing.json",
                "value_digest": gate.digest(public_value),
            }],
        })
        wedge_error = None
        wedge_recovered = None
        try:
            wedge_recovered = gate.read_state(task_key, manifest)
        except Exception as exc:
            wedge_error = exc
        check("recovery accepts correct public artifact after cleanup crash",
              wedge_error is None and wedge_recovered == recovery_state
              and not wedge_directory.exists(),
              (repr(wedge_error), wedge_recovered,
               wedge_directory.exists()))
        if wedge_directory.exists():
            shutil.rmtree(wedge_directory)

        # A committed journal is untrusted input after a crash. A symlinked
        # descendant must not redirect publication outside the pinned lifecycle
        # root, even when the staged bytes and digest are otherwise valid.
        symlink_directory = lifecycle_root / "transactions" / "symlink-escape"
        staged_path = symlink_directory / "staged/value.json"
        escape_value = {"schema_version": 1, "value": "must-not-escape"}
        gate.atomic_write(staged_path, escape_value)
        outside = root / "outside-publication"
        outside.mkdir()
        redirect = lifecycle_root / "artifact-link"
        os.symlink(outside, redirect, target_is_directory=True)
        gate.atomic_write(symlink_directory / "journal.json", {
            "schema_version": 1,
            "task_key": task_key,
            "state_path": str(gate._absolute(state_path)),
            "target_state_digest": gate.digest(recovery_state),
            "artifacts": [{
                "final": "artifact-link/escaped.json",
                "staged": "staged/value.json",
                "value_digest": gate.digest(escape_value),
            }],
        })
        symlink_error = None
        try:
            gate.read_state(task_key, manifest)
        except Exception as exc:
            symlink_error = exc
        escaped = outside / "escaped.json"
        check("transaction recovery rejects symlink redirection",
              isinstance(symlink_error, gate.LifecycleError)
              and not escaped.exists(),
              (repr(symlink_error), escaped.exists()))
        if symlink_directory.exists():
            shutil.rmtree(symlink_directory)
        redirect.unlink()
        shutil.rmtree(outside)

        # Pinning only the initial list operation is insufficient. If the root
        # path is replaced after listing, every journal read, state read,
        # publication, and cleanup operation must continue through the original
        # descriptors rather than reopening the substituted ancestor.
        swap_directory = lifecycle_root / "transactions" / "root-swap"
        swap_staged = swap_directory / "staged/value.json"
        swap_value = {"schema_version": 1, "value": "pinned-root-only"}
        gate.atomic_write(swap_staged, swap_value)
        gate.atomic_write(swap_directory / "journal.json", {
            "schema_version": 1,
            "task_key": task_key,
            "state_path": str(gate._absolute(state_path)),
            "target_state_digest": gate.digest(recovery_state),
            "artifacts": [{
                "final": "swap-target/pinned.json",
                "staged": "staged/value.json",
                "value_digest": gate.digest(swap_value),
            }],
        })
        replacement_root = root / "replacement-lifecycle-root"
        pinned_root = root / "pinned-lifecycle-root"
        shutil.copytree(lifecycle_root, replacement_root, symlinks=True)
        real_listdir = gate.os.listdir
        swapped_root = False
        swap_error = None

        def swap_root_after_list(path):
            nonlocal swapped_root
            names = real_listdir(path)
            if not swapped_root and isinstance(path, int):
                lifecycle_root.rename(pinned_root)
                os.symlink(
                    replacement_root, lifecycle_root,
                    target_is_directory=True)
                swapped_root = True
            return names

        gate.os.listdir = swap_root_after_list
        try:
            gate._recover_transactions_unlocked(lifecycle_root, manifest)
        except Exception as exc:
            swap_error = exc
        finally:
            gate.os.listdir = real_listdir
        pinned_created = pinned_root / "swap-target/pinned.json"
        escaped_created = replacement_root / "swap-target/pinned.json"
        check("recovery keeps lifecycle root descriptors pinned end to end",
              swapped_root and swap_error is None
              and pinned_created.exists() and not escaped_created.exists(),
              (swapped_root, repr(swap_error), pinned_created.exists(),
               escaped_created.exists()))
        if lifecycle_root.is_symlink():
            lifecycle_root.unlink()
        if pinned_root.exists():
            pinned_root.rename(lifecycle_root)
        if replacement_root.exists():
            shutil.rmtree(replacement_root)

        # Guarded reads must use the root descriptor already holding the
        # lifecycle lock. A replacement pathname may contain a self-consistent
        # forged state, or omit the original identity/object bytes entirely;
        # neither may redirect a nested reader away from the pinned tree.
        guarded_state = gate.read_state(task_key, manifest)
        guarded_binding = gate.read_identity_binding(
            "codex", "transaction-owner", manifest)
        guarded_object_digest = guarded_state["recovery_history"][0]
        guarded_object = gate.read_object(
            task_key, "recovery-event", guarded_object_digest, manifest)
        forged_state = json.loads(json.dumps(guarded_state))
        forged_state["signal"]["highwater"] = 199_999
        read_replacement_root = root / "read-replacement-lifecycle-root"
        read_pinned_root = root / "read-pinned-lifecycle-root"
        read_replacement_root.mkdir()
        gate.atomic_write(
            read_replacement_root / state_path.name, forged_state)
        guarded_read_error = None
        observed_state = observed_binding = observed_object = None
        try:
            with gate.lifecycle_guard(manifest):
                lifecycle_root.rename(read_pinned_root)
                os.symlink(
                    read_replacement_root, lifecycle_root,
                    target_is_directory=True)
                observed_state = gate.read_state(task_key, manifest)
                observed_binding = gate.read_identity_binding(
                    "codex", "transaction-owner", manifest)
                observed_object = gate.read_object(
                    task_key, "recovery-event", guarded_object_digest, manifest)
        except Exception as exc:
            guarded_read_error = exc
        finally:
            if lifecycle_root.is_symlink():
                lifecycle_root.unlink()
            if read_pinned_root.exists():
                read_pinned_root.rename(lifecycle_root)
            if read_replacement_root.exists():
                shutil.rmtree(read_replacement_root)
        check("state object and identity reads stay under pinned guard root",
              guarded_read_error is None
              and observed_state == guarded_state
              and observed_binding == guarded_binding
              and observed_object == guarded_object,
              (repr(guarded_read_error), observed_state, guarded_state,
               observed_binding, guarded_binding,
               observed_object, guarded_object))
    finally:
        if checkout_path is not None:
            if checkout_path.is_symlink():
                checkout_path.unlink()
            elif checkout_path.exists():
                shutil.rmtree(checkout_path)
        shutil.rmtree(root)


def static_contract_cases():
    print("protected contract and explicit exclusions")
    windows = json.loads(MANIFEST.read_text())["surface_policies"]["claude"]["model_windows"]
    check("Claude model windows bind Sonnet 5.5 and keep Sonnet 5 for old records",
          windows.get("claude-sonnet-5-5") == 1_000_000
          and windows.get("claude-sonnet-5") == 1_000_000, windows)
    hooks = json.loads((REPO / "ops/config/hooks.json").read_text())
    stop = json.dumps(hooks.get("Stop", []))
    post = json.dumps(hooks.get("PostToolUse", []))
    compact = json.dumps(hooks.get("PreCompact", []))
    check("Claude wiring has context gate on PostToolUse",
          "context-handoff-gate.py" in post
          and "CARR_CONTEXT_HOOK_EVENT=PostToolUse" in post, post)
    check("Claude wiring has context gate on PreCompact",
          "context-handoff-gate.py" in compact
          and "CARR_CONTEXT_HOOK_EVENT=PreCompact" in compact, compact)
    check("Claude wiring keeps context gate on Stop",
          "context-handoff-gate.py" in stop
          and "CARR_CONTEXT_HOOK_EVENT=Stop" in stop, stop)
    manifest = json.loads(MANIFEST.read_text())
    check("manifest makes Codex bare-CLI limitation explicit",
          manifest["surface_contracts"]["codex"]["adapter"] == "bare_cli"
          and manifest["surface_contracts"]["codex"]["native_stop_hook"] is False
          and manifest["surface_contracts"]["codex"]["evidence_trust_boundary"]
          == "controller_capture",
          manifest["surface_contracts"]["codex"])
    check("heartbeat cadence is at most 60 seconds",
          manifest["dispatcher"]["heartbeat_max_seconds"] <= 60,
          manifest["dispatcher"]["heartbeat_max_seconds"])

    # These contracts were frozen unchanged by the reviewed plan.
    base = subprocess.check_output(
        ["git", "show", "01c3977580e8d9d490380f6c2135d1c4d7d20fd7:"
         "hooks/stop_latch.py"], cwd=REPO)
    check("stop_latch.py is byte-identical to approved base",
          base == (REPO / "hooks/stop_latch.py").read_bytes())
    # ops/config/rule-enforcement-map.json was in this frozen list to prove the
    # context-handoff work did not disturb it. It legitimately CHANGED on
    # 2026-09-01 under a SEPARATE authority — Joe's ruling 7f48abf6 reinstating
    # the canonical_edit control (Repo Hygiene Program R02) — so it is no longer
    # byte-identical to the context-handoff baseline, and asserting otherwise
    # would be false. It is dropped from this list, not silently: the map is now
    # covered by gate-integrity's contract hash (re-blessed in the same R02
    # commit) and by control-catalog-parity-gate. stop_latch.py,
    # ops/stop_latch-selftest.py stays frozen. Codex hook continuity additions
    # are checked below against the historical PreToolUse/Stop groups.
    for path in ("ops/stop_latch-selftest.py",):
        base = subprocess.check_output(
            ["git", "show",
             f"01c3977580e8d9d490380f6c2135d1c4d7d20fd7:{path}"], cwd=REPO)
        check(path + " is explicitly unchanged", base == (REPO / path).read_bytes())
    old_codex = json.loads(subprocess.check_output(
        ["git", "show", "01c3977580e8d9d490380f6c2135d1c4d7d20fd7:ops/config/codex-hooks.json"],
        cwd=REPO))
    current_codex = json.loads((REPO / "ops/config/codex-hooks.json").read_text())
    old_hooks = old_codex.get("hooks", {})
    current_hooks = current_codex.get("hooks", {})
    normalized_pretool = json.loads(json.dumps(current_hooks.get("PreToolUse")))
    expected_boot = {
        "matcher": ".*",
        "hooks": [{
            "type": "command",
            "command": "{{REPO}}/.venv/bin/python {{REPO}}/hooks/hook-meter-run.py {{REPO}}/hooks/rule-boot-gate.py",
            "timeout": 5,
            "additionalContextLimit": 5000,
        }],
    }
    actual_boot = normalized_pretool.pop(0) if normalized_pretool else None
    check("Codex new boot hold group is exact", actual_boot == expected_boot)
    old_pretool = old_hooks.get("PreToolUse", [])
    if normalized_pretool and old_pretool:
        expected_route = json.loads(json.dumps(old_pretool[0]))
        expected_route["matcher"] = ".*"
        expected_route["hooks"][0]["additionalContextLimit"] = 5000
        check("Codex expanded rule route group is exact",
              normalized_pretool[0] == expected_route)
        normalized_pretool[0] = json.loads(json.dumps(old_pretool[0]))
    else:
        check("Codex expanded rule route group is exact", False)
    allowed_exec_matchers = {
        "^(Bash|exec_command|functions\\.exec)$": "^(Bash|functions\\.exec)$",
        "^(Bash|exec_command|Read|Grep|Glob|WebFetch|apply_patch|functions\\.(exec|apply_patch)|mcp__(carr|carr_records)__.*)$":
            "^(Bash|Read|Grep|Glob|WebFetch|apply_patch|functions\\.(exec|apply_patch)|mcp__(carr|carr_records)__.*)$",
    }
    for group in normalized_pretool:
        group["matcher"] = allowed_exec_matchers.get(group.get("matcher"), group.get("matcher"))
    # The rule-pack drift gate moved onto the repo interpreter through the hook
    # meter on 2026-09-23 (ops/rule-delivery-cutover.py HOOK_TEMPLATE). The
    # historical Stop group is otherwise unchanged; compare it with that one
    # command spelled the historical way.
    normalized_stop = json.loads(json.dumps(current_hooks.get("Stop")))
    for group in normalized_stop or []:
        for hook in group.get("hooks", []):
            if hook.get("command") == "{{REPO}}/.venv/bin/python {{REPO}}/hooks/hook-meter-run.py {{REPO}}/hooks/rule-pack-drift-gate.py":
                hook["command"] = "/usr/bin/env python3 {{REPO}}/hooks/rule-pack-drift-gate.py"
    check("Codex historical PreToolUse/Stop groups preserved",
          normalized_pretool == old_hooks.get("PreToolUse")
          and normalized_stop == old_hooks.get("Stop"),
          {"historical": old_hooks, "current": {k: current_hooks.get(k) for k in ("PreToolUse", "Stop")}})
    check("Codex continuity lifecycle groups registered",
          all(event in current_hooks for event in ("PreCompact", "PostCompact", "SessionStart", "UserPromptSubmit")),
          sorted(current_hooks))


def main():
    threshold_and_window_cases()
    fallback_and_hook_sequence()
    lifecycle_cas_and_tamper()
    rollout_resolver_cases()
    dispatcher_cases()
    immutable_publication_concurrency()
    wrapper_and_historical_defect()
    independent_review_regressions()
    exact_head_review_regressions()
    static_contract_cases()
    print()
    if FAIL:
        print(f"FAIL {len(FAIL)} check(s): {', '.join(FAIL)}")
        return 1
    print(f"OK {len(PASS)} checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
