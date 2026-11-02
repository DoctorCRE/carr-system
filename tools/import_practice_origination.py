#!/usr/bin/env python3
"""Import a local public file through the typed origination contract."""
from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.util
import json
import re
from pathlib import Path
import subprocess
import sys
from datetime import date, datetime
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.routines.lead_signals import NPPES_ORIGINATION_COLUMNS, origination_rows as nppes_rows


# All offsets are a synthetic fixture contract, not verified Sunbiz offsets.
# Replace from the official usage guide and a real daily corporate file before enabling.
# https://dos.fl.gov/sunbiz/other-services/data-downloads/data-usage-guide/
FLORIDA_SOURCE = next(source for source in json.loads(
    (ROOT / "mcp-server/src/origination-sources.json").read_text()) if source["source"] == "formation-fl")
FLORIDA_LAYOUT = {
    "document_number": (0, 12),
    "name": (12, 204),
    "filing_on": (204, 212),
    "address": (212, 254),
    "city": (254, 282),
    "state": (282, 284),
    "registered_agent_address": (284, 326),
    "postal_code": (326, 336),
}
HEALTHCARE_NAME = re.compile(FLORIDA_SOURCE["healthcare_name_pattern"], re.I)


def florida_rows(line, *, published_on, line_number):
    """Parse only the declared provisional layout; a name match warrants research."""
    raw = line.rstrip("\r\n")
    error = f"Florida corporate line {line_number}: "
    if len(raw) != max(end for _, end in FLORIDA_LAYOUT.values()):
        raise ValueError(error + "fixed-width schema drift")
    row = {key: raw[start:end].strip() for key, (start, end) in FLORIDA_LAYOUT.items()}
    if not re.fullmatch(r"[A-Z0-9]{12}", row["document_number"]) or not row["name"] or not row["city"]:
        raise ValueError(error + "missing or invalid document identity or principal city")
    if not re.fullmatch(r"[A-Z]{2}", row["state"]):
        raise ValueError(error + "invalid principal state")
    try:
        filing_on = datetime.strptime(row["filing_on"], "%Y%m%d").date()
        if len(row["filing_on"]) != 8 or filing_on > published_on:
            raise ValueError()
    except ValueError as exc:
        raise ValueError(error + "invalid filing date") from exc
    if not re.fullmatch(r"[0-9]{5}(?:-[0-9]{4})?", row["postal_code"]):
        raise ValueError(error + "invalid principal postal code")
    if row["state"] != "FL" or row["postal_code"][:3] not in FLORIDA_SOURCE["territory_zip_prefixes"] or not HEALTHCARE_NAME.search(row["name"]):
        return []
    return [{"record_key": row["document_number"], "document_number": row["document_number"],
             "name": row["name"], "city": row["city"], "state": row["state"],
             "filing_on": filing_on.isoformat(), "evidence_date": filing_on.isoformat(),
             "date_basis": "filing",
             "source_url": "https://dos.fl.gov/sunbiz/other-services/data-downloads/daily-data/",
             "facts": {"address": row["address"], "registered_agent_address": row["registered_agent_address"],
                       "postal_code": row["postal_code"], "healthcare_name_match": True}}]


def import_file(path, *, source, published_on, call):
    """Resume only acknowledged batches. A parse failure blocks only this feed."""
    spec = importlib.util.spec_from_file_location("pecos", ROOT / "pipelines/radar/build-pecos-pool.py")
    pecos = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(pecos)
    coverage = call("list-practice-origination", {})["coverage"]
    feed = next(row for row in coverage if row["source"] == source)
    if source == "formation-fl" and not feed.get("enabled"):
        raise ValueError("Florida formation source disabled; confirm the field layout against a real file first")
    checkpoint = feed.get("checkpoint")
    prefix = "unavailable:" + published_on.isoformat() + ":"
    acknowledged = 0
    inserted = 0
    processed = 0

    def submit(records, status="available", detail="Local public file imported"):
        nonlocal checkpoint, inserted
        result = call("record-practice-origination-feed", {
            "idempotency_key": str(uuid4()), "source": source,
            "expected_checkpoint": checkpoint, "checkpoint": prefix + str(processed),
            "published_on": published_on.isoformat(), "status": status,
            "detail": detail[:500], "records": records})
        checkpoint = result["checkpoint"]
        inserted += result["inserted"]

    pending = []
    try:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        prefix = digest.hexdigest() + ":" + published_on.isoformat() + ":"
        acknowledged = int(checkpoint[len(prefix):]) if checkpoint and checkpoint.startswith(prefix) else 0
        with path.open(newline="", encoding="utf-8-sig" if source == "nppes" else "latin1") as stream:
            reader = stream if source == "formation-fl" else csv.DictReader(stream)
            if source != "formation-fl":
                required = NPPES_ORIGINATION_COLUMNS if source == "nppes" else pecos.REQUIRED_COLUMNS
                if not required.issubset(reader.fieldnames or []) or len(reader.fieldnames) != len(set(reader.fieldnames)):
                    raise ValueError(source.upper() + " schema drift; missing or duplicate columns")
            for raw in reader:
                processed += 1
                if source != "formation-fl" and (None in raw or any(value is None for value in raw.values())):
                    raise ValueError(source.upper() + " row shape drift")
                if processed <= acknowledged:
                    continue
                if source == "formation-fl":
                    pending.extend(florida_rows(raw, published_on=published_on, line_number=processed))
                else:
                    pending.extend(nppes_rows([raw], as_of=published_on) if source == "nppes" else
                                   pecos.origination_rows([raw], published_on=published_on))
                if processed % 100 == 0:
                    submit(pending)
                    pending = []
            if processed > acknowledged or not checkpoint or feed.get("status") != "available":
                submit(pending)
    except OSError:
        submit([], "unavailable", "Local public source file is inaccessible")
        raise
    except (ValueError, csv.Error) as exc:
        submit([], "blocked", str(exc))
        raise
    return {"inserted": inserted, "checkpoint": checkpoint, "processed": processed}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", choices=["nppes", "pecos", "formation-fl"])
    parser.add_argument("path", type=Path)
    parser.add_argument("--published-on", required=True, type=date.fromisoformat)
    parser.add_argument("--apply", action="store_true", help="Write evidence through authenticated CARR verbs")
    args = parser.parse_args()
    if not args.apply:
        parser.error("--apply is required to import; no source files are fetched")

    def call(verb, payload):
        result = subprocess.run([str(ROOT / "run.sh"), "call", verb, json.dumps(payload)],
                                check=True, capture_output=True, text=True, cwd=ROOT, timeout=120)
        response = json.loads(result.stdout)
        if response.get("ok") is False:
            raise RuntimeError("Origination call refused: " + str(response.get("error")))
        return response

    print(json.dumps(import_file(args.path, source=args.source, published_on=args.published_on, call=call)))


if __name__ == "__main__":
    main()
