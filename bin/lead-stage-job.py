#!/usr/bin/env python3
"""Run the evidence-driven stage job; optionally admit a local derived batch.

--dry-run only calls the preview read. Local source adapters can call
record-lead-contact directly, or supply --evidence-file outside the checkout.
No provider API, mailbox credentials, message bodies, or send operation here.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import uuid

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from lib import record_call  # noqa: E402
FIELDS = {"lead", "native_ref", "counterparty_address", "kind", "occurred_at",
          "draft_body_sha256", "ended_at", "attended", "automated", "first_contact_draft_id", "lead_stage_signal", "follow_up_after", "archive_reason"}
INVOICE_FIELDS = {"native_ref", "from_address", "deal_name", "client_name", "property_address", "occurred_at"}


def run_job(call, *, dry_run=False, evidence=None, time_zone="America/Chicago"):
    if dry_run:
        if evidence is not None:
            raise ValueError("dry run reads stored evidence; omit --evidence-file")
        return call("lead-stage-preview", {})
    captures = []
    for item in evidence or []:
        invoice = isinstance(item, dict) and "deal_name" in item
        allowed = INVOICE_FIELDS if invoice else FIELDS
        required = {"native_ref", "from_address", "deal_name", "occurred_at"} if invoice else {"lead", "native_ref", "counterparty_address", "kind", "occurred_at"}
        if not isinstance(item, dict) or set(item) - allowed:
            raise ValueError("derived evidence has unsupported fields")
        if not required <= set(item):
            raise ValueError("derived evidence is incomplete")
        verb = "record-deal-invoice" if invoice else "record-lead-contact"
        identity = [verb, item["native_ref"]] if invoice else [item["lead"], item["native_ref"], item["kind"]]
        key = ("deal-invoice:" if invoice else "lead-contact:") + hashlib.sha256(json.dumps(identity).encode()).hexdigest()
        captures.append((verb, {**item, "idempotency_key": key}))
    for verb, args in captures:
        call(verb, args)
    return call("advance-leads", {"idempotency_key": "lead-stage-job:" + str(uuid.uuid4()), "time_zone": time_zone})


def call_verb(verb, args):
    result = record_call.call_verb(verb, args, timeout=120)
    if not result.ok:
        # Errors may contain source identifiers. Console carries only operation
        # and outcome; no captured source text is echoed into a public log.
        raise RuntimeError(f"{verb} failed ({result.kind})")
    return result.reply


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--evidence-file", type=Path)
    parser.add_argument("--time-zone", default="America/Chicago")
    args = parser.parse_args()
    evidence = json.loads(args.evidence_file.read_text()) if args.evidence_file else None
    if evidence is not None and not isinstance(evidence, list):
        parser.error("derived evidence must be an array")
    result = run_job(call_verb, dry_run=args.dry_run, evidence=evidence, time_zone=args.time_zone)
    print(json.dumps(result))


if __name__ == "__main__":
    main()
