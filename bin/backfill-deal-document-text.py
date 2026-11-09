#!/usr/bin/env python3
"""Retain deterministic text for existing deal attachments without model calls."""
import argparse
import importlib.util
import json
import shutil
from pathlib import Path
import sys
import subprocess
from uuid import UUID

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from lib.deal_document_text import backfill
from lib.r2_archive import BUCKET, wrangler_bin


class StorageReaderError(RuntimeError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


def preflight_reader():
    binary = wrangler_bin()
    if not binary:
        raise StorageReaderError('storage_reader_missing', 'Wrangler is missing; run npm --prefix mcp-server ci.')
    if not shutil.which('pdftotext'):
        raise StorageReaderError('parser_missing', 'pdftotext is missing; install poppler (brew install poppler or apt-get install poppler-utils).')
    try:
        parser = subprocess.run(['pdftotext', '-v'], capture_output=True, timeout=10)
        if parser.returncode:
            raise OSError('parser probe failed')
    except (OSError, subprocess.SubprocessError) as error:
        raise StorageReaderError('parser_unavailable', 'pdftotext failed; repair poppler (brew reinstall poppler or apt-get install --reinstall poppler-utils).') from error
    try:
        result = subprocess.run([binary, 'r2', 'bucket', 'info', BUCKET], capture_output=True,
                                timeout=90, cwd=ROOT / 'mcp-server')
        if result.returncode:
            raise StorageReaderError('storage_reader_unavailable', 'Wrangler cannot access R2; check Cloudflare authentication and bucket access.')
    except (OSError, subprocess.SubprocessError) as error:
        raise StorageReaderError('storage_reader_unavailable', 'Wrangler failed; check Cloudflare authentication and bucket access.') from error


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
        if not args.dry_run:
            preflight_reader()
        result = backfill(scan=scan, read_bytes=adapter.read_bytes, text_pages=adapter.text_pages,
                          record=record, max_documents=args.max_documents, dry_run=args.dry_run,
                          after=str(args.after) if args.after else None)
    except StorageReaderError as error:
        print(json.dumps({'ok': False, 'error': error.code, 'remedy': str(error)}), file=sys.stderr)
        return 1
    except (ValueError, RuntimeError, OSError, subprocess.SubprocessError):
        print(json.dumps({'ok': False, 'error': 'backfill_failed', 'remedy': 'Check the scan service and arguments, then retry.'}), file=sys.stderr)
        return 1
    print(json.dumps({'ok': not result['errors'], **result}, sort_keys=True))
    return 1 if result['errors'] else 0


if __name__ == '__main__':
    sys.exit(main())
