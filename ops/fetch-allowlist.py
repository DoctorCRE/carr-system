#!/usr/bin/env python3
"""fetch-allowlist.py — the fetchable-host list, DERIVED FROM THE RECORD.

WHY (Joe, 2026-08-09: "build A and B", after the deal-history research slice ran
half-blind and filed four of fifteen clients found:false).

THE ERROR THIS FIXES. `KNOWN_HOSTS` in hooks/guard-unattended.py is trust encoded
as CODE. That is right for infrastructure — the Worker, Neon, R2, a licensing
board — because those are a fixed set somebody decided once. It is wrong for
client practice websites, which are a different host per client, unknown until
the client exists, and growing every week. Encoding them in code means the list
grows by one hand-edit per client, forever, and every edit is a session asking
to widen a security allowlist. That does not scale and it should not.

So this half of the fix makes the list DATA: the hosts a session may fetch are
the code list UNION the domains already recorded against real people in the
book.

WHERE THE DOMAINS COME FROM, and why not a website column. The obvious design was
a `website` field on the party record. Rejected on 2026-08-09 for two reasons.
First, no such column exists, so it needs a migration against a production
database shared with Dell, to store something the record already implies. Second
and larger: a website column would be written by SESSIONS during research, and a
list a session can append to is not a control — it is a session granting itself
access and writing the permission slip afterwards.

EMAIL DOMAINS avoid both. `frontdesk@example-practice.com` names the practice's
own domain; the field is partner-entered at intake; and the standing doctrine
that identity fields are never edited on research alone already governs it. The
list maintains itself as the book grows, with no migration and no new surface.

HONEST LIMIT, because this is a security boundary and overclaiming one is worse
than not having it. The `update-party-contact` verb CAN write an email, so a
determined or injected session could in principle add a domain and then fetch it.
What this buys is not impossibility, it is ATTRIBUTION: widening now requires
writing an audited, attributed, human-visible record against a named party,
instead of silently reaching an arbitrary host. Audited widening, not blocked
widening. Anyone reading this should not believe otherwise.

FREE-MAIL IS EXCLUDED and that is the load-bearing filter. 16 of 39 client
addresses are gmail.com. Allowlisting gmail.com because a client uses Gmail would
hand every session the whole of Google's mail infrastructure on the strength of
one doctor's personal address. The same reasoning covers carr.us: our own domain
is not a client practice site.

    ops/fetch-allowlist.py            # write out/fetch-allowlist.txt
    ops/fetch-allowlist.py --check    # report only, exit 1 if stale/missing
"""

import os
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
OUT = os.path.join(REPO, "out", "fetch-allowlist.txt")

# Staleness bound. The book changes when a client is added, which is not daily,
# so a week is generous; past that the list is assumed to be missing new clients
# and the bound action below regenerates it.
STALE_DAYS = 7

# Consumer mail providers and our own domain. A client's personal address tells
# us nothing about a practice website.
FREEMAIL = {
    "gmail.com", "googlemail.com", "hotmail.com", "outlook.com", "live.com",
    "msn.com", "yahoo.com", "ymail.com", "rocketmail.com", "aol.com", "aim.com",
    "icloud.com", "me.com", "mac.com", "proton.me", "protonmail.com", "pm.me",
    "gmx.com", "gmx.net", "mail.com", "zoho.com", "yandex.com", "qq.com",
    "163.com", "comcast.net", "att.net", "bellsouth.net", "cox.net",
    "verizon.net", "sbcglobal.net", "charter.net", "earthlink.net", "juno.com",
    "windstream.net", "embarqmail.com", "roadrunner.com", "rr.com",
    # REGIONAL ISPs, added after the first real run. A Gulf Coast practice owner
    # on their local telco's mail service produced gulftel.com, knology.net and
    # harlowdmd.gccoxmail.com — an ISP, an ISP, and a Cox business-mail
    # subdomain. Identical class error to Gmail: the domain belongs to the
    # provider, not the practice, and allowlisting it grants the provider's whole
    # estate on the strength of one doctor's address.
    "gulftel.com", "knology.net", "gccoxmail.com", "coxmail.com", "mchsi.com",
    "bellsouth.com", "centurylink.net", "frontier.com", "mediacombb.net",
    # Ours, not a client's.
    "carr.us", "carr.com",
}

# Institutional TLDs. A lead employed by a university or an agency gives us
# uab.edu, which is a whole institution rather than a practice website. Same
# reasoning as free-mail, applied at the TLD.
INSTITUTIONAL_TLDS = {"edu", "gov", "mil", "int"}

# A hostname, conservatively. Anything else in an email field is data entry we
# should not be turning into a network permission.
HOST_OK = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$")

# The export views that carry a human's email. Each is (view, column). Missing
# views or columns are skipped rather than fatal — this must not become the
# reason a nightly run fails.
#
# VENDORS ARE DELIBERATELY EXCLUDED, and the first run is why. Including
# v_export_vendors produced 98 extra domains that were not practice websites at
# all: wellsfargo.com, bankofamerica.com, morganstanley.com, usbank.com, pnc.com,
# mckesson.com, adp.com, hoganlovells.com, uab.edu. Those are the EMPLOYERS of
# people in the vendor network — a banker, a lawyer, a distributor rep — and the
# inference "a contact's email domain is a site we need to fetch" simply does not
# hold for an employee of a large institution the way it holds for a practice
# owner.
#
# The cost of getting that wrong is not theoretical. This list also extends the
# BASH allowlist, where curl can POST, so shipping it as first written would have
# granted every session an outbound POST channel to ninety-eight major financial,
# legal and healthcare domains — a far better exfiltration target than anything
# the guard currently blocks. Clients and leads only: those are the practice
# sites the verification work actually needs to read (rules 5d44d3f3, d26d63b0),
# and each is one owner-operator's own domain.
SOURCES = [
    ("v_export_clients", '"Email"'),
    ("v_export_leads", '"Email"'),
]


def _db_url():
    path = os.path.expanduser("~/.config/carr/db.env")
    if not os.path.exists(path):
        return None
    for line in open(path):
        if line.startswith("CARR_DB_EXPORTER_URL="):
            return line.split("=", 1)[1].strip().strip("'\"")
    return None


def _raw_via_verb():
    """{view: [raw domain, ...]} through the record verb. Raises on any failure.

    THE DEFAULT PATH, and the reason this function exists at all (2026-08-19).
    Reading the two export views directly needs CARR_DB_EXPORTER_URL, and on a
    SECOND machine that was the only thing standing between it and needing no
    database credential of its own — so the credential question was really this
    one query's question. `export-email-domains` aggregates in SQL and hands
    back domains, never addresses, which is strictly less than the connection
    below can reach, and it arrives through the machine door so the read is
    attributable to the partner who made it rather than to a shared string in a
    file on a laptop.

    The verb is tried FIRST on every machine, primary included, so both run the
    same path and it cannot rot on the machine that never exercises it.
    """
    import json
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    p = subprocess.run(
        [os.path.join(repo, "run.sh"), "call", "export-email-domains", "{}"],
        capture_output=True, text=True, timeout=120, stdin=subprocess.DEVNULL)
    # STDOUT AND STDERR ARE DIFFERENT CHANNELS AND MERGING THEM BROKE THIS
    # (found 2026-08-19, the first live run after the verb reached production).
    # run.sh writes the result as clean JSON on stdout and its identity
    # preamble on stderr. The first version concatenated the two, so the
    # preamble landed AFTER the closing brace, json.loads choked on trailing
    # content, and the failure surfaced as the reason "{" — a parser reporting
    # a brace as the cause of its own confusion. The verb was live and working
    # the whole time; only the reading of it was wrong.
    #
    # raw_decode stops at the end of the first complete object instead of
    # demanding that the whole string be JSON, so trailing output can never
    # break this again on either channel.
    def _first_json(blob):
        at = (blob or "").find("{")
        if at == -1:
            return None
        try:
            return json.JSONDecoder().raw_decode(blob, at)[0]
        except Exception:
            return None

    payload = _first_json(p.stdout)
    if p.returncode != 0 or not isinstance(payload, dict):
        # Only now consult stderr, where a refusal prints "TOOL ERROR {json}".
        err = _first_json(p.stderr)
        why = (err or payload or {}).get("error") if isinstance(err or payload, dict) else None
        first_line = ((p.stderr or p.stdout or "").strip().splitlines() or ["verb call failed"])[0]
        raise RuntimeError(why or first_line[:160])
    if not payload.get("ok"):
        raise RuntimeError(payload.get("error") or "verb returned no ok")
    by_source = payload.get("by_source") or {}
    if not by_source:
        raise RuntimeError("verb returned no per-source domains")
    return {view: list(rows) for view, rows in by_source.items()}, list(payload.get("notes") or [])


def _raw_via_db():
    """{view: [raw domain, ...]} through a direct connection. Raises without one.

    THE FALLBACK, kept rather than deleted because the allowlist must survive
    the worker being unreachable on the machine that has a credential — and
    because deleting it would make this script's correctness depend on a
    network hop it never used to need. A machine with no credential simply does
    not have this path, which is the intended end state for the second Mac.
    """
    import psycopg
    url = _db_url()
    if not url:
        raise RuntimeError("no CARR_DB_EXPORTER_URL in ~/.config/carr/db.env")
    out, notes = {}, []
    with psycopg.connect(url) as conn:
        for view, col in SOURCES:
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        f'select distinct lower(split_part({col}, \'@\', 2)) '
                        f'from {view} where {col} like %s', ("%@%",))
                    out[view] = [r[0] for r in cur.fetchall() if r[0]]
            except Exception as exc:
                conn.rollback()
                notes.append(f"{view}: skipped ({type(exc).__name__})")
    if not out:
        raise RuntimeError("no export view was readable")
    return out, notes


def collect():
    """Return (sorted hosts, per-source counts, notes).

    The SOURCE of the domains is now pluggable; the POLICY below is not, and
    that split is deliberate. Which domains the guard may trust — free-mail
    exclusion by suffix, institutional TLDs, hostname shape — stays here in the
    file that owns the security decision, so a change to the read path can
    never quietly change what the guard trusts.
    """
    notes = []
    try:
        raw, verb_notes = _raw_via_verb()
        notes.extend(verb_notes)
    except Exception as verb_exc:
        notes.append(f"verb path unavailable ({type(verb_exc).__name__}: {verb_exc})")
        raw, db_notes = _raw_via_db()   # raises if this machine has no credential
        notes.extend(db_notes)
        notes.append("read through the direct database connection")
    hosts, counts = set(), {}
    for view, found in raw.items():
        kept = 0
        for d in found:
            d = (d or "").strip().strip(".").lower()
            if not d or not HOST_OK.match(d):
                continue
            # SUFFIX match, not exact. harlowdmd.gccoxmail.com is a Cox
            # subdomain and an exact-membership test sails straight past it.
            if d in FREEMAIL or any(d.endswith("." + f) for f in FREEMAIL):
                continue
            if d.rsplit(".", 1)[-1] in INSTITUTIONAL_TLDS:
                continue
            hosts.add(d)
            kept += 1
        counts[view] = (len(found), kept)
    return sorted(hosts), counts, notes


def write(hosts, counts, notes):
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    lines = [
        "# fetch-allowlist — GENERATED by ops/fetch-allowlist.py. Do not hand-edit.",
        "# Practice domains derived from partner-entered email addresses in the record.",
        "# Consumed by hooks/guard-unattended.py, unioned with its KNOWN_HOSTS.",
        f"# generated: {stamp}",
    ]
    for view, (raw, kept) in sorted(counts.items()):
        lines.append(f"#   {view}: {raw} distinct domains seen, {kept} kept after free-mail filter")
    lines.extend(f"#   {n}" for n in notes)
    lines.append("")
    lines.extend(hosts)
    with open(OUT, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    return stamp


def age_days():
    if not os.path.exists(OUT):
        return None
    mt = datetime.fromtimestamp(os.path.getmtime(OUT), timezone.utc)
    return (datetime.now(timezone.utc) - mt).total_seconds() / 86400.0


def read_hosts():
    """Also imported by the guard. Never raises."""
    try:
        with open(OUT, encoding="utf-8") as fh:
            return [ln.strip() for ln in fh
                    if ln.strip() and not ln.lstrip().startswith("#")]
    except Exception:
        return []


def cmd_check():
    age = age_days()
    n = len(read_hosts())
    # THE BOUND ACTION PRINTS INLINE (rule 590b11e1) — a health row that states a
    # number without stating what a bad number triggers is a metric nobody owns.
    bound = ("on breach: run ops/fetch-allowlist.py (nightly.sh does it); "
             "a client added since the last run cannot have their site read")
    if age is None:
        print(f"fetch-allowlist: MISSING — never generated · {bound}")
        return 1
    if age > STALE_DAYS:
        print(f"fetch-allowlist: STALE — {n} hosts, {age:.1f}d old (cap {STALE_DAYS}d) · {bound}")
        return 1
    print(f"fetch-allowlist: OK — {n} hosts, regenerated {age:.1f}d ago · {bound}")
    return 0


def main():
    if "--check" in sys.argv[1:]:
        return cmd_check()
    hosts, counts, notes = collect()
    stamp = write(hosts, counts, notes)
    print(f"fetch-allowlist: wrote {len(hosts)} host(s) to "
          f"{os.path.relpath(OUT, REPO)} at {stamp}")
    for view, (raw, kept) in sorted(counts.items()):
        print(f"  {view}: {raw} seen, {kept} kept")
    for n in notes:
        print(f"  {n}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
