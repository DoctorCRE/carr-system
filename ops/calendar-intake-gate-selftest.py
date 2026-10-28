#!/usr/bin/env python3
"""Unit checks for the calendar new-attendee completion gate."""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "tools" / "calendar-intake-gate.py"
spec = importlib.util.spec_from_file_location("calendar_intake_gate", SCRIPT)
assert spec and spec.loader
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)

failed: list[str] = []


import pathlib, sys  # noqa: E401
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))  # repo root, for lib/
from lib.gate_check import Check  # noqa: E402

check = Check(
    ok="  ok   {label}",
    fail="  FAIL {label}",
    detail=" {detail}",
    detail_on_ok=True,
    failures=failed,
)


proposals = {"unknown": [{"email": "new@example.com"}]}
complete = {"candidates": {"new@example.com": {
    "mail_search": {"status": "searched", "source": "local Mail search: new@example.com"},
    "research": {"status": "searched", "source": "https://example.com/team"},
    "record": {"status": "created", "ref": "V-CPA-999"},
}}}

print("calendar intake gate")
check("complete three-receipt intake passes", gate.unresolved(proposals, complete) == {})
gaps = gate.unresolved(proposals, {"candidates": {"new@example.com": {
    "mail_search": {"status": "searched", "source": "local mail"},
    "research": {"status": "searched", "source": "https://example.com"},
}}})
check("missing canonical record refuses", gaps == {"new@example.com": ["record"]}, repr(gaps))
gaps = gate.unresolved(proposals, {"candidates": {"new@example.com": {
    "mail_search": {"status": "searched", "source": ""},
    "research": {"status": "searched", "source": "https://example.com"},
    "record": {"status": "ambiguous", "ref": "P-0001"},
}}})
check("empty mail-search and ambiguous identity both refuse",
      gaps == {"new@example.com": ["mail_search", "record"]}, repr(gaps))
gaps = gate.unresolved(proposals, {"candidates": {"new@example.com": {
    "mail_search": {"status": "searched", "source": "local mail"},
    "research": {"status": "searched", "source": "https://example.com"},
    "record": {"status": "ambiguous", "ref": "P-0001"},
}}})
check("ambiguous identity remains pending", gaps == {"new@example.com": ["record"]}, repr(gaps))

with tempfile.TemporaryDirectory() as raw:
    root = Path(raw)
    proposal_path, evidence_path = root / "proposals.json", root / "evidence.json"
    proposal_path.write_text(json.dumps(proposals))
    evidence_path.write_text(json.dumps(complete))
    p = subprocess.run([sys.executable, str(SCRIPT), "--proposals", str(proposal_path),
                        "--evidence", str(evidence_path)], text=True, capture_output=True)
    check("CLI accepts complete intake", p.returncode == 0, p.stdout + p.stderr)
    evidence_path.unlink()
    p = subprocess.run([sys.executable, str(SCRIPT), "--proposals", str(proposal_path),
                        "--evidence", str(evidence_path)], text=True, capture_output=True)
    check("CLI refuses missing evidence file", p.returncode == 78 and "REFUSE" in p.stderr,
          p.stdout + p.stderr)
    p = subprocess.run([sys.executable, str(SCRIPT), "--proposals", str(proposal_path),
                        "--evidence", str(evidence_path), "--aggregate-only"],
                       text=True, capture_output=True)
    check("aggregate refusal carries count without attendee identity",
          p.returncode == 78 and "unresolved=1" in p.stderr
          and "new@example.com" not in p.stdout + p.stderr,
          p.stdout + p.stderr)

    p = subprocess.run([sys.executable, str(SCRIPT), "--proposals", str(proposal_path),
                        "--evidence", str(evidence_path), "--aggregate-only", "--defer-unmatched"],
                       text=True, capture_output=True)
    check("capture mode reports pending intake without refusing matched meetings",
          p.returncode == 0 and "PENDING unresolved=1" in p.stdout
          and "new@example.com" not in p.stdout + p.stderr)
    evidence_path.write_text("not-json")
    p = subprocess.run([sys.executable, str(SCRIPT), "--proposals", str(proposal_path),
                        "--evidence", str(evidence_path), "--aggregate-only", "--defer-unmatched"],
                       text=True, capture_output=True)
    check("capture mode still refuses malformed evidence", p.returncode == 65)

with tempfile.TemporaryDirectory() as raw:
    root = Path(raw)
    proposal_path, evidence_path = root / "proposals.json", root / "evidence.json"
    queue = root / "pending.json"
    proposal_path.write_text(json.dumps(proposals))
    (root / "run.sh").write_text("#!/usr/bin/env python3\nimport json,sys\n"
        "open('dispatch.json','w').write(sys.argv[3])\nprint('{\"ok\":true,\"seq\":123}')\n")
    (root / "run.sh").chmod(0o755)
    command = [sys.executable, str(SCRIPT), "--proposals", str(proposal_path),
               "--evidence", str(evidence_path), "--queue", str(queue),
               "--aggregate-only", "--defer-unmatched", "--dispatch"]
    p = subprocess.run(command, cwd=root, capture_output=True, text=True)
    check("unknown attendee is durably queued and dispatched", p.returncode == 0 and queue.exists() and (root / "dispatch.json").exists())
    sent = json.loads((root / "dispatch.json").read_text())
    check("dispatch sends a UUID idempotency_key", str(uuid.UUID(sent["idempotency_key"])) == sent["idempotency_key"])
    queue.unlink()
    (root / "dispatch.json").unlink()
    p = subprocess.run(command, cwd=root, capture_output=True, text=True)
    retried = json.loads((root / "dispatch.json").read_text())
    check("retry after lost acknowledgement reuses dispatch identity",
          p.returncode == 0 and retried["idempotency_key"] == sent["idempotency_key"]
          and retried["msg_id"] == sent["msg_id"])
    proposal_path.write_text(json.dumps({"unknown": []}))  # eight days later, outside rolling window
    p = subprocess.run(command, cwd=root, capture_output=True, text=True)
    check("rolled-out attendee remains pending until all receipts exist", p.returncode == 0 and queue.exists() and "new@example.com" in queue.read_text())
    evidence_path.write_text(json.dumps(complete))
    p = subprocess.run(command, cwd=root, capture_output=True, text=True)
    check("evidenced intake clears durable pending item", p.returncode == 0 and queue.exists() and json.loads(queue.read_text()) == {})

print("OK all checks passed" if not failed else "FAIL " + ", ".join(failed))
raise SystemExit(bool(failed))
