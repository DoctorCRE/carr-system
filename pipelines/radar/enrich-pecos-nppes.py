#!/usr/bin/env python3
"""
enrich-pecos-nppes.py — give pecos pool rows their own practice geography.

The CMS PPEF enrollment file carries only enrollment STATE, no street/city/zip
(there is NO address sub-file — verified against the data.cms.gov catalog Jul 22,
2026). The precise "here" comes from NPPES instead: the PECOS row already holds
the NPI, and the NPPES registry publishes every NPI's practice LOCATION address.
This is the same registry + practiceLocations join the weekly NPI sweep uses.

Scope: only pecos rows whose name-key collides with another pool are enriched —
a lone pecos row can never reach the candidate threshold (needs a 2nd signal
class), so its geography never changes an outcome. That keeps this to a few
thousand polite API calls, not 32k. Pass --all to enrich the whole pool anyway.

For each NPI: pull the LOCATION address (fallback practiceLocations, then
MAILING). Writes city/county-blank/state/zip back into upstream/pecos.json and
appends a provenance note. NPPES gives no county, so territory matching in
corroborate.py keys on city (TERRITORY_CITIES is comprehensive). Idempotent:
rows already carrying an [nppes ...] detail tag are skipped unless --refresh.

Usage: python3 enrich-pecos-nppes.py [--all] [--refresh]
"""
import json, os, re, sys, time, urllib.request, urllib.error

HERE = os.path.dirname(os.path.abspath(__file__))
UP = os.path.join(HERE, "upstream")
OTHER_POOLS = ("licenses-pool.json", "nppes-moves.json", "tips.json", "deeds.json",
               "jobs.json", "domains.json")
TITLE = {"DR","MD","DO","DC","OD","DDS","DMD","PA","NP","ARNP","APRN","RN","LPC",
         "PT","OT","OTR","PHD","MR","MRS","MS","JR","SR","II","III","IV"}
API = "https://npiregistry.cms.hhs.gov/api/?version=2.1&number="

def key(name):
    p = [t for t in re.sub(r"[^A-Za-z ]", " ", str(name or "")).upper().split()
         if t not in TITLE and len(t) > 1]
    return p[-1] + "|" + p[0] if len(p) >= 2 else None

def npi_of(row):
    m = re.search(r"NPI (\d+)", row.get("detail", ""))
    return m.group(1) if m else None

def loc_for(npi):
    """Return (city, state, zip5) from the NPI's practice location, or None."""
    try:
        with urllib.request.urlopen(API + npi, timeout=25) as fh:
            d = json.load(fh)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
        return None
    if not d.get("results"):
        return None
    r = d["results"][0]
    cands = [a for a in r.get("addresses", []) if a.get("address_purpose") == "LOCATION"]
    cands += r.get("practiceLocations", [])
    cands += [a for a in r.get("addresses", []) if a.get("address_purpose") == "MAILING"]
    for a in cands:
        if a.get("city") and a.get("state"):
            return a["city"].title(), a["state"].upper(), str(a.get("postal_code", ""))[:5]
    return None

def main():
    refresh = "--refresh" in sys.argv
    pool = json.load(open(os.path.join(UP, "pecos.json")))

    if "--all" in sys.argv:
        targets = pool
    else:
        other = set()
        for fn in OTHER_POOLS:
            path = os.path.join(UP, fn)
            if not os.path.exists(path): continue
            for r in json.load(open(path)):
                nm = r.get("name") or r.get("grantee") or r.get("contact") or \
                     ((r.get("first", "") + " " + r.get("last", "")).strip())
                k = key(nm)
                if k: other.add(k)
        targets = [r for r in pool if key(r.get("name")) in other]

    todo = [r for r in targets if (refresh or "[nppes" not in r.get("detail", "")) and npi_of(r)]
    print(f"[enrich] {len(pool)} pool rows, {len(targets)} in scope, {len(todo)} to query")
    hit = terr = 0
    for i, r in enumerate(todo, 1):
        npi = npi_of(r)
        loc = loc_for(npi)
        if loc:
            city, st, zp = loc
            r["city"], r["state_practice"], r["zip"] = city, st, zp
            r["detail"] = re.sub(r"\s*\[nppes[^\]]*\]", "", r["detail"]) + \
                          f" [nppes loc: {city} {st} {zp}]"
            hit += 1
        else:
            r["detail"] = re.sub(r"\s*\[nppes[^\]]*\]", "", r["detail"]) + " [nppes: no location]"
        if i % 100 == 0:
            print(f"  {i}/{len(todo)} · {hit} located")
            json.dump(pool, open(os.path.join(UP, "pecos.json"), "w"), indent=1)  # checkpoint
        time.sleep(0.22)   # ~4.5 req/s, polite to the public API
    json.dump(pool, open(os.path.join(UP, "pecos.json"), "w"), indent=1)
    print(f"[done] {hit}/{len(todo)} rows given a practice location -> upstream/pecos.json")

if __name__ == "__main__":
    main()
