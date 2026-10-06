#!/usr/bin/env python3
"""Read local delivery receipts and CARR transcripts without exporting their bodies."""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from lib.rule_boot_gate import boot_delivery
from lib.rule_recall import delivery_counts, delivered_ids, timestamp


def embedded(value):
    if isinstance(value, dict):
        yield value
        for v in value.values():
            if isinstance(v, (dict, list)):
                yield from embedded(v)
    elif isinstance(value, list):
        for v in value:
            yield from embedded(v)
    elif isinstance(value, str):
        text = value.strip()
        if not any(s in text for s in ("rule-", "rule_boot", "hookSpecificOutput")):
            return
        try:
            yield from embedded(json.loads(text))
            return
        except ValueError:
            pass
        start = text.find("{")
        if start >= 0:
            try:
                doc, _ = json.JSONDecoder().raw_decode(text[start:])
                yield from embedded(doc)
            except ValueError:
                return


def hook_objects(row):
    attachment = row.get("attachment") or {}
    for field in ("stdout", "content"):
        value = attachment.get(field)
        if isinstance(value, str):
            for doc in embedded(value):
                additional = (doc.get("hookSpecificOutput") or {}).get("additionalContext")
                if isinstance(additional, str):
                    yield from embedded(additional)
                yield doc
    for block in (row.get("message") or {}).get("content", []) if isinstance((row.get("message") or {}).get("content"), list) else []:
        if isinstance(block, dict) and block.get("type") == "tool_result":
            content = block.get("content")
            if isinstance(content, str):
                yield from embedded(content)
            elif isinstance(content, list):
                for part in content:
                    if isinstance(part, dict):
                        yield from embedded(part.get("text", ""))


def audit(active_ids, sections, paths, now):
    receipts = []
    inventory = []
    gates = Counter({rid: 0 for rid in active_ids})
    gate_invocations = Counter()
    section_reads = Counter()
    doc_reads = Counter()
    unknown_gate = missing_dates = 0
    turn_keys = set()
    boots = defaultdict(dict)
    epochs = Counter()
    source_seen = set()
    start = timestamp(now).timestamp() - 30 * 86400
    for path in paths:
        state = {"collection": str(path), "rows": 0, "in_window": 0, "invalid": 0}
        if not path.is_file():
            state["missing"] = True
            inventory.append(state)
            continue
        opener = gzip.open if path.suffix == ".gz" else open
        with opener(path, "rt", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                state["rows"] += 1
                try:
                    row = json.loads(line)
                except ValueError:
                    state["invalid"] += 1
                    continue
                if not isinstance(row, dict):
                    state["invalid"] += 1
                    continue
                stamp = row.get("timestamp", row.get("ts", row.get("at", row.get("observed_at"))))
                at = timestamp(stamp)
                if at is None:
                    missing_dates += 1
                    continue
                if not start <= at.timestamp() <= timestamp(now).timestamp():
                    continue
                # Archive/live overlap counts once; UUIDs identify native records.
                key = row.get("uuid") or hashlib.sha256(line.encode()).hexdigest()
                if key in source_seen:
                    continue
                source_seen.add(key)
                state["in_window"] += 1
                context = (row.get("sessionId", path.name.split(".jsonl")[0]),
                           row.get("agentId") or "main")
                if row.get("subtype") == "compact_boundary":
                    epochs[context] += 1
                if row.get("type") == "user" and not row.get("isMeta"):
                    turn_keys.add(key)
                if delivered_ids(row):
                    receipts.append({**row, "observed_at": at.isoformat()})
                if row.get("gate") or row.get("hook"):
                    gate = row.get("gate", row.get("hook"))
                    gate_invocations[gate] += 1
                    ids = re.findall(r"\b[0-9a-f]{8}\b", str(row.get("rule", row.get("rule_id", ""))))
                    if row.get("kind") in {"block", "deny", "violation", "reopen"} or row.get("outcome") in {"deny", "block", "reopen"}:
                        gates.update(r for r in ids if r in gates)
                        if not ids:
                            unknown_gate += 1
                for obj in hook_objects(row):
                    if delivered_ids(obj):
                        receipts.append({**obj, "observed_at": at.isoformat()})
                    boot = obj.get("rule_boot")
                    if isinstance(boot, dict) and boot.get("schema") == "carr-rule-boot/v1":
                        bkey = (*context, epochs[context], boot.get("digest"))
                        boots[bkey][boot.get("page")] = (boot, at.isoformat())
                message = row.get("message") or {}
                for block in message.get("content", []) if isinstance(message.get("content"), list) else []:
                    if not isinstance(block, dict) or block.get("type") != "tool_use":
                        continue
                    name, args = block.get("name", ""), block.get("input") or {}
                    verb = name.rsplit("__", 1)[-1].replace("_", "-")
                    if verb == "call-verb":
                        verb = args.get("verb", "")
                        args = args.get("args", args)
                    calls = [(verb, args)]
                    command = args.get("command", "") if isinstance(args, dict) else ""
                    if name == "Bash" and isinstance(command, str):
                        for m in re.finditer(r"run\.sh\s+call\s+['\"]?([a-z-]+)['\"]?\s+(['\"])(.*?)\2", command, re.S):
                            try:
                                calls.append((m[1], json.loads(m[3])))
                            except ValueError:
                                continue
                    for verb, args in calls:
                        if not isinstance(args, dict):
                            continue
                        if verb == "read-doctrine" and args.get("document"):
                            doc_reads[str(args["document"])] += 1
                        if verb == "doctrine-sections":
                            section_reads.update(str(s) for s in args.get("section_ids", []))
        inventory.append(state)
    for (sid, agent, epoch, digest), pages in boots.items():
        ids = boot_delivery({n: boot for n, (boot, _) in pages.items()})
        if not ids:
            continue
        receipts.append({"schema": "rule-recall-boot-observation/v1", "delivered": ids,
                         "observed_at": max(v[1] for v in pages.values()),
                         "receipt_id": f"boot:{sid}:{agent}:{epoch}:{digest}"})
    reads = []
    for section in sections:
        count = section_reads[section["id"]] + doc_reads[section["slug"]]
        reads.append({**section, "observed_read_calls": count,
                      "status": "observed_read_request" if count else "no_observed_read_request"})
    return {"schema": "rule-recall-audit/v1", "now": now, "active_rules": len(active_ids),
            "deliveries_30d": delivery_counts(receipts, active_ids, now, 30),
            "deliveries_14d": delivery_counts(receipts, active_ids, now, 14),
            "gate_firings_30d": dict(gates), "gate_invocations": dict(gate_invocations),
            "unattributed_gate_firings": unknown_gate, "undated_rows": missing_dates,
            "native_user_turns": len(turn_keys), "inventory": inventory, "sections": reads,
            "receipt_rows": [{"schema": "rule-recall-delivery-observation/v1", "receipt_id": r.get("receipt_id"),
                              "observed_at": r["observed_at"], "delivered": delivered_ids(r)} for r in receipts],
            "limitations": ["Zero means no observed delivery, not no binding moment or obsolete law.",
                "Rule-byte meter has no rule ids. Gate invocations are distinct from rule-attributed denials.",
                "Doctrine counts are read requests, not proof the model read or obeyed the text.",
                "Search results, retrieve output, external sessions and omitted/cleared history may add unobserved reads."]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--standing", type=Path, required=True)
    parser.add_argument("--sections", type=Path, required=True)
    parser.add_argument("--logs-root", type=Path, required=True)
    parser.add_argument("--transcripts-root", type=Path, required=True)
    parser.add_argument("--archive-root", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    live = json.loads(args.standing.read_text())
    active = [r["id"] for r in live["shared_rules"] + live["personal_rules"]]
    paths = [Path.home() / ".config/carr/claude-rule-delivery.jsonl"]
    for name in ("rule-trigger-delivery", "rule-delivery-shadow", "hook-telemetry", "gate-decisions", "conduct-gate", "completion-evidence-gate"):
        paths.extend(sorted(args.logs_root.glob(name + "*.jsonl*")))
    paths.extend(sorted(args.transcripts_root.glob("*carr-system*/*.jsonl")))
    if args.archive_root:
        paths.extend(sorted(args.archive_root.glob("claude/*carr-system*/*.jsonl.gz")))
    now = datetime.now(timezone.utc).isoformat()
    result = audit(active, json.loads(args.sections.read_text()), paths, now)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({"active": len(active), "collections": len(paths),
          "receipts": result["deliveries_30d"]["receipts"], "zero_30d": len(result["deliveries_30d"]["zero"]),
          "native_turns": result["native_user_turns"]}))


if __name__ == "__main__":
    main()
