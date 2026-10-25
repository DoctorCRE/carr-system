#!/usr/bin/env python3
"""
r2_archive.py — the quota-guarded R2 uploader (ORDER 20).

WHAT THIS IS FOR. Every document the factory produces gets an archive copy in
the Cloudflare R2 bucket `carr-documents`, so a file that lives on Joe's Mac and
in his OneDrive also lives somewhere neither a disk nor a sync client can lose.
`attachment.r2_key` is `not null unique` in 0001, so an attachment row cannot
exist until an object exists: the upload is what makes the row insertable, which
is why ORDER 13 had to leave the rows owed and why this module closes that.

THE QUOTA IS JOE'S REQUIREMENT AND IT IS A HARD CAP, NOT AN ALERT (2026-07-31).
Cloudflare's R2 free tier is 10 GB and the account is on it. A billing alert
tells you after the money starts; a cap refuses before it does. So:

  · `system_config` row `r2.quota_gb` holds the cap. WHEN THAT ROW IS ABSENT
    THIS MODULE DEFAULTS TO 8 GB — deliberately under the 10 GB free tier, so a
    missing config row can never be the reason a bill arrives.
  · THE PIPELINE IS THE BUCKET'S SOLE WRITER. Nothing else uploads: no Worker
    route, no dashboard habit, no public access of any kind. That is what makes
    a local ledger trustworthy enough to gate on.
  · Before every upload the ledger (`out/r2-usage.json`) is read and the upload
    is REFUSED if it would land past the cap. Nothing is deleted to make room,
    ever. The file stays on OneDrive, the archive copy is recorded as owed, and
    the report says exactly why.

THREE SOURCES OF TRUTH AND WHAT EACH IS FOR, because a quota guard that
under-counts is worse than no quota guard at all:
  1. the LEDGER — local, instant, and what the per-upload gate actually reads.
  2. the BUCKET's own reported size (`wrangler r2 bucket info`), consulted once
     per run rather than once per upload. It LAGS: measured 2026-07-31, two
     objects verified present by downloading them back still read as
     `object_count: 0` afterwards.
  3. the ATTACHMENT ROWS — the record layer's view, low by construction between
     an upload and the tap that writes its row.
THE LEDGER IS A FLOOR. Any source reporting MORE wins and rebuilds it upward;
any source reporting LESS is printed and never applied. Under-counting is the
only failure that costs money, so every ambiguity resolves upward.

Keys are content-addressed: `documents/<client_ref>/<sha256[:16]>/<filename>`.
Re-uploading the same bytes is a no-op that costs nothing and writes no second
row, which is what makes a rerun idempotent by construction rather than by a
check somebody has to remember.
"""
from __future__ import annotations

from contextlib import contextmanager
import fcntl
import json
import os
import re
import shutil
import subprocess
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LEDGER = os.path.join(REPO, "out", "r2-usage.json")
BUCKET = "carr-documents"

# The floor the whole design rests on. The Cloudflare free tier is 10 GB; this
# sits under it on purpose so that a missing `system_config` row degrades to
# "more careful than configured", never to "unbounded".
DEFAULT_QUOTA_GB = 8
GB = 1024 ** 3

# Wrangler is a repo dependency, not a global one — `which wrangler` returns
# nothing on this Mac and ORDER 13's `shutil.which` check would have silently
# read that as "R2 unavailable". Look where it actually is first.
WRANGLER_CANDIDATES = [
    os.path.join(REPO, "mcp-server", "node_modules", ".bin", "wrangler"),
    "/opt/homebrew/bin/wrangler",
]


class QuotaExceeded(Exception):
    """Raised instead of uploading. Carries the full honest message."""

    def __init__(self, message: str, detail: dict):
        super().__init__(message)
        self.message = message
        self.detail = detail


def wrangler_bin() -> str | None:
    for c in WRANGLER_CANDIDATES:
        if os.path.exists(c) and os.access(c, os.X_OK):
            return c
    return shutil.which("wrangler")


def _wrangler_env() -> dict:
    env = dict(os.environ)
    env.setdefault("CLOUDFLARE_ACCOUNT_ID", "12ccca77eb49142a6be8eb84c0d6a3a0")
    env["PATH"] = "/opt/homebrew/bin:" + env.get("PATH", "")
    return env


# ----------------------------------------------------------------- the quota

def quota_bytes(conn=None) -> tuple[int, str]:
    """The cap, and where it came from. Returns (bytes, provenance sentence)."""
    gb = None
    if conn is not None:
        try:
            with conn.cursor() as cur:
                cur.execute("select value from system_config where key='r2.quota_gb'")
                row = cur.fetchone()
            if row and row[0] is not None:
                v = row[0]
                if isinstance(v, dict):          # {"gb": 8} shape, if it ever lands that way
                    v = v.get("gb", v.get("value"))
                if isinstance(v, (int, float, str)):
                    gb = float(v)
        except Exception as e:                                   # noqa: BLE001
            return (DEFAULT_QUOTA_GB * GB,
                    f"system_config unreadable ({type(e).__name__}); defaulting to "
                    f"{DEFAULT_QUOTA_GB} GB, which is under Cloudflare's 10 GB free tier")
    if gb is None:
        return (DEFAULT_QUOTA_GB * GB,
                f"system_config row r2.quota_gb is absent; defaulting to {DEFAULT_QUOTA_GB} GB, "
                f"which is under Cloudflare's 10 GB free tier")
    return int(gb * GB), f"system_config r2.quota_gb = {gb} GB"


# ---------------------------------------------------------------- the ledger

@contextmanager
def ledger_lock():
    """Hold across ledger load, reconciliation, uploads and usage-summary saves."""
    os.makedirs(os.path.dirname(LEDGER), exist_ok=True)
    with open(LEDGER + '.lock', 'a+b') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def load_ledger(bucket: str = BUCKET) -> dict:
    if os.path.exists(LEDGER):
        try:
            with open(LEDGER) as fh:
                led = json.load(fh)
            led.setdefault("objects", {})
            led.setdefault("bucket", bucket)
            return led
        except (json.JSONDecodeError, OSError):
            pass
    return {"bucket": bucket, "objects": {}, "bytes_used": 0,
            "created_at": _now(), "updated_at": _now(),
            "note": "sole-writer usage ledger for the R2 archive (ORDER 20). "
                    "Rebuilt from the bucket and from attachment rows on drift; "
                    "never hand-edited."}


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def ledger_bytes(led: dict) -> int:
    return sum(int(o.get("bytes", 0)) for o in led["objects"].values())


def save_ledger(led: dict) -> None:
    led["bytes_used"] = ledger_bytes(led)
    led["object_count"] = len(led["objects"])
    led["updated_at"] = _now()
    os.makedirs(os.path.dirname(LEDGER), exist_ok=True)
    tmp = LEDGER + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(led, fh, indent=2, sort_keys=True)
    os.replace(tmp, LEDGER)


# ------------------------------------------------------------ reconciliation

def bucket_size(bucket: str = BUCKET) -> tuple[int | None, int | None, str]:
    """The bucket's own figure. (bytes, object_count, note). None = unreachable."""
    w = wrangler_bin()
    if not w:
        return None, None, "wrangler not found; bucket size not consulted this run"
    try:
        p = subprocess.run([w, "r2", "bucket", "info", bucket],
                           capture_output=True, text=True, timeout=90, env=_wrangler_env())
    except subprocess.TimeoutExpired:
        return None, None, "wrangler r2 bucket info timed out; bucket size not consulted"
    if p.returncode != 0:
        return None, None, f"wrangler r2 bucket info failed: {(p.stderr or p.stdout).strip()[:200]}"
    out = p.stdout
    m = re.search(r"bucket_size:\s*([\d.]+)\s*([A-Za-z]+)", out)
    c = re.search(r"object_count:\s*(\d+)", out)
    if not m:
        return None, None, "bucket_size not present in wrangler output"
    val, unit = float(m.group(1)), m.group(2).upper()
    mult = {"B": 1, "KB": 1000, "MB": 1000 ** 2, "GB": 1000 ** 3, "TB": 1000 ** 4,
            "KIB": 1024, "MIB": 1024 ** 2, "GIB": 1024 ** 3}.get(unit, 1)
    return int(val * mult), (int(c.group(1)) if c else None), "read from the bucket"


def attachment_bytes(conn) -> tuple[int | None, int | None, str]:
    """What the record layer thinks is archived. None when unreadable."""
    if conn is None:
        return None, None, "no database connection; attachment rows not consulted"
    try:
        with conn.cursor() as cur:
            cur.execute("select coalesce(sum(bytes),0), count(*) from attachment where deleted_at is null")
            b, n = cur.fetchone()
        return int(b), int(n), "read from attachment rows"
    except Exception as e:                                       # noqa: BLE001
        return None, None, f"attachment rows unreadable ({type(e).__name__})"


def reconcile(led: dict, conn=None, bucket: str = BUCKET, consult_bucket: bool = True) -> dict:
    """Compare the three sources and correct the ledger. Returns a report dict.

    THE CORRECTION RULE IS ONE SENTENCE: the ledger is a FLOOR, so any source
    reporting MORE wins and a source reporting LESS is reported but never
    applied. It is stated here because it is a judgment, and because the obvious
    alternative is wrong in a way that testing caught rather than reasoning.

    The obvious alternative was "the bucket is ground truth, so the bucket
    always wins". MEASURED 2026-07-31: two objects were uploaded and verified by
    downloading them back byte-identical, and `wrangler r2 bucket info` still
    reported `object_count: 0, bucket_size: 0 B` for some time afterwards.
    Cloudflare's bucket metrics LAG. Letting a lagging zero win would have reset
    the ledger to zero immediately after every upload, which is a quota guard
    that quietly stops guarding. The same asymmetry covers attachment rows,
    where a low sum is the ordinary state between an upload and the tap that
    writes its row.

    Under-counting is the only failure mode that costs money here, so every
    ambiguity resolves toward the larger figure and the disagreement is printed.
    """
    lb = ledger_bytes(led)
    bb, bcount, bnote = (None, None, "bucket not consulted this run")
    if consult_bucket:
        bb, bcount, bnote = bucket_size(bucket)
    ab, acount, anote = attachment_bytes(conn)
    rep = {"ledger_bytes": lb, "ledger_objects": len(led["objects"]),
           "bucket_bytes": bb, "bucket_objects": bcount, "bucket_note": bnote,
           "attachment_bytes": ab, "attachment_rows": acount, "attachment_note": anote,
           "corrected": False, "correction": None}

    highest, source = lb, "the ledger"
    for val, name in ((bb, "the bucket"), (ab, "attachment rows")):
        if val is not None and val > highest:
            highest, source = val, name

    if highest > lb:
        # Carry the difference as one named adjustment entry rather than
        # inventing per-object rows nobody can point at a file for.
        led["objects"].pop("__reconciliation__", None)
        delta = highest - ledger_bytes(led)
        if delta > 0:
            led["objects"]["__reconciliation__"] = {
                "bytes": delta, "sha256": None, "uploaded_at": _now(),
                "why": f"{source} reports {highest} bytes, more than the named objects in this "
                       f"ledger ({ledger_bytes(led)}). Rebuilt upward: the ledger is a floor."}
        rep["corrected"] = True
        rep["correction"] = f"ledger rebuilt upward from {source} ({highest} bytes)"
    else:
        low = [f"{n} says {v}" for v, n in ((bb, "the bucket"), (ab, "attachment rows"))
               if v is not None and v < lb]
        if low:
            rep["correction"] = (
                f"{'; '.join(low)}, below this ledger's {lb}. NOT applied. Cloudflare's bucket "
                f"metrics lag behind an upload (measured), and rows owed for objects already "
                f"uploaded look exactly like this. Shrinking the figure would under-count the quota.")
    led["quota_note_reconciled_at"] = _now()
    save_ledger(led)
    return rep


# -------------------------------------------------------------- the refusal

def human_bytes(b: int) -> str:
    """Pick the unit that keeps the number legible. A refusal that reads
    '0.00 GB is over the cap by 0.00 GB' is a refusal nobody can act on."""
    b = int(b)
    for unit, size in (("GB", GB), ("MB", 1024 ** 2), ("KB", 1024)):
        if abs(b) >= size:
            return f"{b / size:,.2f} {unit}"
    return f"{b:,} bytes"


def refusal_message(filename: str, add_bytes: int, used: int, cap: int, provenance: str) -> str:
    """The honest refusal, verbatim. Joe reads this, so it says what happened,
    what did NOT happen, what is owed, and what would clear it."""
    g = human_bytes                                               # noqa: E731
    return (
        "R2 ARCHIVE REFUSED: the self-enforced quota is full.\n"
        f"  quota           {g(cap)}   ({provenance})\n"
        f"  in use          {g(used)}   (sole-writer ledger out/r2-usage.json)\n"
        f"  this upload     {g(add_bytes)}   {filename}\n"
        f"  would land at   {g(used + add_bytes)}   which is over the cap by {g(used + add_bytes - cap)}\n"
        "Nothing was uploaded, and nothing was deleted to make room. The file stays where it\n"
        "already is on OneDrive, which is the copy Joe actually opens; only the archive copy is\n"
        "missing. The R2 copy and its attachment row are recorded as OWED on the document row,\n"
        "so this is visible in the record rather than lost in a log.\n"
        "To clear it, either raise system_config r2.quota_gb (Joe's call; Cloudflare's free tier\n"
        "is 10 GB and this cap sits under it on purpose), or purge archived documents that no\n"
        "longer need keeping and run the same command again. A rerun after either is safe:\n"
        "objects already in the bucket are recognised and never uploaded twice."
    )


# --------------------------------------------------------------- the upload

def object_key(client_ref: str | None, sha256: str, filename: str) -> str:
    who = re.sub(r"[^A-Za-z0-9._-]+", "-", (client_ref or "unfiled")).strip("-") or "unfiled"
    safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", os.path.basename(filename))
    return f"documents/{who}/{sha256[:16]}/{safe_name}"


MIME = {".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ".pdf": "application/pdf"}


def mime_for(path: str) -> str:
    return MIME.get(os.path.splitext(path)[1].lower(), "application/octet-stream")


def object_exists(key: str, bucket: str = BUCKET) -> bool:
    w = wrangler_bin()
    if not w:
        return False
    p = subprocess.run([w, "r2", "object", "get", f"{bucket}/{key}", "--remote", "--pipe"],
                       capture_output=True, timeout=120, env=_wrangler_env())
    return p.returncode == 0


def upload(path: str, key: str, sha256: str, size: int, cap: int, provenance: str,
           led: dict, bucket: str = BUCKET, dry_run: bool = False) -> dict:
    """Upload one file under the cap. Raises QuotaExceeded instead of overrunning.

    Idempotent by construction: the key contains the content hash, so a rerun
    finds the key already in the ledger and uploads nothing.
    """
    known = led["objects"].get(key)
    if known and known.get("sha256") == sha256:
        return {"key": key, "uploaded": False, "reason": "already archived (same bytes, same key)",
                "bytes": size, "bucket": bucket}

    used = ledger_bytes(led)
    if used + size > cap:
        raise QuotaExceeded(
            refusal_message(os.path.basename(path), size, used, cap, provenance),
            {"quota_bytes": cap, "used_bytes": used, "upload_bytes": size,
             "over_by_bytes": used + size - cap, "key": key, "file": path})

    if dry_run:
        return {"key": key, "uploaded": False, "reason": "dry run; nothing sent", "bytes": size,
                "bucket": bucket}

    w = wrangler_bin()
    if not w:
        raise RuntimeError("wrangler not found; cannot upload. Looked in "
                           + ", ".join(WRANGLER_CANDIDATES))
    p = subprocess.run(
        [w, "r2", "object", "put", f"{bucket}/{key}", "--file", path, "--remote",
         "--content-type", mime_for(path)],
        capture_output=True, text=True, timeout=600, env=_wrangler_env())
    if p.returncode != 0:
        raise RuntimeError(f"R2 upload failed for {key}: {(p.stderr or p.stdout).strip()[:400]}")

    led["objects"][key] = {"bytes": size, "sha256": sha256, "uploaded_at": _now(),
                           "filename": os.path.basename(path)}
    save_ledger(led)
    return {"key": key, "uploaded": True, "reason": "uploaded", "bytes": size, "bucket": bucket}


def delete_object(key: str, led: dict, bucket: str = BUCKET, dry_run: bool = False) -> dict:
    """Remove one object from the bucket AND from the ledger. ADDED 2026-08-07.

    WHY IT EXISTS. Until now this module could only ever add. On 2026-08-07 a
    corrupt 200-byte backup was uploaded and recorded, and there was no code
    path that could take it back out — which meant the choice was between
    leaving a known-bad object in the durable archive claiming to be that day's
    backup, or hand-editing the ledger, which its own note forbids. A one-way
    ledger is not a ledger.

    THE ORDER IS THE BUCKET FIRST, THE LEDGER SECOND, and it is not arbitrary.
    If the bucket delete fails, the ledger still carries the object and the
    quota guard keeps counting bytes that exist. If the order were reversed, a
    failed delete would leave bytes in the bucket that the ledger has forgotten
    — under-counting, which reconcile()'s correction rule names as the only
    failure mode here that costs money.
    """
    known = led["objects"].get(key)
    if dry_run:
        return {"key": key, "deleted": False, "reason": "dry run; nothing sent",
                "bytes": int(known.get("bytes", 0)) if known else 0,
                "was_in_ledger": known is not None}

    w = wrangler_bin()
    if not w:
        raise RuntimeError("wrangler not found; cannot delete. Looked in "
                           + ", ".join(WRANGLER_CANDIDATES))
    p = subprocess.run([w, "r2", "object", "delete", f"{bucket}/{key}", "--remote"],
                       capture_output=True, text=True, timeout=120, env=_wrangler_env())
    if p.returncode != 0:
        raise RuntimeError(f"R2 delete failed for {key}: {(p.stderr or p.stdout).strip()[:400]}")

    freed = 0
    if known is not None:
        freed = int(known.get("bytes", 0))
        del led["objects"][key]
        save_ledger(led)
    return {"key": key, "deleted": True, "reason": "deleted from bucket and ledger",
            "bytes": freed, "was_in_ledger": known is not None}


def usage_summary(led: dict, cap: int, provenance: str) -> str:
    # The cap is stamped into the ledger so the health check can report a
    # percentage without a database connection or a credential of its own.
    led["quota_gb_last_seen"] = cap / GB
    led["quota_provenance_last_seen"] = provenance
    save_ledger(led)
    used = ledger_bytes(led)
    pct = (used / cap * 100) if cap else 0
    return (f"R2 archive: {human_bytes(used)} of {human_bytes(cap)} used ({pct:.1f}%), "
            f"{len(led['objects'])} objects · {provenance}")
