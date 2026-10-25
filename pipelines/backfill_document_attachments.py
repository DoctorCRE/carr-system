#!/usr/bin/env python3
"""
backfill_document_attachments.py — ORDER 20(a). The archive rows ORDER 13 owed.

WHAT WAS OWED AND WHY. ORDER 13's factory produced real files for C-112 and
filed them in Joe's OneDrive deal folder, but R2 was not enabled on the account
that afternoon, and `attachment.r2_key` is `not null unique` in 0001. An
attachment row with a null key is not insertable, and faking the key with a
local path would have put a lie in the record layer, so the rows were left
explicitly owed. Joe enabled R2 the same evening and the bucket now exists. This
script pays the debt for every document row already prepared.

WHAT IT DOES, per document row that is missing an attachment:
  1. reconstruct the file names the factory produced (the verb's own basename
     grammar: <deal name>-<template name>-DRAFT-MM-DD-YYYY, safe-cased), and
     look for them in the deal's OneDrive folder first, then in out/documents/.
  2. hash what it finds, upload it to R2 UNDER THE QUOTA GUARD, insert the
     attachment row, point document.working_attachment / pdf_attachment at it.
  3. patch the document note and write one event per document.

WHAT IT REFUSES TO DO. It does not re-render anything: if the file is not on
disk, the row is reported as unrecoverable and left alone, because a document
row whose bytes are gone is a fact worth seeing, not a reason to invent a new
draft with today's records. It never deletes. It never uploads past the quota
(see lib/r2_archive.py); a refusal leaves the row owed with the reason recorded.

  .venv/bin/python pipelines/backfill_document_attachments.py            # dry run
  .venv/bin/python pipelines/backfill_document_attachments.py --apply    # writes

Needs an owner or writer DSN in DATABASE_URL. `carr_jobs` cannot read
doc_template or attachment at all, by design, so this is not a jobs-tier job.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
from typing import Any

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "lib"))
import r2_archive as r2  # noqa: E402

ONEDRIVE_DEALS = os.environ.get(
    "CARR_ONEDRIVE_DEALS",
    "/Users/booko/Library/CloudStorage/OneDrive-CARR,Inc/Joe's Folder/Deals/Active Deals")
STAGING = os.path.join(REPO, "out", "documents")

# The verb's own naming, copied here on purpose rather than shared: tools.js
# runs in a Worker and this runs on a Mac, so the grammar is duplicated and the
# duplication is what the sha256 check catches if it ever drifts.
SAFE = lambda v: re.sub(r"^_|_$", "", re.sub(r"[^A-Za-z0-9]+", "_", str(v or "document")))  # noqa: E731


def sha256_of(path: str) -> tuple[str, int]:
    h, n = hashlib.sha256(), 0
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
            n += len(chunk)
    return h.hexdigest(), n


def find_deal_folder(names: list[str]) -> str | None:
    """Joe's folder names are how HE thinks of the deal, not the party row's
    display_name (C-112's folder is 'Harborline Pelvic Health'; the party says
    'Harborline Pelvic Therapy'). Shared-word matching, and a miss returns None."""
    if not os.path.isdir(ONEDRIVE_DEALS):
        return None
    tokens = set()
    for w in names:
        if w:
            tokens |= {t.lower() for t in re.findall(r"[A-Za-z]+", w) if len(t) > 2}
    best, best_score = None, 0
    for entry in os.listdir(ONEDRIVE_DEALS):
        full = os.path.join(ONEDRIVE_DEALS, entry)
        if not os.path.isdir(full) or entry.startswith((".", "_")):
            continue
        et = {t.lower() for t in re.findall(r"[A-Za-z]+", entry) if len(t) > 2}
        score = len(tokens & et)
        if score > best_score:
            best, best_score = full, score
    return best if best_score >= 2 else None


def candidate_files(doc: dict) -> dict[str, str]:
    """Locate the produced files. Returns {role: path} for the roles found."""
    # prepared_at is UTC in the database and the verb stamped the name from the
    # Worker's own clock, also UTC, so the stamp is read in UTC. A document
    # prepared after 7pm CT is stamped with the NEXT day, which is why both the
    # UTC date and the local one are tried before a row is called unrecoverable.
    stamps = {doc["prepared_at"].strftime("%m-%d-%Y"),
              doc["prepared_at"].astimezone().strftime("%m-%d-%Y")}
    # The working file's extension is the TEMPLATE's own: output_kinds carries
    # {working,pdf}, which names the roles, not the formats.
    working_exts = [os.path.splitext(doc["source_path"])[1].lower() or ".docx"]
    folder = find_deal_folder([doc["deal_name"], doc["client_name"], doc["org_name"]])
    found: dict[str, str] = {}
    bases = [f"{SAFE(doc['deal_name'] or doc['client_name'])}-{SAFE(doc['template_name'])}-DRAFT-{s}"
             for s in sorted(stamps)]
    for where in [p for p in (folder, STAGING) if p]:
        for base in bases:
            for ext in working_exts:
                p = os.path.join(where, base + ext)
                if "working" not in found and os.path.exists(p):
                    found["working"], found["_basename"] = p, base
            p = os.path.join(where, base + ".pdf")
            if "pdf" not in found and os.path.exists(p):
                found["pdf"], found["_basename"] = p, base
    found.setdefault("_basename", bases[0])
    found["_folder"] = folder or ""
    return found


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="upload and write rows (default: dry run)")
    ap.add_argument("--r2-bucket", default=r2.BUCKET)
    ap.add_argument("--document", help="restrict to one document id")
    a = ap.parse_args()

    url = os.environ.get("DATABASE_URL")
    if not url:
        print("STOP: DATABASE_URL is not set. This needs an owner or writer DSN: carr_jobs "
              "cannot read doc_template or attachment, by design.", file=sys.stderr)
        return 2
    import psycopg

    lines: Any = []
    say = lambda s: (lines.append(s), print(s))[1]                # noqa: E731
    say(f"# Document attachment backfill — {time.strftime('%Y-%m-%d %H:%M')} "
        f"({'APPLY' if a.apply else 'dry run'})")

    with psycopg.connect(url) as cn, cn.cursor() as cur:
        cap, provenance = r2.quota_bytes(cn)
        led = r2.load_ledger(a.r2_bucket)
        rec = r2.reconcile(led, cn, a.r2_bucket)
        say(f"\nQuota: {r2.human_bytes(cap)} ({provenance})")
        say(f"Ledger before: {r2.human_bytes(r2.ledger_bytes(led))} across {len(led['objects'])} objects")
        say(f"Bucket says: {rec['bucket_bytes']} bytes / {rec['bucket_objects']} objects ({rec['bucket_note']})")
        say(f"Attachment rows say: {rec['attachment_bytes']} bytes / {rec['attachment_rows']} rows "
            f"({rec['attachment_note']})")
        if rec["correction"]:
            say(f"Reconciliation: {rec['correction']}")

        cur.execute("select id from actor where slug='joe'")
        actor_row = cur.fetchone()
        if actor_row is None:
            raise RuntimeError("actor target 'joe' was not found")
        actor = actor_row[0]

        # The same joins buildRecordBag uses in the verb, so the reconstructed
        # basename is the one the verb actually produced rather than a lookalike.
        q = """select d.id, d.deal_id, d.client_id, d.prepared_at, d.working_attachment,
                      d.pdf_attachment, d.lint_passed, d.leak_check_passed, d.note,
                      t.name as template_name, t.slug, t.source_path,
                      dl.name as deal_name, p.name as client_name,
                      o.name as org_name, c.roster_ref as client_ref
                 from document d
                 join doc_template t on t.id = d.template_id
                 left join deal dl on dl.id = d.deal_id
                 left join client c on c.id = d.client_id
                 left join party p on p.id = c.party_id
                 left join party o on o.id = p.org_id
                where (d.working_attachment is null or d.pdf_attachment is null)"""
        args: list = []
        if a.document:
            q += " and d.id = %s"
            args.append(a.document)
        q += " order by d.prepared_at"
        cur.execute(q, args)
        if cur.description is None:
            raise RuntimeError("document attachment target query returned no columns")
        cols = [c.name for c in cur.description]
        docs = [dict(zip(cols, r)) for r in cur.fetchall()]

        say(f"\nDocuments missing an attachment: {len(docs)}")
        done = owed = missing = 0
        for d in docs:
            found = candidate_files(d)
            say(f"\n## {d['id']}  {d['slug']}  {d['client_ref'] or '(no client)'}  "
                f"prepared {d['prepared_at']:%Y-%m-%d %H:%M}")
            say(f"   basename: {found['_basename']}")
            say(f"   deal folder: {found['_folder'] or 'NOT MATCHED under Active Deals'}")
            roles = [r_ for r_ in ("working", "pdf") if r_ in found]
            if not roles:
                missing += 1
                say("   UNRECOVERABLE: neither the working file nor the PDF is on disk under that "
                    "name. Nothing re-rendered: a document row whose bytes are gone is a fact to "
                    "see, not a reason to mint a new draft from today's records.")
                continue

            att_ids, refused = {}, None
            for role in roles:
                path = found[role]
                sha, size = sha256_of(path)
                key = r2.object_key(d["client_ref"], sha, path)
                try:
                    up = r2.upload(path, key, sha, size, cap, provenance, led,
                                   a.r2_bucket, dry_run=not a.apply)
                except r2.QuotaExceeded as q_:
                    refused = q_
                    say("   " + q_.message.replace("\n", "\n   "))
                    break
                say(f"   {role:<8} {os.path.basename(path)}  {size:,} bytes  "
                    f"{up['reason']}  key={up['key']}")
                if not a.apply:
                    continue
                cur.execute("select id from attachment where r2_key=%s", (key,))
                row = cur.fetchone()
                if row:
                    att_ids[role] = row[0]
                    say(f"   {role:<8} attachment row already exists ({row[0]}); reused, not duplicated")
                    continue
                cur.execute(
                    """insert into attachment (subject_type, subject_id, r2_key, filename, mime,
                         sha256, bytes, created_by)
                       values (%s,%s,%s,%s,%s,%s,%s,%s) returning id""",
                    ("deal" if d["deal_id"] else "client", d["deal_id"] or d["client_id"],
                     key, os.path.basename(path), r2.mime_for(path), sha, size, actor))
                attachment_row = cur.fetchone()
                if attachment_row is None:
                    raise RuntimeError(f"attachment insert for {role} returned no id")
                att_ids[role] = attachment_row[0]
                say(f"   {role:<8} attachment row written ({att_ids[role]})")

            if refused is not None:
                owed += 1
                if a.apply:
                    cur.execute(
                        "update document set note = %s where id = %s",
                        ((d["note"] or "") + (" · " if d["note"] else "")
                         + f"OWED: R2 archive copy refused by the self-enforced quota on "
                           f"{time.strftime('%Y-%m-%d')} "
                           f"({refused.detail['used_bytes']} of {refused.detail['quota_bytes']} bytes "
                           f"used). The OneDrive copies stand; only the archive copy is missing.",
                         d["id"]))
                continue

            if a.apply:
                note = (f"archived to R2 bucket {a.r2_bucket} on {time.strftime('%Y-%m-%d')}: "
                        + " · ".join(f"{r_}: {os.path.basename(found[r_])}" for r_ in roles)
                        + (". Backfilled by ORDER 20; the files were produced by ORDER 13's run "
                           "before R2 existed."))
                cur.execute(
                    """update document set working_attachment = coalesce(%s, working_attachment),
                           pdf_attachment = coalesce(%s, pdf_attachment),
                           note = case when note is null or note = '' then %s
                                       else note || ' · ' || %s end
                       where id = %s""",
                    (att_ids.get("working"), att_ids.get("pdf"), note, note, d["id"]))
                cur.execute(
                    """insert into event (occurred_at, actor_id, verb, subject_type, subject_id,
                         field, new_value, cause, agent_rationale)
                       values (now(),%s,'backfill-document-attachments',%s,%s,'document.attachments',
                               %s::jsonb,'automation_job',%s)""",
                    (actor, "deal" if d["deal_id"] else "client", d["deal_id"] or d["client_id"],
                     json.dumps({"document_id": str(d["id"]),
                                 "attachments": {k: str(v) for k, v in att_ids.items()}}),
                     "ORDER 20: the archive copy ORDER 13 left owed, now that R2 exists"))
            done += 1

        if a.apply:
            cn.commit()
        say(f"\n{'WROTE' if a.apply else 'WOULD WRITE'}: {done} document(s) archived · "
            f"{owed} left owed by the quota · {missing} unrecoverable (files not on disk)")
        say(r2.usage_summary(led, cap, provenance))
        if not a.apply:
            say("\nDry run. Nothing was uploaded and no row was written. Rerun with --apply.")

    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    out = os.path.join(REPO, "out", f"document-attachment-backfill-{stamp}.md")
    with open(out, "w") as fh:
        fh.write("\n".join(lines) + "\n")
    print(f"\nreport: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
