#!/usr/bin/env python3
"""Propose a disposition for every untriaged calendar row in the ingest inbox.

WHY THIS EXISTS. The calendar lane parks each event as an ingest row and a human
(or a session) must say what it became. Fifty-two sat untriaged because the event
TITLES carry a first name and nothing else — "Alex | Dell | Joe Coffee", "Josh
dinner?" — and resolving a first name against the book is exactly the contamination
that welded two different Beasleys together once already.

THE KEY NOBODY HAD LOOKED FOR. The published feed strips ATTENDEE properties, which
is why every earlier pass failed. But a Google Calendar invitation embeds the FULL
GUEST LIST, with addresses, inside the event DESCRIPTION text. That is an exact
email, and an email resolves deterministically where a first name cannot.

WHAT IT DECIDES, and it never guesses:
  filed     — an external attendee email matches a contact email in the record
  rejected  — no external attendee at all, or the event is plainly personal
  (left)    — a real external attendee with no record: a research candidate, not a
              disposition. Creating the record is intake work and is not this
              script's job, so it leaves the row alone rather than inventing one.

READ-ONLY. It prints a plan; --apply is the caller's separate decision.
"""
import json
import os
import re
import sys
from datetime import date, datetime

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
INTERNAL = ("@carr.us",)
# Free-mail domains still belong to real people and are NOT filtered — several of
# the book's doctors use gmail. Only obvious calendar plumbing is dropped.
NOISE = ("calendar-notification@google.com", "noreply@", "no-reply@",
         "@resource.calendar.google.com", "@group.calendar.google.com")
PERSONAL_HINTS = ("birthday", "haircut", "back to school", "church", "fountain",
                  "tomato fest", "pick up", "dentist appt", "holiday", "vacation",
                  "anniversary", "pto", "out of office")


ATTENDEES = os.path.join(os.environ.get("CARR_CALENDAR_OUTPUT_ROOT", os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "out")),
    "calendar-attendees.json")


def load_local_attendees():
    """Read the capture's atomically published local-store snapshot.

    THE INGEST FEED CANNOT SUPPLY THIS. Microsoft strips ATTENDEE from the published
    feed, so 50 of the 52 untriaged rows carry no addresses at all — only the two
    that happen to be Google invitations, whose guest list survives inside the
    description text. The local store has attendees on 386 events. Written by
    tools/calendar-attendee-dump.py, which must run inside the access bundle.
    """
    try:
        with open(ATTENDEES) as fh:
            return json.load(fh)
    except FileNotFoundError:
        return {}


def emails_in(event, local=None):
    """Every address the event mentions, deduplicated, lowercased.

    V2 joins title and the aware start instant so same-title meetings stay
    separate. All-day feed dates retain embedded addresses but cannot join a
    timed snapshot instant. Legacy title/day snapshots remain readable during
    the upgrade.
    Feed UIDs and EventKit identifiers occupy different namespaces.
    """
    blob = " ".join(str(event.get(k) or "") for k in
                    ("description", "organizer", "summary", "location"))
    extra = []
    if local:
        title = (event.get("summary") or "").strip()
        start = event.get("starts_at") or ""
        if local.get("schema") == "calendar-events/v2":
            if not isinstance(local.get("events"), list):
                raise ValueError("invalid calendar event snapshot")
            if start:
                if event.get("all_day") is True and re.fullmatch(r"\d{4}-\d{2}-\d{2}", start):
                    # DATE values have no instant; do not invent a timezone or
                    # borrow attendees from a same-title timed meeting.
                    date.fromisoformat(start)
                else:
                    instant = datetime.fromisoformat(start.replace("Z", "+00:00"))
                    if instant.tzinfo is None:
                        raise ValueError("calendar ingest start must have a timezone")
                    for row in local["events"]:
                        captured = datetime.fromisoformat(row["start_at"].replace("Z", "+00:00"))
                        if captured.tzinfo is None:
                            raise ValueError("calendar snapshot start must have a timezone")
                        if row["title"].strip() == title and captured == instant:
                            extra.extend(row["emails"])
        else:
            extra = local.get(f"{title}|{start[:10]}", [])
    out = []
    for e in EMAIL_RE.findall(blob) + list(extra):
        e = e.lower().strip(".")
        if any(n in e for n in NOISE):
            continue
        if e not in out:
            out.append(e)
    return out


def main():
    import psycopg
    # DATABASE_URL is what tools/db-tap.py sets, which is the sanctioned door for a
    # read that needs base tables: party and ingest_inbox are invisible to the
    # exporter role by design (views only), and this join needs both.
    dsn = os.environ.get("DATABASE_URL") or os.environ.get("CARR_TRIAGE_DSN")
    if not dsn:
        sys.exit("no DSN — run this through tools/db-tap.py")
    with psycopg.connect(dsn) as conn:
        cur = conn.cursor()
        cur.execute("""
            select lower(btrim(p.email)), r.ref, r.display_name, r.subject_type
              from party p join v_ref_index r on r.party_id = p.id
             where p.email is not null and btrim(p.email) <> ''
               and r.ref is not null and not r.merged
        """)
        by_email = {}
        for email, ref, name, kind in cur.fetchall():
            by_email.setdefault(email, (ref, name, kind))

        cur.execute("""
            select id, external_id, payload->'event'
              from ingest_inbox where status='new' and source='calendar'
             order by payload->'event'->>'starts_at' desc
        """)
        rows = cur.fetchall()

    local = load_local_attendees()

    plan = {"filed": [], "rejected": [], "left": []}
    for item_id, ext, event in rows:
        event = event or {}
        title = (event.get("summary") or "(no title)").strip()
        day = (event.get("starts_at") or "")[:10]
        found = emails_in(event, local)
        external = [e for e in found if not any(e.endswith(i) for i in INTERNAL)]
        hits = [(e,) + by_email[e] for e in external if e in by_email]

        if hits:
            plan["filed"].append(dict(item_id=str(item_id), day=day, title=title,
                                      refs=sorted({h[1] for h in hits}),
                                      matched=[{"email": h[0], "ref": h[1], "name": h[2]} for h in hits]))
        elif not external:
            why = ("no external attendee — internal CARR only"
                   if found else "no attendee addresses at all")
            if any(h in title.lower() for h in PERSONAL_HINTS):
                why = "personal calendar entry, not business contact"
            plan["rejected"].append(dict(item_id=str(item_id), day=day, title=title, why=why))
        elif any(h in title.lower() for h in PERSONAL_HINTS):
            plan["rejected"].append(dict(item_id=str(item_id), day=day, title=title,
                                         why="personal calendar entry, not business contact"))
        else:
            plan["left"].append(dict(item_id=str(item_id), day=day, title=title,
                                     external=external[:6]))

    print(json.dumps(plan, indent=1))
    print(f"\n# filed {len(plan['filed'])} · rejected {len(plan['rejected'])} · "
          f"left for intake {len(plan['left'])} · total {len(rows)}", file=sys.stderr)


if __name__ == "__main__":
    main()
