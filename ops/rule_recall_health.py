import fcntl
import json
import os
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path

from lib.rule_recall import delivery_counts

ACTION = ("on breach: open/update one dedup loop · owner orchestrator · remediation inspect "
          "each listed rule's event, adapter and full-text receipt; repair route or propose "
          "rewrite/retirement; retain boot · verify per-rule benchmark and real-turn recall "
          "100% · auto-clear only when every active rule has a full-text delivery in 14 days")


def run_verb(root, name, payload):
    result = subprocess.run([str(root / "run.sh"), "call", name, json.dumps(payload)],
                            cwd=root, capture_output=True, text=True, timeout=35)
    if result.returncode:
        raise RuntimeError(f"{name} failed ({result.returncode})")
    answer = json.loads(result.stdout[result.stdout.find("{"):])
    if answer.get("error") or answer.get("ok") is False:
        raise RuntimeError(f"{name} did not confirm")
    return answer


UNAVAILABLE_BODY = ("Rule delivery evidence unavailable: no dated full-text receipts in 14 days, so "
                    "delivery silence cannot be measured. " + ACTION + ". Clears only when dated "
                    "full-text receipts are read again.")
BLOCKER_DETAIL = ("The health runner cannot repair installed client routes or perform a source PR; "
                  "the orchestrator must execute that delivery")


def check_recall(rows, rules, state_path, run, *, now=None):
    """Report recall and keep one loop per warning in step with it.

    Each warning ("silence", "unavailable") owns at most one loop. A write is
    saved as `pending` before it is sent and replayed verbatim (same key, same
    request) until its answer is applied or a rejected write is reconciled
    against the loop's current state. A lost response or a failed state save
    never mints a second loop or reuses a key for a changed request."""
    now = now or datetime.now(timezone.utc).isoformat()
    measured = delivery_counts(rows, rules, now, 14)
    zero = measured["zero"] if measured["readable"] else None
    subjects = "; ".join(f"{rules[rid]} ({rid})" for rid in zero or ())
    if zero is None:
        line = f"UNAVAILABLE rule recall — no dated full-text receipts in 14 days; warning retained · {ACTION}"
    else:
        line = (f"{'WARN' if zero else 'OK'} rule recall — {len(zero)}/{len(rules)} active rules "
                f"with zero observed full-text deliveries in 14 days" + (f": {subjects}" if zero else "")
                + f" · {ACTION}")
    path = Path(state_path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with Path(str(path) + ".lock").open("a+") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            state = json.loads(path.read_text()) if path.exists() else {}
            if state.get("pending"):
                _settle(state, run, path)
            silence_body = ("Rule delivery silence: " + subjects + ". " + ACTION + ". A zero is observed "
                            "silence, not authority to retire a rule or proof it had a binding moment.")
            for warning, wanted, body, outcome in (
                    ("unavailable", zero is None, UNAVAILABLE_BODY,
                     "Auto-cleared: dated full-text delivery receipts are readable again."),
                    ("silence", bool(zero), silence_body,
                     "Auto-cleared: every active rule has a dated full-text delivery in the last 14 days.")):
                loop = state.get(warning)
                if wanted and not loop:
                    request = ("add-loop", {"kind": "open_loop", "domain": "system", "owner": "claude",
                               "marker": "none", "body": body, "blocker": "capability",
                               "blocker_detail": BLOCKER_DETAIL})
                elif wanted and warning == "silence" and loop.get("zero") != zero:
                    request = ("update-loop", {"loop_id": loop["loop_id"],
                               "base_version": _read(run, loop["loop_id"])["version"], "body": body})
                elif loop and zero is not None and not wanted:
                    request = ("close-loop", {"loop_id": loop["loop_id"],
                               "base_version": _read(run, loop["loop_id"])["version"],
                               "resolution": "done", "outcome": outcome})
                else:
                    continue
                name, args = request
                state["pending"] = {"warning": warning, "verb": name, "zero": zero,
                                    "args": {**args, "idempotency_key": str(uuid.uuid4())}}
                _save(path, state)
                _settle(state, run, path)
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError) as exc:
        line += f" · loop action FAILED ({type(exc).__name__})"
    return line


def _read(run, loop_id):
    row = run("read-loop", {"loop_id": loop_id})
    row = row.get("loop", row)
    if row.get("loop_id") != loop_id or type(row.get("version")) is not int:
        raise RuntimeError("loop version unavailable")
    return row


def _settle(state, run, path):
    """Send the saved write (a replay returns the stored answer), apply its
    result to the state, and save. Reconcile rejected versioned writes so the
    current measurement can choose a fresh request after another writer acts."""
    pending = state["pending"]
    warning, name, args = pending["warning"], pending["verb"], pending["args"]
    try:
        answer = run(name, dict(args))
        if answer.get("ok") is not True:
            raise RuntimeError(f"{name} did not confirm write")
    except RuntimeError:
        if name not in ("update-loop", "close-loop"):
            raise
        loop = _read(run, args["loop_id"])
        if loop.get("status") in ("done", "dropped"):
            state.pop(warning, None)
        elif loop.get("status") != "open" or loop["version"] == args["base_version"]:
            raise
        del state["pending"]
        _save(path, state)
        return
    if name == "add-loop":
        if not answer.get("loop_id"):
            raise RuntimeError("missing loop id")
        state[warning] = {"loop_id": answer["loop_id"]}
    if name == "close-loop":
        state.pop(warning, None)
    elif warning == "silence":
        state[warning]["zero"] = pending["zero"]
    del state["pending"]
    _save(path, state)


def _save(path, state):
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(state) + "\n")
    os.replace(temp, path)


def check_local(root):
    """Use installed ledgers; do not export statements, inputs or transcript bodies."""
    root = Path(root)
    live = run_verb(root, "standing-context", {"detail": "full"})
    rules = {r["id"]: r["statement"].split(".", 1)[0][:120]
             for r in live["shared_rules"] + live["personal_rules"]}
    logs = [Path.home() / ".config/carr/claude-rule-delivery.jsonl"]
    for name in ("rule-trigger-delivery", "rule-route-delivery", "rule-boot-delivery"):
        logs.extend((root / "out").glob(name + "*.jsonl*"))
    rows = []
    for path in logs:
        if not path.is_file():
            continue
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                try:
                    row = json.loads(line)
                    if isinstance(row, dict):
                        rows.append(row)
                except ValueError:
                    continue
    return check_recall(rows, rules, root / "out/rule-recall-loop.json",
                        lambda name, args: run_verb(root, name, args))
