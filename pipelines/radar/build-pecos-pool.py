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
  _data/pecos-checkpoint-<Q>.json — resumable progress for an interrupted run;
    removed after both final outputs and the source receipt are committed.
  _data/pecos-source-<Q>.json — source digest that makes a completed quarter
    idempotent and refuses changed-content replays.

Usage: python3 build-pecos-pool.py <PPEF_Enrollment_Extract.csv>
Then DELETE the raw CSV and the zip it came from.
"""
import sys, os, json, csv, re, hashlib, tempfile
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))
UP = os.path.join(HERE, "upstream")
DATA = os.path.join(HERE, "_data")
STATES = {"FL", "AL"}
WINDOW_MONTHS = 24
CHECKPOINT_INTERVAL = 10000
REQUIRED_COLUMNS = {
    "NPI", "MULTIPLE_NPI_FLAG", "PECOS_ASCT_CNTL_ID", "ENRLMT_ID",
    "PROVIDER_TYPE_CD", "PROVIDER_TYPE_DESC", "STATE_CD", "FIRST_NAME",
    "MDL_NAME", "LAST_NAME", "ORG_NAME",
}

def origination_rows(rows, *, published_on):
    """Use the public-file publication date; an enrollment ID is not a dated fact."""
    result = []
    for raw in rows:
        if not REQUIRED_COLUMNS.issubset(raw):
            raise ValueError("PECOS schema drift; missing required columns")
        state = raw["STATE_CD"].strip()
        if state not in STATES:
            continue
        npi, enrollment = raw["NPI"].strip(), raw["ENRLMT_ID"].strip()
        if not re.fullmatch(r"\d{10}", npi) or not re.fullmatch(r"[IO]\d{8}\d+", enrollment):
            raise ValueError("PECOS invalid NPI or enrollment identifier")
        name = raw["ORG_NAME"].strip() or " ".join(raw[k].strip() for k in
               ("FIRST_NAME", "MDL_NAME", "LAST_NAME") if raw[k].strip())
        if not name:
            raise ValueError("PECOS missing provider name")
        result.append({"record_key": npi + "|" + enrollment, "npi": npi, "name": name,
                       "city": "", "state": state, "evidence_date": published_on.isoformat(),
                       "date_basis": "publication",
                       "source_url": "https://data.cms.gov/provider-characteristics/medicare-provider-supplier-enrollment/medicare-fee-for-service-public-provider-enrollment",
                       "facts": {"enrollment_id": enrollment, "provider_type": raw["PROVIDER_TYPE_DESC"].strip()}})
    return result


def tc(s): return " ".join(w.capitalize() for w in str(s).split())

def enrl_date(enrlmt_id):
    """I20091005000100 -> date(2009,10,5); None if the ID doesn't decode."""
    s = str(enrlmt_id)
    if len(s) < 9 or s[0] not in "IO" or not s[1:9].isdigit(): return None
    try: return date(int(s[1:5]), int(s[5:7]), int(s[7:9]))
    except ValueError: return None

def atomic_json(path, value):
    fd, temporary = tempfile.mkstemp(prefix=".pecos-", dir=os.path.dirname(path))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, indent=1)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary): os.unlink(temporary)

def digest_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()

def parse_quarter(filename, today):
    match = re.search(r"(\d{4})\.(\d{2})\.\d{2}", os.path.basename(filename))
    if not match:
        return f"{today.year}Q{(today.month - 1) // 3 + 1}"
    year, month = map(int, match.groups())
    if not 1 <= month <= 12:
        raise ValueError("source filename has an invalid data vintage")
    return f"{year}Q{(month - 2) // 3 + 1}" if month > 1 else f"{year - 1}Q4"

def read_source_header(path):
    with open(path, newline="", encoding="latin1") as stream:
        header = next(csv.reader(stream), None)
    if not header or len(header) != len(set(header)) or not REQUIRED_COLUMNS.issubset(header):
        missing = sorted(REQUIRED_COLUMNS.difference(header or []))
        raise ValueError("PECOS source schema drift; missing or duplicate required columns: " + ", ".join(missing))

def save_checkpoint(path, digest, processed, baseline, recent, n_total, n_state):
    atomic_json(path, {
        "source_sha256": digest,
        "processed_rows": processed,
        "baseline": sorted(baseline),
        "recent": {key: {**row, "_d": row["_d"].isoformat()} for key, row in recent.items()},
        "n_total": n_total,
        "n_state": n_state,
    })

def main():
    if len(sys.argv) != 2:
        raise SystemExit("usage: build-pecos-pool.py <PPEF_Enrollment_Extract.csv>")
    src = sys.argv[1]
    today = date.today()
    cutoff = date(today.year - WINDOW_MONTHS // 12, today.month, 1)
    quarter = parse_quarter(src, today)
    digest = digest_file(src)
    read_source_header(src)
    os.makedirs(UP, exist_ok=True)
    os.makedirs(DATA, exist_ok=True)
    checkpoint_path = os.path.join(DATA, f"pecos-checkpoint-{quarter}.json")
    receipt_path = os.path.join(DATA, f"pecos-source-{quarter}.json")
    if os.path.exists(receipt_path):
        receipt = json.load(open(receipt_path))
        if receipt.get("source_sha256") != digest:
            raise ValueError("PECOS quarter replay changed source content; preserve outputs and review the new source")
        print(f"[replay] {quarter} source already processed; outputs remain unchanged")
        return

    if os.path.exists(checkpoint_path):
        checkpoint = json.load(open(checkpoint_path))
        if checkpoint.get("source_sha256") != digest:
            raise ValueError("PECOS checkpoint belongs to different source content; preserve it and review the new source")
        processed = checkpoint["processed_rows"]
        baseline = set(checkpoint["baseline"])
        recent = {key: {**row, "_d": date.fromisoformat(row["_d"])} for key, row in checkpoint["recent"].items()}
        n_total, n_state = checkpoint["n_total"], checkpoint["n_state"]
    else:
        processed = n_total = n_state = 0
        baseline, recent = set(), {}

    def checkpoint_now():
        save_checkpoint(checkpoint_path, digest, processed, baseline, recent, n_total, n_state)

    with open(src, newline="", encoding="latin1") as fh:
        rd = csv.DictReader(fh)
        for row_number, r in enumerate(rd, 1):
            if row_number <= processed:
                continue
            n_total += 1
            state = r["STATE_CD"].strip()
            if state not in STATES:
                processed = row_number
                if processed % CHECKPOINT_INTERVAL == 0: checkpoint_now()
                continue
            n_state += 1
            npi, enrollment_id = r["NPI"].strip(), r["ENRLMT_ID"].strip()
            if not re.fullmatch(r"\d{10}", npi) or not re.fullmatch(r"[IO]\d{8}\d+", enrollment_id):
                raise ValueError(f"PECOS row {row_number} has an invalid NPI or enrollment id")
            baseline.add(f"{npi}|{enrollment_id}")
            last, first = r.get("LAST_NAME", "").strip(), r.get("FIRST_NAME", "").strip()
            if first and last:
                d = enrl_date(enrollment_id)
                if d is not None and d >= cutoff:
                    k = f"{npi}|{state}"
                    if k not in recent or recent[k]["_d"] < d:
                        prof = tc(r.get("PROVIDER_TYPE_DESC", "").replace("PRACTITIONER - ", ""))
                        recent[k] = {"_d": d,
                            "name": f"{tc(first)} {tc(last)}",
                            "profession": prof, "city": "", "county": "",
                            "state": state, "date": d.strftime("%m/%d/%Y"),
                            "detail": f"new Medicare enrollment ({prof}) filed {d.isoformat()}, "
                                      f"NPI {npi}, enrolled state {state} "
                                      f"(practice geography from NPPES via enrich-pecos-nppes.py)"}
            processed = row_number
            if processed % CHECKPOINT_INTERVAL == 0:
                checkpoint_now()

    rows = [{k: v for k, v in r.items() if k != "_d"}
            for r in sorted(recent.values(), key=lambda r: r["_d"], reverse=True)]
    out = os.path.join(UP, "pecos.json")
    bl = os.path.join(DATA, f"pecos-baseline-{quarter}.json")
    checkpoint_now()
    atomic_json(out, rows)
    atomic_json(bl, sorted(baseline))
    atomic_json(receipt_path, {"source_sha256": digest, "rows_scanned": n_total})
    os.unlink(checkpoint_path)
    print(f"[in] {n_total} rows scanned, {n_state} FL/AL")
    print(f"[out] {len(rows)} recent-enrollment practitioner rows (since {cutoff}) -> {out}")
    print(f"[out] {len(baseline)} NPI|enrollment baseline ids -> {bl}")
    print("NOW DELETE the raw CSV and zip (discard rule).")

if __name__ == "__main__":
    main()
