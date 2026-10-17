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

EXCLUDED_TAXONOMIES = {
    "251E": "home health", "28": "hospital", "313M": "skilled nursing",
    "3104A0630X": "assisted living", "332B": "durable medical equipment",
    "3416": "medical transport", "3439": "medical transport", "3446": "medical transport",
    "261QF0400X": "FQHC",
}
PRACTICE_TAXONOMIES = ("1223", "152W", "111N", "207", "208", "225", "103T", "101Y", "1041", "106H", "261Q")


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
                       "authorized_official": {
                           "name": " ".join(value for field in ("First Name", "Middle Name", "Last Name") if (value := _text(raw.get("Authorized Official " + field)))),
                           "title": _text(raw.get("Authorized Official Title or Position"))}})
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


def classify(row):
    codes = row["specialty"].split(", ")
    for prefix, reason in EXCLUDED_TAXONOMIES.items():
        if any(code.startswith(prefix) for code in codes):
            return "EXCLUDE", "Institutional exclusion: " + reason, "excluded"
    if re.search(r"\b(HOSPITAL|FQHC|HEALTH DEPARTMENT|DEPARTMENT OF HEALTH|VETERANS AFFAIRS|WALMART|WALGREENS|CVS)\b", row["name"], re.I):
        return "EXCLUDE", "Institutional, government or big-box employer profile", "excluded"
    if row["mailing_only"]:
        return "REVIEW", "Mailing-only territory tie; verify secondary practice locations and address before qualification", "research"
    if row["kind"] == "person":
        return "WATCH", "Individual enumeration is weak intel, not evidence of an independent practice", "watch"
    if any(code.startswith(PRACTICE_TAXONOMIES) for code in codes):
        return "STRONG", "New practice organization in territory; independence and space need remain unverified", "research"
    return "REVIEW", "Unknown taxonomy or practice profile; retained for human classification", "research"


def _fetch(url):
    request = urllib.request.Request(url, headers={"User-Agent": "CARR lead-signals weekly public data reader"})
    with urllib.request.urlopen(request, timeout=60) as response:
        return response.read()


def prepare(ctx):
    fixture = ctx.fixture
    if fixture is not None:
        rows = parse_nppes(fixture.get("nppes_rows", []), as_of=ctx.now.date(), source=INDEX_URL)
    else:
        urls = weekly_files(_fetch(INDEX_URL).decode("utf-8"), ctx.now.date())
        rows = []
        for url in urls:
            rows.extend(parse_weekly_zip(_fetch(url), as_of=ctx.now.date(), source=url))
    consumed_path = ROOT / "out/routines/lead-signals-weekly/consumed-candidates.json"
    consumed = set(fixture.get("consumed_keys", [])) if fixture is not None else set(json.loads(consumed_path.read_text()) if consumed_path.exists() else [])
    proposed = [row for row in candidates(rows) if row["source_key"] not in consumed]
    for row in proposed:
        row["classification"], row["classification_reason"], row["route"] = classify(row)
        matches = {}
        names = {row["name"], row["authorized_official"]["name"]} - {""}
        for name in sorted(names):
            found = (fixture.get("matches", {}).get(name, {}) if fixture is not None
                     else ctx.read("find", {"query": name}))
            for match in found.get("parties", []):
                if not match.get("merged"):
                    matches[match["ref"]] = {key: match.get(key) for key in ("ref", "kind", "name", "city")}
            for org in found.get("organizations", []):
                for ref in org.get("refs", []) + org.get("role_refs", []):
                    matches.setdefault(ref, {"ref": ref, "name": org["name"]})
        row["existing_candidates"] = [matches[ref] for ref in sorted(matches)]
        row["identity_status"] = "PENDING confirmation"
        if matches:
            possible = row["existing_candidates"]
            match = possible[0]
            exact = (len(possible) == 1 and _text(match.get("name")).casefold() == row["name"].casefold()
                     and _text(match.get("city")).casefold() == row["city"].casefold())
            row["route"] = {"client": "client-intel", "vendor": "vendor-intel", "lead": "prospect-intel"}.get(match.get("kind"), "identity-review") if exact else "identity-review"
    return {"work": bool(proposed), "candidates": proposed, "consumed_keys": sorted(consumed)}


def execute(ctx, plan):
    if ctx.dry_run:
        return {"routine": "lead-signals-weekly", "dry_run": True, "work": plan["work"],
                "candidate_count": len(plan["candidates"]),
                "estimated_scores": [r["score"] for r in plan["candidates"]],
                "model_calls": 0,
                "record_writes": 0, "local_writes": 0}
    proposed, holds, signals = [], [], []
    consumed = set(plan.get("consumed_keys", []))
    for row in sorted(plan["candidates"], key=lambda r: (-r["score"], r["source_key"])):
        proposal = dict(row)
        proposal.update(status="proposed", research_status="PENDING",
                        research_owner="npi-sweep-weekly",
                        space_need="VERIFY; unresearched, no probability or lease horizon assigned",
                        next_action="Research the principal, verify the address and space claim, work the recommendation and Winning it, then request Joe/Dell's touch/watch/skip verdict. No registry entry or outreach before that verdict.")
        if row["classification"] == "EXCLUDE":
            proposal["status"] = "excluded"
            proposal["next_action"] = "Institutional exclusion; keep the reason in the digest. The retained routine can review National Accounts scope before any pursuit."
            holds.append(proposal)
        elif row["existing_candidates"]:
            proposal["status"] = "intel_pending_confirmation"
            proposal["next_action"] = "Confirm the possible existing identity, then file dated intel with record-finding on that record. Do not treat an existing client, vendor or prospect as a new lead."
            signals.append(proposal)
        else:
            proposed.append(proposal)
        ctx.write("add-loop", {"kind": "team_loop", "owner": "claude", "domain": "prospecting",
                  "title": "NPPES proposal: " + row["name"], "body": json.dumps(proposal),
                  "marker": "none", "source_note": "NPPES " + row["npi"] + " " + " ".join(row["sources"])},
                  "lead-signals:proposal:" + row["source_key"])
        consumed.add(row["source_key"])
        if ctx.fixture is None:
            path = ROOT / "out/routines/lead-signals-weekly/consumed-candidates.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix(".tmp")
            temporary.write_text(json.dumps(sorted(consumed)))
            temporary.replace(path)
    output = {"status": "prepared", "proposed": proposed, "holds": holds, "signals": signals, "candidate_count": len(plan["candidates"]), "model_calls": 0,
              "ranked": [], "ranking_status": "PENDING npi-sweep-weekly research and radar rubric"}
    ctx.write("add-loop", {"kind": "team_loop", "owner": "claude", "domain": "prospecting",
              "title": "NPPES preparation " + ctx.now.date().isoformat(),
              "body": json.dumps(output), "marker": "none", "source_note": INDEX_URL},
              "lead-signals:run:" + ctx.now.date().isoformat())
    return output
