#!/usr/bin/env python3
"""
date-founders.py — corevent earn-out dating (open-loops #78 item 4 / #79).
See Automation/radar/corevent-earnout-sop.md for the WHY. READ THAT FIRST.

Stage 0 (always): extract the 409 post-sale founders from the lead router and resolve
                  them against the current owner index. Reports the ~1% naive-join gap.
Stage 1-5 (when corevent.zip + cordata.zip are in the scratch): schema-probe, then run
                  the confirmed join and write corevent-earnout-<date>.json.

CONFIRMED SCHEMA (2026-07-16, first real run against the Jul 2026 quarterly files):
  Both files are FIXED-WIDTH text, not CSV.
  corevt.txt (663 wide): doc# [0:12] | filing-seq [12:17] | event code [17:27] |
      description [37:65] | event date MMDDYYYY [85:93] (some rows also carry a filing
      date at [77:85]; some old rows have no date) | free-text tail [93:] that, for the
      "WAS PART OF A MERGER" form, names the OTHER party's real doc#.
  cordata.txt (1441 wide): doc# [0:12] | name [12:204] | principal-addr city ~[304:327] |
      officer block = up to 6 slots of 128 chars from [668], each: title[0:4] type[4]
      (P=person / C=company) last[5:25] first[25:45].

DIRECTION FINDING (the trap the SOP warned about, one level deeper): the semantically
ideal "disappearing entity" code CORAPCMER ("MERGING INTO:") is DEAD after ~2000 — 0
records since 2010. ALL recent mergers are recorded on the SURVIVING entity as CORAPMER
("CORPORATION WAS A MERGER RESULT" / "WAS PART OF A MERGER. QUALIFIED CORPORATION WAS
<doc>"). So per event we collect the record's own doc# AND any real doc# referenced in
the tail, look up officers for BOTH sides in cordata, and match by PERSON NAME against
the 409 founders. An acquirer's officers are not in the 409, so taking both sides adds
coverage without adding false positives — the person-name match is the real filter.

Because merged entities are NOT purged from cordata (verified 2026-07-16: 99.9% of recent
merged doc#s still resolve there with officers), the join is feasible. A name match is
NOT a person match: every row is filtered to a corroborating signal (the merged entity
is a healthcare-type entity, OR it sits in a territory city) and stays confidence L until
a conversation narrows it. Never a fabricated single date — every row is a 2-5yr RANGE.

USAGE: python3 date-founders.py [CARR_ROOT] [SCRATCH_DIR] [--files]   (raws live in SCRATCH, OFF Drive)

SOURCE MODE (ORDER 29b, the ORDER 29a pattern). Stage 0's founder read defaults
to RECORDS — the router rows come off the pool (`v_export_pool`, the router
source alone) — and falls back to the generated router xlsx, LOUDLY on
stderr, when the pool is unreachable. --files forces the file read. Stages
1-5 (corevent/cordata) are untouched: they read only the scratch zips, never
the router.
"""
import sys, os, glob, json, zipfile, io, re
from datetime import date

# lib/record_sources.py lives two levels up from pipelines/radar/ in the repo;
# the vault copy of this script has no lib/ beside it (manifest.tsv), so the
# import is guarded exactly like every other repointed consumer.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
try:
    from lib.record_sources import (MODE_FILES, MODE_RECORDS, ROUTER_SOURCE, load_pool,
                                     pool_reach, resolve_mode, source_note)
    _HAVE_RECORDS = True
except ImportError:
    MODE_FILES, MODE_RECORDS, _HAVE_RECORDS = "files", "records", False

if _HAVE_RECORDS:
    MODE, _rest = resolve_mode(sys.argv[1:], default=MODE_RECORDS)
else:
    MODE = MODE_FILES
    _rest = [a for a in sys.argv[1:] if a not in ("--files", "--records")]
    if "--records" in sys.argv[1:]:
        print("[date-founders] this copy has no lib/record_sources.py — running file mode",
              file=sys.stderr)

ROOT = _rest[0] if len(_rest) > 0 else os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
SCRATCH = _rest[1] if len(_rest) > 1 else os.path.join(ROOT, "Automation", "radar", "_data")
LEADS_DIR = os.path.join(ROOT, "DNA", "Leads")
RADAR = os.path.join(ROOT, "Automation", "radar")

if MODE == MODE_RECORDS:
    _ok, _why, _, _ = pool_reach((ROUTER_SOURCE,))
    if not _ok:
        print(f"[date-founders] records mode unavailable ({_why}) — falling back to the "
              f"generated files", file=sys.stderr)
        MODE = MODE_FILES

TERRITORY_COUNTIES = {"ESCAMBIA","SANTA ROSA","OKALOOSA","WALTON","BAY","LEON",
                      "MOBILE","BALDWIN"}  # FL panhandle + the AL edge
EARNOUT_MIN_YEARS, EARNOUT_MAX_YEARS = 2, 5
MIN_MERGER_YEAR = 2019   # earn-outs from earlier mergers have largely run out

# Merger / ownership-change event codes (confirmed against the corevt schema probe).
MERGER_CODES = {"CORAPMER","CORAPCMER","CORAPMNEW","GENACEMER","GENACENEW",
                "CORMERNC","CORAPSHEX","CORLCIE","CORAPCCONS","CORAPCONS"}
# (CORLCABMER = "LC ABANDON MERGER" is deliberately excluded — an abandoned, non-consummated merger.)

# corevt fixed-width offsets
CE_DOC=(0,12); CE_CODE=(17,27); CE_DATE=(85,93); CE_DATE_ALT=(77,85); CE_TAIL=93
# cordata fixed-width offsets
CD_DOC=(0,12); CD_NAME=(12,204); CD_PCITY=(304,327)
CD_OFF_BASE=668; CD_OFF_W=128; CD_OFF_N=6

NAME_SUFFIXES = {"JR","SR","II","III","IV","MD","DO","DDS","DMD","PA","ARNP","DPM","OD",
                 "DC","PHD","ESQ","TRUSTEE","TR","LLC","INC","PLLC","PC"}
# Leading \b anchors to word start; NO trailing \b so medical STEMS match (UROLOG->UROLOGY,
# MEDIC->MEDICAL). A trailing \b silently breaks every prefix and matches nothing (the bug that
# first dropped Advanced Urology). Only distinctive stems / multiword phrases — generic words like
# FAMILY / VISION / PLASTIC / OB are qualified to avoid non-medical false positives.
HEALTH_RE = re.compile(r'\b(MEDIC|HEALTH|CLINIC|DENTAL|DENTIST|ORTHODON|ORTHOPED|UROLOG|DERMATOL|'
    r'SURGER|SURGIC|SURGEON|PHYSICIAN|PEDIATR|CARDIOL|ONCOLOG|OPHTHALM|OPTOM|VETERIN|CHIROPRAC|'
    r'THERAP|REHAB|WELLNESS|PATHOLOG|RADIOLOG|ANESTHES|PSYCHIATR|NEUROLOG|NEUROSURG|HOSPITAL|'
    r'PHARMAC|IMAGING|ENDODON|PERIODON|PODIATR|GASTRO|NEPHROL|GYNECOL|OBSTETR|MEDSPA|MED SPA|'
    r'URGENT CARE|PRIMARY CARE|FAMILY MEDICINE|FAMILY PRACTICE|INTERNAL MEDICINE|ANIMAL HOSPITAL|'
    r'ANIMAL CLINIC|EYE CARE|EYE CENTER|VISION CENTER)')
TERRITORY_CITIES = {"PENSACOLA","MILTON","PACE","GULF BREEZE","CANTONMENT","NAVARRE","JAY",
    "CENTURY","TALLAHASSEE","PANAMA CITY","LYNN HAVEN","DESTIN","FORT WALTON BEACH","CRESTVIEW",
    "NICEVILLE","SANTA ROSA BEACH","MARY ESTHER","SHALIMAR","FREEPORT","DEFUNIAK SPRINGS",
    "MOBILE","DAPHNE","FAIRHOPE","SPANISH FORT","GULF SHORES","FOLEY","BONIFAY","CHIPLEY"}
DOCREF_RE = re.compile(r'\b([A-Z]\d{11}|[A-Z]\d{5}|\d{6})\b')

def norm_name(s):
    s = re.sub(r"[^A-Z ]", "", str(s).upper()).strip()
    p = s.split()
    return None if len(p) < 2 else p[-1] + "|" + p[0]

def nkey(last, first):
    """Consistent LAST|FIRST key for both founder names and cordata officers."""
    last = "".join(ch for ch in last.upper() if ch.isalpha())
    fw = re.findall(r"[A-Z]+", first.upper())
    first = fw[0] if fw else ""
    return (last + "|" + first) if last and first else None

def founder_key(name):
    toks = [t for t in re.sub(r"[^A-Za-z ]", " ", str(name)).upper().split()
            if t not in NAME_SUFFIXES and len(t) > 1]
    return nkey(toks[-1], toks[0]) if len(toks) >= 2 else None

def latest(pattern):
    c = sorted(glob.glob(pattern))
    return c[-1] if c else None

def _valid_date(d):
    return (len(d) == 8 and d.isdigit() and "01" <= d[0:2] <= "12"
            and "01" <= d[2:4] <= "31" and "1990" <= d[4:8] <= "2026")

def _merger_date(line):
    d = line[CE_DATE[0]:CE_DATE[1]]
    if _valid_date(d): return d
    d = line[CE_DATE_ALT[0]:CE_DATE_ALT[1]]
    return d if _valid_date(d) else None

# ---------- Stage 0: the founders + the visible gap ----------
ROUTER_COLS = ["SEGMENT", "Name", "Profession", "City", "County"]

def router_rows_from_file(path):
    import openpyxl
    # Schema-validated (orchestrator-lane corrective #1, 2026-07-25): headers by name.
    _d = os.path.dirname(os.path.abspath(__file__))
    for _c in (os.path.join(_d, "..", "..", "lib"), _d):
        if os.path.isfile(os.path.join(_c, "sheets.py")):
            sys.path.insert(0, _c); break
    from sheets import header_map, data_rows
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb["Lead Router"]
    c = header_map(ws, ROUTER_COLS, f"{os.path.basename(path)}[Lead Router]")
    out = [{h: r[c[h]] for h in ROUTER_COLS} for r in data_rows(ws)]
    wb.close()
    return out

def load_founders():
    if MODE == MODE_RECORDS:
        rows = load_pool((ROUTER_SOURCE,))[ROUTER_SOURCE]
        label = source_note(MODE)
    else:
        path = latest(os.path.join(LEADS_DIR, "lead-router-*.xlsx"))
        if not path: raise SystemExit("No lead-router-*.xlsx found.")
        rows = router_rows_from_file(path)
        label = os.path.basename(path)
    print(f"[date-founders] source: {label}", file=sys.stderr)
    f = []
    for r in rows:
        if r["SEGMENT"] and "POST-SALE FOUNDER" in str(r["SEGMENT"]):
            f.append({"name": r["Name"], "prof": r["Profession"], "city": r["City"], "county": r["County"]})
    return f

def stage0(founders):
    idx_path = latest(os.path.join(RADAR, "sunbiz-owner-index-*.json"))
    idx = json.load(open(idx_path)) if idx_path else {}
    hits = 0
    for x in founders:
        k = norm_name(x["name"])
        x["ownerindex_hit"] = bool(k and k in idx)
        if x["ownerindex_hit"]: hits += 1
    print(f"[stage 0] {len(founders)} post-sale founders")
    print(f"[stage 0] resolve to a CURRENT owner-index entity: {hits}/{len(founders)}")
    print(f"[stage 0] => the founder->current-entity path is closed by the segment definition;")
    print(f"[stage 0]    the real join runs corevent -> cordata officers -> founder (see the SOP).")

# ---------- schema probe ----------
def probe_zip(path, label):
    if not os.path.exists(path):
        print(f"[probe] {label}: NOT FOUND at {path}")
        return None
    z = zipfile.ZipFile(path)
    inner = [n for n in z.namelist() if not n.endswith("/")]
    print(f"[probe] {label}: {os.path.basename(path)} contains {inner[:5]}")
    with z.open(inner[0]) as fh:
        head = fh.read(4000).decode("latin-1", "replace")
    lines = head.splitlines()
    print(f"[probe] {label}: first line ({len(lines[0]) if lines else 0} chars):")
    print("        " + (lines[0][:180] if lines else "<empty>"))
    print(f"[probe] {label}: CONFIRM the field offsets/columns above before trusting output.")
    return z

# ---------- Stage 1-5: the confirmed join ----------
def dated_join(founders):
    corevent = os.path.join(SCRATCH, "corevent.zip")
    cordata  = os.path.join(SCRATCH, "cordata.zip")
    ze = probe_zip(corevent, "corevent")
    zc = probe_zip(cordata, "cordata")
    if ze is None or zc is None:
        print("\n[gated] corevent.zip and/or cordata.zip not in the scratch folder. This stage is")
        print(f"        gated on Joe's browser pull. Drop the files in: {SCRATCH}")
        return None

    fidx = {}
    for f in founders:
        k = founder_key(f["name"])
        if k: fidx.setdefault(k, []).append(f)
    print(f"[join] {len(fidx)} distinct founder name-keys")

    # ---- Pass 1: corevt -> {merged doc#: most-recent merger date}, both sides ----
    inner = [n for n in ze.namelist() if not n.endswith("/")][0]
    doc_date = {}
    n = 0
    with ze.open(inner) as fh:
        for raw in fh:
            n += 1
            line = raw.decode("latin-1")
            if line[CE_CODE[0]:CE_CODE[1]].strip() in MERGER_CODES:
                d = _merger_date(line)
                if not d or int(d[4:8]) < MIN_MERGER_YEAR:
                    continue
                iso = d[4:8] + d[0:2] + d[2:4]
                docs = [line[CE_DOC[0]:CE_DOC[1]].strip()] + DOCREF_RE.findall(line[CE_TAIL:])
                for dc in docs:
                    dc = dc.strip()
                    if dc and iso > doc_date.get(dc, ""):
                        doc_date[dc] = iso
    print(f"[join] scanned {n:,} corevt events -> {len(doc_date):,} merged doc#s since {MIN_MERGER_YEAR}")

    # ---- Pass 2: cordata -> officers of merged docs -> match founders (corroborated) ----
    rows, rejected, seen = [], 0, set()
    for m in [x for x in zc.namelist() if x.lower().endswith(".txt")]:
        with zc.open(m) as fh:
            for raw in fh:
                line = raw.decode("latin-1")
                iso = doc_date.get(line[CD_DOC[0]:CD_DOC[1]].strip())
                if not iso:
                    continue
                doc  = line[CD_DOC[0]:CD_DOC[1]].strip()
                ent  = line[CD_NAME[0]:CD_NAME[1]].strip()
                city = line[CD_PCITY[0]:CD_PCITY[1]].strip()
                health = bool(HEALTH_RE.search(ent.upper()))
                local  = city.upper() in TERRITORY_CITIES
                for i in range(CD_OFF_N):
                    b = CD_OFF_BASE + i * CD_OFF_W
                    slot = line[b:b + CD_OFF_W]
                    if len(slot) < 45 or slot[4:5] != "P":
                        continue
                    k = nkey(slot[5:25].strip(), slot[25:45].strip())
                    if not k or k not in fidx:
                        continue
                    for f in fidx[k]:
                        if (k, doc) in seen:
                            continue
                        # a name match is not a person match: require a corroborating signal
                        if not (health or local):
                            rejected += 1
                            seen.add((k, doc))
                            continue
                        seen.add((k, doc))
                        yr = int(iso[0:4])
                        conf = ("L — healthcare-type entity name + name match"
                                if health else
                                "L — entity in a territory city + name match (entity not obviously a practice)")
                        rows.append({
                            "name": f["name"],
                            "profession": f.get("prof"),
                            "founder_city": f.get("city"),
                            "founder_county": f.get("county"),
                            "matched_entity": ent,
                            "entity_principal_city": city,
                            "entity_is_healthcare_name": health,
                            "doc": doc,
                            "merger_date": f"{iso[4:6]}/{iso[6:8]}/{iso[0:4]}",
                            "earnout_start": str(yr + EARNOUT_MIN_YEARS),
                            "earnout_end": str(yr + EARNOUT_MAX_YEARS),
                            "confidence": conf,
                            "note": ("Name match only; corroborate this is the founder's OWN sold "
                                     "practice (not a namesake) before writing a clock to the registry. "
                                     "Earn-out length is a 2-5yr market assumption, never a known term."),
                        })
    rows.sort(key=lambda r: (not r["entity_is_healthcare_name"], r["earnout_start"]))
    print(f"[join] corroborated founder x merged-officer matches: {len(rows)}")
    print(f"[join] name matches rejected as un-corroborated collisions: {rejected}")
    return rows

def main():
    founders = load_founders()
    stage0(founders)
    rows = dated_join(founders)
    if rows is None:
        print("\n[done] Stage 0 only. Dated join awaits the pull + a confirmed schema.")
        return
    out = os.path.join(RADAR, f"corevent-earnout-{date.today().isoformat()}.json")
    json.dump(rows, open(out, "w"), indent=1)
    print(f"\n[done] {len(rows)} corroborated earn-out matches -> {out}")
    print("       Propose these as registry clock updates; nothing self-enters. DELETE the raw zips now.")

if __name__ == "__main__":
    main()
