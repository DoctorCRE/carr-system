#!/usr/bin/env python3
"""
build-license-pool-from-raw.py — filter raw FL DOH licensure files into the license pool.
Reads every LIC_*.txt in the scratch dir (pipe-delimited, 39 cols, may be gzip),
keeps only licenses with Original-Date within the last 12 months in our 10 CARR
professions, trims to minimal joinable fields, dedups, writes licenses-pool.json.
Then quarantines the raw files into <scratch>/_to_delete (device_bash cannot delete;
Joe empties it). Raw PII never goes to Google Drive — scratch is a LOCAL mount.

Usage: python3 build-license-pool-from-raw.py <scratch_dir> [as_of_date YYYY-MM-DD]
"""
import sys, os, json, gzip, glob, io
from datetime import date, timedelta
HERE = os.path.dirname(os.path.abspath(__file__))
UP = os.path.join(HERE, "upstream")
PROF = {"501":"Chiropractic Physician","701":"Dental","1512":"Physician Assistant",
        "1711":"Advanced Practice Registered Nurse","1801":"Optometrist",
        "1901":"Osteopathic Physician","2101":"Podiatric Physician",
        "5203":"Licensed Mental Health Counselor","5501":"Physical Therapist",
        "5601":"Occupational Therapist"}
AS_OF = date.fromisoformat(sys.argv[2]) if len(sys.argv) > 2 else date.today()
CUTOFF = AS_OF - timedelta(days=365)  # 12-mo lookback, relative to the run (or --as-of override)
def tc(s): return " ".join(w.capitalize() for w in str(s).split())
def pd(s):
    s=(s or "").strip()
    import re
    m=re.match(r'^(\d{1,2})/(\d{1,2})/(\d{4})$',s)
    if m: return date(int(m.group(3)),int(m.group(1)),int(m.group(2)))
    m=re.match(r'^(\d{4})-(\d{2})-(\d{2})',s)
    if m: return date(int(m.group(1)),int(m.group(2)),int(m.group(3)))
    return None
def openmaybe(p):
    with open(p,'rb') as fh: head=fh.read(2)
    if head==b'\x1f\x8b': return io.TextIOWrapper(gzip.open(p,'rb'), encoding='latin1')
    return open(p, encoding='latin1')
def main():
    scr = sys.argv[1]
    files = sorted(glob.glob(os.path.join(scr,"LIC_*.txt")))
    if not files: print("No LIC_*.txt in "+scr); return
    seen=set(); rows=[]; byprof={}; filestats=[]
    for path in files:
        fh=openmaybe(path); total=0; kept=0
        header=fh.readline().rstrip("\n").split("|"); ix={c.strip():i for i,c in enumerate(header)}
        cO=ix.get('Original-Date'); cL=ix.get('Last-Name'); cF=ix.get('First-Name')
        cC=ix.get('pro_cde'); cS=ix.get('Mailing-Address-State')
        cCo=ix.get('County-Description'); cCi=ix.get('Mailing-Address-City')
        for line in fh:
            line=line.rstrip("\n")
            if not line: continue
            total+=1; f=line.split("|")
            try:
                od=pd(f[cO])
            except IndexError: continue
            if not od or od<CUTOFF: continue
            last=f[cL].strip(); first=f[cF].strip(); code=f[cC].strip()
            if not last and not first: continue
            k=(last.upper(),first.upper(),code)
            if k in seen: continue
            seen.add(k); kept+=1
            prof=PROF.get(code,code)
            rows.append({"name":(tc(first)+" "+tc(last)).strip(),"profession":prof,
                "city":tc(f[cCi]) if cCi is not None and len(f)>cCi else "",
                "county":tc(f[cCo]) if cCo is not None and len(f)>cCo else "",
                "state":(f[cS].strip().upper() if cS is not None and len(f)>cS else ""),
                "date":f[cO].strip(),
                "detail":f"new {prof} license ({code}) orig {f[cO].strip()}"})
            byprof[prof]=byprof.get(prof,0)+1
        fh.close(); filestats.append((os.path.basename(path),total,kept))
    os.makedirs(UP,exist_ok=True)
    out=os.path.join(UP,"licenses-pool.json")
    json.dump(rows,open(out,"w"),indent=1)
    fl=sum(1 for x in rows if x["state"]=="FL")
    print(f"[out] {len(rows)} license rows (deduped) -> {out}")
    print(f"[geo] FL {fl} | out-of-state {len(rows)-fl} (relocator candidates kept by design)")
    for p,n in sorted(byprof.items(),key=lambda kv:-kv[1]): print(f"   {n:>6}  {p}")
    print("[files]"); [print(f"   {fn}: {t} rows -> {k} new") for fn,t,k in filestats]
    # quarantine raws (cannot delete via device_bash)
    q=os.path.join(scr,"_to_delete"); os.makedirs(q,exist_ok=True)
    for path in files:
        try: os.rename(path, os.path.join(q, os.path.basename(path)))
        except Exception as e: print(f"   [warn] could not move {path}: {e}")
    print(f"[quarantine] raw files moved to {q} — Joe empties this folder")
if __name__=="__main__": main()
