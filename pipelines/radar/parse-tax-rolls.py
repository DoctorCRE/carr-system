#!/usr/bin/env python3
"""
parse-tax-rolls.py — Florida DOR tax-roll parser (open-loop #75).

RUN THIS LOCALLY on Joe's Mac against RAW NAL + SDF county files kept in an
OFF-DRIVE scratch folder. The raw statewide rolls hold millions of people's
records: they NEVER go in the Google-Drive-synced CARR repo. This script reads
them from the scratch, writes only small DERIVED outputs into the repo, and you
delete the raws the same session (the discard rule). Read the WHY in
Automation/radar/tax-roll-sop.md.

WHAT IT PRODUCES (three payoffs of open-loop #75):
  (a) relocating-owner candidates  -> Automation/radar/relocating-owner-candidates-<date>.json
        A doctor licensed in FL in the last 12 months, whose license mailing
        address is OUT OF STATE, who ALSO owns a parcel in a territory county,
        is very likely moving here — knowable months before any entity filing.
        This is the NAL-owner x DOH-licensee join.
  (b) medical sale comps            -> DNA/Deal Management/territory-medical-sale-comps-<date>.xlsx
        Every SDF sale on a medical-use parcel, back as far as the SDF goes,
        with price, date, qualification flag and $/SF. Feeds the comps book.
  (c) medical property + owner table -> DNA/Deal Management/territory-medical-properties-<date>.xlsx
        Payoff (c), own-vs-lease, is ALREADY built in the curated
        territory-medical-properties.xlsx (all 6 counties). This script does NOT
        overwrite that file; it writes a DATED refresh you can diff and merge.

RAW FILES: FL DOR "Tax Roll Data Files" — comma-delimited CSV WITH a header row.
  NAL = Name-Address-Legal: one row per parcel (owner, mailing addr, physical
        addr, DOR use code, values, building SF, last 2 sales).
  SDF = Sale Data File: one row per sale, 2009->present (price, year, month,
        qualification code).
  Download (browser): floridarevenue.com -> Property Tax -> Data Portal ->
  Tax Roll Data Files (public login, same as Sunbiz). Start with Escambia +
  Santa Rosa. Drop the .csv files into the scratch folder below.

USAGE:
  python3 parse-tax-rolls.py [CARR_ROOT] [SCRATCH_DIR]
    CARR_ROOT   default: repo root inferred from this file's location
    SCRATCH_DIR default: ~/Claude/Projects/CARR/_taxroll-scratch   (raws live here, OFF Drive)

The script SCHEMA-PROBES every file first (prints the real header + 2 sample
rows) and STOPS if required fields are missing rather than guessing a layout —
the same discipline the CoStar walk taught (the field you build on may be blank).
Confirm the columns and the DOR use codes against the probe before trusting output.
"""

import sys, os, csv, json, glob, re, datetime

# ---- configuration ---------------------------------------------------------

# DOR use codes that count as "medical" — matches the two buckets already in the
# curated territory-medical-properties.xlsx. Extend here if a pull shows medical
# parcels hiding under office codes (17/18) in this market.
MEDICAL_USE_CODES = {
    "19": "Professional/Medical office",   # professional service buildings (medical/dental)
    "73": "Hospital (private)",            # privately owned hospitals
    # "74": "Homes for the aged / ALF",   # <- uncomment if you want assisted living
}

# FL DOR two-digit county numbers (CO_NO) -> name. Territory ones are what matter;
# the rest are here so a stray file still labels correctly. VERIFY against the
# probe on first run (the probe prints CO_NO); fix any that look wrong.
DOR_COUNTY = {
    "11":"Alachua","12":"Baker","13":"Bay","14":"Bradford","15":"Brevard","16":"Broward",
    "17":"Calhoun","18":"Charlotte","19":"Citrus","20":"Clay","21":"Collier","22":"Columbia",
    "23":"Miami-Dade","24":"DeSoto","25":"Dixie","26":"Duval","27":"Escambia","28":"Flagler",
    "29":"Franklin","30":"Gadsden","31":"Gilchrist","32":"Glades","33":"Gulf","34":"Hamilton",
    "35":"Hardee","36":"Hendry","37":"Hernando","38":"Highlands","39":"Hillsborough","40":"Holmes",
    "41":"Indian River","42":"Jackson","43":"Jefferson","44":"Lafayette","45":"Lake","46":"Lee",
    "47":"Leon","48":"Levy","49":"Liberty","50":"Madison","51":"Manatee","52":"Marion","53":"Martin",
    "54":"Monroe","55":"Nassau","56":"Okaloosa","57":"Okeechobee","58":"Orange","59":"Osceola",
    "60":"Palm Beach","61":"Pasco","62":"Pinellas","63":"Polk","64":"Putnam","65":"St. Johns",
    "66":"St. Lucie","67":"Santa Rosa","68":"Sarasota","69":"Seminole","70":"Sumter","71":"Suwannee",
    "72":"Taylor","73":"Union","74":"Volusia","75":"Wakulla","76":"Walton","77":"Washington",
}

# SDF qualification codes treated as arm's-length / usable comps. FL DOR: "01" is
# the qualified sale. Everything else (quitclaims, family transfers, corrective
# deeds, multi-parcel, etc.) is KEPT but flagged qualified=False so nothing is
# lost and the comps step can decide.
QUALIFIED_SALE_CODES = {"01"}

# Field name resolution: canonical -> ordered list of accepted header names
# (matched case-insensitively, exact first then "contains"). FL DOR renames
# rarely, but this keeps the parser from breaking on a year's tweak.
NAL_FIELDS = {
    "co_no":     ["CO_NO"],
    "parcel":    ["PARCEL_ID", "PARCELID", "PARCEL"],
    "use":       ["DOR_UC", "DORUC"],
    "own_name":  ["OWN_NAME", "OWNER_NAME", "OWNNAME"],
    "own_addr":  ["OWN_ADDR1", "OWNER_ADDR1", "OWN_ADDR"],
    "own_city":  ["OWN_CITY"],
    "own_state": ["OWN_STATE"],
    "own_zip":   ["OWN_ZIPCD", "OWN_ZIP"],
    "phy_addr":  ["PHY_ADDR1", "SITE_ADDR", "PHY_ADDR"],
    "phy_city":  ["PHY_CITY"],
    "phy_zip":   ["PHY_ZIPCD", "PHY_ZIP"],
    "bldg_sf":   ["TOT_LVG_AREA", "TOT_LVG_AR"],
    "land_sf":   ["LND_SQFOOT", "LND_SQFT"],
    "yr_blt":    ["ACT_YR_BLT", "EFF_YR_BLT"],
    "jv":        ["JV", "JUST_VALUE"],
    "sale_prc1": ["SALE_PRC1"], "sale_yr1": ["SALE_YR1"],
    "sale_prc2": ["SALE_PRC2"], "sale_yr2": ["SALE_YR2"],
}
SDF_FIELDS = {
    "co_no":    ["CO_NO"],
    "parcel":   ["PARCEL_ID", "PARCELID", "PARCEL"],
    "sale_prc": ["SALE_PRC", "SALE_PRICE"],
    "sale_yr":  ["SALE_YR"],
    "sale_mo":  ["SALE_MO"],
    "qual_cd":  ["QUAL_CD", "QUALCD"],
    "vi_cd":    ["VI_CD"],
}

# ---- helpers ---------------------------------------------------------------

NAME_SUFFIXES = {"JR", "SR", "II", "III", "IV", "MD", "DO", "DDS", "DMD", "PA",
                 "ARNP", "DPM", "OD", "DC", "PHD", "ESQ", "TRUSTEE", "TR"}
BUSINESS_TOKENS = {"LLC","INC","PA","PLLC","CORP","LP","LLP","LTD","TRUST","FOUNDATION",
                   "ASSOCIATES","ASSOC","GROUP","HOLDINGS","PROPERTIES","REALTY","REIT",
                   "COMPANY","CO","PARTNERS","ENTERPRISES","INVESTMENTS","MANAGEMENT"}

def norm_tokens(name):
    """Order-insensitive person-name key. 'BROWN SAMANTHA J' -> ('BROWN','SAMANTHA')."""
    if not name:
        return None
    toks = re.sub(r"[^A-Za-z ]", " ", name.upper()).split()
    toks = [t for t in toks if t not in NAME_SUFFIXES and len(t) > 1]
    return tuple(sorted(toks)) if toks else None

def looks_like_business(name):
    up = (name or "").upper()
    return any(re.search(r"\b" + re.escape(t) + r"\b", up) for t in BUSINESS_TOKENS)

def norm_addr(a):
    return re.sub(r"\s+", " ", re.sub(r"[^A-Za-z0-9 ]", " ", (a or "").upper())).strip()

def to_int(x):
    try:
        return int(float(str(x).replace(",", "").strip()))
    except Exception:
        return 0

def resolve(fieldnames, wanted):
    """Map canonical keys -> actual header names present in the file."""
    upper = {fn.upper().strip(): fn for fn in (fieldnames or [])}
    out, missing = {}, []
    for canon, cands in wanted.items():
        hit = None
        for c in cands:
            if c.upper() in upper:
                hit = upper[c.upper()]; break
        if not hit:  # loose contains-match fallback
            for up, orig in upper.items():
                if any(c.upper() in up for c in cands):
                    hit = orig; break
        if hit:
            out[canon] = hit
        else:
            missing.append(canon)
    return out, missing

def probe(path, wanted, label, required):
    """Print header + 2 sample rows; return (resolver, fieldnames) or stop if required fields absent."""
    with open(path, newline="", encoding="latin-1") as f:
        rdr = csv.reader(f)
        header = next(rdr, [])
        s1 = next(rdr, []); s2 = next(rdr, [])
    print(f"\n[probe] {label}: {os.path.basename(path)}")
    print(f"        {len(header)} columns. First 18: {header[:18]}")
    res, missing = resolve(header, wanted)
    print(f"        resolved -> " + ", ".join(f"{k}={v}" for k, v in res.items()))
    hard = [m for m in required if m in missing]
    if hard:
        print(f"        !! MISSING REQUIRED FIELDS {hard} — layout not recognized. "
              f"Fix the *_FIELDS map against the header above, then rerun. Skipping this file.")
        return None, header
    if missing:
        print(f"        (optional fields not found, ok: {missing})")
    return res, header

def find_files(scratch, token):
    hits = []
    for pat in (f"*{token}*.csv", f"*{token.lower()}*.csv", f"*{token}*.CSV", f"*{token}*.txt"):
        hits += glob.glob(os.path.join(scratch, "**", pat), recursive=True)
    return sorted(set(hits))

# ---- main ------------------------------------------------------------------

def main():
    here = os.path.dirname(os.path.abspath(__file__))
    ROOT = sys.argv[1] if len(sys.argv) > 1 else os.path.abspath(os.path.join(here, "..", ".."))
    SCRATCH = sys.argv[2] if len(sys.argv) > 2 else os.path.expanduser("~/Claude/Projects/CARR/_taxroll-scratch")
    today = datetime.date.today().isoformat()
    RADAR = os.path.join(ROOT, "Automation", "radar")
    DEALS = os.path.join(ROOT, "DNA", "Deal Management")

    print(f"CARR_ROOT = {ROOT}")
    print(f"SCRATCH   = {SCRATCH}  (raw NAL/SDF live here, OFF Drive — never copied into the repo)")
    if not os.path.isdir(SCRATCH):
        raise SystemExit(f"\nScratch folder not found. Create it and drop the NAL/SDF .csv files there:\n  {SCRATCH}")

    try:
        import openpyxl
    except ImportError:
        raise SystemExit("Need openpyxl:  pip3 install openpyxl")

    # licensee pool (out-of-state only) keyed by normalized name -> records
    pool_path = os.path.join(RADAR, "upstream", "licenses-pool.json")
    lic_by_name, n_oos = {}, 0
    if os.path.exists(pool_path):
        for rec in json.load(open(pool_path, encoding="utf-8")):
            st = (rec.get("state") or "").upper().strip()
            if st and st != "FL":
                k = norm_tokens(rec.get("name"))
                if k:
                    lic_by_name.setdefault(k, []).append(rec); n_oos += 1
        print(f"\nLoaded {n_oos} out-of-state licensees ({len(lic_by_name)} distinct names) for the owner join.")
    else:
        print(f"\n[warn] licensee pool not found at {pool_path} — skipping payoff (a) join.")

    nal_files = find_files(SCRATCH, "NAL")
    sdf_files = find_files(SCRATCH, "SDF")
    print(f"\nNAL files: {[os.path.basename(f) for f in nal_files] or 'NONE'}")
    print(f"SDF files: {[os.path.basename(f) for f in sdf_files] or 'NONE'}")
    if not nal_files and not sdf_files:
        raise SystemExit("No NAL or SDF .csv files in the scratch folder. Download them first (see the header).")

    medical_props = []        # payoff (c) refresh rows
    medical_parcels = set()   # (co_no, parcel) with a medical use — for the SDF filter
    candidates = []           # payoff (a) relocating-owner matches

    # ---- NAL pass ----
    for path in nal_files:
        res, _ = probe(path, NAL_FIELDS, "NAL", required=["parcel", "use", "own_name"])
        if not res:
            continue
        g = lambda row, k: (row.get(res[k], "") if k in res else "").strip()
        n_rows = n_med = n_cand = 0
        with open(path, newline="", encoding="latin-1") as f:
            for row in csv.DictReader(f):
                n_rows += 1
                use = g(row, "use").lstrip("0").zfill(2) if g(row, "use") else ""
                use_key = g(row, "use").strip().lstrip("0") or "0"
                use_norm = use_key.zfill(2)
                co = g(row, "co_no").strip().lstrip("0").zfill(2)
                county = DOR_COUNTY.get(co, f"CO-{co}")
                own_name = g(row, "own_name")
                own_state = g(row, "own_state").upper()

                # payoff (a): out-of-state owner whose name matches an out-of-state new licensee
                if lic_by_name and own_state and own_state != "FL" and not looks_like_business(own_name):
                    key = norm_tokens(own_name)
                    if key and key in lic_by_name:
                        for lic in lic_by_name[key]:
                            lic_state = (lic.get("state") or "").upper()
                            same_state = lic_state == own_state
                            candidates.append({
                                "owner_name": own_name,
                                "licensee_name": lic.get("name"),
                                "profession": lic.get("profession"),
                                "license_date": lic.get("date"),
                                "licensee_city_state": f"{lic.get('city','')}, {lic_state}",
                                "owner_mailing": f"{g(row,'own_addr')}, {g(row,'own_city')}, {own_state} {g(row,'own_zip')}",
                                "parcel": g(row, "parcel"),
                                "property_county": county,
                                "property_address": f"{g(row,'phy_addr')}, {g(row,'phy_city')} {g(row,'phy_zip')}".strip(", "),
                                "use_code": use_norm,
                                "confidence": "high (licensee state matches owner mailing state)" if same_state
                                              else "medium (name match; state differs)",
                            })
                            n_cand += 1

                # payoffs (b prep) + (c): medical parcels
                if use_norm in MEDICAL_USE_CODES:
                    medical_parcels.add((co, g(row, "parcel")))
                    bldg_sf = to_int(g(row, "bldg_sf")); jv = to_int(g(row, "jv"))
                    own_a = norm_addr(g(row, "own_addr")); phy_a = norm_addr(g(row, "phy_addr"))
                    owner_occ = "Yes" if own_a and own_a == phy_a else ("No (absentee)" if own_a else "?")
                    medical_props.append([
                        county, g(row, "parcel"), MEDICAL_USE_CODES[use_norm],
                        g(row, "phy_addr"), g(row, "phy_city"), g(row, "phy_zip"),
                        own_name, g(row, "own_addr"), g(row, "own_city"), own_state,
                        owner_occ, "Yes" if owner_occ.startswith("No") else "",
                        bldg_sf or "", to_int(g(row, "land_sf")) or "", g(row, "yr_blt"),
                        jv or "", round(jv / bldg_sf, 2) if bldg_sf else "",
                        to_int(g(row, "sale_prc1")) or "", g(row, "sale_yr1"),
                        to_int(g(row, "sale_prc2")) or "", g(row, "sale_yr2"),
                    ])
                    n_med += 1
        print(f"        {n_rows:,} parcels scanned -> {n_med:,} medical, {n_cand} relocating-owner hits")

    # ---- SDF pass (payoff b) ----
    sale_comps = []
    for path in sdf_files:
        res, _ = probe(path, SDF_FIELDS, "SDF", required=["parcel", "sale_prc"])
        if not res:
            continue
        g = lambda row, k: (row.get(res[k], "") if k in res else "").strip()
        n_rows = n_hit = 0
        # build a parcel->bldg_sf lookup from the medical props for $/SF
        sf_by_parcel = {(r[0], r[1]): r[12] for r in medical_props}  # (county,parcel)->bldg_sf (county name here)
        med_parcels_by_name = {(DOR_COUNTY.get(co, f"CO-{co}"), pid) for (co, pid) in medical_parcels}
        with open(path, newline="", encoding="latin-1") as f:
            for row in csv.DictReader(f):
                n_rows += 1
                co = g(row, "co_no").strip().lstrip("0").zfill(2)
                county = DOR_COUNTY.get(co, f"CO-{co}")
                pid = g(row, "parcel")
                if (county, pid) not in med_parcels_by_name:
                    continue
                prc = to_int(g(row, "sale_prc"))
                if prc <= 0:
                    continue
                qual = g(row, "qual_cd").strip().zfill(2)
                sf = to_int(sf_by_parcel.get((county, pid), 0))
                sale_comps.append([
                    county, pid, g(row, "sale_yr"), g(row, "sale_mo"), prc,
                    "Yes" if qual in QUALIFIED_SALE_CODES else "No", qual,
                    sf or "", round(prc / sf, 2) if sf else "",
                ])
                n_hit += 1
        print(f"        {n_rows:,} sales scanned -> {n_hit:,} on medical parcels")

    # ---- write derived outputs (small; into the repo) ----
    os.makedirs(RADAR, exist_ok=True); os.makedirs(DEALS, exist_ok=True)

    cand_path = os.path.join(RADAR, f"relocating-owner-candidates-{today}.json")
    json.dump(candidates, open(cand_path, "w", encoding="utf-8"), indent=2)

    def write_xlsx(path, header, rows, title):
        wb = openpyxl.Workbook(); ws = wb.active; ws.title = title
        ws.append(header)
        for r in rows:
            ws.append(r)
        wb.save(path)

    props_path = os.path.join(DEALS, f"territory-medical-properties-{today}.xlsx")
    write_xlsx(props_path,
        ["County","Parcel","Use","Property Address","City","Zip","Owner","Owner Mailing",
         "Owner City","Owner State","Owner-Occupied?","Absentee?","Bldg SF","Land SF","Yr Built",
         "Just Value","$/SF (JV)","Last Sale $","Last Sale Yr","Prior Sale $","Prior Sale Yr"],
        medical_props, "Medical Properties")

    comps_path = os.path.join(DEALS, f"territory-medical-sale-comps-{today}.xlsx")
    write_xlsx(comps_path,
        ["County","Parcel","Sale Yr","Sale Mo","Sale Price","Qualified?","Qual Code","Bldg SF","$/SF"],
        sale_comps, "DOR Sale Comps")

    print("\n=== DONE ===")
    print(f"(a) relocating-owner candidates : {len(candidates):>6}  -> {cand_path}")
    print(f"(b) medical sale comps          : {len(sale_comps):>6}  -> {comps_path}")
    print(f"(c) medical properties (refresh): {len(medical_props):>6}  -> {props_path}")
    print("\nNEXT: eyeball the candidates JSON (these are the leads). Diff the properties refresh")
    print("against the curated DNA/Deal Management/territory-medical-properties.xlsx before merging.")
    print("Then DELETE the raw NAL/SDF files from the scratch folder (discard rule).")

if __name__ == "__main__":
    main()
