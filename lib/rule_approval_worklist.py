"""lib/rule_approval_worklist.py — which blocked rules the system can approve itself.

WHY THIS EXISTS (2026-10-12). Production carried 97 ACTIVE machine_enforceable
rules whose enforcement_status was `blocked`: no ops.rule_approval_receipt row
for their current version. Only approve-rule (ops.approve_rule) writes that
receipt, binds the controls and flips admission to hard_enforced, atomically,
and it refuses unless every requested control is installed and verified in
ops.enforcement_control_catalog. ops/config/rule-enforcement-map.json already
names a control for every one of them. Decision
cbe91287-10a2-48d0-bda7-3b1784b4bb93 (Joe, 2026-10-12) removes his approval
from everything the system can do itself, so a sponsored session carries the
approval — but only for a rule whose control the database will accept.

WHAT IT JOINS. Three sources, each read, never written:
  * the map's rule_controls (which control a rule names),
  * ops/config/rule-triage.v1.json (a rule triaged GONE is retired into a
    survivor, never approved),
  * production, read-only through tools/db-tap.py: the blocked rows of
    ops.v_rule_enforcement_status and the ops.enforcement_control_catalog rows.

One row per blocked rule: its control, the catalog's readiness for that
control, the GONE flag, a readiness verdict with its reason, and the exact
approve-rule JSON. A rule is READY only when it is not GONE, its control is
installed, verified and of a class approve_rule accepts (deny_gate, stop_gate,
schema, transactional_schema — the same set ops.bind_rule_controls checks), and
the map does not itself record the RULE as `surfacing`: a rule the map says is
only reminded of, never blocked, would be overstated by a hard_enforced
receipt (rule ab814a26: recitation is not enforcement).

DRY RUN BY DEFAULT. `--apply` runs approve-rule for the READY rows,
sequentially, through the allowlisted Bash door (`./run.sh call`), minting a
fresh idempotency key per rule, with a reason citing the decision, and appends
every result — the approval receipt id or the refusal verbatim — to
out/rule-approvals.jsonl. A refusal is recorded, never retried and never
worked around: a database refusal is the database's answer.

NO SHEBANG AND NO __main__ BLOCK ON PURPOSE: a new file with either is a new
script ingress on the sealed SCAC frontier. `./run.sh rule-approvals` imports
main().
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import subprocess
import sys
import tempfile
import uuid
from collections import Counter
from collections.abc import Callable, Mapping
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1]
MAP = SOURCE / "ops" / "config" / "rule-enforcement-map.json"
TRIAGE = SOURCE / "ops" / "config" / "rule-triage.v1.json"

SCHEMA = "rule-approval-worklist/v1"
DECISION_ID = "cbe91287-10a2-48d0-bda7-3b1784b4bb93"
POLICY_KIND = "machine_enforceable"
# ops.bind_rule_controls and ops.approve_rule_receipt_activation_v1 both accept
# only these classes; a surfacing or judgment control is refused by name there.
APPROVABLE_CLASSES = frozenset({"deny_gate", "stop_gate", "schema", "transactional_schema"})
READINESS = ("ready", "held", "class_not_approvable", "rule_only_surfaced", "catalog_missing",
             "unmapped", "gone")

DB_SQL = """select json_build_object(
  'rules', coalesce((
    select json_agg(json_build_object(
             'rule_id', s.rule_id,
             'policy_status', s.policy_status,
             'enforcement_class', s.enforcement_class,
             'enforcement_status', s.enforcement_status,
             'control_bound', exists (select 1 from ops.rule_control_binding b
                                       where b.rule_id = s.rule_id))
           order by s.rule_id)
      from ops.v_rule_enforcement_status s
     where s.policy_status = 'active'
       and s.enforcement_class = 'machine_enforceable'
       and s.enforcement_status = 'blocked'
       and s.approval_receipt_id is null), '[]'::json),
  'catalog', coalesce((
    select json_agg(json_build_object(
             'control_key', c.control_key,
             'installed', c.installed,
             'verified', c.verified_at is not null,
             'enforcement_class', c.enforcement_class)
           order by c.control_key)
      from ops.enforcement_control_catalog c), '[]'::json),
  'status_counts', coalesce((
    select json_agg(json_build_object(
             'policy_status', policy_status, 'enforcement_class', enforcement_class,
             'enforcement_status', enforcement_status, 'receipted', receipted, 'n', n)
           order by policy_status, enforcement_class, enforcement_status, receipted)
      from (select policy_status, enforcement_class, enforcement_status,
                   approval_receipt_id is not null as receipted, count(*) as n
              from ops.v_rule_enforcement_status
             group by 1, 2, 3, 4) grouped), '[]'::json)
)::text;
"""


class WorklistError(RuntimeError):
    pass


# ── sources ────────────────────────────────────────────────────────────────

def load_map(path: Path = MAP) -> dict[str, dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    controls = data.get("rule_controls")
    if not isinstance(controls, dict) or not controls:
        raise WorklistError(f"{path.name} carries no rule_controls")
    return controls


def load_gone(path: Path = TRIAGE) -> set[str]:
    """Short ids the triage homes GONE: retired into a survivor, never approved."""
    data = json.loads(path.read_text(encoding="utf-8"))
    rules = data.get("rules")
    if not isinstance(rules, list):
        raise WorklistError(f"{path.name} carries no rules list")
    return {str(r["id"])[:8] for r in rules if str(r.get("home", "")).lower() == "gone"}


def db_tap_runner(runtime_root: Path, reason: str) -> Callable[[str], str]:
    """A read-only production query through tools/db-tap.py (it takes a FILE)."""
    def run(sql: str) -> str:
        fd, tmp = tempfile.mkstemp(prefix="rule-approvals-", suffix=".sql")
        try:
            with os.fdopen(fd, "w") as fh:
                fh.write(sql)
            py = runtime_root / ".venv" / "bin" / "python"
            proc = subprocess.run(
                [str(py if py.exists() else sys.executable), str(runtime_root / "tools" / "db-tap.py"),
                 "--reason", reason, "sql", tmp],
                cwd=runtime_root, capture_output=True, text=True, timeout=180,
                stdin=subprocess.DEVNULL)
        finally:
            os.unlink(tmp)
        if proc.returncode != 0:
            raise WorklistError(f"db-tap read failed (exit {proc.returncode}): "
                                f"{(proc.stderr or proc.stdout).strip()[:800]}")
        return proc.stdout
    return run


def fetch_db_state(run: Callable[[str], str]) -> dict:
    text = run(DB_SQL)
    line = next((ln for ln in reversed(text.splitlines()) if ln.strip().startswith("{")), None)
    if line is None:
        raise WorklistError("db read returned no JSON row")
    state = json.loads(line)
    for key in ("rules", "catalog"):
        if not isinstance(state.get(key), list):
            raise WorklistError(f"db read carries no {key} list")
    return state


# ── the worklist ───────────────────────────────────────────────────────────

def approve_reason(short_id: str, control: str, klass: str) -> str:
    return (f"Decision {DECISION_ID} (Joe, 2026-10-12: remove his approval from everything "
            f"the system can do itself). Rule {short_id} names control {control} "
            f"({klass}) in ops/config/rule-enforcement-map.json, installed and verified in "
            "ops.enforcement_control_catalog; a Joe-sponsored session carries the approval "
            "via ./run.sh rule-approvals --apply.")


def approve_payload(row: Mapping, idempotency_key: str) -> dict:
    """The exact approve-rule arguments for one READY row."""
    return {
        "idempotency_key": idempotency_key,
        "rule_id": row["rule_id"],
        "policy_kind": POLICY_KIND,
        "control_keys": [row["control"]],
        "reason": approve_reason(row["short_id"], row["control"], row["catalog_class"]),
    }


def classify(short_id: str, control: str | None, cat: Mapping | None, gone: bool,
             map_class: str | None = None) -> tuple[str, str]:
    """(readiness, why) for one blocked rule. Order matters: GONE wins over all."""
    if gone:
        return "gone", "triaged GONE: retire into its survivor, never approve"
    if not control:
        return "unmapped", "the enforcement map names no control for this rule"
    if cat is None:
        return "catalog_missing", f"{control} is not in ops.enforcement_control_catalog"
    if not (cat.get("installed") and cat.get("verified")):
        return "held", (f"{control} is held: not installed and verified in the catalog "
                        "(ops/sync_control_catalog.py names why)")
    if cat.get("enforcement_class") not in APPROVABLE_CLASSES:
        return "class_not_approvable", (
            f"{control} is {cat.get('enforcement_class')}; approve_rule accepts only "
            + ", ".join(sorted(APPROVABLE_CLASSES)))
    if map_class == "surfacing":
        return "rule_only_surfaced", (
            f"the map records this rule as surfacing through {control} (a reminder, not a "
            "block); a hard_enforced receipt would overstate it")
    return "ready", f"{control} is installed, verified and {cat['enforcement_class']}"


def build_worklist(db: Mapping, rule_controls: Mapping[str, Mapping], gone: set[str],
                   *, mint: Callable[[], str] = lambda: str(uuid.uuid4())) -> list[dict]:
    catalog = {c["control_key"]: c for c in db["catalog"]}
    rows = []
    for r in db["rules"]:
        rule_id = str(r["rule_id"])
        short = rule_id[:8]
        entry = rule_controls.get(short) or {}
        control = entry.get("control")
        cat = catalog.get(control) if control else None
        map_class = entry.get("enforcement_class")
        readiness, why = classify(short, control, cat, short in gone, map_class)
        row = {
            "rule_id": rule_id,
            "short_id": short,
            "control": control,
            "map_class": map_class,
            "catalog_installed": bool(cat and cat.get("installed")),
            "catalog_verified": bool(cat and cat.get("verified")),
            "catalog_class": cat.get("enforcement_class") if cat else None,
            "gone": short in gone,
            "control_bound": bool(r.get("control_bound")),
            "readiness": readiness,
            "why": why,
        }
        row["approve_rule"] = approve_payload(row, mint()) if readiness == "ready" else None
        rows.append(row)
    return rows


def totals(rows: list[dict]) -> dict:
    by = Counter(r["readiness"] for r in rows)
    held = Counter(r["control"] for r in rows if r["readiness"] == "held")
    return {"blocked": len(rows), **{k: by.get(k, 0) for k in READINESS},
            "held_by_control": dict(sorted(held.items()))}


# ── apply ──────────────────────────────────────────────────────────────────

def record_verb(runtime_root: Path) -> Callable[[str, dict], dict]:
    """The allowlisted Bash door (`./run.sh call`), never the generic passthrough.

    Returns {"ok": True, "result": <json>} or {"ok": False, "refusal": <verbatim>}.
    """
    def call(verb: str, payload: dict) -> dict:
        proc = subprocess.run([str(runtime_root / "run.sh"), "call", verb, json.dumps(payload)],
                              cwd=runtime_root, capture_output=True, text=True, timeout=180,
                              stdin=subprocess.DEVNULL)
        out = proc.stdout or ""
        if proc.returncode == 0 and "{" in out:
            try:
                return {"ok": True, "result": json.loads(out[out.index("{"):])}
            except json.JSONDecodeError:
                pass
        text = "\n".join(ln for ln in (proc.stderr or out).splitlines()
                         if not ln.startswith("local-verb identity ->")).strip()
        return {"ok": False, "refusal": text or f"exit {proc.returncode} with no output"}
    return call


def apply(rows: list[dict], call: Callable[[str, dict], dict], log: Path, *,
          mint: Callable[[], str] = lambda: str(uuid.uuid4()),
          now: Callable[[], str] = lambda: _dt.datetime.now(_dt.timezone.utc).isoformat(),
          limit: int | None = None, max_refusals: int = 3) -> dict:
    """approve-rule for each READY row, one at a time; every result is appended to `log`.

    Stops after `max_refusals` refusals. Every refusal the server cannot map to
    a named error is also written to its incident store, so a refusal that is
    structural (the same database precondition for every remaining row) would
    otherwise spray one identical incident per rule and teach nothing new. The
    rows not attempted are counted in the summary, never silently dropped.
    """
    log.parent.mkdir(parents=True, exist_ok=True)
    counts: Counter[str] = Counter()
    ready = [r for r in rows if r["readiness"] == "ready"]
    if limit is not None:
        ready = ready[:limit]
    attempted = 0
    with log.open("a", encoding="utf-8") as fh:
        for row in ready:
            if counts["refused"] >= max_refusals:
                break
            attempted += 1
            payload = approve_payload(row, mint())
            res = call("approve-rule", payload)
            result = res.get("result") if res.get("ok") else None
            receipt = (result or {}).get("approval_receipt_id")
            outcome = "approved" if receipt else "refused"
            counts[outcome] += 1
            entry = {"schema": SCHEMA, "at": now(), "decision": DECISION_ID,
                     "rule_id": row["rule_id"], "short_id": row["short_id"],
                     "control": row["control"], "idempotency_key": payload["idempotency_key"],
                     "outcome": outcome, "approval_receipt_id": receipt,
                     "enforcement_status": (result or {}).get("enforcement_status"),
                     "refusal": None if receipt else (res.get("refusal") or json.dumps(result))}
            fh.write(json.dumps(entry, sort_keys=True) + "\n")
            fh.flush()
        skipped = Counter(r["readiness"] for r in rows if r["readiness"] != "ready")
        summary = {"schema": SCHEMA, "at": now(), "kind": "summary", "decision": DECISION_ID,
                   "attempted": attempted, "approved": counts["approved"],
                   "refused": counts["refused"], "not_attempted": len(ready) - attempted,
                   "halted_after_refusals": attempted < len(ready),
                   "skipped": dict(sorted(skipped.items()))}
        fh.write(json.dumps(summary, sort_keys=True) + "\n")
    return summary


# ── render ─────────────────────────────────────────────────────────────────

def render(rows: list[dict], tot: dict) -> str:
    head = (f"{'rule':<9} {'control':<26} {'control class':<21} {'rule class':<13} "
            f"{'catalog':<8} {'gone':<5} readiness")
    lines = [head, "-" * len(head)]
    for r in sorted(rows, key=lambda x: (READINESS.index(x["readiness"]), x["control"] or "", x["short_id"])):
        cat = ("ok" if r["catalog_installed"] and r["catalog_verified"]
               else "held" if r["catalog_class"] else "absent")
        lines.append(f"{r['short_id']:<9} {(r['control'] or '-'):<26} {(r['catalog_class'] or '-'):<21} "
                     f"{(r['map_class'] or '-'):<13} {cat:<8} {'yes' if r['gone'] else 'no':<5} "
                     f"{r['readiness']}")
    lines.append("")
    lines.append(f"{tot['blocked']} blocked active machine_enforceable rules: "
                 + ", ".join(f"{tot[k]} {k}" for k in READINESS))
    if tot["held_by_control"]:
        lines.append("held by control: " + ", ".join(f"{k} {v}" for k, v in tot["held_by_control"].items()))
    unbound = sum(1 for r in rows if r["readiness"] == "ready" and not r["control_bound"])
    if unbound:
        lines.append(f"note: {unbound} ready rule(s) are ACTIVE with no rule_control_binding. "
                     "ops.bind_rule_controls verifies, never creates, a binding on an active rule, "
                     "so approve-rule refuses them today ('active rule <id> lacks its immutable "
                     "exact control binding', measured 2026-10-12; WR-000262 asks for the "
                     "adoption path). --apply records each refusal verbatim.")
    lines.append("(dry run — pass --apply to run approve-rule for the ready rows)")
    return "\n".join(lines)


# ── entry ──────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None, *, db: Mapping | None = None,
         call: Callable[[str, dict], dict] | None = None,
         mint: Callable[[], str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="run.sh rule-approvals", description=__doc__.split("\n\n")[0])
    ap.add_argument("--json", action="store_true", help="print the worklist as JSON")
    ap.add_argument("--apply", action="store_true",
                    help="run approve-rule for each READY row (the only write)")
    ap.add_argument("--limit", type=int, default=None, help="apply to at most N ready rows")
    ap.add_argument("--max-refusals", type=int, default=3,
                    help="stop after N refusals (default 3); the rest are counted as not attempted")
    ap.add_argument("--runtime-root", default=str(SOURCE),
                    help="checkout whose tools/db-tap.py and run.sh carry the runtime "
                         "(default: this checkout)")
    ap.add_argument("--log", default=None, help="result log (default <runtime-root>/out/rule-approvals.jsonl)")
    args = ap.parse_args(argv)
    root = Path(args.runtime_root).resolve()
    mint = mint or (lambda: str(uuid.uuid4()))
    try:
        state = db if db is not None else fetch_db_state(
            db_tap_runner(root, "rule-approvals worklist: read blocked rules and the control catalog"))
        rows = build_worklist(state, load_map(), load_gone(), mint=mint)
    except (WorklistError, OSError, ValueError, KeyError) as exc:
        msg = f"UNKNOWN: could not build the rule-approval worklist ({exc})"
        print(json.dumps({"schema": SCHEMA, "ok": False, "error": msg}) if args.json else msg)
        return 1
    tot = totals(rows)
    report: dict = {"schema": SCHEMA, "ok": True, "decision": DECISION_ID, "totals": tot,
              "status_counts": state.get("status_counts"), "rows": rows}
    applied: dict | None = None
    if args.apply:
        log = Path(args.log) if args.log else root / "out" / "rule-approvals.jsonl"
        report["applied"] = applied = apply(rows, call or record_verb(root), log, mint=mint, limit=args.limit,
                                  max_refusals=args.max_refusals)
        report["log"] = str(log)
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(render(rows, tot))
        if applied is not None:
            a = applied
            print(f"\napplied: {a['attempted']} attempted, {a['approved']} approved, "
                  f"{a['refused']} refused, {a['not_attempted']} not attempted"
                  f"{' (halted at the refusal ceiling)' if a['halted_after_refusals'] else ''}; "
                  f"skipped {a['skipped']}; log {report['log']}")
    return 0
