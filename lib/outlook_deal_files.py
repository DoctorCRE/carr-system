"""Archive one matched-mail attachment in R2 and record it on its deal (WR-000510).

Given the decoded attachment metadata and its bytes, `file_attachment` re-detects the
type from the bytes (the declared type and the name are not trusted), enforces the size
cap and the deal-document types, uploads under r2_archive's quota guard through the
`put_object` port, then records the file through the `attach-deal-file` verb through the
`carr` port. The key is content-addressed and the verb idempotent, so a rerun files nothing
twice. One bad attachment returns a status and never raises; results hold no file names.
"""
from __future__ import annotations

import hashlib
import os
import re
import tempfile
import uuid
from typing import Callable

from lib import outlook_attachments as att
from lib import r2_archive

EXTENSIONS = {'pdf': '.pdf', 'docx': '.docx', 'xlsx': '.xlsx', 'pptx': '.pptx'}
OLE_EXTENSIONS = ('.doc', '.xls', '.ppt')


def _name(raw: object, kind: str) -> str:
    base = re.sub(r'[\x00-\x1f\\/]+', '_', os.path.basename(str(raw or '').replace('\\', '/'))).strip(' .')[:200]
    base = base if base else 'attachment'
    ext = EXTENSIONS.get(kind)
    if ext and not base.lower().endswith(ext):
        return base + ext
    if kind == 'ole' and not base.lower().endswith(OLE_EXTENSIONS):
        return base + '.ole'
    return base


def file_attachment(deal: str, meta: dict, data: bytes, *, message_id: str, client_ref: str | None,
                    put_object: Callable[[str, str, bytes], dict], carr: Callable[[str, dict], dict]) -> dict:
    """Status of one attachment: stored, existing, or a refusal reason; never raises."""
    kind = att.detect(data)
    if kind not in att.DEAL_TYPES:
        return {'status': 'not_deal_type'}
    if not data or len(data) > att.CAP:
        return {'status': 'too_large' if data else 'empty'}
    sha = hashlib.sha256(data).hexdigest()
    filename = _name(meta.get('name'), kind)
    key = r2_archive.object_key(client_ref, sha, filename)
    try:
        put_object(key, sha, data)
    except r2_archive.QuotaExceeded:
        return {'status': 'quota_refused', 'sha256': sha}
    except Exception as error:  # noqa: BLE001 - one upload failure must not stop the rest
        return {'status': 'upload_failed', 'sha256': sha, 'error': type(error).__name__}
    source = f"{message_id}/{meta.get('id')}"
    args = {'idempotency_key': str(uuid.uuid5(uuid.NAMESPACE_URL, f'attach-deal-file|{deal}|{source}|{sha}')),
            'deal': deal, 'r2_key': key, 'filename': filename, 'mime': att.DEAL_TYPES[kind],
            'sha256': sha, 'bytes': len(data), 'source': source}
    try:
        reply = carr('attach-deal-file', args)
    except Exception as error:  # noqa: BLE001 - the object stays archived; a rerun records it
        return {'status': 'verb_failed', 'sha256': sha, 'error': type(error).__name__}
    if not isinstance(reply, dict) or reply.get('ok') is not True or not reply.get('attachment_id'):
        return {'status': 'verb_failed', 'sha256': sha}
    return {'status': 'existing' if reply.get('existing') else 'stored', 'sha256': sha,
            'attachment_id': reply.get('attachment_id')}


def r2_put_object(conn=None) -> Callable[[str, str, bytes], dict]:
    """Production port: upload through r2_archive's ledger and hard quota, via a 0600 temp file."""
    def put(key: str, sha: str, data: bytes) -> dict:
        ledger = r2_archive.load_ledger()
        cap, provenance = r2_archive.quota_bytes(conn)
        with tempfile.TemporaryDirectory(prefix='outlook-deal-file-') as folder:
            path = os.path.join(folder, os.path.basename(key))
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, 'wb') as f:
                f.write(data)
            return r2_archive.upload(path, key, sha, len(data), cap, provenance, ledger)
    return put
