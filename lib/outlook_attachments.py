"""Outlook attachment bytes from a private profile copy: type by magic bytes, size cap, sha256.

hxprobe's `db` export lists each attachment with its message, declared MIME type,
size and profile-relative cache path (`~/Files/S0/<n>/Attachments/<k>/<file>`).
This module resolves that path inside a caller's private copy of the profile's
`Files` folder, never the live profile, and reads at most `cap` bytes. The type is
detected from the file's own bytes; the declared type and the file name are never
trusted for it. Only deal document types (PDF, Word, Excel, PowerPoint, and legacy
Office) are written, as `<sha256>.<ext>` with mode 0600, to a caller-named folder,
and only for message rows the caller names (its matched mail), never for all mail.
One call reads at most MAX_FILES files and MAX_TOTAL bytes; one bad attachment gets
an `error` status and never stops the rest.

    python -m lib.outlook_attachments <store.db> <Files copy> [<out dir> <message id>...]

prints one JSON object per attachment. Run it only on private copies: the output
holds file names.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import sys
import zipfile

CAP = 25 * 1024 * 1024
MAX_FILES = 50
MAX_TOTAL = 100 * 1024 * 1024
DEAL_TYPES = {'pdf': 'application/pdf',
              'docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
              'xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
              'pptx': 'application/vnd.openxmlformats-officedocument.presentationml.presentation',
              'ole': 'application/x-ole-storage'}
CACHE_PATH = re.compile(r'~/Files/S0/(\d+)/Attachments/(\d+)/([^/\\]+)')
OOXML = (('word/', 'docx'), ('xl/', 'xlsx'), ('ppt/', 'pptx'))


def detect(data: bytes) -> str | None:
    """A short type name from the leading bytes (and, for ZIP, the member names)."""
    if data.startswith(b'%PDF-'):
        return 'pdf'
    if data.startswith(b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1'):
        return 'ole'
    if data.startswith(b'\x89PNG\r\n\x1a\n'):
        return 'png'
    if data.startswith(b'\xff\xd8\xff'):
        return 'jpeg'
    if data[:6] in (b'GIF87a', b'GIF89a'):
        return 'gif'
    if data.startswith(b'PK\x03\x04'):
        try:
            names = zipfile.ZipFile(io.BytesIO(data)).namelist()
        except Exception:  # zipfile raises several types on hostile input
            return None
        if '[Content_Types].xml' in names:
            for prefix, kind in OOXML:
                if any(n.startswith(prefix) for n in names):
                    return kind
        return 'zip'
    return None


def _read(files: Path, path: str, cap: int) -> tuple[str, bytes | None]:
    """Read one cached file inside `files`, refusing links and anything over `cap`."""
    match = CACHE_PATH.fullmatch(path or '')
    if not match or match.group(3) in ('.', '..'):
        return 'path_refused', None
    store, slot, name = match.groups()
    target = files / 'S0' / store / 'Attachments' / slot / name
    if any(parent.is_symlink() for parent in target.parents if parent != files and files in parent.parents):
        return 'path_refused', None
    try:
        fd = os.open(target, os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        return 'not_cached', None
    except OSError:
        return 'unreadable', None
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            return 'unreadable', None
        if info.st_size > cap:
            return 'too_large', None
        with os.fdopen(fd, 'rb', closefd=False) as f:
            data = f.read(cap + 1)
    finally:
        os.close(fd)
    if len(data) > cap:
        return 'too_large', None
    return 'read', data


def _write(out: Path, digest: str, kind: str, data: bytes) -> Path:
    out.mkdir(mode=0o700, parents=True, exist_ok=True)
    dest = out / f'{digest}.{kind}'
    if not dest.exists():
        tmp = out / f'.{digest}.tmp'
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'wb') as f:
            f.write(data)
        os.replace(tmp, dest)
    return dest


def rows(db: Path) -> list[dict]:
    """Decoded attachments with the message row they belong to, or [] for older decoders.

    hxprobe can split one message into several rows; a message key found on more than
    one row names no single message, so its attachments are left out rather than guessed."""
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    try:
        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {'attachments', 'message_keys'} <= tables:
            return []
        return [dict(r) for r in con.execute(
            'WITH k AS (SELECT local_key, MIN(message_id) AS message_id FROM message_keys '
            '           GROUP BY local_key HAVING COUNT(DISTINCT message_id) = 1) '
            'SELECT k.message_id AS message, a.id AS attachment, a.name, a.name_verified, '
            'a.content_type AS declared_type, a.size AS declared_size, a.path '
            'FROM attachments a JOIN k ON k.local_key = a.message_key '
            'ORDER BY k.message_id, a.id')]
    finally:
        con.close()


def extract(db: Path, files: Path, out: Path | None = None, cap: int = CAP,
            messages: set[int] | None = None, max_files: int = MAX_FILES,
            max_total: int = MAX_TOTAL, attachments: set[int] | None = None) -> list[dict]:
    """One result per attachment; bytes are written only for deal types when `out` is set.

    `messages` limits the work to those message row ids (the caller's matched mail) and
    is required whenever `out` is set. Without `out`, None reads every message.
    `attachments` restricts work before batch caps, so completed files cannot starve retries."""
    if out is not None and messages is None:
        raise ValueError('writing attachments requires the matched message ids')
    files = Path(files)
    results, read_files, read_bytes = [], 0, 0
    for row in rows(db):
        if (messages is not None and row['message'] not in messages) or (attachments is not None and row['attachment'] not in attachments):
            continue
        result = {**row, 'status': 'batch_cap', 'detected_type': None, 'byte_length': None,
                  'sha256': None, 'stored': None}
        results.append(result)
        if read_files >= max_files or read_bytes >= max_total:
            continue
        try:
            status, data = _read(files, row['path'], min(cap, max_total - read_bytes))
            if status == 'too_large' and cap > max_total - read_bytes:
                status = 'batch_cap'
            result['status'] = status
            if data is None:
                continue
            read_files += 1
            read_bytes += len(data)
            kind = detect(data)
            digest = hashlib.sha256(data).hexdigest()
            result.update(detected_type=kind, byte_length=len(data), sha256=digest)
            if kind not in DEAL_TYPES:
                result['status'] = 'type_refused'
            elif out is not None:
                result.update(status='stored', stored=str(_write(Path(out), digest, kind, data)))
        except Exception as error:  # one attachment never stops the batch
            result.update(status='error', error=type(error).__name__, stored=None)
    return results


def main(argv: list[str]) -> int:
    if len(argv) == 4 or len(argv) < 3 or not all(a.isdigit() for a in argv[4:]):
        print('usage: python -m lib.outlook_attachments <store.db> <Files copy> [<out dir> <message id>...]',
              file=sys.stderr)
        return 2
    out = Path(argv[3]) if len(argv) > 4 else None
    messages = {int(a) for a in argv[4:]} if out is not None else None
    for result in extract(Path(argv[1]), Path(argv[2]), out, messages=messages):
        print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main(sys.argv))
