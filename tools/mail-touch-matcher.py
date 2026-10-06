#!/usr/bin/env python3
"""Match outbound mail to people in the record, and surface what was discussed.

The calendar pass proved contact happened. Mail carries more, and Joe said so
plainly: most follow-up never becomes a meeting, so a calendar-only view sees a
fraction of the real contact. Mail also carries SUBSTANCE — terms, commitments,
next steps — which is the half that matters and which currently lives only in his
inbox.

This script does the mechanical half: read the extracted mailbox, match
correspondents against the client roster and lead registry, and group what it
finds by record so the substance can be read per client rather than per message.
It writes NOTHING to the record. It emits proposals.

Reuses the calendar matcher's contact loader rather than reimplementing it, so
both passes resolve a person the same way and cannot drift apart.
"""

import importlib.util
import json
import os
import sys
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
# Loop #129: the original path pointed at a session scratchpad under
# /private/tmp/claude-501 that no longer exists. The extract now resolves in
# order: --extract <path> argument, CARR_MAIL_EXTRACT env var, then the durable
# repo location out/mail-extract.json.
DEFAULT_EXTRACT = os.path.join(HERE, "..", "out", "mail-extract.json")


def resolve_extract(argv):
    if "--extract" in argv:
        i = argv.index("--extract")
        if i + 1 >= len(argv):
            sys.exit("--extract requires a path")
        return argv[i + 1]
    return os.environ.get("CARR_MAIL_EXTRACT") or os.path.abspath(DEFAULT_EXTRACT)

INTERNAL_DOMAIN = "carr.us"
FREEMAIL = {"gmail.com", "icloud.com", "yahoo.com", "hotmail.com", "outlook.com", "aol.com"}
JOE = {"joe.bookout@carr.us", "josephbookout@outlook.com"}


def load_calendar_matcher():
    """Import the calendar matcher as a module so its contact loader is shared."""
    path = os.path.join(HERE, "calendar-touch-matcher.py")
    spec = importlib.util.spec_from_file_location("calendar_touch_matcher", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def domain_of(addr):
    return addr.rsplit("@", 1)[-1].lower() if addr and "@" in addr else ""


def main():
    argv = sys.argv[1:]
    extract = resolve_extract(argv)
    out = os.path.join(os.path.dirname(extract), "mail-matched.json")
    if not os.path.exists(extract):
        sys.exit(f"mail extract not found: {extract}")

    cal = load_calendar_matcher()
    # Shares the calendar matcher's loader, which reads the LIVE exports at the
    # exporters' EXPORT_HOME and refuses an empty book (2026-09-27).
    try:
        by_email, by_domain = cal.load_record_contacts()
    except cal.NoRecordContacts as exc:
        sys.exit(f"no record contacts loaded — {exc}")

    messages = json.load(open(extract))

    # A message counts as CONTACT WITH a person when Joe sent it to them, or when
    # they sent it to Joe. Both directions are evidence the relationship is live;
    # only outbound proves Joe himself followed up, so they stay distinguishable.
    matched = defaultdict(list)
    stats = {
        "messages_total": len(messages),
        "outbound": 0,
        "inbound": 0,
        "unknown_direction": 0,
        "matched_messages": 0,
        "exact_matches": 0,
        "domain_matches": 0,
        "internal_only": 0,
        "unmatched_external": 0,
    }
    unmatched_domains = defaultdict(int)

    for msg in messages:
        direction = (msg.get("direction") or "unknown").lower()
        stats["outbound" if direction == "out" else
              "inbound" if direction == "in" else "unknown_direction"] += 1

        sender = (msg.get("from") or "").lower()
        recipients = [r.lower() for r in (msg.get("to") or []) if r]
        recipients += [r.lower() for r in (msg.get("cc") or []) if r]

        # The counterparties are everyone on the message who is not Joe.
        others = [a for a in ([sender] + recipients) if a and a not in JOE]
        if not others:
            continue
        if all(domain_of(a) == INTERNAL_DOMAIN for a in others):
            stats["internal_only"] += 1
            continue

        hit = None
        for addr in others:
            if addr in by_email:
                hit = (by_email[addr], addr, "exact")
                break
        if hit is None:
            for addr in others:
                dom = domain_of(addr)
                if dom and dom not in FREEMAIL and dom != INTERNAL_DOMAIN and dom in by_domain:
                    hit = (by_domain[dom], addr, "domain")
                    break

        if hit is None:
            for addr in others:
                dom = domain_of(addr)
                if dom and dom != INTERNAL_DOMAIN:
                    unmatched_domains[dom] += 1
            stats["unmatched_external"] += 1
            continue

        label, addr, tier = hit
        stats["matched_messages"] += 1
        stats["exact_matches" if tier == "exact" else "domain_matches"] += 1
        matched[label].append(
            {
                "date": msg.get("date"),
                "direction": direction,
                "matched_on": addr,
                "tier": tier,
                "subject": msg.get("subject"),
                "body": msg.get("body"),
            }
        )

    for msgs in matched.values():
        msgs.sort(key=lambda m: m.get("date") or "")

    payload = {
        "stats": stats,
        "records_with_mail": len(matched),
        "top_unmatched_domains": sorted(
            unmatched_domains.items(), key=lambda kv: -kv[1]
        )[:25],
        "matched": matched,
    }
    json.dump(payload, open(out, "w"), indent=1)

    # Console output carries NO client content — counts and the output path only.
    summary = {k: v for k, v in stats.items()}
    summary["records_with_mail"] = len(matched)
    summary["output_file"] = out
    summary["per_record_message_counts"] = sorted(
        ((len(v), k) for k, v in matched.items()), reverse=True
    )[:20]
    print(json.dumps(summary, indent=1))


if __name__ == "__main__":
    main()
