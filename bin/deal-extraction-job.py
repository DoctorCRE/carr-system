#!/usr/bin/env python3
"""Run a stored PDF text-layer job and submit one proposal-only fact batch."""
import argparse
from decimal import Decimal
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from uuid import UUID

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from lib.deal_extraction import ExtractionJob, MODELS, run_job
from lib.model_call import ask_with_usage
from lib.deal_ocr import ocr_regions
from lib.record_call import call_verb
from lib.r2_archive import BUCKET, wrangler_bin


def call(verb, args):
    result = call_verb(verb, args)
    if not result.ok:
        raise RuntimeError(f'{verb} refused or unavailable ({result.kind})')
    return result.reply


def read_bytes(doc):
    if doc['mime'] != 'application/pdf' or not 0 < doc['bytes'] <= 25 * 1024 * 1024:
        raise ValueError('only stored PDF text layers up to 25 MB are supported')
    binary = wrangler_bin()
    if binary is None:
        raise RuntimeError('authenticated R2 reader unavailable')
    with tempfile.TemporaryDirectory(prefix='deal-extraction-bytes-') as folder:
        target = Path(folder) / 'source.pdf'
        result = subprocess.run([binary, 'r2', 'object', 'get', f"{BUCKET}/{doc['r2_key']}",
                                 '--remote', '--file', str(target)],
                                capture_output=True, timeout=120, cwd=REPO / 'mcp-server')
        if result.returncode:
            raise RuntimeError('authenticated stored object read failed')
        if target.stat().st_size > 25 * 1024 * 1024:
            raise ValueError('stored object exceeds size limit')
        return target.read_bytes()


def text_pages(data):
    with tempfile.TemporaryDirectory(prefix='deal-extraction-text-') as folder:
        source, target = Path(folder) / 'source.pdf', Path(folder) / 'text.txt'
        source.write_bytes(data)
        result = subprocess.run(['pdftotext', '-layout', '-enc', 'UTF-8', str(source), str(target)],
                                capture_output=True, timeout=60)
        if result.returncode:
            return ['']
        text = target.read_text()
        pages = text.split('\f')
        return pages[:-1] if pages[-1] == '' else pages


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--job-id', type=UUID, required=True)
    parser.add_argument('--entity', type=UUID, required=True)
    parser.add_argument('--document', type=UUID, required=True)
    parser.add_argument('--document-type', choices=['loi', 'lease', 'counter', 'amendment'], required=True)
    parser.add_argument('--model', choices=MODELS, required=True)
    parser.add_argument('--max-budget-usd', type=Decimal, default=Decimal('0.50'))
    args = parser.parse_args()
    job = ExtractionJob(args.job_id, args.entity, args.document, args.document_type, args.model, args.max_budget_usd)
    try:
        result = run_job(job, call=call, read_bytes=read_bytes, text_pages=text_pages, model=ask_with_usage, ocr_regions=ocr_regions)
    except (ValueError, RuntimeError, OSError, subprocess.SubprocessError):
        print('Deal extraction failed; no success receipt. Retry with the same job ID.', file=sys.stderr)
        return 1
    print(json.dumps({k: result.get(k) for k in ('ok', 'job_id', 'batch_id', 'canonical', 'replayed')}))
    return 0


if __name__ == '__main__':
    sys.exit(main())
