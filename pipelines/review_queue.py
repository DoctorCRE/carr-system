#!/usr/bin/env python3
"""
review_queue.py — ORDER 16(a), wave2-design §2g. THE ONE unified review queue.

WHAT THIS IS
  Three streams of "something is waiting on a human" used to live in three
  different places: ingest rows nobody had triaged, documents the factory had
  produced, and social posts sitting in Blotato. Three places to remember is
  three places to forget. This pools them into ONE store and ONE rendered page.

WHAT THIS IS NOT, AND THE PROPERTY IS STRUCTURAL RATHER THAN REMEMBERED
  It is a SURFACE, never a pinger. This script has no send path, no push path,
  no notification path, and no database write path of any kind. Grep it: there
  is no `insert`, no `update`, no smtp, no webhook POST. A queue that can
  interrupt you is not a queue, it is a boss.

  And DRAFTS NEVER AUTO-APPLY. The ingest lane proposes activity rows as FILES
  under out/review-queue/drafts/. Each carries the exact verb call that would
  record it, so applying one is a human saying yes and a verb-capable session
  running it. Nothing here reaches the database.

THE THREE LANES
  ingest     ingest_inbox rows -> proposed activity DRAFTS, with a confidence
             band and the one-tap call that would apply each. Needs SELECT on
             ingest_inbox; see the credential note below.
  documents  what the document factory produced and Joe has not cleared. Read
             from out/documents/ — the files are the fact; the `document` table
             is the record of them, and it is not readable under the only
             credential this Mac holds.
  social     a POINTER into the existing Blotato review flow, not a rebuild.
             The house rule already writes one open-loops row per scheduled
             batch ("No scheduled post fires unreviewed"); this lane surfaces
             those rows beside everything else waiting, and sends Joe to
             Blotato, where the posts actually live.

THE CREDENTIAL NOTE, MEASURED RATHER THAN ASSUMED
  ~/.config/carr/db.env holds exactly one credential, CARR_DB_EXPORTER_URL, and
  it is views-only by design (amendment 11). `select` on ingest_inbox and on
  document both return InsufficientPrivilege under it — proved, not guessed.
  So the ingest lane reports NOT CONFIGURED, names the exact grant it needs,
  and drafts nothing. That is a lane status on the page, not a failure: the
  other two lanes are real today and the render happens regardless. Same
  posture as the availability matcher's empty-table report.

  The drafting logic itself is exercisable without that grant: --fixture reads
  ingest-shaped rows from a JSON file and runs the whole drafter, subject
  resolution included, against the LIVE v_ref_index the exporter can read. A
  fixture run writes to out/review-queue/fixture/ and never touches the real
  store, so a rehearsal can never be mistaken for today's queue.

LIFECYCLE (every accumulator states one at creation)
  out/review-queue/queue.json and review-queue.html are ONE stable filename
  each, overwritten every run. out/review-queue/drafts/ is keyed deterministically
  by the ingest row id and pruned at the top of every run: a draft whose source
  row is gone is deleted. So the directory is bounded by ingest_inbox rather
  than growing forever, and it is not an accumulator needing a sweep.

Usage:
  ./run.sh review-queue [--fixture FILE] [--json]

EXIT CODES
  0  ran. An empty queue is a report, not a failure, and so is a lane that
     cannot read its source: the page says which, in plain words.
  2  a genuine crash (bad fixture file, unwritable out/).
"""
from __future__ import annotations

import argparse
import html
import json
import os
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lib.credential_file import credential  # noqa: E402
from lib.local_principal import LocalPrincipalError, local_partner_principal

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "out"
STORE = OUT / "review-queue"
DOCUMENTS = OUT / "documents"

# The standing enforcement rule's own words, from social-media-workflow.md step 4.
# Every scheduled batch writes an open-loops row carrying this in its
# "What it unblocks" cell, so keying on it is reading the house convention
# rather than pattern-matching prose that may be reworded next week.
SOCIAL_UNBLOCKS = "No scheduled post fires unreviewed"

# What an ingest source most likely became, before anyone reads it. A guess the
# human can see and correct beats a blank the human has to fill.
SOURCE_KIND = {
    "notes_call_recording": "call",
    "transcript_drop": "call",
    "mail": "email_in",
    "make": "email_in",
    "mailerlite": "email_in",
    "calendar": "meeting",
    "share_sheet": "note",
    "webform": "note",
}

REF_TOKEN = re.compile(r"\b([LCV])-([A-Z]{3}-)?(\d{3})\b")


# ─────────────────────────────────────────────────────────────────────────────
# reading
# ─────────────────────────────────────────────────────────────────────────────

def db_url() -> str | None:
    # [ORDER 19a] CARR_DB_JOBS_URL first, and for this file it is the difference
    # between a queue and a third of a queue: the ingest lane needs select on
    # `ingest_inbox` and the paperwork lane wants `document`, and the exporter
    # role holds neither (measured: permission denied for table ingest_inbox).
    # The older names stay accepted so the two working lanes never regress.
    for name in ("CARR_DB_JOBS_URL", "CARR_DB_EXPORTER_URL", "DATABASE_URL"):
        url = os.environ.get(name)
        if url:
            return url
    for name in ("CARR_DB_JOBS_URL", "CARR_DB_EXPORTER_URL"):
        # preference, not file order: db.env is a list, not a ranking
        url = credential(name, environ={})
        if url:
            return url
    return None


def load_ref_index(url):
    """The resolver surface (0016). Safe columns only, and the only subject
    lookup a views-only credential can perform."""
    if not url:
        return []
    try:
        import psycopg
        with psycopg.connect(url) as conn, conn.cursor() as cur:
            cur.execute("""select subject_type, subject_id, ref, display_name, org_name, status
                             from v_ref_index where merged is not true""")
            return [dict(zip([d.name for d in cur.description], r)) for r in cur.fetchall()]
    except Exception:
        return []


def read_ingest(url):
    """Returns (rows, status_note). Never raises: a lane that cannot read its
    source reports that fact rather than taking the page down with it."""
    if not url:
        return [], ("not configured: no database credential on this Mac "
                    "(CARR_DB_EXPORTER_URL or DATABASE_URL)")
    try:
        import psycopg
        with psycopg.connect(url) as conn, conn.cursor() as cur:
            cur.execute("""select id, received_at, source, external_id, payload, status
                             from ingest_inbox where status = 'new'
                            order by received_at""")
            rows = [dict(zip([d.name for d in cur.description], r)) for r in cur.fetchall()]
        return rows, "ok"
    except Exception as e:
        name = type(e).__name__
        msg = str(e)
        if "InsufficientPrivilege" in name or "permission denied" in msg:
            return [], ("not configured: the only credential on this Mac is the views-only "
                        "exporter role, which is refused SELECT on the intake table. "
                        "Needs a role holding `select on ingest_inbox`.")
        # Everything else, named precisely rather than folded into "ProgrammingError"
        # for everything: a malformed DSN (bad quoting, invalid option) and an actual
        # server-side ProgrammingError (renamed column, bad SQL) are different repairs,
        # and a status note that can't tell them apart sends the next session hunting
        # for a grant that was never the problem (loop #188, 2026-08-06). The DSN never
        # goes in the message — a connection string carries the password.
        if "invalid connection option" in msg or "invalid dsn" in msg.lower():
            detail = ("malformed connection string (unstripped quoting from db.env, "
                      "most likely) — check db_url() parsing, not a grant")
        else:
            detail = msg.splitlines()[0][:160] if msg and "://" not in msg else "(message withheld: looked like it contained a DSN)"
        return [], f"could not read the intake table ({name}: {detail})"


# ─────────────────────────────────────────────────────────────────────────────
# the ingest drafter — proposes, never applies
# ─────────────────────────────────────────────────────────────────────────────

def flatten(payload) -> str:
    """Every string in the payload, joined. Payloads are UNTRUSTED DATA [A12]:
    this reads them for names and never for instructions."""
    out = []

    def walk(v):
        if isinstance(v, str):
            out.append(v)
        elif isinstance(v, dict):
            for x in v.values():
                walk(x)
        elif isinstance(v, list):
            for x in v:
                walk(x)
    walk(payload)
    return " \n ".join(out)


def resolve_subject(text: str, index: list):
    """(match, confidence_band, reason). Two paths, and neither guesses:
    an explicit ref wins; otherwise a name must match EXACTLY ONE record."""
    for m in REF_TOKEN.finditer(text.upper()):
        token = m.group(0)
        hits = [r for r in index if (r["ref"] or "").upper() == token]
        if len(hits) == 1:
            return hits[0], "high", f"the record reference {token} appears in the item"
        if len(hits) > 1:
            return None, "low", f"{token} matches more than one record"

    # A PERSON beats a COMPANY, and the fixture is what taught this: "Alex Rivera
    # following up..." also contains "Meridian Capital", and three vendors work at
    # Meridian Capital. Matching both tiers at once turned a clean single match
    # into a three-way tie. A company name is shared by everyone inside it; a
    # person's is not, so the person tier is tried alone and the company tier
    # only if it finds nobody. (Example sanitized 2026-08-06, ORDER 42b — the
    # originals were tools/fixtures/ingest-rows-order16.json's real names.)
    low = text.lower()

    def tier(field):
        found = {}
        for r in index:
            name = (r.get(field) or "").strip()
            if len(name) >= 5 and name.lower() in low:
                found[r["subject_id"]] = (r, name)
        return found

    for field, label in (("display_name", "name"), ("org_name", "practice")):
        found = tier(field)
        if not found:
            continue
        if len(found) == 1:
            r, name = list(found.values())[0]
            return r, "medium", f"the {label} {name} appears in the item and matches one record"
        names = sorted({n for _, n in found.values()})
        kinds = sorted({r["subject_type"] for r, _ in found.values()})
        if len(names) == 1:
            # The same human, carried on file as more than one kind of record.
            # Guessing which one an activity belongs on is exactly the guess
            # ORDER 1 removed from the resolver, so it is asked, not assumed.
            return None, "low", (f"{names[0]} is on file as a "
                                 f"{' and a '.join(kinds)}, so it needs you to say which")
        return None, "low", ("more than one record could be meant ("
                             + ", ".join(names[:4]) + ")")
    return None, "low", "no record name in the item matched anything on file"


def draft_from_row(row, index) -> dict:
    """One ingest row becomes one PROPOSED activity. `applied` is false and
    there is no code path in this file that can flip it."""
    payload = row.get("payload") or {}
    text = flatten(payload)
    subject, band, reason = resolve_subject(text, index)
    kind = payload.get("kind") if isinstance(payload, dict) else None
    kind = kind if kind in set(SOURCE_KIND.values()) else SOURCE_KIND.get(row.get("source"), "note")

    # Calendar payloads nest the real fields under "event" (bin/pull-gmail-
    # calendar.py normalize()), so the top-level key loop below never found
    # them — before 2026-08-06 every calendar item fell through to flatten()
    # and drafted as "calendar_event <uid> CONFIRMED ..." noise.
    event = payload.get("event") if isinstance(payload, dict) else None
    event = event if isinstance(event, dict) else {}

    summary = ""
    ev_title = event.get("summary")
    if isinstance(ev_title, str) and ev_title.strip():
        summary = " ".join(ev_title.split())
        loc = event.get("location")
        if isinstance(loc, str) and loc.strip():
            summary += " @ " + " ".join(loc.split())
    if not summary and isinstance(payload, dict):
        for key in ("summary", "subject", "title", "text", "body", "transcript"):
            v = payload.get(key)
            if isinstance(v, str) and v.strip():
                summary = " ".join(v.split())
                break
    if not summary:
        summary = " ".join(text.split())
    # The APPLY CALL carries the whole summary; only the display line is short.
    # The old single 177-char truncation fed "Lunch | Caris..." INTO
    # log-activity, so the chopped string was what the record would have held
    # forever (2026-08-06 triage, loop #216). The 1000-char ceiling below is a
    # guard against flatten() of a transcript-sized payload — past that point
    # the right summary is a human's line, not the payload.
    if len(summary) > 1000:
        summary = summary[:997].rstrip() + "..."
    if not summary:
        summary = "(the item carried no readable text)"
    display = summary if len(summary) <= 180 else summary[:177].rstrip() + "..."

    # occurred_at is WHEN IT HAPPENED. For a calendar item that is the event's
    # own start, never the sweep time — received_at here stamped all 40 of the
    # Aug 3-4 drafts as happening at ingest, whatever day the meeting was
    # (2026-08-06 triage, loop #216).
    received = (row.get("received_at").isoformat()
                if hasattr(row.get("received_at"), "isoformat")
                else str(row.get("received_at")))
    starts_at = event.get("starts_at")
    occurred = starts_at if isinstance(starts_at, str) and starts_at.strip() else received

    # A meeting that has not happened yet is not an activity. The draft still
    # renders — the queue is where a human sees what's inbound — but it says
    # plainly that applying it waits for the meeting.
    def _in_future(iso_s: str) -> bool:
        try:
            t = datetime.fromisoformat(iso_s)
            if t.tzinfo is None:
                t = t.replace(tzinfo=timezone.utc)
            return t > datetime.now(timezone.utc)
        except (ValueError, TypeError):
            return False
    future = _in_future(occurred)

    row_id = str(row.get("id"))
    call: dict[str, Any] = {
        "verb": "log-activity",
        "args": {
            "idempotency_key": f"ingest:{row_id}",
            "ref": (subject or {}).get("ref"),
            "kind": kind,
            "summary": summary,
            "occurred_at": occurred,
        },
    }
    owed_parts = []
    if not subject:
        owed_parts.append("which record this belongs to")
        call["args"]["ref"] = None
    if future:
        owed_parts.append(f"the meeting is in the future (starts {occurred}) — log it after it happens")
    owed = "; ".join(owed_parts) or None

    return {
        "draft_id": f"ingest-{row_id}",
        "source_row": row_id,
        "arrived_from": row.get("source"),
        "arrived_at": received,
        "event_at": starts_at or None,
        "future": future,
        "confidence": band,
        "confidence_reason": reason,
        "subject": ({"name": subject.get("display_name") or subject.get("org_name"),
                     "org": subject.get("org_name"),
                     "kind": subject.get("subject_type"),
                     "status": subject.get("status"),
                     "ref": subject.get("ref")} if subject else None),
        "proposed": {"kind": kind, "summary": display, "owed": owed},
        "apply": call,
        "applied": False,
        "note": ("DRAFT. Nothing in this system applies it. A human says yes and a "
                 "verb-capable session runs the call above."),
    }


def write_drafts(drafts, target: Path):
    """Deterministic filenames + a prune, which is why this directory is bounded
    by the intake table rather than being an accumulator."""
    target.mkdir(parents=True, exist_ok=True)
    keep = set()
    for d in drafts:
        p = target / f"{d['draft_id']}.json"
        p.write_text(json.dumps(d, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        keep.add(p.name)
    pruned = 0
    for p in target.glob("ingest-*.json"):
        if p.name not in keep:
            p.unlink()
            pruned += 1
    return pruned


# ─────────────────────────────────────────────────────────────────────────────
# documents lane
# ─────────────────────────────────────────────────────────────────────────────

def read_documents():
    if not DOCUMENTS.exists():
        return [], "no documents have been produced yet"
    groups = {}
    for f in sorted(DOCUMENTS.iterdir()):
        if f.name.startswith(".") or f.is_dir():
            continue
        groups.setdefault(f.stem, []).append(f)
    items = []
    for stem, files in sorted(groups.items()):
        parts = stem.split("-")
        client = parts[0].replace("_", " ") if parts else stem
        template = parts[1].replace("_", " ") if len(parts) > 1 else "document"
        state = parts[2].lower() if len(parts) > 2 else "draft"
        pdf = next((f for f in files if f.suffix.lower() == ".pdf"), None)
        working = next((f for f in files if f.suffix.lower() in (".xlsx", ".docx")), None)
        newest = max(f.stat().st_mtime for f in files)
        items.append({
            "item_id": f"doc-{stem}",
            "client": client,
            "template": template,
            "state": state,
            "client_facing_pdf": str(pdf) if pdf else None,
            "working_file": str(working) if working else None,
            "produced_at": datetime.fromtimestamp(newest, timezone.utc).isoformat(),
            "routed": "staging only, not yet filed to the deal folder"
                      if str(DOCUMENTS) in str(files[0]) else "filed",
        })
    return items, "ok" if items else "no documents are waiting"


# ─────────────────────────────────────────────────────────────────────────────
# social lane — a pointer, not a rebuild
# ─────────────────────────────────────────────────────────────────────────────

def read_social(url, principal):
    """Read scheduled-post review pointers from canonical v_loops."""
    if not url:
        return [], "not configured: no canonical database credential"
    try:
        import psycopg
        with psycopg.connect(url) as conn, conn.cursor() as cur:
            cur.execute("""select loop_id, number, owner, title, body, marker,
                                  due_on, source_note
                             from v_loops
                            where status = 'open' and unblocks = %s
                              and ((tier = 'shared' and personal_to is null)
                                   or (tier = 'personal' and personal_to = %s))
                            order by render_seq""", (SOCIAL_UNBLOCKS, principal))
            cols = [d.name for d in cur.description]
            rows = [dict(zip(cols, row)) for row in cur.fetchall()]
    except Exception as exc:
        return [], f"canonical loop read unavailable ({type(exc).__name__})"

    items = []
    not_due = 0
    today = datetime.now().date()
    for row in rows:
        body = str(row.get("body") or row.get("title") or "")
        # The marker convention in the open-loops header is the authority on what
        # is live, and the typed columns carry it directly: a future-dated
        # row is silent until its day. Skipping it is not hiding it, which is why
        # the count comes back in the lane status.
        due_on = row.get("due_on")
        if due_on and due_on > today:
            not_due += 1
            continue
        marker_kind = row.get("marker")
        marker = "due now" if marker_kind == "bell" else (
            "now due" if marker_kind == "dated" else "open")
        headline = re.sub(r"\*\*(.+?)\*\*", r"\1", body)
        headline = re.sub(r"[🔔🗓✅⚠️⏳]", "", headline).strip()
        headline = re.sub(r"^\d{4}-\d{2}-\d{2}\s*", "", headline)
        headline = " ".join(headline.split())
        count = None
        m = re.search(r"\b(\d+)\s+review-drafts\b", body)
        if m:
            count = int(m.group(1))
        first = headline.split(". ")[0][:200].rstrip(" .") + "."
        items.append({
            "item_id": f"social-{row.get('number') or row['loop_id']}",
            "owner": row.get("owner") or "",
            "state": marker,
            "headline": first,
            "posts": count,
            "where": row.get("source_note") or "canonical loop record",
        })
    if not items:
        return [], ("nothing is scheduled and waiting"
                    + (f" ({not_due} batch(es) scheduled for a later date, silent until then)"
                       if not_due else ""))
    return items, ("ok" if not not_due else
                   f"ok ({not_due} further batch(es) dated later, silent until their day)")


# ─────────────────────────────────────────────────────────────────────────────
# render
# ─────────────────────────────────────────────────────────────────────────────

CONF_WORD = {"high": "Likely right", "medium": "Worth a look", "low": "Needs you"}

CSS = """
  :root{
    --navy:#002F6C;--navy-deep:#00224D;--orange:#F57F29;
    --color-text:#ffffff;--color-muted:rgba(255,255,255,.62);--focus:#FF9D4D;
    --color-success:#3FB68B;--color-warning:#F5B841;--color-danger:#E5533D;
    --head:'Oswald','Archivo Narrow',sans-serif;--body:'Montserrat','Helvetica Neue',Arial,sans-serif;
    --space-1:4px;--space-2:8px;--space-3:12px;--space-4:16px;--space-6:24px;--space-8:32px;
    --radius-sm:7px;--radius-md:10px;--radius-lg:14px;
    --shadow-md:0 12px 30px rgba(0,15,40,.35);
    --duration-fast:.12s;--ease-out:cubic-bezier(.2,.7,.3,1);
  }
  *{margin:0;padding:0;box-sizing:border-box;-webkit-font-smoothing:antialiased;}
  a:focus-visible,button:focus-visible,summary:focus-visible{outline:3px solid var(--focus);outline-offset:3px;border-radius:var(--radius-sm);}
  :focus:not(:focus-visible){outline:none;}
  html,body{min-height:100%;}
  body{padding:36px 20px 64px;background:radial-gradient(120% 90% at 82% 0%,#063a82 0%,var(--navy) 46%,var(--navy-deep) 100%);background-attachment:fixed;font-family:var(--body);color:#fff;}
  .wrap{max-width:860px;margin:0 auto;}
  .head{text-align:center;margin-bottom:var(--space-6);}
  .wordmark{font-family:var(--head);font-weight:700;letter-spacing:.16em;font-size:20px;text-transform:uppercase;}
  .wordmark .b{display:inline-block;width:30px;height:4px;background:var(--orange);border-radius:2px;vertical-align:middle;margin-left:9px;transform:translateY(-3px);}
  h1{font-family:var(--head);font-weight:600;font-size:44px;line-height:1.03;margin-top:14px;letter-spacing:-.01em;}
  .lede{margin:14px auto 0;max-width:620px;font-size:15px;line-height:1.5;color:rgba(255,255,255,.72);}
  .group{margin-top:var(--space-8);}
  .group-head{font-family:var(--head);font-weight:600;text-transform:uppercase;letter-spacing:.14em;font-size:15px;color:var(--orange);display:flex;align-items:center;margin-bottom:var(--space-4);}
  .group-head .bar{display:inline-block;width:26px;height:3px;background:var(--orange);border-radius:2px;margin-right:12px;}
  .card{border-radius:var(--radius-lg);background:linear-gradient(160deg,rgba(255,255,255,.07),rgba(255,255,255,.025));border:1px solid rgba(255,255,255,.10);padding:18px 20px;margin-bottom:12px;transition:border-color var(--duration-fast) var(--ease-out);}
  .card:hover{border-color:rgba(245,127,41,.45);}
  .card-name{font-family:var(--head);font-weight:600;font-size:20px;letter-spacing:.01em;}
  .chip{display:inline-block;font-family:var(--head);font-weight:500;text-transform:uppercase;letter-spacing:.08em;font-size:11px;padding:3px 9px;border-radius:999px;margin-left:10px;vertical-align:middle;border:1px solid rgba(255,255,255,.28);color:rgba(255,255,255,.78);}
  .chip.good{border-color:rgba(63,182,139,.6);color:#7FDCBB;}
  .chip.warn{border-color:rgba(245,184,65,.6);color:#F5CE7E;}
  .chip.need{border-color:rgba(229,83,61,.6);color:#F09182;}
  .why{margin-top:6px;font-size:14px;line-height:1.55;color:rgba(255,255,255,.74);}
  .act{margin-top:12px;font-size:14px;line-height:1.5;}
  .act b{font-weight:600;color:#fff;}
  .say{display:inline-block;margin-top:6px;font-family:var(--body);font-size:13px;color:var(--navy-deep);background:var(--orange);padding:8px 13px;border-radius:var(--radius-sm);font-weight:600;min-height:44px;line-height:28px;}
  .dim{margin-top:10px;font-size:11px;letter-spacing:.04em;color:rgba(255,255,255,.34);word-break:break-all;}
  .empty{border:1px dashed rgba(255,255,255,.2);border-radius:var(--radius-lg);padding:20px;font-size:14px;line-height:1.55;color:rgba(255,255,255,.66);}
  .empty b{color:rgba(255,255,255,.85);font-weight:600;}
  .foot{margin-top:var(--space-8);font-size:13px;line-height:1.65;color:rgba(255,255,255,.55);text-align:center;}
  .foot b{color:rgba(255,255,255,.8);font-weight:600;}
  @media(max-width:640px){h1{font-size:34px;}body{padding:24px 14px 48px;}}
  @media(prefers-reduced-motion:reduce){*,*::before,*::after{transition-duration:.001ms !important;animation-duration:.001ms !important;}}
"""


def esc(s) -> str:
    """Escape, and strip the one punctuation mark writing-rules bans outright.

    Some of the text on this page is QUOTED from files a human typed, and Joe's
    own notes use em-dashes freely. The rule governs what this system renders,
    so the normalization belongs here, at the single choke point every visible
    string passes through, rather than in each lane where the next lane would
    forget it."""
    s = str(s if s is not None else "")
    s = re.sub(r"\s*—\s*", ", ", s)
    return html.escape(s)


def mask_refs(text: str, name: str) -> str:
    """Swap a raw record reference for the plain name it points at. IDs are join
    keys, not something a human should have to decode."""
    return REF_TOKEN.sub(name, str(text or ""))


def card(name, chip, chip_class, why, action, say=None, dim=None) -> str:
    bits = [f'<div class="card"><div><span class="card-name">{esc(name)}</span>'
            f'<span class="chip {chip_class}">{esc(chip)}</span></div>',
            f'<p class="why">{esc(why)}</p>',
            f'<p class="act">{action}</p>']
    if say:
        bits.append(f'<span class="say">{esc(say)}</span>')
    if dim:
        bits.append(f'<p class="dim">{esc(dim)}</p>')
    bits.append("</div>")
    return "".join(bits)


def empty(text) -> str:
    return f'<div class="empty">{text}</div>'


def render(queue: dict) -> str:
    lanes = queue["lanes"]

    # ── ingest ───────────────────────────────────────────────────────────────
    ing = lanes["ingest"]
    if ing["items"]:
        blocks = []
        for d in ing["items"]:
            subj = d["subject"]["name"] if d["subject"] else "Not matched to a record yet"
            band = d["confidence"]
            cls = {"high": "good", "medium": "warn", "low": "need"}[band]
            # A payload can carry a record reference in its own text, and the draft
            # file keeps it verbatim because the draft is the record of what arrived.
            # The PAGE may not show it (doctrine law 5), so the swap happens here,
            # where the resolved name is in hand, rather than blanking it to nothing.
            named = d["subject"]["name"] if d["subject"] else "this record"
            why = (f'Came in from {d["arrived_from"]}. Reads as a {d["proposed"]["kind"]}: '
                   f'{mask_refs(d["proposed"]["summary"], named)} '
                   f'Matched because {mask_refs(d["confidence_reason"], named)}.')
            act = ("<b>Confirm it, and the touch is on the record.</b> If the match is wrong, "
                   "say who it really was and it re-files.")
            blocks.append(card(subj, CONF_WORD[band], cls, why, act,
                               say="log this one", dim=f'draft {d["draft_id"]}'))
        ingest_html = "".join(blocks)
    elif ing["status"] == "ok":
        ingest_html = empty("<b>Nothing new came in.</b> Calls, mail, and anything sent from "
                            "the phone land here first, already written up, so confirming one "
                            "is a tap instead of a retelling.")
    else:
        ingest_html = empty(f'<b>This lane cannot read its source yet.</b> {esc(ing["status"])} '
                            "Until that lands, anything captured stays queued where it arrived "
                            "and nothing is lost.")

    # ── documents ────────────────────────────────────────────────────────────
    doc = lanes["documents"]
    if doc["items"]:
        blocks = []
        for d in doc["items"]:
            why = (f'The {d["template"]} filled itself from what is already on the deal. '
                   f'The PDF is the copy a client sees; the working file stays yours.')
            act = ("<b>Read the PDF, then say send it and it goes to you to send.</b> "
                   "Nothing leaves this system on its own.")
            path = d["client_facing_pdf"] or d["working_file"]
            blocks.append(card(d["client"], "Ready for you", "good", why, act,
                               say="show me the PDF",
                               dim=Path(path).name if path else None))
        docs_html = "".join(blocks)
    else:
        docs_html = empty("<b>No paperwork is waiting.</b> When a letter of intent or a grid is "
                          "produced, it appears here with the client copy ready to read.")

    # ── social ───────────────────────────────────────────────────────────────
    soc = lanes["social"]
    if soc["items"]:
        blocks = []
        for s in soc["items"]:
            count = f'{s["posts"]} posts' if s["posts"] else "A batch"
            why = (f'{s["headline"]} They are scheduled with lead time on purpose, so you can '
                   f'edit or pull any of them in Blotato before one fires.')
            act = ("<b>Open Blotato and clear the week.</b> Approve, edit, or cancel each one "
                   "there; this page only tells you they are waiting.")
            blocks.append(card(count + " waiting on your review", s["state"], "warn", why, act,
                               dim=s["where"]))
        social_html = "".join(blocks)
    else:
        social_html = empty("<b>Nothing is queued to post.</b> When a week gets scheduled, it "
                            "shows up here and stays until you have cleared it in Blotato.")

    total = queue["counts"]["waiting"]
    head_line = ("Nothing is waiting on you right now."
                 if total == 0 else
                 f'{total} thing{"" if total == 1 else "s"} waiting on you.')

    return f'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>CARR | What is waiting on you</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Oswald:wght@400;500;600;700&family=Montserrat:wght@400;500;600&display=swap" rel="stylesheet">
<style>{CSS}</style>
</head>
<body>
  <main class="wrap">
    <div class="head">
      <div class="wordmark">CARR<span class="b"></span></div>
      <h1>What is waiting on you</h1>
      <p class="lede">{esc(head_line)} Everything that needs a yes from you sits on this one page,
      cleared twice a day. It never buzzes, never emails, and never acts on its own.</p>
    </div>

    <section class="group">
      <h2 class="group-head"><span class="bar"></span>Touches to confirm ({len(ing["items"])})</h2>
      {ingest_html}
    </section>

    <section class="group">
      <h2 class="group-head"><span class="bar"></span>Paperwork to read ({len(doc["items"])})</h2>
      {docs_html}
    </section>

    <section class="group">
      <h2 class="group-head"><span class="bar"></span>Posts to clear ({len(soc["items"])})</h2>
      {social_html}
    </section>

    <p class="foot">Built {esc(queue["built_at_local"])} from what was true at that minute.
    Everything above is a <b>draft</b> until you say yes, and saying <b>undo</b> reverses the last
    thing this system did. Cleared twice a day by habit, once with your morning brief and once
    late afternoon.</p>
  </main>
</body>
</html>
'''


# ─────────────────────────────────────────────────────────────────────────────

BANNED = ["delve", "unlock", "unleash", "harness", "elevate", "unveil", "seamless",
          "cutting-edge", "game-changing", "transformative", "holistic", "tapestry",
          "realm", "testament", "myriad", "plethora", "leverage", "utilize", "synergy",
          "robust", "streamline", "empower", "foster", "facilitate", "paramount",
          "meticulous", "intricate", "multifaceted", "beacon", "embark", "quietly",
          "it's worth noting", "at the end of the day", "the reality is", "at its core"]


def lint(page: str):
    """writing-rules on a rendered surface is not optional (the two diagram
    documents that shipped with 72 em-dashes are why this runs in code)."""
    visible = " ".join(re.findall(r">([^<>]+)<", page))
    findings = []
    if "—" in visible:
        findings.append("em-dash in visible copy")
    low = visible.lower()
    findings += [f"banned term: {b}" for b in BANNED if b in low]
    if re.search(r"\b[LCV]-[A-Z]{0,4}-?\d{3}\b", visible):
        findings.append("an internal record id reached the visible copy (doctrine law 5)")
    return findings


def main() -> int:
    ap = argparse.ArgumentParser(description="Build the unified review queue.")
    ap.add_argument("--fixture", help="ingest-shaped rows from a JSON file; writes to "
                                      "out/review-queue/fixture/ and leaves the real store alone")
    ap.add_argument("--json", action="store_true", help="print the store to stdout")
    a = ap.parse_args()

    try:
        principal = local_partner_principal()
    except LocalPrincipalError as exc:
        print(f"review_queue: REFUSED - {exc}", file=sys.stderr)
        return 78

    store = STORE / "fixture" if a.fixture else STORE
    store.mkdir(parents=True, exist_ok=True)

    url = db_url()
    index = load_ref_index(url)

    if a.fixture:
        try:
            raw = json.loads(Path(a.fixture).read_text(encoding="utf-8"))
        except Exception as e:
            print(f"review_queue: cannot read the fixture file ({e})", file=sys.stderr)
            return 2
        ingest_rows, ingest_status = raw, "ok (FIXTURE, not production data)"
    else:
        ingest_rows, ingest_status = read_ingest(url)

    drafts = [draft_from_row(r, index) for r in ingest_rows]
    pruned = write_drafts(drafts, store / "drafts")

    doc_items, doc_status = read_documents()
    soc_items, soc_status = read_social(url, principal)

    now = datetime.now(timezone.utc)
    queue: dict[str, Any] = {
        "built_at": now.isoformat(),
        "built_at_local": datetime.now().strftime("%A, %b %-d at %-I:%M %p"),
        "is_fixture": bool(a.fixture),
        "resolver_records": len(index),
        "lanes": {
            "ingest": {"status": ingest_status, "items": drafts, "pruned": pruned},
            "documents": {"status": doc_status, "items": doc_items},
            "social": {"status": soc_status, "items": soc_items},
        },
        "counts": {
            "ingest": len(drafts), "documents": len(doc_items), "social": len(soc_items),
            "waiting": len(drafts) + len(doc_items) + len(soc_items),
        },
        "human_gate": ("Every item is a DRAFT. This script has no write path to the database, "
                       "no send path, and no push path."),
    }

    (store / "queue.json").write_text(
        json.dumps(queue, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8")
    page = render(queue)
    findings = lint(page)
    (store / "review-queue.html").write_text(page, encoding="utf-8")

    c = queue["counts"]
    tag = "FIXTURE " if a.fixture else ""
    print(f'{tag}review queue: {c["waiting"]} waiting '
          f'(touches {c["ingest"]}, paperwork {c["documents"]}, posts {c["social"]}) '
          f'-> {store / "review-queue.html"}')
    for lane, key in (("touches", "ingest"), ("paperwork", "documents"), ("posts", "social")):
        st = queue["lanes"][key]["status"]
        if st != "ok" and not st.startswith("ok "):
            print(f"  {lane}: {st}")
    if pruned:
        print(f"  pruned {pruned} draft file(s) whose source item is gone")
    print(f'  writing check: {"clean" if not findings else "; ".join(findings)}')
    if a.json:
        print(json.dumps(queue, indent=2, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
