#!/usr/bin/env python3
"""
build-pecos-pool.py — turn the quarterly CMS PECOS Public Provider Enrollment
extract into the pecos pool. Radar lane 4 (upstream-radar.md); quarterly gate
Jan/Apr/Jul/Oct. Run LOCALLY (Claude Code) — the raw statewide CSV lives in a
non-Drive scratch and is DELETED after this run (discard rule).

Input: the base PPEF_Enrollment_Extract CSV (columns: NPI, MULTIPLE_NPI_FLAG,
PECOS_ASCT_CNTL_ID, ENRLMT_ID, PROVIDER_TYPE_CD, PROVIDER_TYPE_DESC, STATE_CD,
FIRST_NAME, MDL_NAME, LAST_NAME, ORG_NAME). No city/zip — that lives in the
Address sub-file on the same CMS page; state-level filtering only until that
sub-file is pulled. The enrollment date is decoded from ENRLMT_ID
(I|O + YYYYMMDD + seq).

Writes:
  upstream/pecos.json — FL/AL INDIVIDUAL practitioners whose enrollment was
    filed in the last 24 months (matches the pool age-out horizon). Row shape
    matches corroborate.py: {"name","profession","city","county","state",
    "date","detail"} — city/county empty here; practice geography is filled in
    the next step by enrich-pecos-nppes.py (NPPES join on NPI). The PPEF file
    itself has NO street/city/zip and NO address sub-file (CMS catalog, Jul 22).
  _data/pecos-baseline-<Q>.json — compact NPI|ENRLMT_ID set of ALL FL/AL rows
    (individuals + orgs, no names) so next quarter's run can diff true
    newly-appearing enrollments instead of relying on the date decode.

Usage: python3 build-pecos-pool.py <PPEF_Enrollment_Extract.csv>
Then DELETE the raw CSV and the zip it came from.
"""
import sys, os, json, csv, re
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))
UP = os.path.join(HERE, "upstream")
DATA = os.path.join(HERE, "_data")
STATES = {"FL", "AL"}
WINDOW_MONTHS = 24

def tc(s): return " ".join(w.capitalize() for w in str(s).split())

def enrl_date(enrlmt_id):
    """I20091005000100 -> date(2009,10,5); None if the ID doesn't decode."""
    s = str(enrlmt_id)
    if len(s) < 9 or s[0] not in "IO" or not s[1:9].isdigit(): return None
    try: return date(int(s[1:5]), int(s[5:7]), int(s[7:9]))
    except ValueError: return None

def main():
    src = sys.argv[1]
    today = date.today()
    cutoff = date(today.year - WINDOW_MONTHS // 12, today.month, 1)
    # Label the baseline by the DATA vintage (extract date in the filename),
    # not the run date — CMS's "Q1" file is the April extract.
    m = re.search(r"(\d{4})\.(\d{2})\.\d{2}", os.path.basename(src))
    if m:
        y, mo = int(m.group(1)), int(m.group(2))
        quarter = f"{y}Q{(mo - 2) // 3 + 1}" if mo > 1 else f"{y - 1}Q4"
    else:
        quarter = f"{today.year}Q{(today.month - 1) // 3 + 1}"

    baseline = set()
    recent = {}   # NPI|state -> row, newest enrollment wins
    n_total = n_state = 0
    with open(src, newline="", encoding="latin1") as fh:
        rd = csv.DictReader(fh)
        for r in rd:
            n_total += 1
            if r.get("STATE_CD") not in STATES: continue
            n_state += 1
            baseline.add(f"{r['NPI']}|{r['ENRLMT_ID']}")
            last, first = r.get("LAST_NAME", "").strip(), r.get("FIRST_NAME", "").strip()
            if not (last and first): continue   # org rows can't join by person name
            d = enrl_date(r["ENRLMT_ID"])
            if d is None or d < cutoff: continue
            k = f"{r['NPI']}|{r['STATE_CD']}"
            if k in recent and recent[k]["_d"] >= d: continue
            prof = tc(r.get("PROVIDER_TYPE_DESC", "").replace("PRACTITIONER - ", ""))
            recent[k] = {"_d": d,
                "name": f"{tc(first)} {tc(last)}",
                "profession": prof, "city": "", "county": "",
                "state": r["STATE_CD"], "date": d.strftime("%m/%d/%Y"),
                "detail": f"new Medicare enrollment ({prof}) filed {d.isoformat()}, "
                          f"NPI {r['NPI']}, enrolled state {r['STATE_CD']} "
                          f"(practice geography from NPPES via enrich-pecos-nppes.py)"}

    rows = [{k: v for k, v in r.items() if k != "_d"}
            for r in sorted(recent.values(), key=lambda r: r["_d"], reverse=True)]
    out = os.path.join(UP, "pecos.json")
    json.dump(rows, open(out, "w"), indent=1)
    bl = os.path.join(DATA, f"pecos-baseline-{quarter}.json")
    json.dump(sorted(baseline), open(bl, "w"))
    print(f"[in] {n_total} rows scanned, {n_state} FL/AL")
    print(f"[out] {len(rows)} recent-enrollment practitioner rows (since {cutoff}) -> {out}")
    print(f"[out] {len(baseline)} NPI|enrollment baseline ids -> {bl}")
    print("NOW DELETE the raw CSV and zip (discard rule).")

if __name__ == "__main__":
    main()
