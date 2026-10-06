#!/usr/bin/env python3
"""Match calendar attendee emails to people already in the record.

Consumes the local EventKit attendee dump on the unattended path, pulls attendee
email addresses, and matches them against the live client, lead and vendor exports.
The legacy direct database reader requires Full Disk Access.

Produces INFERRED TOUCHES: dated evidence that contact happened, each carrying the
event that proves it and a confidence level. Nothing is written to the record —
these are proposals for a partner to confirm, per the standing rule that the
system infers contact from evidence it already holds and never asks the partner to
report his own activity.

Match tiers, strongest first:
  exact   — attendee email equals a contact email in the record
  domain  — attendee's domain matches the domain of a known contact's email
  none    — external address with no counterpart in the record (a research lead)

Internal @carr.us addresses are reported separately and never counted as client
contact.
"""

import datetime
import hashlib
import json
import os
import re
import shutil
import sqlite3
import sys
import tempfile
import time
from collections import defaultdict

APPLE_EPOCH = 978307200
DEFAULT_DAYS = 120
GROUP_CONTAINER = os.path.expanduser(
    "~/Library/Group Containers/group.com.apple.calendar/Calendar.sqlitedb"
)
# WHERE THE RECORD CONTACTS COME FROM (fixed 2026-09-27). This used to read
# ~/carr-system/out/exports/<name>.xlsx — the exporters' DRAFT directory. Since
# Joe's 2026-08-22 ruling the nightly chain exports LIVE, to EXPORT_HOME
# (CARR's OneDrive), and never writes the draft copy; on the Studio the draft
# directory does not exist at all. openpyxl was never reached, the loader
# returned two empty maps, and every weekday run reported "record contacts
# loaded: 0 emails, 0 domains" — so every external attendee read as unknown and
# the intake gate refused the capture. The live projection is the file a person
# opens, rebuilt every night; the draft is not the record.
#
# The relative paths are the exporters' own ROSTER_REL / REGISTRY_REL
# (exporters/targets.py); tools/test-calendar-touch-matcher.py pins them so the
# two cannot drift apart silently again.
VENDORS_REL = "DNA/Network/vendors.xlsx"
ROSTER_REL = "DNA/Clients/client-roster.xlsx"
REGISTRY_REL = "DNA/Leads/lead-registry.xlsx"
INTERNAL_DOMAIN = "carr.us"


def export_home():
    """The live export root, resolved exactly as the exporters resolve it."""
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from exporters.common import EXPORT_HOME
    return str(EXPORT_HOME)


class NoRecordContacts(RuntimeError):
    """The contact exports yielded no contact at all: a source failure."""
FREEMAIL = {"gmail.com", "icloud.com", "yahoo.com", "hotmail.com", "outlook.com", "aol.com"}


def load_record_contacts(snapshot=None, root=None):
    """Return (email -> label) and (domain -> label) from the client, lead and vendor exports.

    Without a snapshot this reads client, lead and vendor LIVE exports under ``root`` (default: the
    exporters' EXPORT_HOME) and raises NoRecordContacts when they yield nothing,
    because zero known contacts is a broken source, never an empty book: read as
    data it turns every attendee into an unknown.
    """
    by_email, by_domain = {}, {}
    if snapshot is not None:
        if not isinstance(snapshot, list): raise ValueError("contact snapshot must be an array")
        for row in snapshot:
            if not isinstance(row, dict) or set(row) != {"email","ref","name","org"}: raise ValueError("contact snapshot row has invalid shape")
            email=row["email"]
            if not isinstance(email,str) or email!=email.lower() or "@" not in email: raise ValueError("contact snapshot email is invalid")
            if not all(value is None or isinstance(value,str) for value in (row["ref"],row["name"],row["org"])): raise ValueError("contact snapshot identity is invalid")
            label=" / ".join(x for x in (row["ref"],row["name"] or row["org"]) if x) or "(unnamed row)"
            by_email.setdefault(email,label); dom=email.split("@",1)[1]
            if dom not in FREEMAIL and dom!=INTERNAL_DOMAIN: by_domain.setdefault(dom,label)
        return by_email,by_domain
    import openpyxl
    base = root if root is not None else export_home()
    missing = []

    def ingest(path, sheet, id_col, name_col, org_col, email_col):
        full = os.path.join(base, path)
        if not os.path.exists(full):
            missing.append(f"{path} (absent)")
            return
        wb = openpyxl.load_workbook(full, read_only=True, data_only=True)
        if sheet not in wb.sheetnames:
            missing.append(f"{path} (no sheet {sheet!r})")
            wb.close()
            return
        ws = wb[sheet]
        header, idx = None, {}
        for row in ws.iter_rows(values_only=True):
            cells = [str(c).strip() if c is not None else "" for c in row]
            if header is None:
                if any(c.lower() == "email" for c in cells):
                    header = cells
                    for want, key in ((id_col, "id"), (name_col, "name"),
                                      (org_col, "org"), (email_col, "email")):
                        for i, c in enumerate(cells):
                            if c.lower() == want.lower():
                                idx[key] = i
                if header is not None and set(idx) != {"id", "name", "org", "email"}:
                    missing.append(f"{path} (required headers missing)")
                    break
                continue
            if "email" not in idx:
                continue
            email = cells[idx["email"]].strip().lower() if idx["email"] < len(cells) else ""
            name = cells[idx.get("name", 0)] if idx.get("name", 0) < len(cells) else ""
            org = cells[idx.get("org", 0)] if idx.get("org", 0) < len(cells) else ""
            ref = cells[idx.get("id", 0)] if idx.get("id", 0) < len(cells) else ""
            label = " / ".join(x for x in (ref, name or org) if x) or "(unnamed row)"
            if email and "@" in email:
                by_email.setdefault(email, label)
                dom = email.split("@", 1)[1]
                if dom not in FREEMAIL and dom != INTERNAL_DOMAIN:
                    by_domain.setdefault(dom, label)

        if header is None:
            missing.append(f"{path} (required headers missing)")
        wb.close()

    ingest(ROSTER_REL, "Clients", "Client ID", "Name", "Practice / Entity", "Email")
    ingest(REGISTRY_REL, "Registry", "Lead ID", "Contact Name", "Practice", "Email")
    ingest(VENDORS_REL, "Vendors", "ID", "Name", "Company", "Email")
    if missing or not by_email:
        detail = "; ".join(missing) or "no row carried an email address"
        raise NoRecordContacts(f"record contacts: none loaded from the live exports ({detail})")
    return by_email, by_domain


def read_calendar(days):
    if not os.path.exists(GROUP_CONTAINER):
        print(f"FATAL: calendar database not found at {GROUP_CONTAINER}")
        return None
    tmp = tempfile.mkdtemp(prefix="calmatch-")
    local = os.path.join(tmp, "Calendar.sqlitedb")
    try:
        for suffix in ("", "-wal", "-shm"):
            src = GROUP_CONTAINER + suffix
            if os.path.exists(src):
                shutil.copy2(src, local + suffix)
    except PermissionError:
        print("FATAL: cannot read the calendar database.")
        print("       This is a Full Disk Access answer, not an empty calendar.")
        return None

    now = int(time.time()) - APPLE_EPOCH
    cut = now - days * 86400
    con = sqlite3.connect(f"file:{local}?mode=ro", uri=True)
    # A FUTURE EVENT IS NOT A TOUCH. Scheduling a tour is not the same as having
    # met, and counting one as contact would manufacture exactly the false
    # confidence this whole capability exists to remove. Past events become
    # inferred touches; future ones are reported separately as upcoming.
    rows = con.execute(
        """
        SELECT LOWER(p.email),
               date(ci.start_date + ?, 'unixepoch'),
               COALESCE(ci.summary, '(untitled)'),
               CASE WHEN ci.start_date <= ? THEN 'past' ELSE 'upcoming' END
        FROM CalendarItem ci
        JOIN Participant p ON p.owner_id = ci.ROWID
        WHERE p.email IS NOT NULL AND p.email <> ''
          AND ci.start_date > ?
        ORDER BY ci.start_date DESC
        """,
        (APPLE_EPOCH, now, cut),
    ).fetchall()
    con.close()
    shutil.rmtree(tmp, ignore_errors=True)
    return rows




_REF_SHAPE = re.compile(r"^[A-Z]{1,3}-[A-Z0-9-]+$")


def _ref_of(label):
    """The record ref out of a "<ref> / <name>" label, or the label itself.

    Falls back to the whole label deliberately: the verbs resolve a deal NAME as
    well as a ref, so a row that carried no ref is still addressable.
    """
    head = str(label).split(" / ", 1)[0].strip()
    return head if _REF_SHAPE.match(head) else str(label)


def read_dump(path, days):
    """Same rows as read_calendar, built from the access bundle's EventKit dump.

    WHY THIS EXISTS. read_calendar opens the local Calendar DATABASE, which macOS
    guards with FULL DISK ACCESS — a grant that attaches to the responsible
    process. That works from a terminal that holds it and FAILS under a launchd
    agent, which is what the first real fire of the unattended capture hit:
    "FATAL: cannot read the calendar database. This is a Full Disk Access answer,
    not an empty calendar."

    The access bundle already reads the same meetings through EventKit and holds
    a permission that DOES survive into the agent. So the unattended path reads
    its dump instead, and the whole pipeline needs ONE grant rather than two.
    Reading the database stays the default for a human at a terminal.

    V2 carries stable event IDs and aware start timestamps. Legacy title/day
    dumps remain readable, but cannot prove that a same-day event has happened.
    """
    with open(path) as fh:
        dump = json.load(fh)
    now = datetime.datetime.fromtimestamp(time.time(), datetime.timezone.utc)
    floor = now - datetime.timedelta(days=days)
    rows = []
    if isinstance(dump, dict) and dump.get("schema") == "calendar-events/v2":
        if not isinstance(dump.get("events"), list):
            raise ValueError("calendar events must be an array")
        for event in dump["events"]:
            if (not isinstance(event, dict) or not isinstance(event.get("event_id"), str)
                    or not event["event_id"] or not isinstance(event.get("title"), str)
                    or not isinstance(event.get("emails"), list)):
                raise ValueError("invalid calendar event")
            start = datetime.datetime.fromisoformat(event["start_at"])
            if start.tzinfo is None:
                raise ValueError("calendar start timestamp requires timezone")
            if start < floor:
                continue
            when = "upcoming" if start > now else "past"
            for email in event["emails"]:
                if not isinstance(email, str) or "@" not in email:
                    raise ValueError("invalid calendar attendee")
                rows.append((email.strip().lower(), start.date().isoformat(), event["title"],
                             when, event["event_id"], start.isoformat()))
        return rows
    if not isinstance(dump, dict):
        raise ValueError("invalid legacy calendar dump")
    today = now.date()
    for key, emails in dump.items():
        title, _, day = key.rpartition("|")
        on = datetime.date.fromisoformat(day)
        if not isinstance(emails, list):
            raise ValueError("invalid legacy attendees")
        # Date-only dumps cannot prove a same-day meeting already happened.
        when = "upcoming" if on >= today else "past"
        if on < floor.date():
            continue
        for email in emails:
            rows.append((email.strip().lower(), day, title, when))
    return rows


def main():
    # --json exists so an UNATTENDED caller can act on this instead of a human
    # reading prose. bin/calendar-eventkit-capture.sh consumes it. The human
    # report is unchanged and still the default: this adds a mode, it does not
    # replace one.
    argv = [a for a in sys.argv[1:] if a != "--json" and not a.startswith("--from-dump")]
    as_json = "--json" in sys.argv
    dump_path = None
    snapshot_envelope = None
    for i, a in enumerate(sys.argv):
        if a == "--from-dump" and i + 1 < len(sys.argv):
            dump_path = sys.argv[i + 1]
            argv = [x for x in argv if x != dump_path]
    if "--contact-snapshot-stdin" in sys.argv:
        argv=[x for x in argv if x!="--contact-snapshot-stdin"]
        envelope=json.load(sys.stdin); raw=envelope.get("snapshot_text") if isinstance(envelope,dict) else None
        if not isinstance(envelope,dict) or set(envelope)!={"source_snapshot_id","snapshot_digest","contact_count","snapshot_text"} or not isinstance(raw,str) or not isinstance(envelope.get("source_snapshot_id"),str) or not isinstance(envelope.get("snapshot_digest"),str) or type(envelope.get("contact_count")) is not int:
            raise ValueError("contact snapshot envelope has invalid shape")
        if hashlib.sha256(raw.encode()).hexdigest()!=envelope["snapshot_digest"]: raise ValueError("contact snapshot digest mismatch")
        snapshot=json.loads(raw)
        if not isinstance(snapshot,list) or len(snapshot)!=envelope["contact_count"]: raise ValueError("contact snapshot count mismatch")
        snapshot_envelope=envelope
    days = int(argv[0]) if argv else DEFAULT_DAYS
    try:
        by_email, by_domain = load_record_contacts(snapshot if snapshot_envelope else None)
    except NoRecordContacts as exc:
        # Loud, nonzero, and addressless: the capture wrapper prints this line
        # and fails the run instead of mis-reporting every attendee as unknown.
        print(f"FATAL: {exc}", file=sys.stderr)
        return 5
    # In --json mode stdout must be PARSEABLE and nothing else. This banner went
    # to stdout ahead of the payload and would have made json.load choke on the
    # first consumer — caught before shipping, not after.
    print(f"record contacts loaded: {len(by_email)} emails, {len(by_domain)} domains",
          file=sys.stderr if as_json else sys.stdout)

    rows = read_dump(dump_path, days) if dump_path else read_calendar(days)
    if rows is None:
        return 3

    latest, events, upcoming = {}, defaultdict(list), {}
    for row in sorted(rows, key=lambda row: (row[5] if len(row) > 4 else row[1], row[2]), reverse=True):
        email, day, title, when = row[:4]
        event = {"day": day, "title": title}
        if len(row) > 4:
            event.update(event_id=row[4], start_at=row[5])
        if when == "upcoming":
            start = (datetime.datetime.fromisoformat(row[5]) if len(row) > 4 else
                     datetime.datetime.combine(datetime.date.fromisoformat(day),
                                               datetime.time.min, datetime.timezone.utc))
            if email not in upcoming or start < upcoming[email][0]:
                upcoming[email] = (start, day, title)
            continue
        if email not in latest:
            latest[email] = day
        identity = event.get("event_id") or (day, title)
        if not any((e.get("event_id") or (e["day"], e["title"])) == identity for e in events[email]):
            events[email].append(event)

    exact, domain, unknown, internal = {}, {}, {}, set()
    for email in latest:
        dom = email.split("@", 1)[1] if "@" in email else ""
        if dom == INTERNAL_DOMAIN:
            internal.add(email)
        elif email in by_email:
            exact[email] = by_email[email]
        elif dom in by_domain:
            domain[email] = by_domain[dom]
        else:
            unknown[email] = dom

    if as_json:
        # EXACT matches only carry a record ref, because only an exact email
        # match is evidence a named person was in the room. Domain matches say
        # "someone from that org" and must never become a dated touch on an
        # individual; they are reported for a human, never auto-logged.
        payload={
            "ok": True, "days": days,
            "counts": {"emails": len(latest), "internal": len(internal),
                       "exact": len(exact), "domain": len(domain),
                       "unknown": len(unknown)},
            # ref and LABEL are different things and the verbs want the ref.
            # load_record_contacts joins them as "<ref> / <name>", so the first
            # segment is the ref when there is one. A live write refused with
            # subject_not_found because the whole label was sent as the ref —
            # caught by an actual write attempt, not by reading the code.
            "exact": [{"email": e, "ref": _ref_of(exact[e]),
                       "label": str(exact[e]), "last_seen": latest[e],
                       "events": events[e]}
                      for e in exact],
            "domain": [{"email": e, "org": str(domain[e]), "last_seen": latest[e]} for e in domain],
            "unknown": [{"email": e, "domain": d, "last_seen": latest[e]}
                        for e, d in unknown.items()]}
        if snapshot_envelope: payload["_canary_source"]={k:snapshot_envelope[k] for k in ("source_snapshot_id","snapshot_digest","contact_count")}
        json.dump(payload, sys.stdout, indent=1, default=str)
        print()
        return 0

    print(f"window: last {days} days")
    print(f"distinct attendee emails: {len(latest)}  "
          f"(internal {len(internal)}, external {len(latest) - len(internal)})")
    print()
    print(f"  EXACT match to a record contact : {len(exact)}")
    print(f"  DOMAIN match to a known org     : {len(domain)}")
    print(f"  NO match (research candidates)  : {len(unknown)}")
    print()

    if exact or domain:
        print("INFERRED TOUCHES — proposals, nothing written:")
        for tier, bucket in (("exact", exact), ("domain", domain)):
            for email, label in sorted(bucket.items(), key=lambda kv: latest[kv[0]], reverse=True):
                day, title = events[email][0]["day"], events[email][0]["title"]
                print(f"  [{tier:6}] {day}  {label}")
                print(f"            via {email} — {title[:60]}")
    if upcoming:
        known_up = {e: (by_email.get(e) or by_domain.get(e.split("@", 1)[1], ""))
                    for e in upcoming if not e.endswith("@" + INTERNAL_DOMAIN)}
        known_up = {e: l for e, l in known_up.items() if l}
        if known_up:
            print()
            print("UPCOMING — scheduled, NOT a touch, listed so it is never counted as one:")
            for email, label in sorted(known_up.items(), key=lambda kv: upcoming[kv[0]][0]):
                _, day, title = upcoming[email]
                print(f"  {day}  {label} — {title[:55]}")

    if unknown:
        print()
        print("UNMATCHED external addresses (each one a person not in the record):")
        for email, dom in sorted(unknown.items(), key=lambda kv: kv[1])[:25]:
            if dom in FREEMAIL:
                continue
            print(f"  {latest[email]}  {email}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
