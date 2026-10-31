#!/usr/bin/env python3
"""Import a local CMS public file through the typed origination contract."""
from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
from datetime import date
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.routines.lead_signals import NPPES_ORIGINATION_COLUMNS, origination_rows as nppes_rows


def import_file(path, *, source, published_on, call):
    """Resume only acknowledged batches. A parse failure blocks only this feed."""
    spec = importlib.util.spec_from_file_location("pecos", ROOT / "pipelines/radar/build-pecos-pool.py")
    pecos = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(pecos)
    coverage = call("list-practice-origination", {})["coverage"]
    feed = next(row for row in coverage if row["source"] == source)
    checkpoint = feed.get("checkpoint")
    prefix = "unavailable:" + published_on.isoformat() + ":"
    acknowledged = 0
    inserted = 0
    processed = 0

    def submit(records, status="available", detail="Local CMS public file imported"):
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
            reader = csv.DictReader(stream)
            required = NPPES_ORIGINATION_COLUMNS if source == "nppes" else pecos.REQUIRED_COLUMNS
            if not required.issubset(reader.fieldnames or []) or len(reader.fieldnames) != len(set(reader.fieldnames)):
                raise ValueError(source.upper() + " schema drift; missing or duplicate columns")
            for raw in reader:
                processed += 1
                if None in raw or any(value is None for value in raw.values()):
                    raise ValueError(source.upper() + " row shape drift")
                if processed <= acknowledged:
                    continue
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
    parser.add_argument("source", choices=["nppes", "pecos"])
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
