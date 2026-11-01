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
  full name (first.last, firstlast, flast), the attendee's display name does not
  name someone else, and the address shape fits nobody else at the firm or the
  domain, whether or not they already carry an email. A vendor with no company
  has no firm domain. Just before each write a fresh vendor-assignment-context
  read must still show no email, the same name, and the domain carried at that
  company's identity; the write uses that read's party version.

The add-party verb judges person names for both planning and creation. Its
validation-only mode makes no database calls. Dry run by default; output is
counts only, never names or addresses.
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
CALL_TIMEOUT = 120
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


class VerbRefused(Exception):
    """The record answered with a ToolError: nothing was written. Carries only the error code."""


class VerbUncertain(Exception):
    """The call did not answer cleanly: a write may or may not have landed, so it is never retried."""


def _error_code(stderr: str) -> str | None:
    at = stderr.find("TOOL ERROR ")
    if at < 0:
        return None
    try:
        code = json.JSONDecoder().raw_decode(stderr[at + len("TOOL ERROR "):])[0].get("error")
    except (ValueError, AttributeError):
        return "unnamed"
    return code if isinstance(code, str) and re.fullmatch(r"[a-z][a-z0-9_]{0,63}", code) else "unnamed"


def run_verb(verb: str, payload: dict) -> dict:
    """One record verb through ./run.sh call, the payload on stdin so no argv or traceback shows it."""
    try:
        done = subprocess.run([str(ROOT / "run.sh"), "call", verb, "-"], input=json.dumps(payload),
                              capture_output=True, text=True, cwd=ROOT, timeout=CALL_TIMEOUT)
    except subprocess.TimeoutExpired:
        raise VerbUncertain("timeout") from None
    if done.returncode:
        code = _error_code(done.stderr)
        if code:
            raise VerbRefused(code)
        raise VerbUncertain(f"exit_{done.returncode}")
    try:
        return json.loads(done.stdout)
    except ValueError:
        raise VerbUncertain("unparseable") from None


def _tokens(name: str | None) -> list[str]:
    return [t.lower() for t in re.findall(r"[^\W\d_]+", name or "")]


def _names_conflict(display: str | None, name: str) -> bool:
    """True when an attendee's display name names someone other than the vendor person."""
    shown, own = _tokens(display), _tokens(name)
    if "@" in (display or "") or not shown:
        return False
    if len(shown) == 1:
        return shown[0] not in (own[0], own[-1])
    return {shown[0], shown[-1]} != {own[0], own[-1]}


def check_names(names: list) -> list[bool]:
    """Use the local registered add-party verb in validation-only mode; no writes or network."""
    try:
        done = subprocess.run(["node", str(ROOT / "mcp-server/check-party-names.mjs")],
                              input=json.dumps(names), capture_output=True, text=True,
                              cwd=ROOT, timeout=CALL_TIMEOUT)
        results = json.loads(done.stdout) if done.returncode == 0 else None
    except (subprocess.TimeoutExpired, ValueError):
        results = None
    if not isinstance(results, list) or len(results) != len(names) or any(type(r) is not bool for r in results):
        raise VerbUncertain("name_check_failed")
    return results


def _local_forms(name: str) -> set[str]:
    tokens = _tokens(name)
    if len(tokens) < 2 or len(tokens[0]) < 2:
        return set()
    first, last = tokens[0], tokens[-1]
    return {f"{first}.{last}", f"{first}{last}", f"{first}_{last}", f"{first}-{last}", f"{first[0]}{last}"}


def vendor_gaps(vendors) -> list[dict]:
    """Email-less vendors from v_export_vendors with a company, each with the domains that company
    already carries and the names of everyone else on record there."""
    firms: dict[str, list[dict]] = {}
    for row in vendors:
        if _norm(row.get("Company")):
            firms.setdefault(_norm(row.get("Company")), []).append(row)
    domains = {company: sorted({d.lower() for r in rows for d in ADDRESS.findall(r.get("Email") or "")})
               for company, rows in firms.items()}
    return [{"ref": row["ID"], "name": row["Name"], "company": company, "domains": domains[company],
             "colleagues": sorted(r["Name"] for r in firms[company] if r is not row and r.get("Name"))}
            for row in vendors
            if (company := _norm(row.get("Company"))) and domains[company]
            and row.get("Name") and not ADDRESS.search(row.get("Email") or "")]


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

    gaps = {gap["ref"]: gap for gap in vendor_gaps}
    candidates: dict[str, set[str]] = {}
    for gap in vendor_gaps:
        forms = _local_forms(gap["name"])
        for domain in set(gap["domains"]) - FREE_MAIL:
            for email in unknown:
                local, _, at = email.partition("@")
                if at == domain and local in forms and not _names_conflict(unknown[email], gap["name"]):
                    candidates.setdefault(gap["ref"], set()).add(email)
    claims = Counter(e for emails in candidates.values() for e in emails)
    fill, filled, ambiguous = [], set(), set()
    for ref, emails in sorted(candidates.items()):
        email = next(iter(emails))
        local, _, domain = email.partition("@")
        others = gaps[ref]["colleagues"] + [c.get("name") or "" for c in contacts
                                            if str(c["address"]).strip().lower().endswith("@" + domain)]
        if len(emails) == 1 and claims[email] == 1 and not any(local in _local_forms(n) for n in others):
            fill.append({"ref": ref, "email": email, "name": gaps[ref]["name"], "company": gaps[ref]["company"]})
            filled.add(email)
        else:
            ambiguous |= emails

    add = []
    name_checks = dict(zip(unknown, check_names(list(unknown.values()))))
    for email, name in sorted(unknown.items()):
        if email in filled:
            continue
        if email in ambiguous:
            skipped["ambiguous_vendor_email"] += 1
        elif not name_checks[email]:
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


def fill_blocker(gap: dict, vendor: dict) -> str | None:
    """Why a fresh vendor-assignment-context read no longer supports this planned fill, if it doesn't."""
    if not isinstance(vendor.get("party_version"), int):
        return "unreadable"
    if str(vendor.get("party_email") or "").strip():
        return "email_already_set"
    if _norm(vendor.get("party_name")) != _norm(gap["name"]):
        return "identity_changed"
    if not vendor.get("company") or _norm(vendor["company"].get("name")) != gap["company"]:
        return "company_changed"
    if gap["email"].rsplit("@", 1)[-1] not in (vendor.get("company_domains") or []):
        return "domain_not_at_company"
    return None


def apply(p: dict, call) -> dict:
    """Each write once, with its own key. Candidates, refusals and uncertain calls are counted by
    error code, never retried; a fill whose fresh read no longer supports it is skipped."""
    result = Counter(name_skips=0, added=0, add_returned_candidates=0, filled=0, refused=0, uncertain=0)
    errors: Counter = Counter()
    skipped: Counter = Counter()

    def attempt(verb, payload):
        try:
            return call(verb, payload)
        except VerbRefused as e:
            result["refused"] += 1
            errors[str(e)] += 1
            if str(e) == "full_person_name_required":
                result["name_skips"] += 1
        except VerbUncertain as e:
            result["uncertain"] += 1
            errors[str(e)] += 1
        return None

    for person in p["add"]:
        response = attempt("add-party", {"idempotency_key": str(uuid4()), "kind": "person",
                                         "name": person["name"], "email": person["email"]})
        if response is not None:
            result["add_returned_candidates" if response.get("candidates") else "added"] += 1
    for gap in p["fill"]:
        context = attempt("vendor-assignment-context", {"vendor": gap["ref"]})
        if context is None:
            continue
        vendor = context.get("vendor") or {}
        blocker = fill_blocker(gap, vendor)
        if blocker:
            skipped[blocker] += 1
            continue
        if attempt("update-party-contact", {"idempotency_key": str(uuid4()), "party": gap["ref"],
                                            "base_version": vendor["party_version"],
                                            "fields": {"email": gap["email"]}, "source": FILL_SOURCE}) is not None:
            result["filled"] += 1
    return {**result, "fill_skipped": dict(skipped), "errors": dict(errors)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("attendees", type=Path, help="private attendee JSON; read only")
    parser.add_argument("--internal-domain", default="carr.us")
    parser.add_argument("--apply", action="store_true", help="write through add-party and update-party-contact")
    args = parser.parse_args()

    try:
        contacts = run_verb("email-match-contacts", {})["contacts"]
    except (VerbRefused, VerbUncertain) as e:
        print(json.dumps({"error": str(e)}))
        return 1
    _, vendors = read_view("v_export_vendors")
    p = plan(json.loads(args.attendees.read_text()), contacts, vendor_gaps(vendors),
             internal_domain=args.internal_domain)
    report = {"plan": counts(p)}
    if args.apply:
        report["applied"] = apply(p, run_verb)
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
