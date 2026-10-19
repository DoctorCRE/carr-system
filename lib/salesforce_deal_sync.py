"""Plan Salesforce report imports. Salesforce is never a write target."""

import csv
import argparse
import datetime as dt
from collections import Counter
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import uuid
from pathlib import Path
from zoneinfo import ZoneInfo

REPO = Path(__file__).resolve().parents[1]


STAGES = {"pending": "pending", "research": "research", "site selection": "site_selection",
          "negotiation": "negotiation", "legal": "legal", "due diligence": "due_diligence",
          "closing": "closing", "closed": "closed", "closed won": "closed", "fully closed out": "closed"}
OWNERS = {"joe": "joe", "joe bookout": "joe", "dell": "dell", "dell mccraney": "dell",
          "wayne mccraney": "dell"}


def norm(value):
    return " ".join(re.sub(r"[^a-z0-9]+", " ", value.lower()).split())


def sfid(value):
    return value[:15] if value else None


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


HEADER_ALIASES = {
    "opportunity id": "id", "sf id": "id", "id": "id",
    "opportunity name": "name", "deal name": "name", "name": "name",
    "account name": "client", "company name": "client", "company": "client",
    "stage": "stage", "stage name": "stage", "stagename": "stage", "phase": "stage",
    "opportunity owner": "owner", "deal owner": "owner", "owner": "owner",
    "close date": "close_date", "closedate": "close_date", "team members": "team",
}


def read_report(report, expected_count):
    if not report.strip():
        raise ValueError("report_empty")
    reader = csv.DictReader(io.StringIO(report.lstrip("\ufeff")),
                            delimiter="\t" if "\t" in report.splitlines()[0] else ",", strict=True)
    headers = [HEADER_ALIASES.get(norm(h), norm(h)) for h in (reader.fieldnames or [])]
    if len(headers) != len(set(headers)) or not {"id", "name", "client", "stage", "owner", "close_date"} <= set(headers):
        raise ValueError("report_headers_invalid")
    rows = []
    for index, raw in enumerate(reader, 2):
        if None in raw or any(v is None for v in raw.values()):
            raise ValueError(f"report_row_width_invalid:{index}")
        row = {h: v.strip() for h, v in zip(headers, raw.values())}
        if not re.fullmatch(r"006[A-Za-z0-9]{12}(?:[A-Za-z0-9]{3})?", row["id"]) or not row["name"]:
            raise ValueError(f"report_identity_invalid:{index}")
        if row["close_date"]:
            try:
                date = dt.date.fromisoformat(row["close_date"])
            except ValueError:
                try:
                    date = dt.datetime.strptime(row["close_date"], "%m/%d/%Y").date()
                except ValueError:
                    raise ValueError(f"report_date_invalid:{index}") from None
            row["close_date"] = date.isoformat()
        rows.append(row)
    if not isinstance(expected_count, int) or expected_count < 0 or len(rows) != expected_count:
        raise ValueError("report_count_mismatch")
    return rows


def build_plan(report, *, deals, clients, as_of, expected_count):
    rows = read_report(report, expected_count)
    items = []
    for row in rows:
        sid, stage = row["id"], norm(row["stage"])
        item = {"opportunity_id": sid, "name": row["name"]}
        items.append(item)
        owner = OWNERS.get(norm(row["owner"]))
        team = {OWNERS.get(norm(member)) for member in row.get("team", "").split(";")}
        if not owner and not ({"joe", "dell"} & team):
            item.update(kind="out_of_scope", reason="other_owner")
            continue
        if (stage.startswith("closed") or stage == "fully closed out") and row["close_date"] and dt.date.fromisoformat(row["close_date"]) < as_of - dt.timedelta(days=90):
            item.update(kind="out_of_scope", reason="closed_before_window")
            continue
        if stage == "closed lost":
            item.update(kind="held", reason="lost_status_contract_required")
            continue
        if stage not in STAGES or owner is None or (stage.startswith("closed") and (stage == "closed" or not row["close_date"])):
            item.update(kind="held", reason="unmapped_stage_owner_or_close_date")
            continue
        matches = [d for d in deals if sfid(d.get("salesforce_id")) == sfid(sid)]
        matched_by_id = bool(matches)
        if not matches:
            matches = [d for d in deals if norm(d["name"]) == norm(row["name"])]
        if not matches and row["client"]:
            matches = [d for d in deals if not d.get("salesforce_id") and norm(d.get("client_name", "")) == norm(row["client"])]
        if len(matches) > 1 or (matches and matches[0].get("salesforce_id") and sfid(matches[0]["salesforce_id"]) != sfid(sid)):
            item.update(kind="held", reason="ambiguous_or_conflicting_identity")
            continue
        match = matches[0] if matches else None
        if match and not matched_by_id and match.get("client_name") and norm(match["client_name"]) != norm(row["client"]):
            item.update(kind="held", reason="client_conflict")
            continue
        fields = {"phase": STAGES[stage], "salesforce_id": sid}
        if row["close_date"]:
            fields["sf_close_date_placeholder"] = row["close_date"]
        if stage in ("closed won", "fully closed out"):
            fields["outcome"] = "won"
        if match and (match.get("phase") == "closed" or match.get("outcome") in ("won", "lost")):
            if fields["phase"] != "closed" or (match.get("outcome") in ("won", "lost") and fields.get("outcome") != match["outcome"]):
                item.update(kind="held", reason="closed_state_conflict")
                continue
        item.update(kind="update" if match else "create", owner=owner, fields=fields)
        if match:
            item.update(deal=match["id"], base_version=match["base_version"])
            item["fields"] = {k: v for k, v in fields.items()
                              if (sfid(match.get(k)) != sfid(v) if k == "salesforce_id" else match.get(k) != v)}
            if match.get("owner") == owner:
                item["owner"] = None
            if not item["fields"] and item["owner"] is None:
                item["kind"] = "unchanged"
        else:
            candidates = {c["ref"] for c in clients if norm(c["name"]) == norm(row["client"])}
            if len(candidates) != 1:
                item.update(kind="held", reason="client_missing_or_ambiguous")
            else:
                item["client"] = candidates.pop()
    source_ids = Counter(sfid(i["opportunity_id"]) for i in items)
    targets = Counter(i["deal"] for i in items if "deal" in i)
    new_names = Counter(norm(i["name"]) for i in items if i["kind"] == "create")
    for item in items:
        if source_ids[sfid(item["opportunity_id"])] > 1 or targets.get(item.get("deal"), 0) > 1 or (item["kind"] == "create" and new_names[norm(item["name"])] > 1):
            item.update(kind="held", reason="duplicate_source_or_target")
    result = {"schema": "salesforce-deal-sync.v1", "as_of": as_of.isoformat(),
              "report_digest": fingerprint(report), "record_digest": fingerprint(sorted(deals, key=lambda d: d["id"])),
              "items": items, "counts": {"source": len(rows), **{kind: sum(i["kind"] == kind for i in items)
              for kind in ("create", "update", "unchanged", "held", "out_of_scope")}}}
    result["plan_digest"] = fingerprint(result)
    return result


def read_snapshot(call_verb):
    result = call_verb("read-deal-reconciliation", {"all": True})
    if not isinstance(result, dict) or not isinstance(result.get("deals"), list):
        raise ValueError("sync_snapshot_contract_unavailable")
    return result["deals"]


def apply_plan(plan, call_verb, journal_path):
    if plan.get("schema") != "salesforce-deal-sync.v1" or plan.get("plan_digest") != fingerprint({k: v for k, v in plan.items() if k != "plan_digest"}):
        raise ValueError("plan_changed_run_dry_run_again")
    current = read_snapshot(call_verb)
    if plan["record_digest"] != fingerprint(sorted(current, key=lambda d: d["id"])):
        raise ValueError("records_changed_run_dry_run_again")
    original = {d["id"]: d for d in current}
    work = [i for i in plan["items"] if i["kind"] in ("create", "update")]
    for item in work:
        if set(item["fields"]) - {"phase", "outcome", "salesforce_id", "sf_close_date_placeholder"} or item["owner"] not in (None, "joe", "dell"):
            raise ValueError("plan_fields_invalid")
    try:
        fd = os.open(journal_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        raise ValueError("attempt_already_recorded_reconcile_receipts_before_new_plan") from None
    with os.fdopen(fd, "w") as journal:
        def note(entry):
            journal.write(json.dumps(entry, sort_keys=True) + "\n")
            journal.flush()
            os.fsync(journal.fileno())

        def write(verb, args):
            args = {**args, "idempotency_key": str(uuid.uuid5(uuid.NAMESPACE_URL,
                    "carr:salesforce-sync:" + fingerprint({"verb": verb, "args": args})))}
            note({"status": "pending", "verb": verb, "idempotency_key": args["idempotency_key"],
                  "request_digest": fingerprint(args)})
            result = call_verb(verb, args)
            if result.get("ok") is not True:
                raise ValueError("record_verb_refused_check_receipts")
            note({"status": "accepted", "verb": verb, "idempotency_key": args["idempotency_key"]})
            return result

        note({"plan_digest": plan["plan_digest"], "status": "started"})
        verified = 0
        for item in work:
            prior = next((d for d in current if d["id"] == item.get("deal")), None)
            if item["kind"] == "update" and prior != original[item["deal"]]:
                raise ValueError("deal_changed_during_apply_check_receipts")
            if item["kind"] == "create":
                fields = {k: v for k, v in item["fields"].items() if k != "outcome"}
                response = write("new-deal", {"name": item["name"], "client": item["client"],
                                 "deal_type": "other", **fields, "reason": "Salesforce report reconciliation"})
                deal_id = response["deal_id"]
            else:
                deal_id = item["deal"]
                if item["fields"]:
                    guarded_fields = {k: prior.get(k) for k in ("salesforce_id", "phase", "outcome")}
                    write("update-deal", {"deal": deal_id, "base_version": prior["base_version"],
                                          "fields": {**guarded_fields, **item["fields"]}})
            current = read_snapshot(call_verb)
            row = next((d for d in current if d["id"] == deal_id), None)
            expected = {k: v for k, v in item["fields"].items() if item["kind"] != "create" or k != "outcome"}
            if row is None or any(row.get(k) != v for k, v in expected.items()):
                raise ValueError("field_readback_failed_check_receipts")
            if prior and row.get("owner") != prior.get("owner"):
                raise ValueError("owner_changed_during_apply_check_receipts")
            if item["kind"] == "create" and "outcome" in item["fields"]:
                write("update-deal", {"deal": deal_id, "base_version": row["base_version"],
                                     "fields": {"outcome": item["fields"]["outcome"]}})
                current = read_snapshot(call_verb)
                row = next(d for d in current if d["id"] == deal_id)
                if row.get("outcome") != item["fields"]["outcome"]:
                    raise ValueError("outcome_readback_failed_check_receipts")
            if item["owner"] is not None:
                write("set-lead", {"deal": deal_id, "base_version": row["base_version"], "new_lead": item["owner"]})
                current = read_snapshot(call_verb)
                row = next(d for d in current if d["id"] == deal_id)
            if (item["owner"] is not None and row.get("owner") != item["owner"]) or any(row.get(k) != v for k, v in item["fields"].items()):
                raise ValueError("final_readback_failed_check_receipts")
            verified += 1
            note({"status": "verified", "opportunity_digest": fingerprint(item["opportunity_id"])})
        note({"status": "complete", "verified": verified, "held": plan["counts"]["held"]})
        return {"verified": verified, "held": plan["counts"]["held"]}


def deployed_verb(verb, args):
    if verb not in {"read-deal-reconciliation", "find", "new-deal", "update-deal", "set-lead",
                    "deal-room-board", "get-deal-room", "patch-deal-field", "set-market-agent",
                    "add-party", "new-client", "create-national-market-deal"}:
        raise ValueError("verb_not_allowed")
    result = subprocess.run([sys.executable, str(REPO / "tools/call-verb.py"), verb, json.dumps(args)],
                            cwd=REPO, capture_output=True, text=True, timeout=90)
    if result.returncode:
        raise ValueError(f"record_verb_failed:{verb}:reconcile_before_retry")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        raise ValueError(f"record_verb_response_invalid:{verb}") from None


def main(argv=None, call_verb=deployed_verb):
    parser = argparse.ArgumentParser(description="Dry-run a complete Salesforce report, then apply the saved plan through CARR verbs.")
    parser.add_argument("report", nargs="?", help="Salesforce details CSV or browser-captured TSV")
    parser.add_argument("--bindings", type=Path, help="Private reviewed identity/account bindings for a PSV snapshot")
    parser.add_argument("--expected-count", type=int, help="Salesforce report Total Records, required for a dry run")
    parser.add_argument("--as-of", type=dt.date.fromisoformat,
                        default=dt.datetime.now(ZoneInfo("America/Chicago")).date())
    parser.add_argument("--out", type=Path, help="New private plan JSON path; never overwrite a reviewed plan")
    parser.add_argument("--apply", type=Path, help="Apply this saved plan; requires unchanged record snapshot")
    args = parser.parse_args(argv)
    try:
        if args.apply:
            if args.report or args.out or args.bindings or args.expected_count is not None:
                parser.error("--apply takes only a saved plan")
            from lib import salesforce_active_snapshot as active
            saved = json.loads(args.apply.read_text())
            apply = active.apply_plan if saved.get("schema") == "salesforce-active-snapshot.v1" else apply_plan
            result = apply(saved, call_verb, args.apply.with_suffix(".receipts.jsonl"))
            print(json.dumps(result, sort_keys=True))
            return 2 if result["held"] else 0
        if not args.report or args.expected_count is None or not args.out:
            parser.error("a dry run requires report, --expected-count, and --out")
        report = Path(args.report).read_text(encoding="utf-8-sig")
        if not report.strip():
            raise ValueError("report_empty")
        if report.splitlines()[0].startswith("sf_id_tail|"):
            from lib import salesforce_active_snapshot as active
            bindings = json.loads(args.bindings.read_text()) if args.bindings else {}
            plan = active.build_plan(report, active.read_snapshot(call_verb),
                                     expected_count=args.expected_count, bindings=bindings)
        else:
            if args.bindings:
                parser.error("--bindings requires a PSV snapshot")
            rows = read_report(report, args.expected_count)
            deals = read_snapshot(call_verb)
            clients = [{"ref": d["client_ref"], "name": d["client_name"]} for d in deals if d.get("client_ref")]
            for name in sorted({r["client"] for r in rows if r["client"]}):
                if not any(norm(c["name"]) == norm(name) for c in clients):
                    found = call_verb("find", {"query": name})
                    clients.extend({"ref": c["ref"], "name": c["name"]} for c in found.get("parties", [])
                                   if c.get("kind") == "client" and c.get("merged") is False and norm(c["name"]) == norm(name))
            plan = build_plan(report, deals=deals, clients=clients, as_of=args.as_of, expected_count=args.expected_count)
        fd = os.open(args.out, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as output:
            json.dump(plan, output, indent=2, sort_keys=True)
            output.write("\n")
        print(json.dumps(plan["counts"], sort_keys=True))
        return 2 if plan["counts"]["held"] else 0
    except (ValueError, OSError, csv.Error, subprocess.TimeoutExpired) as error:
        print(f"salesforce-sync stopped: {type(error).__name__}" + (f": {error}" if isinstance(error, ValueError) else ""), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
