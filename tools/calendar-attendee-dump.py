#!/usr/bin/env python3
"""Dump event id -> attendee emails from the LOCAL Apple Calendar store.

WHY THIS EXISTS. The calendar ingest lane feeds off the PUBLISHED feed, which
Microsoft strips of ATTENDEE and ORGANIZER by design. Fifty of the fifty-two
untriaged rows therefore carry no addresses at all — their titles are a first name
and nothing else, and matching a first name against the book is the contamination
that welded two different Beasleys together once already.

The LOCAL store has what the feed does not: 137 of 290 events carry attendees. This
writes the mapping so the triage pass can join on it and resolve deterministically.

MUST RUN INSIDE tools/CARR Calendar Access.app. macOS will not show a Calendars
prompt to a process with no usage description, and a bare python binary has no
bundle at all — that is why this lane read as permanently denied until 2026-08-13.

Read-only: it opens nothing, changes nothing, and writes one JSON file of addresses
already sitting in Joe's own calendar.
"""
import hashlib
import json
import os
import sys
import time
from datetime import datetime, timezone

from EventKit import EKEventStore, EKEntityTypeEvent
from Foundation import NSDate

OUT = os.path.join(
    os.environ.get(
        "CARR_CALENDAR_OUTPUT_ROOT",
        os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "out"),
    ),
    "calendar-attendees.json",
)
DAYS_BACK = 400
DAYS_FWD = 120


def request_access(store):
    got = {"v": None}

    def done(ok, err):
        got["v"] = bool(ok)

    if hasattr(store, "requestFullAccessToEventsWithCompletion_"):
        store.requestFullAccessToEventsWithCompletion_(done)
    else:
        store.requestAccessToEntityType_completion_(EKEntityTypeEvent, done)
    for _ in range(600):
        if got["v"] is not None:
            break
        time.sleep(0.1)
    return got["v"]


def main():
    store = EKEventStore.alloc().init()
    if not request_access(store):
        print("Calendars access not granted — nothing written.")
        return 3

    start = NSDate.dateWithTimeIntervalSinceNow_(-DAYS_BACK * 86400)
    end = NSDate.dateWithTimeIntervalSinceNow_(DAYS_FWD * 86400)
    pred = store.predicateForEventsWithStartDate_endDate_calendars_(start, end, None)
    events = store.eventsMatchingPredicate_(pred) or []

    out = {"schema": "calendar-events/v2", "events": []}
    with_attendees = 0
    for ev in events:
        emails = []
        for att in (ev.attendees() or []):
            url = att.URL()
            addr = str(url.resourceSpecifier()) if url else ""
            if "@" in addr:
                emails.append(addr.lower().strip())
        org = ev.organizer()
        if org and org.URL() and "@" in str(org.URL().resourceSpecifier()):
            emails.append(str(org.URL().resourceSpecifier()).lower().strip())
        emails = sorted(set(emails))
        if not emails:
            continue
        with_attendees += 1
        title = str(ev.title() or "").strip()
        start = datetime.fromtimestamp(float(ev.startDate().timeIntervalSince1970()), timezone.utc)
        external = str(ev.calendarItemExternalIdentifier() or "")
        local = str(ev.calendarItemIdentifier() or "")
        identifier = f"server:{external}" if external else f"local:{local}"
        calendar = str(ev.calendar().calendarIdentifier() or "")
        if not (external or local) or not calendar:
            raise ValueError("EventKit event has no stable identity")
        # occurrenceDate is the original recurrence slot, surviving title edits.
        occurrence = ""
        # Detached instances may no longer carry recurrence rules. The server
        # UID and original slot still identify their occurrence after edits.
        slot = ev.occurrenceDate()
        if slot is not None:
            occurrence = str(float(slot.timeIntervalSince1970()))
        elif ev.hasRecurrenceRules():
            raise ValueError("recurring event has no occurrence identity")
        identity = json.dumps([calendar, identifier, occurrence], separators=(",", ":"))
        out["events"].append({
            "event_id": hashlib.sha256(identity.encode()).hexdigest(),
            "start_at": start.isoformat(), "title": title, "emails": emails,
        })

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    fd = os.open(OUT, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as fh:
        json.dump(out, fh, indent=1, sort_keys=True)
    print(f"events scanned: {len(events)}; carrying attendees: {with_attendees}; "
          f"events written: {len(out['events'])} -> {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
