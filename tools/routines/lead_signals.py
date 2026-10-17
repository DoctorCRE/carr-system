from __future__ import annotations

import csv
import io
import json
import re
import urllib.parse
import urllib.request
import zipfile
from datetime import timedelta
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
INDEX_URL = "https://download.cms.gov/nppes/NPI_Files.html"
PREFIXES = {"323", "324", "325", "363", "364", "365", "366"}
COUNTIES = {"ESCAMBIA", "SANTA ROSA", "OKALOOSA", "WALTON", "BAY", "LEON", "MOBILE", "BALDWIN", "HOUSTON"}
CITIES = {"PENSACOLA", "MILTON", "PACE", "GULF BREEZE", "CANTONMENT", "NAVARRE", "JAY", "CENTURY", "TALLAHASSEE", "PANAMA CITY", "LYNN HAVEN", "DESTIN", "FORT WALTON BEACH", "CRESTVIEW", "NICEVILLE", "SANTA ROSA BEACH", "MARY ESTHER", "SHALIMAR", "FREEPORT", "DEFUNIAK SPRINGS", "MOBILE", "DAPHNE", "FAIRHOPE", "SPANISH FORT", "GULF SHORES", "FOLEY", "BONIFAY", "CHIPLEY", "PANAMA CITY BEACH", "DOTHAN", "ENTERPRISE"}
WEIGHTS = {"nppes-org": 4, "nppes-person": 1}


def _text(value):
    return str(value or "").strip()


def _date(value):
    value = _text(value)
    if not value:
        return None
    for pattern in ("%m/%d/%Y", "%Y-%m-%d"):
        try:
            from datetime import datetime
            return datetime.strptime(value[:10], pattern).date()
        except ValueError:
            pass
    raise ValueError("invalid source date")


def in_territory(row):
    state = _text(row.get("state")).upper()
    if state and state not in {"AL", "FL"}:
        return False
    return (_text(row.get("zip"))[:3] in PREFIXES
            or _text(row.get("county")).upper().removesuffix(" COUNTY") in COUNTIES
            or _text(row.get("city")).upper() in CITIES)


class WeeklyLinks(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            href = dict(attrs).get("href", "")
            match = re.search(r"NPPES_Data_Dissemination_(\d{6})_(\d{6})_Weekly_V2\.zip$", href, re.I)
            if match:
                from datetime import datetime
                end = datetime.strptime(match[2], "%m%d%y").date()
                url = urllib.parse.urljoin(INDEX_URL, href)
                if urllib.parse.urlparse(url).netloc != "download.cms.gov":
                    raise ValueError("weekly file is outside CMS")
                self.links.append((end, url))


def weekly_files(html, as_of):
    parser = WeeklyLinks()
    parser.feed(html)
    links = sorted(set(parser.links))
    selected = [(end, url) for end, url in links if as_of - timedelta(days=14) <= end <= as_of]
    if not selected or (as_of - selected[-1][0]).days > 8:
        raise ValueError("NPPES weekly publication is missing or stale")
    return [url for _, url in selected]


def parse_nppes(rows, *, as_of, source, practice_locations=None):
    """Enumeration is the trigger; updates do not fabricate a startup date."""
    cutoff = as_of - timedelta(days=14)
    result = []
    locations = practice_locations or {}
    for raw in rows:
        npi = _text(raw.get("NPI"))
        if not re.fullmatch(r"\d{10}", npi):
            raise ValueError("NPPES row requires a 10-digit NPI")
        if "Provider Enumeration Date" not in raw or "Entity Type Code" not in raw:
            raise ValueError("NPPES provider CSV has missing required columns")
        enumerated = _date(raw.get("Provider Enumeration Date"))
        if enumerated is None or not cutoff <= enumerated <= as_of:
            continue
        entity = _text(raw.get("Entity Type Code"))
        if entity not in {"1", "2"}:
            raise ValueError("unknown NPPES entity type")
        name = (_text(raw.get("Provider Organization Name (Legal Business Name)")) if entity == "2" else
                " ".join(_text(raw.get(k)) for k in ("Provider First Name", "Provider Middle Name", "Provider Last Name (Legal Name)")).strip())
        if not name:
            raise ValueError("NPPES provider name missing")
        addresses = [{"city": raw.get("Provider Business Practice Location Address City Name"),
                      "state": raw.get("Provider Business Practice Location Address State Name"),
                      "zip": raw.get("Provider Business Practice Location Address Postal Code"),
                      "address": raw.get("Provider First Line Business Practice Location Address"),
                      "phone": raw.get("Provider Business Practice Location Address Telephone Number")}] + locations.get(npi, [])
        local = next((a for a in addresses if in_territory(a)), None)
        mailing = {"city": raw.get("Provider Business Mailing Address City Name"), "state": raw.get("Provider Business Mailing Address State Name"), "zip": raw.get("Provider Business Mailing Address Postal Code")}
        if local is None and not in_territory(mailing):
            continue
        # A mailing-only tie remains visible with its weak evidence explicitly marked.
        selected = local or mailing
        codes = [_text(raw.get(f"Healthcare Provider Taxonomy Code_{i}")) for i in range(1, 16)]
        result.append({"name": name, "kind": "org" if entity == "2" else "person", "npi": npi,
                       "date": enumerated.isoformat(), "city": _text(selected.get("city")),
                       "state": _text(selected.get("state")), "zip": _text(selected.get("zip")),
                       "phone": _text(selected.get("phone")), "address": _text(selected.get("address")),
                       "specialty": ", ".join(c for c in codes if c), "signal": "nppes-org" if entity == "2" else "nppes-person",
                       "source": source, "mailing_only": local is None,
                       "raw": raw})
    return result


def parse_weekly_zip(payload, *, as_of, source):
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        primary = [n for n in archive.namelist() if Path(n).name.lower().startswith("npidata_pfile_") and n.lower().endswith(".csv") and "fileheader" not in n.lower()]
        if len(primary) != 1:
            raise ValueError("weekly archive needs exactly one provider CSV")
        locations = {}
        for name in archive.namelist():
            if "pl_pfile_" in Path(name).name.lower() and name.lower().endswith(".csv") and "fileheader" not in name.lower():
                with archive.open(name) as stream:
                    for row in csv.DictReader(io.TextIOWrapper(stream, encoding="utf-8-sig")):
                        locations.setdefault(_text(row.get("NPI")), []).append({
                            "city": row.get("Provider Secondary Practice Location Address - City Name"),
                            "state": row.get("Provider Secondary Practice Location Address - State Name"),
                            "zip": row.get("Provider Secondary Practice Location Address - Postal Code"),
                            "address": row.get("Provider Secondary Practice Location Address- Address Line 1"),
                            "phone": row.get("Provider Secondary Practice Location Address - Telephone Number")})
        with archive.open(primary[0]) as stream:
            reader = csv.DictReader(io.TextIOWrapper(stream, encoding="utf-8-sig"))
            required = {"NPI", "Entity Type Code", "Provider Enumeration Date", "Provider Business Practice Location Address Postal Code"}
            if not required.issubset(reader.fieldnames or []):
                raise ValueError("NPPES provider CSV has missing required columns")
            return parse_nppes(reader, as_of=as_of, source=source, practice_locations=locations)


def candidates(rows):
    """Combine overlapping publications by exact NPI."""
    grouped = {}
    for row in rows:
        identity = "npi:" + row["npi"]
        grouped.setdefault(identity, []).append(row)
    result = []
    for identity, evidence in sorted(grouped.items()):
        ordered = sorted(evidence, key=lambda r: (r.get("date") or "", r.get("source") or ""), reverse=True)
        primary = ordered[0]
        signals = sorted({row["signal"] for row in ordered})
        score = min(10, sum(WEIGHTS[s] for s in signals))
        if primary.get("mailing_only"):
            score = min(score, 1)
        result.append({**primary, "source_key": identity, "sources": sorted({row["source"] for row in ordered}),
                       "score": score, "score_basis": "Estimated evidence weights: " + ", ".join(f"{s}={WEIGHTS[s]}" for s in signals) + ("; mailing-only territory tie, capped at 1" if primary.get("mailing_only") else ""),
                       "signals": signals})
    return result


def _fetch(url):
    request = urllib.request.Request(url, headers={"User-Agent": "CARR lead-signals weekly public data reader"})
    with urllib.request.urlopen(request, timeout=60) as response:
        return response.read()


def prepare(ctx):
    fixture = ctx.fixture
    if fixture is not None:
        rows = parse_nppes(fixture.get("nppes_rows", []), as_of=ctx.now.date(), source=INDEX_URL)
        claims = fixture.get("claims", [])
    else:
        urls = weekly_files(_fetch(INDEX_URL).decode("utf-8"), ctx.now.date())
        rows = []
        for url in urls:
            rows.extend(parse_weekly_zip(_fetch(url), as_of=ctx.now.date(), source=url))
        reservoir = ctx.read("claim-card", {"include_needs_contact": True, "limit": 100000})
        claims = reservoir.get("candidates", [])
        if len(claims) < reservoir.get("claimable", len(claims)):
            raise ValueError("claim-card truncated the lead reservoir")
    consumed_path = ROOT / "out/routines/lead-signals-weekly/consumed-candidates.json"
    consumed = set(fixture.get("consumed_keys", [])) if fixture is not None else set(json.loads(consumed_path.read_text()) if consumed_path.exists() else [])
    proposed = [row for row in candidates(rows) if row["source_key"] not in consumed]
    for row in proposed:
        row["existing_candidates"] = [
            {"pool_id": c.get("pool_id"), "name": c.get("display_name"), "org_name": c.get("org_name"), "city": c.get("city")}
            for c in claims
            if _text(c.get("display_name")).casefold() == row["name"].casefold()
            and _text(c.get("city")).casefold() == _text(row.get("city")).casefold()
        ]
    return {"work": bool(proposed), "candidates": proposed, "consumed_keys": sorted(consumed)}


def execute(ctx, plan):
    if ctx.dry_run:
        return {"routine": "lead-signals-weekly", "dry_run": True, "work": plan["work"],
                "candidate_count": len(plan["candidates"]),
                "estimated_scores": [r["score"] for r in plan["candidates"]],
                "model_calls": 0,
                "record_writes": 0, "local_writes": 0}
    created, reviews = [], []
    consumed = set(plan.get("consumed_keys", []))
    def remember(key):
        consumed.add(key)
        if ctx.fixture is not None:
            return
        path = ROOT / "out/routines/lead-signals-weekly/consumed-candidates.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(sorted(consumed)))
        temporary.replace(path)
    for row in plan["candidates"]:
        key = row["source_key"]
        if row.get("existing_candidates"):
            reviews.append(ctx.review_item("Confirm a lead already in the candidate board: " + row["name"],
                json.dumps({"candidate": row["name"], "sources": row["sources"],
                            "matches": row["existing_candidates"], "fix": "Confirm which candidate this new signal belongs to; a name and city are not an automatic person match."})[:2000],
                "lead-signals:candidate-review:" + key))
            remember(key)
            continue
        sources = [{"url": url, "observed_at": ctx.now.isoformat()} for url in row["sources"] if url.startswith("https://")]
        if not sources:
            reviews.append(ctx.review_item("Verify a new lead's source: " + row["name"],
                json.dumps({"candidate": row["name"], "source_key": key, "sources": row["sources"],
                            "estimated_score": row["score"], "fix": "Resolve this NPPES signal to primary-source evidence before identity intake."})[:2000],
                "lead-signals:source-review:" + key))
            remember(key)
            continue
        evidence = {"sources": sources, "field_evidence": {field: [0] for field in ("name", "company", "phone", "specialty", "market")}, "discrepancies": []}
        party = ctx.write("add-party", {"name": row["name"], "kind": row["kind"], "city": _text(row.get("city")), "state": _text(row.get("state")), "phone": _text(row.get("phone")), "specialty": _text(row.get("specialty")), "research_evidence": evidence}, "lead-signals:party:" + key)
        if party.get("needs_confirm") or not party.get("party_id"):
            reviews.append(ctx.review_item("Confirm the identity of a new lead: " + row["name"],
                json.dumps({"candidate": row["name"], "source_key": key, "sources": row["sources"],
                            "matches": party.get("candidates", []), "fix": "Confirm the correct identity on this review; the routine did not merge or force-create a party."})[:2000],
                "lead-signals:identity-review:" + key))
            remember(key)
            continue
        lead = ctx.write("new-lead", {"party_id": party["party_id"], "stage": "new", "source_type": "lead-signals-weekly", "source_detail": json.dumps({"source_key": key, "npi": row.get("npi"), "sources": row["sources"], "estimated": True}), "score": row["score"], "score_basis": row["score_basis"]}, "lead-signals:lead:" + key)
        created.append(lead)
        remember(key)
    return {"created": created, "reviews": reviews, "candidate_count": len(plan["candidates"]), "model_calls": 0}
