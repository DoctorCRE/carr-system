#!/usr/bin/env python3
"""One-time load of calendar attendees into the record as contacts (WR-000637 C2/C3).

Reads a private attendee file (path argument, never copied into the repo) and
plans two kinds of write, both through record verbs only:

- C3: an external attendee the record does not carry, with a full display name,
  becomes a party through add-party. Never force_new: when add-party answers
  with candidates the attendee is counted, not inserted. A contact is a party,
  never a lead.
- C2: a vendor person with no email gets one through update-party-contact when
  exactly one attendee address at that person's own firm domain matches their
  full name (first.last, firstlast, flast) and that address matches nobody else.
  The party version is read fresh (vendor-assignment-context) just before each write.

A bare address is not an identity, so an attendee without a two-word name is
skipped. Dry run by default; output is counts only, never names or addresses.
"""
from __future__ import annotations

import argparse
import importlib.util
from collections import Counter
import json
from pathlib import Path
import re
import subprocess
import sys
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from lib.outlook_intake import automated, proposable
from lib.unresolved_email import FREE_MAIL

_spec = importlib.util.spec_from_file_location("calendar_touch_matcher", ROOT / "tools/calendar-touch-matcher.py")
assert _spec is not None and _spec.loader is not None
_matcher = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_matcher)
read_view = _matcher.read_view

FILL_SOURCE = "calendar attendee record; address at the firm domain the record carries; matches full name"
ADDRESS = re.compile(r"[\w.+'-]+@([\w-]+(?:\.[\w-]+)+)")


def _norm(name: str | None) -> str:
    return " ".join(str(name or "").lower().split())


def attendees_by_email(data) -> dict[str, str | None]:
    """Collapse either attendee file shape to {address: best display name}."""
    if isinstance(data, dict) and isinstance(data.get("events"), list):
        lists = [e.get("attendees") or e.get("emails") or [] for e in data["events"]]
    elif isinstance(data, dict):
        lists = list(data.values())
    else:
        raise ValueError("attendee file must be an object")
    people: dict[str, str | None] = {}
    for entries in lists:
        for entry in entries:
            email, name = (entry, None) if isinstance(entry, str) else (entry.get("email"), entry.get("name"))
            email = str(email or "").strip().lower()
            name = " ".join(str(name or "").split()) or None
            if not email:
                continue
            if len(name or "") > len(people.get(email) or ""):
                people[email] = name
            else:
                people.setdefault(email, None)
    return people


def _full_name(name: str | None, email: str) -> bool:
    tokens = re.findall(r"[^\W\d_]+", name or "")
    return len(tokens) >= 2 and "@" not in (name or "") and _norm(name) != email


def _local_forms(name: str) -> set[str]:
    tokens = [t.lower() for t in re.findall(r"[^\W\d_]+", name)]
    if len(tokens) < 2 or len(tokens[0]) < 2:
        return set()
    first, last = tokens[0], tokens[-1]
    return {f"{first}.{last}", f"{first}{last}", f"{first}_{last}", f"{first}-{last}", f"{first[0]}{last}"}


def vendor_gaps(vendors) -> list[dict]:
    """Email-less vendors from v_export_vendors, each with the domains its company already carries."""
    domains: dict[str, set[str]] = {}
    for row in vendors:
        company = _norm(row.get("Company"))
        for domain in ADDRESS.findall(row.get("Email") or ""):
            domains.setdefault(company, set()).add(domain.lower())
    return [{"ref": row["ID"], "name": row["Name"], "domains": sorted(domains[_norm(row.get("Company"))])}
            for row in vendors
            if not ADDRESS.search(row.get("Email") or "") and row.get("Name") and domains.get(_norm(row.get("Company")))]


def plan(attendees, contacts, vendor_gaps, *, internal_domain: str) -> dict:
    people = attendees_by_email(attendees)
    carried = {str(c["address"]).strip().lower() for c in contacts}
    carried_names = {_norm(c.get("name")) for c in contacts if c.get("name")}
    external = {e: n for e, n in people.items() if e.rsplit("@", 1)[-1] != internal_domain}
    skipped: Counter = Counter()
    unknown = {}
    for email, name in external.items():
        if email in carried:
            continue
        if not proposable(email):
            skipped["malformed"] += 1
        elif automated(email):
            skipped["automated"] += 1
        else:
            unknown[email] = name

    candidates: dict[str, set[str]] = {}
    for gap in vendor_gaps:
        forms = _local_forms(gap["name"])
        for domain in set(gap["domains"]) - FREE_MAIL:
            for email in unknown:
                local, _, at = email.partition("@")
                if at == domain and local in forms:
                    candidates.setdefault(gap["ref"], set()).add(email)
    claims = Counter(e for emails in candidates.values() for e in emails)
    fill, filled, ambiguous = [], set(), set()
    for ref, emails in sorted(candidates.items()):
        if len(emails) == 1 and claims[next(iter(emails))] == 1:
            email = next(iter(emails))
            fill.append({"ref": ref, "email": email})
            filled.add(email)
        else:
            ambiguous |= emails

    add = []
    for email, name in sorted(unknown.items()):
        if email in filled:
            continue
        if email in ambiguous:
            skipped["ambiguous_vendor_email"] += 1
        elif not _full_name(name, email):
            skipped["no_name"] += 1
        elif _norm(name) in carried_names:
            skipped["name_matches_existing_party"] += 1
        else:
            add.append({"email": email, "name": name})
    return {"external": len(external), "matched_existing": len(external.keys() & carried),
            "add": add, "fill": fill, "skipped": dict(skipped)}


def counts(p: dict) -> dict:
    return {"external": p["external"], "matched_existing": p["matched_existing"],
            "new_contacts": len(p["add"]), "vendor_email_fills": len(p["fill"]), "skipped": p["skipped"]}


def apply(p: dict, call) -> dict:
    """Each write once, with its own key; candidates and refusals are reported, never retried."""
    result = Counter(added=0, add_returned_candidates=0, filled=0, refused=0)
    for person in p["add"]:
        response = call("add-party", {"idempotency_key": str(uuid4()), "kind": "person",
                                      "name": person["name"], "email": person["email"]})
        if response.get("ok") is False:
            result["refused"] += 1
        elif response.get("candidates"):
            result["add_returned_candidates"] += 1
        else:
            result["added"] += 1
    for gap in p["fill"]:
        version = (call("vendor-assignment-context", {"vendor": gap["ref"]}).get("vendor") or {}).get("party_version")
        if not isinstance(version, int):
            result["refused"] += 1
            continue
        response = call("update-party-contact", {"idempotency_key": str(uuid4()), "party": gap["ref"],
                                                 "base_version": version,
                                                 "fields": {"email": gap["email"]}, "source": FILL_SOURCE})
        result["refused" if response.get("ok") is False else "filled"] += 1
    return dict(result)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("attendees", type=Path, help="private attendee JSON; read only")
    parser.add_argument("--internal-domain", default="carr.us")
    parser.add_argument("--apply", action="store_true", help="write through add-party and update-party-contact")
    args = parser.parse_args()

    def call(verb, payload):
        done = subprocess.run([str(ROOT / "run.sh"), "call", verb, json.dumps(payload)],
                              check=True, capture_output=True, text=True, cwd=ROOT, timeout=120)
        return json.loads(done.stdout)

    contacts = call("email-match-contacts", {})["contacts"]
    _, vendors = read_view("v_export_vendors")
    p = plan(json.loads(args.attendees.read_text()), contacts, vendor_gaps(vendors),
             internal_domain=args.internal_domain)
    report = {"plan": counts(p)}
    if args.apply:
        report["applied"] = apply(p, call)
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
