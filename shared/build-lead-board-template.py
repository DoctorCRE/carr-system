#!/usr/bin/env python3
"""
build-lead-board.py — regenerate The Lead Board operating surface.
Derived VIEW. Reads the two sources of truth and writes lead-board.html.
Run by the weekly lead-system run (see DNA/Leads/lead-system-weekly.md) and any
session that moves a worked lead (refresh-on-change law, mirrors the Deal Room).

Usage:
  python3 build-lead-board.py [CARR_ROOT] [--files|--records]

TWO SOURCE MODES (ORDER 26(b), the ORDER 29a pattern).
  --files    the historical read: the router xlsx, the lane JSONs, the registry
             xlsx. Still the fallback, and still the only mode a cloud-only
             session or machine without a database credential can use. No longer the
             default: the chain flipped to records on 2026-08-04 (ORDER 26(c)).
  --records  the same board derived from the record layer: `candidate_pool` (the
             router rows ORDER 25 imported plus the radar-lane rows ORDER 26(a)
             maps) and the lead registry's export view.

The two modes share every transform. What changes is only where the RAW row came
from — an xlsx cell, a JSON object, or the `source_row` jsonb that stored that
same object verbatim. Both modes feed the same template transforms. Records mode
falls back to files, loudly, when the record path is unreachable; a board that
quietly dropped its radar rows would be worse than one that admits it read the files.

CARR_ROOT defaults to the folder two levels up from this script (…/CARR AI).
Sources:
  <root>/DNA/Leads/lead-router-*.xlsx   (latest by name = the 9,320 reservoir)
  <root>/DNA/Leads/lead-registry.xlsx   (Registry tab = the worked call queue)
  <root>/Automation/lead-board-decisions.json  (optional; the curated decision queue)
  <root>/Automation/renewal-radar.json  (optional; CoStar renewal feed, built by build-renewal-feed.py)
  <root>/Automation/entity-formation-leads.json + pre-entity-watch.json  (optional segment feeds)
  generators/lead-board-template.html          (the shell; repo-canonical 2026-08-20,
                                                vault copy is the cloud-only fallback)
Writes:
  <root>/Automation/lead-board.html
Then: update the `the-lead-board` artifact from that file if the desktop app is
connected (update_artifact); otherwise note it and move on. The file is the record.
"""
import sys, os, glob, json, re
from collections import Counter
from datetime import datetime, date
from typing import Any
import openpyxl

_ARGV = [a for a in sys.argv[1:] if not a.startswith("--")]
ROOT = _ARGV[0] if _ARGV else os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
LEADS_DIR = os.path.join(ROOT, "DNA", "Leads")
AUTO = os.path.join(ROOT, "Automation")

# ── source mode ──────────────────────────────────────────────────────────────
# The import is guarded, not assumed: the cloud-only vault copy of this script,
# per manifest.tsv, sits beside a bare sheets.py with no lib/
# package next to it. There, records mode simply does not exist and the board
# runs exactly as it always has.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    from lib.record_sources import (LANE_SOURCES, MODE_FILES, MODE_RECORDS, ROUTER_SOURCE,
                                    load_leads, load_pool, pool_reach, resolve_mode, source_note)
    _HAVE_RECORDS = True
except ImportError:
    MODE_FILES, MODE_RECORDS, _HAVE_RECORDS = "files", "records", False

if _HAVE_RECORDS:
    # Records mode reads all pool sources through v_export_pool_all. The
    # canonical runner uses the repository interpreter with psycopg; pool_reach
    # verifies availability before rendering. Both modes share transforms, but
    # whole-payload parity requires comparing them on the same input data.
    # The fallback below stays exactly as it was — a board that cannot reach the
    # pool still says so loudly and derives from files rather than failing.
    MODE, _ = resolve_mode(sys.argv[1:], default=MODE_RECORDS)
    if MODE == MODE_RECORDS:
        _want = (ROUTER_SOURCE,) + LANE_SOURCES
        _ok, _why, _, _ = pool_reach(_want)
        if not _ok:
            print(f"[lead-board] records mode unavailable ({_why}) — falling back to the "
                  f"generated files", file=sys.stderr)
            MODE = MODE_FILES
else:
    MODE = MODE_FILES
    if "--records" in sys.argv[1:]:
        print("[lead-board] this copy has no lib/record_sources.py — running file mode",
              file=sys.stderr)

# Schema-validated reads (orchestrator-lane corrective #1, 2026-07-25): columns are
# resolved by HEADER NAME via sheets.py — a moved/renamed column halts loudly instead
# of silently shifting fields. Bootstrap finds lib/sheets.py (repo) or sheets.py (vault copy).
_d = os.path.dirname(os.path.abspath(__file__))
for _c in (os.path.join(_d, "..", "lib"), _d):
    if os.path.isfile(os.path.join(_c, "sheets.py")):
        sys.path.insert(0, _c); break
from sheets import header_map, data_rows

def latest_router():
    cands = sorted(glob.glob(os.path.join(LEADS_DIR, "lead-router-*.xlsx")))
    if not cands: raise SystemExit("No lead-router-*.xlsx found in " + LEADS_DIR)
    return cands[-1]

def norm(d):
    if isinstance(d,(datetime,date)): return d.strftime("%Y-%m-%d")
    return str(d).strip() if d not in (None,"") else ""

SEG_ORDER = [
 ("🏥 NATIONAL ACCOUNT — multi-location / expanding","National Account","multi-location · expanding · portfolio rep","hot"),
 ("\U0001F52D PRE-ENTITY — upstream watch","Pre-Entity Watch","corroborated, BEFORE the filing","hot"),
 ("\U0001F195 NEW ENTITY — corp-filings","New Entity","just filed here · FL + AL","hot"),
 ("🔑 LEASE EVENT — decision window","Lease Event","a real-estate decision is coming","hot"),
 ("\U0001F9ED RELOCATING OWNER — moving to territory","Relocating Owner","out-of-state licensee owns FL property here","hot"),
 ("\U0001F3AF PRACTICE RIPE FOR SALE","Ripe for Sale","refer to practice brokers","hot"),
 ("⭐⭐ POST-SALE FOUNDER (owns nothing)","Post-Sale Founder","the hottest class","hot"),
 ("⭐ ASSOCIATE — going-independent window","Associate in Window","2-5 yr nurture","warm"),
 ("⭐ DSO ASSOCIATE — corporate clinic address","DSO Associate","nurture now","warm"),
 ("PRACTICE OWNER (Sunbiz-confirmed)","Practice Owner","expansion / buy-vs-lease","warm"),
 ("🏢 OWNER-OCCUPIER — 2nd location","Owner-Occupier","owns the building · 2nd location","warm"),
 ("SOLO — owner not yet confirmed","Solo, Unconfirmed","confirm on Sunbiz","early"),
 ("NEW GRAD — watch","New Grad","revisit in 3 yr","hold"),
 ("WINDING DOWN","Winding Down","courtesy only","hold"),
 ("🏛 INSTITUTIONAL — watch the physicians","Institutional","watch the doctors, not the building","hold"),
 ("UNCLASSIFIED","Unclassified","needs a pass","hold"),
]

# The thirteen router columns the board renders. The sheet carries four more
# (SUNBIZ entities, Lic Yrs' neighbours, License) which pass through source_row
# and the export untouched; the board has never read them.
ROUTER_COLS = ["SEGMENT", "THE PLAY", "Owns?", "Name", "Profession", "Lic Yrs",
               "Age Band", "# at Address", "Practice Address", "City", "County",
               "Email", "Phone"]


def router_rows_from_file(path):
    """The historical read: one dict per sheet row, keyed by header name."""
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb["Lead Router"]
    c = header_map(ws, ROUTER_COLS, f"{os.path.basename(path)}[Lead Router]")
    out = [{h: r[c[h]] for h in ROUTER_COLS} for r in data_rows(ws)]
    wb.close()
    return out


def load_router(rows):
    """rows -> (board leads, segment plays). Mode-agnostic ON PURPOSE.

    A records-mode row is the same dict, restored from the `source_row` jsonb the
    importer wrote verbatim. Sharing this function is what makes parity a property
    of the code rather than a coincidence that has to be re-tested by eye.
    """
    leads=[]; segPlay={}
    for r in rows:
        if not r["Name"]: continue
        if r["SEGMENT"] and r["SEGMENT"] not in segPlay: segPlay[r["SEGMENT"]] = r["THE PLAY"] or ""
        leads.append({"s":r["SEGMENT"],"n":str(r["Name"]).title() if r["Name"] else "","pr":r["Profession"] or "",
            "ly":(round(float(r["Lic Yrs"]),0) if isinstance(r["Lic Yrs"],(int,float)) else ""),
            "ab":r["Age Band"] or "","na":r["# at Address"] or "","ad":r["Practice Address"] or "",
            "ci":(str(r["City"]).title() if r["City"] else ""),"co":(str(r["County"]).title() if r["County"] else ""),
            "e":r["Email"] or "","ph":r["Phone"] or "","own":r["Owns?"] or ""})
    return leads, segPlay

REGISTRY_NAMED = ["Lead ID", "Owner", "Stage", "Segment", "Contact Name", "Practice",
                  "Specialty", "City/Market", "County", "Email", "Phone", "Next Action",
                  "Next Action Date", "Last Touch", "Detail File"]
# Columns 23 and 25 sit BEYOND the sheet's header row, so the file read has always
# taken them positionally. They are NOT anonymous in the record: v_export_leads
# names them, and exporters/targets.py:REGISTRY_COLS writes them at exactly those
# two indices. Naming them here is what lets one transform serve both modes.
REGISTRY_POS = {23: "Est-Lease-Event", 25: "Event-Confidence"}


def registry_rows_from_file(path):
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb["Registry"]
    c = header_map(ws, REGISTRY_NAMED, "lead-registry.xlsx[Registry]")
    out = []
    for r in data_rows(ws):
        d = {h: r[c[h]] for h in REGISTRY_NAMED}
        for i, nm in REGISTRY_POS.items():
            d[nm] = r[i] if len(r) > i else ""
        out.append(d)
    wb.close()
    return out


def load_registry(rows):
    # Returns EVERY row with its owner attached (2026-07-27). It used to filter to
    # Owner=="Joe" right here, which meant 37 rows with a BLANK owner were dropped on
    # the floor: they appear on nobody's board, Joe's or Dell's. Splitting happens at
    # the call site now, so the unowned ones can be surfaced instead of vanishing.
    out=[]
    for r in rows:
        if not r["Lead ID"]: continue
        out.append({"owner":(r["Owner"] or "").strip(),
            "id":r["Lead ID"],"stage":r["Stage"],"seg":r["Segment"],"name":r["Contact Name"],
            "practice":r["Practice"],"spec":r["Specialty"],
            "city":r["City/Market"],"county":r["County"],"email":r["Email"] or "","phone":r["Phone"] or "",
            "next":str(r["Next Action"] or ""),"nextdate":norm(r["Next Action Date"]),"lasttouch":norm(r["Last Touch"]),
            "detail":r["Detail File"] or "",
            "lease":norm(r["Est-Lease-Event"]),"leaseconf":r["Event-Confidence"] or ""})
    return out

# ── where the raw rows come from ─────────────────────────────────────────────
# Five lanes append board rows; each is a pool `source` slug in records mode and
# a JSON file in file mode. Three other files are NOT lane row sources and are
# read from disk in BOTH modes, deliberately:
#   dso-matches.json      an OVERLAY that re-segments router rows already on the
#                         board — it adds nobody, so it is not a pool source.
#   lead-board-hot.json   ranked REGISTRY leads (L-### ids), not prospects.
#   lead-board-decisions.json  Joe's curated decision queue, a human artefact.
LANE_FILES = {
    "corp-filings":      os.path.join(AUTO, "entity-formation-leads.json"),
    "upstream":          os.path.join(AUTO, "pre-entity-watch.json"),
    "renewal-radar":     os.path.join(AUTO, "renewal-radar.json"),
    "relocating-owner":  os.path.join(AUTO, "relocating-owner-leads.json"),
    "national-accounts": os.path.join(ROOT, "DNA", "Team", "national-accounts.json"),
}

if MODE == MODE_RECORDS:
    POOL = load_pool((ROUTER_SOURCE,) + LANE_SOURCES)
    router_raw = POOL[ROUTER_SOURCE]
    # The pool is the canonical source.  Do not require an exported router merely
    # to recover the source filename: a missing recovery artifact must not turn a
    # live record-layer build into a file-mode build.
    router_path = None
    router_label = "candidate_pool (source='lead-router')"
else:
    POOL = None
    # Explicit recovery path for machines without a record-layer read grant.
    router_path = latest_router()
    router_label = os.path.basename(router_path)
    router_raw = router_rows_from_file(router_path)


def lane(slug):
    """The lane's raw objects, in the lane's own order, from records or from disk."""
    if MODE == MODE_RECORDS:
        return POOL.get(slug, [])
    p = LANE_FILES[slug]
    return json.load(open(p)) if os.path.exists(p) else []


L, segPlay = load_router(router_raw)

# DSO / corporate-associate re-segmentation (2026-07-27, Joe: "seems highly unlikely
# that there is only one"). The board used to derive its DSO count from the router's
# SEGMENT string alone, which labelled a provider a DSO associate ONLY when their email
# carried a corporate domain — true of 3 rows in 9,320, so the board showed exactly ONE.
# dso-match.py now identifies them by PRACTICE ADDRESS against the corporate-locations
# file, and this is the wiring that was never built: nothing read dso-matches.json.
# These are router rows being RE-SEGMENTED, not appended, so the total lead count is
# unchanged and nobody is double-counted. Only class == "dso-associate" moves; the
# owner-at-corporate-address REVIEW rows deliberately keep their existing segment,
# because calling a Sunbiz-confirmed owner an associate inverts the lead.
dso_path = os.path.join(AUTO, "dso-matches.json")
dso_n = 0
if os.path.exists(dso_path):
    DSO_SEG = "⭐ DSO ASSOCIATE — corporate clinic address"
    # The old label said "(email domain proves it)". That is no longer how these are
    # found, and it was the sentence that hid the bug — it read as a proof when it was
    # a filter that caught 3 rows in 9,320. Any router row still carrying the old
    # string is remapped so the rename does not orphan it out of SEG_ORDER.
    OLD_DSO_SEG = "⭐ DSO ASSOCIATE (email domain proves it)"
    for lead in L:
        if lead["s"] == OLD_DSO_SEG: lead["s"] = DSO_SEG
    if OLD_DSO_SEG in segPlay: segPlay[DSO_SEG] = segPlay.pop(OLD_DSO_SEG)
    def _k(n, a):
        return (re.sub(r"[^A-Z0-9]", "", str(n or "").upper()),
                re.sub(r"[^A-Z0-9]", "", str(a or "").upper()))
    dso_by = {}
    for x in json.load(open(dso_path)):
        if x.get("class") != "dso-associate": continue
        dso_by[_k(x.get("name"), x.get("addr"))] = x
    for lead in L:
        m = dso_by.get(_k(lead["n"], lead["ad"]))
        if not m: continue
        lead["s"] = DSO_SEG
        lead["conf"] = m.get("confidence", "")
        lead["brand"] = m.get("brand", "")
        dso_n += 1
    if dso_n:
        segPlay[DSO_SEG] = ("Providers whose PRACTICE ADDRESS matches a known corporate/DSO clinic, so "
                            "they are working for the group rather than owning their own shop. Nurture "
                            "now: the going-independent conversation is the opening. CONFIDENCE: HIGH = "
                            "corporate email domain corroborates the address; MEDIUM = address match at a "
                            "corporate or PE-backed brand; LOW = the brand is a physician-owned MSO or "
                            "partner-equity group, where employment is likelier than ownership but not "
                            "proven — verify before working a LOW. Source: dso-matches.json.")
# Entity-formation feed (the corp-filings lane output: new territory business filings,
# FL Sunbiz + AL SOS — the leads the licensure-based router structurally misses).
ef_n = 0
NEW_SEG = "\U0001F195 NEW ENTITY — corp-filings"
for x in lane("corp-filings"):
    if x.get("in_territory") is False:
        continue  # out-of-territory rows are logged in the file but not shown on the board
    L.append({"s":NEW_SEG,"n":x.get("n",""),"pr":x.get("pr",""),"ly":"","ab":"","na":"",
              "ad":x.get("ad",""),"ci":x.get("ci",""),"co":x.get("co",""),
              "e":x.get("e",""),"ph":x.get("ph",""),"own":x.get("own","YES")})
    ef_n += 1
segPlay["\U0001F195 NEW ENTITY — corp-filings"] = "New business entities filed in territory (FL Sunbiz + Alabama SOS). This is the entity-formation lane, the detector that catches relocators and Alabama leads the licensure list misses. Owner-confirmed by the filing itself."
# Pre-entity watch feed (Radar lane 4, the corroboration engine: corroborate.py output).
PE_SEG = "\U0001F52D PRE-ENTITY — upstream watch"
for x in lane("upstream"):
    if x.get("in_territory") is False: continue
    sens = " ⚠" if (x.get("sensitivity") or "").startswith("PERSONAL") else ""
    L.append({"s":PE_SEG,"n":x.get("n","")+sens,"pr":x.get("pr",""),"ly":"","ab":"","na":"",
              "ad":x.get("ad",""),"ci":x.get("ci",""),"co":x.get("co",""),
              "e":x.get("e",""),"ph":x.get("ph",""),"own":""})
segPlay["\U0001F52D PRE-ENTITY — upstream watch"] = "Corroborated upstream signals: two or more independent pre-entity signals (tip, deed, Medicare enrollment, license, address move) pointing at the same person BEFORE any entity files. The earliest the system can see. ⚠ rows are PERSONAL-adjacent: the detection is never the opener; every touch passes the how-did-you-know-to-call-me test."
# Renewal Radar feed (CoStar tenant renewal leads; doctrine DNA/Leads/renewal-radar-sop.md,
# built by build-renewal-feed.py from the latest DNA/Leads/renewal-radar-*.xlsx). Segment set
# by the feed (LEASE RENEWAL / OWNER-OCCUPIER / INSTITUTIONAL). Carries lease-event + tier fields.
for x in lane("renewal-radar"):
    L.append({"s":x.get("s",""),"n":x.get("n",""),"pr":x.get("pr",""),"ly":"","ab":"","na":"",
              "ad":x.get("ad",""),"ci":x.get("ci",""),"co":x.get("co",""),
              "e":x.get("e",""),"ph":x.get("ph",""),"own":"",
              "le":x.get("le",""),"tier":x.get("tier",""),"conf":x.get("conf",""),
              "ll":x.get("ll",""),"rep":x.get("rep",""),"flag":x.get("flag",""),"newll":x.get("newll","")})
segPlay["🔑 LEASE EVENT — decision window"] = "A lease coming up is NOT a renewal — it means the doctor has reached a real-estate DECISION point: renew, relocate and lease, relocate and buy, buy the space they are in, build ground-up, sale-leaseback, or exit. The job is to be in the room BEFORE they decide and represent whichever way they go. Expiry data is blank in this market, so every date is DERIVED (commencement / move-in / permit) and the confidence tag says how firm. Leverage is built 12-24 months out, not at the deadline. A blank tenant rep = unrepresented = the CARR pitch, pre-qualified. CoStar shows only part of the market; other MLS feeds append into this segment as they come online."
segPlay["🏢 OWNER-OCCUPIER — 2nd location"] = "The practice OWNS its building (CoStar Occupancy=Owned, tax-roll confirmed). Never a renewal lead, but a proven transactor with a balance sheet: a second location, expansion, sale-leaseback, eventual sale, and the best referral source alive. The biggest hole in the old model; never delete these."
segPlay["🏛 INSTITUTIONAL — watch the physicians"] = "Hospital-system, Navy, or county tenants. Do not pitch the building, watch the physicians inside it (D5 team-diff). Institutions are the farm system: where independents come from (Brown filed Serenity while employed at HCA Florida West)."

# Relocating-owner feed (tax-roll NAL owners x out-of-state DOH new-licensee join; built by
# parse-tax-rolls.py -> relocating-owner-candidates, deduped+scored into relocating-owner-leads.json).
# An out-of-state doctor licensed in FL in the last ~12 months who ALSO owns a territory parcel is
# very likely relocating here, knowable months before any entity files. Rows carry their own board
# schema (incl. conf/newll); each is scored so Joe qualifies on the board, none pre-filtered out.
ro_n = 0
for x in lane("relocating-owner"):
    L.append(x); ro_n += 1
segPlay["\U0001F9ED RELOCATING OWNER — moving to territory"] = "An out-of-state clinician licensed in FL in the last ~12 months who ALSO owns a territory parcel is almost certainly moving here — the earliest ownership-backed relocation signal the system has, months before any entity filing. Source: FL DOR tax-roll NAL owners joined to the out-of-state FL DOH new-licensee pool by name. HIGH = the new licensee's state matches the parcel owner's mailing state (act on these); MEDIUM = name match with differing states (a common name can be a different person — verify identity first); LOW = likely a namesake owning many blank-address/non-residential parcels. A name match is not a person match: corroborate the person and the intent before any touch, and never reference how you knew to call."

# National Accounts feed (curated, human-review — the all-client-types lane, added 2026-07-22, Joe).
# Multi-location / expanding healthcare orgs (groups, DSOs, franchise/emerging, regional systems,
# surgical/ASC + acute care). NOT auto-scored: a big expanding system the private-practice score
# would call a "false positive" belongs HERE. No facility type/size auto-excluded. Seed: AltaPointe.
NA_SEG = "🏥 NATIONAL ACCOUNT — multi-location / expanding"
na_n = 0
for x in lane("national-accounts"):
    summary = " \u00b7 ".join([t for t in [x.get("type",""), x.get("locations",""), x.get("status","")] if t])
    loc = ", ".join([t for t in [x.get("ci",""), x.get("co","")] if t])
    pathline = ("  \u00b7  warm path: " + x.get("path","")) if x.get("path") else ""
    L.append({"s":NA_SEG,"n":x.get("n",""),"pr":summary,"ly":"","ab":"","na":"",
              "ad":loc + pathline,"ci":x.get("ci",""),"co":x.get("co",""),
              "e":x.get("email",""),"ph":x.get("phone",""),"own":x.get("own","")})
    na_n += 1
segPlay[NA_SEG] = ("National Accounts: multi-location and expanding healthcare organizations \u2014 groups, DSOs, "
    "franchise/emerging operators, regional systems, and surgical/ASC + acute-care facilities. CARR represents "
    "these portfolio-wide across its agent network. This lane is CURATED and human-review, never auto-scored: a "
    "big expanding system the private-practice model would score 1/10 as a \u2018false positive\u2019 belongs HERE. "
    "Relationship-first \u2014 work the warm path to leadership, not a cold opener. No facility type or size is "
    "auto-excluded; the high-end boundary is Joe & Dell\u2019s judgment. Seed case: AltaPointe Health (Mobile, ~25 locations).")

if MODE == MODE_RECORDS:
    registry_all = load_registry(load_leads(ROOT, MODE_RECORDS))
else:
    registry_all = load_registry(registry_rows_from_file(os.path.join(LEADS_DIR, "lead-registry.xlsx")))
registry = [r for r in registry_all if r["owner"] == "Joe"]
# Owner is the routing key for the whole two-brain split, so a blank one means the
# lead is on NOBODY's board. Surfaced as its own group rather than silently dropped.
unowned = [r for r in registry_all if not r["owner"]]
decisions_path = os.path.join(AUTO, "lead-board-decisions.json")
DECISIONS = json.load(open(decisions_path)) if os.path.exists(decisions_path) else []
hot_path = os.path.join(AUTO, "lead-board-hot.json")
HOT = json.load(open(hot_path)) if os.path.exists(hot_path) else {"ranked":[],"proposed":[],"holds":[],"signals":[]}
hot_n = len(HOT.get("ranked",[])) + len(HOT.get("proposed",[]))

# ── Already-worked marking (2026-07-27) ────────────────────────────────────────
# 34 router rows matched a registry contact by name and carried NO mark, so the
# reservoir happily offered up people who are already an active deal — including
# Dell's. The detail panel has always had an "Already in system" field; nothing
# ever populated it for router rows. Name-only match on purpose: the registry
# holds a City/Market, not a street address, so address cannot be the join key.
# That makes this deliberately WIDE — a common name can collide. The mark
# therefore reads "check before calling", never "this is the same person", and
# it never removes anybody from the board (Joe qualifies, the system does not).
def _namekey(n):
    return re.sub(r"[^a-z]", "", str(n or "").lower())

reg_by_name: dict[str, dict[str, Any]] = {}
for r in registry_all:
    k = _namekey(r.get("name"))
    if k: reg_by_name.setdefault(k, r)

worked_n = 0
for lead in L:
    hit = reg_by_name.get(_namekey(lead["n"]))
    if not hit: continue
    who = hit.get("owner") or "unassigned"
    stage = hit.get("stage") or "in the registry"
    lead["flag"] = f"Already in the registry · {who} · {stage} — check before calling"
    lead["worked"] = 1
    worked_n += 1

segCount = Counter(x["s"] for x in L)
emailCov = sum(1 for x in L if x["e"] and "@" in str(x["e"]))
counties_n = len({str(x["co"]).strip() for x in L if str(x.get("co","")).strip()})
segmeta = [[m[0],m[1],m[2],m[3],segPlay.get(m[0],"")] for m in SEG_ORDER]
queue_n = len([r for r in registry if r["stage"] != "Nurture (Drip)"])

# ── the diagnostic payload ───────────────────────────────────────────────────
# Everything the template consumes, before HTML rendering. CARR_BOARD_PAYLOAD
# opts into writing this data for inspection; normal builds leave it unwritten.
_payload_to = os.environ.get("CARR_BOARD_PAYLOAD")
if _payload_to:
    json.dump({"leads": L, "registry": registry, "unowned": unowned,
               "segcount": dict(segCount), "segmeta": segmeta,
               "counties": counties_n, "emailcov": emailCov, "queue": queue_n,
               "worked_n": worked_n, "dso_n": dso_n, "ef_n": ef_n, "ro_n": ro_n,
               "na_n": na_n, "mode": MODE},
              open(_payload_to, "w"), sort_keys=True, default=str)
    print(f"payload -> {_payload_to}  (mode {MODE})")

# ── the shell ────────────────────────────────────────────────────────────────
# THE REPO COPY IS CANONICAL as of 2026-08-20. This file is hand-authored
# SOURCE, not a render: the generator reads it and writes lead-board.html from
# it. It lived only in the vault, tracked by git nowhere, so its only history
# was Drive's own versioning. That cost a real bug — until 2026-08-09 the
# board's date classifier hardcoded today as 2026-07-13, so four weeks of leads
# rendered as future work rather than overdue, and the fix that repaired it had
# no commit behind it. A source file outside version control cannot be
# reviewed, diffed, or restored from the repo if the vault copy is damaged.
#
# The vault path stays as a FALLBACK rather than being deleted, because the
# cloud-only copy of this script (see manifest.tsv) runs from the vault with no
# repo beside it and would otherwise stop building the board entirely. Repo
# first, vault second, and the vault keeps only the BUILT board once that
# cloud-only surface is retired on its own gate.
# Resolved from the REPO ROOT, not from this file's own directory, because the
# two twins live in different folders (generators/ and shared/) and must find
# the one shell. In the vault's cloud-only copies this resolves to a path that
# does not exist, which is exactly what makes the fallback below fire there.
_repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_repo_shell = os.path.join(_repo_root, "generators", "lead-board-template.html")
_vault_shell = os.path.join(AUTO, "lead-board-template.html")
_shell = _repo_shell if os.path.exists(_repo_shell) else _vault_shell
template = open(_shell, encoding="utf-8").read()
stamp = date.today().strftime("%B %-d, %Y") if hasattr(date.today(),'strftime') else "today"

html = template
html = html.replace("__STAMP__", stamp)
html = html.replace("__COUNTIES__", str(counties_n))
html = html.replace("__TOTAL__", f"{len(L):,}").replace("__EMAIL__", f"{emailCov:,}")
html = html.replace("__QUEUE__", str(queue_n)).replace("__DEC__", str(len(DECISIONS)))
# Freshness, honestly (2026-07-27). The header used to show only the BUILD date, so a
# board rebuilt today off a 15-day-old router read as current. Rule 28 says an
# automation is verified by output freshness; this puts that check on the surface the
# human actually reads, instead of leaving the source date alone in the footer.
m = re.search(r"(\d{4}-\d{2}-\d{2})", os.path.basename(router_path)) if router_path else None
if MODE == MODE_RECORDS:
    src_txt, src_state, src_note = "live", "fresh", "read from canonical candidate_pool"
elif m:
    src_d = datetime.strptime(m.group(1), "%Y-%m-%d").date()
    src_age = (date.today() - src_d).days
    src_txt = src_d.strftime("%B %-d")
    if   src_age <= 9:  src_state, src_note = "fresh", f"{src_age}d old"
    elif src_age <= 16: src_state, src_note = "aging", f"{src_age}d old · a weekly refresh is due"
    else:               src_state, src_note = "stale", f"{src_age}d old · OVERDUE, re-pull the router"
else:
    src_txt, src_state, src_note = "unknown", "stale", "the router filename carries no date"
html = html.replace("__SRCDATE__", src_txt).replace("__SRCSTATE__", src_state).replace("__SRCNOTE__", src_note)
html = html.replace("__UNOWNED_N__", str(len(unowned)))
html = html.replace("__UNOWNED__", json.dumps(unowned,separators=(",",":")))
html = html.replace("__WORKED_N__", str(worked_n))
html = html.replace("__LEADS__", json.dumps(L,separators=(",",":")))
html = html.replace("__REG__", json.dumps(registry,separators=(",",":")))
html = html.replace("__SEGMETA__", json.dumps(segmeta,separators=(",",":")))
html = html.replace("__SEGCOUNT__", json.dumps(dict(segCount),separators=(",",":")))
html = html.replace("__DECISIONS__", json.dumps(DECISIONS,separators=(",",":")))
html = html.replace("__HOTN__", str(hot_n))
html = html.replace("__HOT__", json.dumps(HOT,separators=(",",":")))

out_path = os.path.join(AUTO, "lead-board.html")
open(out_path,"w",encoding="utf-8").write(html)
print(f"Wrote {out_path}  ({os.path.getsize(out_path):,} bytes)")

# Shared-tier publish (added 2026-07-20): every rebuild also drops the built
# board into DNA/Team/live-boards/ for the cloud-only delivery path to re-persist.
# Derived view; overwrite is correct.
shared_path = os.path.join(ROOT, "DNA", "Team", "live-boards", "lead-board-latest.html")
publish_failed = None
try:
    os.makedirs(os.path.dirname(shared_path), exist_ok=True)
    open(shared_path,"w",encoding="utf-8").write(html)
    print(f"Published shared copy: {shared_path}")
except Exception as e:
    publish_failed = e
    print(f"WARNING: shared-tier publish failed ({e}) — cloud fallback will go stale; fix before session end.")
_src = ("records (candidate_pool + v_export_leads)" if MODE == MODE_RECORDS
        else "generated files")
print(f"Router: {router_label} | {len(L):,} leads | queue {queue_n} | "
      f"decisions {len(DECISIONS)} | source: {_src}")
if publish_failed:
    # Corrective #2 (2026-07-25): a failed shared publish must FAIL the run, not
    # whisper — exit 0 here let the heartbeat report success while the cloud fallback went stale.
    sys.exit(f"EXIT 1: board written, but the shared-tier publish failed ({publish_failed}).")
