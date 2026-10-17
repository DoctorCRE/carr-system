#!/usr/bin/env python3
"""
build-license-pool.py — turn the DOH transport file into the license pool.
Radar lane 4, Rung 2 (upstream-radar.md). Reads the in-browser-filtered TSV that
Chrome dropped (already minimal fields, already 12-mo-new, raw statewide file was
filtered in browser memory and never hit disk — discard rule satisfied upstream).
Writes Automation/radar/upstream/licenses-pool.json (weight-1 corroborator).
Then DELETE the transport TSV (it is raw-adjacent PII; do not keep it).

Usage: python3 build-license-pool.py <transport.tsv>
Row shape written (matches corroborate.py's key()/field expectations, name = First Last):
  {"name","profession","city","county","state","date","detail"}
"""
import sys, os, json, csv
HERE = os.path.dirname(os.path.abspath(__file__))
UP = os.path.join(HERE, "upstream")
PROF = {"501":"Chiropractic Physician","701":"Dental","1512":"Physician Assistant",
        "1711":"Advanced Practice Registered Nurse","1801":"Optometrist",
        "1901":"Osteopathic Physician","2101":"Podiatric Physician",
        "5203":"Licensed Mental Health Counselor","5501":"Physical Therapist",
        "5601":"Occupational Therapist"}
def tc(s): return " ".join(w.capitalize() for w in str(s).split())
def main():
    src = sys.argv[1]
    rows, byprof = [], {}
    with open(src, newline="", encoding="latin1") as fh:
        rd = csv.reader(fh, delimiter="\t")
        header = next(rd)  # Last First ProCode OriginalDate State County City
        for r in rd:
            if len(r) < 7: continue
            last, first, code, orig, st, co, city = [x.strip() for x in r[:7]]
            if not last and not first: continue
            prof = PROF.get(code, code)
            rows.append({
                "name": (tc(first)+" "+tc(last)).strip(),
                "profession": prof,
                "city": tc(city), "county": tc(co),
                "state": st.upper(), "date": orig,
                "detail": f"new {prof} license ({code}) orig {orig}, {tc(city)} {st.upper()}",
            })
            byprof[prof] = byprof.get(prof, 0) + 1
    os.makedirs(UP, exist_ok=True)
    out = os.path.join(UP, "licenses-pool.json")
    json.dump(rows, open(out, "w"), indent=1)
    print(f"[out] {len(rows)} license rows -> {out}")
    fl = sum(1 for x in rows if x["state"] == "FL")
    print(f"[geo] FL {fl} · out-of-state {len(rows)-fl} (relocator candidates kept by design)")
    for p, n in sorted(byprof.items(), key=lambda kv:-kv[1]):
        print(f"   {n:>6}  {p}")
if __name__ == "__main__":
    main()
