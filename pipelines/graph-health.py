#!/usr/bin/env python3
"""
graph-health.py — data-quality anomalies as a LIST, not as dots to click.

WHY
  The Obsidian graph is good at making you ASK "why isn't that connected?" and bad
  at answering it. Every diagnosis in the 2026-07-25 session came from the command
  line, not from the graph. This gives the graph its job back (orientation, shape)
  by reporting the anomalies it can only gesture at.

  Reads the same four sources as build-graph-notes.py plus the generated Graph/,
  so it stays honest about what the graph is actually showing.

WHAT IT CHECKS
  1  Isolated nodes .......... records with no edge at all — usually nothing real in them
  2  Placeholder names ....... TBD / unknown / "last name" / parentheticals
  3  Multi-person fields ..... two people crammed into one name cell
  4  Cross-record duplicates . same person as BOTH a lead and a client — the failure
                               that put paying clients on a prospecting drip
  5  Missing source .......... leads with no source, deals with no referral
  6  Name vs email ........... email local part shares no token with the name —
                               catches the "emailing the wrong human" class
  7  Duplicate node titles ... regression guard; must stay 0 or links go ambiguous

Usage:  run.sh graph-health   |   python3 pipelines/graph-health.py [VAULT] [--verbose]
Exit 0 always — this is a report, not a gate.
"""
import sys, os, re, json, glob, difflib
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from lib.drive_recovery import RecoveryArgumentError, parse_recovery_controls
from lib.record_sources import (MODE_FILES, MODE_RECORDS, load_clients, load_deals_doc,
                                load_leads, load_vendors, resolve_mode, source_note)

try:
    _RECOVERY = parse_recovery_controls(sys.argv[1:], "graph-health canonical record ingress")
except RecoveryArgumentError as exc:
    raise SystemExit(f"graph-health: {exc}") from exc
if any(arg != "--verbose" for arg in _RECOVERY.args):
    raise SystemExit("graph-health: unexpected argument")
VERBOSE = "--verbose" in _RECOVERY.args
REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
MODE = MODE_FILES if _RECOVERY.recovery else MODE_RECORDS
ROOT = str(_RECOVERY.vault) if _RECOVERY.recovery else REPO
GRAPH = os.path.join(REPO, "out", "recovery" if _RECOVERY.recovery else "", "Graph")

def s(v): return str(v if v is not None else "").strip()

# ORDER 29a: the anomaly report reads what the graph read. Two readers on two
# sources would report anomalies the graph does not have and miss the ones it does.
vendors = load_vendors(ROOT, MODE)
leads   = load_leads(ROOT, MODE)
clients = load_clients(ROOT, MODE)
deals   = load_deals_doc(ROOT, MODE)["deals"]

findings: list[tuple[str, str, str]] = []  # (severity, category, detail)
def add(sev, cat, detail): findings.append((sev, cat, detail))

# ---------- 1 & 7: graph-derived checks ----------
titles = defaultdict(list)
for sub in ("vendors", "leads", "clients", "deals"):
    for f in glob.glob(os.path.join(GRAPH, sub, "*.md")):
        titles[os.path.basename(f)].append(sub)

for name, subs in sorted(titles.items()):
    if len(subs) > 1:
        add("HIGH", "Duplicate node title",
            f"{name[:-3]} exists in {' + '.join(subs)} — every [[link]] to it is ambiguous")

ent = {}
for sub in ("vendors", "leads", "clients", "deals"):
    for f in glob.glob(os.path.join(GRAPH, sub, "*.md")):
        ent[os.path.splitext(os.path.basename(f))[0]] = 0
for sub in ("vendors", "leads", "clients", "deals", "hubs"):
    for f in glob.glob(os.path.join(GRAPH, sub, "*.md")):
        t = open(f, encoding="utf-8", errors="ignore").read()
        src = os.path.splitext(os.path.basename(f))[0]
        for m in re.findall(r"\[\[([^\]]+)\]\]", t):
            if m in ent:
                ent[m] += 1
                if src in ent: ent[src] += 1
for n, c in sorted(ent.items()):
    if c == 0:
        add("MED", "Isolated node", f"{n} — no firm, market, owner or referral to connect on")

# ---------- 2 & 3: name quality ----------
PLACEHOLDER = re.compile(r"\bTBD\b|\bunknown\b|last name|\benrich\b|^\(|linkedin|listing \d+", re.I)
SUFFIX = r"(?:jr|sr|ii|iii|dds|dmd|md|do|pa|np|cpa|esq|phd|rn|fnp|otr)"
MULTINAME = re.compile(r"^([A-Z][a-z'’-]+ [A-Z][a-z'’-]+) ([A-Z][a-z'’-]+ [A-Z][a-z'’-]+)$")

# A practice name is four capitalised words too ("Seagate Oral Surgery"), so the
# four-word shape alone is not evidence of two people. Any business/clinical word
# means it is an entity, not a pair of humans.
ORG_WORD = re.compile(
    r"\b(center|centre|clinic|clinical|surgery|surgical|health|healthcare|medical|"
    r"medicine|dental|dentistry|dentist|ortho\w*|chiro\w*|veterinar\w*|animal|pet|"
    r"vision|eye|optical|therapy|therapies|wellness|rehab\w*|hospital|institute|"
    r"practice|group|associates|partners|family|pediatric\w*|women\w*|floor|spa|"
    r"studio|lab|labs|imaging|urgent|care|physicians?|dermatolog\w*|endodont\w*|"
    r"periodont\w*|oral|maxillofacial|podiatr\w*|cardiolog\w*|ent|dpc|pllc|llc|inc|"
    r"pa|pc|bank|financial|insurance|realty|construction|design|build|solutions)\b", re.I)

def name_checks(label, name, ident):
    if not name: return
    if PLACEHOLDER.search(name):
        add("MED", "Placeholder name", f"{label} {ident}: “{name}”")
    m = MULTINAME.match(name.strip())
    if m and not ORG_WORD.search(name) and not re.fullmatch(SUFFIX, m.group(2).split()[-1], re.I):
        add("HIGH", "Two people in one field",
            f"{label} {ident}: “{name}” looks like “{m.group(1)}” + “{m.group(2)}”")

for v in vendors: name_checks("vendor", s(v.get("Name")), s(v.get("ID")))
for l in leads:   name_checks("lead",   s(l.get("Contact Name")), s(l.get("Lead ID")))
for c in clients: name_checks("client", s(c.get("Name")), s(c.get("Client ID")))

# ---------- 4: same person as both lead and client ----------
lead_by_name = {s(l.get("Contact Name")).lower(): l for l in leads if s(l.get("Contact Name"))}

# 4a. THE ONE THAT COSTS MONEY: a client on a live deal who is ALSO sitting in a
# prospecting drip, so CARR is emailing "ever considered real estate?" to someone
# mid-transaction. Session 1 caught this on one client by hand; the check found
# six. Ranked above the generic duplicate because it is actively going out.
# LATE stages only. The first version included "research" and "pending", which
# flagged early-stage prospects as live clients and inflated a 2-case problem into
# a 6-case one (Joe caught it: "none of those are even under contract"). A monthly
# newsletter reaching someone at Research or Pending is not a defect — that is what
# nurture is for. It is a defect once they are under contract.
LIVE = ("closing", "legal", "due diligence", "negotiation", "negotiating",
        "won", "active client")
for c in clients:
    nm, st = s(c.get("Name")).lower(), s(c.get("Status"))
    if st.lower().startswith("merged into"):
        continue
    l = lead_by_name.get(nm)
    if not l or not any(k in st.lower() for k in LIVE):
        continue
    # A client under contract should still HEAR from CARR — going silent the moment
    # someone hires you is its own mistake. Joe's call, 2026-07-27: they come off the
    # prospecting lane and onto "Client Care (post-close)". So the defect is being on
    # a PROSPECTING drip, not being on a drip. (Config tab holds the lane vocabulary.)
    drip = s(l.get("Drip Campaign"))
    CLIENT_LANES = ("client care",)
    prospecting = drip and not any(k in drip.lower() for k in CLIENT_LANES)
    if prospecting or (not drip and "drip" in s(l.get("Stage")).lower()):
        add("HIGH", "LIVE CLIENT ON A PROSPECTING DRIP",
            f"{s(c.get('Name'))} — {s(c.get('Client ID'))} is “{st}” but lead "
            f"{s(l.get('Lead ID'))} is “{s(l.get('Stage'))}” on “{drip}”. "
            f"Move to “Client Care (post-close)”, do not just remove them.")

# 4b. the generic case: same person as both a lead and a client
# The 116 roster records Dell exported were migrated into the registry as leads on
# 2026-07-06, so an unworked roster record having a lead row is the DESIGN, not
# damage. Reporting all 46 at MED (35 of them that overlap) buried the handful that
# are actually being worked as a client and a prospect at the same time — which is
# the pair that puts a live client on a drip. Split by status. (2026-07-27)
for c in clients:
    nm = s(c.get("Name")).lower()
    st = s(c.get("Status"))
    if st.lower().startswith("merged into"):
        continue          # a tombstone is a resolved duplicate, not an open one
    if nm and nm in lead_by_name:
        expected = "roster" in st.lower() and "unworked" in st.lower()
        add("LOW" if expected else "MED",
            "Roster/registry overlap (expected)" if expected else "Lead + client duplicate",
            f"“{s(c.get('Name'))}” is {s(c.get('Client ID'))} (client, {st}) "
            f"AND {s(lead_by_name[nm].get('Lead ID'))} (lead)")

# 4c. the same person holding TWO client IDs — pure migration damage.
# A merged duplicate keeps its row as a TOMBSTONE (Status "Merged into C-0xx") so
# that references written before the merge still resolve — the L-162 lesson. A
# tombstone is a resolved duplicate, so it must not keep reporting as an open one,
# or the check never goes quiet and stops being read. (2026-07-27)
def _tombstoned(c):
    return s(c.get("Status")).lower().startswith("merged into")

_by = defaultdict(list)
for c in clients:
    if s(c.get("Name")) and not _tombstoned(c):
        _by[s(c.get("Name")).lower()].append(s(c.get("Client ID")))
for nm, ids in sorted(_by.items()):
    if len(ids) > 1:
        add("HIGH", "Duplicate client record",
            f"“{nm.title()}” holds {len(ids)} client IDs: {', '.join(ids)} — merge to one")

# Near-duplicate names the exact match cannot see. "Dr Jordan Ridley" and "Jordan
# Ridley" were two records for one man and 4c was blind to both of them, so the
# merge pass found a third Ridley row the check had never reported.
_seen = [(s(c.get("Name")), s(c.get("Client ID"))) for c in clients
         if s(c.get("Name")) and not _tombstoned(c)]
_DROP = {"dr", "mr", "mrs", "ms", "dds", "dmd", "md", "do", "jr", "sr", "ii", "iii",
         "cpa", "esq", "phd", "rn", "np", "pa", "fnp", "otr", "the", "and"}
_norm = lambda n: " ".join(sorted(p for p in re.split(r"[^a-zA-Z]+", n.lower())
                                  if len(p) > 1 and p not in _DROP))
_near = defaultdict(list)
for nm, cid in _seen:
    if _norm(nm): _near[_norm(nm)].append(f"{nm} ({cid})")
for key, who in sorted(_near.items()):
    if len(who) > 1 and len({w.split(" (")[0].lower() for w in who}) > 1:
        add("HIGH", "Near-duplicate client name",
            f"{' and '.join(who)} normalise to the same person — confirm and merge")

# ---------- 5: missing source ----------
no_src = [s(l.get("Lead ID")) for l in leads
          if not s(l.get("Source Detail (V-ID / event / referrer)")) and not s(l.get("Source Type"))]
if no_src:
    add("LOW", "Lead with no source", f"{len(no_src)} leads: {', '.join(no_src[:12])}"
        + (" …" if len(no_src) > 12 else ""))
no_ref = [s(d.get("name")) for d in deals if not s(d.get("referral"))]
if no_ref:
    add("LOW", "Deal with no referral", f"{len(no_ref)} deals: {', '.join(no_ref[:10])}"
        + (" …" if len(no_ref) > 10 else ""))

# ---------- 6: name vs email ----------
TITLES = {"dr", "mr", "mrs", "ms", "dds", "dmd", "md", "do", "jr", "sr", "ii", "iii",
          "cpa", "esq", "phd", "rn", "np", "pa", "fnp", "otr", "the", "and"}

def name_parts(name):
    return [p for p in re.split(r"[^a-zA-Z]+", name.lower())
            if len(p) > 1 and p not in TITLES]

# Words that make a local part a PRACTICE inbox rather than a person's address.
# examplefamilydentistry@gmail.com on a row named for an individual clinician is the
# practice's front desk, not a stranger. Free-provider domains hide this from the
# domain test. (Example sanitized 2026-08-06, ORDER 42b — the original named a real lead.)
PRACTICE_WORD = re.compile(
    r"dental|dentist|dds|dmd|ortho|perio|endo|smile|oral|vet|animal|paw|spay|dog|cat|"
    r"medical|medicine|clinic|health|wellness|care|surgery|surgical|derm|cardio|ophth|"
    r"optom|eye|vision|chiro|therapy|rehab|pediatr|family|sinus|foot|podiatr|doc\b|dr\b")


def _fuzzy_hit(part, local):
    """True when `part` appears in `local` with at most one character of slop.

    Covers the whole spelling-variant family: jcombs/Coombs, dtherndon/Therondon,
    shawn@/Shaun, efaulkner/Falkiner. These are one person with a typo on one side,
    not mail going to a stranger, and calling them HIGH is what buried the real ones.
    (Examples sanitized 2026-08-06, ORDER 42b — the originals named real leads.)
    """
    if len(part) < 4:
        return False
    for w in (len(part) - 1, len(part), len(part) + 1):
        for i in range(0, max(1, len(local) - w + 1)):
            win = local[i:i + w]
            if not win:
                continue
            if difflib.SequenceMatcher(None, part, win).ratio() >= (1 - 1.0 / len(part)):
                return True
    return False


def email_check(label, name, email, ident, company="", notes=""):
    """Flag only when the address plausibly belongs to SOMEONE ELSE.

    Real addresses take many shapes and none of them are a token match:
      jondoe@        (concatenated)   jholder@  (initial+surname)
      hicksc@        (surname+initial) c.busby@ (initial.surname)
    Flagging those produced 207 false positives on the first run, cut to 52. On
    2026-07-27 those 52 were read one by one and roughly six were real. The other
    four fifths were four shapes this now models, because a check that is 85% wrong
    is worse than no check: it teaches you to skim past the ones that matter.

      abc@example-dds.com       the DOMAIN is his own name (only the local part was read)
      mcg@3xy.com, nak07d@me    initials, sometimes with a middle initial or credential
      bcombes@ for "Brad Coombs" a one-character spelling variant, not a stranger
      practicemobile@gmail.com  the practice's inbox on a free provider
    (Examples sanitized 2026-08-06, ORDER 42b — the originals were real lead emails/names.)
    """
    name, email = s(name), s(email)
    if not name or "@" not in email or PLACEHOLDER.search(name): return

    # Triage stamps written into the row by the 2026-07-27 name/email pass. A row a
    # human has already ruled on is not an open finding. Suppressing the resolved
    # ones and demoting the known-bad ones is what keeps HIGH meaning "nobody has
    # looked at this yet", which is the only thing that makes HIGH worth reading.
    up = s(notes).upper()
    if "EMAIL-OK" in up:
        return
    if "EMAIL-DEAD" in up:
        add("MED", "Email domain is dead, needs a new address",
            f"{label} {ident}: “{name}” → {email} — domain has no MX and no A. "
            f"Already triaged; it needs a replacement address, not a second look.")
        return
    if "EMAIL-UNVERIFIED" in up:
        add("MED", "Email flagged DO NOT SEND, awaiting confirmation",
            f"{label} {ident}: “{name}” → {email} — already triaged and blocked from "
            f"sending. Clears when someone confirms the address by phone or first contact.")
        return
    parts = name_parts(name)
    if not parts: return
    local = re.sub(r"[^a-z]", "", email.split("@")[0].lower())
    if not local: return
    domain = email.split("@")[1].lower()
    dom = re.sub(r"[^a-z]", "", domain.split(".")[0])

    for p in parts:
        if p in local or local in p:                    # concatenated or truncated
            return
        if len(p) > 3 and p in dom:                     # his own practice domain
            return
    initials = {p[0] for p in parts}
    for p in parts:                                     # initial+surname / surname+initial
        if len(p) > 2 and any(local == i + p or local == p + i or
                              local.startswith(i + p) or local.startswith(p + i)
                              for i in initials):
            return
    # Initials-only, in name order, optionally trailed by a credential or digits:
    # mcg@3xy.com, gwgdmd@example.net, msidpm@example-doctors.org, nak07d@me.com.
    # (Examples sanitized 2026-08-06, ORDER 42b — the originals were real lead emails.)
    if len(parts) >= 2:
        ini = "".join(p[0] for p in parts)
        if local.startswith(ini) and len(ini) >= 2:
            return

    org = re.sub(r"[^a-z]", "", (company or "").lower())
    generic = (org and (local in org or org.startswith(local) or local.startswith(org[:6]))) \
              or (dom and (local in dom or dom.startswith(local)))
    if generic:
        add("LOW", "Shared/company inbox, not a personal address",
            f"{label} {ident}: “{name}” → {email}")
        return
    if PRACTICE_WORD.search(local):
        add("LOW", "Practice inbox, not a personal address",
            f"{label} {ident}: “{name}” → {email} — reaches the front desk, fine to use, "
            f"but it is not a private line to the decision-maker")
        return
    # Initials WITH a middle initial (mcg@ for Mike Galvin, nak07d@ for Nathan
    # Kessling, msidpm@ for Mark Eisner). Short local part, opens on the first
    # name's initial, carries the surname's initial. Kept visible but not HIGH:
    # it is a plausible reading, not a proven one, so a human confirms before sending.
    run = re.match(r"[a-z]+", local).group(0)
    if len(parts) >= 2 and len(run) <= 6 and run[0] == parts[0][0] and parts[-1][0] in run[1:]:
        add("MED", "Initials, verify before sending",
            f"{label} {ident}: “{name}” → {email} — reads as their initials. Confirm once, "
            f"then it is fine.")
        return
    # Truncated surname (hemmiag@ for Andrew Hemmings, jdkinmorrow@ for Jack Morrow).
    for p in parts:
        if len(p) >= 5 and local.startswith(p[:5]):
            add("MED", "Initials, verify before sending",
                f"{label} {ident}: “{name}” → {email} — surname truncated in the address. "
                f"Confirm once, then it is fine.")
            return

    if any(_fuzzy_hit(p, local) for p in parts):
        add("MED", "Name/email spelling variant",
            f"{label} {ident}: “{name}” vs {email} — same person, one of the two is "
            f"misspelled. Fix the record; do not treat as a wrong recipient.")
        return
    add("HIGH", "Name vs email disagree",
        f"{label} {ident}: “{name}” but address is {email} — mail may reach a different person")

for v in vendors: email_check("vendor", v.get("Name"), v.get("Email"), s(v.get("ID")), s(v.get("Company")), s(v.get("Notes")))
for l in leads:   email_check("lead",   l.get("Contact Name"), l.get("Email"), s(l.get("Lead ID")), s(l.get("Practice")), s(l.get("Notes")))
for c in clients: email_check("client", c.get("Name"), c.get("Email"), s(c.get("Client ID")), s(c.get("Practice / Entity")), s(c.get("Notes")))

# ---------- report ----------
ORDER = {"HIGH": 0, "MED": 1, "LOW": 2}
by_cat = defaultdict(list)
for sev, cat, detail in findings: by_cat[(ORDER[sev], sev, cat)].append(detail)

print(f"\nGRAPH HEALTH — {len(vendors)} vendors, {len(leads)} leads, "
      f"{len(clients)} clients, {len(deals)} deals  [source: {source_note(MODE)}]\n" + "=" * 72)
if not findings:
    print("\nNo anomalies found.\n"); sys.exit(0)

CAP = 10000 if VERBOSE else 6
for category_key in sorted(by_cat):
    _, sev, cat = category_key
    items = by_cat[category_key]
    print(f"\n[{sev}] {cat} — {len(items)}")
    for d in items[:CAP]:
        print(f"   · {d}")
    if len(items) > CAP:
        print(f"   … {len(items)-CAP} more (--verbose for all)")

hi = sum(1 for f in findings if f[0] == "HIGH")
print("\n" + "=" * 72)
print(f"{len(findings)} findings — {hi} HIGH."
      + ("" if VERBOSE else "  Re-run with --verbose for the full list."))
print("HIGH = fix before the data is trusted for outreach or a merge.\n")
