#!/usr/bin/env python3
"""Retain deterministic text for existing deal attachments without model calls."""
import argparse
import importlib.util
import json
from pathlib import Path
import sys
import subprocess
from uuid import UUID

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from lib.deal_document_text import backfill


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--max-documents', type=int, default=10000)
    parser.add_argument('--after', type=UUID)
    args = parser.parse_args()
    spec = importlib.util.spec_from_file_location('extraction_adapter', ROOT / 'bin/deal-extraction-job.py')
    adapter = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(adapter)
    def scan(after, limit):
        return adapter.call('list-deal-document-text-sources', {'after': after, 'limit': limit})['documents']
    def record(value):
        result = adapter.call('record-deal-document-text', value)
        if not result.get('ok'):
            raise RuntimeError('retention refused')
    try:
        result = backfill(scan=scan, read_bytes=adapter.read_bytes, text_pages=adapter.text_pages,
                          record=record, max_documents=args.max_documents, dry_run=args.dry_run,
                          after=str(args.after) if args.after else None)
    except (ValueError, RuntimeError, OSError, subprocess.SubprocessError) as error:
        print(json.dumps({'ok': False, 'error': type(error).__name__}), file=sys.stderr)
        return 1
    print(json.dumps({'ok': True, **result}, sort_keys=True))
    return 0


if __name__ == '__main__':
    sys.exit(main())
