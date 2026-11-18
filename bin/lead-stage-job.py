#!/usr/bin/env python3
"""Run the evidence-driven stage job; optionally admit a local derived batch.

--dry-run only calls the preview read. Local source adapters can call
record-lead-contact directly, or supply --evidence-file outside the checkout.
No provider API, mailbox credentials, message bodies, or send operation here.
"""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import sys
from typing import NamedTuple
import uuid

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from lib import engine_mail, local_jobs, outlook_settings, record_call  # noqa: E402
FIELDS = {"lead", "native_ref", "counterparty_address", "kind", "occurred_at",
          "draft_body_sha256", "ended_at", "attended", "automated", "first_contact_draft_id", "lead_stage_signal", "follow_up_after", "archive_reason"}
INVOICE_FIELDS = {"native_ref", "from_address", "deal_name", "client_name", "property_address", "occurred_at"}


class InvoiceClose(NamedTuple):
    invoice_id: str
    deal_id: str
    payment_id: str
    for_user: str


def list_engine_invoice_closes(credentials: Path) -> list[InvoiceClose]:
    """Read engine candidates with a selected payment and its deal's partner slug."""
    raise RuntimeError("Engine invoice list is pending")


@dataclass(frozen=True)
class EngineInvoiceCloses:
    credentials: Path
    receipts: Path

    def run(self, *, dry_run: bool) -> dict[str, int]:
        invoices = list_engine_invoice_closes(self.credentials)
        if dry_run:
            return {"candidates": len(invoices)}
        if not invoices:
            return {}
        self.receipts.mkdir(mode=0o700, parents=True, exist_ok=True)
        counts: Counter[str] = Counter()
        doors: dict[str, engine_mail.Door] = {}
        with local_jobs.locked(self.receipts / "run.lock"):
            for invoice in invoices:
                key = str(uuid.uuid5(uuid.NAMESPACE_URL, json.dumps(["apply-invoice-email", *invoice])))
                # A terminal refusal can leave an invoice captured in the engine.
                refused = self.receipts / key
                if refused.exists():
                    counts["refused"] += 1
                    continue
                if invoice.for_user not in doors:
                    doors[invoice.for_user] = engine_mail.connect(self.credentials, invoice.for_user, job="lead_stage")
                outcome = apply_engine_invoice(doors[invoice.for_user], invoice, key)
                if outcome == "refused":
                    refused.touch(mode=0o600)
                counts[outcome] += 1
        return dict(counts)


def apply_engine_invoice(door: engine_mail.Door, invoice: InvoiceClose, key: str) -> str:
    path = f"/jobs/invoice-emails/{invoice.invoice_id}/apply"
    target = {"deal_id": invoice.deal_id, "payment_id": invoice.payment_id}
    body = json.dumps({**target, "idempotency_key": key}).encode()
    for _ in range(3):
        reply = door("POST", path, body)
        if 400 <= reply.status < 500:
            return "refused"
        if reply.status == 200:
            answer = reply.body
            if all(answer.get(k) == v for k, v in {**target, "invoice_id": invoice.invoice_id}.items()):
                status = answer.get("status")
                if status == "applied" and "application_id" in answer and answer["application_id"] is None:
                    return status
                if status == "attached" and isinstance(answer.get("application_id"), str):
                    return status
            return "unavailable"
        if 0 < reply.status < 500:
            return "unavailable"
    return "unavailable"


def run_job(call, *, dry_run=False, evidence=None, time_zone="America/Chicago",
            engine_invoices: EngineInvoiceCloses | None = None):
    invoice_mode = {"engine_invoice_closes": True} if engine_invoices is not None else {}
    if dry_run:
        if evidence is not None:
            raise ValueError("dry run reads stored evidence; omit --evidence-file")
        result = call("lead-stage-preview", invoice_mode)
    else:
        captures = []
        for item in evidence or []:
            invoice = isinstance(item, dict) and "deal_name" in item
            allowed = INVOICE_FIELDS if invoice else FIELDS
            required = {"native_ref", "from_address", "deal_name", "occurred_at"} if invoice else {"lead", "native_ref", "counterparty_address", "kind", "occurred_at"}
            if not isinstance(item, dict) or set(item) - allowed:
                raise ValueError("derived evidence has unsupported fields")
            if not required <= set(item):
                raise ValueError("derived evidence is incomplete")
            if invoice and engine_invoices is not None:
                raise ValueError("Engine invoice capture belongs to the mail job")
            verb = "record-deal-invoice" if invoice else "record-lead-contact"
            identity = [verb, item["native_ref"]] if invoice else [item["lead"], item["native_ref"], item["kind"]]
            key = str(uuid.uuid5(uuid.NAMESPACE_URL, ("deal-invoice:" if invoice else "lead-contact:") + hashlib.sha256(json.dumps(identity).encode()).hexdigest()))
            captures.append((verb, {**item, "idempotency_key": key}))
        for verb, args in captures:
            call(verb, args)
        result = call("advance-leads", {"idempotency_key": str(uuid.uuid4()), "time_zone": time_zone, **invoice_mode})
    if engine_invoices is not None:
        return {**result, "engine_invoice_closes": engine_invoices.run(dry_run=dry_run)}
    return result


def require_record_reply(verb, args):
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
    settings = outlook_settings.load_lead_stage_settings()
    engine_invoices = None
    if settings is not None:
        assert settings.engine_credentials is not None
        scope = hashlib.sha256(str(settings.engine_credentials).encode()).hexdigest()
        engine_invoices = EngineInvoiceCloses(settings.engine_credentials, settings.source.parent / "lead-stage-refusals" / scope)
    result = run_job(require_record_reply, dry_run=args.dry_run, evidence=evidence, time_zone=args.time_zone,
                     engine_invoices=engine_invoices)
    if engine_invoices is None:
        print(json.dumps(result))
    else:
        counts = result["engine_invoice_closes"]
        print(json.dumps({"lead_moves": len(result["moves"]), "drafts_prepared": result.get("drafts_prepared", 0),
                          "engine_invoice_closes": counts}))
        if counts.get("unavailable"):
            raise SystemExit(1)


if __name__ == "__main__":
    main()
